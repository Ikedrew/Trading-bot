"""
Cohort Policy Types — Pure data structures for Phase 5 Cohort Policy Registry.

NO LOGIC. NO FUNCTIONS. ONLY TYPES.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CohortKey:
    """Identifies a unique cohort by dimensional coordinates."""

    confirmation_strength: str  # "STRONG" / "WEAK" / "INVALID"
    entry_timing: str           # "EARLY" / "MID" / "LATE"
    market_regime: str          # "TRENDING" / "RANGING" / "UNKNOWN"


@dataclass(frozen=True)
class ManagementPolicy:
    """Trade management policy recommendation for a cohort."""

    name: str                # "EXPAND" / "STANDARD" / "REDUCE" / "AVOID"
    description: str         # Human-readable policy summary
    trailing_mode: str       # "OFF" / "LIGHT" / "AGGRESSIVE"
    break_even_mode: str     # "OFF" / "EARLY" / "DELAYED"
    partial_tp_mode: str     # "OFF" / "STANDARD" / "AGGRESSIVE"
    rr_bias: float           # Target RR adjustment (e.g. 2.0, 3.0, 1.5)
    notes: str               # Additional context or reasoning


@dataclass(frozen=True)
class CohortPolicyRecord:
    """Links a cohort to its recommended management policy."""

    cohort: CohortKey
    policy: ManagementPolicy
