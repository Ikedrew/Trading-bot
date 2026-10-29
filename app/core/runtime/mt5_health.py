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
    is_mt5_healthy,
    resync_positions,
)

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

    def __init__(self, states: list[Any], config: Any) -> None:
        self._states = states
        self._magic = getattr(config, "BOT_MAGIC", 0)
        self._base_cooldown = float(getattr(config, "MT5_RECONNECT_COOLDOWN_SECONDS", 10.0))
        self._max_cooldown = float(getattr(config, "MT5_RECONNECT_MAX_COOLDOWN_SECONDS", 60.0))

        self.mt5_state: str = MT5_CONNECTED
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_fail_count: int = 0

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
            now = time.time()
            effective_cooldown = min(
                self._base_cooldown * (2 ** min(self._reconnect_fail_count, 4)),
                self._max_cooldown,
            )
            if now - self._last_reconnect_attempt < effective_cooldown:
                return False  # Still in cooldown — skip cycle

            self._last_reconnect_attempt = now

            # Try first symbol for reconnect (any symbol works — shared MT5 connection)
            if attempt_reconnect(self._states[0].symbol):
                self.mt5_state = MT5_CONNECTED
                self._reconnect_fail_count = 0
                logger.info("[LIVE_SCANNER] RECONNECT SUCCESS — resuming all symbols")
                # Re-select all symbols and resync positions after reconnect
                self._resync_all_symbols()
            else:
                self._reconnect_fail_count += 1
                logger.info("[LIVE_SCANNER] RECONNECT FAILED — fail_count=%d", self._reconnect_fail_count)

            return False  # Even on success, skip this cycle (let next iteration proceed normally)

        # ─── CONNECTED STATE: validate health ─────────────────────────
        if not is_mt5_healthy():
            self.mt5_state = MT5_DISCONNECTED
            logger.info("[LIVE_SCANNER] MT5 DISCONNECTED — entering degraded mode")
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
            except Exception as _resync_exc:
                logger.error(
                    "[RECONNECT_RESYNC_ERROR] symbol=%s error=%s",
                    sym_state.symbol, _resync_exc,
                )
