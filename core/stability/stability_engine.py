"""
Stability Engine — Converts drift signals into system-wide stability state.

NO trade execution changes. ONLY state classification.
Implements hysteresis: system cannot jump from PROTECTED → HEALTHY directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.drift.drift_classifier import DriftStatus
from core.drift.drift_scoring import DriftMetrics
from core.stability.stability_state import StabilitySnapshot, SystemStabilityState


# ─── THRESHOLDS ───────────────────────────────────────────────────────────────

_VOLATILE_DEGRADED_RATIO = 0.30   # >30% cohorts DEGRADED → VOLATILE
_CONSECUTIVE_WARNING_LIMIT = 3    # 3+ consecutive warning cycles → DEGRADED


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def evaluate_system_stability(
    drift_results: list[tuple[DriftMetrics, DriftStatus]],
    previous_snapshot: StabilitySnapshot | None = None,
) -> StabilitySnapshot:
    """
    Evaluate overall system stability from drift classification results.

    Rules (evaluated in priority order):
    1. ANY BROKEN_EDGE + negative expectancy → PROTECTED_MODE
    2. >30% cohorts DEGRADED or worse → VOLATILE
    3. 3+ consecutive degradation cycles → DEGRADED
    4. Improving drift trend from PROTECTED/DEGRADED → RECOVERY_MODE
    5. Otherwise → HEALTHY

    Hysteresis:
    - Cannot jump from PROTECTED_MODE → HEALTHY directly
    - Must pass through RECOVERY_MODE first

    Args:
        drift_results: List of (DriftMetrics, DriftStatus) per cohort.
        previous_snapshot: Previous StabilitySnapshot for trend tracking.

    Returns:
        New StabilitySnapshot reflecting current system state.
    """
    if not drift_results:
        return _empty_snapshot(previous_snapshot)

    # Build cohort state map
    cohort_states: dict[str, DriftStatus] = {}
    for metrics, status in drift_results:
        cohort_states[metrics.cohort_key] = status

    # Find worst cohort
    worst_key, worst_status = _find_worst(drift_results)

    # Compute aggregate system drift score
    system_drift = _compute_system_drift(drift_results)

    # Count status categories
    total = len(drift_results)
    broken_count = sum(1 for _, s in drift_results if s == DriftStatus.BROKEN_EDGE)
    degraded_count = sum(1 for _, s in drift_results if s == DriftStatus.DEGRADED)
    warning_count = sum(1 for _, s in drift_results if s == DriftStatus.WARNING)
    degraded_or_worse = broken_count + degraded_count

    # Check for negative expectancy in broken cohorts
    has_broken_negative = any(
        m.expectancy_drift < 0 and s == DriftStatus.BROKEN_EDGE
        for m, s in drift_results
    )

    # Track consecutive degradation cycles
    prev_cycles = previous_snapshot.consecutive_degradation_cycles if previous_snapshot else 0
    prev_state = previous_snapshot.global_state if previous_snapshot else None
    prev_stable_ts = previous_snapshot.last_stable_timestamp if previous_snapshot else ""

    # Determine if currently degrading
    currently_degrading = degraded_or_worse > 0 or warning_count >= _CONSECUTIVE_WARNING_LIMIT
    consecutive_cycles = (prev_cycles + 1) if currently_degrading else 0

    # ─── CLASSIFICATION (priority order) ──────────────────────────────

    # Rule 1: BROKEN_EDGE + negative expectancy → PROTECTED_MODE
    if has_broken_negative:
        new_state = SystemStabilityState.PROTECTED_MODE

    # Rule 2: >30% cohorts DEGRADED or worse → VOLATILE
    elif total > 0 and (degraded_or_worse / total) > _VOLATILE_DEGRADED_RATIO:
        new_state = SystemStabilityState.VOLATILE

    # Rule 3: 3+ consecutive warning/degradation cycles → DEGRADED
    elif consecutive_cycles >= _CONSECUTIVE_WARNING_LIMIT:
        new_state = SystemStabilityState.DEGRADED

    # Rule 4: Improving from PROTECTED/DEGRADED → RECOVERY_MODE
    elif _is_recovering(prev_state, degraded_or_worse, system_drift, previous_snapshot):
        new_state = SystemStabilityState.RECOVERY_MODE

    # Default: HEALTHY
    else:
        new_state = SystemStabilityState.HEALTHY

    # ─── HYSTERESIS: prevent PROTECTED → HEALTHY jump ─────────────────
    new_state = _apply_hysteresis(new_state, prev_state)

    # Update last stable timestamp
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if new_state == SystemStabilityState.HEALTHY:
        last_stable = now
    else:
        last_stable = prev_stable_ts or now

    return StabilitySnapshot(
        global_state=new_state,
        cohort_states=cohort_states,
        worst_cohort=worst_key,
        system_drift_score=round(system_drift, 4),
        consecutive_degradation_cycles=consecutive_cycles,
        last_stable_timestamp=last_stable,
    )


# ─── INTERNAL HELPERS ─────────────────────────────────────────────────────────

def _empty_snapshot(previous: StabilitySnapshot | None) -> StabilitySnapshot:
    """Return a healthy snapshot when no drift data is available."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return StabilitySnapshot(
        global_state=SystemStabilityState.HEALTHY,
        cohort_states={},
        worst_cohort="",
        system_drift_score=0.0,
        consecutive_degradation_cycles=0,
        last_stable_timestamp=now,
    )


def _find_worst(
    drift_results: list[tuple[DriftMetrics, DriftStatus]],
) -> tuple[str, DriftStatus]:
    """Find the cohort with the worst drift status and highest score."""
    # Priority: BROKEN_EDGE > DEGRADED > WARNING > STABLE
    status_priority = {
        DriftStatus.BROKEN_EDGE: 4,
        DriftStatus.DEGRADED: 3,
        DriftStatus.WARNING: 2,
        DriftStatus.STABLE: 1,
    }

    worst = max(
        drift_results,
        key=lambda x: (status_priority.get(x[1], 0), x[0].composite_drift_score),
    )
    return worst[0].cohort_key, worst[1]


def _compute_system_drift(
    drift_results: list[tuple[DriftMetrics, DriftStatus]],
) -> float:
    """Compute aggregate system drift as weighted average of cohort drift scores."""
    if not drift_results:
        return 0.0

    total_score = sum(m.composite_drift_score for m, _ in drift_results)
    return total_score / len(drift_results)


def _is_recovering(
    prev_state: SystemStabilityState | None,
    degraded_or_worse: int,
    system_drift: float,
    previous_snapshot: StabilitySnapshot | None,
) -> bool:
    """
    Detect if system is recovering from a degraded/protected state.

    Recovery = previously in PROTECTED/DEGRADED/VOLATILE, now showing improvement.
    """
    if prev_state is None:
        return False

    was_bad = prev_state in (
        SystemStabilityState.PROTECTED_MODE,
        SystemStabilityState.DEGRADED,
        SystemStabilityState.VOLATILE,
    )

    if not was_bad:
        return False

    # Currently no broken edges and drift improving
    prev_drift = previous_snapshot.system_drift_score if previous_snapshot else 1.0
    improving = system_drift < prev_drift and degraded_or_worse == 0

    return improving


def _apply_hysteresis(
    new_state: SystemStabilityState,
    prev_state: SystemStabilityState | None,
) -> SystemStabilityState:
    """
    Apply hysteresis rules to prevent abrupt state transitions.

    Rules:
    - Cannot jump from PROTECTED_MODE → HEALTHY directly (must pass RECOVERY_MODE)
    - Cannot jump from DEGRADED → HEALTHY directly (must pass RECOVERY_MODE)
    """
    if prev_state is None:
        return new_state

    # Block PROTECTED → HEALTHY (must go through RECOVERY)
    if prev_state == SystemStabilityState.PROTECTED_MODE and new_state == SystemStabilityState.HEALTHY:
        return SystemStabilityState.RECOVERY_MODE

    # Block DEGRADED → HEALTHY (must go through RECOVERY)
    if prev_state == SystemStabilityState.DEGRADED and new_state == SystemStabilityState.HEALTHY:
        return SystemStabilityState.RECOVERY_MODE

    return new_state
