"""Immutable result/history state for canonical snapshot-bound question cycles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint


QUESTION_RESULT_SCHEMA = "canonical_question_result_v2"
QUESTION_CYCLE_SCHEMA = "canonical_question_cycle_v1"
LEGACY_QUESTION_RESULT_SCHEMAS = ("canonical_question_result_v1",)
DEFAULT_QUESTION_CYCLE_DIRECTORY = Path("reports/research/questions/_canonical_cycle")


class QuestionCycleStateError(RuntimeError):
    """Question history/current projection could not be safely persisted."""


def atomic_json(path: Path, payload: Mapping[str, Any], *, immutable: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                prefix=".question-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if immutable:
            try:
                # Publish without overwriting a concurrently completed identity.
                os.link(temporary, path)
            except FileExistsError:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if canonical_json(existing) != canonical_json(payload):
                    raise QuestionCycleStateError("IMMUTABLE_HISTORY_ID_COLLISION:" + str(path))
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise QuestionCycleStateError("IMMUTABLE_HISTORY_UNREADABLE:" + str(path)) from exc
        if canonical_json(existing) != canonical_json(payload):
            raise QuestionCycleStateError("IMMUTABLE_HISTORY_ID_COLLISION:" + str(path))
        return
    atomic_json(path, payload, immutable=True)


@dataclass(frozen=True)
class CanonicalQuestionResult:
    question_id: str
    question_version: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    evaluated_at: str
    status: str
    substantive_answer: Any = None
    sample_n: int | None = None
    minimum_required_n: int | None = None
    sample_deficit: int | None = None
    key_metrics: dict[str, Any] = field(default_factory=dict)
    confidence: Any = None
    statistical_output: Any = None
    evidence_datasets: tuple[str, ...] = ()
    evidence_references: tuple[dict[str, Any], ...] = ()
    limitations: tuple[Any, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    implementation_status: str = "IMPLEMENTED"
    changed_since_previous: bool = False
    previous_snapshot_id: str | None = None
    previous_result_id: str | None = None
    runner: str | None = None
    runner_version: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    reason_details: Any = None
    failure_reason: str | None = None
    # Governed evaluation identity.  Absent (None) on results published before
    # governed evaluator identity existed; those remain byte-for-byte
    # verifiable under their original identity material.
    evaluation_identity: dict[str, Any] | None = None
    evaluation_identity_digest: str | None = None
    result_id: str = ""

    def identity_material(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("result_id", None)
        for name in ("reason_code", "reason", "reason_details"):
            if value.get(name) is None:
                # Results written before Repair 5 had no governed-reason
                # transport.  Absence stays absence and their IDs remain
                # verifiable; historical records are never enriched.
                value.pop(name, None)
        if value.get("evaluation_identity") is None and value.get(
                "evaluation_identity_digest") is None:
            # Legacy result: reproduce its original identity material exactly so
            # historical result_ids remain verifiable and no historical payload
            # needs mutation.
            value.pop("evaluation_identity", None)
            value.pop("evaluation_identity_digest", None)
        return value

    def derived_id(self) -> str:
        return "QRESULT-" + fingerprint(self.identity_material())[:32].upper()

    def __post_init__(self) -> None:
        if not self.question_id or not self.snapshot_id:
            raise QuestionCycleStateError("QUESTION_RESULT_IDENTITY_INCOMPLETE")
        expected = self.derived_id()
        if self.result_id and self.result_id != expected:
            raise QuestionCycleStateError("QUESTION_RESULT_FINGERPRINT_MISMATCH")
        object.__setattr__(self, "result_id", expected)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for name in ("reason_code", "reason", "reason_details"):
            if payload.get(name) is None:
                payload.pop(name, None)
        # A legacy result is re-emitted under its original schema so historical
        # payloads stay byte-identical on disk; it is never upgraded in place.
        schema = (
            LEGACY_QUESTION_RESULT_SCHEMAS[0]
            if payload.get("evaluation_identity") is None
            and payload.get("evaluation_identity_digest") is None
            else QUESTION_RESULT_SCHEMA
        )
        return {"result_schema": schema, **payload}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalQuestionResult":
        schema = value.get("result_schema")
        if schema not in (QUESTION_RESULT_SCHEMA, *LEGACY_QUESTION_RESULT_SCHEMAS):
            raise QuestionCycleStateError("UNKNOWN_QUESTION_RESULT_SCHEMA")
        fields = dict(value)
        fields.pop("result_schema", None)
        for name in ("evidence_datasets", "evidence_references", "limitations", "missing_evidence"):
            fields[name] = tuple(fields.get(name) or ())
        # Deterministic migration: a legacy record simply has no governed
        # evaluation identity.  It is not invalidated merely for lacking the
        # field; it is evaluated against current identity by the invalidation
        # rule.
        if fields.get("evaluation_identity") is None:
            fields["evaluation_identity"] = None
        if fields.get("evaluation_identity_digest") is None:
            fields["evaluation_identity_digest"] = None
        return cls(**fields)


@dataclass(frozen=True)
class CanonicalQuestionCycleResult:
    cycle_id: str
    snapshot_id: str
    fingerprint: str
    investigation_epoch: str
    predecessor_cycle_id: str | None
    predecessor_snapshot_id: str | None
    frontier_start: str
    frontier_end: str
    total_questions: int
    evaluated_count: int
    retained_count: int
    complete_count: int
    negative_result_count: int
    insufficient_count: int
    waiting_count: int
    blocked_count: int
    unimplemented_count: int
    alias_count: int
    changed_question_ids: tuple[str, ...]
    unchanged_question_ids: tuple[str, ...]
    failed_question_ids: tuple[str, ...]
    cycle_status: str
    started_at: str
    completed_at: str
    current_projection_path: str
    cycle_history_path: str
    question_history_root: str
    planning: dict[str, str] = field(default_factory=dict)
    question_deltas: dict[str, dict[str, Any]] = field(default_factory=dict)
    result_ids: dict[str, str] = field(default_factory=dict)
    # Governed evaluation identity of this cycle.  Distinct from the evidence
    # snapshot identity above: the same snapshot may legitimately be re-read by a
    # changed evaluator.
    evaluation_identity_fingerprint: str = ""
    stale_evaluation_question_ids: tuple[str, ...] = ()
    deep_pending_question_ids: tuple[str, ...] = ()
    execution_policy_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_schema": QUESTION_CYCLE_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalQuestionCycleResult":
        if value.get("cycle_schema") != QUESTION_CYCLE_SCHEMA:
            raise QuestionCycleStateError("UNKNOWN_QUESTION_CYCLE_SCHEMA")
        fields = dict(value)
        fields.pop("cycle_schema", None)
        for name in ("changed_question_ids", "unchanged_question_ids", "failed_question_ids",
                     "stale_evaluation_question_ids", "deep_pending_question_ids"):
            fields[name] = tuple(fields.get(name) or ())
        result = cls(**fields)
        if not result.cycle_id.startswith("QCYCLE-") or result.total_questions != 70:
            raise QuestionCycleStateError("QUESTION_CYCLE_IDENTITY_INVALID")
        return result


class QuestionCycleStore:
    """Immutable per-question/cycle history plus derived all-question projection."""

    def __init__(self, directory: Path | str = DEFAULT_QUESTION_CYCLE_DIRECTORY):
        self.directory = Path(directory)
        self.cycles_directory = self.directory / "cycles"
        self.projections_directory = self.directory / "projections"
        self.question_history_directory = self.directory / "question_history"
        self.current_path = self.directory / "current_projection.json"

    def cycle_path(self, cycle_id: str) -> Path:
        return self.cycles_directory / f"{cycle_id}.json"

    def question_result_path(
        self, question_id: str, snapshot_id: str,
        evaluation_identity_digest: str | None = None,
    ) -> Path:
        """History path for one governed result.

        Results are keyed by question, evidence snapshot AND evaluation
        identity.  A re-evaluation of the *same* immutable snapshot under a
        changed evaluator is a new governed result and must not overwrite or
        collide with the historical one; the evaluation-identity digest
        separates them.  Legacy paths (no digest) remain readable.
        """
        directory = self.question_history_directory / question_id
        if not evaluation_identity_digest:
            return directory / f"{snapshot_id}.json"
        return directory / f"{snapshot_id}__{evaluation_identity_digest}.json"

    def legacy_question_result_path(self, question_id: str, snapshot_id: str) -> Path:
        return self.question_history_directory / question_id / f"{snapshot_id}.json"

    def projection_path(self, cycle_id: str) -> Path:
        return self.projections_directory / f"{cycle_id}.json"

    def load_cycle(self, cycle_id: str) -> CanonicalQuestionCycleResult | None:
        path = self.cycle_path(cycle_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise QuestionCycleStateError("QUESTION_CYCLE_HISTORY_UNREADABLE") from exc
        return CanonicalQuestionCycleResult.from_dict(value)

    def load_current(self) -> dict[str, Any] | None:
        if not self.current_path.exists():
            return None
        try:
            value = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise QuestionCycleStateError("CURRENT_QUESTION_PROJECTION_UNREADABLE") from exc
        if value.get("cycle_schema") != QUESTION_CYCLE_SCHEMA:
            raise QuestionCycleStateError("CURRENT_QUESTION_PROJECTION_SCHEMA_INVALID")
        questions = value.get("questions")
        if not isinstance(questions, dict):
            raise QuestionCycleStateError("CURRENT_QUESTION_PROJECTION_INVALID")
        return value

    def save_question_result(self, result: CanonicalQuestionResult) -> Path:
        path = self.question_result_path(
            result.question_id, result.snapshot_id,
            result.evaluation_identity_digest or None,
        )
        immutable_json(path, result.to_dict())
        return path

    def load_question_result(
        self, question_id: str, snapshot_id: str,
        evaluation_identity_digest: str | None = None,
    ) -> CanonicalQuestionResult | None:
        """Load one immutable result.

        When an evaluation-identity digest is supplied, the governed
        identity-specific result is preferred; a legacy (digest-less) result is
        accepted only as an explicit legacy fallback so historical queries keep
        working without ever being silently treated as current.
        """
        candidates = []
        if evaluation_identity_digest:
            candidates.append(self.question_result_path(
                question_id, snapshot_id, evaluation_identity_digest))
        candidates.append(self.legacy_question_result_path(question_id, snapshot_id))
        for path in candidates:
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError) as exc:
                raise QuestionCycleStateError("QUESTION_RESULT_HISTORY_UNREADABLE") from exc
            result = CanonicalQuestionResult.from_dict(value)
            if result.question_id != question_id or result.snapshot_id != snapshot_id:
                raise QuestionCycleStateError("QUESTION_RESULT_HISTORY_IDENTITY_MISMATCH")
            return result
        return None

    def load_projection(self, cycle_id: str) -> dict[str, Any]:
        path = self.projection_path(cycle_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise QuestionCycleStateError("IMMUTABLE_PROJECTION_UNREADABLE") from exc
        if value.get("cycle_id") != cycle_id:
            raise QuestionCycleStateError("IMMUTABLE_PROJECTION_ID_MISMATCH")
        return value

    def save_projection(self, projection: Mapping[str, Any]) -> None:
        cycle_id = str(projection.get("cycle_id") or "")
        if not cycle_id:
            raise QuestionCycleStateError("PROJECTION_WITHOUT_CYCLE_ID")
        immutable_json(self.projection_path(cycle_id), projection)
        atomic_json(self.current_path, projection)

    def _checked_receipt(self, path: Path, authority: Mapping[str, Any]):
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("receipt too large")
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            if envelope["sha256"] != fingerprint(payload) or payload["authority"] != authority:
                raise ValueError("authority/digest mismatch")
            return payload
        except Exception as exc:
            raise QuestionCycleStateError("QUESTION_RECEIPT_INVALID:" + path.name) from exc

    def save_checkpoint(self, cycle_id, authority, question_id, entry, delta, prerequisites):
        result = CanonicalQuestionResult.from_dict(entry["result"])
        if result.question_id != question_id:
            raise QuestionCycleStateError("CHECKPOINT_QUESTION_MISMATCH")
        # Immutable result is durable BEFORE the checkpoint advertises it.
        self.save_question_result(result)
        payload = {"authority": authority, "question_id": question_id,
                   "entry": entry, "delta": delta, "prerequisites": prerequisites}
        if len(canonical_json(payload).encode()) > 16 * 1024 * 1024:
            return  # bounded checkpoint; full cycle persistence remains authoritative
        immutable_json(self.directory / "checkpoints" / cycle_id / (question_id + ".json"),
                       {"payload": payload, "sha256": fingerprint(payload)})

    def load_checkpoint(self, cycle_id, authority, question_id, prerequisites):
        payload = self._checked_receipt(
            self.directory / "checkpoints" / cycle_id / (question_id + ".json"), authority)
        if payload is None:
            return None
        if payload["question_id"] != question_id or payload["prerequisites"] != prerequisites:
            raise QuestionCycleStateError("CHECKPOINT_DEPENDENCY_MISMATCH")
        self.verify_entry_history(payload["entry"])
        return payload

    def verify_entry_history(self, entry):
        result = CanonicalQuestionResult.from_dict(entry["result"])
        history = self.load_question_result(result.question_id, result.snapshot_id,
                                            result.evaluation_identity_digest)
        if history is None or canonical_json(history.to_dict()) != canonical_json(result.to_dict()):
            raise QuestionCycleStateError("REPLAY_IMMUTABLE_RESULT_MISMATCH")
        return result

    def save_replay_receipt(self, cycle, projection, authority):
        payload = {"authority": authority, "cycle_sha256": fingerprint(cycle.to_dict()),
                   "projection_sha256": fingerprint(projection)}
        immutable_json(self.directory / "replay" / (cycle.cycle_id + ".json"),
                       {"payload": payload, "sha256": fingerprint(payload)})

    def load_replay(self, cycle_id, authority):
        payload = self._checked_receipt(self.directory / "replay" / (cycle_id + ".json"), authority)
        if payload is None:
            return None
        cycle = self.load_cycle(cycle_id)
        projection = self.load_projection(cycle_id)
        if (cycle is None or fingerprint(cycle.to_dict()) != payload["cycle_sha256"]
                or fingerprint(projection) != payload["projection_sha256"]
                or set(projection["questions"]) != set(cycle.result_ids)
                or len(cycle.result_ids) != 70):
            raise QuestionCycleStateError("COMPLETED_REPLAY_MISMATCH")
        for qid, entry in projection["questions"].items():
            result = self.verify_entry_history(entry)
            if result.question_id != qid or cycle.result_ids[qid] != result.result_id:
                raise QuestionCycleStateError("COMPLETED_REPLAY_RESULT_MISMATCH")
        return cycle, projection

    def save_cycle(self, result: CanonicalQuestionCycleResult) -> None:
        immutable_json(self.cycle_path(result.cycle_id), result.to_dict())


__all__ = [
    "CanonicalQuestionCycleResult",
    "CanonicalQuestionResult",
    "DEFAULT_QUESTION_CYCLE_DIRECTORY",
    "LEGACY_QUESTION_RESULT_SCHEMAS",
    "QUESTION_CYCLE_SCHEMA",
    "QUESTION_RESULT_SCHEMA",
    "QuestionCycleStateError",
    "QuestionCycleStore",
]
