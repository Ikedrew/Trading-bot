"""Market regime filter — volatility and chop (no trade signals)."""

from __future__ import annotations
from data.mt5_data import Candle


def passes_market_filter(
    candles: list[Candle],
    closed_index: int,
    *,
    lookback_bars: int,
    min_sum_range: float,
    chop_net_move_ratio: float,
) -> tuple[bool, str]:
    """
    Last `lookback_bars` candles (ending at closed_index) must show enough range
    and enough net direction vs noise (not choppy).
    """

    if closed_index < lookback_bars - 1:
        return False, "not enough candles"

    window = candles[closed_index - (lookback_bars - 1): closed_index + 1]

    if len(window) != lookback_bars:
        return False, "invalid window size"

    total_range = sum(c.high - c.low for c in window)

    if total_range < min_sum_range:
        return False, f"low volatility (range={total_range:.6f})"

    net = abs(window[-1].close - window[0].open)

    if total_range <= 0:
        return False, "zero range error"

    if net < chop_net_move_ratio * total_range:
        return False, f"chop detected (net={net:.6f})"

    return True, "trend aligned"
