"""
Strategy Family Models — Data definitions for the family authority system.

Pure data. No trading logic. No runtime side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StrategyFamily(str, Enum):
    """
    Strategy family classification.

    Each family represents a distinct market behaviour the strategy attempts to exploit.
    Patterns are grouped by the type of price action they detect, not by direction.
    """
    REVERSAL = "REVERSAL"
    """Exhaustion → direction change. Patterns that signal trend termination."""

    MOMENTUM = "MOMENTUM"
    """Strong directional commitment. Patterns showing consecutive directional conviction."""

    CONTINUATION = "CONTINUATION"
    """Trend-following after pullback. Currently NO patterns in library. Future expansion."""

    BREAKOUT = "BREAKOUT"
    """Range escape signals. Currently NO patterns in library. Future expansion."""

    MEAN_REVERSION = "MEAN_REVERSION"
    """Bounce from statistical extremes. Currently NO patterns in library. Future expansion."""


class EligibilityReason(str, Enum):
    """Why a family was included or excluded from eligibility."""
    ALWAYS_ELIGIBLE = "ALWAYS_ELIGIBLE"             # Passthrough mode (no filtering active)
    PHASE_MATCH = "PHASE_MATCH"                     # Phase research recommends this family
    PHASE_MISMATCH = "PHASE_MISMATCH"               # Phase research recommends against
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE" # Not enough data to decide
    RESEARCH_PROMOTED = "RESEARCH_PROMOTED"         # Validated by research pipeline
    MANUALLY_DISABLED = "MANUALLY_DISABLED"         # Operator override
    NO_PATTERNS_AVAILABLE = "NO_PATTERNS_AVAILABLE" # Family exists but has no pattern detectors


@dataclass(frozen=True)
class FamilyEligibility:
    """
    Result of the authority's eligibility check for one family in one context.
    """
    family: StrategyFamily
    eligible: bool
    reason: EligibilityReason
    confidence: float = 0.0     # 0.0-1.0, research confidence backing this decision
    evidence_source: str = ""   # e.g. "M9_phase_pattern", "M10_family_per_phase"


@dataclass(frozen=True)
class PatternClassification:
    """
    Result of classifying a single detected pattern into its strategy family.
    """
    pattern: str
    family: Optional[StrategyFamily]
    confidence: float
    reason: str
    known: bool                 # Whether the pattern exists in the registry

    @property
    def family_name(self) -> str:
        return self.family.value if self.family else "UNKNOWN"


@dataclass(frozen=True)
class FamilySelectionResult:
    """
    Complete output from StrategyFamilyAuthority for one cycle.

    Contains eligibility for ALL families so downstream can understand
    why certain patterns were/weren't considered.
    """
    eligible_families: tuple[StrategyFamily, ...]
    rejected_families: tuple[StrategyFamily, ...]
    all_eligibility: tuple[FamilyEligibility, ...]
    selected_family: Optional[StrategyFamily] = None
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    mode: str = "PASSTHROUGH"   # PASSTHROUGH | RESEARCH_GATED | OPERATOR_OVERRIDE
    context_used: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def eligible_family_names(self) -> list[str]:
        return [f.value for f in self.eligible_families]

    @property
    def rejected_family_names(self) -> list[str]:
        return [f.value for f in self.rejected_families]

    def is_eligible(self, family: StrategyFamily) -> bool:
        return family in self.eligible_families


@dataclass(frozen=True)
class ResearchValidation:
    """
    Metadata required before a research rule can be activated.

    Rules cannot become active unless ALL validation fields are satisfied.
    """
    minimum_sample_size: int
    actual_sample_size: int
    p_value: float
    confidence_interval: tuple[float, float]
    walk_forward_validated: bool
    experiment_source: str       # e.g. "M10_strategy_family_per_phase"
    validation_date: str         # ISO format date

    @property
    def is_valid(self) -> bool:
        """Check if all activation criteria are met."""
        return (
            self.actual_sample_size >= self.minimum_sample_size
            and self.p_value < 0.05
            and self.walk_forward_validated
        )
