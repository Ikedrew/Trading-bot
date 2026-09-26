"""
Treatment Memory Store v1 -- deterministic, restart-safe persistence for
Wave 7.

Stage 3 / Wave 7. This store is the ONLY writer of `TreatmentMemoryRecord`,
`RevisitPolicy` and `RevisitDecision` records. It reuses the established
local-authority persistence pattern of this repository (the committed Wave 0
generated-research store, the Wave 4 search-record store, the Wave 5 agenda
store and the Wave 6 protocol store):

    - one local JSON file per store (no network, no S3);
    - deterministic canonical JSON serialisation (sorted keys, fixed
      separators);
    - atomic replace-on-write (temp file, fsync, os.replace) so a crash can
      never leave a half-written research history;
    - strict, fail-closed loading -- corrupted, conflicting, orphaned or
      unverifiable content raises instead of being silently repaired;
    - semantic deduplication on registration, so re-deriving an equivalent
      memory after a restart is not duplicated;
    - immutable history: a memory identity is never rebound to different
      immutable semantics.

DEDUP AND HISTORY SEMANTICS
===========================
Two registrations with the same identity are the SAME memory, and the second
returns the persisted record unchanged with its original `created_at`:
re-recording a conclusion is not re-concluding it. Any material change -- a
different disposition, a different evidence boundary, a different population --
produces a DIFFERENT identity, which is therefore a NEW historical record stored
alongside the old one.

CONFLICTING MEMORIES ARE PRESERVED
==================================
Two records that disagree about the same treatment are two identities, and both
are stored. The store never picks a winner, never merges them, and never drops
one. Superseded records are preserved for the same reason.

NO IMPORT-TIME WRITES
---------------------
Importing this module performs no I/O beyond resolving a default path constant.
Records are written ONLY by an explicit `register_*` call.

NO PRODUCTION AUTHORITY
-----------------------
The store reads and writes exactly one treatment-memory JSON file. It has no
path to production configuration, baseline activation, candidate promotion,
execution or runtime strategy behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.revisit_governance import (
    RevisitDecision,
    RevisitGovernanceError,
    RevisitPolicy,
    is_revisit_decision_identity,
    is_revisit_policy_identity,
)
from research_engine.lifecycle.treatment_memory import (
    TREATMENT_MEMORY_SCHEMA_VERSION,
    TreatmentMemoryError,
    TreatmentMemoryIdentityConflict,
    TreatmentMemoryRecord,
    TreatmentMemoryValidationError,
    is_treatment_memory_identity,
)

# Local authority pattern (mirrors the Wave 0 / 4 / 5 / 6 stores). Injectable
# per instance so tests never touch a real path.
DEFAULT_TREATMENT_MEMORY_STORE_PATH = Path(
    "logs/research_lifecycle/treatment_memory.json")

# Top-level document marker. Bumping this is a clean-reset decision, never a
# silent migration.
STORE_FORMAT = "treatment_memory_store_v1"


class TreatmentMemoryStore:
    """
    Persistent store of immutable Wave 7 treatment-research history.

    Registration is the only mutating operation and is always explicit.
    Equivalent records deduplicate to the existing record with the original
    creation provenance preserved, because re-recording a conclusion is not
    re-concluding it.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = (Path(path) if path is not None
                      else DEFAULT_TREATMENT_MEMORY_STORE_PATH)
        self._memories: dict[str, TreatmentMemoryRecord] = {}
        self._memory_by_semantic: dict[str, str] = {}
        self._policies: dict[str, RevisitPolicy] = {}
        self._policy_by_semantic: dict[str, str] = {}
        self._decisions: dict[str, RevisitDecision] = {}
        self._decision_by_semantic: dict[str, str] = {}
        self._load()

    # -- Introspection ------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._memories)

    def __contains__(self, memory_identity: object) -> bool:
        return memory_identity in self._memories

    def memories(self) -> tuple:
        """All memories, deterministically ordered by memory identity."""
        return tuple(self._memories[key] for key in sorted(self._memories))

    def policies(self) -> tuple:
        """All revisit policies, deterministically ordered by policy identity."""
        return tuple(self._policies[key] for key in sorted(self._policies))

    def decisions(self) -> tuple:
        """All revisit decisions, ordered by decision identity."""
        return tuple(self._decisions[key] for key in sorted(self._decisions))

    def get_memory(self, memory_identity: str) -> TreatmentMemoryRecord | None:
        if not is_treatment_memory_identity(memory_identity):
            raise TreatmentMemoryValidationError(
                f"memory_identity {memory_identity!r} is outside the governed "
                f"TMR namespace")
        return self._memories.get(memory_identity)

    def get_memory_by_semantic_identity(
            self, semantic_identity: str) -> TreatmentMemoryRecord | None:
        if not isinstance(semantic_identity, str) or not semantic_identity:
            raise TreatmentMemoryValidationError(
                "semantic_identity must be a non-empty string")
        bound = self._memory_by_semantic.get(semantic_identity)
        return self._memories.get(bound) if bound else None

    def get_policy(self, policy_identity: str) -> RevisitPolicy | None:
        if not is_revisit_policy_identity(policy_identity):
            raise RevisitGovernanceError(
                f"policy_identity {policy_identity!r} is outside the governed "
                f"RVP namespace")
        return self._policies.get(policy_identity)

    def get_decision(self, decision_identity: str) -> RevisitDecision | None:
        if not is_revisit_decision_identity(decision_identity):
            raise RevisitGovernanceError(
                f"decision_identity {decision_identity!r} is outside the "
                f"governed RVD namespace")
        return self._decisions.get(decision_identity)

    def memories_for_signature(
            self, signature_identity: str) -> tuple:
        """Every memory recorded against one exact treatment signature."""
        return tuple(row for row in self.memories()
                     if row.treatment_signature_identity == signature_identity)

    def memories_for_disposition(self, disposition: str) -> tuple:
        """Every memory with a given governed historical disposition."""
        return tuple(row for row in self.memories()
                     if row.disposition.value == disposition)


    # -- Mutation (explicit action only) ------------------------------------
    def register_memory(
            self, memory: TreatmentMemoryRecord) -> TreatmentMemoryRecord:
        """
        Register one historical treatment memory. Returns the persisted record.

        Duplicate-safe: an equivalent memory resolves to the existing record and
        is not written twice. A presented identity that already exists with
        different immutable semantics fails closed, because rebinding a memory
        identity would silently rewrite history.
        """
        if not isinstance(memory, TreatmentMemoryRecord):
            raise TreatmentMemoryValidationError(
                f"expected TreatmentMemoryRecord, got {type(memory).__name__}")

        bound = self._memory_by_semantic.get(memory.semantic_identity)
        if bound is not None:
            existing = self._memories[bound]
            if existing.memory_identity != memory.memory_identity:
                raise TreatmentMemoryIdentityConflict(
                    "treatment memory semantic identity is already bound to a "
                    "different memory record")
            return existing

        if memory.memory_identity in self._memories:
            raise TreatmentMemoryIdentityConflict(
                f"treatment memory identity {memory.memory_identity} already "
                f"exists with different immutable semantics")

        self._memories[memory.memory_identity] = memory
        self._memory_by_semantic[memory.semantic_identity] = (
            memory.memory_identity)
        self._persist()
        return memory

    def register_policy(self, policy: RevisitPolicy) -> RevisitPolicy:
        """
        Register a revisit policy version. Duplicate-safe.

        Two policy versions are two identities and both are kept, which is what
        allows an old revisit decision to keep naming the rules that produced it
        after the rules have changed.
        """
        if not isinstance(policy, RevisitPolicy):
            raise RevisitGovernanceError(
                f"expected RevisitPolicy, got {type(policy).__name__}")
        bound = self._policy_by_semantic.get(policy.semantic_identity)
        if bound is not None:
            return self._policies[bound]
        if policy.policy_identity in self._policies:
            raise RevisitGovernanceError(
                f"revisit policy identity {policy.policy_identity} already "
                f"exists with different immutable semantics")
        self._policies[policy.policy_identity] = policy
        self._policy_by_semantic[policy.semantic_identity] = policy.policy_identity
        self._persist()
        return policy

    def register_decision(
            self, decision: RevisitDecision) -> RevisitDecision:
        """
        Register an immutable revisit decision. Duplicate-safe.

        A decision may only be recorded for a policy this store already holds
        and for memories this store already holds. A decision that names a
        policy or a memory the store has never seen would be a determination
        with nothing behind it, so it fails closed.
        """
        if not isinstance(decision, RevisitDecision):
            raise RevisitGovernanceError(
                f"expected RevisitDecision, got {type(decision).__name__}")
        if decision.policy_identity not in self._policies:
            raise RevisitGovernanceError(
                f"a revisit decision may only reference a revisit policy this "
                f"store already holds; unknown policy "
                f"{decision.policy_identity}")
        for memory_identity in decision.memory_identities:
            if memory_identity not in self._memories:
                raise RevisitGovernanceError(
                    f"revisit decision {decision.decision_identity} references "
                    f"memory {memory_identity}, which is not in the store; a "
                    f"determination may never be recorded against history that "
                    f"does not exist")
        bound = self._decision_by_semantic.get(decision.semantic_identity)
        if bound is not None:
            return self._decisions[bound]
        if decision.decision_identity in self._decisions:
            raise RevisitGovernanceError(
                f"revisit decision identity {decision.decision_identity} "
                f"already exists with different immutable semantics")
        self._decisions[decision.decision_identity] = decision
        self._decision_by_semantic[decision.semantic_identity] = (
            decision.decision_identity)
        self._persist()
        return decision


    # -- Persistence --------------------------------------------------------
    def _document(self) -> dict:
        return {
            "format": STORE_FORMAT,
            "schema_version": TREATMENT_MEMORY_SCHEMA_VERSION,
            "memories": [row.to_dict() for row in self.memories()],
            "policies": [row.to_dict() for row in self.policies()],
            "decisions": [row.to_dict() for row in self.decisions()],
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
            raise TreatmentMemoryError(
                f"treatment memory store is unreadable: {self._path}: {exc}"
            ) from exc

        if not isinstance(document, Mapping):
            raise TreatmentMemoryError(
                f"treatment memory store must be a JSON object: {self._path}")
        if document.get("format") != STORE_FORMAT:
            raise TreatmentMemoryError(
                f"unknown treatment memory store format: "
                f"{document.get('format')!r}")
        if document.get("schema_version") != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise TreatmentMemoryError(
                f"unsupported treatment memory store schema_version: "
                f"{document.get('schema_version')!r}")

        memories = self._load_section(
            document, "memories", TreatmentMemoryRecord.from_dict,
            is_treatment_memory_identity, "treatment memory", "memory_identity")
        policies = self._load_section(
            document, "policies", RevisitPolicy.from_dict,
            is_revisit_policy_identity, "revisit policy", "policy_identity")
        decisions = self._load_section(
            document, "decisions", RevisitDecision.from_dict,
            is_revisit_decision_identity, "revisit decision", "decision_identity")

        for decision in decisions.values():
            if decision.policy_identity not in policies:
                raise RevisitGovernanceError(
                    f"persisted revisit decision {decision.decision_identity} "
                    f"references policy {decision.policy_identity}, which is not "
                    f"in the store; a decision may never outlive the rules that "
                    f"produced it")
            for memory_identity in decision.memory_identities:
                if memory_identity not in memories:
                    raise RevisitGovernanceError(
                        f"persisted revisit decision "
                        f"{decision.decision_identity} references memory "
                        f"{memory_identity}, which is not in the store; a "
                        f"determination may never outlive its history")

        self._memories = memories
        self._memory_by_semantic = {
            row.semantic_identity: row.memory_identity
            for row in memories.values()}
        self._policies = policies
        self._policy_by_semantic = {
            row.semantic_identity: row.policy_identity
            for row in policies.values()}
        self._decisions = decisions
        self._decision_by_semantic = {
            row.semantic_identity: row.decision_identity
            for row in decisions.values()}

    def _load_section(self, document, name, from_dict, predicate, label,
                      identity_attr):
        """
        Parse and stage one persisted section, failing closed on every defect.

        `identity_attr` is passed explicitly rather than sniffed: memories,
        policies and decisions all carry more than one `*_identity` attribute,
        so guessing would mis-key the store.
        """
        rows = document.get(name)
        if not isinstance(rows, list):
            raise TreatmentMemoryError(
                f"treatment memory store {name!r} must be a list")
        staged: dict[str, Any] = {}
        for position, row in enumerate(rows):
            try:
                record = from_dict(row)
            except (TreatmentMemoryError, RevisitGovernanceError) as exc:
                # Preserve the specific fail-closed type and add the position.
                raise type(exc)(
                    f"malformed {label} at position {position}: {exc}") from exc
            identity = getattr(record, identity_attr, None)
            if not isinstance(identity, str) or not predicate(identity):
                raise TreatmentMemoryError(
                    f"persisted {label} {identity!r} is outside the governed "
                    f"{identity_attr} namespace")
            if identity in staged:
                raise TreatmentMemoryError(
                    f"duplicate {label} identity in store: {identity}")
            staged[identity] = record
        return staged


__all__ = [
    "DEFAULT_TREATMENT_MEMORY_STORE_PATH",
    "STORE_FORMAT",
    "TreatmentMemoryStore",
]

