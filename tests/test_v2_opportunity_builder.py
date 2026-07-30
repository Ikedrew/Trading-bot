"""
Tests for V2OpportunityBuilder.

Verifies:
    1. Builder creates valid V2Opportunity objects
    2. Missing optional context does not crash
    3. Existing V1 pipeline unaffected
    4. JSON persistence works
    5. correlation_id propagation works
    6. No execution imports exist
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

import pytest

from core.v2_opportunity import V2Opportunity
from core.v2_opportunity_builder import (
    _LOCAL_DIR,
    build_v2_opportunity,
    persist_v2_opportunity,
    read_v2_opportunities,
)


@dataclass
class MockMarketContext:
    """Mock MarketContext with full structure."""
    class _H4:
        regime = "RANGING"
        trend_bias = "NEUTRAL"
        trend_strength = 0.3
        atr_ratio = 1.1
    class _H1:
        direction = "BULLISH"
        swing_structure = "HH_HL"
        bos_confirmed = True
        bos_direction = "BULLISH"
        ema_position = 0.5
    class _M15:
        quality_score = 0.7
        at_key_level = True
        order_block_present = True
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


class TestBuilderCreation:
    """Builder produces valid V2Opportunity objects."""

    def test_basic_creation(self):
        """Builder with minimal params produces valid object."""
        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
        )
        assert isinstance(opp, V2Opportunity)
        assert opp.symbol == "EURUSD"
        assert opp.timestamp_utc == 1753574400.0
        assert opp.opportunity_id.startswith("v2_EURUSD_")

    def test_with_market_context(self):
        """Builder extracts fields from MarketContext."""
        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            market_context=MockMarketContext(),
            bid=1.08500,
            ask=1.08510,
        )
        assert opp.h4_regime == "RANGING"
        assert opp.h1_bias == "BULLISH"
        assert opp.h1_bos_confirmed is True
        assert opp.h1_bos_direction == "BULLISH"
        assert opp.near_support is True
        assert opp.order_block_present is True
        assert opp.spread == pytest.approx(0.0001, abs=0.00001)

    def test_with_pattern_features(self):
        """Builder stores pattern as feature."""
        opp = build_v2_opportunity(
            symbol="GBPUSD",
            timestamp_utc=1753574400.0,
            pattern_detected="HAMMER",
            pattern_direction="BUY",
            pattern_quality=0.85,
            candle_range=0.0015,
            body_ratio=0.3,
            wick_ratio=0.6,
        )
        assert opp.pattern_detected == "HAMMER"
        assert opp.pattern_direction == "BUY"
        assert opp.pattern_quality == 0.85
        assert opp.candle_range == 0.0015

    def test_with_risk_geometry(self):
        """Builder captures multiple stop distances."""
        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            proposed_direction="BUY",
            candle_stop_distance=0.00027,
            structure_stop_distance=0.00113,
            atr_stop_distance=0.00080,
        )
        assert opp.proposed_direction == "BUY"
        assert opp.candle_stop_distance == 0.00027
        assert opp.structure_stop_distance == 0.00113
        assert opp.risk_distance_pips == pytest.approx(11.3, abs=0.1)

    def test_correlation_id_propagation(self):
        """correlation_id passes through to opportunity."""
        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            correlation_id="COR-500-EURUSD-AB12",
        )
        assert opp.correlation_id == "COR-500-EURUSD-AB12"


class TestMissingContext:
    """Missing optional fields do not crash the builder."""

    def test_no_market_context(self):
        """Builder works without MarketContext."""
        opp = build_v2_opportunity(
            symbol="USDJPY",
            timestamp_utc=1753574400.0,
            market_context=None,
        )
        assert opp.h4_regime == ""
        assert opp.h1_bias == ""
        assert opp.h1_bos_confirmed is False

    def test_partial_market_context(self):
        """MarketContext with missing sub-objects."""
        class PartialCtx:
            h4 = None
            h1 = None
            m15 = None
            regime = None
            tradability_score = 0.0

        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            market_context=PartialCtx(),
        )
        assert opp.h4_regime == ""
        assert opp.h1_bias == ""

    def test_zero_prices(self):
        """Zero bid/ask produces zero spread."""
        opp = build_v2_opportunity(
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            bid=0.0,
            ask=0.0,
        )
        assert opp.spread == 0.0
        assert opp.proposed_entry == 0.0


class TestPersistence:
    """V2Opportunities persist to JSONL."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v2_opportunity_builder as mod
        self._original = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.v2_opportunity_builder as mod
        mod._LOCAL_DIR = self._original
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persist_creates_file(self):
        """persist_v2_opportunity writes to JSONL."""
        from pathlib import Path
        opp = build_v2_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0)
        result = persist_v2_opportunity(opp)
        assert result is True
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        assert len(files) == 1

    def test_persist_valid_json(self):
        """Persisted records are valid JSON."""
        from pathlib import Path
        opp = build_v2_opportunity(
            symbol="EURUSD", timestamp_utc=1753574400.0,
            pattern_detected="HAMMER", bid=1.085, ask=1.0851)
        persist_v2_opportunity(opp)
        files = list(Path(self.temp_dir).rglob("*.jsonl"))
        with open(files[0]) as f:
            record = json.loads(f.readline())
        assert record["symbol"] == "EURUSD"
        assert record["pattern_detected"] == "HAMMER"

    def test_read_back(self):
        """read_v2_opportunities returns persisted records."""
        opp = build_v2_opportunity(
            symbol="GBPUSD", timestamp_utc=1753574400.0)
        persist_v2_opportunity(opp)
        records = read_v2_opportunities(symbol="GBPUSD")
        assert len(records) == 1
        assert records[0]["symbol"] == "GBPUSD"


class TestSafety:
    """No execution or pipeline imports."""

    def test_no_forbidden_imports(self):
        import inspect
        import core.v2_opportunity_builder as m
        source = inspect.getsource(m)
        for f in ["from core.pipeline", "from execution",
                  "from risk", "import MetaTrader5", "from core.runtime"]:
            assert f not in source, f"Contains: {f}"

    def test_v1_pipeline_unaffected(self):
        """Building V2Opportunity does not import V1 decision logic."""
        # The builder should work without any V1 pipeline components
        opp = build_v2_opportunity(symbol="TEST", timestamp_utc=1.0)
        assert opp is not None
