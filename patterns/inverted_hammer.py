"""INVERTED_HAMMER + SHOOTING_STAR — 1-bar upper rejection patterns."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import INVERTED_HAMMER, SHOOTING_STAR


@register_class
class InvertedHammerPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "INVERTED_HAMMER"

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

        if upper_wick > body * 2 and lower_wick < body and body / total_range < 0.4:
            if c.close > c.open:
                conf = min(1.0, upper_wick / (total_range + 1e-12))
                return [Signal("INVERTED_HAMMER", Side.BUY, closed_index, c.time, confidence=round(conf, 3))]
        return []


@register_class
class ShootingStarPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "SHOOTING_STAR"

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

        if upper_wick > body * 2 and lower_wick < body and body / total_range < 0.4:
            if c.close < c.open:
                conf = min(1.0, upper_wick / (total_range + 1e-12))
                return [Signal("SHOOTING_STAR", Side.SELL, closed_index, c.time, confidence=round(conf, 3))]
        return []
