"""Focused synthetic tests for canonical RW9.3 R2."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import (
    load_report_for_question,
    resolve_report_validity,
)
from research_engine.control_plane.risk_policy_evidence import build_risk_policy_evidence
from research_engine.experiments import r2_guard_attribution as r2
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    validate_all_definitions,
)
from research_engine.registry.master_repair_ledger import operational_baseline
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    CLUSTERED_INFERENCE,
    GUARD_TAXONOMY_V1,
    R2_CONTRACT,
)


def _decision(opportunity: str, timestamp: int, *guards: str) -> list[dict]:
    base = {
        "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": opportunity,
        "canonical_symbol": "EURUSD",
        "entity_id": f"entity-{opportunity}",
        "correlation_id": f"correlation-{opportunity}",
        "timestamp_utc": timestamp,
        "stages_reached": ["risk"],
        "stages_passed": ["risk"],
    }
    if not guards:
        return [{**base, "decision_id": f"decision-{opportunity}"}]
    return [{
        **base,
        "decision_id": f"decision-{opportunity}-{index}",
        "record_role": "runtime_guard_rejection",
        "event_type": "RISK_REJECTION",
        "guard_name": guard,
    } for index, guard in enumerate(guards)]


def _outcome(
    opportunity: str,
    timestamp: int,
    outcome_r: float,
    suffix: int,
    *,
    account: str = "account-a",
) -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "shadow_trade_id": f"nshadow_{suffix:016x}",
            "canonical_opportunity_id": opportunity,
            "entity_id": f"entity-{opportunity}",
            "correlation_id": f"correlation-{opportunity}",
            "strategy_id": "MEAN_REVERSION",
            "evaluated_horizon": "SCALP",
            "account_id": account,
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp,
            "trade_horizon": "SCALP",
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {
            "pnl_r_multiple": outcome_r,
            "exit_timestamp": timestamp + 10,
        },
    }


def _evidence(
    *,
    allowed_n: int = 30,
    per_guard_n: dict[str, int] | None = None,
    allowed_r: float = 1.0,
    blocked_r: float = 1.0,
    multi_guard: bool = False,
    unknown_guard: bool = False,
    fanout: bool = False,
    reverse_inputs: bool = False,
):
    counts = {guard: 30 for guard in r2.GUARDS}
    if per_guard_n:
        counts.update(per_guard_n)
    decisions: list[dict] = []
    outcomes: list[dict] = []
    suffix = 1

    for index in range(allowed_n):
        opportunity = f"EURUSD*allowed-{index:03d}"
        timestamp = suffix * 100
        decisions.extend(_decision(opportunity, timestamp))
        outcomes.append(_outcome(opportunity, timestamp, allowed_r, suffix))
        suffix += 1

    for guard in r2.GUARDS:
        for index in range(counts[guard]):
            opportunity = f"EURUSD*{guard.lower()}-{index:03d}"
            timestamp = suffix * 100
            decisions.extend(_decision(opportunity, timestamp, f"{guard}_GUARD"))
            outcomes.append(_outcome(opportunity, timestamp, blocked_r, suffix))
            if fanout and guard == "SPREAD" and index == 0:
                suffix += 1
                outcomes.append(_outcome(
                    opportunity, timestamp, blocked_r, suffix, account="account-b",
                ))
            suffix += 1

    if multi_guard:
        opportunity = "EURUSD*multi-guard"
        timestamp = suffix * 100
        decisions.extend(_decision(
            opportunity, timestamp, "SPREAD_GUARD", "CORRELATION_GUARD",
        ))
        outcomes.append(_outcome(opportunity, timestamp, -9.0, suffix))
        suffix += 1
    if unknown_guard:
        opportunity = "EURUSD*unknown-guard"
        timestamp = suffix * 100
        decisions.extend(_decision(opportunity, timestamp, "MYSTERY_GUARD"))
        outcomes.append(_outcome(opportunity, timestamp, -9.0, suffix))

    if reverse_inputs:
        decisions.reverse()
        outcomes.reverse()
    return build_risk_policy_evidence(
        decisions,
        outcomes,
        baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


def _without_generated(report: dict) -> dict:
    result = deepcopy(report)
    result.pop("generated", None)
    return result


def test_r2_has_dedicated_unique_ownership_and_q10_cannot_complete_it(tmp_path):
    question = REGISTRY_BY_ID["R2"]
    assert question.runner_module == "research_engine.experiments.r2_guard_attribution"
    assert question.runner_function == "run_r2"
    assert question.report_filename == r2.REPORT_FILENAME == "r2_guard_attribution.json"
    assert sum(item.report_filename == question.report_filename for item in REGISTRY) == 1
    assert canonical_report_owner(question.report_filename) == "R2"

    (tmp_path / "q10_guard_efficacy.json").write_text(json.dumps({
        "question_id": "Q10", "status": "COMPLETE", "epoch": "CURRENT",
    }), encoding="utf-8")
    report, path = load_report_for_question(
        "R2", question.report_filename, reports_dir=tmp_path,
    )
    assert report is None
    assert Path(path).name == question.report_filename


def test_exact_four_guard_estimands_comparator_weighting_cr0_and_holm():
    report = r2.analyse(_evidence(allowed_r=1.0, blocked_r=-1.0))
    tests = report["overall"]["tests"]

    assert report["status"] == "COMPLETE"
    assert r2.GUARDS == tuple(guard["guard_id"] for guard in GUARD_TAXONOMY_V1)
    assert tuple(test["test_identity"] for test in tests) == r2.HOLM_FAMILY
    assert len(tests) == 4
    for test in tests:
        assert test["estimand"] == R2_CONTRACT["per_guard_estimand"]
        assert test["candidate_opportunities"] == 30
        assert test["comparator_opportunities"] == 30
        assert test["candidate_mean_r"] == -1.0
        assert test["comparator_mean_r"] == 1.0
        assert test["estimate"] == -2.0
        assert test["interpretation"] == "SUPPORTED_BENEFIT"
        assert test["cluster_identity"] == "canonical_opportunity_id"
        assert test["covariance"] == CLUSTERED_INFERENCE["finite_sample_correction"]
        assert "holm_adjusted_p_value" in test
    assert report["provenance"]["pre_decision_stratum_fields"] == []
    assert "all governed allowed opportunities" in report["provenance"]["comparator_rule"]


def test_guard_exclusive_grain_preserves_multi_guard_once_without_double_counting():
    evidence = _evidence(multi_guard=True)
    report = r2.analyse(evidence)
    residual = report["provenance"]["multi_guard_residual"]

    assert evidence.summary.multi_guard_count == 1
    assert residual["opportunities"] == 1
    assert residual["canonical_opportunity_ids"] == ["EURUSD*multi-guard"]
    assert "excluded from every per-guard contrast" in residual["attribution_rule"]
    assert all(test["candidate_opportunities"] == 30 for test in report["overall"]["tests"])
    assert sum(report["dataset"]["guard_exclusive_opportunities"].values()) == 120


def test_unknown_guard_blocks_and_invalid_baseline_blocks():
    unknown = r2.analyse(_evidence(unknown_guard=True))
    invalid_baseline = r2.analyse(build_risk_policy_evidence([], [], baseline_authority=None))

    assert unknown["status"] == "BLOCKED"
    assert "UNKNOWN_OR_MISSING_GUARD_AUTHORITY" in unknown["provenance"]["readiness"]["blockers"]
    assert invalid_baseline["status"] == "BLOCKED"
    assert "BASELINE_RISK_POLICY_AUTHORITY_MISSING" in invalid_baseline["provenance"]["readiness"]["blockers"]


def test_account_fanout_does_not_increase_guard_exclusive_n():
    evidence = _evidence(fanout=True)
    report = r2.analyse(evidence)

    assert evidence.summary.account_fanout_observation_count == 1
    assert evidence.summary.guard_exclusive_counts["SPREAD"] == 30
    spread = report["overall"]["tests"][0]
    assert spread["candidate_opportunities"] == 30
    assert report["dataset"]["sample_size"] == 150


def test_one_underpowered_guard_cannot_borrow_and_valid_null_can_complete():
    waiting = r2.analyse(_evidence(per_guard_n={"DAILY_LOSS": 29}))
    complete = r2.analyse(_evidence())

    assert waiting["status"] == "WAITING_DATA"
    guard_states = waiting["provenance"]["readiness"]["guards"]
    assert guard_states["DAILY_LOSS"]["state"] == "WAITING_DATA"
    assert guard_states["DAILY_LOSS"]["guard_exclusive_opportunities"] == 29
    assert all(guard_states[guard]["state"] == "READY" for guard in r2.GUARDS[:-1])
    assert complete["status"] == "COMPLETE"
    assert complete["overall"]["finding_classification"] == (
        "SUFFICIENT_NULL_NO_SUPPORTED_GUARD_BENEFIT"
    )
    assert all(test["interpretation"] == "NOT_SUPPORTED" for test in complete["overall"]["tests"])


def test_report_and_provenance_are_deterministic_valid_and_tamper_closed():
    first = r2.analyse(_evidence())
    second = r2.analyse(_evidence(reverse_inputs=True))

    assert _without_generated(first) == _without_generated(second)
    assert first["provenance"]["report_digest"] == second["provenance"]["report_digest"]
    assert r2.validate_r2_report(first)[0] is True
    validity, _ = resolve_report_validity(
        r2.REPORT_FILENAME,
        first,
        expected_question_id="R2",
        accepted_question_ids=("Q10",),
    )
    assert validity == ReportValidity.VALID_CURRENT
    drifted = deepcopy(first)
    drifted["provenance"]["holm_family_order"] = drifted["provenance"]["holm_family_order"][:-1]
    assert r2.validate_r2_report(drifted)[0] is False


def test_registry_versions_counts_and_adjacent_question_boundaries():
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert len(REGISTRY) == len({item.id for item in REGISTRY}) == 70
    assert all(definition.definition_version == 1 for definition in definitions.values())
    assert all(
        not any(issue.category == "INVALID_VERSION" for issue in result.results)
        for result in health.values()
    )
    assert operational_baseline() == (58, 12)

    assert REGISTRY_BY_ID["R1"].runner_function == "run_r1"
    assert REGISTRY_BY_ID["R1"].report_filename == "r1_risk_layer_effectiveness.json"
    assert [REGISTRY_BY_ID[qid].report_filename for qid in ("R3", "R4", "R5")] == [
        "r3_probability_of_ruin.json",
        "r4_drawdown_threshold.json",
        "r5_position_sizing.json",
    ]

    tree = ast.parse(Path(r2.__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint({"config", "core", "risk"})
    assert "Research-only" in r2.analyse(_evidence())["warnings"][0]
