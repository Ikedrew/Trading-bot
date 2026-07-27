"""
Multi-Timeframe Authority — H1 Bias Analyzer.

Responsibility: Determine directional preference from H1 candle structure.
Produces: BiasSnapshot (BULLISH, BEARISH, NEUTRAL + confidence)

Ownership: core/timeframes/h1_bias.py
Dependencies: types.py, data.mt5_data.Candle
Must NOT import from: cache.py, integration.py, engine.py

Algorithm:
  1. Detect swing structure (HH/HL = bullish, LH/LL = bearish)
  2. Compute EMA-20 position and slope
  3. Assess momentum (consecutive directional closes)
  4. Combine into directional bias with conservative confidence
"""

from __future__ import annotations

from data.mt5_data import Candle
from core.timeframes.types import BiasDirection, BiasSnapshot


def _ema_value(values: list[float], period: int) -> float:
    """Compute latest EMA value."""
    if not values or period <= 0:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    alpha = 2.0 / (period + 1.0)
    ema_val = sum(values[:period]) / period
    for v in values[period:]:
        ema_val = alpha * v + (1.0 - alpha) * ema_val
    return ema_val


def _swing_structure(candles: list[Candle], lookback: int) -> tuple[str, float, float | None, float | None]:
    """
    Analyze swing structure over recent bars.
    Returns (structure_type, strength, last_swing_high, last_swing_low) where:
      structure_type: "HH_HL" | "LH_LL" | "MIXED"
      strength: 0.0–1.0
      last_swing_high: most recent confirmed swing high price (or None)
      last_swing_low: most recent confirmed swing low price (or None)
    """
    if len(candles) < lookback + 1:
        return "MIXED", 0.0, None, None

    window = candles[-lookback:]

    # Find swing highs and lows (simple: local extrema with 1-bar confirmation)
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    for i in range(1, len(window) - 1):
        if window[i].high > window[i - 1].high and window[i].high > window[i + 1].high:
            swing_highs.append(window[i].high)
        if window[i].low < window[i - 1].low and window[i].low < window[i + 1].low:
            swing_lows.append(window[i].low)

    # Extract last swing prices (regardless of structure type)
    _last_high = swing_highs[-1] if swing_highs else None
    _last_low = swing_lows[-1] if swing_lows else None

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "MIXED", 0.0, _last_high, _last_low

    # Check last 3 swing highs/lows for pattern
    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]

    hh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i - 1])
    hl_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i - 1])
    lh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i - 1])
    ll_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i - 1])

    bullish_signals = hh_count + hl_count
    bearish_signals = lh_count + ll_count
    total = bullish_signals + bearish_signals

    if total == 0:
        return "MIXED", 0.0, _last_high, _last_low

    if bullish_signals > bearish_signals:
        strength = bullish_signals / max(total, 1)
        return "HH_HL", min(1.0, strength), _last_high, _last_low
    elif bearish_signals > bullish_signals:
        strength = bearish_signals / max(total, 1)
        return "LH_LL", min(1.0, strength), _last_high, _last_low
    else:
        return "MIXED", 0.0, _last_high, _last_low


def _momentum_score(candles: list[Candle], lookback: int) -> float:
    """
    Compute directional momentum from recent closes.
    Returns: -1.0 (strong bearish) to +1.0 (strong bullish), 0.0 = neutral.
    """
    if len(candles) < lookback:
        return 0.0

    window = candles[-lookback:]
    bullish = sum(1 for c in window if c.close > c.open)
    bearish = sum(1 for c in window if c.close < c.open)
    total = len(window)

    if total == 0:
        return 0.0

    return (bullish - bearish) / total


def _detect_bos(candles: list[Candle], lookback: int) -> tuple[bool, str]:
    """
    Detect Break of Structure from H1 swing pivots.

    BOS = current close has broken beyond the last confirmed swing level.
    - Bullish BOS: close > last swing high
    - Bearish BOS: close < last swing low

    Uses 1-bar confirmed pivots (same method as _swing_structure).

    Args:
        candles: H1 candle history
        lookback: number of recent bars to scan for pivots

    Returns:
        (bos_confirmed: bool, bos_direction: str)
        bos_direction is "BULLISH", "BEARISH", or ""
    """
    if len(candles) < lookback + 1 or lookback < 4:
        return False, ""

    window = candles[-lookback:]
    current_close = candles[-1].close

    # Find swing highs and lows (1-bar confirmation — same as _swing_structure)
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    # Exclude the last bar from pivot detection (it's the bar we're testing against)
    for i in range(1, len(window) - 2):
        if window[i].high > window[i - 1].high and window[i].high > window[i + 1].high:
            swing_highs.append(window[i].high)
        if window[i].low < window[i - 1].low and window[i].low < window[i + 1].low:
            swing_lows.append(window[i].low)

    if not swing_highs and not swing_lows:
        return False, ""

    # Check bullish BOS: current close > last swing high
    if swing_highs:
        last_swing_high = swing_highs[-1]
        if current_close > last_swing_high:
            return True, "BULLISH"

    # Check bearish BOS: current close < last swing low
    if swing_lows:
        last_swing_low = swing_lows[-1]
        if current_close < last_swing_low:
            return True, "BEARISH"

    return False, ""


def analyze_bias(candles: list[Candle]) -> BiasSnapshot:
    """
    Determine H1 directional bias from candle structure.

    Args:
        candles: List of H1 candles (ideally 50+ bars for reliable bias)

    Returns:
        BiasSnapshot with direction and confidence.

    Algorithm:
        1. Swing structure analysis (HH/HL vs LH/LL)
        2. EMA-20 position (price above/below)
        3. EMA slope direction
        4. Momentum (consecutive directional closes)
        5. Conservative confidence scoring
    """
    min_bars = 20
    if len(candles) < min_bars:
        return BiasSnapshot(
            direction=BiasDirection.NEUTRAL,
            confidence=0.0,
            bar_time=candles[-1].time if candles else 0,
            ema_position=0.0,
            swing_structure="MIXED",
            bos_confirmed=False,
            bos_direction="",
        )

    closes = [c.close for c in candles]
    bar_time = candles[-1].time
    current_close = closes[-1]

    # 1. Swing structure
    swing_lookback = min(20, len(candles) - 2)
    structure_type, structure_strength, _last_swing_high, _last_swing_low = _swing_structure(candles, swing_lookback)

    # 2. EMA-20 position
    ema_20 = _ema_value(closes, 20)
    # Compute ATR for normalization
    atr_sum = 0.0
    for i in range(max(1, len(candles) - 14), len(candles)):
        atr_sum += candles[i].high - candles[i].low
    atr_approx = atr_sum / min(14, len(candles) - 1) if len(candles) > 1 else 0.0001
    ema_position = (current_close - ema_20) / atr_approx if atr_approx > 0 else 0.0

    # 3. EMA slope (last 5 bars)
    if len(closes) >= 25:
        ema_prev = _ema_value(closes[:-5], 20)
        ema_slope = (ema_20 - ema_prev) / atr_approx if atr_approx > 0 else 0.0
    else:
        ema_slope = 0.0

    # 4. Momentum
    momentum = _momentum_score(candles, min(10, len(candles)))

    # 5. Combine signals into bias
    bullish_score = 0.0
    bearish_score = 0.0

    # Structure contribution (strongest signal)
    if structure_type == "HH_HL":
        bullish_score += structure_strength * 0.4
    elif structure_type == "LH_LL":
        bearish_score += structure_strength * 0.4

    # EMA position contribution
    if ema_position > 0.5:
        bullish_score += min(0.3, ema_position * 0.15)
    elif ema_position < -0.5:
        bearish_score += min(0.3, abs(ema_position) * 0.15)

    # EMA slope contribution
    if ema_slope > 0.1:
        bullish_score += min(0.15, ema_slope * 0.1)
    elif ema_slope < -0.1:
        bearish_score += min(0.15, abs(ema_slope) * 0.1)

    # Momentum contribution
    if momentum > 0.2:
        bullish_score += momentum * 0.15
    elif momentum < -0.2:
        bearish_score += abs(momentum) * 0.15

    # Determine direction
    net = bullish_score - bearish_score
    confidence = min(1.0, max(bullish_score, bearish_score))

    # Conservative: require clear signal for directional bias
    if net > 0.2:
        direction = BiasDirection.BULLISH
    elif net < -0.2:
        direction = BiasDirection.BEARISH
    else:
        direction = BiasDirection.NEUTRAL
        confidence = max(0.0, 0.3 - abs(net))  # Low confidence when neutral

    # 6. Break of Structure detection
    bos_lookback = min(20, len(candles) - 2)
    bos_confirmed, bos_direction = _detect_bos(candles, bos_lookback)

    return BiasSnapshot(
        direction=direction,
        confidence=round(confidence, 4),
        bar_time=bar_time,
        ema_position=round(ema_position, 4),
        swing_structure=structure_type,
        bos_confirmed=bos_confirmed,
        bos_direction=bos_direction,
        last_swing_high=round(_last_swing_high, 8) if _last_swing_high is not None else None,
        last_swing_low=round(_last_swing_low, 8) if _last_swing_low is not None else None,
    )
