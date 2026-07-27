"""
R4 — Drawdown Halt Threshold Experiment.

Question:
    At what realised drawdown should the system automatically suspend
    trading because historical recovery probability becomes unacceptable?

Outputs:
    - drawdown_distribution
    - recovery_probability by threshold
    - historical_recovery_time
    - recommended halt threshold
    - resume threshold
    - recommendation (HALT / CONTINUE / MONITOR)

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)

_MIN_SAMPLES = 50
_RISK_PER_TRADE = 0.01
_THRESHOLDS_TO_TEST = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]


def _simulate_equity_curve(r_values: list[float], risk_per_trade: float = _RISK_PER_TRADE) -> dict[str, Any]:
    """Simulate equity curve and compute drawdown statistics."""
    equity = 1.0
    peak = 1.0
    drawdowns: list[float] = []
    max_dd = 0.0
    dd_durations: list[int] = []  # bars in drawdown
    current_dd_start: int | None = None

    for i, r in enumerate(r_values):
        equity += r * risk_per_trade
        if equity > peak:
            peak = equity
            if current_dd_start is not None:
                dd_durations.append(i - current_dd_start)
                current_dd_start = None
        else:
            if current_dd_start is None:
                current_dd_start = i

        dd = (peak - equity) / peak if peak > 0 else 0
        drawdowns.append(dd)
        max_dd = max(max_dd, dd)

    if current_dd_start is not None:
        dd_durations.append(len(r_values) - current_dd_start)

    return {
        "max_drawdown": round(max_dd, 4),
        "avg_drawdown": round(sum(drawdowns) / len(drawdowns), 4) if drawdowns else 0,
        "drawdown_count": sum(1 for d in drawdowns if d > 0.05),
        "avg_recovery_trades": round(sum(dd_durations) / len(dd_durations), 1) if dd_durations else 0,
        "max_recovery_trades": max(dd_durations) if dd_durations else 0,
        "final_equity": round(equity, 4),
    }


def _recovery_analysis(r_values: list[float], thresholds: list[float]) -> list[dict[str, Any]]:
    """For each threshold, compute recovery probability and time."""
    results: list[dict[str, Any]] = []
    n = len(r_values)

    for threshold in thresholds:
        # Simulate: how often does DD exceed threshold, and does it recover?
        equity = 1.0
        peak = 1.0
        breaches = 0
        recoveries = 0
        recovery_times: list[int] = []

        in_breach = False
        breach_start = 0

        for i, r in enumerate(r_values):
            equity += r * _RISK_PER_TRADE
            if equity > peak:
                peak = equity
                if in_breach:
                    recoveries += 1
                    recovery_times.append(i - breach_start)
                    in_breach = False

            dd = (peak - equity) / peak if peak > 0 else 0
            if dd >= threshold and not in_breach:
                breaches += 1
                in_breach = True
                breach_start = i

        recovery_prob = recoveries / breaches if breaches > 0 else 1.0
        avg_recovery = sum(recovery_times) / len(recovery_times) if recovery_times else 0

        results.append({
            "threshold": threshold,
            "breaches": breaches,
            "recoveries": recoveries,
            "recovery_probability": round(recovery_prob, 4),
            "avg_recovery_trades": round(avg_recovery, 1),
            "recommendation": "HALT" if recovery_prob < 0.50 else "MONITOR" if recovery_prob < 0.80 else "CONTINUE",
        })

    return results


def run_drawdown_threshold(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run R4: Drawdown Halt Threshold experiment."""
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(shadow_trades, min_samples=_MIN_SAMPLES, require_outcome=True)
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="R4", status=status,
            overall={"reason": reason}, confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)),
            recommendation="WAIT", warnings=[reason],
        )

    r_values = extract_r_multiples(shadow_trades)
    if len(r_values) < _MIN_SAMPLES:
        return build_report(
            question_id="R4", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {len(r_values)} R-multiples"},
            confidence="INSUFFICIENT_DATA",
            dataset={"r_multiples": len(r_values)},
            fingerprint=build_fingerprint(len(r_values), len(shadow_trades) - len(r_values)),
            recommendation="WAIT",
        )

    n = len(r_values)
    equity_stats = _simulate_equity_curve(r_values)
    recovery_analysis = _recovery_analysis(r_values, _THRESHOLDS_TO_TEST)

    # Find recommended halt threshold (first where recovery < 50%)
    halt_threshold = None
    resume_threshold = None
    for ra in recovery_analysis:
        if ra["recommendation"] == "HALT" and halt_threshold is None:
            halt_threshold = ra["threshold"]
        if ra["recommendation"] == "CONTINUE" and halt_threshold is not None and resume_threshold is None:
            resume_threshold = ra["threshold"]

    if halt_threshold is None:
        halt_threshold = 0.50  # Default: very conservative
        recommended_halt = "No halt needed at tested thresholds. System shows strong recovery."
    else:
        recommended_halt = f"Halt trading at {halt_threshold:.0%} drawdown. Recovery probability drops below 50%."

    if resume_threshold is None:
        resume_threshold = halt_threshold * 0.5  # Resume at half the halt threshold

    confidence = compute_confidence(n, equity_stats["max_drawdown"] < 0.30)
    recommendation = "PROMOTE" if halt_threshold >= 0.20 and confidence in ("HIGH", "MEDIUM") else "MONITOR"

    finding = f"Recommended halt at {halt_threshold:.0%} DD. Max observed DD: {equity_stats['max_drawdown']:.1%}. Avg recovery: {equity_stats['avg_recovery_trades']} trades."

    report = build_report(
        question_id="R4", status=ReadinessStatus.COMPLETE,
        overall={
            "recommended_halt_threshold": halt_threshold,
            "resume_threshold": resume_threshold,
            "max_observed_drawdown": equity_stats["max_drawdown"],
            "avg_drawdown": equity_stats["avg_drawdown"],
            "avg_recovery_trades": equity_stats["avg_recovery_trades"],
            "max_recovery_trades": equity_stats["max_recovery_trades"],
            "recovery_analysis": recovery_analysis,
        },
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "r_multiples_used": n, "coverage": coverage},
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=[f"Risk per trade: {_RISK_PER_TRADE:.1%}", "Sequential simulation of observed R-multiples"],
        provenance={"experiment_module": "research_engine.experiments.drawdown_threshold", "registry_id": "R4", "function": "run_drawdown_threshold", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "r4_drawdown_threshold.json")
    update_knowledge_map("R4", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_drawdown_threshold()
    o = result.get("overall", {})
    print(f"R4: halt={o.get('recommended_halt_threshold', '?')} | max_dd={o.get('max_observed_drawdown', '?')} | rec={result.get('recommendation')}")
