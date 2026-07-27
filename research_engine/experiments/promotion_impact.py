"""
P1 — Promotion Impact Analysis Experiment.

Question:
    If a specific recommendation is promoted into production, what
    measurable improvement in EV, win rate, drawdown, trade frequency,
    and risk is expected?

Produces: PROMOTE / WAIT / REJECT with confidence.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    _deep_get,
    build_fingerprint,
    build_report,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)

_MIN_SAMPLES = 100


def _estimate_pattern_removal_impact(
    r_values_by_pattern: dict[str, list[float]],
    patterns_to_remove: list[str],
) -> dict[str, Any]:
    """Estimate impact of removing specific patterns."""
    removed_r: list[float] = []
    kept_r: list[float] = []
    for pattern, rs in r_values_by_pattern.items():
        if pattern in patterns_to_remove:
            removed_r.extend(rs)
        else:
            kept_r.extend(rs)

    if not kept_r:
        return {"ev_change": 0, "trade_reduction": 0, "feasible": False}

    current_ev = sum(removed_r + kept_r) / len(removed_r + kept_r) if (removed_r + kept_r) else 0
    new_ev = sum(kept_r) / len(kept_r) if kept_r else 0
    trade_reduction = len(removed_r) / (len(removed_r) + len(kept_r)) if (removed_r + kept_r) else 0

    return {
        "ev_change": round(new_ev - current_ev, 4),
        "current_ev": round(current_ev, 4),
        "projected_ev": round(new_ev, 4),
        "trades_removed": len(removed_r),
        "trades_remaining": len(kept_r),
        "trade_reduction_pct": round(trade_reduction * 100, 1),
        "feasible": True,
    }


def _estimate_threshold_change_impact(
    r_values: list[float],
    scores: list[float],
    current_threshold: float,
    new_threshold: float,
) -> dict[str, Any]:
    """Estimate impact of changing score threshold."""
    if not r_values or not scores or len(r_values) != len(scores):
        return {"ev_change": 0, "feasible": False}

    current_trades = [(r, s) for r, s in zip(r_values, scores) if s >= current_threshold]
    new_trades = [(r, s) for r, s in zip(r_values, scores) if s >= new_threshold]

    current_ev = sum(r for r, _ in current_trades) / len(current_trades) if current_trades else 0
    new_ev = sum(r for r, _ in new_trades) / len(new_trades) if new_trades else 0

    return {
        "ev_change": round(new_ev - current_ev, 4),
        "current_ev": round(current_ev, 4),
        "projected_ev": round(new_ev, 4),
        "current_trades": len(current_trades),
        "projected_trades": len(new_trades),
        "trade_change_pct": round((len(new_trades) - len(current_trades)) / max(len(current_trades), 1) * 100, 1),
        "feasible": len(new_trades) >= 10,
    }


def run_promotion_impact(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Run P1: Promotion Impact Analysis.

    Estimates the impact of the most likely promotion candidates:
    1. Removing negative-EV patterns
    2. Adjusting score thresholds
    3. Strategy-specific filtering
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(
        shadow_trades, min_samples=_MIN_SAMPLES,
        require_lineage=True, require_outcome=True, require_strategy=True,
    )
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="P1", status=status, overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)), recommendation="WAIT", warnings=[reason],
        )

    # Extract data by pattern
    r_by_pattern: dict[str, list[float]] = defaultdict(list)
    all_r: list[float] = []
    all_scores: list[float] = []

    for record in shadow_trades:
        rm = _deep_get(record, "simulated_outcome", "pnl_r_multiple")
        if rm is None:
            continue
        r = float(rm)
        all_r.append(r)
        pattern = _deep_get(record, "decision_snapshot", "pattern") or "UNKNOWN"
        r_by_pattern[pattern].append(r)
        score = _deep_get(record, "decision_snapshot", "score") or 0
        all_scores.append(float(score))

    n = len(all_r)
    if n < _MIN_SAMPLES:
        return build_report(
            question_id="P1", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} R-multiples"}, confidence="INSUFFICIENT_DATA",
            dataset={"r_multiples": n}, fingerprint=build_fingerprint(n, len(shadow_trades) - n),
            recommendation="WAIT",
        )

    # Identify negative-EV patterns (candidates for removal)
    negative_patterns: list[str] = []
    pattern_analysis: list[dict[str, Any]] = []
    for pattern, rs in r_by_pattern.items():
        if len(rs) >= 10:
            avg = sum(rs) / len(rs)
            pattern_analysis.append({"pattern": pattern, "n": len(rs), "ev": round(avg, 4)})
            if avg < -0.2:  # Strongly negative
                negative_patterns.append(pattern)

    pattern_analysis.sort(key=lambda p: p["ev"])

    # Impact estimates
    impact_remove_worst = _estimate_pattern_removal_impact(r_by_pattern, negative_patterns)

    # Threshold impact (test raising threshold from 0.35 to 0.45)
    impact_raise_threshold = _estimate_threshold_change_impact(all_r, all_scores, 0.35, 0.45)

    # Overall current metrics
    current_ev = sum(all_r) / n
    current_wr = sum(1 for r in all_r if r > 0) / n

    # Best single promotion
    best_ev_change = max(
        impact_remove_worst.get("ev_change", 0),
        impact_raise_threshold.get("ev_change", 0),
    )

    confidence = compute_confidence(n, best_ev_change > 0.05)

    # Recommendation
    if best_ev_change > 0.10 and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = f"Promotion candidate identified: removing {len(negative_patterns)} patterns improves EV by {impact_remove_worst.get('ev_change', 0):+.4f}R."
    elif best_ev_change > 0:
        recommendation = "WAIT"
        finding = f"Small improvement possible ({best_ev_change:+.4f}R) but not yet confident enough."
    else:
        recommendation = "REJECT"
        finding = "No promotion candidate improves EV. Current configuration is near-optimal."

    report = build_report(
        question_id="P1", status=ReadinessStatus.COMPLETE,
        overall={
            "current_ev": round(current_ev, 4),
            "current_win_rate": round(current_wr, 4),
            "best_ev_improvement": round(best_ev_change, 4),
            "impact_remove_patterns": impact_remove_worst,
            "impact_raise_threshold": impact_raise_threshold,
            "negative_patterns": negative_patterns,
            "pattern_analysis": pattern_analysis[:10],
            "promotable": best_ev_change > 0.05,
        },
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "r_multiples_used": n, "patterns_found": len(r_by_pattern), "coverage": coverage},
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=["Pattern removal: excludes all trades from negative-EV patterns", "Threshold change: 0.35 → 0.45 minimum score", "Impact estimated from historical shadow trades"],
        warnings=[w for w in [f"High contamination in dataset" if coverage.get("contamination_rate", 0) > 0.1 else "", f"Few patterns with sufficient data" if len(pattern_analysis) < 5 else ""] if w],
        provenance={"experiment_module": "research_engine.experiments.promotion_impact", "registry_id": "P1", "function": "run_promotion_impact", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "p1_promotion_impact.json")
    update_knowledge_map("P1", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_promotion_impact()
    o = result.get("overall", {})
    print(f"P1: best_improvement={o.get('best_ev_improvement', '?')} patterns_to_remove={o.get('negative_patterns', [])} rec={result.get('recommendation')}")
