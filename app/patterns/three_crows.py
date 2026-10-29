"""THREE_BLACK_CROWS — 3-bar bearish continuation pattern."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import THREE_BLACK_CROWS


@register_class
class ThreeBlackCrowsPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "THREE_BLACK_CROWS"

    @property
    def bar_count(self) -> int:
        return 3

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        first, second, third = candles[closed_index - 2], candles[closed_index - 1], candles[closed_index]

        if (
            first.close < first.open
            and second.close < second.open
            and third.close < third.open
            and second.close < first.close
            and third.close < second.close
        ):
            bodies = [abs(c.close - c.open) for c in (first, second, third)]
            avg_body = sum(bodies) / 3
            min_body = min(bodies)
            conf = min(1.0, min_body / (avg_body + 1e-12))
            return [Signal("THREE_BLACK_CROWS", Side.SELL, closed_index, third.time, confidence=round(conf, 3))]
        return []
