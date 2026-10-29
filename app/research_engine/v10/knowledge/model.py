"""
Knowledge State Model.

Represents the system's accumulated understanding about a subject,
backed by research evidence.

Knowledge ≠ Finding.
    Finding = one research run produced this result.
    Knowledge = across available evidence, this is the current understanding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class KnowledgeStatus(str, Enum):
    """Current status of a knowledge item."""
    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CONTRADICTED = "CONTRADICTED"
    SUPERSEDED = "SUPERSEDED"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class EvidenceRef:
    """Reference to a piece of supporting or contradicting evidence."""
    question_id: str = ""
    run_id: str = ""
    outcome: str = ""
    confidence: str = ""
    feedback_type: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "feedback_type": self.feedback_type,
            "timestamp": self.timestamp,
        }


@dataclass
class KnowledgeItem:
    """
    One piece of accumulated system knowledge.

    Evidence-backed, versioned, contradiction-aware.
    Cannot directly modify trading behaviour.
    """
    # Identity
    knowledge_id: str = ""
    subject: str = ""  # What the knowledge is about
    system_area: str = ""  # MARKET, DECISION, STRATEGY, RISK, EXECUTION, OUTCOME, CROSS_UNIVERSE

    # Current understanding
    statement: str = ""  # Concise representation of current belief
    status: str = KnowledgeStatus.UNRESOLVED.value
    confidence: str = ""  # HIGH, MEDIUM, LOW, INSUFFICIENT

    # Evidence
    supporting_evidence: list[EvidenceRef] = field(default_factory=list)
    contradicting_evidence: list[EvidenceRef] = field(default_factory=list)
    evidence_count: int = 0

    # Versioning
    knowledge_version: int = 1
    first_observed_at: str = ""
    last_updated_at: str = ""

    # Lineage
    source_universes: list[str] = field(default_factory=list)
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    # Governance
    governance_note: str = (
        "This is accumulated research knowledge. "
        "It cannot directly modify trading behaviour."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "subject": self.subject,
            "system_area": self.system_area,
            "statement": self.statement,
            "status": self.status,
            "confidence": self.confidence,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "evidence_count": self.evidence_count,
            "knowledge_version": self.knowledge_version,
            "first_observed_at": self.first_observed_at,
            "last_updated_at": self.last_updated_at,
            "source_universes": self.source_universes,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "governance_note": self.governance_note,
        }
