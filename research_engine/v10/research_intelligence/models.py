"""
Research Intelligence — Data models and result structures.

Defines the standard interface for research questions and results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QuestionDefinition:
    """Registered research question metadata."""
    id: str
    name: str
    category: str  # outcome, risk, execution, prediction, selection, regime, etc.
    description: str = ""
    domain: str = "trade"  # trade, decision, market, strategy
    required_fields: list[str] = field(default_factory=list)
    required_segments: list[str] = field(default_factory=list)
    minimum_sample_size: int = 10
    experiment_module: str = ""
    status: str = "active"  # active, deprecated, draft
    status: str = "active"  # active, deprecated, draft


@dataclass
class ExperimentResult:
    """Standardised experiment output."""
    question_id: str
    question_name: str
    sample_size: int
    result: dict[str, Any] = field(default_factory=dict)
    confidence: str = "LOW"  # HIGH, MEDIUM, LOW
    recommendation: str = "INCONCLUSIVE"  # SUPPORTED, REJECTED, INCONCLUSIVE
    limitations: list[str] = field(default_factory=list)
    filters_applied: dict[str, str] = field(default_factory=dict)
    segment_population: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_name": self.question_name,
            "sample_size": self.sample_size,
            "result": self.result,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "limitations": self.limitations,
            "filters_applied": self.filters_applied,
            "segment_population": self.segment_population,
            "error": self.error,
        }


def classify_confidence(sample_size: int, data_completeness_pct: float = 100.0) -> str:
    """Classify confidence based on sample size and data quality."""
    if sample_size >= 30 and data_completeness_pct >= 90:
        return "HIGH"
    elif sample_size >= 10:
        return "MEDIUM"
    return "LOW"
