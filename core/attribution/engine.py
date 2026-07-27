"""
Evidence Attribution Engine — decomposes assessment scores into factor contributions.

Reads ONLY from OpportunityAssessment (components + weights_used + scores).
Never modifies scoring. Pure decomposition.

Usage:
    from core.attribution import compute_attribution

    attribution = compute_attribution(assessment=opportunity_assessment)
    # attribution.total_score → 0.62
    # attribution.contributions[0].name → "Bias Alignment"
    # attribution.contributions[0].contribution → 0.144
    # attribution.top_contributors → top 3 by contribution
"""

from __future__ import annotations

from typing import Any

from core.attribution.model import EvidenceContribution, ScoreAttribution


# ─── WEIGHT PROFILES (must match scoring engine exactly) ──────────────────────
# These are READ-ONLY references. The scoring engine is the authority.
# Attribution just explains what the scoring engine already computed.

_GLOBAL_WEIGHTS = {
    "pattern_quality": 0.14,
    "bias_alignment": 0.18,
    "market_quality": 0.08,
    "trend_alignment": 0.10,
    "chop_clarity": 0.06,
    "volatility_quality": 0.07,
    "bias_stability": 0.07,
    "confirmation_pre": 0.06,
    "htf_alignment": 0.14,
    "h4_alignment": 0.10,
}

# Human-readable names for each component
_DISPLAY_NAMES = {
    "pattern_quality": "Pattern Quality",
    "bias_alignment": "Bias Alignment",
    "market_quality": "Market Quality",
    "trend_alignment": "Trend Alignment",
    "chop_clarity": "Chop Clarity",
    "volatility_quality": "Volatility Quality",
    "bias_stability": "Bias Stability",
    "confirmation_pre": "Confirmation",
    "htf_alignment": "HTF Alignment",
    "h4_alignment": "H4 Alignment",
}


def compute_attribution(*, assessment: Any) -> ScoreAttribution:
    """
    Decompose the assessment's score into per-factor contributions.

    Reads assessment.components (raw values) and assessment.weights_used
    to reconstruct the exact contribution of each factor.

    Never raises — returns empty attribution on error.
    Never modifies scoring.

    Args:
        assessment: OpportunityAssessment (frozen analytical snapshot)

    Returns:
        ScoreAttribution (frozen decomposition)
    """
    if assessment is None:
        return ScoreAttribution(
            contributions=(),
            total_score=0.0,
            metadata={"error": "no_assessment"},
        )

    try:
        return _build_attribution(assessment)
    except Exception:
        return ScoreAttribution(
            contributions=(),
            total_score=0.0,
            metadata={"error": "attribution_failed"},
        )


def _build_attribution(assessment: Any) -> ScoreAttribution:
    """Internal attribution computation — may raise."""
    components = getattr(assessment, "components", {})
    weights_used = getattr(assessment, "weights_used", "global_fallback")
    score_strategy = getattr(assessment, "score_strategy", 0.0)
    score_neutral = getattr(assessment, "score_neutral", 0.0)

    # Determine which weights to use for attribution
    # We attribute the STRATEGY score (the one that matters for policy)
    if weights_used == "strategy_specific":
        # Strategy-specific weights — try to import them
        weights = _resolve_strategy_weights(assessment)
        attributed_score = score_strategy
    else:
        weights = _GLOBAL_WEIGHTS
        attributed_score = score_neutral

    # Build contributions
    contributions: list[EvidenceContribution] = []
    computed_total = 0.0

    for factor_name, weight in weights.items():
        raw_value = components.get(factor_name, 0.0)
        contribution = round(weight * raw_value, 6)
        computed_total += contribution

        contributions.append(EvidenceContribution(
            name=_DISPLAY_NAMES.get(factor_name, factor_name),
            weight=weight,
            raw_value=raw_value,
            contribution=contribution,
        ))

    # Sort by contribution (largest impact first)
    contributions.sort(key=lambda c: c.contribution, reverse=True)

    return ScoreAttribution(
        contributions=tuple(contributions),
        total_score=attributed_score,
        weights_profile=weights_used,
        metadata={
            "symbol": getattr(assessment, "symbol", ""),
            "cycle_id": getattr(assessment, "cycle_id", 0),
            "computed_total": round(computed_total, 4),
            "score_neutral": score_neutral,
            "score_strategy": score_strategy,
            "selected_strategy": getattr(assessment, "selected_strategy", None),
        },
    )


def _resolve_strategy_weights(assessment: Any) -> dict[str, float]:
    """
    Attempt to load the strategy-specific weights that were used for scoring.

    Falls back to global weights if strategy weights cannot be resolved.
    """
    try:
        from core.pipeline.strategy_weights import get_weights_for_strategy
        from core.pipeline.strategy_classifier import StrategyType

        selected = getattr(assessment, "selected_strategy", None)
        if selected is None:
            return _GLOBAL_WEIGHTS

        strategy_map = {
            "CONTINUATION": StrategyType.CONTINUATION,
            "REVERSAL": StrategyType.REVERSAL,
            "FALSE_BREAK": StrategyType.FALSE_BREAK,
        }
        stype = strategy_map.get(selected)
        if stype:
            return get_weights_for_strategy(stype)
    except Exception:
        pass

    return _GLOBAL_WEIGHTS
