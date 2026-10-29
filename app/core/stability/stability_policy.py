"""
Stability Policy — Defines what each stability state is allowed to do.

NO execution logic. PURE policy mapping.
These policies are advisory — enforcement is handled by the runtime layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.stability.stability_state import SystemStabilityState


# ─── POLICY TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StabilityPolicy:
    """Defines operational constraints for a given system stability state."""

    allow_new_trades: bool
    reduce_risk: bool
    disable_partial_tp: bool
    disable_trailing: bool
    max_position_size_multiplier: float  # 1.0 = full, 0.5 = half, 0.0 = none
    commentary: str


# ─── POLICY MAP ───────────────────────────────────────────────────────────────

_POLICY_MAP: dict[SystemStabilityState, StabilityPolicy] = {
    SystemStabilityState.HEALTHY: StabilityPolicy(
        allow_new_trades=True,
        reduce_risk=False,
        disable_partial_tp=False,
        disable_trailing=False,
        max_position_size_multiplier=1.0,
        commentary="Full trading enabled. All strategies active at normal sizing.",
    ),
    SystemStabilityState.VOLATILE: StabilityPolicy(
        allow_new_trades=True,
        reduce_risk=True,
        disable_partial_tp=False,
        disable_trailing=False,
        max_position_size_multiplier=0.75,
        commentary="Reduced position size (0.75x). Market conditions unstable. Monitor closely.",
    ),
    SystemStabilityState.DEGRADED: StabilityPolicy(
        allow_new_trades=True,
        reduce_risk=True,
        disable_partial_tp=False,
        disable_trailing=True,
        max_position_size_multiplier=0.5,
        commentary="Reduced risk (0.5x). Aggressive trailing disabled. Edge quality declining.",
    ),
    SystemStabilityState.RECOVERY_MODE: StabilityPolicy(
        allow_new_trades=True,
        reduce_risk=True,
        disable_partial_tp=False,
        disable_trailing=True,
        max_position_size_multiplier=0.5,
        commentary="Slow scaling only. No new strategies. Rebuilding confidence from degraded state.",
    ),
    SystemStabilityState.PROTECTED_MODE: StabilityPolicy(
        allow_new_trades=False,
        reduce_risk=True,
        disable_partial_tp=True,
        disable_trailing=True,
        max_position_size_multiplier=0.0,
        commentary="Only manage existing trades. NO new entries. Edge is broken or unconfirmed.",
    ),
}


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def get_stability_policy(state: SystemStabilityState) -> StabilityPolicy:
    """
    Get the operational policy for a given system stability state.

    Args:
        state: Current SystemStabilityState.

    Returns:
        StabilityPolicy defining allowed operations and constraints.
    """
    return _POLICY_MAP[state]


def get_all_policies() -> dict[SystemStabilityState, StabilityPolicy]:
    """Return the complete policy map for reference/reporting."""
    return dict(_POLICY_MAP)
