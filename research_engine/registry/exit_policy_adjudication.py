"""HD09 — frozen V1 science for exit-policy counterfactual research.

This module is governance only.  It does not replay a policy, run an EX
experiment, generate a report, or change runtime behaviour.  Implementations
must satisfy these named contracts without choosing scientific rules locally.
"""
from __future__ import annotations


HD09_ADJUDICATION_VERSION = "hd09_exit_policy_counterfactual_v1"
PATH_SCHEMA = "exit_bar_path_v1"
BASELINE_POLICY_ID = "SHADOW_BASELINE_V1"
ALPHA = 0.05
CONFIDENCE_LEVEL = 0.95
Z_95 = 1.959963984540054


BASELINE_POLICY_V1 = {
    "policy_id": BASELINE_POLICY_ID,
    "policy_version": 1,
    "population": "governed completed shadow_runtime_v1 lifecycles with eligible exit_bar_path_v1",
    "entry_source": "exit_bar_path_v1.entry_price from shadow_runtime_v1 OPEN.construction.entry_price",
    "direction_source": "exit_bar_path_v1.direction from shadow_runtime_v1 OPEN.construction.direction",
    "stop_source": "exit_bar_path_v1.baseline_stop_loss from shadow_runtime_v1 OPEN.construction.stop_loss",
    "target_source": "exit_bar_path_v1.baseline_take_profit from shadow_runtime_v1 OPEN.construction.take_profit",
    "timeout_source": "shadow_runtime_v1 OPEN.simulation_assumptions.timeout_bars",
    "timeout_authority": "core.shadow.assumptions.build_assumptions using core.shadow.models.TIMEOUT_BARS",
    "authoritative_timeout_bars": {"SCALP": 9, "INTRADAY": 96, "EXTENDED": 864},
    "timeout_validation": (
        "Persisted OPEN.simulation_assumptions.timeout_bars must equal the value for the "
        "same OPEN.horizon in core.shadow.models.TIMEOUT_BARS; disagreement is ineligible."
    ),
    "entry_bar_rule": "entry bar is not evaluated; replay begins with the first completed M5 bar after entry",
    "bar_order": "strict increasing canonical UTC M5 timestamps",
    "fill_model": "EXACT_PRICE",
    "slippage_policy": "ZERO",
    "commission_policy": "ZERO",
    "spread_policy": "ZERO_COST",
    "risk_unit": "initial absolute distance between entry and original SL",
    "r_formula": {
        "BUY": "(exit_price - entry_price) / risk_distance",
        "SELL": "(entry_price - exit_price) / risk_distance",
    },
    "barrier_order": ("effective_protective_stop", "take_profit", "timeout"),
    "same_bar_sl_tp_collision": "SL_FIRST",
    "ordinary_barrier_rule_on_timeout_bar": (
        "Evaluate the original/effective protective stop first and TP second; only when neither "
        "barrier is touched does timeout exit at that bar's close."
    ),
    "timeout_price": "authoritative timeout-bar close",
    "baseline_simulation_authority": "core.shadow.runtime.ShadowRuntime.evaluate_bar",
}


BASELINE_REPRODUCTION_CONTRACT = {
    "required": True,
    "rule": (
        "A lifecycle enters any candidate comparison only when an independent SHADOW_BASELINE_V1 "
        "replay over its governed path reproduces every required observed baseline field."
    ),
    "required_observed_fields": (
        "baseline_timeout_bars",
        "baseline_simulation_model_version",
        "observed_exit_reason",
        "observed_exit_price",
        "exit_utc_epoch_s",
        "observed_bars_held",
        "observed_pnl_r_multiple",
        "observed_mfe_r",
    ),
    "exit_reason_rule": "exact canonical token equality: stop_loss, take_profit, or timeout",
    "exit_timestamp_rule": "exact UTC epoch-second equality",
    "bars_held_rule": "exact integer equality",
    "exit_price_rule": "math.isclose with rel_tol=0 and abs_tol=1e-12",
    "r_rule": (
        "Compute unrounded R from exact exit and initial risk; reproduce when Python round(replay_R, 4) "
        "equals the finite persisted observed_pnl_r_multiple within abs_tol=1e-12."
    ),
    "mfe_rule": (
        "Compute baseline-window MFE_R from full favourable M5 bar extremes through and including "
        "the reproduced exit bar; reproduce when round(replay_MFE_R, 4) equals observed_mfe_r "
        "within abs_tol=1e-12.  Full exit-bar range matches shadow_runtime_v1 semantics."
    ),
    "missing_field_rule": "exclude with BASELINE_REPRODUCTION_AUTHORITY_MISSING",
    "disagreement_rule": "exclude with BASELINE_REPRODUCTION_FAILED and retain field-level diagnostics",
    "no_approximation": True,
    "path_extension_required": True,
    "exit_bar_path_v1_required_extension": {
        "source": "the same governed shadow_runtime_v1 OPEN+CLOSE pair already used by exit_bar_path_v1",
        "fields": {
            "baseline_timeout_bars": "OPEN.simulation_assumptions.timeout_bars",
            "baseline_simulation_model_version": "OPEN.simulation_model_version",
            "observed_exit_reason": "CLOSE.exit_reason",
            "observed_exit_price": "CLOSE.exit_price",
            "observed_bars_held": "CLOSE.bars_held",
            "observed_pnl_r_multiple": "CLOSE.outcome.pnl_r_multiple",
            "observed_mfe_r": "CLOSE.outcome.mfe_r",
        },
        "provenance_rule": (
            "All added fields participate in lifecycle_source_digest and analytical_digest; no "
            "fallback to reconstructed, legacy, or account-execution values."
        ),
    },
}


CANDIDATE_POLICIES_V1 = (
    {
        "policy_id": "TRAIL_ACT_0_25R_DIST_0_10R_V1", "policy_type": "TRAILING",
        "activation_r": 0.25, "distance_r": 0.10,
    },
    {
        "policy_id": "TRAIL_ACT_0_50R_DIST_0_25R_V1", "policy_type": "TRAILING",
        "activation_r": 0.50, "distance_r": 0.25,
    },
    {
        "policy_id": "TRAIL_ACT_1_00R_DIST_0_50R_V1", "policy_type": "TRAILING",
        "activation_r": 1.00, "distance_r": 0.50,
    },
    {"policy_id": "REDUCED_TP_0_50R_V1", "policy_type": "REDUCED_TP", "target_cap_r": 0.50},
    {"policy_id": "REDUCED_TP_1_00R_V1", "policy_type": "REDUCED_TP", "target_cap_r": 1.00},
    {"policy_id": "REDUCED_TP_1_50R_V1", "policy_type": "REDUCED_TP", "target_cap_r": 1.50},
    {"policy_id": "TIME_CAP_20_BARS_V1", "policy_type": "TIME_CAP", "bar_cap": 20},
    {"policy_id": "TIME_CAP_60_BARS_V1", "policy_type": "TIME_CAP", "bar_cap": 60},
    {"policy_id": "TIME_CAP_180_BARS_V1", "policy_type": "TIME_CAP", "bar_cap": 180},
)


CANDIDATE_REPLAY_CONTRACT = {
    "vocabulary_closed": True,
    "parameter_search_forbidden": True,
    "outcome_driven_expansion_forbidden": True,
    "common": {
        "original_sl_active": True,
        "original_tp_active_except_reduced_tp_cap": True,
        "baseline_timeout_active": True,
        "path_boundary": "candidate replay may not extend beyond the reproduced baseline path",
        "costs_and_fills": "inherit SHADOW_BASELINE_V1 exact-fill and zero-cost assumptions",
    },
    "trailing": {
        "favourable_extreme": "BUY uses completed-bar high; SELL uses completed-bar low",
        "activation": "extreme excursion from entry reaches activation_r times initial risk_distance",
        "update_cadence": "once after each completed bar that did not exit",
        "effective_next_bar": True,
        "monotonic": "BUY trailing level never decreases; SELL trailing level never increases",
        "effective_stop": (
            "BUY max(original_SL, highest_completed_favourable_extreme - distance_R*risk); "
            "SELL min(original_SL, lowest_completed_favourable_extreme + distance_R*risk)."
        ),
        "activation_bar_rule": "a stop computed from a bar cannot trigger within that same bar",
        "same_bar_trailing_tp_collision": "effective protective stop first, then original TP",
    },
    "reduced_tp": {
        "target": (
            "The closer of original TP and entry +/- target_cap_r*initial_risk_distance; a cap "
            "never moves the target farther from entry."
        ),
        "barrier_order": "original SL first, capped TP second, baseline timeout third",
    },
    "time_cap": {
        "effective_cap": "min(candidate bar_cap, baseline_timeout_bars)",
        "cap_bar_order": "original SL first, original TP second, then cap exit at bar close",
        "bar_count": "first post-entry completed M5 bar is bar 1",
        "exit_reason": (
            "time_cap only when candidate bar_cap < baseline_timeout_bars; when the effective "
            "cap equals baseline timeout the canonical reason remains timeout"
        ),
    },
}


COMMON_ANALYTICAL_CONTRACT = {
    "row_grain": "one completed lifecycle x one candidate policy",
    "pairing_identity": ("shadow_trade_id", "canonical_opportunity_id", "trade_horizon"),
    "cluster_identity": "canonical_opportunity_id",
    "account_fanout_rule": "account/broker executions do not create exit-policy observations",
    "duplicate_rule": "one row per pairing identity and candidate; duplicates fail closed",
    "repeated_horizon_rule": "horizons from one opportunity are repeated measurements",
    "horizon_weight": "w_oh = 1 / k_o",
    "horizon_weight_definition": (
        "k_o is the number of counterfactual-eligible lifecycle horizons for canonical opportunity o "
        "in the exact question analytical population; weights sum to one per opportunity within each policy."
    ),
}


CLUSTERED_INFERENCE = {
    "model": "candidate cell-means fixed-effect model with no redundant intercept",
    "design": "one deterministic indicator column per frozen candidate in frozen vocabulary order",
    "weight": COMMON_ANALYTICAL_CONTRACT["horizon_weight"],
    "bread": "A = X' W X",
    "cluster_score": "s_o = sum_rows_in_opportunity(w_oh * x * residual)",
    "covariance": "A^-1 (sum_o s_o s_o') A^-1",
    "finite_sample_correction": "none (CR0); do not apply HC/CR1 or row-independent covariance",
    "reference_distribution": "asymptotic standard normal for scalar contrasts",
    "degenerate_se_rule": "SE=0 gives p=1 when estimate=0 and p=0 otherwise",
    "confidence_level": CONFIDENCE_LEVEL,
    "critical_value": Z_95,
    "alpha": ALPHA,
    "test_sidedness": "two-sided",
}


EX1_CONTRACT = {
    "question_id": "EX1",
    "report_filename": "ex1_exit_efficiency.json",
    "candidates": "all nine CANDIDATE_POLICIES_V1",
    "paired_endpoint": "candidate simulated R - reproduced SHADOW_BASELINE_V1 R",
    "model": CLUSTERED_INFERENCE,
    "null_per_candidate": "population weighted mean paired R difference = 0",
    "holm_family": "exactly nine two-sided candidate p-values; one Holm step-down family",
    "positive_improvement": (
        "candidate estimate > 0, Holm-adjusted p <= 0.05, and its unadjusted 95% clustered "
        "interval lower bound > 0"
    ),
    "raw_mean_insufficient": True,
    "valid_null": "sufficient estimable analysis with no policy meeting positive_improvement is COMPLETE",
}


EX2_CONTRACT = {
    "question_id": "EX2",
    "report_filename": "ex2_profit_retention.json",
    "candidates": tuple(
        item["policy_id"] for item in CANDIDATE_POLICIES_V1
        if item["policy_type"] == "TRAILING"
    ),
    "mfe_window": (
        "first post-entry M5 bar through and including the reproduced baseline exit bar; use full "
        "bar high for BUY and low for SELL, matching shadow_runtime_v1 exit-bar MFE semantics"
    ),
    "mfe_r": "max(0, favourable excursion / initial risk_distance)",
    "eligibility": "finite baseline-window MFE_R strictly greater than zero",
    "candidate_endpoint": "candidate exit_R / baseline-window-MFE_R",
    "baseline_endpoint": "reproduced baseline exit_R / baseline-window-MFE_R",
    "paired_endpoint": "candidate retention - baseline retention",
    "model": CLUSTERED_INFERENCE,
    "null_per_candidate": "population weighted mean paired retention difference = 0",
    "holm_family": "exactly three trailing-policy two-sided p-values; one Holm step-down family",
    "positive_improvement": (
        "candidate estimate > 0, Holm-adjusted p <= 0.05, and unadjusted 95% clustered "
        "interval lower bound > 0"
    ),
    "observational_ratio_substitution_forbidden": True,
    "valid_null": "sufficient estimable analysis with no supported positive trailing effect is COMPLETE",
}


EX9_CONTRACT = {
    "question_id": "EX9",
    "report_filename": "ex9_timeout_loss.json",
    "candidates": "all nine CANDIDATE_POLICIES_V1",
    "endpoint_a": {
        "name": "paired_timeout_indicator_change",
        "baseline_indicator": "1 iff reproduced baseline exit_reason == timeout, else 0",
        "candidate_indicator": "1 iff candidate exit_reason is timeout or time_cap, else 0",
        "paired_endpoint": "candidate indicator - baseline indicator",
        "null": "population weighted mean paired timeout-indicator change = 0",
        "supported_reduction": "estimate < 0 and family Holm-adjusted p <= 0.05",
    },
    "endpoint_b": {
        "name": "baseline_timeout_loss_converted_positive",
        "population": "reproduced baseline exit_reason == timeout and reproduced baseline R < 0",
        "candidate_endpoint": "1 iff candidate simulated R > 0, else 0; zero is not positive",
        "null": "population weighted mean positive-conversion indicator = 0",
        "supported_conversion": "estimate > 0 and family Holm-adjusted p <= 0.05",
        "minimum_distinct_canonical_opportunities": 30,
    },
    "model": CLUSTERED_INFERENCE,
    "holm_family": (
        "one family of exactly 18 two-sided p-values in frozen candidate order: nine endpoint A "
        "tests followed by nine endpoint B tests"
    ),
    "family_completeness": "all 18 tests must be valid and estimable; missing tests cannot be dropped",
    "valid_null": "sufficient estimable 18-test analysis with no supported directional effect is COMPLETE",
    "observed_timeout_non_timeout_comparison_forbidden": True,
}


HETEROGENEITY_CONTRACT = {
    "questions": ("EX5", "EX6", "EX7", "EX8"),
    "estimand": "heterogeneity of candidate-minus-reproduced-baseline R effect",
    "candidates": "all nine CANDIDATE_POLICIES_V1",
    "model": "paired effect ~ candidate + dimension + candidate:dimension",
    "weighting_and_cluster": COMMON_ANALYTICAL_CONTRACT,
    "covariance": CLUSTERED_INFERENCE["covariance"],
    "finite_sample_correction": CLUSTERED_INFERENCE["finite_sample_correction"],
    "global_test": "cluster-robust Wald chi-square test that all candidate:dimension interactions are zero",
    "global_alpha": ALPHA,
    "followup_gate": "global p <= 0.05",
    "followups": "within-candidate pairwise eligible-level contrasts",
    "multiplicity": "one Holm family per question across every predeclared follow-up contrast",
    "minimum_total_paired_lifecycles": 200,
    "minimum_distinct_canonical_opportunities": 200,
    "minimum_distinct_opportunities_per_candidate_dimension_cell": 30,
    "minimum_levels": 2,
    "level_selection": (
        "include every canonical level meeting the pre-outcome coverage/cell thresholds in canonical "
        "authority order; outcome means, effects, or p-values may not select levels"
    ),
    "valid_null": "sufficient estimable non-rejection is COMPLETE",
    "dimensions": {
        "EX5": {
            "name": "trade_horizon", "source": "exit_bar_path_v1.trade_horizon",
            "levels": ("SCALP", "INTRADAY", "EXTENDED"), "coverage": 0.50,
        },
        "EX6": {
            "name": "strategy_family",
            "source": "shadow_runtime_v1 OPEN.live_facts.strategy using core.v10.strategy_family.StrategyFamily",
            "levels": (
                "LIQUIDITY_SWEEP_REVERSAL", "FALSE_BREAK", "TREND_CONTINUATION",
                "BREAKOUT_EXPANSION", "MEAN_REVERSION", "RANGE_REACTION",
            ),
            "excluded_level": "NONE",
            "historical_label_mapping_forbidden": True, "coverage": 0.50,
        },
        "EX7": {
            "name": "market_regime", "source": "shadow_runtime_v1 OPEN.live_facts.regime",
            "h4_fallback_forbidden": True, "coverage": 0.80,
        },
        "EX8": {
            "name": "candlestick_pattern", "source": "shadow_runtime_v1 OPEN.live_facts.pattern",
            "coverage": 0.50,
        },
    },
    "later_path_extension": (
        "EX6-EX8 require their named immutable OPEN.live_facts field in governed analytical "
        "evidence/provenance; no inference or fallback from other fields."
    ),
}


EX10_CONTRACT = {
    "question_id": "EX10",
    "report_filename": "ex10_walk_forward.json",
    "chronology": "exit_bar_path_v1.entry_utc_epoch_s ascending",
    "tie_rule": "all rows sharing canonical_opportunity_id remain together; ties use canonical opportunity ID",
    "candidate_set": "all nine CANDIDATE_POLICIES_V1",
    "fold_type": "five-fold expanding-window validation",
    "minimum_training_opportunities": 200,
    "minimum_validation_opportunities_per_fold": 50,
    "minimum_valid_folds": 5,
    "minimum_total_distinct_opportunities": 450,
    "purge": "remove training lifecycles whose exit UTC is not before validation entry UTC",
    "embargo": "one M5 bar: retained training exit must be <= validation start UTC - 300 seconds",
    "selection": (
        "training evidence only; select the highest opportunity-weighted mean paired R improvement "
        "among candidates with positive estimate and nine-policy Holm-adjusted p <= 0.05"
    ),
    "no_training_winner": "select baseline/no-improvement for that fold",
    "tie_break": "frozen CANDIDATE_POLICIES_V1 order",
    "validation": "freeze selected policy before evaluating unseen validation opportunities",
    "validation_endpoint": "selected candidate R - reproduced baseline R",
    "aggregation": "pool each opportunity's first unseen validation result exactly once across folds",
    "inference": CLUSTERED_INFERENCE,
    "supported_oos_improvement": "pooled estimate > 0, two-sided p <= 0.05, and 95% CI lower bound > 0",
    "valid_null": "sufficient leakage-safe evaluation with no supported OOS improvement is COMPLETE",
}


SAMPLE_AND_READINESS_CONTRACT = {
    "common_path_coverage": 0.95,
    "EX1": {"minimum_paired_lifecycles": 200, "minimum_distinct_opportunities": 200},
    "EX2": {"minimum_paired_lifecycles": 200, "minimum_distinct_opportunities": 200},
    "EX9": {
        "minimum_paired_lifecycles": 200, "minimum_distinct_opportunities": 200,
        "endpoint_b_minimum_distinct_opportunities": 30,
    },
    "EX5_EX8": {
        "minimum_paired_lifecycles": 200, "minimum_distinct_opportunities": 200,
        "minimum_cell_opportunities": 30,
    },
    "EX10": {
        "minimum_distinct_opportunities": 450,
        "minimum_training_opportunities": 200,
        "minimum_validation_opportunities_per_fold": 50,
        "minimum_valid_folds": 5,
    },
    "BLOCKED": (
        "missing/invalid HD09 authority, exit_bar_path_v1 authority or provenance; unknown policy; "
        "baseline reproduction failure; invalid/unstable model or multiplicity family; invalid report"
    ),
    "WAITING_DATA": (
        "valid authority and machinery but insufficient governed path coverage, paired lifecycle, "
        "distinct-opportunity, cell, baseline-timeout-loss, or OOS-fold evidence"
    ),
    "COMPLETE": (
        "valid CURRENT report from sufficient governed evidence and the exact question contract; "
        "a sufficiently evaluated null/no-reliable-difference result is COMPLETE"
    ),
    "self_declared_status_forbidden": True,
}


REPORT_OWNERSHIP = {
    "EX1": "ex1_exit_efficiency.json",
    "EX2": "ex2_profit_retention.json",
    "EX5": "ex5_horizon_exit.json",
    "EX6": "ex6_strategy_exit.json",
    "EX7": "ex7_regime_exit.json",
    "EX8": "ex8_pattern_exit.json",
    "EX9": "ex9_timeout_loss.json",
    "EX10": "ex10_walk_forward.json",
}


REPORT_VALIDITY_CONTRACT = {
    "sole_owner": "each question owns only REPORT_OWNERSHIP[question_id]",
    "compatibility_aliases": (),
    "required_provenance": (
        "exact exit_bar_path_v1 population digest",
        "SHADOW_BASELINE_V1 identity/version",
        "exact candidate identities and parameters",
        "baseline reproduction exclusions and diagnostics",
        "exact analytical population digest",
        "horizon weights and opportunity clusters",
        "inference configuration",
        "complete Holm family membership and adjusted p-values",
    ),
    "current_rule": "all evidence components and policy/configuration identities must be CURRENT and exact",
    "change_sensitivity": "evidence, policy, eligibility, model, or multiplicity changes must change report provenance",
    "reorder_invariance": True,
}


HD09_ADJUDICATED_CONTRACT = {
    "version": HD09_ADJUDICATION_VERSION,
    "implementation_blocked_until_decision": False,
    "path_schema": PATH_SCHEMA,
    "baseline": BASELINE_POLICY_V1,
    "baseline_reproduction": BASELINE_REPRODUCTION_CONTRACT,
    "candidate_policies": CANDIDATE_POLICIES_V1,
    "candidate_replay": CANDIDATE_REPLAY_CONTRACT,
    "common_analytical": COMMON_ANALYTICAL_CONTRACT,
    "ex1": EX1_CONTRACT,
    "ex2": EX2_CONTRACT,
    "ex9": EX9_CONTRACT,
    "ex5_ex8": HETEROGENEITY_CONTRACT,
    "ex10": EX10_CONTRACT,
    "sample_readiness": SAMPLE_AND_READINESS_CONTRACT,
    "report_ownership": REPORT_OWNERSHIP,
    "report_validity": REPORT_VALIDITY_CONTRACT,
    "claim_boundary": (
        "Results describe simulated policy outcomes on the governed historical population; they do "
        "not establish future profitability, causal market truth, production suitability, or prop readiness."
    ),
}
