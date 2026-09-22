"""Focused Repair 4B.4 tests for governed X6 execution stability."""
from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from research_engine.control_plane.execution_evidence import (
    attest_execution_analytical_subset,
    build_governed_execution_evidence,
)
from research_engine.control_plane.models import ReadinessStatus
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.execution_stability import (
    DIMENSIONS,
    MIN_CELL_DECISIONS,
    MIN_CELL_RESULTS,
    MIN_OVERALL_DECISIONS,
    MIN_OVERALL_RESULTS,
    REPORT_FILENAME,
    build_x6_report,
    run_x6,
    spread_band,
)
from research_engine.registry import BASELINE_QUESTION_IDS, REGISTRY, REGISTRY_BY_ID
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    MASTER_REPAIR_LEDGER,
    operational_baseline,
)
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_TARGETS,
    X6_HD08_ADJUDICATED_CONTRACT,
)


class _EmptyCandidates:
    def list_all(self):
        return []


def _result(correlation: str, account: int, slippage, *, result_ok=True, epoch="CURRENT"):
    return {
        "schema_version": "execution_results_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD" if int(correlation.split("-")[-1]) % 2 == 0 else "GBPUSD",
        "account_id": f"ACCOUNT-{account}", "broker": "Broker", "broker_server": "Server",
        "order_ticket": int(correlation.split("-")[-1]) * 10 + account + 1,
        "result_ok": result_ok, "retcode": 10009 if result_ok is True else 10030,
        "slippage": slippage, "slippage_semantic": "measured_execution_slippage",
    }


def _context(correlation: str, *, epoch="CURRENT", spread_ratio=None, raw_spread=999.0):
    index = int(correlation.split("-")[-1])
    ratio = (0.05 if index % 2 == 0 else 0.30) if spread_ratio is None else spread_ratio
    return {
        "schema_version": "execution_context_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD" if index % 2 == 0 else "GBPUSD",
        "market_access": {
            "session_state": "LONDON" if index % 2 == 0 else "NEW_YORK",
            "spread": raw_spread, "spread_atr_ratio": ratio,
        },
    }


def _trace(correlation: str, *, epoch="CURRENT", include_regime=True, h4="EXTREME"):
    index = int(correlation.split("-")[-1])
    market_state = {"h4": {"volatility_state": h4}}
    if include_regime:
        market_state["regime"] = {
            "volatility_state": "CALM" if index % 2 == 0 else "VOLATILE",
        }
    return {
        "schema_version": "decision_trace_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD" if index % 2 == 0 else "GBPUSD", "action": "EXECUTE",
        "v10_market_state": market_state,
    }


def _population(*, reliable=True, decisions=40, accounts=3):
    results, contexts, traces = [], [], []
    for index in range(decisions):
        correlation = f"COR-{index}"
        contexts.append(_context(correlation))
        traces.append(_trace(correlation))
        cell_base = (0.01 if index % 2 == 0 else (0.08 if reliable else 0.01))
        cluster_noise = ((index // 2) % 5 - 2) * 0.0005
        for account in range(accounts):
            results.append(_result(
                correlation, account,
                -(cell_base + cluster_noise + account * 0.00005),
                result_ok=None if account == 2 else (index % 5 != 0),
            ))
    return results, contexts, traces


def _state(tmp_path, report, evidence):
    if report is not None:
        (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    results, contexts, traces = evidence
    return build_question_state(
        "X6", reports_dir=tmp_path,
        evidence_source={
            "execution_results_v1": results,
            "execution_context": contexts,
            "decision_trace": traces,
        },
        candidate_registry=_EmptyCandidates(),
    )


def test_frozen_endpoints_dimensions_and_exact_thresholds():
    assert DIMENSIONS == ("symbol", "session", "spread_band", "volatility")
    assert (MIN_OVERALL_RESULTS, MIN_OVERALL_DECISIONS) == (100, 30)
    assert (MIN_CELL_RESULTS, MIN_CELL_DECISIONS) == (30, 10)
    assert spread_band(0.099999) == "LOW"
    assert spread_band(0.10) == "NORMAL"
    assert spread_band(0.249999) == "NORMAL"
    assert spread_band(0.25) == "HIGH"
    assert spread_band(float("nan")) == "MISSING_INVALID"
    assert spread_band(-0.1) == "MISSING_INVALID"


def test_three_current_components_strict_join_stale_exclusion_and_reorder_invariance():
    evidence = _population(reliable=False)
    results, contexts, traces = map(list, evidence)
    results += [_result("COR-900", 0, 99.0, epoch="LEGACY"), _result("COR-901", 0, 99.0)]
    contexts.append(_context("COR-900", epoch="LEGACY"))
    traces.append(_trace("COR-900", epoch="LEGACY"))
    report = run_x6(results, contexts, traces)
    reversed_report = run_x6(list(reversed(results)), list(reversed(contexts)), list(reversed(traces)))
    assert report["status"] == "COMPLETE"
    assert report["fingerprint"] == reversed_report["fingerprint"]
    assert report["fingerprint"]["sources"] == [
        "decision_trace_v1", "execution_context", "execution_results_v1",
    ]
    accounting = report["overall"]["evidence_accounting"]
    assert all(item["stale_records_excluded"] == 1 for item in accounting["components"].values())
    assert accounting["join_and_identity_exclusions"]["missing_context"] == 1


def test_absolute_measured_slippage_tristate_failure_and_no_h4_fallback():
    results, contexts, traces = _population(reliable=False)
    results[0]["slippage"] = -0.5
    results[1]["slippage_semantic"] = "derived_compatibility"
    results[0]["result_ok"] = "false"
    traces[0] = _trace("COR-0", include_regime=False, h4="VOLATILE")
    report = build_x6_report(results, contexts, traces)
    overall = report["overall"]
    assert overall["primary_endpoint"]["absolute_value_used"] is True
    assert overall["primary_endpoint"]["reconstructed_slippage_used"] is False
    assert overall["analytical_population"]["measured_slippage_eligible_results"] == 119
    assert overall["analytical_population"]["unknown_execution_status_results"] == 41
    volatility = overall["condition_dimensions"]["volatility"]
    assert volatility["missing_or_invalid_unclassified_results"] == 3
    assert "EXTREME" not in volatility["cells"]


def test_fanout_cluster_weighting_wald_pairwise_holm_and_supported_direction():
    report = build_x6_report(*_population(reliable=True))
    assert report["status"] == "COMPLETE"
    overall = report["overall"]
    assert overall["analytical_population"]["governed_matched_account_results"] == 120
    assert overall["analytical_population"]["distinct_correlation_id_decisions"] == 40
    assert set(overall["condition_dimensions"]) == set(DIMENSIONS)
    symbol = overall["condition_dimensions"]["symbol"]
    assert symbol["global_test"]["degrees_of_freedom"] == 1
    assert symbol["global_test"]["p_value"] <= 0.05
    weights = symbol["model"]["cluster_total_weights"]
    assert weights and all(math.isclose(value, 1.0) for value in weights.values())
    assert symbol["model"]["naive_row_independent_covariance_used"] is False
    pair = symbol["pairwise_followups"]
    assert pair["multiplicity"] == "one Holm step-down family within this dimension only"
    assert pair["results"][0]["holm_adjusted_p_value"] <= 0.05
    assert pair["results"][0]["supported_higher_slippage_cell"] == "GBPUSD"


def test_valid_null_completes_and_invalid_spread_bucket_is_never_compared():
    results, contexts, traces = _population(reliable=False)
    for context in contexts[::2]:
        context["market_access"]["spread_atr_ratio"] = None
    report = build_x6_report(results, contexts, traces)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["classification"] == "NO_RELIABLE_EXECUTION_QUALITY_DIFFERENCE"
    spread = report["overall"]["condition_dimensions"]["spread_band"]
    assert spread["cells"]["MISSING_INVALID"]["measured_slippage_eligible_n"] == 60
    assert "MISSING_INVALID" not in spread["sufficient_primary_cells"]
    assert spread["dimension_state"] == "INSUFFICIENT"


def test_overall_and_cell_gates_fail_closed_and_provenance_rejects_fabrication():
    insufficient = build_x6_report(*_population(decisions=33, accounts=3))
    assert insufficient["overall"]["n"] == 99
    assert insufficient["status"] == "INSUFFICIENT_DATA"
    assert all(
        value["dimension_state"] == "INSUFFICIENT"
        for value in insufficient["overall"]["condition_dimensions"].values()
    )
    evidence = build_governed_execution_evidence(*_population(reliable=False), require_decision_trace=True)
    fabricated = deepcopy(evidence.observations[0])
    object.__setattr__(fabricated, "cluster_id", "FABRICATED")
    with pytest.raises(ValueError, match="not part of the governed"):
        attest_execution_analytical_subset(evidence, [fabricated])


def test_ownership_readiness_registry_and_structural_completion(tmp_path):
    evidence = _population(reliable=False)
    assert canonical_report_owner(REPORT_FILENAME) == "X6"
    relabelled = build_x6_report(*evidence)
    relabelled["question_id"] = "X3"
    assert resolve_report_ownership(REPORT_FILENAME, "X3", report_metadata=relabelled).allowed is False
    assert _state(tmp_path, None, evidence).readiness_status == ReadinessStatus.BLOCKED
    small = _population(decisions=10, accounts=3)
    assert _state(tmp_path, build_x6_report(*small), small).readiness_status == ReadinessStatus.WAITING_DATA
    assert _state(tmp_path, build_x6_report(*evidence), evidence).readiness_status == ReadinessStatus.COMPLETE
    assert REGISTRY_BY_ID["X6"].runner_module == "research_engine.experiments.execution_stability"
    assert REGISTRY_BY_ID["X6"].runner_function == "run_x6"
    assert REGISTRY_BY_ID["X6"].report_filename == REPORT_FILENAME
    assert MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert X6_HD08_ADJUDICATED_CONTRACT["status"] == "ADJUDICATED"
    assert operational_baseline() == (48, 22)
    assert WAVE_A_NO_RUNNER_TARGETS == {"L6", "G1", "G2", "G3"}
    assert len(REGISTRY) == len({question.id for question in REGISTRY}) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    assert {item.definition_version for item in build_definitions_from_registry(REGISTRY).values()} == {1}
