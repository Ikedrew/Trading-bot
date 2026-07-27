"""
Session Guard — Hard pre-execution gate based on trading hours.

Deterministic allow/block decision derived entirely from:
  - Current UTC time
  - Current UTC weekday
  - Configuration

No persistence required. No MT5 calls. No state mutation.
This is an ENTRY GATE — blocks new entries only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

def _get_trading_hours_start() -> int:
    try:
        from core import config
        return int(getattr(config, "TRADING_HOURS_START_UTC", 7))
    except ImportError:
        return 7


def _get_trading_hours_end() -> int:
    try:
        from core import config
        return int(getattr(config, "TRADING_HOURS_END_UTC", 21))
    except ImportError:
        return 21


def _get_friday_cutoff() -> int:
    try:
        from core import config
        return int(getattr(config, "BLOCK_FRIDAY_AFTER_HOUR", 20))
    except ImportError:
        return 20


def _get_sunday_open() -> int:
    try:
        from core import config
        return int(getattr(config, "BLOCK_SUNDAY_BEFORE_HOUR", 22))
    except ImportError:
        return 22


def _is_session_guard_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "SESSION_GUARD_ENABLED", True))
    except ImportError:
        return True


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionGuardResult:
    """Result of session guard evaluation."""
    allowed: bool
    reason: str
    hour: int = 0
    weekday: int = 0  # 0=Monday, 6=Sunday


# ─── GUARD EVALUATION ─────────────────────────────────────────────────────────

def check_session(*, now_utc: datetime | None = None) -> SessionGuardResult:
    """
    Evaluate whether current UTC time permits new trade entries.

    Pure function: deterministic from time + config. No state. No side effects.
    No MT5 calls. No persistence.

    Args:
        now_utc: Current UTC datetime (injectable for testing). Defaults to now.

    Returns:
        SessionGuardResult with allowed=True if trading permitted.
    """
    if not _is_session_guard_enabled():
        return SessionGuardResult(allowed=True, reason="SESSION_GUARD_DISABLED")

    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    hour = now_utc.hour
    weekday = now_utc.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    # ─── SATURDAY: Market closed ──────────────────────────────────
    if weekday == 5:
        _log_block("MARKET_CLOSED_SATURDAY", hour, weekday)
        return SessionGuardResult(
            allowed=False,
            reason="SESSION_BLOCKED:MARKET_CLOSED_SATURDAY",
            hour=hour,
            weekday=weekday,
        )

    # ─── SUNDAY: Before configured open ───────────────────────────
    sunday_open = _get_sunday_open()
    if weekday == 6 and hour < sunday_open:
        _log_block("SUNDAY_CLOSED", hour, weekday)
        return SessionGuardResult(
            allowed=False,
            reason="SESSION_BLOCKED:SUNDAY_CLOSED",
            hour=hour,
            weekday=weekday,
        )

    # ─── SUNDAY: After configured open → allowed (overrides trading hours) ─
    if weekday == 6 and hour >= sunday_open:
        return SessionGuardResult(
            allowed=True,
            reason="SESSION_OPEN",
            hour=hour,
            weekday=weekday,
        )

    # ─── FRIDAY: After configured cutoff ──────────────────────────
    friday_cutoff = _get_friday_cutoff()
    if weekday == 4 and hour >= friday_cutoff:
        _log_block("FRIDAY_CUTOFF", hour, weekday)
        return SessionGuardResult(
            allowed=False,
            reason="SESSION_BLOCKED:FRIDAY_CUTOFF",
            hour=hour,
            weekday=weekday,
        )

    # ─── TRADING HOURS WINDOW ─────────────────────────────────────
    start_hour = _get_trading_hours_start()
    end_hour = _get_trading_hours_end()

    if hour < start_hour or hour >= end_hour:
        _log_block("OUTSIDE_TRADING_HOURS", hour, weekday)
        return SessionGuardResult(
            allowed=False,
            reason="SESSION_BLOCKED:OUTSIDE_TRADING_HOURS",
            hour=hour,
            weekday=weekday,
        )

    # ─── ALLOWED ──────────────────────────────────────────────────
    return SessionGuardResult(
        allowed=True,
        reason="SESSION_OPEN",
        hour=hour,
        weekday=weekday,
    )


# ─── LOGGING (throttled — logs only on state changes) ─────────────────────────

_last_logged_reason: str | None = None


def _log_block(reason: str, hour: int, weekday: int) -> None:
    """Log session block. Throttled to once per reason change."""
    global _last_logged_reason
    if reason != _last_logged_reason:
        _day_names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        day_name = _day_names[weekday] if 0 <= weekday <= 6 else "?"
        logger.info(
            "[SESSION_BLOCKED] reason=%s hour=%d weekday=%s",
            reason, hour, day_name,
        )
        _last_logged_reason = reason


def reset_session_log_state() -> None:
    """Reset throttle state (for testing)."""
    global _last_logged_reason
    _last_logged_reason = None
