"""
Daily Loss Limit Guard — blocks new entries when intraday loss exceeds threshold.

Uses broker equity as authoritative source:
    daily_loss_pct = ((daily_start_equity - current_equity) / daily_start_equity) * 100

Persists state to disk (survives restart). Resets at day boundary.
Fail-closed: if equity unavailable, blocks trading.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from core import config
from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_STATE_FILE_DEFAULT = "logs/daily_loss_state.json"


def _get_state_path() -> Path:
    return Path(getattr(config, "DAILY_LOSS_STATE_FILE", _STATE_FILE_DEFAULT))


def _get_threshold() -> float:
    return float(getattr(config, "DAILY_LOSS_LIMIT_PERCENT", 4.0))


def _is_enabled() -> bool:
    return bool(getattr(config, "ENABLE_DAILY_LOSS_LIMIT", True))


def _get_reset_hour_utc() -> int:
    return int(getattr(config, "DAILY_RESET_HOUR_UTC", 0))


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

REJECT_DAILY_LOSS_EXCEEDED = "DAILY_LOSS_LIMIT_EXCEEDED"
REJECT_EQUITY_UNAVAILABLE = "DAILY_LOSS_EQUITY_UNAVAILABLE"


@dataclass(frozen=True)
class DailyLossResult:
    """Result of daily loss limit evaluation."""
    allowed: bool
    daily_loss_pct: float = 0.0
    daily_start_equity: float = 0.0
    current_equity: float = 0.0
    threshold: float = 0.0
    reason: str = ""


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

@dataclass
class _DailyLossState:
    """Persisted daily state."""
    date: str  # YYYY-MM-DD
    daily_start_equity: float
    limit_triggered: bool
    last_updated: float


def _today_str() -> str:
    """Current UTC date string using configured reset hour."""
    now = datetime.now(tz=timezone.utc)
    reset_hour = _get_reset_hour_utc()
    # If current hour < reset_hour, we're still in "yesterday's" trading day
    if now.hour < reset_hour:
        from datetime import timedelta
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def _load_state() -> _DailyLossState | None:
    """Load persisted daily loss state. Returns None if missing/invalid."""
    try:
        path = _get_state_path()
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        date_val = data.get("date", "")
        equity_val = data.get("daily_start_equity", 0)
        if not date_val or not isinstance(equity_val, (int, float)) or equity_val <= 0:
            return None
        return _DailyLossState(
            date=str(date_val),
            daily_start_equity=float(equity_val),
            limit_triggered=bool(data.get("limit_triggered", False)),
            last_updated=float(data.get("last_updated", 0)),
        )
    except Exception as exc:
        logger.warning("[DAILY_LOSS_GUARD] state_load_error=%s", exc)
        return None


def _persist_state(state: _DailyLossState) -> bool:
    """Persist daily loss state to disk. Atomic write. Never raises."""
    try:
        path = _get_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "date": state.date,
            "daily_start_equity": round(state.daily_start_equity, 4),
            "limit_triggered": state.limit_triggered,
            "last_updated": _time.time(),
        }
        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="daily_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, str(path))
        return True
    except Exception as exc:
        logger.warning("[DAILY_LOSS_GUARD] persist_error=%s", exc)
        return False


# ─── DAILY LOSS GUARD ─────────────────────────────────────────────────────────

class DailyLossGuard:
    """
    Tracks daily starting equity and blocks new entries when daily loss exceeds threshold.

    Uses broker equity (balance + unrealised P&L) as authoritative source.
    Persists state to disk — survives restarts. Resets at configured day boundary.

    Usage:
        guard = DailyLossGuard()
        result = guard.check()
        if not result.allowed:
            # block new entries (trade management continues)
    """

    def __init__(self) -> None:
        self._state: _DailyLossState | None = _load_state()
        self._limit_logged: bool = False

        # Validate loaded state is for today
        if self._state is not None:
            if self._state.date != _today_str():
                # New day — will reinitialise on first check
                logger.info(
                    "[DAILY_LOSS_LIMIT_RESET] previous_date=%s new_date=%s",
                    self._state.date, _today_str(),
                )
                self._state = None
                self._limit_logged = False
            elif self._state.limit_triggered:
                logger.warning(
                    "[DAILY_LOSS_LIMIT_ACTIVE] restored_from_disk date=%s "
                    "daily_start_equity=%.2f limit_triggered=true",
                    self._state.date, self._state.daily_start_equity,
                )
                self._limit_logged = True

    @property
    def daily_start_equity(self) -> float:
        return self._state.daily_start_equity if self._state else 0.0

    @property
    def is_triggered(self) -> bool:
        return self._state.limit_triggered if self._state else False

    def check(self) -> DailyLossResult:
        """
        Evaluate current daily loss against threshold.

        Returns DailyLossResult with allowed=True if trading is permitted.
        Fail-closed: returns allowed=False if equity is unavailable.
        """
        if not _is_enabled():
            return DailyLossResult(allowed=True, reason="DISABLED")

        threshold = _get_threshold()

        # Fetch current equity from broker
        try:
            info = mt5_call(mt5.account_info)
        except Exception as exc:
            logger.error("[DAILY_LOSS_GUARD] equity_error=%s — fail-closed", exc)
            return DailyLossResult(
                allowed=False,
                reason=REJECT_EQUITY_UNAVAILABLE,
            )

        if info is None:
            return DailyLossResult(
                allowed=False,
                reason=REJECT_EQUITY_UNAVAILABLE,
            )

        current_equity = float(info.equity)
        today = _today_str()

        # ─── DAY BOUNDARY DETECTION ───────────────────────────────────
        if self._state is None or self._state.date != today:
            # New trading day: establish baseline
            self._state = _DailyLossState(
                date=today,
                daily_start_equity=current_equity,
                limit_triggered=False,
                last_updated=_time.time(),
            )
            _persist_state(self._state)
            self._limit_logged = False
            logger.info(
                "[DAILY_LOSS_LIMIT_RESET] date=%s daily_start_equity=%.2f",
                today, current_equity,
            )
            return DailyLossResult(
                allowed=True,
                daily_loss_pct=0.0,
                daily_start_equity=current_equity,
                current_equity=current_equity,
                threshold=threshold,
            )
        # ─── END DAY BOUNDARY ─────────────────────────────────────────

        start_equity = self._state.daily_start_equity

        # Safety: start equity must be positive
        if start_equity <= 0:
            return DailyLossResult(allowed=True, reason="INVALID_START_EQUITY")

        # Calculate daily loss
        daily_loss_pct = ((start_equity - current_equity) / start_equity) * 100.0

        # Check threshold
        if daily_loss_pct >= threshold:
            if not self._state.limit_triggered:
                self._state = _DailyLossState(
                    date=today,
                    daily_start_equity=start_equity,
                    limit_triggered=True,
                    last_updated=_time.time(),
                )
                _persist_state(self._state)
                logger.critical(
                    "[DAILY_LOSS_LIMIT_TRIGGERED] date=%s daily_loss=%.2f%% "
                    "threshold=%.2f%% start_equity=%.2f current_equity=%.2f",
                    today, daily_loss_pct, threshold, start_equity, current_equity,
                )

            if not self._limit_logged:
                self._limit_logged = True

            return DailyLossResult(
                allowed=False,
                daily_loss_pct=round(daily_loss_pct, 4),
                daily_start_equity=start_equity,
                current_equity=current_equity,
                threshold=threshold,
                reason=REJECT_DAILY_LOSS_EXCEEDED,
            )

        # Below threshold — trading allowed
        return DailyLossResult(
            allowed=True,
            daily_loss_pct=round(daily_loss_pct, 4),
            daily_start_equity=start_equity,
            current_equity=current_equity,
            threshold=threshold,
        )

    def reset_for_new_day(self, equity: float) -> None:
        """Manually reset for a new day (e.g., testing or operator override)."""
        today = _today_str()
        self._state = _DailyLossState(
            date=today,
            daily_start_equity=equity,
            limit_triggered=False,
            last_updated=_time.time(),
        )
        _persist_state(self._state)
        self._limit_logged = False
        logger.info("[DAILY_LOSS_LIMIT_RESET] manual date=%s equity=%.2f", today, equity)
