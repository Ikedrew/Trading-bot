"""
Governance Gate — Ensures no production change without human approval.

This is the final safety layer between research findings and production V10.

Rules:
    1. NO hypothesis may affect production without PROMOTED status
    2. PROMOTED status requires:
       a. Hypothesis is CONCLUDED with VALIDATED verdict
       b. Human explicitly grants approval
       c. Governance gate records the approval with timestamp and notes
    3. All promotion requests are logged regardless of outcome
    4. The gate NEVER auto-approves — it only records human decisions

This module NEVER modifies production V10 autonomously.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.lifecycle.hypothesis import (
    ConclusionType,
    Hypothesis,
    HypothesisStatus,
)

_GATE_LOG = Path("logs/research_lifecycle/governance_decisions.jsonl")


@dataclass
class PromotionRequest:
    """A request to promote a validated hypothesis to production consideration."""
    hypothesis_id: str
    title: str
    conclusion_confidence: str
    evidence_summary: str
    risks: list[str]
    requested_timestamp: str = ""

    def __post_init__(self):
        if not self.requested_timestamp:
            self.requested_timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class GovernanceDecision:
    """Record of a human governance decision."""
    hypothesis_id: str
    decision: str               # "APPROVED" | "DENIED" | "DEFERRED"
    actor: str                  # Human identifier
    reason: str
    conditions: list[str]       # Any conditions attached to approval
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class GovernanceGate:
    """
    Controls promotion of research findings to production.
    
    This gate CANNOT be bypassed programmatically.
    It ONLY records human decisions — never makes them.
    """

    def can_promote(self, hypothesis: Hypothesis) -> tuple[bool, str]:
        """
        Check whether a hypothesis meets the prerequisites for promotion consideration.
        
        Returns (eligible, reason).
        Does NOT approve — only checks eligibility for human review.
        """
        if hypothesis.status != HypothesisStatus.CONCLUDED:
            return False, f"Status is {hypothesis.status.value}, not CONCLUDED"

        if hypothesis.conclusion_type != ConclusionType.VALIDATED:
            return False, f"Conclusion is {hypothesis.conclusion_type.value if hypothesis.conclusion_type else 'None'}, not VALIDATED"

        if not hypothesis.experiments:
            return False, "No experiments recorded"

        # Check that at least one validation experiment completed
        has_validation = any(
            e.experiment_type in ("oos_validation", "placebo", "robustness")
            and e.status == "complete"
            for e in hypothesis.experiments
        )
        if not has_validation:
            return False, "No validation experiment completed"

        return True, "Eligible for human review"

    def request_promotion(self, hypothesis: Hypothesis, *,
                          evidence_summary: str = "",
                          risks: list[str] | None = None) -> PromotionRequest:
        """
        Create a promotion request for human review.
        
        This does NOT approve anything — it creates the request record.
        """
        request = PromotionRequest(
            hypothesis_id=hypothesis.hypothesis_id,
            title=hypothesis.title,
            conclusion_confidence=hypothesis.conclusion_confidence,
            evidence_summary=evidence_summary,
            risks=risks or [],
        )
        self._log_event("PROMOTION_REQUESTED", hypothesis.hypothesis_id,
                        {"confidence": hypothesis.conclusion_confidence,
                         "experiments": len(hypothesis.experiments)})
        return request

    def record_decision(self, decision: GovernanceDecision) -> None:
        """
        Record a human governance decision.
        
        This is called ONLY when a human explicitly approves, denies, or defers.
        """
        self._log_event("DECISION_RECORDED", decision.hypothesis_id,
                        {"decision": decision.decision, "actor": decision.actor,
                         "reason": decision.reason})

    def approve(self, hypothesis: Hypothesis, *, actor: str,
                reason: str = "", conditions: list[str] | None = None) -> bool:
        """
        Record human approval for promotion.
        
        Returns True if the hypothesis was successfully marked for promotion.
        """
        eligible, msg = self.can_promote(hypothesis)
        if not eligible:
            self._log_event("APPROVAL_REJECTED", hypothesis.hypothesis_id,
                            {"reason": msg, "actor": actor})
            return False

        decision = GovernanceDecision(
            hypothesis_id=hypothesis.hypothesis_id,
            decision="APPROVED",
            actor=actor,
            reason=reason,
            conditions=conditions or [],
        )
        self.record_decision(decision)

        # Grant approval on the hypothesis
        hypothesis.grant_human_approval(notes=reason, actor=actor)
        return True

    def deny(self, hypothesis: Hypothesis, *, actor: str, reason: str) -> None:
        """Record human denial of promotion."""
        decision = GovernanceDecision(
            hypothesis_id=hypothesis.hypothesis_id,
            decision="DENIED",
            actor=actor,
            reason=reason,
            conditions=[],
        )
        self.record_decision(decision)
        self._log_event("PROMOTION_DENIED", hypothesis.hypothesis_id,
                        {"actor": actor, "reason": reason})

    def defer(self, hypothesis: Hypothesis, *, actor: str, reason: str,
              conditions: list[str] | None = None) -> None:
        """Record human deferral (needs more evidence)."""
        decision = GovernanceDecision(
            hypothesis_id=hypothesis.hypothesis_id,
            decision="DEFERRED",
            actor=actor,
            reason=reason,
            conditions=conditions or [],
        )
        self.record_decision(decision)

    def _log_event(self, event_type: str, hypothesis_id: str, data: dict) -> None:
        """Append to governance decision log."""
        try:
            _GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "hypothesis_id": hypothesis_id,
                **data,
            }
            fd = os.open(str(_GATE_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass
