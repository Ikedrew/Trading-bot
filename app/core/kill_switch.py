"""
Emergency Kill Switch — File-based runtime trading halt.

When the kill switch flag file exists, all NEW trade entries are blocked.
Trade management (SL/TP updates, trailing, position monitoring) continues.

Operator control:
  ACTIVATE:   create  logs/kill_switch.flag
  DEACTIVATE: delete  logs/kill_switch.flag

Zero dependencies. Instant effect. No restart required.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_KILL_SWITCH_PATH = "logs/kill_switch.flag"


def _get_kill_switch_path() -> str:
    try:
        from core import config
        return str(getattr(config, "KILL_SWITCH_PATH", _KILL_SWITCH_PATH))
    except ImportError:
        return _KILL_SWITCH_PATH


# ─── STATE TRACKING (transition detection) ────────────────────────────────────

_last_known_state: bool | None = None  # None = not yet checked


def is_kill_switch_active() -> bool:
    """
    Check if kill switch flag file exists.

    Returns True if trading should be halted.

    Fail-safe behaviour:
      - If filesystem read succeeds: use actual file state.
      - If filesystem read fails AND last known state exists: use last known state.
      - If filesystem read fails AND no prior state: return True (fail-closed).

    This prevents transient I/O issues from causing unnecessary trading halts
    while maintaining fail-closed safety for genuine uncertainty.

    Logs transitions (ACTIVATED / DEACTIVATED) exactly once per state change.
    """
    global _last_known_state

    try:
        active = os.path.exists(_get_kill_switch_path())
    except Exception as exc:
        # Filesystem error — classify and respond
        if _last_known_state is not None:
            # SOFT FAIL: We have a prior valid reading. Use it.
            # Transient I/O (latency, contention) should not hard-stop trading
            # if the last confirmed state was "not active".
            logger.warning(
                "[KILL_SWITCH_IO_ERROR] error=%s — using last_known_state=%s",
                exc, _last_known_state,
            )
            return _last_known_state
        else:
            # HARD FAIL: No prior state. Cannot determine safety. Block trading.
            logger.warning(
                "[KILL_SWITCH_IO_ERROR_FAILSAFE] error=%s — no prior state, blocking entries",
                exc,
            )
            return True

    # Detect transitions
    if _last_known_state is not None and active != _last_known_state:
        if active:
            logger.critical(
                "[KILL_SWITCH_ACTIVATED] path=%s — all new entries blocked",
                _get_kill_switch_path(),
            )
        else:
            logger.info(
                "[KILL_SWITCH_DEACTIVATED] path=%s — normal trading resumed",
                _get_kill_switch_path(),
            )

    _last_known_state = active
    return active


def reset_kill_switch_state() -> None:
    """Reset internal transition tracking (for testing)."""
    global _last_known_state
    _last_known_state = None
