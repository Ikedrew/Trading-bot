"""
Proposal Factory.

Creates governed ChangeProposals from research feedback/knowledge
that is proposal-eligible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.proposals.model import ChangeProposal, Candidate


class ProposalFactory:
    """
    Creates ChangeProposals from proposal-eligible research feedback.

    Only feedback with proposal_eligible=True should be passed here.
    The factory does NOT validate eligibility — that's the caller's job.
    """

    def from_feedback(self, feedback: dict[str, Any]) -> ChangeProposal:
        """Create a proposal from a research feedback artifact."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pid = f"prop_{feedback.get('question_id', 'unknown')}_{uuid.uuid4().hex[:6]}"

        return ChangeProposal(
            proposal_id=pid,
            source_feedback_ids=[feedback.get("feedback_id", "")],
            source_finding_ids=[feedback.get("source_finding_id", "")],
            source_knowledge_ids=[],
            system_area=feedback.get("system_area", ""),
            target_component=feedback.get("affected_component", ""),
            problem_statement=feedback.get("interpretation", ""),
            hypothesis=feedback.get("hypothesis", "") or f"Addressing {feedback.get('feedback_type', '')} in {feedback.get('system_area', '')} may improve system performance.",
            proposed_change="",  # To be filled by researcher/governance
            expected_effect=f"Improve {feedback.get('system_area', '').lower()} performance",
            validation_required=f"Compare baseline vs candidate on the affected population",
            created_at=now,
            universe_versions=feedback.get("universe_versions", {}),
            population_versions=feedback.get("population_versions", {}),
        )

    def create_candidate(self, proposal: ChangeProposal, description: str = "", config: dict[str, Any] | None = None) -> Candidate:
        """Create a candidate from an accepted proposal."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cid = f"cand_{proposal.proposal_id}_{uuid.uuid4().hex[:4]}"

        return Candidate(
            candidate_id=cid,
            proposal_id=proposal.proposal_id,
            candidate_version="1",
            description=description or proposal.hypothesis,
            configuration=config or {},
            created_at=now,
        )
