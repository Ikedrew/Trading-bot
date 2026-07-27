"""
Evidence Attribution Models — decompose scores into contributing factors.

Every final score becomes explainable:
    "Pattern Quality contributed 0.113 to the final 0.62 score"

These objects are OBSERVATIONAL. They explain scoring.
They do NOT modify scoring.

INVARIANT: Frozen after construction. Pure decomposition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceContribution:
    """
    Single factor's contribution to a composite score.

    name: Human-readable factor name (e.g., "Pattern Quality")
    weight: How important the factor is in the weighting scheme (0.0–1.0)
    raw_value: The original signal value before weighting (0.0–1.0)
    contribution: The actual impact on final score (weight × raw_value)
    metadata: Optional context (thresholds, classification, notes)
    """
    name: str
    weight: float
    raw_value: float
    contribution: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "name": self.name,
            "weight": round(self.weight, 4),
            "raw_value": round(self.raw_value, 4),
            "contribution": round(self.contribution, 4),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ScoreAttribution:
    """
    Complete decomposition of a composite score into its contributing factors.

    contributions: Ordered list of all factor contributions (largest first)
    total_score: The final composite score being explained
    weights_profile: Which weight profile was used ("strategy_specific" or "global_fallback")
    metadata: Optional context (strategy, regime, scoring version)
    """
    contributions: tuple[EvidenceContribution, ...]
    total_score: float
    weights_profile: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "contributions": [c.to_dict() for c in self.contributions],
            "total_score": round(self.total_score, 4),
            "weights_profile": self.weights_profile,
            "metadata": self.metadata,
        }

    @property
    def top_contributors(self) -> tuple[EvidenceContribution, ...]:
        """Top 3 contributors by absolute contribution."""
        sorted_c = sorted(self.contributions, key=lambda c: c.contribution, reverse=True)
        return tuple(sorted_c[:3])

    @property
    def weakest_factors(self) -> tuple[EvidenceContribution, ...]:
        """Bottom 3 contributors (weakest signals)."""
        sorted_c = sorted(self.contributions, key=lambda c: c.raw_value)
        return tuple(sorted_c[:3])
