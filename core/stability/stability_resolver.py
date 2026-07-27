"""
Stability Resolver — Converts StabilitySnapshot into actionable policy.

NO execution modification. ONLY decision layer.
PROTECTED_MODE override always wins regardless of other signals.
"""

from __future__ import annotations

from core.drift.drift_classifier import DriftStatus
from core.stability.stability_state import StabilitySnapshot, SystemStabilityState
from core.stability.stability_policy import StabilityPolicy, get_stability_policy


# ─── WORST-COHORT OVERRIDE THRESHOLDS ─────────────────────────────────────────

_BROKEN_EDGE_DRIFT_CRITICAL = 0.7  # If worst cohort drift > this, escalate policy


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def resolve_policy(snapshot: StabilitySnapshot) -> StabilityPolicy:
    """
    Resolve the effective operational policy from a StabilitySnapshot.

    Logic:
    1. Map global_state → base policy
    2. Apply worst_cohort override rules (may escalate)
    3. Enforce PROTECTED_MODE override (always wins)

    Args:
        snapshot: Current StabilitySnapshot.

    Returns:
        StabilityPolicy reflecting the most conservative applicable constraints.
    """
    # Step 1: Base policy from global state
    base_policy = get_stability_policy(snapshot.global_state)

    # Step 2: Check worst-cohort override
    escalated_policy = _apply_worst_cohort_override(snapshot, base_policy)

    # Step 3: PROTECTED_MODE always wins
    final_policy = _enforce_protected_override(snapshot, escalated_policy)

    return final_policy


# ─── OVERRIDE LOGIC ───────────────────────────────────────────────────────────

def _apply_worst_cohort_override(
    snapshot: StabilitySnapshot,
    base_policy: StabilityPolicy,
) -> StabilityPolicy:
    """
    Escalate policy if worst cohort warrants stricter constraints.

    Rules:
    - If worst cohort is BROKEN_EDGE and system drift > critical threshold,
      escalate to at least DEGRADED-level constraints even if global state
      hasn't caught up yet.
    - If multiple cohorts are BROKEN_EDGE, escalate to PROTECTED constraints.
    """
    if not snapshot.cohort_states:
        return base_policy

    # Count broken edges
    broken_count = sum(
        1 for s in snapshot.cohort_states.values()
        if s == DriftStatus.BROKEN_EDGE
    )

    # Multiple broken edges → escalate to PROTECTED
    if broken_count >= 2:
        protected = get_stability_policy(SystemStabilityState.PROTECTED_MODE)
        return _merge_stricter(base_policy, protected)

    # Single broken edge + high system drift → escalate to DEGRADED minimum
    if broken_count == 1 and snapshot.system_drift_score > _BROKEN_EDGE_DRIFT_CRITICAL:
        degraded = get_stability_policy(SystemStabilityState.DEGRADED)
        return _merge_stricter(base_policy, degraded)

    return base_policy


def _enforce_protected_override(
    snapshot: StabilitySnapshot,
    current_policy: StabilityPolicy,
) -> StabilityPolicy:
    """
    PROTECTED_MODE override always wins.

    If global state is PROTECTED_MODE, force the protected policy
    regardless of any other resolution logic.
    """
    if snapshot.global_state == SystemStabilityState.PROTECTED_MODE:
        return get_stability_policy(SystemStabilityState.PROTECTED_MODE)

    return current_policy


def _merge_stricter(
    current: StabilityPolicy,
    override: StabilityPolicy,
) -> StabilityPolicy:
    """
    Merge two policies, taking the STRICTER constraint from each field.

    Stricter means:
    - allow_new_trades: False wins over True
    - reduce_risk: True wins over False
    - disable_partial_tp: True wins over False
    - disable_trailing: True wins over False
    - max_position_size_multiplier: lower value wins
    - commentary: override commentary used if policy escalated
    """
    escalated = (
        override.max_position_size_multiplier < current.max_position_size_multiplier
        or (not override.allow_new_trades and current.allow_new_trades)
    )

    return StabilityPolicy(
        allow_new_trades=current.allow_new_trades and override.allow_new_trades,
        reduce_risk=current.reduce_risk or override.reduce_risk,
        disable_partial_tp=current.disable_partial_tp or override.disable_partial_tp,
        disable_trailing=current.disable_trailing or override.disable_trailing,
        max_position_size_multiplier=min(
            current.max_position_size_multiplier,
            override.max_position_size_multiplier,
        ),
        commentary=override.commentary if escalated else current.commentary,
    )
