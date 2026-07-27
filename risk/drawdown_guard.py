"""
Max drawdown protection guard — blocks new trades when account drawdown exceeds threshold.

Model: High-watermark equity drawdown.
    drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100

Fail-closed: if account state is unknown, trading is blocked.
Peak equity is PERSISTED to disk — survives restarts.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from core import config
from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)

# ─── REJECTION REASONS ────────────────────────────────────────────────────────

REJECT_MAX_DRAWDOWN_EXCEEDED = "MAX_DRAWDOWN_EXCEEDED"
REJECT_ACCOUNT_STATE_UNKNOWN = "ACCOUNT_STATE_UNKNOWN"

# ─── PERSISTENCE CONFIG ───────────────────────────────────────────────────────

_PEAK_FILE_DEFAULT = "logs/drawdown_peak.json"


def _get_peak_path() -> Path:
    return Path(getattr(config, "DRAWDOWN_PEAK_FILE", _PEAK_FILE_DEFAULT))


@dataclass(frozen=True)
class DrawdownResult:
    """Result of drawdown guard evaluation."""
    allowed: bool
    drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── PEAK PERSISTENCE ─────────────────────────────────────────────────────────

def _load_persisted_peak() -> float | None:
    """
    Load peak equity from disk. Returns None if file doesn't exist or is invalid.
    Never raises.
    """
    try:
        path = _get_peak_path()
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        peak = data.get("peak_equity")
        if peak is None or not isinstance(peak, (int, float)) or peak <= 0:
            return None
        logger.info("[DRAWDOWN_PEAK_LOADED] peak=%.2f", peak)
        return float(peak)
    except Exception as exc:
        logger.warning("[DRAWDOWN_PEAK_LOAD_ERROR] error=%s — will reinitialise", exc)
        return None


def _persist_peak(peak_equity: float) -> bool:
    """
    Persist peak equity to disk. Atomic write (temp → fsync → replace).
    Returns True on success, False on failure. Never raises.
    """
    try:
        path = _get_peak_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "peak_equity": round(peak_equity, 4),
            "last_updated": _time.time(),
            "currency": "account",
        }
        json_bytes = json.dumps(data, indent=2).encode("utf-8")

        # Atomic write: temp file → fsync → replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="peak_"
        )
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, str(path))
        return True

    except Exception as exc:
        logger.warning("[DRAWDOWN_PEAK_PERSIST_ERROR] using memory fallback error=%s", exc)
        return False


# ─── DRAWDOWN GUARD ───────────────────────────────────────────────────────────

class DrawdownGuard:
    """
    Tracks equity high-watermark and blocks trades when drawdown exceeds threshold.

    Peak equity is persisted to disk and restored on startup.
    Peak is strictly monotonic — it NEVER decreases.

    Usage:
        guard = DrawdownGuard()
        result = guard.check()
        if not result.allowed:
            # reject trade
    """

    def __init__(self) -> None:
        # Load persisted peak (or start at 0)
        stored = _load_persisted_peak()
        self._peak_equity: float = stored if stored is not None else 0.0
        self._peak_persisted: bool = stored is not None

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    def check(self) -> DrawdownResult:
        """
        Evaluate current drawdown against configured threshold.

        Returns DrawdownResult with allowed=True if trading is permitted.
        Fail-closed: returns allowed=False if account state cannot be determined.

        Side effect: updates and persists peak equity if new high reached.
        """
        if not getattr(config, "ENABLE_DRAWDOWN_GUARD", False):
            return DrawdownResult(allowed=True)

        threshold = float(getattr(config, "MAX_DRAWDOWN_PERCENT", 10.0))

        # Fetch account state
        try:
            info = mt5_call(mt5.account_info)
        except Exception as exc:
            logger.error(
                "[DRAWDOWN_GUARD] reason=%s metadata={\"error\": \"%s\"}",
                REJECT_ACCOUNT_STATE_UNKNOWN, exc,
            )
            return DrawdownResult(
                allowed=False,
                reason=REJECT_ACCOUNT_STATE_UNKNOWN,
                metadata={"error": str(exc)},
            )

        if info is None:
            last_err = mt5.last_error()
            logger.warning(
                "[DRAWDOWN_GUARD] reason=%s metadata={\"mt5_last_error\": \"%s\"}",
                REJECT_ACCOUNT_STATE_UNKNOWN, last_err,
            )
            return DrawdownResult(
                allowed=False,
                reason=REJECT_ACCOUNT_STATE_UNKNOWN,
                metadata={"mt5_last_error": str(last_err)},
            )

        current_equity = float(info.equity)

        # ─── PEAK UPDATE (strict monotonic — never decreases) ─────────
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
            _persist_peak(self._peak_equity)
            logger.info("[DRAWDOWN_PEAK_UPDATE] new_peak=%.2f", self._peak_equity)
        # ─── END PEAK UPDATE ──────────────────────────────────────────

        # Startup correction: if stored peak < current equity (stale/corrupt)
        # This is handled by the condition above (current > peak → updates)

        # First call — no peak established yet
        if self._peak_equity <= 0:
            return DrawdownResult(allowed=True, current_equity=current_equity, peak_equity=self._peak_equity)

        # Calculate drawdown
        drawdown_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100.0

        if drawdown_pct >= threshold:
            logger.warning(
                "[DRAWDOWN_GUARD] BLOCKED reason=%s drawdown=%.2f%% threshold=%.2f%% "
                "peak=%.2f current=%.2f",
                REJECT_MAX_DRAWDOWN_EXCEEDED, drawdown_pct, threshold,
                self._peak_equity, current_equity,
            )
            return DrawdownResult(
                allowed=False,
                drawdown_pct=drawdown_pct,
                peak_equity=self._peak_equity,
                current_equity=current_equity,
                reason=REJECT_MAX_DRAWDOWN_EXCEEDED,
                metadata={
                    "drawdown_pct": round(drawdown_pct, 2),
                    "threshold": threshold,
                    "peak_equity": round(self._peak_equity, 2),
                    "current_equity": round(current_equity, 2),
                },
            )

        return DrawdownResult(
            allowed=True,
            drawdown_pct=drawdown_pct,
            peak_equity=self._peak_equity,
            current_equity=current_equity,
        )

    def reset_peak(self, equity: float | None = None) -> None:
        """Reset high watermark (e.g. for replay or session reset)."""
        self._peak_equity = equity if equity is not None else 0.0
        if self._peak_equity > 0:
            _persist_peak(self._peak_equity)
