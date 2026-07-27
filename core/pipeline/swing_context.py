"""
Swing Context Engine — Higher-timeframe structural filter.

Acts as a pre-execution gating layer that evaluates macro swing structure.
Prevents microstructure-driven reversal noise from triggering trades.

Position in pipeline:
    Market Data → Swing Context Engine → Bias FSM → Pattern Engine → EV → Execution

Outputs:
    - current_swing_direction: BULLISH / BEARISH / NEUTRAL
    - swing_phase: EXPANSION / DISTRIBUTION / CORRECTION
    - swing_strength: 0.0–1.0
    - swing_break_confirmed: bool

Hard execution rule:
    If swing_break_confirmed == False AND signal == REVERSAL → BLOCK

Design: deterministic, stateful (tracks swing points over time), no learning.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any

from data.mt5_data import Candle
from strategy.signals import Side


# ─── ENUMS ────────────────────────────────────────────────────────────────────

class SwingDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SwingPhase(str, Enum):
    EXPANSION = "EXPANSION"
    DISTRIBUTION = "DISTRIBUTION"
    CORRECTION = "CORRECTION"


# ─── OUTPUT STRUCTURE ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SwingContext:
    """Immutable output of swing context evaluation."""
    current_swing_direction: SwingDirection
    swing_phase: SwingPhase
    swing_strength: float               # 0.0–1.0
    swing_break_confirmed: bool
    last_swing_high: float
    last_swing_low: float
    reasoning: str


# ─── PARAMETERS ───────────────────────────────────────────────────────────────

_SWING_LOOKBACK = 50            # Bars to scan for swing points
_SWING_PIVOT_BARS = 3           # Bars on each side to confirm a pivot
_BOS_CONFIRMATION_BARS = 2      # Candle closes needed to confirm break
_EXPANSION_DISPLACEMENT = 0.6   # Min displacement ratio for EXPANSION phase
_DISTRIBUTION_RANGE_RATIO = 0.3 # Max range ratio for DISTRIBUTION detection


# ─── SWING POINT DETECTION ────────────────────────────────────────────────────

def _find_swing_highs(candles: list[Candle], closed_i: int, lookback: int, pivot_bars: int) -> list[float]:
    """Find swing high prices (local maxima) in lookback window."""
    highs: list[float] = []
    start = max(0, closed_i - lookback)
    for i in range(start + pivot_bars, closed_i - pivot_bars + 1):
        is_pivot = True
        for j in range(1, pivot_bars + 1):
            if candles[i].high <= candles[i - j].high or candles[i].high <= candles[i + j].high:
                is_pivot = False
                break
        if is_pivot:
            highs.append(candles[i].high)
    return highs


def _find_swing_lows(candles: list[Candle], closed_i: int, lookback: int, pivot_bars: int) -> list[float]:
    """Find swing low prices (local minima) in lookback window."""
    lows: list[float] = []
    start = max(0, closed_i - lookback)
    for i in range(start + pivot_bars, closed_i - pivot_bars + 1):
        is_pivot = True
        for j in range(1, pivot_bars + 1):
            if candles[i].low >= candles[i - j].low or candles[i].low >= candles[i + j].low:
                is_pivot = False
                break
        if is_pivot:
            lows.append(candles[i].low)
    return lows


# ─── MAIN ENGINE ──────────────────────────────────────────────────────────────

def compute_swing_context(
    candles: list[Candle],
    closed_i: int,
) -> SwingContext:
    """
    Compute current swing structural context from price data.

    Args:
        candles: Full candle history
        closed_i: Last closed bar index

    Returns:
        SwingContext (immutable) with direction, phase, strength, break status
    """
    if closed_i < _SWING_LOOKBACK:
        return SwingContext(
            current_swing_direction=SwingDirection.NEUTRAL,
            swing_phase=SwingPhase.DISTRIBUTION,
            swing_strength=0.0,
            swing_break_confirmed=False,
            last_swing_high=0.0,
            last_swing_low=0.0,
            reasoning="Insufficient data for swing analysis",
        )

    # Find swing points
    swing_highs = _find_swing_highs(candles, closed_i, _SWING_LOOKBACK, _SWING_PIVOT_BARS)
    swing_lows = _find_swing_lows(candles, closed_i, _SWING_LOOKBACK, _SWING_PIVOT_BARS)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return SwingContext(
            current_swing_direction=SwingDirection.NEUTRAL,
            swing_phase=SwingPhase.DISTRIBUTION,
            swing_strength=0.3,
            swing_break_confirmed=False,
            last_swing_high=swing_highs[-1] if swing_highs else candles[closed_i].high,
            last_swing_low=swing_lows[-1] if swing_lows else candles[closed_i].low,
            reasoning="Insufficient swing points for directional analysis",
        )

    last_sh = swing_highs[-1]
    prev_sh = swing_highs[-2]
    last_sl = swing_lows[-1]
    prev_sl = swing_lows[-2]

    current_price = candles[closed_i].close

    # ─── DIRECTION (HH/HL vs LH/LL) ──────────────────────────────────
    hh = last_sh > prev_sh  # Higher high
    hl = last_sl > prev_sl  # Higher low
    lh = last_sh < prev_sh  # Lower high
    ll = last_sl < prev_sl  # Lower low

    if hh and hl:
        direction = SwingDirection.BULLISH
    elif lh and ll:
        direction = SwingDirection.BEARISH
    else:
        direction = SwingDirection.NEUTRAL

    # ─── BREAK OF STRUCTURE (BOS) ─────────────────────────────────────
    # Check if price has broken the last significant swing level
    bos_confirmed = False
    bos_reasoning = ""

    if direction == SwingDirection.BULLISH or direction == SwingDirection.NEUTRAL:
        # Check for bullish BOS: price closes above last swing high
        closes_above = sum(
            1 for i in range(max(0, closed_i - _BOS_CONFIRMATION_BARS), closed_i + 1)
            if candles[i].close > last_sh
        )
        if closes_above >= _BOS_CONFIRMATION_BARS:
            bos_confirmed = True
            bos_reasoning = f"Bullish BOS: {closes_above} closes above swing high {last_sh:.5f}"

    if direction == SwingDirection.BEARISH or (direction == SwingDirection.NEUTRAL and not bos_confirmed):
        # Check for bearish BOS: price closes below last swing low
        closes_below = sum(
            1 for i in range(max(0, closed_i - _BOS_CONFIRMATION_BARS), closed_i + 1)
            if candles[i].close < last_sl
        )
        if closes_below >= _BOS_CONFIRMATION_BARS:
            bos_confirmed = True
            bos_reasoning = f"Bearish BOS: {closes_below} closes below swing low {last_sl:.5f}"

    # ─── PHASE DETECTION ──────────────────────────────────────────────
    # Compute recent displacement
    lookback_short = min(10, closed_i)
    recent_window = candles[closed_i - lookback_short: closed_i + 1]
    total_range = max(c.high for c in recent_window) - min(c.low for c in recent_window)
    net_move = abs(candles[closed_i].close - candles[closed_i - lookback_short].open)

    if total_range > 0:
        displacement_ratio = net_move / total_range
    else:
        displacement_ratio = 0.0

    # ATR for normalisation
    atr = sum(c.high - c.low for c in recent_window) / len(recent_window) if recent_window else 0.0001

    # Phase classification
    if displacement_ratio >= _EXPANSION_DISPLACEMENT and bos_confirmed:
        phase = SwingPhase.EXPANSION
    elif displacement_ratio <= _DISTRIBUTION_RANGE_RATIO:
        phase = SwingPhase.DISTRIBUTION
    else:
        # Correction: retracement within dominant structure
        if direction == SwingDirection.BULLISH and current_price < last_sh:
            phase = SwingPhase.CORRECTION
        elif direction == SwingDirection.BEARISH and current_price > last_sl:
            phase = SwingPhase.CORRECTION
        else:
            phase = SwingPhase.EXPANSION if bos_confirmed else SwingPhase.DISTRIBUTION

    # ─── STRENGTH SCORE ───────────────────────────────────────────────
    strength = 0.0

    # Swing displacement contribution (0–0.3)
    if total_range > 0 and atr > 0:
        swing_range = abs(last_sh - last_sl)
        strength += min(0.3, (swing_range / (atr * 10)) * 0.3)

    # Structure consistency (0–0.3)
    if direction != SwingDirection.NEUTRAL:
        strength += 0.3 if (hh and hl) or (lh and ll) else 0.15

    # BOS confirmation (0–0.2)
    if bos_confirmed:
        strength += 0.2

    # Displacement quality (0–0.2)
    strength += min(0.2, displacement_ratio * 0.3)

    strength = round(min(1.0, strength), 3)

    # ─── BUILD REASONING ──────────────────────────────────────────────
    structure_label = ""
    if hh and hl:
        structure_label = "HH+HL"
    elif lh and ll:
        structure_label = "LH+LL"
    elif hh:
        structure_label = "HH (HL missing)"
    elif ll:
        structure_label = "LL (LH missing)"
    else:
        structure_label = "mixed"

    reasoning = (
        f"Dir={direction.value} | Phase={phase.value} | Str={strength:.2f} | "
        f"Structure={structure_label} | BOS={bos_confirmed} | "
        f"Displacement={displacement_ratio:.2f} | {bos_reasoning}"
    )

    return SwingContext(
        current_swing_direction=direction,
        swing_phase=phase,
        swing_strength=strength,
        swing_break_confirmed=bos_confirmed,
        last_swing_high=last_sh,
        last_swing_low=last_sl,
        reasoning=reasoning,
    )


# ─── EXECUTION PERMISSION CHECK ───────────────────────────────────────────────

def check_swing_permission(
    swing_context: SwingContext,
    trade_side: Side,
    strategy_type: str,
) -> tuple[bool, str | None]:
    """
    Check if trade is permitted by swing structure.

    HARD RULE: reversal signals blocked unless swing_break_confirmed.
    DIRECTIONAL RULE: trades must align with swing direction (or break confirmed).

    Args:
        swing_context: Current swing analysis output
        trade_side: BUY or SELL
        strategy_type: A_CONTINUATION / B_REVERSAL / C_FALSE_BREAK

    Returns:
        (allowed: bool, block_reason: str | None)
    """
    direction = swing_context.current_swing_direction
    bos = swing_context.swing_break_confirmed

    # ─── REVERSAL BLOCK (non-overridable) ─────────────────────────────
    if "REVERSAL" in strategy_type and not bos:
        return False, "swing_structure_not_broken (reversal requires BOS confirmation)"

    # ─── DIRECTIONAL ALIGNMENT ────────────────────────────────────────
    if trade_side == Side.BUY:
        if direction == SwingDirection.BULLISH:
            return True, None  # Aligned
        if direction == SwingDirection.BEARISH and not bos:
            return False, "swing_direction_bearish (BUY blocked without bullish BOS)"
        # NEUTRAL or BOS confirmed = allow
        return True, None

    if trade_side == Side.SELL:
        if direction == SwingDirection.BEARISH:
            return True, None  # Aligned
        if direction == SwingDirection.BULLISH and not bos:
            return False, "swing_direction_bullish (SELL blocked without bearish BOS)"
        return True, None

    return True, None  # Fallback: allow
