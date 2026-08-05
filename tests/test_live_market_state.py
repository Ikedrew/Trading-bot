"""Tests for live_market_state snapshot dataset."""
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_market_state import (
    update_live_market_state,
    read_live_market_state,
    read_all_live_states,
    _SCHEMA_VERSION,
)


@dataclass(frozen=True)
class _MockQuality:
    location_score: float = 0.7
    structure_score: float = 0.8
    behaviour_score: float = 0.6
    formation_score: float = 0.75
    overall_quality: float = 0.72


@dataclass(frozen=True)
class _MockOpp:
    observation_id: str = "abc123"
    opportunity_state: str = "WATCHING"
    directional_bias: str = "BEARISH"
    opportunity_type: str = "ZONE_REACTION"
    quality: _MockQuality = field(default_factory=_MockQuality)


@dataclass(frozen=True)
class _MockStrategy:
    strategy_family: str = "MEAN_REVERSION"
    strategy_confidence: float = 0.7
    directional_context: str = "BEARISH"


@dataclass(frozen=True)
class _MockMovement:
    minimum_expected_move: float = 10.0
    maximum_expected_move: float = 30.0
    measurement_unit: str = "pips"


@dataclass(frozen=True)
class _MockLifecycle:
    expected_duration_minutes: float = 60.0
    holding_style: str = "INTRADAY"


@dataclass(frozen=True)
class _MockHorizon:
    horizon_type: str = "INTRADAY"
    movement_expectation: _MockMovement = field(default_factory=_MockMovement)
    trade_lifecycle: _MockLifecycle = field(default_factory=_MockLifecycle)


@dataclass(frozen=True)
class _MockStop:
    price: float = 1.0895
    structure_source: str = "H1_SWING"
    reasoning: str = ""


@dataclass(frozen=True)
class _MockTarget:
    price: float = 1.082
    structure_source: str = "H1_SWING"
    reasoning: str = ""


@dataclass(frozen=True)
class _MockEntry:
    entry_status: str = "READY"
    entry_method: str = "CONFIRMATION_ENTRY"
    trade_direction: str = "SHORT"
    entry_price: float = 1.087
    entry_zone: str = ""
    stop_reference: _MockStop = field(default_factory=_MockStop)
    target_reference: _MockTarget = field(default_factory=_MockTarget)
    risk_distance: float = 0.0025
    reward_distance: float = 0.005
    expected_rr: float = 2.0
    reasoning: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockRiskProfile:
    risk_percentage: float = 0.0025
    max_loss_amount: float = 124.0
    position_size: float = 4.22


@dataclass(frozen=True)
class _MockRisk:
    approved: bool = True
    rejection_reason: str = ""
    risk_profile: _MockRiskProfile = field(default_factory=_MockRiskProfile)


@dataclass(frozen=True)
class _MockOrderDetails:
    order_type: str = "MARKET"
    volume: float = 4.22
    symbol: str = "EURUSD"
    direction: str = "SHORT"
    entry_price: float = 1.087
    stop_loss: float = 1.0895
    take_profit: float = 1.082


@dataclass(frozen=True)
class _MockExecution:
    approved: bool = True
    rejection_reason: str = ""
    order_details: _MockOrderDetails = field(default_factory=_MockOrderDetails)


@dataclass(frozen=True)
class _MockRegime:
    regime: str = "RANGING"
    regime_confidence: float = 0.85
    volatility_state: str = "NORMAL"


@dataclass(frozen=True)
class _MockH4:
    trend: str = "NEUTRAL"
    trend_strength: float = 0.2
    market_phase: str = "CONSOLIDATION"


@dataclass(frozen=True)
class _MockH1:
    bos_confirmed: bool = True
    bos_direction: str = "BEARISH"
    structural_clarity: float = 0.72
    dominant_trend: str = "BEARISH"


@dataclass(frozen=True)
class _MockM15:
    pullback_active: bool = True
    displacement_present: bool = False
    range_position: float = 0.75


@dataclass(frozen=True)
class _MockM5:
    momentum_direction: str = "BEARISH"
    momentum_strength: float = 0.6
    rejection_present: bool = True
    confirmation_candle: bool = False
    local_bos: bool = False


@dataclass(frozen=True)
class _MockLocation:
    location_type: str = "SUPPLY_ZONE"
    inside_institutional_zone: bool = True
    zone_quality: float = 0.8
    range_position: float = 0.75
    premium_discount: str = "PREMIUM"


@dataclass(frozen=True)
class _MockHTF:
    macro_bias: str = "BEARISH"
    structure_alignment: float = 0.8


@dataclass(frozen=True)
class _MockMarketState:
    regime: _MockRegime = field(default_factory=_MockRegime)
    h4: _MockH4 = field(default_factory=_MockH4)
    h1: _MockH1 = field(default_factory=_MockH1)
    m15: _MockM15 = field(default_factory=_MockM15)
    m5: _MockM5 = field(default_factory=_MockM5)
    location: _MockLocation = field(default_factory=_MockLocation)
    htf_alignment: _MockHTF = field(default_factory=_MockHTF)


@dataclass
class _MockPipeline:
    market_state: _MockMarketState = field(default_factory=_MockMarketState)
    opportunity: _MockOpp = field(default_factory=_MockOpp)
    strategy: _MockStrategy = field(default_factory=_MockStrategy)
    horizon: _MockHorizon = field(default_factory=_MockHorizon)
    entry: _MockEntry = field(default_factory=_MockEntry)
    risk: _MockRisk = field(default_factory=_MockRisk)
    execution: _MockExecution = field(default_factory=_MockExecution)

    @property
    def approved(self):
        return self.execution.approved

    @property
    def rejection_stage(self):
        return "" if self.approved else "execution"


class TestUpdateAndRead:
    def test_creates_state_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        result = update_live_market_state(
            symbol="EURUSD", cycle_id=100, bar_time=1785800000,
            v10_pipeline_result=_MockPipeline(),
            engine_result={"action": "EXECUTE", "score": 0.72},
        )
        assert result is not None
        assert (tmp_path / "EURUSD.json").exists()

    def test_overwrites_previous(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        update_live_market_state(symbol="GBPUSD", cycle_id=1, bar_time=100,
                                engine_result={"action": "NO_TRADE", "score": 0.1})
        update_live_market_state(symbol="GBPUSD", cycle_id=2, bar_time=200,
                                engine_result={"action": "NO_TRADE", "score": 0.5})
        state = json.loads((tmp_path / "GBPUSD.json").read_text())
        assert state["cycle_id"] == 2
        assert state["score"] == 0.5

    def test_read_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        update_live_market_state(symbol="AUDUSD", cycle_id=5, bar_time=999,
                                v10_pipeline_result=_MockPipeline())
        state = read_live_market_state("AUDUSD")
        assert state is not None
        assert state["symbol"] == "AUDUSD"

    def test_read_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        assert read_live_market_state("MISSING") is None

    def test_read_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        update_live_market_state(symbol="EURUSD", cycle_id=1, bar_time=100,
                                engine_result={"action": "NO_TRADE"})
        update_live_market_state(symbol="GBPUSD", cycle_id=1, bar_time=100,
                                engine_result={"action": "NO_TRADE"})
        all_states = read_all_live_states()
        assert "EURUSD" in all_states
        assert "GBPUSD" in all_states


class TestV10Extraction:
    def test_full_v10_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        result = update_live_market_state(
            symbol="EURUSD", cycle_id=42, bar_time=1785800000,
            v10_pipeline_result=_MockPipeline(),
            engine_result={"action": "EXECUTE", "score": 0.72},
        )
        assert result["market"]["regime"] == "RANGING"
        assert result["market"]["h4_trend"] == "NEUTRAL"
        assert result["market"]["h1_bos_direction"] == "BEARISH"
        assert result["market"]["location_type"] == "SUPPLY_ZONE"
        assert result["market"]["m5_momentum"] == "BEARISH"
        assert result["opportunity"]["state"] == "WATCHING"
        assert result["opportunity"]["overall_quality"] == 0.72
        assert result["strategy"]["family"] == "MEAN_REVERSION"
        assert result["strategy"]["confidence"] == 0.7
        assert result["entry"]["status"] == "READY"
        assert result["entry"]["price"] == 1.087
        assert result["entry"]["expected_rr"] == 2.0
        assert result["risk"]["approved"] is True
        assert result["risk"]["position_size"] == 4.22
        assert result["execution"]["approved"] is True
        assert result["approved"] is True
        assert result["rejection_stage"] == ""

    def test_schema_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", str(tmp_path))
        result = update_live_market_state(
            symbol="X", cycle_id=1, bar_time=0,
            engine_result={"action": "NO_TRADE"},
        )
        assert result["schema_version"] == _SCHEMA_VERSION

    def test_failure_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_market_state._LOCAL_DIR", "/nonexistent/path/that/fails")
        result = update_live_market_state(symbol="", cycle_id=0, bar_time=0)
        assert result is None
