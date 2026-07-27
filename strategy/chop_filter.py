"""Post-signal chop filter using only candle price action."""

from __future__ import annotations

from data.mt5_data import Candle


def passes_chop_filter(
    candles: list[Candle],
    closed_index: int,
    *,
    lookback_bars: int,
    min_sum_range: float,
    chop_net_move_ratio: float,
    max_overlap_ratio: float,
) -> bool:
    """
    True when market is tradable (not chop), False when chop/sideways.
    Uses:
      - range expansion over lookback
      - net move vs total range
      - adjacent candle overlap ratio
    """
    if lookback_bars < 2:
        return False
    if closed_index < lookback_bars - 1 or closed_index >= len(candles):
        return False

    window = candles[closed_index - (lookback_bars - 1) : closed_index + 1]
    if len(window) != lookback_bars:
        return False

    total_range = sum(c.high - c.low for c in window)
    if total_range <= 0 or total_range < min_sum_range:
        return False

    net_move = abs(window[-1].close - window[0].open)
    if net_move < chop_net_move_ratio * total_range:
        return False

    overlap_hits = 0
    pairs = len(window) - 1
    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]

        overlap = min(prev.high, cur.high) - max(prev.low, cur.low)
        if overlap <= 0:
            continue

        prev_range = prev.high - prev.low
        cur_range = cur.high - cur.low
        denom = min(prev_range, cur_range)
        if denom <= 0:
            continue

        overlap_ratio = overlap / denom
        if overlap_ratio >= 0.5:
            overlap_hits += 1

    if pairs > 0 and (overlap_hits / pairs) > max_overlap_ratio:
        return False

    return True
