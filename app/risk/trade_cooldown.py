"""
B1: Trade Cooldown — Per-symbol, outcome-aware, persistent.

Prevents rapid re-entry after trade exits. Tracks per-symbol state with
different cooldown durations for wins vs losses.

Persists to disk — survives restarts. No cooldown reset on reboot.
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

_STATE_FILE_DEFAULT = "logs/trade_cooldown_state.json"


def _get_state_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "TRADE_COOLDOWN_STATE_FILE", _STATE_FILE_DEFAULT))
    except ImportError:
        return Path(_STATE_FILE_DEFAULT)


def _get_base_cooldown() -> float:
    try:
        from core import config
        return float(getattr(config, "COOLDOWN_SECONDS", 300.0))
    except ImportError:
        return 300.0


def _get_loss_cooldown() -> float:
    try:
        from core import config
        return float(getattr(config, "COOLDOWN_AFTER_LOSS_SECONDS", 600.0))
    except ImportError:
        return 600.0


# ─── STATE ────────────────────────────────────────────────────────────────────

@dataclass
class SymbolCooldownEntry:
    """Per-symbol cooldown state."""
    last_exit_time: float
    last_direction: str  # "BUY" or "SELL"
    last_result: str     # "WIN", "LOSS", or "UNKNOWN"


class TradeCooldownManager:
    """
    Per-symbol, outcome-aware cooldown system.

    Tracks when each symbol last exited a trade and whether it was a win or loss.
    Applies different cooldown durations based on outcome.
    Persists state to disk — survives restarts.
    """

    def __init__(self) -> None:
        self._state: dict[str, SymbolCooldownEntry] = {}
        self._load()

    def can_open_trade(self, symbol: str, current_time: float) -> bool:
        """
        Check if a new trade is allowed for this symbol.

        Returns True if cooldown has elapsed, False if still blocked.
        """
        entry = self._state.get(symbol)
        if entry is None:
            return True  # No prior trade for this symbol

        # Determine applicable cooldown duration
        if entry.last_result == "LOSS":
            cooldown = _get_loss_cooldown()
        else:
            cooldown = _get_base_cooldown()

        elapsed = current_time - entry.last_exit_time
        return elapsed >= cooldown

    def get_remaining_cooldown(self, symbol: str, current_time: float) -> float:
        """Get seconds remaining until cooldown expires. 0.0 if not blocked."""
        entry = self._state.get(symbol)
        if entry is None:
            return 0.0
        cooldown = _get_loss_cooldown() if entry.last_result == "LOSS" else _get_base_cooldown()
        remaining = cooldown - (current_time - entry.last_exit_time)
        return max(0.0, remaining)

    def record_trade_exit(
        self,
        symbol: str,
        direction: str,
        result: str,
        exit_time: float | None = None,
    ) -> None:
        """
        Record a trade exit. Updates cooldown state and persists.

        Args:
            symbol: Trading symbol
            direction: "BUY" or "SELL"
            result: "WIN", "LOSS", or "UNKNOWN"
            exit_time: Unix timestamp (default: now)
        """
        ts = exit_time if exit_time is not None else _time.time()
        self._state[symbol] = SymbolCooldownEntry(
            last_exit_time=ts,
            last_direction=direction,
            last_result=result,
        )
        self._persist()

        logger.info(
            "[TRADE_COOLDOWN] recorded symbol=%s direction=%s result=%s "
            "cooldown=%.0fs",
            symbol, direction, result,
            _get_loss_cooldown() if result == "LOSS" else _get_base_cooldown(),
        )

    # ─── PERSISTENCE ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load cooldown state from disk. Never raises."""
        try:
            path = _get_state_path()
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            for symbol, entry_data in data.items():
                if symbol.startswith("_"):
                    continue
                if not isinstance(entry_data, dict):
                    continue
                self._state[symbol] = SymbolCooldownEntry(
                    last_exit_time=float(entry_data.get("last_exit_time", 0)),
                    last_direction=str(entry_data.get("last_direction", "UNKNOWN")),
                    last_result=str(entry_data.get("last_result", "UNKNOWN")),
                )
            if self._state:
                logger.info("[TRADE_COOLDOWN] loaded state for %d symbols", len(self._state))
        except Exception as exc:
            logger.warning("[TRADE_COOLDOWN] load_error=%s", exc)

    def _persist(self) -> None:
        """Persist cooldown state to disk. Atomic write. Never raises."""
        try:
            path = _get_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            for symbol, entry in self._state.items():
                data[symbol] = {
                    "last_exit_time": entry.last_exit_time,
                    "last_direction": entry.last_direction,
                    "last_result": entry.last_result,
                }
            json_bytes = json.dumps(data, indent=2).encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="cd_")
            try:
                os.write(fd, json_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.warning("[TRADE_COOLDOWN] persist_error=%s", exc)
