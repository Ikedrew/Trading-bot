"""V10 Research Base — Shared utilities."""
from __future__ import annotations
import statistics
from datetime import datetime, timezone
from typing import Any

def compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"count": 0, "win_rate": 0, "loss_rate": 0}
    n = len(trades)
    r_vals = [t.get("realised_r", 0) for t in trades]
    pnl_vals = [t.get("final_pnl", 0) or 0 for t in trades]
    winners = [t for t in trades if t.get("realised_r", 0) > 0]
    losers = [t for t in trades if t.get("realised_r", 0) <= 0]
    win_rate = len(winners) / n
    loss_rate = len(losers) / n
    win_r = [t["realised_r"] for t in winners]
    loss_r = [t["realised_r"] for t in losers]
    avg_win_r = statistics.mean(win_r) if win_r else 0
    avg_loss_r = statistics.mean(loss_r) if loss_r else 0
    expectancy_r = (win_rate * avg_win_r) + (loss_rate * avg_loss_r)
    gp = sum(p for p in pnl_vals if p > 0)
    gl = sum(p for p in pnl_vals if p < 0)
    pf = gp / abs(gl) if gl != 0 else (999 if gp > 0 else 0)
    return {
        "count": n, "win_rate": round(win_rate, 4), "loss_rate": round(loss_rate, 4),
        "winners": len(winners), "losers": len(losers),
        "average_r": round(statistics.mean(r_vals), 4),
        "median_r": round(statistics.median(r_vals), 4),
        "std_r": round(statistics.stdev(r_vals), 4) if n > 1 else 0,
        "average_win_r": round(avg_win_r, 4), "average_loss_r": round(avg_loss_r, 4),
        "expectancy_r": round(expectancy_r, 4),
        "total_pnl": round(sum(pnl_vals), 2),
        "average_pnl": round(sum(pnl_vals) / n, 4),
        "largest_winner": round(max(pnl_vals), 4) if pnl_vals else 0,
        "largest_loser": round(min(pnl_vals), 4) if pnl_vals else 0,
        "profit_factor": round(pf, 2),
        "confidence": classify_confidence(n),
    }

def classify_confidence(n: int) -> str:
    if n >= 30: return "HIGH"
    elif n >= 10: return "MEDIUM"
    return "LOW"

def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()
