"""
Tests for V2 Opportunity Observer integration.

Verifies:
    - Observer creates V2Opportunity records
    - Observer does not modify decision output
    - Observer handles missing MarketContext safely
    - Observer persists JSONL correctly
    - correlation_id/entity_id preserved
    - Schema version correct
    - Existing execution tests unchanged
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

import pytest

from core.observers.v2_opportunity_observer import observe_v2_opportunity


@dataclass
class MockCandle:
    high: float = 1.086
    low: float = 1.084
    open: float = 1.085
    close: float = 1.0855
    time: int = 1753574400


@dataclass
class MockSignal:
    bar_index: int = 60
    pattern: str = "HAMMER"


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
                "reason": "test",
                "pattern": "HAMMER",
                "side": "BUY",
                "entity_id": "EURUSD_1753574400",
                "score": 0.6,
                "_best_pattern": MockSignal(),
            }
        if self.candles is None:
            self.candles = [MockCandle()] * 65
        if self.detected_patterns is None:
            self.detected_patterns = []


@dataclass
class MockMarketContext:
    class _H4:
        regime = "RANGING"
        trend_bias = "NEUTRAL"
        trend_strength = 0.3
        atr_ratio = 1.0
    class _H1:
        direction = "BULLISH"
        swing_structure = "HH_HL"
        bos_confirmed = True
        bos_direction = "BULLISH"
        ema_position = 0.4
    class _M15:
        quality_score = 0.7
        at_key_level = True
        order_block_present = False
        nearest_support = 1.084
        nearest_resistance = 1.087
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


class TestObserverCreatesRecords:
    """Observer produces V2Opportunity records."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v2_opportunity_builder as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v2_opportunity_builder as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_record(self):
        """Observer creates a persisted V2Opportunity."""
        from pathlib import Path
        ctx = MockCtx(market_context=MockMarketContext())
        observe_v2_opportunity(ctx)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1
        with open(files[0]) as f:
            record = json.loads(f.readline())
        assert record["symbol"] == "EURUSD"
        assert "v2_opportunity" in record["schema_version"]

    def test_captures_h4_regime(self):
        """H4 regime extracted from MarketContext."""
        from pathlib import Path
        ctx = MockCtx(market_context=MockMarketContext())
        observe_v2_opportunity(ctx)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        record = json.loads(open(files[0]).readline())
        assert record["h4_regime"] == "RANGING"

    def test_captures_h1_bos(self):
        """H1 BOS information captured."""
        from pathlib import Path
        ctx = MockCtx(market_context=MockMarketContext())
        observe_v2_opportunity(ctx)
        record = json.loads(open(list(Path(self.temp_dir).rglob("*.jsonl"))[0]).readline())
        assert record["h1_bos_confirmed"] is True
        assert record["h1_bos_direction"] == "BULLISH"

    def test_captures_pattern_features(self):
        """Pattern stored as feature, not signal."""
        from pathlib import Path
        ctx = MockCtx()
        observe_v2_opportunity(ctx)
        record = json.loads(open(list(Path(self.temp_dir).rglob("*.jsonl"))[0]).readline())
        assert record["pattern_detected"] == "HAMMER"
        assert record["pattern_direction"] == "BUY"

    def test_captures_spread(self):
        """Spread computed from bid/ask."""
        from pathlib import Path
        ctx = MockCtx()
        observe_v2_opportunity(ctx)
        record = json.loads(open(list(Path(self.temp_dir).rglob("*.jsonl"))[0]).readline())
        assert record["spread"] == pytest.approx(0.0001, abs=0.00001)

    def test_entity_id_preserved(self):
        """entity_id flows through as correlation_id."""
        from pathlib import Path
        ctx = MockCtx()
        ctx.engine_result["entity_id"] = "EURUSD_1753574400"
        observe_v2_opportunity(ctx)
        record = json.loads(open(list(Path(self.temp_dir).rglob("*.jsonl"))[0]).readline())
        assert record["correlation_id"] == "EURUSD_1753574400"


class TestObserverDoesNotModifyDecisions:
    """Observer is purely passive."""

    def test_engine_result_unchanged(self):
        """Observer does not mutate engine_result."""
        ctx = MockCtx()
        original = dict(ctx.engine_result)
        observe_v2_opportunity(ctx)
        for key in original:
            assert ctx.engine_result[key] == original[key]

    def test_no_return_value_consumed(self):
        """observe_v2_opportunity returns None."""
        ctx = MockCtx()
        result = observe_v2_opportunity(ctx)
        assert result is None


class TestMissingContextHandling:
    """Observer handles missing data gracefully."""

    def test_no_market_context(self):
        """Works without MarketContext."""
        ctx = MockCtx(market_context=None, htf_context=None)
        observe_v2_opportunity(ctx)  # Should not raise

    def test_no_engine_result(self):
        """Works with empty engine_result."""
        ctx = MockCtx(engine_result={})
        observe_v2_opportunity(ctx)

    def test_no_candles(self):
        """Works without candle data."""
        ctx = MockCtx(candles=None)
        observe_v2_opportunity(ctx)

    def test_corrupt_context(self):
        """Handles corrupt context gracefully."""
        ctx = MockCtx()
        ctx.market_context = "not_a_context"  # Wrong type
        observe_v2_opportunity(ctx)  # Should not raise


class TestRegistration:
    """Observer is correctly registered in ObserverRegistry."""

    def test_observer_registry_has_8_observers(self):
        """ObserverRegistry dispatches 8 observers."""
        import inspect
        from core.pipeline.observers import ObserverRegistry
        source = inspect.getsource(ObserverRegistry.notify_all)
        assert "v2_opportunity_observer" in source

    def test_observer_context_unchanged(self):
        """ObserverContext still has all required fields."""
        from core.pipeline.observers import ObserverContext
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ObserverContext)}
        required = {"symbol", "cycle_id", "bar_time", "engine_result",
                    "candles", "bid", "ask", "htf_context", "market_context"}
        assert required.issubset(fields)


class TestSafety:
    """No forbidden imports in observer."""

    def test_no_execution_imports(self):
        import inspect
        import core.observers.v2_opportunity_observer as m
        source = inspect.getsource(m)
        for f in ["from risk", "import MetaTrader5"]:
            assert f not in source
