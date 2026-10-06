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
from research_engine.v10.continuous.validation_queue import ValidationQueueStore
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


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


def build_unified_research_projection(
    *, continuous_cycle_id: str, frontier: Any, question_projection: Mapping[str, Any] | None,
    bridge: Any | None, scientific_store: ScientificStateStore,
    optimisation_registry: OptimisationRegistry, validation_store: ValidationQueueStore,
    q71: Mapping[str, Any] | None = None,
    shadow_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    predecessor_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose authorities without becoming one; no status is derived from wall time."""
    document = scientific_store.document
    dependencies = document.get("dependencies", {})
    findings = _current_finding_versions(document)
    hypothesis_rows = []
    for hypothesis in sorted(optimisation_registry.list_hypotheses(),
                             key=lambda item: item.hypothesis_id):
        row = hypothesis.to_dict()
        row["dependent_candidates"] = sorted(
            candidate.candidate_id for candidate in optimisation_registry.list_candidates()
            if candidate.hypothesis_id == hypothesis.hypothesis_id)
        row["review_requirement"] = (
            "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in hypothesis.status else None)
        hypothesis_rows.append(row)
    candidates: list[dict[str, Any]] = []
    jobs_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for job in validation_store.ordered():
        jobs_by_candidate.setdefault(job.candidate_id, []).append(job.to_dict())
    shadow_evidence = shadow_evidence or {}
    for candidate in sorted(optimisation_registry.list_candidates(), key=lambda row: row.candidate_id):
        row = candidate.to_dict()
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
        candidates.append(row)

    question_rows: list[dict[str, Any]] = []
    questions = (question_projection or {}).get("questions", {})
    if question_projection is not None:
        for question_id, raw in _baseline_items(questions):
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
        "new_blockers": list(_value(bridge, "review_required", ())),
    }
    material = {
        "continuous_cycle_id": continuous_cycle_id,
        "data_frontier": {
            "snapshot_id": _value(frontier, "snapshot_id"),
            "fingerprint": _value(frontier, "fingerprint"),
            "investigation_epoch": _value(frontier, "investigation_epoch"),
            "frontier_start": _value(frontier, "frontier_start"),
            "frontier_end": _value(frontier, "frontier_end"),
            "predecessor_snapshot_id": _value(frontier, "predecessor_snapshot_id"),
            "changed_datasets": list(_value(frontier, "changed_datasets", ())),
            "stale_datasets": list(_value(frontier, "stale_datasets", ())),
            "missing_optional_datasets": list(_value(frontier, "missing_optional_datasets", ())),
            "status": _value(frontier, "status"),
            "last_successful_research_cycle": continuous_cycle_id,
            "freshness": "FROZEN_AT_FRONTIER",
        },
        "canonical_questions": question_rows,
        "generated_questions": list((q71 or {}).get("generated_questions", [])),
        "findings": findings,
        "hypotheses": hypothesis_rows,
        "candidates": candidates,
        "investigations_and_work_queues": {
            "active_research_investigations": [row.get("generated_question_id") for row in
                                               (q71 or {}).get("generated_questions", [])
                                               if row.get("status") == "ACTIVE"],
            "waiting_investigations": [row.get("generated_question_id") for row in
                                       (q71 or {}).get("generated_questions", [])
                                       if row.get("status") == "WAITING_FOR_DATA"],
            "generated_question_queue": list((q71 or {}).get("queue", [])),
            "validation_queue": [job.to_dict() for job in validation_store.ordered()],
            "forward_validation_queue": [job.to_dict() for job in validation_store.ordered()
                                         if job.kind == "FORWARD_VALIDATION"],
            "shadow_active_candidates": [row["candidate_id"] for row in candidates
                                         if row.get("status") == "SHADOW_VALIDATION_ACTIVE"],
            "review_required": [job.job_id for job in validation_store.ordered()
                                if job.status == "REVIEW_REQUIRED"],
        },
        "what_changed": changed,
        "predecessor_projection_version": (predecessor_projection or {}).get("projection_version"),
    }
    version = "RPROJ-" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA, "projection_version": version, **material}


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
            "data_frontier", "canonical_questions", "what_changed",
            "predecessor_projection_version",
        }
    }
    material.update({
        "continuous_cycle_id": continuous_cycle_id,
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
           "build_evaluation_refresh_projection", "build_unified_research_projection"]
