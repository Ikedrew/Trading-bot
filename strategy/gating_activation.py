"""
Gating Activation — Structural validation layer.

PURPOSE: Validates that a specific trade setup meets real-world conditions.
NOT eligibility (that's eligibility_activation.py).

BOUNDARY RULE:
    eligibility = "can this strategy TYPE exist in this regime?" (binary physics)
    gating = "does THIS SPECIFIC SETUP qualify under real conditions?" (context validation)

    Eligibility answers: "Is reversal allowed right now?"
    Gating answers: "Is THIS reversal at THIS level with THIS rejection valid?"

    No strategy logic should exist in both layers.

Each strategy has mandatory structural requirements:
    REVERSAL: key level + rejection/sweep
    FALSE_BREAK: sweep + rejection + return within N candles
    CONTINUATION: BOS or displacement + alignment

Design: deterministic, pure function, no side effects.
"""

from __future__ import annotations

from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal, Side
from strategy.schema_activation import RegimeOutput


# ─── CONTEXT EXTRACTION ───────────────────────────────────────────────────────

def extract_context(
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
    swing_direction: str = "NEUTRAL",
    swing_break_confirmed: bool = False,
) -> dict[str, Any]:
    """Extract structural context required for gating decisions."""
    if closed_i < 10:
        return {"valid": False}

    current = candles[closed_i]
    lookback = 10
    window = candles[closed_i - lookback: closed_i]

    recent_high = max(c.high for c in window)
    recent_low = min(c.low for c in window)
    range_size = recent_high - recent_low
    if range_size <= 0:
        return {"valid": False}

    # Swing proximity
    at_swing_high = current.high >= recent_high * 0.998
    at_swing_low = current.low <= recent_low * 1.002

    # Liquidity sweep
    sweep_high = current.high > recent_high and current.close < recent_high
    sweep_low = current.low < recent_low and current.close > recent_low

    # Rejection (wick quality)
    candle_range = current.high - current.low
    if candle_range > 0:
        upper_wick = (current.high - max(current.open, current.close)) / candle_range
        lower_wick = (min(current.open, current.close) - current.low) / candle_range
    else:
        upper_wick = 0.0
        lower_wick = 0.0
    rejection = upper_wick > 0.5 or lower_wick > 0.5

    # Displacement
    body = abs(current.close - current.open)
    body_ratio = body / candle_range if candle_range > 0 else 0.0

    # Volatility
    atr = sum(c.high - c.low for c in window) / len(window)
    vol_expanding = (current.high - current.low) > atr * 1.5

    # False break timing (did price return inside structure within last 3 bars?)
    fb_return_within_3 = False
    if closed_i >= 3:
        for i in range(closed_i - 2, closed_i + 1):
            c = candles[i]
            if c.close <= recent_high and c.close >= recent_low:
                fb_return_within_3 = True
                break

    return {
        "valid": True,
        "at_swing_high": at_swing_high,
        "at_swing_low": at_swing_low,
        "at_key_level": at_swing_high or at_swing_low,
        "sweep_high": sweep_high,
        "sweep_low": sweep_low,
        "liquidity_sweep": sweep_high or sweep_low,
        "rejection": rejection,
        "strong_displacement": body_ratio > 0.7,
        "body_ratio": round(body_ratio, 3),
        "vol_expanding": vol_expanding,
        "swing_direction": swing_direction,
        "swing_break_confirmed": swing_break_confirmed,
        "fb_return_within_3": fb_return_within_3,
    }


# ─── HARD GATE FUNCTIONS ──────────────────────────────────────────────────────

def gate_reversal(context: dict[str, Any], regime: RegimeOutput) -> tuple[bool, str]:
    """
    REVERSAL hard gate.

    REQUIRED: (key_level OR swing) AND (liquidity_sweep OR rejection)
    """
    if not context.get("valid"):
        return False, "invalid_context"

    at_level = context.get("at_key_level", False)
    has_sweep = context.get("liquidity_sweep", False)
    has_rejection = context.get("rejection", False)

    if not at_level and not (context.get("at_swing_high") or context.get("at_swing_low")):
        return False, "no_key_level_or_swing"

    if not has_sweep and not has_rejection:
        return False, "no_liquidity_sweep_or_rejection"

    return True, "passed"


def gate_false_break(context: dict[str, Any], regime: RegimeOutput) -> tuple[bool, str]:
    """
    FALSE_BREAK hard gate.

    REQUIRED: liquidity_sweep AND rejection AND return within 1-3 candles
    """
    if not context.get("valid"):
        return False, "invalid_context"

    has_sweep = context.get("liquidity_sweep", False)
    has_rejection = context.get("rejection", False)
    fb_return = context.get("fb_return_within_3", False)

    if not has_sweep:
        return False, "no_liquidity_sweep"

    if not has_rejection:
        return False, "no_rejection_after_sweep"

    if not fb_return:
        return False, "no_return_within_3_candles"

    return True, "passed"


def gate_continuation(
    context: dict[str, Any],
    regime: RegimeOutput,
    pattern: Signal,
) -> tuple[bool, str]:
    """
    CONTINUATION hard gate.

    REQUIRED: HTF alignment + displacement OR BOS
    HARD RULE: if RANGE regime → blocked unless BOS confirmed
    """
    if not context.get("valid"):
        return False, "invalid_context"

    swing_dir = context.get("swing_direction", "NEUTRAL")
    bos = context.get("swing_break_confirmed", False)
    displacement = context.get("strong_displacement", False)

    # Alignment check
    aligned = (
        (swing_dir == "BULLISH" and pattern.side == Side.BUY) or
        (swing_dir == "BEARISH" and pattern.side == Side.SELL)
    )

    # NOTE: RANGE blocking is handled in eligibility_activation.py (not duplicated here)
    # Gating only validates THIS setup's structural conditions.

    if not aligned and swing_dir != "NEUTRAL":
        return False, "not_aligned_with_swing"

    if not displacement and not bos:
        return False, "no_displacement_or_bos"

    return True, "passed"
