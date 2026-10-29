"""
Health Monitor — Cycle-level health observation for the live scanner.

Manages heartbeat writing, no-trade alerting, liveness/stall detection,
and Discord heartbeat throttling. Purely observational — never controls
runtime flow.

This module OWNS:
    - Heartbeat file writing (logs/heartbeat.json)
    - No-trade cycle tracking and alerting
    - Liveness stall detection
    - Discord heartbeat (throttled)
    - log_heartbeat / log_liveness_status dispatch

This module does NOT own:
    - Runtime loop control
    - continue/break decisions
    - Sleep/poll timing
    - MT5 reconnect logic
    - Trading decisions
    - Risk decisions
    - Execution logic
    - Strategy logic
    - Cycle scheduling
    - Configuration ownership
    - Diagnostic reports (score pressure, calibration, dashboard)

Design: observational service — never raises to caller, never controls flow.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

from core.event_bus import log_heartbeat, log_liveness_status
from core.quiet_period_diagnostics import emit_quiet_period_alert

logger = logging.getLogger(__name__)


class HealthMonitor:
    """
    Cycle-level health observation service.

    Handles:
        - Heartbeat file writes (atomic JSON)
        - No-trade alert tracking
        - Liveness/stall detection
        - Discord heartbeat emission (throttled)
        - log_heartbeat / log_liveness_status calls

    Usage:
        monitor = HealthMonitor(n_symbols=len(states), config=config)
        # At end of each cycle:
        monitor.tick(cycle_id, cycle_latency_s, mt5_state, cycle_had_trade)
        # At early-exit points:
        monitor.write_heartbeat("mt5_disconnected", cycle_id, 0, mt5_state)
    """

    def __init__(self, n_symbols: int, config: Any) -> None:
        self._n_symbols = n_symbols
        self._config = config
        self._no_trade_threshold: int = int(getattr(config, "NO_TRADE_ALERT_THRESHOLD", 100))
        self._no_trade_repeat: int = int(getattr(config, "NO_TRADE_ALERT_REPEAT_INTERVAL", 25))
        self._stall_threshold: float = float(getattr(config, "LIVENESS_STALL_THRESHOLD_SECONDS", 10.0))
        self._consecutive_no_trade_cycles: int = 0

    @property
    def consecutive_no_trade_cycles(self) -> int:
        """Current consecutive no-trade cycle count (read-only)."""
        return self._consecutive_no_trade_cycles

    def write_heartbeat(self, status: str, cycle_id: int, latency_ms: int, mt5_state: str) -> None:
        """
        Write heartbeat JSON file. Never raises. Purely observational.

        Called from multiple paths:
            - "alive" at end of successful cycle
            - "mt5_disconnected" on MT5 health failure
            - "drawdown_blocked" on drawdown guard block
        """
        try:
            _hb_dir = "logs"
            _hb_path = os.path.join(_hb_dir, "heartbeat.json")
            os.makedirs(_hb_dir, exist_ok=True)
            _hb_data = json.dumps({
                "timestamp": round(time.time(), 3),
                "cycle_id": cycle_id,
                "status": status,
                "latency_ms": latency_ms,
                "symbols": self._n_symbols,
                "mt5_state": mt5_state,
            }, separators=(",", ":")).encode("utf-8")
            _fd, _tmp = tempfile.mkstemp(dir=_hb_dir, suffix=".tmp", prefix="hb_")
            try:
                os.write(_fd, _hb_data)
                os.fsync(_fd)
            finally:
                os.close(_fd)
            os.replace(_tmp, _hb_path)
        except Exception:
            pass  # Heartbeat failure must never crash runtime

    def tick(self, cycle_id: int, cycle_latency_s: float, mt5_state: str, cycle_had_trade: bool = False, *, cycle_had_fill: bool | None = None) -> None:
        """
        End-of-cycle health observation. Never raises.

        Performs:
            1. No-trade alert tracking
            2. Heartbeat log emission
            3. Heartbeat file write
            4. Discord heartbeat (throttled: every 10 cycles)
            5. Stall detection + liveness status

        Args:
            cycle_id: Current cycle number.
            cycle_latency_s: Elapsed time for this cycle in seconds.
            mt5_state: Current MT5 connection state string.
            cycle_had_trade: Legacy flag (EXECUTE decision generated). Preserved for compatibility.
            cycle_had_fill: Whether broker confirmed a fill this cycle. Takes priority if provided.
        """
        # Use fill confirmation if available, otherwise fall back to legacy flag
        _trade_confirmed = cycle_had_fill if cycle_had_fill is not None else cycle_had_trade

        # ─── NO-TRADE ALERT ───────────────────────────────────────────
        try:
            if _trade_confirmed:
                self._consecutive_no_trade_cycles = 0
            else:
                self._consecutive_no_trade_cycles += 1
                if (
                    self._consecutive_no_trade_cycles >= self._no_trade_threshold
                    and (
                        self._consecutive_no_trade_cycles == self._no_trade_threshold
                        or self._consecutive_no_trade_cycles % self._no_trade_repeat == 0
                    )
                ):
                    logger.warning(
                        "[ALERT] consecutive_no_trade_cycles=%d threshold=%d symbols=%d runtime=LIVE",
                        self._consecutive_no_trade_cycles, self._no_trade_threshold, self._n_symbols,
                    )
                    # F2: Emit quiet period diagnostic with rejection breakdown
                    try:
                        emit_quiet_period_alert(self._consecutive_no_trade_cycles)
                    except Exception:
                        pass
        except Exception:
            pass
        # ─── END NO-TRADE ALERT ───────────────────────────────────────

        # ─── HEARTBEAT + LIVENESS ─────────────────────────────────────
        try:
            # Emit heartbeat log
            log_heartbeat(cycle_id, time.time(), "ALL", mt5_state)

            # Write heartbeat file
            self.write_heartbeat("alive", cycle_id, int(cycle_latency_s * 1000), mt5_state)

            # Discord heartbeat (throttled: every 10 cycles)
            if cycle_id % 10 == 0:
                try:
                    _dl = getattr(self._config, "_discord_logger", None)
                    if _dl is not None:
                        _dl.event("HEARTBEAT", {
                            "cycle": cycle_id,
                            "latency_ms": int(cycle_latency_s * 1000),
                            "symbols": self._n_symbols,
                            "mt5": mt5_state,
                        })
                except Exception:
                    pass

            # Stall detection
            if cycle_latency_s > self._stall_threshold:
                logger.warning(
                    "[LIVENESS_STALL] cycle_id=%d latency_seconds=%.2f "
                    "threshold_seconds=%.1f symbols=%d",
                    cycle_id, cycle_latency_s, self._stall_threshold, self._n_symbols,
                )
                log_liveness_status("STALLED", cycle_latency_s, cycle_id)
            else:
                log_liveness_status("OK", cycle_latency_s, cycle_id)

        except Exception:
            pass  # Heartbeat failure must never crash runtime
        # ─── END HEARTBEAT + LIVENESS ─────────────────────────────────
