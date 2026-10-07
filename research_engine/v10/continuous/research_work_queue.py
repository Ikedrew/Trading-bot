"""Governed deferred execution for expensive canonical research evaluators.

The queue is an execution authority only.  Scientific status remains owned by
canonical question results; queue state says whether an affected result is
fresh, retained while recomputation is pending, or failed operationally.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json


QUEUE_SCHEMA = "governed_research_work_queue_v1"
POLICY_ID = "research_execution_policy_v1"
FAST = "FAST"
DEEP = "DEEP"
PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
SUPERSEDED = "SUPERSEDED"
ACTIVE = {PENDING, RUNNING}
TERMINAL = {COMPLETED, FAILED, SUPERSEDED}
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_DEEP_RUNTIME_SECONDS = 900.0
DEFAULT_DEEP_WORK_COOLDOWN_SECONDS = 300.0
DEFAULT_MAX_PENDING_DEEP_JOBS = 32


class ResearchWorkQueueError(RuntimeError):
    """The governed work queue is corrupt or a transition is invalid."""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluator_key(question: Any) -> str:
    module = str(getattr(question, "runner_module", "") or "")
    function = str(getattr(question, "runner_function", "") or "")
    return module + "." + function if module and function else ""


@dataclass(frozen=True)
class EvaluatorExecutionMetadata:
    measured_runtime_seconds: float | None = None
    prior_peak_memory_bytes: int | None = None
    population_wide_scan: bool = False
    inline_safe: bool = True
    latest_scope_supersedes: bool = False


# Stage A measurements are operational evidence, not scientific thresholds.
# The explicit evaluator metadata is authoritative and policy-versioned.
MEASURED_EVALUATORS: dict[str, EvaluatorExecutionMetadata] = {
    "research_engine.experiments.dataset_suitability.run_g1":
        EvaluatorExecutionMetadata(
            measured_runtime_seconds=3025.07,
            prior_peak_memory_bytes=4_680_000_000,
            population_wide_scan=True,
            inline_safe=False,
            latest_scope_supersedes=True,
        ),
    "research_engine.experiments.execution_stability.run_x6":
        EvaluatorExecutionMetadata(measured_runtime_seconds=278.54),
    "research_engine.experiments.lineage_coverage.run_g2":
        EvaluatorExecutionMetadata(measured_runtime_seconds=149.43),
    "research_engine.experiments.probability_of_ruin.run_probability_of_ruin":
        EvaluatorExecutionMetadata(measured_runtime_seconds=130.51),
    "research_engine.experiments.component_reward.run":
        EvaluatorExecutionMetadata(measured_runtime_seconds=120.58),
}


@dataclass(frozen=True)
class ResearchExecutionPolicy:
    policy_id: str = POLICY_ID
    deep_runtime_seconds: float = DEFAULT_DEEP_RUNTIME_SECONDS
    deep_work_cooldown_seconds: float = DEFAULT_DEEP_WORK_COOLDOWN_SECONDS
    max_pending_deep_jobs: int = DEFAULT_MAX_PENDING_DEEP_JOBS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    evaluator_metadata: Mapping[str, EvaluatorExecutionMetadata] = field(
        default_factory=lambda: dict(MEASURED_EVALUATORS))

    def __post_init__(self) -> None:
        if (not self.policy_id or self.deep_runtime_seconds <= 0
                or self.deep_work_cooldown_seconds < 0
                or self.max_pending_deep_jobs <= 0 or self.max_attempts <= 0):
            raise ResearchWorkQueueError("RESEARCH_EXECUTION_POLICY_INVALID")

    def classify(
        self, question: Any, *, population_records: int | None = None,
        observed_runtime_seconds: float | None = None,
    ) -> dict[str, Any]:
        key = evaluator_key(question)
        metadata = self.evaluator_metadata.get(key, EvaluatorExecutionMetadata())
        runtime = (
            observed_runtime_seconds
            if observed_runtime_seconds is not None
            else metadata.measured_runtime_seconds
        )
        reasons: list[str] = []
        if not metadata.inline_safe:
            reasons.append("EXPLICIT_NOT_INLINE_SAFE")
        if metadata.population_wide_scan:
            reasons.append("POPULATION_WIDE_SCAN")
        if runtime is not None and float(runtime) >= self.deep_runtime_seconds:
            reasons.append("MEASURED_RUNTIME_EXCEEDS_POLICY")
        execution_class = DEEP if reasons else FAST
        return {
            "policy_id": self.policy_id,
            "execution_class": execution_class,
            "evaluator": key,
            "measured_runtime_seconds": runtime,
            "population_records": population_records,
            "prior_peak_memory_bytes": metadata.prior_peak_memory_bytes,
            "population_wide_scan": metadata.population_wide_scan,
            "inline_safe": metadata.inline_safe,
            "latest_scope_supersedes": metadata.latest_scope_supersedes,
            "deep_work_cooldown_seconds": self.deep_work_cooldown_seconds,
            "max_pending_deep_jobs": self.max_pending_deep_jobs,
            "max_attempts": self.max_attempts,
            "reasons": reasons or ["INLINE_SAFE_UNDER_POLICY"],
        }


@dataclass
class DeepWorkJob:
    job_id: str
    question_id: str
    evaluator: str
    evaluator_identity_digest: str
    triggering_snapshot_id: str
    triggering_epoch_id: str
    dependency_identity: str
    prerequisite_identities: dict[str, str]
    policy: dict[str, Any]
    frontier_start: str
    frontier_end: str
    queued_at: str
    state: str = PENDING
    attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    result_id: str | None = None
    result_snapshot_id: str | None = None
    failure_reason: str | None = None
    superseded_by: str | None = None
    transitions: list[dict[str, Any]] = field(default_factory=list)

    def identity_material(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "evaluator": self.evaluator,
            "evaluator_identity_digest": self.evaluator_identity_digest,
            "triggering_snapshot_id": self.triggering_snapshot_id,
            "triggering_epoch_id": self.triggering_epoch_id,
            "dependency_identity": self.dependency_identity,
            "prerequisite_identities": dict(sorted(
                self.prerequisite_identities.items())),
            "policy_id": str(self.policy.get("policy_id") or ""),
        }

    def derived_id(self) -> str:
        return "RJOB-" + _digest(self.identity_material())[:24].upper()

    def validate(self) -> None:
        if self.state not in {PENDING, RUNNING, COMPLETED, FAILED, SUPERSEDED}:
            raise ResearchWorkQueueError("DEEP_WORK_STATE_INVALID")
        if self.job_id != self.derived_id():
            raise ResearchWorkQueueError("DEEP_WORK_IDENTITY_MISMATCH")
        if self.attempts < 0:
            raise ResearchWorkQueueError("DEEP_WORK_ATTEMPTS_INVALID")
        if self.state == COMPLETED and (
                not self.result_id
                or self.result_snapshot_id != self.triggering_snapshot_id):
            raise ResearchWorkQueueError("DEEP_WORK_COMPLETION_INVALID")
        if self.state == SUPERSEDED and not self.superseded_by:
            raise ResearchWorkQueueError("DEEP_WORK_SUPERSESSION_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeepWorkJob":
        fields = {
            name: deepcopy(value[name])
            for name in cls.__dataclass_fields__ if name in value
        }
        job = cls(**fields)
        job.validate()
        return job


def deep_work_job(
    *, question_id: str, evaluator: str, evaluator_identity_digest: str,
    snapshot_id: str, epoch_id: str, dependency_identity: str,
    prerequisite_identities: Mapping[str, str], policy: Mapping[str, Any],
    frontier_start: str, frontier_end: str, queued_at: str,
) -> DeepWorkJob:
    job = DeepWorkJob(
        job_id="", question_id=question_id, evaluator=evaluator,
        evaluator_identity_digest=evaluator_identity_digest,
        triggering_snapshot_id=snapshot_id, triggering_epoch_id=epoch_id,
        dependency_identity=dependency_identity,
        prerequisite_identities=dict(sorted(prerequisite_identities.items())),
        policy=dict(policy), frontier_start=frontier_start,
        frontier_end=frontier_end, queued_at=queued_at,
    )
    job.job_id = job.derived_id()
    job.transitions.append({"state": PENDING, "at": queued_at})
    job.validate()
    return job


class ResearchWorkQueueStore:
    """Atomic persistent queue with deterministic coalescing and recovery."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.jobs: dict[str, DeepWorkJob] = {}
        self.epochs: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResearchWorkQueueError("RESEARCH_WORK_QUEUE_UNREADABLE") from exc
        if not isinstance(value, Mapping) or value.get("schema") != QUEUE_SCHEMA:
            raise ResearchWorkQueueError("RESEARCH_WORK_QUEUE_SCHEMA_INVALID")
        rows = value.get("jobs")
        epochs = value.get("epochs")
        if not isinstance(rows, list) or not isinstance(epochs, list):
            raise ResearchWorkQueueError("RESEARCH_WORK_QUEUE_CONTENT_INVALID")
        for row in rows:
            job = DeepWorkJob.from_dict(row)
            if job.job_id in self.jobs:
                raise ResearchWorkQueueError("DEEP_WORK_JOB_DUPLICATE")
            if job.state == RUNNING:
                job.state = PENDING
                job.failure_reason = "RECOVERED_INTERRUPTED_ATTEMPT"
                job.started_at = None
                job.transitions.append({
                    "state": PENDING,
                    "reason": "RECOVERED_INTERRUPTED_ATTEMPT",
                })
            self.jobs[job.job_id] = job
        seen_epochs: set[str] = set()
        for row in epochs:
            if not isinstance(row, Mapping):
                raise ResearchWorkQueueError("RESEARCH_WORK_EPOCH_INVALID")
            epoch_id = str(row.get("epoch_id") or "")
            if not epoch_id or epoch_id in seen_epochs:
                raise ResearchWorkQueueError("RESEARCH_WORK_EPOCH_DUPLICATE")
            seen_epochs.add(epoch_id)
            self.epochs.append(dict(row))

    def save(self) -> None:
        payload = {
            "schema": QUEUE_SCHEMA,
            "jobs": [self.jobs[key].to_dict() for key in sorted(self.jobs)],
            "epochs": list(self.epochs),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise ResearchWorkQueueError(
                "RESEARCH_WORK_QUEUE_PERSISTENCE_FAILED") from exc

    def ordered(self) -> tuple[DeepWorkJob, ...]:
        return tuple(sorted(
            self.jobs.values(),
            key=lambda job: (job.queued_at, job.question_id, job.job_id),
        ))

    def enqueue(
        self, job: DeepWorkJob, *, latest_scope_supersedes: bool,
        max_pending_jobs: int,
    ) -> DeepWorkJob:
        job.validate()
        job.policy["max_pending_deep_jobs"] = int(max_pending_jobs)
        existing = self.jobs.get(job.job_id)
        if existing is not None:
            return existing
        active = [item for item in self.jobs.values() if item.state in ACTIVE]
        same_question = [
            item for item in active
            if item.question_id == job.question_id
            and item.evaluator_identity_digest == job.evaluator_identity_digest
        ]
        if latest_scope_supersedes:
            for prior in same_question:
                if (job.frontier_start <= prior.frontier_start
                        and job.frontier_end >= prior.frontier_end):
                    prior.state = SUPERSEDED
                    prior.superseded_by = job.job_id
                    prior.transitions.append({
                        "state": SUPERSEDED,
                        "superseded_by": job.job_id,
                        "at": job.queued_at,
                    })
        active_after = sum(
            item.state in ACTIVE for item in self.jobs.values()) + 1
        if active_after > max_pending_jobs:
            job.transitions.append({
                "state": PENDING,
                "reason": "BACKPRESSURE_HIGH_WATERMARK",
                "pending_jobs": active_after,
                "configured_high_watermark": max_pending_jobs,
            })
        self.jobs[job.job_id] = job
        self.save()
        return job

    def record_epoch(
        self, *, epoch_id: str, snapshot_id: str, closed_at: str,
        deep_job_ids: Sequence[str],
    ) -> None:
        record = {
            "sequence": len(self.epochs) + 1,
            "epoch_id": epoch_id,
            "snapshot_id": snapshot_id,
            "closed_at": closed_at,
            "deep_job_ids": sorted(set(str(item) for item in deep_job_ids)),
        }
        for existing in self.epochs:
            if existing.get("epoch_id") == epoch_id:
                if (
                    existing.get("snapshot_id") != snapshot_id
                    or existing.get("closed_at") != closed_at
                ):
                    raise ResearchWorkQueueError("RESEARCH_WORK_EPOCH_IDENTITY_COLLISION")
                existing["deep_job_ids"] = sorted(
                    set(existing.get("deep_job_ids") or ())
                    | set(record["deep_job_ids"]))
                self.save()
                return
        self.epochs.append(record)
        self.save()

    def claim_next(
        self, *, now: str, policy: ResearchExecutionPolicy,
    ) -> DeepWorkJob | None:
        if any(job.state == RUNNING for job in self.jobs.values()):
            return None
        now_value = _parse_time(now)
        completed_times = [
            _parse_time(job.completed_at or "")
            for job in self.jobs.values() if job.state == COMPLETED
        ]
        completed_times = [item for item in completed_times if item is not None]
        if now_value is not None and completed_times:
            elapsed = (now_value - max(completed_times)).total_seconds()
            if elapsed < policy.deep_work_cooldown_seconds:
                return None
        for job in self.ordered():
            if job.state != PENDING:
                continue
            if job.attempts >= policy.max_attempts:
                job.state = FAILED
                job.failure_reason = "MAX_ATTEMPTS_EXHAUSTED"
                job.transitions.append({
                    "state": FAILED, "at": now,
                    "reason": job.failure_reason,
                })
                self.save()
                continue
            job.state = RUNNING
            job.attempts += 1
            job.started_at = now
            job.transitions.append({
                "state": RUNNING, "at": now, "attempt": job.attempts,
            })
            self.save()
            return job
        return None

    def complete(
        self, job_id: str, *, result_id: str, result_snapshot_id: str,
        evaluator_identity_digest: str, completed_at: str,
    ) -> DeepWorkJob:
        job = self.jobs.get(job_id)
        if job is None or job.state != RUNNING:
            raise ResearchWorkQueueError("DEEP_WORK_COMPLETION_NOT_RUNNING")
        if any(
                newer.question_id == job.question_id
                and newer.evaluator_identity_digest == job.evaluator_identity_digest
                and newer.job_id != job.job_id
                and newer.state in {PENDING, RUNNING, COMPLETED}
                and newer.queued_at > job.queued_at
                for newer in self.jobs.values()):
            raise ResearchWorkQueueError("STALE_DEEP_WORK_COMPLETION")
        if (result_snapshot_id != job.triggering_snapshot_id
                or evaluator_identity_digest != job.evaluator_identity_digest
                or not result_id):
            raise ResearchWorkQueueError("DEEP_WORK_RESULT_IDENTITY_MISMATCH")
        job.state = COMPLETED
        job.completed_at = completed_at
        job.result_id = result_id
        job.result_snapshot_id = result_snapshot_id
        job.failure_reason = None
        job.transitions.append({
            "state": COMPLETED, "at": completed_at, "result_id": result_id,
        })
        job.validate()
        self.save()
        return job

    def fail(self, job_id: str, *, reason: str, failed_at: str) -> DeepWorkJob:
        job = self.jobs.get(job_id)
        if job is None or job.state != RUNNING:
            raise ResearchWorkQueueError("DEEP_WORK_FAILURE_NOT_RUNNING")
        job.state = FAILED
        job.completed_at = failed_at
        job.failure_reason = reason
        job.transitions.append({
            "state": FAILED, "at": failed_at, "reason": reason,
        })
        self.save()
        return job

    def metrics(
        self, *, current_epoch_id: str | None = None,
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        now_text = generated_at or datetime.now(timezone.utc).isoformat()
        now = _parse_time(now_text)
        pending = [job for job in self.jobs.values() if job.state == PENDING]
        running = [job for job in self.jobs.values() if job.state == RUNNING]
        failed = [job for job in self.jobs.values() if job.state == FAILED]
        configured_limits = [
            int(job.policy.get("max_pending_deep_jobs"))
            for job in self.jobs.values()
            if isinstance(job.policy.get("max_pending_deep_jobs"), int)
            and int(job.policy["max_pending_deep_jobs"]) > 0
        ]
        high_watermark = (
            min(configured_limits)
            if configured_limits else DEFAULT_MAX_PENDING_DEEP_JOBS)
        ages = []
        for job in pending:
            queued = _parse_time(job.queued_at)
            if now is not None and queued is not None:
                ages.append(max(0.0, (now - queued).total_seconds()))
        unresolved_ages = []
        for job in self.jobs.values():
            if job.state not in {PENDING, RUNNING, FAILED}:
                continue
            queued = _parse_time(job.queued_at)
            if now is not None and queued is not None:
                unresolved_ages.append(
                    max(0.0, (now - queued).total_seconds()))
        watermark = 0
        latest_deep_epoch = None
        for epoch in self.epochs:
            states = [
                self.jobs[job_id].state
                for job_id in epoch.get("deep_job_ids", ())
                if job_id in self.jobs
            ]
            if any(state in {PENDING, RUNNING, FAILED} for state in states):
                break
            watermark = int(epoch["sequence"])
            latest_deep_epoch = str(epoch["epoch_id"])
        latest = self.epochs[-1] if self.epochs else None
        return {
            "current_epoch_id": current_epoch_id,
            "latest_closed_epoch_id": (
                None if latest is None else latest.get("epoch_id")),
            "latest_fast_processed_epoch_id": (
                None if latest is None else latest.get("epoch_id")),
            "latest_deep_processed_epoch_id": latest_deep_epoch,
            "pending_deep_jobs": len(pending),
            "running_deep_jobs": len(running),
            "oldest_pending_seconds": max(ages) if ages else 0.0,
            "lag_epochs": (
                0 if latest is None else int(latest["sequence"]) - watermark),
            "lag_seconds": max(unresolved_ages) if unresolved_ages else 0.0,
            "currently_running_deep_job": (
                None if not running else sorted(job.job_id for job in running)[0]),
            "failed_deep_jobs": sorted(job.job_id for job in failed),
            "blocked_deep_jobs": [],
            "max_pending_deep_jobs": high_watermark,
            "backpressure_active": (
                len(pending) + len(running) > high_watermark),
            "projection_generated_at": now_text,
        }


def process_one_deep_job(
    *, store: ResearchWorkQueueStore,
    executor: Callable[[DeepWorkJob], Mapping[str, Any]],
    policy: ResearchExecutionPolicy | None = None,
    now: str | None = None,
) -> DeepWorkJob | None:
    """Run at most one job and validate its governed completion identity."""
    resolved_policy = policy or ResearchExecutionPolicy()
    stamp = now or datetime.now(timezone.utc).isoformat()
    job = store.claim_next(now=stamp, policy=resolved_policy)
    if job is None:
        return None
    try:
        output = dict(executor(job))
        return store.complete(
            job.job_id,
            result_id=str(output.get("result_id") or ""),
            result_snapshot_id=str(output.get("snapshot_id") or ""),
            evaluator_identity_digest=str(
                output.get("evaluator_identity_digest") or ""),
            completed_at=str(output.get("completed_at") or stamp),
        )
    except Exception as exc:
        current = store.jobs.get(job.job_id)
        if current is not None and current.state == RUNNING:
            store.fail(
                job.job_id,
                reason=f"{type(exc).__name__}:{exc}",
                failed_at=stamp,
            )
        raise


__all__ = [
    "COMPLETED", "DEEP", "DeepWorkJob", "FAILED", "FAST", "PENDING",
    "POLICY_ID", "RUNNING", "SUPERSEDED", "EvaluatorExecutionMetadata",
    "ResearchExecutionPolicy", "ResearchWorkQueueError",
    "ResearchWorkQueueStore", "deep_work_job", "evaluator_key",
    "process_one_deep_job",
]
