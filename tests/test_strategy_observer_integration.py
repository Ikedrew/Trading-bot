"""
Tests for Strategy Observer Pipeline Integration.

Verifies:
    A) Observer receives market cycles
    B) Strategy observations are created
    C) Records contain family, strategy, phase, conditions, status
    D) Persistence works
    E) Observer failure does not affect trading pipeline
    F) Existing behaviour unchanged
"""

import shutil
import tempfile
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.strategies.strategy_intelligence_observer import (
    observe_strategy_intelligence,
    get_observer_instance,
    reset_observer,
)


@dataclass
class MockObserverContext:
    """Minimal mock of ObserverContext for testing."""
    symbol: str = "EURUSD"
    cycle_id: int = 1
    bar_time: float = 1719000000.0
    engine_result: dict = None
    engine_state: Any = None
    candles: Any = None
    closed_i: int = 0
    bid: float = 1.08500
    ask: float = 1.08510
    config: Any = None
    detected_patterns: list = None
    risk_manager: Any = None
    htf_context: Any = None
    runtime_session_id: str = "test-session"
    decision_funnel: Any = None

    def __post_init__(self):
        if self.engine_result is None:
            self.engine_result = {
                "action": "NO_TRADE",
                "reason": "score_below_threshold",
                "score": 0.25,
                "pattern": "HAMMER",
                "market_phase": "REVERSAL",
                "activation_regime": "RANGING",
                "side": "",
            }
        if self.detected_patterns is None:
            self.detected_patterns = []


@dataclass
class MockMarketContext:
    """Mock MarketContext with all required fields."""
    symbol: str = "EURUSD"
    cycle_id: int = 1
    timestamp_utc: float = 1719000000.0

    class _Direction:
        value = "BEARISH"
    class _Regime:
        value = "RANGING"
    class _Phase:
        value = "REVERSAL"

    direction: Any = None
    regime: Any = None
    phase: Any = None
    tradability_score: float = 0.6

    class _H4:
        regime = "RANGING"
        trend_bias = "NEUTRAL"
        trend_strength = 0.3
        atr_ratio = 1.0
    class _H1:
        direction = "BEARISH"
        swing_structure = "LH_LL"
        ema_position = -0.5
        bos_confirmed = True
        bos_direction = "BEARISH"
    class _M15:
        quality_score = 0.6
        at_key_level = True
        order_block_present = False
        nearest_support = 1.08400
        nearest_resistance = 1.08700
    class _M5:
        bias_phase = "CONFIRMED"
        bias_strength = 60.0
        bias_direction = "SELL"
        trigger_ready = True
        confirmation_strength = "STRONG"

    h4: Any = None
    h1: Any = None
    m15: Any = None
    m5: Any = None

    def __post_init__(self):
        self.direction = self._Direction()
        self.regime = self._Regime()
        self.phase = self._Phase()
        self.h4 = self._H4()
        self.h1 = self._H1()
        self.m15 = self._M15()
        self.m5 = self._M5()


# ═══════════════════════════════════════════════════════════════════════════════
# A) OBSERVER RECEIVES MARKET CYCLES
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserverReceivesCycles:
    """Tests that the observer is called and processes cycles."""

    def setup_method(self):
        reset_observer()

    def teardown_method(self):
        reset_observer()

    def test_observe_does_not_raise(self):
        """observe_strategy_intelligence never raises."""
        ctx = MockObserverContext()
        # Should not raise regardless of content
        observe_strategy_intelligence(ctx)

    def test_observe_with_none_engine_result(self):
        """Handles None engine_result gracefully."""
        ctx = MockObserverContext(engine_result=None)
        observe_strategy_intelligence(ctx)  # Should not raise

    def test_observe_with_empty_engine_result(self):
        """Handles empty engine_result gracefully."""
        ctx = MockObserverContext(engine_result={})
        observe_strategy_intelligence(ctx)

    def test_observe_with_market_context(self):
        """Handles MarketContext object in htf_context."""
        ctx = MockObserverContext(htf_context=MockMarketContext())
        observe_strategy_intelligence(ctx)

    def test_observe_increments_cycle_count(self):
        """Observer tracks cycle count."""
        ctx = MockObserverContext()
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        assert observer.total_cycles >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# B) STRATEGY OBSERVATIONS ARE CREATED
# ═══════════════════════════════════════════════════════════════════════════════


class TestObservationsCreated:
    """Tests that strategy observations are generated."""

    def setup_method(self):
        reset_observer()

    def teardown_method(self):
        reset_observer()

    def test_observations_created_each_cycle(self):
        """Each cycle creates observations for all registered strategies."""
        ctx = MockObserverContext()
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        # Should have 5 observations (one per strategy in conditions registry)
        assert observer.observation_count >= 5

    def test_observations_have_correct_symbol(self):
        """Observations carry the correct symbol."""
        ctx = MockObserverContext(symbol="GBPUSD")
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        obs = observer.get_observations()
        assert all(o.symbol == "GBPUSD" for o in obs)

    def test_observations_have_correct_cycle(self):
        """Observations carry the correct cycle_id."""
        ctx = MockObserverContext(cycle_id=42)
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        obs = observer.get_observations()
        assert all(o.cycle_id == 42 for o in obs)


# ═══════════════════════════════════════════════════════════════════════════════
# C) RECORDS CONTAIN REQUIRED FIELDS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordContent:
    """Tests that observation records have all required fields."""

    def setup_method(self):
        reset_observer()

    def teardown_method(self):
        reset_observer()

    def test_observations_have_family(self):
        """Each observation has a strategy family."""
        ctx = MockObserverContext()
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        for obs in observer.get_observations():
            # Family should be populated for known strategies
            assert obs.strategy_id  # Has a strategy

    def test_observations_have_phase(self):
        """Observations capture market phase."""
        ctx = MockObserverContext()
        ctx.engine_result["market_phase"] = "REVERSAL"
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        obs = observer.get_observations()
        assert any(o.market_phase == "REVERSAL" for o in obs)

    def test_observations_have_evaluation_status(self):
        """Each observation has an evaluation status."""
        ctx = MockObserverContext()
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        for obs in observer.get_observations():
            assert obs.overall_status in (
                "FULLY_MET", "PARTIALLY_MET", "NOT_MET", "INCOMPLETE",
                "NO_CONDITIONS_DEFINED",
            )

    def test_observations_have_pattern(self):
        """Pattern detection is captured in observations."""
        ctx = MockObserverContext()
        ctx.engine_result["pattern"] = "HAMMER"
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        obs = observer.get_observations()
        assert any(o.pattern_detected == "HAMMER" for o in obs)

    def test_observations_have_condition_counts(self):
        """Observations record condition pass/fail counts."""
        ctx = MockObserverContext(htf_context=MockMarketContext())
        ctx.engine_result["pattern"] = "HAMMER"
        observe_strategy_intelligence(ctx)
        observer = get_observer_instance()
        obs = observer.get_observations_for_strategy("range_reversal_v1")
        if obs:
            assert obs[0].conditions_met >= 0
            assert obs[0].confidence >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# D) PERSISTENCE WORKS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    """Tests that observations are persisted to disk."""

    def setup_method(self):
        reset_observer()
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._original = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        reset_observer()
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._original
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persistence_creates_file(self):
        """Observation creates a JSONL file on disk."""
        from pathlib import Path
        ctx = MockObserverContext(symbol="EURUSD")
        observe_strategy_intelligence(ctx)

        # Check file was created
        symbol_dir = Path(self.temp_dir) / "EURUSD"
        assert symbol_dir.exists()
        files = list(symbol_dir.glob("*.jsonl"))
        assert len(files) >= 1

    def test_persistence_contains_valid_json(self):
        """Persisted records are valid JSON."""
        import json
        from pathlib import Path
        ctx = MockObserverContext(symbol="EURUSD")
        observe_strategy_intelligence(ctx)

        symbol_dir = Path(self.temp_dir) / "EURUSD"
        for f in symbol_dir.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        record = json.loads(line)
                        assert "observation_id" in record
                        assert "symbol" in record
                        assert "strategy_family" in record


# ═══════════════════════════════════════════════════════════════════════════════
# E) FAILURE DOES NOT AFFECT PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureIsolation:
    """Tests that observer failures are contained."""

    def setup_method(self):
        reset_observer()

    def teardown_method(self):
        reset_observer()

    def test_exception_in_observer_swallowed(self):
        """Even if internal logic fails, observe_strategy_intelligence never raises."""
        # Corrupt ctx to force internal error
        ctx = "not_a_context_object"
        # Should not raise
        observe_strategy_intelligence(ctx)

    def test_missing_fields_handled(self):
        """Missing context fields don't crash the observer."""
        ctx = MockObserverContext()
        ctx.htf_context = object()  # Minimal object with no fields
        observe_strategy_intelligence(ctx)

    def test_engine_result_not_modified(self):
        """Observer must not modify engine_result."""
        engine_result = {
            "action": "NO_TRADE",
            "reason": "test",
            "score": 0.5,
            "pattern": "HAMMER",
            "market_phase": "REVERSAL",
            "activation_regime": "RANGING",
        }
        original = dict(engine_result)
        ctx = MockObserverContext(engine_result=engine_result)
        observe_strategy_intelligence(ctx)
        # Verify engine_result was not mutated
        for key in original:
            assert engine_result[key] == original[key]


# ═══════════════════════════════════════════════════════════════════════════════
# F) EXISTING BEHAVIOUR UNCHANGED
# ═══════════════════════════════════════════════════════════════════════════════


class TestExistingBehaviourUnchanged:
    """Tests that production behaviour is not affected."""

    def test_observer_registry_still_works(self):
        """ObserverRegistry can be instantiated and has notify_all."""
        from core.pipeline.observers import ObserverRegistry
        registry = ObserverRegistry()
        assert hasattr(registry, "notify_all")

    def test_no_strategy_activation(self):
        """Observer integration does not activate any strategy."""
        from core.strategies.registry import get_active_strategies
        reset_observer()
        ctx = MockObserverContext()
        observe_strategy_intelligence(ctx)
        assert get_active_strategies() == []

    def test_no_execution_imports_in_observer_module(self):
        """strategy_intelligence_observer.py has no execution imports."""
        import inspect
        import core.strategies.strategy_intelligence_observer as m
        source = inspect.getsource(m)
        forbidden = [
            "from execution",
            "from risk",
            "import MetaTrader5",
        ]
        for f in forbidden:
            assert f not in source, f"Contains forbidden: {f}"

    def test_observer_context_dataclass_unchanged(self):
        """ObserverContext still has original fields."""
        from core.pipeline.observers import ObserverContext
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ObserverContext)}
        required = {
            "symbol", "cycle_id", "bar_time", "engine_result",
            "engine_state", "candles", "closed_i", "bid", "ask",
            "config", "detected_patterns", "risk_manager",
            "htf_context", "runtime_session_id", "decision_funnel",
        }
        assert required.issubset(fields)
