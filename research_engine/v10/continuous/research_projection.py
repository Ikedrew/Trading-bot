"""Deterministic derived read model for the continuous research system."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.q71_worker import relevant_evidence_identity
from research_engine.v10.continuous.research_work_queue import ResearchWorkQueueStore
from research_engine.v10.continuous.validation_queue import ValidationQueueStore
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry
from research_engine.v10.continuous.generated_question_lifecycle import (
    MISSING_EVALUATOR, NOT_SCIENTIFICALLY_RESOLVED, WAITING_FOR_DATA,
    effective_status, execution_freshness, lifecycle_flags,
)
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionExecutionStore, GeneratedQuestionResultStore,
)



PROJECTION_SCHEMA = "unified_research_projection_v1"
DEFAULT_PROJECTION_DIRECTORY = Path("reports/research/continuous_projection")


class ResearchProjectionError(RuntimeError):
    pass


def _baseline_items(questions: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """Validate baseline membership/identity before emitting registry order.

    Generated questions have their own authority and projection field.  A
    mapping cannot repeat keys; validate embedded identities too so aliases
    cannot silently emit duplicate question rows.
    """
    if (not isinstance(questions, Mapping)
            or set(questions) != set(BASELINE_QUESTION_IDS)):
        raise ResearchProjectionError("CANONICAL_70_PROJECTION_INVARIANT_FAILED")
    items = []
    for question_id in BASELINE_QUESTION_IDS:
        raw = questions[question_id]
        if not isinstance(raw, Mapping) or raw.get("question_id", question_id) != question_id:
            raise ResearchProjectionError("CANONICAL_70_PROJECTION_INVARIANT_FAILED")
        result = raw.get("result")
        if isinstance(result, Mapping) and result.get("question_id", question_id) != question_id:
            raise ResearchProjectionError("CANONICAL_70_PROJECTION_INVARIANT_FAILED")
        items.append((question_id, raw))
    return items


def _value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)


def _current_finding_versions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding_id, versions in sorted(document.get("findings", {}).items()):
        if versions:
            row = dict(versions[-1])
            links = document.get("dependencies", {}).get(finding_id, {})
            row["dependent_hypotheses"] = list(links.get("hypotheses", []))
            row["dependent_candidates"] = list(links.get("candidates", []))
            rows.append(row)
    return rows


def _counts(rows: Sequence[Mapping[str, Any]], name: str) -> dict[str, int]:
    """Deterministic status counts for the projection's queue diagnostics."""
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(name) or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _generated_question_rows(
    *, q71: Mapping[str, Any] | None, findings: Sequence[Mapping[str, Any]],
    dependencies: Mapping[str, Any],
    execution_store: GeneratedQuestionExecutionStore | None,
    result_store: GeneratedQuestionResultStore | None,
) -> list[dict[str, Any]]:
    """Merge generated-question generation state with governed execution state.

    The projection remains the sole authority: the Lab and every other consumer
    read this derivation instead of guessing meaning from a status string.
    """
    states = {} if execution_store is None else execution_store.states()
    rows: list[dict[str, Any]] = []
    for raw in (q71 or {}).get("generated_questions", []):
        row = dict(raw)
        question_id = str(row.get("generated_question_id") or "")
        execution = dict(states.get(question_id) or {})
        result = None
        if result_store is not None and execution.get("latest_result_id"):
            result = result_store.load_result(str(execution["latest_result_id"]))
        generation_status = row.get("status")
        execution_status = execution.get("execution_status")
        lifecycle = effective_status(generation_status, execution_status)
        evaluator_available = bool(
            execution.get("evaluator_key") or row.get("evaluator_key"))
        evidence_available = bool(row.get("evidence_availability"))
        current_epoch = str(row.get("last_evaluated_snapshot") or "")
        current_evidence_identity = relevant_evidence_identity({
            "evidence_history": [dict(row.get("last_evidence_event") or {})],
            "last_evaluated_snapshot": current_epoch,
        })
        result_epoch = str(execution.get("result_evidence_identity") or "")
        current_digest = str(execution.get("evaluator_identity_digest") or "")
        freshness = execution_freshness(
            execution_status=execution_status,
            result_evidence_epoch=result_epoch,
            current_evidence_epoch=current_evidence_identity,
            result_evaluator_digest=execution.get("result_evaluator_digest"),
            current_evaluator_digest=current_digest,
        )
        scientific = str(
            execution.get("scientific_status") or NOT_SCIENTIFICALLY_RESOLVED)
        linked_findings = sorted({
            str(item.get("finding_id")) for item in findings
            if question_id in (item.get("source_question_ids") or ())
        } - {"None", ""})
        linked_hypotheses = sorted({
            str(hyp) for finding_id in linked_findings
            for hyp in (dependencies.get(finding_id, {}) or {}).get("hypotheses", [])
        })
        linked_candidates = sorted({
            str(cand) for finding_id in linked_findings
            for cand in (dependencies.get(finding_id, {}) or {}).get("candidates", [])
        })
        row.update({
            "execution_status": execution_status,
            "scientific_status": scientific,
            "execution_freshness": freshness,
            "lifecycle_status": lifecycle,
            "evaluator_key": execution.get("evaluator_key") or row.get("evaluator_key"),
            "evaluator_version": execution.get("evaluator_version"),
            "evaluator_identity_digest": execution.get("evaluator_identity_digest"),
            "evaluator_available": evaluator_available,
            "latest_result_id": execution.get("latest_result_id"),
            "result_history": list(execution.get("result_history") or ()),
            "predecessor_result_id": execution.get("predecessor_result_id"),
            "governed_scientific_metrics": dict(
                execution.get("governed_scientific_metrics") or {}),
            "reason_code": execution.get("reason_code") or row.get("retirement_reason"),
            "waiting_reason": (
                execution.get("reason_code") if lifecycle == WAITING_FOR_DATA
                else None),
            "missing_evaluator_reason": (
                execution.get("reason_code") if lifecycle == MISSING_EVALUATOR
                else None),
            "reentry_reason": execution.get("reentry_reason") or row.get("reentry_reason"),
            "superseded_by": execution.get("superseded_by") or row.get("superseded_by"),
            "supersession_reason": (
                execution.get("supersession_reason") or row.get("supersession_reason")),
            "supersession_history": list(
                execution.get("supersession_history") or row.get("supersession_history") or ()),
            "queue_state": generation_status,
            "evidence_epoch": result_epoch or None,
            "evidence_frontier": None if result is None else result.evidence_frontier,
            "evidence_datasets": (
                list(result.evidence_datasets) if result is not None
                else list(row.get("evidence_datasets") or ())),
            "linked_findings": linked_findings,
            "linked_hypotheses": linked_hypotheses,
            "linked_candidates": linked_candidates,
            "result_ids": list(
                result_store.result_ids(question_id) if result_store is not None else ()),
        })
        row.update(lifecycle_flags(
            generation_status=generation_status,
            execution_status=execution_status,
            evaluator_available=evaluator_available,
            evidence_available=evidence_available,
            scientific=scientific))
        rows.append(row)
    return rows



def build_unified_research_projection(
    *, continuous_cycle_id: str, frontier: Any, question_projection: Mapping[str, Any] | None,
    bridge: Any | None, scientific_store: ScientificStateStore,
    optimisation_registry: OptimisationRegistry, validation_store: ValidationQueueStore,
    q71: Mapping[str, Any] | None = None,
    shadow_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    predecessor_projection: Mapping[str, Any] | None = None,
    generated_execution_store: GeneratedQuestionExecutionStore | None = None,
    generated_result_store: GeneratedQuestionResultStore | None = None,

    research_work_store: ResearchWorkQueueStore | None = None,
    projection_generated_at: str | None = None,
) -> dict[str, Any]:
    """Compose authorities without becoming one; no status is derived from wall time."""
    document = scientific_store.document
    question_authority = (question_projection or {}).get("questions", {})
    baseline_items = (
        _baseline_items(question_authority)
        if question_projection is not None else [])
    pending_question_ids = {
        str(question_id)
        for question_id, raw in baseline_items
        if isinstance(raw, Mapping)
        and str(raw.get("work_state") or "").startswith("DEEP_")
    }
    dependencies = document.get("dependencies", {})
    findings = _current_finding_versions(document)
    for row in findings:
        source_ids = set(row.get("source_question_ids") or [
            row.get("source_question_id")])
        pending = sorted((source_ids - {None}) & pending_question_ids)
        row["execution_freshness"] = (
            "DEEP_STALE" if pending else "CURRENT")
        row["pending_question_ids"] = pending
    hypothesis_rows = []
    for hypothesis in sorted(optimisation_registry.list_hypotheses(),
                             key=lambda item: item.hypothesis_id):
        row = hypothesis.to_dict()
        row["dependent_candidates"] = sorted(
            candidate.candidate_id for candidate in optimisation_registry.list_candidates()
            if candidate.hypothesis_id == hypothesis.hypothesis_id)
        row["review_requirement"] = (
            "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in hypothesis.status else None)
        pending = sorted(
            {str(hypothesis.source_question)} & pending_question_ids
            if hypothesis.source_question else ())
        row["execution_freshness"] = (
            "DEEP_STALE" if pending else "CURRENT")
        row["pending_question_ids"] = pending
        hypothesis_rows.append(row)
    candidates: list[dict[str, Any]] = []
    jobs_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for job in validation_store.ordered():
        jobs_by_candidate.setdefault(job.candidate_id, []).append(job.to_dict())
    shadow_evidence = shadow_evidence or {}
    for candidate in sorted(optimisation_registry.list_candidates(), key=lambda row: row.candidate_id):
        row = candidate.to_dict()
        get_plan = getattr(optimisation_registry, "get_plan", None)
        plan = get_plan(candidate.candidate_id) if get_plan is not None else None
        row["plan"] = None if plan is None else plan.to_dict()
        hypothesis = optimisation_registry.get_hypothesis(candidate.hypothesis_id)
        row["source_finding_ids"] = sorted(set(
            candidate.source_finding_versions
            or ([hypothesis.source_finding] if hypothesis and hypothesis.source_finding else [])))
        row["source_question_ids"] = sorted(set(
            [hypothesis.source_question] if hypothesis and hypothesis.source_question else []))
        row["validation_queue"] = jobs_by_candidate.get(candidate.candidate_id, [])
        row["shadow_evidence"] = dict(shadow_evidence.get(candidate.candidate_id, {}))
        row["live_approved"] = bool(candidate.shadow_binding.get("live_approved", False))
        row["promotion_action"] = "HUMAN_REVIEW_REQUIRED" if candidate.status == "READY_FOR_PROMOTION_REVIEW" else None
        pending = sorted(set(row["source_question_ids"]) & pending_question_ids)
        row["execution_freshness"] = (
            "DEEP_STALE" if pending else "CURRENT")
        row["pending_question_ids"] = pending
        candidates.append(row)

    generated_rows = _generated_question_rows(
        q71=q71, findings=findings, dependencies=dependencies,
        execution_store=generated_execution_store,
        result_store=generated_result_store)

    question_rows: list[dict[str, Any]] = []
    if question_projection is not None:
        for question_id, raw in baseline_items:
            row = dict(raw)
            row.setdefault("question_id", question_id)
            linked_findings = sorted({
                item.get("finding_id") for item in findings
                if question_id in (item.get("source_question_ids") or [item.get("source_question_id")])
            } - {None})
            linked_hypotheses = sorted({
                hyp for fid in linked_findings
                for hyp in dependencies.get(fid, {}).get("hypotheses", [])
            })
            linked_candidates = sorted({
                cand for fid in linked_findings
                for cand in dependencies.get(fid, {}).get("candidates", [])
            })
            row.update({"linked_findings": linked_findings,
                        "linked_hypotheses": linked_hypotheses,
                        "linked_candidates": linked_candidates,
                        "changed_this_cycle": question_id in set(_value(bridge, "question_changes_processed", ()))})
            question_rows.append(row)

    prior_generated_rows = {
        str(row.get("generated_question_id")): row
        for row in (predecessor_projection or {}).get("generated_questions", [])
        if isinstance(row, Mapping)
    }
    generated_transitions = [
        {"generated_question_id": row.get("generated_question_id"),
         "lifecycle_status": row.get("lifecycle_status"),
         "execution_status": row.get("execution_status"),
         "scientific_status": row.get("scientific_status"),
         "result_id": row.get("latest_result_id")}
        for row in generated_rows
        if row.get("latest_result_id")
        and row.get("latest_result_id") != (
            prior_generated_rows.get(
                str(row.get("generated_question_id"))) or {}).get("latest_result_id")
    ]

    changed = {
        "new_data": list(_value(frontier, "changed_datasets", ())),
        "questions_changed": list(_value(bridge, "question_changes_processed", ())),
        "new_answers": list(_value(bridge, "question_changes_processed", ())),
        "newly_sufficient_questions": [],
        "new_findings": list(_value(bridge, "findings_created", ())),
        "weakened_findings": list(_value(bridge, "findings_weakened", ())),
        "new_hypotheses": list(_value(bridge, "hypotheses_created", ())),
        "invalidated_hypotheses": list(_value(bridge, "hypotheses_invalidated", ())),
        "proposed_candidates": list(_value(bridge, "candidates_created", ())),
        "validation_transitions": [row for rows in jobs_by_candidate.values() for row in rows
                                   if row.get("status") in {"COMPLETED", "FAILED", "BLOCKED"}],
        "shadow_transitions": [],
        "new_q71_questions": list((q71 or {}).get("new_question_ids", [])),
        "retired_questions": list((q71 or {}).get("retired_question_ids", [])),
        "superseded_questions": list((q71 or {}).get("superseded_questions", [])),
        "q71_execution_transitions": generated_transitions,
        "new_q71_results": [
            row["result_id"] for row in generated_transitions if row.get("result_id")],
        "new_blockers": list(_value(bridge, "review_required", ())),
    }
    research_lag = (
        {
            "current_epoch_id": _value(frontier, "investigation_epoch"),
            "latest_closed_epoch_id": _value(frontier, "investigation_epoch"),
            "latest_fast_processed_epoch_id": _value(frontier, "investigation_epoch"),
            "latest_deep_processed_epoch_id": _value(frontier, "investigation_epoch"),
            "pending_deep_jobs": 0,
            "running_deep_jobs": 0,
            "oldest_pending_seconds": 0.0,
            "lag_epochs": 0,
            "lag_seconds": 0.0,
            "currently_running_deep_job": None,
            "failed_deep_jobs": [],
            "blocked_deep_jobs": [],
            "max_pending_deep_jobs": 0,
            "backpressure_active": False,
            "projection_generated_at": _value(frontier, "membership_closed_at"),
        }
        if research_work_store is None else research_work_store.metrics(
            current_epoch_id=_value(frontier, "investigation_epoch"),
            generated_at=(
                projection_generated_at
                or _value(frontier, "membership_closed_at")),
        )
    )
    material = {
        "continuous_cycle_id": continuous_cycle_id,
        "canonical_question_cycle_id": (
            None if question_projection is None else question_projection.get("cycle_id")),
        "data_frontier": {
            "snapshot_id": _value(frontier, "snapshot_id"),
            "fingerprint": _value(frontier, "fingerprint"),
            "investigation_epoch": _value(frontier, "investigation_epoch"),
            "frontier_start": _value(frontier, "frontier_start"),
            "frontier_end": _value(frontier, "frontier_end"),
            "membership_closed_at": _value(frontier, "membership_closed_at"),
            "predecessor_snapshot_id": _value(frontier, "predecessor_snapshot_id"),
            "changed_datasets": list(_value(frontier, "changed_datasets", ())),
            "stale_datasets": list(_value(frontier, "stale_datasets", ())),
            "missing_optional_datasets": list(_value(frontier, "missing_optional_datasets", ())),
            "status": _value(frontier, "status"),
            "last_successful_research_cycle": continuous_cycle_id,
            "freshness": "FROZEN_AT_FRONTIER",
        },
        "canonical_questions": question_rows,
        "generated_questions": generated_rows,
        "findings": findings,
        "hypotheses": hypothesis_rows,
        "candidates": candidates,
        "investigations_and_work_queues": {
            "active_research_investigations": [
                row.get("generated_question_id") for row in generated_rows
                if row.get("status") == "ACTIVE"],
            "waiting_investigations": [
                row.get("generated_question_id") for row in generated_rows
                if row.get("lifecycle_status") == WAITING_FOR_DATA],
            "missing_evaluator_investigations": [
                row.get("generated_question_id") for row in generated_rows
                if row.get("lifecycle_status") == MISSING_EVALUATOR],
            "scientifically_actionable_generated_questions": [
                row.get("generated_question_id") for row in generated_rows
                if row.get("question_scientifically_actionable")],
            "generated_question_lifecycle_counts": _counts(
                generated_rows, "lifecycle_status"),
            "generated_question_execution_counts": _counts(
                generated_rows, "execution_status"),
            "generated_question_freshness_counts": _counts(
                generated_rows, "execution_freshness"),
            "generated_question_queue": list((q71 or {}).get("queue", [])),
            "validation_queue": [job.to_dict() for job in validation_store.ordered()],
            "forward_validation_queue": [job.to_dict() for job in validation_store.ordered()
                                         if job.kind == "FORWARD_VALIDATION"],
            "shadow_active_candidates": [row["candidate_id"] for row in candidates
                                         if row.get("status") == "SHADOW_VALIDATION_ACTIVE"],
            "review_required": [job.job_id for job in validation_store.ordered()
                                if job.status == "REVIEW_REQUIRED"],
            "deep_research_queue": [
                job.to_dict() for job in (
                    () if research_work_store is None
                    else research_work_store.ordered())
            ],
        },
        "research_lag": research_lag,
        "what_changed": changed,
        "predecessor_projection_version": (predecessor_projection or {}).get("projection_version"),
    }
    version = "RPROJ-" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA, "projection_version": version, **material}


def build_research_work_refresh_projection(
    *, continuous_cycle_id: str,
    predecessor_projection: Mapping[str, Any],
    research_work_store: ResearchWorkQueueStore,
    projection_generated_at: str,
) -> dict[str, Any]:
    """Refresh execution freshness/lag without changing scientific truth."""
    material = {
        key: value
        for key, value in predecessor_projection.items()
        if key not in {
            "projection_schema", "projection_version", "continuous_cycle_id",
            "predecessor_projection_version", "research_lag",
        }
    }
    jobs = {job.job_id: job for job in research_work_store.ordered()}
    question_rows = []
    for raw in predecessor_projection.get("canonical_questions", ()):
        row = dict(raw)
        job = jobs.get(str(row.get("deep_work_job_id") or ""))
        if job is not None:
            row["work_state"] = {
                "PENDING": "DEEP_PENDING",
                "RUNNING": "DEEP_RUNNING",
                "FAILED": "DEEP_STALE",
                "SUPERSEDED": "DEEP_STALE",
                "COMPLETED": "DEEP_STALE",
            }[job.state]
            row["execution_freshness"] = "DEEP_STALE"
        question_rows.append(row)
    queues = dict(
        predecessor_projection.get("investigations_and_work_queues") or {})
    queues["deep_research_queue"] = [
        job.to_dict() for job in research_work_store.ordered()]
    frontier = predecessor_projection.get("data_frontier") or {}
    material.update({
        "continuous_cycle_id": continuous_cycle_id,
        "canonical_questions": question_rows,
        "investigations_and_work_queues": queues,
        "research_lag": research_work_store.metrics(
            current_epoch_id=frontier.get("investigation_epoch"),
            generated_at=projection_generated_at,
        ),
        "predecessor_projection_version": predecessor_projection.get(
            "projection_version"),
    })
    version = "RPROJ-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA,
            "projection_version": version, **material}


def build_evaluation_refresh_projection(
    *, continuous_cycle_id: str, frontier: Any,
    question_projection: Mapping[str, Any],
    predecessor_projection: Mapping[str, Any],
    refreshed_question_ids: Sequence[str],
) -> dict[str, Any]:
    """Publish new evaluator authority without replaying scientific stages.

    The prior projection's scientific state is retained verbatim.  Only the
    canonical question read model, frontier/cycle authority, and an explicit
    evaluation-refresh diagnostic change.  No finding, hypothesis, candidate,
    or validation transition is manufactured.
    """
    prior_rows = {
        str(row.get("question_id")): row
        for row in predecessor_projection.get("canonical_questions", ())
        if isinstance(row, Mapping)
    }
    question_rows: list[dict[str, Any]] = []
    questions = question_projection.get("questions") or {}
    for question_id, raw in _baseline_items(questions):
        row = dict(raw)
        row.setdefault("question_id", question_id)
        prior = prior_rows.get(str(question_id), {})
        for name in ("linked_findings", "linked_hypotheses", "linked_candidates"):
            row[name] = list(prior.get(name) or ())
        row["changed_this_cycle"] = False
        question_rows.append(row)

    prior_frontier = predecessor_projection.get("data_frontier") or {}
    data_frontier = {
        **dict(prior_frontier),
        "snapshot_id": _value(frontier, "snapshot_id"),
        "fingerprint": _value(frontier, "fingerprint"),
        "investigation_epoch": _value(frontier, "investigation_epoch"),
        "frontier_start": _value(frontier, "frontier_start"),
        "frontier_end": _value(frontier, "frontier_end"),
        "membership_closed_at": _value(frontier, "membership_closed_at"),
        "predecessor_snapshot_id": _value(frontier, "predecessor_snapshot_id"),
        "changed_datasets": list(_value(frontier, "changed_datasets", ())),
        "stale_datasets": list(_value(frontier, "stale_datasets", ())),
        "missing_optional_datasets": list(_value(frontier, "missing_optional_datasets", ())),
        "status": _value(frontier, "status"),
        "last_successful_research_cycle": continuous_cycle_id,
        "freshness": "FROZEN_AT_FRONTIER",
    }
    changed = {
        "new_data": [], "questions_changed": [], "new_answers": [],
        "newly_sufficient_questions": [], "new_findings": [],
        "weakened_findings": [], "new_hypotheses": [],
        "invalidated_hypotheses": [], "proposed_candidates": [],
        "validation_transitions": [], "shadow_transitions": [],
        "new_q71_questions": [], "retired_questions": [], "new_blockers": [],
        "evaluation_refresh": sorted(set(str(item) for item in refreshed_question_ids)),
    }
    material = {
        key: value for key, value in predecessor_projection.items()
        if key not in {
            "projection_schema", "projection_version", "continuous_cycle_id",
            "canonical_question_cycle_id",
            "data_frontier", "canonical_questions", "what_changed",
            "predecessor_projection_version",
        }
    }
    material.update({
        "continuous_cycle_id": continuous_cycle_id,
        "canonical_question_cycle_id": question_projection.get("cycle_id"),
        "data_frontier": data_frontier,
        "canonical_questions": question_rows,
        "what_changed": changed,
        "predecessor_projection_version": predecessor_projection.get("projection_version"),
    })
    version = "RPROJ-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA, "projection_version": version, **material}


class ResearchProjectionStore:
    def __init__(self, directory: Path | str = DEFAULT_PROJECTION_DIRECTORY):
        self.directory = Path(directory)
        self.history_directory = self.directory / "history"
        self.latest_path = self.directory / "latest.json"

    def load_latest(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        try:
            value = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResearchProjectionError("LATEST_PROJECTION_UNREADABLE") from exc
        if value.get("projection_schema") != PROJECTION_SCHEMA:
            raise ResearchProjectionError("LATEST_PROJECTION_SCHEMA_INVALID")
        return value

    def save(self, projection: Mapping[str, Any]) -> Path:
        version = str(projection.get("projection_version") or "")
        if projection.get("projection_schema") != PROJECTION_SCHEMA or not version:
            raise ResearchProjectionError("PROJECTION_IDENTITY_INVALID")
        path = self.history_directory / f"{version}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if canonical_json(existing) != canonical_json(projection):
                raise ResearchProjectionError("PROJECTION_IDENTITY_COLLISION")
        else:
            self._atomic(path, projection)
        self._atomic(self.latest_path, projection)
        return path

    @staticmethod
    def _atomic(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise ResearchProjectionError("PROJECTION_PERSISTENCE_FAILED") from exc


__all__ = ["PROJECTION_SCHEMA", "ResearchProjectionError", "ResearchProjectionStore",
           "build_evaluation_refresh_projection",
           "build_research_work_refresh_projection",
           "build_unified_research_projection"]
