"""Immutable checkpoints for bounded continuous research invocations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json


CYCLE_SCHEMA = "continuous_research_cycle_v1"
DEFAULT_CYCLE_STATE_DIRECTORY = Path("data/research/continuous/cycles")


class ContinuousCycleStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContinuousResearchCycleResult:
    continuous_cycle_id: str
    cycle_outcome: str
    frontier_snapshot_id: str | None
    question_cycle_id: str | None
    bridge_run_id: str | None
    q71_agenda_id: str | None
    q71_queue_id: str | None
    validation_queue_version: str | None
    projection_version: str | None
    predecessor_cycle_id: str | None
    started_at: str
    completed_at: str
    stage_statuses: dict[str, str]
    failure_stage: str | None = None
    failure_reason: str | None = None
    validation_transitions: tuple[dict[str, Any], ...] = ()
    shadow_eligibility: tuple[dict[str, Any], ...] = ()
    projection_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_schema": CYCLE_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContinuousResearchCycleResult":
        if value.get("cycle_schema") != CYCLE_SCHEMA:
            raise ContinuousCycleStateError("CONTINUOUS_CYCLE_SCHEMA_INVALID")
        fields = dict(value)
        fields.pop("cycle_schema", None)
        fields["validation_transitions"] = tuple(fields.get("validation_transitions") or ())
        fields["shadow_eligibility"] = tuple(fields.get("shadow_eligibility") or ())
        return cls(**fields)


class ContinuousCycleStore:
    def __init__(self, directory: Path | str = DEFAULT_CYCLE_STATE_DIRECTORY):
        self.directory = Path(directory)
        self.history_directory = self.directory / "history"
        self.latest_path = self.directory / "latest_success.json"

    def load_latest_success(self) -> ContinuousResearchCycleResult | None:
        if not self.latest_path.exists():
            return None
        try:
            pointer = json.loads(self.latest_path.read_text(encoding="utf-8"))
            cycle_id = str(pointer["continuous_cycle_id"])
            history = json.loads((self.history_directory / f"{cycle_id}.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ContinuousCycleStateError("LATEST_CONTINUOUS_CYCLE_INVALID") from exc
        result = ContinuousResearchCycleResult.from_dict(history)
        if result.cycle_outcome not in {"COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"}:
            raise ContinuousCycleStateError("LATEST_POINTER_IS_NOT_SUCCESS")
        return result

    def save(self, result: ContinuousResearchCycleResult, *, successful: bool) -> Path:
        path = self.history_directory / f"{result.continuous_cycle_id}.json"
        self._immutable(path, result.to_dict())
        if successful:
            self._atomic(self.latest_path, {
                "cycle_schema": CYCLE_SCHEMA,
                "continuous_cycle_id": result.continuous_cycle_id,
                "cycle_outcome": result.cycle_outcome,
                "completed_at": result.completed_at,
                "projection_version": result.projection_version,
                "predecessor_cycle_id": result.predecessor_cycle_id,
            })
        return path

    @classmethod
    def _immutable(cls, path: Path, value: Mapping[str, Any]) -> None:
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ContinuousCycleStateError("CONTINUOUS_CYCLE_HISTORY_UNREADABLE") from exc
            if canonical_json(existing) != canonical_json(value):
                raise ContinuousCycleStateError("CONTINUOUS_CYCLE_IDENTITY_COLLISION")
            return
        cls._atomic(path, value)

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
            raise ContinuousCycleStateError("CONTINUOUS_CYCLE_PERSISTENCE_FAILED") from exc


__all__ = ["CYCLE_SCHEMA", "ContinuousCycleStateError", "ContinuousCycleStore",
           "ContinuousResearchCycleResult", "DEFAULT_CYCLE_STATE_DIRECTORY"]
