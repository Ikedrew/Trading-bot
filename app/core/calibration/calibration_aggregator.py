"""
Calibration Aggregator — Aggregates cohort recommendations into global parameter proposal.

NO direct config mutation. ONLY produces a GlobalCalibrationPlan proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.calibration.calibration_engine import CalibrationRecommendation


@dataclass(frozen=True)
class GlobalCalibrationPlan:
    """Aggregated parameter proposal from all approved cohort recommendations."""

    final_break_even_rr: float
    final_trailing_start_rr: float
    final_trailing_step: float
    final_partial_tp_state: bool
    applied_cohorts: list[str]


def aggregate_recommendations(
    recommendations: list[CalibrationRecommendation],
) -> GlobalCalibrationPlan:
    """
    Compute weighted-average global parameters from approved recommendations.

    Weights combine:
      - expectancy (higher = more influence)
      - sample_size (larger = more stable)
      - confidence_score (higher = more trusted)

    Args:
        recommendations: List of approved CalibrationRecommendation objects.

    Returns:
        GlobalCalibrationPlan with weighted averages across all cohorts.
    """
    if not recommendations:
        return GlobalCalibrationPlan(
            final_break_even_rr=1.0,
            final_trailing_start_rr=1.5,
            final_trailing_step=0.0005,
            final_partial_tp_state=False,
            applied_cohorts=[],
        )

    total_weight = 0.0
    weighted_be = 0.0
    weighted_trail_start = 0.0
    weighted_trail_step = 0.0
    partial_votes_for = 0
    partial_votes_against = 0
    applied: list[str] = []

    for rec in recommendations:
        w = _compute_weight(rec)
        total_weight += w

        weighted_be += rec.recommended_break_even_rr * w
        weighted_trail_start += rec.recommended_trailing_start_rr * w
        weighted_trail_step += rec.recommended_trailing_step * w

        if rec.recommended_partial_tp:
            partial_votes_for += 1
        else:
            partial_votes_against += 1

        applied.append(rec.cohort_key)

    if total_weight <= 0:
        total_weight = 1.0

    return GlobalCalibrationPlan(
        final_break_even_rr=round(weighted_be / total_weight, 4),
        final_trailing_start_rr=round(weighted_trail_start / total_weight, 4),
        final_trailing_step=round(weighted_trail_step / total_weight, 6),
        final_partial_tp_state=partial_votes_for > partial_votes_against,
        applied_cohorts=applied,
    )


def _compute_weight(rec: CalibrationRecommendation) -> float:
    """Compute composite weight from expectancy, sample size, and confidence."""
    exp_w = max(0.1, rec.expectancy) if rec.expectancy > 0 else 0.1
    size_w = min(1.0, rec.sample_size / 50.0)
    conf_w = rec.confidence_score

    return round(exp_w * size_w * conf_w, 6)
