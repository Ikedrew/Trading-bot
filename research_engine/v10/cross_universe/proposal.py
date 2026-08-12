"""
Cross-Universe Research Proposals.

Produces structured governed research follow-up proposals from
cross-universe classifications.

A proposal is NOT an automatic recommendation to change the trading system.
It is a research question generated from observed cross-universe evidence.

Proposals:
    - Cannot execute trades
    - Cannot modify bot configuration
    - Cannot override research governance
    - Cannot claim unsupported conclusions
    - Must reference supporting universes and evidence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.cross_universe.classifier import (
    Classification,
    CrossUniverseClassification,
    DimensionClassification,
)


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH PROPOSAL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchProposal:
    """
    A governed research follow-up proposal.

    Generated from observed cross-universe evidence.
    Cannot directly modify trading behaviour.
    """
    proposal_type: str = ""  # RESEARCH_FOLLOW_UP, INVESTIGATION, DATA_GAP
    trigger: str = ""  # The classification that triggered this proposal
    statement: str = ""  # What should be investigated
    supporting_universes: list[str] = field(default_factory=list)
    evidence_entity_ids: list[str] = field(default_factory=list)
    required_analysis: str = ""  # What methodology should be applied
    governance_note: str = "This is a research proposal, not a trading recommendation."

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_type": self.proposal_type,
            "trigger": self.trigger,
            "statement": self.statement,
            "supporting_universes": self.supporting_universes,
            "evidence_entity_ids": self.evidence_entity_ids,
            "required_analysis": self.required_analysis,
            "governance_note": self.governance_note,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PROPOSAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════


class ProposalGenerator:
    """
    Generates research proposals from cross-universe classifications.

    Rules:
        - Every proposal has an explicit trigger (classification)
        - Every proposal references the supporting universes
        - No proposal claims unsupported conclusions
        - No proposal can directly alter trading behaviour

    Usage:
        generator = ProposalGenerator()
        proposals = generator.generate(classification)
    """

    def generate(self, classification: CrossUniverseClassification) -> list[ResearchProposal]:
        """
        Generate research proposals from a classification result.

        Returns zero or more proposals. Not every classification triggers a proposal.
        """
        proposals: list[ResearchProposal] = []

        for dc in classification.dimension_classifications:
            proposal = self._generate_from_dimension(dc, classification.entity_id)
            if proposal:
                proposals.append(proposal)

        return proposals

    def generate_batch(
        self, classifications: list[CrossUniverseClassification]
    ) -> list[ResearchProposal]:
        """Generate proposals from multiple classifications, deduplicating by trigger."""
        all_proposals: list[ResearchProposal] = []
        seen_triggers: set[str] = set()

        for cls in classifications:
            for proposal in self.generate(cls):
                if proposal.trigger not in seen_triggers:
                    all_proposals.append(proposal)
                    seen_triggers.add(proposal.trigger)
                else:
                    # Add entity_id to existing proposal's evidence
                    for existing in all_proposals:
                        if existing.trigger == proposal.trigger:
                            existing.evidence_entity_ids.extend(
                                proposal.evidence_entity_ids
                            )
                            break

        return all_proposals

    def _generate_from_dimension(
        self, dc: DimensionClassification, entity_id: str
    ) -> ResearchProposal | None:
        """Generate a proposal from a single dimension classification."""
        if dc.classification == Classification.DECISION_EXECUTE_RISK_BLOCKED:
            return ResearchProposal(
                proposal_type="INVESTIGATION",
                trigger=dc.classification,
                statement=(
                    "Decision produced EXECUTE but Risk assessment was BLOCKED. "
                    "Investigate whether the risk gate prevented execution despite "
                    "the pipeline's decision to trade."
                ),
                supporting_universes=["DECISION", "RISK"],
                evidence_entity_ids=[entity_id],
                required_analysis="Trace the decision funnel to determine terminal outcome.",
            )

        if dc.classification == Classification.CONTRADICTORY:
            if dc.dimension_name == "risk_vs_execution":
                return ResearchProposal(
                    proposal_type="INVESTIGATION",
                    trigger=f"{dc.classification}_{dc.dimension_name}",
                    statement=(
                        "Risk control blocked this trade but an execution record exists. "
                        "This may indicate a risk-gate bypass or data inconsistency."
                    ),
                    supporting_universes=["RISK", "EXECUTION"],
                    evidence_entity_ids=[entity_id],
                    required_analysis="Verify lifecycle integrity and risk-gate enforcement.",
                )

        if dc.classification == Classification.OUTCOME_MISSING:
            return ResearchProposal(
                proposal_type="DATA_GAP",
                trigger=dc.classification,
                statement=(
                    "Execution record exists but no corresponding outcome was found. "
                    "Investigate whether the trade is still open or whether outcome "
                    "data was not persisted."
                ),
                supporting_universes=["EXECUTION", "OUTCOME"],
                evidence_entity_ids=[entity_id],
                required_analysis="Check trade closure status and outcome persistence.",
            )

        if dc.classification == Classification.NO_EXECUTION:
            if dc.dimension_name == "risk_vs_execution":
                # Risk approved but no execution — could be normal (other gate blocked)
                # or could indicate execution failure
                return ResearchProposal(
                    proposal_type="RESEARCH_FOLLOW_UP",
                    trigger=f"{dc.classification}_{dc.dimension_name}",
                    statement=(
                        "Risk approved this evaluation but no execution occurred. "
                        "Determine whether another gate (entry, execution) prevented "
                        "the trade or whether an execution failure occurred."
                    ),
                    supporting_universes=["RISK", "EXECUTION", "DECISION"],
                    evidence_entity_ids=[entity_id],
                    required_analysis="Cross-reference with Decision terminal stage.",
                )

        # Most classifications do not generate proposals — they are informational
        return None
