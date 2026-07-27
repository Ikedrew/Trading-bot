"""
Regime Activation Engine — Market environment classifier.

Classifies current market into TRENDING / RANGE / TRANSITIONAL.
Provides volatility, structure, liquidity, and session context.

This is a NON-DECISION layer. It does NOT:
    - classify strategies
    - rank trades
    - output entry signals
    - compute expected value
    - influence execution directly

It provides context consumed by:
    → mapping_activation (pattern interpretation)
    → gating_activation (threshold tightening/relaxing)
    → selection_activation (final weighting)

Design: deterministic, pure function, no state mutation.
"""

from __future__ import annotations

import time as _time
from typing import Any

from data.mt5_data import Candle
from strategy.schema_activation import RegimeOutput


# ─── REGIME HYSTERESIS (prevents flip noise) ──────────────────────────────────
# Regime must persist for N consecutive classifications before switching.
# This prevents oscillation at threshold boundaries.

_HYSTERESIS_REQUIRED = 3  # Must classify same regime N times before switch

_regime_history: list[str] = []
_confirmed_regime: str = "TRANSITIONAL"


def _apply_hysteresis(raw_regime: str) -> str:
    """Apply regime smoothing. Require N consecutive same-regime before switching."""
    global _confirmed_regime

    _regime_history.append(raw_regime)
    if len(_regime_history) > _HYSTERESIS_REQUIRED + 2:
        _regime_history.pop(0)

    # Check if last N classifications agree
    if len(_regime_history) >= _HYSTERESIS_REQUIRED:
        recent = _regime_history[-_HYSTERESIS_REQUIRED:]
        if all(r == raw_regime for r in recent):
            _confirmed_regime = raw_regime

    return _confirmed_regime


# ─── PARAMETERS ───────────────────────────────────────────────────────────────

_LOOKBACK = 20
_TREND_DISPLACEMENT_MIN = 0.25
_RANGE_DISPLACEMENT_MAX = 0.15
_HIGH_VOL_MULT = 1.5
_LOW_VOL_MULT = 0.5


# ─── SESSION DETECTION ────────────────────────────────────────────────────────

def _get_session(bar_time: int) -> str:
    """Determine trading session from bar timestamp (UTC)."""
    try:
        hour = _time.gmtime(bar_time).tm_hour
    except (ValueError, OSError):
        return "OFF_SESSION"

    if 0 <= hour < 7:
        return "ASIAN"
    elif 7 <= hour < 12:
        return "LONDON"
    elif 12 <= hour < 14:
        return "OVERLAP"
    elif 14 <= hour < 21:
        return "NY"
    else:
        return "OFF_SESSION"


# ─── MAIN REGIME CLASSIFIER ──────────────────────────────────────────────────

def classify_regime(
    candles: list[Candle],
    closed_i: int,
) -> RegimeOutput:
    """
    Classify current market regime from price structure.

    Returns RegimeOutput (frozen, immutable).
    """
    if closed_i < _LOOKBACK:
        return RegimeOutput(
            regime="TRANSITIONAL", regime_confidence=0.3,
            volatility_state="MEDIUM", structure_state="BROKEN",
            trend_strength=0.0, range_quality=0.0, noise_index=0.7,
            liquidity_condition="CHOPPY",
            session_context=_get_session(candles[closed_i].time) if candles else "OFF_SESSION",
            notes="Insufficient data",
        )

    window = candles[closed_i - _LOOKBACK: closed_i + 1]
    current = candles[closed_i]
    session = _get_session(current.time)

    # ─── DISPLACEMENT ─────────────────────────────────────────────────
    net_move = abs(window[-1].close - window[0].open)
    total_range = sum(c.high - c.low for c in window)
    displacement = net_move / total_range if total_range > 0 else 0.0

    # ─── STRUCTURE (HH/HL vs LH/LL) ──────────────────────────────────
    mid = len(window) // 2
    first_highs = [c.high for c in window[:mid]]
    second_highs = [c.high for c in window[mid:]]
    first_lows = [c.low for c in window[:mid]]
    second_lows = [c.low for c in window[mid:]]

    hh = max(second_highs) > max(first_highs)
    hl = min(second_lows) > min(first_lows)
    lh = max(second_highs) < max(first_highs)
    ll = min(second_lows) < min(first_lows)

    trending_bull = hh and hl
    trending_bear = lh and ll

    # ─── TREND STRENGTH ───────────────────────────────────────────────
    trend_strength = 0.0
    if trending_bull or trending_bear:
        trend_strength = min(1.0, displacement * 2.5)
        if displacement >= 0.35:
            trend_strength = min(1.0, trend_strength + 0.2)

    # ─── RANGE QUALITY ────────────────────────────────────────────────
    range_quality = 0.0
    if not trending_bull and not trending_bear:
        # Check for mean reversion (highs and lows contained)
        high_variance = max(second_highs) - min(first_highs)
        low_variance = max(second_lows) - min(first_lows)
        containment = 1.0 - min(1.0, (high_variance + low_variance) / (total_range / len(window) + 0.0001))
        range_quality = max(0.0, containment)

    # ─── VOLATILITY STATE ─────────────────────────────────────────────
    atr = total_range / len(window) if window else 0.0001
    current_range = current.high - current.low
    vol_ratio = current_range / atr if atr > 0 else 1.0

    if vol_ratio >= _HIGH_VOL_MULT:
        volatility_state = "HIGH"
    elif vol_ratio <= _LOW_VOL_MULT:
        volatility_state = "LOW"
    else:
        volatility_state = "MEDIUM"

    # ─── STRUCTURE STATE ──────────────────────────────────────────────
    if displacement >= 0.30 and (trending_bull or trending_bear):
        structure_state = "EXPANDING"
    elif displacement <= 0.12:
        structure_state = "CONTRACTING"
    elif trending_bull or trending_bear:
        structure_state = "ORDERLY"
    else:
        structure_state = "BROKEN"

    # ─── NOISE INDEX ──────────────────────────────────────────────────
    # Overlap ratio (consecutive candle overlap = noise)
    overlaps = 0
    for i in range(1, len(window)):
        overlap = min(window[i].high, window[i-1].high) - max(window[i].low, window[i-1].low)
        denom = min(window[i].high - window[i].low, window[i-1].high - window[i-1].low)
        if denom > 0 and overlap / denom >= 0.5:
            overlaps += 1
    noise_index = round(overlaps / max(1, len(window) - 1), 3)

    # ─── LIQUIDITY CONDITION ──────────────────────────────────────────
    # Check for manipulation (long wicks beyond levels with close back inside)
    wick_rejections = 0
    for c in window[-5:]:
        cr = c.high - c.low
        if cr > 0:
            upper_wick = (c.high - max(c.open, c.close)) / cr
            lower_wick = (min(c.open, c.close) - c.low) / cr
            if upper_wick > 0.6 or lower_wick > 0.6:
                wick_rejections += 1

    if wick_rejections >= 3:
        liquidity_condition = "MANIPULATED"
    elif noise_index > 0.6:
        liquidity_condition = "CHOPPY"
    else:
        liquidity_condition = "CLEAN"

    # ─── REGIME CLASSIFICATION ────────────────────────────────────────
    if (trending_bull or trending_bear) and displacement >= _TREND_DISPLACEMENT_MIN and trend_strength >= 0.5:
        regime = "TRENDING"
        regime_confidence = min(1.0, trend_strength * 0.8 + displacement * 0.4)
    elif displacement <= _RANGE_DISPLACEMENT_MAX and range_quality >= 0.4:
        regime = "RANGE"
        regime_confidence = min(1.0, range_quality * 0.7 + (1.0 - noise_index) * 0.3)
    else:
        regime = "TRANSITIONAL"
        regime_confidence = max(0.2, 0.5 - noise_index * 0.3)

    # Confidence reduction for broken/manipulated conditions
    if structure_state == "BROKEN":
        regime_confidence *= 0.7
    if liquidity_condition == "MANIPULATED":
        regime_confidence *= 0.8
    if noise_index > 0.6:
        regime_confidence *= 0.8

    regime_confidence = round(min(1.0, max(0.0, regime_confidence)), 3)

    # ─── CONFIDENCE HARD GATE ─────────────────────────────────────────
    if regime_confidence < 0.6 and regime != "TRANSITIONAL":
        regime = "TRANSITIONAL"

    notes = (
        f"disp={displacement:.2f} trend_str={trend_strength:.2f} "
        f"range_q={range_quality:.2f} noise={noise_index:.2f} "
        f"struct={'HH+HL' if trending_bull else 'LH+LL' if trending_bear else 'mixed'}"
    )

    # Apply hysteresis (prevent regime flip noise at boundaries)
    regime = _apply_hysteresis(regime)

    return RegimeOutput(
        regime=regime,
        regime_confidence=regime_confidence,
        volatility_state=volatility_state,
        structure_state=structure_state,
        trend_strength=round(trend_strength, 3),
        range_quality=round(range_quality, 3),
        noise_index=noise_index,
        liquidity_condition=liquidity_condition,
        session_context=session,
        notes=notes,
    )
