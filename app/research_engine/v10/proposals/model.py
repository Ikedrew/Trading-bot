"""
Proposal / Validation / Promotion Models.

These are research governance artifacts.
They NEVER modify or deploy trading system changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationStatus(str, Enum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"


class PromotionStatus(str, Enum):
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    PENDING_VALIDATION = "PENDING_VALIDATION"
    BLOCKED = "BLOCKED"


@dataclass
class ChangeProposal:
    """
    A governed research hypothesis about a possible system change.
    NOT a trading instruction. NOT a deployment artifact.
    """
    proposal_id: str = ""
    source_feedback_ids: list[str] = field(default_factory=list)
    source_finding_ids: list[str] = field(default_factory=list)
    source_knowledge_ids: list[str] = field(default_factory=list)

    system_area: str = ""
    target_component: str = ""
    problem_statement: str = ""
    hypothesis: str = ""
    proposed_change: str = ""
    expected_effect: str = ""
    validation_required: str = ""

    governance_status: str = "PROPOSED"
    created_at: str = ""

    # Lineage
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    governance_note: str = (
        "This is a research proposal. It does not modify or authorize "
        "modification of the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source_feedback_ids": self.source_feedback_ids,
            "source_finding_ids": self.source_finding_ids,
            "source_knowledge_ids": self.source_knowledge_ids,
            "system_area": self.system_area,
            "target_component": self.target_component,
            "problem_statement": self.problem_statement,
            "hypothesis": self.hypothesis,
            "proposed_change": self.proposed_change,
            "expected_effect": self.expected_effect,
            "validation_required": self.validation_required,
            "governance_status": self.governance_status,
            "created_at": self.created_at,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "governance_note": self.governance_note,
        }


@dataclass
class Candidate:
    """
    The proposed version/configuration being tested.
    Distinct from baseline/production.

    A candidate must have explicit:
        - change_type (what kind of intervention)
        - configuration (declarative parameters)
        - target_metric (what success means)
        - provenance (where it came from)
    """
    candidate_id: str = ""
    proposal_id: str = ""
    candidate_version: str = "1"
    description: str = ""
    hypothesis: str = ""

    # Change definition
    change_type: str = ""  # POPULATION_FILTER, THRESHOLD_CHANGE, RISK_PARAMETER, POSITION_SIZING, CODE_CHANGE, UNSUPPORTED
    configuration: dict[str, Any] = field(default_factory=dict)

    # Experiment parameters
    target_metric: str = "mean_r"
    expected_effect: str = ""  # increase, decrease, reduce_variance
    minimum_improvement: float = 0.0
    critical_metrics: list[str] = field(default_factory=list)

    # Status
    design_status: str = "UNDEFINED"  # UNDEFINED, DESIGNED, EXPERIMENTABLE, BLOCKED

    # Lineage
    source_proposal_id: str = ""
    source_finding_ids: list[str] = field(default_factory=list)
    source_feedback_ids: list[str] = field(default_factory=list)
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    # Governance
    governance_status: str = "CANDIDATE"
    governance_note: str = (
        "This is a research candidate. It does not modify or "
        "authorize modification of the trading system."
    )
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "proposal_id": self.proposal_id,
            "candidate_version": self.candidate_version,
            "description": self.description,
            "hypothesis": self.hypothesis,
            "change_type": self.change_type,
            "configuration": self.configuration,
            "target_metric": self.target_metric,
            "expected_effect": self.expected_effect,
            "minimum_improvement": self.minimum_improvement,
            "critical_metrics": self.critical_metrics,
            "design_status": self.design_status,
            "source_proposal_id": self.source_proposal_id,
            "source_finding_ids": self.source_finding_ids,
            "source_feedback_ids": self.source_feedback_ids,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "governance_status": self.governance_status,
            "governance_note": self.governance_note,
            "created_at": self.created_at,
        }


@dataclass
class ValidationResult:
    """
    Baseline vs Candidate comparison result.
    Preserves full reproducibility context.
    """
    validation_id: str = ""
    proposal_id: str = ""
    candidate_id: str = ""

    # Identity
    baseline_identity: str = ""
    candidate_identity: str = ""

    # Versions
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)
    analysis_version: str = ""

    # Metrics
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    delta_metrics: dict[str, Any] = field(default_factory=dict)

    # Sample
    sample_sizes: dict[str, int] = field(default_factory=dict)

    # Result
    status: str = ValidationStatus.PENDING.value
    improvement_detected: bool = False
    regression_detected: bool = False
    target_metric: str = ""
    target_improvement: float = 0.0
    limitations: list[str] = field(default_factory=list)

    governance_note: str = (
        "This is a validation result. It does not modify the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "proposal_id": self.proposal_id,
            "candidate_id": self.candidate_id,
            "baseline_identity": self.baseline_identity,
            "candidate_identity": self.candidate_identity,
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
            "analysis_version": self.analysis_version,
            "baseline_metrics": self.baseline_metrics,
            "candidate_metrics": self.candidate_metrics,
            "delta_metrics": self.delta_metrics,
            "sample_sizes": self.sample_sizes,
            "status": self.status,
            "improvement_detected": self.improvement_detected,
            "regression_detected": self.regression_detected,
            "target_metric": self.target_metric,
            "target_improvement": self.target_improvement,
            "limitations": self.limitations,
            "governance_note": self.governance_note,
        }


@dataclass
class PromotionDecision:
    """
    Deterministic promotion eligibility decision.
    NEVER deploys or activates anything.
    """
    candidate_id: str = ""
    proposal_id: str = ""
    validation_id: str = ""
    status: str = PromotionStatus.PENDING_VALIDATION.value
    eligible: bool = False
    blockers: list[str] = field(default_factory=list)
    satisfied_gates: list[str] = field(default_factory=list)
    decision_timestamp: str = ""

    governance_note: str = (
        "Promotion eligibility is a research recommendation only. "
        "It does not deploy, activate, or modify the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "proposal_id": self.proposal_id,
            "validation_id": self.validation_id,
            "status": self.status,
            "eligible": self.eligible,
            "blockers": self.blockers,
            "satisfied_gates": self.satisfied_gates,
            "decision_timestamp": self.decision_timestamp,
            "governance_note": self.governance_note,
        }
