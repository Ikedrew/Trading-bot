"""
Runtime State Classifier — Gap detection and runtime health classification.

Detects runtime gaps between cycles, classifies gap types, and emits
diagnostic events. Purely observational — never controls flow.

This module OWNS:
    - Gap detection (time between cycles)
    - Gap type classification (MT5_DISCONNECT / HOST_SUSPEND / EVENT_LOOP_STALL)
    - Runtime incident logging
    - Event emission for runtime health
    - Discord incident alerting

This module does NOT own:
    - Runtime loop control
    - Trading decisions
    - Shutdown handling
    - Execution authority
    - Cycle scheduling

Design: observational classifier — never raises, never controls flow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.event_stream import emit_system_health
from core.mt5_connection import MT5_CONNECTED

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuntimeGapEvent:
    """A detected runtime gap event."""
    gap_type: str
    """MT5_DISCONNECT, HOST_SUSPEND, or EVENT_LOOP_STALL."""
    gap_seconds: float
    gap_minutes: int
    last_cycle: int
    resumed_cycle: int
    mt5_state: str


# ─── RUNTIME STATE CLASSIFIER ─────────────────────────────────────────────────

class RuntimeStateClassifier:
    """
    Detects and classifies runtime gaps between cycles.

    Tracks the wall-clock time of each cycle and detects gaps >60 seconds.
    Classifies gap types and emits diagnostic events.

    Usage:
        classifier = RuntimeStateClassifier()
        # At start of each cycle:
        classifier.check_gap(cycle_id=cycle_id, cycle_start=time.time(),
                            mt5_state=mt5_state, config=config)
    """

    def __init__(self) -> None:
        self._last_cycle_wall: float = time.time()

    def check_gap(
        self,
        *,
        cycle_id: int,
        cycle_start: float,
        mt5_state: str,
        config: Any,
    ) -> RuntimeGapEvent | None:
        """
        Check for runtime gap and emit diagnostics if detected.

        Updates internal timestamp tracking. Never raises.

        Args:
            cycle_id: Current cycle number.
            cycle_start: Current cycle start timestamp.
            mt5_state: Current MT5 connection state string.
            config: Configuration object (for Discord logger).

        Returns:
            RuntimeGapEvent if a gap was detected, None otherwise.
        """
        result: RuntimeGapEvent | None = None

        if cycle_id > 1:
            _gap_s = cycle_start - self._last_cycle_wall
            if _gap_s > 60:
                _gap_min = int(_gap_s / 60)
                # Classify gap type
                if mt5_state != MT5_CONNECTED:
                    _gap_type = "MT5_DISCONNECT"
                elif _gap_s > 300:
                    _gap_type = "HOST_SUSPEND"
                else:
                    _gap_type = "EVENT_LOOP_STALL"

                logger.warning(
                    "[RUNTIME_CLASSIFIER] type=%s gap_minutes=%d last_cycle=%d resumed_cycle=%d",
                    _gap_type, _gap_min, cycle_id - 1, cycle_id,
                )

                # Persist runtime incident to events/ layer (source of truth)
                try:
                    emit_system_health({
                        "incident_type": _gap_type,
                        "gap_minutes": _gap_min,
                        "gap_seconds": int(_gap_s),
                        "last_cycle": cycle_id - 1,
                        "resumed_cycle": cycle_id,
                        "mt5_state": mt5_state,
                    }, source="live_scanner:runtime_classifier")
                except Exception:
                    pass

                # Discord: runtime incident alert
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    _dl = getattr(config, "_discord_logger", None)
                    if _dl is not None:
                        _dl.event("ERROR", {
                            "location": "live_scanner:runtime_classifier",
                            "error_type": _gap_type,
                            "message": f"Runtime gap detected: {_gap_min}m ({_gap_type})",
                            "incident": {
                                "type": _gap_type,
                                "gap_minutes": _gap_min,
                                "cycles": f"{cycle_id - 1} → {cycle_id}",
                                "mt5_state": mt5_state,
                                "time": _dt.now(tz=_tz.utc).strftime("%Y-%m-%d %H:%M UTC"),
                            },
                        })
                except Exception:
                    pass

                result = RuntimeGapEvent(
                    gap_type=_gap_type,
                    gap_seconds=_gap_s,
                    gap_minutes=_gap_min,
                    last_cycle=cycle_id - 1,
                    resumed_cycle=cycle_id,
                    mt5_state=mt5_state,
                )

        self._last_cycle_wall = cycle_start
        return result
