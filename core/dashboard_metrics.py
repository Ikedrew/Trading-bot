"""
F4: Performance Metrics Dashboard Emission.

Extends dashboard system with real P&L-aware trading performance metrics.
All metrics are derived from the persistent trade journal (restart-safe).

This is observability only — no trading logic changes.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from typing import Any

from core.trade_journal import get_daily_summary, get_trades_by_date

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _include_pnl() -> bool:
    try:
        from core import config
        return bool(getattr(config, "DASHBOARD_INCLUDE_PNL_METRICS", True))
    except ImportError:
        return True


def _emit_daily_summary() -> bool:
    try:
        from core import config
        return bool(getattr(config, "DASHBOARD_EMIT_DAILY_SUMMARY", True))
    except ImportError:
        return True


# ─── DATA MODEL ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PerformanceDashboard:
    """Real-time trading performance metrics (journal-derived)."""
    trades_today: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    avg_pnl: float
    avg_r_multiple: float | None


# ─── METRICS COMPUTATION ──────────────────────────────────────────────────────

def compute_daily_performance() -> PerformanceDashboard:
    """
    Compute today's performance metrics from the trade journal.

    Data source: trade_journal.jsonl (persistent, restart-safe).
    Filter: today's date only.
    Never uses in-memory counters.
    """
    try:
        summary = get_daily_summary()
        trades_today = summary.get("trades", 0)
        wins = summary.get("wins", 0)
        losses = summary.get("losses", 0)
        net_pnl = summary.get("net_pnl", 0.0)
        avg_pnl = summary.get("avg_pnl", 0.0)
        win_rate = summary.get("win_rate", 0.0)

        # R-multiple calculation (if risk data available)
        avg_r = _compute_avg_r_multiple()

        return PerformanceDashboard(
            trades_today=trades_today,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 4),
            net_pnl=round(net_pnl, 2),
            avg_pnl=round(avg_pnl, 2),
            avg_r_multiple=avg_r,
        )

    except Exception as exc:
        logger.debug("[DASHBOARD_METRICS] compute_error=%s", exc)
        return PerformanceDashboard(
            trades_today=0, wins=0, losses=0,
            win_rate=0.0, net_pnl=0.0, avg_pnl=0.0,
            avg_r_multiple=None,
        )


def _compute_avg_r_multiple() -> float | None:
    """
    Compute average R-multiple from today's trades.

    R = net_pnl / risk_amount (where risk = SL distance × volume).
    Returns None if insufficient data.
    """
    try:
        trades = get_trades_by_date()
        if not trades:
            return None

        r_multiples: list[float] = []
        for t in trades:
            # Risk = |entry - initial_sl| * volume (in price terms)
            risk_distance = abs(t.entry_price - t.initial_sl)
            if risk_distance > 0 and t.initial_volume > 0:
                # R = pnl_in_price_terms / risk_distance
                # Use net_pnl / (risk_distance * volume) as proxy
                # But net_pnl is in account currency, not normalized
                # Simpler: if we have the raw pnl and risk, compute R
                risk_amount = risk_distance * t.initial_volume
                if risk_amount > 0:
                    r = t.net_pnl / risk_amount if risk_amount != 0 else 0.0
                    r_multiples.append(r)

        if not r_multiples:
            return None

        return round(sum(r_multiples) / len(r_multiples), 4)

    except Exception:
        return None


# ─── DASHBOARD PAYLOAD BUILDER ────────────────────────────────────────────────

def build_performance_payload() -> dict[str, Any]:
    """
    Build performance metrics payload for dashboard emission.

    Returns dict suitable for inclusion in dashboard output.
    """
    if not _include_pnl():
        return {}

    perf = compute_daily_performance()

    return {
        "performance": {
            "trades_today": perf.trades_today,
            "wins": perf.wins,
            "losses": perf.losses,
            "win_rate": perf.win_rate,
            "net_pnl": perf.net_pnl,
            "avg_pnl": perf.avg_pnl,
            "avg_r_multiple": perf.avg_r_multiple,
        }
    }


# ─── DAILY SUMMARY EMISSION ──────────────────────────────────────────────────

_daily_summary_emitted: bool = False


def emit_daily_performance_summary() -> dict | None:
    """
    Emit end-of-day performance summary (once per day).

    Returns the performance dict if emitted, None if already emitted or disabled.
    """
    global _daily_summary_emitted

    if not _emit_daily_summary():
        return None

    if _daily_summary_emitted:
        return None

    perf = compute_daily_performance()

    if perf.trades_today == 0:
        return None  # Nothing to report

    _daily_summary_emitted = True

    logger.info(
        "[DAILY_PERFORMANCE_SUMMARY] trades=%d win_rate=%.0f%% net_pnl=%+.2f avg_r=%.2f",
        perf.trades_today,
        perf.win_rate * 100,
        perf.net_pnl,
        perf.avg_r_multiple or 0.0,
    )

    return {
        "trades": perf.trades_today,
        "win_rate": perf.win_rate,
        "net_pnl": perf.net_pnl,
        "avg_r_multiple": perf.avg_r_multiple,
    }


def reset_daily_summary_flag() -> None:
    """Reset daily summary emission flag (called by D4 daily reset)."""
    global _daily_summary_emitted
    _daily_summary_emitted = False


# ─── PERIODIC EMISSION (INTEGRATION POINT) ────────────────────────────────────

def emit_dashboard_performance(cycle_id: int = 0) -> None:
    """
    Emit performance metrics as part of regular dashboard cycle.

    Call every N cycles (e.g. 25) alongside existing dashboard emission.
    """
    if not _include_pnl():
        return

    try:
        perf = compute_daily_performance()

        if perf.trades_today > 0:
            logger.info(
                "[DASHBOARD] cycle=%d win_rate=%.2f net_pnl=%+.2f trades=%d",
                cycle_id, perf.win_rate, perf.net_pnl, perf.trades_today,
            )
        else:
            logger.debug("[DASHBOARD] cycle=%d no_trades_yet", cycle_id)

    except Exception as exc:
        logger.debug("[DASHBOARD_WARNING] insufficient_trade_history_for_metrics error=%s", exc)


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────

def validate_dashboard_metrics_config() -> list[str]:
    """Validate dashboard metrics config."""
    return []  # No required config — all optional with defaults
