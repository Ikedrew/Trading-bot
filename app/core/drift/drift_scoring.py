"""
Drift Scoring — Quantifies deviation between baseline and current performance.

PURE math layer. NO execution impact. NO live system modification.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.drift.drift_baseline import CohortBaseline
from core.drift.current_snapshot import CurrentCohortState


# ─── DRIFT DIRECTION ──────────────────────────────────────────────────────────

IMPROVING = "improving"
DEGRADING = "degrading"
STABLE = "stable"


# ─── DRIFT METRICS TYPE ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class DriftMetrics:
    """Quantified deviation between baseline and current cohort performance."""

    cohort_key: str
    expectancy_drift: float       # current - baseline (positive = improving)
    winrate_drift: float          # current - baseline (positive = improving)
    variance_drift: float         # abs(current - baseline) (always >= 0)
    composite_drift_score: float  # 0.0–1.0 (higher = more drift)
    direction: str                # "improving" | "degrading" | "stable"


# ─── COMPOSITE WEIGHTS ────────────────────────────────────────────────────────

_WEIGHT_EXPECTANCY = 0.50
_WEIGHT_WINRATE = 0.30
_WEIGHT_VARIANCE = 0.20

# Stability thresholds
_STABLE_THRESHOLD = 0.10   # Composite below this = stable
_IMPROVING_EXPECTANCY_MIN = 0.0  # Net positive expectancy drift = improving


# ─── DRIFT CALCULATION ────────────────────────────────────────────────────────

def calculate_drift(
    baseline: CohortBaseline,
    current: CurrentCohortState,
) -> DriftMetrics:
    """
    Calculate drift between historical baseline and current rolling state.

    Composite drift score:
        expectancy component (50%) + winrate component (30%) + variance component (20%)

    Each component is normalized to 0–1 range before weighting.

    Direction:
        - improving: expectancy drift > 0 AND composite < moderate
        - degrading: expectancy drift < 0 OR variance exploding
        - stable: composite drift score below threshold

    Args:
        baseline: CohortBaseline (historical reference).
        current: CurrentCohortState (recent rolling window).

    Returns:
        DriftMetrics with all deviation measurements.
    """
    # Raw drifts
    expectancy_drift = current.current_expectancy - baseline.baseline_expectancy
    winrate_drift = current.current_win_rate - baseline.baseline_win_rate
    variance_drift = abs(current.current_variance - baseline.baseline_variance)

    # Normalize components to 0–1 for composite scoring
    exp_component = _normalize_expectancy_drift(expectancy_drift, baseline.baseline_expectancy)
    wr_component = _normalize_winrate_drift(winrate_drift)
    var_component = _normalize_variance_drift(variance_drift, baseline.baseline_variance)

    # Weighted composite
    composite = (
        _WEIGHT_EXPECTANCY * exp_component
        + _WEIGHT_WINRATE * wr_component
        + _WEIGHT_VARIANCE * var_component
    )
    composite = round(min(1.0, max(0.0, composite)), 4)

    # Direction classification
    direction = _classify_direction(expectancy_drift, winrate_drift, composite)

    return DriftMetrics(
        cohort_key=baseline.cohort_key,
        expectancy_drift=round(expectancy_drift, 4),
        winrate_drift=round(winrate_drift, 4),
        variance_drift=round(variance_drift, 4),
        composite_drift_score=composite,
        direction=direction,
    )


# ─── NORMALIZATION HELPERS ────────────────────────────────────────────────────

def _normalize_expectancy_drift(drift: float, baseline_exp: float) -> float:
    """
    Normalize expectancy drift to 0–1.

    Uses absolute drift relative to baseline magnitude.
    A drift of ±1R from a 0.5R baseline = very significant (1.0).
    """
    if baseline_exp == 0:
        return min(1.0, abs(drift))
    return min(1.0, abs(drift) / max(0.1, abs(baseline_exp)))


def _normalize_winrate_drift(drift: float) -> float:
    """
    Normalize winrate drift to 0–1.

    Winrate is already 0–1, so absolute drift maps directly.
    A 20% winrate shift = 0.2 component (significant).
    """
    return min(1.0, abs(drift) * 2.0)  # Scale: 50% shift = max


def _normalize_variance_drift(drift: float, baseline_var: float) -> float:
    """
    Normalize variance drift to 0–1.

    Variance doubling = high concern (1.0).
    """
    if baseline_var == 0:
        return min(1.0, drift)
    return min(1.0, drift / max(0.1, baseline_var))


def _classify_direction(
    expectancy_drift: float,
    winrate_drift: float,
    composite: float,
) -> str:
    """Classify drift direction based on metrics."""
    if composite < _STABLE_THRESHOLD:
        return STABLE

    if expectancy_drift > 0 and winrate_drift >= -0.05:
        return IMPROVING

    return DEGRADING
