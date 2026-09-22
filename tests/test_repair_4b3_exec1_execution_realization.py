"""Focused Repair 4B.3 tests for governed EXEC1 realization analysis."""
from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from research_engine.control_plane.models import ReadinessStatus
from research_engine.control_plane.report_ownership import canonical_report_owner, resolve_report_ownership
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.exec1_execution_realization import (
    REPORT_FILENAME,
    build_exec1_report,
    valid_spread_atr_ratio,
)
from research_engine.experiments.execution_protection_research import run_exec1
from research_engine.registry import BASELINE_QUESTION_IDS, REGISTRY, REGISTRY_BY_ID
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER, operational_baseline
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_TARGETS, X6_HD08_ADJUDICATED_CONTRACT


class _EmptyCandidates:
    def list_all(self):
        return []


def _result(correlation: str, account: str, result_ok=True, *, epoch="CURRENT") -> dict:
    index = int(correlation.split("-")[-1]) * 10 + int(account.split("-")[-1]) + 1
    return {
        "schema_version": "execution_results_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD", "account_id": account, "broker": "Broker",
        "broker_server": "Server", "order_ticket": index, "position_ticket": index,
        "result_ok": result_ok, "retcode": 10009 if result_ok is True else 10030,
        "comment": "filled" if result_ok is True else "rejected",
        "slippage": 999.0, "slippage_semantic": "derived_compatibility",
    }


def _context(correlation: str, exposure, *, raw_spread=999.0, epoch="CURRENT") -> dict:
    return {
        "schema_version": "execution_context_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD",
        "market_access": {
            "session_state": "LONDON", "spread": raw_spread,
            "spread_atr_ratio": exposure,
        },
    }


def _trace(correlation: str, action="EXECUTE", *, epoch="CURRENT") -> dict:
    return {
        "schema_version": "decision_trace_v1", "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}", "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD", "action": action,
        "v10_market_state": {"regime": {"volatility_state": "NORMAL"}},
    }


def _population(mode="adverse", *, decisions=20, accounts=2):
    results, contexts, traces = [], [], []
    for index in range(decisions):
        correlation = f"COR-{index}"
        exposure = 0.05 + index * 0.025
        contexts.append(_context(correlation, exposure))
        traces.append(_trace(correlation))
        for account_index in range(accounts):
            if mode == "adverse":
                result_ok = index < decisions // 2
            elif mode == "favourable":
                result_ok = index >= decisions // 2
            else:
                result_ok = account_index % 2 == 0
            results.append(_result(correlation, f"ACCOUNT-{account_index}", result_ok))
    return results, contexts, traces


def _report(mode="adverse", **kwargs):
    return build_exec1_report(*_population(mode, **kwargs))


def _state(tmp_path, report, evidence):
    if report is not None:
        (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    results, contexts, traces = evidence
    return build_question_state(
        "EXEC1", reports_dir=tmp_path,
        evidence_source={
            "execution_results_v1": results,
            "execution_context": contexts,
            "decision_trace": traces,
        },
        candidate_registry=_EmptyCandidates(),
    )


def test_current_three_component_evidence_strict_execute_filter_and_stale_exclusion():
    results, contexts, traces = _population("null")
    traces[0]["action"] = "NO_TRADE"
    traces[1].pop("action")
    results.append({**_result("COR-999", "ACCOUNT-0"), "data_epoch": "LEGACY"})
    contexts.append({**_context("COR-999", 0.2), "data_epoch": "LEGACY"})
    traces.append({**_trace("COR-999"), "data_epoch": "LEGACY"})
    report = build_exec1_report(results, contexts, traces)
    population = report["overall"]["otherwise_valid_population"]
    assert population["excluded_non_execute"] == 2
    assert population["excluded_missing_action"] == 2
    assert report["fingerprint"]["epoch"] == "CURRENT"
    components = report["overall"]["evidence_accounting"]["components"]
    assert all(item["stale_records_excluded"] == 1 for item in components.values())


def test_fanout_rows_remain_observations_with_one_weight_per_decision():
    report = _report("null", decisions=15, accounts=3)
    population = report["overall"]["primary_analytical_population"]
    assert population["account_result_n"] == 45
    assert population["distinct_correlation_id_decisions"] == 15
    weights = report["overall"]["primary_association"]["cluster_total_weights"]
    assert len(weights) == 15
    assert all(value == pytest.approx(1.0) for value in weights.values())


def test_status_tristate_and_descriptive_reliability_are_non_tautological():
    results, contexts, traces = _population("null")
    results[0]["result_ok"] = None
    results[1]["result_ok"] = "false"
    report = build_exec1_report(results, contexts, traces)
    descriptive = report["overall"]["descriptive_execution_reliability"]
    analytical = report["overall"]["primary_analytical_population"]
    assert descriptive["unknown_status"] == 2
    assert analytical["excluded_unknown_status"] == 2
    assert descriptive["failures"] == 19
    assert descriptive["inferential_exposure"] is False
    assert report["overall"]["measured_slippage"]["role"] == (
        "not used as the primary result_ok exposure"
    )


def test_exposure_authority_validation_and_no_raw_spread_fallback_or_bins():
    assert valid_spread_atr_ratio(0) == 0.0
    for value in (None, True, -0.1, math.nan, math.inf, -math.inf, "0.25", "bad"):
        assert valid_spread_atr_ratio(value) is None
    results, contexts, traces = _population("null")
    contexts[0]["market_access"]["spread_atr_ratio"] = None
    contexts[1]["market_access"]["spread_atr_ratio"] = -0.1
    report = build_exec1_report(results, contexts, traces)
    assert report["overall"]["primary_analytical_population"][
        "excluded_invalid_spread_atr_ratio"
    ] == 4
    association = report["overall"]["primary_association"]
    assert association["exposure"] == "execution_context.market_access.spread_atr_ratio"
    assert "no bins" in association["exposure_treatment"]
    assert 999.0 not in {
        association["beta_spread"], association["effect_per_0_10"]
    }


@pytest.mark.parametrize("mode,expected", [
    ("adverse", "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION"),
    ("favourable", "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION"),
    ("null", "NO_RELIABLE_ASSOCIATION"),
])
def test_weighted_lpm_classifications_complete(mode, expected):
    report = _report(mode)
    association = report["overall"]["primary_association"]
    assert report["status"] == "COMPLETE"
    assert report["overall"]["classification"] == expected
    assert association["model"] == "successful_execution ~ intercept + spread_atr_ratio"
    assert association["model_family"] == "cluster-weighted linear probability model"
    assert association["null"] == "beta_spread = 0"
    assert association["alpha"] == 0.05
    assert association["naive_row_independent_covariance_used"] is False
    assert association["effect_per_0_10"] == pytest.approx(
        0.1 * association["beta_spread"], abs=1e-12
    )


def test_reorder_is_deterministic_for_model_and_current_provenance():
    results, contexts, traces = _population("adverse")
    forward = build_exec1_report(results, contexts, traces)
    reverse = build_exec1_report(
        list(reversed(results)), list(reversed(contexts)), list(reversed(traces)),
    )
    assert forward["overall"]["primary_association"] == reverse["overall"]["primary_association"]
    assert forward["fingerprint"] == reverse["fingerprint"]


@pytest.mark.parametrize("mutation", ["rows", "decisions", "exposure", "all_success", "all_failure"])
def test_sufficiency_and_estimability_fail_closed(mutation):
    results, contexts, traces = _population("adverse")
    if mutation == "rows":
        results = results[:29]
    elif mutation == "decisions":
        results, contexts, traces = _population("adverse", decisions=9, accounts=4)
    elif mutation == "exposure":
        for context in contexts:
            context["market_access"]["spread_atr_ratio"] = 0.2
    elif mutation == "all_success":
        for result in results:
            result["result_ok"] = True
    else:
        for result in results:
            result["result_ok"] = False
    report = build_exec1_report(results, contexts, traces)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["classification"] == "INSUFFICIENT_EVIDENCE"


def test_exact_30_row_10_decision_boundary_is_accepted_when_estimable():
    report = _report("adverse", decisions=10, accounts=3)
    assert report["overall"]["primary_analytical_population"]["account_result_n"] == 30
    assert report["overall"]["primary_analytical_population"][
        "distinct_correlation_id_decisions"
    ] == 10
    assert report["status"] == "COMPLETE"


def test_provenance_is_exact_change_sensitive_and_secondary_is_non_blocking():
    results, contexts, traces = _population("null")
    original = build_exec1_report(results, contexts, traces)
    changed_contexts = deepcopy(contexts)
    changed_contexts[0]["market_access"]["spread_atr_ratio"] += 0.001
    changed = build_exec1_report(results, changed_contexts, traces)
    assert original["fingerprint"] != changed["fingerprint"]
    assert original["fingerprint"]["records_used"] == 80
    secondary = original["overall"]["optional_successful_execution_trade_truth_secondary"]
    assert secondary == {
        "status": "NOT_EVALUATED",
        "completion_dependency": False,
        "reason": "No secondary inferential model is frozen; failed executions receive no synthetic outcome.",
    }
    assert original["status"] == "COMPLETE"


def test_runner_ownership_and_relabelled_metadata():
    results, contexts, traces = _population("null")
    report = run_exec1(results, contexts, traces)
    assert report["question_id"] == "EXEC1"
    assert canonical_report_owner(REPORT_FILENAME) == "EXEC1"
    assert resolve_report_ownership(
        REPORT_FILENAME, "EXEC1", report_metadata=report,
    ).allowed is True
    relabelled = deepcopy(report)
    relabelled["question_id"] = "X3"
    assert resolve_report_ownership(
        REPORT_FILENAME, "EXEC1", report_metadata=relabelled,
    ).allowed is False
    assert canonical_report_owner("w4_x3_session_quality.json") == "X3"


def test_readiness_missing_insufficient_and_all_complete_results(tmp_path):
    evidence = _population("null")
    assert _state(tmp_path, None, evidence).readiness_status == ReadinessStatus.BLOCKED
    insufficient = build_exec1_report(*_population("adverse", decisions=9, accounts=3))
    assert _state(tmp_path, insufficient, evidence).readiness_status == ReadinessStatus.WAITING_DATA
    for mode in ("adverse", "favourable", "null"):
        report = _report(mode)
        assert _state(tmp_path, report, _population(mode)).readiness_status == ReadinessStatus.COMPLETE


def test_structural_and_non_target_invariants():
    assert MASTER_REPAIR_LEDGER["EXEC1"].structurally_operational
    assert MASTER_REPAIR_LEDGER["X3"].structurally_operational
    assert not MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert X6_HD08_ADJUDICATED_CONTRACT["status"] == "ADJUDICATED"
    assert not REGISTRY_BY_ID["X6"].runner_module
    assert not REGISTRY_BY_ID["X6"].runner_function
    assert operational_baseline() == (47, 23)
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert len(REGISTRY) == len({item.id for item in REGISTRY}) == 70
    assert tuple(item.id for item in REGISTRY) == BASELINE_QUESTION_IDS
    assert all(
        definition.definition_version == 1
        for definition in build_definitions_from_registry(REGISTRY).values()
    )
