"""Tests for V10 Risk Engine."""

import pytest
from core.v10.market_state import V10MarketState, H1State, M5State
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType
from core.v10.entry_model import (
    EntryDecision, EntryStatus, TradeDirection, EntryMethod,
    StopReference, TargetReference,
)
from core.v10.risk_model import AccountContext, RiskDecision
from core.v10.risk_engine import assess_risk


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _state(symbol="EURUSD"):
    return V10MarketState(symbol=symbol, timestamp_utc=1000.0, m5=M5State(atr=0.0006))

def _opp():
    return OpportunityAssessment(
        observation_id="risk_test", symbol="EURUSD", timestamp_utc=1000.0,
        opportunity_state="VALID", directional_bias="BEARISH",
    )

def _strat(family=StrategyFamily.MEAN_REVERSION.value):
    return StrategyDecision(
        opportunity_id="risk_test", symbol="EURUSD", timestamp_utc=1000.0,
        strategy_family=family, strategy_confidence=0.7,
    )

def _horizon(h=HorizonType.SCALP.value):
    return HorizonDecision(opportunity_id="risk_test", symbol="EURUSD", timestamp_utc=1000.0, horizon_type=h)

def _valid_entry(rr=2.0, risk_dist=0.0010):
    reward_dist = risk_dist * rr
    return EntryDecision(
        opportunity_id="risk_test", symbol="EURUSD", timestamp_utc=1000.0,
        trade_direction=TradeDirection.SELL.value,
        entry_method=EntryMethod.LIMIT_ENTRY.value,
        entry_status=EntryStatus.READY.value,
        entry_price=1.0900,
        stop_reference=StopReference(price=1.0900 + risk_dist, structure_source="above_supply", reasoning="Above supply OB"),
        target_reference=TargetReference(price=1.0900 - reward_dist, structure_source="H1_demand", reasoning="Next demand"),
        risk_distance=risk_dist,
        reward_distance=reward_dist,
        expected_rr=rr,
    )

def _account(**kwargs):
    defaults = dict(balance=10000.0, equity=10000.0, current_open_risk_pct=0.0,
                    open_positions=0, daily_loss_pct=0.0, symbols_with_positions=[])
    defaults.update(kwargs)
    return AccountContext(**defaults)

def _broker():
    """Broker context with EURUSD tick metadata for exact sizing."""
    from core.v10.broker_context import BrokerContext
    return BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        symbol="EURUSD", spread=0.00012, available_margin=5000.0,
        tick_value=1.0, tick_size=0.00001,  # $1 per tick per lot (EURUSD standard)
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        point=0.00001, digits=5,
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestApproval:
    def test_valid_trade_approved(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account())
        assert result.approved is True

    def test_approved_has_position_size(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account(), _broker())
        assert result.risk_profile.position_size > 0

    def test_approved_has_geometry(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account(), _broker())
        assert result.trade_geometry.stop_distance > 0
        assert result.trade_geometry.expected_rr >= 1.5


class TestRejection:
    def test_invalid_entry_rejected(self):
        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            entry_status=EntryStatus.INVALID.value,
        )
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account())
        assert result.approved is False
        assert "INVALID" in result.rejection_reason

    def test_missing_stop_rejected(self):
        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            trade_direction=TradeDirection.SELL.value,
            entry_status=EntryStatus.READY.value,
            entry_price=1.0900, risk_distance=0,
            stop_reference=StopReference(price=0),
        )
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account())
        assert result.approved is False

    def test_zero_stop_distance_rejected(self):
        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            trade_direction=TradeDirection.SELL.value,
            entry_status=EntryStatus.READY.value,
            entry_price=1.0900, risk_distance=0,
            stop_reference=StopReference(price=1.0900),
        )
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account())
        assert result.approved is False

    def test_low_rr_rejected(self):
        entry = _valid_entry(rr=1.0)  # Below 1.5 minimum
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account())
        assert result.approved is False
        assert "R:R" in result.rejection_reason


class TestPositionSizing:
    def test_size_calculated_correctly(self):
        # 0.25% of 10000 = $25 risk. Stop = 10 pips = 100 ticks. tick_value=$1 → 0.25 lots
        entry = _valid_entry(rr=2.0, risk_dist=0.0010)  # 10 pips
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account(), _broker())
        assert result.approved is True
        assert abs(result.risk_profile.position_size - 0.25) < 0.01

    def test_wider_stop_reduces_size(self):
        entry_tight = _valid_entry(rr=2.0, risk_dist=0.0010)  # 10 pips
        entry_wide = _valid_entry(rr=2.0, risk_dist=0.0020)   # 20 pips
        r_tight = assess_risk(_state(), _opp(), _strat(), _horizon(), entry_tight, _account(), _broker())
        r_wide = assess_risk(_state(), _opp(), _strat(), _horizon(), entry_wide, _account(), _broker())
        assert r_tight.approved and r_wide.approved
        assert r_wide.risk_profile.position_size < r_tight.risk_profile.position_size


class TestExposureLimits:
    def test_daily_loss_limit_blocks(self):
        acct = _account(daily_loss_pct=0.05)  # 5% > 4% limit
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), acct)
        assert result.approved is False
        assert "Daily loss" in result.rejection_reason

    def test_max_positions_blocks(self):
        acct = _account(open_positions=3)  # At limit
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), acct)
        assert result.approved is False
        assert "positions" in result.rejection_reason.lower()

    def test_total_exposure_blocks(self):
        acct = _account(current_open_risk_pct=0.035)  # 3.5% > 3% limit
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), acct)
        assert result.approved is False


class TestStrategyAwareness:
    def test_breakout_requires_higher_rr(self):
        # Breakout min RR = 2.0 (entry has 1.8 → rejected)
        entry = _valid_entry(rr=1.8)
        strat = _strat(StrategyFamily.BREAKOUT_EXPANSION.value)
        result = assess_risk(_state(), _opp(), strat, _horizon(), entry, _account())
        assert result.approved is False

    def test_breakout_approved_at_higher_rr(self):
        entry = _valid_entry(rr=2.5)
        strat = _strat(StrategyFamily.BREAKOUT_EXPANSION.value)
        result = assess_risk(_state(), _opp(), strat, _horizon(), entry, _account())
        assert result.approved is True


class TestHorizonAwareness:
    def test_extended_reduces_position_size(self):
        entry = _valid_entry(rr=2.0)
        r_scalp = assess_risk(_state(), _opp(), _strat(), _horizon(HorizonType.SCALP.value), entry, _account(), _broker())
        r_ext = assess_risk(_state(), _opp(), _strat(), _horizon(HorizonType.EXTENDED.value), entry, _account(), _broker())
        assert r_scalp.approved and r_ext.approved
        assert r_ext.risk_profile.position_size < r_scalp.risk_profile.position_size


class TestIntegrity:
    def test_risk_cannot_change_direction(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account())
        # RiskDecision has no trade_direction field to modify
        assert "trade_direction" not in result.to_dict()

    def test_risk_cannot_override_invalid_opportunity(self):
        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            entry_status=EntryStatus.INVALID.value,
        )
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), entry, _account())
        assert result.approved is False

    def test_immutable(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account())
        with pytest.raises(Exception):
            result.approved = False  # type: ignore

    def test_to_dict_structure(self):
        result = assess_risk(_state(), _opp(), _strat(), _horizon(), _valid_entry(), _account())
        d = result.to_dict()
        assert "approved" in d
        assert "risk_profile" in d
        assert "trade_geometry" in d
        assert "risk_checks" in d
