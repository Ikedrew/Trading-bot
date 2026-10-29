"""
Strategy Activation Engine (Module 1.2) — Context-aware strategy permission system.

Sits AFTER pattern detection and BEFORE scoring.
Decides which strategies are ALLOWED in current market context
and assigns activation weights based on structural evidence.

This module does NOT predict trades. It answers:
    "Which strategies are structurally valid right now?"

Pipeline position:
    1.1 Pattern Detection → 1.2 Strategy Activation → 2.1 Scoring

Output:
    - strategy_candidates (list of allowed/blocked strategies with weights)
    - selected_strategy (highest activation weight)
    - regime classification
    - context state snapshot

Design: deterministic, no learning, no adaptation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal, Side


# ─── TYPES ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyCandidate:
    """One evaluated strategy with activation status."""
    strategy: str               # REVERSAL / FALSE_BREAK / CONTINUATION
    allowed: bool
    activation_weight: float    # 0.0–1.0
    confidence: float           # 0.0–1.0
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StrategyActivationResult:
    """Complete output of strategy activation engine."""
    strategy_candidates: tuple[StrategyCandidate, ...]
    selected_strategy: str | None
    selected_weight: float
    regime: str                 # TRENDING / RANGE / TRANSITIONAL
    context_state: dict[str, Any]


# ─── PATTERN SETS ─────────────────────────────────────────────────────────────

_REVERSAL_PATTERNS = frozenset({
    "HAMMER", "HANGING_MAN", "INVERTED_HAMMER", "SHOOTING_STAR",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "TWEEZER_TOP", "TWEEZER_BOTTOM",
    "MORNING_STAR", "EVENING_STAR",
    "THREE_INSIDE_UP", "THREE_INSIDE_DOWN",
})

_CONTINUATION_PATTERNS = frozenset({
    "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
})

# FALSE_BREAK uses same patterns as REVERSAL (context differentiates)
_FALSE_BREAK_PATTERNS = _REVERSAL_PATTERNS


# ─── REGIME DETECTION ─────────────────────────────────────────────────────────

def _detect_regime(candles: list[Candle], closed_i: int) -> str:
    """Classify current market regime from price structure."""
    if closed_i < 20:
        return "TRANSITIONAL"

    lookback = 20
    window = candles[closed_i - lookback: closed_i + 1]

    # Compute directional displacement
    net_move = abs(window[-1].close - window[0].open)
    total_range = sum(c.high - c.low for c in window)
    if total_range <= 0:
        return "TRANSITIONAL"

    displacement = net_move / total_range

    # Check for HH/HL or LH/LL structure (simplified)
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    mid = len(window) // 2

    first_half_high = max(highs[:mid])
    second_half_high = max(highs[mid:])
    first_half_low = min(lows[:mid])
    second_half_low = min(lows[mid:])

    trending_up = second_half_high > first_half_high and second_half_low > first_half_low
    trending_down = second_half_high < first_half_high and second_half_low < first_half_low

    if (trending_up or trending_down) and displacement >= 0.25:
        return "TRENDING"
    elif displacement <= 0.15:
        return "RANGE"
    else:
        return "TRANSITIONAL"


# ─── CONTEXT EXTRACTION ───────────────────────────────────────────────────────

def _extract_context(
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
    swing_direction: str = "NEUTRAL",
    swing_break_confirmed: bool = False,
) -> dict[str, Any]:
    """Extract structural context for strategy gating."""
    if closed_i < 10:
        return {"valid": False}

    current = candles[closed_i]
    lookback = 10
    window = candles[closed_i - lookback: closed_i]

    # Swing proximity
    recent_high = max(c.high for c in window)
    recent_low = min(c.low for c in window)
    range_size = recent_high - recent_low
    if range_size <= 0:
        return {"valid": False}

    at_swing_high = (current.high >= recent_high * 0.998)
    at_swing_low = (current.low <= recent_low * 1.002)

    # Liquidity sweep detection (wick beyond level + close back inside)
    sweep_high = current.high > recent_high and current.close < recent_high
    sweep_low = current.low < recent_low and current.close > recent_low

    # Rejection detection (wick > 50% of candle range)
    candle_range = current.high - current.low
    if candle_range > 0:
        upper_wick = (current.high - max(current.open, current.close)) / candle_range
        lower_wick = (min(current.open, current.close) - current.low) / candle_range
    else:
        upper_wick = 0.0
        lower_wick = 0.0

    rejection = upper_wick > 0.5 or lower_wick > 0.5

    # Volatility state
    atr = sum(c.high - c.low for c in window) / len(window)
    current_range = current.high - current.low
    vol_expanding = current_range > atr * 1.5
    vol_compressing = current_range < atr * 0.5

    # Displacement (momentum)
    body = abs(current.close - current.open)
    body_ratio = body / candle_range if candle_range > 0 else 0.0
    strong_displacement = body_ratio > 0.7

    return {
        "valid": True,
        "at_swing_high": at_swing_high,
        "at_swing_low": at_swing_low,
        "at_key_level": at_swing_high or at_swing_low,
        "sweep_high": sweep_high,
        "sweep_low": sweep_low,
        "liquidity_sweep": sweep_high or sweep_low,
        "rejection": rejection,
        "vol_expanding": vol_expanding,
        "vol_compressing": vol_compressing,
        "strong_displacement": strong_displacement,
        "body_ratio": round(body_ratio, 3),
        "swing_direction": swing_direction,
        "swing_break_confirmed": swing_break_confirmed,
    }


# ─── STRATEGY EVALUATORS ──────────────────────────────────────────────────────

def _evaluate_reversal(
    pattern: Signal,
    context: dict[str, Any],
    regime: str,
) -> StrategyCandidate:
    """Evaluate REVERSAL strategy activation."""
    reasons: list[str] = []
    weight = 0.0

    # Pattern eligibility
    if pattern.pattern not in _REVERSAL_PATTERNS:
        return StrategyCandidate("REVERSAL", False, 0.0, 0.0, ("pattern_not_reversal_type",))

    # Context gating
    at_level = context.get("at_key_level", False)
    has_sweep = context.get("liquidity_sweep", False)
    has_rejection = context.get("rejection", False)
    vol_expanding = context.get("vol_expanding", False)

    if not at_level and not has_sweep:
        return StrategyCandidate("REVERSAL", False, 0.0, 0.0, ("no_key_level_or_sweep",))

    # Build weight
    if has_sweep and at_level and has_rejection:
        weight = 0.85
        reasons.append("full_confluence: sweep+level+rejection")
    elif has_sweep and has_rejection:
        weight = 0.70
        reasons.append("sweep+rejection")
    elif at_level and has_rejection:
        weight = 0.60
        reasons.append("level+rejection")
    elif at_level or has_sweep:
        weight = 0.40
        reasons.append("partial_context")
    else:
        weight = 0.20
        reasons.append("weak_context")

    # Regime modulation
    if regime == "TRENDING" and not has_sweep:
        weight *= 0.4
        reasons.append("trending_suppression")
    elif regime == "RANGE":
        weight *= 1.2
        reasons.append("range_boost")
    elif regime == "TRANSITIONAL":
        weight *= 0.5
        reasons.append("transitional_dampening")

    # Vol expanding in trend = suppress reversal
    if vol_expanding and regime == "TRENDING":
        weight *= 0.3
        reasons.append("vol_expanding_in_trend")

    weight = round(min(1.0, max(0.0, weight)), 3)
    allowed = weight >= 0.25

    return StrategyCandidate("REVERSAL", allowed, weight, min(1.0, weight * 1.1), tuple(reasons))


def _evaluate_false_break(
    pattern: Signal,
    context: dict[str, Any],
    regime: str,
) -> StrategyCandidate:
    """Evaluate FALSE_BREAK strategy activation."""
    reasons: list[str] = []
    weight = 0.0

    if pattern.pattern not in _FALSE_BREAK_PATTERNS:
        return StrategyCandidate("FALSE_BREAK", False, 0.0, 0.0, ("pattern_not_fb_type",))

    has_sweep = context.get("liquidity_sweep", False)
    has_rejection = context.get("rejection", False)
    at_level = context.get("at_key_level", False)

    # FALSE BREAK requires: breakout + rejection + return
    if not has_sweep:
        return StrategyCandidate("FALSE_BREAK", False, 0.0, 0.0, ("no_liquidity_sweep",))

    if has_sweep and has_rejection:
        weight = 0.85
        reasons.append("sweep+rejection (classic false break)")
    elif has_sweep:
        weight = 0.50
        reasons.append("sweep_only (awaiting rejection)")

    # Regime modulation
    if regime == "RANGE":
        weight *= 1.3
        reasons.append("range_boost (FB high relevance)")
    elif regime == "TRENDING":
        weight *= 0.5
        reasons.append("trending_suppression")
    elif regime == "TRANSITIONAL":
        weight *= 0.5
        reasons.append("transitional_dampening")

    weight = round(min(1.0, max(0.0, weight)), 3)
    allowed = weight >= 0.25

    return StrategyCandidate("FALSE_BREAK", allowed, weight, min(1.0, weight * 1.1), tuple(reasons))


def _evaluate_continuation(
    pattern: Signal,
    context: dict[str, Any],
    regime: str,
    swing_direction: str,
) -> StrategyCandidate:
    """Evaluate CONTINUATION strategy activation."""
    reasons: list[str] = []
    weight = 0.0

    # Alignment check
    aligned_with_swing = (
        (swing_direction == "BULLISH" and pattern.side == Side.BUY) or
        (swing_direction == "BEARISH" and pattern.side == Side.SELL)
    )

    has_displacement = context.get("strong_displacement", False)
    vol_expanding = context.get("vol_expanding", False)
    has_rejection = context.get("rejection", False)
    bos = context.get("swing_break_confirmed", False)

    # Must be trend-aligned
    if not aligned_with_swing and swing_direction != "NEUTRAL":
        return StrategyCandidate("CONTINUATION", False, 0.0, 0.0, ("not_aligned_with_swing",))

    # Build weight
    if aligned_with_swing and has_displacement and bos:
        weight = 0.90
        reasons.append("full_trend_confluence: aligned+displacement+BOS")
    elif aligned_with_swing and has_displacement:
        weight = 0.70
        reasons.append("aligned+displacement")
    elif aligned_with_swing:
        weight = 0.50
        reasons.append("aligned_only")
    elif swing_direction == "NEUTRAL":
        weight = 0.30
        reasons.append("neutral_swing (weak continuation)")

    # Significant rejection at level = suppress continuation
    if has_rejection and context.get("at_key_level", False):
        weight *= 0.5
        reasons.append("rejection_at_level (continuation weakened)")

    # Regime modulation
    if regime == "TRENDING":
        weight *= 1.3
        reasons.append("trending_boost")
    elif regime == "RANGE":
        if not bos:
            weight *= 0.3
            reasons.append("range_suppression (no breakout)")
    elif regime == "TRANSITIONAL":
        weight *= 0.5
        reasons.append("transitional_dampening")

    weight = round(min(1.0, max(0.0, weight)), 3)
    allowed = weight >= 0.25

    # Strong continuation patterns get bonus
    if pattern.pattern in _CONTINUATION_PATTERNS and allowed:
        weight = min(1.0, weight * 1.1)
        reasons.append("strong_pattern_bonus")

    return StrategyCandidate("CONTINUATION", allowed, weight, min(1.0, weight * 1.1), tuple(reasons))


# ─── MAIN ENGINE ──────────────────────────────────────────────────────────────

def activate_strategies(
    *,
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
    swing_direction: str = "NEUTRAL",
    swing_break_confirmed: bool = False,
) -> StrategyActivationResult:
    """
    Evaluate which strategies are allowed in current context.

    Called AFTER pattern detection, BEFORE scoring.
    Returns ranked candidates with activation weights.

    Args:
        candles: Full candle history
        closed_i: Last closed bar index
        pattern: Detected pattern Signal
        swing_direction: From Swing Context Engine (BULLISH/BEARISH/NEUTRAL)
        swing_break_confirmed: BOS status from Swing Context Engine

    Returns:
        StrategyActivationResult with candidates + selected strategy
    """
    # Detect regime
    regime = _detect_regime(candles, closed_i)

    # Extract context
    context = _extract_context(candles, closed_i, pattern, swing_direction, swing_break_confirmed)

    if not context.get("valid", False):
        return StrategyActivationResult(
            strategy_candidates=(),
            selected_strategy=None,
            selected_weight=0.0,
            regime=regime,
            context_state=context,
        )

    # Evaluate all three strategies
    reversal = _evaluate_reversal(pattern, context, regime)
    false_break = _evaluate_false_break(pattern, context, regime)
    continuation = _evaluate_continuation(pattern, context, regime, swing_direction)

    candidates = (reversal, false_break, continuation)

    # Select highest activation weight among allowed candidates
    allowed = [c for c in candidates if c.allowed]
    if allowed:
        selected = max(allowed, key=lambda c: c.activation_weight)
        selected_strategy = selected.strategy
        selected_weight = selected.activation_weight
    else:
        selected_strategy = None
        selected_weight = 0.0

    return StrategyActivationResult(
        strategy_candidates=candidates,
        selected_strategy=selected_strategy,
        selected_weight=selected_weight,
        regime=regime,
        context_state=context,
    )
