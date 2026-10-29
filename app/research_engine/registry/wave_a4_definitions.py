"""Wave A4 semantic-alignment assessment.

The registry, runners, resolver, readiness rules, and report contracts do not
currently establish mutually consistent scientific definitions for this
tranche.  The targets therefore remain unchanged and fail-closed.  This module
records the exact conflicts without changing runner, evidence, readiness,
report, collection, trading, or multi-account behaviour.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule,
    EvidenceAuthority,
    EvidenceProducer,
    JoinContract,
    ResearchQuestionDefinition,
)


WAVE_A4_1_TARGETS = frozenset({"M1", "M11", "X3", "EXEC1"})
WAVE_A4_2_TARGETS = frozenset({"D3", "D4", "D5"})
WAVE_A4_3_TARGETS = frozenset({"L1", "L2", "L3", "L4"})
WAVE_A4_4_TARGETS = frozenset({"EX5", "EX6", "EX7", "EX8"})
WAVE_A4_TARGETS = (
    WAVE_A4_1_TARGETS
    | WAVE_A4_2_TARGETS
    | WAVE_A4_3_TARGETS
    | WAVE_A4_4_TARGETS
)

WAVE_A4_4_RESEARCH_CLASSIFICATIONS = {
    "EX5": "associative",
    "EX6": "associative",
    "EX7": "descriptive",
    "EX8": "descriptive",
}


# Human-adjudicated execution-semantics contracts.  These are governance only:
# the existing X3/EXEC1 runners remain scientifically mismatched and X6 remains
# runnerless until RW7 implementation work is performed.
EXECUTION_SHARED_EVIDENCE_CONTRACT = {
    "definition_version": 1,
    "status": "ADJUDICATED",
    "current_selection_required": True,
    "schema_and_stale_exclusion_required": True,
    "execution_observation_grain": "one distinct account/broker execution result",
    "shared_decision_cluster": "correlation_id",
    "identity_requirements": (
        "account-result identity",
        "correlation_id",
        "canonical_opportunity_id consistency where available",
        "canonical-symbol consistency",
    ),
    "join_cardinality": "many account results to one context per correlation_id",
    "ambiguous_or_conflicting_identity_policy": "fail closed",
    "heuristic_joins_forbidden": (
        "symbol-only",
        "timestamp proximity",
        "input ordering",
        "account balance changes",
    ),
    "provenance_requirements": (
        "exact analytical-subset attestation",
        "deterministic digest",
        "change sensitivity",
        "canonical-order invariance",
    ),
    "evidence_limitations": (
        "no universal normalized structured failure taxonomy",
        "not every pre-broker failure is canonically observed",
        "execution_results_v1 lacks authoritative partial-fill volume",
        "broker order latency is not canonically recorded",
        "failed executions have no realized trade outcome",
        "trade_truth_v1 has no separate pre-cost and post-cost R authorities",
    ),
}


X3_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT = {
    "definition_version": 1,
    "status": "ADJUDICATED",
    "canonical_question": (
        "Which trading sessions produce the best execution quality "
        "(lowest slippage, fewest rejects)?"
    ),
    "research_classification": "bounded session-conditioned execution diagnostic",
    "population": (
        "Validity-approved CURRENT account execution results deterministically "
        "joined to one CURRENT pre-execution execution_context record."
    ),
    "unit_of_analysis": "one distinct account/broker execution result",
    "cluster_field": "correlation_id",
    "multi_account_rule": (
        "Multiple account executions from one decision are valid execution "
        "observations but are not independent strategy opportunities."
    ),
    "session_authority": "execution_context.market_access.session_state",
    "unknown_session_policy": "exclude from session claims and count explicitly",
    "primary_endpoint": "producer-measured absolute slippage by session",
    "primary_slippage_semantic": "measured_execution_slippage",
    "reconstructed_slippage_allowed": False,
    "secondary_endpoint": "explicit result_ok failure rate by session",
    "rejection_denominator": (
        "terminal account execution results with result_ok explicitly True or False"
    ),
    "rejection_numerator": "result_ok is explicitly False",
    "success_definition": "result_ok is explicitly True",
    "unknown_result_ok_policy": "exclude from denominator and count explicitly",
    "missing_result_ok_defaults_to_failure": False,
    "retcode_role": "descriptive only; does not override explicit result_ok",
    "composite_score_allowed": False,
    "overall_minimum_account_results": 30,
    "session_minimum_account_results": 30,
    "session_minimum_distinct_decisions": 10,
    "primary_session_sufficiency": {
        "minimum_measured_slippage_results": 30,
        "minimum_distinct_decisions": 10,
    },
    "secondary_session_sufficiency": {
        "minimum_known_result_ok_results": 30,
        "minimum_distinct_decisions": 10,
    },
    "minimum_primary_sufficient_sessions": 2,
    "endpoint_specific_sufficiency": True,
    "completion_criterion": (
        "Overall threshold passes; at least two sessions are sufficient for the "
        "primary measured-slippage endpoint; rejection evidence for those sessions "
        "is explicitly sufficient or insufficient; claims remain endpoint/session bounded."
    ),
    "primary_model": "absolute_measured_slippage ~ categorical session",
    "model_coding": "deterministic reference coding",
    "model_population": "primary-sufficient evaluated sessions only",
    "cluster_normalized_weight": "w_di = 1 / k_d",
    "cluster_total_weight": 1,
    "cluster_weight_scope": (
        "eligible account execution observations within the evaluated "
        "primary-session population for correlation_id decision d"
    ),
    "cluster_covariance": "correlation_id clustered sandwich",
    "bread": "A = X' W X",
    "cluster_score": "s_d = sum_observations_in_d(w_di * x_di * residual_di)",
    "covariance_formula": "A^-1 (sum_d s_d s_d') A^-1",
    "naive_row_independent_covariance_allowed": False,
    "global_test": "deterministic cluster-robust Wald-type joint session test",
    "global_null": (
        "Mean absolute measured slippage is equal across all sufficient "
        "evaluated sessions; all non-reference session coefficients are jointly zero."
    ),
    "global_df": "S - 1",
    "global_statistic": "b_session' V_session^-1 b_session",
    "global_p_value": "upper-tail chi-square probability with S - 1 degrees of freedom",
    "global_alpha": 0.05,
    "global_null_result": "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
    "global_null_can_complete": True,
    "global_rejection_result": "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
    "followup_gate": "global p <= 0.05",
    "followup_family": "all pairwise contrasts between sufficient primary-endpoint sessions",
    "followup_count": "C(S, 2)",
    "followup_contrast": "linear contrast of fitted session means from the primary model",
    "contrast_variance": "c' V_cluster c",
    "contrast_interval": "estimate +/- 1.96 * cluster-robust SE",
    "contrast_p_value": "two-sided cluster-aware p-value",
    "multiplicity_method": "Holm step-down family-wise error-rate control",
    "multiplicity_family": "one global family containing all predeclared pairwise session contrasts",
    "followup_alpha": 0.05,
    "omnibus_in_holm_family": False,
    "pairwise_claim_rule": "global p <= 0.05 and Holm-adjusted pairwise p <= 0.05",
    "unique_best_rule": (
        "Exactly one sufficient session has Holm-supported lower fitted mean absolute "
        "measured slippage than every other sufficient evaluated session."
    ),
    "no_unique_best_result": "NO_UNIQUE_SUPPORTED_BEST",
    "raw_mean_winner_allowed": False,
    "canonical_order_breaks_scientific_ties": False,
    "unique_best_required_for_completion": False,
    "secondary_rejection_inference": "descriptive with cluster-aware uncertainty only",
    "secondary_rejection_pairwise_tests_allowed": False,
    "secondary_rejection_can_override_primary_best": False,
    "session_endpoint_states": (
        "PRIMARY_SUFFICIENT",
        "REJECTION_SUFFICIENT",
        "BOTH",
        "INSUFFICIENT",
    ),
    "completed_results": (
        "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
        "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE",
    ),
    "insufficient_result": "INSUFFICIENT_DATA / WAITING_DATA",
    "claim_boundary": (
        "Claims apply only to sufficient evaluated sessions, the CURRENT evidence "
        "period/population, and the measured-slippage primary endpoint; lower "
        "slippage does not establish strategy expectancy or profitability."
    ),
    "descriptive_metrics_authorize_universal_best_session_claim": False,
    "report_identity": "w4_x3_session_quality.json",
    "structurally_operational": True,
}


EXEC1_EXECUTION_SEMANTICS_ADJUDICATED_CONTRACT = {
    "definition_version": 1,
    "status": "ADJUDICATED",
    "exposure_contract_status": "ADJUDICATED",
    "ready_for_implementation": True,
    "canonical_question": (
        "Do execution failures or adverse conditions degrade otherwise valid opportunities?"
    ),
    "research_classification": "governed associative execution-realization degradation",
    "causal_claim_allowed": False,
    "otherwise_valid_authority": "decision_trace_v1.action",
    "otherwise_valid_value": "EXECUTE",
    "otherwise_valid_consistency": (
        "The pre-execution decision must be an authorized EXECUTE decision; "
        "terminal_stage/lineage consistency must pass before account realization."
    ),
    "post_outcome_validity_inputs_forbidden": True,
    "required_evidence_components": (
        "CURRENT execution_results_v1",
        "CURRENT execution_context",
        "CURRENT decision_trace_v1",
    ),
    "optional_secondary_evidence": "CURRENT trade_truth_v1",
    "unit_of_analysis": "one distinct account/broker terminal execution result",
    "strategy_opportunity_unit": "one canonical decision/opportunity",
    "cluster_field": "correlation_id",
    "successful_execution": "result_ok is explicitly True",
    "failed_execution": "result_ok is explicitly False",
    "unknown_execution_status": "missing or ambiguous result_ok",
    "unknown_status_policy": "exclude from success-versus-failure inference and report separately",
    "failure_taxonomy_required": False,
    "primary_estimand": "opportunity-level execution realization degradation",
    "primary_outcome": "successful execution realization at account-result grain",
    "operational_subquestion": (
        "Among otherwise-valid EXECUTE decisions reaching account execution, is "
        "greater pre-execution spread burden associated with a lower probability "
        "of successful execution realization?"
    ),
    "primary_exposure_authority": (
        "execution_context.market_access.spread_atr_ratio"
    ),
    "primary_exposure_temporal_class": "PRE_EXECUTION",
    "primary_exposure_validity": (
        "present, numeric, finite, and non-negative"
    ),
    "invalid_exposure_policy": "exclude from primary inference and count explicitly",
    "primary_exposure_treatment": "continuous",
    "raw_spread_substitution_allowed": False,
    "spread_reconstruction_allowed": False,
    "x6_spread_bands_used": False,
    "outcome_selected_cutpoints_allowed": False,
    "exposure_rationale": (
        "spread_atr_ratio expresses pre-execution spread burden relative to "
        "contemporaneous market movement scale and is more comparable across "
        "heterogeneous canonical instruments than raw price-unit spread"
    ),
    "primary_outcome_encoding": {
        "SUCCESS": 1,
        "FAILURE": 0,
        "UNKNOWN": "excluded from primary inference and counted",
    },
    "primary_model_family": "cluster-weighted linear probability model",
    "primary_model": "successful_execution ~ intercept + spread_atr_ratio",
    "additional_covariates_allowed": False,
    "interactions_allowed": False,
    "nonlinear_terms_allowed": False,
    "logistic_model_allowed": False,
    "primary_coefficient": "beta_spread",
    "primary_estimand_interpretation": (
        "change in successful-execution realization probability associated with "
        "a one-unit increase in pre-execution spread_atr_ratio within the evaluated "
        "CURRENT EXECUTE population"
    ),
    "descriptive_effect_per_0_10": "0.10 * beta_spread",
    "sample_standardization_allowed": False,
    "cluster_normalized_weight": "w_di = 1 / k_d",
    "cluster_total_weight": 1,
    "weighted_estimator": "beta = (X' W X)^-1 X' W y",
    "design_row": "x_di = [1, spread_atr_ratio_di]",
    "cluster_covariance": "correlation_id clustered sandwich",
    "bread": "A = X' W X",
    "cluster_score": "s_d = sum_rows_in_d(w_di * x_di * residual_di)",
    "covariance_formula": "A^-1 (sum_d s_d s_d') A^-1",
    "naive_row_independent_covariance_allowed": False,
    "primary_null": "H0: beta_spread = 0",
    "primary_test_sidedness": "two-sided",
    "primary_alpha": 0.05,
    "primary_interval": "beta_spread +/- 1.96 * cluster-robust SE",
    "adverse_direction": "beta_spread < 0",
    "favourable_direction": "beta_spread > 0",
    "classification_rules": {
        "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION": (
            "all gates pass; two-sided p <= 0.05; beta_spread < 0; 95% interval below 0"
        ),
        "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION": (
            "all gates pass; two-sided p <= 0.05; beta_spread > 0; 95% interval above 0"
        ),
        "NO_RELIABLE_ASSOCIATION": (
            "all gates pass; two-sided p > 0.05 or 95% interval includes 0"
        ),
    },
    "completed_results": (
        "RELIABLE_ADVERSE_EXECUTION_ASSOCIATION",
        "RELIABLE_FAVOURABLE_EXECUTION_ASSOCIATION",
        "NO_RELIABLE_ASSOCIATION",
    ),
    "interpretation": (
        "Execution conditions are associatively related to reduced successful "
        "realization of otherwise-valid opportunities."
    ),
    "synthetic_failed_trade_r_allowed": False,
    "descriptive_failure_counts_are_inferential_exposure": False,
    "measured_slippage_primary_result_ok_exposure_allowed": False,
    "secondary_analysis": (
        "For successful executions only, associate producer-measured adverse "
        "execution conditions with canonical realized R/net outcome from CURRENT "
        "trade_truth_v1 using an exact account-level identity join."
    ),
    "secondary_analysis_optional_for_completion": True,
    "secondary_model_status": "not frozen; descriptive or INSUFFICIENT only",
    "secondary_join_heuristics_allowed": False,
    "overall_minimum_terminal_results": 30,
    "overall_minimum_distinct_decisions": 10,
    "subgroup_minimum_account_results": 10,
    "subgroup_minimum_distinct_decisions": 5,
    "unknown_status_counts_toward_comparative_sufficiency": False,
    "minimum_distinct_exposure_values": 2,
    "full_rank_design_required": True,
    "outcome_variation_required": True,
    "constant_outcome_classification": "INSUFFICIENT_EVIDENCE",
    "rank_failure_classification": "INSUFFICIENT_EVIDENCE",
    "null_result_can_complete": True,
    "favourable_result_can_complete": True,
    "adverse_result_can_complete": True,
    "primary_analytical_population_requirements": (
        "CURRENT governed execution result",
        "valid account execution observation identity and correlation_id",
        "strict matched CURRENT execution_context",
        "strict matched CURRENT decision_trace_v1",
        "decision_trace_v1.action == EXECUTE",
        "result_ok explicitly True or False",
        "finite non-negative spread_atr_ratio",
        "no identity conflict",
    ),
    "primary_provenance": (
        "exact CURRENT results + context + decision-trace analytical subset; "
        "EXECUTE-only, known-status, valid-spread rows; deterministic, reorder "
        "invariant, change sensitive, and fail closed"
    ),
    "claim_boundary": (
        "Within the evaluated CURRENT otherwise-valid EXECUTE population, report "
        "association only; do not claim spread caused failures, failed executions "
        "would have been profitable, or execution reduced strategy expectancy"
    ),
    "completion_criterion": (
        "All three CURRENT components are deterministically linked; >=30 eligible "
        "known-status rows and >=10 EXECUTE decisions exist; spread and outcome vary; "
        "the weighted design and clustered covariance are estimable; exact provenance "
        "passes. Any declared adverse, favourable, or null result may COMPLETE."
    ),
    "insufficient_result": "WAITING_DATA / INSUFFICIENT_DATA",
    "report_identity": "w4_exec1_execution_failures.json",
    "structurally_operational": True,
}

WAVE_A4_SEMANTICALLY_ADJUDICATED = frozenset({"X3", "EXEC1"})

# Repairs 4B.2/4B.3 close X3 and EXEC1 while retaining their frozen assessments
# below as design provenance.  The other A4 targets remain unresolved.
WAVE_A4_RESOLVED = frozenset({"X3", "EXEC1"})
WAVE_A4_UNRESOLVED = WAVE_A4_TARGETS - WAVE_A4_RESOLVED
WAVE_A4_OVERRIDES: dict[str, dict] = {
    "X3": {
        "hypothesis": (
            "Mean producer-measured absolute execution slippage differs across "
            "sufficient CURRENT execution-context sessions."
        ),
        "null_hypothesis": (
            "All sufficient evaluated sessions have equal mean producer-measured "
            "absolute execution slippage."
        ),
        "population_definition": (
            "Validity-approved CURRENT execution_results_v1 account/broker results "
            "strictly joined by correlation_id to one CURRENT execution_context; "
            "fan-out children remain observations and correlation_id is the cluster."
        ),
        "metric_definition": (
            "Primary: cluster-normalized weighted categorical-session model of "
            "absolute producer-measured slippage, one cluster-robust Wald omnibus, "
            "and omnibus-gated global Holm pairwise contrasts. Secondary: explicit "
            "result_ok failure rate by session with correlation-clustered descriptive "
            "uncertainty. COMPLETE requires >=30 matched results, >=2 sessions each "
            "with >=30 primary rows and >=10 decisions, valid covariance/provenance; "
            "a reliable or valid null result can complete."
        ),
        "evidence_authorities": (
            EvidenceAuthority(
                dataset="execution_results_v1",
                schema_version="execution_results_v1",
                producer=EvidenceProducer.EXECUTION_RESULTS,
                field_path="slippage + slippage_semantic + result_ok",
                semantic_meaning=(
                    "account-result execution status and producer-measured execution slippage"
                ),
            ),
            EvidenceAuthority(
                dataset="execution_context",
                schema_version="execution_context_v1",
                field_path="market_access.session_state",
                semantic_meaning="authoritative pre-execution session",
            ),
        ),
        "join_contract": JoinContract(
            join_keys=("correlation_id",),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Many distinct account execution results join one unambiguous context; "
                "opportunity, decision and canonical-symbol conflicts fail closed."
            ),
        ),
        "epoch_requirement": "CURRENT",
        "minimum_sample": 30,
        "completion_rule": CompletionRule(
            rule_type="clustered_session_execution_quality",
            threshold=30,
            description=(
                "COMPLETE after >=30 governed matched results, at least two primary-"
                "sufficient 30-result/10-decision sessions, estimable cluster-robust "
                "Wald inference, secondary rejection accounting, and CURRENT exact-"
                "subset provenance; either reliable difference or valid null completes."
            ),
        ),
    },
    "EXEC1": {
        "hypothesis": (
            "Within otherwise-valid CURRENT EXECUTE decisions, continuous pre-"
            "execution spread_atr_ratio is associated with successful account-level "
            "execution realization."
        ),
        "null_hypothesis": "beta_spread = 0 in the weighted linear probability model.",
        "population_definition": (
            "CURRENT execution_results_v1 account observations strictly joined by "
            "correlation_id to one CURRENT execution_context and one CURRENT "
            "decision_trace_v1 with action exactly EXECUTE; known status and finite "
            "non-negative spread_atr_ratio are required."
        ),
        "metric_definition": (
            "Cluster-normalized weighted linear probability model successful_execution "
            "~ intercept + spread_atr_ratio with correlation_id sandwich covariance. "
            "COMPLETE requires >=30 analytical rows, >=10 decisions, exposure and "
            "outcome variation, estimability, and exact CURRENT provenance; adverse, "
            "favourable, and valid-null classifications may complete."
        ),
        "evidence_authorities": (
            EvidenceAuthority(
                dataset="execution_results_v1",
                schema_version="execution_results_v1",
                producer=EvidenceProducer.EXECUTION_RESULTS,
                field_path="result_ok + retcode + account execution identity",
                semantic_meaning="explicit terminal account execution realization",
            ),
            EvidenceAuthority(
                dataset="execution_context",
                schema_version="execution_context_v1",
                field_path="market_access.spread_atr_ratio",
                semantic_meaning="continuous authoritative pre-execution spread burden",
            ),
            EvidenceAuthority(
                dataset="decision_trace_v1",
                schema_version="decision_trace_v1",
                field_path="action",
                semantic_meaning="authoritative pre-execution EXECUTE boundary",
            ),
        ),
        "join_contract": JoinContract(
            join_keys=("correlation_id",),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Many distinct account results join one unambiguous context and one "
                "authoritative decision trace; opportunity, decision and canonical-"
                "symbol conflicts fail closed."
            ),
        ),
        "epoch_requirement": "CURRENT",
        "minimum_sample": 30,
        "completion_rule": CompletionRule(
            rule_type="clustered_execution_realization_association",
            threshold=30,
            description=(
                "COMPLETE after the 30-row/10-decision, exposure/outcome variation, "
                "weighted-design, clustered-covariance, and exact-provenance gates; "
                "adverse, favourable, or valid-null association may complete."
            ),
        ),
    },
}

WAVE_A4_UNRESOLVED_REASONS = {
    "M1": (
        "Registry intent asks whether authoritative H4 regime classification "
        "predicts trade R-multiple. The mapped legacy_canonical.run_q06 runner "
        "does not relate regime to outcomes at all: it counts the raw "
        "decision_trace regime field, separately counts shadow outcomes, and "
        "reports only regime frequency. It performs no deterministic trace-to-"
        "outcome join, regime-cell R statistics, controls, ordering, or "
        "predictive validation. Its effective sample is split between decision "
        "traces for the reported distribution and completed shadow records for "
        "status/confidence; COMPLETE requires merely one shadow outcome, while "
        "the registry declares coverage gates but no sample threshold. The "
        "shadow loader also combines canonical reconstructed and separate "
        "research-shadow populations without an M1 CURRENT-only filter or "
        "canonical-opportunity deduplication. Account execution fanout is not "
        "an input, but repeated horizon simulations can inflate the shadow "
        "count. This is descriptive regime-frequency reporting, not associative "
        "or predictive outcome research. Repair requires one authoritative H4 "
        "regime field on a CURRENT completed-shadow population, an approved "
        "canonical-opportunity/horizon observation rule, deterministic outcome "
        "pairing, explicit grouping/controls, and a scientifically sufficient "
        "predictive or approved descriptive test. Left fail-closed."
    ),
    "M11": (
        "Registry intent asks whether regime + phase + bias provides more "
        "predictive value than pattern identity and declares shadow_trades plus "
        "decision_trace with lineage coverage. market_research.run_m11 loads "
        "only shadow trades and performs no decision_trace join. It accepts "
        "compatibility fields for regime, phase, bias, and pattern, although "
        "bias is not a registry required field. It pools CURRENT completed "
        "shadow records across symbol, strategy, and horizon; one shadow "
        "lifecycle is one observation, so account fanout is absent but multiple "
        "horizon simulations for one canonical opportunity count separately. "
        "The metric is the unweighted standard deviation of in-sample cell mean "
        "R for pattern-only versus regime+phase+bias cells, with 'more "
        "predictive' set when context dispersion exceeds pattern dispersion by "
        "15%. There is no per-cell minimum, confounder control, time ordering, "
        "holdout, or predictive score; COMPLETE requires only one fully labelled "
        "combined record, despite the registry coverage rules and lack of an "
        "explicit minimum sample. This is a pooled descriptive association, not "
        "comparative predictive evidence. Repair requires authoritative context "
        "fields and join ownership, an approved opportunity/horizon unit, "
        "confounder and cell-sufficiency rules, and genuine predictive validation "
        "or an approved observational narrowing. Left fail-closed."
    ),
    "X3": (
        "The human-adjudicated X3 contract preserves the registry question and "
        "defines two separate endpoints: producer-measured absolute slippage is "
        "primary and explicit-result_ok rejection rate is secondary; no composite "
        "score is permitted. execution_protection_research.run_x3 analyses only slippage; "
        "it does not compute rejection or failure rates from result_ok/retcode "
        "or execution_attempts. Its session authority is explicitly the nested "
        "execution_context.market_access.session_state field, flattened only by "
        "the runner adapter. Results join to one context by correlation_id, with "
        "canonical opportunity/entity/symbol consistency checks; one context may "
        "legitimately fan out to distinct account-grained results identified by "
        "account/order/deal/position identity. Thus one matched account execution "
        "is the execution observation, not a new strategy signal. Only records "
        "whose producer marks slippage_semantic as "
        "measured_execution_slippage are analysed; resolver-derived requested-"
        "versus-fill compatibility values and missing slippage are excluded. The "
        "approved contract requires >=30 matched observations overall, >=30 "
        "eligible account results and >=10 distinct correlation_id decisions per "
        "claimable session, and at least two primary-slippage-sufficient sessions. "
        "Missing result_ok is excluded and counted, never defaulted to failure. "
        "The runner still declares COMPLETE at the old overall threshold and does "
        "not implement endpoint-specific sufficiency or the adjudicated cluster-normalized "
        "weighted session model, clustered Wald omnibus, omnibus-gated global Holm "
        "pairwise family, or unique supported-best rule. The scientific definition is "
        "fully adjudicated, but the runner remains fail-closed pending repair."
    ),
    "EXEC1": (
        "The human-adjudicated EXEC1 contract preserves the associative question "
        "of whether failures/adverse conditions degrade otherwise-valid opportunities. "
        "Otherwise-valid means decision_trace_v1.action is the authorized EXECUTE "
        "value before account realization. The canonical resolver correctly "
        "uses CURRENT execution_results_v1 as the primary population, and "
        "execution_protection_research.run_exec1 likewise loads execution "
        "results; it does not use protection_audit or treat execution_attempts "
        "as equivalent evidence. Each account-grained execution result is a "
        "legitimate observation, allowing different account outcomes for one "
        "canonical decision, but the runner neither reports account grouping nor "
        "enforces a correlation_id + account identity grain. It descriptively "
        "counts result_ok, retcodes, and per-symbol failure rates; missing "
        "result_ok defaults to failure and missing retcode to '?', so rejection "
        "semantics are not isolated. Optional execution_context is joined by "
        "correlation_id, but the purported spread_at_execution statistic is "
        "populated with result slippage rather than context spread. No valid-"
        "opportunity or outcome evidence is joined, so the runner cannot measure "
        "degradation of otherwise valid opportunities or adverse-condition "
        "effects. The registry and runner require >=30 result rows; per-symbol "
        "cells require >=10. This supports descriptive execution-result reliability "
        "only, not the approved opportunity-level successful-realization estimand. "
        "The approved minimums are >=30 terminal results and >=10 distinct decisions "
        "overall, and >=10 results plus >=5 decisions per inferential subgroup. "
        "Missing result_ok is excluded, and failed executions never receive synthetic "
        "R. A successful-execution-only CURRENT trade_truth_v1 outcome association is "
        "optional and cannot block primary completion. The scientific definition is "
        "adjudicated, but the existing runner remains fail-closed pending repair."
    ),
    "D3": (
        "Registry intent asks whether enabling the negative-EV execution-policy "
        "gate improves realised expectancy, requiring ev, r_multiple, and "
        "policy_trade_allowed joined across shadow_trades and decision_trace. "
        "The registered legacy_canonical.run_q21 runner reads only shadow "
        "outcomes and never consumes ev, policy_trade_allowed, decision_trace, "
        "or a gate-on/gate-off population. It merely counts completed shadow "
        "records whose decision_snapshot.score is >=0.45 and reports the overall "
        "shadow win rate. The production Stage-4 ev is pre-decision, but is a "
        "comparative price-distance heuristic (p_success * TP distance - "
        "p_failure * SL distance), not expected R or monetary P&L; its semantic "
        "version and probability/reward/risk provenance are not persisted in "
        "decision_trace. policy_trade_allowed is the pre-execution result of the "
        "multi-gate ExecutionPolicy (score, strategy confidence, EV, and RR), not "
        "an EV-gate-only treatment indicator, and DecisionTrace does not persist "
        "that field even though the resolver looks for its literal name. No EV "
        "or policy convenience aliases are defined by the resolver. The runner "
        "requires >=20 shadow records while the registry declares no sample "
        "threshold, counts one completed shadow simulation as an observation, "
        "does not deduplicate canonical opportunities, and can count repeated "
        "horizons separately; account fanout is absent. This is descriptive "
        "score-threshold coverage, not EV calibration or policy evaluation. "
        "Repair requires a versioned pre-decision EV contract with explicit "
        "units/provenance, a persisted EV-gate treatment/counterfactual policy "
        "state distinct from other policy gates, a commensurate subsequent "
        "outcome, deterministic CURRENT opportunity-level pairing, and an "
        "approved gate-effect design and sufficiency rule. Left fail-closed."
    ),
    "D4": (
        "Registry intent asks whether score thresholds are optimal within H4 "
        "regime and market-state segments. legacy_canonical.run_q02 does not "
        "perform that analysis. It applies a fixed [0.30, 0.35, 0.40, 0.45, "
        "0.50] grid to the literal decision_snapshot.score on the pooled shadow "
        "population and reports in-sample win rate and mean R for every nonempty "
        "threshold subset; it neither selects an optimum nor groups outcomes by "
        "regime or market_state. Separately, it filters raw decision traces on "
        "score_neutral and regime only to report a regime-frequency count, with "
        "no trace-to-shadow join. Thus the runner has competing unpaired score "
        "authorities, while the resolver's generic score requirement can accept "
        "score, score_strategy, overall_score, or signal_score; confidence and "
        "p_success are distinct fields and cannot establish score authority. "
        "Score and regime inputs are pre-decision, and shadow R is post-outcome, "
        "but missing shadow pnl_r_multiple defaults to zero in the legacy loader. "
        "The hidden runner minimum is >=20 shadow records, whereas the registry "
        "has coverage gates but no sample threshold or per-context cell minimum. "
        "Its unit is one completed shadow simulation, with no canonical-"
        "opportunity deduplication; account executions are absent but repeated "
        "horizons can inflate the decision-level sample. This is descriptive "
        "pooled threshold filtering, not predictive validation or optimal policy "
        "evaluation. Repair requires one canonical pre-decision score, CURRENT "
        "opportunity/outcome pairing, authoritative regime and market-state "
        "grouping, horizon/deduplication rules, cell sufficiency, and an approved "
        "out-of-sample threshold comparison/optimality criterion. Left fail-closed."
    ),
    "D5": (
        "Registry intent asks which rejected decisions would have succeeded and "
        "requires a decision_trace-to-shadow outcome join. The mapped "
        "legacy_canonical.run_q03 runner reads decision_trace only, defines its "
        "entire population as action == NO_TRADE with a nonempty terminal_stage, "
        "and reports counts of terminal stages and reasons. It does not load "
        "shadow evidence, join canonical_opportunity_id/entity_id, inspect an "
        "outcome, horizon, or R-multiple, or distinguish actual realised trades "
        "from hypothetical evidence. PATTERN_REJECT, NO_TRADE, and RISK_BLOCK "
        "are not given an authoritative equivalence: the runner explicitly "
        "selects only NO_TRADE actions and may merely stratify whatever causes "
        "appear in terminal_stage/reason. Rejection status and reason are "
        "pre-outcome decision facts, but the runner supplies no subsequent label "
        "and therefore cannot leak or estimate success. COMPLETE requires just "
        "one matching trace; the registry declares lineage >=0.80 and outcome "
        "coverage >=0.50 but no sample or per-category threshold. Its unit is one "
        "decision-trace rejection row, not an account execution; rejected "
        "opportunities have zero broker executions. The absent counterfactual "
        "contract would need to treat any horizon shadows as repeated measures "
        "within one canonical opportunity, not independent rejected "
        "opportunities. This is descriptive rejection-funnel reporting, not "
        "counterfactual missed-opportunity research. Repair requires an approved "
        "canonical rejection taxonomy, one rejected canonical-opportunity grain, "
        "a leakage-safe CURRENT hypothetical-outcome authority with explicit "
        "horizon and provenance, deterministic pairing and repeated-measure "
        "handling, explicit separation from realised execution truth, and "
        "category/cell sufficiency rules. Left fail-closed."
    ),
    "L1": (
        "Registry intent asks whether pattern performance degrades over time, "
        "requiring pattern, R-multiple, and entry_time from shadow evidence. "
        "The mapped legacy_canonical.run_q05 runner never reads entry_time, "
        "sorts chronologically, or defines early/late or rolling windows. It "
        "pools completed shadow simulations by decision_snapshot.pattern and "
        "reports win rate and mean R for pattern cells with >=5 observations; "
        "the report is marked COMPLETE whenever any shadow outcome exists. The "
        "legacy shadow adapter combines reconstructed canonical shadows with "
        "research_shadow_trades, carries no timestamp, CURRENT-only guard, "
        "canonical-opportunity identity, or horizon treatment into the analytic "
        "row, and defaults missing pnl_r_multiple to zero. Account execution "
        "fanout is absent, but repeated horizon simulations can count as "
        "independent pattern observations. This is pooled descriptive pattern "
        "performance, not temporal drift, learning, adaptation, or causal "
        "improvement. L1 and E2 are scientifically distinct but share the exact "
        "run_q05 runner and q5_pattern_degradation.json artifact. The runner "
        "emits legacy question_id Q5, which both questions accept, so latest-"
        "report resolution can treat the same artifact as authority for either "
        "claim even though it contains no temporal degradation result. Repair "
        "requires an authoritative CURRENT timestamped pattern-outcome "
        "population, canonical-opportunity/horizon repeated-measure rule, "
        "deterministic temporal windows or trend test with sufficiency, and a "
        "distinct L1 runner/report identity that cannot inherit E2 completion. "
        "Left fail-closed."
    ),
    "L2": (
        "Registry intent asks whether outcome performance improves after "
        "architecture changes, requiring shadow R-multiple, entry_time, and "
        "schema_version. legacy_canonical.run_q15 reads none of those fields or "
        "shadow evidence: it counts JSON files in analysis/reports, treats one "
        "report file as one observation, and always declares COMPLETE, including "
        "when zero reports exist. It records neither an architecture-change "
        "boundary nor pre/post outcome windows, ordering, market-regime, symbol, "
        "strategy, or horizon controls. File count and filesystem enumeration "
        "are not authoritative chronology and do not demonstrate temporal "
        "improvement, system adaptation, or causal benefit. Account fanout is "
        "not consumed, but the runner has no strategy-outcome population at all. "
        "The registry declares no validation or minimum-sample rule, and the "
        "runner imposes none. Repair requires a persisted versioned architecture "
        "change/intervention boundary, authoritative CURRENT pre/post outcomes "
        "with event-time ordering and canonical-opportunity/horizon treatment, "
        "approved mix controls and sufficiency, and a runner/report that measures "
        "the resulting temporal association or an explicitly designed causal "
        "effect. Left fail-closed."
    ),
    "L3": (
        "Registry intent asks whether scoring-weight assumptions, regime "
        "classifications, and strategy mappings remain correct. The mapped "
        "component_reward.run implementation is D1's component-to-outcome "
        "attribution experiment: it selects pattern-detected decision traces "
        "with component dictionaries, joins a shadow outcome primarily by "
        "correlation_id with a symbol+cycle compatibility fallback, and reports "
        "per-component mean-R separation, Pearson correlation, and fixed "
        "component interactions. Although the attribution row carries regime "
        "and strategy fields, the analysis never tests regime classification or "
        "strategy-mapping correctness and does not test architecture adaptation. "
        "One matched decision trace/shadow outcome is the intended observation; "
        "account executions are absent, while duplicate/repeated shadow "
        "identities are indexed to one record rather than governed as repeated "
        "measures. Analysis requires >=5 matched outcomes and >=5 per component "
        "cell (>=3 for interactions), yet the persisted report declares COMPLETE "
        "when merely one matched outcome exists and the registry declares only "
        "coverage gates, not a sample threshold. This is descriptive/associative "
        "component attribution, not evidence that the wider architecture "
        "assumptions remain valid. L3 and D1 are scientifically distinct but "
        "share the exact runner and q1_component_reward.json artifact. The "
        "runner emits legacy question_id Q1, accepted by both questions, so one "
        "artifact can falsely establish report ownership/completion for L3. "
        "Repair requires separately defined tests and authoritative fields for "
        "weight, regime, and strategy-mapping validity, a CURRENT canonical join "
        "and repeated-measure rule, aligned completion/sufficiency, and a "
        "distinct L3 runner/report identity. Left fail-closed."
    ),
    "L4": (
        "Registry intent asks whether market behaviour changes over time enough "
        "to invalidate strategy assumptions, requiring H4 regime, pattern, "
        "R-multiple, and entry_time from shadow_trades plus market_context. The "
        "mapped legacy_canonical.run_q17 runner instead loads trade_truth only, "
        "does not read market_context or shadow evidence, and computes no regime, "
        "pattern, outcome, temporal, drift, stability, or assumption-validity "
        "metric. It reports only the number of trade-truth rows and declares "
        "COMPLETE whenever one exists; its artifact is named "
        "q17_drawdown_precursors.json, reflecting a different legacy purpose. "
        "No timestamp is used, so chronology is undefined rather than based on "
        "authoritative event/entry time. A trade-truth row is account-level live "
        "outcome evidence; without canonical-decision aggregation, legitimate "
        "multi-account fanout could inflate a strategy/market-drift population. "
        "The registry has H4-regime and outcome coverage gates but no sample or "
        "temporal-cell sufficiency threshold, while the runner requires only one "
        "row. This is a descriptive evidence-count placeholder, not temporal "
        "drift, adaptation, or causal improvement research. Repair requires an "
        "authoritative CURRENT market-state/pattern/outcome population, event-"
        "time ordering, canonical-opportunity aggregation and explicit horizon "
        "treatment, defined temporal windows/trend and assumption-invalidation "
        "criteria with cell sufficiency, and an aligned L4 runner/report. Left "
        "fail-closed."
    ),
    "EX5": (
        "Registry intent asks whether SCALP/INTRADAY/EXTENDED horizons require "
        "different exit policies and explicitly says trailing parameters are "
        "tested. exit_depth.run_ex5 does not test or simulate a trailing rule. "
        "It loads only CURRENT completed shadow_runtime_v1 lifecycles, requires "
        "MFE and MAE even though registry readiness does not require MAE, and "
        "groups one shadow lifecycle by identity.evaluated_horizon. It reports "
        "observed shadow realised R, win rate, MFE, MAE, literal mapped shadow "
        "exit-reason counts, realised-R/MFE capture for MFE > 0.05, and MFE "
        "minus realised-R giveback. The only comparison marks capture means as "
        "different when at least two available horizon means span >0.15; it is "
        "an in-sample observational association, not policy evaluation. The "
        "preserved trade_state_progression and entry/exit timestamps are not "
        "consumed, so feasible alternative exits, ordering, and lookahead-safe "
        "decision information are not established. The runner requires >=30 "
        "CURRENT MFE/MAE lifecycles overall and >=10 per reported cell, but "
        "declares COMPLETE when any one cell remains; the registry has coverage "
        "gates and no sample rule. The runner excludes the resolver's separate "
        "research_shadow_trades population, creating an evidence-population "
        "conflict. Account executions are absent, but multiple horizon shadow "
        "lifecycles from one canonical opportunity count independently. These "
        "are shadow simulated closes, not actual broker closes or reconstructed "
        "candidate-policy closes. Repair requires an approved observational "
        "narrowing or a path-aware, chronology-valid candidate-policy simulator "
        "with declared decision inputs and outcomes, one authoritative shadow "
        "population, canonical-opportunity/repeated-horizon handling, and aligned "
        "overall/cell sufficiency and completion rules. Left fail-closed."
    ),
    "EX6": (
        "Registry intent asks whether strategy families require different exit "
        "policies. exit_depth.run_ex6 instead performs an in-sample observational "
        "comparison of completed CURRENT shadow lifecycles grouped by strategy. "
        "Strategy comes from identity.strategy_id or decision_snapshot.strategy, "
        "but the runner accepts only REVERSAL, CONTINUATION, and FALSE_BREAK, "
        "whereas registry wording names REVERSAL, MOMENTUM, and CONTINUATION. "
        "For each surviving cell it reports shadow realised R, win rate, MFE, "
        "MAE, mapped shadow exit-reason counts, realised-R/MFE capture, and "
        "giveback; stop_loss, take_profit, and timeout mapped to max_bars_timeout "
        "are historical shadow categories, not selectable treatments. A >0.15 "
        "span between at least two observed strategy mean-R values is labelled "
        "a difference, but no alternative policy behaviour is simulated and no "
        "causal or policy effect follows from those category differences. The "
        "runner does not consume trade_state_progression or timestamps; MFE, "
        "MAE, realised R, and exit reason are post-outcome diagnostics and cannot "
        "be pre-exit predictive inputs. It requires >=30 CURRENT MFE/MAE "
        "lifecycles overall and >=10 per reported cell yet declares COMPLETE "
        "with one cell; registry readiness has coverage gates but no sample "
        "threshold and requires pattern although the runner does not. The runner "
        "uses only ingested shadow_runtime_v1 while the resolver also admits "
        "research_shadow_trades. One shadow lifecycle is one observation: account "
        "fanout is absent, but repeated horizons for one canonical opportunity "
        "are not aggregated. Shadow closes remain distinct from actual broker or "
        "candidate-policy exits. Repair requires aligned strategy vocabulary and "
        "population/field authority, canonical-opportunity/repeated-horizon rules, "
        "aligned comparative cell sufficiency/completion, and either approved "
        "observational wording or a chronology-valid policy comparison design. "
        "Left fail-closed."
    ),
    "EX7": (
        "Registry intent asks whether TRENDING/RANGING/TRANSITIONAL regimes "
        "require different exit policies. exit_depth.run_ex7 only describes "
        "CURRENT completed shadow lifecycle exit metrics grouped by the frozen "
        "decision_snapshot.h4_regime. It reports observed shadow realised R, win "
        "rate, MFE, MAE, mapped exit-reason counts, realised-R/MFE capture, and "
        "giveback. It performs no between-regime comparison, candidate-rule "
        "simulation, predictive validation, or policy evaluation. Although the "
        "adapter preserves entry/exit facts and forward-appended per-bar "
        "trade_state_progression, the runner consumes neither timestamps nor the "
        "path. Final MFE, MAE, realised R, exit reason, and later path are "
        "post-outcome facts; they establish neither when an alternative exit was "
        "feasible nor what it would have realised, and cannot silently become "
        "pre-exit predictors. The runner requires >=30 CURRENT MFE/MAE lifecycles "
        "overall and >=10 per reported regime but declares COMPLETE with any one "
        "cell; registry readiness has coverage gates, no sample threshold, and "
        "does not require MAE. The runner reads only ingested shadow_runtime_v1 "
        "while the resolver additionally admits research_shadow_trades. Account "
        "executions are absent, but repeated horizon lifecycles from one canonical "
        "opportunity count separately. Shadow timeout/target/stop closes are not "
        "actual broker exits or hypothetical exits under another policy. Repair "
        "requires an approved descriptive narrowing or an ordered, field-audited "
        "path authority and explicit candidate-policy simulation/evaluation, plus "
        "one population authority, opportunity/horizon repeated-measure treatment, "
        "and aligned comparative cell sufficiency/completion. Left fail-closed."
    ),
    "EX8": (
        "Registry intent asks whether candlestick patterns require different "
        "exit policies based on MFE/MAE profiles. exit_depth.run_ex8 only "
        "describes CURRENT completed shadow lifecycles grouped by the literal "
        "decision_snapshot.pattern. It reports observed shadow realised R, win "
        "rate, MFE, MAE, mapped exit-reason counts, realised-R/MFE capture, and "
        "giveback. Its top-five output is ranked by sample count, not exit "
        "performance; it does not identify a best or optimal policy, compare "
        "candidate rules, simulate hypothetical exits, or define an evaluation "
        "design. The preserved trade_state_progression and entry/exit facts are "
        "not consumed, so later MFE/MAE/final outcome cannot establish an "
        "alternative exit or serve as leakage-safe pre-exit information. The "
        "runner requires >=30 CURRENT MFE/MAE lifecycles overall and >=10 per "
        "reported pattern, yet declares COMPLETE with one cell; registry "
        "readiness has pattern/outcome coverage gates but no sample threshold "
        "or comparative-cell rule. The runner uses only ingested "
        "shadow_runtime_v1 whereas the resolver also admits separate "
        "research_shadow_trades. Account executions do not enter the sample, "
        "but multiple horizon shadows for one canonical opportunity can inflate "
        "strategy-exit evidence. Actual broker closes, current-policy shadow "
        "closes, and reconstructed candidate-policy closes remain distinct. "
        "Repair requires an approved descriptive narrowing or a declared "
        "chronology-valid candidate-policy comparison with leakage-safe inputs "
        "and optimality criterion, one authoritative population, explicit "
        "canonical-opportunity/horizon aggregation, and aligned multi-cell "
        "sufficiency/completion. Left fail-closed."
    ),
}


def apply_wave_a4_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Apply evidence-backed A4 overrides while retaining frozen design history."""
    result = dict(definitions)
    for qid, overrides in WAVE_A4_OVERRIDES.items():
        result[qid] = replace(result[qid], **overrides)
    return result
