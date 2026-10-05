"""Connection failure classification and bounded scanner incident reporting.

This module does not call MT5 or reconnect. Owners report observed failures;
symbol/capability failures remain outside this shared connection incident.
"""
from __future__ import annotations

import logging
import time
from typing import Any


def is_connection_error(error: Any) -> bool:
    """Recognise native IPC errors, without treating symbol errors as IPC loss."""
    if not isinstance(error, (tuple, list)) or len(error) != 2:
        return False
    return error[0] in {-10000, -10001, -10002, -10003, -10004, -10005}


class MT5ConnectionError(RuntimeError):
    def __init__(self, error: Any, operation: str) -> None:
        self.error = tuple(error)
        self.operation = operation
        super().__init__(f"{operation}: {self.error}")


def mt5_failure(error: Any, operation: str) -> RuntimeError:
    """Capture last_error immediately at a failed API call, before it changes."""
    if is_connection_error(error):
        return MT5ConnectionError(error, operation)
    return RuntimeError(f"{operation}: {error}")


class MT5ConnectionIncident:
    """One scanner session's incident; no global log filter or retry policy.

    First loss and every changed reason are visible. After five minutes one
    CRITICAL escalation is emitted; recovery resets the incident completely.
    """
    def __init__(self, affected_symbols: int, logger: logging.Logger,
                 *, clock=time.monotonic, prolonged_seconds: float = 300.0) -> None:
        self.affected_symbols = affected_symbols
        self.logger = logger
        self._clock = clock
        self._prolonged_seconds = prolonged_seconds
        self.active = False
        self._error: Any = None
        self._started = 0.0
        self._escalated = False

    def lost(self, error: Any, *, startup_abort: bool = False) -> None:
        error = tuple(error)
        if not self.active:
            self.active = True
            self._started = self._clock()
            self._error = error
            log = self.logger.critical if startup_abort else self.logger.error
            log("[MT5_CONNECTION_LOST] error=%s affected_symbols=%d state=DEGRADED action=%s",
                error, self.affected_symbols,
                "ABORT_STARTUP" if startup_abort else "BLOCK_TRADING_RECONNECT")
            self._escalated = startup_abort
        elif error != self._error:
            self.logger.error(
                "[MT5_CONNECTION_CHANGED] error=%s previous_error=%s affected_symbols=%d state=DEGRADED",
                error, self._error, self.affected_symbols)
            self._error = error
        self.check_prolonged()

    def check_prolonged(self) -> None:
        if self.active and not self._escalated and self._clock() - self._started >= self._prolonged_seconds:
            self._escalated = True
            self.logger.critical(
                "[MT5_CONNECTION_UNRECOVERED] error=%s affected_symbols=%d state=DEGRADED trading_blocked=true",
                self._error, self.affected_symbols)

    def restored(self) -> None:
        if self.active:
            self.logger.info("[MT5_CONNECTION_RESTORED] affected_symbols=%d", self.affected_symbols)
            self.active = False
            self._error = None
            self._escalated = False
