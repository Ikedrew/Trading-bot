"""
Research Agenda Store v1 -- deterministic, restart-safe persistence for Wave 5.

Stage 3 / Wave 5. This store is the ONLY writer of `ResearchOpportunity`,
`ResearchAgenda`, `AgendaFreeze` and `ResearchQueue` records. It reuses the
established local-authority persistence pattern of this repository (the committed
Wave 0 generated-research store and the Wave 4 search-record store):

    - one local JSON file per store (no network / S3 dependency);
    - deterministic canonical JSON serialisation (sorted keys, fixed separators);
    - atomic replace-on-write (temp file, fsync, `os.replace`) so a crash can
      never leave a half-written agenda history;
    - strict, fail-closed loading -- corrupted, conflicting, orphaned or
      unverifiable content raises instead of being silently repaired;
    - semantic deduplication on registration, so rebuilding a semantically
      identical agenda after a restart is not duplicated;
    - immutable history: an agenda identity is never rebound to different
      immutable semantics, and a policy change creates a NEW agenda rather than
      overwriting the old one.

DEDUP AND HISTORY SEMANTICS
===========================
Two registrations with the same identity are the SAME agenda/queue/freeze and
the second returns the persisted record unchanged (its original `created_at` is
preserved -- re-deriving an agenda is not re-creating it). Any material change --
a different policy, a different considered population, a different ordering or a
different capacity -- produces a DIFFERENT identity, which is therefore a NEW
historical agenda stored alongside the old one.

ORPHAN REFERENCES FAIL CLOSED
=============================
A freeze may only be persisted for an agenda this store already holds, and a
queue only for an agenda this store already holds: a snapshot of "what research
was next" cannot outlive or precede the agenda it describes.

NO IMPORT-TIME WRITES
---------------------
Importing this module performs no I/O beyond resolving a default path constant.
Records are written ONLY by an explicit `register_*` call.

NO PRODUCTION AUTHORITY
-----------------------
The store reads and writes exactly one agenda-history JSON file. It has no path
to production configuration, baseline activation, candidate promotion, execution
or runtime strategy behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_agenda import (
    RESEARCH_AGENDA_SCHEMA_VERSION,
    AgendaFreeze,
    ResearchAgenda,
    ResearchAgendaValidationError,
    is_agenda_freeze_identity,
    is_research_agenda_identity,
)
from research_engine.lifecycle.research_opportunity import (
    RESEARCH_OPPORTUNITY_SCHEMA_VERSION,
    ResearchAgendaError,
    ResearchOpportunity,
    ResearchOpportunityValidationError,
    is_research_opportunity_identity,
)
from research_engine.lifecycle.research_priority import (
    PRIORITY_POLICY_SCHEMA_VERSION,
    PrioritisationPolicy,
    ResearchPriorityError,
    is_priority_policy_identity,
)
from research_engine.lifecycle.research_queue import (
    RESEARCH_QUEUE_SCHEMA_VERSION,
    ResearchQueue,
    ResearchQueueValidationError,
    is_research_queue_identity,
)

# Local authority pattern (mirrors the Wave 0 / Wave 4 stores). Injectable per
# instance so tests never touch a real path.
DEFAULT_AGENDA_STORE_PATH = Path("logs/research_lifecycle/research_agenda.json")

# Top-level document marker. Bumping this is a clean-reset decision, never a
# silent migration.
STORE_FORMAT = "research_agenda_store_v1"


class ResearchAgendaStore:
    """
    Persistent store of immutable Wave 5 agenda history.

    Registration is the only mutating operation and is always explicit.
    Equivalent records deduplicate to the existing record; the original creation
    timestamp is preserved, because re-deriving an agenda is not re-creating it.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_AGENDA_STORE_PATH
        self._opportunities: dict[str, ResearchOpportunity] = {}
        self._policies: dict[str, PrioritisationPolicy] = {}
        self._agendas: dict[str, ResearchAgenda] = {}
        self._agenda_by_identity: dict[str, str] = {}
        self._freezes: dict[str, AgendaFreeze] = {}
        self._queues: dict[str, ResearchQueue] = {}
        self._load()

    # ─── Introspection ──────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._agendas)

    def __contains__(self, agenda_identity: object) -> bool:
        return agenda_identity in self._agendas

    def opportunities(self) -> tuple[ResearchOpportunity, ...]:
        """All opportunities, ordered deterministically by opportunity identity."""
        return tuple(self._opportunities[key] for key in sorted(self._opportunities))

    def policies(self) -> tuple[PrioritisationPolicy, ...]:
        """All policies, ordered deterministically by policy identity."""
        return tuple(self._policies[key] for key in sorted(self._policies))

    def agendas(self) -> tuple[ResearchAgenda, ...]:
        """All agendas, ordered deterministically by agenda identity."""
        return tuple(self._agendas[key] for key in sorted(self._agendas))

    def queues(self) -> tuple[ResearchQueue, ...]:
        """All queues, ordered deterministically by queue identity."""
        return tuple(self._queues[key] for key in sorted(self._queues))

    def freezes(self) -> tuple[AgendaFreeze, ...]:
        """All T0 freezes, ordered deterministically by freeze identity."""
        return tuple(self._freezes[key] for key in sorted(self._freezes))

    def get_opportunity(self, opportunity_identity: str) -> ResearchOpportunity | None:
        return self._opportunities.get(opportunity_identity)

    def get_policy(self, policy_identity: str) -> PrioritisationPolicy | None:
        return self._policies.get(policy_identity)

    def get_agenda(self, agenda_identity: str) -> ResearchAgenda | None:
        return self._agendas.get(agenda_identity)

    def get_agenda_by_semantic_identity(
            self, semantic_identity: str) -> ResearchAgenda | None:
        agenda_identity = self._agenda_by_identity.get(semantic_identity)
        return self._agendas.get(agenda_identity) if agenda_identity else None

    def get_freeze(self, freeze_identity: str) -> AgendaFreeze | None:
        return self._freezes.get(freeze_identity)

    def get_queue(self, queue_identity: str) -> ResearchQueue | None:
        return self._queues.get(queue_identity)

    def freezes_for(self, agenda_identity: str) -> tuple[AgendaFreeze, ...]:
        return tuple(
            self._freezes[key] for key in sorted(self._freezes)
            if self._freezes[key].agenda_identity == agenda_identity)

    def queues_for(self, agenda_identity: str) -> tuple[ResearchQueue, ...]:
        return tuple(
            self._queues[key] for key in sorted(self._queues)
            if self._queues[key].agenda_identity == agenda_identity)

    # ─── Mutation (explicit action only) ────────────────────────────────

    def register_opportunity(
            self, opportunity: ResearchOpportunity) -> ResearchOpportunity:
        """Register a governed research opportunity. Duplicate-safe."""
        if not isinstance(opportunity, ResearchOpportunity):
            raise ResearchOpportunityValidationError(
                f"expected ResearchOpportunity, got {type(opportunity).__name__}")
        existing = self._opportunities.get(opportunity.opportunity_identity)
        if existing is not None:
            if existing.semantic_identity != opportunity.semantic_identity:
                raise ResearchOpportunityValidationError(
                    f"opportunity identity {opportunity.opportunity_identity} already "
                    f"exists with different immutable semantics")
            return existing
        self._opportunities[opportunity.opportunity_identity] = opportunity
        self._persist()
        return opportunity

    def register_policy(self, policy: PrioritisationPolicy) -> PrioritisationPolicy:
        """
        Register a prioritisation policy.

        A policy is registered automatically with its agenda, but it may also be
        registered on its own so a human research process can pin the rule before
        any agenda is built under it.
        """
        if not isinstance(policy, PrioritisationPolicy):
            raise ResearchPriorityError(
                f"expected PrioritisationPolicy, got {type(policy).__name__}")
        existing = self._policies.get(policy.policy_identity)
        if existing is not None:
            if existing.semantic_identity != policy.semantic_identity:
                raise ResearchPriorityError(
                    f"policy identity {policy.policy_identity} already exists with "
                    f"different immutable semantics")
            return existing
        self._policies[policy.policy_identity] = policy
        self._persist()
        return policy


    def register_agenda(self, agenda: ResearchAgenda) -> ResearchAgenda:
        """
        Register a governed agenda together with its policy and population.

        Duplicate-safe: an equivalent agenda resolves to the persisted record and
        is not written twice. A presented agenda identity that already exists
        with different immutable semantics fails closed.
        """
        if not isinstance(agenda, ResearchAgenda):
            raise ResearchAgendaValidationError(
                f"expected ResearchAgenda, got {type(agenda).__name__}")
        existing_id = self._agenda_by_identity.get(agenda.semantic_identity)
        if existing_id is not None:
            existing = self._agendas[existing_id]
            if existing.agenda_identity != agenda.agenda_identity:
                raise ResearchAgendaValidationError(
                    "agenda semantic identity is already bound to a different agenda")
            return existing
        if agenda.agenda_identity in self._agendas:
            raise ResearchAgendaValidationError(
                f"agenda identity {agenda.agenda_identity} already exists with "
                f"different immutable semantics")
        for item in agenda.population:
            self._register_opportunity_silently(item)
        self._policies.setdefault(agenda.policy.policy_identity, agenda.policy)
        self._agendas[agenda.agenda_identity] = agenda
        self._agenda_by_identity[agenda.semantic_identity] = agenda.agenda_identity
        self._persist()
        return agenda

    def register_freeze(self, freeze: AgendaFreeze) -> AgendaFreeze:
        """
        Register a T0 agenda freeze against an already-registered agenda.

        A freeze may only be persisted for an agenda this store already holds: a
        snapshot of "what research was next at T0" cannot exist without the agenda
        that produced it.
        """
        if not isinstance(freeze, AgendaFreeze):
            raise ResearchAgendaValidationError(
                f"expected AgendaFreeze, got {type(freeze).__name__}")
        if freeze.agenda_identity not in self._agendas:
            raise ResearchAgendaValidationError(
                "an agenda freeze may only be registered for an already-recorded "
                "agenda; the agenda provenance must be persisted first")
        existing = self._freezes.get(freeze.freeze_identity)
        if existing is not None:
            if existing.semantic_identity != freeze.semantic_identity:
                raise ResearchAgendaValidationError(
                    f"freeze identity {freeze.freeze_identity} already exists with "
                    f"different immutable semantics")
            return existing
        self._freezes[freeze.freeze_identity] = freeze
        self._persist()
        return freeze

    def register_queue(self, queue: ResearchQueue) -> ResearchQueue:
        """
        Register a bounded research queue against an already-registered agenda.

        A queue is a governed ORDER of research work, never an execution record.
        Registering one runs nothing, creates no candidate and mutates no
        canonical question.
        """
        if not isinstance(queue, ResearchQueue):
            raise ResearchQueueValidationError(
                f"expected ResearchQueue, got {type(queue).__name__}")
        if queue.agenda_identity not in self._agendas:
            raise ResearchQueueValidationError(
                "a research queue may only be registered for an already-recorded "
                "agenda; the agenda provenance must be persisted first")
        existing = self._queues.get(queue.queue_identity)
        if existing is not None:
            if existing.semantic_identity != queue.semantic_identity:
                raise ResearchQueueValidationError(
                    f"queue identity {queue.queue_identity} already exists with "
                    f"different immutable semantics")
            return existing
        self._queues[queue.queue_identity] = queue
        self._persist()
        return queue

    def _register_opportunity_silently(self, opportunity: ResearchOpportunity) -> None:
        """Stage a population member without a per-item write. No public action."""
        existing = self._opportunities.get(opportunity.opportunity_identity)
        if existing is not None:
            if existing.semantic_identity != opportunity.semantic_identity:
                raise ResearchOpportunityValidationError(
                    f"opportunity identity {opportunity.opportunity_identity} already "
                    f"exists with different immutable semantics")
            return
        self._opportunities[opportunity.opportunity_identity] = opportunity


    # ─── Persistence ────────────────────────────────────────────────────

    def _document(self) -> dict[str, Any]:
        return {
            "format": STORE_FORMAT,
            "opportunity_schema_version": RESEARCH_OPPORTUNITY_SCHEMA_VERSION,
            "policy_schema_version": PRIORITY_POLICY_SCHEMA_VERSION,
            "agenda_schema_version": RESEARCH_AGENDA_SCHEMA_VERSION,
            "queue_schema_version": RESEARCH_QUEUE_SCHEMA_VERSION,
            "opportunities": [item.to_dict() for item in self.opportunities()],
            "policies": [item.to_dict() for item in self.policies()],
            "agendas": [item.to_dict() for item in self.agendas()],
            "freezes": [item.to_dict() for item in self.freezes()],
            "queues": [item.to_dict() for item in self.queues()],
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
            raise ResearchAgendaError(
                f"research agenda store is unreadable: {self._path}: {exc}") from exc

        if not isinstance(document, Mapping):
            raise ResearchAgendaError(
                f"research agenda store must be a JSON object: {self._path}")
        if document.get("format") != STORE_FORMAT:
            raise ResearchAgendaError(
                f"unknown research agenda store format: {document.get('format')!r}")
        for name, expected in (
            ("opportunity_schema_version", RESEARCH_OPPORTUNITY_SCHEMA_VERSION),
            ("policy_schema_version", PRIORITY_POLICY_SCHEMA_VERSION),
            ("agenda_schema_version", RESEARCH_AGENDA_SCHEMA_VERSION),
            ("queue_schema_version", RESEARCH_QUEUE_SCHEMA_VERSION),
        ):
            # Every section is also required to be present, so an absent version
            # marker cannot be mistaken for an unsupported one.
            if name not in document:
                raise ResearchAgendaError(
                    f"research agenda store is missing {name!r}; the document is not "
                    f"a complete {STORE_FORMAT} record")
            if document.get(name) != expected:
                raise ResearchAgendaError(
                    f"unsupported research agenda store {name}: "
                    f"{document.get(name)!r}")

        opportunities = self._load_section(
            document, "opportunities", ResearchOpportunity.from_dict,
            is_research_opportunity_identity, "opportunity", "opportunity_identity")
        policies = self._load_section(
            document, "policies", PrioritisationPolicy.from_dict,
            is_priority_policy_identity, "policy", "policy_identity")
        agendas = self._load_section(
            document, "agendas", ResearchAgenda.from_dict,
            is_research_agenda_identity, "agenda", "agenda_identity")
        freezes = self._load_section(
            document, "freezes", AgendaFreeze.from_dict,
            is_agenda_freeze_identity, "agenda freeze", "freeze_identity")
        queues = self._load_section(
            document, "queues", ResearchQueue.from_dict,
            is_research_queue_identity, "research queue", "queue_identity")

        for freeze in freezes.values():
            if freeze.agenda_identity not in agendas:
                raise ResearchAgendaValidationError(
                    f"persisted agenda freeze {freeze.freeze_identity} references "
                    f"agenda {freeze.agenda_identity}, which is not in the store; a "
                    f"T0 freeze may never outlive its agenda")
            freeze.assert_belongs_to(agendas[freeze.agenda_identity])
        for queue in queues.values():
            if queue.agenda_identity not in agendas:
                raise ResearchQueueValidationError(
                    f"persisted research queue {queue.queue_identity} references "
                    f"agenda {queue.agenda_identity}, which is not in the store; a "
                    f"bounded queue may never outlive its agenda")
            queue.assert_belongs_to(agendas[queue.agenda_identity])

        self._opportunities = opportunities
        self._policies = policies
        self._agendas = agendas
        self._agenda_by_identity = {
            row.semantic_identity: row.agenda_identity for row in agendas.values()}
        self._freezes = freezes
        self._queues = queues


    def _load_section(self, document, name, from_dict, predicate, label, identity_attr):
        """
        Parse and stage one persisted section, failing closed on every defect.

        `identity_attr` is passed explicitly rather than sniffed: several Wave 5
        records carry more than one `*_identity` attribute (an agenda and its
        freeze both carry `policy_identity`), so guessing would mis-key the store.
        """
        rows = document.get(name)
        if not isinstance(rows, list):
            raise ResearchAgendaError(
                f"research agenda store {name!r} must be a list")
        staged: dict[str, Any] = {}
        for position, row in enumerate(rows):
            try:
                record = from_dict(row)
            except ResearchAgendaError as exc:
                # Preserve the specific fail-closed type and add the position.
                raise type(exc)(f"malformed {label} at position {position}: {exc}") from exc
            identity = getattr(record, identity_attr, None)
            if not isinstance(identity, str) or not predicate(identity):
                raise ResearchAgendaError(
                    f"persisted {label} {identity!r} is outside the governed "
                    f"{identity_attr} namespace")
            if identity in staged:
                raise ResearchAgendaError(
                    f"duplicate {label} identity in store: {identity}")
            staged[identity] = record
        return staged


__all__ = [
    "DEFAULT_AGENDA_STORE_PATH",
    "STORE_FORMAT",
    "ResearchAgendaStore",
]
