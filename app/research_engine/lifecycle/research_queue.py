"""
Research Queue v1 -- a BOUNDED, DETERMINISTIC order of research attention.

Stage 3 / Wave 5. This module answers exactly one question:

    If research capacity became available, what governed work is currently NEXT?

THE QUEUE IS NOT AN EXECUTION ENGINE
====================================
This is the mandatory distinction. A `ResearchQueue` is a governed RECOMMENDATION
and ORDER of research work. It is not permission to run it. Constructing one:

    - runs no experiment, backtest, hypothesis or research cycle;
    - creates, activates, promotes or treats no candidate;
    - mutates no canonical research question;
    - touches no broker, risk, sizing, strategy, production configuration or
      data-collection behaviour.

Nothing in this module imports any of those paths, and it performs no I/O.

THE BOUND IS EXPLICIT AND TRUNCATION IS NEVER SILENT
====================================================
Capacity is a required, explicit, identity-bearing input. When the executable
ordering is longer than the capacity, the queue records EVERY excluded item,
with its agenda position and its governed reason for exclusion. Truncation is
therefore a visible, auditable act rather than a quiet loss of research
opportunity -- and because `capacity` is identity material, changing the
capacity rule produces a different queue identity.

ONLY EXECUTABLE WORK IS EVER QUEUED
===================================
The queue is drawn from the agenda's EXECUTABLE entries alone. A BLOCKED,
WAITING_DATA, REFUSED, DEFERRED or COMPLETE opportunity -- or a READY item whose
dependencies are unmet -- is retained by the agenda for provenance and appears
in the queue's excluded record, never as actionable next work.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_agenda import (
    RESEARCH_AGENDA_SCHEMA_VERSION,
    AgendaEntry,
    ResearchAgenda,
    ResearchAgendaValidationError,
    is_research_agenda_identity,
)
from research_engine.lifecycle.research_opportunity import (
    ResearchAgendaError,
    OpportunityState,
    is_research_opportunity_identity,
)

RESEARCH_QUEUE_SCHEMA_VERSION: int = 1

# `QUE-` (research queue) is Wave 5's own namespace, disjoint from `ROP-`,
# `POL-`, `AGD-`, `AFR-`, every Wave 0-4 namespace and every canonical prefix.
RESEARCH_QUEUE_ID_PREFIX = "QUE-"
RESEARCH_QUEUE_ID_DIGEST_CHARS = 16

_ID_DIGEST_CHARS = RESEARCH_QUEUE_ID_DIGEST_CHARS
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_QUE_ID_RE = re.compile(
    rf"^{re.escape(RESEARCH_QUEUE_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_QUEUE_ID_DIGEST_CHARS}}}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ResearchQueueValidationError(ResearchAgendaError):
    """Queue material is invalid, incomplete or would silently lose work."""


#: The ONLY opportunity states that may appear in the research queue. Every other
#: governed state is retained by the agenda for provenance and can never become
#: queued research work.
QUEUED_STATES: frozenset[OpportunityState] = frozenset({OpportunityState.READY})

#: Why an executable item did not make it into the queue. Capacity truncation is
#: the only reason an EXECUTABLE item may be excluded; everything else is a
#: non-executable state, which is never truncated away without a record.
EXCLUSION_CAPACITY = "EXCLUDED_BY_CAPACITY"
EXCLUSION_NOT_EXECUTABLE = "NOT_EXECUTABLE_RESEARCH"


def is_research_queue_identity(value: Any) -> bool:
    """True only for IDs inside the reserved research-queue namespace."""
    return isinstance(value, str) and bool(_QUE_ID_RE.match(value))


def research_queue_identity_for(semantic_identity: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise ResearchQueueValidationError(
            "queue semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{RESEARCH_QUEUE_ID_PREFIX}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise ResearchQueueValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "queue semantic material").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QueueExclusion:
    """
    ONE opportunity that was considered and NOT queued, with the reason why.

    A queue that could truncate without recording this would be a hidden loss of
    research opportunity -- exactly the winner-only-history failure this whole
    research engine exists to prevent, applied to attention instead of evidence.
    """

    opportunity_identity: str
    agenda_position: int
    reason: str
    state: str
    unmet_dependencies: tuple[str, ...] = ()

    def _validate(self) -> "QueueExclusion":
        if not is_research_opportunity_identity(self.opportunity_identity):
            raise ResearchQueueValidationError(
                f"a queue exclusion must name a governed `ROP-*` opportunity identity, "
                f"got {self.opportunity_identity!r}")
        if not isinstance(self.agenda_position, int) or isinstance(
                self.agenda_position, bool):
            raise ResearchQueueValidationError(
                f"queue exclusion agenda_position must be an integer, got "
                f"{self.agenda_position!r}")
        if self.agenda_position < 0:
            raise ResearchQueueValidationError(
                "queue exclusion agenda_position must not be negative")
        if not isinstance(self.reason, str) or not _REASON_RE.match(self.reason):
            raise ResearchQueueValidationError(
                f"queue exclusion reason must be a CLOSED SHOUTY_SNAKE_CASE code, got "
                f"{self.reason!r}")
        if self.reason not in (EXCLUSION_CAPACITY, EXCLUSION_NOT_EXECUTABLE):
            raise ResearchQueueValidationError(
                f"unknown queue exclusion reason {self.reason!r}; the closed set is "
                f"{(EXCLUSION_CAPACITY, EXCLUSION_NOT_EXECUTABLE)}")
        _encode(self.to_dict(), "queue exclusion")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_identity": self.opportunity_identity,
            "agenda_position": self.agenda_position,
            "reason": self.reason,
            "state": self.state,
            "unmet_dependencies": list(self.unmet_dependencies),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueueExclusion":
        if not isinstance(data, Mapping):
            raise ResearchQueueValidationError(
                f"persisted queue exclusion must be a mapping, got {type(data).__name__}")
        expected = {
            "opportunity_identity", "agenda_position", "reason", "state",
            "unmet_dependencies",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchQueueValidationError(
                f"persisted queue exclusion missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchQueueValidationError(
                f"persisted queue exclusion has unknown fields: {unknown}")
        if not isinstance(data["unmet_dependencies"], list):
            raise ResearchQueueValidationError(
                "persisted queue exclusion unmet_dependencies must be a list")
        return cls(
            opportunity_identity=data["opportunity_identity"],
            agenda_position=data["agenda_position"],
            reason=data["reason"],
            state=data["state"],
            unmet_dependencies=tuple(data["unmet_dependencies"]),
        )._validate()


@dataclass(frozen=True)
class ResearchQueue:
    """
    A bounded, deterministic queue of the next governed research work.

    IDENTITY MATERIAL: the schema version, the bound agenda identity, the
    agenda's policy identity and version, the EXPLICIT capacity bound, the queued
    entries (position, opportunity identity, state, information band, cost band
    and full priority rationale), and EVERY exclusion with its reason.

    `capacity` being identity material is the mechanism that makes a capacity
    change provenance-visible: two different capacity rules are two different
    queues, and neither can rewrite the other.

    NOT IDENTITY MATERIAL: `created_at` and `note`.
    """

    agenda_identity: str
    policy_identity: str
    policy_version: str
    capacity: int
    entries: tuple[AgendaEntry, ...]
    exclusions: tuple[QueueExclusion, ...]
    evidence_boundary: str
    created_at: str = ""            # provenance only; NOT identity
    note: str = ""                  # provenance only; NOT identity
    schema_version: int = RESEARCH_QUEUE_SCHEMA_VERSION
    semantic_identity: str = ""
    queue_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = research_queue_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchQueueValidationError(
                    "presented queue semantic identity does not match the queue "
                    "material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.queue_identity:
            if self.queue_identity != expected_id:
                raise ResearchQueueValidationError(
                    f"presented queue identity {self.queue_identity!r} does not match "
                    f"the queue material")
        else:
            object.__setattr__(self, "queue_identity", expected_id)

    @classmethod
    def build(
        cls,
        agenda: ResearchAgenda,
        *,
        capacity: int,
        created_at: str = "",
        note: str = "",
    ) -> "ResearchQueue":
        """
        Construct the bounded queue from a governed agenda.

        Only the agenda's EXECUTABLE entries are eligible. Everything else is
        recorded in `exclusions` with `NOT_EXECUTABLE_RESEARCH`, and executable
        work beyond the bound is recorded with `EXCLUDED_BY_CAPACITY`. No item is
        ever dropped without a record.
        """
        if not isinstance(agenda, ResearchAgenda):
            raise ResearchQueueValidationError(
                f"expected ResearchAgenda, got {type(agenda).__name__}")
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ResearchQueueValidationError(
                f"queue capacity must be an explicit integer bound, got {capacity!r}; "
                f"capacity is a governed input, not an inferred default")
        if capacity < 1:
            raise ResearchQueueValidationError(
                f"queue capacity must be at least 1 to be a meaningful bound, got "
                f"{capacity}")

        entries: list[AgendaEntry] = []
        exclusions: list[QueueExclusion] = []
        for entry in agenda.executable_entries:
            if len(entries) < capacity:
                entries.append(entry)
            else:
                exclusions.append(QueueExclusion(
                    opportunity_identity=entry.opportunity_identity,
                    agenda_position=entry.position,
                    reason=EXCLUSION_CAPACITY,
                    state=entry.state,
                )._validate())
        for entry in agenda.blocked_entries:
            exclusions.append(QueueExclusion(
                opportunity_identity=entry.opportunity_identity,
                agenda_position=entry.position,
                reason=EXCLUSION_NOT_EXECUTABLE,
                state=entry.state,
                unmet_dependencies=entry.unmet_dependencies,
            )._validate())
        exclusions.sort(key=lambda item: item.agenda_position)

        return cls(
            agenda_identity=agenda.agenda_identity,
            policy_identity=agenda.policy.policy_identity,
            policy_version=agenda.policy.policy_version,
            capacity=capacity,
            entries=tuple(entries),
            exclusions=tuple(exclusions),
            evidence_boundary=agenda.evidence_boundary,
            created_at=created_at,
            note=note,
        )


    # -- Views --------------------------------------------------------------
    @property
    def queued_identities(self) -> tuple[str, ...]:
        return tuple(entry.opportunity_identity for entry in self.entries)

    @property
    def capacity_exclusions(self) -> tuple[QueueExclusion, ...]:
        """Executable work that was considered and cut by the capacity bound."""
        return tuple(item for item in self.exclusions
                     if item.reason == EXCLUSION_CAPACITY)

    @property
    def non_executable_exclusions(self) -> tuple[QueueExclusion, ...]:
        """Work that could never have been queued, whatever the capacity."""
        return tuple(item for item in self.exclusions
                     if item.reason == EXCLUSION_NOT_EXECUTABLE)

    def assert_belongs_to(self, agenda: ResearchAgenda) -> "ResearchQueue":
        """Fail closed if the queue does not describe THIS agenda."""
        if not isinstance(agenda, ResearchAgenda):
            raise ResearchQueueValidationError(
                f"expected ResearchAgenda, got {type(agenda).__name__}")
        if self.agenda_identity != agenda.agenda_identity:
            raise ResearchQueueValidationError(
                f"queue {self.queue_identity} binds agenda {self.agenda_identity}, not "
                f"{agenda.agenda_identity}")
        if self.policy_identity != agenda.policy.policy_identity:
            raise ResearchQueueValidationError(
                "queue policy identity contradicts the agenda it claims to bound")
        return self

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_queue",
            "schema_version": self.schema_version,
            "agenda_identity": self.agenda_identity,
            "policy_identity": self.policy_identity,
            "policy_version": self.policy_version,
            "capacity": self.capacity,
            "evidence_boundary": self.evidence_boundary,
            "queued": [
                {
                    "agenda_position": entry.position,
                    "opportunity_identity": entry.opportunity_identity,
                    "state": entry.state,
                    "information_band": entry.information_band,
                    "cost_band": entry.cost_band,
                    "criteria": entry.assessment.to_dict()["components"],
                }
                for entry in self.entries
            ],
            "excluded": [item.to_dict() for item in self.exclusions],
        }

    def _validate(self) -> "ResearchQueue":
        if self.schema_version != RESEARCH_QUEUE_SCHEMA_VERSION:
            raise ResearchQueueValidationError(
                f"queue schema_version must be {RESEARCH_QUEUE_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not is_research_agenda_identity(self.agenda_identity):
            raise ResearchQueueValidationError(
                f"a queue must bind a governed `AGD-*` agenda identity, got "
                f"{self.agenda_identity!r}")
        if isinstance(self.capacity, bool) or not isinstance(self.capacity, int):
            raise ResearchQueueValidationError(
                f"queue capacity must be an explicit integer, got {self.capacity!r}")
        if self.capacity < 1:
            raise ResearchQueueValidationError(
                f"queue capacity must be at least 1, got {self.capacity}")
        if len(self.entries) > self.capacity:
            raise ResearchQueueValidationError(
                f"queue holds {len(self.entries)} entries but its declared capacity is "
                f"{self.capacity}; the bound must be honoured, not asserted")
        for entry in self.entries:
            entry._validate()
            if not entry.executable:
                raise ResearchQueueValidationError(
                    f"non-executable opportunity {entry.opportunity_identity} "
                    f"(state {entry.state}) may never enter the research queue")
            if entry.state not in {state.value for state in QUEUED_STATES}:
                raise ResearchQueueValidationError(
                    f"opportunity state {entry.state!r} is not a queueable research "
                    f"state; only {sorted(s.value for s in QUEUED_STATES)} may be queued")
        queued = [entry.opportunity_identity for entry in self.entries]
        if len(set(queued)) != len(queued):
            raise ResearchQueueValidationError(
                "a queue may not contain the same opportunity twice")
        excluded = [item.opportunity_identity for item in self.exclusions]
        if len(set(excluded)) != len(excluded):
            raise ResearchQueueValidationError(
                "a queue may not exclude the same opportunity twice")
        overlap = sorted(set(queued) & set(excluded))
        if overlap:
            raise ResearchQueueValidationError(
                f"opportunities may not be both queued and excluded: {overlap}")
        # Queued work must be the agenda's leading executable work, in order.
        if [entry.position for entry in self.entries] != sorted(
                entry.position for entry in self.entries):
            raise ResearchQueueValidationError(
                "queued entries must follow the governed agenda order")
        _encode(self.semantic_material(), "queue semantic material")
        return self


    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "entries": [entry.to_dict() for entry in self.entries],
            "created_at": self.created_at,
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "queue_identity": self.queue_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchQueue":
        if not isinstance(data, Mapping):
            raise ResearchQueueValidationError(
                f"persisted queue must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "agenda_identity", "policy_identity",
            "policy_version", "capacity", "evidence_boundary", "queued", "excluded",
            "entries", "created_at", "note", "semantic_identity", "queue_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchQueueValidationError(
                f"persisted queue missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchQueueValidationError(
                f"persisted queue has unknown fields: {unknown}")
        if data["kind"] != "research_queue":
            raise ResearchQueueValidationError(
                f"persisted queue kind must be 'research_queue', got {data['kind']!r}")
        for name in ("queued", "excluded", "entries"):
            if not isinstance(data[name], list):
                raise ResearchQueueValidationError(
                    f"persisted queue {name} must be a list")
        entries = tuple(AgendaEntry.from_dict(row) for row in data["entries"])
        exclusions = tuple(QueueExclusion.from_dict(row) for row in data["excluded"])
        return cls(
            agenda_identity=data["agenda_identity"],
            policy_identity=data["policy_identity"],
            policy_version=data["policy_version"],
            capacity=data["capacity"],
            entries=entries,
            exclusions=exclusions,
            evidence_boundary=data["evidence_boundary"],
            created_at=data["created_at"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            queue_identity=data["queue_identity"],
        )


__all__ = [
    "EXCLUSION_CAPACITY",
    "EXCLUSION_NOT_EXECUTABLE",
    "QUEUED_STATES",
    "QueueExclusion",
    "RESEARCH_QUEUE_ID_DIGEST_CHARS",
    "RESEARCH_QUEUE_ID_PREFIX",
    "RESEARCH_QUEUE_SCHEMA_VERSION",
    "ResearchQueue",
    "ResearchQueueValidationError",
    "is_research_queue_identity",
    "research_queue_identity_for",
]
