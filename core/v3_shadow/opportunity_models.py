"""
V3 Opportunity Assessment Model — Evaluates market context quality.

This model describes WHETHER the environment is worth investigating,
NOT what to do about it.

It does NOT contain:
    - BUY / SELL directions
    - Trade signals
    - Risk calculations
    - Stop/target distances
    - Execution decisions

It answers: "Is this market environment aligned enough to continue
down the decision pipeline?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_OPPORTUNITY_SCHEMA_VERSION = "v3_opportunity_assessment_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# ASSESSMENT STATES
# ═══════════════════════════════════════════════════════════════════════════════

# Descriptive quality states (NOT trade decisions)
HIGH_QUALITY_CONTEXT = "HIGH_QUALITY_CONTEXT"
INTERESTING_CONTEXT = "INTERESTING_CONTEXT"
MIXED_CONTEXT = "MIXED_CONTEXT"
LOW_QUALITY_CONTEXT = "LOW_QUALITY_CONTEXT"
INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


# ═══════════════════════════════════════════════════════════════════════════════
# ALIGNMENT RESULT (per evaluation area)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AlignmentResult:
    """Result of evaluating one alignment dimension."""
    score: float = 0.0          # 0-1 (how well-aligned)
    factors: list[str] = field(default_factory=list)    # Supporting evidence
    conflicts: list[str] = field(default_factory=list)  # Conflicting evidence
    missing: list[str] = field(default_factory=list)    # Missing information


# ═══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITY ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OpportunityAssessment:
    """
    Immutable assessment of market context quality.

    Produced each cycle from V3MarketContext.
    Consumed by future Horizon Engine and Entry Model.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _OPPORTUNITY_SCHEMA_VERSION

    # Overall assessment
    assessment_state: str = INSUFFICIENT_CONTEXT
    confidence: float = 0.0         # 0-1

    # Context quality (average of three alignments)
    context_quality: float = 0.0    # 0-1

    # Per-area alignment
    structure_alignment: AlignmentResult = field(default_factory=AlignmentResult)
    location_alignment: AlignmentResult = field(default_factory=AlignmentResult)
    behaviour_alignment: AlignmentResult = field(default_factory=AlignmentResult)

    # Evidence summary
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)

    # Observations (human-readable)
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "assessment_state": self.assessment_state,
            "confidence": round(self.confidence, 4),
            "context_quality": round(self.context_quality, 4),
            "structure_alignment": {
                "score": round(self.structure_alignment.score, 4),
                "factors": list(self.structure_alignment.factors),
                "conflicts": list(self.structure_alignment.conflicts),
                "missing": list(self.structure_alignment.missing),
            },
            "location_alignment": {
                "score": round(self.location_alignment.score, 4),
                "factors": list(self.location_alignment.factors),
                "conflicts": list(self.location_alignment.conflicts),
                "missing": list(self.location_alignment.missing),
            },
            "behaviour_alignment": {
                "score": round(self.behaviour_alignment.score, 4),
                "factors": list(self.behaviour_alignment.factors),
                "conflicts": list(self.behaviour_alignment.conflicts),
                "missing": list(self.behaviour_alignment.missing),
            },
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
            "missing_information": list(self.missing_information),
            "observations": list(self.observations),
        }
