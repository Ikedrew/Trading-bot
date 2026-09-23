"""Focused synthetic tests for canonical RW9.2 R1."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import (
    load_report_for_question,
    resolve_report_validity,
)
from research_engine.control_plane.risk_policy_evidence import build_risk_policy_evidence
from research_engine.experiments import r1_risk_layer_effectiveness as r1
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    validate_all_definitions,
)
from research_engine.registry.master_repair_ledger import operational_baseline
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    CLUSTERED_INFERENCE,
    R1_CONTRACT,
    RUIN_MODEL_V1,
)


def _decision(opportunity: str, timestamp: int, *, blocked: bool) -> dict:
    row = {
        "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": opportunity,
        "canonical_symbol": "EURUSD",
        "entity_id": f"entity-{opportunity}",
        "decision_id": f"decision-{opportunity}",
        "correlation_id": f"correlation-{opportunity}",
        "timestamp_utc": timestamp,
        "stages_reached": ["risk"],
        "stages_passed": ["risk"],
    }
    if blocked:
        row.update({
            "record_role": "runtime_guard_rejection",
            "event_type": "RISK_REJECTION",
            "guard_name": "SPREAD_GUARD",
        })
    return row


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
            "decision_id": f"decision-{opportunity}",
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
    allowed: list[float],
    blocked: list[float],
    *,
    fanout_first_blocked: bool = False,
    reverse_inputs: bool = False,
):
    decisions: list[dict] = []
    outcomes: list[dict] = []
    suffix = 1
    for arm, values in (("allowed", allowed), ("blocked", blocked)):
        for index, value in enumerate(values):
            opportunity = f"EURUSD*{arm}-{index:03d}"
            timestamp = suffix * 100
            decisions.append(_decision(opportunity, timestamp, blocked=arm == "blocked"))
            outcomes.append(_outcome(opportunity, timestamp, value, suffix))
            suffix += 1
            if fanout_first_blocked and arm == "blocked" and index == 0:
                outcomes.append(_outcome(
                    opportunity,
                    timestamp,
                    value,
                    suffix,
                    account="account-b",
                ))
                suffix += 1
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


def test_r1_has_dedicated_unique_registry_and_report_ownership(tmp_path):
    question = REGISTRY_BY_ID["R1"]
    assert question.runner_module == "research_engine.experiments.r1_risk_layer_effectiveness"
    assert question.runner_function == "run_r1"
    assert question.report_filename == r1.REPORT_FILENAME == "r1_risk_layer_effectiveness.json"
    assert sum(item.report_filename == question.report_filename for item in REGISTRY) == 1
    assert canonical_report_owner(question.report_filename) == "R1"

    (tmp_path / "q10_guard_efficacy.json").write_text(json.dumps({
        "question_id": "Q10",
        "status": "COMPLETE",
        "epoch": "CURRENT",
    }), encoding="utf-8")
    report, path = load_report_for_question(
        "R1", question.report_filename, reports_dir=tmp_path,
    )
    assert report is None
    assert Path(path).name == question.report_filename


def test_r1_consumes_rw91_population_and_estimates_expectancy_with_hd10_inference():
    evidence = _evidence([2.0] * 50, [1.0] * 50)
    report = r1.analyse(evidence)
    expectancy = report["overall"]["tests"][0]

    assert report["status"] == "COMPLETE"
    assert expectancy["test_identity"] == r1.EXPECTANCY_ENDPOINT
    assert expectancy["estimand"] == R1_CONTRACT["primary_estimand"]
    assert expectancy["allowed_mean_r"] == 2.0
    assert expectancy["blocked_counterfactual_mean_r"] == 1.0
    assert expectancy["estimate"] == 1.0
    assert expectancy["cluster_identity"] == "canonical_opportunity_id"
    assert expectancy["covariance"] == CLUSTERED_INFERENCE["finite_sample_correction"]
    assert report["provenance"]["rw91_evidence_provenance_digest"] == evidence.provenance["digest"]
    assert report["provenance"]["holm_family_order"] == list(r1.HOLM_FAMILY)
    assert report["provenance"]["pre_decision_adjustment_set"] == []
    assert "outcome-driven selection is forbidden" in report["provenance"]["adjustment_set_rule"]
    assert report["provenance"]["exclusions"]["by_reason"] == {}


def test_r1_survival_endpoint_uses_frozen_ruin_model_and_holm_family():
    report = r1.analyse(_evidence([1.0] * 50, [-1.0] * 50))
    survival = report["overall"]["tests"][1]

    assert report["status"] == "COMPLETE"
    assert survival["test_identity"] == r1.SURVIVAL_ENDPOINT
    assert survival["estimand"] == R1_CONTRACT["secondary_estimand"]
    assert survival["allowed_arm"]["analytical_ruin_probability"] == 0.0
    assert survival["blocked_counterfactual_arm"]["analytical_ruin_probability"] == 1.0
    assert survival["estimate"] == -1.0
    for arm in ("allowed_arm", "blocked_counterfactual_arm"):
        simulation = survival[arm]["monte_carlo"]
        assert simulation["model_version"] == RUIN_MODEL_V1["model_version"]
        assert simulation["simulations"] == RUIN_MODEL_V1["monte_carlo"]["n_simulations"]
        assert simulation["trade_horizon"] == RUIN_MODEL_V1["monte_carlo"]["trade_horizon"]
        assert simulation["ruin_threshold_pct"] == RUIN_MODEL_V1["ruin_threshold_pct"]
        assert survival[arm]["agreement_within_tolerance"] is True
    assert {test["test_identity"] for test in report["overall"]["tests"]} == set(r1.HOLM_FAMILY)
    assert all("holm_adjusted_p_value" in test for test in report["overall"]["tests"])


def test_account_fanout_is_one_opportunity_and_cannot_multiply_r1_sample():
    evidence = _evidence([1.0] * 50, [1.0] * 50, fanout_first_blocked=True)
    report = r1.analyse(evidence)

    assert evidence.summary.account_fanout_observation_count == 1
    assert evidence.summary.blocked_count == 50
    assert report["dataset"]["blocked_counterfactual_opportunities"] == 50
    assert report["dataset"]["sample_size"] == 100
    assert report["overall"]["tests"][0]["blocked_counterfactual_opportunities"] == 50


def test_invalid_authority_blocks_and_insufficient_arms_wait():
    valid = _evidence([1.0] * 50, [1.0] * 50)
    invalid = build_risk_policy_evidence([], [], baseline_authority=None)
    blocked = r1.analyse(invalid)
    waiting = r1.analyse(_evidence([1.0] * 49, [1.0] * 49))

    assert blocked["status"] == "BLOCKED"
    assert "BASELINE_RISK_POLICY_AUTHORITY_MISSING" in blocked["provenance"]["readiness"]["blockers"]
    assert waiting["status"] == "WAITING_DATA"
    assert set(waiting["provenance"]["readiness"]["blockers"]) >= {
        "ALLOWED_ARM_BELOW_REQUIRED",
        "BLOCKED_COUNTERFACTUAL_ARM_BELOW_REQUIRED",
    }
    assert valid.baseline_authority_state == "CURRENT"


def test_sufficient_null_is_complete_report_valid_and_deterministic():
    evidence = _evidence([1.0] * 50, [1.0] * 50)
    first = r1.analyse(evidence)
    second = r1.analyse(_evidence([1.0] * 50, [1.0] * 50, reverse_inputs=True))

    assert first["status"] == "COMPLETE"
    assert first["overall"]["finding_classification"] == "SUFFICIENT_NULL_NO_SUPPORTED_BENEFIT"
    assert all(test["interpretation"] == "NOT_SUPPORTED" for test in first["overall"]["tests"])
    assert _without_generated(first) == _without_generated(second)
    assert first["provenance"]["report_digest"] == second["provenance"]["report_digest"]
    assert r1.validate_r1_report(first)[0] is True
    drifted = deepcopy(first)
    drifted["provenance"]["baseline_identity_hash"] = "drifted"
    assert r1.validate_r1_report(drifted)[0] is False
    validity, _ = resolve_report_validity(
        r1.REPORT_FILENAME,
        first,
        expected_question_id="R1",
        accepted_question_ids=("Q10",),
    )
    assert validity == ReportValidity.VALID_CURRENT


def test_registry_versions_count_ledger_and_production_boundary_are_unchanged():
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert len(REGISTRY) == len({item.id for item in REGISTRY}) == 70
    assert all(definition.definition_version == 1 for definition in definitions.values())
    assert all(
        not any(issue.category == "INVALID_VERSION" for issue in result.results)
        for result in health.values()
    )
    assert operational_baseline() == (58, 12)

    assert REGISTRY_BY_ID["R2"].runner_function == "run_r2"
    assert REGISTRY_BY_ID["R2"].report_filename == "r2_guard_attribution.json"
    assert [REGISTRY_BY_ID[qid].report_filename for qid in ("R3", "R4", "R5")] == [
        "r3_probability_of_ruin.json",
        "r4_drawdown_threshold.json",
        "r5_position_sizing.json",
    ]

    tree = ast.parse(Path(r1.__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint({"config", "core", "risk"})
    assert "Research-only" in r1.analyse(_evidence([1.0] * 50, [1.0] * 50))["warnings"][0]
