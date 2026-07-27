"""
Tests for M3A — H1 BOS (Break of Structure) Generation.

Validates:
1. Bullish BOS detected correctly
2. Bearish BOS detected correctly
3. No BOS when price remains within swing range
4. Insufficient swing data handled safely
5. BOS fields propagate to BiasSnapshot and MarketContext
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.timeframes.h1_bias import analyze_bias, _detect_bos
from core.timeframes.types import BiasSnapshot, BiasDirection


# ─── HELPERS ──────────────────────────────────────────────────────────────────


@dataclass
class FakeH1Candle:
    """Minimal candle for H1 BOS testing."""
    open: float
    high: float
    low: float
    close: float
    time: int = 0
    tick_volume: int = 100
    real_volume: int = 100
    spread: int = 1


def _make_ranging_candles(n: int, base: float = 1.1000, range_size: float = 0.0020) -> list[FakeH1Candle]:
    """Create n candles oscillating within a range (no BOS)."""
    candles = []
    for i in range(n):
        mid = base + (range_size * 0.5 * (1 if i % 2 == 0 else -1))
        candles.append(FakeH1Candle(
            open=mid - range_size * 0.2,
            high=mid + range_size * 0.3,
            low=mid - range_size * 0.3,
            close=mid + range_size * 0.1,
            time=i * 3600,
        ))
    return candles


def _make_bullish_breakout_candles(n: int = 25) -> list[FakeH1Candle]:
    """
    Create candles with clear swing pivots, then a bullish breakout at the end.
    Structure: ranging with swings → final bar closes above last swing high.
    """
    candles = []
    # Build up swing highs and lows
    prices = [
        # Swing low → swing high → swing low → swing high → breakout
        1.1000, 1.1010, 1.1020, 1.1015, 1.1005,  # up then down (swing high at idx 2)
        1.0995, 1.0985, 1.0990, 1.1000, 1.1010,  # down then up (swing low at idx 6)
        1.1020, 1.1030, 1.1025, 1.1015, 1.1010,  # up then down (swing high at idx 11)
        1.1000, 1.0990, 1.0995, 1.1005, 1.1015,  # down then up (swing low at idx 16)
        1.1025, 1.1030, 1.1020, 1.1015, 1.1050,  # final breakout close above swing high
    ]
    for i, close in enumerate(prices):
        high = close + 0.0005
        low = close - 0.0005
        candles.append(FakeH1Candle(
            open=close - 0.0002,
            high=high,
            low=low,
            close=close,
            time=i * 3600,
        ))
    return candles


def _make_bearish_breakout_candles(n: int = 25) -> list[FakeH1Candle]:
    """
    Create candles with clear swing pivots, then a bearish breakout at the end.
    Final bar closes below last swing low.
    """
    candles = []
    prices = [
        # Swing high → swing low → swing high → swing low → breakdown
        1.1020, 1.1015, 1.1005, 1.1010, 1.1020,  # down then up (swing low at idx 2)
        1.1025, 1.1030, 1.1025, 1.1015, 1.1005,  # up then down (swing high at idx 6)
        1.0995, 1.0990, 1.0995, 1.1005, 1.1015,  # down then up (swing low at idx 7)
        1.1020, 1.1025, 1.1020, 1.1010, 1.1000,  # up then down (swing high at idx 16)
        1.0995, 1.0990, 1.0995, 1.1000, 1.0975,  # final breakdown close below swing low
    ]
    for i, close in enumerate(prices):
        high = close + 0.0005
        low = close - 0.0005
        candles.append(FakeH1Candle(
            open=close + 0.0002,
            high=high,
            low=low,
            close=close,
            time=i * 3600,
        ))
    return candles


# ─── TEST: _detect_bos FUNCTION ───────────────────────────────────────────────


class TestDetectBos:
    """Direct tests of the _detect_bos() function."""

    def test_bullish_bos_detected(self):
        """Close above last swing high → bullish BOS."""
        candles = _make_bullish_breakout_candles()
        bos, direction = _detect_bos(candles, 20)
        assert bos is True
        assert direction == "BULLISH"

    def test_bearish_bos_detected(self):
        """Close below last swing low → bearish BOS."""
        candles = _make_bearish_breakout_candles()
        bos, direction = _detect_bos(candles, 20)
        assert bos is True
        assert direction == "BEARISH"

    def test_no_bos_in_range(self):
        """Price stays within swing range → no BOS."""
        candles = _make_ranging_candles(25)
        bos, direction = _detect_bos(candles, 20)
        assert bos is False
        assert direction == ""

    def test_insufficient_data(self):
        """Less than lookback candles → no BOS, no crash."""
        candles = [FakeH1Candle(open=1.0, high=1.01, low=0.99, close=1.0, time=i * 3600) for i in range(5)]
        bos, direction = _detect_bos(candles, 20)
        assert bos is False
        assert direction == ""

    def test_no_swing_pivots_found(self):
        """Flat candles with no pivots → no BOS."""
        candles = [FakeH1Candle(open=1.1, high=1.1, low=1.1, close=1.1, time=i * 3600) for i in range(25)]
        bos, direction = _detect_bos(candles, 20)
        assert bos is False
        assert direction == ""


# ─── TEST: analyze_bias INCLUDES BOS FIELDS ───────────────────────────────────


class TestAnalyzeBiasWithBos:
    """BOS fields appear in BiasSnapshot from analyze_bias()."""

    def test_bullish_bos_in_snapshot(self):
        candles = _make_bullish_breakout_candles()
        result = analyze_bias(candles)
        assert isinstance(result, BiasSnapshot)
        assert result.bos_confirmed is True
        assert result.bos_direction == "BULLISH"

    def test_bearish_bos_in_snapshot(self):
        candles = _make_bearish_breakout_candles()
        result = analyze_bias(candles)
        assert isinstance(result, BiasSnapshot)
        assert result.bos_confirmed is True
        assert result.bos_direction == "BEARISH"

    def test_no_bos_in_ranging_snapshot(self):
        candles = _make_ranging_candles(30)
        result = analyze_bias(candles)
        assert isinstance(result, BiasSnapshot)
        assert result.bos_confirmed is False
        assert result.bos_direction == ""

    def test_insufficient_bars_no_bos(self):
        candles = [FakeH1Candle(open=1.0, high=1.01, low=0.99, close=1.0, time=i * 3600) for i in range(10)]
        result = analyze_bias(candles)
        assert result.bos_confirmed is False
        assert result.bos_direction == ""


# ─── TEST: MARKET CONTEXT PROPAGATION ─────────────────────────────────────────


class TestMarketContextPropagation:
    """BOS propagates through MarketContext builder."""

    def test_h1_bos_in_market_context(self):
        from core.market_context.builder import MarketContextBuilder

        # Create a mock HTFContext with bos_confirmed=True
        @dataclass
        class MockBias:
            direction: object = None
            confidence: float = 0.7
            bar_time: int = 0
            ema_position: float = 0.5
            swing_structure: str = "HH_HL"
            bos_confirmed: bool = True
            bos_direction: str = "BULLISH"

        class MockDir:
            value = "BULLISH"

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(bias=MockBias(direction=MockDir()))
        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1000.0)

        assert ctx.h1.bos_confirmed is True
        assert ctx.h1.bos_direction == "BULLISH"

    def test_h1_bos_in_to_dict(self):
        from core.market_context.models import MarketContext, H1Summary

        ctx = MarketContext(
            symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0,
            h1=H1Summary(bos_confirmed=True, bos_direction="BEARISH"),
        )
        d = ctx.to_dict()
        assert d["h1"]["bos_confirmed"] is True
        assert d["h1"]["bos_direction"] == "BEARISH"
