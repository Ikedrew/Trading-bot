"""
Drift Classifier — Classifies system health per cohort based on drift metrics.

NO trading changes. NO parameter changes. PURE classification layer.
"""

from __future__ import annotations

from enum import Enum

from core.drift.drift_scoring import DriftMetrics
from core.drift.drift_baseline import CohortBaseline


# ─── DRIFT STATUS ─────────────────────────────────────────────────────────────

class DriftStatus(str, Enum):
    """Cohort health classification based on drift severity."""

    STABLE = "STABLE"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    BROKEN_EDGE = "BROKEN_EDGE"


# ─── THRESHOLDS ───────────────────────────────────────────────────────────────

_STABLE_MAX = 0.10
_WARNING_MAX = 0.25
_DEGRADED_MAX = 0.50
# Above _DEGRADED_MAX → BROKEN_EDGE


# ─── CLASSIFIER ───────────────────────────────────────────────────────────────

def classify_drift(
    drift_metrics: DriftMetrics,
    baseline: CohortBaseline | None = None,
) -> DriftStatus:
    """
    Classify cohort health from drift metrics.

    Rules:
        composite_drift < 0.10  → STABLE
        0.10 – 0.25            → WARNING
        0.25 – 0.50            → DEGRADED
        > 0.50                 → BROKEN_EDGE

    Special rule:
        If expectancy flips sign (was positive, now negative or vice versa),
        immediately escalate to DEGRADED or BROKEN_EDGE regardless of composite.

    Args:
        drift_metrics: DriftMetrics from calculate_drift().
        baseline: Optional CohortBaseline for sign-flip detection.
                  If provided, enables the expectancy sign-flip rule.

    Returns:
        DriftStatus classification.
    """
    composite = drift_metrics.composite_drift_score

    # Special rule: expectancy sign flip
    if baseline is not None:
        if _expectancy_sign_flipped(baseline.baseline_expectancy, drift_metrics.expectancy_drift):
            # Flip from positive to negative = at least DEGRADED
            if composite > _DEGRADED_MAX:
                return DriftStatus.BROKEN_EDGE
            return DriftStatus.DEGRADED

    # Standard composite thresholds
    if composite <= _STABLE_MAX:
        return DriftStatus.STABLE

    if composite <= _WARNING_MAX:
        return DriftStatus.WARNING

    if composite <= _DEGRADED_MAX:
        return DriftStatus.DEGRADED

    return DriftStatus.BROKEN_EDGE


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _expectancy_sign_flipped(baseline_expectancy: float, expectancy_drift: float) -> bool:
    """
    Detect if expectancy has flipped sign.

    A sign flip means:
        - Baseline was positive, current is negative (edge lost)
        - Baseline was negative, current is positive (edge gained — less critical)

    We only escalate on positive → negative (edge loss).
    """
    current_expectancy = baseline_expectancy + expectancy_drift

    # Only escalate when edge is LOST (positive baseline → negative current)
    if baseline_expectancy > 0 and current_expectancy < 0:
        return True

    return False
