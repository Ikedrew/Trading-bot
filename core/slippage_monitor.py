"""
C3: Slippage Monitoring — Execution quality measurement.

Measures the difference between expected price (at signal/order submission)
and fill price (actual broker execution) to detect broker inefficiency,
execution drift, or edge degradation.

This is monitoring-only — it does NOT block trading.

Provides:
- Per-trade slippage recording
- Per-symbol + global rolling statistics
- ATR-normalised slippage metric
- Alert on threshold breach (log only)
- JSONL persistence for audit trail
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "SLIPPAGE_MONITORING_ENABLED", True))
    except ImportError:
        return True


def _get_alert_threshold_pips() -> float:
    try:
        from core import config
        return float(getattr(config, "SLIPPAGE_ALERT_THRESHOLD_PIPS", 0.5))
    except ImportError:
        return 0.5


def _get_journal_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "SLIPPAGE_JOURNAL_FILE", "runtime/slippage_journal.jsonl"))
    except ImportError:
        return Path("runtime/slippage_journal.jsonl")


def _get_max_history() -> int:
    try:
        from core import config
        return int(getattr(config, "SLIPPAGE_MAX_HISTORY", 500))
    except ImportError:
        return 500


# ─── DATA MODEL ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlippageRecord:
    """Single trade slippage measurement."""
    symbol: str
    timestamp: float
    expected_price: float
    fill_price: float
    slippage: float  # fill - expected (positive = worse for buyer)
    slippage_pips: float
    atr_at_time: float | None = None
    slippage_atr_ratio: float | None = None  # |slippage| / ATR


@dataclass(frozen=True)
class SlippageStats:
    """Rolling slippage statistics."""
    trade_count: int
    mean_slippage: float
    max_slippage: float
    mean_slippage_pips: float
    max_slippage_pips: float
    mean_atr_ratio: float | None = None


# ─── POINT SIZE LOOKUP ────────────────────────────────────────────────────────

# Standard pip sizes for common FX pairs
_POINT_SIZES: dict[str, float] = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCHF": 0.0001, "USDCAD": 0.0001,
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,
}


def _get_point_size(symbol: str) -> float:
    """Get pip size for symbol. Falls back to MT5 or default 0.0001."""
    # Strip suffix (e.g. EURUSD_SB → EURUSD)
    base = symbol.replace("_SB", "").replace("_sb", "")
    if base in _POINT_SIZES:
        return _POINT_SIZES[base]

    # Try MT5 symbol_info
    try:
        import MetaTrader5 as mt5
        from core.mt5_timeout import mt5_call
        info = mt5_call(mt5.symbol_info, symbol)
        if info is not None and hasattr(info, "point"):
            return float(info.point)
    except Exception:
        pass

    return 0.0001  # Default


# ─── SLIPPAGE MONITOR ─────────────────────────────────────────────────────────

class SlippageMonitor:
    """
    Records and analyses trade execution slippage.

    Maintains rolling history per symbol and global.
    Persists to JSONL file for audit trail.
    Emits alerts when thresholds are breached.
    """

    def __init__(self) -> None:
        self._history: deque[SlippageRecord] = deque(maxlen=_get_max_history())
        self._per_symbol: dict[str, deque[SlippageRecord]] = {}
        self._alert_emitted: dict[str, bool] = {}

    def record(
        self,
        *,
        symbol: str,
        expected_price: float,
        fill_price: float,
        atr: float | None = None,
    ) -> SlippageRecord:
        """
        Record a single trade's slippage.

        Call this immediately after receiving broker fill confirmation.

        Args:
            symbol: Trading symbol
            expected_price: Price at order submission (bid/ask at signal time)
            fill_price: Actual broker fill price
            atr: ATR at time of trade (for normalised metric)

        Returns:
            SlippageRecord with calculated metrics.
        """
        if not _is_enabled():
            return SlippageRecord(
                symbol=symbol, timestamp=_time.time(),
                expected_price=expected_price, fill_price=fill_price,
                slippage=0.0, slippage_pips=0.0,
            )

        # Calculate slippage
        slippage = fill_price - expected_price
        point_size = _get_point_size(symbol)
        slippage_pips = slippage / point_size if point_size > 0 else 0.0

        # ATR-normalised ratio
        atr_ratio: float | None = None
        if atr is not None and atr > 0:
            atr_ratio = abs(slippage) / atr

        record = SlippageRecord(
            symbol=symbol,
            timestamp=_time.time(),
            expected_price=expected_price,
            fill_price=fill_price,
            slippage=round(slippage, 8),
            slippage_pips=round(slippage_pips, 2),
            atr_at_time=atr,
            slippage_atr_ratio=round(atr_ratio, 4) if atr_ratio is not None else None,
        )

        # Store in history
        self._history.append(record)
        if symbol not in self._per_symbol:
            self._per_symbol[symbol] = deque(maxlen=_get_max_history())
        self._per_symbol[symbol].append(record)

        # Log
        logger.info(
            "[SLIPPAGE] Symbol: %s Expected: %.5f Fill: %.5f Slippage: %+.1f pips",
            symbol, expected_price, fill_price, slippage_pips,
        )

        # Persist
        self._persist_record(record)

        # Check alert threshold
        self._check_alert(symbol)

        return record

    def get_stats(self, symbol: str | None = None) -> SlippageStats:
        """
        Get rolling slippage statistics.

        Args:
            symbol: Specific symbol, or None for global stats.

        Returns:
            SlippageStats with mean/max slippage metrics.
        """
        if symbol is not None:
            records = list(self._per_symbol.get(symbol, []))
        else:
            records = list(self._history)

        if not records:
            return SlippageStats(
                trade_count=0, mean_slippage=0.0, max_slippage=0.0,
                mean_slippage_pips=0.0, max_slippage_pips=0.0,
            )

        slippages = [r.slippage for r in records]
        pips = [r.slippage_pips for r in records]
        atr_ratios = [r.slippage_atr_ratio for r in records if r.slippage_atr_ratio is not None]

        return SlippageStats(
            trade_count=len(records),
            mean_slippage=round(sum(slippages) / len(slippages), 6),
            max_slippage=round(max(abs(s) for s in slippages), 6),
            mean_slippage_pips=round(sum(pips) / len(pips), 2),
            max_slippage_pips=round(max(abs(p) for p in pips), 2),
            mean_atr_ratio=round(sum(atr_ratios) / len(atr_ratios), 4) if atr_ratios else None,
        )

    def _check_alert(self, symbol: str) -> None:
        """Emit alert if mean slippage exceeds threshold."""
        threshold = _get_alert_threshold_pips()
        stats = self.get_stats(symbol)

        if stats.trade_count < 3:
            return  # Not enough data

        if abs(stats.mean_slippage_pips) > threshold:
            if not self._alert_emitted.get(symbol, False):
                self._alert_emitted[symbol] = True
                logger.warning(
                    "[SLIPPAGE_ALERT] %s mean slippage exceeded threshold "
                    "Mean: %.1f pips Max: %.1f pips Threshold: %.1f pips Trades: %d",
                    symbol, stats.mean_slippage_pips, stats.max_slippage_pips,
                    threshold, stats.trade_count,
                )

    def _persist_record(self, record: SlippageRecord) -> None:
        """Append record to JSONL file. Never raises."""
        try:
            path = _get_journal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "symbol": record.symbol,
                "timestamp": record.timestamp,
                "timestamp_iso": datetime.fromtimestamp(record.timestamp, tz=timezone.utc).isoformat(),
                "expected_price": record.expected_price,
                "fill_price": record.fill_price,
                "slippage": record.slippage,
                "slippage_pips": record.slippage_pips,
                "atr_at_time": record.atr_at_time,
                "slippage_atr_ratio": record.slippage_atr_ratio,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.debug("[SLIPPAGE] persist_error=%s", exc)


# ─── MODULE SINGLETON ─────────────────────────────────────────────────────────

_monitor: SlippageMonitor | None = None


def get_monitor() -> SlippageMonitor:
    """Get or create the singleton slippage monitor."""
    global _monitor
    if _monitor is None:
        _monitor = SlippageMonitor()
    return _monitor


# ─── CONVENIENCE API ──────────────────────────────────────────────────────────

def record_slippage(
    *,
    symbol: str,
    expected_price: float,
    fill_price: float,
    atr: float | None = None,
) -> SlippageRecord:
    """Record trade slippage. Uses singleton monitor."""
    return get_monitor().record(
        symbol=symbol,
        expected_price=expected_price,
        fill_price=fill_price,
        atr=atr,
    )


def get_slippage_stats(symbol: str | None = None) -> SlippageStats:
    """Get slippage statistics. Uses singleton monitor."""
    return get_monitor().get_stats(symbol)


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_slippage_config() -> list[str]:
    """Validate slippage monitoring config at startup."""
    errors: list[str] = []
    if not _is_enabled():
        return errors

    threshold = _get_alert_threshold_pips()
    if threshold <= 0:
        errors.append(f"SLIPPAGE_ALERT_THRESHOLD_PIPS must be > 0 (got {threshold})")

    return errors
