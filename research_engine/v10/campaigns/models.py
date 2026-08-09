"""
Campaign Engine — Data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.base import timestamp_now


@dataclass
class ResearchCampaign:
    """Definition of a research campaign (investigation)."""
    campaign_id: str
    name: str
    objective: str
    description: str = ""
    domains: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)
    version: str = "1"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "objective": self.objective,
            "description": self.description,
            "domains": self.domains,
            "questions": self.questions,
            "filters": self.filters,
            "version": self.version,
            "created_at": self.created_at,
        }


@dataclass
class CampaignFinding:
    """A single finding from a campaign question."""
    question_id: str
    question_name: str
    domain: str
    sample_size: int = 0
    result_value: float = 0.0
    result_metric: str = ""
    confidence: str = "LOW"
    evidence_maturity: str = ""
    decision_status: str = ""
    priority: str = "LOW"
    priority_score: float = 0.0
    recommendation: str = ""
    next_step: str = ""
    limitations: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_name": self.question_name,
            "domain": self.domain,
            "sample_size": self.sample_size,
            "result_value": self.result_value,
            "result_metric": self.result_metric,
            "confidence": self.confidence,
            "evidence_maturity": self.evidence_maturity,
            "decision_status": self.decision_status,
            "priority": self.priority,
            "priority_score": self.priority_score,
            "recommendation": self.recommendation,
            "next_step": self.next_step,
            "limitations": self.limitations,
            "error": self.error,
        }


@dataclass
class CampaignResult:
    """Complete result of a campaign execution."""
    campaign_id: str
    campaign_name: str = ""
    objective: str = ""
    filters_applied: dict[str, str] = field(default_factory=dict)
    questions_executed: int = 0
    questions_failed: int = 0
    findings: list[CampaignFinding] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    confidence_summary: dict[str, int] = field(default_factory=dict)
    evidence_summary: dict[str, int] = field(default_factory=dict)
    execution_time_seconds: float = 0.0
    timestamp: str = ""
    error: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = timestamp_now()

    @property
    def high_priority_findings(self) -> list[CampaignFinding]:
        return [f for f in self.findings if f.priority == "HIGH"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "objective": self.objective,
            "filters_applied": self.filters_applied,
            "questions_executed": self.questions_executed,
            "questions_failed": self.questions_failed,
            "findings": [f.to_dict() for f in self.findings],
            "recommendations": self.recommendations,
            "data_gaps": self.data_gaps,
            "confidence_summary": self.confidence_summary,
            "evidence_summary": self.evidence_summary,
            "execution_time_seconds": self.execution_time_seconds,
            "timestamp": self.timestamp,
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN EVENTS (observability hooks — interface only)
# ═══════════════════════════════════════════════════════════════

class CampaignEvent:
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    QUESTION_STARTED = "QUESTION_STARTED"
    QUESTION_COMPLETED = "QUESTION_COMPLETED"
    FINDING_CREATED = "FINDING_CREATED"
    CAMPAIGN_COMPLETED = "CAMPAIGN_COMPLETED"
