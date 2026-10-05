"""
MT5 Health Manager — Connection lifecycle with exponential backoff reconnect.

Manages MT5 connection state, reconnect attempts with exponential backoff,
and post-reconnect symbol reactivation + position resync.

This module OWNS:
    - MT5 connection state tracking (connected/disconnected)
    - Reconnect decision logic (backoff timing)
    - Reconnect attempts
    - Post-reconnect symbol reactivation and position resync

This module does NOT own:
    - Trade decisions
    - Risk logic
    - Execution logic
    - Market scanning
    - Heartbeat writing (caller responsibility)
    - Sleep/poll timing (caller responsibility)

Design: stateful manager, never raises to caller, preserves existing logging.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.mt5_connection import (
    MT5_CONNECTED,
    MT5_DISCONNECTED,
    attempt_reconnect,
    mt5_health_error,
    resync_positions,
)
from core.mt5_incident import MT5ConnectionError, MT5ConnectionIncident

logger = logging.getLogger(__name__)


class MT5HealthManager:
    """
    MT5 connection health state machine with exponential backoff.

    Usage:
        manager = MT5HealthManager(states, config)
        # In main loop:
        if not manager.check_and_reconnect():
            # MT5 unavailable — skip this cycle
            continue
    """

    def __init__(self, states: list[Any], config: Any, *, incident: MT5ConnectionIncident | None = None) -> None:
        self._states = states
        self._magic = getattr(config, "BOT_MAGIC", 0)
        self._base_cooldown = float(getattr(config, "MT5_RECONNECT_COOLDOWN_SECONDS", 10.0))
        self._max_cooldown = float(getattr(config, "MT5_RECONNECT_MAX_COOLDOWN_SECONDS", 60.0))

        self.mt5_state: str = MT5_CONNECTED
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_fail_count: int = 0
        self.incident = incident or MT5ConnectionIncident(len(states), logger)

    def mark_unavailable(self, error: tuple) -> None:
        """Feed failures enter the same degraded state as the health probe."""
        self.mt5_state = MT5_DISCONNECTED
        self.incident.lost(error)

    def check_and_reconnect(self) -> bool:
        """
        Check MT5 connection health and attempt reconnect if needed.

        Returns:
            True if MT5 is healthy and trading can proceed.
            False if MT5 is unavailable (caller should skip this cycle).

        Never raises. Preserves existing reconnect behaviour exactly.
        """
        # ─── DISCONNECTED STATE: attempt reconnect with backoff ───────
        if self.mt5_state != MT5_CONNECTED:
            self.incident.check_prolonged()
            now = time.time()
            effective_cooldown = min(
                self._base_cooldown * (2 ** min(self._reconnect_fail_count, 4)),
                self._max_cooldown,
            )
            if now - self._last_reconnect_attempt < effective_cooldown:
                return False  # Still in cooldown — skip cycle

            self._last_reconnect_attempt = now

            # Try first symbol for reconnect (any symbol works — shared MT5 connection)
            if attempt_reconnect(self._states[0].symbol, on_failure=self.mark_unavailable):
                self.mt5_state = MT5_CONNECTED
                self._reconnect_fail_count = 0
                # Re-select all symbols and resync positions after reconnect
                self._resync_all_symbols()
                # Resync can discover a new IPC loss; never announce a false recovery.
                error = mt5_health_error()
                if error is not None:
                    self.mark_unavailable(error)
                elif self.mt5_state == MT5_CONNECTED:
                    self.incident.restored()
            else:
                self._reconnect_fail_count += 1

            return False  # Even on success, skip this cycle (let next iteration proceed normally)

        # ─── CONNECTED STATE: validate health ─────────────────────────
        error = mt5_health_error()
        if error is not None:
            self.mark_unavailable(error)
            return False

        return True  # Healthy — proceed with trading

    def _resync_all_symbols(self) -> None:
        """Re-select all symbols in Market Watch and resync positions after reconnect."""
        for sym_state in self._states:
            try:
                import MetaTrader5 as _mt5
                _mt5.symbol_select(sym_state.symbol, True)
                resync_positions(
                    trade_manager=sym_state.trade_manager,
                    symbol=sym_state.symbol,
                    magic=self._magic,
                )
            except MT5ConnectionError as exc:
                self.mark_unavailable(exc.error)
                break
            except Exception as _resync_exc:
                logger.error(
                    "[RECONNECT_RESYNC_ERROR] symbol=%s error=%s",
                    sym_state.symbol, _resync_exc,
                )
