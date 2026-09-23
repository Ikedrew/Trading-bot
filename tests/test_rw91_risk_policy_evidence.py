"""Focused synthetic contract tests for the RW9.1 evidence foundation."""
from __future__ import annotations

from copy import deepcopy

from research_engine.control_plane import risk_policy_evidence as evidence
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    baseline_risk_policy_identity_hash,
)


def _decision(
    opportunity: str,
    timestamp: int,
    *,
    guard: str | None = None,
    contributing_guard: str | None = None,
) -> dict:
    row = {
        "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": opportunity,
        "canonical_symbol": "EURUSD",
        "entity_id": f"entity-{opportunity}",
        "decision_id": f"decision-{opportunity}",
        "correlation_id": f"correlation-{opportunity}",
        "cycle_id": f"cycle-{opportunity}",
        "timestamp_utc": timestamp,
        "stages_reached": ["risk"],
        "stages_passed": ["risk"],
    }
    if guard is not None:
        row.update({
            "record_role": "runtime_guard_rejection",
            "event_type": "RISK_REJECTION",
            "guard_name": guard,
        })
    if contributing_guard is not None:
        row["risk_rejection_detail"] = {"guard_name": contributing_guard}
    return row


def _outcome(
    opportunity: str,
    entry: int,
    r_multiple: float,
    *,
    account: str = "account-a",
    suffix: int = 1,
    exit_utc: int | None = None,
) -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "shadow_trade_id": f"nshadow_{suffix:016x}",
            "canonical_opportunity_id": opportunity,
            "entity_id": f"entity-{opportunity}",
            "decision_id": f"decision-{opportunity}",
            "correlation_id": f"correlation-{opportunity}",
            "strategy_id": "MEAN_REVERSION",
            "evaluated_horizon": "SCALP",
            "account_id": account,
            "shadow_type": evidence.PRIMARY_HORIZON_SCOPE,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": entry,
            "trade_horizon": "SCALP",
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_timestamp": exit_utc if exit_utc is not None else entry + 10,
        },
    }


def _build(decisions: list[dict], outcomes: list[dict]):
    return evidence.build_risk_policy_evidence(
        decisions,
        outcomes,
        baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


def test_canonical_population_preserves_disposition_guards_and_account_grain():
    decisions = [
        _decision("EURUSD*allowed", 100),
        _decision(
            "EURUSD*blocked",
            200,
            guard="CORRELATION_GUARD",
            contributing_guard="SPREAD_GUARD",
        ),
    ]
    outcomes = [
        _outcome("EURUSD*allowed", 100, 1.25, suffix=1),
        _outcome("EURUSD*blocked", 200, -1.0, account="account-a", suffix=2),
        _outcome("EURUSD*blocked", 200, 0.5, account="account-b", suffix=3),
    ]

    built = _build(decisions, outcomes)
    assert [row.canonical_opportunity_id for row in built.records] == [
        "EURUSD*allowed",
        "EURUSD*blocked",
    ]
    allowed, blocked = built.records
    assert allowed.risk_disposition == evidence.ALLOWED
    assert blocked.risk_disposition == evidence.BLOCKED
    assert blocked.outcome_counterfactual is True
    assert blocked.outcome_r_multiple == -0.25
    assert blocked.account_execution_count == 2
    assert blocked.active_guards == ("CORRELATION", "SPREAD")
    assert blocked.terminal_guard == "CORRELATION"
    assert blocked.multi_guard is True
    assert blocked.guard_exclusive is None

    summary = built.summary
    assert summary.total_candidate_observations == 2
    assert summary.eligible_observations == 2
    assert summary.distinct_canonical_opportunities == 2
    assert summary.allowed_count == 1
    assert summary.blocked_count == 1
    assert summary.blocked_with_valid_counterfactual_count == 1
    assert summary.account_fanout_observation_count == 1
    assert summary.guard_exposure_counts == {
        "SPREAD": 1,
        "CORRELATION": 1,
        "REGIME": 0,
        "DAILY_LOSS": 0,
    }


def test_population_and_all_digests_are_deterministic_under_reordered_inputs():
    decisions = [
        _decision("EURUSD*first", 100),
        _decision("EURUSD*second", 200, guard="REGIME_GUARD"),
    ]
    outcomes = [
        _outcome("EURUSD*first", 100, 0.5, suffix=4),
        _outcome("EURUSD*second", 200, -0.75, suffix=5),
    ]
    forward = _build(decisions, outcomes)
    reverse = _build(list(reversed(decisions)), list(reversed(outcomes)))

    assert forward.records == reverse.records
    assert forward.exclusions == reverse.exclusions
    assert forward.provenance == reverse.provenance
    assert (
        evidence.build_chronological_risk_population(forward)
        == evidence.build_chronological_risk_population(reverse)
    )


def test_missing_or_drifted_hd10_baseline_authority_fails_closed():
    exact, reason = evidence.resolve_baseline_authority(deepcopy(BASELINE_RISK_POLICY_V1))
    assert exact == BASELINE_RISK_POLICY_V1
    assert reason is None
    assert evidence.resolve_baseline_authority(None) == (
        None,
        evidence.BASELINE_AUTHORITY_MISSING,
    )
    assert evidence.resolve_baseline_authority({}) == (
        None,
        evidence.BASELINE_AUTHORITY_MISSING,
    )

    drifted = deepcopy(BASELINE_RISK_POLICY_V1)
    drifted["policy_version"] = 2
    drifted["identity_hash"] = baseline_risk_policy_identity_hash(drifted)
    assert evidence.resolve_baseline_authority(drifted) == (
        None,
        evidence.BASELINE_AUTHORITY_DRIFT,
    )

    built = evidence.build_risk_policy_evidence(
        [_decision("EURUSD*missing", 100)],
        [_outcome("EURUSD*missing", 100, 1.0, suffix=6)],
    )
    assert built.records == ()
    assert built.baseline_authority_state == evidence.BASELINE_AUTHORITY_MISSING
    assert built.summary.exclusions_by_reason == {
        evidence.BASELINE_AUTHORITY_MISSING: 1,
    }


def test_stable_exclusions_cover_missing_counterfactual_and_unknown_guard():
    decisions = [
        _decision("EURUSD*known", 100, guard="DAILY_LOSS_GUARD"),
        _decision("EURUSD*unknown", 200, guard="future_guard"),
        _decision("EURUSD*missing", 300, guard="SPREAD_GUARD"),
    ]
    outcomes = [
        _outcome("EURUSD*known", 100, -1.0, suffix=7),
        _outcome("EURUSD*unknown", 200, 0.25, suffix=8),
    ]
    built = _build(decisions, outcomes)

    assert evidence.GUARD_IDS == ("SPREAD", "CORRELATION", "REGIME", "DAILY_LOSS")
    assert evidence.normalize_guard_name("CORRELATION_GUARD") == "CORRELATION"
    assert evidence.normalize_guard_name("future_guard") is None
    unknown = next(row for row in built.records if row.canonical_opportunity_id.endswith("unknown"))
    assert unknown.unmapped_guards == ("future_guard",)
    assert unknown.guard_authority_state == evidence.GUARD_AUTHORITY_UNMAPPED
    assert built.summary.exclusions_by_reason == {
        "MISSING_BLOCKED_COUNTERFACTUAL_OUTCOME": 1,
    }
    assert set(built.summary.exclusions_by_reason).issubset(evidence.EXCLUSION_REASONS)


def test_decision_to_outcome_linkage_rejects_conflicting_declared_lineage():
    outcome = _outcome("EURUSD*mismatch", 100, 1.0, suffix=9)
    outcome["identity"]["entity_id"] = "different-entity"
    built = _build([_decision("EURUSD*mismatch", 100)], [outcome])

    assert built.records == ()
    assert built.summary.exclusions_by_reason == {
        "DECISION_OUTCOME_LINEAGE_MISMATCH": 1,
    }


def test_chronological_population_and_windows_are_evidence_only_and_purged():
    decisions = [_decision(f"EURUSD*{index}", index * 100) for index in range(1, 5)]
    outcomes = [
        _outcome("EURUSD*1", 100, 0.1, suffix=11, exit_utc=150),
        _outcome("EURUSD*2", 200, 0.2, suffix=12, exit_utc=350),
        _outcome("EURUSD*3", 300, 0.3, suffix=13, exit_utc=310),
        _outcome("EURUSD*4", 400, 0.4, suffix=14, exit_utc=410),
    ]
    population = evidence.build_chronological_risk_population(
        _build(list(reversed(decisions)), list(reversed(outcomes)))
    )
    windows = evidence.split_chronological_windows(population)

    assert population.entry_chronology() == (100, 200, 300, 400)
    assert population.eligible_count == population.distinct_canonical_opportunities == 4
    assert [row.canonical_opportunity_id for row in windows.calibration_records] == [
        "EURUSD*1"
    ]
    assert [row.canonical_opportunity_id for row in windows.purged_records] == [
        "EURUSD*2"
    ]
    assert [row.canonical_opportunity_id for row in windows.validation_records] == [
        "EURUSD*3",
        "EURUSD*4",
    ]
    assert windows.first_validation_entry_utc == 300


def test_module_remains_an_evidence_foundation_not_an_r1_to_r5_runner():
    public_names = set(vars(evidence))
    assert not any(name.startswith(("run_r", "estimate_r", "simulate_r", "optimise_r")) for name in public_names)
    assert "Q10" not in public_names
