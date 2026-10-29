"""
Research Protocol Store v1 -- deterministic, restart-safe persistence for Wave 6.

Stage 3 / Wave 6. This store is the ONLY writer of `ResearchProtocol` and
`ProtocolFreeze` records. It reuses the established local-authority persistence
pattern of this repository (the committed Wave 0 generated-research store, the
Wave 4 search-record store and the Wave 5 agenda store):

    - one local JSON file per store (no network / S3 dependency);
    - deterministic canonical JSON serialisation (sorted keys, fixed separators);
    - atomic replace-on-write (temp file, fsync, `os.replace`) so a crash can
      never leave a half-written protocol history;
    - strict, fail-closed loading -- corrupted, conflicting, orphaned or
      unverifiable content raises instead of being silently repaired;
    - semantic deduplication on registration, so rebuilding a semantically
      identical protocol after a restart is not duplicated;
    - immutable history: a protocol identity is never rebound to different
      immutable semantics, and a change creates a NEW protocol rather than
      overwriting the old one.

DEDUP AND HISTORY SEMANTICS
===========================
Two registrations with the same identity are the SAME protocol and the second
returns the persisted record unchanged, preserving its original `created_at` --
re-deriving a protocol is not re-creating it. Any material change -- a different
dimension, comparison, operation, criterion or evidence boundary -- produces a
DIFFERENT identity, which is therefore a NEW historical protocol stored alongside
the old one.

ORPHAN REFERENCES FAIL CLOSED
=============================
A freeze may only be persisted for a protocol this store already holds, and a
superseding protocol may only be persisted once the protocol it supersedes is
present: a record of "what investigating this means" cannot outlive or precede
the contract it corrects, and a corrected contract cannot claim a predecessor the
store has never seen.

NO IMPORT-TIME WRITES
---------------------
Importing this module performs no I/O beyond resolving a default path constant.
Records are written ONLY by an explicit `register_*` call.

NO PRODUCTION AUTHORITY
-----------------------
The store reads and writes exactly one protocol-history JSON file. It has no path
to production configuration, baseline activation, candidate promotion, execution
or runtime strategy behaviour, and it registers nothing on import.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_opportunity import ResearchOpportunity
from research_engine.lifecycle.research_protocol import (
    RESEARCH_PROTOCOL_SCHEMA_VERSION,
    ProtocolAuthorisationError,
    ProtocolFreeze,
    ResearchProtocol,
    ResearchProtocolError,
    is_protocol_freeze_identity,
    is_research_protocol_identity,
)

# Local authority pattern (mirrors the Wave 0 / Wave 4 / Wave 5 stores).
# Injectable per instance so tests never touch a real path.
DEFAULT_RESEARCH_PROTOCOL_STORE_PATH = Path(
    "logs/research_lifecycle/research_protocols.json")

# Top-level document marker. Bumping this is a clean-reset decision, never a
# silent migration.
STORE_FORMAT = "research_protocol_store_v1"


class ResearchProtocolStore:
    """
    Persistent store of immutable Wave 6 protocol history.

    Registration is the only mutating operation and is always explicit. Equivalent
    records deduplicate to the existing record; the original creation timestamp is
    preserved, because re-deriving a protocol is not re-creating it.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = (
            Path(path) if path is not None else DEFAULT_RESEARCH_PROTOCOL_STORE_PATH)
        self._protocols: dict[str, ResearchProtocol] = {}
        self._protocol_by_semantic: dict[str, str] = {}
        self._freezes: dict[str, ProtocolFreeze] = {}
        self._load()

    # ─── Introspection ──────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._protocols)

    def __contains__(self, protocol_identity: object) -> bool:
        return protocol_identity in self._protocols

    def protocols(self) -> tuple[ResearchProtocol, ...]:
        """All protocols, ordered deterministically by protocol identity."""
        return tuple(self._protocols[key] for key in sorted(self._protocols))

    def freezes(self) -> tuple[ProtocolFreeze, ...]:
        """All T0 protocol freezes, ordered deterministically by freeze identity."""
        return tuple(self._freezes[key] for key in sorted(self._freezes))

    def get_protocol(self, protocol_identity: str) -> ResearchProtocol | None:
        return self._protocols.get(protocol_identity)

    def get_protocol_by_semantic_identity(
            self, semantic_identity: str) -> ResearchProtocol | None:
        identity = self._protocol_by_semantic.get(semantic_identity)
        return self._protocols.get(identity) if identity else None

    def get_freeze(self, freeze_identity: str) -> ProtocolFreeze | None:
        return self._freezes.get(freeze_identity)

    def freezes_for(
            self, protocol_identity: str) -> tuple[ProtocolFreeze, ...]:
        return tuple(
            self._freezes[key] for key in sorted(self._freezes)
            if self._freezes[key].protocol_identity == protocol_identity)

    def supersessions(self) -> tuple[tuple[str, str], ...]:
        """
        Every recorded supersession as `(superseding, superseded)`, in
        deterministic order.

        The OLD protocol is always retained alongside the new one, so this is a
        readable history rather than a replacement.
        """
        return tuple(
            (item.protocol_identity, item.supersedes)
            for item in self.protocols() if item.supersedes)


    # ─── Mutation (explicit action only) ────────────────────────────────

    def register_protocol(
        self,
        protocol: ResearchProtocol,
        *,
        opportunity: ResearchOpportunity,
    ) -> ResearchProtocol:
        """
        Register a governed protocol, PROVING its authorisation at the same time.

        The `opportunity` argument is MANDATORY, not optional. This is the
        mechanical form of "a protocol may never be free-floating": there is no
        call that can persist a protocol without presenting the Wave 5
        opportunity it claims to authorise, and `assert_binds_to` re-proves the
        identity, state, subject, evidence boundary and Wave 4 provenance before
        anything is written.

        Duplicate-safe: an equivalent protocol resolves to the persisted record and
        is not written twice. A presented identity that already exists with
        different immutable semantics fails closed.

        Registering a protocol runs no research, creates no candidate and applies
        no treatment. It writes one line of governed history.
        """
        if not isinstance(protocol, ResearchProtocol):
            raise ResearchProtocolError(
                f"expected ResearchProtocol, got {type(protocol).__name__}")
        if not isinstance(opportunity, ResearchOpportunity):
            raise ProtocolAuthorisationError(
                f"a protocol may only be registered together with the governed "
                f"ResearchOpportunity it authorises, got {type(opportunity).__name__}")
        protocol.assert_binds_to(opportunity)

        existing_id = self._protocol_by_semantic.get(protocol.semantic_identity)
        if existing_id is not None:
            existing = self._protocols[existing_id]
            if existing.protocol_identity != protocol.protocol_identity:
                raise ResearchProtocolError(
                    "protocol semantic identity is already bound to a different "
                    "protocol")
            return existing
        if protocol.protocol_identity in self._protocols:
            raise ResearchProtocolError(
                f"protocol identity {protocol.protocol_identity} already exists with "
                f"different immutable semantics")
        # A superseding protocol may not precede the protocol it supersedes.
        if protocol.supersedes and protocol.supersedes not in self._protocols:
            raise ResearchProtocolError(
                f"protocol {protocol.protocol_identity} supersedes "
                f"{protocol.supersedes}, which is not in this store; a corrected "
                f"contract may not claim a predecessor that was never recorded")
        self._protocols[protocol.protocol_identity] = protocol
        self._protocol_by_semantic[protocol.semantic_identity] = (
            protocol.protocol_identity)
        self._persist()
        return protocol

    def register_freeze(
        self,
        freeze: ProtocolFreeze,
        *,
        protocol: ResearchProtocol,
    ) -> ProtocolFreeze:
        """
        Register a T0 protocol freeze against an already-registered protocol.

        A freeze may only be persisted for a protocol this store already holds,
        and `assert_belongs_to` re-derives the expected freeze from the protocol so
        a snapshot can never drift from the contract it claims to have frozen.
        """
        if not isinstance(freeze, ProtocolFreeze):
            raise ResearchProtocolError(
                f"expected ProtocolFreeze, got {type(freeze).__name__}")
        if not isinstance(protocol, ResearchProtocol):
            raise ResearchProtocolError(
                f"expected ResearchProtocol, got {type(protocol).__name__}")
        if protocol.protocol_identity not in self._protocols:
            raise ResearchProtocolError(
                "a protocol freeze may only be registered for an already-recorded "
                "protocol; the protocol provenance must be persisted first")
        if freeze.protocol_identity != protocol.protocol_identity:
            raise ResearchProtocolError(
                f"freeze {freeze.freeze_identity} binds protocol "
                f"{freeze.protocol_identity}, not {protocol.protocol_identity}")
        freeze.assert_belongs_to(protocol)
        existing = self._freezes.get(freeze.freeze_identity)
        if existing is not None:
            if existing.semantic_identity != freeze.semantic_identity:
                raise ResearchProtocolError(
                    f"freeze identity {freeze.freeze_identity} already exists with "
                    f"different immutable semantics")
            return existing
        self._freezes[freeze.freeze_identity] = freeze
        self._persist()
        return freeze


    # ─── Persistence ────────────────────────────────────────────────────

    def _document(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            "protocol_schema_version": RESEARCH_PROTOCOL_SCHEMA_VERSION,
            "protocols": [item.to_dict() for item in self.protocols()],
            "freezes": [item.to_dict() for item in self.freezes()],
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
        reinterprets corrupted, conflicting, orphaned or unverifiable content.
        """
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchProtocolError(
                f"research protocol store is unreadable: {self._path}: {exc}") from exc

        if not isinstance(document, Mapping):
            raise ResearchProtocolError(
                f"research protocol store must be a JSON object: {self._path}")
        if document.get("format") != STORE_FORMAT:
            raise ResearchProtocolError(
                f"unknown research protocol store format: {document.get('format')!r}")
        if "protocol_schema_version" not in document:
            raise ResearchProtocolError(
                f"research protocol store is missing 'protocol_schema_version'; the "
                f"document is not a complete {STORE_FORMAT} record")
        if document.get("protocol_schema_version") != RESEARCH_PROTOCOL_SCHEMA_VERSION:
            raise ResearchProtocolError(
                f"unsupported research protocol store protocol_schema_version: "
                f"{document.get('protocol_schema_version')!r}")

        protocols = self._load_section(
            document, "protocols", ResearchProtocol.from_dict,
            is_research_protocol_identity, "protocol", "protocol_identity")
        freezes = self._load_section(
            document, "freezes", ProtocolFreeze.from_dict,
            is_protocol_freeze_identity, "protocol freeze", "freeze_identity")

        for freeze in freezes.values():
            if freeze.protocol_identity not in protocols:
                raise ResearchProtocolError(
                    f"persisted protocol freeze {freeze.freeze_identity} references "
                    f"protocol {freeze.protocol_identity}, which is not in the store; "
                    f"a T0 freeze may never outlive its protocol")
            freeze.assert_belongs_to(protocols[freeze.protocol_identity])
        for protocol in protocols.values():
            if protocol.supersedes and protocol.supersedes not in protocols:
                raise ResearchProtocolError(
                    f"persisted protocol {protocol.protocol_identity} supersedes "
                    f"{protocol.supersedes}, which is not in the store; an orphaned "
                    f"supersession reference may never be repaired silently")

        self._protocols = protocols
        self._protocol_by_semantic = {
            row.semantic_identity: row.protocol_identity
            for row in protocols.values()}
        self._freezes = freezes

    def _load_section(
            self, document, name, from_dict, predicate, label, identity_attr):
        """
        Parse and stage one persisted section, failing closed on every defect.

        `identity_attr` is passed explicitly rather than sniffed: both record types
        carry more than one `*_identity` attribute, so guessing would mis-key the
        store.
        """
        rows = document.get(name)
        if not isinstance(rows, list):
            raise ResearchProtocolError(
                f"research protocol store {name!r} must be a list")
        staged: dict[str, Any] = {}
        for position, row in enumerate(rows):
            try:
                record = from_dict(row)
            except ResearchProtocolError as exc:
                # Preserve the specific fail-closed type and add the position.
                raise type(exc)(f"malformed {label} at position {position}: {exc}") from exc
            identity = getattr(record, identity_attr, None)
            if not isinstance(identity, str) or not predicate(identity):
                raise ResearchProtocolError(
                    f"persisted {label} {identity!r} is outside the governed "
                    f"{identity_attr} namespace")
            if identity in staged:
                raise ResearchProtocolError(
                    f"duplicate {label} identity in store: {identity}")
            staged[identity] = record
        return staged


__all__ = [
    "DEFAULT_RESEARCH_PROTOCOL_STORE_PATH",
    "STORE_FORMAT",
    "ResearchProtocolStore",
]

