"""
H4: Weekend Position Protection — Time-based exposure safety gate.

Prevents weekend gap risk by:
1. Optionally flattening all positions before Friday market close
2. Blocking new entries during the weekend window

This is a time-based exposure safety gate, not a strategy modification.

"No unintended exposure exists over the weekend boundary."
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _flatten_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "FLATTEN_BEFORE_WEEKEND", True))
    except ImportError:
        return True


def _block_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "BLOCK_NEW_TRADES_BEFORE_WEEKEND", True))
    except ImportError:
        return True


def _get_flatten_hour() -> int:
    try:
        from core import config
        return int(getattr(config, "FRIDAY_FLATTEN_HOUR_UTC", 20))
    except ImportError:
        return 20


def _get_state_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "WEEKEND_STATE_FILE", "runtime/weekend_state.json"))
    except ImportError:
        return Path("runtime/weekend_state.json")


# ─── RESULT TYPES ─────────────────────────────────────────────────────────────

REJECT_WEEKEND_BLOCK = "WEEKEND_TRADING_BLOCKED"

ACTION_ALLOW = "ALLOW"
ACTION_BLOCK = "BLOCK"
ACTION_FLATTEN = "FLATTEN_REQUIRED"


@dataclass(frozen=True)
class WeekendGateResult:
    """Result of weekend gate evaluation."""
    allowed: bool
    reason: str | None = None
    action: str = ACTION_ALLOW


# ─── WEEKEND STATE DETECTION ──────────────────────────────────────────────────

def is_friday_close_window(current_time: datetime | None = None) -> bool:
    """
    Detect if we are in the Friday pre-close flatten window.

    Returns True if:
    - Day is Friday AND hour >= FRIDAY_FLATTEN_HOUR_UTC
    - Day is Saturday or Sunday (full weekend)
    """
    now = current_time or datetime.now(tz=timezone.utc)
    weekday = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    # Saturday or Sunday — full weekend
    if weekday >= 5:
        return True

    # Friday after flatten hour
    if weekday == 4 and now.hour >= _get_flatten_hour():
        return True

    return False


def is_weekend_over(current_time: datetime | None = None) -> bool:
    """
    Detect if the weekend period has ended (Monday session start).

    Returns True if current day is Monday–Thursday, or Friday before flatten hour.
    """
    now = current_time or datetime.now(tz=timezone.utc)
    weekday = now.weekday()

    if weekday <= 3:  # Mon–Thu
        return True
    if weekday == 4 and now.hour < _get_flatten_hour():  # Friday before flatten
        return True
    return False


# ─── PRE-TRADE GATE ──────────────────────────────────────────────────────────

def check_weekend_gate(current_time: datetime | None = None) -> WeekendGateResult:
    """
    Pre-trade gate — blocks new entries during weekend window.

    Must be called BEFORE execution.place_market().

    Returns:
        WeekendGateResult with allowed=False during weekend window.
    """
    if not _block_enabled():
        return WeekendGateResult(allowed=True, action=ACTION_ALLOW)

    now = current_time or datetime.now(tz=timezone.utc)

    if is_friday_close_window(now):
        logger.info(
            "[WEEKEND_GUARD] Trading blocked: Friday flatten window active "
            "hour=%d flatten_hour=%d weekday=%d",
            now.hour, _get_flatten_hour(), now.weekday(),
        )
        return WeekendGateResult(
            allowed=False,
            reason=REJECT_WEEKEND_BLOCK,
            action=ACTION_BLOCK,
        )

    return WeekendGateResult(allowed=True, action=ACTION_ALLOW)


# ─── FLATTEN LOGIC ────────────────────────────────────────────────────────────

def flatten_all_positions(
    *,
    trade_managers: list[Any] | None = None,
    execution: Any | None = None,
    reason: str = "WEEKEND_FLATTEN",
) -> int:
    """
    Close all open positions before weekend.

    Iterates all TradeStateManagers, closes each open position
    via the execution layer.

    Args:
        trade_managers: List of TradeStateManager instances.
        execution: MT5Execution instance for closing.
        reason: Reason string for logging.

    Returns:
        Number of positions closed.
    """
    if not _flatten_enabled():
        return 0

    if not trade_managers or execution is None:
        return 0

    closed = 0
    total_positions = 0

    for tm in trade_managers:
        if tm is None:
            continue
        positions = tm.positions_open()
        total_positions += len(positions)

        for pos in positions:
            if pos.mt5_ticket is None or pos.mt5_ticket <= 0:
                continue
            try:
                result = execution.close_position(
                    symbol=pos.symbol,
                    position_ticket=int(pos.mt5_ticket),
                    volume=None,  # Full close
                )
                if result.ok:
                    closed += 1
                    logger.info(
                        "[WEEKEND_FLATTEN] Closed position symbol=%s ticket=%d "
                        "volume=%.4f reason=%s",
                        pos.symbol, pos.mt5_ticket, pos.volume, reason,
                    )
                    # Discord: weekend flatten close
                    try:
                        from core import config as _cfg
                        _dl = getattr(_cfg, "_discord_logger", None)
                        if _dl is not None:
                            _dl.event("TRADE_CLOSED", {
                                "symbol": pos.symbol,
                                "ticket": pos.mt5_ticket,
                                "reason": "weekend flatten",
                                "details": {
                                    "entry_price": getattr(pos, "entry_price", None),
                                    "exit_price": None,
                                    "pnl": None,
                                    "duration": None,
                                    "close_type": "forced",
                                },
                            })
                    except Exception:
                        pass
                else:
                    logger.warning(
                        "[WEEKEND_FLATTEN] Failed to close symbol=%s ticket=%d "
                        "retcode=%d",
                        pos.symbol, pos.mt5_ticket, result.retcode,
                    )
            except Exception as exc:
                logger.error(
                    "[WEEKEND_FLATTEN] Error closing symbol=%s ticket=%d: %s",
                    pos.symbol, pos.mt5_ticket, exc,
                )

    if closed > 0 or total_positions > 0:
        logger.info(
            "[WEEKEND_FLATTEN] Closing %d/%d positions before weekend reason=%s",
            closed, total_positions, reason,
        )

    # Persist flatten event
    _persist_weekend_state(closed=closed, reason=reason)

    return closed


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def _persist_weekend_state(closed: int = 0, reason: str = "") -> bool:
    """Persist weekend state for restart awareness."""
    try:
        path = _get_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "weekend_mode_active": True,
            "last_flatten_time": datetime.now(tz=timezone.utc).isoformat(),
            "positions_closed": closed,
            "reason": reason,
            "last_updated": _time.time(),
        }
        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="wk_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(path))
        return True
    except Exception as exc:
        logger.warning("[WEEKEND_PROTECTION] persist_error=%s", exc)
        return False


def load_weekend_state() -> dict | None:
    """Load persisted weekend state. Returns None if missing."""
    try:
        path = _get_state_path()
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clear_weekend_state() -> None:
    """Clear weekend state (called on Monday reset)."""
    try:
        path = _get_state_path()
        if path.exists():
            path.unlink()
        logger.info("[WEEKEND_PROTECTION] weekend state cleared — trading resumed")
    except Exception as exc:
        logger.warning("[WEEKEND_PROTECTION] clear_error=%s", exc)


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_weekend_config() -> list[str]:
    """Validate weekend protection config at startup."""
    errors: list[str] = []
    hour = _get_flatten_hour()
    if hour < 0 or hour > 23:
        errors.append(f"FRIDAY_FLATTEN_HOUR_UTC must be 0-23 (got {hour})")
    return errors
