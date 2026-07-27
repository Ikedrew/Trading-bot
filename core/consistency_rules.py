"""
H2: Consistency Rules Compliance — Prop firm performance distribution enforcement.

Ensures trading performance meets prop firm consistency requirements:
1. Daily profit cap — prevents over-earning on a single day
2. Concentration rule — no single day dominates total performance
3. Minimum trading days — sustained activity requirement

Persists daily performance history. Survives restarts.
Resets daily bucket at D4 day boundary.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "CONSISTENCY_RULES_ENABLED", True))
    except ImportError:
        return True


def _get_max_daily_profit() -> float:
    try:
        from core import config
        return float(getattr(config, "MAX_DAILY_PROFIT_PERCENT", 2.0))
    except ImportError:
        return 2.0


def _get_min_trading_days() -> int:
    try:
        from core import config
        return int(getattr(config, "MIN_TRADING_DAYS", 5))
    except ImportError:
        return 5


def _get_max_concentration() -> float:
    try:
        from core import config
        return float(getattr(config, "MAX_SINGLE_DAY_CONTRIBUTION_PERCENT", 40.0))
    except ImportError:
        return 40.0


def _get_lock_after_cap() -> bool:
    try:
        from core import config
        return bool(getattr(config, "LOCK_AFTER_DAILY_PROFIT_CAP", True))
    except ImportError:
        return True


def _get_reset_hour_utc() -> int:
    try:
        from core import config
        return int(getattr(config, "DAILY_RESET_HOUR_UTC", 0))
    except ImportError:
        return 0


def _get_state_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "CONSISTENCY_STATE_FILE", "runtime/consistency_tracker.json"))
    except ImportError:
        return Path("runtime/consistency_tracker.json")


# ─── CURRENT DAY KEY ──────────────────────────────────────────────────────────

def _current_day_key() -> str:
    """Compute current trading day key respecting reset hour."""
    now = datetime.now(tz=timezone.utc)
    reset_hour = _get_reset_hour_utc()
    if now.hour < reset_hour:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


# ─── DATA MODELS ──────────────────────────────────────────────────────────────

REJECT_DAILY_PROFIT_CAP = "MAX_DAILY_PROFIT_EXCEEDED"
REJECT_CONCENTRATION = "CONCENTRATION_LIMIT"
REJECT_MIN_DAYS = "MIN_TRADING_DAYS_NOT_MET"


@dataclass(frozen=True)
class DailyPerformance:
    """Single day's performance record."""
    date: str
    profit_percent: float
    trade_count: int


@dataclass(frozen=True)
class ConsistencyStatus:
    """Full consistency evaluation snapshot."""
    total_profit_percent: float
    daily_profits: list
    active_trading_days: int
    max_daily_profit_percent: float
    today_profit_percent: float
    violates_daily_cap: bool
    violates_concentration_rule: bool
    violates_min_days: bool


@dataclass(frozen=True)
class ConsistencyGuardResult:
    """Result of consistency gate evaluation."""
    allowed: bool
    reason: str = ""
    today_profit_percent: float = 0.0
    max_daily_profit_limit: float = 0.0


# ─── CONSISTENCY TRACKER ──────────────────────────────────────────────────────

class ConsistencyTracker:
    """
    Tracks daily performance and enforces consistency rules.

    Persists history to disk. Survives restarts.
    Call record_trade_result() after each closed trade.
    Call check_gate() before each new trade execution.
    """

    def __init__(self) -> None:
        self._daily_history: dict[str, dict] = {}  # {date: {profit_pct, trade_count}}
        self._today_key: str = _current_day_key()
        self._load()

    @property
    def today_profit_percent(self) -> float:
        entry = self._daily_history.get(self._today_key)
        return entry["profit_pct"] if entry else 0.0

    @property
    def today_trade_count(self) -> int:
        entry = self._daily_history.get(self._today_key)
        return entry["trade_count"] if entry else 0

    @property
    def active_trading_days(self) -> int:
        return sum(1 for d in self._daily_history.values() if d.get("trade_count", 0) > 0)

    def record_trade_result(self, profit_percent: float) -> None:
        """
        Record a closed trade's profit contribution to today's total.

        Must be called AFTER trade closes with realized P&L.
        Persists immediately.
        """
        self._ensure_today()

        entry = self._daily_history[self._today_key]
        entry["profit_pct"] = round(entry["profit_pct"] + profit_percent, 4)
        entry["trade_count"] = entry["trade_count"] + 1

        self._persist()

        logger.info(
            "[CONSISTENCY] trade_recorded day=%s profit=%.4f%% day_total=%.4f%% trades=%d",
            self._today_key, profit_percent, entry["profit_pct"], entry["trade_count"],
        )

    def evaluate(self) -> ConsistencyStatus:
        """Evaluate all consistency rules against current history."""
        self._ensure_today()

        # Gather daily profits
        daily_profits = []
        for day_key, entry in sorted(self._daily_history.items()):
            daily_profits.append(DailyPerformance(
                date=day_key,
                profit_percent=entry["profit_pct"],
                trade_count=entry["trade_count"],
            ))

        # Compute metrics
        total_profit = sum(e["profit_pct"] for e in self._daily_history.values())
        today_entry = self._daily_history.get(self._today_key, {"profit_pct": 0.0})
        today_profit = today_entry["profit_pct"]
        max_daily = max((e["profit_pct"] for e in self._daily_history.values()), default=0.0)
        active_days = self.active_trading_days

        # Rule checks
        max_daily_cap = _get_max_daily_profit()
        violates_cap = today_profit >= max_daily_cap

        # Concentration: max single day / total (only meaningful if total > 0)
        if total_profit > 0 and max_daily > 0:
            concentration = (max_daily / total_profit) * 100.0
            violates_concentration = concentration > _get_max_concentration()
        else:
            violates_concentration = False

        # Min trading days
        violates_min_days = active_days < _get_min_trading_days()

        return ConsistencyStatus(
            total_profit_percent=round(total_profit, 4),
            daily_profits=daily_profits,
            active_trading_days=active_days,
            max_daily_profit_percent=round(max_daily, 4),
            today_profit_percent=round(today_profit, 4),
            violates_daily_cap=violates_cap,
            violates_concentration_rule=violates_concentration,
            violates_min_days=violates_min_days,
        )

    def check_gate(self) -> ConsistencyGuardResult:
        """
        Hard execution gate — blocks new entries when daily profit cap reached.

        Only enforces daily profit cap as a hard block.
        Concentration and min-days are warnings/soft enforcement.
        """
        if not _is_enabled():
            return ConsistencyGuardResult(allowed=True, reason="CONSISTENCY_RULES_DISABLED")

        if not _get_lock_after_cap():
            return ConsistencyGuardResult(allowed=True, reason="LOCK_DISABLED")

        self._ensure_today()

        today_entry = self._daily_history.get(self._today_key, {"profit_pct": 0.0})
        today_profit = today_entry["profit_pct"]
        max_daily_cap = _get_max_daily_profit()

        if today_profit >= max_daily_cap:
            logger.warning(
                "[CONSISTENCY_BLOCK] Reason: %s Daily Profit: %.2f%% Cap: %.2f%%",
                REJECT_DAILY_PROFIT_CAP, today_profit, max_daily_cap,
            )
            return ConsistencyGuardResult(
                allowed=False,
                reason=REJECT_DAILY_PROFIT_CAP,
                today_profit_percent=today_profit,
                max_daily_profit_limit=max_daily_cap,
            )

        return ConsistencyGuardResult(
            allowed=True,
            reason="",
            today_profit_percent=today_profit,
            max_daily_profit_limit=max_daily_cap,
        )

    def reset_day(self) -> None:
        """Called by D4 daily reset — close current day bucket, start new."""
        self._today_key = _current_day_key()
        self._ensure_today()
        self._persist()
        logger.info("[CONSISTENCY] day_reset new_day=%s", self._today_key)

    # ─── PERSISTENCE ──────────────────────────────────────────────────

    def _ensure_today(self) -> None:
        """Ensure today's bucket exists. Handle day rollover."""
        today = _current_day_key()
        if today != self._today_key:
            self._today_key = today
        if self._today_key not in self._daily_history:
            self._daily_history[self._today_key] = {"profit_pct": 0.0, "trade_count": 0}

    def _load(self) -> None:
        """Load persisted state. Never raises."""
        try:
            path = _get_state_path()
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            history = data.get("daily_history", {})
            if isinstance(history, dict):
                self._daily_history = history
                if self._daily_history:
                    logger.info(
                        "[CONSISTENCY] loaded state days=%d",
                        len(self._daily_history),
                    )
        except Exception as exc:
            logger.warning("[CONSISTENCY] load_error=%s", exc)

    def _persist(self) -> None:
        """Persist state to disk. Atomic write. Never raises."""
        try:
            path = _get_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "daily_history": self._daily_history,
                "last_updated": _time.time(),
                "today_key": self._today_key,
            }
            json_bytes = json.dumps(data, indent=2).encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="ct_")
            try:
                os.write(fd, json_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.warning("[CONSISTENCY] persist_error=%s", exc)


# ─── MODULE-LEVEL INSTANCE ────────────────────────────────────────────────────

_tracker: ConsistencyTracker | None = None


def get_tracker() -> ConsistencyTracker:
    """Get or create the singleton consistency tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ConsistencyTracker()
    return _tracker


# ─── CONVENIENCE API ──────────────────────────────────────────────────────────

def check_consistency_gate() -> ConsistencyGuardResult:
    """Check consistency rules before execution. Uses singleton tracker."""
    return get_tracker().check_gate()


def record_trade_result(profit_percent: float) -> None:
    """Record closed trade profit. Uses singleton tracker."""
    get_tracker().record_trade_result(profit_percent)


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_consistency_config() -> list[str]:
    """Validate consistency config at startup. Returns list of errors."""
    errors: list[str] = []
    if not _is_enabled():
        return errors

    cap = _get_max_daily_profit()
    if cap <= 0:
        errors.append(f"MAX_DAILY_PROFIT_PERCENT must be > 0 (got {cap})")

    days = _get_min_trading_days()
    if days <= 0:
        errors.append(f"MIN_TRADING_DAYS must be > 0 (got {days})")

    conc = _get_max_concentration()
    if conc <= 0 or conc > 100:
        errors.append(f"MAX_SINGLE_DAY_CONTRIBUTION_PERCENT must be 0 < x <= 100 (got {conc})")

    return errors
