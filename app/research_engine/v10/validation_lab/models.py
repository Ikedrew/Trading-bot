"""
Validation Lab — Data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.base import timestamp_now


class ValidationStatus:
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ValidationDecision:
    IMPROVED = "IMPROVED"
    NO_IMPROVEMENT = "NO_IMPROVEMENT"
    REGRESSION = "REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class ValidationRun:
    """Complete record of a candidate validation."""
    validation_id: str
    candidate_id: str = ""
    baseline_id: str = ""
    created_at: str = ""
    status: str = "CREATED"
    dataset_filters: dict[str, str] = field(default_factory=dict)

    # Results
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, Any] = field(default_factory=dict)
    regressions: list[dict[str, Any]] = field(default_factory=list)

    # Decision
    decision: str = ""
    confidence: str = "LOW"
    evidence_maturity: str = ""
    recommendation: str = ""
    limitations: list[str] = field(default_factory=list)

    # Governance
    sample_size: int = 0
    population_description: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "candidate_id": self.candidate_id,
            "baseline_id": self.baseline_id,
            "created_at": self.created_at,
            "status": self.status,
            "dataset_filters": self.dataset_filters,
            "baseline_metrics": self.baseline_metrics,
            "candidate_metrics": self.candidate_metrics,
            "comparison": self.comparison,
            "regressions": self.regressions,
            "decision": self.decision,
            "confidence": self.confidence,
            "evidence_maturity": self.evidence_maturity,
            "recommendation": self.recommendation,
            "limitations": self.limitations,
            "sample_size": self.sample_size,
            "population_description": self.population_description,
        }
