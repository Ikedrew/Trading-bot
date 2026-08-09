"""
Optimisation Bridge — Data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from research_engine.v10.base import timestamp_now


class ChangeRisk(str, Enum):
    LOW = "LOW"        # Parameter adjustments (ATR multiplier, thresholds)
    MEDIUM = "MEDIUM"  # Logic changes (filters, selection rules)
    HIGH = "HIGH"      # Architecture changes (new strategies, new models)


class HypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class CandidateStatus(str, Enum):
    PROPOSED = "PROPOSED"
    READY_FOR_TEST = "READY_FOR_TEST"
    TESTING = "TESTING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass
class ResearchHypothesis:
    """A testable hypothesis derived from a research finding."""
    hypothesis_id: str
    source_finding: str = ""
    source_question: str = ""
    domain: str = ""
    created_at: str = ""
    statement: str = ""
    target_component: str = ""
    expected_effect: str = ""
    confidence: str = "LOW"
    evidence_strength: str = ""
    status: str = "PROPOSED"

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "source_finding": self.source_finding,
            "source_question": self.source_question,
            "domain": self.domain,
            "created_at": self.created_at,
            "statement": self.statement,
            "target_component": self.target_component,
            "expected_effect": self.expected_effect,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "status": self.status,
        }


@dataclass
class OptimisationCandidate:
    """A proposed bot change linked to a hypothesis and baseline."""
    candidate_id: str
    hypothesis_id: str = ""
    baseline_id: str = ""
    created_at: str = ""
    component: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    risk_level: str = "LOW"
    status: str = "PROPOSED"
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()
        if not self.baseline_id:
            raise ValueError("OptimisationCandidate requires a baseline_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "baseline_id": self.baseline_id,
            "created_at": self.created_at,
            "component": self.component,
            "changes": self.changes,
            "expected_outcome": self.expected_outcome,
            "risk_level": self.risk_level,
            "status": self.status,
            "notes": self.notes,
        }


@dataclass
class ValidationPlan:
    """Defines how a candidate will be validated against its baseline."""
    candidate_id: str
    baseline_id: str
    created_at: str = ""
    metrics: list[str] = field(default_factory=list)
    target_questions: list[str] = field(default_factory=list)
    regression_questions: list[str] = field(default_factory=list)
    success_conditions: dict[str, str] = field(default_factory=dict)
    failure_conditions: dict[str, str] = field(default_factory=dict)
    minimum_sample: int = 20
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "baseline_id": self.baseline_id,
            "created_at": self.created_at,
            "metrics": self.metrics,
            "target_questions": self.target_questions,
            "regression_questions": self.regression_questions,
            "success_conditions": self.success_conditions,
            "failure_conditions": self.failure_conditions,
            "minimum_sample": self.minimum_sample,
            "notes": self.notes,
        }


def classify_change_risk(changes: dict[str, Any]) -> str:
    """Classify the risk level of proposed changes."""
    _HIGH_KEYWORDS = {"strategy", "model", "architecture", "engine", "pipeline"}
    _MEDIUM_KEYWORDS = {"filter", "rule", "logic", "selection", "regime"}

    all_keys = " ".join(str(k).lower() for k in changes.keys())
    all_vals = " ".join(str(v).lower() for v in changes.values() if isinstance(v, str))
    combined = all_keys + " " + all_vals

    if any(kw in combined for kw in _HIGH_KEYWORDS):
        return ChangeRisk.HIGH
    if any(kw in combined for kw in _MEDIUM_KEYWORDS):
        return ChangeRisk.MEDIUM
    return ChangeRisk.LOW
