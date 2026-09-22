"""Focused governance tests for the adjudicated EXEC1 primary exposure."""
from __future__ import annotations

import inspect

from research_engine.experiments.execution_protection_research import run_exec1
from research_engine.registry import BASELINE_QUESTION_IDS, REGISTRY, REGISTRY_BY_ID
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    MASTER_REPAIR_LEDGER,
    operational_baseline,
)
from research_engine.registry.wave_a4_definitions import (
    EXEC1_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT as CONTRACT,
    X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT,
)
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_TARGETS,
    X6_HD08_ADJUDICATED_CONTRACT,
)


def test_identity_population_and_associative_boundary_are_frozen():
    assert CONTRACT["status"] == "ADJUDICATED"
    assert CONTRACT["exposure_contract_status"] == "ADJUDICATED"
    assert CONTRACT["ready_for_implementation"] is True
    assert CONTRACT["canonical_question"] == (
        "Do execution failures or adverse conditions degrade otherwise valid opportunities?"
    )
    assert "associative" in CONTRACT["research_classification"]
    assert CONTRACT["causal_claim_allowed"] is False
    assert CONTRACT["otherwise_valid_authority"] == "decision_trace_v1.action"
    assert CONTRACT["otherwise_valid_value"] == "EXECUTE"
    assert CONTRACT["post_outcome_validity_inputs_forbidden"] is True
    assert "associated" in CONTRACT["operational_subquestion"]
    assert "do not claim spread caused failures" in CONTRACT["claim_boundary"]


def test_continuous_normalized_spread_authority_has_no_substitutes_or_bins():
    assert CONTRACT["primary_exposure_authority"] == (
        "execution_context.market_access.spread_atr_ratio"
    )
    assert CONTRACT["primary_exposure_temporal_class"] == "PRE_EXECUTION"
    assert CONTRACT["primary_exposure_validity"] == (
        "present, numeric, finite, and non-negative"
    )
    assert CONTRACT["primary_exposure_treatment"] == "continuous"
    assert CONTRACT["raw_spread_substitution_allowed"] is False
    assert CONTRACT["spread_reconstruction_allowed"] is False
    assert CONTRACT["x6_spread_bands_used"] is False
    assert CONTRACT["outcome_selected_cutpoints_allowed"] is False
    assert "more comparable" in CONTRACT["exposure_rationale"]


def test_outcome_linear_probability_model_and_cluster_inference_are_exact():
    assert CONTRACT["primary_outcome_encoding"] == {
        "SUCCESS": 1,
        "FAILURE": 0,
        "UNKNOWN": "excluded from primary inference and counted",
    }
    assert CONTRACT["primary_model_family"] == "cluster-weighted linear probability model"
    assert CONTRACT["primary_model"] == (
        "successful_execution ~ intercept + spread_atr_ratio"
    )
    assert CONTRACT["additional_covariates_allowed"] is False
    assert CONTRACT["interactions_allowed"] is False
    assert CONTRACT["nonlinear_terms_allowed"] is False
    assert CONTRACT["logistic_model_allowed"] is False
    assert CONTRACT["cluster_normalized_weight"] == "w_di = 1 / k_d"
    assert CONTRACT["cluster_total_weight"] == 1
    assert CONTRACT["weighted_estimator"] == "beta = (X' W X)^-1 X' W y"
    assert CONTRACT["cluster_covariance"] == "correlation_id clustered sandwich"
    assert CONTRACT["bread"] == "A = X' W X"
    assert CONTRACT["cluster_score"] == (
        "s_d = sum_rows_in_d(w_di * x_di * residual_di)"
    )
    assert CONTRACT["covariance_formula"] == "A^-1 (sum_d s_d s_d') A^-1"
    assert CONTRACT["naive_row_independent_covariance_allowed"] is False


def test_null_directions_classifications_and_completion_are_frozen():
    assert CONTRACT["primary_null"] == "H0: beta_spread = 0"
    assert CONTRACT["primary_test_sidedness"] == "two-sided"
    assert CONTRACT["primary_alpha"] == 0.05
    assert CONTRACT["primary_interval"] == (
        "beta_spread +/- 1.96 * cluster-robust SE"
    )
    assert CONTRACT["adverse_direction"] == "beta_spread < 0"
    assert CONTRACT["favourable_direction"] == "beta_spread > 0"
    assert set(CONTRACT["classification_rules"]) == {
        "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION",
        "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION",
        "NO_RELIABLE_ASSOCIATION",
    }
    assert CONTRACT["completed_results"] == (
        "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION",
        "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION",
        "NO_RELIABLE_ASSOCIATION",
    )
    assert CONTRACT["adverse_result_can_complete"] is True
    assert CONTRACT["favourable_result_can_complete"] is True
    assert CONTRACT["null_result_can_complete"] is True


def test_sufficiency_evidence_provenance_and_non_tautology_are_frozen():
    assert CONTRACT["required_evidence_components"] == (
        "CURRENT execution_results_v1",
        "CURRENT execution_context",
        "CURRENT decision_trace_v1",
    )
    assert CONTRACT["overall_minimum_terminal_results"] == 30
    assert CONTRACT["overall_minimum_distinct_decisions"] == 10
    assert CONTRACT["subgroup_minimum_account_results"] == 10
    assert CONTRACT["subgroup_minimum_distinct_decisions"] == 5
    assert CONTRACT["minimum_distinct_exposure_values"] == 2
    assert CONTRACT["full_rank_design_required"] is True
    assert CONTRACT["outcome_variation_required"] is True
    assert CONTRACT["constant_outcome_classification"] == "INSUFFICIENT_EVIDENCE"
    assert CONTRACT["rank_failure_classification"] == "INSUFFICIENT_EVIDENCE"
    assert CONTRACT["descriptive_failure_counts_are_inferential_exposure"] is False
    assert CONTRACT["measured_slippage_primary_result_ok_exposure_allowed"] is False
    assert "exact CURRENT results + context + decision-trace" in CONTRACT["primary_provenance"]


def test_optional_secondary_remains_non_blocking_and_scientifically_bounded():
    assert CONTRACT["optional_secondary_evidence"] == "CURRENT trade_truth_v1"
    assert "successful executions only" in CONTRACT["secondary_analysis"]
    assert CONTRACT["secondary_analysis_optional_for_completion"] is True
    assert CONTRACT["secondary_model_status"] == (
        "not frozen; descriptive or INSUFFICIENT only"
    )
    assert CONTRACT["synthetic_failed_trade_r_allowed"] is False


def test_runner_and_structural_state_advance_only_after_implementation():
    assert tuple(inspect.signature(run_exec1).parameters) == (
        "execution_results", "execution_contexts", "decision_traces",
    )
    assert run_exec1.__module__ == (
        "research_engine.experiments.execution_protection_research"
    )
    assert REGISTRY_BY_ID["EXEC1"].runner_function == "run_exec1"
    assert MASTER_REPAIR_LEDGER["EXEC1"].structurally_operational
    assert MASTER_REPAIR_LEDGER["X3"].structurally_operational
    assert X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT["structurally_operational"] is True
    assert not MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert X6_HD08_ADJUDICATED_CONTRACT["status"] == "ADJUDICATED"
    assert not REGISTRY_BY_ID["X6"].runner_module
    assert not REGISTRY_BY_ID["X6"].runner_function
    assert operational_baseline() == (47, 23)
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}


def test_registry_baseline_and_definition_versions_remain_exact():
    assert len(REGISTRY) == len({question.id for question in REGISTRY}) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    definitions = build_definitions_from_registry(REGISTRY)
    assert all(definition.definition_version == 1 for definition in definitions.values())
