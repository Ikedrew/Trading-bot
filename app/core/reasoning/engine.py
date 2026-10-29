"""
Reasoning Engine — generates human-readable explanations from OpportunityAssessment.

The system already decides. This engine explains WHY.

Usage:
    from core.reasoning import generate_reasoning

    reasoning = generate_reasoning(assessment=opportunity_assessment)
    # reasoning.primary_thesis → "Trend continuation likely — bullish momentum..."
    # reasoning.supporting_evidence → ["HTF alignment supports...", ...]
    # reasoning.contradicting_evidence → ["Regime transitional...", ...]

Architecture:
    generate_reasoning(assessment)
        ├── reason_pattern(assessment)     → pattern identity + quality
        ├── reason_trend(assessment)       → trend + bias alignment
        ├── reason_structure(assessment)   → market state + chop + stability
        ├── reason_regime(assessment)      → regime + strategy classification
        ├── reason_momentum(assessment)    → volatility + confirmation + delta
        └── reason_htf(assessment)         → H1/M15/H4 alignment

Rules:
    - NEVER makes trading decisions
    - NEVER modifies assessment or any upstream state
    - NEVER raises (returns degraded reasoning on error)
    - Output is purely observational
"""

from __future__ import annotations

from typing import Any

from core.reasoning.model import DecisionReasoning
from core.reasoning.reasoners import (
    reason_pattern,
    reason_trend,
    reason_structure,
    reason_regime,
    reason_momentum,
    reason_htf,
)


def generate_reasoning(*, assessment: Any) -> DecisionReasoning:
    """
    Generate human-readable reasoning from an OpportunityAssessment.

    Consumes ONLY existing analytical fields — never recalculates.
    Never raises — returns minimal reasoning on any error.

    Args:
        assessment: OpportunityAssessment (frozen analytical snapshot)

    Returns:
        DecisionReasoning (frozen explanation object)
    """
    if assessment is None:
        return DecisionReasoning(
            primary_thesis="No assessment available",
            supporting_evidence=(),
            contradicting_evidence=(),
        )

    try:
        return _build_reasoning(assessment)
    except Exception:
        # Reasoning failure must never affect pipeline
        return DecisionReasoning(
            primary_thesis=f"Reasoning generation failed for {getattr(assessment, 'symbol', 'unknown')}",
            supporting_evidence=(),
            contradicting_evidence=(),
            metadata={"error": True},
        )


def _build_reasoning(assessment: Any) -> DecisionReasoning:
    """Internal reasoning construction — may raise."""
    supporting: list[str] = []
    contradicting: list[str] = []

    # Collect evidence from each reasoner
    for reasoner in (reason_pattern, reason_trend, reason_structure, reason_regime, reason_momentum, reason_htf):
        try:
            s, c = reasoner(assessment)
            supporting.extend(s)
            contradicting.extend(c)
        except Exception:
            pass  # Individual reasoner failure is non-fatal

    # Build primary thesis from dominant evidence
    primary_thesis = _synthesize_thesis(assessment, supporting, contradicting)

    # Build alternative thesis
    alternative = _synthesize_alternative(assessment, contradicting)

    # Build confidence explanation
    confidence_expl = _explain_confidence(assessment)

    return DecisionReasoning(
        primary_thesis=primary_thesis,
        supporting_evidence=tuple(supporting),
        contradicting_evidence=tuple(contradicting),
        alternative_thesis=alternative,
        confidence_explanation=confidence_expl,
        metadata={
            "symbol": getattr(assessment, "symbol", ""),
            "cycle_id": getattr(assessment, "cycle_id", 0),
            "support_count": len(supporting),
            "contradict_count": len(contradicting),
            "score_neutral": getattr(assessment, "score_neutral", 0.0),
            "score_strategy": getattr(assessment, "score_strategy", 0.0),
        },
    )


# ─── THESIS SYNTHESIS ─────────────────────────────────────────────────────────

def _synthesize_thesis(assessment: Any, supporting: list[str], contradicting: list[str]) -> str:
    """
    Build a one-sentence primary thesis from assessment state.

    This does NOT decide anything — it describes what the analysis found.
    """
    side = getattr(assessment, "side", "")
    strategy = getattr(assessment, "selected_strategy", None)
    regime = getattr(assessment, "regime", "TRANSITIONAL")
    pattern = getattr(assessment, "pattern", "")
    market_state = getattr(assessment, "market_state", "TRANSITIONAL")

    # Direction description
    direction = "bullish" if side == "BUY" else "bearish" if side == "SELL" else "neutral"

    # Strategy context
    if strategy == "CONTINUATION":
        thesis_type = "Trend continuation"
    elif strategy == "REVERSAL":
        thesis_type = "Mean-reversion / reversal"
    elif strategy == "FALSE_BREAK":
        thesis_type = "False-break trap"
    else:
        thesis_type = "Directional opportunity"

    # Strength qualifier from evidence balance
    balance = len(supporting) - len(contradicting)
    if balance >= 4:
        qualifier = "strongly supported"
    elif balance >= 2:
        qualifier = "likely"
    elif balance >= 0:
        qualifier = "possible but contested"
    else:
        qualifier = "weakly supported with significant headwinds"

    # Compose
    pattern_desc = pattern.replace("_", " ").lower()
    # Remove redundant direction word if pattern already contains it
    if direction in pattern_desc:
        thesis = f"{thesis_type} {qualifier} — {pattern_desc}"
    else:
        thesis = f"{thesis_type} {qualifier} — {direction} {pattern_desc}"

    if market_state == "STRUCTURED":
        thesis += " in structured market"
    elif market_state == "CHOP":
        thesis += " in choppy conditions"

    return thesis


def _synthesize_alternative(assessment: Any, contradicting: list[str]) -> str | None:
    """
    Build an alternative thesis based on contradicting evidence.

    Returns None if no meaningful alternative can be constructed.
    """
    if not contradicting:
        return None

    side = getattr(assessment, "side", "")
    strategy = getattr(assessment, "selected_strategy", None)
    regime = getattr(assessment, "regime", "TRANSITIONAL")

    # Counter-direction interpretation
    opposite = "bearish" if side == "BUY" else "bullish" if side == "SELL" else "neutral"

    if regime == "TRANSITIONAL":
        return f"Market may be ranging — {opposite} reversal from current level"
    elif regime == "RANGE" and strategy == "CONTINUATION":
        return f"Range-bound market — breakout attempt may fail and revert {opposite}"
    elif any("counter-trend" in c.lower() or "against" in c.lower() for c in contradicting):
        return f"Counter-trend exhaustion — price may snap back {opposite}"
    elif any("htf" in c.lower() or "h4" in c.lower() or "h1" in c.lower() for c in contradicting):
        return f"Higher-timeframe structure contradicts — macro flow is {opposite}"
    elif len(contradicting) >= 3:
        return f"Multiple headwinds suggest opportunity may fail — consider {opposite} scenario"

    return None


def _explain_confidence(assessment: Any) -> str:
    """
    Explain WHY confidence is at its current level (not the number — the reason).
    """
    strat_conf = getattr(assessment, "strategy_confidence", 0.0)
    regime_conf = getattr(assessment, "regime_confidence", 0.5)
    weights_used = getattr(assessment, "weights_used", "global_fallback")
    score_neutral = getattr(assessment, "score_neutral", 0.0)
    score_strategy = getattr(assessment, "score_strategy", 0.0)

    parts: list[str] = []

    # Strategy confidence level
    if strat_conf >= 0.7:
        parts.append("high strategy classification confidence")
    elif strat_conf >= 0.4:
        parts.append("moderate strategy classification confidence")
    elif strat_conf > 0:
        parts.append("low strategy classification confidence")
    else:
        parts.append("no strategy classified (global fallback)")

    # Regime certainty
    if regime_conf < 0.4:
        parts.append("regime uncertain")
    elif regime_conf >= 0.8:
        parts.append("regime highly certain")

    # Weighting impact
    if weights_used == "strategy_specific" and score_strategy > score_neutral:
        parts.append("strategy-specific weights boosted score")
    elif weights_used == "global_fallback":
        parts.append("using global fallback weights")

    # Score level
    if score_neutral >= 0.6:
        parts.append("strong baseline score")
    elif score_neutral <= 0.3:
        parts.append("weak baseline score")

    return " — ".join(parts).capitalize() if parts else "Standard confidence level"
