"""
Generated Research Store v1 — deterministic, restart-safe persistence for
generated research identities.

Stage ③ / Wave 0 foundation. This store is the ONLY writer of generated
research records. It reuses the established local-authority persistence
pattern of this repository:

    - one local JSON file per store (no network/S3 dependency at Wave 0);
    - deterministic canonical JSON serialisation (sorted keys, fixed separators);
    - atomic replace-on-write (write temp, fsync, os.replace) so a crash can
      never leave a half-written identity store;
    - strict, fail-closed loading — corrupted or conflicting content raises
      instead of being silently repaired or reinterpreted;
    - deterministic retrieval by generated research ID and by semantic identity.

NO IMPORT-TIME WRITES
---------------------
Importing this module performs no I/O beyond resolving a default path
constant. Records are written ONLY by an explicit `register()` call.

NO PRODUCTION AUTHORITY
-----------------------
The store reads and writes exactly one generated-research JSON file. It has no
path to production configuration, baseline activation, candidate promotion,
execution, or runtime strategy behaviour. `GovernanceGate` and the existing
human-governed candidate/promotion machinery remain the sole authorities and
are not imported or modified here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    GENERATED_RESEARCH_SCHEMA_VERSION,
    GeneratedResearchError,
    GeneratedResearchIdentityConflict,
    GeneratedResearchNamespaceViolation,
    GeneratedResearchProposal,
    GeneratedResearchRecord,
    GeneratedResearchValidationError,
    canonical_json,
    is_generated_research_id,
)

# Local authority pattern (mirrors lifecycle/logs/research_lifecycle). Injectable
# per instance so tests never touch a real path.
DEFAULT_STORE_PATH = Path("logs/research_lifecycle/generated_research.jsonl.json")

# Top-level document marker. Bumping this is a clean-reset decision, never a
# silent migration.
STORE_FORMAT = "generated_research_store_v1"


class GeneratedResearchStore:
    """
    Persistent store of immutable generated research identities.

    Registration is the only mutating operation and is always explicit.
    Equivalent proposals deduplicate to the existing record (the original
    creation timestamp is preserved — re-discovery is not re-creation).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_STORE_PATH
        self._by_id: dict[str, GeneratedResearchRecord] = {}
        self._by_identity: dict[str, str] = {}   # semantic_identity -> generated_research_id
        self._load()

    # ─── Introspection ──────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, generated_research_id: object) -> bool:
        return generated_research_id in self._by_id

    def all(self) -> tuple[GeneratedResearchRecord, ...]:
        """All records, deterministically ordered by generated research ID."""
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, generated_research_id: str) -> GeneratedResearchRecord | None:
        """Deterministic retrieval by generated research ID."""
        if not isinstance(generated_research_id, str) or not generated_research_id:
            raise GeneratedResearchValidationError(
                "generated_research_id must be a non-empty string")
        return self._by_id.get(generated_research_id)

    def get_by_semantic_identity(
        self, semantic_identity: str,
    ) -> GeneratedResearchRecord | None:
        """Deterministic retrieval / dedup by deterministic semantic identity."""
        if not isinstance(semantic_identity, str) or not semantic_identity:
            raise GeneratedResearchValidationError(
                "semantic_identity must be a non-empty string")
        generated_research_id = self._by_identity.get(semantic_identity)
        return self._by_id.get(generated_research_id) if generated_research_id else None

    # ─── Mutation (explicit action only) ────────────────────────────────

    def register(
        self,
        proposal: GeneratedResearchProposal,
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> GeneratedResearchRecord:
        """
        Register a generated research identity. Returns the persisted record.

        Duplicate-safe by construction: an equivalent proposal resolves to the
        existing record and is not written twice. A presented ID that already
        exists with different immutable semantics fails closed, as does any
        attempt to place generated research inside the canonical namespace.
        """
        record = GeneratedResearchRecord.create(proposal, provenance=provenance)
        self._assert_generated_namespace(record.generated_research_id)

        existing_id = self._by_identity.get(record.semantic_identity)
        if existing_id is not None:
            existing = self._by_id[existing_id]
            if existing.generated_research_id != record.generated_research_id:
                raise GeneratedResearchIdentityConflict(
                    "semantic identity is already bound to a different generated ID")
            return existing  # deduplicated: original provenance/timestamp preserved

        if record.generated_research_id in self._by_id:
            raise GeneratedResearchIdentityConflict(
                f"generated research ID {record.generated_research_id} already exists "
                "with different immutable semantics")

        self._by_id[record.generated_research_id] = record
        self._by_identity[record.semantic_identity] = record.generated_research_id
        self._persist()
        return record

    def register_record(self, record: GeneratedResearchRecord) -> GeneratedResearchRecord:
        """
        Persist an already-built record (deterministic replay path).

        The record is re-validated and re-bound to its own identity, so a
        conflicting or malformed record can never enter the store.
        """
        if not isinstance(record, GeneratedResearchRecord):
            raise GeneratedResearchValidationError(
                f"expected GeneratedResearchRecord, got {type(record).__name__}")
        record.validate()
        self._assert_generated_namespace(record.generated_research_id)

        existing = self._by_id.get(record.generated_research_id)
        if existing is not None:
            if existing.semantic_identity != record.semantic_identity:
                raise GeneratedResearchIdentityConflict(
                    f"generated research ID {record.generated_research_id} already exists "
                    "with different immutable semantics")
            return existing

        bound = self._by_identity.get(record.semantic_identity)
        if bound is not None and bound != record.generated_research_id:
            raise GeneratedResearchIdentityConflict(
                "semantic identity is already bound to a different generated ID")

        self._by_id[record.generated_research_id] = record
        self._by_identity[record.semantic_identity] = record.generated_research_id
        self._persist()
        return record

    # ─── Namespace isolation ────────────────────────────────────────────

    @staticmethod
    def _assert_generated_namespace(generated_research_id: str) -> None:
        """
        Reject any ID that is not unambiguously inside the reserved namespace.

        Generated research must never be able to pose as, or collide with, a
        canonical question ID. Imported lazily to keep this module free of any
        import-time dependency on the canonical registry.
        """
        from research_engine.lifecycle.generated_research_isolation import (
            assert_generated_research_id_isolated,
        )

        assert_generated_research_id_isolated(generated_research_id)

    # ─── Persistence ────────────────────────────────────────────────────

    def _document(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            "schema_version": GENERATED_RESEARCH_SCHEMA_VERSION,
            "id_prefix": GENERATED_RESEARCH_ID_PREFIX,
            "records": [record.to_dict() for record in self.all()],
        }

    def _persist(self) -> None:
        """Deterministic atomic write: temp file, fsync, then os.replace."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json(self._document()) + "\n"
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
        Load persisted records. Fails closed: never silently repairs, skips or
        reinterprets corrupted, conflicting or unverifiable content.
        """
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratedResearchError(
                f"generated research store is unreadable: {self._path}: {exc}") from exc

        if not isinstance(document, Mapping):
            raise GeneratedResearchError(
                f"generated research store must be a JSON object: {self._path}")
        if document.get("format") != STORE_FORMAT:
            raise GeneratedResearchError(
                f"unknown generated research store format: {document.get('format')!r}")
        if document.get("schema_version") != GENERATED_RESEARCH_SCHEMA_VERSION:
            raise GeneratedResearchValidationError(
                f"unsupported generated research store schema_version: "
                f"{document.get('schema_version')!r}")
        if document.get("id_prefix") != GENERATED_RESEARCH_ID_PREFIX:
            raise GeneratedResearchError(
                f"unexpected generated research id prefix: {document.get('id_prefix')!r}")

        rows = document.get("records")
        if not isinstance(rows, list):
            raise GeneratedResearchError("generated research store 'records' must be a list")

        staged: dict[str, GeneratedResearchRecord] = {}
        identities: dict[str, str] = {}
        for position, row in enumerate(rows):
            try:
                record = GeneratedResearchRecord.from_dict(row)
            except GeneratedResearchError as exc:
                # Preserve the specific fail-closed type (validation vs identity
                # conflict) and add the position — never silently skip or repair.
                raise type(exc)(
                    f"malformed generated research record at position {position}: {exc}"
                ) from exc
            self._assert_generated_namespace(record.generated_research_id)

            if record.generated_research_id in staged:
                raise GeneratedResearchIdentityConflict(
                    f"duplicate generated research ID in store: {record.generated_research_id}")
            bound = identities.get(record.semantic_identity)
            if bound is not None and bound != record.generated_research_id:
                raise GeneratedResearchIdentityConflict(
                    f"semantic identity {record.semantic_identity} is bound to two "
                    f"generated IDs in store: {bound} / {record.generated_research_id}")
            staged[record.generated_research_id] = record
            identities[record.semantic_identity] = record.generated_research_id

        self._by_id = staged
        self._by_identity = identities


__all__ = [
    "DEFAULT_STORE_PATH",
    "GeneratedResearchStore",
    "STORE_FORMAT",
    "is_generated_research_id",
]
