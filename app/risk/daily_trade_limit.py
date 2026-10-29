"""
A4: Daily Trade Limit Guard — Deterministic trade-frequency risk control.

Prevents runaway execution, overtrading, transaction-cost blowups,
and prop-firm consistency violations.

Tracks:
- Total trades opened today (global cap)
- Trades opened per symbol today (per-symbol cap)

Persists to disk immediately on every trade registration.
Resets ONLY via D4 Daily Reset evaluation (day boundary).
Survives restart, crash, warm-start, and checkpoint recovery.

Only counts SUCCESSFUL broker fills. Rejected orders do NOT increment.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_STATE_FILE_DEFAULT = "logs/daily_trade_limit_state.json"


def _get_state_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "DAILY_TRADE_LIMIT_STATE_FILE", _STATE_FILE_DEFAULT))
    except ImportError:
        return Path(_STATE_FILE_DEFAULT)


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "DAILY_TRADE_LIMIT_ENABLED", True))
    except ImportError:
        return True


def _get_max_total() -> int:
    try:
        from core import config
        return int(getattr(config, "MAX_TRADES_PER_DAY_TOTAL", 20))
    except ImportError:
        return 20


def _get_max_per_symbol() -> int:
    try:
        from core import config
        return int(getattr(config, "MAX_TRADES_PER_DAY_PER_SYMBOL", 5))
    except ImportError:
        return 5


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


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

REJECT_GLOBAL_LIMIT = "DAILY_TRADE_LIMIT_GLOBAL"
REJECT_SYMBOL_LIMIT = "DAILY_TRADE_LIMIT_SYMBOL"


@dataclass(frozen=True)
class DailyTradeLimitResult:
    """Result of daily trade limit evaluation."""
    allowed: bool
    reason: str = ""
    remaining_total: int = 0
    remaining_symbol: int = 0


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def _load_state(path: Path | None = None) -> dict[str, Any] | None:
    """Load persisted daily trade limit state. Returns None if missing/invalid."""
    try:
        p = path or _get_state_path()
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if "current_day_key" not in data:
            return None
        return data
    except Exception as exc:
        logger.warning("[DAILY_TRADE_LIMIT] state_load_error=%s", exc)
        return None


def _persist_state(state: dict[str, Any], path: Path | None = None) -> bool:
    """Persist daily trade limit state to disk. Atomic write. Never raises."""
    try:
        p = path or _get_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        json_bytes = json.dumps(state, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix="dtl_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(p))
        return True
    except Exception as exc:
        logger.warning("[DAILY_TRADE_LIMIT] persist_error=%s", exc)
        return False


# ─── DAILY TRADE LIMIT MANAGER ────────────────────────────────────────────────

class DailyTradeLimitManager:
    """
    Deterministic trade-frequency risk control.

    Tracks total and per-symbol trade counts per trading day.
    Blocks new entries when limits are reached.
    Persists state immediately on every successful trade registration.
    Resets only at day boundary (D4 integration).

    Usage:
        manager = DailyTradeLimitManager()
        result = manager.can_open_trade("EURUSD_SB")
        if not result.allowed:
            # block new entry
        ...
        # After successful broker fill:
        manager.record_trade_open("EURUSD_SB")
    """

    def __init__(self) -> None:
        self._current_day_key: str = ""
        self._total_trades_today: int = 0
        self._per_symbol: dict[str, int] = {}
        self._load()

    @property
    def total_trades_today(self) -> int:
        return self._total_trades_today

    @property
    def per_symbol_counts(self) -> dict[str, int]:
        return dict(self._per_symbol)

    @property
    def current_day_key(self) -> str:
        return self._current_day_key

    def can_open_trade(self, symbol: str) -> DailyTradeLimitResult:
        """
        Check if a new trade is allowed for this symbol.

        Returns DailyTradeLimitResult with:
        - allowed: bool
        - reason: str (empty if allowed)
        - remaining_total: int
        - remaining_symbol: int
        """
        if not _is_enabled():
            return DailyTradeLimitResult(
                allowed=True,
                reason="DISABLED",
                remaining_total=_get_max_total(),
                remaining_symbol=_get_max_per_symbol(),
            )

        # Day boundary check — reset if new day detected
        self._check_day_boundary()

        max_total = _get_max_total()
        max_per_symbol = _get_max_per_symbol()
        symbol_count = self._per_symbol.get(symbol, 0)

        remaining_total = max(0, max_total - self._total_trades_today)
        remaining_symbol = max(0, max_per_symbol - symbol_count)

        # Rule 1: Global limit
        if self._total_trades_today >= max_total:
            logger.warning(
                "[DAILY_TRADE_LIMIT_BLOCK] symbol=%s total=%d/%d "
                "symbol_count=%d/%d reason=global_limit",
                symbol, self._total_trades_today, max_total,
                symbol_count, max_per_symbol,
            )
            return DailyTradeLimitResult(
                allowed=False,
                reason=REJECT_GLOBAL_LIMIT,
                remaining_total=0,
                remaining_symbol=remaining_symbol,
            )

        # Rule 2: Per-symbol limit
        if symbol_count >= max_per_symbol:
            logger.warning(
                "[DAILY_TRADE_LIMIT_BLOCK] symbol=%s total=%d/%d "
                "symbol_count=%d/%d reason=symbol_limit",
                symbol, self._total_trades_today, max_total,
                symbol_count, max_per_symbol,
            )
            return DailyTradeLimitResult(
                allowed=False,
                reason=REJECT_SYMBOL_LIMIT,
                remaining_total=remaining_total,
                remaining_symbol=0,
            )

        # Allowed
        return DailyTradeLimitResult(
            allowed=True,
            reason="",
            remaining_total=remaining_total,
            remaining_symbol=remaining_symbol,
        )

    def record_trade_open(self, symbol: str) -> None:
        """
        Record a successful trade open. Increments counters and persists immediately.

        ONLY call this after a confirmed successful broker fill.
        Never call for rejected/failed orders.
        """
        # Day boundary check — ensure we're counting on the right day
        self._check_day_boundary()

        self._total_trades_today += 1
        self._per_symbol[symbol] = self._per_symbol.get(symbol, 0) + 1
        self._persist()

        logger.info(
            "[DAILY_TRADE_LIMIT] recorded symbol=%s total=%d/%d symbol_count=%d/%d",
            symbol, self._total_trades_today, _get_max_total(),
            self._per_symbol[symbol], _get_max_per_symbol(),
        )

    def reset(self) -> None:
        """
        Reset all counters to zero. Called by D4 Daily Reset integration.
        Persists immediately after reset.
        """
        prev_total = self._total_trades_today
        prev_symbols = dict(self._per_symbol)

        self._current_day_key = _current_day_key()
        self._total_trades_today = 0
        self._per_symbol = {}
        self._persist()

        logger.info(
            "[DAILY_TRADE_LIMIT_RESET] day=%s previous_total=%d previous_symbols=%s",
            self._current_day_key, prev_total, prev_symbols,
        )

    # ─── INTERNAL ─────────────────────────────────────────────────────

    def _check_day_boundary(self) -> None:
        """Check if a new trading day has started. Reset if so."""
        today = _current_day_key()
        if self._current_day_key != today:
            # New day detected — reset counters
            logger.info(
                "[DAILY_TRADE_LIMIT_DAY_CHANGE] old=%s new=%s — resetting counters",
                self._current_day_key, today,
            )
            self._current_day_key = today
            self._total_trades_today = 0
            self._per_symbol = {}
            self._persist()

    def _load(self) -> None:
        """Load state from disk. Never raises."""
        data = _load_state()
        if data is None:
            # No prior state — initialise for today
            self._current_day_key = _current_day_key()
            self._total_trades_today = 0
            self._per_symbol = {}
            return

        stored_day = data.get("current_day_key", "")
        today = _current_day_key()

        if stored_day != today:
            # Stale state from previous day — start fresh
            self._current_day_key = today
            self._total_trades_today = 0
            self._per_symbol = {}
            return

        # Valid state for today — restore
        self._current_day_key = stored_day
        self._total_trades_today = int(data.get("total_trades_today", 0))
        per_symbol_raw = data.get("per_symbol", {})
        if isinstance(per_symbol_raw, dict):
            self._per_symbol = {
                str(k): int(v) for k, v in per_symbol_raw.items()
                if isinstance(v, (int, float))
            }
        else:
            self._per_symbol = {}

        if self._total_trades_today > 0:
            logger.info(
                "[DAILY_TRADE_LIMIT] restored state day=%s total=%d symbols=%s",
                self._current_day_key, self._total_trades_today, self._per_symbol,
            )

    def _persist(self) -> None:
        """Persist current state to disk immediately. Never raises."""
        state = {
            "current_day_key": self._current_day_key,
            "total_trades_today": self._total_trades_today,
            "per_symbol": self._per_symbol,
            "last_updated": _time.time(),
        }
        _persist_state(state)

    def _get_state_snapshot(self) -> dict[str, Any]:
        """Return current state as dict (for observability/testing)."""
        return {
            "current_day_key": self._current_day_key,
            "total_trades_today": self._total_trades_today,
            "per_symbol": dict(self._per_symbol),
        }
