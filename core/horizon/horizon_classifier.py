"""
Horizon Classifier — Determines which horizons are plausible for an opportunity.

Uses existing context only (no new indicators):
    - Strategy type (CONTINUATION/REVERSAL/FALSE_BREAK)
    - H4 regime (TRENDING/RANGE/TRANSITIONAL)
    - H1 direction + BOS confirmation
    - M15 structure quality
    - HTF alignment scores
    - Volatility/chop context

This module is PURELY OBSERVATIONAL. It does NOT:
    - Change execution behaviour
    - Modify SL/TP generation
    - Affect trade approval
    - Gate or filter opportunities

It answers: "Which horizons are plausible for this opportunity?"
"""

from __future__ import annotations

import logging
from typing import Any

from core.horizon.horizon_models import (
    TradeHorizon,
    HorizonAssessment,
    HorizonClassificationResult,
)
from core.horizon.horizon_profiles import ALL_PROFILES, HorizonProfile

logger = logging.getLogger(__name__)


def classify_horizons(
    *,
    # Strategy context
    strategy_type: str = "",          # "CONTINUATION" | "REVERSAL" | "FALSE_BREAK" | ""
    strategy_confidence: float = 0.0,
    # H4 regime
    h4_regime: str = "",              # "TRENDING" | "RANGE" | "TRANSITIONAL" | ""
    h4_regime_confidence: float = 0.0,
    # H1 context
    h1_direction: str = "",           # "BULLISH" | "BEARISH" | "NEUTRAL" | ""
    h1_bos_confirmed: bool = False,
    # Scoring components (from 10-factor engine)
    htf_alignment: float = 0.0,      # Combined H1+M15 alignment score (0.0–1.0)
    h4_alignment: float = 0.0,       # H4 regime alignment score (0.0–1.0)
    market_quality: float = 0.0,     # M15 structure quality (0.0–1.0)
    chop_clarity: float = 0.0,       # Inverse of noise (0.0–1.0)
    volatility_quality: float = 0.0, # Directional volatility (0.0–1.0)
    # Pattern context
    pattern: str = "",
    direction: str = "",              # "BUY" | "SELL"
) -> HorizonClassificationResult:
    """
    Classify which trade horizons are plausible for the current opportunity.

    Returns assessments for ALL horizons (SCALP, INTRADAY, EXTENDED) with
    eligibility, confidence, and reasoning for each.

    Args:
        strategy_type: Selected strategy classification
        h4_regime: H4 regime state
        h1_direction: H1 structural direction
        h1_bos_confirmed: Whether H1 break-of-structure is confirmed
        htf_alignment: Combined HTF alignment score
        h4_alignment: H4 alignment score
        market_quality: M15 structure quality score
        chop_clarity: Clarity of price action
        volatility_quality: Quality of directional volatility
        pattern: Detected pattern name
        direction: Trade direction

    Returns:
        HorizonClassificationResult with per-horizon assessments.
    """
    assessments: list[HorizonAssessment] = []

    for horizon_name, profile in ALL_PROFILES.items():
        assessment = _evaluate_horizon(
            profile=profile,
            strategy_type=strategy_type,
            strategy_confidence=strategy_confidence,
            h4_regime=h4_regime,
            h4_regime_confidence=h4_regime_confidence,
            h1_direction=h1_direction,
            h1_bos_confirmed=h1_bos_confirmed,
            htf_alignment=htf_alignment,
            h4_alignment=h4_alignment,
            market_quality=market_quality,
            chop_clarity=chop_clarity,
            volatility_quality=volatility_quality,
            pattern=pattern,
            direction=direction,
        )
        assessments.append(assessment)

    return HorizonClassificationResult(assessments=assessments)


def _evaluate_horizon(
    *,
    profile: HorizonProfile,
    strategy_type: str,
    strategy_confidence: float,
    h4_regime: str,
    h4_regime_confidence: float,
    h1_direction: str,
    h1_bos_confirmed: bool,
    htf_alignment: float,
    h4_alignment: float,
    market_quality: float,
    chop_clarity: float,
    volatility_quality: float,
    pattern: str,
    direction: str,
) -> HorizonAssessment:
    """Evaluate eligibility and confidence for one horizon profile."""

    reasons: list[str] = []
    penalties: list[str] = []
    confidence = 0.5  # Start at neutral

    # ─── TREND REQUIREMENT ────────────────────────────────────────────
    is_trending = "TRENDING" in h4_regime.upper() if h4_regime else False

    if profile.requires_trend:
        if is_trending:
            confidence += 0.15
            reasons.append("H4_TRENDING_CONFIRMED")
        else:
            # Hard fail — extended horizon requires trending
            return HorizonAssessment(
                horizon=profile.name,
                eligible=False,
                confidence=max(0.0, 0.2 * h4_regime_confidence),
                reasoning=f"Requires trending regime but got {h4_regime or 'UNKNOWN'}",
                evidence={
                    "h4_regime": h4_regime,
                    "requirement": "TRENDING",
                    "met": False,
                },
            )

    # ─── BOS REQUIREMENT ──────────────────────────────────────────────
    if profile.requires_bos:
        if h1_bos_confirmed:
            confidence += 0.10
            reasons.append("H1_BOS_CONFIRMED")
        else:
            return HorizonAssessment(
                horizon=profile.name,
                eligible=False,
                confidence=max(0.0, 0.15),
                reasoning="Requires H1 BOS confirmation (not confirmed)",
                evidence={
                    "h1_bos_confirmed": False,
                    "requirement": "BOS",
                    "met": False,
                },
            )

    # ─── HTF ALIGNMENT CHECK ─────────────────────────────────────────
    if htf_alignment >= profile.min_htf_alignment:
        alignment_bonus = (htf_alignment - profile.min_htf_alignment) * 0.3
        confidence += alignment_bonus
        reasons.append(f"HTF_ALIGNED({htf_alignment:.2f}>={profile.min_htf_alignment})")
    elif profile.min_htf_alignment > 0:
        deficit = profile.min_htf_alignment - htf_alignment
        if deficit > 0.3:
            # Large deficit — not eligible
            return HorizonAssessment(
                horizon=profile.name,
                eligible=False,
                confidence=max(0.0, htf_alignment * 0.5),
                reasoning=f"HTF alignment {htf_alignment:.2f} below required {profile.min_htf_alignment}",
                evidence={
                    "htf_alignment": htf_alignment,
                    "required": profile.min_htf_alignment,
                    "deficit": round(deficit, 4),
                },
            )
        else:
            # Small deficit — eligible but low confidence
            confidence -= deficit * 0.5
            penalties.append(f"HTF_WEAK({htf_alignment:.2f}<{profile.min_htf_alignment})")

    # ─── STRUCTURE QUALITY CHECK ──────────────────────────────────────
    if market_quality >= profile.requires_structure_quality:
        quality_bonus = (market_quality - profile.requires_structure_quality) * 0.15
        confidence += quality_bonus
        reasons.append(f"STRUCTURE_QUALITY({market_quality:.2f})")
    elif profile.requires_structure_quality > 0:
        deficit = profile.requires_structure_quality - market_quality
        if deficit > 0.3:
            return HorizonAssessment(
                horizon=profile.name,
                eligible=False,
                confidence=max(0.0, market_quality * 0.4),
                reasoning=f"M15 structure quality {market_quality:.2f} below {profile.requires_structure_quality}",
                evidence={
                    "market_quality": market_quality,
                    "required": profile.requires_structure_quality,
                },
            )
        else:
            confidence -= deficit * 0.3
            penalties.append(f"STRUCTURE_WEAK({market_quality:.2f})")

    # ─── STRATEGY + REGIME SYNERGY ────────────────────────────────────
    if profile.name == "SCALP":
        # Scalp is always eligible (baseline)
        confidence += 0.10
        reasons.append("SCALP_ALWAYS_ELIGIBLE")
    elif profile.name == "INTRADAY":
        # Intraday benefits from continuation in trends or reversal in ranges
        if strategy_type == "CONTINUATION" and is_trending:
            confidence += 0.10
            reasons.append("CONTINUATION_IN_TREND")
        elif strategy_type == "REVERSAL" and "RANGE" in (h4_regime or ""):
            confidence += 0.08
            reasons.append("REVERSAL_IN_RANGE")
        elif chop_clarity < 0.4:
            confidence -= 0.10
            penalties.append("CHOPPY_ENVIRONMENT")
    elif profile.name == "EXTENDED":
        # Extended requires strong trend + continuation
        if strategy_type == "CONTINUATION":
            confidence += 0.10
            reasons.append("CONTINUATION_STRATEGY")
        elif strategy_type == "REVERSAL":
            confidence -= 0.15
            penalties.append("REVERSAL_AGAINST_EXTENDED")

    # ─── VOLATILITY QUALITY ───────────────────────────────────────────
    if profile.name in ("INTRADAY", "EXTENDED"):
        if volatility_quality >= 0.6:
            confidence += 0.05
            reasons.append(f"GOOD_VOLATILITY({volatility_quality:.2f})")
        elif volatility_quality < 0.3:
            confidence -= 0.10
            penalties.append(f"LOW_VOLATILITY({volatility_quality:.2f})")

    # ─── H4 ALIGNMENT BONUS (for higher horizons) ────────────────────
    if profile.name == "EXTENDED" and h4_alignment >= 0.7:
        confidence += 0.10
        reasons.append(f"STRONG_H4_ALIGNMENT({h4_alignment:.2f})")

    # ─── CLAMP CONFIDENCE ─────────────────────────────────────────────
    confidence = max(0.0, min(1.0, confidence))

    # ─── ELIGIBILITY THRESHOLD ────────────────────────────────────────
    eligible = confidence >= 0.4  # Minimum confidence for eligibility

    reasoning_parts = reasons + ([f"penalties: {', '.join(penalties)}"] if penalties else [])
    reasoning = "; ".join(reasoning_parts) if reasoning_parts else "default"

    return HorizonAssessment(
        horizon=profile.name,
        eligible=eligible,
        confidence=round(confidence, 4),
        reasoning=reasoning,
        evidence={
            "h4_regime": h4_regime,
            "h1_direction": h1_direction,
            "h1_bos_confirmed": h1_bos_confirmed,
            "htf_alignment": round(htf_alignment, 4),
            "h4_alignment": round(h4_alignment, 4),
            "market_quality": round(market_quality, 4),
            "strategy_type": strategy_type,
            "chop_clarity": round(chop_clarity, 4),
            "volatility_quality": round(volatility_quality, 4),
        },
    )
