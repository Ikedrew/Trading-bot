"""
Multi-Timeframe Authority — M15 Structure Analyzer.

Responsibility: Validate trade setups against M15 structural context.
Produces: StructureSnapshot (quality score, key levels, order blocks)

Ownership: core/timeframes/m15_structure.py
Dependencies: types.py, data.mt5_data.Candle
Must NOT import from: cache.py, integration.py, engine.py

Algorithm:
  1. Identify swing highs/lows (structural pivots)
  2. Detect support/resistance levels from pivot clusters
  3. Assess price proximity to key levels
  4. Detect order blocks (strong impulsive moves from levels)
  5. Compute structure quality score (clarity, tradability)
"""

from __future__ import annotations

from data.mt5_data import Candle
from core.timeframes.types import StructureSnapshot


def _find_swing_highs(candles: list[Candle], left: int = 2, right: int = 2) -> list[tuple[int, float]]:
    """Find swing highs with left/right confirmation bars. Returns [(index, price)]."""
    swings: list[tuple[int, float]] = []
    for i in range(left, len(candles) - right):
        is_high = True
        for j in range(1, left + 1):
            if candles[i].high <= candles[i - j].high:
                is_high = False
                break
        if not is_high:
            continue
        for j in range(1, right + 1):
            if candles[i].high <= candles[i + j].high:
                is_high = False
                break
        if is_high:
            swings.append((i, candles[i].high))
    return swings


def _find_swing_lows(candles: list[Candle], left: int = 2, right: int = 2) -> list[tuple[int, float]]:
    """Find swing lows with left/right confirmation bars. Returns [(index, price)]."""
    swings: list[tuple[int, float]] = []
    for i in range(left, len(candles) - right):
        is_low = True
        for j in range(1, left + 1):
            if candles[i].low >= candles[i - j].low:
                is_low = False
                break
        if not is_low:
            continue
        for j in range(1, right + 1):
            if candles[i].low >= candles[i + j].low:
                is_low = False
                break
        if is_low:
            swings.append((i, candles[i].low))
    return swings


def _nearest_level(price: float, levels: list[float]) -> float:
    """Find the nearest price level. Returns 0.0 if no levels."""
    if not levels:
        return 0.0
    return min(levels, key=lambda lv: abs(lv - price))


def _detect_order_block(candles: list[Candle], lookback: int) -> bool:
    """
    Detect order block: a strong impulsive move (3+ consecutive directional candles
    with expanding bodies) from a consolidation zone in recent history.
    """
    if len(candles) < lookback + 3:
        return False

    window = candles[-lookback:]

    # Look for 3 consecutive strong directional candles
    for i in range(len(window) - 3):
        c1, c2, c3 = window[i], window[i + 1], window[i + 2]

        # All bullish
        if c1.close > c1.open and c2.close > c2.open and c3.close > c3.open:
            b1 = c1.close - c1.open
            b2 = c2.close - c2.open
            b3 = c3.close - c3.open
            if b2 > b1 and b3 > b2:  # Expanding bodies
                return True

        # All bearish
        if c1.close < c1.open and c2.close < c2.open and c3.close < c3.open:
            b1 = c1.open - c1.close
            b2 = c2.open - c2.close
            b3 = c3.open - c3.close
            if b2 > b1 and b3 > b2:  # Expanding bodies
                return True

    return False


def _structure_clarity(candles: list[Candle], swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]]) -> float:
    """
    Compute structure clarity score (0.0–1.0).
    High clarity = well-defined swings with clear separation.
    Low clarity = choppy, overlapping, indecisive.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return 0.2  # Insufficient structure

    # Measure swing separation consistency
    high_diffs = [abs(swing_highs[i][1] - swing_highs[i - 1][1]) for i in range(1, len(swing_highs))]
    low_diffs = [abs(swing_lows[i][1] - swing_lows[i - 1][1]) for i in range(1, len(swing_lows))]

    if not high_diffs or not low_diffs:
        return 0.3

    # ATR approximation for normalization
    recent = candles[-min(14, len(candles)):]
    atr_approx = sum(c.high - c.low for c in recent) / len(recent) if recent else 0.0001

    # Average swing amplitude (normalized by ATR)
    avg_high_swing = sum(high_diffs) / len(high_diffs)
    avg_low_swing = sum(low_diffs) / len(low_diffs)
    avg_swing = (avg_high_swing + avg_low_swing) / 2.0
    normalized_swing = avg_swing / atr_approx if atr_approx > 0 else 0.0

    # Clarity: larger swings relative to ATR = clearer structure
    # Penalize very small swings (chop) and reward clear moves
    if normalized_swing > 2.0:
        clarity = 0.9
    elif normalized_swing > 1.0:
        clarity = 0.6 + (normalized_swing - 1.0) * 0.3
    elif normalized_swing > 0.5:
        clarity = 0.3 + (normalized_swing - 0.5) * 0.6
    else:
        clarity = normalized_swing * 0.6  # Very choppy

    return min(1.0, max(0.0, clarity))


def analyze_structure(candles: list[Candle], current_price: float) -> StructureSnapshot:
    """
    Evaluate M15 structural quality at current price.

    Args:
        candles: List of M15 candles (ideally 50+ bars)
        current_price: Current bid price for proximity calculations

    Returns:
        StructureSnapshot with quality score and structural metrics.

    Algorithm:
        1. Find swing highs/lows (structural pivots)
        2. Identify nearest support/resistance from pivots
        3. Assess proximity to key levels (at_key_level)
        4. Detect order blocks
        5. Compute overall structure quality (clarity + level proximity)
    """
    min_bars = 10
    if len(candles) < min_bars:
        return StructureSnapshot(
            quality_score=0.0,
            bar_time=candles[-1].time if candles else 0,
            nearest_support=0.0,
            nearest_resistance=0.0,
            at_key_level=False,
            order_block_present=False,
        )

    bar_time = candles[-1].time

    # 1. Find swing pivots
    swing_highs = _find_swing_highs(candles)
    swing_lows = _find_swing_lows(candles)

    # 2. Extract levels
    resistance_levels = [price for _, price in swing_highs]
    support_levels = [price for _, price in swing_lows]

    # 3. Nearest levels
    nearest_resistance = _nearest_level(current_price, [r for r in resistance_levels if r > current_price])
    nearest_support = _nearest_level(current_price, [s for s in support_levels if s < current_price])

    # Fallback: if no level above/below, use closest overall
    if nearest_resistance == 0.0 and resistance_levels:
        nearest_resistance = _nearest_level(current_price, resistance_levels)
    if nearest_support == 0.0 and support_levels:
        nearest_support = _nearest_level(current_price, support_levels)

    # 4. ATR for proximity assessment
    recent = candles[-min(14, len(candles)):]
    atr_approx = sum(c.high - c.low for c in recent) / len(recent) if recent else 0.0001

    # At key level: within 1.5 ATR of nearest S/R
    at_support = nearest_support > 0 and abs(current_price - nearest_support) < atr_approx * 1.5
    at_resistance = nearest_resistance > 0 and abs(current_price - nearest_resistance) < atr_approx * 1.5
    at_key_level = at_support or at_resistance

    # 5. Order block detection
    ob_lookback = min(15, len(candles) - 3)
    order_block_present = _detect_order_block(candles, ob_lookback) if ob_lookback >= 3 else False

    # 6. Structure quality score
    clarity = _structure_clarity(candles, swing_highs, swing_lows)

    # Bonus for being at a key level (tradable location)
    level_bonus = 0.2 if at_key_level else 0.0

    # Bonus for order block presence (institutional interest)
    ob_bonus = 0.1 if order_block_present else 0.0

    # Penalty for no clear levels
    level_penalty = 0.0
    if not resistance_levels and not support_levels:
        level_penalty = 0.3

    quality_score = min(1.0, max(0.0, clarity + level_bonus + ob_bonus - level_penalty))

    return StructureSnapshot(
        quality_score=round(quality_score, 4),
        bar_time=bar_time,
        nearest_support=round(nearest_support, 6),
        nearest_resistance=round(nearest_resistance, 6),
        at_key_level=at_key_level,
        order_block_present=order_block_present,
    )
