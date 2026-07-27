"""
Stability State — Represents overall system health state.

NO execution logic. PURE state representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.drift.drift_classifier import DriftStatus


# ─── SYSTEM STABILITY STATES ──────────────────────────────────────────────────

class SystemStabilityState(str, Enum):
    """Overall system health classification."""

    HEALTHY = "HEALTHY"
    VOLATILE = "VOLATILE"
    DEGRADED = "DEGRADED"
    RECOVERY_MODE = "RECOVERY_MODE"
    PROTECTED_MODE = "PROTECTED_MODE"


# ─── STABILITY SNAPSHOT ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class StabilitySnapshot:
    """
    Point-in-time snapshot of system stability across all monitored cohorts.

    Fields:
        global_state: Overall system health classification.
        cohort_states: Per-cohort drift status mapping.
        worst_cohort: Cohort key with highest drift / worst status.
        system_drift_score: Aggregate drift score across all cohorts (0.0–1.0).
        consecutive_degradation_cycles: Number of consecutive checks showing degradation.
        last_stable_timestamp: ISO timestamp of last fully stable check.
    """

    global_state: SystemStabilityState
    cohort_states: dict[str, DriftStatus] = field(default_factory=dict)
    worst_cohort: str = ""
    system_drift_score: float = 0.0
    consecutive_degradation_cycles: int = 0
    last_stable_timestamp: str = ""
