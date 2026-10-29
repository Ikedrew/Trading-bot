"""
Control Plane Data Models.

Defines the lifecycle, state, and configuration models for the research system.
These models are the control layer — they do NOT contain research analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════


class QuestionLifecycle(str, Enum):
    """Lifecycle state of a research question."""
    DISCOVERED = "DISCOVERED"      # Gap/anomaly identified by findings
    CANDIDATE = "CANDIDATE"        # Proposed as a new question
    VALIDATED = "VALIDATED"         # Passes deduplication + universe validation
    ACTIVE = "ACTIVE"              # Approved and in the question bank
    RUN = "RUN"                    # Has been executed at least once
    SUPERSEDED = "SUPERSEDED"      # Replaced by a better question
    MERGED = "MERGED"              # Combined into another question
    ARCHIVED = "ARCHIVED"          # No longer relevant


class FindingOutcome(str, Enum):
    """What happened after a question produced a finding."""
    RETAIN = "RETAIN"              # Finding is current and valid
    SUPERSEDE = "SUPERSEDE"        # Finding replaced by newer run
    INCONCLUSIVE = "INCONCLUSIVE"  # Insufficient evidence
    ANOMALY = "ANOMALY"            # Finding is anomalous
    EXCEPTIONAL = "EXCEPTIONAL"    # Finding shows exceptional behaviour
    GENERATE_FOLLOWUP = "GENERATE_FOLLOWUP"  # Finding spawned new question


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION PRODUCT INDEX
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class QuestionProductIndex:
    """Index entry pointing to an independent question product."""
    question_id: str
    title: str
    lifecycle: QuestionLifecycle
    angle_primary: str              # E.g., "EXECUTION", "DECISION+MARKET"
    last_run_id: str = ""
    last_run_timestamp: str = ""
    latest_finding_path: str = ""   # Path to latest.json
    history_count: int = 0
    finding_outcome: str = ""       # Latest finding's outcome
    blocked_reason: str = ""
    candidate_source: str = ""      # What generated this candidate (finding ID)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "lifecycle": self.lifecycle.value,
            "angle_primary": self.angle_primary,
            "last_run_id": self.last_run_id,
            "last_run_timestamp": self.last_run_timestamp,
            "latest_finding_path": self.latest_finding_path,
            "history_count": self.history_count,
            "finding_outcome": self.finding_outcome,
            "blocked_reason": self.blocked_reason,
            "candidate_source": self.candidate_source,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH RUN MANIFEST
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchRunManifest:
    """Complete manifest for one research run."""
    run_id: str
    timestamp: str
    engine_version: str
    question_bank_version: str
    questions_requested: int
    questions_executed: int
    questions_blocked: int
    questions_failed: int
    questions_inconclusive: int
    findings_generated: int
    anomalies_detected: int
    exceptional_views: int
    candidate_questions_generated: int
    population_versions: dict[str, str] = field(default_factory=dict)
    universe_versions: dict[str, str] = field(default_factory=dict)
    duration_seconds: float = 0.0
    executed_question_ids: list[str] = field(default_factory=list)
    blocked_question_ids: list[str] = field(default_factory=list)
    failed_question_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "engine_version": self.engine_version,
            "question_bank_version": self.question_bank_version,
            "questions_requested": self.questions_requested,
            "questions_executed": self.questions_executed,
            "questions_blocked": self.questions_blocked,
            "questions_failed": self.questions_failed,
            "questions_inconclusive": self.questions_inconclusive,
            "findings_generated": self.findings_generated,
            "anomalies_detected": self.anomalies_detected,
            "exceptional_views": self.exceptional_views,
            "candidate_questions_generated": self.candidate_questions_generated,
            "population_versions": self.population_versions,
            "universe_versions": self.universe_versions,
            "duration_seconds": self.duration_seconds,
            "executed_question_ids": self.executed_question_ids,
            "blocked_question_ids": self.blocked_question_ids,
            "failed_question_ids": self.failed_question_ids,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# GROWTH LIMITS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class GrowthLimits:
    """Configurable limits for controlled question development."""
    max_active_questions: int = 60
    max_new_questions_per_run: int = 5
    max_candidate_questions: int = 30
    require_evidence_for_new_question: bool = True
    require_universe_validation: bool = True
    require_deduplication: bool = True
    require_lineage: bool = True
    require_approval_for_activation: bool = True
    auto_activate_questions: bool = False
    auto_optimise: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_active_questions": self.max_active_questions,
            "max_new_questions_per_run": self.max_new_questions_per_run,
            "max_candidate_questions": self.max_candidate_questions,
            "require_evidence_for_new_question": self.require_evidence_for_new_question,
            "require_universe_validation": self.require_universe_validation,
            "require_deduplication": self.require_deduplication,
            "require_lineage": self.require_lineage,
            "require_approval_for_activation": self.require_approval_for_activation,
            "auto_activate_questions": self.auto_activate_questions,
            "auto_optimise": self.auto_optimise,
        }


# Default growth limits
DEFAULT_GROWTH_LIMITS = GrowthLimits()


# ═══════════════════════════════════════════════════════════════════════════════
# CONTROL PLANE STATE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class UniverseHealth:
    """Health snapshot for one universe."""
    universe_id: str
    status: str  # VALID, DEGRADED, INVALID
    record_count: int
    population_count: int
    last_build_timestamp: str
    content_hash: str


@dataclass
class ControlPlaneState:
    """
    The complete state of the research system.

    This is the persistent index that makes the system navigable.
    It does NOT contain research findings — only references to them.
    """
    engine_version: str = "1.0.0"
    last_updated: str = ""
    last_run_id: str = ""
    last_run_timestamp: str = ""

    # Universe health
    universes: list[UniverseHealth] = field(default_factory=list)

    # Population summary
    populations_valid: int = 0
    populations_empty: int = 0
    populations_degraded: int = 0

    # Question index
    questions: list[QuestionProductIndex] = field(default_factory=list)
    questions_active: int = 0
    questions_candidate: int = 0
    questions_blocked: int = 0
    questions_archived: int = 0
    questions_run: int = 0

    # Latest run summary
    latest_run: ResearchRunManifest | None = None

    # Growth limits
    growth_limits: GrowthLimits = field(default_factory=GrowthLimits)

    # Research development
    candidate_questions: list[dict[str, Any]] = field(default_factory=list)
    gaps_discovered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "last_updated": self.last_updated,
            "last_run_id": self.last_run_id,
            "last_run_timestamp": self.last_run_timestamp,
            "universes": [
                {"universe_id": u.universe_id, "status": u.status,
                 "record_count": u.record_count, "population_count": u.population_count,
                 "last_build_timestamp": u.last_build_timestamp}
                for u in self.universes
            ],
            "populations_valid": self.populations_valid,
            "populations_empty": self.populations_empty,
            "populations_degraded": self.populations_degraded,
            "questions_active": self.questions_active,
            "questions_candidate": self.questions_candidate,
            "questions_blocked": self.questions_blocked,
            "questions_archived": self.questions_archived,
            "questions_run": self.questions_run,
            "latest_run": self.latest_run.to_dict() if self.latest_run else None,
            "growth_limits": self.growth_limits.to_dict(),
            "candidate_questions_count": len(self.candidate_questions),
            "gaps_discovered": self.gaps_discovered,
        }
