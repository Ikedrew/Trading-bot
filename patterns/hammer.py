"""HAMMER + HANGING_MAN — 1-bar lower rejection patterns."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import HAMMER, HANGING_MAN


@register_class
class HammerPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "HAMMER"

    @property
    def bar_count(self) -> int:
        return 1

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        c = candles[closed_index]
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low
        total_range = c.high - c.low

        if total_range < 1e-12:
            return []

        if lower_wick > body * 2 and upper_wick < body and body / total_range < 0.4:
            if c.close > c.open:
                # Confidence: wick dominance ratio (higher = cleaner rejection)
                conf = min(1.0, lower_wick / (total_range + 1e-12))
                return [Signal("HAMMER", Side.BUY, closed_index, c.time, confidence=round(conf, 3))]
        return []


@register_class
class HangingManPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "HANGING_MAN"

    @property
    def bar_count(self) -> int:
        return 1

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        c = candles[closed_index]
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low
        total_range = c.high - c.low

        if total_range < 1e-12:
            return []

        if lower_wick > body * 2 and upper_wick < body and body / total_range < 0.4:
            if c.close < c.open:
                conf = min(1.0, lower_wick / (total_range + 1e-12))
                return [Signal("HANGING_MAN", Side.SELL, closed_index, c.time, confidence=round(conf, 3))]
        return []
