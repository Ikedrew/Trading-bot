"""Tests for V10 Runtime Metadata — expanded AccountContext + BrokerContext."""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.risk_engine import calculate_position_size_exact
from core.runtime.account_provider import get_account_context, get_broker_context


class TestAccountContextModel:
    def test_defaults_to_unavailable(self):
        ctx = AccountContext()
        assert ctx.available is False
        assert ctx.balance == 0.0
        assert ctx.equity == 0.0
        assert ctx.leverage == 0

    def test_populated_is_available(self):
        ctx = AccountContext(balance=10000.0, equity=9800.0, leverage=100)
        assert ctx.available is True

    def test_no_hardcoded_balance(self):
        """Default balance must be 0, not 10000 or any other fictitious value."""
        ctx = AccountContext()
        assert ctx.balance == 0.0

    def test_expanded_fields_exist(self):
        ctx = AccountContext(
            login=12345, server="Pepperstone-Live", currency="USD",
            leverage=100, margin_mode=2,
            balance=25000.0, equity=24500.0, credit=0.0,
            profit=-500.0, margin=1200.0, margin_free=23300.0,
            margin_level=2041.67, stop_out_level=50.0,
        )
        assert ctx.login == 12345
        assert ctx.currency == "USD"
        assert ctx.leverage == 100
        assert ctx.profit == -500.0
        assert ctx.margin_free == 23300.0


class TestBrokerContextModel:
    def test_defaults_to_unavailable(self):
        ctx = BrokerContext()
        assert ctx.available is False
        assert ctx.connected is False
        assert ctx.tick_value == 0.0

    def test_populated_is_available(self):
        ctx = BrokerContext(connected=True, symbol_available=True, symbol="EURUSD")
        assert ctx.available is True

    def test_symbol_metadata_fields(self):
        ctx = BrokerContext(
            connected=True, symbol_available=True, symbol="EURUSD",
            market_open=True, digits=5, point=0.00001,
            contract_size=100000.0, tick_size=0.00001, tick_value=1.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            stops_level=10, freeze_level=5,
        )
        assert ctx.digits == 5
        assert ctx.point == 0.00001
        assert ctx.tick_value == 1.0
        assert ctx.volume_min == 0.01
        assert ctx.volume_max == 100.0
        assert ctx.stops_level == 10

    def test_pricing_fields(self):
        ctx = BrokerContext(bid=1.09000, ask=1.09012, spread=0.00012)
        assert ctx.bid == 1.09000
        assert ctx.ask == 1.09012

    def test_to_dict_includes_all(self):
        ctx = BrokerContext(
            connected=True, symbol="EURUSD", tick_value=1.0,
            volume_min=0.01, stops_level=10,
        )
        d = ctx.to_dict()
        assert "tick_value" in d
        assert "volume_min" in d
        assert "stops_level" in d


class TestExactPositionSizing:
    def test_fx_sizing_with_tick_value(self):
        # EURUSD: tick_value=$1/lot for 0.00001 tick
        # Stop = 10 pips = 100 ticks (at 0.00001 tick_size)
        # Risk = $25
        # Size = $25 / (100 × $1) = 0.25 lots
        size = calculate_position_size_exact(
            risk_amount=25.0,
            stop_distance=0.0010,  # 10 pips
            tick_value=1.0,        # $1 per tick per lot
            tick_size=0.00001,     # 1 point
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        assert size == 0.25

    def test_index_sizing_with_tick_value(self):
        # NAS100: tick_value=$0.10/lot for 0.1 tick_size
        # Stop = 15 points = 150 ticks
        # Risk = $25
        # Size = $25 / (150 × $0.10) = 1.6666... → rounded to step
        size = calculate_position_size_exact(
            risk_amount=25.0,
            stop_distance=15.0,
            tick_value=0.10,
            tick_size=0.1,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        assert size == 1.66  # Rounded down to 0.01 step

    def test_below_volume_min_returns_zero(self):
        # Risk too small for minimum lot
        size = calculate_position_size_exact(
            risk_amount=0.50,
            stop_distance=0.0050,
            tick_value=1.0,
            tick_size=0.00001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        assert size == 0.0  # 0.001 lots < min 0.01

    def test_above_volume_max_clamped(self):
        size = calculate_position_size_exact(
            risk_amount=100000.0,
            stop_distance=0.0001,
            tick_value=1.0,
            tick_size=0.00001,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
        )
        assert size == 50.0  # Clamped to max

    def test_zero_tick_value_returns_zero(self):
        size = calculate_position_size_exact(
            risk_amount=25.0, stop_distance=0.001,
            tick_value=0.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert size == 0.0

    def test_different_instruments_different_sizes(self):
        # Same risk, different tick_values → different sizes
        fx_size = calculate_position_size_exact(
            risk_amount=25.0, stop_distance=0.001,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        idx_size = calculate_position_size_exact(
            risk_amount=25.0, stop_distance=15.0,
            tick_value=0.10, tick_size=0.1,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        assert fx_size != idx_size


class TestAccountProviderExpanded:
    @patch("core.runtime.account_provider.mt5_call")
    def test_reads_expanded_fields(self, mock_call):
        mock_info = MagicMock()
        mock_info.login = 12345
        mock_info.server = "Pepperstone-Live"
        mock_info.currency = "USD"
        mock_info.leverage = 100
        mock_info.margin_mode = 2
        mock_info.balance = 25000.0
        mock_info.equity = 24500.0
        mock_info.credit = 0.0
        mock_info.profit = -500.0
        mock_info.margin = 1200.0
        mock_info.margin_free = 23300.0
        mock_info.margin_level = 2041.67
        mock_info.margin_so_so = 50.0
        mock_call.return_value = mock_info

        ctx = get_account_context()
        assert ctx.login == 12345
        assert ctx.leverage == 100
        assert ctx.margin_free == 23300.0
        assert ctx.available is True


class TestBrokerProviderExpanded:
    @patch("core.runtime.account_provider.mt5_call")
    def test_reads_symbol_metadata(self, mock_call):
        mock_terminal = MagicMock()
        mock_terminal.connected = True
        mock_terminal.name = "Pepperstone MT5"
        mock_terminal.company = "Pepperstone"

        mock_sym = MagicMock()
        mock_sym.trade_mode = 4
        mock_sym.digits = 5
        mock_sym.point = 0.00001
        mock_sym.trade_contract_size = 100000.0
        mock_sym.trade_tick_size = 0.00001
        mock_sym.trade_tick_value = 1.0
        mock_sym.volume_min = 0.01
        mock_sym.volume_max = 100.0
        mock_sym.volume_step = 0.01
        mock_sym.trade_stops_level = 10
        mock_sym.trade_freeze_level = 5
        mock_sym.trade_exemode = 2

        mock_acct = MagicMock()
        mock_acct.margin_free = 8000.0
        mock_acct.balance = 10000.0

        mock_call.side_effect = [mock_terminal, mock_sym, mock_acct, 1]

        ctx = get_broker_context(symbol="EURUSD", bid=1.09, ask=1.09012)
        assert ctx.connected is True
        assert ctx.symbol_available is True
        assert ctx.digits == 5
        assert ctx.tick_value == 1.0
        assert ctx.volume_min == 0.01
        assert ctx.stops_level == 10
        assert ctx.contract_size == 100000.0


class TestExecutionValidation:
    def test_volume_below_min_rejected(self):
        from core.v10.execution_engine import build_execution_decision
        from core.v10.entry_model import EntryDecision, EntryStatus, TradeDirection, StopReference, TargetReference
        from core.v10.risk_model import RiskDecision, RiskProfile
        from core.v10.market_state import V10MarketState

        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            trade_direction="SELL", entry_status="READY",
            entry_price=1.09, risk_distance=0.001, reward_distance=0.002, expected_rr=2.0,
            stop_reference=StopReference(price=1.091),
            target_reference=TargetReference(price=1.088),
        )
        risk = RiskDecision(approved=True, risk_profile=RiskProfile(position_size=0.005))  # Below min
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0)
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            spread=0.00012, available_margin=5000.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )

        result = build_execution_decision(entry, risk, state, broker)
        assert not result.approved
        assert "below minimum" in result.rejection_reason.lower()

    def test_stop_distance_below_stops_level_rejected(self):
        from core.v10.execution_engine import build_execution_decision
        from core.v10.entry_model import EntryDecision, StopReference, TargetReference
        from core.v10.risk_model import RiskDecision, RiskProfile
        from core.v10.market_state import V10MarketState

        entry = EntryDecision(
            opportunity_id="x", symbol="EURUSD", timestamp_utc=1000.0,
            trade_direction="SELL", entry_status="READY",
            entry_price=1.09, risk_distance=0.00005, reward_distance=0.0001, expected_rr=2.0,
            stop_reference=StopReference(price=1.09005),
            target_reference=TargetReference(price=1.0899),
        )
        risk = RiskDecision(approved=True, risk_profile=RiskProfile(position_size=0.25))
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0)
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            spread=0.000005,  # Tiny spread so spread check passes
            available_margin=5000.0,
            stops_level=10, point=0.00001,  # Min stop = 10 × 0.00001 = 0.0001
            volume_min=0.01, volume_max=100.0,
        )

        result = build_execution_decision(entry, risk, state, broker)
        assert not result.approved
        assert "broker minimum" in result.rejection_reason.lower()
