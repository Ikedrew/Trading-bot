"""
Phase 4B Orchestrator — Unified entry point for Contextual Expectancy Analysis.

STRICTLY OFFLINE — never imported by runtime code.
Does NOT modify execution, scoring, or risk logic.

Usage:
    from tools.cohort_analysis.phase4b_orchestrator import run_full_phase4b_report
    result = run_full_phase4b_report(enriched_trades)
"""

from __future__ import annotations

from typing import Any

from tools.cohort_analysis.expectancy_model import CohortKey, CohortStats, ExpectancyResult
from tools.cohort_analysis.expectancy_engine import build_expectancy_map, group_by_cohort
from tools.cohort_analysis.policy_engine import classify_policy, explain_policy, PolicyRecommendation


def run_expectancy_analysis(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Run complete contextual expectancy analysis on enriched trade records.

    Args:
        trades: List of enriched trade records (with confirmation, entry_timing,
                engine_state, outcome_rr, mfe_r, mae_r).

    Returns:
        Dict containing:
            - cohorts: list of ExpectancyResult (one per observed cohort)
            - policies: list of PolicyRecommendation (one per cohort)
            - raw_map: dict mapping CohortKey → CohortStats
    """
    expectancy_map = build_expectancy_map(trades)

    cohorts: list[ExpectancyResult] = []
    policies: list[PolicyRecommendation] = []

    for key, stats in expectancy_map.items():
        cohorts.append(ExpectancyResult(cohort=key, stats=stats))
        policies.append(explain_policy(stats, key))

    return {
        "cohorts": cohorts,
        "policies": policies,
        "raw_map": expectancy_map,
    }


def generate_policy_map(
    expectancy_map: dict[CohortKey, CohortStats],
) -> dict[CohortKey, dict[str, Any]]:
    """
    Generate policy recommendations for each cohort in the expectancy map.

    Args:
        expectancy_map: Mapping of CohortKey → CohortStats from build_expectancy_map().

    Returns:
        Dict mapping CohortKey → {policy, reason, stats}
    """
    policy_map: dict[CohortKey, dict[str, Any]] = {}

    for key, stats in expectancy_map.items():
        recommendation = explain_policy(stats, key)
        policy_map[key] = {
            "policy": recommendation.policy,
            "reason": recommendation.reasoning,
            "confidence": recommendation.confidence,
            "stats": stats,
        }

    return policy_map


def run_full_phase4b_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Run full Phase 4B pipeline: expectancy analysis + policy generation + summary.

    Args:
        trades: List of enriched trade records.

    Returns:
        Complete report dict:
            - expectancy_analysis: output of run_expectancy_analysis()
            - policy_map: output of generate_policy_map()
            - summary: best/worst cohort, highest/lowest expectancy
    """
    expectancy_analysis = run_expectancy_analysis(trades)
    raw_map = expectancy_analysis["raw_map"]
    policy_map = generate_policy_map(raw_map)

    # Build summary
    best_cohort: CohortKey | None = None
    worst_cohort: CohortKey | None = None
    highest_expectancy: float = float("-inf")
    lowest_expectancy: float = float("inf")

    for key, stats in raw_map.items():
        if stats.trade_count < 3:
            continue  # Skip insufficient data
        if stats.expectancy > highest_expectancy:
            highest_expectancy = stats.expectancy
            best_cohort = key
        if stats.expectancy < lowest_expectancy:
            lowest_expectancy = stats.expectancy
            worst_cohort = key

    # Handle empty case
    if best_cohort is None:
        highest_expectancy = 0.0
    if worst_cohort is None:
        lowest_expectancy = 0.0

    # Most stable cohort (lowest variance with sufficient data)
    most_stable: CohortKey | None = None
    lowest_variance: float = float("inf")
    for key, stats in raw_map.items():
        if stats.trade_count < 3:
            continue
        if stats.variance < lowest_variance:
            lowest_variance = stats.variance
            most_stable = key

    # Highest edge cohort (best expectancy with sufficient sample ≥5 trades)
    highest_edge: CohortKey | None = None
    highest_edge_expectancy: float = float("-inf")
    min_sample = 5
    for key, stats in raw_map.items():
        if stats.trade_count < min_sample:
            continue
        if stats.expectancy > highest_edge_expectancy:
            highest_edge_expectancy = stats.expectancy
            highest_edge = key

    return {
        "expectancy_analysis": expectancy_analysis,
        "policy_map": policy_map,
        "summary": {
            "best_cohort": best_cohort,
            "worst_cohort": worst_cohort,
            "most_stable_cohort": most_stable,
            "highest_edge_cohort": highest_edge,
            "highest_expectancy": round(highest_expectancy, 4) if highest_expectancy != float("-inf") else 0.0,
            "lowest_expectancy": round(lowest_expectancy, 4) if lowest_expectancy != float("inf") else 0.0,
            "lowest_variance": round(lowest_variance, 4) if lowest_variance != float("inf") else 0.0,
            "highest_edge_expectancy": round(highest_edge_expectancy, 4) if highest_edge_expectancy != float("-inf") else 0.0,
            "total_cohorts": len(raw_map),
            "total_trades": len(trades),
        },
    }
