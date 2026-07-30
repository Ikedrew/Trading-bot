"""V10 OpportunityAssessment — Structured trade opportunity evaluation.

Answers: "Given the current market state, is a meaningful opportunity forming?"

Does NOT contain:
  - Entry price / stop / target
  - Risk parameters
  - Execution decisions
  - Strategy selection (which strategy to use)

Contains:
  - Whether an opportunity exists (state)
  - What type of opportunity (type classification)
  - Directional bias from structure
  - Quality scores per dimension
  - Human-readable reasoning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "v10_opportunity_assessment_v1"


# ═══════════════════════════════════════════════════════════════
# QUALITY SCORES
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OpportunityQuality:
    """Dimension-specific quality scores (0.0–1.0 each)."""

    location_score: float = 0.0       # Is price at a meaningful level?
    structure_score: float = 0.0      # Does structure support the thesis?
    behaviour_score: float = 0.0      # Does the environment support movement?
    formation_score: float = 0.0      # Is M15 showing a meaningful reaction?
    overall_quality: float = 0.0      # Weighted composite


# ═══════════════════════════════════════════════════════════════
# OPPORTUNITY ASSESSMENT
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OpportunityAssessment:
    """
    Complete opportunity evaluation — immutable snapshot.

    Created by the OpportunityEngine from V10MarketState.
    Consumed by downstream strategy selection (not implemented yet).
    """

    # Identity
    observation_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _SCHEMA_VERSION

    # Core assessment
    opportunity_state: str = ""           # VALID / INVALID / WATCHING
    directional_bias: str = ""            # BULLISH / BEARISH / NEUTRAL
    opportunity_type: str = ""            # LIQUIDITY_SWEEP / STRUCTURE_SHIFT / ZONE_REACTION / etc.

    # Quality
    quality: OpportunityQuality = field(default_factory=OpportunityQuality)

    # Reasoning (human-readable explanations)
    reasoning: list[str] = field(default_factory=list)

    # Contributing factors
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "opportunity_state": self.opportunity_state,
            "directional_bias": self.directional_bias,
            "opportunity_type": self.opportunity_type,
            "quality": {
                "location_score": round(self.quality.location_score, 4),
                "structure_score": round(self.quality.structure_score, 4),
                "behaviour_score": round(self.quality.behaviour_score, 4),
                "formation_score": round(self.quality.formation_score, 4),
                "overall_quality": round(self.quality.overall_quality, 4),
            },
            "reasoning": list(self.reasoning),
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
        }
