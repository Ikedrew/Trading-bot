"""
Research Feedback Model.

Represents the governed research feedback artifact produced by
interpreting a completed research finding.

A feedback artifact identifies what the finding means for the trading system
without directly modifying any system component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FeedbackType(str, Enum):
    """What the research finding implies about the system."""
    CONFIRMED_STRENGTH = "CONFIRMED_STRENGTH"
    IDENTIFIED_WEAKNESS = "IDENTIFIED_WEAKNESS"
    OPPORTUNITY = "OPPORTUNITY"
    UNCERTAINTY = "UNCERTAINTY"
    DATA_GAP = "DATA_GAP"
    RESEARCH_GAP = "RESEARCH_GAP"
    NO_ACTION = "NO_ACTION"


class SystemArea(str, Enum):
    """Which system area a finding concerns."""
    MARKET = "MARKET"
    DECISION = "DECISION"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    EXECUTION = "EXECUTION"
    OUTCOME = "OUTCOME"
    CROSS_UNIVERSE = "CROSS_UNIVERSE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ResearchFeedback:
    """
    A governed research feedback artifact.

    Produced from a completed ResearchFinding.
    Identifies what the research means for the trading system.
    Cannot directly modify any system component.
    """
    # Identity
    feedback_id: str = ""
    source_finding_id: str = ""  # question_id + run_id
    run_id: str = ""
    question_id: str = ""

    # Finding context
    finding_outcome: str = ""
    finding_confidence: str = ""

    # Feedback classification
    system_area: str = SystemArea.UNKNOWN.value
    feedback_type: str = FeedbackType.NO_ACTION.value

    # Interpretation
    interpretation: str = ""
    evidence_summary: str = ""
    affected_component: str = ""
    hypothesis: str = ""
    recommended_research: list[str] = field(default_factory=list)

    # Proposal eligibility
    proposal_eligible: bool = False
    proposal_blocked_reason: str = ""

    # Governance
    governance_note: str = (
        "This is a research feedback artifact. "
        "It cannot directly modify trading behaviour."
    )

    # Lineage
    question_version: str = ""
    analysis_version: str = ""
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    # Timestamp
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "source_finding_id": self.source_finding_id,
            "run_id": self.run_id,
            "question_id": self.question_id,
            "finding_outcome": self.finding_outcome,
            "finding_confidence": self.finding_confidence,
            "system_area": self.system_area,
            "feedback_type": self.feedback_type,
            "interpretation": self.interpretation,
            "evidence_summary": self.evidence_summary,
            "affected_component": self.affected_component,
            "hypothesis": self.hypothesis,
            "recommended_research": self.recommended_research,
            "proposal_eligible": self.proposal_eligible,
            "proposal_blocked_reason": self.proposal_blocked_reason,
            "governance_note": self.governance_note,
            "question_version": self.question_version,
            "analysis_version": self.analysis_version,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "created_at": self.created_at,
        }
