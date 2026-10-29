"""Stable directional setup bias."""

from __future__ import annotations

from data.mt5_data import Candle
from strategy.signals import Side


def setup_bias(
    candles: list[Candle],
    closed_index: int,
    *,
    ma_period: int,
    min_distance_from_ma: float,
) -> Side | None:
    """
    Stable directional bias using:
    - MA position
    - MA slope
    - recent candle agreement
    """
    if closed_index < ma_period + 3:
        return None

    current_slice = candles[closed_index - ma_period : closed_index]
    current_ma = sum(c.close for c in current_slice) / len(current_slice)

    previous_slice = candles[closed_index - ma_period - 1 : closed_index - 1]
    previous_ma = sum(c.close for c in previous_slice) / len(previous_slice)

    close = candles[closed_index].close

    ma_rising = current_ma > previous_ma
    ma_falling = current_ma < previous_ma

    recent = candles[closed_index - 3 : closed_index]
    bullish_closes = sum(1 for c in recent if c.close > c.open)
    bearish_closes = sum(1 for c in recent if c.close < c.open)

    bullish_eval = (
        close > current_ma + min_distance_from_ma
        and ma_rising
        and bullish_closes >= 2
    )
    bearish_eval = (
        close < current_ma - min_distance_from_ma
        and ma_falling
        and bearish_closes >= 2
    )

    if bullish_eval:
        return Side.BUY

    if bearish_eval:
        return Side.SELL

    return None
