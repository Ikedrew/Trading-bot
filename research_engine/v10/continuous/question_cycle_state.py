"""Immutable result/history state for canonical snapshot-bound question cycles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint


QUESTION_RESULT_SCHEMA = "canonical_question_result_v1"
QUESTION_CYCLE_SCHEMA = "canonical_question_cycle_v1"
DEFAULT_QUESTION_CYCLE_DIRECTORY = Path("reports/research/questions/_canonical_cycle")


class QuestionCycleStateError(RuntimeError):
    """Question history/current projection could not be safely persisted."""


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


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
    atomic_json(path, payload)


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
    failure_reason: str | None = None
    result_id: str = ""

    def identity_material(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("result_id", None)
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
        return {"result_schema": QUESTION_RESULT_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalQuestionResult":
        if value.get("result_schema") != QUESTION_RESULT_SCHEMA:
            raise QuestionCycleStateError("UNKNOWN_QUESTION_RESULT_SCHEMA")
        fields = dict(value)
        fields.pop("result_schema", None)
        for name in ("evidence_datasets", "evidence_references", "limitations", "missing_evidence"):
            fields[name] = tuple(fields.get(name) or ())
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

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_schema": QUESTION_CYCLE_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalQuestionCycleResult":
        if value.get("cycle_schema") != QUESTION_CYCLE_SCHEMA:
            raise QuestionCycleStateError("UNKNOWN_QUESTION_CYCLE_SCHEMA")
        fields = dict(value)
        fields.pop("cycle_schema", None)
        for name in ("changed_question_ids", "unchanged_question_ids", "failed_question_ids"):
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

    def question_result_path(self, question_id: str, snapshot_id: str) -> Path:
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
        path = self.question_result_path(result.question_id, result.snapshot_id)
        immutable_json(path, result.to_dict())
        return path

    def load_question_result(
        self, question_id: str, snapshot_id: str,
    ) -> CanonicalQuestionResult | None:
        path = self.question_result_path(question_id, snapshot_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise QuestionCycleStateError("QUESTION_RESULT_HISTORY_UNREADABLE") from exc
        result = CanonicalQuestionResult.from_dict(value)
        if result.question_id != question_id or result.snapshot_id != snapshot_id:
            raise QuestionCycleStateError("QUESTION_RESULT_HISTORY_IDENTITY_MISMATCH")
        return result

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

    def save_cycle(self, result: CanonicalQuestionCycleResult) -> None:
        immutable_json(self.cycle_path(result.cycle_id), result.to_dict())


__all__ = [
    "CanonicalQuestionCycleResult",
    "CanonicalQuestionResult",
    "DEFAULT_QUESTION_CYCLE_DIRECTORY",
    "QUESTION_CYCLE_SCHEMA",
    "QUESTION_RESULT_SCHEMA",
    "QuestionCycleStateError",
    "QuestionCycleStore",
]
