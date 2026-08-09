"""
Question Development System — Controlled research growth.

Manages the lifecycle of candidate questions from discovery to activation.
Enforces growth limits to prevent uncontrolled question proliferation.

Lifecycle:
    DISCOVERED → CANDIDATE → VALIDATED → ACTIVE → RUN → FINDING
                                                      ├── RETAIN
                                                      ├── SUPERSEDE
                                                      ├── MERGE
                                                      ├── ARCHIVE
                                                      └── GENERATE_FOLLOWUP

Hard rule: auto_activate_questions = False by default.
A finding can PROPOSE a question. It cannot silently ADD one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.control_plane.models import (
    GrowthLimits,
    QuestionLifecycle,
    DEFAULT_GROWTH_LIMITS,
)

logger = logging.getLogger(__name__)


@dataclass
class CandidateQuestion:
    """A proposed research question discovered from findings."""
    candidate_id: str
    title: str
    research_intent: str
    source_finding_id: str  # Which finding generated this candidate
    source_question_id: str  # Which question's finding generated it
    discovered_at: str = ""
    lifecycle: QuestionLifecycle = QuestionLifecycle.DISCOVERED
    proposed_angles: list[str] = field(default_factory=list)
    evidence: str = ""  # Why this question should exist
    validation_status: str = ""  # PENDING, PASSED, FAILED
    rejection_reason: str = ""
    approved_by: str = ""  # Manual approval marker
    approved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "research_intent": self.research_intent,
            "source_finding_id": self.source_finding_id,
            "source_question_id": self.source_question_id,
            "discovered_at": self.discovered_at,
            "lifecycle": self.lifecycle.value,
            "proposed_angles": self.proposed_angles,
            "evidence": self.evidence,
            "validation_status": self.validation_status,
            "rejection_reason": self.rejection_reason,
        }


class QuestionDevelopmentSystem:
    """
    Manages controlled growth of the research question bank.

    Enforces:
        - Maximum active questions
        - Maximum new questions per run
        - Maximum candidate queue size
        - Evidence requirement for new questions
        - Deduplication requirement
        - Manual approval for activation
    """

    def __init__(self, limits: GrowthLimits | None = None):
        self._limits = limits or DEFAULT_GROWTH_LIMITS
        self._candidates: list[CandidateQuestion] = []
        self._active_count: int = 0

    @property
    def limits(self) -> GrowthLimits:
        return self._limits

    @property
    def candidates(self) -> list[CandidateQuestion]:
        return self._candidates

    def set_active_count(self, count: int) -> None:
        """Set the current number of active questions."""
        self._active_count = count

    def propose_candidate(
        self,
        candidate: CandidateQuestion,
    ) -> tuple[bool, str]:
        """
        Propose a new candidate question.

        Returns:
            (accepted, reason) — True if accepted into candidate queue.
        """
        # Check candidate queue limit
        if len(self._candidates) >= self._limits.max_candidate_questions:
            return False, (
                f"Candidate queue full ({len(self._candidates)}/"
                f"{self._limits.max_candidate_questions})"
            )

        # Check evidence requirement
        if self._limits.require_evidence_for_new_question and not candidate.evidence:
            return False, "Evidence required for new question (growth limit)"

        # Check lineage requirement
        if self._limits.require_lineage and not candidate.source_finding_id:
            return False, "Source finding lineage required (growth limit)"

        # Check deduplication
        if self._limits.require_deduplication:
            for existing in self._candidates:
                if existing.title.lower() == candidate.title.lower():
                    return False, f"Duplicate of existing candidate: {existing.candidate_id}"

        # Accept
        candidate.lifecycle = QuestionLifecycle.CANDIDATE
        if not candidate.discovered_at:
            candidate.discovered_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        self._candidates.append(candidate)
        return True, "Accepted into candidate queue"

    def validate_candidate(self, candidate_id: str) -> tuple[bool, str]:
        """
        Validate a candidate question (check universe requirements exist).

        Returns:
            (valid, reason)
        """
        candidate = self._get_candidate(candidate_id)
        if candidate is None:
            return False, f"Candidate {candidate_id} not found"

        # Universe validation
        if self._limits.require_universe_validation and not candidate.proposed_angles:
            candidate.validation_status = "FAILED"
            candidate.rejection_reason = "No proposed angles/universes defined"
            return False, "No proposed angles defined"

        candidate.validation_status = "PASSED"
        candidate.lifecycle = QuestionLifecycle.VALIDATED
        return True, "Validation passed"

    def activate_candidate(
        self, candidate_id: str, approved_by: str = ""
    ) -> tuple[bool, str]:
        """
        Activate a validated candidate question into the bank.

        Requires manual approval unless auto_activate_questions is True.

        Returns:
            (activated, reason)
        """
        candidate = self._get_candidate(candidate_id)
        if candidate is None:
            return False, f"Candidate {candidate_id} not found"

        if candidate.lifecycle != QuestionLifecycle.VALIDATED:
            return False, f"Candidate must be VALIDATED first (current: {candidate.lifecycle.value})"

        # Check approval requirement
        if self._limits.require_approval_for_activation:
            if not self._limits.auto_activate_questions and not approved_by:
                return False, "Manual approval required (auto_activate=False)"

        # Check active question limit
        if self._active_count >= self._limits.max_active_questions:
            return False, (
                f"Active question limit reached ({self._active_count}/"
                f"{self._limits.max_active_questions})"
            )

        candidate.lifecycle = QuestionLifecycle.ACTIVE
        candidate.approved_by = approved_by
        candidate.approved_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._active_count += 1
        return True, "Activated"

    def get_pending_candidates(self) -> list[CandidateQuestion]:
        """Get candidates awaiting validation or approval."""
        return [
            c for c in self._candidates
            if c.lifecycle in (QuestionLifecycle.CANDIDATE, QuestionLifecycle.VALIDATED)
        ]

    def _get_candidate(self, candidate_id: str) -> CandidateQuestion | None:
        for c in self._candidates:
            if c.candidate_id == candidate_id:
                return c
        return None
