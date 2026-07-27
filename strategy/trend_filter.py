"""EMA-based trend quality filter for generated signals."""

from __future__ import annotations

from data.mt5_data import Candle
from strategy.signals import Side, Signal


def ema(values: list[float], period: int) -> float | None:
    """Return the latest EMA value for the given period."""
    if period <= 0 or len(values) < period:
        return None

    alpha = 2.0 / (period + 1.0)
    ema_value = sum(values[:period]) / period  # standard SMA seed
    for price in values[period:]:
        ema_value = alpha * price + (1.0 - alpha) * ema_value
    return ema_value


def trend_bias_from_ema(candles: list[Candle], closed_index: int, *, period: int) -> Side | None:
    """
    BUY bias when close is above EMA, SELL bias when close is below EMA.
    Returns None when EMA cannot be computed or close equals EMA.
    """
    if closed_index < 0 or closed_index >= len(candles):
        return None

    closes = [c.close for c in candles[: closed_index + 1]]
    ema_value = ema(closes, period)
    if ema_value is None:
        return None

    close = candles[closed_index].close
    if close > ema_value:
        return Side.BUY
    if close < ema_value:
        return Side.SELL
    return None


def passes_trend_filter(signal: Signal, candles: list[Candle], closed_index: int, *, period: int) -> bool:
    """Reject BUY below EMA and SELL above EMA."""
    bias = trend_bias_from_ema(candles, closed_index, period=period)
    if bias is None:
        return False
    return signal.side == bias
