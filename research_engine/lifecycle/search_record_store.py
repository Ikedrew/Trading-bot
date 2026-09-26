"""
Search Record Store v1 -- deterministic, restart-safe persistence for the
immutable Wave 4 search history.

Stage 3 / Wave 4. This store is the ONLY writer of `SearchRecord` and
`SelectionFreeze` objects. It reuses the established local-authority persistence
pattern of this repository (the committed Wave 0 generated-research store):

    - one local JSON file per store (no network / S3 dependency);
    - deterministic canonical JSON serialisation (sorted keys, fixed separators);
    - atomic replace-on-write (temp file, fsync, `os.replace`) so a crash can
      never leave a half-written search history;
    - strict, fail-closed loading -- corrupted, conflicting or unverifiable
      content raises instead of being silently repaired or reinterpreted;
    - semantic deduplication on registration, so an equivalent COMPLETED search
      that is re-registered after a restart is not duplicated;
    - immutable completed history: a search identity is never rebound to
      different immutable semantics.

DEDUP SEMANTICS
===============
Two registrations with the same `SearchRecord` identity are the SAME search and
the second one returns the persisted record unchanged (its original
`created_at` provenance is preserved -- re-running a search is not re-deriving
it). Any material change -- a different discovery boundary, dataset fingerprint,
alternative set, parent research or search specification -- produces a
DIFFERENT search identity, which is therefore a NEW historical search and is
stored alongside the old one rather than replacing it.

NO IMPORT-TIME WRITES
---------------------
Importing this module performs no I/O beyond resolving a default path constant.
Records are written ONLY by an explicit `register()` call.

NO PRODUCTION AUTHORITY
-----------------------
The store reads and writes exactly one search-history JSON file. It has no path
to production configuration, baseline activation, candidate promotion, execution
or runtime strategy behaviour. `GovernanceGate` and the existing human-governed
candidate/promotion machinery remain the sole authorities and are neither
imported nor modified here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.search_provenance import (
    SEARCH_PROVENANCE_SCHEMA_VERSION,
    SearchIdentityConflict,
    SearchProvenanceError,
    SearchProvenanceValidationError,
    SearchRecord,
    SelectionFreeze,
)

# Local authority pattern (mirrors the Wave 0 generated-research store).
# Injectable per instance so tests never touch a real path.
DEFAULT_SEARCH_STORE_PATH = Path("logs/research_lifecycle/search_records.json")

# Top-level document marker. Bumping this is a clean-reset decision, never a
# silent migration.
STORE_FORMAT = "search_record_store_v1"


class SearchRecordStore:
    """
    Persistent store of immutable governed search history.

    Registration is the only mutating operation and is always explicit.
    Equivalent searches deduplicate to the existing record; the original
    creation timestamp is preserved, because re-discovering a search is not
    re-creating it.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_SEARCH_STORE_PATH
        self._by_id: dict[str, SearchRecord] = {}
        self._by_identity: dict[str, str] = {}   # semantic_identity -> search identity
        self._freezes: dict[str, SelectionFreeze] = {}
        self._load()

    # ─── Introspection ──────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, search_identity: object) -> bool:
        return search_identity in self._by_id

    def all(self) -> tuple[SearchRecord, ...]:
        """All records, deterministically ordered by search identity."""
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, search_identity: str) -> SearchRecord | None:
        """Deterministic retrieval by search identity."""
        if not isinstance(search_identity, str) or not search_identity:
            raise SearchProvenanceValidationError(
                "search_identity must be a non-empty string")
        return self._by_id.get(search_identity)

    def get_by_semantic_identity(self, semantic_identity: str) -> SearchRecord | None:
        """Deterministic retrieval / dedup by deterministic semantic identity."""
        if not isinstance(semantic_identity, str) or not semantic_identity:
            raise SearchProvenanceValidationError(
                "semantic_identity must be a non-empty string")
        search_identity = self._by_identity.get(semantic_identity)
        return self._by_id.get(search_identity) if search_identity else None

    def get_freeze(self, freeze_identity: str) -> SelectionFreeze | None:
        return self._freezes.get(freeze_identity)

    def freezes_for(self, search_identity: str) -> tuple[SelectionFreeze, ...]:
        return tuple(
            self._freezes[key] for key in sorted(self._freezes)
            if self._freezes[key].search_identity == search_identity)

    # ─── Mutation (explicit action only) ────────────────────────────────

    def register(self, record: SearchRecord) -> SearchRecord:
        """
        Register a governed search. Returns the persisted record.

        Duplicate-safe by construction: an equivalent search resolves to the
        existing record and is not written twice. A presented search identity
        that already exists with different immutable semantics fails closed.
        """
        if not isinstance(record, SearchRecord):
            raise SearchProvenanceValidationError(
                f"expected SearchRecord, got {type(record).__name__}")

        existing_id = self._by_identity.get(record.semantic_identity)
        if existing_id is not None:
            existing = self._by_id[existing_id]
            if existing.search_identity != record.search_identity:
                raise SearchIdentityConflict(
                    "search semantic identity is already bound to a different search")
            return existing  # deduplicated: original provenance/timestamp preserved

        if record.search_identity in self._by_id:
            raise SearchIdentityConflict(
                f"search identity {record.search_identity} already exists with "
                "different immutable semantics")

        self._by_id[record.search_identity] = record
        self._by_identity[record.semantic_identity] = record.search_identity
        self._persist()
        return record

    def register_freeze(self, freeze: SelectionFreeze) -> SelectionFreeze:
        """
        Register a frozen T0 selection against an already-registered search.

        A freeze may only be persisted for a search this store already holds: a
        selection cannot be frozen against a search that was never recorded,
        because there would be no alternatives to prove were considered.
        """
        if not isinstance(freeze, SelectionFreeze):
            raise SearchProvenanceValidationError(
                f"expected SelectionFreeze, got {type(freeze).__name__}")
        if freeze.search_identity not in self._by_id:
            raise SearchProvenanceValidationError(
                "a selection freeze may only be registered for an already-recorded "
                "search; the search provenance must be persisted first")
        existing = self._freezes.get(freeze.freeze_identity)
        if existing is not None:
            if existing.semantic_identity != freeze.semantic_identity:
                raise SearchIdentityConflict(
                    f"freeze identity {freeze.freeze_identity} already exists with "
                    "different immutable semantics")
            return existing
        self._freezes[freeze.freeze_identity] = freeze
        self._persist()
        return freeze

    # ─── Persistence ────────────────────────────────────────────────────

    def _document(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            "schema_version": SEARCH_PROVENANCE_SCHEMA_VERSION,
            "records": [record.to_dict() for record in self.all()],
            "freezes": [self._freezes[key].to_dict() for key in sorted(self._freezes)],
        }

    def _persist(self) -> None:
        payload = canonical_json(self._document()) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _load(self) -> None:
        """
        Load persisted history. Fails closed: never silently repairs, skips or
        reinterprets corrupted, conflicting or unverifiable content.
        """
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise SearchProvenanceError(
                f"search record store is unreadable: {self._path}: {exc}") from exc

        if not isinstance(document, Mapping):
            raise SearchProvenanceError(
                f"search record store must be a JSON object: {self._path}")
        if document.get("format") != STORE_FORMAT:
            raise SearchProvenanceError(
                f"unknown search record store format: {document.get('format')!r}")
        if document.get("schema_version") != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"unsupported search record store schema_version: "
                f"{document.get('schema_version')!r}")

        rows = document.get("records")
        if not isinstance(rows, list):
            raise SearchProvenanceError("search record store 'records' must be a list")

        staged: dict[str, SearchRecord] = {}
        identities: dict[str, str] = {}
        for position, row in enumerate(rows):
            try:
                record = SearchRecord.from_dict(row)
            except SearchProvenanceError as exc:
                raise type(exc)(
                    f"malformed search record at position {position}: {exc}") from exc
            if record.search_identity in staged:
                raise SearchIdentityConflict(
                    f"duplicate search identity in store: {record.search_identity}")
            bound = identities.get(record.semantic_identity)
            if bound is not None and bound != record.search_identity:
                raise SearchIdentityConflict(
                    f"semantic identity {record.semantic_identity} is bound to two "
                    f"searches in store: {bound} / {record.search_identity}")
            staged[record.search_identity] = record
            identities[record.semantic_identity] = record.search_identity

        freeze_rows = document.get("freezes")
        freezes: dict[str, SelectionFreeze] = {}
        if freeze_rows is not None:
            if not isinstance(freeze_rows, list):
                raise SearchProvenanceError(
                    "search record store 'freezes' must be a list")
            for position, row in enumerate(freeze_rows):
                try:
                    freeze = SelectionFreeze.from_dict(row)
                except SearchProvenanceError as exc:
                    raise type(exc)(
                        f"malformed selection freeze at position {position}: {exc}"
                    ) from exc
                if freeze.freeze_identity in freezes:
                    raise SearchIdentityConflict(
                        f"duplicate selection freeze identity: {freeze.freeze_identity}")
                if freeze.search_identity not in staged:
                    raise SearchProvenanceValidationError(
                        "persisted selection freeze references a search that is not "
                        "in the store; a freeze may never outlive its search record")
                freezes[freeze.freeze_identity] = freeze

        self._by_id = staged
        self._by_identity = identities
        self._freezes = freezes


__all__ = [
    "DEFAULT_SEARCH_STORE_PATH",
    "STORE_FORMAT",
    "SearchRecordStore",
]

STORE_FORMAT = "search_record_store_v1"

