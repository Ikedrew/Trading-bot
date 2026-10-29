"""
Strategy Research Queries — High-level query interface for strategy evidence.

Provides research-oriented queries over the evidence store.
Designed for use by research experiments (M9/M10/M11) and the
Research Command Centre.

This is RESEARCH ONLY. No execution logic. No decision pipeline imports.
"""

from __future__ import annotations

from typing import Any

from core.strategies.evidence_store import StrategyEvidenceRecord, StrategyEvidenceStore


def get_strategy_history(
    store: StrategyEvidenceStore,
    strategy_id: str,
) -> dict[str, Any]:
    """
    Get complete history for a strategy: all observations + outcomes.

    Args:
        store: The evidence store to query.
        strategy_id: Strategy to retrieve history for.

    Returns:
        Dict with observations, outcomes, and timeline.
    """
    records = store.get_records_for_strategy(strategy_id)
    resolved = [r for r in records if r.is_resolved]
    pending = [r for r in records if not r.has_outcome]

    return {
        "strategy_id": strategy_id,
        "total_observations": len(records),
        "resolved": len(resolved),
        "pending": len(pending),
        "timeline": [
            {
                "evidence_id": r.evidence_id,
                "timestamp": r.created_at,
                "phase": r.market_phase,
                "regime": r.regime,
                "conditions_met": r.conditions_met,
                "confidence": r.confidence,
                "overall_status": r.overall_status,
                "outcome": r.outcome_status,
                "realised_r": r.realised_r,
            }
            for r in records
        ],
    }


def get_strategy_statistics(
    store: StrategyEvidenceStore,
    strategy_id: str,
) -> dict[str, Any]:
    """
    Get performance statistics for a strategy.

    Returns sample size, win rate, average R, expectancy, confidence.
    """
    return store.get_strategy_statistics(strategy_id)


def get_family_statistics(
    store: StrategyEvidenceStore,
    family: str,
) -> dict[str, Any]:
    """
    Get performance statistics for an entire strategy family.

    Aggregates all strategies within the family.
    """
    return store.get_family_statistics(family)


def get_phase_strategy_performance(
    store: StrategyEvidenceStore,
) -> dict[str, dict[str, Any]]:
    """
    Get performance grouped by market phase × strategy.

    Returns nested dict: {phase: {strategy_id: stats}}

    Example:
        {
            "REVERSAL": {
                "range_reversal_v1": {
                    "sample_size": 120,
                    "win_rate": 0.58,
                    "average_r": 0.42,
                    ...
                }
            }
        }
    """
    return store.get_phase_strategy_performance()


def get_strategy_evidence_summary(
    store: StrategyEvidenceStore,
) -> dict[str, Any]:
    """
    Get a high-level summary of all evidence collected.

    Useful for Research Command Centre integration.
    """
    all_records = store.get_all_records()
    resolved = store.get_resolved_records()

    # Group by strategy
    by_strategy: dict[str, list[StrategyEvidenceRecord]] = {}
    for r in all_records:
        if r.strategy_id not in by_strategy:
            by_strategy[r.strategy_id] = []
        by_strategy[r.strategy_id].append(r)

    # Group by family
    by_family: dict[str, list[StrategyEvidenceRecord]] = {}
    for r in resolved:
        if r.family not in by_family:
            by_family[r.family] = []
        by_family[r.family].append(r)

    return {
        "total_observations": len(all_records),
        "total_resolved": len(resolved),
        "total_pending": len(all_records) - len(resolved),
        "strategies_observed": len(by_strategy),
        "families_with_evidence": len(by_family),
        "per_strategy": {
            sid: {
                "observations": len(records),
                "resolved": sum(1 for r in records if r.is_resolved),
                "wins": sum(1 for r in records if r.is_win),
                "losses": sum(1 for r in records if r.is_loss),
            }
            for sid, records in by_strategy.items()
        },
        "per_family": {
            fam: {
                "sample_size": len(records),
                "win_rate": round(
                    sum(1 for r in records if r.is_win) / len(records), 4
                ) if records else 0.0,
                "average_r": round(
                    sum(r.realised_r for r in records) / len(records), 4
                ) if records else 0.0,
            }
            for fam, records in by_family.items()
        },
    }


def get_condition_effectiveness(
    store: StrategyEvidenceStore,
    strategy_id: str,
) -> dict[str, Any]:
    """
    Analyse whether condition confidence correlates with outcome.

    Groups resolved records by confidence level and compares outcomes.
    """
    resolved = store.get_records_for_context(
        strategy_id=strategy_id, resolved_only=True
    )

    if not resolved:
        return {
            "strategy_id": strategy_id,
            "sample_size": 0,
            "high_confidence": {"n": 0, "win_rate": 0.0, "avg_r": 0.0},
            "low_confidence": {"n": 0, "win_rate": 0.0, "avg_r": 0.0},
            "conclusion": "INSUFFICIENT_DATA",
        }

    high_conf = [r for r in resolved if r.confidence >= 0.7]
    low_conf = [r for r in resolved if r.confidence < 0.7]

    def _bucket_stats(records: list[StrategyEvidenceRecord]) -> dict[str, Any]:
        n = len(records)
        if n == 0:
            return {"n": 0, "win_rate": 0.0, "avg_r": 0.0}
        wins = sum(1 for r in records if r.is_win)
        avg_r = sum(r.realised_r for r in records) / n
        return {"n": n, "win_rate": round(wins / n, 4), "avg_r": round(avg_r, 4)}

    high_stats = _bucket_stats(high_conf)
    low_stats = _bucket_stats(low_conf)

    # Determine if confidence is predictive
    if high_stats["n"] >= 20 and low_stats["n"] >= 20:
        if high_stats["avg_r"] > low_stats["avg_r"]:
            conclusion = "CONFIDENCE_PREDICTIVE"
        elif high_stats["avg_r"] < low_stats["avg_r"]:
            conclusion = "CONFIDENCE_INVERSELY_PREDICTIVE"
        else:
            conclusion = "CONFIDENCE_NOT_PREDICTIVE"
    else:
        conclusion = "INSUFFICIENT_DATA"

    return {
        "strategy_id": strategy_id,
        "sample_size": len(resolved),
        "high_confidence": high_stats,
        "low_confidence": low_stats,
        "conclusion": conclusion,
    }
