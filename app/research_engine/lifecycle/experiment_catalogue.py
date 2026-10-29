"""
Experiment Catalogue — Permanent governed registry of all research experiments.

Every research experiment is uniquely identified, persisted, traced, and searchable.
Once an experiment reaches a terminal status (COMPLETED/FAILED/CANCELLED), its core
execution identity becomes immutable.

Storage:
    logs/research_lifecycle/experiment_registry.json — current state
    logs/research_lifecycle/audit_log.jsonl — shared with hypothesis registry

Answers:
    "What experiments have been run?"
    "What exactly did each experiment test?"
    "What data did it use?"
    "What happened?"
    "What did the research system conclude?"
    "Where is the evidence?"

This module NEVER modifies production V10.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT LIFECYCLE STATES
# ═══════════════════════════════════════════════════════════════════════════════

class ExperimentLifecycle(str, Enum):
    """Execution lifecycle of an experiment (distinct from Hypothesis lifecycle)."""
    REGISTERED = "REGISTERED"       # Defined and persisted, not yet started
    QUEUED = "QUEUED"               # Waiting for resources/scheduling
    RUNNING = "RUNNING"             # Actively executing
    COMPLETED = "COMPLETED"         # Finished successfully with result
    FAILED = "FAILED"               # Execution failed (error)
    CANCELLED = "CANCELLED"         # Manually stopped before completion
    BLOCKED = "BLOCKED"             # Cannot proceed (missing data/dependency)


_TERMINAL_STATES = {ExperimentLifecycle.COMPLETED, ExperimentLifecycle.FAILED, ExperimentLifecycle.CANCELLED}

_VALID_TRANSITIONS = {
    ExperimentLifecycle.REGISTERED: {ExperimentLifecycle.QUEUED, ExperimentLifecycle.RUNNING, ExperimentLifecycle.CANCELLED, ExperimentLifecycle.BLOCKED},
    ExperimentLifecycle.QUEUED: {ExperimentLifecycle.RUNNING, ExperimentLifecycle.CANCELLED, ExperimentLifecycle.BLOCKED},
    ExperimentLifecycle.RUNNING: {ExperimentLifecycle.COMPLETED, ExperimentLifecycle.FAILED, ExperimentLifecycle.CANCELLED},
    ExperimentLifecycle.BLOCKED: {ExperimentLifecycle.QUEUED, ExperimentLifecycle.RUNNING, ExperimentLifecycle.CANCELLED},
    # Terminal states have no outgoing transitions
    ExperimentLifecycle.COMPLETED: set(),
    ExperimentLifecycle.FAILED: set(),
    ExperimentLifecycle.CANCELLED: set(),
}


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT RECORD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExperimentRecord:
    """
    Permanent record of a research experiment.
    
    Immutability contract:
    - Once status reaches COMPLETED/FAILED/CANCELLED, core identity fields
      (definition, dataset_fingerprint, parameters, timestamps) are frozen.
    - Amendments are recorded separately, never overwrite originals.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    experiment_id: str = ""
    version: int = 1
    title: str = ""
    description: str = ""
    experiment_type: str = ""           # From ExperimentType enum

    # ─── RELATIONSHIPS ────────────────────────────────────────────────
    hypothesis_id: str = ""
    research_question_id: str = ""
    parent_experiment_id: str = ""      # If this is a follow-up
    supersedes_experiment_id: str = ""  # If this replaces an earlier experiment
    related_experiment_ids: list[str] = field(default_factory=list)

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    status: ExperimentLifecycle = ExperimentLifecycle.REGISTERED
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    # ─── DATASET PROVENANCE ───────────────────────────────────────────
    dataset_id: str = ""
    dataset_version: str = ""
    dataset_fingerprint: dict[str, Any] = field(default_factory=dict)
    observation_count: int = 0
    population: str = ""
    filters_applied: list[str] = field(default_factory=list)

    # ─── EXPERIMENT DEFINITION ────────────────────────────────────────
    definition: dict[str, Any] = field(default_factory=dict)  # Full ExperimentDefinition serialised
    parameters: dict[str, Any] = field(default_factory=dict)
    control_description: str = ""
    treatment_description: str = ""
    null_hypothesis: str = ""

    # ─── EXECUTION METADATA ───────────────────────────────────────────
    runner_version: str = "lifecycle_v1"
    research_engine_version: str = "1.0.0"

    # ─── RESULT SUMMARY ──────────────────────────────────────────────
    result_summary: dict[str, Any] = field(default_factory=dict)
    validation_summary: dict[str, Any] = field(default_factory=dict)
    oos_summary: dict[str, Any] = field(default_factory=dict)
    placebo_summary: dict[str, Any] = field(default_factory=dict)
    statistical_summary: dict[str, Any] = field(default_factory=dict)

    # ─── CONCLUSION ───────────────────────────────────────────────────
    conclusion: str = ""                # VALIDATED / REJECTED / INCONCLUSIVE / ""
    confidence: str = ""                # HIGH / MEDIUM / LOW / INSUFFICIENT
    evidence_maturity: str = ""         # From evidence_maturity.py
    decision_status: str = ""           # From evidence_maturity.py assess_decision
    classification: str = ""            # GREEN / AMBER / RED

    # ─── ARTEFACTS ────────────────────────────────────────────────────
    report_path: str = ""
    result_path: str = ""
    audit_log_reference: str = ""

    # ─── GOVERNANCE ───────────────────────────────────────────────────
    promotion_eligible: bool = False
    human_approval_required: bool = True
    human_approval_status: str = ""     # PENDING / APPROVED / DENIED / N/A
    approved_by: str = ""
    approved_at: str = ""

    # ─── AMENDMENTS ───────────────────────────────────────────────────
    amendments: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.experiment_id:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d")
            self.experiment_id = f"EXP-{ts}-{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATES

    @property
    def is_immutable(self) -> bool:
        return self.is_terminal

    def transition(self, to_status: ExperimentLifecycle, *, reason: str = "") -> bool:
        """Attempt lifecycle transition. Returns False if invalid."""
        if to_status not in _VALID_TRANSITIONS.get(self.status, set()):
            return False
        self.status = to_status
        if to_status == ExperimentLifecycle.RUNNING and not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()
        if to_status in _TERMINAL_STATES and not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat()
        return True

    def amend(self, *, field_name: str, old_value: Any, new_value: Any, reason: str, actor: str = "system"):
        """Record a non-destructive amendment (for non-core fields on non-terminal experiments)."""
        if self.is_immutable and field_name in (
            "definition", "parameters", "dataset_fingerprint", "dataset_id",
            "experiment_type", "hypothesis_id", "created_at", "started_at",
            "completed_at", "observation_count", "population",
        ):
            return  # Silently reject mutation of immutable fields
        self.amendments.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "field": field_name,
            "old_value": str(old_value)[:200],
            "new_value": str(new_value)[:200],
            "reason": reason,
            "actor": actor,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "experiment_type": self.experiment_type,
            "hypothesis_id": self.hypothesis_id,
            "research_question_id": self.research_question_id,
            "parent_experiment_id": self.parent_experiment_id,
            "supersedes_experiment_id": self.supersedes_experiment_id,
            "related_experiment_ids": self.related_experiment_ids,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_fingerprint": self.dataset_fingerprint,
            "observation_count": self.observation_count,
            "population": self.population,
            "filters_applied": self.filters_applied,
            "definition": self.definition,
            "parameters": self.parameters,
            "control_description": self.control_description,
            "treatment_description": self.treatment_description,
            "null_hypothesis": self.null_hypothesis,
            "runner_version": self.runner_version,
            "research_engine_version": self.research_engine_version,
            "result_summary": self.result_summary,
            "validation_summary": self.validation_summary,
            "oos_summary": self.oos_summary,
            "placebo_summary": self.placebo_summary,
            "statistical_summary": self.statistical_summary,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "evidence_maturity": self.evidence_maturity,
            "decision_status": self.decision_status,
            "classification": self.classification,
            "report_path": self.report_path,
            "result_path": self.result_path,
            "audit_log_reference": self.audit_log_reference,
            "promotion_eligible": self.promotion_eligible,
            "human_approval_required": self.human_approval_required,
            "human_approval_status": self.human_approval_status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "amendments": self.amendments,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperimentRecord":
        return cls(
            experiment_id=data.get("experiment_id", ""),
            version=data.get("version", 1),
            title=data.get("title", ""),
            description=data.get("description", ""),
            experiment_type=data.get("experiment_type", ""),
            hypothesis_id=data.get("hypothesis_id", ""),
            research_question_id=data.get("research_question_id", ""),
            parent_experiment_id=data.get("parent_experiment_id", ""),
            supersedes_experiment_id=data.get("supersedes_experiment_id", ""),
            related_experiment_ids=data.get("related_experiment_ids", []),
            status=ExperimentLifecycle(data.get("status", "REGISTERED")),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            dataset_id=data.get("dataset_id", ""),
            dataset_version=data.get("dataset_version", ""),
            dataset_fingerprint=data.get("dataset_fingerprint", {}),
            observation_count=data.get("observation_count", 0),
            population=data.get("population", ""),
            filters_applied=data.get("filters_applied", []),
            definition=data.get("definition", {}),
            parameters=data.get("parameters", {}),
            control_description=data.get("control_description", ""),
            treatment_description=data.get("treatment_description", ""),
            null_hypothesis=data.get("null_hypothesis", ""),
            runner_version=data.get("runner_version", "lifecycle_v1"),
            research_engine_version=data.get("research_engine_version", "1.0.0"),
            result_summary=data.get("result_summary", {}),
            validation_summary=data.get("validation_summary", {}),
            oos_summary=data.get("oos_summary", {}),
            placebo_summary=data.get("placebo_summary", {}),
            statistical_summary=data.get("statistical_summary", {}),
            conclusion=data.get("conclusion", ""),
            confidence=data.get("confidence", ""),
            evidence_maturity=data.get("evidence_maturity", ""),
            decision_status=data.get("decision_status", ""),
            classification=data.get("classification", ""),
            report_path=data.get("report_path", ""),
            result_path=data.get("result_path", ""),
            audit_log_reference=data.get("audit_log_reference", ""),
            promotion_eligible=data.get("promotion_eligible", False),
            human_approval_required=data.get("human_approval_required", True),
            human_approval_status=data.get("human_approval_status", ""),
            approved_by=data.get("approved_by", ""),
            approved_at=data.get("approved_at", ""),
            amendments=data.get("amendments", []),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT CATALOGUE
# ═══════════════════════════════════════════════════════════════════════════════

_CATALOGUE_DIR = Path("logs/research_lifecycle")
_CATALOGUE_FILE = _CATALOGUE_DIR / "experiment_registry.json"
_AUDIT_LOG = _CATALOGUE_DIR / "audit_log.jsonl"


class ExperimentCatalogue:
    """
    Permanent governed registry of all research experiments.
    
    Provides:
    - CRUD with immutability enforcement
    - Search/query across all dimensions
    - Persistence across restarts
    - Audit trail for all mutations
    - Catalogue summary for Research Command Center
    """

    def __init__(self):
        self._experiments: dict[str, ExperimentRecord] = {}
        self._load()

    # ─── REGISTRATION ─────────────────────────────────────────────────

    def register(self, record: ExperimentRecord) -> str:
        """Register a new experiment. Returns experiment_id."""
        if record.experiment_id in self._experiments:
            raise ValueError(f"Experiment {record.experiment_id} already exists")
        self._experiments[record.experiment_id] = record
        self._audit("EXPERIMENT_REGISTERED", record.experiment_id,
                    {"title": record.title, "hypothesis": record.hypothesis_id,
                     "type": record.experiment_type})
        self._save()
        return record.experiment_id

    # ─── RETRIEVAL ────────────────────────────────────────────────────

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        return self._experiments.get(experiment_id)

    def all(self) -> list[ExperimentRecord]:
        return list(self._experiments.values())

    # ─── LIFECYCLE TRANSITIONS ────────────────────────────────────────

    def start(self, experiment_id: str, *, reason: str = "") -> bool:
        """Transition to RUNNING."""
        rec = self._experiments.get(experiment_id)
        if not rec:
            return False
        if rec.transition(ExperimentLifecycle.RUNNING, reason=reason):
            self._audit("EXPERIMENT_STARTED", experiment_id, {"reason": reason})
            self._save()
            return True
        return False

    def complete(self, experiment_id: str, *, result_summary: dict | None = None,
                 conclusion: str = "", classification: str = "",
                 report_path: str = "", reason: str = "") -> bool:
        """Transition to COMPLETED with result."""
        rec = self._experiments.get(experiment_id)
        if not rec:
            return False
        if rec.transition(ExperimentLifecycle.COMPLETED, reason=reason):
            if result_summary:
                rec.result_summary = result_summary
            if conclusion:
                rec.conclusion = conclusion
            if classification:
                rec.classification = classification
            if report_path:
                rec.report_path = report_path
            self._audit("EXPERIMENT_COMPLETED", experiment_id,
                        {"conclusion": conclusion, "classification": classification})
            self._save()
            return True
        return False

    def fail(self, experiment_id: str, *, reason: str = "") -> bool:
        """Transition to FAILED."""
        rec = self._experiments.get(experiment_id)
        if not rec:
            return False
        if rec.transition(ExperimentLifecycle.FAILED, reason=reason):
            self._audit("EXPERIMENT_FAILED", experiment_id, {"reason": reason})
            self._save()
            return True
        return False

    def cancel(self, experiment_id: str, *, reason: str = "") -> bool:
        """Transition to CANCELLED."""
        rec = self._experiments.get(experiment_id)
        if not rec:
            return False
        if rec.transition(ExperimentLifecycle.CANCELLED, reason=reason):
            self._audit("EXPERIMENT_CANCELLED", experiment_id, {"reason": reason})
            self._save()
            return True
        return False

    # ─── UPDATE (non-immutable fields only) ───────────────────────────

    def update_result(self, experiment_id: str, **fields) -> bool:
        """Update result/validation/conclusion fields. Blocked on terminal+immutable core."""
        rec = self._experiments.get(experiment_id)
        if not rec:
            return False
        allowed_fields = {"result_summary", "validation_summary", "oos_summary",
                          "placebo_summary", "statistical_summary", "conclusion",
                          "confidence", "evidence_maturity", "decision_status",
                          "classification", "report_path", "result_path",
                          "promotion_eligible", "human_approval_status",
                          "approved_by", "approved_at", "duration_seconds"}
        for key, value in fields.items():
            if key in allowed_fields:
                setattr(rec, key, value)
        self._save()
        return True

    # ─── SEARCH / QUERY ───────────────────────────────────────────────

    def find_by_hypothesis(self, hypothesis_id: str) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values() if r.hypothesis_id == hypothesis_id]

    def find_by_type(self, experiment_type: str) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values() if r.experiment_type == experiment_type]

    def find_by_status(self, status: ExperimentLifecycle) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values() if r.status == status]

    def find_by_dataset(self, dataset_id: str) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values() if r.dataset_id == dataset_id]

    def find_by_fingerprint(self, content_hash: str) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values()
                if r.dataset_fingerprint.get("content_hash") == content_hash]

    def find_by_date_range(self, start: str, end: str) -> list[ExperimentRecord]:
        return [r for r in self._experiments.values()
                if start <= (r.created_at or "") <= end]

    def get_latest(self, n: int = 10) -> list[ExperimentRecord]:
        return sorted(self._experiments.values(),
                      key=lambda r: r.created_at or "", reverse=True)[:n]

    def get_history(self, experiment_id: str) -> list[ExperimentRecord]:
        """Get all versions/related experiments in a chain."""
        chain = []
        current = self.get(experiment_id)
        while current:
            chain.append(current)
            parent_id = current.parent_experiment_id
            current = self.get(parent_id) if parent_id else None
        return list(reversed(chain))

    # ─── CATALOGUE SUMMARY ────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Summary for Research Command Center consumption."""
        by_status = {}
        by_type = {}
        by_hypothesis = {}
        for r in self._experiments.values():
            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1
            by_type[r.experiment_type] = by_type.get(r.experiment_type, 0) + 1
            by_hypothesis.setdefault(r.hypothesis_id, []).append(r.experiment_id)

        return {
            "total_experiments": len(self._experiments),
            "by_status": by_status,
            "by_type": by_type,
            "by_hypothesis": {k: len(v) for k, v in by_hypothesis.items()},
            "recent": [r.experiment_id for r in self.get_latest(5)],
        }

    def generate_catalogue_report(self) -> str:
        """Generate human-readable catalogue markdown."""
        lines = ["# Research Experiment Catalogue", ""]
        lines.append(f"**Total experiments**: {len(self._experiments)}")
        lines.append(f"**Generated**: {datetime.now(timezone.utc).isoformat()}")
        lines.append("")
        lines.append("| ID | Hypothesis | Type | Population | N | Result | Status |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in sorted(self._experiments.values(), key=lambda x: x.created_at or "", reverse=True):
            n = r.observation_count or r.result_summary.get("n", "")
            conclusion = r.conclusion or r.classification or ""
            lines.append(f"| {r.experiment_id} | {r.hypothesis_id} | {r.experiment_type} | "
                         f"{r.population[:20]} | {n} | {conclusion} | {r.status.value} |")
        lines.append("")
        return "\n".join(lines)

    # ─── PERSISTENCE ──────────────────────────────────────────────────

    def _save(self) -> None:
        _CATALOGUE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "experiments": {eid: r.to_dict() for eid, r in self._experiments.items()},
        }
        tmp = _CATALOGUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(_CATALOGUE_FILE)

    def _load(self) -> None:
        if not _CATALOGUE_FILE.exists():
            return
        try:
            data = json.loads(_CATALOGUE_FILE.read_text(encoding="utf-8"))
            for eid, rec_data in data.get("experiments", {}).items():
                self._experiments[eid] = ExperimentRecord.from_dict(rec_data)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # Start fresh on corruption — existing audit log preserved

    def _audit(self, event: str, experiment_id: str, data: dict | None = None) -> None:
        try:
            _CATALOGUE_DIR.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "experiment_id": experiment_id,
                **(data or {}),
            }
            fd = os.open(str(_AUDIT_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass
