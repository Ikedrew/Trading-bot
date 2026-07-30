"""
Tests for V3 Execution Assessment (Phase 7).

Verifies:
    - READY_FOR_EXECUTION when all upstream positive
    - EXECUTION_CONSTRAINED when partial issues
    - NOT_EXECUTABLE when missing upstream
    - SIMULATED_ONLY when risk is poor
    - Correct price/stop/target calculation
    - Observer integration
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_shadow.context_models import (
    HTFStructureContext, LocationContext, BehaviourContext, V3MarketContext,
)
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment, AlignmentResult,
    HIGH_QUALITY_CONTEXT, INTERESTING_CONTEXT, INSUFFICIENT_CONTEXT,
)
from core.v3_shadow.horizon_models import HorizonAssessment, INTRADAY, NO_HORIZON
from core.v3_shadow.entry_models import (
    EntryAssessment, VALID_ENTRY_CONFIRMATION, WEAK_ENTRY_CONFIRMATION,
    NO_ENTRY_CONFIRMATION, INSUFFICIENT_ENTRY_DATA,
)
from core.v3_shadow.risk_models import (
    RiskAssessment, ACCEPTABLE_RISK, MARGINAL_RISK, POOR_RISK, INSUFFICIENT_RISK_DATA,
)
from core.v3_shadow.execution_models import (
    ExecutionAssessment,
    READY_FOR_EXECUTION, EXECUTION_CONSTRAINED, NOT_EXECUTABLE, SIMULATED_ONLY,
    MGMT_TRAIL_BREAKEVEN,
)
from core.v3_shadow.execution_builder import build_execution_assessment


def _ctx():
    return V3MarketContext(symbol="EURUSD", timestamp_utc=1753574400.0, overall_confidence=0.7)

def _opp(state=INTERESTING_CONTEXT):
    return OpportunityAssessment(symbol="EURUSD", assessment_state=state, confidence=0.7)

def _horizon(selected=INTRADAY):
    return HorizonAssessment(symbol="EURUSD", selected_horizon=selected, confidence=0.7,
                             expected_move_min_pips=20.0, expected_move_max_pips=50.0)

def _entry(state=VALID_ENTRY_CONFIRMATION, direction="BULLISH"):
    return EntryAssessment(symbol="EURUSD", entry_state=state, direction=direction,
                           primary_trigger="RETEST_ENTRY", confidence=0.7)

def _risk(state=ACCEPTABLE_RISK, stop=10.0, target=30.0):
    return RiskAssessment(symbol="EURUSD", risk_state=state, horizon=INTRADAY,
                          stop_distance_pips=stop, target_distance_pips=target,
                          risk_reward_ratio=target/stop if stop > 0 else 0)


class TestReadyForExecution:
    """All upstream positive → READY_FOR_EXECUTION."""

    def test_all_positive(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(), _entry(), _risk(),
            bid=1.08500, ask=1.08510)
        assert ex.execution_state == READY_FOR_EXECUTION
        assert ex.direction == "BULLISH"
        assert ex.entry_price > 0
        assert ex.stop_price > 0
        assert ex.target_price > ex.entry_price

    def test_bearish_direction(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(), _entry(direction="BEARISH"), _risk(),
            bid=1.08500, ask=1.08510)
        assert ex.direction == "BEARISH"
        assert ex.stop_price > ex.entry_price
        assert ex.target_price < ex.entry_price


class TestExecutionConstrained:
    """Partial issues → EXECUTION_CONSTRAINED."""

    def test_weak_entry(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(),
            _entry(WEAK_ENTRY_CONFIRMATION), _risk(),
            bid=1.085, ask=1.0851)
        assert ex.execution_state == EXECUTION_CONSTRAINED

    def test_marginal_risk(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(),
            _entry(), _risk(MARGINAL_RISK),
            bid=1.085, ask=1.0851)
        assert ex.execution_state == EXECUTION_CONSTRAINED


class TestNotExecutable:
    """Missing upstream → NOT_EXECUTABLE."""

    def test_no_horizon(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(NO_HORIZON),
            _entry(), _risk(), bid=1.085, ask=1.0851)
        assert ex.execution_state == NOT_EXECUTABLE

    def test_insufficient_context(self):
        ex = build_execution_assessment(
            _ctx(), _opp(INSUFFICIENT_CONTEXT), _horizon(),
            _entry(), _risk(), bid=1.085, ask=1.0851)
        assert ex.execution_state == NOT_EXECUTABLE

    def test_insufficient_entry(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(),
            _entry(INSUFFICIENT_ENTRY_DATA), _risk(),
            bid=1.085, ask=1.0851)
        assert ex.execution_state == NOT_EXECUTABLE


class TestSimulatedOnly:
    """Poor risk → SIMULATED_ONLY."""

    def test_poor_risk(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(),
            _entry(), _risk(POOR_RISK),
            bid=1.085, ask=1.0851)
        assert ex.execution_state == SIMULATED_ONLY

    def test_no_entry_confirmation(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(),
            _entry(NO_ENTRY_CONFIRMATION), _risk(),
            bid=1.085, ask=1.0851)
        assert ex.execution_state == SIMULATED_ONLY


class TestPriceCalculation:
    """Stop/target computed correctly."""

    def test_bullish_stop_below_entry(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(), _entry(), _risk(stop=10.0, target=30.0),
            bid=1.08500, ask=1.08510)
        assert ex.stop_price < ex.entry_price
        assert ex.target_price > ex.entry_price
        # Stop should be ~10 pips below
        stop_dist = (ex.entry_price - ex.stop_price) / 0.0001
        assert stop_dist == pytest.approx(10.0, abs=0.5)

    def test_spread_recorded(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(), _entry(), _risk(),
            bid=1.08500, ask=1.08510)
        assert ex.spread_at_entry == pytest.approx(1.0, abs=0.1)
        assert ex.total_entry_cost_pips > ex.spread_at_entry


class TestManagement:
    """Management profile from horizon."""

    def test_intraday_management(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(INTRADAY), _entry(), _risk(),
            bid=1.085, ask=1.0851)
        assert ex.management_profile == MGMT_TRAIL_BREAKEVEN


class TestModels:
    """Model correctness."""

    def test_frozen(self):
        ex = ExecutionAssessment(symbol="EURUSD")
        with pytest.raises(Exception):
            ex.symbol = "X"

    def test_to_dict(self):
        ex = build_execution_assessment(
            _ctx(), _opp(), _horizon(), _entry(), _risk(),
            bid=1.085, ask=1.0851)
        d = ex.to_dict()
        assert d["execution_state"] == READY_FOR_EXECUTION
        assert "entry_price" in d
        assert "management_profile" in d
        assert json.loads(json.dumps(d, default=str))


class TestObserver:
    """Observer persists ExecutionAssessment."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig = {}
        for attr in ("_LOCAL_DIR", "_CONTEXT_DIR", "_ASSESSMENT_DIR",
                     "_HORIZON_DIR", "_ENTRY_DIR", "_RISK_DIR", "_EXECUTION_DIR"):
            self._orig[attr] = getattr(mod, attr)
            setattr(mod, attr, self.temp_dir + "/" + attr)

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        for attr, val in self._orig.items():
            setattr(mod, attr, val)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persists_execution(self):
        from core.v3_shadow.observer import observe_market_understanding

        @dataclass
        class MockCandle:
            high: float = 1.086
            low: float = 1.084
            open: float = 1.085
            close: float = 1.0855
            time: int = 1753574400

        @dataclass
        class Ctx:
            symbol: str = "EURUSD"
            cycle_id: int = 1
            bar_time: float = 1753574400.0
            engine_result: dict = None
            engine_state: Any = None
            candles: list = None
            closed_i: int = 60
            bid: float = 1.08500
            ask: float = 1.08510
            htf_context: Any = None
            market_context: Any = None
            runtime_session_id: str = "t"
            decision_funnel: Any = None
            config: Any = None
            detected_patterns: list = None
            risk_manager: Any = None

        ctx = Ctx(
            engine_result={"entity_id": "TEST"},
            candles=[MockCandle(
                high=1.085 + (i % 5) * 0.0003,
                low=1.083 + (i % 3) * 0.0002,
                open=1.084, close=1.0845,
                time=1753574400 + i * 300,
            ) for i in range(65)],
        )
        observe_market_understanding(ctx)

        exec_files = list(Path(self.temp_dir).rglob("*.jsonl"))
        # Should have execution file among others
        exec_specific = [f for f in exec_files if "_EXECUTION_DIR" in str(f)]
        # At least the execution dir should have been created
        all_records = []
        for f in exec_files:
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        rec = json.loads(line)
                        if rec.get("schema_version", "").startswith("v3_execution"):
                            all_records.append(rec)
        assert len(all_records) == 1
        assert "execution_state" in all_records[0]


class TestSafety:
    def test_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.execution_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source
