"""
I4: Equity Curve Tracking / Performance Monitoring.

Persistent, append-only performance tracking system that records:
- Daily equity snapshots
- Equity curve history
- Rolling performance metrics (Sharpe, drawdown, win rate)
- Edge decay detection alerts

This is observability ONLY — no trading impact.
Trading logic must NEVER depend on this module.
"""

from __future__ import annotations

import json
import logging
import math
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "EQUITY_CURVE_ENABLED", True))
    except ImportError:
        return True


def _get_curve_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "EQUITY_CURVE_FILE", "runtime/equity_curve.jsonl"))
    except ImportError:
        return Path("runtime/equity_curve.jsonl")


def _get_sharpe_threshold() -> float:
    try:
        from core import config
        return float(getattr(config, "SHARPE_DECAY_THRESHOLD", 0.5))
    except ImportError:
        return 0.5


# ─── DATA MODEL ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EquitySnapshot:
    """Single daily equity record."""
    timestamp: float
    timestamp_iso: str
    equity: float
    balance: float
    unrealized_pnl: float
    realized_pnl: float


@dataclass(frozen=True)
class PerformanceMetrics:
    """Rolling performance metrics."""
    sharpe_30d: float
    max_drawdown_30d_pct: float
    win_rate_30d_pct: float | None
    total_snapshots: int
    period_days: int


# ─── SNAPSHOT RECORDING ───────────────────────────────────────────────────────

def record_daily_equity_snapshot(
    *,
    equity: float | None = None,
    balance: float | None = None,
) -> EquitySnapshot | None:
    """
    Record a daily equity snapshot to the append-only JSONL curve file.

    Args:
        equity: Account equity (if None, fetches from MT5).
        balance: Account balance (if None, fetches from MT5).

    Returns:
        EquitySnapshot if recorded, None on failure or disabled.
    """
    if not _is_enabled():
        return None

    # Fetch from MT5 if not provided
    if equity is None or balance is None:
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
            info = mt5_call(mt5.account_info)
            if info is None:
                logger.warning("[EQUITY_CURVE] Cannot record — account_info unavailable")
                return None
            equity = float(info.equity)
            balance = float(info.balance)
        except Exception as exc:
            logger.warning("[EQUITY_CURVE] Cannot record — MT5 error: %s", exc)
            return None

    unrealized = equity - balance
    realized = balance - _get_starting_balance(balance)

    now = _time.time()
    snapshot = EquitySnapshot(
        timestamp=now,
        timestamp_iso=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        equity=round(equity, 2),
        balance=round(balance, 2),
        unrealized_pnl=round(unrealized, 2),
        realized_pnl=round(realized, 2),
    )

    # Append to JSONL (never overwrite)
    success = _append_snapshot(snapshot)
    if success:
        logger.info(
            "[EQUITY_SNAPSHOT] equity=%.2f balance=%.2f unrealized=%.2f realized=%.2f",
            snapshot.equity, snapshot.balance, snapshot.unrealized_pnl, snapshot.realized_pnl,
        )

    return snapshot if success else None


def _get_starting_balance(current_balance: float) -> float:
    """Get starting balance for realized P&L calculation. Uses challenge baseline if available."""
    try:
        from core import config
        start = float(getattr(config, "CHALLENGE_START_EQUITY", 0))
        if start > 0:
            return start
    except ImportError:
        pass
    # Fallback: use current balance (realized = 0)
    return current_balance


def _append_snapshot(snapshot: EquitySnapshot) -> bool:
    """Append snapshot to JSONL file. Never raises."""
    try:
        path = _get_curve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": snapshot.timestamp,
            "timestamp_iso": snapshot.timestamp_iso,
            "equity": snapshot.equity,
            "balance": snapshot.balance,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "realized_pnl": snapshot.realized_pnl,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception as exc:
        logger.warning("[EQUITY_CURVE] append_error=%s", exc)
        return False


# ─── CURVE READER ─────────────────────────────────────────────────────────────

def load_equity_curve(days: int = 30, path: Path | None = None) -> list[dict]:
    """
    Load last N days of equity curve data.

    Args:
        days: Number of days to look back.
        path: Override file path (for testing).

    Returns:
        List of snapshot dicts (oldest first).
    """
    p = path or _get_curve_path()
    if not p.exists():
        return []

    cutoff = _time.time() - (days * 86400)
    results: list[dict] = []

    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", 0) >= cutoff:
                        results.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception as exc:
        logger.warning("[EQUITY_CURVE] load_error=%s", exc)

    return results


# ─── ROLLING PERFORMANCE METRICS ──────────────────────────────────────────────

def compute_performance_metrics(days: int = 30, path: Path | None = None) -> PerformanceMetrics:
    """
    Compute rolling performance metrics from equity curve.

    Returns:
        PerformanceMetrics with Sharpe, drawdown, and win rate.
    """
    curve = load_equity_curve(days=days, path=path)

    if len(curve) < 2:
        return PerformanceMetrics(
            sharpe_30d=0.0,
            max_drawdown_30d_pct=0.0,
            win_rate_30d_pct=None,
            total_snapshots=len(curve),
            period_days=days,
        )

    # Extract equity values (sorted by timestamp)
    equities = [entry["equity"] for entry in sorted(curve, key=lambda x: x["timestamp"])]

    # Daily returns
    daily_returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            ret = (equities[i] - equities[i - 1]) / equities[i - 1]
            daily_returns.append(ret)

    # Sharpe ratio (simplified: mean / std, annualized not needed for decay detection)
    sharpe = 0.0
    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        if len(daily_returns) >= 2:
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_ret = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = (mean_ret / std_ret) if std_ret > 0 else 0.0
        else:
            sharpe = 0.0

    # Max drawdown
    max_dd_pct = _compute_max_drawdown(equities)

    # Win rate from daily returns (positive days / total days)
    if daily_returns:
        positive_days = sum(1 for r in daily_returns if r > 0)
        win_rate = (positive_days / len(daily_returns)) * 100.0
    else:
        win_rate = None

    return PerformanceMetrics(
        sharpe_30d=round(sharpe, 4),
        max_drawdown_30d_pct=round(max_dd_pct, 4),
        win_rate_30d_pct=round(win_rate, 1) if win_rate is not None else None,
        total_snapshots=len(curve),
        period_days=days,
    )


def _compute_max_drawdown(equities: list[float]) -> float:
    """Compute max drawdown percentage from equity series."""
    if not equities:
        return 0.0

    peak = equities[0]
    max_dd = 0.0

    for eq in equities:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = ((peak - eq) / peak) * 100.0
            if dd > max_dd:
                max_dd = dd

    return max_dd


# ─── EDGE DECAY DETECTION ────────────────────────────────────────────────────

def check_edge_decay(days: int = 30, path: Path | None = None) -> bool:
    """
    Check if strategy edge is decaying based on Sharpe ratio.

    Returns True if decay detected (alert emitted).
    """
    if not _is_enabled():
        return False

    metrics = compute_performance_metrics(days=days, path=path)

    if metrics.total_snapshots < 5:
        return False  # Not enough data

    threshold = _get_sharpe_threshold()

    if metrics.sharpe_30d < threshold:
        logger.warning(
            "[EDGE_DECAY_WARNING] Sharpe_30d=%.2f WinRate_30d=%.1f%% MaxDD_30d=%.1f%%",
            metrics.sharpe_30d,
            metrics.win_rate_30d_pct or 0.0,
            metrics.max_drawdown_30d_pct,
        )
        return True

    # Healthy — log metrics
    logger.info(
        "[EQUITY_METRICS] sharpe_30d=%.2f winrate_30d=%.1f%% max_dd_30d=%.1f%%",
        metrics.sharpe_30d,
        metrics.win_rate_30d_pct or 0.0,
        metrics.max_drawdown_30d_pct,
    )
    return False


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_equity_curve_config() -> list[str]:
    """Validate equity curve config at startup."""
    errors: list[str] = []
    if not _is_enabled():
        return errors
    threshold = _get_sharpe_threshold()
    if threshold < 0:
        errors.append(f"SHARPE_DECAY_THRESHOLD must be >= 0 (got {threshold})")
    return errors
