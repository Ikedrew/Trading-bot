"""
Hypothesis Model — First-class entity for the research lifecycle.

A Hypothesis tracks a claim about V10 behaviour through its complete
lifecycle from initial detection to governed conclusion.

State Machine:
    DETECTED → REGISTERED → TESTING → CHALLENGED → CONCLUDED
                                                       ├─ VALIDATED
                                                       ├─ REJECTED
                                                       └─ INCONCLUSIVE

    VALIDATED → PROMOTED (requires human approval gate)

Invariants:
    - Every state transition is recorded with timestamp, reason, and evidence
    - A hypothesis can never skip states
    - PROMOTED requires explicit human approval (governance gate)
    - No hypothesis affects production without PROMOTED status
    - All experiments and results are linked to the hypothesis
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class HypothesisStatus(str, Enum):
    """Lifecycle states for a research hypothesis."""
    DETECTED = "DETECTED"           # Anomaly or pattern identified from data
    REGISTERED = "REGISTERED"       # Formally entered into investigation registry
    TESTING = "TESTING"             # Primary experiment(s) executing
    CHALLENGED = "CHALLENGED"       # Validation/falsification experiments running
    CONCLUDED = "CONCLUDED"         # Final verdict reached (see conclusion_type)
    PROMOTED = "PROMOTED"           # Human-approved for production consideration


class ConclusionType(str, Enum):
    """Outcome of a concluded hypothesis."""
    VALIDATED = "VALIDATED"         # Evidence supports the claim
    REJECTED = "REJECTED"           # Evidence refutes the claim
    INCONCLUSIVE = "INCONCLUSIVE"   # Cannot determine with available data
    SUPERSEDED = "SUPERSEDED"       # Replaced by a better-formed hypothesis


class HypothesisCategory(str, Enum):
    """Domain category for the hypothesis."""
    PATTERN_SIGNAL = "PATTERN_SIGNAL"
    GEOMETRY_DEFECT = "GEOMETRY_DEFECT"
    GUARD_QUALITY = "GUARD_QUALITY"
    EXECUTION_LEAKAGE = "EXECUTION_LEAKAGE"
    REGIME_CONDITIONING = "REGIME_CONDITIONING"
    SCORE_MONOTONICITY = "SCORE_MONOTONICITY"
    STRATEGY_EDGE = "STRATEGY_EDGE"
    DIRECTION_INVERSION = "DIRECTION_INVERSION"
    STALENESS = "STALENESS"
    OTHER = "OTHER"


@dataclass
class StateTransition:
    """Record of a hypothesis state change."""
    from_status: str
    to_status: str
    timestamp: str
    reason: str
    evidence_ref: str = ""          # Link to experiment/finding that triggered transition
    actor: str = "system"           # "system" | "human" | experiment_id


@dataclass
class ExperimentRef:
    """Reference to an experiment executed for this hypothesis."""
    experiment_id: str
    experiment_type: str            # "primary", "oos_validation", "placebo", "robustness"
    status: str = "pending"         # "pending", "running", "complete", "failed"
    result_summary: str = ""
    result_path: str = ""           # Path to full result file
    timestamp: str = ""


@dataclass
class Hypothesis:
    """
    A research hypothesis tracked through its complete governed lifecycle.

    This is the primary entity in the research lifecycle system.
    It links detection → experiments → validation → conclusion.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    hypothesis_id: str = ""
    title: str = ""
    description: str = ""
    category: HypothesisCategory = HypothesisCategory.OTHER

    # ─── CLAIM ────────────────────────────────────────────────────────
    claim: str = ""                     # The specific testable assertion
    null_hypothesis: str = ""           # What we'd expect if the claim is false
    falsification_conditions: list[str] = field(default_factory=list)

    # ─── PROVENANCE ───────────────────────────────────────────────────
    source: str = ""                    # What triggered detection (baseline finding, etc.)
    source_finding_id: str = ""         # Link to the research finding that generated this
    detected_timestamp: str = ""
    population_description: str = ""    # Which observations this applies to

    # ─── LIFECYCLE STATE ──────────────────────────────────────────────
    status: HypothesisStatus = HypothesisStatus.DETECTED
    conclusion_type: ConclusionType | None = None
    conclusion_reason: str = ""
    conclusion_confidence: str = ""     # HIGH / MEDIUM / LOW / INSUFFICIENT

    # ─── EXPERIMENTS ──────────────────────────────────────────────────
    experiments: list[ExperimentRef] = field(default_factory=list)

    # ─── AUDIT TRAIL ──────────────────────────────────────────────────
    transitions: list[StateTransition] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # ─── GOVERNANCE ───────────────────────────────────────────────────
    human_approval_required: bool = True
    human_approval_granted: bool = False
    human_approval_timestamp: str = ""
    human_approval_notes: str = ""

    # ─── DISCOVERY CONTEXT ────────────────────────────────────────────
    discovery_bias_notes: str = ""      # How many things were tested before this
    multiple_testing_count: int = 0     # Approx number of variants examined
    bonferroni_threshold: float = 0.05  # Adjusted significance threshold

    def __post_init__(self):
        if not self.hypothesis_id:
            self.hypothesis_id = f"H-{uuid.uuid4().hex[:8]}"
        if not self.detected_timestamp:
            self.detected_timestamp = datetime.now(timezone.utc).isoformat()

    # ─── STATE MACHINE ────────────────────────────────────────────────

    _VALID_TRANSITIONS = {
        HypothesisStatus.DETECTED: {HypothesisStatus.REGISTERED},
        HypothesisStatus.REGISTERED: {HypothesisStatus.TESTING},
        HypothesisStatus.TESTING: {HypothesisStatus.CHALLENGED, HypothesisStatus.CONCLUDED},
        HypothesisStatus.CHALLENGED: {HypothesisStatus.CONCLUDED},
        HypothesisStatus.CONCLUDED: {HypothesisStatus.PROMOTED},
    }

    def transition(self, to_status: HypothesisStatus, *, reason: str,
                   evidence_ref: str = "", actor: str = "system") -> bool:
        """
        Attempt a state transition. Returns True if valid, False if rejected.

        Enforces:
        - Valid state machine transitions only
        - PROMOTED requires human_approval_granted = True
        - All transitions are recorded
        """
        if to_status not in self._VALID_TRANSITIONS.get(self.status, set()):
            return False

        if to_status == HypothesisStatus.PROMOTED and not self.human_approval_granted:
            return False

        transition = StateTransition(
            from_status=self.status.value,
            to_status=to_status.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            evidence_ref=evidence_ref,
            actor=actor,
        )
        self.transitions.append(transition)
        self.status = to_status
        return True

    def conclude(self, conclusion: ConclusionType, *, reason: str,
                 confidence: str = "MEDIUM", evidence_ref: str = "") -> bool:
        """Conclude the hypothesis with a formal verdict."""
        if self.status not in (HypothesisStatus.TESTING, HypothesisStatus.CHALLENGED):
            return False

        self.conclusion_type = conclusion
        self.conclusion_reason = reason
        self.conclusion_confidence = confidence

        return self.transition(
            HypothesisStatus.CONCLUDED,
            reason=f"{conclusion.value}: {reason}",
            evidence_ref=evidence_ref,
        )

    def grant_human_approval(self, *, notes: str = "", actor: str = "human") -> bool:
        """Grant human approval for promotion. Only valid for CONCLUDED/VALIDATED."""
        if self.status != HypothesisStatus.CONCLUDED:
            return False
        if self.conclusion_type != ConclusionType.VALIDATED:
            return False

        self.human_approval_granted = True
        self.human_approval_timestamp = datetime.now(timezone.utc).isoformat()
        self.human_approval_notes = notes
        return True

    def add_experiment(self, experiment_id: str, experiment_type: str) -> ExperimentRef:
        """Register a new experiment for this hypothesis."""
        ref = ExperimentRef(
            experiment_id=experiment_id,
            experiment_type=experiment_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.experiments.append(ref)
        return ref

    def update_experiment(self, experiment_id: str, *,
                          status: str = "", result_summary: str = "",
                          result_path: str = "") -> bool:
        """Update an experiment's status/result."""
        for exp in self.experiments:
            if exp.experiment_id == experiment_id:
                if status:
                    exp.status = status
                if result_summary:
                    exp.result_summary = result_summary
                if result_path:
                    exp.result_path = result_path
                return True
        return False

    # ─── SERIALISATION ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialise for persistence."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "claim": self.claim,
            "null_hypothesis": self.null_hypothesis,
            "falsification_conditions": self.falsification_conditions,
            "source": self.source,
            "source_finding_id": self.source_finding_id,
            "detected_timestamp": self.detected_timestamp,
            "population_description": self.population_description,
            "status": self.status.value,
            "conclusion_type": self.conclusion_type.value if self.conclusion_type else None,
            "conclusion_reason": self.conclusion_reason,
            "conclusion_confidence": self.conclusion_confidence,
            "experiments": [
                {"experiment_id": e.experiment_id, "experiment_type": e.experiment_type,
                 "status": e.status, "result_summary": e.result_summary,
                 "result_path": e.result_path, "timestamp": e.timestamp}
                for e in self.experiments
            ],
            "transitions": [
                {"from": t.from_status, "to": t.to_status, "timestamp": t.timestamp,
                 "reason": t.reason, "evidence_ref": t.evidence_ref, "actor": t.actor}
                for t in self.transitions
            ],
            "tags": self.tags,
            "human_approval_required": self.human_approval_required,
            "human_approval_granted": self.human_approval_granted,
            "human_approval_timestamp": self.human_approval_timestamp,
            "human_approval_notes": self.human_approval_notes,
            "discovery_bias_notes": self.discovery_bias_notes,
            "multiple_testing_count": self.multiple_testing_count,
            "bonferroni_threshold": self.bonferroni_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        """Deserialise from persistence."""
        h = cls(
            hypothesis_id=data.get("hypothesis_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            category=HypothesisCategory(data.get("category", "OTHER")),
            claim=data.get("claim", ""),
            null_hypothesis=data.get("null_hypothesis", ""),
            falsification_conditions=data.get("falsification_conditions", []),
            source=data.get("source", ""),
            source_finding_id=data.get("source_finding_id", ""),
            detected_timestamp=data.get("detected_timestamp", ""),
            population_description=data.get("population_description", ""),
            status=HypothesisStatus(data.get("status", "DETECTED")),
            conclusion_type=ConclusionType(data["conclusion_type"]) if data.get("conclusion_type") else None,
            conclusion_reason=data.get("conclusion_reason", ""),
            conclusion_confidence=data.get("conclusion_confidence", ""),
            tags=data.get("tags", []),
            human_approval_required=data.get("human_approval_required", True),
            human_approval_granted=data.get("human_approval_granted", False),
            human_approval_timestamp=data.get("human_approval_timestamp", ""),
            human_approval_notes=data.get("human_approval_notes", ""),
            discovery_bias_notes=data.get("discovery_bias_notes", ""),
            multiple_testing_count=data.get("multiple_testing_count", 0),
            bonferroni_threshold=data.get("bonferroni_threshold", 0.05),
        )
        # Restore experiments
        for e_data in data.get("experiments", []):
            ref = ExperimentRef(
                experiment_id=e_data.get("experiment_id", ""),
                experiment_type=e_data.get("experiment_type", ""),
                status=e_data.get("status", "pending"),
                result_summary=e_data.get("result_summary", ""),
                result_path=e_data.get("result_path", ""),
                timestamp=e_data.get("timestamp", ""),
            )
            h.experiments.append(ref)
        # Restore transitions
        for t_data in data.get("transitions", []):
            t = StateTransition(
                from_status=t_data.get("from", ""),
                to_status=t_data.get("to", ""),
                timestamp=t_data.get("timestamp", ""),
                reason=t_data.get("reason", ""),
                evidence_ref=t_data.get("evidence_ref", ""),
                actor=t_data.get("actor", "system"),
            )
            h.transitions.append(t)
        return h
