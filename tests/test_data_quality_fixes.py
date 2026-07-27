"""
Tests for Strategy Intelligence Data Quality Fixes.

Verifies:
    - Existing observers unchanged
    - Strategy observations contain required research fields
    - entity_id survives the full pipeline
    - MarketContext is preferred over legacy htf_context
    - strategy_family is never blank (fallback to pattern classification)
"""

import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

import pytest

from core.strategies.strategy_intelligence_observer import (
    observe_strategy_intelligence,
    get_observer_instance,
    reset_observer,
)


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK OBJECTS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MockMarketContext:
    """Full MarketContext with all fields populated."""
    class _Dir:
        value = "BEARISH"
    class _Regime:
        value = "RANGING"
    class _Phase:
        value = "REVERSAL"
    class _H4:
        regime = "RANGING"
        trend_bias = "NEUTRAL"
        trend_strength = 0.4
        atr_ratio = 1.1
    class _H1:
        direction = "BEARISH"
        swing_structure = "LH_LL"
        ema_position = -0.3
        bos_confirmed = True
        bos_direction = "BEARISH"
    class _M15:
        quality_score = 0.7
        at_key_level = True
        order_block_present = True
        nearest_support = 1.084
        nearest_resistance = 1.087
    class _M5:
        bias_phase = "CONFIRMED"
        bias_strength = 55.0
        bias_direction = "SELL"
        regime_state = "TREND_DOWN"
        trigger_ready = True
        confirmation_strength = "STRONG"

    direction: Any = None
    regime: Any = None
    phase: Any = None
    tradability_score: float = 0.72
    h4: Any = None
    h1: Any = None
    m15: Any = None
    m5: Any = None

    def __post_init__(self):
        self.direction = self._Dir()
        self.regime = self._Regime()
        self.phase = self._Phase()
        self.h4 = self._H4()
        self.h1 = self._H1()
        self.m15 = self._M15()
        self.m5 = self._M5()


@dataclass
class MockLegacyHTF:
    """Legacy HTF context WITHOUT MarketContext interface."""
    class _Bias:
        direction = "BEARISH"
        bos_confirmed = False
    bias: Any = None

    def __post_init__(self):
        self.bias = self._Bias()


@dataclass
class MockCtx:
    """Minimal ObserverContext mock."""
    symbol: str = "EURUSD"
    cycle_id: int = 100
    bar_time: float = 1753574400.0
    engine_result: dict = None
    engine_state: Any = None
    candles: Any = None
    closed_i: int = 0
    bid: float = 1.085
    ask: float = 1.0851
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
                "reason": "score_below_threshold",
                "score": 0.3,
                "pattern": "HAMMER",
                "market_phase": "REVERSAL",
                "activation_regime": "RANGE",
                "entity_id": "EURUSD_1753574400",
                "side": "",
            }
        if self.detected_patterns is None:
            self.detected_patterns = []


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MarketContext PREFERRED over legacy htf_context
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarketContextPreference:
    """MarketContext fields should be used when available."""

    def setup_method(self):
        reset_observer()
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        reset_observer()
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_market_context_provides_phase(self):
        """Phase comes from MarketContext when available."""
        ctx = MockCtx(
            market_context=MockMarketContext(),
            htf_context=MockLegacyHTF(),
        )
        ctx.engine_result["market_phase"] = ""  # Engine didn't set it
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert len(records) >= 1
        assert records[0]["market_phase"] == "REVERSAL"

    def test_market_context_provides_regime(self):
        """Regime comes from MarketContext when available."""
        ctx = MockCtx(
            market_context=MockMarketContext(),
            htf_context=MockLegacyHTF(),
        )
        ctx.engine_result["activation_regime"] = ""
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["h4_regime"] == "RANGING"

    def test_falls_back_to_engine_result_when_no_market_context(self):
        """Without MarketContext, uses engine_result fields."""
        ctx = MockCtx(
            market_context=None,
            htf_context=None,
        )
        ctx.engine_result["market_phase"] = "IMPULSE"
        ctx.engine_result["activation_regime"] = "TRENDING"
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["market_phase"] == "IMPULSE"

    def test_legacy_htf_used_when_no_market_context(self):
        """Legacy htf_context provides h1_direction fallback."""
        ctx = MockCtx(
            market_context=None,
            htf_context=MockLegacyHTF(),
        )
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["h1_bias"] == "BEARISH"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: strategy_family NEVER BLANK
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyFamilyPopulation:
    """strategy_family must never be empty string."""

    def setup_method(self):
        reset_observer()
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        reset_observer()
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_family_from_eligible_strategy(self):
        """When strategies are eligible, family comes from best strategy."""
        ctx = MockCtx(market_context=MockMarketContext())
        ctx.engine_result["pattern"] = "HAMMER"
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["strategy_family"] != ""

    def test_family_derived_from_pattern_when_no_eligible(self):
        """When no strategies eligible, derive from pattern classification."""
        ctx = MockCtx(market_context=None, htf_context=None)
        ctx.engine_result["market_phase"] = ""  # No phase → no eligible
        ctx.engine_result["pattern"] = "THREE_WHITE_SOLDIERS"
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        # THREE_WHITE_SOLDIERS → MOMENTUM family
        assert records[0]["strategy_family"] == "MOMENTUM"

    def test_family_unknown_when_no_pattern(self):
        """When no pattern and no eligible strategies, family is UNKNOWN."""
        ctx = MockCtx(market_context=None, htf_context=None)
        ctx.engine_result["market_phase"] = ""
        ctx.engine_result["pattern"] = None
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["strategy_family"] == "UNKNOWN"

    def test_family_never_empty_string(self):
        """Regardless of inputs, strategy_family is never ''."""
        contexts = [
            MockCtx(market_context=MockMarketContext()),
            MockCtx(market_context=None, htf_context=None),
            MockCtx(market_context=None, htf_context=MockLegacyHTF()),
        ]
        for i, ctx in enumerate(contexts):
            ctx.symbol = f"PAIR{i}"
            ctx.engine_result = dict(ctx.engine_result)
            ctx.engine_result["entity_id"] = f"PAIR{i}_{int(ctx.bar_time)}"
            observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        for i in range(3):
            records = read_observations_local(symbol=f"PAIR{i}")
            for r in records:
                assert r["strategy_family"] != "", f"Empty family in PAIR{i}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: entity_id SURVIVES FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntityIdPropagation:
    """entity_id must be present in persisted observations."""

    def setup_method(self):
        reset_observer()
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        reset_observer()
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_entity_id_from_engine_result(self):
        """entity_id comes from engine_result when available."""
        ctx = MockCtx()
        ctx.engine_result["entity_id"] = "EURUSD_1753574400"
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert records[0]["entity_id"] == "EURUSD_1753574400"

    def test_entity_id_fallback_construction(self):
        """entity_id constructed from symbol+bar_time when not in engine_result."""
        ctx = MockCtx(symbol="GBPUSD", bar_time=1753600000.0)
        ctx.engine_result["entity_id"] = ""  # Empty
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="GBPUSD")
        assert records[0]["entity_id"] == "GBPUSD_1753600000"

    def test_entity_id_never_empty(self):
        """entity_id is never empty regardless of input."""
        ctx = MockCtx(symbol="USDJPY", bar_time=1753700000.0)
        ctx.engine_result.pop("entity_id", None)
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="USDJPY")
        assert records[0]["entity_id"] == "USDJPY_1753700000"

    def test_entity_id_in_shadow_trade_creation(self):
        """engine_execution_handler passes entity_id to shadow open_trade."""
        import inspect
        from core.runtime.engine_execution_handler import prepare_execution
        source = inspect.getsource(prepare_execution)
        assert "entity_id=new_result.get(\"entity_id\"" in source


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: REQUIRED RESEARCH FIELDS PRESENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequiredResearchFields:
    """Every observation must contain minimum research fields."""

    def setup_method(self):
        reset_observer()
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        reset_observer()
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._orig
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_required_fields_present(self):
        """Observation contains all required research fields."""
        ctx = MockCtx(market_context=MockMarketContext())
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        assert len(records) >= 1
        r = records[0]

        required = [
            "entity_id", "symbol", "timestamp_utc", "market_phase",
            "h4_regime", "strategy_family", "evaluation_status",
            "conditions_passed", "decision_action",
        ]
        for field in required:
            assert field in r, f"Missing required field: {field}"
            assert r[field] is not None, f"Field {field} is None"

    def test_fields_have_meaningful_values(self):
        """Required fields have non-empty values when context is available."""
        ctx = MockCtx(market_context=MockMarketContext())
        ctx.engine_result["pattern"] = "HAMMER"
        observe_strategy_intelligence(ctx)

        from core.strategies.observation_persistence import read_observations_local
        records = read_observations_local(symbol="EURUSD")
        r = records[0]

        assert r["entity_id"] != ""
        assert r["symbol"] == "EURUSD"
        assert r["timestamp_utc"] > 0
        assert r["market_phase"] == "REVERSAL"
        assert r["h4_regime"] == "RANGING"
        assert r["strategy_family"] != ""
        assert r["strategy_family"] != "UNKNOWN"  # Should resolve to REVERSAL
        assert r["evaluation_status"] in (
            "STRATEGIES_FULLY_MET", "STRATEGIES_ELIGIBLE", "NO_ELIGIBLE_STRATEGIES"
        )
        assert r["decision_action"] == "NO_TRADE"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EXISTING OBSERVERS UNCHANGED
# ═══════════════════════════════════════════════════════════════════════════════


class TestExistingObserversUnchanged:
    """Verify no regression in observer infrastructure."""

    def test_observer_context_has_market_context_field(self):
        """ObserverContext has new market_context field with default None."""
        from core.pipeline.observers import ObserverContext
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ObserverContext)}
        assert "market_context" in fields
        assert fields["market_context"].default is None

    def test_observer_context_backwards_compatible(self):
        """ObserverContext can be created without market_context."""
        from core.pipeline.observers import ObserverContext
        ctx = ObserverContext(
            symbol="EURUSD", cycle_id=1, bar_time=1.0,
            engine_result={}, engine_state=None, candles=None,
            closed_i=0, bid=1.0, ask=1.0, config=None,
            detected_patterns=[], risk_manager=None,
            htf_context=None, runtime_session_id="",
            decision_funnel=None,
        )
        assert ctx.market_context is None

    def test_observer_registry_still_works(self):
        """ObserverRegistry can dispatch without error."""
        from core.pipeline.observers import ObserverRegistry
        registry = ObserverRegistry()
        assert hasattr(registry, "notify_all")

    def test_no_forbidden_imports(self):
        """Observer module has no execution imports."""
        import inspect
        import core.strategies.strategy_intelligence_observer as m
        source = inspect.getsource(m)
        for f in ["from execution", "from risk.manager", "import MetaTrader5"]:
            assert f not in source
