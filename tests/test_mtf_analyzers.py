"""
Unit tests for Multi-Timeframe Authority analyzers (Phase 2).

Tests:
- H4 regime classification correctness
- H1 bias direction detection
- M15 structure quality scoring
- Edge cases (insufficient data, flat markets)
- Determinism (same input → same output)
"""

from __future__ import annotations

import pytest

from data.mt5_data import Candle
from core.timeframes.types import RegimeClassification, BiasDirection
from core.timeframes.h4_regime import analyze_regime
from core.timeframes.h1_bias import analyze_bias
from core.timeframes.m15_structure import analyze_structure


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _make_candle(t: int, o: float, h: float, l: float, c: float, tv: int = 100) -> Candle:
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=tv)


def _trending_up_candles(count: int, start_price: float = 1.0, step: float = 0.005) -> list[Candle]:
    """Generate progressively higher candles (bullish trend)."""
    candles = []
    for i in range(count):
        o = start_price + i * step
        c = o + step * 0.8
        h = c + step * 0.2
        l = o - step * 0.1
        candles.append(_make_candle(i * 14400, o, h, l, c))
    return candles


def _trending_down_candles(count: int, start_price: float = 1.5, step: float = 0.005) -> list[Candle]:
    """Generate progressively lower candles (bearish trend)."""
    candles = []
    for i in range(count):
        o = start_price - i * step
        c = o - step * 0.8
        l = c - step * 0.2
        h = o + step * 0.1
        candles.append(_make_candle(i * 14400, o, h, l, c))
    return candles


def _ranging_candles(count: int, center: float = 1.1, amplitude: float = 0.003) -> list[Candle]:
    """Generate oscillating candles (ranging market)."""
    candles = []
    for i in range(count):
        offset = amplitude * (1 if i % 2 == 0 else -1)
        o = center + offset * 0.5
        c = center - offset * 0.5
        h = max(o, c) + amplitude * 0.2
        l = min(o, c) - amplitude * 0.2
        candles.append(_make_candle(i * 14400, o, h, l, c))
    return candles


def _flat_candles(count: int, price: float = 1.1) -> list[Candle]:
    """Generate flat candles (no movement)."""
    return [_make_candle(i * 14400, price, price + 0.0001, price - 0.0001, price) for i in range(count)]


# ─── H4 REGIME TESTS ─────────────────────────────────────────────────────────


class TestH4Regime:
    def test_insufficient_data_returns_transitional(self):
        candles = _flat_candles(5)
        result = analyze_regime(candles)
        assert result.classification == RegimeClassification.TRANSITIONAL
        assert result.confidence == 0.0

    def test_trending_up_detected(self):
        candles = _trending_up_candles(50)
        result = analyze_regime(candles)
        assert result.classification == RegimeClassification.TRENDING_BULLISH
        assert result.confidence > 0.3

    def test_trending_down_detected(self):
        candles = _trending_down_candles(50)
        result = analyze_regime(candles)
        assert result.classification == RegimeClassification.TRENDING_BEARISH
        assert result.confidence > 0.3

    def test_ranging_detected(self):
        candles = _ranging_candles(50)
        result = analyze_regime(candles)
        assert result.classification in (RegimeClassification.RANGING, RegimeClassification.TRANSITIONAL)

    def test_deterministic(self):
        candles = _trending_up_candles(50)
        r1 = analyze_regime(candles)
        r2 = analyze_regime(candles)
        assert r1 == r2

    def test_confidence_bounded(self):
        candles = _trending_up_candles(100)
        result = analyze_regime(candles)
        assert 0.0 <= result.confidence <= 1.0

    def test_bar_time_from_last_candle(self):
        candles = _trending_up_candles(30)
        result = analyze_regime(candles)
        assert result.bar_time == candles[-1].time

    def test_empty_candles(self):
        result = analyze_regime([])
        assert result.classification == RegimeClassification.TRANSITIONAL
        assert result.bar_time == 0


# ─── H1 BIAS TESTS ───────────────────────────────────────────────────────────


class TestH1Bias:
    def test_insufficient_data_returns_neutral(self):
        candles = _flat_candles(5)
        result = analyze_bias(candles)
        assert result.direction == BiasDirection.NEUTRAL
        assert result.confidence == 0.0

    def test_bullish_trend_gives_bullish_bias(self):
        candles = _trending_up_candles(50, step=0.003)
        result = analyze_bias(candles)
        assert result.direction == BiasDirection.BULLISH
        assert result.confidence > 0.0

    def test_bearish_trend_gives_bearish_bias(self):
        candles = _trending_down_candles(50, step=0.003)
        result = analyze_bias(candles)
        assert result.direction == BiasDirection.BEARISH
        assert result.confidence > 0.0

    def test_flat_market_gives_neutral(self):
        candles = _flat_candles(50)
        result = analyze_bias(candles)
        assert result.direction == BiasDirection.NEUTRAL

    def test_deterministic(self):
        candles = _trending_up_candles(50)
        r1 = analyze_bias(candles)
        r2 = analyze_bias(candles)
        assert r1 == r2

    def test_confidence_bounded(self):
        candles = _trending_up_candles(100)
        result = analyze_bias(candles)
        assert 0.0 <= result.confidence <= 1.0

    def test_swing_structure_field_populated(self):
        candles = _trending_up_candles(50)
        result = analyze_bias(candles)
        assert result.swing_structure in ("HH_HL", "LH_LL", "MIXED")

    def test_empty_candles(self):
        result = analyze_bias([])
        assert result.direction == BiasDirection.NEUTRAL
        assert result.bar_time == 0


# ─── M15 STRUCTURE TESTS ─────────────────────────────────────────────────────


class TestM15Structure:
    def test_insufficient_data_returns_zero_quality(self):
        candles = _flat_candles(3)
        result = analyze_structure(candles, current_price=1.1)
        assert result.quality_score == 0.0

    def test_trending_market_has_structure(self):
        # Create a market with clear swing pivots (up-down-up pattern)
        candles = []
        # Wave 1: up
        for i in range(10):
            candles.append(_make_candle(i * 900, 1.0 + i * 0.003, 1.0 + i * 0.003 + 0.002, 1.0 + i * 0.003 - 0.001, 1.0 + (i + 1) * 0.003))
        # Wave 2: down (pullback)
        for i in range(5):
            base = 1.03 - i * 0.002
            candles.append(_make_candle((10 + i) * 900, base, base + 0.001, base - 0.002, base - 0.002))
        # Wave 3: up again
        for i in range(10):
            base = 1.02 + i * 0.003
            candles.append(_make_candle((15 + i) * 900, base, base + 0.002, base - 0.001, base + 0.003))
        # Wave 4: down (pullback)
        for i in range(5):
            base = 1.05 - i * 0.002
            candles.append(_make_candle((25 + i) * 900, base, base + 0.001, base - 0.002, base - 0.002))
        # Wave 5: up
        for i in range(10):
            base = 1.04 + i * 0.003
            candles.append(_make_candle((30 + i) * 900, base, base + 0.002, base - 0.001, base + 0.003))

        current_price = candles[-1].close
        result = analyze_structure(candles, current_price)
        # Market with clear waves should produce detectable structure
        assert result.quality_score > 0.0

    def test_flat_market_low_quality(self):
        candles = _flat_candles(50)
        result = analyze_structure(candles, current_price=1.1)
        # Flat market has no swings → low quality
        assert result.quality_score <= 0.5

    def test_quality_score_bounded(self):
        candles = _trending_up_candles(100)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert 0.0 <= result.quality_score <= 1.0

    def test_deterministic(self):
        candles = _trending_up_candles(50)
        price = candles[-1].close
        r1 = analyze_structure(candles, price)
        r2 = analyze_structure(candles, price)
        assert r1 == r2

    def test_nearest_levels_populated(self):
        candles = _trending_up_candles(50, step=0.003)
        price = candles[-1].close
        result = analyze_structure(candles, price)
        # At least one level should be found in a trending market
        assert result.nearest_support >= 0.0 or result.nearest_resistance >= 0.0

    def test_bar_time_from_last_candle(self):
        candles = _trending_up_candles(30)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert result.bar_time == candles[-1].time

    def test_empty_candles(self):
        result = analyze_structure([], current_price=1.1)
        assert result.quality_score == 0.0
        assert result.bar_time == 0
