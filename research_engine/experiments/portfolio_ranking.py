"""
D6 — Portfolio Ranking Quality Experiment.

Question:
    When multiple trades are available simultaneously, is the ranking
    model consistently choosing the highest expectancy opportunity?

Measures:
    - Ranking accuracy (did top-ranked produce best R?)
    - Opportunity cost (EV of chosen vs best available)
    - Missed EV
    - Top-N accuracy
    - Ranking quality score

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

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
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)

_MIN_SAMPLES = 30
_MIN_CONCURRENT_CYCLES = 10  # Need at least 10 cycles with multiple signals


def run_portfolio_ranking(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run D6: Portfolio Ranking Quality experiment."""
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(
        shadow_trades, min_samples=_MIN_SAMPLES, require_lineage=True, require_outcome=True,
    )
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="D6", status=status, overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)), recommendation="WAIT", warnings=[reason],
        )

    # Group trades by cycle_id to find concurrent opportunities
    by_cycle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in shadow_trades:
        cycle_id = (
            _deep_get(record, "identity", "cycle_id")
            or _deep_get(record, "decision_snapshot", "cycle_id")
            or ""
        )
        r_mult = _deep_get(record, "simulated_outcome", "pnl_r_multiple")
        if cycle_id and r_mult is not None:
            by_cycle[str(cycle_id)].append(record)

    # Filter to cycles with multiple concurrent trades
    concurrent_cycles = {cid: trades for cid, trades in by_cycle.items() if len(trades) >= 2}

    if len(concurrent_cycles) < _MIN_CONCURRENT_CYCLES:
        return build_report(
            question_id="D6", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {len(concurrent_cycles)} cycles with multiple trades (need {_MIN_CONCURRENT_CYCLES})", "total_cycles": len(by_cycle), "concurrent_cycles": len(concurrent_cycles)},
            confidence="INSUFFICIENT_DATA",
            dataset={"total_records": len(shadow_trades), "concurrent_cycles": len(concurrent_cycles)},
            fingerprint=build_fingerprint(len(shadow_trades), 0), recommendation="WAIT",
            warnings=[f"Need more concurrent opportunity data ({len(concurrent_cycles)}/{_MIN_CONCURRENT_CYCLES})"],
        )

    # Analyse ranking quality
    correct_selections = 0
    total_selections = 0
    opportunity_costs: list[float] = []
    missed_evs: list[float] = []

    for cid, trades in concurrent_cycles.items():
        # Get R-multiples for all trades in this cycle
        r_multiples = []
        for t in trades:
            rm = _deep_get(t, "simulated_outcome", "pnl_r_multiple")
            score = _deep_get(t, "decision_snapshot", "score") or 0
            r_multiples.append({"r": float(rm), "score": float(score)})

        if len(r_multiples) < 2:
            continue

        # The "selected" trade is the one with highest score (how ranking works)
        selected = max(r_multiples, key=lambda x: x["score"])
        best_available = max(r_multiples, key=lambda x: x["r"])

        total_selections += 1
        if selected["r"] >= best_available["r"] * 0.9:  # Within 10% of best
            correct_selections += 1

        cost = best_available["r"] - selected["r"]
        opportunity_costs.append(cost)
        if cost > 0:
            missed_evs.append(cost)

    # Compute metrics
    n_evaluated = total_selections
    if n_evaluated == 0:
        return build_report(
            question_id="D6", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": "No evaluable concurrent selections"},
            confidence="INSUFFICIENT_DATA", dataset={"concurrent_cycles": len(concurrent_cycles)},
            fingerprint=build_fingerprint(len(shadow_trades), 0), recommendation="WAIT",
        )

    ranking_accuracy = correct_selections / n_evaluated
    avg_opportunity_cost = sum(opportunity_costs) / len(opportunity_costs) if opportunity_costs else 0
    total_missed_ev = sum(missed_evs)
    avg_missed_ev = sum(missed_evs) / len(missed_evs) if missed_evs else 0
    ranking_score = ranking_accuracy * 100  # 0-100

    confidence = compute_confidence(n_evaluated, ranking_accuracy > 0.60)

    if ranking_accuracy >= 0.70 and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = f"Ranking selects best opportunity {ranking_accuracy:.0%} of the time. Avg cost: {avg_opportunity_cost:+.3f}R."
    elif ranking_accuracy >= 0.50:
        recommendation = "MONITOR"
        finding = f"Ranking accuracy {ranking_accuracy:.0%} is moderate. Opportunity cost: {avg_opportunity_cost:+.3f}R."
    else:
        recommendation = "RECALIBRATE"
        finding = f"Ranking accuracy {ranking_accuracy:.0%} is poor. Missing {total_missed_ev:.2f}R total."

    report = build_report(
        question_id="D6", status=ReadinessStatus.COMPLETE,
        overall={
            "ranking_accuracy": round(ranking_accuracy, 4),
            "opportunity_cost_avg": round(avg_opportunity_cost, 4),
            "missed_ev_total": round(total_missed_ev, 4),
            "missed_ev_avg": round(avg_missed_ev, 4),
            "ranking_quality_score": round(ranking_score, 1),
            "cycles_evaluated": n_evaluated,
            "correct_selections": correct_selections,
        },
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "concurrent_cycles": len(concurrent_cycles), "evaluable_selections": n_evaluated, "coverage": coverage},
        fingerprint=build_fingerprint(len(shadow_trades), 0),
        recommendation=recommendation,
        assumptions=["Selection = highest scored trade in cycle", "Correct = within 10% of best available R", "Opportunity cost = best_R - selected_R"],
        provenance={"experiment_module": "research_engine.experiments.portfolio_ranking", "registry_id": "D6", "function": "run_portfolio_ranking", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "d6_portfolio_ranking.json")
    update_knowledge_map("D6", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_portfolio_ranking()
    o = result.get("overall", {})
    print(f"D6: accuracy={o.get('ranking_accuracy', '?')} cost={o.get('opportunity_cost_avg', '?')} rec={result.get('recommendation')}")
