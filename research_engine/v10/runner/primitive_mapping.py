"""
Question-to-Primitive Mapping.

Maps each of the 45 questions to the analysis primitives it requires,
based on the question's analysis_type and declared views.

Also provides question-specific primitive parameters where a question's
semantic fields differ from primitive defaults.

A new question can reuse existing primitives without runner modification.
Only genuinely new analytical capabilities require a new primitive.
"""

from __future__ import annotations

from research_engine.v10.universes.models import AnalysisType, ViewType


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS TYPE → PRIMARY PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════════

# Maps AnalysisType to the primary primitive(s) that handle it.
# Some analysis types map to multiple primitives (composite analysis).
ANALYSIS_TYPE_PRIMITIVES: dict[str, list[str]] = {
    AnalysisType.EXPECTANCY.value: ["expectancy"],
    AnalysisType.DISTRIBUTION.value: ["distribution", "expectancy"],
    AnalysisType.COMPARISON.value: ["comparison"],
    AnalysisType.CORRELATION.value: ["predictive_power"],
    AnalysisType.CALIBRATION.value: ["calibration"],
    AnalysisType.SEGMENTATION.value: ["segmentation", "expectancy"],
    AnalysisType.TEMPORAL.value: ["degradation"],
    AnalysisType.SIMULATION.value: ["expectancy", "distribution"],
    AnalysisType.COUNTERFACTUAL.value: ["comparison", "expectancy"],
    AnalysisType.DEGRADATION.value: ["degradation"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION-SPECIFIC PRIMITIVE PARAMETERS
# ═══════════════════════════════════════════════════════════════════════════════

# These override primitive defaults for questions where the universe's semantic
# field names differ from the primitive's expected parameter names.
# Only needed when the default is wrong for a specific question.

QUESTION_PARAMETERS: dict[str, dict[str, object]] = {
    # ─── Execution Universe ────────────────────────────────────────────────────
    # E-009: Trade Duration vs Outcome
    # predictive_power defaults to feature_field='score' but E-009 tests duration.
    "E-009": {
        "feature_field": "duration_seconds",
        "outcome_field": "r_multiple",
    },
    # E-010: Risk:Reward Ratio Effectiveness
    # comparison defaults to group_field='regime' but E-010 compares intended R:R.
    # Since intended R:R isn't a categorical field, we compare by exit_reason instead
    # (SL hit vs TP hit reveals whether R:R structure is working).
    "E-010": {
        "group_field": "exit_reason",
        "metric_field": "r_multiple",
    },

    # ─── Decision Universe ─────────────────────────────────────────────────────
    # D-004: Rejection Stage Analysis (analysis_type changed to SEGMENTATION)
    # Hypothesis: Where are trades rejected?
    # terminal_reason is categorical (97% coverage). Segmentation shows counts per stage.
    "D-004": {
        "dimensions": ["terminal_reason"],
        "metric_field": "r_multiple",
    },
    # D-007: Risk Gate Value
    # comparison should group by whether risk approved or rejected.
    "D-007": {
        "group_field": "risk_approved",
        "metric_field": "score",
    },

    # ─── Market Universe ───────────────────────────────────────────────────────
    # M-001: Regime Predicts Outcomes — segment by regime
    "M-001": {
        "dimensions": ["regime"],
        "metric_field": "r_multiple",
    },
    # M-002: HTF Alignment Value
    "M-002": {
        "feature_field": "htf_alignment_strength",
        "outcome_field": "r_multiple",
    },
    # M-003: Volatility State Impact — segment by regime + volatility
    "M-003": {
        "dimensions": ["regime", "volatility_state"],
        "metric_field": "r_multiple",
    },
    # M-004: Market Structure Clarity
    "M-004": {
        "feature_field": "h1_structural_clarity",
        "outcome_field": "r_multiple",
    },
    # M-005: Location Quality Impact — segment by location type
    "M-005": {
        "dimensions": ["location_type"],
        "metric_field": "r_multiple",
    },
    # M-006: Session Edge Variation — segment by session
    "M-006": {
        "dimensions": ["session"],
        "metric_field": "r_multiple",
    },

    # ─── Strategy Universe ─────────────────────────────────────────────────────
    # S-001: Strategy Family Expectancy — segment by family
    "S-001": {
        "dimensions": ["family"],
        "metric_field": "r_multiple",
    },
    # S-002: Pattern Expectancy — segment by pattern
    "S-002": {
        "dimensions": ["pattern"],
        "metric_field": "r_multiple",
    },
    # S-003: Strategy Selection Accuracy
    "S-003": {
        "predicted_field": "confidence",
        "outcome_field": "r_multiple",
    },

    # ─── Cross-angle ──────────────────────────────────────────────────────────
    # DM-001: Decision Quality Under Regime — segment by regime
    "DM-001": {
        "dimensions": ["regime"],
        "metric_field": "r_multiple",
    },
    # DM-003: Rejection Rate by Market State — segment by regime
    "DM-003": {
        "dimensions": ["regime"],
        "metric_field": "r_multiple",
    },
    # MS-001: Strategy x Regime — segment by both
    "MS-001": {
        "dimensions": ["regime", "family"],
        "metric_field": "r_multiple",
    },
    # MS-002: Pattern x Market Context
    "MS-002": {
        "dimensions": ["regime", "pattern"],
        "metric_field": "r_multiple",
    },

    # ─── Tier 1 Repairs (verified 45-question audit) ──────────────────────────
    # D-005: Opportunity Quality Predictive Value
    # Hypothesis: Does opportunity_quality predict r_multiple?
    # predictive_power default feature=score is wrong for this question.
    "D-005": {
        "feature_field": "opportunity_quality",
        "outcome_field": "r_multiple",
    },
    # DS-001: Strategy Confidence Calibration
    # Hypothesis: Is strategy confidence calibrated to outcomes?
    # calibration default predicted=p_success has 3% coverage. confidence has 54%.
    "DS-001": {
        "predicted_field": "confidence",
        "outcome_field": "r_multiple",
    },
    # DS-002: Strategy Conditions vs Outcome
    # Hypothesis: Do conditions_met predict outcome?
    # predictive_power default feature=score. conditions_met is integer (numeric valid).
    "DS-002": {
        "feature_field": "conditions_met",
        "outcome_field": "r_multiple",
    },
    # EM-001: Regime-Conditioned Expectancy
    # Hypothesis: Does expectancy differ across regimes?
    # segmentation default dimensions=[symbol] doesn't answer the question.
    "EM-001": {
        "dimensions": ["regime"],
        "metric_field": "r_multiple",
    },
    # ES-001: Execution Quality by Strategy
    # Hypothesis: Do strategy families have different execution quality?
    # family is categorical, 20% coverage in Execution Universe.
    "ES-001": {
        "dimensions": ["family"],
        "metric_field": "r_multiple",
    },
    # DMS-001: Decision × Strategy × Market
    # Hypothesis: Does decision quality vary by strategy×regime?
    # Both fields categorical, available on EXECUTE population.
    "DMS-001": {
        "dimensions": ["regime", "family"],
        "metric_field": "r_multiple",
    },
    # D-006: Opportunity Failure Characterisation
    # Hypothesis: What characterises false positives?
    # Segmentation by opportunity_state (VALID/INVALID/WATCHING) shows outcome
    # by opportunity classification. Field is categorical, 60% coverage.
    "D-006": {
        "dimensions": ["opportunity_state"],
        "metric_field": "r_multiple",
    },
    # E-003: Exit Reason Distribution (analysis_type changed to SEGMENTATION)
    # Hypothesis: What % exit via SL vs TP?
    # exit_reason is categorical (100% coverage). Segmentation shows counts+mean_r.
    "E-003": {
        "dimensions": ["exit_reason"],
        "metric_field": "r_multiple",
    },

    # ─── Shadow Research Questions ────────────────────────────────────────────
    # SD-004: Rejection Stage Counterfactual Expectancy
    # Segment shadow R by the terminal_reason of the originating decision.
    # Requires entity_id join to Decision Universe for terminal_reason field.
    "SD-004": {
        "dimensions": ["terminal_reason"],
        "metric_field": "r_multiple",
    },
    # SD-005: Shadow Horizon Comparison
    # Compare R across horizons (SCALP vs INTRADAY vs EXTENDED).
    "SD-005": {
        "group_field": "trade_horizon",
        "metric_field": "r_multiple",
    },
    # SD-006: Shadow Strategy Expectancy
    # Segment shadow R by strategy_id.
    "SD-006": {
        "dimensions": ["strategy_id"],
        "metric_field": "r_multiple",
    },
    # SD-007: Shadow Regime Expectancy
    # Segment shadow R by regime.
    "SD-007": {
        "dimensions": ["regime"],
        "metric_field": "r_multiple",
    },
}


def resolve_primitives_for_question(
    analysis_type: str,
    views: tuple,
) -> list[str]:
    """
    Determine which primitives a question requires from its contract.

    Args:
        analysis_type: The question's declared AnalysisType value.
        views: Tuple of ViewType values the question declares.

    Returns:
        List of primitive names to execute.
    """
    # Primary primitives from analysis type
    primitives = list(ANALYSIS_TYPE_PRIMITIVES.get(analysis_type, ["expectancy"]))

    # Add view-required primitives
    if ViewType.ANOMALOUS in views:
        if "anomaly_analysis" not in primitives:
            primitives.append("anomaly_analysis")
    if ViewType.EXCEPTIONAL in views:
        if "exceptional_analysis" not in primitives:
            primitives.append("exceptional_analysis")

    return primitives


def build_full_mapping(
    questions: tuple,
) -> dict[str, list[str]]:
    """
    Build the complete question_id → primitives mapping for all questions.

    Args:
        questions: The canonical question bank tuple.

    Returns:
        Dict mapping question_id to list of primitive names.
    """
    mapping: dict[str, list[str]] = {}
    for q in questions:
        primitives = resolve_primitives_for_question(
            q.analysis_type.value, q.views
        )
        mapping[q.question_id] = primitives
    return mapping
