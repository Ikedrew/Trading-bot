"""
Candidate Registry — Data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.base import timestamp_now


class CandidateStatus:
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    SHADOW_TESTING = "SHADOW_TESTING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    ARCHIVED = "ARCHIVED"


@dataclass
class ValidationEntry:
    """One validation result attached to a candidate."""
    validation_id: str = ""
    timestamp: str = ""
    decision: str = ""
    confidence: str = ""
    sample_size: int = 0
    expectancy_delta: float = 0.0
    regressions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "timestamp": self.timestamp,
            "decision": self.decision,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "expectancy_delta": self.expectancy_delta,
            "regressions": self.regressions,
        }


@dataclass
class CandidateRecord:
    """Complete record for an optimisation candidate."""
    candidate_id: str
    hypothesis_id: str = ""
    baseline_id: str = ""
    component: str = ""
    created_at: str = ""
    created_from_question: str = ""
    created_from_campaign: str = ""
    description: str = ""
    change_definition: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    risk_level: str = "LOW"
    status: str = "PROPOSED"
    validation_history: list[ValidationEntry] = field(default_factory=list)
    status_history: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()
        if not self.status_history:
            self.status_history = [{"status": self.status, "timestamp": self.created_at}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "baseline_id": self.baseline_id,
            "component": self.component,
            "created_at": self.created_at,
            "created_from_question": self.created_from_question,
            "created_from_campaign": self.created_from_campaign,
            "description": self.description,
            "change_definition": self.change_definition,
            "expected_outcome": self.expected_outcome,
            "risk_level": self.risk_level,
            "status": self.status,
            "validation_history": [v.to_dict() for v in self.validation_history],
            "status_history": self.status_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRecord:
        val_history = [
            ValidationEntry(**v) for v in data.get("validation_history", [])
        ]
        record = cls(
            candidate_id=data["candidate_id"],
            hypothesis_id=data.get("hypothesis_id", ""),
            baseline_id=data.get("baseline_id", ""),
            component=data.get("component", ""),
            created_at=data.get("created_at", ""),
            created_from_question=data.get("created_from_question", ""),
            created_from_campaign=data.get("created_from_campaign", ""),
            description=data.get("description", ""),
            change_definition=data.get("change_definition", {}),
            expected_outcome=data.get("expected_outcome", ""),
            risk_level=data.get("risk_level", "LOW"),
            status=data.get("status", "PROPOSED"),
            validation_history=val_history,
            status_history=data.get("status_history", []),
        )
        return record
