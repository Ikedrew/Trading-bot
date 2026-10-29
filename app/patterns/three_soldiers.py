"""THREE_WHITE_SOLDIERS — 3-bar bullish continuation pattern."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import THREE_WHITE_SOLDIERS


@register_class
class ThreeWhiteSoldiersPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "THREE_WHITE_SOLDIERS"

    @property
    def bar_count(self) -> int:
        return 3

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        first, second, third = candles[closed_index - 2], candles[closed_index - 1], candles[closed_index]

        if (
            first.close > first.open
            and second.close > second.open
            and third.close > third.open
            and second.close > first.close
            and third.close > second.close
        ):
            # Confidence: consistency of progressive closes
            bodies = [abs(c.close - c.open) for c in (first, second, third)]
            avg_body = sum(bodies) / 3
            min_body = min(bodies)
            conf = min(1.0, min_body / (avg_body + 1e-12))
            return [Signal("THREE_WHITE_SOLDIERS", Side.BUY, closed_index, third.time, confidence=round(conf, 3))]
        return []
