"""
Horizon Models — Data structures for trade horizon intelligence.

Defines the horizon enum and assessment result used throughout
the observation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TradeHorizon(str, Enum):
    """Possible trade horizons for an opportunity."""
    SCALP = "SCALP"          # Minutes. M5 structure. Quick resolution.
    INTRADAY = "INTRADAY"    # Hours. M15/H1 structure. Within-session.
    EXTENDED = "EXTENDED"    # Days. H1/H4 structure. Multi-session.


@dataclass
class HorizonAssessment:
    """Assessment of one horizon's viability for an opportunity."""

    horizon: str                     # TradeHorizon value
    eligible: bool                   # Whether this horizon is plausible
    confidence: float                # 0.0–1.0 confidence in eligibility
    reasoning: str                   # Human-readable explanation
    evidence: dict[str, Any] = field(default_factory=dict)
    # Supporting/contradicting factors

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "eligible": self.eligible,
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "evidence": self.evidence,
        }


@dataclass
class HorizonClassificationResult:
    """Complete horizon classification for one opportunity."""

    assessments: list[HorizonAssessment] = field(default_factory=list)

    @property
    def eligible_horizons(self) -> list[str]:
        return [a.horizon for a in self.assessments if a.eligible]

    @property
    def best_horizon(self) -> str | None:
        eligible = [a for a in self.assessments if a.eligible]
        if not eligible:
            return None
        return max(eligible, key=lambda a: a.confidence).horizon

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessments": [a.to_dict() for a in self.assessments],
            "eligible_horizons": self.eligible_horizons,
            "best_horizon": self.best_horizon,
        }

    def to_summary_dict(self) -> dict[str, dict[str, Any]]:
        """Compact representation keyed by horizon name."""
        return {
            a.horizon: {
                "eligible": a.eligible,
                "confidence": round(a.confidence, 4),
            }
            for a in self.assessments
        }
