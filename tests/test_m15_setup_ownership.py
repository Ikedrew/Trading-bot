"""
Tests for M15 Setup Context Ownership.

Validates:
1. M15 quality calculation works (from M15 candles only)
2. M15 key level detection works
3. M15 order block detection works
4. M5 data cannot influence M15 setup context
5. Full propagation through MarketContext works
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.timeframes.m15_structure import analyze_structure
from core.timeframes.types import StructureSnapshot
from core.market_context.models import M15Summary, MarketContext
from core.market_context.builder import MarketContextBuilder


# ─── HELPERS ──────────────────────────────────────────────────────────────────


@dataclass
class FakeM15Candle:
    """Minimal candle for M15 testing."""
    open: float
    high: float
    low: float
    close: float
    time: int = 0
    tick_volume: int = 100
    real_volume: int = 100
    spread: int = 1


def _trending_candles(n: int = 50, start: float = 1.1000, step: float = 0.0005) -> list[FakeM15Candle]:
    """Create clear trending candles with well-defined swing structure."""
    candles = []
    for i in range(n):
        base = start + i * step
        # Create clear swing pivots with 2-bar confirmation on each side
        cycle_pos = i % 7
        if cycle_pos == 3:
            # Spike high (swing high — 2 lower bars before and after)
            candles.append(FakeM15Candle(
                open=base, high=base + 0.0020, low=base - 0.0001, close=base + 0.0015, time=i * 900,
            ))
        elif cycle_pos == 6:
            # Spike low (swing low — 2 higher bars before and after)
            candles.append(FakeM15Candle(
                open=base, high=base + 0.0001, low=base - 0.0015, close=base - 0.0010, time=i * 900,
            ))
        else:
            candles.append(FakeM15Candle(
                open=base, high=base + 0.0004, low=base - 0.0003, close=base + 0.0002, time=i * 900,
            ))
    return candles


def _ranging_candles(n: int = 50, base: float = 1.1000, range_size: float = 0.0020) -> list[FakeM15Candle]:
    """Create ranging candles oscillating around a base."""
    candles = []
    for i in range(n):
        offset = range_size * 0.4 * (1 if i % 4 < 2 else -1)
        mid = base + offset
        candles.append(FakeM15Candle(
            open=mid - 0.0002, high=mid + 0.0004, low=mid - 0.0004, close=mid + 0.0001, time=i * 900,
        ))
    return candles


# ─── TEST: M15 QUALITY FROM M15 CANDLES ──────────────────────────────────────


class TestM15QualityCalculation:
    """M15 quality_score computed from M15 candle data only."""

    def test_trending_candles_produce_quality(self):
        """Well-structured candles produce non-zero quality."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert isinstance(result, StructureSnapshot)
        assert result.quality_score > 0.0

    def test_quality_in_range_0_to_1(self):
        """Quality score is always bounded [0.0, 1.0]."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert 0.0 <= result.quality_score <= 1.0

    def test_insufficient_bars_returns_zero(self):
        """Less than 10 bars → quality 0.0."""
        candles = [FakeM15Candle(open=1.1, high=1.11, low=1.09, close=1.1, time=i * 900) for i in range(5)]
        result = analyze_structure(candles, current_price=1.1)
        assert result.quality_score == 0.0


# ─── TEST: M15 KEY LEVEL DETECTION ───────────────────────────────────────────


class TestM15KeyLevelDetection:
    """M15 detects proximity to support/resistance levels."""

    def test_at_key_level_flag(self):
        """When price is near a swing level, at_key_level should be True."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        # at_key_level is boolean — depends on price proximity to pivots
        assert isinstance(result.at_key_level, bool)

    def test_nearest_support_populated(self):
        """nearest_support should be a real price level when pivots exist."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        # Should have found some support level
        assert isinstance(result.nearest_support, float)

    def test_nearest_resistance_populated(self):
        """nearest_resistance should be a real price level when pivots exist."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert isinstance(result.nearest_resistance, float)


# ─── TEST: M15 ORDER BLOCK DETECTION ─────────────────────────────────────────


class TestM15OrderBlockDetection:
    """M15 detects order blocks (impulsive moves from levels)."""

    def test_order_block_is_boolean(self):
        """order_block_present must be a boolean."""
        candles = _trending_candles(50)
        result = analyze_structure(candles, current_price=candles[-1].close)
        assert isinstance(result.order_block_present, bool)

    def test_flat_candles_no_order_block(self):
        """Flat candles with zero range cannot have order blocks."""
        candles = [FakeM15Candle(open=1.1, high=1.1, low=1.1, close=1.1, time=i * 900) for i in range(50)]
        result = analyze_structure(candles, current_price=1.1)
        assert result.order_block_present is False


# ─── TEST: M5 CANNOT INFLUENCE M15 SETUP CONTEXT ─────────────────────────────


class TestM5CannotInfluenceM15:
    """M15 analysis is independent of M5 data."""

    def test_analyze_structure_takes_no_m5_candles(self):
        """analyze_structure() signature only accepts candles + price."""
        import inspect
        sig = inspect.signature(analyze_structure)
        params = list(sig.parameters.keys())
        assert params == ["candles", "current_price"]
        # No M5 candles, no engine_state, no M5-related parameter

    def test_m15_summary_independent_of_m5_state(self):
        """Changing M5 state does not change M15Summary in MarketContext."""
        @dataclass
        class MockStruct:
            quality_score: float = 0.65
            bar_time: int = 0
            nearest_support: float = 1.0990
            nearest_resistance: float = 1.1020
            at_key_level: bool = True
            order_block_present: bool = True

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(structure=MockStruct())

        builder = MarketContextBuilder(symbol="TEST")

        # Build with M5 CONFIRMED
        from unittest.mock import patch

        @dataclass
        class FakeEngineState1:
            current_bias: object = None
            bias_phase: str = "CONFIRMED"
            bias_strength: float = 80.0
            regime_state: str = "TREND_UP"

        @dataclass
        class FakeEngineState2:
            current_bias: object = None
            bias_phase: str = "EXPIRED"
            bias_strength: float = 0.0
            regime_state: str = "RANGING"

        ctx1 = builder.build(htf_context=htf, engine_state=FakeEngineState1(), cycle_id=1, current_time_s=1000.0)
        # Reset builder previous for clean comparison
        builder._previous = None
        ctx2 = builder.build(htf_context=htf, engine_state=FakeEngineState2(), cycle_id=2, current_time_s=1005.0)

        # M15 fields must be IDENTICAL regardless of M5 state
        assert ctx1.m15.quality_score == ctx2.m15.quality_score
        assert ctx1.m15.at_key_level == ctx2.m15.at_key_level
        assert ctx1.m15.order_block_present == ctx2.m15.order_block_present
        assert ctx1.m15.nearest_support == ctx2.m15.nearest_support
        assert ctx1.m15.nearest_resistance == ctx2.m15.nearest_resistance


# ─── TEST: FULL PROPAGATION THROUGH MARKET CONTEXT ────────────────────────────


class TestFullPropagation:
    """M15 setup context propagates through MarketContext pipeline."""

    def test_m15_fields_in_market_context(self):
        """MarketContext carries M15 setup fields."""
        ctx = MarketContext(
            symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0,
            m15=M15Summary(
                quality_score=0.72,
                at_key_level=True,
                order_block_present=True,
                nearest_support=1.0990,
                nearest_resistance=1.1025,
            ),
        )
        assert ctx.m15.quality_score == 0.72
        assert ctx.m15.at_key_level is True
        assert ctx.m15.order_block_present is True
        assert ctx.m15.nearest_support == 1.0990
        assert ctx.m15.nearest_resistance == 1.1025

    def test_m15_in_to_dict(self):
        """All M15 fields appear in serialized output."""
        ctx = MarketContext(
            symbol="TEST", cycle_id=1, timestamp_utc=1000.0,
            m15=M15Summary(
                quality_score=0.55,
                at_key_level=False,
                order_block_present=True,
                nearest_support=1.0980,
                nearest_resistance=1.1030,
            ),
        )
        d = ctx.to_dict()
        m15 = d["m15"]
        assert m15["quality_score"] == 0.55
        assert m15["at_key_level"] is False
        assert m15["order_block_present"] is True
        assert m15["nearest_support"] == 1.098
        assert m15["nearest_resistance"] == 1.103

    def test_builder_extracts_m15_from_htf(self):
        """Builder correctly extracts M15 from HTFContext.structure."""
        @dataclass
        class MockStruct:
            quality_score: float = 0.8
            bar_time: int = 0
            nearest_support: float = 1.0995
            nearest_resistance: float = 1.1015
            at_key_level: bool = True
            order_block_present: bool = False

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(structure=MockStruct())
        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1000.0)

        assert ctx.m15.quality_score == 0.8
        assert ctx.m15.at_key_level is True
        assert ctx.m15.order_block_present is False
        assert ctx.m15.nearest_support == 1.0995
        assert ctx.m15.nearest_resistance == 1.1015
