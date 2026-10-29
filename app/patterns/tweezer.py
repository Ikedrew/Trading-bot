"""TWEEZER_TOP + TWEEZER_BOTTOM — 2-bar equal-extreme patterns."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import TWEEZER_TOP, TWEEZER_BOTTOM


@register_class
class TweezerTopPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "TWEEZER_TOP"

    @property
    def bar_count(self) -> int:
        return 2

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        prev, last = candles[closed_index - 1], candles[closed_index]

        body_prev = abs(prev.close - prev.open)
        if body_prev < 1e-12:
            return []

        if prev.close > prev.open and last.close < last.open and abs(prev.high - last.high) < 0.001:
            # Confidence: how close the highs match (closer = higher confidence)
            diff = abs(prev.high - last.high)
            conf = min(1.0, 1.0 - (diff / 0.001))
            return [Signal("TWEEZER_TOP", Side.SELL, closed_index, last.time, confidence=round(conf, 3))]
        return []


@register_class
class TweezerBottomPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "TWEEZER_BOTTOM"

    @property
    def bar_count(self) -> int:
        return 2

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        prev, last = candles[closed_index - 1], candles[closed_index]

        body_prev = abs(prev.close - prev.open)
        if body_prev < 1e-12:
            return []

        if prev.close < prev.open and last.close > last.open and abs(prev.low - last.low) < 0.001:
            diff = abs(prev.low - last.low)
            conf = min(1.0, 1.0 - (diff / 0.001))
            return [Signal("TWEEZER_BOTTOM", Side.BUY, closed_index, last.time, confidence=round(conf, 3))]
        return []
