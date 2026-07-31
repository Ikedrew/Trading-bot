"""
Tests for Macro Context Phase 2 — Cache Integration.

Verifies:
  1. MN1 candles load correctly via analyze_regime
  2. W1 candles load correctly via analyze_bias
  3. D1 candles load correctly via analyze_regime
  4. Missing macro data degrades safely (returns None)
  5. Existing H4/H1/M15 behaviour unchanged
  6. HTFContext.macro is populated when data available
  7. HTFContext.macro is None when no macro data
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from core.timeframes.cache import (
    TimeframeCache,
    _TF_D1, _TF_W1, _TF_MN, _TF_H4, _TF_H1, _TF_M15,
)
from core.timeframes.types import (
    HTFContext,
    MacroSnapshot,
    RegimeSnapshot,
    BiasSnapshot,
    StructureSnapshot,
    RegimeClassification,
    BiasDirection,
)


# ═══════════════════════════════════════════════════════════════
# TEST HELPERS
# ═══════════════════════════════════════════════════════════════


@dataclass
class FakeCandle:
    """Minimal candle for testing."""
    time: int = 1785400000
    open: float = 1.0800
    high: float = 1.0850
    low: float = 1.0780
    close: float = 1.0830
    tick_volume: int = 100
    spread: int = 2
    real_volume: int = 0


def _make_candles(count: int, base_time: int = 1785000000, tf_seconds: int = 86400) -> list[FakeCandle]:
    """Generate a list of fake candles with incrementing timestamps."""
    candles = []
    for i in range(count):
        candles.append(FakeCandle(
            time=base_time + (i * tf_seconds),
            open=1.08 + i * 0.001,
            high=1.085 + i * 0.001,
            low=1.078 + i * 0.001,
            close=1.083 + i * 0.001,
        ))
    return candles


def _make_regime_snapshot(trend="BULLISH", strength=0.7) -> RegimeSnapshot:
    return RegimeSnapshot(
        classification=RegimeClassification.TRENDING_BULLISH,
        confidence=0.8,
        bar_time=1785400000,
        atr_ratio=1.1,
        ema_slope=0.5,
        trend_bias=trend,
        trend_strength=strength,
    )


def _make_bias_snapshot(direction="BULLISH", confidence=0.75) -> BiasSnapshot:
    return BiasSnapshot(
        direction=BiasDirection.BULLISH if direction == "BULLISH" else BiasDirection.BEARISH,
        confidence=confidence,
        bar_time=1785400000,
        ema_position=0.5,
        swing_structure="HH_HL",
        bos_confirmed=True,
        bos_direction=direction,
        bos_level=1.0850,
        last_swing_high=1.0900,
        last_swing_low=1.0800,
    )


def _make_structure_snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        quality_score=0.7,
        bar_time=1785400000,
        nearest_support=1.0800,
        nearest_resistance=1.0900,
        at_key_level=False,
        order_block_present=False,
    )


# ═══════════════════════════════════════════════════════════════
# CACHE SETUP
# ═══════════════════════════════════════════════════════════════


class TestMacroCacheConstants:
    """Verify TF constants and config are correctly registered."""

    def test_tf_constants_exist(self):
        assert _TF_D1 == 16408
        assert _TF_W1 == 32769
        assert _TF_MN == 49153

    def test_cache_has_macro_entries(self):
        cache = TimeframeCache("EURUSD")
        assert _TF_D1 in cache._entries
        assert _TF_W1 in cache._entries
        assert _TF_MN in cache._entries

    def test_cache_has_macro_configs(self):
        cache = TimeframeCache("EURUSD")
        tf_names = [cfg.name for cfg in cache._tf_configs]
        assert "D1" in tf_names
        assert "W1" in tf_names
        assert "MN" in tf_names


# ═══════════════════════════════════════════════════════════════
# ANALYZER DISPATCH
# ═══════════════════════════════════════════════════════════════


class TestAnalyzerDispatch:
    """Verify _run_analyzer routes D1/W1/MN to correct analyzers."""

    @patch("core.timeframes.cache.analyze_regime")
    def test_d1_uses_regime_analyzer(self, mock_regime):
        mock_regime.return_value = _make_regime_snapshot()
        cache = TimeframeCache("EURUSD")
        candles = _make_candles(30)
        result = cache._run_analyzer(_TF_D1, candles)
        mock_regime.assert_called_once_with(candles)
        assert isinstance(result, RegimeSnapshot)

    @patch("core.timeframes.cache.analyze_bias")
    def test_w1_uses_bias_analyzer(self, mock_bias):
        mock_bias.return_value = _make_bias_snapshot()
        cache = TimeframeCache("EURUSD")
        candles = _make_candles(30)
        result = cache._run_analyzer(_TF_W1, candles)
        mock_bias.assert_called_once_with(candles)
        assert isinstance(result, BiasSnapshot)

    @patch("core.timeframes.cache.analyze_regime")
    def test_mn_uses_regime_analyzer(self, mock_regime):
        mock_regime.return_value = _make_regime_snapshot()
        cache = TimeframeCache("EURUSD")
        candles = _make_candles(30)
        result = cache._run_analyzer(_TF_MN, candles)
        mock_regime.assert_called_once_with(candles)
        assert isinstance(result, RegimeSnapshot)

    @patch("core.timeframes.cache.analyze_regime")
    def test_h4_still_uses_regime_analyzer(self, mock_regime):
        """Existing H4 behaviour unchanged."""
        mock_regime.return_value = _make_regime_snapshot()
        cache = TimeframeCache("EURUSD")
        candles = _make_candles(30)
        result = cache._run_analyzer(_TF_H4, candles)
        mock_regime.assert_called_once()
        assert isinstance(result, RegimeSnapshot)

    @patch("core.timeframes.cache.analyze_bias")
    def test_h1_still_uses_bias_analyzer(self, mock_bias):
        """Existing H1 behaviour unchanged."""
        mock_bias.return_value = _make_bias_snapshot()
        cache = TimeframeCache("EURUSD")
        candles = _make_candles(30)
        result = cache._run_analyzer(_TF_H1, candles)
        mock_bias.assert_called_once()
        assert isinstance(result, BiasSnapshot)


# ═══════════════════════════════════════════════════════════════
# MacroSnapshot BUILDING
# ═══════════════════════════════════════════════════════════════


class TestBuildMacroSnapshot:
    """Verify _build_macro_snapshot correctly maps analyzer outputs."""

    def test_no_data_returns_none(self):
        """All macro entries empty → macro is None."""
        cache = TimeframeCache("EURUSD")
        result = cache._build_macro_snapshot(1.0850)
        assert result is None

    def test_monthly_only(self):
        """Only MN has data → partial MacroSnapshot."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_MN].snapshot = _make_regime_snapshot("BULLISH", 0.65)
        cache._entries[_TF_MN].bar_time = 1785000000
        result = cache._build_macro_snapshot(1.0850)
        assert result is not None
        assert result.monthly_trend == "BULLISH"
        assert result.monthly_trend_strength == 0.65
        assert result.weekly_trend == ""
        assert result.daily_bias == ""

    def test_weekly_only(self):
        """Only W1 has data → weekly fields populated."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BEARISH", 0.70)
        cache._entries[_TF_W1].bar_time = 1785300000
        result = cache._build_macro_snapshot(1.0850)
        assert result is not None
        assert result.weekly_trend == "BEARISH"
        assert result.weekly_trend_strength == 0.70
        assert result.weekly_swing_high == 1.0900
        assert result.weekly_swing_low == 1.0800

    def test_daily_only(self):
        """Only D1 has data → daily fields populated."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_D1].snapshot = _make_regime_snapshot("BEARISH", 0.55)
        cache._entries[_TF_D1].bar_time = 1785400000
        result = cache._build_macro_snapshot(1.0850)
        assert result is not None
        assert result.daily_bias == "BEARISH"
        assert result.daily_bias_strength == 0.55
        assert result.daily_atr_ratio == 1.1

    def test_all_three_populated(self):
        """Full macro data available."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_MN].snapshot = _make_regime_snapshot("BULLISH", 0.80)
        cache._entries[_TF_MN].bar_time = 1784000000
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BULLISH", 0.75)
        cache._entries[_TF_W1].bar_time = 1785200000
        cache._entries[_TF_D1].snapshot = _make_regime_snapshot("BULLISH", 0.60)
        cache._entries[_TF_D1].bar_time = 1785400000
        result = cache._build_macro_snapshot(1.0850)
        assert result is not None
        assert result.monthly_trend == "BULLISH"
        assert result.weekly_trend == "BULLISH"
        assert result.daily_bias == "BULLISH"
        assert result.bar_time == 1785400000  # max of all bar_times

    def test_weekly_range_position_calculated(self):
        """Weekly range position computed from swing levels + current price."""
        cache = TimeframeCache("EURUSD")
        # swing_high=1.0900, swing_low=1.0800, price=1.0850 → position = 0.50
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BULLISH", 0.7)
        cache._entries[_TF_W1].bar_time = 1785300000
        result = cache._build_macro_snapshot(1.0850)
        assert result.weekly_range_position == pytest.approx(0.50, abs=0.01)

    def test_weekly_range_position_at_low(self):
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BULLISH", 0.7)
        cache._entries[_TF_W1].bar_time = 1785300000
        result = cache._build_macro_snapshot(1.0800)  # at swing_low
        assert result.weekly_range_position == 0.0

    def test_weekly_range_position_at_high(self):
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BULLISH", 0.7)
        cache._entries[_TF_W1].bar_time = 1785300000
        result = cache._build_macro_snapshot(1.0900)  # at swing_high
        assert result.weekly_range_position == 1.0


# ═══════════════════════════════════════════════════════════════
# HTFContext INTEGRATION
# ═══════════════════════════════════════════════════════════════


class TestHTFContextMacro:
    """Verify get_htf_context includes macro field."""

    def test_htf_context_has_macro_field(self):
        """HTFContext dataclass has macro attribute."""
        ctx = HTFContext()
        assert ctx.macro is None

    def test_htf_context_macro_none_when_no_data(self):
        """get_htf_context returns macro=None when no D1/W1/MN cached."""
        cache = TimeframeCache("EURUSD")
        ctx = cache.get_htf_context(current_price=1.0850)
        assert ctx.macro is None

    def test_htf_context_macro_populated_when_data_exists(self):
        """get_htf_context returns populated macro when D1/W1/MN have data."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_D1].snapshot = _make_regime_snapshot("BULLISH", 0.6)
        cache._entries[_TF_D1].bar_time = 1785400000
        cache._entries[_TF_W1].snapshot = _make_bias_snapshot("BULLISH", 0.7)
        cache._entries[_TF_W1].bar_time = 1785300000
        ctx = cache.get_htf_context(current_price=1.0850)
        assert ctx.macro is not None
        assert isinstance(ctx.macro, MacroSnapshot)
        assert ctx.macro.daily_bias == "BULLISH"
        assert ctx.macro.weekly_trend == "BULLISH"

    def test_existing_htf_fields_unchanged(self):
        """H4/H1/M15 fields still work exactly as before."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_H4].snapshot = _make_regime_snapshot("BEARISH", 0.5)
        cache._entries[_TF_H1].snapshot = _make_bias_snapshot("BEARISH", 0.6)
        cache._entries[_TF_M15].snapshot = _make_structure_snapshot()
        ctx = cache.get_htf_context(current_price=1.0850)
        assert ctx.regime is not None
        assert ctx.regime.trend_bias == "BEARISH"
        assert ctx.bias is not None
        assert ctx.bias.direction == BiasDirection.BEARISH
        assert ctx.structure is not None
        assert ctx.structure.quality_score == 0.7

    def test_is_populated_ignores_macro(self):
        """is_populated only checks regime/bias/structure (not macro)."""
        ctx = HTFContext(macro=MacroSnapshot(monthly_trend="BULLISH"))
        assert ctx.is_populated is False  # No regime/bias/structure

    def test_is_populated_with_regime(self):
        ctx = HTFContext(regime=_make_regime_snapshot())
        assert ctx.is_populated is True


# ═══════════════════════════════════════════════════════════════
# DEGRADATION TESTS
# ═══════════════════════════════════════════════════════════════


class TestMacroDegradation:
    """Verify missing/partial macro data degrades safely."""

    def test_analyzer_failure_returns_none(self):
        """If analyzer raises, snapshot stays None."""
        cache = TimeframeCache("EURUSD")
        with patch("core.timeframes.cache.analyze_regime", side_effect=ValueError("bad data")):
            result = cache._run_analyzer(_TF_D1, _make_candles(5))
        assert result is None

    def test_macro_none_does_not_affect_h4h1m15(self):
        """Even if macro build fails, H4/H1/M15 still return correctly."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_H4].snapshot = _make_regime_snapshot()
        cache._entries[_TF_H1].snapshot = _make_bias_snapshot()
        # No D1/W1/MN data
        ctx = cache.get_htf_context(current_price=1.0850)
        assert ctx.macro is None
        assert ctx.regime is not None
        assert ctx.bias is not None

    def test_partial_macro_still_builds(self):
        """If only D1 available, MacroSnapshot still built (partial)."""
        cache = TimeframeCache("EURUSD")
        cache._entries[_TF_D1].snapshot = _make_regime_snapshot("NEUTRAL", 0.3)
        cache._entries[_TF_D1].bar_time = 1785400000
        ctx = cache.get_htf_context(current_price=1.0850)
        assert ctx.macro is not None
        assert ctx.macro.monthly_trend == ""  # MN not available
        assert ctx.macro.weekly_trend == ""   # W1 not available
        assert ctx.macro.daily_bias == "NEUTRAL"
