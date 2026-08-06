"""
V10 Research Base — Utilities shared by all experiment modules.

Provides:
    - Standard metric computation
    - Report formatting
    - Confidence classification
    - Result structure
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_REPORTS_DIR = "reports/research"


def compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute standard performance metrics for a group of trades.

    Returns a dict with all common research metrics.
    """
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

    gross_profit = sum(p for p in pnl_vals if p > 0)
    gross_loss = sum(p for p in pnl_vals if p < 0)
    profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else (999 if gross_profit > 0 else 0)

    return {
        "count": n,
        "win_rate": round(win_rate, 4),
        "loss_rate": round(loss_rate, 4),
        "winners": len(winners),
        "losers": len(losers),
        "average_r": round(statistics.mean(r_vals), 4),
        "median_r": round(statistics.median(r_vals), 4),
        "std_r": round(statistics.stdev(r_vals), 4) if n > 1 else 0,
        "average_win_r": round(avg_win_r, 4),
        "average_loss_r": round(avg_loss_r, 4),
        "expectancy_r": round(expectancy_r, 4),
        "total_pnl": round(sum(pnl_vals), 2),
        "average_pnl": round(sum(pnl_vals) / n, 4),
        "largest_winner": round(max(pnl_vals), 4) if pnl_vals else 0,
        "largest_loser": round(min(pnl_vals), 4) if pnl_vals else 0,
        "profit_factor": round(profit_factor, 2),
        "confidence": classify_confidence(n),
    }


def classify_confidence(n: int) -> str:
    """Classify statistical confidence based on sample size."""
    if n >= 30:
        return "HIGH"
    elif n >= 10:
        return "MEDIUM"
    else:
        return "LOW"


def save_report(report: dict[str, Any], filename: str, reports_dir: str | None = None) -> tuple[str, str]:
    """
    Save report as JSON + Markdown.

    Returns: (json_path, md_path)
    """
    out_dir = Path(reports_dir or _REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{filename}.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    md_path = out_dir / f"{filename}.md"
    if "markdown" in report:
        md_path.write_text(report["markdown"], encoding="utf-8")

    return str(json_path), str(md_path)


def timestamp_now() -> str:
    """ISO timestamp for report generation."""
    return datetime.now(timezone.utc).isoformat()
