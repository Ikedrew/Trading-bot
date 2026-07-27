"""
Tests for I5: Startup Self-Test.

Covers:
Success Cases:
- All checks pass ? PASS summary emitted
- Config integrity passes with valid config
- State recovery initializes fresh

Failure Cases:
- Missing symbol ? StartupSelfTestError
- Candle fetch failure ? StartupSelfTestError
- Tick failure ? StartupSelfTestError
- Position query failure ? StartupSelfTestError
- Heartbeat write failure ? StartupSelfTestError
- MT5 connection failure ? StartupSelfTestError
- Account validation failure ? StartupSelfTestError
- Config integrity failure ? StartupSelfTestError
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.startup_self_test import (
    StartupSelfTestError,
    run_startup_self_test,
    _check_config_integrity,
    _check_mt5_connection,
    _check_account,
    _check_symbol_resolution,
    _check_candle_retrieval,
    _check_tick_data,
    _check_position_query,
    _check_state_recovery,
    _check_heartbeat,
)


# --- HELPERS ------------------------------------------------------------------

def _mock_mt5_success():
    """Patch MT5 calls to return valid responses."""
    term_info = MagicMock()
    version = ("5.0", "1234", "MetaTrader 5")
    account = MagicMock(login=12345, balance=100000.0, leverage=100)
    symbol_info = MagicMock(visible=True)
    tick = MagicMock(bid=1.1000, ask=1.1002)
    rates = [MagicMock(time=1700000000, open=1.1, high=1.11, low=1.09, close=1.105)]

    def _mt5_call_side_effect(fn, *args, **kwargs):
        fn_name = getattr(fn, "__name__", str(fn))
        if "terminal_info" in fn_name:
            return term_info
        if "account_info" in fn_name:
            return account
        if "symbol_info_tick" in fn_name:
            return tick
        if "symbol_info" in fn_name:
            return symbol_info
        if "copy_rates" in fn_name:
            return rates
        if "positions_get" in fn_name:
            return ()
        return MagicMock()

    return _mt5_call_side_effect


# --- TEST: CONFIG INTEGRITY ---------------------------------------------------

class TestConfigIntegrity:
    def test_valid_config_passes(self):
        """Valid config passes integrity check."""
        # Uses real config (should be valid from prior tests)
        _check_config_integrity()  # Should not raise

    def test_missing_strategy_fails(self):
        """Missing STRATEGY_NAME fails."""
        from core import config
        original = config.STRATEGY_NAME
        try:
            config.STRATEGY_NAME = ""
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_config_integrity()
            assert "STRATEGY_NAME" in str(exc_info.value)
        finally:
            config.STRATEGY_NAME = original

    def test_invalid_magic_fails(self):
        """Invalid BOT_MAGIC fails."""
        from core import config
        original = config.BOT_MAGIC
        try:
            config.BOT_MAGIC = -1
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_config_integrity()
            assert "BOT_MAGIC" in str(exc_info.value)
        finally:
            config.BOT_MAGIC = original


# --- TEST: MT5 CONNECTION ------------------------------------------------------

class TestMT5Connection:
    def test_mt5_none_terminal_fails(self):
        """terminal_info returning None fails."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_mt5_connection()
            assert "terminal" in str(exc_info.value).lower()

    def test_mt5_connection_ok(self):
        """Valid terminal_info passes."""
        mock_term = MagicMock()
        mock_version = ("5.0", "build", "info")

        with patch("core.startup_self_test.mt5_call", return_value=mock_term), \
             patch("core.startup_self_test.mt5.version", return_value=mock_version):
            _check_mt5_connection()  # Should not raise


# --- TEST: ACCOUNT VALIDATION -------------------------------------------------

class TestAccountValidation:
    def test_account_none_fails(self):
        """account_info returning None fails."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_account()
            assert "account_info" in str(exc_info.value)

    def test_zero_balance_fails(self):
        """Account with zero balance fails."""
        mock_acct = MagicMock(login=123, balance=0.0, leverage=100)
        with patch("core.startup_self_test.mt5_call", return_value=mock_acct):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_account()
            assert "balance" in str(exc_info.value).lower()

    def test_valid_account_passes(self):
        """Valid account passes."""
        mock_acct = MagicMock(login=12345, balance=50000.0, leverage=100)
        with patch("core.startup_self_test.mt5_call", return_value=mock_acct):
            _check_account()  # Should not raise


# --- TEST: SYMBOL RESOLUTION --------------------------------------------------

class TestSymbolResolution:
    def test_missing_symbol_fails(self):
        """symbol_info returning None for a symbol fails."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_symbol_resolution(["XAUUSD"])
            assert "XAUUSD" in str(exc_info.value)

    def test_valid_symbols_pass(self):
        """All symbols resolving passes."""
        mock_info = MagicMock(visible=True)
        with patch("core.startup_self_test.mt5_call", return_value=mock_info):
            _check_symbol_resolution(["EURUSD", "GBPUSD"])


# --- TEST: CANDLE RETRIEVAL ---------------------------------------------------

class TestCandleRetrieval:
    def test_empty_candles_fails(self):
        """Empty candle response fails."""
        with patch("core.startup_self_test.mt5_call", return_value=[]):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_candle_retrieval(["EURUSD"])
            assert "EURUSD" in str(exc_info.value)

    def test_none_candles_fails(self):
        """None candle response fails."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_candle_retrieval(["GBPUSD"])

    def test_valid_candles_pass(self):
        """Valid candle data passes."""
        rates = [MagicMock()]
        with patch("core.startup_self_test.mt5_call", return_value=rates):
            _check_candle_retrieval(["EURUSD", "GBPUSD"])


# --- TEST: TICK DATA ----------------------------------------------------------

class TestTickData:
    def test_none_tick_fails(self):
        """None tick response fails."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_tick_data(["USDJPY"])
            assert "USDJPY" in str(exc_info.value)

    def test_zero_bid_fails(self):
        """Tick with bid=0 fails."""
        mock_tick = MagicMock(bid=0.0, ask=1.1)
        with patch("core.startup_self_test.mt5_call", return_value=mock_tick):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_tick_data(["EURUSD"])
            assert "Invalid tick" in str(exc_info.value)

    def test_valid_ticks_pass(self):
        """Valid tick data passes."""
        mock_tick = MagicMock(bid=1.1000, ask=1.1002)
        with patch("core.startup_self_test.mt5_call", return_value=mock_tick):
            _check_tick_data(["EURUSD"])


# --- TEST: POSITION QUERY -----------------------------------------------------

class TestPositionQuery:
    def test_none_positions_fails(self):
        """positions_get returning None fails (broker permission issue)."""
        with patch("core.startup_self_test.mt5_call", return_value=None):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_position_query()
            assert "permission" in str(exc_info.value).lower()

    def test_empty_positions_passes(self):
        """Empty tuple (no positions) passes — just needs to work."""
        with patch("core.startup_self_test.mt5_call", return_value=()):
            _check_position_query()  # Should not raise

    def test_positions_present_passes(self):
        """Having positions also passes."""
        with patch("core.startup_self_test.mt5_call", return_value=(MagicMock(),)):
            _check_position_query()


# --- TEST: STATE RECOVERY -----------------------------------------------------

class TestStateRecovery:
    def test_fresh_state_initializes(self, tmp_path):
        """Fresh state (no files) initializes without error."""
        # Patch state paths to temp dir
        with patch("risk.daily_loss_guard._get_state_path", return_value=tmp_path / "dl.json"), \
             patch("risk.daily_loss_guard.mt5_call", return_value=MagicMock(equity=100000)), \
             patch("risk.daily_trade_limit._get_state_path", return_value=tmp_path / "dtl.json"), \
             patch("risk.trade_cooldown._get_state_path", return_value=tmp_path / "cd.json"), \
             patch("core.daily_reset._get_state_path", return_value=tmp_path / "dr.json"):
            _check_state_recovery()  # Should not raise


# --- TEST: HEARTBEAT ----------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_write_failure_fails(self, tmp_path):
        """Heartbeat write failure aborts."""
        with patch("core.startup_self_test.write_heartbeat", return_value=False):
            with pytest.raises(StartupSelfTestError) as exc_info:
                _check_heartbeat()
            assert "heartbeat" in str(exc_info.value).lower()

    def test_heartbeat_success(self, tmp_path):
        """Heartbeat write + read succeeds."""
        hb_path = tmp_path / "heartbeat.json"
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            _check_heartbeat()  # Should not raise


# --- TEST: FULL SUITE ---------------------------------------------------------

class TestFullSuite:
    def test_all_pass_emits_summary(self, tmp_path, caplog):
        """All checks passing emits SELF_TEST_PASSED."""
        import logging

        mock_term = MagicMock()
        mock_acct = MagicMock(login=123, balance=50000.0, leverage=100)
        mock_sym = MagicMock(visible=True)
        mock_tick = MagicMock(bid=1.1, ask=1.1002)
        mock_rates = [MagicMock()]

        call_count = [0]
        def _side_effect(fn, *args, **kwargs):
            call_count[0] += 1
            fn_name = getattr(fn, "__name__", "")
            if "terminal_info" in fn_name:
                return mock_term
            if "account_info" in fn_name:
                return mock_acct
            if "symbol_info_tick" in fn_name:
                return mock_tick
            if "symbol_info" in fn_name:
                return mock_sym
            if "copy_rates" in fn_name:
                return mock_rates
            if "positions_get" in fn_name:
                return ()
            return MagicMock()

        hb_path = tmp_path / "heartbeat.json"

        with patch("core.startup_self_test.mt5_call", side_effect=_side_effect), \
             patch("core.startup_self_test.mt5.version", return_value=("5",)), \
             patch("core.startup_self_test.mt5.symbol_select", return_value=True), \
             patch("risk.daily_loss_guard._get_state_path", return_value=tmp_path / "dl.json"), \
             patch("risk.daily_loss_guard.mt5_call", return_value=MagicMock(equity=100000)), \
             patch("risk.daily_trade_limit._get_state_path", return_value=tmp_path / "dtl.json"), \
             patch("risk.trade_cooldown._get_state_path", return_value=tmp_path / "cd.json"), \
             patch("core.daily_reset._get_state_path", return_value=tmp_path / "dr.json"), \
             patch("core.heartbeat._get_heartbeat_path", return_value=hb_path), \
             caplog.at_level(logging.INFO):
            run_startup_self_test(symbols=["EURUSD"])

        assert "SELF_TEST_PASSED" in caplog.text
