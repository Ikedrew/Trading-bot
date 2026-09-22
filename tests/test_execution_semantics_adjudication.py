"""Governance-only adjudication contract for X3, EXEC1, and X6."""
from __future__ import annotations

from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    MASTER_REPAIR_LEDGER,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a4_definitions import (
    EXEC1_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT,
    EXECUTION_SHARED_EVIDENCE_CONTRACT,
    WAVE_A4_SEMANTICALLY_ADJUDICATED,
    X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT,
)
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
    X6_HD08_ADJUDICATED_CONTRACT,
)


def test_x3_dual_endpoint_contract_is_adjudicated_but_not_implemented():
    c = X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT
    assert c["status"] == "ADJUDICATED"
    assert "Which trading sessions produce the best execution quality" in c["canonical_question"]
    assert c["primary_endpoint"] == "producer-measured absolute slippage by session"
    assert c["primary_slippage_semantic"] == "measured_execution_slippage"
    assert c["reconstructed_slippage_allowed"] is False
    assert c["secondary_endpoint"] == "explicit result_ok failure rate by session"
    assert c["rejection_numerator"] == "result_ok is explicitly False"
    assert c["success_definition"] == "result_ok is explicitly True"
    assert "exclude" in c["unknown_result_ok_policy"]
    assert c["missing_result_ok_defaults_to_failure"] is False
    assert c["retcode_role"].startswith("descriptive only")
    assert c["composite_score_allowed"] is False
    assert c["session_authority"] == "execution_context.market_access.session_state"
    assert c["unit_of_analysis"] == "one distinct account/broker execution result"
    assert c["cluster_field"] == "correlation_id"
    assert c["overall_minimum_account_results"] == 30
    assert c["session_minimum_account_results"] == 30
    assert c["session_minimum_distinct_decisions"] == 10
    assert c["primary_session_sufficiency"] == {
        "minimum_measured_slippage_results": 30,
        "minimum_distinct_decisions": 10,
    }
    assert c["secondary_session_sufficiency"] == {
        "minimum_known_result_ok_results": 30,
        "minimum_distinct_decisions": 10,
    }
    assert c["minimum_primary_sufficient_sessions"] == 2
    assert c["descriptive_metrics_authorize_universal_best_session_claim"] is False
    assert c["report_identity"] == "w4_x3_session_quality.json"
    assert "X3" in WAVE_A4_SEMANTICALLY_ADJUDICATED
    assert MASTER_REPAIR_LEDGER["X3"].gates.scientific_definition == "PASS"
    assert REGISTRY_BY_ID["X3"].runner_module == (
        "research_engine.experiments.execution_protection_research"
    )
    assert REGISTRY_BY_ID["X3"].runner_function == "run_x3"
    assert MASTER_REPAIR_LEDGER["X3"].structurally_operational


def test_x3_clustered_primary_inference_and_global_test_are_frozen():
    c = X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT
    assert c["primary_model"] == "absolute_measured_slippage ~ categorical session"
    assert c["model_coding"] == "deterministic reference coding"
    assert c["model_population"] == "primary-sufficient evaluated sessions only"
    assert c["cluster_normalized_weight"] == "w_di = 1 / k_d"
    assert c["cluster_total_weight"] == 1
    assert c["cluster_covariance"] == "correlation_id clustered sandwich"
    assert c["bread"] == "A = X' W X"
    assert c["cluster_score"] == (
        "s_d = sum_observations_in_d(w_di * x_di * residual_di)"
    )
    assert c["covariance_formula"] == "A^-1 (sum_d s_d s_d') A^-1"
    assert c["naive_row_independent_covariance_allowed"] is False
    assert c["global_test"] == "deterministic cluster-robust Wald-type joint session test"
    assert "equal across all sufficient" in c["global_null"]
    assert c["global_df"] == "S - 1"
    assert c["global_statistic"] == "b_session' V_session^-1 b_session"
    assert c["global_p_value"] == (
        "upper-tail chi-square probability with S - 1 degrees of freedom"
    )
    assert c["global_alpha"] == 0.05
    assert c["global_null_result"] == "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE"
    assert c["global_null_can_complete"] is True
    assert c["global_rejection_result"] == "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE"


def test_x3_omnibus_gated_pairwise_holm_and_best_claim_are_frozen():
    c = X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT
    assert c["followup_gate"] == "global p <= 0.05"
    assert c["followup_family"] == (
        "all pairwise contrasts between sufficient primary-endpoint sessions"
    )
    assert c["followup_count"] == "C(S, 2)"
    assert c["contrast_variance"] == "c' V_cluster c"
    assert c["contrast_interval"] == "estimate +/- 1.96 * cluster-robust SE"
    assert c["multiplicity_method"] == "Holm step-down family-wise error-rate control"
    assert c["multiplicity_family"] == (
        "one global family containing all predeclared pairwise session contrasts"
    )
    assert c["followup_alpha"] == 0.05
    assert c["omnibus_in_holm_family"] is False
    assert c["pairwise_claim_rule"] == (
        "global p <= 0.05 and Holm-adjusted pairwise p <= 0.05"
    )
    assert "every other sufficient evaluated session" in c["unique_best_rule"]
    assert c["raw_mean_winner_allowed"] is False
    assert c["canonical_order_breaks_scientific_ties"] is False
    assert c["no_unique_best_result"] == "NO_UNIQUE_SUPPORTED_BEST"
    assert c["unique_best_required_for_completion"] is False


def test_x3_rejection_endpoint_and_claim_boundaries_remain_secondary():
    c = X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT
    assert c["secondary_rejection_inference"] == (
        "descriptive with cluster-aware uncertainty only"
    )
    assert c["secondary_rejection_pairwise_tests_allowed"] is False
    assert c["secondary_rejection_can_override_primary_best"] is False
    assert c["session_endpoint_states"] == (
        "PRIMARY_SUFFICIENT",
        "REJECTION_SUFFICIENT",
        "BOTH",
        "INSUFFICIENT",
    )
    assert c["completed_results"] == (
        "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
        "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
    )
    assert "CURRENT evidence" in c["claim_boundary"]
    assert "does not establish strategy expectancy" in c["claim_boundary"]


def test_exec1_associative_realization_contract_is_adjudicated_and_implemented():
    c = EXEC1_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT
    assert c["status"] == "ADJUDICATED"
    assert "Do execution failures or adverse conditions degrade" in c["canonical_question"]
    assert "associative" in c["research_classification"]
    assert c["causal_claim_allowed"] is False
    assert c["otherwise_valid_authority"] == "decision_trace_v1.action"
    assert c["otherwise_valid_value"] == "EXECUTE"
    assert c["post_outcome_validity_inputs_forbidden"] is True
    assert c["required_evidence_components"] == (
        "CURRENT execution_results_v1",
        "CURRENT execution_context",
        "CURRENT decision_trace_v1",
    )
    assert c["optional_secondary_evidence"] == "CURRENT trade_truth_v1"
    assert c["successful_execution"] == "result_ok is explicitly True"
    assert c["failed_execution"] == "result_ok is explicitly False"
    assert "exclude" in c["unknown_status_policy"]
    assert c["failure_taxonomy_required"] is False
    assert c["primary_estimand"] == "opportunity-level execution realization degradation"
    assert c["primary_outcome"] == "successful execution realization at account-result grain"
    assert c["synthetic_failed_trade_r_allowed"] is False
    assert "successful executions only" in c["secondary_analysis"]
    assert "trade_truth_v1" in c["secondary_analysis"]
    assert c["secondary_analysis_optional_for_completion"] is True
    assert c["unit_of_analysis"] == "one distinct account/broker terminal execution result"
    assert c["cluster_field"] == "correlation_id"
    assert c["overall_minimum_terminal_results"] == 30
    assert c["overall_minimum_distinct_decisions"] == 10
    assert c["subgroup_minimum_account_results"] == 10
    assert c["subgroup_minimum_distinct_decisions"] == 5
    assert c["null_result_can_complete"] is True
    assert c["report_identity"] == "w4_exec1_execution_failures.json"
    assert "EXEC1" in WAVE_A4_SEMANTICALLY_ADJUDICATED
    assert MASTER_REPAIR_LEDGER["EXEC1"].structurally_operational


def test_hd08_and_x6_endpoint_condition_and_completion_contract_are_adjudicated():
    hd08 = HUMAN_SEMANTIC_DECISIONS["HD08"]
    c = X6_HD08_ADJUDICATED_CONTRACT
    assert hd08.affected_question_ids == ("X6",)
    assert hd08.exact_decision.startswith("ADJUDICATED:")
    assert hd08.implementation_blocked_until_decision is False
    assert c["status"] == "ADJUDICATED"
    assert c["implementation_blocked_until_decision"] is False
    assert c["primary_endpoint"] == "producer-measured absolute slippage"
    assert c["secondary_endpoints"] == ("explicit result_ok failure rate", "retcode mix")
    assert c["composite_endpoint_allowed"] is False
    assert c["evidence_components"] == (
        "CURRENT execution_results_v1",
        "CURRENT execution_context",
        "CURRENT decision_trace_v1",
    )
    assert c["unit_of_analysis"] == "one distinct account execution result"
    assert c["cluster_field"] == "correlation_id"
    assert c["spread_authority"] == "execution_context.market_access.spread_atr_ratio"
    assert c["spread_bands"] == {
        "LOW": "spread_atr_ratio < 0.10",
        "NORMAL": "0.10 <= spread_atr_ratio < 0.25",
        "HIGH": "spread_atr_ratio >= 0.25",
        "MISSING_INVALID": "missing/non-finite/invalid spread_atr_ratio",
    }
    assert c["raw_spread_substitution_allowed"] is False
    assert c["volatility_authority"] == (
        "decision_trace_v1.v10_market_state.regime.volatility_state"
    )
    assert c["condition_dimensions"] == (
        "canonical symbol",
        "account/broker",
        "session",
        "spread band",
        "pre-decision volatility band",
    )
    assert "no unrestricted" in c["dimension_analysis"]
    assert c["minimum_matched_account_results"] == 100
    assert c["minimum_distinct_decisions"] == 30
    assert c["cell_minimum_account_results"] == 30
    assert c["cell_minimum_distinct_decisions"] == 10
    assert c["minimum_comparable_sufficient_cells_per_claimed_dimension"] == 2
    assert "INSUFFICIENT" in c["sparse_dimension_policy"]
    assert c["null_result_can_complete"] is True


def test_x6_frozen_design_tracks_hd08_and_is_now_canonical():
    d = WAVE_A_NO_RUNNER_DESIGNS["X6"]
    trace = next(item for item in d.evidence_authority if item.dataset == "decision_trace_v1")
    assert trace.fields == (
        "correlation_id",
        "canonical_opportunity_id",
        "v10_market_state.regime.volatility_state",
    )
    assert "LOW <0.10" in d.metric_definition
    assert "NORMAL [0.10,0.25)" in d.metric_definition
    assert "HIGH >=0.25" in d.metric_definition
    assert "no composite endpoint" in d.metric_definition
    assert "ADJUDICATED HD08" in d.human_semantic_decisions[0]
    assert d.report_identity == "x6_execution_stability.json"
    # HD08 adjudicates the frozen design; implementation remains a later repair.
    assert not REGISTRY_BY_ID["X6"].runner_module
    assert not REGISTRY_BY_ID["X6"].runner_function
    assert not REGISTRY_BY_ID["X6"].report_filename
    assert "X6" in WAVE_A_NO_RUNNER_TARGETS
    assert not MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert MASTER_REPAIR_LEDGER["X6"].human_semantic_decision_required is False


def test_shared_current_join_provenance_and_limitations_are_frozen():
    c = EXECUTION_SHARED_EVIDENCE_CONTRACT
    assert c["status"] == "ADJUDICATED"
    assert c["current_selection_required"] is True
    assert c["schema_and_stale_exclusion_required"] is True
    assert c["shared_decision_cluster"] == "correlation_id"
    assert c["join_cardinality"] == "many account results to one context per correlation_id"
    assert c["ambiguous_or_conflicting_identity_policy"] == "fail closed"
    assert "exact analytical-subset attestation" in c["provenance_requirements"]
    assert "deterministic digest" in c["provenance_requirements"]
    limitations = " ".join(c["evidence_limitations"])
    for expected in (
        "structured failure taxonomy",
        "pre-broker failure",
        "partial-fill volume",
        "broker order latency",
        "failed executions have no realized trade outcome",
        "pre-cost and post-cost R",
    ):
        assert expected in limitations


def test_governance_adjudication_preserves_current_structure_and_baseline():
    assert operational_baseline() == (47, 23)
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert len(REGISTRY) == len({q.id for q in REGISTRY}) == 70
    assert tuple(q.id for q in REGISTRY) == BASELINE_QUESTION_IDS
    definitions = build_definitions_from_registry(REGISTRY)
    assert all(definition.definition_version == 1 for definition in definitions.values())
    reports = {
        X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT["report_identity"],
        EXEC1_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT["report_identity"],
        X6_HD08_ADJUDICATED_CONTRACT["report_identity"],
    }
    assert reports == {
        "w4_x3_session_quality.json",
        "w4_exec1_execution_failures.json",
        "x6_execution_stability.json",
    }
