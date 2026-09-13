"""Focused contract proofs for the P0 non-shadow evidence boundary."""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane.evidence_resolver import (
    EvidenceSnapshot,
    join_execution_context_results,
    normalise_evidence_record,
    resolve_question_evidence,
)
from research_engine.data_quality.classifier import DataEpoch
from research_engine.registry.research_question_registry import (
    EXEC1,
    MGMT1,
    MGMT2,
    PROT1,
    REGISTRY,
    X1,
    X3,
    X5,
)


def _truth(index: int = 1, r: float = 0.5) -> dict:
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": f"trade-{index}",
            "correlation_id": f"corr-{index}",
            "canonical_opportunity_id": f"opp-{index}",
            "account_id": "account-a",
            "symbol": "EURUSD",
        },
        "outcome": {"r_multiple_realised": r},
    }


def _context(correlation_id: str = "corr-1", opportunity_id: str = "opp-1") -> dict:
    return {
        "schema_version": "execution_context_v1",
        "correlation_id": correlation_id,
        "canonical_opportunity_id": opportunity_id,
        "symbol": "EURUSD",
        "market_access": {"session_state": "LONDON"},
    }


def _result(
    correlation_id: str = "corr-1",
    account_id: str = "account-a",
    opportunity_id: str = "opp-1",
) -> dict:
    return {
        "schema_version": "execution_results_v1",
        "correlation_id": correlation_id,
        "canonical_opportunity_id": opportunity_id,
        "account_id": account_id,
        "symbol": "EURUSD",
        "result_ok": True,
        "retcode": 10009,
        "slippage": 0.0001,
        "slippage_semantic": "measured_execution_slippage",
    }


def _action(index: int = 1, action_type: str = "SLTP_MODIFY") -> dict:
    return {
        "schema_version": "management_actions_v1",
        "management_action_id": f"action-{index}",
        "trade_id": f"trade-{index}",
        "correlation_id": f"corr-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "account_id": "account-a",
        "action_type": action_type,
        "action_reason": "trailing stop",
    }


def test_canonical_trade_truth_v1_is_current_eligible():
    dataset = EvidenceSnapshot(datasets={"trade_truth": [_truth()]}).get("trade_truth")
    assert len(dataset.current_records) == 1
    assert dataset.transitional_count == dataset.legacy_count == 0


@pytest.mark.parametrize(
    "record",
    [
        {"trade_id": "legacy", "correlation_id": "corr"},
        {
            "schema_version": "trade_truth_v1",
            "data_epoch": "CURRENT",
            "trade_id": "flat",
            "correlation_id": "corr",
            "r_multiple_realised": 1.0,
        },
    ],
)
def test_noncanonical_trade_truth_is_not_silently_current(record):
    dataset = EvidenceSnapshot(datasets={"trade_truth": [record]}).get("trade_truth")
    assert dataset.current_records == []


def test_trade_truth_normalisation_preserves_nested_canonical_facts():
    raw = _truth(r=-0.75)
    row = normalise_evidence_record(raw, "trade_truth")
    assert row["identity"] == raw["identity"]
    assert row["outcome"] == raw["outcome"]
    assert row["trade_id"] == "trade-1"
    assert row["correlation_id"] == "corr-1"
    assert row["canonical_opportunity_id"] == "opp-1"
    assert row["account_id"] == "account-a"
    assert row["r_multiple_realised"] == -0.75


def test_execution_join_one_context_to_one_result():
    joined = join_execution_context_results([_result()], [_context()])
    assert len(joined["matched"]) == 1
    assert joined["ambiguous"] == 0


def test_execution_join_one_context_to_multiple_account_results():
    joined = join_execution_context_results(
        [_result(account_id="account-a"), _result(account_id="account-b")],
        [_context()],
    )
    assert len(joined["matched"]) == 2
    assert {pair["result"]["account_id"] for pair in joined["matched"]} == {
        "account-a", "account-b",
    }


def test_execution_join_does_not_match_unrelated_correlations():
    joined = join_execution_context_results([_result("corr-result")], [_context("corr-context")])
    assert joined["matched"] == []
    assert joined["results_without_context"] == 1


@pytest.mark.parametrize(
    "results,contexts",
    [
        ([_result(account_id="account-a"), _result(account_id="account-a")], [_context()]),
        ([_result(opportunity_id="opp-wrong")], [_context()]),
        ([_result()], [_context(), deepcopy(_context())]),
    ],
)
def test_execution_join_fails_closed_on_inconsistent_or_ambiguous_lineage(results, contexts):
    joined = join_execution_context_results(results, contexts)
    assert joined["matched"] == []
    assert joined["ambiguous"] >= 1


def test_producer_measured_slippage_keeps_provenance():
    row = normalise_evidence_record(_result(), "execution_results_v1")
    assert row["slippage"] == 0.0001
    assert row["slippage_semantic"] == "measured_execution_slippage"
    assert row["slippage_provenance"] == "producer_measured"


def test_derived_slippage_is_explicitly_compatibility_only():
    raw = _result()
    raw.update({
        "slippage": None,
        "slippage_semantic": "unknown",
        "request": {"entry_reference": 1.1000},
        "fill": {"price": 1.1003},
    })
    row = normalise_evidence_record(raw, "execution_results_v1")
    assert row["slippage_semantic"] == "unknown"
    assert row["slippage_provenance"] == "derived_compatibility"
    assert row["derived_compatibility_slippage"] == pytest.approx(0.0003)
    assert row["slippage"] is None


def test_unknown_slippage_is_not_promoted_to_measured():
    raw = _result()
    raw.update({"slippage": None, "slippage_semantic": "unknown"})
    row = normalise_evidence_record(raw, "execution_results_v1")
    assert row["slippage_provenance"] == "unknown"
    assert row["slippage"] is None


def test_exec1_registry_matches_primary_runner_ownership():
    assert tuple(source.value for source in EXEC1.data_sources) == ("execution_results_v1",)
    assert EXEC1.required_fields == ("result_ok", "retcode")
    assert any(rule.field == "sample_size" and rule.threshold == 30 for rule in EXEC1.validation_rules)


def test_prot1_registry_has_no_false_execution_join():
    assert tuple(source.value for source in PROT1.data_sources) == ("protection_audit_v1",)
    assert PROT1.required_fields == ("correlation_id", "protection_status")
    assert any(rule.field == "sample_size" and rule.threshold == 30 for rule in PROT1.validation_rules)


def test_x5_runner_consumes_nested_trade_truth(monkeypatch, tmp_path):
    import research_engine.experiments.execution_protection_research as module

    monkeypatch.chdir(tmp_path)
    decisions = [
        {"canonical_opportunity_id": f"opp-{i}", "ev": 0.5, "symbol": "EURUSD"}
        for i in range(30)
    ]
    truths = [_truth(i, r=0.25) for i in range(30)]
    monkeypatch.setattr(module, "_load_decision_trace", lambda: decisions)
    monkeypatch.setattr(module, "_load_trade_truth", lambda: truths)
    report = module.run_x5()
    assert report["status"] == "COMPLETE"
    assert report["overall"]["n"] == 30


def test_management_questions_obtain_canonical_outcome_joins():
    actions = [_action(i) for i in range(1, 20)]
    truths = [_truth(i) for i in range(1, 20)]
    snapshot = EvidenceSnapshot(datasets={
        "management_actions": actions,
        "trade_truth": truths,
    })
    mgmt1 = resolve_question_evidence(MGMT1, snapshot)
    mgmt2 = resolve_question_evidence(MGMT2, snapshot)
    assert mgmt1.usable_count == 19
    assert mgmt2.usable_count == 19
    assert mgmt2.metrics["matched_management_outcomes"] == 19


def test_management_conflicting_identity_fails_closed():
    action = _action()
    action["canonical_opportunity_id"] = "conflicting-opportunity"
    resolution = resolve_question_evidence(
        MGMT2,
        EvidenceSnapshot(datasets={"management_actions": [action], "trade_truth": [_truth()]}),
    )
    assert resolution.usable_count == 0
    assert resolution.excluded_count >= 1


def test_sample_gates_are_not_weakened():
    thresholds = {
        X1.id: 30,
        X3.id: 30,
        X5.id: 30,
        EXEC1.id: 30,
        PROT1.id: 30,
        MGMT1.id: 30,
    }
    for question in (X1, X3, X5, EXEC1, PROT1, MGMT1):
        assert any(
            rule.field == "sample_size" and rule.operator == ">="
            and rule.threshold == thresholds[question.id]
            for rule in question.validation_rules
        )
    assert any(
        rule.field == "max_action_type_sample_size" and rule.threshold == 15
        for rule in MGMT2.validation_rules
    )


def test_canonical_registry_still_builds_all_70_questions():
    assert len(REGISTRY) == 70
