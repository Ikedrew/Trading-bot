"""
Tests for V3 MarketUnderstanding Engine.

Verifies:
    - Model immutability and serialization
    - Per-timeframe builders produce correct outputs
    - Orchestrator combines all layers
    - Observer integrates without affecting decisions
    - Missing data handled gracefully
    - No execution coupling
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_shadow.models import (
    MarketUnderstanding,
    H4Understanding,
    H1Understanding,
    M15Understanding,
    M5Understanding,
    M1Understanding,
    _SCHEMA_VERSION,
)
from core.v3_shadow.builders import (
    build_h4_understanding,
    build_h1_understanding,
    build_m15_understanding,
    build_m5_understanding,
    build_m1_understanding,
    build_market_understanding,
)
from core.v3_shadow.observer import observe_market_understanding


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
class MockH4:
    regime: str = "RANGING"
    trend_bias: str = "NEUTRAL"
    trend_strength: float = 0.3
    atr_ratio: float = 1.0
    swing_high: float = 0.0
    swing_low: float = 0.0


@dataclass
class MockH1:
    direction: str = "BULLISH"
    confidence: float = 0.7
    swing_structure: str = "HH_HL"
    bos_confirmed: bool = True
    bos_direction: str = "BULLISH"
    swing_high: float = 1.088
    swing_low: float = 1.082


@dataclass
class MockM15:
    swing_high: float = 1.087
    swing_low: float = 1.083
    nearest_support: float = 1.084
    nearest_resistance: float = 1.087
    quality_score: float = 0.7
    at_key_level: bool = True
    order_block_present: bool = False


@dataclass
class MockMarketContext:
    h4: Any = None
    h1: Any = None
    m15: Any = None
    phase: Any = None

    def __post_init__(self):
        if self.h4 is None:
            self.h4 = MockH4()
        if self.h1 is None:
            self.h1 = MockH1()
        if self.m15 is None:
            self.m15 = MockM15()


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
            self.engine_result = {"entity_id": "EURUSD_1753574400"}
        if self.candles is None:
            self.candles = [MockCandle(
                high=1.085 + (i % 5) * 0.0003,
                low=1.083 + (i % 3) * 0.0002,
                open=1.084 + 0.0005,
                close=1.084 + 0.0008,
                time=1753574400 + i * 300,
            ) for i in range(65)]


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """MarketUnderstanding model correctness."""

    def test_frozen(self):
        """Models are immutable."""
        mu = MarketUnderstanding(symbol="EURUSD")
        with pytest.raises(Exception):
            mu.symbol = "CHANGED"

    def test_to_dict(self):
        """Serialization includes all layers."""
        mu = MarketUnderstanding(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            confidence=0.8,
        )
        d = mu.to_dict()
        assert d["schema_version"] == _SCHEMA_VERSION
        assert d["symbol"] == "EURUSD"
        assert d["confidence"] == 0.8
        assert "h4" in d
        assert "h1" in d
        assert "m15" in d
        assert "m5" in d
        assert "m1" in d

    def test_defaults(self):
        """Default values are neutral/empty."""
        mu = MarketUnderstanding()
        assert mu.h4.trend == ""
        assert mu.h1.bos_confirmed is False
        assert mu.m15.range_position == 0.0
        assert mu.m5.atr == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# H4 BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestH4Builder:
    """H4 understanding builder."""

    def test_from_market_context(self):
        """Extracts H4 trend and volatility from MarketContext."""
        ctx = MockMarketContext()
        h4 = build_h4_understanding(market_context=ctx)
        assert h4.trend == "NEUTRAL"  # RANGING → NEUTRAL
        assert h4.volatility_state == "NEUTRAL"  # atr_ratio=1.0

    def test_trending_regime(self):
        """Trending regime produces directional trend."""
        ctx = MockMarketContext(h4=MockH4(regime="TRENDING_BULLISH", trend_bias="BULLISH", trend_strength=0.8))
        h4 = build_h4_understanding(market_context=ctx)
        assert h4.trend == "BULLISH"
        assert h4.trend_strength == 0.8

    def test_expansion_volatility(self):
        """High ATR ratio = EXPANSION."""
        ctx = MockMarketContext(h4=MockH4(atr_ratio=1.5))
        h4 = build_h4_understanding(market_context=ctx)
        assert h4.volatility_state == "EXPANSION"

    def test_no_data(self):
        """Returns empty H4 without context."""
        h4 = build_h4_understanding()
        assert h4.trend == ""
        assert h4.volatility_state == ""


# ═══════════════════════════════════════════════════════════════════════════════
# H1 BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestH1Builder:
    """H1 understanding builder."""

    def test_from_market_context(self):
        """Extracts H1 BOS and swings."""
        ctx = MockMarketContext()
        h1 = build_h1_understanding(market_context=ctx)
        assert h1.bos_confirmed is True
        assert h1.bos_direction == "BULLISH"
        assert h1.dominant_trend == "BULLISH"
        assert h1.swing_high == 1.088
        assert h1.swing_low == 1.082

    def test_no_data(self):
        """Returns empty H1 without context."""
        h1 = build_h1_understanding()
        assert h1.bos_confirmed is False
        assert h1.dominant_trend == ""


# ═══════════════════════════════════════════════════════════════════════════════
# M15 BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestM15Builder:
    """M15 understanding builder."""

    def test_range_position(self):
        """Computes range position from swing levels."""
        ctx = MockMarketContext()
        m15 = build_m15_understanding(
            market_context=ctx, current_price=1.085, atr=0.001)
        # (1.085 - 1.083) / (1.087 - 1.083) = 0.5
        assert m15.range_position == pytest.approx(0.5, abs=0.01)
        assert m15.swing_high == 1.087
        assert m15.swing_low == 1.083

    def test_no_data(self):
        """Returns empty M15 without context."""
        m15 = build_m15_understanding()
        assert m15.range_position == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# M5 BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestM5Builder:
    """M5 understanding builder."""

    def test_computes_atr(self):
        """ATR computed from candle data."""
        candles = [MockCandle(high=1.086, low=1.084)] * 20
        m5 = build_m5_understanding(candles=candles, bid=1.085, ask=1.0851)
        assert m5.atr == pytest.approx(0.002, abs=0.0001)

    def test_spread(self):
        """Spread computed from bid/ask."""
        m5 = build_m5_understanding(bid=1.085, ask=1.0851, candles=[MockCandle()] * 20)
        assert m5.spread == pytest.approx(0.0001, abs=0.00001)

    def test_rejection_detection(self):
        """Detects rejection candle."""
        # Hammer: big lower wick
        candles = [MockCandle(high=1.086, low=1.084)] * 19
        candles.append(MockCandle(high=1.086, low=1.082, open=1.0858, close=1.0855))
        m5 = build_m5_understanding(candles=candles, bid=1.085, ask=1.0851)
        assert m5.rejection_present is True
        assert m5.rejection_direction == "BULLISH"

    def test_no_data(self):
        """Returns empty M5 without candles."""
        m5 = build_m5_understanding()
        assert m5.atr == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# M1 BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestM1Builder:
    """M1 understanding builder."""

    def test_basic(self):
        """Produces M1 with spread and micro range."""
        candles = [MockCandle(high=1.0860 + i * 0.0001, low=1.0840 + i * 0.0001,
                              open=1.0845, close=1.0855) for i in range(10)]
        m1 = build_m1_understanding(candles=candles, bid=1.085, ask=1.0851)
        assert m1.spread_at_observation == pytest.approx(0.0001, abs=0.00001)
        assert m1.micro_range_pips > 0

    def test_no_data(self):
        """Returns minimal M1 without candles."""
        m1 = build_m1_understanding(bid=1.085, ask=1.0851)
        assert m1.spread_at_observation > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestrator:
    """MarketUnderstandingBuilder orchestrates all layers."""

    def test_full_build(self):
        """Produces complete MarketUnderstanding with all layers."""
        candles = [MockCandle(
            high=1.085 + (i % 5) * 0.0003,
            low=1.083 + (i % 3) * 0.0002,
            open=1.084, close=1.0845,
            time=1753574400 + i * 300,
        ) for i in range(65)]

        mu = build_market_understanding(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            candles=candles,
            market_context=MockMarketContext(),
            bid=1.085,
            ask=1.0851,
        )

        assert mu.symbol == "EURUSD"
        assert mu.schema_version == _SCHEMA_VERSION
        assert mu.confidence > 0.0
        assert mu.h1.bos_confirmed is True
        assert mu.m15.range_position > 0.0
        assert mu.m5.atr > 0.0

    def test_minimal_build(self):
        """Produces MarketUnderstanding with minimal data."""
        mu = build_market_understanding(
            symbol="USDJPY",
            timestamp_utc=1753574400.0,
        )
        assert mu.symbol == "USDJPY"
        assert mu.confidence == 0.0  # No data → no confidence

    def test_observations_generated(self):
        """Human-readable observations are generated."""
        mu = build_market_understanding(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            market_context=MockMarketContext(),
            candles=[MockCandle()] * 65,
            bid=1.085,
            ask=1.0851,
        )
        assert isinstance(mu.observations, list)
        # Should have at least H1 BOS observation
        assert any("BOS" in obs for obs in mu.observations)

    def test_serializable(self):
        """to_dict produces valid JSON."""
        mu = build_market_understanding(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            market_context=MockMarketContext(),
            candles=[MockCandle()] * 65,
            bid=1.085, ask=1.0851,
        )
        d = mu.to_dict()
        serialized = json.dumps(d, default=str)
        restored = json.loads(serialized)
        assert restored["symbol"] == "EURUSD"
        assert restored["h1"]["bos_confirmed"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserver:
    """V3 shadow observer integration."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_record(self):
        """Observer persists MarketUnderstanding to JSONL."""
        ctx = MockCtx(market_context=MockMarketContext())
        observe_market_understanding(ctx)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(open(files[0]).readline())
        assert record["schema_version"] == _SCHEMA_VERSION
        assert record["symbol"] == "EURUSD"

    def test_does_not_modify_engine_result(self):
        """Observer does not mutate engine_result."""
        ctx = MockCtx()
        original = dict(ctx.engine_result)
        observe_market_understanding(ctx)
        assert ctx.engine_result == original

    def test_returns_none(self):
        """Observer returns None."""
        ctx = MockCtx()
        result = observe_market_understanding(ctx)
        assert result is None

    def test_handles_missing_context(self):
        """Works without MarketContext."""
        ctx = MockCtx(market_context=None, htf_context=None, candles=None)
        observe_market_understanding(ctx)  # Should not raise

    def test_registered_in_observer_registry(self):
        """Observer #10 is registered."""
        import inspect
        from core.pipeline.observers import ObserverRegistry
        source = inspect.getsource(ObserverRegistry.notify_all)
        assert "observe_market_understanding" in source


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """No execution coupling."""

    def test_models_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.models as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline", "from core.runtime"]:
            assert forbidden not in source

    def test_builders_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.builders as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.runtime"]:
            assert forbidden not in source

    def test_no_buy_sell_in_model(self):
        """MarketUnderstanding does not contain trade signal fields."""
        from dataclasses import fields as dc_fields
        import core.v3_shadow.models as m
        # Check that no field name contains trade action terms
        for f in dc_fields(m.MarketUnderstanding):
            assert "execute" not in f.name.lower()
            assert "order" not in f.name.lower()
            assert "position" not in f.name.lower()
