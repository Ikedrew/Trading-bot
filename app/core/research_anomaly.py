"""
Research Anomaly Handling — Classifies trades and provides filtered dataset views.

Does NOT delete trades. Does NOT modify historical records.
Adds metadata for research filtering.

Views:
    FULL         — all validated trades (source of truth)
    FX_ONLY      — instrument_class in (FX_MAJOR, FX_JPY)
    NORMALISED   — anomaly_status == NORMAL (no extremes)
    INDEX_ONLY   — instrument_class == INDEX

Usage:
    from core.research_anomaly import load_research_view, DatasetView
    trades = load_research_view(DatasetView.FX_ONLY)
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.instrument_utils import get_instrument_class, InstrumentClass

logger = logging.getLogger(__name__)

_RESEARCH_READY_DIR = "logs/research_ready_trade_dataset"
_OUTPUT_DIR = "logs/research_ready_trade_dataset"  # Annotated version in same dir


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

EXTREME_R_HIGH = 5.0      # Flag R > +5
EXTREME_R_LOW = -3.0      # Flag R < -3
EXTREME_PNL_PERCENTILE = 2.5  # Top/bottom 2.5%


class DatasetView(str, Enum):
    FULL = "FULL"
    FX_ONLY = "FX_ONLY"
    NORMALISED = "NORMALISED"
    INDEX_ONLY = "INDEX_ONLY"


# ═══════════════════════════════════════════════════════════════
# ANOMALY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

def classify_anomalies(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add anomaly_status and anomaly_reasons to each trade.

    Does NOT modify the original trade dicts — returns new list with added fields.
    """
    # Compute percentiles for PnL flagging
    pnl_values = sorted([t.get("final_pnl", 0) or 0 for t in trades])
    n = len(pnl_values)
    if n > 10:
        idx_low = int(n * EXTREME_PNL_PERCENTILE / 100)
        idx_high = int(n * (100 - EXTREME_PNL_PERCENTILE) / 100)
        pnl_floor = pnl_values[idx_low]
        pnl_ceil = pnl_values[idx_high]
    else:
        pnl_floor = -999999
        pnl_ceil = 999999

    # Compute R values
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        exit_price = t.get("exit_price", 0)
        direction = t.get("direction", "")
        risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0
        if risk_distance > 0 and exit_price > 0:
            price_move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
            t["realised_r"] = round(price_move / risk_distance, 4)
        elif "realised_r" not in t:
            t["realised_r"] = 0.0

    result = []
    for t in trades:
        reasons = []
        r = t.get("realised_r", 0)
        pnl = t.get("final_pnl", 0) or 0
        inst_class = t.get("instrument_class", "") or get_instrument_class(t.get("symbol", "")).value

        # Rule 1: Extreme R multiple
        if r > EXTREME_R_HIGH or r < EXTREME_R_LOW:
            reasons.append("EXTREME_R_MULTIPLE")

        # Rule 2: Extreme PnL (percentile-based)
        if pnl < pnl_floor or pnl > pnl_ceil:
            reasons.append("EXTREME_PNL")

        # Rule 3: Non-FX instrument
        if inst_class not in ("FX_MAJOR", "FX_JPY"):
            reasons.append("NON_FX_INSTRUMENT")

        annotated = dict(t)
        annotated["anomaly_status"] = "FLAGGED" if reasons else "NORMAL"
        annotated["anomaly_reasons"] = reasons
        result.append(annotated)

    return result


# ═══════════════════════════════════════════════════════════════
# DATASET VIEWS
# ═══════════════════════════════════════════════════════════════

def filter_view(trades: list[dict[str, Any]], view: DatasetView) -> list[dict[str, Any]]:
    """Filter annotated trades by view."""
    if view == DatasetView.FULL:
        return trades
    elif view == DatasetView.FX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") in ("FX_MAJOR", "FX_JPY")]
    elif view == DatasetView.NORMALISED:
        return [t for t in trades if t.get("anomaly_status") == "NORMAL"]
    elif view == DatasetView.INDEX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") == "INDEX"]
    return trades


def load_research_view(view: DatasetView = DatasetView.FULL) -> list[dict[str, Any]]:
    """
    Load research-ready trades, classify anomalies, and return filtered view.

    This is the primary entry point for research experiments.
    """
    data_file = Path(_RESEARCH_READY_DIR) / "research_ready_trades.jsonl"
    if not data_file.exists():
        logger.warning("[RESEARCH_ANOMALY] research_ready_trades.jsonl not found")
        return []

    trades = []
    for line in data_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    annotated = classify_anomalies(trades)
    return filter_view(annotated, view)


# ═══════════════════════════════════════════════════════════════
# IMPACT ANALYSIS
# ═══════════════════════════════════════════════════════════════

def compute_view_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute key metrics for a dataset view."""
    if not trades:
        return {"count": 0}

    n = len(trades)
    r_vals = [t.get("realised_r", 0) for t in trades]
    pnl_vals = [t.get("final_pnl", 0) or 0 for t in trades]
    winners = [t for t in trades if (t.get("final_pnl", 0) or 0) > 0]
    win_rate = len(winners) / n

    gp = sum(p for p in pnl_vals if p > 0)
    gl = sum(p for p in pnl_vals if p < 0)
    pf = gp / abs(gl) if gl != 0 else (999 if gp > 0 else 0)

    return {
        "count": n,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(sum(pnl_vals), 2),
        "average_r": round(statistics.mean(r_vals), 4) if r_vals else 0,
        "median_r": round(statistics.median(r_vals), 4) if r_vals else 0,
        "profit_factor": round(pf, 2),
        "avg_pnl": round(sum(pnl_vals) / n, 4),
    }


def build_anomaly_report(output_dir: str | None = None) -> dict[str, Any]:
    """
    Build the full anomaly analysis report.

    Returns summary dict and writes JSON + MD reports.
    """
    data_file = Path(_RESEARCH_READY_DIR) / "research_ready_trades.jsonl"
    trades = []
    for line in data_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    annotated = classify_anomalies(trades)

    # Views
    full = filter_view(annotated, DatasetView.FULL)
    fx_only = filter_view(annotated, DatasetView.FX_ONLY)
    normalised = filter_view(annotated, DatasetView.NORMALISED)
    index_only = filter_view(annotated, DatasetView.INDEX_ONLY)

    # Flagged trades
    flagged = [t for t in annotated if t["anomaly_status"] == "FLAGGED"]
    reason_counts: dict[str, int] = {}
    for t in flagged:
        for r in t["anomaly_reasons"]:
            reason_counts[r] = reason_counts.get(r, 0) + 1

    # Metrics per view
    metrics = {
        "FULL": compute_view_metrics(full),
        "FX_ONLY": compute_view_metrics(fx_only),
        "NORMALISED": compute_view_metrics(normalised),
        "INDEX_ONLY": compute_view_metrics(index_only),
    }

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_counts": {
            "full": len(full),
            "fx_only": len(fx_only),
            "normalised": len(normalised),
            "index_only": len(index_only),
            "flagged": len(flagged),
        },
        "anomaly_reasons": dict(sorted(reason_counts.items(), key=lambda x: -x[1])),
        "metrics_by_view": metrics,
        "flagged_trades": [
            {"trade_id": t.get("trade_id"), "symbol": t.get("symbol"),
             "realised_r": t.get("realised_r"), "final_pnl": t.get("final_pnl"),
             "anomaly_reasons": t["anomaly_reasons"]}
            for t in flagged
        ],
    }

    # Write reports
    out_path = Path(output_dir or "reports/research")
    out_path.mkdir(parents=True, exist_ok=True)

    (out_path / "anomaly_analysis_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    # Markdown
    md = []
    md.append("# Research Dataset Anomaly Analysis")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append("")
    md.append("## Dataset Summary")
    md.append("")
    md.append("| Dataset | Trades | Description |")
    md.append("|---|---|---|")
    md.append(f"| Full | {len(full)} | All validated trades (source of truth) |")
    md.append(f"| FX Only | {len(fx_only)} | Core forex system |")
    md.append(f"| Normalised | {len(normalised)} | Without extreme events |")
    md.append(f"| Index Only | {len(index_only)} | Index trades separately |")
    md.append(f"| Flagged | {len(flagged)} | Trades with anomalies |")

    md.append("")
    md.append("## Flagged Trade Summary")
    md.append("")
    md.append("| Reason | Count |")
    md.append("|---|---|")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        md.append(f"| {reason} | {count} |")

    md.append("")
    md.append("## Impact Analysis")
    md.append("")
    md.append("| Metric | Full | FX Only | Normalised | Index Only |")
    md.append("|---|---|---|---|---|")
    for metric in ["count", "win_rate", "total_pnl", "average_r", "median_r", "profit_factor"]:
        row = f"| {metric} |"
        for view in ["FULL", "FX_ONLY", "NORMALISED", "INDEX_ONLY"]:
            val = metrics[view].get(metric, 0)
            if metric == "win_rate":
                row += f" {val:.0%} |"
            elif metric in ("total_pnl", "avg_pnl"):
                row += f" ${val:.2f} |"
            elif metric == "count":
                row += f" {val} |"
            else:
                row += f" {val:.4f} |"
        md.append(row)

    md.append("")
    md.append("## Key Questions Answered")
    md.append("")

    # Is V10 profitable because of repeatable behaviour?
    norm_pnl = metrics["NORMALISED"]["total_pnl"]
    full_pnl = metrics["FULL"]["total_pnl"]
    md.append(f"### 1. Is V10 profitable because of repeatable behaviour?")
    if norm_pnl > 0:
        md.append(f"YES — Normalised dataset (no extremes) shows ${norm_pnl:.2f} profit")
    elif norm_pnl < 0:
        md.append(f"NO — Normalised dataset shows ${norm_pnl:.2f} (negative). Profit depends on extremes.")
    else:
        md.append(f"INCONCLUSIVE — Normalised PnL is near zero.")

    md.append("")
    md.append("### 2. Are results dependent on extreme events?")
    extreme_pnl = full_pnl - norm_pnl
    md.append(f"Extreme events contribute ${extreme_pnl:.2f} to total PnL (full=${full_pnl:.2f}, normalised=${norm_pnl:.2f})")
    if abs(extreme_pnl) > abs(norm_pnl) * 2:
        md.append("**YES** — extreme events dominate results")
    else:
        md.append("**NO** — normalised results are the primary driver")

    md.append("")
    md.append("### 3. Are FX and index behaviour different?")
    fx_r = metrics["FX_ONLY"]["average_r"]
    idx_r = metrics["INDEX_ONLY"]["average_r"]
    md.append(f"FX average R: {fx_r:.4f} | Index average R: {idx_r:.4f}")
    if abs(fx_r - idx_r) > 0.5:
        md.append("**YES** — significantly different behaviour")
    else:
        md.append("Similar behaviour or insufficient index sample")

    md.append("")
    md.append("### 4. Should future research separate instruments?")
    if len(index_only) >= 5 and abs(fx_r - idx_r) > 0.3:
        md.append("**YES** — index trades should be analysed separately")
    else:
        md.append("Not critical yet — index sample too small for reliable separate analysis")

    md.append("")
    md.append("## Flagged Trades Detail")
    md.append("")
    md.append("| Trade ID | Symbol | R | PnL | Reasons |")
    md.append("|---|---|---|---|---|")
    for t in flagged:
        md.append(f"| {t.get('trade_id','')} | {t.get('symbol','')} | "
                 f"{t.get('realised_r', 0):+.2f} | ${t.get('final_pnl', 0):.2f} | "
                 f"{', '.join(t['anomaly_reasons'])} |")

    md.append("")
    md.append("---")
    md.append("*Anomaly classification is for research filtering only. No trades were removed or modified.*")

    (out_path / "anomaly_analysis_report.md").write_text("\n".join(md), encoding="utf-8")

    return report
