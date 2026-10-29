"""
Regime Drift Detector — Detects whether market regime breakdown is causing performance change.

Compares expected regime performance (from baseline) vs current regime performance
to identify structural shifts in edge quality.

NO execution logic. NO parameter tuning. PURE diagnostic layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.drift.drift_baseline import CohortBaseline
from core.drift.current_snapshot import CurrentCohortState


# ─── REPORT TYPES ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AffectedCohort:
    """A cohort identified as affected by regime drift."""

    cohort_key: str
    baseline_expectancy: float
    current_expectancy: float
    expectancy_delta: float
    pattern: str  # e.g. "STRONG_EARLY_DEGRADATION", "LATE_ENTRY_BREAKDOWN", "RANGE_MISCLASSIFICATION"


@dataclass(frozen=True)
class RegimeDriftReport:
    """Diagnostic report on regime-driven performance shifts."""

    affected_cohorts: list[AffectedCohort]
    regime_shift_detected: bool
    likely_cause: str


# ─── DETECTION THRESHOLDS ─────────────────────────────────────────────────────

_DEGRADATION_THRESHOLD = -0.20      # Expectancy drop to flag a cohort
_STRONG_EARLY_EXP_MIN = 0.5        # Baseline expectancy floor for STRONG+EARLY
_LATE_BREAKDOWN_THRESHOLD = -0.30   # Severe drop for LATE entries
_RANGE_VARIANCE_MULTIPLIER = 2.0   # Variance doubling in RANGING = misclassification signal


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def detect_regime_drift(
    baselines: dict[str, CohortBaseline],
    current_states: dict[str, CurrentCohortState],
) -> RegimeDriftReport:
    """
    Detect whether regime breakdown is causing performance degradation.

    Identifies three patterns:
    1. STRONG+EARLY degradation — momentum edge eroding (regime may have shifted)
    2. LATE entry breakdown — late entries failing harder than expected
    3. RANGE misclassification — RANGING cohorts with exploding variance

    Args:
        baselines: Historical baseline per cohort.
        current_states: Current rolling snapshot per cohort.

    Returns:
        RegimeDriftReport with affected cohorts, detection flag, and likely cause.
    """
    affected: list[AffectedCohort] = []

    # Only compare cohorts present in both baseline and current
    common_keys = set(baselines.keys()) & set(current_states.keys())

    for key in common_keys:
        baseline = baselines[key]
        current = current_states[key]

        delta = current.current_expectancy - baseline.baseline_expectancy

        # Pattern 1: STRONG+EARLY degradation
        pattern = _check_strong_early_degradation(key, baseline, current, delta)
        if pattern:
            affected.append(pattern)
            continue

        # Pattern 2: LATE entry breakdown
        pattern = _check_late_entry_breakdown(key, baseline, current, delta)
        if pattern:
            affected.append(pattern)
            continue

        # Pattern 3: RANGE misclassification
        pattern = _check_range_misclassification(key, baseline, current, delta)
        if pattern:
            affected.append(pattern)
            continue

        # General degradation (not pattern-specific)
        if delta < _DEGRADATION_THRESHOLD:
            affected.append(AffectedCohort(
                cohort_key=key,
                baseline_expectancy=baseline.baseline_expectancy,
                current_expectancy=current.current_expectancy,
                expectancy_delta=round(delta, 4),
                pattern="GENERAL_DEGRADATION",
            ))

    regime_shift = len(affected) > 0
    cause = _determine_likely_cause(affected)

    return RegimeDriftReport(
        affected_cohorts=affected,
        regime_shift_detected=regime_shift,
        likely_cause=cause,
    )


# ─── PATTERN DETECTORS ────────────────────────────────────────────────────────

def _check_strong_early_degradation(
    key: str,
    baseline: CohortBaseline,
    current: CurrentCohortState,
    delta: float,
) -> AffectedCohort | None:
    """
    Detect STRONG+EARLY cohort degradation.

    This is the primary momentum edge — if it degrades, the regime
    likely shifted away from trending conditions.
    """
    parts = key.upper().split("+")
    if len(parts) < 2:
        return None

    strength, timing = parts[0], parts[1]

    if strength != "STRONG" or timing != "EARLY":
        return None

    # Only flag if baseline was genuinely strong and current has degraded
    if baseline.baseline_expectancy < _STRONG_EARLY_EXP_MIN:
        return None

    if delta < _DEGRADATION_THRESHOLD:
        return AffectedCohort(
            cohort_key=key,
            baseline_expectancy=baseline.baseline_expectancy,
            current_expectancy=current.current_expectancy,
            expectancy_delta=round(delta, 4),
            pattern="STRONG_EARLY_DEGRADATION",
        )

    return None


def _check_late_entry_breakdown(
    key: str,
    baseline: CohortBaseline,
    current: CurrentCohortState,
    delta: float,
) -> AffectedCohort | None:
    """
    Detect LATE entry breakdown.

    LATE entries are inherently fragile. If they degrade severely,
    market structure has likely changed (exhaustion moves failing).
    """
    parts = key.upper().split("+")
    if len(parts) < 2:
        return None

    timing = parts[1] if len(parts) > 1 else ""

    if timing != "LATE":
        return None

    if delta < _LATE_BREAKDOWN_THRESHOLD:
        return AffectedCohort(
            cohort_key=key,
            baseline_expectancy=baseline.baseline_expectancy,
            current_expectancy=current.current_expectancy,
            expectancy_delta=round(delta, 4),
            pattern="LATE_ENTRY_BREAKDOWN",
        )

    return None


def _check_range_misclassification(
    key: str,
    baseline: CohortBaseline,
    current: CurrentCohortState,
    delta: float,
) -> AffectedCohort | None:
    """
    Detect RANGING regime misclassification.

    If RANGING cohort variance has doubled, the regime filter may be
    miscategorizing trending conditions as ranging (or vice versa).
    """
    parts = key.upper().split("+")
    regime = parts[2] if len(parts) > 2 else ""

    if regime != "RANGING":
        return None

    # Variance explosion = regime misclassification signal
    if baseline.baseline_variance > 0:
        variance_ratio = current.current_variance / baseline.baseline_variance
    else:
        variance_ratio = current.current_variance

    if variance_ratio >= _RANGE_VARIANCE_MULTIPLIER and delta < 0:
        return AffectedCohort(
            cohort_key=key,
            baseline_expectancy=baseline.baseline_expectancy,
            current_expectancy=current.current_expectancy,
            expectancy_delta=round(delta, 4),
            pattern="RANGE_MISCLASSIFICATION",
        )

    return None


# ─── CAUSE INFERENCE ──────────────────────────────────────────────────────────

def _determine_likely_cause(affected: list[AffectedCohort]) -> str:
    """Infer the most likely structural cause from affected patterns."""
    if not affected:
        return "NO_DRIFT_DETECTED"

    patterns = [a.pattern for a in affected]

    # Count pattern occurrences
    strong_early = patterns.count("STRONG_EARLY_DEGRADATION")
    late_breakdown = patterns.count("LATE_ENTRY_BREAKDOWN")
    range_misclass = patterns.count("RANGE_MISCLASSIFICATION")
    general = patterns.count("GENERAL_DEGRADATION")

    # Prioritized cause inference
    if strong_early > 0 and range_misclass > 0:
        return "REGIME_TRANSITION: Market likely shifted from trending to ranging. Both momentum edge and range classification affected."

    if strong_early > 0:
        return "MOMENTUM_EDGE_EROSION: STRONG+EARLY cohort degrading suggests trending conditions weakening or regime filter lagging."

    if range_misclass > 0:
        return "REGIME_MISCLASSIFICATION: RANGING cohort variance explosion suggests regime filter is miscategorizing market conditions."

    if late_breakdown > 0:
        return "EXHAUSTION_FAILURE: LATE entry breakdown suggests market structure changes — exhaustion moves no longer following through."

    if general > 0:
        return "BROAD_DEGRADATION: Multiple cohorts degrading without clear pattern. Possible macro regime shift or strategy decay."

    return "UNCLASSIFIED_DRIFT"
