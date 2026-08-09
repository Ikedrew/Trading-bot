"""
V10 Research Anomaly Layer.

Classifies trades as NORMAL or FLAGGED based on statistical and structural rules.
NEVER removes trades. Anomaly status is metadata attached to each trade.

This layer enables dual-view research:
    STANDARD     - only NORMAL trades
    FULL_RAW     - all trades (NORMAL + FLAGGED)
    ANOMALY_ONLY - only FLAGGED trades

Usage:
    from research_engine.v10.anomaly_layer import classify_anomalies, AnomalyStatus

    trades = load_trades()
    result = classify_anomalies(trades)
    # result["trades"] has anomaly_status/anomaly_reasons on each trade
    # result["normal"] / result["flagged"] are pre-filtered lists
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

logger = logging.getLogger(__name__)

_ANOMALIES_FILE = "logs/research_views/anomalies.jsonl"


# ═══════════════════════════════════════════════════════════════
# THRESHOLDS (configurable per cycle if needed)
# ═══════════════════════════════════════════════════════════════

_EXTREME_R_THRESHOLD = 5.0       # |R| > 5 is unusual
_EXTREME_PNL_STDEV = 3.0        # PnL > 3 stdev from mean
_MIN_DURATION_SECONDS = 5.0     # Trades < 5s are suspicious
_MAX_RR_RATIO = 20.0            # R:R > 20 implies geometry issue
_MIN_RR_RATIO = 0.3             # R:R < 0.3 implies geometry issue


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def classify_anomalies(
    trades: list[dict[str, Any]],
    output_file: str | None = None,
) -> dict[str, Any]:
    """
    Classify each trade as NORMAL or FLAGGED.

    Mutates trades in-place (adds anomaly_status, anomaly_reasons).
    Also writes anomalies.jsonl for audit.

    Returns:
        {
            "total": int,
            "normal_count": int,
            "flagged_count": int,
            "trades": list (all, with status),
            "normal": list (NORMAL only),
            "flagged": list (FLAGGED only),
            "anomaly_summary": dict,
        }
    """
    if not trades:
        return {
            "total": 0, "normal_count": 0, "flagged_count": 0,
            "trades": [], "normal": [], "flagged": [],
            "anomaly_summary": {},
        }

    # Compute population stats for relative thresholds
    pnl_vals = [t.get("final_pnl", 0) or 0 for t in trades]
    pnl_mean = statistics.mean(pnl_vals) if pnl_vals else 0
    pnl_stdev = statistics.stdev(pnl_vals) if len(pnl_vals) > 1 else 0

    r_vals = [t.get("realised_r", 0) for t in trades]
    r_mean = statistics.mean(r_vals) if r_vals else 0
    r_stdev = statistics.stdev(r_vals) if len(r_vals) > 1 else 0

    # Classify each trade
    reason_counts: dict[str, int] = {}

    for t in trades:
        reasons = []

        # Rule 1: Extreme R-multiple
        r = t.get("realised_r", 0)
        if abs(r) > _EXTREME_R_THRESHOLD:
            reasons.append("EXTREME_R_MULTIPLE")

        # Rule 2: Extreme PnL (statistical outlier)
        pnl = t.get("final_pnl", 0) or 0
        if pnl_stdev > 0 and abs(pnl - pnl_mean) > _EXTREME_PNL_STDEV * pnl_stdev:
            reasons.append("EXTREME_PNL")

        # Rule 3: Invalid risk geometry
        rr = t.get("rr_ratio", 0)
        if rr > _MAX_RR_RATIO or (0 < rr < _MIN_RR_RATIO):
            reasons.append("INVALID_RISK_GEOMETRY")

        # Rule 4: Non-standard stop placement
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        direction = t.get("direction", "")
        if entry > 0 and sl > 0:
            if direction == "BUY" and sl >= entry:
                reasons.append("NON_STANDARD_STOP")
            elif direction == "SELL" and sl <= entry:
                reasons.append("NON_STANDARD_STOP")

        # Rule 5: Ultra-short duration
        duration = t.get("duration_seconds", 0)
        if 0 < duration < _MIN_DURATION_SECONDS:
            reasons.append("ULTRA_SHORT_DURATION")

        # Rule 6: Negative duration (timestamp inconsistency)
        if duration < 0:
            reasons.append("DATA_INCONSISTENCY")

        # Rule 7: Missing critical fields
        if not t.get("symbol") or not t.get("direction") or entry <= 0:
            reasons.append("DATA_INCONSISTENCY")

        # Assign status
        if reasons:
            t["anomaly_status"] = "FLAGGED"
            t["anomaly_reasons"] = reasons
        else:
            t["anomaly_status"] = "NORMAL"
            t["anomaly_reasons"] = []

        for r_name in reasons:
            reason_counts[r_name] = reason_counts.get(r_name, 0) + 1

    # Split into views
    normal = [t for t in trades if t["anomaly_status"] == "NORMAL"]
    flagged = [t for t in trades if t["anomaly_status"] == "FLAGGED"]

    # Write anomaly audit file
    _write_anomalies(flagged, output_file)

    logger.info(
        f"[ANOMALY_LAYER] {len(trades)} trades classified: "
        f"{len(normal)} NORMAL, {len(flagged)} FLAGGED"
    )

    return {
        "total": len(trades),
        "normal_count": len(normal),
        "flagged_count": len(flagged),
        "trades": trades,
        "normal": normal,
        "flagged": flagged,
        "anomaly_summary": {
            "reason_counts": reason_counts,
            "flagged_symbols": _symbol_breakdown(flagged),
            "impact": _compute_impact(normal, flagged),
            "thresholds": {
                "extreme_r": _EXTREME_R_THRESHOLD,
                "extreme_pnl_stdev": _EXTREME_PNL_STDEV,
                "min_duration_s": _MIN_DURATION_SECONDS,
                "max_rr": _MAX_RR_RATIO,
                "min_rr": _MIN_RR_RATIO,
            },
        },
    }


# ═══════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════

def _write_anomalies(flagged: list[dict], output_file: str | None = None) -> None:
    """Write anomaly audit log."""
    out = Path(output_file or _ANOMALIES_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    for t in flagged:
        entries.append({
            "trade_id": t.get("trade_id", ""),
            "position_ticket": t.get("position_ticket", 0),
            "symbol": t.get("symbol", ""),
            "direction": t.get("direction", ""),
            "anomaly_status": t["anomaly_status"],
            "anomaly_reasons": t["anomaly_reasons"],
            "realised_r": t.get("realised_r", 0),
            "final_pnl": t.get("final_pnl", 0),
            "rr_ratio": t.get("rr_ratio", 0),
            "duration_seconds": t.get("duration_seconds", 0),
            "entry_price": t.get("entry_price", 0),
            "stop_loss": t.get("stop_loss", 0),
        })

    lines = [json.dumps(e, default=str) for e in entries]
    out.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _symbol_breakdown(flagged: list[dict]) -> dict[str, int]:
    """Count flagged trades per symbol."""
    counts: dict[str, int] = {}
    for t in flagged:
        sym = t.get("symbol", "UNKNOWN")
        counts[sym] = counts.get(sym, 0) + 1
    return counts


def _compute_impact(normal: list[dict], flagged: list[dict]) -> dict[str, Any]:
    """Compute how anomalies impact overall metrics."""
    from research_engine.v10.base import compute_metrics

    all_trades = normal + flagged
    if not all_trades:
        return {}

    all_m = compute_metrics(all_trades)
    normal_m = compute_metrics(normal) if normal else {"expectancy_r": 0, "win_rate": 0}

    return {
        "full_raw_expectancy": all_m.get("expectancy_r", 0),
        "standard_expectancy": normal_m.get("expectancy_r", 0),
        "expectancy_diff": round(
            all_m.get("expectancy_r", 0) - normal_m.get("expectancy_r", 0), 4
        ),
        "full_raw_win_rate": all_m.get("win_rate", 0),
        "standard_win_rate": normal_m.get("win_rate", 0),
    }
