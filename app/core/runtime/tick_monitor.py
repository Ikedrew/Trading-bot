"""
Tick Monitor — Tick freshness evaluation and feed health diagnostics.

Evaluates whether a tick is valid/fresh and emits diagnostic events
for stale transitions. Purely observational — never controls flow.

This module OWNS:
    - Tick freshness evaluation via stale_monitor
    - FRESH→STALE transition detection and logging
    - STALE→FRESH recovery detection and logging
    - Feed health event emission
    - Risk guard event emission for stale data
    - Stale debug diagnostics

This module does NOT own:
    - Tick fetching (caller fetches)
    - Flow control (no continue/break)
    - Trading decisions
    - Runtime orchestration
    - Bar retrieval
    - Decision making

Design: pure evaluation — returns TickMonitorResult, never controls flow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.event_stream import emit_feed_health
from core.runtime.risk_event_emitter import emit_risk_guard_result

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TickMonitorResult:
    """Result of tick freshness evaluation."""
    valid: bool
    """True if tick is fresh and processing should continue."""
    stale: bool = False
    """True if tick is stale."""
    error: bool = False
    """True if stale_monitor raised an exception (fail-safe skip)."""


# ─── TICK MONITOR ─────────────────────────────────────────────────────────────

class TickMonitor:
    """
    Evaluates tick freshness and emits diagnostic events.

    Handles:
        - Calling stale_monitor.on_tick()
        - FRESH→STALE transition logging + event emission
        - STALE→FRESH recovery logging + event emission
        - Debug diagnostics for stale ticks
        - Risk guard event emission

    Usage:
        monitor = TickMonitor()
        result = monitor.evaluate(symbol=..., sym_state=..., tick_time=...)
        if not result.valid:
            continue  # Caller owns flow control
    """

    def evaluate(
        self,
        *,
        symbol: str,
        stale_monitor: Any,
        tick_time: float,
    ) -> TickMonitorResult:
        """
        Evaluate tick freshness and emit diagnostics.

        Args:
            symbol: Symbol name for logging.
            stale_monitor: The StaleDataMonitor instance for this symbol.
            tick_time: Timestamp of the last tick from MT5.

        Returns:
            TickMonitorResult with valid=True if tick is fresh.
            TickMonitorResult with valid=False if stale or error.

        Never raises.
        """
        _was_stale = stale_monitor.stale_state

        # Evaluate tick freshness
        try:
            _tick_stale = stale_monitor.on_tick(tick_time, time.time())
        except Exception:
            logger.warning("[STALE_DATA] symbol=%s monitor_error=on_tick — fail-safe skip", symbol)
            return TickMonitorResult(valid=False, error=True)

        if _tick_stale.is_stale:
            # [STALE DEBUG] Diagnostic — surface actual time values
            _now_wall = time.time()
            _last_tick = stale_monitor.last_tick_time
            print(f"[STALE DEBUG] symbol={symbol} now={_now_wall:.0f} tick_time={tick_time} last_tick={_last_tick} diff={tick_time - _last_tick if _last_tick else 'N/A'} stale_since={stale_monitor._tick_stale_since} threshold={stale_monitor.stale_tick_timeout}s")

            # Log FRESH→STALE transition (first detection)
            if not _was_stale:
                logger.warning(
                    "[STALE_DATA] symbol=%s transition=FRESH_TO_STALE type=TICK "
                    "last_tick_time=%d stale_seconds=%.1f",
                    symbol, tick_time, _tick_stale.stale_duration_seconds,
                )
                # Persist feed health transition to events/ (source of truth)
                try:
                    emit_feed_health(symbol, {
                        "transition": "FRESH_TO_STALE",
                        "feed_type": "TICK",
                        "stale_duration_seconds": round(_tick_stale.stale_duration_seconds, 1),
                        "last_tick_time": tick_time,
                        "threshold_seconds": getattr(stale_monitor, "stale_tick_timeout", 60),
                    }, source="stale_monitor")
                except Exception:
                    pass
                # Risk guard event
                emit_risk_guard_result(symbol, "stale_data_monitor", "REJECTED", "data_stale_fresh_to_stale", {
                    "data_age_ms": int(_tick_stale.stale_duration_seconds * 1000),
                    "max_allowed_age_ms": int(getattr(stale_monitor, "stale_tick_timeout", 60) * 1000),
                    "feed_source": "mt5_tick",
                    "layer": "STALE_DATA",
                    "transition": "FRESH_TO_STALE",
                })
            elif _tick_stale.escalation_level >= 2:
                logger.warning(
                    "[STALE_DATA] symbol=%s type=TICK stale_seconds=%.1f "
                    "level=%d action=%s",
                    symbol, _tick_stale.stale_duration_seconds,
                    _tick_stale.escalation_level, _tick_stale.action,
                )

            return TickMonitorResult(valid=False, stale=True)

        # Log STALE→FRESH recovery transition
        if _was_stale and not stale_monitor.stale_state:
            logger.info(
                "[STALE_DATA] symbol=%s transition=STALE_TO_FRESH type=TICK "
                "recovered=true tick_time=%d",
                symbol, tick_time,
            )
            try:
                emit_feed_health(symbol, {
                    "transition": "STALE_TO_FRESH",
                    "feed_type": "TICK",
                    "tick_time": tick_time,
                    "recovery": True,
                }, source="stale_monitor")
            except Exception:
                pass

        return TickMonitorResult(valid=True)
