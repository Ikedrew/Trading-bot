"""
Trade Query — Answers: "How are trades performing?"

Reads from: logs/trade_journal/*.jsonl
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


_JOURNAL_DIR = Path("logs/trade_journal")


def get_recent_trades(days: int = 7) -> list[dict[str, Any]]:
    """Read trade_journal records from the last N days."""
    if not _JOURNAL_DIR.exists():
        return []

    cutoff = time.time() - (days * 86400)
    trades: list[dict[str, Any]] = []

    for f in sorted(_JOURNAL_DIR.glob("*.jsonl"), reverse=True):
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("exit_time", 0) >= cutoff:
                    trades.append(rec)
        except Exception:
            continue

    return trades


def get_trade_summary(days: int = 7) -> dict[str, Any]:
    """
    Summarise recent trading performance.

    Returns:
        - total_trades
        - wins / losses
        - win_rate
        - total_pnl
        - avg_r_multiple
        - avg_duration_minutes
        - by_horizon breakdown
        - by_pattern breakdown
    """
    trades = get_recent_trades(days=days)

    if not trades:
        return {
            "total_trades": 0,
            "period_days": days,
            "message": "No trades in this period.",
        }

    wins = 0
    losses = 0
    total_pnl = 0.0
    r_multiples: list[float] = []
    durations: list[float] = []
    by_horizon: dict[str, list[float]] = {}
    by_pattern: dict[str, list[float]] = {}

    for t in trades:
        pnl = t.get("net_pnl", 0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1

        # R-multiple
        risk = abs(t.get("entry_price", 0) - t.get("initial_sl", 0))
        if risk > 0:
            direction = t.get("direction", "BUY")
            if direction == "BUY":
                r = (t.get("exit_price", 0) - t.get("entry_price", 0)) / risk
            else:
                r = (t.get("entry_price", 0) - t.get("exit_price", 0)) / risk
            r_multiples.append(round(r, 2))

        durations.append(t.get("duration_seconds", 0) / 60.0)

        # Group by horizon
        h = t.get("trade_horizon", "SCALP")
        by_horizon.setdefault(h, []).append(pnl)

        # Group by pattern
        p = t.get("pattern_name", "UNKNOWN")
        by_pattern.setdefault(p, []).append(pnl)

    total = wins + losses
    return {
        "period_days": days,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total, 3) if total > 0 else 0.0,
        "total_pnl": round(total_pnl, 2),
        "avg_r_multiple": round(sum(r_multiples) / len(r_multiples), 3) if r_multiples else 0.0,
        "avg_duration_minutes": round(sum(durations) / len(durations), 1) if durations else 0.0,
        "by_horizon": {h: {"count": len(v), "pnl": round(sum(v), 2)} for h, v in by_horizon.items()},
        "by_pattern": {p: {"count": len(v), "pnl": round(sum(v), 2)} for p, v in by_pattern.items()},
    }
