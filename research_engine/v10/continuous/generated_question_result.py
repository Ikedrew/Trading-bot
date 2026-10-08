"""Immutable result and execution-state persistence for generated (Q71+) questions.

Two authorities live here, deliberately separated:

``GeneratedQuestionResultStore``
    Append-only immutable results plus immutable execution batches.  A result is
    addressed by a digest over its governed inputs, so re-running the same
    evaluator against the same evidence epoch under the same evaluator identity
    reproduces the same artifact instead of minting a duplicate.

``GeneratedQuestionExecutionStore``
    The mutable execution read model: current execution status, scientific
    status, execution freshness, evaluator identity, result lineage, re-entry
    reason, supersession/retirement lineage and the work-item ledger.

Neither store computes science.  Neither store writes findings, hypotheses or
candidates: only the scientific-state bridge may do that.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.governed_scientific_result import (
    SCIENTIFIC_RESULT_SCHEMA,
)
from research_engine.v10.continuous.generated_question_lifecycle import (
    NEVER_EXECUTED, NOT_SCIENTIFICALLY_RESOLVED, TERMINAL_STATES,
    execution_freshness,
)
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
)


GENERATED_RESULT_SCHEMA = "generated_question_result_v1"
GENERATED_BATCH_SCHEMA = "generated_question_execution_batch_v1"
EXECUTION_STATE_SCHEMA = "generated_question_execution_state_v1"
DEFAULT_GENERATED_RESULT_DIRECTORY = Path("data/research/continuous/q71_results")
DEFAULT_EXECUTION_STATE_PATH = Path(
    "data/research/continuous/q71_execution_state.json")


class GeneratedQuestionResultError(RuntimeError):
    """Immutable result identity is invalid, or history would be rewritten."""


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionResultError(
                "GENERATED_RESULT_HISTORY_UNREADABLE:" + str(path)) from exc
        if canonical_json(existing) != canonical_json(payload):
            raise GeneratedQuestionResultError(
                "GENERATED_RESULT_IDENTITY_COLLISION:" + str(path))
        return
    _atomic_json(path, payload)


@dataclass(frozen=True)
class GeneratedQuestionResult:
    """One immutable governed result for one generated question."""

    generated_question_id: str
    observation_cell_identity: str
    evidence_class: str
    question_type: str
    population_identity: str
    horizon: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    evidence_frontier: str
    evaluator_key: str
    evaluator_version: str
    evaluator_identity_digest: str
    capability_class: str
    execution_identity: str
    work_item_id: str
    execution_status: str
    scientific_status: str
    reason_code: str
    reason: str
    evidence_datasets: tuple[str, ...] = ()
    evidence_references: tuple[dict[str, Any], ...] = ()
    governed_scientific_metrics: dict[str, Any] = field(default_factory=dict)
    transport_result: dict[str, Any] = field(default_factory=dict)
    predecessor_result_id: str | None = None
    superseded_by: str | None = None
    recorded_at: str = ""
    result_id: str = ""

    def identity_material(self) -> dict[str, Any]:
        """Deterministic identity over governed inputs only.

        Wall-clock recording time is provenance, never identity.
        """
        return {
            "schema": GENERATED_RESULT_SCHEMA,
            "generated_question_id": self.generated_question_id,
            "observation_cell_identity": self.observation_cell_identity,
            "evidence_class": self.evidence_class,
            "question_type": self.question_type,
            "population_identity": self.population_identity,
            "horizon": self.horizon,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "evidence_frontier": self.evidence_frontier,
            "evaluator_key": self.evaluator_key,
            "evaluator_version": self.evaluator_version,
            "evaluator_identity_digest": self.evaluator_identity_digest,
            "capability_class": self.capability_class,
            "execution_identity": self.execution_identity,
            "work_item_id": self.work_item_id,
            "execution_status": self.execution_status,
            "scientific_status": self.scientific_status,
            "reason_code": self.reason_code,
            "evidence_datasets": list(self.evidence_datasets),
            "evidence_references": [dict(item) for item in self.evidence_references],
            "governed_scientific_metrics": dict(self.governed_scientific_metrics),
            "transport_result": dict(self.transport_result),
            "predecessor_result_id": self.predecessor_result_id,
        }

    @property
    def derived_result_id(self) -> str:
        return "GQR-" + _digest(self.identity_material())[:32].upper()

    def validate(self) -> "GeneratedQuestionResult":
        if not self.generated_question_id.startswith("GEN-"):
            raise GeneratedQuestionResultError("GENERATED_QUESTION_ID_INVALID")
        if not self.execution_identity or not self.work_item_id:
            raise GeneratedQuestionResultError("GENERATED_RESULT_EXECUTION_IDENTITY_REQUIRED")
        if self.result_id != self.derived_result_id:
            raise GeneratedQuestionResultError("GENERATED_RESULT_IDENTITY_MISMATCH")
        if not self.transport_result:
            raise GeneratedQuestionResultError("GENERATED_RESULT_TRANSPORT_REQUIRED")
        if self.transport_result.get("question_id") != self.generated_question_id:
            raise GeneratedQuestionResultError(
                "GENERATED_RESULT_TRANSPORT_QUESTION_MISMATCH")
        return self

    def transport(self) -> CanonicalQuestionResult:
        """The governed scientific transport object (Repair Block 1 contract)."""
        return CanonicalQuestionResult.from_dict(dict(self.transport_result))

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_schema": GENERATED_RESULT_SCHEMA,
            **asdict(self),
            "evidence_datasets": list(self.evidence_datasets),
            "evidence_references": [dict(item) for item in self.evidence_references],
            "scientific_result_schema": SCIENTIFIC_RESULT_SCHEMA,
        }

    @classmethod
    def build(
        cls, *, predecessor_result_id: str | None = None, recorded_at: str = "",
        **values: Any,
    ) -> "GeneratedQuestionResult":
        result = cls(
            predecessor_result_id=predecessor_result_id, recorded_at=recorded_at,
            **values,
        )
        result = cls(**{**asdict(result), "result_id": result.derived_result_id})
        return result.validate()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedQuestionResult":
        if not isinstance(value, Mapping):
            raise GeneratedQuestionResultError("GENERATED_RESULT_MALFORMED")
        if value.get("result_schema") != GENERATED_RESULT_SCHEMA:
            raise GeneratedQuestionResultError("GENERATED_RESULT_SCHEMA_INVALID")
        fields = {
            name: value[name]
            for name in cls.__dataclass_fields__ if name in value
        }
        fields["evidence_datasets"] = tuple(fields.get("evidence_datasets") or ())
        fields["evidence_references"] = tuple(
            dict(item) for item in (fields.get("evidence_references") or ()))
        result = cls(**fields)
        return result.validate()

@dataclass(frozen=True)
class GeneratedQuestionExecutionBatch:
    """The immutable set of generated questions executed against one frontier."""

    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    evaluator_registry_identity: str
    entries: tuple[dict[str, Any], ...] = ()
    recorded_at: str = ""
    batch_id: str = ""

    def identity_material(self) -> dict[str, Any]:
        return {
            "schema": GENERATED_BATCH_SCHEMA,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "evaluator_registry_identity": self.evaluator_registry_identity,
            "entries": [dict(item) for item in self.entries],
        }

    @property
    def derived_batch_id(self) -> str:
        return "GQB-" + _digest(self.identity_material())[:32].upper()

    @property
    def result_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item["result_id"]) for item in self.entries if item.get("result_id"))

    def validate(self) -> "GeneratedQuestionExecutionBatch":
        if self.batch_id != self.derived_batch_id:
            raise GeneratedQuestionResultError("GENERATED_BATCH_IDENTITY_MISMATCH")
        if not self.snapshot_id or not self.investigation_epoch:
            raise GeneratedQuestionResultError("GENERATED_BATCH_EVIDENCE_IDENTITY_REQUIRED")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_schema": GENERATED_BATCH_SCHEMA,
            "batch_id": self.batch_id,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "evaluator_registry_identity": self.evaluator_registry_identity,
            "entries": [dict(item) for item in self.entries],
            "result_ids": list(self.result_ids),
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def build(
        cls, *, entries: Sequence[Mapping[str, Any]], recorded_at: str = "",
        **values: Any,
    ) -> "GeneratedQuestionExecutionBatch":
        batch = cls(
            entries=tuple(dict(item) for item in entries),
            recorded_at=recorded_at, **values,
        )
        batch = cls(**{**asdict(batch), "batch_id": batch.derived_batch_id})
        return batch.validate()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedQuestionExecutionBatch":
        if value.get("batch_schema") != GENERATED_BATCH_SCHEMA:
            raise GeneratedQuestionResultError("GENERATED_BATCH_SCHEMA_INVALID")
        batch = cls(
            snapshot_id=str(value.get("snapshot_id") or ""),
            snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
            investigation_epoch=str(value.get("investigation_epoch") or ""),
            evaluator_registry_identity=str(
                value.get("evaluator_registry_identity") or ""),
            entries=tuple(dict(item) for item in (value.get("entries") or ())),
            recorded_at=str(value.get("recorded_at") or ""),
            batch_id=str(value.get("batch_id") or ""),
        )
        return batch.validate()

class GeneratedQuestionResultStore:
    """Append-only immutable generated-question results and batches."""

    def __init__(self, directory: Path | str = DEFAULT_GENERATED_RESULT_DIRECTORY):
        self.directory = Path(directory)
        self.results_directory = self.directory / "results"
        self.batches_directory = self.directory / "batches"
        self.index_path = self.directory / "index.json"
        self._index = self._load_index()

    # -- Index -----------------------------------------------------------
    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema": GENERATED_RESULT_SCHEMA, "questions": {}}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionResultError("GENERATED_RESULT_INDEX_UNREADABLE") from exc
        if (not isinstance(value, Mapping)
                or value.get("schema") != GENERATED_RESULT_SCHEMA
                or not isinstance(value.get("questions"), Mapping)):
            raise GeneratedQuestionResultError("GENERATED_RESULT_INDEX_INVALID")
        return {"schema": GENERATED_RESULT_SCHEMA, "questions": dict(value["questions"])}

    @property
    def index(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._index))

    def result_path(self, result_id: str) -> Path:
        return self.results_directory / f"{result_id}.json"

    def batch_path(self, batch_id: str) -> Path:
        return self.batches_directory / f"{batch_id}.json"

    # -- Results ---------------------------------------------------------
    def save_result(self, result: GeneratedQuestionResult) -> Path:
        """Persist immutably and index it.  History is never overwritten."""
        result.validate()
        path = self.result_path(result.result_id)
        _immutable_json(path, result.to_dict())
        entry = self._index["questions"].setdefault(
            result.generated_question_id, {"history": [], "latest": None})
        if result.result_id not in entry["history"]:
            entry["history"] = sorted(set(entry["history"]) | {result.result_id})
        entry["latest"] = result.result_id
        entry["execution_status"] = result.execution_status
        entry["scientific_status"] = result.scientific_status
        entry["evidence_epoch"] = result.investigation_epoch
        entry["evaluator_identity_digest"] = result.evaluator_identity_digest
        entry["evaluator_key"] = result.evaluator_key
        _atomic_json(self.index_path, self._index)
        return path

    def load_result(self, result_id: str) -> GeneratedQuestionResult | None:
        path = self.result_path(str(result_id))
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionResultError(
                "GENERATED_RESULT_UNREADABLE:" + str(result_id)) from exc
        result = GeneratedQuestionResult.from_dict(value)
        if result.result_id != result_id:
            raise GeneratedQuestionResultError(
                "GENERATED_RESULT_IDENTITY_MISMATCH:" + str(result_id))
        return result

    def result_ids(self, generated_question_id: str) -> tuple[str, ...]:
        entry = self._index["questions"].get(generated_question_id) or {}
        return tuple(entry.get("history") or ())

    def latest_result(self, generated_question_id: str) -> GeneratedQuestionResult | None:
        entry = self._index["questions"].get(generated_question_id) or {}
        latest = entry.get("latest")
        return None if not latest else self.load_result(str(latest))

    def results(self) -> tuple[GeneratedQuestionResult, ...]:
        rows = []
        for question_id in sorted(self._index["questions"]):
            for result_id in self.result_ids(question_id):
                result = self.load_result(result_id)
                if result is not None:
                    rows.append(result)
        return tuple(rows)

    # -- Batches ---------------------------------------------------------
    def record_batch(self, batch: GeneratedQuestionExecutionBatch) -> Path:
        batch.validate()
        path = self.batch_path(batch.batch_id)
        _immutable_json(path, batch.to_dict())
        return path

    def load_batch(self, batch_id: str) -> GeneratedQuestionExecutionBatch | None:
        path = self.batch_path(str(batch_id))
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionResultError(
                "GENERATED_BATCH_UNREADABLE:" + str(batch_id)) from exc
        batch = GeneratedQuestionExecutionBatch.from_dict(value)
        if batch.batch_id != batch_id:
            raise GeneratedQuestionResultError(
                "GENERATED_BATCH_IDENTITY_MISMATCH:" + str(batch_id))
        return batch

def work_item_identity(
    *, generated_question_id: str, evidence_epoch: str,
    evaluator_identity_digest: str,
) -> str:
    """Deterministic governed work-item identity.

    One question, one evidence epoch and one evaluator identity is exactly one
    unit of governed work: a retry reuses the identity, and two simultaneous
    executions of the same unit are mechanically impossible to distinguish and
    therefore rejected.
    """
    return "GQW-" + _digest({
        "generated_question_id": generated_question_id,
        "evidence_epoch": evidence_epoch,
        "evaluator_identity_digest": evaluator_identity_digest,
    })[:32].upper()


@dataclass
class GeneratedQuestionExecutionStore:
    """Mutable execution read model for generated questions."""

    path: Path | str = DEFAULT_EXECUTION_STATE_PATH

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._document = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": EXECUTION_STATE_SCHEMA, "questions": {}, "work_items": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GeneratedQuestionResultError(
                "GENERATED_EXECUTION_STATE_UNREADABLE") from exc
        if (not isinstance(value, Mapping)
                or value.get("schema") != EXECUTION_STATE_SCHEMA
                or not isinstance(value.get("questions"), Mapping)
                or not isinstance(value.get("work_items"), Mapping)):
            raise GeneratedQuestionResultError("GENERATED_EXECUTION_STATE_INVALID")
        return {
            "schema": EXECUTION_STATE_SCHEMA,
            "questions": {str(k): dict(v) for k, v in value["questions"].items()},
            "work_items": {str(k): dict(v) for k, v in value["work_items"].items()},
        }

    def _save(self) -> None:
        _atomic_json(self.path, self._document)

    # -- Introspection ---------------------------------------------------
    @property
    def document(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._document))

    def state(self, generated_question_id: str) -> dict[str, Any] | None:
        row = self._document["questions"].get(str(generated_question_id))
        return None if row is None else dict(row)

    def states(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._document["questions"].items()}

    def work_item(self, work_item_id: str) -> dict[str, Any] | None:
        row = self._document["work_items"].get(str(work_item_id))
        return None if row is None else dict(row)

    def active_work_items(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(value) for _, value in sorted(self._document["work_items"].items())
            if value.get("state") in {"CLAIMED", "RUNNING"}
        )

    # -- Mutation --------------------------------------------------------
    def record_claim(
        self, *, generated_question_id: str, work_item_id: str,
        evidence_epoch: str, evaluator_key: str,
        evaluator_identity_digest: str, claimed_at: str,
        max_attempts: int,
    ) -> dict[str, Any]:
        """Claim one governed work item; refuses duplicates and exhaustion."""
        existing_item = self._document["work_items"].get(work_item_id)
        if existing_item is not None and existing_item.get("state") == "RUNNING":
            raise GeneratedQuestionResultError(
                "GENERATED_WORK_ALREADY_RUNNING:" + work_item_id)
        if existing_item is not None and existing_item.get("state") == "COMPLETED":
            return dict(existing_item)
        attempts = int((existing_item or {}).get("attempts") or 0) + 1
        if attempts > int(max_attempts):
            raise GeneratedQuestionResultError(
                "GENERATED_WORK_RETRY_CAP_EXHAUSTED:" + work_item_id)
        item = {
            "work_item_id": work_item_id,
            "generated_question_id": generated_question_id,
            "evidence_epoch": evidence_epoch,
            "evaluator_key": evaluator_key,
            "evaluator_identity_digest": evaluator_identity_digest,
            "state": "RUNNING",
            "attempts": attempts,
            "claimed_at": claimed_at,
            "completed_at": None,
            "result_id": None,
            "reason_code": None,
        }
        self._document["work_items"][work_item_id] = item
        self._document["questions"][generated_question_id] = {
            **dict(self._document["questions"].get(generated_question_id) or {}),
            "generated_question_id": generated_question_id,
            "execution_status": "RUNNING",
            "execution_freshness": "EXECUTING",
            "work_item_id": work_item_id,
            "claimed_at": claimed_at,
        }
        self._save()
        return dict(item)

    def record_outcome(
        self, *, generated_question_id: str, work_item_id: str,
        execution_status: str, scientific_status: str, reason_code: str,
        reason: str, result_id: str | None, evidence_epoch: str,
        evaluator_key: str, evaluator_identity_digest: str,
        evaluator_version: str, completed_at: str,
        governed_scientific_metrics: Mapping[str, Any] | None = None,
        predecessor_result_id: str | None = None,
        result_evidence_identity: str | None = None,
        result_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Record the evaluator-derived outcome.  The worker never guesses it."""
        if work_item_id:
            item = self._document["work_items"].setdefault(work_item_id, {
                "work_item_id": work_item_id,
                "generated_question_id": generated_question_id,
                "evidence_epoch": evidence_epoch,
                "evaluator_key": evaluator_key,
                "evaluator_identity_digest": evaluator_identity_digest,
                "attempts": 1,
                "claimed_at": completed_at,
            })
            item.update({
                "state": "COMPLETED" if result_id else "FAILED",
                "completed_at": completed_at,
                "result_id": result_id,
                "reason_code": reason_code,
            })
        previous = dict(self._document["questions"].get(generated_question_id) or {})
        history = list(previous.get("result_history") or ())
        if result_id and result_id not in history:
            history.append(result_id)
        self._document["questions"][generated_question_id] = {
            **previous,
            "generated_question_id": generated_question_id,
            "execution_status": execution_status,
            "scientific_status": scientific_status,
            "execution_freshness": (
                "EXECUTING" if execution_status == "RUNNING" else
                NEVER_EXECUTED if result_id is None else
                execution_freshness(
                    execution_status=execution_status,
                    result_evidence_epoch=evidence_epoch,
                    current_evidence_epoch=evidence_epoch,
                    result_evaluator_digest=evaluator_identity_digest,
                    current_evaluator_digest=evaluator_identity_digest)),
            "reason_code": reason_code,
            "reason": reason,
            "work_item_id": work_item_id,
            "evaluator_key": evaluator_key,
            "evaluator_version": evaluator_version,
            "evaluator_identity_digest": evaluator_identity_digest,
            "latest_result_id": result_id,
            "result_history": history,
            "result_evidence_epoch": evidence_epoch if result_id else None,
            "result_evidence_identity": (
                result_evidence_identity if result_id else None),
            "result_snapshot_id": result_snapshot_id if result_id else None,
            "result_evaluator_digest": (
                evaluator_identity_digest if result_id else None),
            "predecessor_result_id": (
                predecessor_result_id or previous.get("latest_result_id")),
            "governed_scientific_metrics": dict(governed_scientific_metrics or {}),
            "completed_at": completed_at,
        }
        self._save()
        return dict(self._document["questions"][generated_question_id])

    def record_supersession(
        self, *, generated_question_id: str, status: str, reason_code: str,
        reason: str, recorded_at: str, superseded_by: str | None = None,
        cancel_work_items: bool = True,
    ) -> dict[str, Any]:
        """Retire or supersede a question and safely cancel its queued work."""
        if status not in TERMINAL_STATES:
            raise GeneratedQuestionResultError(
                "GENERATED_SUPERSESSION_STATUS_INVALID:" + str(status))
        previous = dict(self._document["questions"].get(generated_question_id) or {})
        lineage = list(previous.get("supersession_history") or ())
        lineage.append({
            "status": status, "reason_code": reason_code, "reason": reason,
            "superseded_by": superseded_by, "recorded_at": recorded_at,
        })
        self._document["questions"][generated_question_id] = {
            **previous,
            "generated_question_id": generated_question_id,
            "execution_status": status,
            "reason_code": reason_code,
            "reason": reason,
            "superseded_by": superseded_by,
            "supersession_reason": reason_code,
            "retirement_reason": (
                reason_code if status == "RETIRED" else previous.get("retirement_reason")),
            "execution_freshness": "NOT_APPLICABLE",
            "supersession_history": lineage,
            "reentry_authorized": False,
            "updated_at": recorded_at,
        }
        if cancel_work_items:
            for work_item_id, item in self._document["work_items"].items():
                if (item.get("generated_question_id") == generated_question_id
                        and item.get("state") in {"CLAIMED", "RUNNING"}):
                    item["state"] = "SUPERSEDED"
                    item["superseded_by"] = superseded_by
                    item["reason_code"] = reason_code
        self._save()
        return dict(self._document["questions"][generated_question_id])

    def record_reentry(
        self, *, generated_question_id: str, reason_code: str, reason: str,
        recorded_at: str,
    ) -> dict[str, Any]:
        """Persist a machine-readable re-entry reason without executing."""
        previous = dict(self._document["questions"].get(generated_question_id) or {})
        self._document["questions"][generated_question_id] = {
            **previous,
            "generated_question_id": generated_question_id,
            "reentry_reason": reason_code,
            "reentry_reason_detail": reason,
            "reentry_count": int(previous.get("reentry_count") or 0) + 1,
            "reentry_at": recorded_at,
            "reentry_authorized": True,
        }
        self._save()
        return dict(self._document["questions"][generated_question_id])

    def metrics(self) -> dict[str, Any]:
        """Bounded-autonomy and queue-lag metrics for the projection."""
        counts: dict[str, int] = {}
        for row in self._document["questions"].values():
            status = str(row.get("execution_status") or "GENERATED")
            counts[status] = counts.get(status, 0) + 1
        return {
            "executed_question_count": sum(
                1 for row in self._document["questions"].values()
                if row.get("latest_result_id")),
            "running_work_items": sum(
                1 for item in self._document["work_items"].values()
                if item.get("state") == "RUNNING"),
            "failed_work_items": sum(
                1 for item in self._document["work_items"].values()
                if item.get("state") == "FAILED"),
            "superseded_work_items": sum(
                1 for item in self._document["work_items"].values()
                if item.get("state") == "SUPERSEDED"),
            "execution_status_counts": dict(sorted(counts.items())),
        }


__all__ = [
    "DEFAULT_EXECUTION_STATE_PATH",
    "DEFAULT_GENERATED_RESULT_DIRECTORY",
    "EXECUTION_STATE_SCHEMA",
    "GENERATED_BATCH_SCHEMA",
    "GENERATED_RESULT_SCHEMA",
    "GeneratedQuestionExecutionBatch",
    "GeneratedQuestionExecutionStore",
    "GeneratedQuestionResult",
    "GeneratedQuestionResultError",
    "GeneratedQuestionResultStore",
    "work_item_identity",
]
