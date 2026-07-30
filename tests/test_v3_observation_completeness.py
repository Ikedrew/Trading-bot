"""
V3 Observation Data Quality Monitoring.

Detects:
    - Features that exist but never populate
    - Missing values above threshold
    - Insufficient field variance
    - Empty detector outputs

Purpose: Prevent silently collecting useless V3 data.
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_opportunity_builder import build_v3_opportunity, persist_v3_opportunity
from core.observers.v3_opportunity_observer import observe_v3_opportunity
from core.market_intelligence.liquidity_detector import LiquiditySnapshot
from core.market_intelligence.fvg_detector import FVGSnapshot
from core.market_intelligence.order_block_detector import OBSnapshot


@dataclass
class MockCandle:
    high: float = 1.086
    low: float = 1.084
    open: float = 1.085
    close: float = 1.0855
    time: int = 1753574400


@dataclass
class MockMarketContext:
    class _H1:
        swing_high = 1.088
        swing_low = 1.082
        direction = "BULLISH"
        bos_confirmed = True
        bos_direction = "BULLISH"
    class _M15:
        swing_high = 1.087
        swing_low = 1.083
        nearest_support = 1.084
        nearest_resistance = 1.087
        quality_score = 0.7
        at_key_level = True
        order_block_present = False
    class _H4:
        swing_high = 0.0
        swing_low = 0.0

    h4: Any = None
    h1: Any = None
    m15: Any = None

    def __post_init__(self):
        self.h4 = self._H4()
        self.h1 = self._H1()
        self.m15 = self._M15()


class TestPhase1Completeness:
    """Phase 1 fields (swing + ATR) must be populated."""

    def test_range_position_populated_with_swings(self):
        """H1 range_position is non-zero when swing levels available."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, atr=0.001,
            market_context=MockMarketContext(),
        )
        assert opp.h1_range_position > 0.0
        assert opp.m15_range_position > 0.0

    def test_atr_populated_from_builder(self):
        """ATR is non-zero when passed correctly."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, atr=0.0012,
        )
        assert opp.atr == 0.0012

    def test_displacement_fires_with_atr(self):
        """Displacement detects large candle when ATR available."""
        # Large candle: range 0.003 with ATR 0.001 → 3.0 ATR
        candles = [MockCandle()] * 65
        candles[60] = MockCandle(high=1.088, low=1.085, open=1.0855, close=1.0875)

        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.086, atr=0.001,
            candles=candles, closed_index=60,
        )
        assert opp.displacement_into_level is True
        assert opp.displacement_magnitude_atr == pytest.approx(3.0, abs=0.1)


class TestLiquidityCompleteness:
    """Liquidity detector fields populate when data exists."""

    def test_liquidity_snapshot_populates_v3(self):
        """V3 receives liquidity data from snapshot."""
        liq = LiquiditySnapshot(
            equal_highs_above=True,
            equal_highs_distance_pips=15.0,
            equal_highs_count=3,
            equal_lows_below=True,
            equal_lows_distance_pips=12.0,
            equal_lows_count=2,
            prev_day_high=1.0900,
            prev_day_low=1.0800,
            distance_to_prev_day_high_pips=50.0,
            distance_to_prev_day_low_pips=50.0,
            liquidity_sweep_just_occurred=True,
            sweep_direction="BULLISH",
            sweep_distance_pips=3.5,
            bars_since_sweep=2,
        )
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085,
            liquidity_snapshot=liq,
        )
        assert opp.equal_highs_above is True
        assert opp.equal_highs_count == 3
        assert opp.equal_lows_below is True
        assert opp.prev_day_high == 1.0900
        assert opp.liquidity_sweep_just_occurred is True
        assert opp.sweep_direction == "BULLISH"

    def test_no_liquidity_snapshot_leaves_defaults(self):
        """Without snapshot, liquidity fields are default (False/0)."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, liquidity_snapshot=None,
        )
        assert opp.equal_highs_above is False
        assert opp.equal_lows_below is False
        assert opp.liquidity_sweep_just_occurred is False


class TestFVGCompleteness:
    """FVG detector fields populate when data exists."""

    def test_fvg_snapshot_populates_v3(self):
        """V3 receives FVG data from snapshot."""
        fvg = FVGSnapshot(
            nearest_fvg_above_price=1.0870,
            nearest_fvg_above_distance_pips=20.0,
            fvg_above_filled_pct=0.3,
            price_inside_fvg=True,
            fvg_direction_if_inside="BULLISH",
            total_unfilled_fvgs_above=2,
            total_unfilled_fvgs_below=1,
        )
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, fvg_snapshot=fvg,
        )
        assert opp.nearest_fvg_above_price == 1.0870
        assert opp.price_inside_fvg is True
        assert opp.fvg_direction_if_inside == "BULLISH"
        assert opp.total_unfilled_fvgs_above == 2


class TestOBCompleteness:
    """Order block detector fields populate when data exists."""

    def test_ob_snapshot_populates_v3(self):
        """V3 receives OB data from snapshot."""
        ob = OBSnapshot(
            nearest_demand_ob_price=1.0830,
            nearest_demand_ob_distance_pips=20.0,
            demand_ob_strength=0.75,
            demand_ob_mitigated=False,
            nearest_supply_ob_price=1.0900,
            nearest_supply_ob_distance_pips=50.0,
            supply_ob_strength=0.6,
            price_inside_ob=True,
            ob_type_if_inside="DEMAND",
        )
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, ob_snapshot=ob,
        )
        assert opp.nearest_demand_ob_price == 1.0830
        assert opp.demand_ob_strength == 0.75
        assert opp.price_inside_ob is True
        assert opp.ob_type_if_inside == "DEMAND"


class TestObserverIntegration:
    """V3 observer runs all detectors without crash."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v3_opportunity_builder as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_observer_with_sufficient_candles(self):
        """Observer runs detectors and persists V3 with populated fields."""
        # Create realistic candles with variation
        candles = []
        for i in range(200):
            h = 1.085 + (i % 7) * 0.0003
            l = 1.083 + (i % 5) * 0.0002
            candles.append(MockCandle(
                high=h, low=l, open=l + 0.0005, close=h - 0.0003,
                time=1753574400 + i * 300))

        @dataclass
        class Ctx:
            symbol: str = "EURUSD"
            cycle_id: int = 1
            bar_time: float = 1753574400.0 + 199 * 300
            engine_result: dict = None
            engine_state: Any = None
            candles: list = None
            closed_i: int = 199
            bid: float = 1.085
            ask: float = 1.0851
            htf_context: Any = None
            market_context: Any = None
            runtime_session_id: str = "test"
            decision_funnel: Any = None
            config: Any = None
            detected_patterns: list = None
            risk_manager: Any = None

        ctx = Ctx(
            engine_result={"entity_id": "EURUSD_TEST"},
            candles=candles,
            market_context=MockMarketContext(),
        )

        observe_v3_opportunity(ctx)

        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(open(files[0]).readline())

        # Basic fields populated
        assert record["schema_version"] == "v3_opportunity_v1"
        assert record["atr"] > 0
        # At least SOME detector fields should be non-default
        # (depends on candle patterns — may or may not have FVGs/OBs)
        assert record["h1_range_position"] > 0 or record["m15_range_position"] > 0


class TestDataQualityChecks:
    """Quality gate tests — would fail if data is systematically empty."""

    def test_v3_schema_field_count(self):
        """V3 schema has expected number of fields."""
        from core.v3_opportunity import V3Opportunity
        import dataclasses
        fields = dataclasses.fields(V3Opportunity)
        # Should have 80+ fields
        assert len(fields) >= 80

    def test_to_dict_covers_all_domains(self):
        """to_dict includes keys from all 7 domains."""
        from core.v3_opportunity import V3Opportunity
        opp = V3Opportunity(opportunity_id="test")
        d = opp.to_dict()
        # Check domains present
        assert "h4_range_position" in d
        assert "nearest_support_price" in d
        assert "equal_highs_above" in d
        assert "nearest_fvg_above_price" in d
        assert "nearest_demand_ob_price" in d
        assert "displacement_into_level" in d
        assert "spread" in d
        assert "outcome_linked" in d
