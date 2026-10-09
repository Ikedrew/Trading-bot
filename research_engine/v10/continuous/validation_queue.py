"""Governed dispatch queue for existing validation implementations.

This module does not validate candidates itself.  It verifies queue eligibility,
persists deterministic jobs, and invokes explicitly supplied adapters to the
existing validation/forward-validation implementations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


QUEUE_SCHEMA = "continuous_validation_queue_v1"
DEFAULT_VALIDATION_QUEUE_PATH = Path("data/research/continuous/validation_queue.json")
VALIDATION = "VALIDATION"
FORWARD_VALIDATION = "FORWARD_VALIDATION"
QUEUED = "QUEUED"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
WAITING_FOR_DATA = "WAITING_FOR_DATA"
TERMINAL = {COMPLETED, FAILED, BLOCKED, REVIEW_REQUIRED}


class ValidationQueueError(RuntimeError):
    """Queue authority is corrupt or a transition could not be persisted."""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass
class ValidationJob:
    job_id: str
    kind: str
    candidate_id: str
    policy_id: str
    treatment_hash: str
    validation_plan: dict[str, Any]
    source_snapshot_id: str
    source_cycle_id: str
    source_finding_version: str = ""
    source_hypothesis_id: str = ""
    status: str = QUEUED
    attempts: int = 0
    queued_at: str = ""
    output_validation_record: dict[str, Any] | None = None
    failure_reason: str | None = None
    review_reason: str | None = None
    transitions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationJob":
        fields = {name: deepcopy(value[name]) for name in cls.__dataclass_fields__ if name in value}
        job = cls(**fields)
        if job.kind not in {VALIDATION, FORWARD_VALIDATION} or job.status not in {
            QUEUED, RUNNING, COMPLETED, FAILED, BLOCKED, REVIEW_REQUIRED,
            WAITING_FOR_DATA,
        }:
            raise ValidationQueueError("VALIDATION_JOB_STATE_INVALID")
        if job.job_id != validation_job_id(job.kind, job.candidate_id, job.policy_id,
                                           job.treatment_hash, job.validation_plan,
                                           job.source_snapshot_id):
            raise ValidationQueueError("VALIDATION_JOB_IDENTITY_MISMATCH")
        return job


def validation_job_id(kind: str, candidate_id: str, policy_id: str,
                      treatment_hash: str, plan: Mapping[str, Any],
                      source_snapshot_id: str) -> str:
    material = {
        "kind": kind, "candidate_id": candidate_id, "policy_id": policy_id,
        "treatment_hash": treatment_hash, "validation_plan": dict(plan),
        "source_snapshot_id": source_snapshot_id,
    }
    return "VJOB-" + _digest(material)[:24].upper()


class ValidationQueueStore:
    """Atomic queue read model with deterministic job identities."""

    def __init__(self, path: Path | str = DEFAULT_VALIDATION_QUEUE_PATH):
        self.path = Path(path)
        self.jobs: dict[str, ValidationJob] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationQueueError("VALIDATION_QUEUE_UNREADABLE") from exc
        if not isinstance(value, Mapping) or value.get("schema") != QUEUE_SCHEMA:
            raise ValidationQueueError("VALIDATION_QUEUE_SCHEMA_INVALID")
        rows = value.get("jobs")
        if not isinstance(rows, list):
            raise ValidationQueueError("VALIDATION_QUEUE_JOBS_INVALID")
        for row in rows:
            job = ValidationJob.from_dict(row)
            if job.job_id in self.jobs:
                raise ValidationQueueError("VALIDATION_JOB_DUPLICATE")
            # A crash after marking RUNNING is safely resumable.
            if job.status == RUNNING:
                job.status = QUEUED
                job.failure_reason = "RECOVERED_INTERRUPTED_ATTEMPT"
            self.jobs[job.job_id] = job

    def save(self) -> None:
        payload = {"schema": QUEUE_SCHEMA,
                   "jobs": [self.jobs[key].to_dict() for key in sorted(self.jobs)]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise ValidationQueueError("VALIDATION_QUEUE_PERSISTENCE_FAILED") from exc

    def add(self, job: ValidationJob) -> ValidationJob:
        existing = self.jobs.get(job.job_id)
        if existing:
            if canonical_json(existing.to_dict()) != canonical_json(job.to_dict()):
                # State transitions differ from the pristine proposed job and are valid.
                identity_fields = ("kind", "candidate_id", "policy_id", "treatment_hash",
                                   "validation_plan", "source_snapshot_id")
                if any(getattr(existing, key) != getattr(job, key) for key in identity_fields):
                    raise ValidationQueueError("VALIDATION_JOB_IDENTITY_COLLISION")
            return existing
        self.jobs[job.job_id] = job
        self.save()
        return job

    def ordered(self) -> tuple[ValidationJob, ...]:
        return tuple(self.jobs[key] for key in sorted(self.jobs))


def _plan_problem(candidate: Any, plan: Mapping[str, Any]) -> str | None:
    if not candidate.target_population:
        return "TARGET_POPULATION_MISSING"
    if not candidate.changes or not candidate.policy_id:
        return "TREATMENT_DEFINITION_MISSING"
    minimum = plan.get("minimum_sample")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
        return "MINIMUM_SAMPLE_MISSING"
    if not plan.get("success_conditions") or not plan.get("failure_conditions"):
        return "SUCCESS_OR_FAILURE_CRITERIA_MISSING"
    fidelity = " ".join(str(key).lower() for key in plan.get("success_conditions", {}))
    fidelity += " " + str(plan.get("notes", "")).lower()
    if not any(token in fidelity for token in ("fidelity", "unchanged", "only ", "scope")):
        return "TREATMENT_FIDELITY_REQUIREMENT_MISSING"
    return None


def _treatment_identity_problem(candidate: Any, item: Mapping[str, Any]) -> str | None:
    if (item.get("policy_id") != candidate.policy_id or
            item.get("treatment_hash") != candidate.treatment_hash or
            not candidate.treatment_hash):
        return "TREATMENT_IDENTITY_MISMATCH"
    frozen_policy = candidate.changes.get("frozen_policy") if candidate.changes else None
    if not isinstance(frozen_policy, Mapping):
        return "FROZEN_POLICY_MISSING"
    expected = hashlib.sha256(canonical_json(dict(frozen_policy)).encode("utf-8")).hexdigest()
    if expected != candidate.treatment_hash:
        return "TREATMENT_HASH_MISMATCH"
    return None


def enqueue_validation_handoff(
    handoff: Iterable[Mapping[str, Any]], *, registry: OptimisationRegistry,
    store: ValidationQueueStore, snapshot_id: str, cycle_id: str,
) -> tuple[ValidationJob, ...]:
    """Verify Block 3 handoff and enqueue each candidate at most once."""
    created: list[ValidationJob] = []
    for item in sorted(handoff, key=lambda row: str(row.get("candidate_id", ""))):
        candidate_id = str(item.get("candidate_id") or "")
        candidate = registry.get_candidate(candidate_id)
        if candidate is None:
            raise ValidationQueueError("HANDOFF_CANDIDATE_NOT_IN_MODERN_REGISTRY:" + candidate_id)
        plan_obj = registry.get_plan(candidate_id)
        plan = dict(item.get("validation_plan") or (plan_obj.to_dict() if plan_obj else {}))
        job_id = validation_job_id(VALIDATION, candidate_id, candidate.policy_id,
                                   candidate.treatment_hash, plan, snapshot_id)
        status, reason = QUEUED, None
        if candidate.status != "PROPOSED":
            continue
        identity_problem = _treatment_identity_problem(candidate, item)
        if identity_problem:
            status, reason = BLOCKED, identity_problem
        else:
            reason = _plan_problem(candidate, plan)
            if reason:
                status = REVIEW_REQUIRED
        job = ValidationJob(
            job_id=job_id, kind=VALIDATION, candidate_id=candidate_id,
            policy_id=candidate.policy_id, treatment_hash=candidate.treatment_hash,
            validation_plan=plan, source_snapshot_id=snapshot_id,
            source_cycle_id=cycle_id,
            source_finding_version=str(item.get("source_finding_version") or ""),
            source_hypothesis_id=str(item.get("source_hypothesis_id") or ""),
            status=status, queued_at=str(plan.get("created_at") or ""),
            review_reason=reason if status == REVIEW_REQUIRED else None,
            failure_reason=reason if status == BLOCKED else None,
            transitions=[{"status": status, "at": str(plan.get("created_at") or ""),
                          "reason": reason}],
        )
        before = job_id in store.jobs
        persisted = store.add(job)
        if not before:
            created.append(persisted)
    return tuple(created)


def enqueue_forward_validation(candidate: Any, plan: Mapping[str, Any], *,
                               store: ValidationQueueStore, snapshot_id: str,
                               cycle_id: str, timestamp: str) -> ValidationJob:
    job = ValidationJob(
        job_id=validation_job_id(FORWARD_VALIDATION, candidate.candidate_id,
                                 candidate.policy_id, candidate.treatment_hash,
                                 plan, snapshot_id),
        kind=FORWARD_VALIDATION, candidate_id=candidate.candidate_id,
        policy_id=candidate.policy_id, treatment_hash=candidate.treatment_hash,
        validation_plan=dict(plan), source_snapshot_id=snapshot_id,
        source_cycle_id=cycle_id, status=QUEUED, queued_at=timestamp,
        transitions=[{"status": QUEUED, "at": timestamp}],
    )
    return store.add(job)


def process_validation_queue(
    *, store: ValidationQueueStore, registry: OptimisationRegistry,
    validation_executor: Callable[[Any, Mapping[str, Any], ValidationJob], Mapping[str, Any]] | None = None,
    forward_executor: Callable[[Any, Mapping[str, Any], ValidationJob], Mapping[str, Any]] | None = None,
    max_jobs: int = 1,
) -> dict[str, Any]:
    """Dispatch bounded work to existing-tool adapters and persist lifecycle state."""
    transitions: list[dict[str, Any]] = []
    remaining = max(0, int(max_jobs))
    for job in store.ordered():
        executor = validation_executor if job.kind == VALIDATION else forward_executor
        executor_retry = (
            job.status == BLOCKED
            and job.failure_reason == "VALIDATION_EXECUTOR_UNAVAILABLE"
            and executor is not None
        )
        if (remaining == 0
                or (job.status not in {QUEUED, WAITING_FOR_DATA}
                    and not executor_retry)):
            continue
        if executor is None:
            if (job.status == BLOCKED
                    and job.failure_reason == "VALIDATION_EXECUTOR_UNAVAILABLE"):
                continue
            job.status = BLOCKED
            job.failure_reason = "VALIDATION_EXECUTOR_UNAVAILABLE"
            job.transitions.append({"status": BLOCKED,
                                    "reason": job.failure_reason})
            store.save()
            transitions.append({"job_id": job.job_id,
                                "candidate_id": job.candidate_id,
                                "kind": job.kind, "status": BLOCKED,
                                "failure_reason": job.failure_reason})
            continue
        candidate = registry.get_candidate(job.candidate_id)
        if candidate is None:
            job.status = BLOCKED
            job.failure_reason = "QUEUED_CANDIDATE_MISSING:" + job.candidate_id
            job.transitions.append({"status": BLOCKED,
                                    "reason": job.failure_reason})
            store.save()
            transitions.append({"job_id": job.job_id,
                                "candidate_id": job.candidate_id,
                                "kind": job.kind, "status": BLOCKED,
                                "failure_reason": job.failure_reason})
            continue
        frozen_plan = registry.get_plan(job.candidate_id)
        if frozen_plan is None or canonical_json(frozen_plan.to_dict()) != canonical_json(
                job.validation_plan):
            job.status = BLOCKED
            job.failure_reason = "QUEUED_FROZEN_PLAN_IDENTITY_MISMATCH"
            job.transitions.append({"status": BLOCKED, "reason": job.failure_reason})
            store.save()
            transitions.append({"job_id": job.job_id,
                                "candidate_id": job.candidate_id,
                                "kind": job.kind, "status": BLOCKED,
                                "failure_reason": job.failure_reason})
            continue
        expected = "PROPOSED" if job.kind == VALIDATION else "VALIDATED"
        if candidate.status != expected:
            job.status = BLOCKED
            job.failure_reason = "CANDIDATE_STATE_INELIGIBLE:" + candidate.status
            store.save()
            continue
        prior_status = job.status
        prior_attempts = job.attempts
        prior_output = deepcopy(job.output_validation_record)
        prior_transition_count = len(job.transitions)
        job.status, job.attempts = RUNNING, job.attempts + 1
        job.transitions.append({"status": RUNNING, "attempt": job.attempts})
        store.save()
        remaining -= 1
        candidate_before = deepcopy(candidate)
        try:
            output = dict(executor(candidate, job.validation_plan, job))
            outcome = str(output.get("status") or "").upper()
            if outcome not in {
                "VALIDATED", "FORWARD_VALIDATED", "FAILED", "REJECTED",
                WAITING_FOR_DATA, BLOCKED, REVIEW_REQUIRED,
            }:
                raise ValidationQueueError("VALIDATION_EXECUTOR_OUTCOME_INVALID:" + outcome)
            output_snapshot = str(output.get("snapshot_id") or "")
            if job.kind == VALIDATION and output_snapshot != job.source_snapshot_id:
                raise ValidationQueueError("VALIDATION_OUTPUT_SNAPSHOT_MISMATCH")
            if job.kind == FORWARD_VALIDATION and (
                    not output_snapshot
                    or str(output.get("source_snapshot_id") or job.source_snapshot_id)
                    != job.source_snapshot_id):
                raise ValidationQueueError("FORWARD_VALIDATION_OUTPUT_SNAPSHOT_MISMATCH")
            if output.get("candidate_id") not in (None, job.candidate_id):
                raise ValidationQueueError("VALIDATION_OUTPUT_CANDIDATE_MISMATCH")
            if output.get("treatment_hash") not in (None, job.treatment_hash):
                raise ValidationQueueError("VALIDATION_OUTPUT_TREATMENT_MISMATCH")
            job.output_validation_record = output
            if outcome in {"VALIDATED", "FORWARD_VALIDATED"}:
                required = "VALIDATED" if job.kind == VALIDATION else "FORWARD_VALIDATED"
                if outcome != required:
                    raise ValidationQueueError("VALIDATION_STAGE_OUTCOME_MISMATCH")
                registry.update_candidate_status(job.candidate_id, outcome)
                job.status = COMPLETED
            else:
                reason = str(output.get("reason") or outcome)
                if outcome in {"FAILED", "REJECTED"}:
                    registry.update_candidate_status(job.candidate_id, "VALIDATION_FAILED")
                    job.status, job.failure_reason = FAILED, reason
                elif outcome == WAITING_FOR_DATA:
                    job.status, job.failure_reason = WAITING_FOR_DATA, reason
                elif outcome == REVIEW_REQUIRED:
                    job.status, job.review_reason = REVIEW_REQUIRED, reason
                else:
                    job.status, job.failure_reason = BLOCKED, reason
            # Re-checking an unchanged waiting population is intentionally a
            # no-op: duplicate cadence ticks do not manufacture attempts or
            # transition history while the producer has supplied no new data.
            if (job.status == WAITING_FOR_DATA and prior_status == WAITING_FOR_DATA
                    and prior_output is not None
                    and canonical_json(prior_output) == canonical_json(output)):
                job.attempts = prior_attempts
                job.transitions = job.transitions[:prior_transition_count]
                store.save()
                remaining += 1
                continue
            job.transitions.append({"status": job.status, "outcome": outcome})
            registry.save()
            store.save()
            transitions.append({"job_id": job.job_id, "candidate_id": job.candidate_id,
                                "kind": job.kind, "status": job.status,
                                "candidate_status": registry.get_candidate(job.candidate_id).status})
        except Exception as exc:
            # Candidate registry and queue are two existing authorities.  Undo
            # an in-memory/status transition if either persistence leg failed,
            # then persist one explicit failed attempt.  Never leave a VALIDATED
            # candidate behind a failed queue receipt.
            registry._candidates[job.candidate_id] = candidate_before
            job.status, job.failure_reason = BLOCKED, f"{type(exc).__name__}:{exc}"
            job.transitions.append({"status": BLOCKED, "reason": job.failure_reason})
            try:
                registry.save()
                store.save()
            except Exception as persist_exc:
                raise ValidationQueueError(
                    "VALIDATION_TRANSITION_RECOVERY_FAILED") from persist_exc
            transitions.append({"job_id": job.job_id, "candidate_id": job.candidate_id,
                                "kind": job.kind, "status": BLOCKED,
                                "failure_reason": job.failure_reason})
    shadow_eligibility = [
        {"candidate_id": candidate.candidate_id,
         "status": "ELIGIBLE_FOR_SHADOW_BIND",
         "policy_id": candidate.policy_id,
         "treatment_hash": candidate.treatment_hash,
         "live_approved": False}
        for candidate in sorted(registry.list_candidates(), key=lambda row: row.candidate_id)
        if candidate.status == "FORWARD_VALIDATED" and not candidate.shadow_binding
    ]
    return {"transitions": transitions, "shadow_eligibility": shadow_eligibility}


__all__ = [
    "BLOCKED", "COMPLETED", "FAILED", "FORWARD_VALIDATION", "QUEUED",
    "REVIEW_REQUIRED", "RUNNING", "VALIDATION", "WAITING_FOR_DATA", "ValidationJob",
    "ValidationQueueError", "ValidationQueueStore", "enqueue_forward_validation",
    "enqueue_validation_handoff", "process_validation_queue", "validation_job_id",
]
