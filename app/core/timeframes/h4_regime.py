"""
Multi-Timeframe Authority — H4 Regime Analyzer.

Responsibility: Classify macro market environment from H4 candle data.
Produces: RegimeSnapshot (TRENDING_BULLISH/BEARISH, RANGING, VOLATILE, TRANSITIONAL)

Ownership: core/timeframes/h4_regime.py
Dependencies: types.py, data.mt5_data.Candle
Must NOT import from: cache.py, integration.py, engine.py

Algorithm:
  1. Compute EMA-20 slope (trend direction + strength)
  2. Compute ATR-14 ratio vs rolling average (volatility assessment)
  3. Detect range compression (consolidation)
  4. Classify regime from combined signals
"""

from __future__ import annotations

from data.mt5_data import Candle
from core.timeframes.types import RegimeClassification, RegimeSnapshot


def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA series. Returns list same length as input (NaN-free after warmup)."""
    if not values or period <= 0:
        return []
    alpha = 2.0 / (period + 1.0)
    result: list[float] = []
    ema_val = sum(values[:period]) / period if len(values) >= period else values[0]
    for i, v in enumerate(values):
        if i < period:
            # Use SMA for warmup period
            ema_val = sum(values[: i + 1]) / (i + 1)
        else:
            ema_val = alpha * v + (1.0 - alpha) * ema_val
        result.append(ema_val)
    return result


def _atr(candles: list[Candle], period: int) -> list[float]:
    """Compute ATR series using Wilder smoothing."""
    if len(candles) < 2:
        return [0.0] * len(candles)

    tr_values: list[float] = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    # Wilder smoothing
    if len(tr_values) < period:
        avg = sum(tr_values) / len(tr_values) if tr_values else 0.0
        return [avg] * len(tr_values)

    atr_series: list[float] = []
    atr_val = sum(tr_values[:period]) / period
    for i, tr in enumerate(tr_values):
        if i < period:
            atr_val = sum(tr_values[: i + 1]) / (i + 1)
        else:
            atr_val = (atr_val * (period - 1) + tr) / period
        atr_series.append(atr_val)
    return atr_series


def _detect_hh_hl(candles: list[Candle], lookback: int) -> tuple[int, int]:
    """Count higher-highs and higher-lows in recent candles. Returns (hh_count, hl_count)."""
    if len(candles) < lookback + 1:
        return 0, 0
    window = candles[-lookback:]
    hh = 0
    hl = 0
    for i in range(1, len(window)):
        if window[i].high > window[i - 1].high:
            hh += 1
        if window[i].low > window[i - 1].low:
            hl += 1
    return hh, hl


def _detect_lh_ll(candles: list[Candle], lookback: int) -> tuple[int, int]:
    """Count lower-highs and lower-lows in recent candles. Returns (lh_count, ll_count)."""
    if len(candles) < lookback + 1:
        return 0, 0
    window = candles[-lookback:]
    lh = 0
    ll = 0
    for i in range(1, len(window)):
        if window[i].high < window[i - 1].high:
            lh += 1
        if window[i].low < window[i - 1].low:
            ll += 1
    return lh, ll


def analyze_regime(candles: list[Candle]) -> RegimeSnapshot:
    """
    Classify H4 market regime from candle structure.

    Args:
        candles: List of H4 candles (ideally 50+ bars for reliable classification)

    Returns:
        RegimeSnapshot with classification and confidence.

    Algorithm:
        1. EMA-20 slope → trend direction and strength
        2. ATR-14 current vs 50-bar average → volatility ratio
        3. HH/HL or LH/LL sequences → structural trend confirmation
        4. Range compression detection → consolidation
        5. Combine signals into classification with confidence
    """
    min_bars = 20
    if len(candles) < min_bars:
        return RegimeSnapshot(
            classification=RegimeClassification.TRANSITIONAL,
            confidence=0.0,
            bar_time=candles[-1].time if candles else 0,
            atr_ratio=0.0,
            ema_slope=0.0,
        )

    closes = [c.close for c in candles]
    bar_time = candles[-1].time

    # 1. EMA-20 slope (normalized by ATR)
    ema_20 = _ema(closes, 20)
    atr_series = _atr(candles, 14)
    current_atr = atr_series[-1] if atr_series else 0.0001

    # Slope over last 5 bars (normalized)
    slope_lookback = min(5, len(ema_20) - 1)
    if slope_lookback > 0 and current_atr > 0:
        ema_slope = (ema_20[-1] - ema_20[-1 - slope_lookback]) / (current_atr * slope_lookback)
    else:
        ema_slope = 0.0

    # 2. ATR ratio (current vs rolling average)
    atr_avg_period = min(50, len(atr_series))
    atr_avg = sum(atr_series[-atr_avg_period:]) / atr_avg_period if atr_avg_period > 0 else current_atr
    atr_ratio = current_atr / atr_avg if atr_avg > 0 else 1.0

    # 3. Structure detection (last 10 bars)
    struct_lookback = min(10, len(candles) - 1)
    hh, hl = _detect_hh_hl(candles, struct_lookback)
    lh, ll = _detect_lh_ll(candles, struct_lookback)

    bullish_structure = (hh + hl) / (struct_lookback * 2) if struct_lookback > 0 else 0.0
    bearish_structure = (lh + ll) / (struct_lookback * 2) if struct_lookback > 0 else 0.0

    # 4. Range compression (last 10 bars: ratio of range to ATR)
    recent = candles[-struct_lookback:] if struct_lookback > 0 else candles[-5:]
    range_high = max(c.high for c in recent)
    range_low = min(c.low for c in recent)
    total_range = range_high - range_low
    range_ratio = total_range / (current_atr * len(recent)) if current_atr > 0 and recent else 1.0

    # 5. Classification logic
    # ─── H4 TREND BIAS (shadow — observational only) ──────────────────
    # Determine directional bias from structure + EMA slope
    _bias_lookback = min(5, struct_lookback)
    _hh5, _hl5 = _detect_hh_hl(candles, _bias_lookback)
    _lh5, _ll5 = _detect_lh_ll(candles, _bias_lookback)
    _max_struct_points = _bias_lookback  # max possible HH or HL in window

    if _max_struct_points > 0:
        _bull_ratio = (_hh5 + _hl5) / (_max_struct_points * 2)
        _bear_ratio = (_lh5 + _ll5) / (_max_struct_points * 2)
    else:
        _bull_ratio = 0.0
        _bear_ratio = 0.0

    if _bull_ratio > 0.5 and ema_slope > 0.05:
        _trend_bias = "BULLISH"
        _trend_strength = round(min(1.0, (_bull_ratio + min(ema_slope / 0.3, 1.0)) / 2.0), 3)
    elif _bear_ratio > 0.5 and ema_slope < -0.05:
        _trend_bias = "BEARISH"
        _trend_strength = round(min(1.0, (_bear_ratio + min(abs(ema_slope) / 0.3, 1.0)) / 2.0), 3)
    else:
        _trend_bias = "NEUTRAL"
        _trend_strength = round(max(0.0, 1.0 - (_bull_ratio + _bear_ratio)), 3)
    # ─── END H4 TREND BIAS ────────────────────────────────────────────

    # Volatile: ATR ratio > 1.5 (current volatility much higher than average)
    if atr_ratio > 1.5:
        confidence = min(1.0, (atr_ratio - 1.0) / 1.5)
        return RegimeSnapshot(
            classification=RegimeClassification.VOLATILE,
            confidence=confidence,
            bar_time=bar_time,
            atr_ratio=round(atr_ratio, 4),
            ema_slope=round(ema_slope, 4),
            trend_bias=_trend_bias,
            trend_strength=_trend_strength,
        )

    # Trending bullish: strong positive slope + bullish structure
    if ema_slope > 0.15 and bullish_structure > 0.5:
        trend_strength = min(1.0, (ema_slope / 0.5 + bullish_structure) / 2.0)
        return RegimeSnapshot(
            classification=RegimeClassification.TRENDING_BULLISH,
            confidence=trend_strength,
            bar_time=bar_time,
            atr_ratio=round(atr_ratio, 4),
            ema_slope=round(ema_slope, 4),
            trend_bias=_trend_bias,
            trend_strength=_trend_strength,
        )

    # Trending bearish: strong negative slope + bearish structure
    if ema_slope < -0.15 and bearish_structure > 0.5:
        trend_strength = min(1.0, (abs(ema_slope) / 0.5 + bearish_structure) / 2.0)
        return RegimeSnapshot(
            classification=RegimeClassification.TRENDING_BEARISH,
            confidence=trend_strength,
            bar_time=bar_time,
            atr_ratio=round(atr_ratio, 4),
            ema_slope=round(ema_slope, 4),
            trend_bias=_trend_bias,
            trend_strength=_trend_strength,
        )

    # Ranging: low slope + compressed range + low ATR ratio
    if abs(ema_slope) < 0.1 and range_ratio < 0.8 and atr_ratio < 1.2:
        ranging_confidence = min(1.0, (1.0 - abs(ema_slope) / 0.1 + (1.0 - range_ratio)) / 2.0)
        return RegimeSnapshot(
            classification=RegimeClassification.RANGING,
            confidence=max(0.3, ranging_confidence),
            bar_time=bar_time,
            atr_ratio=round(atr_ratio, 4),
            ema_slope=round(ema_slope, 4),
            trend_bias=_trend_bias,
            trend_strength=_trend_strength,
        )

    # Transitional: doesn't clearly fit any category
    return RegimeSnapshot(
        classification=RegimeClassification.TRANSITIONAL,
        confidence=0.3,
        bar_time=bar_time,
        atr_ratio=round(atr_ratio, 4),
        ema_slope=round(ema_slope, 4),
        trend_bias=_trend_bias,
        trend_strength=_trend_strength,
    )
