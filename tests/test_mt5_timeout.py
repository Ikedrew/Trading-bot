"""
Tests for C1: MT5 Timeout Protection + Circuit Breaker.

Covers:
- Successful MT5 call returns normally
- Timeout returns None (failure result)
- Exception propagation from MT5 call
- Consecutive timeout counter increments
- Successful call resets counter
- Circuit breaker activates at threshold
- Circuit breaker remains inactive below threshold
- Circuit breaker blocks calls when OPEN
- Circuit breaker recovers after cooldown (HALF_OPEN → CLOSED)
- order_send timeout handling
- positions_get timeout handling
- symbol_info_tick timeout handling
- copy_rates_from_pos timeout handling
- Scanner survival: hung call does not block caller
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mt5_timeout import (
    call_with_timeout,
    mt5_call,
    is_circuit_open,
    get_circuit_state,
    get_timeout_metrics,
    reset_circuit_breaker,
    _breaker,
    CircuitState,
    CIRCUIT_BREAKER_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY_SECONDS,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_breaker_state():
    """Reset circuit breaker state before each test."""
    with _breaker._lock:
        _breaker.state = CircuitState.CLOSED
        _breaker.consecutive_timeouts = 0
        _breaker.total_timeouts = 0
        _breaker.total_calls = 0
        _breaker.total_successes = 0
        _breaker.last_timeout_time = 0.0
        _breaker.opened_at = 0.0
    yield
    reset_circuit_breaker()


# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────

def _fast_function(x: int, y: int) -> int:
    """Completes immediately."""
    return x + y


def _slow_function(duration: float = 5.0) -> str:
    """Blocks for `duration` seconds."""
    time.sleep(duration)
    return "completed"


def _raising_function() -> None:
    """Raises an exception."""
    raise ValueError("MT5 internal error")


def _returns_none() -> None:
    """Returns None (simulates MT5 returning None on failure)."""
    return None


# ─── TEST: SUCCESSFUL CALL ────────────────────────────────────────────────────

class TestSuccessfulCall:
    def test_returns_result_normally(self):
        """Successful MT5 call returns the function result."""
        result = call_with_timeout(_fast_function, 3, 4, timeout_seconds=2.0)
        assert result == 7

    def test_mt5_call_alias_works(self):
        """mt5_call convenience alias returns result."""
        result = mt5_call(_fast_function, 10, 20, timeout=2.0)
        assert result == 30

    def test_returns_none_from_function(self):
        """Function returning None is distinguished from timeout (timeout=None too, but counter not incremented)."""
        # When function itself returns None AND completes in time, it counts as success
        result = call_with_timeout(_returns_none, timeout_seconds=2.0)
        assert result is None
        # But counter was NOT incremented (function completed, not timeout)
        assert _breaker.consecutive_timeouts == 0

    def test_kwargs_passed_correctly(self):
        """Keyword arguments are forwarded to the function."""
        def _kw_func(a, b=10):
            return a * b
        result = call_with_timeout(_kw_func, 5, b=3, timeout_seconds=2.0)
        assert result == 15


# ─── TEST: TIMEOUT FAILURE ────────────────────────────────────────────────────

class TestTimeoutFailure:
    def test_timeout_returns_none(self):
        """Slow function exceeding timeout returns None."""
        result = call_with_timeout(_slow_function, 10.0, timeout_seconds=0.1)
        assert result is None

    def test_timeout_does_not_block_caller(self):
        """Caller returns promptly even if function hangs."""
        start = time.time()
        result = call_with_timeout(_slow_function, 10.0, timeout_seconds=0.2)
        elapsed = time.time() - start
        assert result is None
        assert elapsed < 1.0  # Must return well before the 10s sleep

    def test_timeout_increments_counter(self):
        """Each timeout increments consecutive_timeouts."""
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.consecutive_timeouts == 1

        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.consecutive_timeouts == 2


# ─── TEST: EXCEPTION PROPAGATION ──────────────────────────────────────────────

class TestExceptionPropagation:
    def test_exception_is_raised(self):
        """Exceptions from MT5 calls propagate to caller."""
        with pytest.raises(ValueError, match="MT5 internal error"):
            call_with_timeout(_raising_function, timeout_seconds=2.0)

    def test_exception_does_not_increment_timeout(self):
        """Exception is NOT a timeout — counter stays at 0."""
        try:
            call_with_timeout(_raising_function, timeout_seconds=2.0)
        except ValueError:
            pass
        assert _breaker.consecutive_timeouts == 0

    def test_exception_counts_as_success_for_breaker(self):
        """MT5 responded (with error) — not a communication failure."""
        try:
            call_with_timeout(_raising_function, timeout_seconds=2.0)
        except ValueError:
            pass
        assert _breaker.total_successes == 1


# ─── TEST: COUNTER RESET ──────────────────────────────────────────────────────

class TestCounterReset:
    def test_success_resets_consecutive_timeouts(self):
        """Successful call after timeouts resets counter to 0."""
        # Simulate 2 timeouts
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.consecutive_timeouts == 2

        # Successful call resets
        call_with_timeout(_fast_function, 1, 2, timeout_seconds=2.0)
        assert _breaker.consecutive_timeouts == 0

    def test_total_timeouts_never_resets(self):
        """Total timeout count accumulates (never resets on success)."""
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        call_with_timeout(_fast_function, 1, 2, timeout_seconds=2.0)  # Success
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)

        assert _breaker.total_timeouts == 3
        assert _breaker.consecutive_timeouts == 1  # Reset then +1


# ─── TEST: CIRCUIT BREAKER ACTIVATION ─────────────────────────────────────────

class TestCircuitBreakerActivation:
    def test_activates_at_threshold(self):
        """Circuit breaker opens after CIRCUIT_BREAKER_THRESHOLD consecutive timeouts."""
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)

        assert _breaker.state == CircuitState.OPEN
        assert is_circuit_open() is True
        assert get_circuit_state() == "OPEN"

    def test_remains_closed_below_threshold(self):
        """Circuit breaker stays CLOSED below threshold."""
        for _ in range(CIRCUIT_BREAKER_THRESHOLD - 1):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)

        assert _breaker.state == CircuitState.CLOSED
        assert is_circuit_open() is False

    def test_open_blocks_calls_immediately(self):
        """When OPEN, calls return None without executing the function."""
        # Trip the breaker
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.state == CircuitState.OPEN

        # Next call should be blocked immediately (not wait for timeout)
        call_count = [0]
        def _counting_func():
            call_count[0] += 1
            return "should_not_reach"

        start = time.time()
        result = call_with_timeout(_counting_func, timeout_seconds=5.0)
        elapsed = time.time() - start

        assert result is None
        assert call_count[0] == 0  # Function was never called
        assert elapsed < 0.5  # Returned immediately

    def test_recovery_after_cooldown(self):
        """After cooldown, breaker moves to HALF_OPEN and allows a probe call."""
        # Trip the breaker
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.state == CircuitState.OPEN

        # Simulate cooldown elapsed by backdating opened_at
        with _breaker._lock:
            _breaker.opened_at = time.time() - CIRCUIT_BREAKER_RECOVERY_SECONDS - 1.0

        # Next call should be allowed (probe)
        result = call_with_timeout(_fast_function, 5, 5, timeout_seconds=2.0)
        assert result == 10
        assert _breaker.state == CircuitState.CLOSED  # Recovered

    def test_half_open_timeout_reopens(self):
        """If probe call in HALF_OPEN times out, breaker returns to OPEN."""
        # Trip the breaker
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.state == CircuitState.OPEN

        # Simulate cooldown elapsed
        with _breaker._lock:
            _breaker.opened_at = time.time() - CIRCUIT_BREAKER_RECOVERY_SECONDS - 1.0

        # Probe call times out — should re-open
        result = call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert result is None
        assert _breaker.state == CircuitState.OPEN


# ─── TEST: METRICS ────────────────────────────────────────────────────────────

class TestMetrics:
    def test_metrics_snapshot(self):
        """get_timeout_metrics returns expected fields."""
        call_with_timeout(_fast_function, 1, 2, timeout_seconds=2.0)
        call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)

        metrics = get_timeout_metrics()
        assert metrics["total_calls"] == 2
        assert metrics["total_successes"] == 1
        assert metrics["total_timeouts"] == 1
        assert metrics["consecutive_timeouts"] == 1
        assert metrics["state"] == "CLOSED"

    def test_manual_reset(self):
        """reset_circuit_breaker clears state."""
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            call_with_timeout(_slow_function, 10.0, timeout_seconds=0.05)
        assert _breaker.state == CircuitState.OPEN

        reset_circuit_breaker()
        assert _breaker.state == CircuitState.CLOSED
        assert _breaker.consecutive_timeouts == 0


# ─── TEST: PROTECTED MT5 CALLS (mocked) ──────────────────────────────────────

class TestProtectedMT5Calls:
    """Verify specific MT5 functions go through timeout wrapper."""

    def test_order_send_timeout(self):
        """mt5.order_send going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.order_send = lambda req: time.sleep(10) or None  # Hangs

        result = mt5_call(mock_mt5.order_send, {"action": 1}, timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_positions_get_timeout(self):
        """mt5.positions_get going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.positions_get = lambda **kw: time.sleep(10) or None

        result = mt5_call(mock_mt5.positions_get, symbol="EURUSD", timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_symbol_info_tick_timeout(self):
        """mt5.symbol_info_tick going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.symbol_info_tick = lambda sym: time.sleep(10) or None

        result = mt5_call(mock_mt5.symbol_info_tick, "EURUSD", timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_copy_rates_from_pos_timeout(self):
        """mt5.copy_rates_from_pos going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.copy_rates_from_pos = lambda *a: time.sleep(10) or None

        result = mt5_call(mock_mt5.copy_rates_from_pos, "EURUSD", 5, 0, 300, timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_account_info_timeout(self):
        """mt5.account_info going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.account_info = lambda: time.sleep(10) or None

        result = mt5_call(mock_mt5.account_info, timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_terminal_info_timeout(self):
        """mt5.terminal_info going through mt5_call respects timeout."""
        mock_mt5 = MagicMock()
        mock_mt5.terminal_info = lambda: time.sleep(10) or None

        result = mt5_call(mock_mt5.terminal_info, timeout=0.1)
        assert result is None
        assert _breaker.consecutive_timeouts == 1

    def test_order_send_success(self):
        """Successful order_send returns result and resets counter."""
        mock_result = MagicMock()
        mock_result.retcode = 10009

        result = mt5_call(lambda: mock_result, timeout=2.0)
        assert result is mock_result
        assert _breaker.consecutive_timeouts == 0


# ─── TEST: SCANNER SURVIVAL ──────────────────────────────────────────────────

class TestScannerSurvival:
    def test_hung_call_does_not_deadlock(self):
        """A permanently hung MT5 call does not block the calling thread."""
        def _permanently_hung():
            # Simulate a truly stuck MT5 call
            event = threading.Event()
            event.wait()  # Blocks forever

        start = time.time()
        result = call_with_timeout(_permanently_hung, timeout_seconds=0.2)
        elapsed = time.time() - start

        assert result is None
        assert elapsed < 1.0
        assert _breaker.consecutive_timeouts == 1

    def test_multiple_symbols_continue_after_timeout(self):
        """Simulates scanner processing multiple symbols — one timeout doesn't block others."""
        results = []

        # Symbol 1: hangs
        r1 = call_with_timeout(_slow_function, 10.0, timeout_seconds=0.1)
        results.append(r1)

        # Symbol 2: fast (should still work)
        r2 = call_with_timeout(_fast_function, 100, 200, timeout_seconds=2.0)
        results.append(r2)

        # Symbol 3: fast
        r3 = call_with_timeout(_fast_function, 5, 5, timeout_seconds=2.0)
        results.append(r3)

        assert results == [None, 300, 10]
        assert _breaker.consecutive_timeouts == 0  # Reset by successes
