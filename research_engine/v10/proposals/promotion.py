"""
Promotion Gate.

Deterministic promotion eligibility rules.
NEVER deploys, activates, or modifies the trading system.

A candidate becomes PROMOTION_ELIGIBLE only when ALL gates are satisfied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_engine.v10.proposals.model import (
    ChangeProposal,
    Candidate,
    PromotionDecision,
    PromotionStatus,
    ValidationResult,
    ValidationStatus,
)


class PromotionGate:
    """
    Evaluates deterministic promotion eligibility.

    Gates:
        1. Proposal exists and is governed
        2. Candidate is reproducible
        3. Validation completed
        4. Target improvement detected
        5. No critical regression
        6. Sample sufficiency
        7. Provenance is valid

    Result: PROMOTION_ELIGIBLE or NOT_ELIGIBLE with explicit blockers.
    """

    def evaluate(
        self,
        proposal: ChangeProposal,
        candidate: Candidate,
        validation: ValidationResult,
    ) -> PromotionDecision:
        """
        Evaluate all promotion gates.

        Returns PromotionDecision with eligibility and explicit blockers.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        blockers: list[str] = []
        satisfied: list[str] = []

        # Gate 1: Proposal is governed
        if proposal.proposal_id and proposal.governance_note:
            satisfied.append("PROPOSAL_GOVERNED")
        else:
            blockers.append("Proposal is not properly governed or identified")

        # Gate 2: Candidate is reproducible
        if candidate.candidate_id and candidate.proposal_id == proposal.proposal_id:
            satisfied.append("CANDIDATE_REPRODUCIBLE")
        else:
            blockers.append("Candidate identity is missing or mismatched")

        # Gate 3: Validation completed
        if validation.status == ValidationStatus.VALIDATED.value:
            satisfied.append("VALIDATION_COMPLETED")
        elif validation.status == ValidationStatus.PENDING.value:
            blockers.append("Validation has not been completed")
        elif validation.status == ValidationStatus.REJECTED.value:
            blockers.append(f"Validation REJECTED: {'; '.join(validation.limitations)}")
        elif validation.status == ValidationStatus.INCONCLUSIVE.value:
            blockers.append(f"Validation INCONCLUSIVE: {'; '.join(validation.limitations)}")
        elif validation.status == ValidationStatus.BLOCKED.value:
            blockers.append(f"Validation BLOCKED: {'; '.join(validation.limitations)}")

        # Gate 4: Improvement detected
        if validation.improvement_detected:
            satisfied.append("IMPROVEMENT_DETECTED")
        else:
            blockers.append("No target improvement detected")

        # Gate 5: No critical regression
        if not validation.regression_detected:
            satisfied.append("NO_CRITICAL_REGRESSION")
        else:
            blockers.append("Critical regression detected")

        # Gate 6: Sample sufficiency
        analytical = validation.sample_sizes.get("analytical_sample", 0)
        minimum = validation.sample_sizes.get("minimum_required", 20)
        if analytical >= minimum:
            satisfied.append("SAMPLE_SUFFICIENT")
        else:
            blockers.append(f"Insufficient sample: {analytical} < {minimum}")

        # Gate 7: Provenance valid
        if validation.universe_versions or validation.population_versions:
            satisfied.append("PROVENANCE_VALID")
        else:
            blockers.append("No provenance/version information in validation")

        # Decision
        eligible = len(blockers) == 0
        status = PromotionStatus.PROMOTION_ELIGIBLE.value if eligible else PromotionStatus.NOT_ELIGIBLE.value

        return PromotionDecision(
            candidate_id=candidate.candidate_id,
            proposal_id=proposal.proposal_id,
            validation_id=validation.validation_id,
            status=status,
            eligible=eligible,
            blockers=blockers,
            satisfied_gates=satisfied,
            decision_timestamp=now,
        )
