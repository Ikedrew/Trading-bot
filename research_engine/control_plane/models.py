"""
Research Control Plane - Data models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReadinessStatus(str, Enum):
    """Question readiness classification."""
    READY = "READY"
    WAITING_DATA = "WAITING_DATA"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    NO_RUNNER = "NO_RUNNER"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class RunnerStatus(str, Enum):
    """Runner implementation/execution status."""
    READY = "READY"
    NO_RUNNER = "NO_RUNNER"
    NOT_RUN = "NOT_RUN"
    ERROR = "ERROR"
    ALIAS = "ALIAS"


class ReportValidity(str, Enum):
    """Validity classification of a report artifact."""
    VALID_CURRENT = "VALID_CURRENT"
    INVALIDATED = "INVALIDATED"
    STALE = "STALE"
    LEGACY = "LEGACY"
    SUPERSEDED = "SUPERSEDED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


class ApplicationStatus(str, Enum):
    """Production application state."""
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    APPROVED_NOT_DEPLOYED = "APPROVED_NOT_DEPLOYED"
    DEPLOYED = "DEPLOYED"
    VERIFIED = "VERIFIED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class CanonicalReportRecord:
    """Authoritative canonical report record for one research question artifact.

    Represents a discovered report with its canonical classification.
    """
    canonical_question_id: str
    report_path: str
    source_question_id: str
    generated_at: str
    evidence_epoch: str
    report_status: str
    report_validity: ReportValidity
    validity_reason: str
    sample_size: int | None
    finding: str
    confidence: str
    recommendation: str
    evidence_fingerprint: dict[str, Any]
    superseded_by: str | None
    invalidation_reason: str | None
    is_authoritative: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "canonical_question_id": self.canonical_question_id,
            "report_path": self.report_path,
            "source_question_id": self.source_question_id,
            "generated_at": self.generated_at,
            "evidence_epoch": self.evidence_epoch,
            "report_status": self.report_status,
            "report_validity": self.report_validity.value,
            "validity_reason": self.validity_reason,
            "sample_size": self.sample_size,
            "finding": self.finding,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "evidence_fingerprint": self.evidence_fingerprint,
            "superseded_by": self.superseded_by,
            "invalidation_reason": self.invalidation_reason,
            "is_authoritative": self.is_authoritative,
        }


@dataclass
class QuestionState:
    """Canonical read-only state for one research question."""
    question_id: str
    title: str
    description: str
    category: str
    canonical_registry: str = (
        "research_engine.registry.research_question_registry.REGISTRY"
    )
    state_status: str = "NOT_RUN"
    readiness_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    readiness_reason: str = ""
    required_sample_size: int | None = None
    required_evidence: list[str] = field(default_factory=list)
    available_evidence: list[str] = field(default_factory=list)
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)
    evidence_metrics: dict[str, Any] = field(default_factory=dict)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    excluded_evidence_count: int = 0
    runner_status: RunnerStatus = RunnerStatus.NO_RUNNER
    runner_module: str = ""
    runner_function: str = ""
    current_sample_size: int | None = None
    evidence_epoch: str = ""
    latest_report_path: str = ""
    latest_report_status: str = ""
    latest_result: Any = None
    latest_finding: str = ""
    confidence: str = ""
    report_validity: ReportValidity = ReportValidity.MISSING
    report_validity_reason: str = ""
    last_run_at: str = ""
    candidate_status: str = "NONE"
    candidate_id: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_mapping_status: str = "NONE"
    unmapped_candidate_ids: list[str] = field(default_factory=list)
    production_application_status: ApplicationStatus = ApplicationStatus.UNKNOWN
    warnings: list[str] = field(default_factory=list)
    next_action: str = ""
    # Canonical report layer fields
    authoritative_report: CanonicalReportRecord | None = None
    report_history: list[CanonicalReportRecord] = field(default_factory=list)
    report_history_count: int = 0
    invalidated_report_count: int = 0
    stale_report_count: int = 0
    superseded_report_count: int = 0
    legacy_report_count: int = 0
    malformed_report_count: int = 0
    latest_valid_run_timestamp: str = ""
    # Governance layer fields
    candidate_count: int = 0
    decision_status: str = "NOT_REVIEWED"
    latest_decision: dict[str, Any] | None = None
    latest_application_event: dict[str, Any] | None = None
    governance_warnings: list[str] = field(default_factory=list)
    next_governance_action: str = ""
    identity_status: str = "CANONICAL_OWNER"
    scientific_owner_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "question_id": self.question_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "canonical_registry": self.canonical_registry,
            "state_status": self.state_status,
            "readiness_status": self.readiness_status.value,
            "readiness_reason": self.readiness_reason,
            "required_sample_size": self.required_sample_size,
            "required_evidence": self.required_evidence,
            "available_evidence": self.available_evidence,
            "evidence_sources": self.evidence_sources,
            "evidence_metrics": self.evidence_metrics,
            "requirements": self.requirements,
            "excluded_evidence_count": self.excluded_evidence_count,
            "runner_status": self.runner_status.value,
            "runner_module": self.runner_module,
            "runner_function": self.runner_function,
            "current_sample_size": self.current_sample_size,
            "evidence_epoch": self.evidence_epoch,
            "latest_report_path": self.latest_report_path,
            "latest_report_status": self.latest_report_status,
            "latest_result": self.latest_result,
            "latest_finding": self.latest_finding,
            "confidence": self.confidence,
            "report_validity": self.report_validity.value,
            "report_validity_reason": self.report_validity_reason,
            "last_run_at": self.last_run_at,
            "candidate_status": self.candidate_status,
            "candidate_id": self.candidate_id,
            "candidates": self.candidates,
            "candidate_mapping_status": self.candidate_mapping_status,
            "unmapped_candidate_ids": self.unmapped_candidate_ids,
            "production_application_status": self.production_application_status.value,
            "warnings": self.warnings,
            "next_action": self.next_action,
            # Canonical report layer
            "authoritative_report": self.authoritative_report.to_dict() if self.authoritative_report else None,
            "report_history": [r.to_dict() for r in self.report_history],
            "report_history_count": self.report_history_count,
            "invalidated_report_count": self.invalidated_report_count,
            "stale_report_count": self.stale_report_count,
            "superseded_report_count": self.superseded_report_count,
            "legacy_report_count": self.legacy_report_count,
            "malformed_report_count": self.malformed_report_count,
            "latest_valid_run_timestamp": self.latest_valid_run_timestamp,
            # Governance layer
            "candidate_count": self.candidate_count,
            "decision_status": self.decision_status,
            "latest_decision": self.latest_decision,
            "latest_application_event": self.latest_application_event,
            "governance_warnings": self.governance_warnings,
            "next_governance_action": self.next_governance_action,
            "identity_status": self.identity_status,
            "scientific_owner_id": self.scientific_owner_id,
        }


@dataclass
class CandidateState:
    """Read-only candidate information for a question."""
    candidate_id: str = ""
    status: str = "NONE"
    created_from_question: str = ""
    description: str = ""
    risk_level: str = ""
    has_validation: bool = False
    validation_count: int = 0
    latest_validation_decision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "created_from_question": self.created_from_question,
            "description": self.description,
            "risk_level": self.risk_level,
            "has_validation": self.has_validation,
            "validation_count": self.validation_count,
            "latest_validation_decision": self.latest_validation_decision,
        }
