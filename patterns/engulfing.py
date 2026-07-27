"""BULLISH_ENGULFING + BEARISH_ENGULFING — 2-bar reversal patterns."""

from __future__ import annotations

from data.mt5_data import Candle
from patterns.base import PatternDetector
from patterns.registry import register_class
from strategy.signals import Side, Signal
from patterns.ids import BULLISH_ENGULFING, BEARISH_ENGULFING


@register_class
class BullishEngulfingPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "BULLISH_ENGULFING"

    @property
    def bar_count(self) -> int:
        return 2

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        prev, last = candles[closed_index - 1], candles[closed_index]

        if (
            prev.close < prev.open
            and last.close > last.open
            and last.open < prev.close
            and last.close > prev.open
        ):
            # Confidence: how much the current body exceeds the previous body
            prev_body = abs(prev.close - prev.open)
            last_body = abs(last.close - last.open)
            conf = min(1.0, last_body / (prev_body + 1e-12)) if prev_body > 0 else 0.7
            return [Signal("BULLISH_ENGULFING", Side.BUY, closed_index, last.time, confidence=round(min(conf, 1.0), 3))]
        return []


@register_class
class BearishEngulfingPattern(PatternDetector):
    @property
    def name(self) -> str:
        return "BEARISH_ENGULFING"

    @property
    def bar_count(self) -> int:
        return 2

    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        prev, last = candles[closed_index - 1], candles[closed_index]

        if (
            prev.close > prev.open
            and last.close < last.open
            and last.open > prev.close
            and last.close < prev.open
        ):
            prev_body = abs(prev.close - prev.open)
            last_body = abs(last.close - last.open)
            conf = min(1.0, last_body / (prev_body + 1e-12)) if prev_body > 0 else 0.7
            return [Signal("BEARISH_ENGULFING", Side.SELL, closed_index, last.time, confidence=round(min(conf, 1.0), 3))]
        return []
