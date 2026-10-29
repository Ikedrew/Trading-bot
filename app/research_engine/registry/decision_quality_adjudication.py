"""Frozen HD04 science for D2, D3, D4, D5, and X5.

This is governance authority only.  It does not load evidence, execute a
research runner, write a report, or change production decisions.
"""
from __future__ import annotations

from typing import Final


HD04_VERSION: Final = "hd04_decision_quality_authority_v1"
HD04_TARGETS: Final = ("D2", "D3", "D4", "D5", "X5")

STATISTICAL_UNIT: Final = (
    "One canonical opportunity/decision is one statistical observation. The "
    "required lineage identity is the nonempty composite (entity_id, "
    "canonical_opportunity_id); canonical_opportunity_id defines the statistical "
    "cluster and entity_id proves the lifecycle lineage. Account fanout, broker "
    "executions, horizons, and repeated persisted representations never enlarge N."
)
IDENTITY_FAILURE_RULE: Final = (
    "Missing or partial composite identity is ineligible and explicitly accounted. "
    "Byte-equivalent replay duplicates may collapse once with an audit count; "
    "nonidentical duplicates, multiple entity_id values for one canonical opportunity, "
    "or conflicting predictors/actions/outcomes fail closed. An unresolved conflict in "
    "required authority is BLOCKED, never an extra observation."
)

DECISION_TIME_FIELD: Final = "decision_trace.timestamp_utc"
PREDICTOR_TIME_FIELD: Final = "opportunity_assessments.assessed_at_utc"
OUTCOME_TIME_FIELD: Final = "shadow_trades.simulated_outcome.exit_timestamp"
CHRONOLOGY_CONTRACT: Final = (
    "The canonical completed-decision time is decision_trace.timestamp_utc. Predictor "
    "values are frozen in the same opportunity's opportunity_assessments record at "
    "assessed_at_utc. Eligibility requires assessed_at_utc <= "
    "decision_trace.timestamp_utc < simulated_outcome.exit_timestamp, all parseable "
    "UTC instants in one CURRENT epoch. Report/file creation time, filesystem time, "
    "shadow CLOSE ingestion time, execution-result time, and reconstructed likely "
    "ordering are forbidden. Missing or contradictory ordering is BLOCKED authority "
    "for that opportunity and cannot be relabelled WAITING_DATA."
)
NO_LEAKAGE_CONTRACT: Final = (
    "Every predictor, threshold membership, action, and treatment assignment must be "
    "persisted or deterministically fixed from persisted information no later than the "
    "canonical decision time. realised R, MFE, MAE, exit reason, exit time, future bars, "
    "broker PnL, and every other post-decision outcome may appear only as responses. "
    "No response may construct or repair p_success, score, predicted EV, threshold "
    "membership, rejection reason, or treatment assignment."
)

PROBABILITY_AUTHORITY: Final = {
    "value": "opportunity_assessments.p_success",
    "source": "opportunity_assessments.probability_source",
    "version": "opportunity_assessments.probability_model_version",
    "cross_check": "decision_trace.p_success",
    "domain": "finite [0,1]",
    "semantic": (
        "ProbabilityEstimator's decision-time forecast of success, where success is "
        "the subsequently completed PRIMARY_HORIZON_SIMULATION outcome having "
        "pnl_r_multiple > 0. It is a forecast probability suitable for empirical "
        "calibration testing, not proof that it is already calibrated. Generic "
        "confidence, strategy_confidence, score_neutral, or score_strategy cannot "
        "substitute. Empty source/version, disagreement with decision_trace.p_success, "
        "nonfinite/out-of-domain values, or mixed versions are BLOCKED authority."
    ),
}

PREDICTED_EV_AUTHORITY: Final = {
    "version": "predicted_ev_r_v1",
    "persisted_value": "opportunity_assessments.ev",
    "persisted_risk": "opportunity_assessments.ev_risk",
    "persisted_reward": "opportunity_assessments.ev_reward",
    "persisted_rr": "opportunity_assessments.rr_effective",
    "probability": PROBABILITY_AUTHORITY["value"],
    "r_units": (
        "predicted_ev_r_v1 = opportunity_assessments.ev / "
        "opportunity_assessments.ev_risk = p_success * rr_effective - "
        "(1 - p_success), with strictly positive finite persisted ev_risk"
    ),
    "semantic": (
        "The authoritative forecast is the raw decision-time ExpectedValueResult EV. "
        "predicted_ev_r_v1 is only its lossless R-unit normalization using the same "
        "persisted decision-time risk and reward; both equalities must agree within "
        "declared serialization tolerance. It is neither reconstructed from realised R "
        "nor substituted from score/confidence. Production Stage-4 aliases, missing "
        "model version, invalid geometry, nonfinite values, or inconsistent formulae "
        "are BLOCKED authority. D3 and X5 must use this exact same authority."
    ),
}

SCORE_AUTHORITY: Final = {
    "field": "decision_trace.score_strategy",
    "domain": "finite [0,1] strategy-weighted confluence composite",
    "direction": "higher means a stronger predicted opportunity",
    "production_threshold": 0.35,
    "semantic": (
        "score_strategy is the pre-decision strategy-weighted composite consumed by "
        "the engine's score gate. score_neutral, final_score, generic score aliases, "
        "confidence, p_success, EV, component values, and shadow snapshot scores are "
        "forbidden substitutes. The persisted value must agree with the same "
        "opportunity assessment. Missing, nonfinite, out-of-domain, or conflicting "
        "values fail closed. The frozen 0.35 production threshold may be evaluated "
        "historically but this research cannot change it."
    ),
}

SHARED_OUTCOME_AUTHORITY: Final = (
    "Exactly one completed CURRENT shadow_trades lifecycle with "
    "identity.shadow_type == PRIMARY_HORIZON_SIMULATION for the same composite "
    "identity supplies simulated_outcome.pnl_r_multiple in R units and "
    "simulated_outcome.exit_timestamp. Alternative horizons are excluded rather than "
    "averaged; account/broker fanout is excluded; replay-identical OPEN/CLOSE lifecycle "
    "representations collapse once. Multiple primary horizons, conflicting completed "
    "outcomes, identity disagreement, nonfinite R, missing exit time, or outcome time "
    "not strictly after decision time fail closed. Missing outcome is missing, never 0."
)

ACTION_TAXONOMY: Final = {
    "ACCEPTED_DECISION": "action=EXECUTE and terminal_stage=execute",
    "EV_FILTER_REJECTED": (
        "action=NO_TRADE, terminal_stage=ev_policy, producer reason code "
        "NEGATIVE_EXPECTED_VALUE, ev_gate_enabled=true, ev_rejection_bypassed=false, "
        "and persisted ev_positive=false"
    ),
    "OTHER_DECISION_REJECTED": (
        "action=NO_TRADE with a resolved producer terminal_stage/reason other than the "
        "EV-specific class; strategy/signal, scoring, policy, swing, and data-invalid "
        "classes remain separate"
    ),
    "RISK_PLAN_BLOCKED": "action=NO_TRADE and producer terminal_stage=risk",
    "EXECUTION_BLOCKED": (
        "requires a separately governed execution-result authority; it is never "
        "inferred from action, absence of execution, or a missing broker fill"
    ),
    "AMBIGUOUS": (
        "unknown stage/reason, contradictory same-rank reasons, inconsistent action, "
        "or unresolved multi-reason precedence; excluded and reported fail-closed"
    ),
}
MULTI_REASON_RULE: Final = (
    "Use the producer's structural terminal_stage as the primary reason and canonical "
    "pipeline order only to order separately persisted reasons. Preserve all reasons. "
    "A unique earliest stage may classify once; contradictory reasons at that stage "
    "are AMBIGUOUS. Absence of execution alone never proves rejection."
)

COMMON_INFERENCE: Final = {
    "alpha": 0.05,
    "confidence_level": 0.95,
    "split": "chronological 60 discovery / 40 later unseen validation minimum",
    "resampling": (
        "deterministic opportunity-level permutation tests and percentile bootstrap "
        "confidence intervals, seeded from the immutable input snapshot digest"
    ),
    "multiplicity": (
        "No adjustment across D2/D3/D4/D5/X5 because they are separate canonical "
        "questions. Secondary diagnostics are descriptive. If multiple subgroup "
        "contrasts are promoted to inferential claims within one report, declare the "
        "family before evaluation and apply deterministic Holm at alpha 0.05."
    ),
}

QUESTION_CONTRACTS: Final = {
    "D2": {
        "primary_estimand": (
            "Later-validation probability calibration: joint calibration intercept=0 "
            "and slope=1 for binary 1[pnl_r_multiple>0] regressed on logit(p_success)."
        ),
        "null": "H0: calibration intercept=0 and calibration slope=1 (joint two-sided Wald test).",
        "diagnostics": "Brier score, ECE, and discovery-frozen decile reliability table.",
        "sufficiency": "N>=100 paired; discovery>=60; validation>=40; >=2 validation bins each N>=10.",
        "completion": "A calibrated, miscalibrated, null, or adverse valid result may COMPLETE.",
    },
    "D3": {
        "population": (
            "Opportunities proven to have reached the enabled, non-bypassed EV gate "
            "with valid predicted_ev_r_v1 and a primary-horizon outcome."
        ),
        "treatment": "EV_PASS versus EV_FILTER_REJECTED at the historical zero-EV boundary.",
        "primary_estimand": "Later-validation mean realised-R difference EV_PASS minus EV_FILTER_REJECTED.",
        "null": "H0: the two groups have equal mean realised R; two-sided opportunity permutation test.",
        "sufficiency": "N>=100 paired; discovery>=60; validation>=40; each validation group>=10.",
        "completion": "Observational predictive filter value only; null/adverse results may COMPLETE.",
    },
    "D4": {
        "primary_estimand": "Spearman association between decision-time score_strategy and later realised R in validation.",
        "null": "H0: Spearman rho=0; two-sided opportunity permutation test.",
        "secondary": (
            "Realised-R difference above versus below the frozen historical 0.35 "
            "production threshold, with confidence interval; no threshold optimisation "
            "and no production change."
        ),
        "sufficiency": "N>=100 paired; discovery>=60; validation>=40; validation >=15 on each threshold side.",
        "completion": "No association, null threshold effect, or adverse direction may COMPLETE.",
    },
    "D5": {
        "population": (
            "Resolved pre-outcome decision rejections with governed primary-horizon "
            "counterfactual shadow outcomes; invalid/unavailable and ambiguous records "
            "are excluded with accounting."
        ),
        "comparator": "ACCEPTED_DECISION opportunities evaluated on the same shadow counterfactual authority.",
        "primary_estimand": "Later-validation mean counterfactual-R difference rejected minus accepted.",
        "null": "H0: rejected and accepted mean counterfactual R are equal; two-sided opportunity permutation test.",
        "sufficiency": (
            "Rejected N>=100 paired; rejected discovery>=60; rejected validation>=40; "
            "accepted validation>=15. Any class/reason inferential claim requires >=15 "
            "per compared validation subgroup."
        ),
        "completion": "Good filtering, missed opportunity, null, mixed, or adverse valid results may COMPLETE.",
    },
    "X5": {
        "primary_estimand": "Later-validation Spearman association of predicted_ev_r_v1 with subsequently realised R.",
        "null": "H0: Spearman rho=0; two-sided opportunity permutation test.",
        "diagnostics": (
            "Bias, MAE, RMSE, slope, discovery-frozen calibration bins, and positive- "
            "versus non-positive-EV summaries are secondary. Numeric calibration is "
            "not claimed unless D2 probability calibration and magnitude diagnostics support it."
        ),
        "sufficiency": (
            "N>=100 paired; discovery>=60; validation>=40; validation positive-EV and "
            "non-positive-EV groups each>=15; interpreted bins each>=10."
        ),
        "completion": "No association, miscalibration, null, or adverse valid results may COMPLETE.",
    },
}

READINESS_SEMANTICS: Final = (
    "COMPLETE requires the exact frozen authorities, strict identity/chronology, "
    "CURRENT provenance, all stated sample/cell gates, deterministic inference, and "
    "the question's uniquely owned VALID_CURRENT report. WAITING_DATA applies only "
    "when those semantic authorities and machinery are valid and only sample quantity "
    "or coverage is short. BLOCKED applies to missing/ambiguous identity, chronology, "
    "predictor/version, treatment/action, counterfactual outcome lineage, mixed epoch, "
    "or conflicting authority. Report existence or self-declared status cannot complete."
)

HISTORICAL_AUTHORITY: Final = {
    "D2": (
        "AUTHORITY_APPEARS_AVAILABLE: opportunity_assessments persists p_success, "
        "probability_source, probability_model_version, assessed_at_utc, and canonical "
        "identity. Bare decision_trace history without the strict matching assessment "
        "is ambiguous and BLOCKED; actual CURRENT coverage remains to be measured."
    ),
    "D3": (
        "AUTHORITY_APPEARS_AVAILABLE: opportunity_assessments persists raw EV, risk, "
        "reward, RR, p_success and version, while decision_trace persists EV-gate/action "
        "facts. Only strict agreeing joins qualify; actual CURRENT coverage remains to be measured."
    ),
    "D4": (
        "AUTHORITY_APPEARS_AVAILABLE: CURRENT decision_trace persists score_strategy, "
        "identity, decision time, action and terminal authority; assessment agreement "
        "is mandatory. Actual CURRENT coverage remains to be measured."
    ),
    "D5": (
        "AUTHORITY_APPEARS_AVAILABLE for engine decision rejections and governed shadow "
        "counterfactuals. Runtime risk/execution blocks are not inferable from absent "
        "execution and require separate authority; actual CURRENT coverage remains to be measured."
    ),
    "X5": (
        "AUTHORITY_APPEARS_AVAILABLE through the exact D3 predicted_ev_r_v1 authority "
        "and primary-horizon shadow R. Strict source/version/formula agreement is "
        "mandatory; actual CURRENT coverage remains to be measured."
    ),
}

QUESTION_SEPARATION: Final = (
    "D2 tests probability calibration; D3 tests the predictive separation of the "
    "historical EV filter; D4 tests score association and its frozen threshold; D5 "
    "tests rejected-opportunity counterfactual quality; X5 tests continuous predicted "
    "EV ranking against realised R. Each owns a distinct estimand, runner, report, "
    "validity, readiness, and completion state. No report or generic decision-quality "
    "summary may complete another question."
)

FUTURE_IDENTITIES: Final = {
    "D2": ("research_engine.experiments.d2_paired_calibration.run_d2", "d2_paired_probability_calibration_v1.json"),
    "D3": ("research_engine.experiments.d3_predicted_ev_gate.run_d3", "d3_predicted_ev_gate_validation_v1.json"),
    "D4": ("research_engine.experiments.d4_score_threshold_validation.run_d4", "d4_score_threshold_validation_v1.json"),
    "D5": ("research_engine.experiments.d5_rejected_opportunity_validation.run_d5", "d5_rejected_opportunity_validation_v1.json"),
    "X5": ("research_engine.experiments.x5_predicted_ev_realised_r_validation.run_x5", "x5_predicted_ev_realised_r_validation_v1.json"),
}

STAGE4_GAPS: Final = (
    "Measure CURRENT coverage of strict decision_trace-to-opportunity_assessments "
    "agreement, including probability source/model version and EV formula fields.",
    "Measure strict decision/assessment time preceding primary shadow exit time; do "
    "not reconstruct chronology for records lacking it.",
    "Persist/use separately governed runtime risk and execution-block authority if a "
    "future D5 extension intends to study those mechanisms; absence of execution is insufficient.",
    "Verify PRIMARY_HORIZON_SIMULATION uniqueness; historical multi-horizon averages "
    "are not HD04 outcome authority.",
)

