"""
Tests for H4 trend propagation fix.

Verifies that build_h4_understanding() unconditionally propagates
trend_bias and trend_strength from RegimeSnapshot for ALL classifications:
  - TRENDING_BULLISH → BULLISH
  - TRENDING_BEARISH → BEARISH
  - VOLATILE + bullish → BULLISH
  - VOLATILE + bearish → BEARISH
  - TRANSITIONAL + trend_bias → preserved
  - RANGING + NEUTRAL bias → NEUTRAL
"""

import pytest
from unittest.mock import MagicMock
from core.market_understanding.builders import build_h4_understanding
from core.timeframes.types import RegimeSnapshot, RegimeClassification


def _make_htf_context(classification: RegimeClassification, trend_bias: str, trend_strength: float) -> MagicMock:
    """Build a mock HTFContext with a RegimeSnapshot."""
    regime_snap = RegimeSnapshot(
        classification=classification,
        confidence=0.8,
        bar_time=1785400000,
        atr_ratio=1.1,
        ema_slope=0.3,
        trend_bias=trend_bias,
        trend_strength=trend_strength,
    )
    ctx = MagicMock()
    ctx.regime = regime_snap
    return ctx


class TestTrendingBullish:
    def test_trending_bullish_propagates_trend(self):
        htf = _make_htf_context(RegimeClassification.TRENDING_BULLISH, "BULLISH", 0.75)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.75

    def test_trending_bullish_high_strength(self):
        htf = _make_htf_context(RegimeClassification.TRENDING_BULLISH, "BULLISH", 0.95)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.95


class TestTrendingBearish:
    def test_trending_bearish_propagates_trend(self):
        htf = _make_htf_context(RegimeClassification.TRENDING_BEARISH, "BEARISH", 0.70)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BEARISH"
        assert result.trend_strength == 0.70

    def test_trending_bearish_moderate_strength(self):
        htf = _make_htf_context(RegimeClassification.TRENDING_BEARISH, "BEARISH", 0.55)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BEARISH"
        assert result.trend_strength == 0.55


class TestVolatileBullish:
    def test_volatile_bullish_propagates_trend(self):
        """VOLATILE regime with bullish structure → h4.trend = BULLISH (previously lost)."""
        htf = _make_htf_context(RegimeClassification.VOLATILE, "BULLISH", 0.65)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.65

    def test_volatile_bullish_high_strength(self):
        htf = _make_htf_context(RegimeClassification.VOLATILE, "BULLISH", 0.80)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.80


class TestVolatileBearish:
    def test_volatile_bearish_propagates_trend(self):
        """VOLATILE regime with bearish structure → h4.trend = BEARISH (previously lost)."""
        htf = _make_htf_context(RegimeClassification.VOLATILE, "BEARISH", 0.60)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BEARISH"
        assert result.trend_strength == 0.60


class TestVolatileNeutral:
    def test_volatile_neutral_propagates_neutral(self):
        """VOLATILE regime with no directional structure → NEUTRAL."""
        htf = _make_htf_context(RegimeClassification.VOLATILE, "NEUTRAL", 0.30)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "NEUTRAL"
        assert result.trend_strength == 0.30


class TestTransitional:
    def test_transitional_bullish_propagates(self):
        """TRANSITIONAL with bullish bias → preserved."""
        htf = _make_htf_context(RegimeClassification.TRANSITIONAL, "BULLISH", 0.40)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.40

    def test_transitional_bearish_propagates(self):
        htf = _make_htf_context(RegimeClassification.TRANSITIONAL, "BEARISH", 0.35)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BEARISH"
        assert result.trend_strength == 0.35

    def test_transitional_neutral_propagates(self):
        htf = _make_htf_context(RegimeClassification.TRANSITIONAL, "NEUTRAL", 0.50)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "NEUTRAL"


class TestRanging:
    def test_ranging_neutral_preserved(self):
        """RANGING regime with NEUTRAL trend_bias → h4.trend = NEUTRAL."""
        htf = _make_htf_context(RegimeClassification.RANGING, "NEUTRAL", 0.80)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "NEUTRAL"
        assert result.trend_strength == 0.80

    def test_ranging_with_directional_bias(self):
        """RANGING but analyzer detected directional structure → propagated."""
        htf = _make_htf_context(RegimeClassification.RANGING, "BULLISH", 0.45)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "BULLISH"
        assert result.trend_strength == 0.45


class TestEdgeCases:
    def test_no_htf_context_returns_empty(self):
        """No HTFContext → default empty."""
        result = build_h4_understanding(htf_context=None)
        assert result.trend == ""
        assert result.trend_strength == 0.0

    def test_htf_context_no_regime(self):
        """HTFContext with regime=None → default empty."""
        ctx = MagicMock()
        ctx.regime = None
        result = build_h4_understanding(htf_context=ctx)
        assert result.trend == ""

    def test_empty_trend_bias_defaults_neutral(self):
        """RegimeSnapshot.trend_bias="" → defaults to NEUTRAL."""
        htf = _make_htf_context(RegimeClassification.VOLATILE, "", 0.0)
        result = build_h4_understanding(htf_context=htf)
        assert result.trend == "NEUTRAL"

    def test_volatility_state_still_computed(self):
        """ATR ratio > 1.3 → EXPANSION regardless of regime."""
        snap = RegimeSnapshot(
            classification=RegimeClassification.VOLATILE,
            confidence=0.8, bar_time=1785400000,
            atr_ratio=1.6, ema_slope=0.2,
            trend_bias="BULLISH", trend_strength=0.65,
        )
        ctx = MagicMock()
        ctx.regime = snap
        result = build_h4_understanding(htf_context=ctx)
        assert result.volatility_state == "EXPANSION"
        assert result.trend == "BULLISH"
