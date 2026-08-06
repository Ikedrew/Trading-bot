"""
Research Anomaly Classification — Lambda-compatible version.

Extracted from core/research_anomaly.py. Same logic, no local filesystem dependency.
"""

from __future__ import annotations

import statistics
from typing import Any


# Instrument classification (simplified for Lambda — no external imports)
_FX_SYMBOLS = frozenset({
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    "EURJPY", "GBPJPY", "EURGBP", "AUDCAD", "AUDNZD", "NZDCAD",
})
_INDEX_SYMBOLS = frozenset({"US500", "NAS100", "US30", "GER40", "UK100", "JPN225"})
_COMMODITY_SYMBOLS = frozenset({"XAUUSD", "XAGUSD", "XTIUSD", "XBRUSD"})


def get_instrument_class(symbol: str) -> str:
    s = symbol.upper().rstrip("_SB").rstrip(".C")
    if s in _FX_SYMBOLS or (len(s) == 6 and s[:3].isalpha() and s[3:].isalpha()):
        if "JPY" in s:
            return "FX_JPY"
        return "FX_MAJOR"
    for idx in _INDEX_SYMBOLS:
        if idx in s:
            return "INDEX"
    for cmd in _COMMODITY_SYMBOLS:
        if cmd in s:
            return "COMMODITY"
    return "UNKNOWN"


def classify_anomalies(
    trades: list[dict[str, Any]],
    extreme_r_high: float = 5.0,
    extreme_r_low: float = -3.0,
    extreme_pnl_percentile: float = 2.5,
) -> list[dict[str, Any]]:
    """
    Add anomaly_status and anomaly_reasons to each trade.

    Returns new list with added fields (does not modify originals).
    """
    # Compute R values if not present
    for t in trades:
        if "realised_r" not in t:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            exit_price = t.get("exit_price", 0)
            direction = t.get("direction", "")
            risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0
            if risk_distance > 0 and exit_price > 0:
                price_move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
                t["realised_r"] = round(price_move / risk_distance, 4)
            else:
                t["realised_r"] = 0.0

    # Compute PnL percentiles
    pnl_values = sorted([t.get("final_pnl", 0) or 0 for t in trades])
    n = len(pnl_values)
    if n > 10:
        idx_low = int(n * extreme_pnl_percentile / 100)
        idx_high = int(n * (100 - extreme_pnl_percentile) / 100)
        pnl_floor = pnl_values[idx_low]
        pnl_ceil = pnl_values[idx_high]
    else:
        pnl_floor = -999999
        pnl_ceil = 999999

    result = []
    for t in trades:
        reasons = []
        r = t.get("realised_r", 0)
        pnl = t.get("final_pnl", 0) or 0
        inst_class = t.get("instrument_class", "") or get_instrument_class(t.get("symbol", ""))

        if r > extreme_r_high or r < extreme_r_low:
            reasons.append("EXTREME_R_MULTIPLE")
        if pnl < pnl_floor or pnl > pnl_ceil:
            reasons.append("EXTREME_PNL")
        if inst_class not in ("FX_MAJOR", "FX_JPY"):
            reasons.append("NON_FX_INSTRUMENT")

        annotated = dict(t)
        annotated["anomaly_status"] = "FLAGGED" if reasons else "NORMAL"
        annotated["anomaly_reasons"] = reasons
        annotated["instrument_class"] = inst_class
        result.append(annotated)

    return result


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
    }


def build_anomaly_report(trades: list[dict[str, Any]], config: dict) -> dict[str, Any]:
    """
    Build the full anomaly analysis report from annotated trades.

    Args:
        trades: Raw trades (will be annotated)
        config: Dict with extreme_r_high, extreme_r_low, extreme_pnl_percentile

    Returns:
        Report dict ready for JSON serialization.
    """
    from datetime import datetime, timezone

    annotated = classify_anomalies(
        trades,
        extreme_r_high=config.get("extreme_r_high", 5.0),
        extreme_r_low=config.get("extreme_r_low", -3.0),
        extreme_pnl_percentile=config.get("extreme_pnl_percentile", 2.5),
    )

    # Filter views
    full = annotated
    fx_only = [t for t in annotated if t.get("instrument_class", "") in ("FX_MAJOR", "FX_JPY")]
    normalised = [t for t in annotated if t.get("anomaly_status") == "NORMAL"]
    index_only = [t for t in annotated if t.get("instrument_class", "") == "INDEX"]
    flagged = [t for t in annotated if t["anomaly_status"] == "FLAGGED"]

    # Reason counts
    reason_counts: dict[str, int] = {}
    for t in flagged:
        for r in t["anomaly_reasons"]:
            reason_counts[r] = reason_counts.get(r, 0) + 1

    # Metrics
    metrics = {
        "FULL": compute_view_metrics(full),
        "FX_ONLY": compute_view_metrics(fx_only),
        "NORMALISED": compute_view_metrics(normalised),
        "INDEX_ONLY": compute_view_metrics(index_only),
    }

    return {
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


def format_markdown_report(report: dict[str, Any]) -> str:
    """Generate markdown report string from report dict."""
    md = []
    md.append("# Research Dataset Anomaly Analysis")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append("")
    md.append("## Dataset Summary")
    md.append("")
    md.append("| Dataset | Trades |")
    md.append("|---|---|")
    for k, v in report["dataset_counts"].items():
        md.append(f"| {k} | {v} |")
    md.append("")
    md.append("## Anomaly Reasons")
    md.append("")
    md.append("| Reason | Count |")
    md.append("|---|---|")
    for reason, count in report.get("anomaly_reasons", {}).items():
        md.append(f"| {reason} | {count} |")
    md.append("")
    md.append("## Metrics by View")
    md.append("")
    md.append("| Metric | Full | FX Only | Normalised | Index Only |")
    md.append("|---|---|---|---|---|")
    metrics = report["metrics_by_view"]
    for metric in ["count", "win_rate", "total_pnl", "average_r", "profit_factor"]:
        row = f"| {metric} |"
        for view in ["FULL", "FX_ONLY", "NORMALISED", "INDEX_ONLY"]:
            val = metrics.get(view, {}).get(metric, 0)
            if metric == "win_rate":
                row += f" {val:.0%} |"
            elif metric in ("total_pnl",):
                row += f" ${val:.2f} |"
            else:
                row += f" {val} |"
        md.append(row)
    md.append("")
    md.append("---")
    md.append("*Generated by Lambda anomaly analysis job*")
    return "\n".join(md)
