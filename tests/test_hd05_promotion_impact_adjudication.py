"""Focused governance tests for frozen HD05/P1 science."""
from __future__ import annotations

import inspect

from research_engine.registry import promotion_impact_adjudication as A
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY


def test_hd05_governs_exactly_p1_and_is_adjudicated():
    assert A.HD05_TARGETS == ("P1",)
    hd05 = HUMAN_SEMANTIC_DECISIONS["HD05"]
    assert hd05.affected_question_ids == ("P1",)
    assert "ADJUDICATED" in hd05.exact_decision
    assert not hd05.implementation_blocked_until_decision


def test_eligible_promotion_requires_decision_application_and_verified_deployment():
    contract = A.ELIGIBLE_PROMOTION
    for required in (
        "COMPLETED human ACCEPT", "APPROVED_NOT_DEPLOYED", "DEPLOYED",
        "exactly one VERIFIED", "COMPLETED ApplicationService", "activated pointer",
    ):
        assert required in contract
    assert "ACCEPT/APPROVED alone" in contract
    for forbidden in ("Git commits", "report or filesystem timestamps", "candidate creation", "chronological coincidence"):
        assert forbidden in A.FORBIDDEN_PROMOTION_INFERENCES


def test_promotion_identity_is_deterministic_immutable_and_conflict_closed():
    assert A.PROMOTION_IDENTITY_FIELDS == (
        "candidate_id", "evaluation_id", "recommendation_id", "application_id",
        "operation_id", "treatment_id", "treatment_spec_sha256",
        "from_baseline_id", "from_baseline_config_hash",
        "to_baseline_id", "to_baseline_config_hash",
    )
    assert "SHA-256 of canonical JSON" in A.PROMOTION_IDENTITY
    assert "Repeated identical persistence is one promotion" in A.PROMOTION_IDENTITY
    assert "fails closed" in A.PROMOTION_IDENTITY


def test_effective_boundary_uses_governed_activation_not_convenience_time():
    assert A.EFFECTIVE_BOUNDARY_FIELD == "ApplicationService.operation.activated_pointer.activated_at"
    boundary = A.EFFECTIVE_BOUNDARY
    assert "resulting baseline as active_baseline_id" in boundary
    assert "source baseline as previous_baseline_id" in boundary
    assert "not activation time" in boundary
    for forbidden in ("report", "Git", "filesystem", "inferred first trade"):
        assert forbidden in boundary
    assert "never reconstructed" in boundary


def test_one_canonical_opportunity_and_primary_horizon_are_the_grain():
    assert "one statistical observation" in A.STATISTICAL_UNIT
    assert "(entity_id, canonical_opportunity_id)" in A.STATISTICAL_UNIT
    assert "Account/broker fanout" in A.STATISTICAL_UNIT
    assert "repeated horizons cannot enlarge N" in A.STATISTICAL_UNIT
    assert "PRIMARY_HORIZON_SIMULATION" in A.STATISTICAL_UNIT
    assert "conflicting lineage fail closed" in A.STATISTICAL_UNIT


def test_pre_post_membership_requires_baseline_identity_and_boundary():
    assert "from_baseline_id" in A.PRE_POST_MEMBERSHIP["PRE"]
    assert "to_baseline_id" in A.PRE_POST_MEMBERSHIP["POST"]
    assert "config_hash" in A.PRE_POST_MEMBERSHIP["PRE"]
    assert "config_hash" in A.PRE_POST_MEMBERSHIP["POST"]
    assert "Chronology alone never assigns baseline membership" in A.PRE_POST_MEMBERSHIP["failure"]
    assert "spanning" in A.PRE_POST_MEMBERSHIP["purge"]
    assert "No arbitrary calendar window" in A.PRE_POST_MEMBERSHIP["window"]


def test_primary_estimand_and_r_normalized_outcome_are_explicit():
    assert A.PRIMARY_ESTIMAND["name"] == "mix_standardized_post_minus_pre_mean_realised_r"
    assert "POST mean realised R minus PRE mean realised R" in A.PRIMARY_ESTIMAND["definition"]
    assert "equals 0" in A.PRIMARY_ESTIMAND["null"]
    assert "two-sided alpha 0.05" in A.PRIMARY_ESTIMAND["inference"]
    assert "95%" in A.PRIMARY_ESTIMAND["inference"]
    assert "pnl_r_multiple" in A.OUTCOME_AUTHORITY
    assert "Raw account P&L" in A.OUTCOME_AUTHORITY
    assert "never zero" in A.OUTCOME_AUTHORITY


def test_risk_changes_cannot_masquerade_as_edge():
    rule = A.RISK_NORMALISATION
    assert "opportunity-level R" in rule
    assert "never raw money P&L" in rule
    assert "separates edge from capital amplification" in rule
    assert "BLOCKS" in rule


def test_secondary_metrics_are_explicit_and_never_weighted():
    assert tuple(A.SECONDARY_METRICS) == (
        "win_rate", "drawdown_r", "opportunity_frequency", "dispersion_tail",
        "capital_risk", "execution_cost",
    )
    for contract in A.SECONDARY_METRICS.values():
        assert set(contract) == {"authority", "purpose", "role", "direction"}
    assert "no weighted promotion score" in A.NO_WEIGHTED_SCORE
    assert "no weighted" in A.NO_WEIGHTED_SCORE


def test_mix_controls_materiality_and_sparse_cell_rules_are_frozen():
    assert A.MANDATORY_MIX_CONTROLS == (
        "identity.symbol", "authoritative StrategyFamily", "evaluated_horizon",
        "decision-time H4 Regime",
    )
    assert "TV>=0.10" in A.MIX_CONTROL
    assert ">=30 PRE and >=30 POST" in A.MIX_CONTROL
    assert "common-support" in A.MIX_CONTROL
    assert "never manufactured" in A.MIX_CONTROL
    assert "Unadjusted pooling cannot support" in A.MIX_CONTROL


def test_multiple_promotions_are_separate_and_not_pseudoreplicates():
    rule = A.MULTIPLE_PROMOTIONS
    assert "separately" in rule
    assert "latest eligible non-rolled-back transition" in rule
    assert "Never blend transitions" in rule
    assert "No inferential aggregate is canonical" in rule
    assert "never treated as opportunity replicates" in rule


def test_candidate_experiment_is_not_deployed_impact():
    distinction = A.CANDIDATE_VS_PROMOTION
    assert "Candidate validation asks" in distinction
    assert "P1 starts only after" in distinction
    assert "never supplies P1's POST outcome" in distinction
    assert "degradation or no reliable" in distinction


def test_statistical_sufficiency_multiplicity_and_valid_negative_completion():
    assert A.MINIMUM_PRE_N == 100
    assert A.MINIMUM_POST_N == 100
    assert A.MINIMUM_CELL_PER_ARM == 30
    assert A.ALPHA == 0.05
    assert A.CONFIDENCE_LEVEL == 0.95
    assert "deterministic Holm" in A.STATISTICAL_GOVERNANCE
    assert "null or supported degradation" in A.STATISTICAL_GOVERNANCE
    assert A.RESULT_VOCABULARY == (
        "PROMOTION_IMPROVED", "PROMOTION_DEGRADED", "NO_RELIABLE_DIFFERENCE",
        "INSUFFICIENT_EVIDENCE", "PROMOTION_UNMEASURABLE",
    )


def test_complete_waiting_data_and_blocked_are_distinct():
    complete = A.READINESS_SEMANTICS["COMPLETE"]
    waiting = A.READINESS_SEMANTICS["WAITING_DATA"]
    blocked = A.READINESS_SEMANTICS["BLOCKED"]
    assert "VALID_CURRENT P1 report" in complete
    assert "PROMOTION_DEGRADED" in complete and "NO_RELIABLE_DIFFERENCE" in complete
    assert "All scientific authorities" in waiting and "below its frozen threshold" in waiting
    assert "Missing promotion authority is never WAITING_DATA" in blocked
    assert "never reconstructed" in blocked


def test_p1_is_research_memory_not_deployment_authority():
    memory = A.INSTITUTIONAL_MEMORY
    assert "research evidence only" in memory
    for prohibited in ("roll back", "re-promote", "create/apply a candidate", "change production"):
        assert prohibited in memory
    assert "future hypotheses" in memory


def test_historical_authority_is_truthfully_blocked_and_carried_forward():
    history = A.HISTORICAL_AUTHORITY
    assert "three PROPOSED candidates" in history
    assert "zero locally provable eligible promotions" in history
    assert "no wall-clock boundary" in history
    assert "does not expose authoritative source baseline/config identity" in history
    assert "BLOCKED, not WAITING_DATA" in history
    assert len(A.STAGE4_GAPS) == 6
    assert any("baseline_id" in gap for gap in A.STAGE4_GAPS)


def test_future_runner_report_identity_and_ownership_remain_unique():
    p1 = next(question for question in REGISTRY if question.id == "P1")
    assert A.FUTURE_RUNNER == "research_engine.experiments.promotion_impact.run_p1"
    # Adjudication only: the existing hypothetical-policy runner is deliberately
    # not repaired or rewired in this task.
    assert f"{p1.runner_module}.{p1.runner_function}" == (
        "research_engine.experiments.promotion_impact.run_promotion_impact"
    )
    assert A.FUTURE_REPORT == p1.report_filename == "p1_promotion_impact.json"
    assert "sole owner" in A.OWNERSHIP
    assert "No candidate evaluation" in A.OWNERSHIP


def test_hd04_unchanged_all_human_science_adjudicated_and_baseline_stable():
    hd04 = HUMAN_SEMANTIC_DECISIONS["HD04"]
    assert hd04.affected_question_ids == ("D2", "D3", "D4", "D5", "X5")
    assert "ADJUDICATED" in hd04.exact_decision
    assert not hd04.implementation_blocked_until_decision
    assert not any(
        decision.implementation_blocked_until_decision
        for decision in HUMAN_SEMANTIC_DECISIONS.values()
    )
    assert operational_baseline() == (63, 7)
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == frozenset({
        "EX5", "EX6", "EX8", "L2", "L3", "L6", "L7",
    })


def test_adjudication_has_no_runtime_data_collection_or_production_side_effects():
    source = inspect.getsource(A)
    for forbidden in (
        "boto3", "core.config", "ApplicationService(", "CandidateRegistry(",
        "persist_", "write_text", "open(", "subprocess",
    ):
        assert forbidden not in source
    assert "Governance authority only" in A.__doc__
