"""Focused governed-provenance contract for Repair 1B.4 targets."""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments import (
    execution_protection_research as execution,
    management_research as management,
    risk_research as risk,
    shadow_validation,
)


def _result(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "execution_results_v1",
        "epoch": epoch,
        "correlation_id": f"corr-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "entity_id": f"entity-{index}",
        "account_id": "account-1",
        "symbol": "EURUSD",
        "result_ok": index % 5 != 0,
        "retcode": 0 if index % 5 else 10006,
        "fill_price": 1.1,
        "slippage": 0.1 + index / 1000,
        "slippage_semantic": "measured_execution_slippage",
    }


def _context(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "execution_context_v1",
        "epoch": epoch,
        "correlation_id": f"corr-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "symbol": "EURUSD",
        "market_access": {
            "session_state": "LONDON", "spread": 1.2,
            "spread_atr_ratio": 0.05 + index / 1000,
        },
        "infrastructure": {"latency_ms": 5.0, "feed_state": "OK"},
        "risk_environment": {"drawdown_pct": 1.0, "open_positions": 1},
    }


def _attempt(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "execution_attempts_v1",
        "epoch": epoch,
        "attempt_id": f"attempt-{index}",
        "correlation_id": f"corr-{index}",
        "trade_id": f"trade-{index}",
        "symbol": "EURUSD",
        "action_type": "ORDER_PLACE",
        "attempt_number": 1,
        "broker_result": {"ok": index % 5 != 0, "retcode": 0},
    }


def _protection(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "protection_audit_v1",
        "epoch": epoch,
        "correlation_id": f"corr-{index}",
        "symbol": "EURUSD",
        "position_ticket": index,
        "requested_sl": 1.09,
        "requested_tp": 1.12,
        "broker_confirmed_sl": 1.09,
        "broker_confirmed_tp": 1.12,
        "protection_status": "PROTECTED",
        "protection_failure_reason": "",
        "correction_attempted": False,
        "correction_success": False,
    }


def _trace(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "decision_trace_v1", "data_epoch": epoch,
        "correlation_id": f"corr-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "decision_id": f"decision-{index}",
        "entity_id": f"entity-{index}",
        "symbol": "EURUSD", "action": "EXECUTE",
        "v10_market_state": {"regime": {"volatility_state": "NORMAL"}},
    }


def _risk(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "risk_deviation_v1",
        "epoch": epoch,
        "trade_id": f"trade-{index}",
        "correlation_id": f"corr-{index}",
        "symbol": "EURUSD",
        "planned_risk_R": -1.0,
        "actual_risk_R": -1.0,
        "risk_deviation": 1.0,
        "risk_classification": "NORMAL",
        "semantic_stage": "post_outcome_analysis",
    }


def _action(index: int, *, epoch: str = "CURRENT", matched: bool = True) -> dict:
    suffix = index if matched else index + 1000
    return {
        "schema_version": "management_actions_v1",
        "epoch": epoch,
        "management_action_id": f"action-{suffix}",
        "trade_id": f"trade-{suffix}",
        "correlation_id": f"corr-{suffix}",
        "canonical_opportunity_id": f"opp-{suffix}",
        "symbol": "EURUSD",
        "action_type": "SLTP_MODIFY",
        "action_reason": "trailing_stop",
    }


def _truth(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "trade_truth_v1",
        "epoch": epoch,
        "identity": {
            "trade_id": f"trade-{index}",
            "correlation_id": f"corr-{index}",
            "canonical_opportunity_id": f"opp-{index}",
            "symbol": "EURUSD",
        },
        "outcome": {"r_multiple_realised": 1.0 if index % 2 else -0.5},
        "exit": {"exit_reason": "take_profit"},
    }


def _shadow(index: int, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "epoch": epoch,
        "identity": {
            "trade_id": f"nshadow_{index:016x}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "canonical_opportunity_id": f"opp-{index}",
            "entity_id": f"entity-{index}",
            "symbol": "EURUSD",
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
            "evaluated_horizon": "SCALP",
            "trade_horizon": "SCALP",
        },
        "decision_snapshot": {"strategy": "REVERSAL", "h4_regime": "TRENDING"},
        "simulated_outcome": {"pnl_r_multiple": 1.0 if index % 2 else -0.25},
    }


def _structured(report: dict) -> dict:
    return report["fingerprint"]["evidence_provenance"]


def _assert_current(report: dict) -> None:
    assert validate_evidence_provenance(_structured(report))[:2] == (True, CURRENT)
    assert report["fingerprint"]["epoch"] == CURRENT


def _patch_execution(
    monkeypatch, *, results=None, contexts=None, attempts=None, protections=None, traces=None,
):
    monkeypatch.setattr(execution, "_load_results", lambda: results or [])
    monkeypatch.setattr(execution, "_load_context", lambda: contexts or [])
    monkeypatch.setattr(execution, "_load_attempts", lambda: attempts or [])
    monkeypatch.setattr(execution, "_load_protection", lambda: protections or [])
    monkeypatch.setattr(execution, "_load_decision_trace", lambda: traces or [])


def test_x1_truthfully_proves_results_and_context_and_excludes_stale(monkeypatch):
    results = [_result(index) for index in range(35)]
    contexts = [_context(index) for index in range(35)]
    stale = _result(999, epoch="LEGACY")
    _patch_execution(monkeypatch, results=[*results, stale], contexts=contexts)

    report = execution.run_x1()
    _assert_current(report)
    components = {item["source"]: item for item in _structured(report)["components"]}
    assert set(components) == {"execution_results_v1", "execution_context"}
    assert components["execution_results_v1"]["digest"] == evidence_digest(results)
    assert components["execution_results_v1"]["records_excluded"] == 1
    assert report["overall"]["measured_slippage_count"] == 35


def test_x2_truthfully_proves_results_and_attempts(monkeypatch):
    results = [_result(index) for index in range(35)]
    attempts = [_attempt(index) for index in range(35)]
    _patch_execution(monkeypatch, results=results, attempts=attempts)

    report = execution.run_x2()
    _assert_current(report)
    components = {item["source"]: item for item in _structured(report)["components"]}
    assert set(components) == {"execution_results_v1", "execution_attempts_v1"}
    assert report["overall"]["results_total"] == 35
    assert report["overall"]["attempts_total"] == 35


def test_execution_required_stale_side_fails_closed(monkeypatch):
    _patch_execution(
        monkeypatch,
        results=[_result(index) for index in range(35)],
        contexts=[_context(index, epoch="LEGACY") for index in range(35)],
    )
    report = execution.run_x1()
    assert report["status"] == "COMPLETE"
    assert report["fingerprint"]["epoch"] == "UNVERIFIED"


def test_prot1_names_only_protection_audit_and_filters_stale(monkeypatch):
    current = [_protection(index) for index in range(35)]
    stale = _protection(999, epoch="LEGACY")
    _patch_execution(monkeypatch, protections=[*current, stale])

    report = execution.run_prot1()
    _assert_current(report)
    component = _structured(report)["components"][0]
    assert component["source"] == "protection_audit_v1"
    assert component["source"] != "execution_results_v1"
    assert component["records_excluded"] == 1
    assert component["digest"] == evidence_digest(current)


def test_risk1_proves_actual_risk_projection_and_preserves_calculation(monkeypatch):
    current = [_risk(index) for index in range(35)]
    stale = _risk(999, epoch="LEGACY")
    monkeypatch.setattr(risk, "_load_risk_deviation", lambda: [*current, stale])

    report = risk.run_risk1()
    _assert_current(report)
    component = _structured(report)["components"][0]
    assert component["source"] == "risk_deviation_v1"
    assert component["records_excluded"] == 1
    assert report["overall"]["classification_distribution"] == {"NORMAL": 35}


@pytest.mark.parametrize("runner", (management.run_mgmt1, management.run_mgmt2))
def test_management_targets_prove_both_sources_without_shadow(monkeypatch, runner):
    actions = [_action(index) for index in range(35)]
    truths = [_truth(index) for index in range(35)]
    monkeypatch.setattr(management, "_load_actions", lambda: actions)
    monkeypatch.setattr(management, "_load_outcomes", lambda: truths)

    report = runner()
    _assert_current(report)
    sources = {item["source"] for item in _structured(report)["components"]}
    assert sources == {"management_actions_v1", "trade_truth_v1"}
    assert "shadow_trades" not in sources


def test_management_stale_side_and_unmatched_actions_are_fail_closed_or_accounted(monkeypatch):
    actions = [_action(index) for index in range(35)] + [_action(1, matched=False)]
    truths = [_truth(index) for index in range(35)]
    monkeypatch.setattr(management, "_load_actions", lambda: actions)
    monkeypatch.setattr(management, "_load_outcomes", lambda: truths)
    report = management.run_mgmt2()
    _assert_current(report)
    assert report["overall"]["unmatched_actions_excluded"] == 1
    assert report["overall"]["sample_size"] == 35

    stale_truths = deepcopy(truths)
    for item in stale_truths:
        item["epoch"] = "LEGACY"
    monkeypatch.setattr(management, "_load_outcomes", lambda: stale_truths)
    stale = management.run_mgmt2()
    assert stale["status"] == "INSUFFICIENT_DATA"
    assert stale["fingerprint"]["epoch"] == "UNVERIFIED"


class _TruthSource:
    def __init__(self, truths: list[dict]):
        self.truths = truths

    def read_dataset(self, dataset: str, **kwargs):
        assert dataset == "trade_truth"
        return self.truths


def _patch_x4(monkeypatch, shadows: list[dict], truths: list[dict]):
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: shadows,
    )
    monkeypatch.setattr(
        "research_engine.data_access.s3_source.get_default_source",
        lambda: _TruthSource(truths),
    )


def test_x4_uses_canonical_shadow_and_trade_truth_join(monkeypatch, tmp_path):
    shadows = [_shadow(index) for index in range(200)]
    truths = [_truth(index) for index in range(200)]
    monkeypatch.chdir(tmp_path)
    _patch_x4(monkeypatch, shadows, truths)

    report = shadow_validation.run()
    _assert_current(report)
    sources = {item["source"] for item in _structured(report)["components"]}
    assert sources == {"shadow_trades", "trade_truth_v1"}
    assert report["status"] == "COMPLETE"
    assert report["overall"]["matched_trades"] == 200
    validity, reason = resolve_report_validity(
        "q16_shadow_validation.json",
        report,
        expected_question_id="X4",
        accepted_question_ids=("Q16",),
    )
    assert validity == ReportValidity.VALID_CURRENT, reason

    stale_truths = deepcopy(truths)
    for item in stale_truths:
        item["epoch"] = "LEGACY"
    _patch_x4(monkeypatch, shadows, stale_truths)
    stale = shadow_validation.run()
    assert stale["status"] == "BLOCKED"
    assert stale["fingerprint"]["epoch"] == "UNVERIFIED"


def test_all_seven_source_mappings_and_non_target_safety(monkeypatch, tmp_path):
    results = [_result(index) for index in range(35)]
    contexts = [_context(index) for index in range(35)]
    attempts = [_attempt(index) for index in range(35)]
    protections = [_protection(index) for index in range(35)]
    _patch_execution(
        monkeypatch, results=results, contexts=contexts,
        attempts=attempts, protections=protections,
        traces=[_trace(index) for index in range(35)],
    )
    monkeypatch.setattr(risk, "_load_risk_deviation", lambda: [_risk(i) for i in range(35)])
    monkeypatch.setattr(management, "_load_actions", lambda: [_action(i) for i in range(35)])
    monkeypatch.setattr(management, "_load_outcomes", lambda: [_truth(i) for i in range(35)])
    monkeypatch.chdir(tmp_path)
    _patch_x4(monkeypatch, [_shadow(i) for i in range(35)], [_truth(i) for i in range(35)])

    reports = {
        "X1": execution.run_x1(),
        "X2": execution.run_x2(),
        "X4": shadow_validation.run(),
        "RISK-1": risk.run_risk1(),
        "MGMT-1": management.run_mgmt1(),
        "MGMT-2": management.run_mgmt2(),
        "PROT1": execution.run_prot1(),
    }
    for report in reports.values():
        _assert_current(report)

    # X3 and EXEC1 now use their governed CURRENT execution evidence contracts.
    assert execution.run_x3()["fingerprint"]["epoch"] == "CURRENT"
    assert execution.run_exec1()["fingerprint"]["epoch"] == "CURRENT"
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID
    assert REGISTRY_BY_ID["X6"].runner_module
    for question_id in ("R1", "R2", "R3", "R4", "R5", "D1"):
        assert question_id not in reports


def test_resolver_accepts_current_target_but_scientific_gate_still_blocks(monkeypatch):
    results = [_result(index) for index in range(200)]
    contexts = [_context(index) for index in range(200)]
    _patch_execution(monkeypatch, results=results, contexts=contexts)
    report = execution.run_x1()
    validity, reason = resolve_report_validity(
        "w4_x1_slippage.json", report, expected_question_id="X1",
    )
    assert validity == ReportValidity.VALID_CURRENT, reason

    blocked = deepcopy(report)
    blocked["warnings"].append("EPOCH_WARNING: synthetic scientific blocker")
    blocked_validity, _ = resolve_report_validity(
        "w4_x1_slippage.json", blocked, expected_question_id="X1",
    )
    assert blocked_validity == ReportValidity.INVALIDATED
