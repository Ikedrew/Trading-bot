"""
Strategy Knowledge Library — Models.

Pure knowledge representation. No trading logic. No calculations.
No execution imports. No decision pipeline dependencies.

These models describe WHAT strategies exist and WHAT they require,
not HOW to evaluate or execute them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyFamily(str, Enum):
    """Strategy family classification."""
    REVERSAL = "REVERSAL"
    MOMENTUM = "MOMENTUM"
    CONTINUATION = "CONTINUATION"
    BREAKOUT = "BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    STRUCTURE = "STRUCTURE"


class EvidenceStatus(str, Enum):
    """Research evidence status for a strategy."""
    HYPOTHESIS = "HYPOTHESIS"
    RESEARCHING = "RESEARCHING"
    SHADOW_TESTING = "SHADOW_TESTING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ConfidenceLevel(str, Enum):
    """Confidence in the strategy hypothesis."""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class StrategyDefinition:
    """
    Immutable knowledge definition of a trading strategy.

    This is a DESCRIPTION, not an implementation.
    It answers: "What is this strategy? When is it relevant? What does it need?"

    It does NOT:
        - Calculate anything
        - Evaluate conditions
        - Score opportunities
        - Make decisions
        - Connect to execution
    """
    # ─── IDENTITY ─────────────────────────────────────────────────────
    strategy_id: str
    name: str
    family: StrategyFamily

    # ─── HYPOTHESIS ───────────────────────────────────────────────────
    hypothesis: str
    description: str

    # ─── APPLICABILITY ────────────────────────────────────────────────
    valid_market_phases: tuple[str, ...] = ()
    valid_regimes: tuple[str, ...] = ()
    preferred_context: tuple[str, ...] = ()

    # ─── CONDITIONS (descriptive identifiers only) ────────────────────
    required_conditions: tuple[str, ...] = ()
    invalid_conditions: tuple[str, ...] = ()

    # ─── RESEARCH STATUS ──────────────────────────────────────────────
    evidence_status: EvidenceStatus = EvidenceStatus.HYPOTHESIS
    confidence_level: ConfidenceLevel = ConfidenceLevel.NONE

    # ─── METADATA ─────────────────────────────────────────────────────
    version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def family_name(self) -> str:
        return self.family.value

    @property
    def is_hypothesis(self) -> bool:
        return self.evidence_status == EvidenceStatus.HYPOTHESIS

    @property
    def is_active(self) -> bool:
        return self.evidence_status == EvidenceStatus.ACTIVE

    @property
    def phase_count(self) -> int:
        return len(self.valid_market_phases)

    @property
    def condition_count(self) -> int:
        return len(self.required_conditions)


@dataclass(frozen=True)
class FamilyDefinition:
    """
    Knowledge definition of a strategy family.

    Describes WHAT behaviour the family exploits and WHAT its
    general hypothesis is.
    """
    family: StrategyFamily
    hypothesis: str
    description: str
    typical_phases: tuple[str, ...] = ()
    typical_regimes: tuple[str, ...] = ()
