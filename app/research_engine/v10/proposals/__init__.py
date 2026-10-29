"""
Finding → Proposal → Validation → Promotion.

Governed bridge from research understanding to candidate system changes.

NEVER deploys, activates, or modifies the trading bot directly.
Produces governed artifacts for human/governance decision.

Components:
    - model: ChangeProposal, Candidate, ValidationResult, PromotionDecision
    - generator: Creates proposals from feedback/knowledge
    - validator: Baseline vs Candidate comparison
    - promotion: Deterministic promotion eligibility gates
    - store: Persistent proposal/validation artifacts
"""

from research_engine.v10.proposals.model import (
    ChangeProposal,
    Candidate,
    ValidationResult,
    PromotionDecision,
    ValidationStatus,
    PromotionStatus,
)
from research_engine.v10.proposals.generator import ProposalFactory
from research_engine.v10.proposals.validator import ProposalValidator
from research_engine.v10.proposals.promotion import PromotionGate

__all__ = [
    "ChangeProposal",
    "Candidate",
    "ValidationResult",
    "PromotionDecision",
    "ValidationStatus",
    "PromotionStatus",
    "ProposalFactory",
    "ProposalValidator",
    "PromotionGate",
]
