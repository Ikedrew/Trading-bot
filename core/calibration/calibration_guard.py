"""
Calibration Guard — Prevents overfitting and unstable parameter drift.

PURE validation layer. NO trading logic.
Approves or rejects CalibrationRecommendation based on safety bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.calibration.calibration_engine import CalibrationRecommendation


# ─── GUARD THRESHOLDS ─────────────────────────────────────────────────────────

_MAX_BE_CHANGE = 0.25            # ±0.25 RR max per run
_MAX_TRAIL_START_CHANGE = 0.25   # ±0.25 RR max per run
_MAX_TRAIL_STEP_CHANGE_PCT = 0.20  # ±20% max change
_MIN_SAMPLE_SIZE = 30            # Ignore cohort below this
_MIN_CONFIDENCE = 0.7            # Only apply if confidence > this
_MAX_VARIANCE = 4.0              # Reject if variance exceeds this


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuardResult:
    """Approval or rejection of a calibration recommendation."""

    approved: bool
    recommendation: CalibrationRecommendation
    rejections: list[str]  # Empty if approved


# ─── GUARD LOGIC ──────────────────────────────────────────────────────────────

def evaluate_recommendation(rec: CalibrationRecommendation) -> GuardResult:
    """
    Evaluate whether a calibration recommendation is safe to apply.

    Checks:
    1. Sample size >= 30
    2. Confidence score > 0.7
    3. Variance <= threshold (regime stability)
    4. BE change within ±0.25
    5. Trail start change within ±0.25
    6. Trail step change within ±20%

    Args:
        rec: CalibrationRecommendation to evaluate.

    Returns:
        GuardResult with approved=True if all checks pass,
        or approved=False with list of rejection reasons.
    """
    rejections: list[str] = []

    # Check 1: Minimum sample size
    if rec.sample_size < _MIN_SAMPLE_SIZE:
        rejections.append(
            f"INSUFFICIENT_SAMPLE: {rec.sample_size} trades < {_MIN_SAMPLE_SIZE} minimum"
        )

    # Check 2: Confidence threshold
    if rec.confidence_score < _MIN_CONFIDENCE:
        rejections.append(
            f"LOW_CONFIDENCE: {rec.confidence_score:.3f} < {_MIN_CONFIDENCE} threshold"
        )

    # Check 3: Variance stability
    if rec.variance > _MAX_VARIANCE:
        rejections.append(
            f"UNSTABLE_VARIANCE: {rec.variance:.3f} > {_MAX_VARIANCE} threshold"
        )

    # Check 4: BE change magnitude
    be_delta = abs(rec.recommended_break_even_rr - rec.break_even_trigger_rr)
    if be_delta > _MAX_BE_CHANGE:
        rejections.append(
            f"EXCESSIVE_BE_CHANGE: Δ{be_delta:.3f} > ±{_MAX_BE_CHANGE} limit "
            f"(current={rec.break_even_trigger_rr}, recommended={rec.recommended_break_even_rr})"
        )

    # Check 5: Trail start change magnitude
    trail_delta = abs(rec.recommended_trailing_start_rr - rec.trailing_start_rr)
    if trail_delta > _MAX_TRAIL_START_CHANGE:
        rejections.append(
            f"EXCESSIVE_TRAIL_CHANGE: Δ{trail_delta:.3f} > ±{_MAX_TRAIL_START_CHANGE} limit "
            f"(current={rec.trailing_start_rr}, recommended={rec.recommended_trailing_start_rr})"
        )

    # Check 6: Trail step percentage change
    if rec.trailing_step > 0:
        step_pct_change = abs(rec.recommended_trailing_step - rec.trailing_step) / rec.trailing_step
        if step_pct_change > _MAX_TRAIL_STEP_CHANGE_PCT:
            rejections.append(
                f"EXCESSIVE_STEP_CHANGE: {step_pct_change:.1%} > ±{_MAX_TRAIL_STEP_CHANGE_PCT:.0%} limit "
                f"(current={rec.trailing_step}, recommended={rec.recommended_trailing_step})"
            )

    return GuardResult(
        approved=len(rejections) == 0,
        recommendation=rec,
        rejections=rejections,
    )


def evaluate_batch(
    recommendations: list[CalibrationRecommendation],
) -> tuple[list[GuardResult], list[GuardResult]]:
    """
    Evaluate a batch of recommendations. Returns (approved, rejected) split.

    Args:
        recommendations: List of CalibrationRecommendation to evaluate.

    Returns:
        Tuple of (approved_list, rejected_list).
    """
    approved: list[GuardResult] = []
    rejected: list[GuardResult] = []

    for rec in recommendations:
        result = evaluate_recommendation(rec)
        if result.approved:
            approved.append(result)
        else:
            rejected.append(result)

    return approved, rejected
