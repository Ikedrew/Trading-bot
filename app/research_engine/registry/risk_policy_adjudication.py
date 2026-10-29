"""HD10 — frozen V1 science for risk-policy effectiveness and simulation research.

This module is governance only.  It does not change risk limits, halt thresholds,
daily-loss behaviour, or position sizing, and it does not run an R experiment,
generate a report, or write state.  Implementations must satisfy these named
contracts instead of choosing scientific rules locally.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

HD10_ADJUDICATION_VERSION = "hd10_risk_policy_estimation_and_simulation_v1"
BASELINE_RISK_POLICY_ID = "RISK_POLICY_BASELINE_V1"
RUIN_MODEL_VERSION = "ruin_model_v1"
ALPHA = 0.05
CONFIDENCE_LEVEL = 0.95
Z_95 = 1.959963984540054

ADJUDICATED_OPTION = "A"
ADJUDICATED_OPTION_DEFINITION = (
    "One canonical versioned R1-R5 risk-policy research contract: shared principles, one frozen "
    "baseline risk-policy identity, distinct R1/R2 estimands, versioned R3-R5 simulation models, "
    "an account-safe unit of analysis, a chronological validation rule, and an explicit production "
    "boundary.  Governance only: this adjudication implements no runtime behaviour."
)


GUARD_TAXONOMY_V1 = (
    {
        "guard_id": "SPREAD",
        "guard_type": "PRE_TRADE_COST_GUARD",
        "scientific_role": (
            "Vetoes an otherwise eligible opportunity when the executable spread makes the planned "
            "1R risk uneconomic."
        ),
        "required_evidence_role": (
            "per-opportunity spread state at decision time plus the guard verdict, taken from the "
            "canonical decision trace"
        ),
    },
    {
        "guard_id": "CORRELATION",
        "guard_type": "PORTFOLIO_EXPOSURE_GUARD",
        "scientific_role": (
            "Vetoes an opportunity that would concentrate correlated exposure beyond the declared "
            "portfolio limit."
        ),
        "required_evidence_role": (
            "per-opportunity existing correlated exposure at decision time plus the guard verdict, "
            "taken from the canonical decision trace"
        ),
    },
    {
        "guard_id": "REGIME",
        "guard_type": "CONTEXT_GUARD",
        "scientific_role": (
            "Vetoes an opportunity whose governed regime condition is declared untradeable."
        ),
        "required_evidence_role": (
            "per-opportunity governed regime label at decision time plus the guard verdict, taken "
            "from the canonical decision trace"
        ),
    },
    {
        "guard_id": "DAILY_LOSS",
        "guard_type": "SESSION_STATE_GUARD",
        "scientific_role": (
            "Suspends new risk once the realised daily loss limit has been reached."
        ),
        "required_evidence_role": (
            "per-session realised loss state at decision time plus the guard verdict, taken from the "
            "canonical decision trace"
        ),
    },
)


def baseline_risk_policy_identity_hash(
    policy: Mapping[str, Any] | None = None,
) -> str:
    """Deterministic 16-character identity digest of the frozen baseline contract.

    The digest is a sha256 over canonical JSON (sorted keys, compact separators,
    NaN forbidden) of the contract content with any previously recorded
    ``identity_hash`` removed, so the digest is stable and self-consistent.
    """
    material = dict(BASELINE_RISK_POLICY_V1 if policy is None else policy)
    material.pop("identity_hash", None)
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


_BASELINE_RISK_POLICY_V1_BASE: dict[str, Any] = {
    "policy_id": BASELINE_RISK_POLICY_ID,
    "policy_version": 1,
    "population": (
        "governed completed shadow_runtime_v1 lifecycles with canonical decision-to-outcome lineage"
    ),
    "risk_unit": "initial absolute distance between entry and the original protective stop (1R)",
    "outcome_source": "governed shadow_runtime_v1 lifecycle outcome R-multiple",
    "decision_source": "governed canonical decision trace verdict per canonical opportunity",
    "guard_taxonomy": tuple(guard["guard_id"] for guard in GUARD_TAXONOMY_V1),
    "exposure_controls": (
        "the declared portfolio exposure/correlation limits, the per-trade risk fraction, and the "
        "daily loss limit in force during the evidence window"
    ),
    "substitutions_forbidden": (
        "reconstructed, inferred, account-execution-derived, or forward-filled guard verdicts, risk "
        "state, or outcomes may never stand in for canonical historical authority"
    ),
    "identity_rule": (
        "every R1-R5 report provenance must carry the RISK_POLICY_BASELINE_V1 policy identity, "
        "version, and identity hash of the contract actually used"
    ),
    "identity_hash_rule": (
        "sha256 over this contract serialised as canonical JSON with sort_keys=True, "
        "separators=(',', ':') and allow_nan=False; the hex digest is truncated to 16 characters"
    ),
    "missing_authority_rule": (
        "fail closed with BASELINE_RISK_POLICY_AUTHORITY_MISSING: without a CURRENT and exact "
        "historical RISK_POLICY_BASELINE_V1 authority no R1-R5 estimation, report, or COMPLETE "
        "status may be produced, and no default policy is assumed"
    ),
    "drift_rule": (
        "any change to identity, version, or digest that is not accompanied by a new HD10 "
        "adjudication version is BLOCKED with BASELINE_RISK_POLICY_IDENTITY_DRIFT"
    ),
}

BASELINE_RISK_POLICY_V1: dict[str, Any] = {
    **_BASELINE_RISK_POLICY_V1_BASE,
    "identity_hash": baseline_risk_policy_identity_hash(_BASELINE_RISK_POLICY_V1_BASE),
}


CLUSTERED_INFERENCE = {
    "cluster_identity": "canonical_opportunity_id",
    "finite_sample_correction": (
        "none (CR0); do not apply HC/CR1 or row-independent covariance"
    ),
    "reference_distribution": "asymptotic standard normal for scalar contrasts",
    "confidence_level": CONFIDENCE_LEVEL,
    "alpha": ALPHA,
    "test_sidedness": "two-sided",
    "minimum_clusters": 50,
}


ACCOUNT_GRAIN_CONTRACT = {
    "decision_grain": "canonical_opportunity_id",
    "row_grain": "one eligible observation per canonical opportunity per evaluation window",
    "account_fanout_rule": (
        "multiple broker/account executions of the same canonical opportunity are one observation: "
        "they may never be counted, paired, weighted, or simulated as independent draws, so no "
        "account fanout pseudoreplication is possible"
    ),
    "opportunity_outcome_rule": (
        "the opportunity outcome is the equal-weight mean of its eligible account-execution "
        "R-multiples, and k_o records how many executions were combined"
    ),
    "weighting": (
        "w_o = 1 / k_o so that each canonical opportunity contributes total weight one and the "
        "weights sum to one"
    ),
    "required_grain_fields": ("canonical_opportunity_id", "account_id"),
    "cross_account_heterogeneity": (
        "account dispersion is a declared descriptive statistic only; it is never extra evidence, "
        "never a larger sample, and never a separate estimand"
    ),
    "violation_state": "BLOCKED with ACCOUNT_FANOUT_PSEUDOREPLICATION",
}

R1_CONTRACT = {
    "question_id": "R1",
    "scientific_intent": (
        "Global risk-control effectiveness: does the whole risk layer change expectancy and survival?"
    ),
    "estimand_kind": (
        "observational treatment contrast on governed opportunities, reported as a weighted mean "
        "R-multiple difference"
    ),
    "unit_of_analysis": ACCOUNT_GRAIN_CONTRACT["decision_grain"],
    "data_sources": ("decision_trace", "shadow_trades"),
    "treatment_arm": (
        "canonical opportunity allowed by the risk layer, measured by its governed realised outcome "
        "R-multiple"
    ),
    "counterfactual_arm": (
        "canonical opportunity blocked by the risk layer, measured by its governed shadow outcome "
        "R-multiple; the blocked shadow outcome is the only admissible counterfactual"
    ),
    "primary_estimand": (
        "opportunity-weighted mean R-multiple difference: allowed-arm outcome mean minus "
        "risk-layer-blocked counterfactual outcome mean, with the pre-decision adjustment set "
        "declared before any outcome is inspected"
    ),
    "secondary_estimand": (
        "survival contrast: difference in the probability of reaching the frozen RUIN_MODEL_V1 ruin "
        "threshold between the two arms over the frozen horizon"
    ),
    "forbidden_endpoints": (
        "any count of RISK_BLOCK events, guard firings, ledger rows, or trades: a larger block count "
        "is never evidence of benefit",
        "any endpoint read from the legacy q10_guard_efficacy.json artifact",
        "win rate alone, or any endpoint that ignores the R-multiple magnitude of the blocked arm",
    ),
    "adjustment_rule": (
        "pre-decision covariate adjustment only; the adjustment set is frozen before estimation and "
        "outcome-driven covariate selection is forbidden"
    ),
    "exclusion_rule": (
        "opportunities without a governed outcome in either arm are excluded, with per-arm exclusion "
        "counts and diagnostics reported"
    ),
    "multiplicity": "one two-sided Holm family over the primary and secondary estimands",
    "valid_null": "sufficient governed coverage with no reliable effect is COMPLETE",
    "claim_boundary": (
        "observational contrast on historical shadow evidence; it does not establish a randomised "
        "causal effect, future benefit, or production authority"
    ),
}

R2_CONTRACT = {
    "question_id": "R2",
    "scientific_intent": (
        "Per-guard attribution: did each individual spread, correlation, regime, and daily-loss guard "
        "improve final expectancy?"
    ),
    "estimand_kind": "per-guard observational attribution contrast on governed opportunities",
    "unit_of_analysis": ACCOUNT_GRAIN_CONTRACT["decision_grain"],
    "data_sources": ("decision_trace", "shadow_trades"),
    "guard_taxonomy": GUARD_TAXONOMY_V1,
    "attribution_grain": (
        "one guard-exclusive treatment: a blocked opportunity enters guard g's contrast only when g "
        "is the only firing guard, so opportunities where two or more guards fire are excluded from "
        "every per-guard contrast and reported once as a declared multi-guard residual stratum"
    ),
    "per_guard_estimand": (
        "opportunity-weighted mean R-multiple difference: guard-g-blocked counterfactual outcomes "
        "minus matched allowed outcomes inside the same pre-decision stratum definition"
    ),
    "matching_rule": (
        "the pre-decision matching/covariate set is declared before outcome inspection and "
        "outcome-driven matching is forbidden"
    ),
    "multiplicity": "one two-sided Holm family across exactly the four declared guard contrasts",
    "interaction_claims": (
        "guard-interaction claims are not supported by this design and require a separate predeclared "
        "design; they may never be inferred from the four single-guard contrasts"
    ),
    "no_borrowed_evidence": (
        "a guard-specific claim may never be satisfied by another guard's contrast, by the aggregate "
        "R1 result, or by a RISK_BLOCK count"
    ),
    "aggregate_forbidden": (
        "a single aggregate RISK_BLOCK quantity can never attribute value to an individual guard"
    ),
    "valid_null": "sufficient governed coverage with no reliable per-guard effect is COMPLETE",
    "claim_boundary": (
        "an observational per-guard contrast on historical shadow evidence; it does not establish a "
        "randomised causal effect or a production change"
    ),
}

RUIN_MODEL_V1 = {
    "model_version": RUIN_MODEL_VERSION,
    "population": "governed completed shadow lifecycles with an outcome R-multiple",
    "minimum_samples": 50,
    "outcome_coverage": 0.95,
    "risk_per_trade_pct": 0.01,
    "ruin_threshold_pct": 0.50,
    "acceptable_ruin_threshold": 0.05,
    "analytical_estimator": (
        "classical gambler's ruin odds-ratio approximation on the observed R distribution"
    ),
    "analytical_formula": "P(ruin) = ((q * avg_loss_r) / (p * avg_win_r)) ** (1 / ruin_threshold_pct)",
    "no_edge_rule": "a non-positive edge makes the analytical ruin estimate 1.0 rather than undefined",
    "monte_carlo": {
        "estimator": "i.i.d. resampling of observed R-multiples with replacement",
        "n_simulations": 10000,
        "trade_horizon": 5000,
        "interval": "95% Wilson score interval over the frozen simulation set",
    },
    "seed_contract": {
        "required": True,
        "seed_base": 20240101,
        "per_simulation_seed": (
            "simulation s uses random.Random(seed_base + s) for s = 1..n_simulations"
        ),
        "ordering": "ascending, single use, one independent generator per simulation",
        "process_global_state_forbidden": True,
    },
    "agreement_rule": (
        "the analytical and Monte Carlo estimates must both be reported and must agree within the "
        "predeclared absolute tolerance of 0.05; disagreement invalidates the result instead of "
        "producing a finding"
    ),
    "agreement_tolerance": 0.05,
    "determinism_rule": (
        "re-running the frozen model with the frozen seed set reproduces the Monte Carlo estimate and "
        "its interval exactly"
    ),
    "nondeterminism_forbidden": True,
    "claim_boundary": (
        "a model-conditional estimate on the observed R distribution; it is not a guarantee about "
        "future accounts or market conditions"
    ),
}


R3_CONTRACT = {
    "question_id": "R3",
    "scientific_intent": (
        "Given the measured edge, variance, and sizing, what is the probability that the account "
        "eventually reaches catastrophic drawdown?"
    ),
    "ruin_model": RUIN_MODEL_V1,
    "estimand_kind": "model-conditional probability of reaching the frozen ruin threshold",
    "independent_verification": (
        "the analytical and the frozen Monte Carlo estimators must both be reported and must agree "
        "within the predeclared tolerance"
    ),
    "required_outputs": (
        "analytical ruin probability",
        "Monte Carlo ruin probability with interval",
        "survival probability and expected survival trades",
        "required sample size and attained confidence",
        "model version, parameters, and the exact seed set used",
    ),
    "no_alternative_model": (
        "no alternative ruin model, ruin threshold, risk-per-trade, or simulation horizon may be "
        "substituted or searched within this contract version"
    ),
    "valid_null": "sufficient governed coverage with an estimable ruin probability is COMPLETE",
    "claim_boundary": RUIN_MODEL_V1["claim_boundary"],
}

HALT_THRESHOLD_GRID_V1 = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)


R4_CONTRACT = {
    "question_id": "R4",
    "scientific_intent": (
        "At what realised drawdown should trading be suspended because historical recovery "
        "probability becomes unacceptable?"
    ),
    "grid": HALT_THRESHOLD_GRID_V1,
    "grid_closed": True,
    "outcome_driven_expansion_forbidden": True,
    "parameter_search_forbidden": True,
    "simulation": {
        "population": "governed completed shadow lifecycles with an outcome R-multiple",
        "minimum_samples": 50,
        "outcome_coverage": 0.95,
        "order": "strictly increasing canonical entry UTC; ties broken by canonical opportunity ID",
        "risk_per_trade_pct": 0.01,
        "equity_model": "sequential compounding of observed R-multiples at the frozen risk per trade",
        "no_look_ahead": (
            "a threshold's recovery statistics may only use the drawdown episodes and the bars at or "
            "after their breaches"
        ),
    },
    "definitions": {
        "drawdown": "peak-to-trough decline of the simulated equity curve since its running peak",
        "breach": "first lifecycle of an episode where drawdown reaches or exceeds the threshold",
        "recovery": "equity regains the peak that preceded the breach",
        "unrecovered_at_series_end": (
            "an episode still open at series end counts as unrecovered; it is never imputed, "
            "censored, or dropped"
        ),
    },
    "halt_rule": (
        "the smallest grid threshold whose historical recovery probability is below 0.50 becomes the "
        "research halt threshold"
    ),
    "no_halt_fallback": (
        "when no tested threshold reaches the halt rule, the research halt threshold is recorded as "
        "the grid maximum 0.50"
    ),
    "resume_rule": "the research resume threshold is half of the research halt threshold",
    "required_outputs": (
        "recovery probability per grid threshold",
        "breach and recovery episode counts per threshold",
        "mean and maximum recovery duration",
        "the full-grid sensitivity of the recommendation",
    ),
    "runtime_effect": "none",
    "production_directive": False,
    "valid_null": (
        "sufficient governed coverage with no threshold reaching the halt rule is COMPLETE and is "
        "reported as the grid-maximum fallback"
    ),
    "claim_boundary": (
        "historical recovery statistics on one simulated path; the output is an advisory research "
        "threshold and never a runtime halt instruction"
    ),
}

SIZING_MODELS_V1 = (
    {"model_id": "FIXED_RISK_0_50_PCT", "family": "FIXED_RISK", "risk_fraction": 0.005},
    {"model_id": "FIXED_RISK_1_00_PCT", "family": "FIXED_RISK", "risk_fraction": 0.01},
    {"model_id": "FIXED_RISK_2_00_PCT", "family": "FIXED_RISK", "risk_fraction": 0.02},
    {"model_id": "FULL_KELLY_V1", "family": "KELLY", "kelly_multiplier": 1.0},
    {"model_id": "HALF_KELLY_V1", "family": "FRACTIONAL_KELLY", "kelly_multiplier": 0.5},
    {"model_id": "QUARTER_KELLY_V1", "family": "FRACTIONAL_KELLY", "kelly_multiplier": 0.25},
    {
        "model_id": "FIXED_LOT_V1",
        "family": "FIXED_LOT",
        "lot_rule": (
            "one constant canonical lot size for every lifecycle, so risk per lifecycle is whatever "
            "that constant lot implies"
        ),
    },
    {
        "model_id": "DYNAMIC_RISK_V1",
        "family": "DYNAMIC",
        "rule": (
            "the risk fraction is a predeclared deterministic function of pre-decision state only, "
            "with no outcome feedback inside the evaluation window"
        ),
    },
)


R5_CONTRACT = {
    "question_id": "R5",
    "scientific_intent": (
        "Which position sizing model maximises long-term growth while respecting acceptable drawdown?"
    ),
    "models": SIZING_MODELS_V1,
    "vocabulary_closed": True,
    "declared_families": ("FIXED_RISK", "KELLY", "FRACTIONAL_KELLY", "FIXED_LOT", "DYNAMIC"),
    "unknown_model_rule": (
        "any model or parameter outside SIZING_MODELS_V1 is BLOCKED; nothing may be added after "
        "outcomes are inspected"
    ),
    "objective": "long-term growth: maximise the geometric mean growth factor per lifecycle",
    "objective_estimator": (
        "exp(mean(ln(1 + risk_fraction * R))) computed on the frozen evaluation window"
    ),
    "forbidden_objectives": (
        "Sharpe ratio, return/volatility ratios, and the legacy sharpe_approx column: they are "
        "diagnostics at most and never the selection criterion",
        "arithmetic total return or terminal equity alone",
        "any objective chosen or re-fitted after outcomes are inspected",
    ),
    "eligibility": (
        "a model is selectable only when its maximum drawdown is at or below 0.30 and its ruin "
        "probability under RUIN_MODEL_V1 is at or below the frozen acceptable threshold"
    ),
    "max_acceptable_drawdown": 0.30,
    "kelly_formula": "f* = (p * b - q) / b with b = avg_win_r / avg_loss_r and q = 1 - p",
    "kelly_inputs": (
        "governed observed win rate and mean win/loss R on the frozen evaluation window only"
    ),
    "negative_kelly_rule": (
        "a Kelly fraction at or below zero makes the whole Kelly family ineligible; it is never "
        "traded as zero risk"
    ),
    "no_live_feedback": (
        "sizing outputs are research results only and may not feed live risk or lot sizing"
    ),
    "valid_null": (
        "sufficient governed coverage with no eligible model is COMPLETE with an explicit "
        "no-eligible-model finding"
    ),
    "claim_boundary": (
        "sequential replay of one observed R distribution; ordering, variance, and edge are not "
        "re-estimated forward"
    ),
}

VALIDATION_CHRONOLOGY = {
    "order": ("R3", "R4", "R5"),
    "rule": (
        "registry dependency order is respected: R3 must be COMPLETE or an explicit valid null "
        "before R4 is evaluated, and R4 before R5"
    ),
    "windows": {
        "calibration": (
            "the earliest 70% of eligible canonical opportunities in canonical entry-chronology order"
        ),
        "validation": (
            "the most recent 30% of eligible canonical opportunities, read once after the freeze"
        ),
    },
    "minimum_validation_opportunities": 50,
    "purge": (
        "calibration evidence whose outcome window closes at or after the first validation entry UTC "
        "is excluded"
    ),
    "freeze_rule": (
        "R3 ruin inputs, R4 recovery statistics, and R5 sizing inputs are estimated on calibration "
        "evidence only and frozen before the validation window is read"
    ),
    "no_retrofit": (
        "changing an earlier question's frozen evidence after a later question was validated "
        "invalidates the later question's result"
    ),
    "dependencies": {"R4": ("R3",), "R5": ("E1", "R3")},
}


SAMPLE_AND_READINESS_CONTRACT = {
    "common_cluster_minimum": 50,
    "R1": {
        "lineage_coverage": 0.80,
        "outcome_coverage": 0.50,
        "minimum_allowed_arm_opportunities": 50,
        "minimum_blocked_counterfactual_opportunities": 50,
    },
    "R2": {
        "lineage_coverage": 0.80,
        "outcome_coverage": 0.50,
        "minimum_guard_exclusive_opportunities_per_guard": 30,
    },
    "R3": {"outcome_coverage": 0.95, "minimum_distinct_opportunities": 50},
    "R4": {
        "outcome_coverage": 0.95,
        "minimum_distinct_opportunities": 50,
        "minimum_breached_grid_thresholds": 1,
    },
    "R5": {"outcome_coverage": 0.95, "minimum_distinct_opportunities": 50},
    "BLOCKED": (
        "missing or invalid HD10 authority; baseline risk-policy authority, identity, or hash "
        "missing, stale, or drifted; unknown guard or sizing model; account fanout present; "
        "non-reproducible or process-global randomness; invalid multiplicity family; self-declared "
        "status"
    ),
    "WAITING_DATA": (
        "valid authority and machinery but insufficient governed coverage, arm, guard-exclusive, "
        "cluster, or breach evidence"
    ),
    "COMPLETE": (
        "valid CURRENT report from sufficient governed evidence and the exact question contract; a "
        "sufficiently evaluated null is COMPLETE"
    ),
    "self_declared_status_forbidden": True,
}

REPORT_OWNERSHIP = {
    "R1": "r1_risk_layer_effectiveness.json",
    "R2": "r2_guard_attribution.json",
    "R3": "r3_probability_of_ruin.json",
    "R4": "r4_drawdown_threshold.json",
    "R5": "r5_position_sizing.json",
}


LEGACY_REPORT_COMPATIBILITY = {
    "q10_guard_efficacy.json": ("Q10",),
}


REPORT_VALIDITY_CONTRACT = {
    "sole_owner": (
        "each question owns only REPORT_OWNERSHIP[question_id]; R1 and R2 never share an artifact"
    ),
    "compatibility_aliases": (
        "q10_guard_efficacy.json stays a legacy Q10 compatibility artifact only and can never "
        "satisfy R1 or R2"
    ),
    "required_provenance": (
        "RISK_POLICY_BASELINE_V1 identity, version, and identity hash",
        "exact question ID and contract version",
        "exact analytical population digest and cluster identity",
        "per-arm or per-guard exclusion counts and diagnostics",
        "frozen model version, parameters, and seed set for R3-R5",
        "inference configuration and complete multiplicity family membership",
        "chronological calibration/validation split identity",
    ),
    "current_rule": "all evidence components and policy/model identities must be CURRENT and exact",
    "change_sensitivity": (
        "evidence, policy, eligibility, model, seed, or multiplicity changes must change report "
        "provenance"
    ),
    "research_only": True,
}


PRODUCTION_APPLICATION_BOUNDARY = {
    "authority": "research only",
    "may_not": (
        "change live risk-per-trade, lot sizing, or the daily loss limit",
        "install a halt, resume, or suspend threshold in the runtime",
        "relax or reinterpret prop-firm challenge constraints, which are exogenous inputs",
        "gate, promote, or block any other research question",
    ),
    "application_requires": (
        "a separate human production-application decision that names the owner, effective date, "
        "rollback plan, and the exact contract version it applies"
    ),
    "report_requirement": (
        "every R1-R5 report must carry a research-only warning and no runtime directive"
    ),
    "claim_boundary": (
        "Results describe risk-control and simulation behaviour on the governed historical "
        "population; they do not establish future survival, causal market truth, production "
        "suitability, or prop-account readiness."
    ),
}

SHARED_RISK_PRINCIPLES = (
    "One question, one estimand, one report: no question inherits another question's validity, "
    "finding, or completion.",
    "Every unit of analysis is a canonical opportunity; account executions are one observation, so "
    "account fanout can never manufacture evidence.",
    "Counts are never evidence: RISK_BLOCK totals, guard-firing frequencies, and ledger row counts "
    "cannot establish effectiveness or attribution.",
    "Every result carries an explicit frozen model/version identity and a baseline identity hash, and "
    "missing historical authority fails closed instead of defaulting.",
    "Observational contrasts are labelled observational; only predeclared, chronologically validated "
    "designs may support validation claims.",
    "Simulation parameters, seeds, and vocabularies are frozen before outcomes are inspected, and "
    "outcome-driven expansion is forbidden.",
    "Nothing in R1-R5 is a production directive; any application requires a separate human "
    "production-application decision.",
)


HD10_ADJUDICATED_CONTRACT = {
    "version": HD10_ADJUDICATION_VERSION,
    "option": ADJUDICATED_OPTION,
    "option_definition": ADJUDICATED_OPTION_DEFINITION,
    "implementation_blocked_until_decision": False,
    "questions": ("R1", "R2", "R3", "R4", "R5"),
    "principles": SHARED_RISK_PRINCIPLES,
    "baseline": BASELINE_RISK_POLICY_V1,
    "guard_taxonomy": GUARD_TAXONOMY_V1,
    "account_grain": ACCOUNT_GRAIN_CONTRACT,
    "inference": CLUSTERED_INFERENCE,
    "r1": R1_CONTRACT,
    "r2": R2_CONTRACT,
    "ruin_model": RUIN_MODEL_V1,
    "r3": R3_CONTRACT,
    "r4": R4_CONTRACT,
    "r5": R5_CONTRACT,
    "validation_chronology": VALIDATION_CHRONOLOGY,
    "sample_readiness": SAMPLE_AND_READINESS_CONTRACT,
    "report_ownership": REPORT_OWNERSHIP,
    "legacy_report_compatibility": LEGACY_REPORT_COMPATIBILITY,
    "report_validity": REPORT_VALIDITY_CONTRACT,
    "production_boundary": PRODUCTION_APPLICATION_BOUNDARY,
    "claim_boundary": PRODUCTION_APPLICATION_BOUNDARY["claim_boundary"],
}
