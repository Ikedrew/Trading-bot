"""Tests for V10 Execution Engine."""

import pytest
from core.v10.market_state import V10MarketState, M5State
from core.v10.entry_model import (
    EntryDecision, EntryStatus, EntryMethod, TradeDirection,
    StopReference, TargetReference,
)
from core.v10.risk_model import RiskDecision, RiskProfile, TradeGeometry
from core.v10.broker_context import BrokerContext
from core.v10.execution_model import ExecutionType
from core.v10.execution_engine import build_execution_decision


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _state(symbol="EURUSD"):
    return V10MarketState(symbol=symbol, timestamp_utc=1000.0, m5=M5State(atr=0.0006))

def _valid_entry(method=EntryMethod.CONFIRMATION_ENTRY.value):
    return EntryDecision(
        opportunity_id="exec_test", symbol="EURUSD", timestamp_utc=1000.0,
        trade_direction=TradeDirection.SELL.value,
        entry_method=method,
        entry_status=EntryStatus.READY.value,
        entry_price=1.0900,
        stop_reference=StopReference(price=1.0910, structure_source="above_supply", reasoning="Above supply"),
        target_reference=TargetReference(price=1.0880, structure_source="H1_demand", reasoning="Demand zone"),
        risk_distance=0.0010,
        reward_distance=0.0020,
        expected_rr=2.0,
    )

def _approved_risk():
    return RiskDecision(
        opportunity_id="exec_test", symbol="EURUSD", timestamp_utc=1000.0,
        approved=True,
        risk_profile=RiskProfile(risk_percentage=0.0025, max_loss_amount=25.0, position_size=0.25),
        trade_geometry=TradeGeometry(
            entry_price=1.0900, stop_price=1.0910, target_price=1.0880,
            stop_distance=0.0010, reward_distance=0.0020, expected_rr=2.0,
        ),
    )

def _rejected_risk():
    return RiskDecision(
        opportunity_id="exec_test", symbol="EURUSD", timestamp_utc=1000.0,
        approved=False, rejection_reason="Daily loss exceeded",
    )

def _good_broker():
    return BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        spread=0.00012, available_margin=5000.0,
    )


# ═══════════════════════════════════════════════════════════════
# TESTS: APPROVAL
# ═══════════════════════════════════════════════════════════════

class TestApproval:
    def test_all_conditions_met_approved(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.approved is True

    def test_approved_has_order_details(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.order_details.symbol == "EURUSD"
        assert result.order_details.direction == TradeDirection.SELL.value
        assert result.order_details.volume == 0.25
        assert result.order_details.stop_loss == 1.0910
        assert result.order_details.take_profit == 1.0880


# ═══════════════════════════════════════════════════════════════
# TESTS: REJECTION
# ═══════════════════════════════════════════════════════════════

class TestRejection:
    def test_rejected_risk_blocks(self):
        result = build_execution_decision(_valid_entry(), _rejected_risk(), _state(), _good_broker())
        assert result.approved is False
        assert "Risk rejected" in result.rejection_reason

    def test_invalid_entry_blocks(self):
        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            entry_status=EntryStatus.WAITING.value,
            trade_direction=TradeDirection.SELL.value,
            risk_distance=0.0010,
        )
        result = build_execution_decision(entry, _approved_risk(), _state(), _good_broker())
        assert result.approved is False
        assert "not ready" in result.rejection_reason.lower()

    def test_high_spread_blocks(self):
        # Spread = 0.0005, stop distance = 0.0010 → ratio = 50% > 30%
        broker = BrokerContext(connected=True, symbol_available=True, market_open=True,
                               spread=0.0005, available_margin=5000.0)
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), broker)
        assert result.approved is False
        assert "Spread" in result.rejection_reason

    def test_disconnected_broker_blocks(self):
        broker = BrokerContext(connected=False, symbol_available=True, market_open=True, spread=0.0001, available_margin=5000.0)
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), broker)
        assert result.approved is False
        assert "disconnected" in result.rejection_reason.lower()

    def test_unavailable_symbol_blocks(self):
        broker = BrokerContext(connected=True, symbol_available=False, market_open=True, spread=0.0001, available_margin=5000.0)
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), broker)
        assert result.approved is False
        assert "not available" in result.rejection_reason.lower()

    def test_market_closed_blocks(self):
        broker = BrokerContext(connected=True, symbol_available=True, market_open=False, spread=0.0001, available_margin=5000.0)
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), broker)
        assert result.approved is False
        assert "closed" in result.rejection_reason.lower()


# ═══════════════════════════════════════════════════════════════
# TESTS: ORDER TYPE MAPPING
# ═══════════════════════════════════════════════════════════════

class TestOrderMapping:
    def test_limit_entry_creates_limit_order(self):
        entry = _valid_entry(method=EntryMethod.LIMIT_ENTRY.value)
        result = build_execution_decision(entry, _approved_risk(), _state(), _good_broker())
        assert result.order_details.order_type == ExecutionType.LIMIT.value

    def test_confirmation_entry_creates_market_order(self):
        entry = _valid_entry(method=EntryMethod.CONFIRMATION_ENTRY.value)
        result = build_execution_decision(entry, _approved_risk(), _state(), _good_broker())
        assert result.order_details.order_type == ExecutionType.MARKET.value

    def test_break_entry_creates_stop_order(self):
        entry = _valid_entry(method=EntryMethod.BREAK_ENTRY.value)
        result = build_execution_decision(entry, _approved_risk(), _state(), _good_broker())
        assert result.order_details.order_type == ExecutionType.STOP.value


# ═══════════════════════════════════════════════════════════════
# TESTS: INTEGRITY
# ═══════════════════════════════════════════════════════════════

class TestIntegrity:
    def test_cannot_change_direction(self):
        """Execution preserves direction from entry."""
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.order_details.direction == TradeDirection.SELL.value

    def test_cannot_change_stop(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.order_details.stop_loss == 1.0910

    def test_cannot_change_target(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.order_details.take_profit == 1.0880

    def test_slippage_protection_set(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.protection.max_slippage_price > 0

    def test_timeout_protection_set(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        assert result.protection.timeout_seconds > 0

    def test_immutable(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        with pytest.raises(Exception):
            result.approved = False  # type: ignore

    def test_to_dict_complete(self):
        result = build_execution_decision(_valid_entry(), _approved_risk(), _state(), _good_broker())
        d = result.to_dict()
        assert "approved" in d
        assert "order_details" in d
        assert "execution_checks" in d
        assert "protection" in d
