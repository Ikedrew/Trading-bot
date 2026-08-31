"""Tests for decision_trace_v2 migration.

Proves:
1. decision_trace_v2 contains all original v1 fields
2. decision_trace_v2 contains all V10 pipeline fields
3. S3 writes go to decision_trace/ (not v10/)
4. No writes occur to v10/* paths
5. Decision Ledger still persists
6. Execution path is unchanged
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.decision_trace import (
    build_decision_trace,
    persist_decision_trace,
    DecisionTrace,
    _SCHEMA_VERSION,
    _S3_PREFIX,
    _S3_BUCKET,
)


# ─── MOCK V10 PIPELINE RESULT ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _MockQuality:
    location_score: float = 0.7
    structure_score: float = 0.8
    behaviour_score: float = 0.6
    formation_score: float = 0.75
    overall_quality: float = 0.72


@dataclass(frozen=True)
class _MockOpportunity:
    observation_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    opportunity_state: str = "VALID"
    directional_bias: str = "BEARISH"
    opportunity_type: str = "ZONE_REACTION"
    quality: _MockQuality = field(default_factory=_MockQuality)
    reasoning: list = field(default_factory=lambda: ["Strong supply zone", "H1 BOS confirmed"])
    supporting_factors: list = field(default_factory=list)
    conflicting_factors: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockH4:
    trend: str = "BEARISH"
    trend_strength: float = 0.6
    market_phase: str = "IMPULSE"
    structure_type: str = "HH_HL"
    swing_high: float = 1.092
    swing_low: float = 1.085
    last_bos_direction: str = "BEARISH"
    atr: float = 0.0045
    volatility_state: str = "NORMAL"
    atr_percentile: float = 0.5
    major_liquidity_above: float = 1.095
    major_liquidity_below: float = 1.082


@dataclass(frozen=True)
class _MockH1:
    dominant_trend: str = "BEARISH"
    structure_type: str = "LH_LL"
    structural_clarity: float = 0.8
    bos_confirmed: bool = True
    bos_direction: str = "BEARISH"
    bos_level: float = 1.087
    choch_detected: bool = False
    choch_direction: str = ""
    swing_high: float = 1.091
    swing_low: float = 1.085
    demand_ob_high: float = 0.0
    demand_ob_low: float = 0.0
    supply_ob_high: float = 1.091
    supply_ob_low: float = 1.0905
    nearest_fvg_above: float = 0.0
    nearest_fvg_below: float = 0.0
    equal_highs_level: float = 0.0
    equal_lows_level: float = 0.0
    session_high: float = 1.093
    session_low: float = 1.084


@dataclass(frozen=True)
class _MockM15:
    internal_bos: bool = True
    internal_bos_direction: str = "BEARISH"
    internal_choch: bool = False
    pullback_active: bool = True
    pullback_depth_atr: float = 1.2
    retracement_pct: float = 0.5
    displacement_present: bool = True
    displacement_direction: str = "BEARISH"
    displacement_magnitude_atr: float = 1.5
    refined_demand_ob_high: float = 0.0
    refined_demand_ob_low: float = 0.0
    refined_supply_ob_high: float = 1.0895
    refined_supply_ob_low: float = 1.089
    nearest_fvg: float = 0.0
    swing_high: float = 1.09
    swing_low: float = 1.086
    range_position: float = 0.7


@dataclass(frozen=True)
class _MockM5:
    local_bos: bool = True
    local_bos_direction: str = "BEARISH"
    momentum_direction: str = "BEARISH"
    momentum_strength: float = 0.7
    rejection_present: bool = True
    rejection_direction: str = "BEARISH"
    rejection_strength_atr: float = 0.9
    at_institutional_zone: bool = True
    zone_type: str = "SUPPLY_OB"
    confirmation_candle: bool = True
    atr: float = 0.00055
    spread: float = 0.00012
    spread_atr_ratio: float = 0.22


@dataclass(frozen=True)
class _MockRegime:
    regime: str = "TRENDING"
    regime_confidence: float = 0.85
    volatility_state: str = "NORMAL"
    volatility_level: float = 0.5
    expansion_state: str = "EXPANDING"
    compression_bars: int = 0
    momentum_direction: str = "BEARISH"
    momentum_strength: float = 0.6


@dataclass(frozen=True)
class _MockLocation:
    location_type: str = "SUPPLY_ZONE"
    inside_institutional_zone: bool = True
    zone_quality: float = 0.8
    zone_mitigated: bool = False
    premium_discount: str = "PREMIUM"
    range_position: float = 0.75
    liquidity_above: bool = True
    liquidity_below: bool = False
    nearest_liquidity_direction: str = "ABOVE"
    nearest_liquidity_distance_pips: float = 15.0
    demand_zones_nearby: int = 1
    supply_zones_nearby: int = 2
    fvg_zones_nearby: int = 0


@dataclass(frozen=True)
class _MockHTFAlignment:
    macro_bias: str = "BEARISH"
    macro_bias_strength: float = 0.7
    structure_alignment: float = 0.8
    authority_timeframe: str = "H4"
    phase_alignment: str = "ALIGNED"


@dataclass(frozen=True)
class _MockMarketState:
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    schema_version: str = "v10_market_state_v1"
    confidence: float = 0.85
    observations: list = field(default_factory=list)
    h4: _MockH4 = field(default_factory=_MockH4)
    h1: _MockH1 = field(default_factory=_MockH1)
    m15: _MockM15 = field(default_factory=_MockM15)
    m5: _MockM5 = field(default_factory=_MockM5)
    regime: _MockRegime = field(default_factory=_MockRegime)
    location: _MockLocation = field(default_factory=_MockLocation)
    htf_alignment: _MockHTFAlignment = field(default_factory=_MockHTFAlignment)


@dataclass(frozen=True)
class _MockStrategy:
    opportunity_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    strategy_family: str = "TREND_CONTINUATION"
    directional_context: str = "BEARISH"
    strategy_confidence: float = 0.78
    reasoning: list = field(default_factory=lambda: ["H4 trend aligned"])
    supporting_conditions: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockMovement:
    minimum_expected_move: float = 15.0
    maximum_expected_move: float = 45.0
    measurement_unit: str = "pips"


@dataclass(frozen=True)
class _MockLifecycle:
    expected_duration_minutes: float = 120.0
    holding_style: str = "INTRADAY"


@dataclass(frozen=True)
class _MockHorizon:
    opportunity_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    horizon_type: str = "INTRADAY"
    movement_expectation: _MockMovement = field(default_factory=_MockMovement)
    trade_lifecycle: _MockLifecycle = field(default_factory=_MockLifecycle)
    supporting_factors: list = field(default_factory=list)
    reasoning: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockStopRef:
    price: float = 1.0895
    structure_source: str = "H1_SWING_HIGH"
    reasoning: str = "Above supply OB"


@dataclass(frozen=True)
class _MockTargetRef:
    price: float = 1.0820
    structure_source: str = "H1_SWING_LOW"
    reasoning: str = "Next demand zone"


@dataclass(frozen=True)
class _MockEntry:
    opportunity_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    trade_direction: str = "SHORT"
    entry_method: str = "MARKET"
    entry_status: str = "READY"
    entry_price: float = 1.0870
    entry_zone: str = ""
    stop_reference: _MockStopRef = field(default_factory=_MockStopRef)
    target_reference: _MockTargetRef = field(default_factory=_MockTargetRef)
    risk_distance: float = 0.0025
    reward_distance: float = 0.005
    expected_rr: float = 2.0
    reasoning: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockRiskProfile:
    risk_percentage: float = 1.0
    max_loss_amount: float = 100.0
    position_size: float = 0.04


@dataclass(frozen=True)
class _MockRisk:
    opportunity_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    approved: bool = True
    rejection_reason: str = ""
    risk_profile: _MockRiskProfile = field(default_factory=_MockRiskProfile)
    trade_geometry: object = None
    risk_checks: object = None
    reasoning: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockOrderDetails:
    symbol: str = "EURUSD"
    direction: str = "SHORT"
    order_type: str = "MARKET"
    volume: float = 0.04
    entry_price: float = 1.087
    stop_loss: float = 1.0895
    take_profit: float = 1.082


@dataclass(frozen=True)
class _MockExecution:
    opportunity_id: str = "abc123def4567890"
    symbol: str = "EURUSD"
    timestamp_utc: float = 1785400000.0
    approved: bool = True
    rejection_reason: str = ""
    order_details: _MockOrderDetails = field(default_factory=_MockOrderDetails)
    execution_checks: object = None
    protection: object = None
    reasoning: list = field(default_factory=list)


@dataclass(frozen=True)
class _MockAccount:
    balance: float = 10000.0
    equity: float = 10050.0
    margin_free: float = 9900.0
    leverage: int = 100
    open_positions: int = 1
    daily_loss_pct: float = 0.2

    @property
    def available(self) -> bool:
        return self.balance > 0


@dataclass(frozen=True)
class _MockBroker:
    connected: bool = True
    symbol_available: bool = True
    symbol: str = "EURUSD"
    spread: float = 0.00012
    tick_value: float = 10.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    stops_level: int = 0
    bid: float = 1.087
    ask: float = 1.0872
    market_open: bool = True

    @property
    def available(self) -> bool:
        return self.connected and self.symbol_available


@dataclass
class _MockPipelineResult:
    market_state: _MockMarketState = field(default_factory=_MockMarketState)
    opportunity: _MockOpportunity = field(default_factory=_MockOpportunity)
    strategy: _MockStrategy = field(default_factory=_MockStrategy)
    horizon: _MockHorizon = field(default_factory=_MockHorizon)
    entry: _MockEntry = field(default_factory=_MockEntry)
    risk: _MockRisk = field(default_factory=_MockRisk)
    execution: _MockExecution = field(default_factory=_MockExecution)
    decision_context: object = None
    account_snapshot: _MockAccount = field(default_factory=_MockAccount)
    broker_snapshot: _MockBroker = field(default_factory=_MockBroker)
    events: object = None

    @property
    def approved(self) -> bool:
        return self.execution.approved

    @property
    def rejection_stage(self) -> str:
        if not self.execution.approved:
            return "execution"
        return ""


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _engine_result_with_v10():
    """Simulate the dict returned by run_v10_cycle with a PipelineResult."""
    return {
        "action": "EXECUTE",
        "reason": "V10: TREND_CONTINUATION",
        "score": 0.72,
        "pattern": "TREND_CONTINUATION",
        "strategy": "TREND_CONTINUATION",
        "entity_id": "EURUSD_1785400000",
        "symbol": "EURUSD",
        "cycle_id": 42,
        "v10_pipeline_result": _MockPipelineResult(),
    }


def _engine_result_without_v10():
    """Simulate legacy engine result (no v10_pipeline_result)."""
    return {
        "action": "NO_TRADE",
        "reason": "score_below_threshold:0.28",
        "score": 0.28,
        "pattern": "ENGULFING",
        "entity_id": "EURUSD_1785400000",
        "symbol": "EURUSD",
        "cycle_id": 42,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CLASSES
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaVersion:
    def test_schema_is_v2(self):
        assert _SCHEMA_VERSION == "decision_trace_v2"

    def test_s3_prefix_is_decision_trace(self):
        assert _S3_PREFIX == "decision_trace"

    def test_s3_bucket_is_new_runtime_bucket(self):
        assert _S3_BUCKET == "trading-bot-v10-data"


class TestV1FieldsPreserved:
    """Existing decision_trace v1 fields must still be present."""

    def test_v1_identity_fields(self):
        trace = build_decision_trace(
            engine_result=_engine_result_without_v10(),
            runtime_session_id="test_session",
            pattern_count=1,
        )
        d = trace.to_dict()
        assert "entity_id" in d
        assert "symbol" in d
        assert "cycle_id" in d
        assert "timestamp_utc" in d
        assert "runtime_session_id" in d

    def test_v1_outcome_fields(self):
        trace = build_decision_trace(
            engine_result=_engine_result_without_v10(),
            runtime_session_id="test_session",
        )
        d = trace.to_dict()
        assert "action" in d
        assert "terminal_stage" in d
        assert "terminal_reason" in d
        assert "stages_reached" in d
        assert "stages_passed" in d

    def test_v1_scoring_fields(self):
        trace = build_decision_trace(
            engine_result=_engine_result_without_v10(),
            runtime_session_id="test_session",
        )
        d = trace.to_dict()
        assert "score_neutral" in d
        assert "score_strategy" in d
        assert "score_delta" in d
        assert "components" in d
        assert "weakest_component" in d
        assert "threshold_gap" in d

    def test_v1_works_without_v10(self):
        """Build trace without v10_pipeline_result — v1 compat."""
        trace = build_decision_trace(
            engine_result=_engine_result_without_v10(),
            runtime_session_id="test_session",
            pattern_count=1,
        )
        assert trace.action == "NO_TRADE"
        assert trace.symbol == "EURUSD"
        assert trace.engine_version == ""
        assert trace.v10_market_state == {}
        assert trace.v10_opportunity == {}


class TestV10FieldsPopulated:
    """V10 pipeline data must be extracted into trace when provided."""

    def test_engine_version_set(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        assert trace.engine_version == "V10"

    def test_observation_id_extracted(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        assert trace.observation_id == ""
        assert trace.v10_observation_id == "abc123def4567890"

    def test_correlation_id_generated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        assert trace.correlation_id == ""
        assert trace.v10_correlation_id.startswith("v10_EURUSD_")

    def test_market_state_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        ms = trace.v10_market_state
        assert ms["h4"]["trend"] == "BEARISH"
        assert ms["h1"]["bos_confirmed"] is True
        assert ms["m15"]["pullback_active"] is True
        assert ms["m5"]["momentum_direction"] == "BEARISH"
        assert ms["regime"]["regime"] == "TRENDING"
        assert ms["location"]["location_type"] == "SUPPLY_ZONE"
        assert ms["htf_alignment"]["macro_bias"] == "BEARISH"

    def test_opportunity_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        opp = trace.v10_opportunity
        assert opp["state"] == "VALID"
        assert opp["overall_quality"] == 0.72
        assert opp["location_score"] == 0.7
        assert opp["structure_score"] == 0.8
        assert len(opp["reasoning"]) == 2

    def test_strategy_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        s = trace.v10_strategy
        assert s["family"] == "TREND_CONTINUATION"
        assert s["confidence"] == 0.78
        assert s["direction"] == "BEARISH"

    def test_horizon_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        h = trace.v10_horizon
        assert h["type"] == "INTRADAY"
        assert h["min_move"] == 15.0
        assert h["max_move"] == 45.0
        assert h["unit"] == "pips"
        assert h["duration_minutes"] == 120.0

    def test_entry_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        e = trace.v10_entry
        assert e["method"] == "MARKET"
        assert e["status"] == "READY"
        assert e["direction"] == "SHORT"
        assert e["entry_price"] == 1.087
        assert e["stop_price"] == 1.0895
        assert e["target_price"] == 1.082
        assert e["expected_rr"] == 2.0

    def test_risk_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        r = trace.v10_risk
        assert r["approved"] is True
        assert r["position_size"] == 0.04
        assert r["risk_percentage"] == 1.0

    def test_execution_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        ex = trace.v10_execution
        assert ex["approved"] is True
        assert ex["order_type"] == "MARKET"
        assert ex["volume"] == 0.04

    def test_account_snapshot_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        a = trace.v10_account_snapshot
        assert a is not None
        assert a["balance"] == 10000.0
        assert a["equity"] == 10050.0

    def test_broker_snapshot_populated(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        b = trace.v10_broker_snapshot
        assert b is not None
        assert b["spread"] == 0.00012
        assert b["bid"] == 1.087

    def test_to_dict_is_json_serializable(self):
        trace = build_decision_trace(
            engine_result=_engine_result_with_v10(),
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        d = trace.to_dict()
        line = json.dumps(d, default=str)
        assert len(line) > 100
        parsed = json.loads(line)
        assert parsed["engine_version"] == "V10"
        assert parsed["v10_market_state"]["regime"]["regime"] == "TRENDING"


class TestNoV10PrefixWrites:
    """Confirm no code writes to v10/* S3 paths."""

    def test_s3_writer_deleted(self):
        """core/v10/s3_writer.py must not exist."""
        path = ROOT / "core" / "v10" / "s3_writer.py"
        assert not path.exists(), f"s3_writer.py still exists at {path}"

    def test_decision_persistence_deleted(self):
        """core/v10/decision_persistence.py must not exist."""
        path = ROOT / "core" / "v10" / "decision_persistence.py"
        assert not path.exists(), f"decision_persistence.py still exists at {path}"

    def test_no_v10_prefix_in_codebase(self):
        """No production .py file should reference v10/decisions or v10/events S3 paths."""
        forbidden = ("v10/decisions", "v10/events", "v10/executions", "v10/outcomes")
        violations = []
        for py_file in ROOT.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".hypothesis" in str(py_file):
                continue
            if "test_decision_trace_v2" in str(py_file):
                continue  # Skip this test file itself
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for pattern in forbidden:
                    if f'"{pattern}' in content or f"'{pattern}" in content:
                        violations.append(f"{py_file.relative_to(ROOT)}: contains {pattern}")
            except Exception:
                pass
        assert violations == [], f"v10/ prefix references found:\n" + "\n".join(violations)

    def test_no_logs_v10_references(self):
        """No production code should write to logs/v10_decisions or logs/v10_events."""
        forbidden = ("logs/v10_decisions", "logs/v10_events")
        violations = []
        for py_file in (ROOT / "core").rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for pattern in forbidden:
                    if pattern in content:
                        violations.append(f"{py_file.relative_to(ROOT)}: {pattern}")
            except Exception:
                pass
        assert violations == [], f"logs/v10 references found:\n" + "\n".join(violations)


class TestDecisionLedgerStillPersists:
    """persist_v10_full must still write to Decision Ledger."""

    def test_persist_v10_full_calls_write_to_ledger(self):
        """Verify persist_v10_full still invokes ledger write."""
        import inspect
        from core.v10.persistence_adapter import persist_v10_full
        source = inspect.getsource(persist_v10_full)
        assert "_write_to_ledger" in source
        assert "build_v10_ledger_entry" in source

    def test_persist_v10_full_does_not_call_upload_decision(self):
        """Verify persist_v10_full no longer calls S3 upload."""
        import inspect
        from core.v10.persistence_adapter import persist_v10_full
        source = inspect.getsource(persist_v10_full)
        assert "upload_decision" not in source
        assert "_write_v10_record" not in source
        assert "s3_writer" not in source


class TestExecutionPathUnchanged:
    """Execution orchestrator must not be affected by this migration."""

    def test_execution_orchestrator_has_no_v10_persistence(self):
        """ExecutionOrchestrator must not reference V10 persistence."""
        import inspect
        from execution.execution_orchestrator import ExecutionOrchestrator
        source = inspect.getsource(ExecutionOrchestrator)
        assert "s3_writer" not in source
        assert "v10_decisions" not in source
        assert "persist_v10" not in source

    def test_mt5_execution_has_no_v10_persistence(self):
        """MT5Execution must not reference V10 persistence."""
        import inspect
        from execution.mt5_execution import MT5Execution
        source = inspect.getsource(MT5Execution)
        assert "s3_writer" not in source
        assert "v10_decisions" not in source


class TestS3KeyFormat:
    """S3 key must use new schema-versioned partition."""

    def test_s3_key_contains_schema_version(self):
        """The S3 key must include schema_version=decision_trace_v2."""
        import inspect
        from core.decision_trace import _write_s3
        source = inspect.getsource(_write_s3)
        assert "schema_version=" in source
        assert "decision_trace_v2" in source or "_SCHEMA_VERSION" in source


class TestEntityIdPresent:
    """Every decision_trace_v2 record must contain entity_id."""

    def test_execute_has_entity_id(self):
        """EXECUTE decision must have non-empty entity_id."""
        result = _engine_result_with_v10()
        # Simulate the entity_id that scanner_adapter now provides
        result["entity_id"] = "EURUSD_1785400000"
        trace = build_decision_trace(
            engine_result=result,
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        assert trace.entity_id == "EURUSD_1785400000"
        assert trace.entity_id != ""

    def test_no_trade_has_entity_id(self):
        """NO_TRADE decision must have non-empty entity_id."""
        result = _engine_result_without_v10()
        result["entity_id"] = "EURUSD_1785400000"
        trace = build_decision_trace(
            engine_result=result,
            runtime_session_id="test_session",
        )
        assert trace.entity_id == "EURUSD_1785400000"
        assert trace.entity_id != ""

    def test_execute_has_both_ids(self):
        """EXECUTE decision must have both entity_id and correlation_id."""
        result = _engine_result_with_v10()
        result["entity_id"] = "EURUSD_1785400000"
        trace = build_decision_trace(
            engine_result=result,
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        # entity_id from engine_result
        assert trace.entity_id == "EURUSD_1785400000"
        # V10-local correlation remains diagnostic, not canonical runtime lineage.
        assert trace.correlation_id == ""
        assert trace.v10_correlation_id.startswith("v10_EURUSD_")

    def test_entity_id_format(self):
        """entity_id must be {symbol}_{bar_time} format."""
        result = _engine_result_with_v10()
        result["entity_id"] = "AUDUSD_1785763456"
        result["symbol"] = "AUDUSD"
        trace = build_decision_trace(
            engine_result=result,
            runtime_session_id="test_session",
            v10_pipeline_result=_MockPipelineResult(),
        )
        parts = trace.entity_id.split("_")
        assert parts[0] == "AUDUSD"
        assert parts[1].isdigit()
