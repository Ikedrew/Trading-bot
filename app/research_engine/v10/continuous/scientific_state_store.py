"""Append-only authority for the continuous scientific-state bridge.

The modern optimisation registry remains the candidate/hypothesis current-state
authority.  This store owns versioned finding history, dependency transitions,
and immutable bridge-run receipts that the older registry cannot represent.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json


SCIENTIFIC_STATE_SCHEMA = "continuous_scientific_state_v1"
BRIDGE_RUN_SCHEMA = "scientific_state_bridge_run_v1"
DEFAULT_SCIENTIFIC_STATE_DIRECTORY = Path("data/research/scientific_state")


class ScientificStateStoreError(RuntimeError):
    """The authoritative bridge state is corrupt or could not be persisted."""


def _empty_document() -> dict[str, Any]:
    return {
        "schema": SCIENTIFIC_STATE_SCHEMA,
        "findings": {},
        "hypothesis_history": {},
        "candidate_history": {},
        "dependencies": {},
        "current": {"findings": {}, "hypotheses": {}, "candidates": {}},
        "reconciled_external_lineage": {},
    }


class ScientificStateStore:
    """Strict, restart-safe scientific state with immutable run receipts."""

    def __init__(self, directory: Path | str = DEFAULT_SCIENTIFIC_STATE_DIRECTORY):
        self.directory = Path(directory)
        self.state_path = self.directory / "state.json"
        self.runs_directory = self.directory / "runs"
        self._document = self._load()

    @property
    def document(self) -> dict[str, Any]:
        return deepcopy(self._document)

    def replace_document(self, document: Mapping[str, Any]) -> None:
        self._validate(document)
        self._document = deepcopy(dict(document))

    def run_path(self, bridge_run_id: str) -> Path:
        return self.runs_directory / f"{bridge_run_id}.json"

    def load_run(self, bridge_run_id: str) -> dict[str, Any] | None:
        path = self.run_path(bridge_run_id)
        if not path.exists():
            return None
        value = self._read(path, "BRIDGE_RUN_UNREADABLE")
        if value.get("run_schema") != BRIDGE_RUN_SCHEMA:
            raise ScientificStateStoreError("BRIDGE_RUN_SCHEMA_INVALID")
        return value

    def commit(self, document: Mapping[str, Any], run: Mapping[str, Any]) -> None:
        """Commit state then the immutable success receipt.

        The run receipt is the completion authority and is always written last.
        If receipt persistence fails the previous state is restored.  A crash
        between the two writes is restart-safe because rerunning deterministically
        recreates the same state and no completed receipt exists.
        """
        self._validate(document)
        if run.get("run_schema") != BRIDGE_RUN_SCHEMA:
            raise ScientificStateStoreError("BRIDGE_RUN_SCHEMA_INVALID")
        previous = self._document
        try:
            self._atomic(self.state_path, document)
            self._immutable(self.run_path(str(run["bridge_run_id"])), run)
        except Exception as exc:
            try:
                if previous == _empty_document() and not self.state_path.exists():
                    pass
                else:
                    self._atomic(self.state_path, previous)
            except Exception as restore_exc:
                raise ScientificStateStoreError(
                    f"SCIENTIFIC_STATE_COMMIT_AND_RECOVERY_FAILED:{exc}:{restore_exc}"
                ) from restore_exc
            raise ScientificStateStoreError(
                f"SCIENTIFIC_STATE_COMMIT_FAILED:{type(exc).__name__}:{exc}"
            ) from exc
        self._document = deepcopy(dict(document))

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _empty_document()
        value = self._read(self.state_path, "SCIENTIFIC_STATE_UNREADABLE")
        self._validate(value)
        return value

    @staticmethod
    def _validate(value: Mapping[str, Any]) -> None:
        if not isinstance(value, Mapping) or value.get("schema") != SCIENTIFIC_STATE_SCHEMA:
            raise ScientificStateStoreError("SCIENTIFIC_STATE_SCHEMA_INVALID")
        required = {
            "findings", "hypothesis_history", "candidate_history",
            "dependencies", "current", "reconciled_external_lineage",
        }
        if not required.issubset(value):
            raise ScientificStateStoreError("SCIENTIFIC_STATE_SECTIONS_MISSING")
        for key in required - {"current"}:
            if not isinstance(value[key], dict):
                raise ScientificStateStoreError("SCIENTIFIC_STATE_SECTION_INVALID:" + key)
        current = value["current"]
        if not isinstance(current, Mapping) or set(current) != {
            "findings", "hypotheses", "candidates"
        }:
            raise ScientificStateStoreError("SCIENTIFIC_STATE_CURRENT_INVALID")
        for finding_id, versions in value["findings"].items():
            if not isinstance(versions, list) or not versions:
                raise ScientificStateStoreError("FINDING_HISTORY_INVALID:" + finding_id)
            expected = list(range(1, len(versions) + 1))
            if [row.get("finding_version") for row in versions] != expected:
                raise ScientificStateStoreError("FINDING_VERSION_CHAIN_INVALID:" + finding_id)
            if any(row.get("finding_id") != finding_id for row in versions):
                raise ScientificStateStoreError("FINDING_IDENTITY_COLLISION:" + finding_id)
        for finding_id, links in value["dependencies"].items():
            if finding_id not in value["findings"] or not isinstance(links, Mapping):
                raise ScientificStateStoreError("DEPENDENCY_GRAPH_FINDING_INVALID:" + finding_id)
            hypotheses = links.get("hypotheses")
            candidates = links.get("candidates")
            if not isinstance(hypotheses, list) or not isinstance(candidates, list):
                raise ScientificStateStoreError("DEPENDENCY_GRAPH_LINKS_INVALID:" + finding_id)
            if len(hypotheses) != len(set(hypotheses)) or len(candidates) != len(set(candidates)):
                raise ScientificStateStoreError("DEPENDENCY_GRAPH_DUPLICATE_LINK:" + finding_id)
            if any(item not in current["hypotheses"] for item in hypotheses):
                raise ScientificStateStoreError("DEPENDENCY_GRAPH_HYPOTHESIS_MISSING:" + finding_id)
            if any(item not in current["candidates"] for item in candidates):
                raise ScientificStateStoreError("DEPENDENCY_GRAPH_CANDIDATE_MISSING:" + finding_id)

    @staticmethod
    def _read(path: Path, label: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ScientificStateStoreError(label + ":" + str(path)) from exc
        if not isinstance(value, dict):
            raise ScientificStateStoreError(label + ":NOT_OBJECT")
        return value

    @staticmethod
    def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
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

    @classmethod
    def _immutable(cls, path: Path, payload: Mapping[str, Any]) -> None:
        if path.exists():
            existing = cls._read(path, "IMMUTABLE_BRIDGE_RUN_UNREADABLE")
            if canonical_json(existing) != canonical_json(payload):
                raise ScientificStateStoreError("BRIDGE_RUN_IDENTITY_COLLISION")
            return
        cls._atomic(path, payload)


__all__ = [
    "BRIDGE_RUN_SCHEMA",
    "DEFAULT_SCIENTIFIC_STATE_DIRECTORY",
    "SCIENTIFIC_STATE_SCHEMA",
    "ScientificStateStore",
    "ScientificStateStoreError",
]
