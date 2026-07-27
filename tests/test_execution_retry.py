"""
Tests for C4: Execution Retry (Requotes/Timeouts).

Covers:
- REQUOTE (10004) triggers immediate retry with fresh tick
- TIMEOUT (10006) triggers 1s delayed retry
- Hard reject does NOT retry
- Retry count capped at 1
- Metrics increment correctly
- Failure after retry is final
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.mt5_execution import (
    MT5Execution,
    ExecutionResult,
    _execution_metrics,
    get_execution_metrics,
)
from risk.models import OrderIntent
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset execution metrics before each test."""
    _execution_metrics["total_submitted"] = 0
    _execution_metrics["total_success"] = 0
    _execution_metrics["total_failed"] = 0
    _execution_metrics["total_blocked"] = 0
    _execution_metrics["requote_retry_count"] = 0
    _execution_metrics["timeout_retry_count"] = 0
    _execution_metrics["total_retries"] = 0
    _execution_metrics["latency_sum_ms"] = 0.0
    _execution_metrics["latency_count"] = 0
    _execution_metrics["retcodes"] = {}
    with patch("execution.mt5_execution._cfg.DRY_RUN", False):
        yield


@pytest.fixture
def intent():
    """Standard test OrderIntent."""
    return OrderIntent(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.01,
        entry_reference=1.08500,
        sl=1.08400,
        tp=1.08700,
        pattern="TEST_PATTERN",
    )


def _mock_tick(bid=1.08500, ask=1.08502):
    """Create a mock tick."""
    t = MagicMock()
    t.bid = bid
    t.ask = ask
    return t


def _mock_result(retcode, deal=0, order=0, comment="", price=None):
    """Create a mock MT5 order_send result."""
    r = MagicMock()
    r.retcode = retcode
    r.deal = deal
    r.order = order
    r.comment = comment
    r.price = price
    return r


# --- TEST: REQUOTE TRIGGERS RETRY ---------------------------------------------

class TestRequoteRetry:
    def test_requote_retries_with_fresh_tick(self, intent, reset_metrics):
        """REQUOTE (10004) triggers immediate retry with fresh tick."""
        # First call: tick fetch
        tick1 = _mock_tick(ask=1.08502)
        # Second call: order_send returns REQUOTE
        requote_result = _mock_result(10004)
        # Third call: retry tick fetch (fresh tick)
        tick2 = _mock_tick(ask=1.08505)
        # Fourth call: retry order_send succeeds
        success_result = _mock_result(10009, deal=12345, order=67890, price=1.08505)

        call_sequence = [tick1, requote_result, tick2, success_result]
        call_idx = [0]

        def _mt5_call_side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_mt5_call_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is True
        assert _execution_metrics["requote_retry_count"] == 1
        assert _execution_metrics["total_retries"] == 1

    def test_requote_retry_fails_final(self, intent, reset_metrics):
        """REQUOTE retry that also fails ? final failure."""
        tick1 = _mock_tick()
        requote1 = _mock_result(10004)
        tick2 = _mock_tick(ask=1.08506)
        requote2 = _mock_result(10004)  # Retry also requotes

        call_sequence = [tick1, requote1, tick2, requote2]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is False
        assert result.retcode == 10004
        assert _execution_metrics["requote_retry_count"] == 1


# --- TEST: TIMEOUT TRIGGERS RETRY ---------------------------------------------

class TestTimeoutRetry:
    def test_timeout_retries_after_delay(self, intent, reset_metrics):
        """TIMEOUT (10006) triggers retry after 1s delay."""
        tick1 = _mock_tick()
        timeout_result = _mock_result(10006)
        tick2 = _mock_tick(ask=1.08504)
        success_result = _mock_result(10009, deal=111, order=222, price=1.08504)

        call_sequence = [tick1, timeout_result, tick2, success_result]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False), \
             patch("execution.mt5_execution._time.sleep") as mock_sleep:
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is True
        assert _execution_metrics["timeout_retry_count"] == 1
        assert _execution_metrics["total_retries"] == 1
        # Verify 1s delay was applied
        mock_sleep.assert_called_once_with(1.0)


# --- TEST: HARD REJECT NO RETRY -----------------------------------------------

class TestHardRejectNoRetry:
    def test_insufficient_funds_no_retry(self, intent, reset_metrics):
        """Retcode 10019 (insufficient funds) ? NO retry."""
        tick = _mock_tick()
        reject_result = _mock_result(10019, comment="No money")

        call_sequence = [tick, reject_result]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is False
        assert result.retcode == 10019
        assert _execution_metrics["requote_retry_count"] == 0
        assert _execution_metrics["timeout_retry_count"] == 0
        assert _execution_metrics["total_retries"] == 0

    def test_invalid_stops_no_retry(self, intent, reset_metrics):
        """Retcode 10016 (invalid stops) ? NO retry."""
        tick = _mock_tick()
        reject_result = _mock_result(10016)

        call_sequence = [tick, reject_result]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is False
        assert _execution_metrics["total_retries"] == 0


# --- TEST: RETRY COUNT CAPPED -------------------------------------------------

class TestRetryCap:
    def test_max_one_retry(self, intent, reset_metrics):
        """Only 1 retry attempt — never 2+."""
        tick1 = _mock_tick()
        requote1 = _mock_result(10004)
        tick2 = _mock_tick()
        requote2 = _mock_result(10004)  # Retry also fails — no second retry

        call_sequence = [tick1, requote1, tick2, requote2]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        # Only 1 retry total, not 2
        assert _execution_metrics["total_retries"] == 1
        assert result.ok is False


# --- TEST: METRICS -------------------------------------------------------------

class TestMetrics:
    def test_metrics_reflect_retries(self, intent, reset_metrics):
        """Execution metrics include retry counts."""
        tick = _mock_tick()
        requote = _mock_result(10004)
        tick2 = _mock_tick()
        success = _mock_result(10009, deal=1, order=1, price=1.085)

        call_sequence = [tick, requote, tick2, success]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            exec_engine.place_market(intent)

        metrics = get_execution_metrics()
        assert metrics["requote_retry_count"] == 1
        assert metrics["total_retries"] == 1


# --- TEST: SUCCESS WITHOUT RETRY ----------------------------------------------

class TestSuccessNoRetry:
    def test_immediate_success_no_retry(self, intent, reset_metrics):
        """Immediate success (10009) ? no retry logic triggered."""
        tick = _mock_tick()
        success = _mock_result(10009, deal=999, order=888, price=1.08502)

        call_sequence = [tick, success]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False):
            mock_spread.return_value = MagicMock(allowed=True)

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

        assert result.ok is True
        assert _execution_metrics["total_retries"] == 0
        assert _execution_metrics["requote_retry_count"] == 0
