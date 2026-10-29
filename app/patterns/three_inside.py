"""THREE_INSIDE_UP + THREE_INSIDE_DOWN — 3-bar inside bar breakout patterns."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import THREE_INSIDE_UP, THREE_INSIDE_DOWN


@register_class
class ThreeInsideUpPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "THREE_INSIDE_UP"

    @property
    def bar_count(self) -> int:
        return 3

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        first, second, third = candles[closed_index - 2], candles[closed_index - 1], candles[closed_index]

        if (
            first.close < first.open
            and second.close > second.open
            and second.low > first.low
            and second.high < first.high
            and third.close > third.open
            and third.close > second.open
        ):
            # Confidence: how contained the inside bar is (tighter = higher)
            first_range = first.high - first.low
            second_range = second.high - second.low
            conf = min(1.0, 1.0 - (second_range / (first_range + 1e-12)))
            return [Signal("THREE_INSIDE_UP", Side.BUY, closed_index, third.time, confidence=round(conf, 3))]
        return []


@register_class
class ThreeInsideDownPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "THREE_INSIDE_DOWN"

    @property
    def bar_count(self) -> int:
        return 3

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        first, second, third = candles[closed_index - 2], candles[closed_index - 1], candles[closed_index]

        if (
            first.close > first.open
            and second.close < second.open
            and second.low > first.low
            and second.high < first.high
            and third.close < third.open
            and third.close < second.open
        ):
            first_range = first.high - first.low
            second_range = second.high - second.low
            conf = min(1.0, 1.0 - (second_range / (first_range + 1e-12)))
            return [Signal("THREE_INSIDE_DOWN", Side.SELL, closed_index, third.time, confidence=round(conf, 3))]
        return []
