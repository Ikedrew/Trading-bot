"""Focused governance tests for frozen HD04 decision-quality science."""
from __future__ import annotations

from research_engine.registry import decision_quality_adjudication as A
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY


TARGETS = ("D2", "D3", "D4", "D5", "X5")


def test_exact_targets_and_one_opportunity_grain_are_frozen():
    assert A.HD04_TARGETS == TARGETS
    assert HUMAN_SEMANTIC_DECISIONS["HD04"].affected_question_ids == TARGETS
    assert "one statistical observation" in A.STATISTICAL_UNIT
    assert "(entity_id, canonical_opportunity_id)" in A.STATISTICAL_UNIT
    for forbidden_fanout in ("Account fanout", "broker executions", "horizons"):
        assert forbidden_fanout in A.STATISTICAL_UNIT
    assert "nonidentical duplicates" in A.IDENTITY_FAILURE_RULE
    assert "fail closed" in A.IDENTITY_FAILURE_RULE


def test_decision_chronology_and_no_outcome_leakage_are_mandatory():
    assert A.DECISION_TIME_FIELD == "decision_trace.timestamp_utc"
    assert A.PREDICTOR_TIME_FIELD == "opportunity_assessments.assessed_at_utc"
    assert A.OUTCOME_TIME_FIELD == "shadow_trades.simulated_outcome.exit_timestamp"
    assert "assessed_at_utc <= decision_trace.timestamp_utc < simulated_outcome.exit_timestamp" in A.CHRONOLOGY_CONTRACT
    assert "filesystem time" in A.CHRONOLOGY_CONTRACT
    assert "cannot be relabelled WAITING_DATA" in A.CHRONOLOGY_CONTRACT
    for response in ("realised R", "MFE", "MAE", "exit reason", "future bars"):
        assert response in A.NO_LEAKAGE_CONTRACT
    assert "only as responses" in A.NO_LEAKAGE_CONTRACT


def test_d2_probability_authority_and_estimand_are_explicit():
    assert A.PROBABILITY_AUTHORITY["value"] == "opportunity_assessments.p_success"
    assert A.PROBABILITY_AUTHORITY["version"] == "opportunity_assessments.probability_model_version"
    assert A.PROBABILITY_AUTHORITY["cross_check"] == "decision_trace.p_success"
    assert A.PROBABILITY_AUTHORITY["domain"] == "finite [0,1]"
    assert "pnl_r_multiple > 0" in A.PROBABILITY_AUTHORITY["semantic"]
    assert "Generic confidence" in A.PROBABILITY_AUTHORITY["semantic"]
    assert "intercept=0" in A.QUESTION_CONTRACTS["D2"]["primary_estimand"]
    assert "slope=1" in A.QUESTION_CONTRACTS["D2"]["primary_estimand"]


def test_d3_and_x5_share_one_non_outcome_predicted_ev_authority():
    assert A.PREDICTED_EV_AUTHORITY["version"] == "predicted_ev_r_v1"
    assert A.PREDICTED_EV_AUTHORITY["persisted_value"] == "opportunity_assessments.ev"
    assert "ev / opportunity_assessments.ev_risk" in A.PREDICTED_EV_AUTHORITY["r_units"]
    assert "nor substituted from score/confidence" in A.PREDICTED_EV_AUTHORITY["semantic"]
    assert "realised R" in A.PREDICTED_EV_AUTHORITY["semantic"]
    assert "EV_PASS versus EV_FILTER_REJECTED" in A.QUESTION_CONTRACTS["D3"]["treatment"]
    assert "Spearman association" in A.QUESTION_CONTRACTS["X5"]["primary_estimand"]


def test_d4_score_authority_and_estimand_are_unique():
    assert A.SCORE_AUTHORITY["field"] == "decision_trace.score_strategy"
    assert A.SCORE_AUTHORITY["production_threshold"] == 0.35
    assert "higher means" in A.SCORE_AUTHORITY["direction"]
    for forbidden in ("score_neutral", "confidence", "p_success", "EV"):
        assert forbidden in A.SCORE_AUTHORITY["semantic"]
    assert "Spearman association" in A.QUESTION_CONTRACTS["D4"]["primary_estimand"]
    assert "cannot change it" in A.SCORE_AUTHORITY["semantic"]


def test_d3_treatment_is_not_generic_rejection():
    ev_rejection = A.ACTION_TAXONOMY["EV_FILTER_REJECTED"]
    assert "terminal_stage=ev_policy" in ev_rejection
    assert "NEGATIVE_EXPECTED_VALUE" in ev_rejection
    assert "ev_gate_enabled=true" in ev_rejection
    assert "ev_rejection_bypassed=false" in ev_rejection
    assert "absence of execution alone" in A.MULTI_REASON_RULE.lower()


def test_d5_rejection_and_counterfactual_authorities_are_explicit():
    assert "ACCEPTED_DECISION" in A.ACTION_TAXONOMY
    assert "RISK_PLAN_BLOCKED" in A.ACTION_TAXONOMY
    assert "EXECUTION_BLOCKED" in A.ACTION_TAXONOMY
    assert "separately governed execution-result authority" in A.ACTION_TAXONOMY["EXECUTION_BLOCKED"]
    d5 = A.QUESTION_CONTRACTS["D5"]
    assert "counterfactual shadow outcomes" in d5["population"]
    assert "ACCEPTED_DECISION" in d5["comparator"]
    assert "rejected minus accepted" in d5["primary_estimand"]
    assert "contradictory reasons" in A.MULTI_REASON_RULE


def test_shared_outcome_is_current_primary_horizon_and_never_zero_filled():
    authority = A.SHARED_OUTCOME_AUTHORITY
    assert "CURRENT" in authority
    assert "PRIMARY_HORIZON_SIMULATION" in authority
    assert "simulated_outcome.pnl_r_multiple" in authority
    assert "Alternative horizons are excluded rather than averaged" in authority
    assert "Missing outcome is missing, never 0" in authority
    assert "account" in authority.lower()


def test_all_five_estimands_and_sufficiency_rules_are_distinct_and_explicit():
    assert tuple(A.QUESTION_CONTRACTS) == TARGETS
    estimands = [A.QUESTION_CONTRACTS[qid]["primary_estimand"] for qid in TARGETS]
    assert len(set(estimands)) == 5
    for qid in TARGETS:
        contract = A.QUESTION_CONTRACTS[qid]
        assert "null" in contract
        assert "sufficiency" in contract
        assert "completion" in contract
        assert "N>=100" in contract["sufficiency"]
        assert "may COMPLETE" in contract["completion"]
    assert A.COMMON_INFERENCE["alpha"] == 0.05
    assert A.COMMON_INFERENCE["confidence_level"] == 0.95
    assert "deterministic" in A.COMMON_INFERENCE["resampling"]
    assert "No adjustment across" in A.COMMON_INFERENCE["multiplicity"]
    assert "Holm" in A.COMMON_INFERENCE["multiplicity"]


def test_readiness_freezes_waiting_data_versus_blocked():
    semantics = A.READINESS_SEMANTICS
    assert "WAITING_DATA applies only" in semantics
    assert "only sample quantity" in semantics
    for authority in ("identity", "chronology", "predictor/version", "treatment/action"):
        assert authority in semantics
    assert "BLOCKED applies" in semantics
    assert "VALID_CURRENT report" in semantics


def test_historical_authority_is_truthful_and_does_not_reconstruct():
    assert set(A.HISTORICAL_AUTHORITY) == set(TARGETS)
    for status in A.HISTORICAL_AUTHORITY.values():
        assert "AUTHORITY_APPEARS_AVAILABLE" in status
        assert "actual current coverage remains to be measured" in status.lower()
    assert "Bare decision_trace history" in A.HISTORICAL_AUTHORITY["D2"]
    assert "absent execution" in A.HISTORICAL_AUTHORITY["D5"]


def test_unique_future_runner_and_report_ownership_is_preserved():
    questions = {question.id: question for question in REGISTRY}
    reports = []
    for qid, (runner, report) in A.FUTURE_IDENTITIES.items():
        question = questions[qid]
        assert runner == f"{question.runner_module}.{question.runner_function}"
        assert report == question.report_filename
        reports.append(report)
    assert len(reports) == len(set(reports)) == 5
    assert "may complete another question" in A.QUESTION_SEPARATION


def test_hd04_remains_adjudicated_after_hd05_and_baseline_unchanged():
    hd04 = HUMAN_SEMANTIC_DECISIONS["HD04"]
    hd05 = HUMAN_SEMANTIC_DECISIONS["HD05"]
    assert "ADJUDICATED" in hd04.exact_decision
    assert not hd04.implementation_blocked_until_decision
    assert hd05.affected_question_ids == ("P1",)
    assert "ADJUDICATED" in hd05.exact_decision
    assert not hd05.implementation_blocked_until_decision
    assert operational_baseline() == (63, 7)
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == frozenset({
        "EX5", "EX6", "EX8", "L2", "L3", "L6", "L7",
    })


def test_adjudication_module_has_no_runtime_or_production_side_effects():
    source = __import__("inspect").getsource(A)
    for forbidden in ("boto3", "subprocess", "core.config", "persist_", "write_text", "open("):
        assert forbidden not in source
    assert "governance authority only" in A.__doc__
