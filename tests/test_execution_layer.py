"""
Execution layer tests — validates MT5Execution behaviour with fully mocked MT5.

Covers:
- place_market() success path
- Pre-execution validation (symbol/volume rejection)
- close_position() behaviour
- Idempotency guard (duplicate blocking)
- DRY_RUN mode
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.models import OrderIntent
from strategy.signals import Side


# --- HELPERS ------------------------------------------------------------------

def _make_intent(symbol="EURUSD", side=Side.BUY, volume=0.01, sl=1.08, tp=1.12, pattern="HAMMER"):
    return OrderIntent(symbol=symbol, side=side, volume=volume, entry_reference=1.10, sl=sl, tp=tp, pattern=pattern)


def _mock_order_result(retcode=10009, deal=12345, order=67890, comment="Request executed", price=1.1001):
    r = MagicMock()
    r.retcode = retcode
    r.deal = deal
    r.order = order
    r.comment = comment
    r.price = price
    return r


def _mock_tick(bid=1.10, ask=1.1002):
    t = MagicMock()
    t.bid = bid
    t.ask = ask
    t.time = 1716400000
    return t


def _mock_symbol_info(visible=True, trade_mode=4, volume_min=0.01, volume_max=100.0, volume_step=0.01, filling_mode=2):
    s = MagicMock()
    s.visible = visible
    s.trade_mode = trade_mode
    s.volume_min = volume_min
    s.volume_max = volume_max
    s.volume_step = volume_step
    s.filling_mode = filling_mode
    return s


# --- PLACE_MARKET SUCCESS -----------------------------------------------------

class TestPlaceMarketSuccess:
    def test_successful_buy_order(self):
        """Successful BUY order returns ok=True with deal/order IDs."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_cfg.DRY_RUN_EXECUTION_LOGS = False
            mock_mt5.TRADE_RETCODE_DONE = 10009
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TIME_GTC = 0
            mock_mt5.ORDER_FILLING_IOC = 2
            mock_mt5.symbol_info_tick.return_value = _mock_tick()
            mock_mt5.symbol_info.return_value = _mock_symbol_info()
            mock_mt5.order_send.return_value = _mock_order_result()

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution(magic=713001, deviation=20)
            result = exe.place_market(_make_intent())

            assert result.ok is True
            assert result.deal == 12345
            assert result.order == 67890
            assert result.fill_price == 1.1001
            mock_mt5.order_send.assert_called_once()

    def test_successful_sell_order(self):
        """Successful SELL order uses bid price."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_cfg.DRY_RUN_EXECUTION_LOGS = False
            mock_mt5.TRADE_RETCODE_DONE = 10009
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TIME_GTC = 0
            mock_mt5.ORDER_FILLING_IOC = 2
            mock_mt5.symbol_info_tick.return_value = _mock_tick()
            mock_mt5.symbol_info.return_value = _mock_symbol_info()
            mock_mt5.order_send.return_value = _mock_order_result()

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution(magic=713001)
            intent = _make_intent(side=Side.SELL)
            result = exe.place_market(intent)

            assert result.ok is True


# --- PRE-EXECUTION VALIDATION -------------------------------------------------

class TestPreExecutionValidation:
    def test_symbol_not_found_rejects(self):
        """If symbol_info returns None, order is rejected before MT5 call."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.symbol_info.return_value = None

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution()
            result = exe.place_market(_make_intent())

            assert result.ok is False
            assert "PREVALIDATION_FAILED" in result.comment
            mock_mt5.order_send.assert_not_called()

    def test_symbol_not_visible_rejects(self):
        """If symbol is not visible, order is rejected."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.symbol_info.return_value = _mock_symbol_info(visible=False)

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution()
            result = exe.place_market(_make_intent())

            assert result.ok is False
            assert "SYMBOL_NOT_VISIBLE" in result.comment
            mock_mt5.order_send.assert_not_called()

    def test_volume_below_min_rejects(self):
        """If volume is below broker minimum, order is rejected."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.symbol_info.return_value = _mock_symbol_info(volume_min=0.1)

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution()
            result = exe.place_market(_make_intent(volume=0.01))

            assert result.ok is False
            assert "VOLUME_BELOW_MIN" in result.comment
            mock_mt5.order_send.assert_not_called()

    def test_market_not_tradeable_rejects(self):
        """If trade_mode is 0, order is rejected."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.symbol_info.return_value = _mock_symbol_info(trade_mode=0)

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution()
            result = exe.place_market(_make_intent())

            assert result.ok is False
            assert "SYMBOL_NOT_TRADEABLE" in result.comment
            mock_mt5.order_send.assert_not_called()


# --- CLOSE POSITION -----------------------------------------------------------

class TestClosePosition:
    def test_close_position_success(self):
        """Successful position close returns ok=True."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.TRADE_RETCODE_DONE = 10009
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TIME_GTC = 0
            mock_mt5.ORDER_FILLING_IOC = 2

            pos = MagicMock()
            pos.type = 0  # BUY position ? close with SELL
            pos.volume = 0.01
            mock_mt5.positions_get.return_value = (pos,)
            mock_mt5.symbol_info_tick.return_value = _mock_tick()
            mock_mt5.symbol_info.return_value = _mock_symbol_info()
            mock_mt5.order_send.return_value = _mock_order_result()

            from execution.mt5_execution import MT5Execution
            exe = MT5Execution()
            result = exe.close_position("EURUSD", 12345)

            assert result.ok is True
            mock_mt5.order_send.assert_called_once()

    def test_close_position_not_found(self):
        """If position doesn't exist, returns failure."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = False
            mock_mt5.positions_get.return_value = None

            from execution.mt5_execution import MT5Execution
            exe = MT5Execution()
            result = exe.close_position("EURUSD", 99999)

            assert result.ok is False
            assert "POSITION_NOT_FOUND" in result.comment


# --- IDEMPOTENCY --------------------------------------------------------------

class TestIdempotency:
    def test_duplicate_intent_blocked(self):
        """Second identical order within window is blocked."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = True
            mock_cfg.DRY_RUN_EXECUTION_LOGS = False
            mock_mt5.symbol_info.return_value = _mock_symbol_info()
            mock_mt5.symbol_info_tick.return_value = _mock_tick()
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_FILLING_IOC = 2

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution(magic=713001)
            intent = _make_intent()

            # First call succeeds (DRY_RUN)
            r1 = exe.place_market(intent)
            assert r1.ok is True

            # Second identical call is blocked
            r2 = exe.place_market(intent)
            assert r2.ok is False
            assert "DUPLICATE_INTENT_BLOCKED" in r2.comment


# --- DRY RUN MODE -------------------------------------------------------------

class TestDryRunMode:
    def test_dry_run_does_not_call_mt5(self):
        """In DRY_RUN mode, MT5 order_send is never called."""
        with patch("execution.mt5_execution.mt5") as mock_mt5, \
             patch("execution.mt5_execution._cfg") as mock_cfg:
            mock_cfg.DRY_RUN = True
            mock_cfg.DRY_RUN_EXECUTION_LOGS = False
            mock_mt5.symbol_info.return_value = _mock_symbol_info()
            mock_mt5.symbol_info_tick.return_value = _mock_tick()
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_FILLING_IOC = 2

            from execution.mt5_execution import MT5Execution, _recent_intents
            _recent_intents.clear()

            exe = MT5Execution()
            result = exe.place_market(_make_intent())

            assert result.ok is True
            assert result.comment == "dry_run"
            mock_mt5.order_send.assert_not_called()
