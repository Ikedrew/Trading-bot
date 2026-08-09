"""
Question-to-Primitive Mapping.

Maps each of the 45 questions to the analysis primitives it requires,
based on the question's analysis_type and declared views.

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
