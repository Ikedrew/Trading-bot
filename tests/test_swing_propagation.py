"""
Tests for swing level propagation through MarketContext → V3 pipeline.

Verifies:
    A) Swing levels propagate from BiasSnapshot/StructureSnapshot to MarketContext
    B) Range position computed correctly
    C) Missing swing data handled gracefully
    D) Correct timeframe authority used
"""

from dataclasses import dataclass
from typing import Any

import pytest

from core.market_context.models import H1Summary, M15Summary, MarketContext
from core.market_context.builder import MarketContextBuilder
from core.v3_opportunity_builder import build_v3_opportunity, _range_position, _distance_pips


# ═══════════════════════════════════════════════════════════════════════════════
# MOCKS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MockBiasSnapshot:
    """Simulates BiasSnapshot from h1_bias.py analyzer."""
    direction: Any = None
    confidence: float = 0.7
    swing_structure: str = "HH_HL"
    ema_position: float = 0.5
    bos_confirmed: bool = True
    bos_direction: str = "BULLISH"
    last_swing_high: float = 1.0880
    last_swing_low: float = 1.0820
    bar_time: int = 1753574400

    def __post_init__(self):
        if self.direction is None:
            self.direction = _MockDirection("BULLISH")


@dataclass
class _MockDirection:
    value: str


@dataclass
class MockStructureSnapshot:
    """Simulates StructureSnapshot from m15_structure.py analyzer."""
    quality_score: float = 0.7
    bar_time: int = 1753574400
    nearest_support: float = 1.0840
    nearest_resistance: float = 1.0870
    at_key_level: bool = True
    order_block_present: bool = False


@dataclass
class MockRegimeSnapshot:
    """Simulates RegimeSnapshot from h4_regime.py analyzer."""
    classification: Any = None
    confidence: float = 0.6
    trend_bias: str = "NEUTRAL"
    trend_strength: float = 0.3
    atr_ratio: float = 1.0

    def __post_init__(self):
        if self.classification is None:
            self.classification = _MockDirection("RANGING")


@dataclass
class MockHTFContext:
    """Simulates HTFContext from TimeframeCache."""
    regime: Any = None
    bias: Any = None
    structure: Any = None


@dataclass
class MockMarketContext:
    """MarketContext-like object with swing fields for V3 builder testing."""
    class _H4:
        swing_high = 0.0
        swing_low = 0.0
    class _H1:
        swing_high = 1.0880
        swing_low = 1.0820
        direction = "BULLISH"
        bos_confirmed = True
        bos_direction = "BULLISH"
    class _M15:
        swing_high = 1.0870
        swing_low = 1.0840
        nearest_support = 1.0840
        nearest_resistance = 1.0870
        quality_score = 0.7
        at_key_level = True
        order_block_present = False
    class _Regime:
        value = "RANGING"

    h4: Any = None
    h1: Any = None
    m15: Any = None
    regime: Any = None
    tradability_score: float = 0.6

    def __post_init__(self):
        self.h4 = self._H4()
        self.h1 = self._H1()
        self.m15 = self._M15()
        self.regime = self._Regime()


# ═══════════════════════════════════════════════════════════════════════════════
# A) SWING PROPAGATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSwingPropagation:
    """Swing levels propagate from snapshots through MarketContext."""

    def test_h1_swing_levels_in_summary(self):
        """H1Summary receives swing_high/swing_low from BiasSnapshot."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(
            regime=MockRegimeSnapshot(),
            bias=MockBiasSnapshot(last_swing_high=1.0880, last_swing_low=1.0820),
            structure=MockStructureSnapshot(),
        )
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        assert ctx.h1.swing_high == 1.0880
        assert ctx.h1.swing_low == 1.0820

    def test_m15_swing_levels_in_summary(self):
        """M15Summary receives swing_high/swing_low from StructureSnapshot."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(
            regime=MockRegimeSnapshot(),
            bias=MockBiasSnapshot(),
            structure=MockStructureSnapshot(nearest_resistance=1.0870, nearest_support=1.0840),
        )
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        assert ctx.m15.swing_high == 1.0870
        assert ctx.m15.swing_low == 1.0840

    def test_swing_levels_in_to_dict(self):
        """MarketContext.to_dict() includes swing_high/swing_low."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(
            regime=MockRegimeSnapshot(),
            bias=MockBiasSnapshot(last_swing_high=1.0900, last_swing_low=1.0800),
            structure=MockStructureSnapshot(nearest_resistance=1.0870, nearest_support=1.0830),
        )
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        d = ctx.to_dict()
        assert d["h1"]["swing_high"] == pytest.approx(1.0900, abs=1e-6)
        assert d["h1"]["swing_low"] == pytest.approx(1.0800, abs=1e-6)
        assert d["m15"]["swing_high"] == pytest.approx(1.0870, abs=1e-6)
        assert d["m15"]["swing_low"] == pytest.approx(1.0830, abs=1e-6)

    def test_v3_receives_h1_swings(self):
        """V3 builder reads swing levels from MarketContext."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0850,
            market_context=MockMarketContext(),
        )
        assert opp.h1_swing_high == 1.0880
        assert opp.h1_swing_low == 1.0820

    def test_v3_receives_m15_swings(self):
        """V3 builder reads M15 swing levels."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0850,
            market_context=MockMarketContext(),
        )
        assert opp.m15_swing_high == 1.0870
        assert opp.m15_swing_low == 1.0840


# ═══════════════════════════════════════════════════════════════════════════════
# B) RANGE POSITION CALCULATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRangePosition:
    """Range position computed correctly from swing levels."""

    def test_midpoint(self):
        """Price at midpoint gives 0.5."""
        # swing_low=1.0900, swing_high=1.1000, price=1.0950
        assert _range_position(1.0950, 1.0900, 1.1000) == pytest.approx(0.5, abs=0.001)

    def test_bottom(self):
        """Price at swing_low gives 0.0."""
        assert _range_position(1.0900, 1.0900, 1.1000) == 0.0

    def test_top(self):
        """Price at swing_high gives 1.0."""
        assert _range_position(1.1000, 1.0900, 1.1000) == 1.0

    def test_below_range(self):
        """Price below swing_low gives 0.0."""
        assert _range_position(1.0800, 1.0900, 1.1000) == 0.0

    def test_above_range(self):
        """Price above swing_high gives 1.0."""
        assert _range_position(1.1100, 1.0900, 1.1000) == 1.0

    def test_premium_zone(self):
        """Price at 75% gives 0.75 (premium)."""
        # Range 100 pips, price 75 pips from low
        assert _range_position(1.0975, 1.0900, 1.1000) == pytest.approx(0.75, abs=0.001)

    def test_discount_zone(self):
        """Price at 25% gives 0.25 (discount)."""
        assert _range_position(1.0925, 1.0900, 1.1000) == pytest.approx(0.25, abs=0.001)

    def test_v3_range_position_populated(self):
        """V3 builder computes non-zero h1_range_position when swings available."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0850,  # Between h1_swing_low=1.0820 and h1_swing_high=1.0880
            market_context=MockMarketContext(),
        )
        # (1.0850 - 1.0820) / (1.0880 - 1.0820) = 0.003 / 0.006 = 0.5
        assert opp.h1_range_position == pytest.approx(0.5, abs=0.01)

    def test_v3_m15_range_position(self):
        """M15 range position computed from M15 swings."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0855,  # Between m15 low=1.0840 and high=1.0870
            market_context=MockMarketContext(),
        )
        # (1.0855 - 1.0840) / (1.0870 - 1.0840) = 0.0015 / 0.003 = 0.5
        assert opp.m15_range_position == pytest.approx(0.5, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# C) MISSING SWING DATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingSwingData:
    """Graceful handling when swing levels unavailable."""

    def test_no_htf_context(self):
        """Builder works without HTFContext — returns neutral."""
        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=None, cycle_id=1, current_time_s=1.0)
        assert ctx.h1.swing_high == 0.0
        assert ctx.h1.swing_low == 0.0
        assert ctx.m15.swing_high == 0.0
        assert ctx.m15.swing_low == 0.0

    def test_no_bias_snapshot(self):
        """H1 defaults when BiasSnapshot is None."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(regime=MockRegimeSnapshot(), bias=None, structure=None)
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        assert ctx.h1.swing_high == 0.0
        assert ctx.h1.swing_low == 0.0

    def test_bias_snapshot_without_swing_fields(self):
        """Handles BiasSnapshot that doesn't have swing fields (legacy)."""
        class LegacyBias:
            direction = _MockDirection("NEUTRAL")
            confidence = 0.5
            swing_structure = "MIXED"
            ema_position = 0.0
            bos_confirmed = False
            bos_direction = ""
            # Note: no last_swing_high/last_swing_low

        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(regime=MockRegimeSnapshot(), bias=LegacyBias(), structure=None)
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        assert ctx.h1.swing_high == 0.0  # getattr default
        assert ctx.h1.swing_low == 0.0

    def test_v3_zero_swings_zero_position(self):
        """V3 range_position is 0 when swings are zero (no crash)."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0850,
            market_context=None,
        )
        assert opp.h1_range_position == 0.0
        assert opp.m15_range_position == 0.0
        assert opp.h4_range_position == 0.0

    def test_equal_swings_zero_position(self):
        """Range position is 0.0 when high == low (zero-width range)."""
        assert _range_position(1.085, 1.085, 1.085) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# D) AUTHORITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSwingAuthority:
    """Correct timeframe used for swing levels."""

    def test_h1_authority_for_h1_swings(self):
        """H1 swing levels come from H1 BiasSnapshot, not M15."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(
            regime=MockRegimeSnapshot(),
            bias=MockBiasSnapshot(last_swing_high=1.0900, last_swing_low=1.0800),
            structure=MockStructureSnapshot(nearest_resistance=1.0870, nearest_support=1.0840),
        )
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        # H1 should use BiasSnapshot values (wider range)
        assert ctx.h1.swing_high == 1.0900
        assert ctx.h1.swing_low == 1.0800
        # M15 should use StructureSnapshot values (tighter range)
        assert ctx.m15.swing_high == 1.0870
        assert ctx.m15.swing_low == 1.0840

    def test_m15_uses_structure_authority(self):
        """M15 swing levels come from StructureSnapshot nearest S/R."""
        builder = MarketContextBuilder(symbol="EURUSD")
        htf = MockHTFContext(
            regime=MockRegimeSnapshot(),
            bias=MockBiasSnapshot(),
            structure=MockStructureSnapshot(nearest_resistance=1.0950, nearest_support=1.0750),
        )
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1.0)
        assert ctx.m15.swing_high == 1.0950
        assert ctx.m15.swing_low == 1.0750

    def test_v3_distance_pips_h1(self):
        """V3 computes correct distance from H1 swing levels."""
        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price=1.0850,
            market_context=MockMarketContext(),  # h1.swing_high=1.0880
        )
        # Distance to H1 high: |1.0850 - 1.0880| / 0.0001 = 30 pips
        assert opp.h1_distance_from_high_pips == pytest.approx(30.0, abs=0.1)
        # Distance to H1 low: |1.0850 - 1.0820| / 0.0001 = 30 pips
        assert opp.h1_distance_from_low_pips == pytest.approx(30.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# E) DISTANCE UTILITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDistancePips:
    """Pip distance calculations."""

    def test_eurusd_10_pips(self):
        """10 pip distance for EURUSD."""
        assert _distance_pips(1.0850, 1.0840, 0.0001) == pytest.approx(10.0, abs=0.1)

    def test_usdjpy_10_pips(self):
        """10 pip distance for USDJPY (pip_size=0.01)."""
        assert _distance_pips(150.50, 150.40, 0.01) == pytest.approx(10.0, abs=0.1)

    def test_zero_price(self):
        """Zero values return 0."""
        assert _distance_pips(0, 1.085, 0.0001) == 0.0
        assert _distance_pips(1.085, 0, 0.0001) == 0.0
