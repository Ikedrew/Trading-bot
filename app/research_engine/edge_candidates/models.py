"""
Edge Candidate Schema — Structured research hypotheses for validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EdgeCandidate:
    """A structured hypothesis about a condition set with positive expectancy."""

    candidate_id: str = ""
    hypothesis: str = ""

    # Entry conditions
    conditions: dict[str, str] = field(default_factory=dict)
    # e.g. {"pattern": "TWEEZER_BOTTOM", "session": "OFF_SESSION"}

    # Evidence
    sample_size: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0  # EV per trade (R)
    profit_factor: float = 0.0
    total_r: float = 0.0

    # Quality scoring
    confidence_score: float = 0.0  # 0-100
    stability_score: float = 0.0   # 0-100
    overfit_risk: str = "UNKNOWN"  # LOW / MEDIUM / HIGH

    # Flags
    single_pattern_dependent: bool = False
    single_symbol_dependent: bool = False
    single_regime_dependent: bool = False
    low_sample: bool = False

    # Validation
    validation_status: str = "PENDING"  # PENDING / VALIDATED / FAILED
    validation_results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis": self.hypothesis,
            "conditions": self.conditions,
            "sample_size": self.sample_size,
            "win_rate": round(self.win_rate, 4),
            "expectancy": round(self.expectancy, 4),
            "profit_factor": round(self.profit_factor, 2),
            "total_r": round(self.total_r, 2),
            "confidence_score": round(self.confidence_score, 1),
            "stability_score": round(self.stability_score, 1),
            "overfit_risk": self.overfit_risk,
            "single_pattern_dependent": self.single_pattern_dependent,
            "single_symbol_dependent": self.single_symbol_dependent,
            "single_regime_dependent": self.single_regime_dependent,
            "low_sample": self.low_sample,
            "validation_status": self.validation_status,
            "validation_results": self.validation_results,
        }

    def to_validation_spec(self) -> dict[str, Any]:
        """Export as walk-forward validation input."""
        return {
            "candidate_id": self.candidate_id,
            "conditions": self.conditions,
            "training_requirements": {
                "min_samples": max(10, self.sample_size // 3),
                "features_required": list(self.conditions.keys()),
            },
            "validation_required": True,
        }
