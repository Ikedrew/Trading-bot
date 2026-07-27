"""
Strategy-Specific Weight Profiles — Differentiated scoring per strategy type.

Each strategy (A/B/C) has its own weight profile that emphasises
different factors based on what matters for that strategy archetype.

This module is PURE DATA. No computation, no mutation, no side effects.
These are initial logical assumptions — NOT optimised values.

Architecture rule:
    - The engine computes raw component scores (0.0–1.0) per factor
    - This module provides weights PER STRATEGY TYPE
    - The engine applies: composite = sum(weight[k] * component[k])
    - No learning. No adaptation. Static assumptions for data collection.

Later analysis will determine if these assumptions are correct.
"""

from __future__ import annotations

from core.pipeline.strategy_classifier import StrategyType


# ─── STRATEGY WEIGHT PROFILES ─────────────────────────────────────────────────
# Each profile sums to 1.00
# Factors: pattern_quality, bias_alignment, market_quality, trend_alignment,
#           chop_clarity, volatility_quality, bias_stability, confirmation_pre,
#           htf_alignment, h4_alignment

STRATEGY_WEIGHTS: dict[StrategyType, dict[str, float]] = {

    # A = CONTINUATION
    # Emphasis: trend strength, bias alignment, H4 direction
    # Logic: continuation trades need strong trend backing + HTF support
    StrategyType.CONTINUATION: {
        "pattern_quality": 0.10,
        "bias_alignment": 0.22,
        "market_quality": 0.08,
        "trend_alignment": 0.15,
        "chop_clarity": 0.05,
        "volatility_quality": 0.05,
        "bias_stability": 0.08,
        "confirmation_pre": 0.05,
        "htf_alignment": 0.12,
        "h4_alignment": 0.10,
    },

    # B = REVERSAL
    # Emphasis: pattern quality, volatility (exhaustion signals), low trend alignment
    # Logic: reversals need strong rejection patterns at extremes
    StrategyType.REVERSAL: {
        "pattern_quality": 0.20,
        "bias_alignment": 0.05,       # Low — reversals trade AGAINST bias
        "market_quality": 0.10,
        "trend_alignment": 0.05,      # Low — counter-trend by definition
        "chop_clarity": 0.08,
        "volatility_quality": 0.15,   # High — exhaustion / extension signals
        "bias_stability": 0.05,       # Low — looking for instability
        "confirmation_pre": 0.12,     # High — need strong candle rejection
        "htf_alignment": 0.10,
        "h4_alignment": 0.10,
    },

    # C = FALSE BREAK
    # Emphasis: pattern quality (rejection), volatility (sweep), confirmation
    # Logic: false breaks need clear sweep + immediate rejection + structural level
    StrategyType.FALSE_BREAK: {
        "pattern_quality": 0.18,
        "bias_alignment": 0.08,
        "market_quality": 0.06,
        "trend_alignment": 0.06,
        "chop_clarity": 0.06,
        "volatility_quality": 0.12,   # High — sweep dynamics
        "bias_stability": 0.06,
        "confirmation_pre": 0.18,     # Highest — rejection candle is critical
        "htf_alignment": 0.10,
        "h4_alignment": 0.10,
    },
}


# ─── GLOBAL FALLBACK (original 10-factor model) ──────────────────────────────
# Used when classifier confidence is too low to assign a strategy

GLOBAL_WEIGHTS: dict[str, float] = {
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


def get_weights_for_strategy(strategy: StrategyType) -> dict[str, float]:
    """
    Return weight profile for given strategy type.

    Falls back to GLOBAL_WEIGHTS if strategy not found (should never happen).
    """
    return STRATEGY_WEIGHTS.get(strategy, GLOBAL_WEIGHTS)
