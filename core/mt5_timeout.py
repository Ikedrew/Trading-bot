"""
MT5 Timeout Protection + Circuit Breaker.

Centralised wrapper that ensures NO MetaTrader5 API call can block
the scanner loop indefinitely.

Every MT5 call goes through `call_with_timeout()` which:
1. Submits the MT5 function to a bounded thread pool
2. Returns result if completed within timeout
3. Returns None + logs CRITICAL if timeout exceeded
4. Tracks consecutive timeouts
5. Trips circuit breaker after threshold reached

Usage:
    from core.mt5_timeout import mt5_call, is_circuit_open

    result = mt5_call(mt5.order_send, request)
    if result is None:
        # Timeout or circuit breaker blocked
        ...
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS: float = 10.0
CIRCUIT_BREAKER_THRESHOLD: int = 3
CIRCUIT_BREAKER_RECOVERY_SECONDS: float = 60.0
_POOL_SIZE: int = 2  # MT5 is single-threaded internally; 2 workers is sufficient


# ─── CIRCUIT BREAKER STATE ────────────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Blocked — too many timeouts
    HALF_OPEN = "HALF_OPEN" # Testing — allow single call to probe


@dataclass
class _CircuitBreaker:
    """Thread-safe circuit breaker for MT5 communication."""
    state: CircuitState = CircuitState.CLOSED
    consecutive_timeouts: int = 0
    last_timeout_time: float = 0.0
    opened_at: float = 0.0
    total_timeouts: int = 0
    total_calls: int = 0
    total_successes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        with self._lock:
            self.total_calls += 1
            self.total_successes += 1
            if self.consecutive_timeouts > 0:
                logger.info(
                    "[MT5_TIMEOUT] counter_reset previous_consecutive=%d",
                    self.consecutive_timeouts,
                )
            self.consecutive_timeouts = 0
            if self.state in (CircuitState.OPEN, CircuitState.HALF_OPEN):
                logger.info(
                    "[MT5_CIRCUIT_BREAKER] RECOVERED state=%s→CLOSED",
                    self.state.value,
                )
                self.state = CircuitState.CLOSED

    def record_timeout(self, func_name: str, timeout_s: float) -> None:
        with self._lock:
            self.total_calls += 1
            self.total_timeouts += 1
            self.consecutive_timeouts += 1
            self.last_timeout_time = time.time()

            logger.critical(
                "[MT5_TIMEOUT] function=%s timeout=%.1fs consecutive=%d total=%d",
                func_name, timeout_s, self.consecutive_timeouts, self.total_timeouts,
            )

            if self.consecutive_timeouts >= CIRCUIT_BREAKER_THRESHOLD:
                if self.state != CircuitState.OPEN:
                    self.state = CircuitState.OPEN
                    self.opened_at = time.time()
                    logger.critical(
                        "[MT5_CIRCUIT_BREAKER] ACTIVATED consecutive_timeouts=%d "
                        "threshold=%d state=OPEN trading_halted=true",
                        self.consecutive_timeouts, CIRCUIT_BREAKER_THRESHOLD,
                    )

    def should_allow_call(self) -> bool:
        """Check if a call should be permitted through the breaker."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                elapsed = time.time() - self.opened_at
                if elapsed >= CIRCUIT_BREAKER_RECOVERY_SECONDS:
                    self.state = CircuitState.HALF_OPEN
                    logger.info(
                        "[MT5_CIRCUIT_BREAKER] HALF_OPEN elapsed=%.1fs "
                        "allowing_probe_call=true",
                        elapsed,
                    )
                    return True
                return False

            # HALF_OPEN: allow one call (probe)
            return True

    def get_snapshot(self) -> dict[str, Any]:
        """Return current breaker state for observability."""
        with self._lock:
            return {
                "state": self.state.value,
                "consecutive_timeouts": self.consecutive_timeouts,
                "total_timeouts": self.total_timeouts,
                "total_calls": self.total_calls,
                "total_successes": self.total_successes,
                "last_timeout_time": self.last_timeout_time,
                "opened_at": self.opened_at,
            }


# Module-level singletons
_breaker = _CircuitBreaker()
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Get or create the singleton thread pool. Thread-safe."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=_POOL_SIZE,
                    thread_name_prefix="mt5_worker",
                )
    return _executor


# ─── TIMEOUT WRAPPER ──────────────────────────────────────────────────────────

def call_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> T | None:
    """
    Execute an MT5 API function with timeout protection.

    Args:
        func: The MT5 function to call (e.g., mt5.order_send)
        *args: Positional arguments for the function
        timeout_seconds: Maximum wait time (default 10s)
        **kwargs: Keyword arguments for the function

    Returns:
        Function result on success, None on timeout or circuit breaker block.

    Behaviour:
        - If circuit breaker is OPEN: returns None immediately (no call made)
        - If call completes within timeout: returns result, resets timeout counter
        - If call exceeds timeout: returns None, increments timeout counter
        - Uses bounded thread pool (no per-call thread creation)
    """
    func_name = getattr(func, "__name__", str(func))

    # Circuit breaker check
    if not _breaker.should_allow_call():
        logger.warning(
            "[MT5_CIRCUIT_BREAKER] BLOCKED function=%s state=%s",
            func_name, _breaker.state.value,
        )
        return None

    # Submit to thread pool
    executor = _get_executor()

    def _task() -> T:
        if kwargs:
            return func(*args, **kwargs)
        else:
            return func(*args)

    future: Future = executor.submit(_task)

    try:
        result = future.result(timeout=timeout_seconds)
    except (FuturesTimeoutError, TimeoutError):
        # Future did not complete within timeout
        _breaker.record_timeout(func_name, timeout_seconds)
        future.cancel()
        return None
    except BaseException as exc:
        # Function raised an exception — propagate it
        _breaker.record_success()  # Not a timeout — MT5 responded (with error)
        raise

    _breaker.record_success()
    return result


# ─── CONVENIENCE ALIAS ────────────────────────────────────────────────────────

def mt5_call(func: Callable[..., T], *args: Any, timeout: float = DEFAULT_TIMEOUT_SECONDS, **kwargs: Any) -> T | None:
    """
    Shorthand for call_with_timeout.

    Example:
        result = mt5_call(mt5.order_send, request)
        info = mt5_call(mt5.account_info)
        tick = mt5_call(mt5.symbol_info_tick, symbol)
    """
    return call_with_timeout(func, *args, timeout_seconds=timeout, **kwargs)


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def is_circuit_open() -> bool:
    """Check if circuit breaker is blocking MT5 calls."""
    return _breaker.state == CircuitState.OPEN


def get_circuit_state() -> str:
    """Return current circuit breaker state string."""
    return _breaker.state.value


def get_timeout_metrics() -> dict[str, Any]:
    """Return circuit breaker metrics snapshot."""
    return _breaker.get_snapshot()


def reset_circuit_breaker() -> None:
    """Manually reset circuit breaker (e.g., after operator intervention)."""
    with _breaker._lock:
        prev = _breaker.state.value
        _breaker.state = CircuitState.CLOSED
        _breaker.consecutive_timeouts = 0
        logger.info("[MT5_CIRCUIT_BREAKER] MANUAL_RESET previous_state=%s", prev)


def get_pool_stats() -> dict[str, Any]:
    """Return thread pool statistics for monitoring."""
    executor = _get_executor()
    return {
        "pool_size": _POOL_SIZE,
        "threads_alive": sum(1 for t in threading.enumerate() if t.name.startswith("mt5_worker")),
        "total_calls": _breaker.total_calls,
    }
