"""EVENING_STAR — 3-bar bearish reversal pattern."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import EVENING_STAR


@register_class
class EveningStarPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "EVENING_STAR"

    @property
    def bar_count(self) -> int:
        return 3

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        first, second, third = candles[closed_index - 2], candles[closed_index - 1], candles[closed_index]

        if (
            first.close > first.open
            and abs(second.close - second.open) < (second.high - second.low) * 0.3
            and third.close < third.open
            and third.close < second.open
        ):
            mid_range = second.high - second.low
            mid_body = abs(second.close - second.open)
            conf = min(1.0, 1.0 - (mid_body / (mid_range + 1e-12)))
            return [Signal("EVENING_STAR", Side.SELL, closed_index, third.time, confidence=round(conf, 3))]
        return []
