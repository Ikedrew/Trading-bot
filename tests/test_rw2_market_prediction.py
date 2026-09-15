"""Focused proof of the implemented RW2 market-prediction foundation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_engine.experiments.market_prediction_rw2 import (
    CONTRACTS,
    REPORT_FILENAMES,
    analyse,
    assert_leakage_safe_predictors,
    build_opportunity_observations,
    chronological_split,
)
from research_engine.registry.research_question_registry import REGISTRY_BY_ID


def _shadow(
    index: int,
    *,
    opportunity: str | None = None,
    horizon: str = "SCALP",
    outcome: float | None = None,
    epoch: str = "CURRENT",
) -> dict:
    opportunity = opportunity or f"OPP-{index:04d}"
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    regime = "TRENDING" if index % 2 else "RANGING"
    phase = ("IMPULSE", "PULLBACK", "EXHAUSTION", "CONSOLIDATION")[index % 4]
    bias = "BULLISH" if (index // 2) % 2 else "BEARISH"
    if outcome is None:
        outcome = (1.0 if regime == "TRENDING" else -0.5) + (0.4 if phase in {"IMPULSE", "EXHAUSTION"} else -0.2)
    return {
        "data_epoch": epoch,
        "identity": {
            "canonical_opportunity_id": opportunity,
            "entity_id": f"ENTITY-{index:04d}",
            "symbol": "EURUSD",
            "shadow_trade_id": f"nshadow_{index:016x}"[-24:],
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp.isoformat(),
            "h4_regime": regime,
            "market_phase": phase,
            "h1_bias": bias,
            "pattern": "PIN_BAR" if index % 2 else "ENGULFING",
            "strategy": "REVERSAL",
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
        "account_id": "ACCOUNT-A",
    }


def _trace(index: int, *, phase: str | None = None) -> dict:
    row = _shadow(index)
    snapshot = row["decision_snapshot"]
    return {
        "data_epoch": "CURRENT",
        "canonical_opportunity_id": row["identity"]["canonical_opportunity_id"],
        "timestamp_utc": snapshot["timestamp_decision_utc"],
        "v10_market_state": {
            "regime": {"regime": snapshot["h4_regime"]},
            "h4": {"market_phase": phase or snapshot["market_phase"]},
            "h1": {"dominant_trend": snapshot["h1_bias"]},
        },
    }


def _market_context_rows(count: int) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{
        "data_epoch": "CURRENT", "entity_id": "SEED", "symbol": "EURUSD",
        "timestamp": (start - timedelta(hours=1)).isoformat(), "market_phase": "CONSOLIDATION",
    }]
    for index in range(count):
        shadow = _shadow(index)
        rows.append({
            "data_epoch": "CURRENT",
            "canonical_opportunity_id": shadow["identity"]["canonical_opportunity_id"],
            "entity_id": shadow["identity"]["entity_id"],
            "symbol": "EURUSD",
            "timestamp": shadow["decision_snapshot"]["timestamp_decision_utc"],
            "market_phase": shadow["decision_snapshot"]["market_phase"],
        })
    return rows


def test_one_canonical_opportunity_is_one_observation_despite_horizon_and_account_fanout():
    first = _shadow(1, opportunity="ONE", horizon="SCALP", outcome=1.0)
    second = _shadow(1, opportunity="ONE", horizon="EXTENDED", outcome=3.0)
    second["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    second["account_id"] = "ACCOUNT-B"
    observations, diagnostics = build_opportunity_observations([first, second])
    assert len(observations) == 1
    assert observations[0].canonical_opportunity_id == "ONE"
    assert observations[0].outcome_r == 2.0
    assert diagnostics["repeated_rows_collapsed"] == 1


def test_missing_outcome_stays_missing_and_non_current_is_excluded():
    missing = _shadow(1, outcome=1.0)
    missing["simulated_outcome"]["pnl_r_multiple"] = None
    legacy = _shadow(2, epoch="LEGACY")
    observations, diagnostics = build_opportunity_observations([missing, legacy])
    assert len(observations) == 1 and observations[0].outcome_r is None
    assert diagnostics["missing_outcomes"] == 1
    assert diagnostics["excluded_non_current"] == 1


def test_conflicting_canonical_opportunity_join_fails_closed():
    first = _shadow(1, opportunity="CONFLICT")
    second = _shadow(2, opportunity="CONFLICT")
    report = analyse("M1", [first, second])
    assert report["status"] == "BLOCKED"
    assert report["overall"]["diagnostics"]["ambiguous_opportunities"] == ("CONFLICT",)


def test_chronological_partition_is_deterministic_and_strictly_later():
    rows, _ = build_opportunity_observations([_shadow(i) for i in reversed(range(20))])
    discovery, validation = chronological_split(rows)
    assert {row.canonical_opportunity_id for row in discovery}.isdisjoint(
        row.canonical_opportunity_id for row in validation
    )
    assert max(row.decision_time for row in discovery) < min(row.decision_time for row in validation)
    assert chronological_split(rows) == (discovery, validation)


@pytest.mark.parametrize("field", ["r_multiple", "mfe_r", "mae", "exit_reason", "future_regime", "broker_pnl"])
def test_post_outcome_predictors_are_rejected(field: str):
    with pytest.raises(ValueError, match="post-outcome predictor leakage"):
        assert_leakage_safe_predictors([field])


def test_descriptive_association_alone_cannot_complete_predictive_question():
    report = analyse("M1", [_shadow(i) for i in range(20)])
    assert report["overall"]["descriptive_association"]
    assert report["status"] == "WAITING_DATA"
    assert report["overall"]["predictive_evidence_supported"] is None


@pytest.mark.parametrize("question_id,count", [("M1", 64), ("M3", 96), ("M7", 96)])
def test_shadow_prediction_runners_complete_only_with_later_validation(question_id: str, count: int):
    report = analyse(question_id, [_shadow(i) for i in range(count)])
    assert report["status"] == "COMPLETE"
    assert report["overall"]["discovery"]["period_end"] < report["overall"]["later_validation"]["period_start"]
    assert report["overall"]["predictive_evaluation"]["paired_validation_n"] >= CONTRACTS[question_id].minimum_validation
    assert report["dataset"]["independent_observations"] == count


def test_m8_requires_canonical_market_context_and_snapshot_cannot_substitute():
    records = [_shadow(i) for i in range(64)]
    report = analyse("M8", records)
    assert report["status"] == "BLOCKED"
    assert "snapshot context cannot substitute" in report["overall"]["completion_reason"]


def test_m8_uses_ordered_current_market_context_and_completes():
    count = 64
    report = analyse("M8", [_shadow(i) for i in range(count)], market_context_records=_market_context_rows(count))
    assert report["status"] == "COMPLETE"
    assert report["overall"]["evidence_authority"].startswith("canonical CURRENT market_context")
    assert report["overall"]["predictive_evaluation"]["candidate"]["eligible_cells"]


def test_m8_material_snapshot_authority_conflict_fails_closed():
    records = [_shadow(i) for i in range(64)]
    context = _market_context_rows(64)
    context[10]["market_phase"] = "REVERSAL"
    report = analyse("M8", records, market_context_records=context)
    assert report["status"] == "BLOCKED"


def test_m11_requires_authoritative_trace_and_enforces_cell_sufficiency():
    records = [_shadow(i) for i in range(128)]
    assert analyse("M11", records)["status"] == "BLOCKED"
    report = analyse("M11", records, decision_trace_records=[_trace(i) for i in range(128)])
    assert report["status"] == "COMPLETE"
    cells = report["overall"]["predictive_evaluation"]["candidate"]
    assert len(cells["eligible_cells"]) >= 2
    assert min(cells["discovery_cell_counts"][cell] for cell in cells["eligible_cells"]) >= CONTRACTS["M11"].minimum_cell
    assert min(cells["validation_cell_counts"][cell] for cell in cells["eligible_cells"]) >= CONTRACTS["M11"].minimum_cell


def test_m11_conflicting_trace_join_fails_closed():
    records = [_shadow(i) for i in range(128)]
    traces = [_trace(i) for i in range(128)]
    traces.append(_trace(3, phase="REVERSAL"))
    assert analyse("M11", records, decision_trace_records=traces)["status"] == "BLOCKED"


def test_rw2_registry_has_unique_canonical_report_and_runner_ownership():
    filenames = []
    for question_id, filename in REPORT_FILENAMES.items():
        question = REGISTRY_BY_ID[question_id]
        assert question.runner_module == "research_engine.experiments.market_prediction_rw2"
        assert question.runner_function == f"run_{question_id.lower()}"
        assert question.report_filename == filename
        filenames.append(filename)
    assert len(filenames) == len(set(filenames)) == 5
    all_owners = [question.report_filename for question in REGISTRY_BY_ID.values()]
    assert all(all_owners.count(filename) == 1 for filename in filenames)


def test_control_plane_readiness_counts_distinct_opportunities_not_shadow_rows():
    from research_engine.control_plane.evidence_resolver import EvidenceSnapshot, resolve_question_evidence

    rows = []
    for index in range(60):
        rows.append(_shadow(index, horizon="SCALP"))
        duplicate = _shadow(index, horizon="SCALP")
        duplicate["identity"]["shadow_trade_id"] = f"nshadow_{index + 1000:016x}"
        duplicate["account_id"] = "ACCOUNT-B"
        rows.append(duplicate)
    evidence = resolve_question_evidence(
        REGISTRY_BY_ID["M1"], EvidenceSnapshot({"shadow_trades": rows})
    )
    assert evidence.usable_count == 60
    assert evidence.metrics["repeated_rows_collapsed"] == 60
    sample = next(item for item in evidence.requirements if item.name == "sample_size")
    assert sample.current == 60 and sample.satisfied is True


def test_rw2_report_existence_does_not_override_scientific_status():
    from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
    from research_engine.control_plane.readiness import resolve_readiness

    question = REGISTRY_BY_ID["M1"]
    waiting, _ = resolve_readiness(
        question, None, RunnerStatus.READY, ReportValidity.VALID_CURRENT,
        "WAITING_DATA", {},
    )
    blocked, _ = resolve_readiness(
        question, None, RunnerStatus.READY, ReportValidity.VALID_CURRENT,
        "BLOCKED", {},
    )
    assert waiting == ReadinessStatus.WAITING_DATA
    assert blocked == ReadinessStatus.BLOCKED


def test_rw2_ledger_gain_is_derived_and_rw1_ownership_remains_intact():
    from research_engine.control_plane.report_ownership import canonical_report_owner
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER,
        REPAIR_WAVES,
        STRUCTURALLY_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (32, 38)
    assert REPAIR_WAVES["RW2"].implemented is True
    assert set(REPAIR_WAVES["RW2"].direct_gain) == {"M1", "M3", "M7", "M8", "M11"}
    assert set(REPAIR_WAVES["RW2"].direct_gain) <= STRUCTURALLY_OPERATIONAL_IDS
    assert all(MASTER_REPAIR_LEDGER[qid].structurally_operational for qid in REPAIR_WAVES["RW2"].direct_gain)
    assert canonical_report_owner("q1_component_reward.json") == "D1"
    assert canonical_report_owner("q5_pattern_degradation.json") == "E2"


def test_unrelated_ledger_operational_ids_are_exactly_frozen_baseline_plus_rw2():
    from research_engine.registry.master_repair_ledger import STRUCTURALLY_OPERATIONAL_IDS
    from research_engine.registry.wave_a1_definitions import WAVE_A1_RESOLVED
    from research_engine.registry.wave_a2_definitions import WAVE_A2_RESOLVED

    assert STRUCTURALLY_OPERATIONAL_IDS == frozenset(WAVE_A1_RESOLVED | WAVE_A2_RESOLVED) | {
        "M1", "M3", "M7", "M8", "M11", "D2"
    }
