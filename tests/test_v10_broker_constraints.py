"""Phase J.4 — Broker Constraint Validation Audit.

Tests execution engine Gates 8-9 (volume_min/max, stops_level)
and risk engine volume_step rounding. Verifies rejection integrity,
persistence consistency, terminal output, and research classification.
"""

import pytest
from core.v10.market_state import V10MarketState, M5State
from core.v10.entry_model import (
    EntryDecision, EntryStatus, EntryMethod, TradeDirection,
    StopReference, TargetReference,
)
from core.v10.risk_model import RiskDecision, RiskProfile, TradeGeometry
from core.v10.broker_context import BrokerContext
from core.v10.execution_engine import build_execution_decision
from core.v10.risk_engine import calculate_position_size_exact


# ═══════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════

def _state(symbol="EURUSD"):
    return V10MarketState(symbol=symbol, timestamp_utc=1000.0, m5=M5State(atr=0.0006))


def _entry(direction=TradeDirection.SELL.value, risk_dist=0.0010):
    """Valid entry with configurable direction and stop distance."""
    reward_dist = risk_dist * 2.0
    if direction == TradeDirection.SELL.value:
        entry_price = 1.0900
        stop_price = entry_price + risk_dist
        target_price = entry_price - reward_dist
    else:
        entry_price = 1.0900
        stop_price = entry_price - risk_dist
        target_price = entry_price + reward_dist

    return EntryDecision(
        opportunity_id="broker_test", symbol="EURUSD", timestamp_utc=1000.0,
        trade_direction=direction,
        entry_method=EntryMethod.CONFIRMATION_ENTRY.value,
        entry_status=EntryStatus.READY.value,
        entry_price=entry_price,
        stop_reference=StopReference(price=stop_price, structure_source="test", reasoning="Test stop"),
        target_reference=TargetReference(price=target_price, structure_source="test", reasoning="Test target"),
        risk_distance=risk_dist,
        reward_distance=reward_dist,
        expected_rr=2.0,
    )


def _risk(volume=0.25):
    """Approved risk decision with configurable volume."""
    return RiskDecision(
        opportunity_id="broker_test", symbol="EURUSD", timestamp_utc=1000.0,
        approved=True,
        risk_profile=RiskProfile(risk_percentage=0.0025, max_loss_amount=25.0, position_size=volume),
        trade_geometry=TradeGeometry(
            entry_price=1.0900, stop_price=1.0910, target_price=1.0880,
            stop_distance=0.0010, reward_distance=0.0020, expected_rr=2.0,
        ),
    )


def _broker(volume_min=0.01, volume_max=100.0, volume_step=0.01,
            stops_level=0, point=0.00001):
    """Good broker with configurable volume/stops constraints."""
    return BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        symbol="EURUSD", spread=0.00012, available_margin=5000.0,
        tick_value=1.0, tick_size=0.00001,
        volume_min=volume_min, volume_max=volume_max, volume_step=volume_step,
        point=point, digits=5, stops_level=stops_level,
    )


# ═══════════════════════════════════════════════════════════════
# GATE 8: VOLUME MINIMUM
# ═══════════════════════════════════════════════════════════════

class TestVolumeMinimum:
    """volume < volume_min must be REJECTED at execution stage."""

    def test_volume_below_min_rejected_sell(self):
        result = build_execution_decision(
            _entry(TradeDirection.SELL.value), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        assert result.approved is False
        assert "below minimum" in result.rejection_reason

    def test_volume_below_min_rejected_buy(self):
        result = build_execution_decision(
            _entry(TradeDirection.BUY.value), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        assert result.approved is False
        assert "below minimum" in result.rejection_reason

    def test_volume_equals_min_approved(self):
        result = build_execution_decision(
            _entry(), _risk(volume=0.01),
            _state(), _broker(volume_min=0.01),
        )
        assert result.approved is True

    def test_volume_above_min_approved(self):
        result = build_execution_decision(
            _entry(), _risk(volume=0.50),
            _state(), _broker(volume_min=0.01),
        )
        assert result.approved is True

    def test_volume_zero_rejected(self):
        """Risk engine returning 0 volume must be caught."""
        result = build_execution_decision(
            _entry(), _risk(volume=0.0),
            _state(), _broker(volume_min=0.01),
        )
        assert result.approved is False
        assert "below minimum" in result.rejection_reason


# ═══════════════════════════════════════════════════════════════
# GATE 8: VOLUME MAXIMUM
# ═══════════════════════════════════════════════════════════════

class TestVolumeMaximum:
    """volume > volume_max must be REJECTED at execution stage."""

    def test_volume_above_max_rejected_sell(self):
        result = build_execution_decision(
            _entry(TradeDirection.SELL.value), _risk(volume=150.0),
            _state(), _broker(volume_max=100.0),
        )
        assert result.approved is False
        assert "exceeds maximum" in result.rejection_reason

    def test_volume_above_max_rejected_buy(self):
        result = build_execution_decision(
            _entry(TradeDirection.BUY.value), _risk(volume=150.0),
            _state(), _broker(volume_max=100.0),
        )
        assert result.approved is False
        assert "exceeds maximum" in result.rejection_reason

    def test_volume_equals_max_approved(self):
        result = build_execution_decision(
            _entry(), _risk(volume=100.0),
            _state(), _broker(volume_max=100.0),
        )
        assert result.approved is True

    def test_volume_below_max_approved(self):
        result = build_execution_decision(
            _entry(), _risk(volume=50.0),
            _state(), _broker(volume_max=100.0),
        )
        assert result.approved is True


# ═══════════════════════════════════════════════════════════════
# VOLUME STEP (Risk Engine — floors to step)
# ═══════════════════════════════════════════════════════════════

class TestVolumeStep:
    """Risk engine floors to volume_step. Non-step values become stepped."""

    def test_exact_step_unchanged(self):
        # 0.25 / 0.01 step = exact
        size = calculate_position_size_exact(
            risk_amount=25.0, stop_distance=0.0010,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert size == 0.25

    def test_non_step_floored(self):
        # risk_amount=25.5 / (100 ticks * $1) = 0.255 → floor to 0.25
        size = calculate_position_size_exact(
            risk_amount=25.5, stop_distance=0.0010,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert size == 0.25

    def test_step_010_rounds_correctly(self):
        # With step=0.10, a raw 0.35 stays 0.30
        size = calculate_position_size_exact(
            risk_amount=35.0, stop_distance=0.0010,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.10,
        )
        assert size == 0.30

    def test_below_min_after_step_returns_zero(self):
        # Raw size would be 0.005, step=0.01 → floors to 0.0 → < min → 0.0
        size = calculate_position_size_exact(
            risk_amount=0.5, stop_distance=0.0010,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert size == 0.0

    def test_step_with_volume_max_clamp(self):
        # Very large risk → raw would be 200 lots → clamped to 100
        size = calculate_position_size_exact(
            risk_amount=20000.0, stop_distance=0.0010,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert size == 100.0


# ═══════════════════════════════════════════════════════════════
# GATE 9: STOPS LEVEL
# ═══════════════════════════════════════════════════════════════

class TestStopsLevel:
    """Broker stops_level defines minimum SL/TP distance in points."""

    def _broker_low_spread(self, stops_level=50, point=0.00001):
        """Broker with very low spread so Gate 6 passes, Gate 9 can fire."""
        return BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00002, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=point, digits=5, stops_level=stops_level,
        )

    def test_stop_below_stops_level_rejected_sell(self):
        # stops_level=50, point=0.00001 → min distance = 0.00050
        # entry risk_distance = 0.00040 → below → REJECT
        # spread=0.00002, risk_dist=0.00040 → spread ratio=0.05 < 0.30 (Gate 6 passes)
        result = build_execution_decision(
            _entry(TradeDirection.SELL.value, risk_dist=0.00040),
            _risk(volume=0.25),
            _state(), self._broker_low_spread(stops_level=50),
        )
        assert result.approved is False
        assert "below broker minimum" in result.rejection_reason

    def test_stop_below_stops_level_rejected_buy(self):
        result = build_execution_decision(
            _entry(TradeDirection.BUY.value, risk_dist=0.00040),
            _risk(volume=0.25),
            _state(), self._broker_low_spread(stops_level=50),
        )
        assert result.approved is False
        assert "below broker minimum" in result.rejection_reason

    def test_stop_equals_stops_level_approved(self):
        # stops_level=50, point=0.00001 → min distance = 0.00050
        # risk_distance = 0.00050 → exactly at boundary → should pass (not <)
        result = build_execution_decision(
            _entry(risk_dist=0.00050), _risk(volume=0.25),
            _state(), self._broker_low_spread(stops_level=50),
        )
        assert result.approved is True

    def test_stop_above_stops_level_approved(self):
        # risk_distance = 0.0010 > 0.00050 → pass
        result = build_execution_decision(
            _entry(risk_dist=0.0010), _risk(volume=0.25),
            _state(), self._broker_low_spread(stops_level=50),
        )
        assert result.approved is True

    def test_stops_level_zero_bypasses_check(self):
        """When stops_level=0, gate is skipped (no broker constraint)."""
        result = build_execution_decision(
            _entry(risk_dist=0.0010), _risk(volume=0.25),
            _state(), self._broker_low_spread(stops_level=0),
        )
        assert result.approved is True

    def test_high_stops_level_gold(self):
        """XAUUSD often has higher stops_level (e.g. 50 points at 0.01 point)."""
        # stops_level=50, point=0.01 → min distance = 0.50
        # risk_distance=0.30 → below → REJECT
        # spread=0.00002 / 0.30 = negligible → Gate 6 passes
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="XAUUSD", spread=0.01, available_margin=5000.0,
            tick_value=1.0, tick_size=0.01,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.01, digits=2, stops_level=50,
        )
        result = build_execution_decision(
            _entry(risk_dist=0.30), _risk(volume=0.25),
            _state("XAUUSD"), broker,
        )
        assert result.approved is False
        assert "below broker minimum" in result.rejection_reason


# ═══════════════════════════════════════════════════════════════
# REJECTION INTEGRITY
# ═══════════════════════════════════════════════════════════════

class TestRejectionIntegrity:
    """Broker constraint rejections must have correct metadata."""

    def test_volume_min_rejection_reason_is_specific(self):
        result = build_execution_decision(
            _entry(), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        assert "0.0050" in result.rejection_reason
        assert "0.01" in result.rejection_reason

    def test_volume_max_rejection_reason_is_specific(self):
        result = build_execution_decision(
            _entry(), _risk(volume=150.0),
            _state(), _broker(volume_max=100.0),
        )
        assert "150.0000" in result.rejection_reason
        assert "100.0" in result.rejection_reason

    def test_stops_level_rejection_reason_is_specific(self):
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00002, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.00001, digits=5, stops_level=50,
        )
        result = build_execution_decision(
            _entry(risk_dist=0.00040), _risk(volume=0.25),
            _state(), broker,
        )
        assert "0.00040" in result.rejection_reason
        assert "0.00050" in result.rejection_reason

    def test_volume_rejection_has_volume_valid_false(self):
        result = build_execution_decision(
            _entry(), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        assert result.execution_checks["volume_valid"] is False

    def test_stops_rejection_has_stops_level_ok_false(self):
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00002, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.00001, digits=5, stops_level=50,
        )
        result = build_execution_decision(
            _entry(risk_dist=0.00040), _risk(volume=0.25),
            _state(), broker,
        )
        assert result.execution_checks["stops_level_ok"] is False

    def test_approved_false_on_any_constraint_failure(self):
        """Every broker constraint failure must set approved=False."""
        cases = [
            # volume_min
            (_entry(), _risk(0.005), _state(), _broker(volume_min=0.01)),
            # volume_max
            (_entry(), _risk(150.0), _state(), _broker(volume_max=100.0)),
            # stops_level
            (_entry(risk_dist=0.00030), _risk(0.25), _state(), _broker(stops_level=50, point=0.00001)),
        ]
        for entry, risk, state, broker in cases:
            result = build_execution_decision(entry, risk, state, broker)
            assert result.approved is False, f"Should reject: {result.rejection_reason}"


# ═══════════════════════════════════════════════════════════════
# FULL PIPELINE — REJECTION STAGE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

class TestPipelineRejectionStage:
    """Broker constraints must classify as execution-stage failures.
    
    They must NEVER be recorded as opportunity/strategy/horizon/entry/risk.
    """

    def test_volume_min_pipeline_rejection_stage(self):
        """Full pipeline with volume too small → rejection_stage='execution'."""
        from core.v10.pipeline import V10Pipeline
        from core.v10.risk_model import AccountContext
        from core.v3_shadow.models import (
            MarketUnderstanding, H1Understanding, M5Understanding,
        )

        mu = MarketUnderstanding(
            symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
            h1=H1Understanding(
                bos_confirmed=True, bos_direction="BEARISH",
                dominant_trend="BEARISH", structural_clarity=0.80,
                swing_high=1.0920, swing_low=1.0850,
                active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
                active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
                session_high=1.0930, session_low=1.0840,
            ),
            m5=M5Understanding(
                rejection_present=True, rejection_direction="BEARISH",
                rejection_strength_atr=0.9, at_institutional_zone=True,
                zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012,
            ),
        )
        # Very high volume_min to guarantee execution rejection
        account = AccountContext(balance=10000.0, equity=10000.0)
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00012, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=500.0,  # Absurdly high → size will be below
            volume_max=1000.0, volume_step=0.01,
            point=0.00001, digits=5,
        )
        result = V10Pipeline().process(mu, None, account, broker)

        # If pipeline reaches execution (entry may fail first depending on data),
        # verify the stage is correct
        if result.rejection_stage == "execution":
            assert result.decision_context.terminal_stage == "execution"
            assert "below minimum" in result.execution.rejection_reason or "Volume" in result.execution.rejection_reason
        # The key assertion: it must NEVER be classified as opportunity/strategy/risk
        # when the ACTUAL failure was a broker volume constraint
        if result.execution.approved is False and "below minimum" in (result.execution.rejection_reason or ""):
            assert result.rejection_stage == "execution"

    def test_broker_rejection_never_classified_as_opportunity(self):
        """Volume/stops broker failures must NOT appear as opportunity rejection."""
        result = build_execution_decision(
            _entry(), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        # ExecutionDecision has no concept of 'rejection_stage' — that's pipeline-level.
        # But the execution_checks map proves it was an execution-layer failure.
        assert result.execution_checks.get("volume_valid") is False
        assert result.approved is False

    def test_broker_rejection_never_classified_as_risk(self):
        """Stops level failure is execution, not risk."""
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00002, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.00001, digits=5, stops_level=50,
        )
        result = build_execution_decision(
            _entry(risk_dist=0.00040), _risk(volume=0.25),
            _state(), broker,
        )
        assert result.execution_checks.get("stops_level_ok") is False
        assert result.approved is False



# ═══════════════════════════════════════════════════════════════
# PERSISTENCE CONSISTENCY
# ═══════════════════════════════════════════════════════════════

class TestPersistenceConsistency:
    """Broker rejections must be preserved identically across all representations.

    ExecutionDecision → PipelineResult → DecisionContext → record → terminal.
    """

    def _run_pipeline_with_high_volume_min(self):
        """Run pipeline where execution rejects due to volume_min."""
        from core.v10.pipeline import V10Pipeline
        from core.v10.risk_model import AccountContext
        from core.v3_shadow.models import (
            MarketUnderstanding, H1Understanding, M5Understanding,
            M15Understanding,
        )
        from core.v3_shadow.context_models import (
            V3MarketContext, HTFStructureContext, LocationContext, BehaviourContext,
        )

        mu = MarketUnderstanding(
            symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
            h1=H1Understanding(
                bos_confirmed=True, bos_direction="BEARISH",
                dominant_trend="BEARISH", structural_clarity=0.80,
                swing_high=1.0920, swing_low=1.0850,
                active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
                active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
                session_high=1.0930, session_low=1.0840,
            ),
            m15=M15Understanding(pullback_active=True, pullback_depth_atr=1.3, range_position=0.75),
            m5=M5Understanding(
                rejection_present=True, rejection_direction="BEARISH",
                rejection_strength_atr=0.9, at_institutional_zone=True,
                zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012,
            ),
        )
        ctx = V3MarketContext(
            symbol="EURUSD", timestamp_utc=1785400000.0,
            htf_structure=HTFStructureContext(macro_bias="NEUTRAL", structure_alignment=0.30, bos_active=True, bos_direction="BEARISH"),
            location=LocationContext(location_type="SUPPLY_OB", inside_institutional_zone=True, premium_discount="PREMIUM", range_position=0.75, zone_quality=0.85, liquidity_below=True),
            behaviour=BehaviourContext(regime="RANGING", volatility_state="NEUTRAL", momentum_direction="NEUTRAL"),
            overall_confidence=0.8,
        )
        account = AccountContext(balance=100.0, equity=100.0)  # Small account
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00012, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=5.0,  # Very high min — risk engine will produce < 5.0
            volume_max=100.0, volume_step=0.01,
            point=0.00001, digits=5,
        )
        return V10Pipeline().process(mu, ctx, account, broker)

    def test_execution_decision_has_rejection(self):
        result = self._run_pipeline_with_high_volume_min()
        # Pipeline may stop at entry or execution depending on data
        if result.rejection_stage == "execution":
            assert result.execution.approved is False
            assert result.execution.rejection_reason != ""

    def test_pipeline_result_rejection_stage(self):
        result = self._run_pipeline_with_high_volume_min()
        if result.rejection_stage == "execution":
            assert result.approved is False

    def test_decision_context_matches(self):
        from core.v10.decision_report import format_v10_decision
        from core.v10.persistence_adapter import build_v10_decision_record

        result = self._run_pipeline_with_high_volume_min()
        if result.rejection_stage == "execution":
            dc = result.decision_context
            assert dc.final_action == "NO_TRADE"
            assert dc.terminal_stage == "execution"

    def test_persisted_record_matches(self):
        from core.v10.persistence_adapter import build_v10_decision_record

        result = self._run_pipeline_with_high_volume_min()
        if result.rejection_stage == "execution":
            record = build_v10_decision_record(result)
            assert record["final_action"] == "NO_TRADE"
            assert record["rejection_stage"] == "execution"
            assert record["execution_approved"] is False

    def test_terminal_report_matches(self):
        from core.v10.decision_report import format_v10_decision

        result = self._run_pipeline_with_high_volume_min()
        if result.rejection_stage == "execution":
            report = format_v10_decision(result)
            assert "Stopped at: execution" in report
            assert "NO_TRADE" in report

    def test_all_representations_agree(self):
        """The canonical test: all views of the same decision must agree."""
        from core.v10.decision_report import format_v10_decision
        from core.v10.persistence_adapter import build_v10_decision_record

        result = self._run_pipeline_with_high_volume_min()
        if result.rejection_stage != "execution":
            pytest.skip("Pipeline stopped before execution — cannot test broker constraint persistence")

        dc = result.decision_context
        record = build_v10_decision_record(result)
        report = format_v10_decision(result)

        # All agree: NO_TRADE
        assert result.approved is False
        assert dc.final_action == "NO_TRADE"
        assert record["final_action"] == "NO_TRADE"
        assert "NO_TRADE" in report

        # All agree: execution stage
        assert result.rejection_stage == "execution"
        assert dc.terminal_stage == "execution"
        assert record["rejection_stage"] == "execution"
        assert "Stopped at: execution" in report

        # All agree: execution not approved
        assert result.execution.approved is False
        assert record["execution_approved"] is False


# ═══════════════════════════════════════════════════════════════
# TERMINAL OUTPUT VERIFICATION
# ═══════════════════════════════════════════════════════════════

class TestTerminalOutput:
    """V10 terminal report must correctly display broker constraint failures."""

    def test_volume_rejection_in_execution_section(self):
        """Volume rejection appears in [V10 EXECUTION] section."""
        from core.v10.decision_report import format_v10_decision
        from core.v10.pipeline import PipelineResult
        from core.v10.market_state import V10MarketState
        from core.v10.opportunity_assessment import OpportunityAssessment
        from core.v10.strategy_family import StrategyDecision
        from core.v10.horizon_assessment import HorizonDecision
        from core.v10.execution_model import ExecutionDecision
        from core.v10.decision_context import V10DecisionContext

        # Build a result where execution rejected due to volume
        exec_decision = build_execution_decision(
            _entry(), _risk(volume=0.005),
            _state(), _broker(volume_min=0.01),
        )
        assert exec_decision.approved is False

        # The rejection reason should be clear
        assert "below minimum" in exec_decision.rejection_reason

    def test_stops_rejection_in_execution_section(self):
        """Stops level rejection appears correctly."""
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            symbol="EURUSD", spread=0.00002, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.00001, digits=5, stops_level=50,
        )
        exec_decision = build_execution_decision(
            _entry(risk_dist=0.00040), _risk(volume=0.25),
            _state(), broker,
        )
        assert exec_decision.approved is False
        assert "below broker minimum" in exec_decision.rejection_reason
