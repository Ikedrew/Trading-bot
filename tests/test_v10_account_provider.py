"""Tests for V10 Runtime Account/Broker Provider."""

import pytest
from unittest.mock import patch, MagicMock
from core.runtime.account_provider import get_account_context, get_broker_context
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext


class TestAccountProvider:
    def test_unavailable_when_mt5_fails(self):
        """When MT5 is not available, returns zero-balance context."""
        with patch("core.runtime.account_provider.mt5_call", side_effect=Exception("no MT5")):
            ctx = get_account_context()
        assert ctx.balance == 0.0
        assert ctx.equity == 0.0

    def test_unavailable_when_account_info_none(self):
        with patch("core.runtime.account_provider.mt5_call", return_value=None):
            ctx = get_account_context()
        assert ctx.balance == 0.0

    @patch("core.runtime.account_provider.mt5_call")
    def test_reads_live_balance(self, mock_call):
        mock_info = MagicMock()
        mock_info.balance = 25000.0
        mock_info.equity = 24500.0
        mock_call.return_value = mock_info

        ctx = get_account_context(open_positions=2, daily_loss_pct=0.01)
        assert ctx.balance == 25000.0
        assert ctx.equity == 24500.0
        assert ctx.open_positions == 2
        assert ctx.daily_loss_pct == 0.01


class TestBrokerProvider:
    def test_unavailable_when_mt5_fails(self):
        with patch("core.runtime.account_provider.mt5_call", side_effect=Exception("no MT5")):
            ctx = get_broker_context(symbol="EURUSD", bid=1.09, ask=1.0901)
        assert ctx.connected is False
        assert ctx.symbol_available is False

    @patch("core.runtime.account_provider.mt5_call")
    def test_reads_live_spread(self, mock_call):
        # terminal_info → connected
        mock_terminal = MagicMock()
        mock_terminal.connected = True
        # symbol_info → available, trade_mode=4
        mock_sym = MagicMock()
        mock_sym.trade_mode = 4
        # account_info → margin
        mock_acct = MagicMock()
        mock_acct.margin_free = 8000.0
        mock_acct.balance = 10000.0
        # positions_total
        mock_call.side_effect = [mock_terminal, mock_sym, mock_acct, 1]

        ctx = get_broker_context(symbol="EURUSD", bid=1.09000, ask=1.09012)
        assert ctx.connected is True
        assert ctx.symbol_available is True
        assert ctx.market_open is True
        assert ctx.spread == pytest.approx(0.00012, abs=1e-6)
        assert ctx.available_margin == 8000.0

    def test_no_hardcoded_defaults_in_model(self):
        """BrokerContext defaults to disconnected — not fictitious values."""
        ctx = BrokerContext()
        assert ctx.connected is False
        assert ctx.available_margin == 0.0

    @patch("core.runtime.account_provider.mt5_call")
    def test_disconnected_terminal(self, mock_call):
        mock_terminal = MagicMock()
        mock_terminal.connected = False
        mock_call.return_value = mock_terminal

        ctx = get_broker_context(symbol="EURUSD")
        assert ctx.connected is False


class TestPipelineWithUnavailableContext:
    def test_zero_balance_rejects_at_risk(self):
        """Pipeline rejects cleanly when account data unavailable."""
        from core.market_understanding.models import MarketUnderstanding, H1Understanding, M5Understanding
        from core.v10.pipeline import V10Pipeline

        mu = MarketUnderstanding(
            symbol="EURUSD", timestamp_utc=1000.0,
            h1=H1Understanding(bos_confirmed=True, bos_direction="BEARISH", structural_clarity=0.8,
                               swing_high=1.095, swing_low=1.085,
                               active_supply_ob_high=1.094, active_supply_ob_low=1.0935),
            m5=M5Understanding(atr=0.0006, rejection_present=True, at_institutional_zone=True, zone_type="SUPPLY_OB"),
        )
        # Default contexts = unavailable (balance=0, disconnected)
        pipeline = V10Pipeline()
        result = pipeline.process(mu)

        # Should reject — either at risk (balance=0) or execution (disconnected)
        assert not result.approved

    def test_disconnected_broker_rejects_at_execution(self):
        """Disconnected broker causes clean execution rejection."""
        from core.market_understanding.models import MarketUnderstanding
        from core.v10.pipeline import V10Pipeline
        from core.v10.risk_model import AccountContext
        from core.v10.broker_context import BrokerContext

        mu = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1000.0)
        account = AccountContext(balance=10000.0, equity=10000.0)
        broker = BrokerContext(connected=False)

        pipeline = V10Pipeline()
        result = pipeline.process(mu, None, account, broker)
        assert not result.execution.approved
