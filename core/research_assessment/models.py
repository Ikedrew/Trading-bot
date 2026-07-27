"""
Research Assessment Models — Data contract between Research and Production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResearchAssessment:
    """
    Informational assessment from the Research Engine for one production decision.

    This is OBSERVATIONAL ONLY. It does not influence execution.
    It provides empirical evidence alongside synthetic probability for comparison.
    """

    # Match status
    candidate_match: bool = False
    candidate_id: str = ""

    # Empirical statistics (from validated candidates)
    historical_win_rate: float = 0.0
    empirical_ev: float = 0.0
    sample_size: int = 0
    walk_forward_survivor: bool = False
    walk_forward_positive_splits: int = 0
    walk_forward_total_splits: int = 0

    # Confidence
    research_confidence: str = "NONE"  # HIGH / MEDIUM / LOW / NONE

    # Conditions matched
    matched_conditions: dict[str, str] = field(default_factory=dict)

    # Reasoning
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_match": self.candidate_match,
            "candidate_id": self.candidate_id,
            "historical_win_rate": round(self.historical_win_rate, 4),
            "empirical_ev": round(self.empirical_ev, 4),
            "sample_size": self.sample_size,
            "walk_forward_survivor": self.walk_forward_survivor,
            "walk_forward_positive_splits": self.walk_forward_positive_splits,
            "walk_forward_total_splits": self.walk_forward_total_splits,
            "research_confidence": self.research_confidence,
            "matched_conditions": dict(self.matched_conditions),
            "reasoning": self.reasoning,
        }


# Singleton neutral assessment (no match found)
NEUTRAL_ASSESSMENT = ResearchAssessment(
    reasoning="No validated candidate matches current decision context",
)
