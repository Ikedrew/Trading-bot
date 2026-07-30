"""
Tests for V3 Opportunity Pipeline (schema, builder, observer).

Verifies:
    - Schema creation and serialization
    - Builder computes range positions and distances correctly
    - Builder handles missing context gracefully
    - Observer creates records without affecting decisions
    - Observer persists JSONL correctly
    - No forbidden imports (execution/risk/pipeline coupling)
    - Observer registered in ObserverRegistry
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_opportunity import V3Opportunity, _SCHEMA_VERSION
from core.v3_opportunity_builder import (
    build_v3_opportunity,
    persist_v3_opportunity,
    read_v3_opportunities,
    _range_position,
    _distance_pips,
)
from core.observers.v3_opportunity_observer import observe_v3_opportunity


# ═══════════════════════════════════════════════════════════════════════════════
# MOCKS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MockCandle:
    high: float = 1.086
    low: float = 1.084
    open: float = 1.085
    close: float = 1.0855
    time: int = 1753574400


@dataclass
class MockMarketContext:
    class _H4:
        swing_high = 1.090
        swing_low = 1.080
        regime = "RANGING"
    class _H1:
        swing_high = 1.088
        swing_low = 1.082
        bos_price = 1.086
        direction = "BULLISH"
    class _M15:
        swing_high = 1.087
        swing_low = 1.083
        nearest_support = 1.084
        nearest_resistance = 1.087
        quality_score = 0.7
    class _Regime:
        value = "RANGING"

    h4: Any = None
    h1: Any = None
    m15: Any = None
    regime: Any = None
    tradability_score: float = 0.65

    def __post_init__(self):
        self.h4 = self._H4()
        self.h1 = self._H1()
        self.m15 = self._M15()
        self.regime = self._Regime()


@dataclass
class MockCtx:
    symbol: str = "EURUSD"
    cycle_id: int = 100
    bar_time: float = 1753574400.0
    engine_result: dict = None
    engine_state: Any = None
    candles: list = None
    closed_i: int = 60
    bid: float = 1.08500
    ask: float = 1.08510
    config: Any = None
    detected_patterns: list = None
    risk_manager: Any = None
    htf_context: Any = None
    runtime_session_id: str = "test"
    decision_funnel: Any = None
    market_context: Any = None

    def __post_init__(self):
        if self.engine_result is None:
            self.engine_result = {
                "action": "NO_TRADE",
                "entity_id": "EURUSD_1753574400",
            }
        if self.candles is None:
            self.candles = [MockCandle()] * 65


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Schema:
    """V3Opportunity schema correctness."""

    def test_frozen_dataclass(self):
        """V3Opportunity is immutable."""
        opp = V3Opportunity(opportunity_id="test_1")
        with pytest.raises(Exception):
            opp.symbol = "CHANGED"

    def test_to_dict(self):
        """Serialization includes all key fields."""
        opp = V3Opportunity(
            opportunity_id="v3_test",
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            price_at_observation=1.085,
            h4_range_position=0.5,
            nearest_support_price=1.084,
        )
        d = opp.to_dict()
        assert d["schema_version"] == _SCHEMA_VERSION
        assert d["symbol"] == "EURUSD"
        assert d["price_at_observation"] == 1.085
        assert d["h4_range_position"] == 0.5
        assert d["nearest_support_price"] == 1.084

    def test_from_dict_roundtrip(self):
        """from_dict(to_dict()) produces equivalent object."""
        opp = V3Opportunity(
            opportunity_id="v3_rt",
            symbol="GBPUSD",
            timestamp_utc=100.0,
            h4_swing_high=1.30,
            h4_swing_low=1.28,
            equal_highs_above=True,
            equal_highs_count=3,
        )
        d = opp.to_dict()
        restored = V3Opportunity.from_dict(d)
        assert restored.opportunity_id == opp.opportunity_id
        assert restored.h4_swing_high == opp.h4_swing_high
        assert restored.equal_highs_above is True
        assert restored.equal_highs_count == 3

    def test_schema_version(self):
        """Schema version is correct."""
        opp = V3Opportunity(opportunity_id="test")
        assert opp.schema_version == "v3_opportunity_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Builder:
    """V3OpportunityBuilder correctness."""

    def test_basic_creation(self):
        """Builder with minimal params produces valid V3."""
        opp = build_v3_opportunity(symbol="EURUSD", timestamp_utc=1753574400.0)
        assert isinstance(opp, V3Opportunity)
        assert opp.symbol == "EURUSD"
        assert opp.opportunity_id.startswith("v3_EURUSD_")

    def test_range_position_mid(self):
        """Price in middle of range gives ~0.5."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1.0,
            price=1.085,
            market_context=MockMarketContext(),
        )
        # H4: low=1.080, high=1.090, price=1.085 → 0.5
        assert opp.h4_range_position == pytest.approx(0.5, abs=0.01)

    def test_range_position_extremes(self):
        """Price at low gives 0, at high gives 1."""
        assert _range_position(1.080, 1.080, 1.090) == 0.0
        assert _range_position(1.090, 1.080, 1.090) == 1.0
        assert _range_position(1.070, 1.080, 1.090) == 0.0  # below range
        assert _range_position(1.100, 1.080, 1.090) == 1.0  # above range

    def test_distance_pips(self):
        """Distance computed correctly."""
        # 1.0850 to 1.0840 = 10 pips for EURUSD
        assert _distance_pips(1.0850, 1.0840, 0.0001) == pytest.approx(10.0, abs=0.1)
        # 0 values return 0
        assert _distance_pips(0, 1.0840, 0.0001) == 0.0
        assert _distance_pips(1.085, 0, 0.0001) == 0.0

    def test_with_market_context(self):
        """Builder extracts swing levels from MarketContext."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1.0,
            price=1.085, bid=1.085, ask=1.0851,
            market_context=MockMarketContext(),
        )
        assert opp.h4_swing_high == 1.090
        assert opp.h4_swing_low == 1.080
        assert opp.h1_swing_high == 1.088
        assert opp.h1_swing_low == 1.082
        assert opp.nearest_support_price == 1.084
        assert opp.nearest_resistance_price == 1.087
        assert opp.nearest_support_timeframe == "M15"

    def test_spread_computed(self):
        """Spread from bid/ask."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1.0,
            bid=1.08500, ask=1.08510,
        )
        assert opp.spread == pytest.approx(0.0001, abs=0.00001)

    def test_rejection_candle_detection(self):
        """Detects rejection candle from candle data."""
        # Long lower wick candle (hammer)
        candles = [MockCandle(high=1.086, low=1.083, open=1.0858, close=1.0855)] * 65
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1.0,
            price=1.085, atr=0.001,
            candles=candles, closed_index=60,
        )
        # Wick = min(open,close) - low = 1.0855 - 1.083 = 0.0025
        # Body = |close-open| = 0.0003
        # Wick > body * 1.5 → rejection
        assert opp.rejection_candle_present is True

    def test_no_market_context(self):
        """Works without MarketContext — all zeros."""
        opp = build_v3_opportunity(
            symbol="USDJPY", timestamp_utc=1.0,
            market_context=None,
        )
        assert opp.h4_swing_high == 0.0
        assert opp.h1_swing_low == 0.0
        assert opp.nearest_support_price == 0.0

    def test_correlation_id_propagation(self):
        """Correlation ID passes through."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1.0,
            correlation_id="COR-999",
        )
        assert opp.correlation_id == "COR-999"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Persistence:
    """V3 JSONL persistence."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v3_opportunity_builder as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persist_creates_file(self):
        """persist_v3_opportunity writes JSONL file."""
        opp = build_v3_opportunity(symbol="EURUSD", timestamp_utc=1753574400.0)
        result = persist_v3_opportunity(opp)
        assert result is True
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1

    def test_persist_valid_json(self):
        """Persisted record is valid JSON."""
        opp = build_v3_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            price=1.085, bid=1.085, ask=1.0851)
        persist_v3_opportunity(opp)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        with open(files[0]) as f:
            record = json.loads(f.readline())
        assert record["schema_version"] == "v3_opportunity_v1"
        assert record["symbol"] == "EURUSD"

    def test_read_back(self):
        """read_v3_opportunities returns persisted records."""
        opp = build_v3_opportunity(symbol="GBPUSD", timestamp_utc=1753574400.0)
        persist_v3_opportunity(opp)
        records = read_v3_opportunities(symbol="GBPUSD")
        assert len(records) == 1
        assert records[0]["symbol"] == "GBPUSD"


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Observer:
    """V3 Observer integration."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v3_opportunity_builder as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_record(self):
        """Observer creates a persisted V3Opportunity."""
        ctx = MockCtx(market_context=MockMarketContext())
        observe_v3_opportunity(ctx)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(open(files[0]).readline())
        assert record["schema_version"] == "v3_opportunity_v1"

    def test_does_not_modify_engine_result(self):
        """Observer does not mutate engine_result."""
        ctx = MockCtx()
        original = dict(ctx.engine_result)
        observe_v3_opportunity(ctx)
        assert ctx.engine_result == original

    def test_returns_none(self):
        """Observer returns None (no value consumed)."""
        ctx = MockCtx()
        result = observe_v3_opportunity(ctx)
        assert result is None

    def test_handles_missing_context(self):
        """Works without MarketContext."""
        ctx = MockCtx(market_context=None, htf_context=None)
        observe_v3_opportunity(ctx)  # Should not raise

    def test_handles_no_candles(self):
        """Works without candle data."""
        ctx = MockCtx(candles=None)
        observe_v3_opportunity(ctx)  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION TEST
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Registration:
    """Observer registered in ObserverRegistry."""

    def test_registry_has_v3(self):
        """ObserverRegistry dispatches observer #9."""
        import inspect
        from core.pipeline.observers import ObserverRegistry
        source = inspect.getsource(ObserverRegistry.notify_all)
        assert "v3_opportunity_observer" in source

    def test_observer_context_fields(self):
        """ObserverContext still has all required fields for V3."""
        from core.pipeline.observers import ObserverContext
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ObserverContext)}
        required = {"symbol", "bar_time", "engine_result", "candles",
                    "bid", "ask", "htf_context", "market_context", "closed_i"}
        assert required.issubset(fields)


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestV3Safety:
    """No forbidden imports."""

    def test_schema_no_forbidden(self):
        import inspect
        import core.v3_opportunity as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.pipeline", "from core.runtime"]:
            assert f not in source

    def test_builder_no_forbidden(self):
        import inspect
        import core.v3_opportunity_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.pipeline", "from core.runtime"]:
            assert f not in source

    def test_observer_no_forbidden(self):
        import inspect
        import core.observers.v3_opportunity_observer as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5"]:
            assert f not in source
