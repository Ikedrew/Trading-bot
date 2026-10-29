"""Restart-safe immutable persistence for Wave 8 coverage snapshots."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_coverage import (
    CoverageSnapshot, ResearchCoverageIdentityConflict,
    ResearchCoverageValidationError, is_coverage_snapshot_identity,
)

DEFAULT_RESEARCH_COVERAGE_STORE_PATH = Path("logs/research_lifecycle/research_coverage.json")
STORE_FORMAT = "research_coverage_store_v1"


class ResearchCoverageStore:
    """Append-only semantic history; writes happen only on registration."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_RESEARCH_COVERAGE_STORE_PATH
        self._snapshots: dict[str, CoverageSnapshot] = {}
        self._by_semantic: dict[str, str] = {}
        self._load()

    @property
    def path(self) -> Path: return self._path
    def __len__(self) -> int: return len(self._snapshots)
    def snapshots(self) -> tuple[CoverageSnapshot, ...]: return tuple(self._snapshots[k] for k in sorted(self._snapshots))

    def get(self, identity: str) -> CoverageSnapshot | None:
        if not is_coverage_snapshot_identity(identity): raise ResearchCoverageValidationError("invalid CVS identity")
        return self._snapshots.get(identity)

    def register(self, snapshot: CoverageSnapshot) -> CoverageSnapshot:
        if not isinstance(snapshot, CoverageSnapshot): raise ResearchCoverageValidationError("expected CoverageSnapshot")
        bound = self._by_semantic.get(snapshot.semantic_identity)
        if bound:
            existing = self._snapshots[bound]
            if existing.coverage_snapshot_identity != snapshot.coverage_snapshot_identity: raise ResearchCoverageIdentityConflict("semantic snapshot identity rebound")
            return existing
        if snapshot.coverage_snapshot_identity in self._snapshots: raise ResearchCoverageIdentityConflict("snapshot identity already has other semantics")
        self._snapshots[snapshot.coverage_snapshot_identity] = snapshot
        self._by_semantic[snapshot.semantic_identity] = snapshot.coverage_snapshot_identity
        try: self._persist()
        except Exception:
            self._snapshots.pop(snapshot.coverage_snapshot_identity, None); self._by_semantic.pop(snapshot.semantic_identity, None)
            raise
        return snapshot

    register_snapshot = register

    def _document(self) -> dict[str, Any]:
        return {"format": STORE_FORMAT, "schema_version": 1,
                "snapshots": [self._snapshots[k].to_dict() for k in sorted(self._snapshots)]}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        payload = canonical_json(self._document()) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp, self._path)
            try:
                descriptor = os.open(str(self._path.parent), os.O_RDONLY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
            except OSError:
                pass
        finally:
            if temp.exists(): temp.unlink()

    def _load(self) -> None:
        if not self._path.exists(): return
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchCoverageValidationError("coverage store is corrupted") from exc
        if not isinstance(document, dict) or set(document) != {"format", "schema_version", "snapshots"} or document["format"] != STORE_FORMAT or document["schema_version"] != 1 or not isinstance(document["snapshots"], list):
            raise ResearchCoverageValidationError("invalid coverage store document")
        for raw in document["snapshots"]:
            snapshot = CoverageSnapshot.from_dict(raw)
            if snapshot.coverage_snapshot_identity in self._snapshots or snapshot.semantic_identity in self._by_semantic:
                raise ResearchCoverageIdentityConflict("duplicate snapshot in store")
            self._snapshots[snapshot.coverage_snapshot_identity] = snapshot
            self._by_semantic[snapshot.semantic_identity] = snapshot.coverage_snapshot_identity
