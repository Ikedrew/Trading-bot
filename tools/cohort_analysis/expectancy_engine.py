"""
Expectancy Calculation Engine — Groups trades by cohort and computes statistics.

STRICTLY OFFLINE — never imported by runtime code.
Pure analysis: takes enriched trade records, produces CohortKey → CohortStats mapping.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.cohort_analysis.expectancy_model import CohortKey, CohortStats, ExpectancyResult
from tools.cohort_analysis.cohort_builder import build_cohort_from_trade


def _extract_cohort_key(record: dict[str, Any]) -> CohortKey:
    """Extract CohortKey from an enriched trade record. Delegates to single source of truth."""
    return build_cohort_from_trade(record)


def _outcome_r(record: dict[str, Any]) -> float | None:
    """Extract outcome in R from a trade record. Returns None if unavailable."""
    rr = record.get("outcome_rr")
    if rr is not None:
        win = record.get("outcome_win")
        if win is False and rr > 0:
            return -rr
        return float(rr)

    # Fallback: approximate from win/loss
    win = record.get("outcome_win")
    if win is True:
        return 2.0  # Assume 2R winner (BASE_RR)
    elif win is False:
        return -1.0  # Assume 1R loser
    return None


def group_by_cohort(trades: list[dict[str, Any]]) -> dict[CohortKey, list[dict[str, Any]]]:
    """
    Group enriched trade records by their CohortKey.

    Args:
        trades: List of enriched trade records with confirmation, entry_timing, engine_state.

    Returns:
        Mapping of CohortKey → list of trade records in that cohort.
    """
    groups: dict[CohortKey, list[dict[str, Any]]] = defaultdict(list)

    for record in trades:
        key = _extract_cohort_key(record)
        groups[key].append(record)

    return dict(groups)


def compute_stats(cohort_trades: list[dict[str, Any]]) -> CohortStats:
    """
    Compute performance statistics for a list of trades in a single cohort.

    Args:
        cohort_trades: Trade records belonging to one cohort.

    Returns:
        CohortStats with win_rate, avg_rr, expectancy, variance, trade_count, mfe_mean, mae_mean.
    """
    outcomes: list[float] = []
    wins = 0
    losses = 0
    mfe_values: list[float] = []
    mae_values: list[float] = []

    for record in cohort_trades:
        r = _outcome_r(record)
        if r is not None:
            outcomes.append(r)
            if r > 0:
                wins += 1
            else:
                losses += 1

        mfe = record.get("mfe_r")
        if mfe is not None:
            mfe_values.append(float(mfe))

        mae = record.get("mae_r")
        if mae is not None:
            mae_values.append(float(mae))

    trade_count = len(cohort_trades)

    if not outcomes:
        return CohortStats(
            win_rate=0.0,
            avg_rr=0.0,
            expectancy=0.0,
            variance=0.0,
            trade_count=trade_count,
            mfe_mean=0.0,
            mae_mean=0.0,
        )

    total_r = sum(outcomes)
    avg_rr = total_r / len(outcomes)
    win_rate = wins / len(outcomes) if outcomes else 0.0

    # Variance
    mean = total_r / len(outcomes)
    variance = sum((x - mean) ** 2 for x in outcomes) / len(outcomes) if len(outcomes) > 1 else 0.0

    # MFE / MAE means
    mfe_mean = sum(mfe_values) / len(mfe_values) if mfe_values else 0.0
    mae_mean = sum(mae_values) / len(mae_values) if mae_values else 0.0

    return CohortStats(
        win_rate=round(win_rate, 4),
        avg_rr=round(avg_rr, 4),
        expectancy=round(avg_rr, 4),
        variance=round(variance, 4),
        trade_count=trade_count,
        mfe_mean=round(mfe_mean, 4),
        mae_mean=round(mae_mean, 4),
    )


def build_expectancy_map(trades: list[dict[str, Any]]) -> dict[CohortKey, CohortStats]:
    """
    Build complete expectancy map from enriched trades.

    Groups trades by cohort, computes stats for each, returns full mapping.

    Args:
        trades: List of enriched trade records.

    Returns:
        Mapping of CohortKey → CohortStats for every observed cohort.
    """
    groups = group_by_cohort(trades)
    return {key: compute_stats(group) for key, group in groups.items()}
