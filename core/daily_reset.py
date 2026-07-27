"""
D4: Daily State Reset — Deterministic day-boundary reset coordinator.

Ensures all daily-scoped counters reset exactly once per trading day.
Idempotent: safe across restarts, crashes, and multiple evaluations.

Persists last_reset_day_key to prevent duplicate resets.
Emits [DAILY_RESET] event with previous day summary.

Note: DailyLossGuard handles its own internal reset independently.
This module coordinates the broader daily context (trade counts, P&L summary).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_STATE_FILE_DEFAULT = "logs/daily_reset_state.json"


def _get_state_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "DAILY_RESET_STATE_FILE", _STATE_FILE_DEFAULT))
    except ImportError:
        return Path(_STATE_FILE_DEFAULT)


def _get_reset_hour_utc() -> int:
    try:
        from core import config
        return int(getattr(config, "DAILY_RESET_HOUR_UTC", 0))
    except ImportError:
        return 0


def _current_day_key() -> str:
    """Compute current trading day key (YYYY-MM-DD) respecting reset hour."""
    now = datetime.now(tz=timezone.utc)
    reset_hour = _get_reset_hour_utc()
    if now.hour < reset_hour:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def _load_last_reset_key() -> str | None:
    """Load last reset day key from disk. Returns None if missing/invalid."""
    try:
        path = _get_state_path()
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("last_reset_day_key", None)
    except Exception:
        return None


def _persist_reset_key(day_key: str) -> bool:
    """Persist last reset day key to disk. Atomic write."""
    try:
        path = _get_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_reset_day_key": day_key,
            "last_reset_unix": _time.time(),
        }
        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="reset_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(path))
        return True
    except Exception as exc:
        logger.warning("[DAILY_RESET_PERSIST_ERROR] error=%s", exc)
        return False


# ─── DAILY RESET COORDINATOR ──────────────────────────────────────────────────

class DailyResetCoordinator:
    """
    Evaluates day boundary and triggers daily reset exactly once per trading day.

    Call evaluate() once per scanner cycle. It determines whether a new day
    has started and performs reset if needed. Idempotent and restart-safe.
    """

    def __init__(self) -> None:
        self._last_reset_key: str | None = _load_last_reset_key()
        self._reset_performed_this_session: bool = False

        # If loaded key matches today, no reset needed
        today = _current_day_key()
        if self._last_reset_key == today:
            self._reset_performed_this_session = True

    @property
    def last_reset_day(self) -> str | None:
        return self._last_reset_key

    def evaluate(self) -> bool:
        """
        Check if daily reset is needed and perform if so.

        Returns True if reset was performed this call, False otherwise.
        Idempotent: calling multiple times in the same day = no-op after first.
        """
        today = _current_day_key()

        # Same day — no reset needed
        if self._last_reset_key == today:
            return False

        # NEW DAY DETECTED — perform reset
        previous_day = self._last_reset_key or "unknown"

        # Gather previous day summary (from trade journal if available)
        prev_summary = self._get_previous_day_summary(previous_day)

        # Persist BEFORE resetting (ensures reset is durable)
        _persist_reset_key(today)
        self._last_reset_key = today
        self._reset_performed_this_session = True

        # Emit reset event
        logger.info(
            "[DAILY_RESET] date=%s previous_date=%s previous_pnl=%.2f "
            "previous_trades=%d reason=day_boundary",
            today, previous_day,
            prev_summary.get("net_pnl", 0.0),
            prev_summary.get("trades", 0),
        )

        # Send external alert
        try:
            from core.alerting import send_alert, AlertLevel
            send_alert(
                level=AlertLevel.INFO,
                event_type="DAILY_RESET",
                message=f"New trading day: {today}",
                metrics=prev_summary,
                detail={"previous_date": previous_day, "reset_reason": "day_boundary"},
            )
        except Exception:
            pass

        return True

    def _get_previous_day_summary(self, day_key: str) -> dict[str, Any]:
        """Get previous day's trading summary from journal. Never raises."""
        try:
            from core.trade_journal import get_daily_summary
            return get_daily_summary(day_key)
        except Exception:
            return {"date": day_key, "trades": 0, "net_pnl": 0.0}
