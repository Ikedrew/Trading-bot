"""
Research Agenda v1 -- a bounded, reproducible ORDER of research attention.

Stage 3 / Wave 5. `research_opportunity` says what is worth attention and why;
`research_priority` says how the inputs become an order. This module produces
the resulting GOVERNED AGENDA and freezes it at T0. It executes nothing.

THE AGENDA IS A RECORD OF ATTENTION, NOT A SCHEDULE
===================================================
An agenda answers one question -- "in what order should these governed
opportunities be considered?" -- and it is a statement about ATTENTION. It is
not permission to run anything, and building one changes no production, runtime,
configuration or data-collection behaviour whatsoever.

THE WHOLE POPULATION IS RETAINED
=================================
The agenda keeps EVERY considered opportunity, not merely the top of the list.
This is the same anti-p-hacking discipline Wave 4 applied to alternatives: if a
lower-ranked item were dropped after the ordering was seen, the agenda identity
would change. Dropping work is therefore not a free action -- it produces a
DIFFERENT agenda whose population is visibly smaller.

CALLER ORDER IS NOT SEMANTICS
=============================
The population is canonicalised by `canonical_opportunities` before anything is
ordered, so the caller's list order cannot alter the agenda identity or the
final ordering.

NON-READY WORK IS RETAINED, NEVER QUEUABLE
==========================================
BLOCKED / WAITING_DATA / REFUSED / DEFERRED / COMPLETE opportunities are all
kept, in their own deterministically ordered section, with their governed reason
codes. They are retained for provenance and are structurally excluded from the
executable ordering, so an agenda can never present impossible work as runnable.

NO PRODUCTION AUTHORITY
-----------------------
No runner, orchestrator, `GovernanceGate`, broker, risk, sizing, baseline or
candidate-promotion path is imported or reachable. No I/O at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_opportunity import (
    ResearchAgendaError,
    ResearchOpportunity,
    ResearchOpportunityValidationError,
    canonical_opportunities,
    is_research_opportunity_identity,
    unmet_dependencies,
    unlock_map,
)
from research_engine.lifecycle.research_priority import (
    PriorityAssessment,
    PrioritisationPolicy,
    TIE_BREAK_KEY,
    order_opportunities,
)

RESEARCH_AGENDA_SCHEMA_VERSION: int = 1

# `AGD-` (research agenda) and `AFR-` (agenda freeze) are Wave 5's own
# namespaces, disjoint from `ROP-`, `POL-`, `QUE-`, every Wave 0-4 namespace and
# every canonical programme prefix.
RESEARCH_AGENDA_ID_PREFIX = "AGD-"
AGENDA_FREEZE_ID_PREFIX = "AFR-"
RESEARCH_AGENDA_ID_DIGEST_CHARS = 16

_ID_DIGEST_CHARS = RESEARCH_AGENDA_ID_DIGEST_CHARS
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_AGD_ID_RE = re.compile(
    rf"^{re.escape(RESEARCH_AGENDA_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_AGENDA_ID_DIGEST_CHARS}}}$")
_AFR_ID_RE = re.compile(
    rf"^{re.escape(AGENDA_FREEZE_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_AGENDA_ID_DIGEST_CHARS}}}$")
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")


class ResearchAgendaValidationError(ResearchAgendaError):
    """Agenda or freeze material is invalid, incomplete or self-contradictory."""


def is_research_agenda_identity(value: Any) -> bool:
    """True only for IDs inside the reserved research-agenda namespace."""
    return isinstance(value, str) and bool(_AGD_ID_RE.match(value))


def is_agenda_freeze_identity(value: Any) -> bool:
    """True only for IDs inside the reserved agenda-freeze namespace."""
    return isinstance(value, str) and bool(_AFR_ID_RE.match(value))


def _identity_for(prefix: str, semantic_identity: str, label: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise ResearchAgendaValidationError(
            f"{label} must be a lowercase 64-character sha256 hex digest")
    return f"{prefix}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def research_agenda_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        RESEARCH_AGENDA_ID_PREFIX, semantic_identity, "agenda semantic identity")


def agenda_freeze_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        AGENDA_FREEZE_ID_PREFIX, semantic_identity, "agenda freeze semantic identity")


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise ResearchAgendaValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "agenda semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ResearchAgendaValidationError(
            f"{label} must be a trimmed, non-empty string")
    return value


def _require_refs(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchAgendaValidationError(f"{label} must be a sequence of references")
    refs: list[str] = []
    for item in values:
        text = _require_text(item, f"{label} entry")
        if not _REFERENCE_RE.match(text):
            raise ResearchAgendaValidationError(
                f"{label} entry must be a governed 'scheme:value' reference, got {text!r}")
        refs.append(text)
    if len(set(refs)) != len(refs):
        raise ResearchAgendaValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(refs))


# -- The agenda entry --------------------------------------------------------


@dataclass(frozen=True)
class AgendaEntry:
    """
    ONE considered opportunity, with its position, its state and its rationale.

    The entry is the agenda's unit of retention. Every entry carries the full
    expanded priority assessment, so "why is this here?" is answerable from the
    record alone -- no re-derivation, no hidden weighting.
    """

    position: int
    opportunity_identity: str
    state: str
    executable: bool
    assessment: PriorityAssessment
    unmet_dependencies: tuple[str, ...] = ()
    unlocks: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    information_band: str = ""
    cost_band: str = ""
    unknown_cost_factors: tuple[str, ...] = ()

    def _validate(self) -> "AgendaEntry":
        if isinstance(self.position, bool) or not isinstance(self.position, int):
            raise ResearchAgendaValidationError(
                f"agenda entry position must be an integer, got {self.position!r}")
        if self.position < 0:
            raise ResearchAgendaValidationError(
                f"agenda entry position must not be negative, got {self.position!r}")
        if not is_research_opportunity_identity(self.opportunity_identity):
            raise ResearchAgendaValidationError(
                f"agenda entry must name a governed `ROP-*` opportunity identity, got "
                f"{self.opportunity_identity!r}")
        if not isinstance(self.assessment, PriorityAssessment):
            raise ResearchAgendaValidationError(
                f"agenda entry must carry a PriorityAssessment, got "
                f"{type(self.assessment).__name__}")
        if self.assessment.opportunity_identity != self.opportunity_identity:
            raise ResearchAgendaValidationError(
                f"agenda entry assessment names {self.assessment.opportunity_identity!r} "
                f"but the entry is {self.opportunity_identity!r}")
        _encode(self.to_dict(), "agenda entry")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "opportunity_identity": self.opportunity_identity,
            "state": self.state,
            "executable": self.executable,
            "assessment": self.assessment.to_dict(),
            "unmet_dependencies": list(self.unmet_dependencies),
            "unlocks": list(self.unlocks),
            "reason_codes": list(self.reason_codes),
            "information_band": self.information_band,
            "cost_band": self.cost_band,
            "unknown_cost_factors": list(self.unknown_cost_factors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgendaEntry":
        if not isinstance(data, Mapping):
            raise ResearchAgendaValidationError(
                f"persisted agenda entry must be a mapping, got {type(data).__name__}")
        expected = {
            "position", "opportunity_identity", "state", "executable", "assessment",
            "unmet_dependencies", "unlocks", "reason_codes", "information_band",
            "cost_band", "unknown_cost_factors",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchAgendaValidationError(
                f"persisted agenda entry missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchAgendaValidationError(
                f"persisted agenda entry has unknown fields: {unknown}")
        for name in ("unmet_dependencies", "unlocks", "reason_codes",
                     "unknown_cost_factors"):
            if not isinstance(data[name], list):
                raise ResearchAgendaValidationError(
                    f"persisted agenda entry {name} must be a list")
        if not isinstance(data["executable"], bool):
            raise ResearchAgendaValidationError(
                "persisted agenda entry 'executable' must be a boolean")
        return cls(
            position=data["position"],
            opportunity_identity=data["opportunity_identity"],
            state=data["state"],
            executable=data["executable"],
            assessment=PriorityAssessment.from_dict(data["assessment"]),
            unmet_dependencies=tuple(data["unmet_dependencies"]),
            unlocks=tuple(data["unlocks"]),
            reason_codes=tuple(data["reason_codes"]),
            information_band=data["information_band"],
            cost_band=data["cost_band"],
            unknown_cost_factors=tuple(data["unknown_cost_factors"]),
        )._validate()


@dataclass(frozen=True)
class ResearchAgenda:
    """
    A governed, deterministically identified research agenda.

    IDENTITY MATERIAL: the schema version, the prioritisation policy identity
    and version, the ordered criterion list, the evidence boundary, the ENTIRE
    considered population (in canonical identity order), the full ordered
    entries, and the parent research references.

    Because the whole population is identity material, dropping a lower-ranked
    opportunity after seeing the ordering necessarily changes the agenda
    identity. That is the point: the record proves what was actually considered,
    not merely what survived.

    NOT IDENTITY MATERIAL: `created_at` and `note`. Re-deriving a
    semantically identical agenda later reproduces the same identity.
    """

    policy: PrioritisationPolicy
    evidence_boundary: str
    entries: tuple[AgendaEntry, ...]
    population: tuple[ResearchOpportunity, ...]
    parent_research_refs: tuple[str, ...] = ()
    created_at: str = ""            # provenance only; NOT identity
    note: str = ""                  # provenance only; NOT identity
    schema_version: int = RESEARCH_AGENDA_SCHEMA_VERSION
    semantic_identity: str = ""
    agenda_identity: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.policy, PrioritisationPolicy):
            raise ResearchAgendaValidationError(
                f"expected PrioritisationPolicy, got {type(self.policy).__name__}")
        # Canonicalise BEFORE anything else: caller order is not semantics.
        object.__setattr__(
            self, "population", canonical_opportunities(self.population))
        object.__setattr__(self, "parent_research_refs", _require_refs(
            self.parent_research_refs, "parent_research_refs"))
        object.__setattr__(self, "entries", tuple(self.entries))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = research_agenda_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchAgendaValidationError(
                    "presented agenda semantic identity does not match the agenda "
                    "material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.agenda_identity:
            if self.agenda_identity != expected_id:
                raise ResearchAgendaValidationError(
                    f"presented agenda identity {self.agenda_identity!r} does not match "
                    f"the agenda material")
        else:
            object.__setattr__(self, "agenda_identity", expected_id)

    @classmethod
    def build(
        cls,
        policy: PrioritisationPolicy,
        opportunities: Iterable[ResearchOpportunity],
        *,
        evidence_boundary: str,
        parent_research_refs: Iterable[str] = (),
        created_at: str = "",
        note: str = "",
    ) -> "ResearchAgenda":
        """
        Derive an agenda from a governed population under a governed policy.

        This is the ONLY construction path. The ordering is computed here, never
        supplied, so a caller cannot present an ordering the policy would not
        have produced.
        """
        population = canonical_opportunities(opportunities)
        ordered = order_opportunities(policy, population)
        unlocks = unlock_map(population)
        entries = tuple(
            AgendaEntry(
                position=position,
                opportunity_identity=item.opportunity_identity,
                state=item.state.value,
                executable=item.is_executable_research and not unmet_dependencies(
                    item, population),
                assessment=assessment,
                unmet_dependencies=unmet_dependencies(item, population),
                unlocks=unlocks.get(item.opportunity_identity, ()),
                reason_codes=item.reason_codes,
                information_band=item.information.band().value,
                cost_band=item.cost.band().value,
                unknown_cost_factors=item.cost.unknown_factors(),
            )._validate()
            for position, (item, assessment) in enumerate(ordered))
        return cls(
            policy=policy,
            evidence_boundary=evidence_boundary,
            entries=entries,
            population=population,
            parent_research_refs=tuple(parent_research_refs or ()),
            created_at=created_at,
            note=note,
        )


    # -- Views --------------------------------------------------------------
    @property
    def ordered_identities(self) -> tuple[str, ...]:
        """Every considered opportunity, in the governed order."""
        return tuple(entry.opportunity_identity for entry in self.entries)

    @property
    def executable_entries(self) -> tuple[AgendaEntry, ...]:
        """Only structurally executable research, in governed order."""
        return tuple(entry for entry in self.entries if entry.executable)

    @property
    def blocked_entries(self) -> tuple[AgendaEntry, ...]:
        """Dependency-blocked or non-READY work, retained for provenance."""
        return tuple(entry for entry in self.entries if not entry.executable)

    def entry_for(self, opportunity_identity: str) -> AgendaEntry:
        for entry in self.entries:
            if entry.opportunity_identity == opportunity_identity:
                return entry
        raise ResearchAgendaValidationError(
            f"opportunity {opportunity_identity!r} is not in this agenda; the agenda "
            f"retains exactly its considered population")

    def opportunity_for(self, opportunity_identity: str) -> ResearchOpportunity:
        for item in self.population:
            if item.opportunity_identity == opportunity_identity:
                return item
        raise ResearchAgendaValidationError(
            f"opportunity {opportunity_identity!r} is not in this agenda population")

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_agenda",
            "schema_version": self.schema_version,
            "policy_identity": self.policy.policy_identity,
            "policy_version": self.policy.policy_version,
            "ordered_criteria": list(self.policy.ordered_criteria()),
            "evidence_boundary": self.evidence_boundary,
            "parent_research_refs": list(self.parent_research_refs),
            "considered_population": [
                item.opportunity_identity for item in self.population],
            "ordering": [
                {
                    "position": entry.position,
                    "opportunity_identity": entry.opportunity_identity,
                    "state": entry.state,
                    "executable": entry.executable,
                    "criteria": entry.assessment.to_dict()["components"],
                    "unmet_dependencies": list(entry.unmet_dependencies),
                    "unlocks": list(entry.unlocks),
                    "reason_codes": list(entry.reason_codes),
                    "information_band": entry.information_band,
                    "cost_band": entry.cost_band,
                    "unknown_cost_factors": list(entry.unknown_cost_factors),
                }
                for entry in self.entries
            ],
        }


    def _validate(self) -> "ResearchAgenda":
        if self.schema_version != RESEARCH_AGENDA_SCHEMA_VERSION:
            raise ResearchAgendaValidationError(
                f"agenda schema_version must be {RESEARCH_AGENDA_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        _require_text(self.evidence_boundary, "agenda evidence_boundary")

        if not self.population:
            raise ResearchAgendaValidationError(
                "a governed agenda must retain at least one considered opportunity; "
                "an agenda over an empty population would be indistinguishable from "
                "an agenda that considered nothing")
        if len(self.entries) != len(self.population):
            raise ResearchAgendaValidationError(
                f"an agenda must retain its WHOLE considered population: {len(self.entries)} "
                f"entries for {len(self.population)} opportunities. Dropping an "
                f"opportunity after the ordering was seen is not a free action -- it "
                f"must be recorded as a different, smaller population.")
        population_ids = [item.opportunity_identity for item in self.population]
        entry_ids = [entry.opportunity_identity for entry in self.entries]
        if sorted(entry_ids) != sorted(population_ids):
            missing = sorted(set(population_ids) - set(entry_ids))
            extra = sorted(set(entry_ids) - set(population_ids))
            raise ResearchAgendaValidationError(
                f"agenda entries do not match the considered population; "
                f"missing={missing} unexpected={extra}")
        if len(set(entry_ids)) != len(entry_ids):
            raise ResearchAgendaValidationError(
                "an agenda may not contain the same opportunity twice")
        if [entry.position for entry in self.entries] != list(range(len(self.entries))):
            raise ResearchAgendaValidationError(
                "agenda positions must be a dense, ordered 0..n-1 sequence")

        for entry in self.entries:
            self._validate_entry(entry)
        _encode(self.semantic_material(), "agenda semantic material")
        return self

    def _validate_entry(self, entry: AgendaEntry) -> None:
        """An entry must faithfully PROJECT its opportunity, never restate it."""
        entry._validate()
        item = self.opportunity_for(entry.opportunity_identity)
        if entry.state != item.state.value:
            raise ResearchAgendaValidationError(
                f"agenda entry state {entry.state!r} contradicts the opportunity state "
                f"{item.state.value!r}")
        unmet = unmet_dependencies(item, self.population)
        expected_executable = item.is_executable_research and not unmet
        if entry.executable != expected_executable:
            raise ResearchAgendaValidationError(
                f"agenda entry executability for {entry.opportunity_identity} "
                f"contradicts the governed state and dependency material")
        if entry.unmet_dependencies != unmet:
            raise ResearchAgendaValidationError(
                f"agenda entry unmet dependencies for {entry.opportunity_identity} "
                f"contradict the governed dependency graph")
        if entry.information_band != item.information.band().value:
            raise ResearchAgendaValidationError(
                f"agenda entry information band {entry.information_band!r} contradicts "
                f"the recorded research information value")
        if entry.cost_band != item.cost.band().value:
            raise ResearchAgendaValidationError(
                f"agenda entry cost band {entry.cost_band!r} contradicts the recorded "
                f"research cost")
        if entry.unknown_cost_factors != item.cost.unknown_factors():
            raise ResearchAgendaValidationError(
                f"agenda entry unknown cost factors for {entry.opportunity_identity} "
                f"contradict the recorded research cost")

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "policy": self.policy.to_dict(),
            "population": [item.to_dict() for item in self.population],
            "created_at": self.created_at,
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "agenda_identity": self.agenda_identity,
        }


    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchAgenda":
        """Strict deserialisation. Corrupt or self-contradictory content fails closed."""
        if not isinstance(data, Mapping):
            raise ResearchAgendaValidationError(
                f"persisted agenda must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "policy_identity", "policy_version",
            "ordered_criteria", "evidence_boundary", "parent_research_refs",
            "considered_population", "ordering", "policy", "population", "created_at",
            "note", "semantic_identity", "agenda_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchAgendaValidationError(
                f"persisted agenda missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchAgendaValidationError(
                f"persisted agenda has unknown fields: {unknown}")
        if data["kind"] != "research_agenda":
            raise ResearchAgendaValidationError(
                f"persisted agenda kind must be 'research_agenda', got {data['kind']!r}")
        policy = PrioritisationPolicy.from_dict(data["policy"])
        if data["policy_identity"] != policy.policy_identity:
            raise ResearchAgendaValidationError(
                "persisted agenda policy identity contradicts the persisted policy")
        if data["policy_version"] != policy.policy_version:
            raise ResearchAgendaValidationError(
                "persisted agenda policy version contradicts the persisted policy")
        if list(data["ordered_criteria"]) != list(policy.ordered_criteria()):
            raise ResearchAgendaValidationError(
                "persisted agenda ordered criteria contradict the persisted policy; "
                "the ordering rule a reader sees must be the rule that was applied")
        rows = data["population"]
        ordering = data["ordering"]
        for name, value in (("population", rows), ("ordering", ordering),
                            ("considered_population", data["considered_population"]),
                            ("parent_research_refs", data["parent_research_refs"])):
            if not isinstance(value, list):
                raise ResearchAgendaValidationError(
                    f"persisted agenda {name} must be a list")
        try:
            population = tuple(ResearchOpportunity.from_dict(row) for row in rows)
        except ResearchAgendaError as exc:
            raise ResearchAgendaValidationError(
                f"persisted agenda population is malformed: {exc}") from exc
        entries = tuple(
            AgendaEntry.from_dict({
                "position": row["position"],
                "opportunity_identity": row["opportunity_identity"],
                "state": row["state"],
                "executable": row["executable"],
                "assessment": {"opportunity_identity": row["opportunity_identity"],
                               "components": row["criteria"]},
                "unmet_dependencies": row["unmet_dependencies"],
                "unlocks": row["unlocks"],
                "reason_codes": row["reason_codes"],
                "information_band": row["information_band"],
                "cost_band": row["cost_band"],
                "unknown_cost_factors": row["unknown_cost_factors"],
            })
            for row in ordering)
        return cls(
            policy=policy,
            evidence_boundary=data["evidence_boundary"],
            entries=entries,
            population=population,
            parent_research_refs=tuple(data["parent_research_refs"]),
            created_at=data["created_at"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            agenda_identity=data["agenda_identity"],
        )


@dataclass(frozen=True)
class AgendaFreeze:
    """
    The immutable T0 snapshot of "what research was next, and why".

    A later result must never be able to retroactively alter it. The freeze binds
    the agenda identity, the prioritisation policy identity and version, the
    whole considered population, the ordered executable queue, the evidence
    boundary, the dependency state, the information-value representation, the
    cost representation, and the capacity/budget rule.

    `frozen_at` is PROVENANCE ONLY and is deliberately absent from identity
    material: re-freezing semantically identical agenda state at a later moment
    reproduces the same freeze identity, which is what makes the snapshot
    verifiable rather than merely recorded.

    `capacity` is the governed research-capacity rule in force at the freeze. It
    is identity material, so a changed capacity rule is visible in the T0 record
    rather than silently reinterpreted. `None` means "no capacity bound was
    declared", which is an honest statement, not a zero bound.
    """

    agenda_identity: str
    policy_identity: str
    policy_version: str
    ordered_criteria: tuple[str, ...]
    considered_population: tuple[str, ...]
    ordered_identities: tuple[str, ...]
    executable_identities: tuple[str, ...]
    blocked_identities: tuple[str, ...]
    evidence_boundary: str
    dependency_state: Mapping[str, tuple[str, ...]]
    information_value_state: Mapping[str, str]
    cost_state: Mapping[str, str]
    unknown_cost_factors: Mapping[str, tuple[str, ...]]
    capacity: int | None = None
    parent_research_refs: tuple[str, ...] = ()
    frozen_at: str = ""            # provenance only; NOT identity
    note: str = ""                 # provenance only; NOT identity
    schema_version: int = RESEARCH_AGENDA_SCHEMA_VERSION
    semantic_identity: str = ""
    freeze_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_criteria", tuple(self.ordered_criteria))
        object.__setattr__(
            self, "considered_population", tuple(sorted(self.considered_population)))
        object.__setattr__(self, "ordered_identities", tuple(self.ordered_identities))
        object.__setattr__(
            self, "executable_identities", tuple(self.executable_identities))
        object.__setattr__(
            self, "blocked_identities", tuple(self.blocked_identities))
        object.__setattr__(
            self, "parent_research_refs", _require_refs(
                self.parent_research_refs, "parent_research_refs"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = agenda_freeze_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchAgendaValidationError(
                    "presented freeze semantic identity does not match the frozen "
                    "agenda material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.freeze_identity:
            if self.freeze_identity != expected_id:
                raise ResearchAgendaValidationError(
                    f"presented freeze identity {self.freeze_identity!r} does not match "
                    f"the frozen agenda material")
        else:
            object.__setattr__(self, "freeze_identity", expected_id)


    @classmethod
    def freeze(
        cls,
        agenda: ResearchAgenda,
        *,
        capacity: int | None = None,
        frozen_at: str = "",
        note: str = "",
    ) -> "AgendaFreeze":
        """
        Freeze an agenda at T0, before any subsequent result exists.

        The freeze captures the WHOLE considered population alongside the ordered
        executable work, so a later reader can prove both what was chosen and
        what was considered and not chosen.
        """
        if not isinstance(agenda, ResearchAgenda):
            raise ResearchAgendaValidationError(
                f"expected ResearchAgenda, got {type(agenda).__name__}")
        return cls(
            agenda_identity=agenda.agenda_identity,
            policy_identity=agenda.policy.policy_identity,
            policy_version=agenda.policy.policy_version,
            ordered_criteria=agenda.policy.ordered_criteria(),
            considered_population=tuple(
                item.opportunity_identity for item in agenda.population),
            ordered_identities=agenda.ordered_identities,
            executable_identities=tuple(
                entry.opportunity_identity for entry in agenda.executable_entries),
            blocked_identities=tuple(
                entry.opportunity_identity for entry in agenda.blocked_entries),
            evidence_boundary=agenda.evidence_boundary,
            dependency_state={
                item.opportunity_identity: item.depends_on
                for item in agenda.population},
            information_value_state={
                item.opportunity_identity: item.information.band().value
                for item in agenda.population},
            cost_state={
                item.opportunity_identity: item.cost.band().value
                for item in agenda.population},
            unknown_cost_factors={
                item.opportunity_identity: item.cost.unknown_factors()
                for item in agenda.population},
            capacity=capacity,
            parent_research_refs=agenda.parent_research_refs,
            frozen_at=frozen_at,
            note=note,
        )

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "agenda_freeze",
            "schema_version": self.schema_version,
            "agenda_identity": self.agenda_identity,
            "policy_identity": self.policy_identity,
            "policy_version": self.policy_version,
            "ordered_criteria": list(self.ordered_criteria),
            "considered_population": list(self.considered_population),
            "ordered_identities": list(self.ordered_identities),
            "executable_identities": list(self.executable_identities),
            "blocked_identities": list(self.blocked_identities),
            "evidence_boundary": self.evidence_boundary,
            "dependency_state": {
                key: list(value) for key, value in sorted(self.dependency_state.items())},
            "information_value_state": dict(sorted(self.information_value_state.items())),
            "cost_state": dict(sorted(self.cost_state.items())),
            "unknown_cost_factors": {
                key: list(value)
                for key, value in sorted(self.unknown_cost_factors.items())},
            "capacity": self.capacity,
            "parent_research_refs": list(self.parent_research_refs),
        }


    def _validate(self) -> "AgendaFreeze":
        if self.schema_version != RESEARCH_AGENDA_SCHEMA_VERSION:
            raise ResearchAgendaValidationError(
                f"agenda freeze schema_version must be {RESEARCH_AGENDA_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not is_research_agenda_identity(self.agenda_identity):
            raise ResearchAgendaValidationError(
                f"an agenda freeze must bind a governed `AGD-*` agenda identity, got "
                f"{self.agenda_identity!r}")
        if not self.ordered_criteria:
            raise ResearchAgendaValidationError(
                "an agenda freeze must record the ordering criteria that were in force; "
                "a frozen order with no recorded rule is unverifiable")
        if self.ordered_criteria[-1] != TIE_BREAK_KEY:
            raise ResearchAgendaValidationError(
                f"a frozen agenda must end with the governed deterministic tie-break "
                f"{TIE_BREAK_KEY!r}, got {self.ordered_criteria[-1]!r}")
        if not self.considered_population:
            raise ResearchAgendaValidationError(
                "an agenda freeze must record the whole considered population")
        if not self.ordered_identities:
            raise ResearchAgendaValidationError(
                "an agenda freeze must record the ordered considered population")
        if sorted(self.ordered_identities) != sorted(self.considered_population):
            raise ResearchAgendaValidationError(
                "the frozen ordering must cover exactly the frozen considered population")
        if sorted(self.executable_identities + self.blocked_identities) != sorted(
                self.considered_population):
            raise ResearchAgendaValidationError(
                "every frozen opportunity must be classified as either executable or "
                "blocked; nothing may be silently unclassified")
        if set(self.executable_identities) & set(self.blocked_identities):
            raise ResearchAgendaValidationError(
                "an opportunity may not be both executable and blocked in one freeze")
        if self.capacity is not None:
            if isinstance(self.capacity, bool) or not isinstance(self.capacity, int):
                raise ResearchAgendaValidationError(
                    f"agenda freeze capacity must be an integer or None, got "
                    f"{self.capacity!r}")
            if self.capacity < 0:
                raise ResearchAgendaValidationError(
                    f"agenda freeze capacity must not be negative, got {self.capacity!r}")

        # The four state maps must cover exactly the frozen population, or a
        # reader could not reconstruct the T0 decision.
        for name, mapping in (
            ("dependency_state", self.dependency_state),
            ("information_value_state", self.information_value_state),
            ("cost_state", self.cost_state),
            ("unknown_cost_factors", self.unknown_cost_factors),
        ):
            if not isinstance(mapping, Mapping):
                raise ResearchAgendaValidationError(
                    f"agenda freeze {name} must be a mapping")
            if set(mapping) != set(self.considered_population):
                raise ResearchAgendaValidationError(
                    f"agenda freeze {name} must cover exactly the frozen population; a "
                    f"missing or extra entry would make the T0 state unreconstructable")
        _require_text(self.evidence_boundary, "agenda freeze evidence_boundary")
        _encode(self.semantic_material(), "agenda freeze semantic material")
        return self

    def assert_belongs_to(self, agenda: ResearchAgenda) -> "AgendaFreeze":
        """Fail closed if the freeze does not describe THIS agenda."""
        if not isinstance(agenda, ResearchAgenda):
            raise ResearchAgendaValidationError(
                f"expected ResearchAgenda, got {type(agenda).__name__}")
        if self.agenda_identity != agenda.agenda_identity:
            raise ResearchAgendaValidationError(
                f"freeze {self.freeze_identity} binds agenda {self.agenda_identity}, "
                f"not {agenda.agenda_identity}")
        if self.policy_identity != agenda.policy.policy_identity:
            raise ResearchAgendaValidationError(
                "freeze policy identity contradicts the agenda it claims to freeze")
        if tuple(self.ordered_identities) != tuple(agenda.ordered_identities):
            raise ResearchAgendaValidationError(
                "frozen ordering contradicts the agenda it claims to freeze; a T0 "
                "snapshot may not reorder the agenda it froze")
        return self


    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "frozen_at": self.frozen_at,
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "freeze_identity": self.freeze_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgendaFreeze":
        if not isinstance(data, Mapping):
            raise ResearchAgendaValidationError(
                f"persisted agenda freeze must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "agenda_identity", "policy_identity",
            "policy_version", "ordered_criteria", "considered_population",
            "ordered_identities", "executable_identities", "blocked_identities",
            "evidence_boundary", "dependency_state", "information_value_state",
            "cost_state", "unknown_cost_factors", "capacity", "parent_research_refs",
            "frozen_at", "note", "semantic_identity", "freeze_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchAgendaValidationError(
                f"persisted agenda freeze missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchAgendaValidationError(
                f"persisted agenda freeze has unknown fields: {unknown}")
        if data["kind"] != "agenda_freeze":
            raise ResearchAgendaValidationError(
                f"persisted agenda freeze kind must be 'agenda_freeze', got "
                f"{data['kind']!r}")
        for name in ("ordered_criteria", "considered_population", "ordered_identities",
                     "executable_identities", "blocked_identities",
                     "parent_research_refs"):
            if not isinstance(data[name], list):
                raise ResearchAgendaValidationError(
                    f"persisted agenda freeze {name} must be a list")
        for name in ("dependency_state", "information_value_state", "cost_state",
                     "unknown_cost_factors"):
            if not isinstance(data[name], Mapping):
                raise ResearchAgendaValidationError(
                    f"persisted agenda freeze {name} must be a mapping")
        return cls(
            agenda_identity=data["agenda_identity"],
            policy_identity=data["policy_identity"],
            policy_version=data["policy_version"],
            ordered_criteria=tuple(data["ordered_criteria"]),
            considered_population=tuple(data["considered_population"]),
            ordered_identities=tuple(data["ordered_identities"]),
            executable_identities=tuple(data["executable_identities"]),
            blocked_identities=tuple(data["blocked_identities"]),
            evidence_boundary=data["evidence_boundary"],
            dependency_state={
                key: tuple(value) for key, value in data["dependency_state"].items()},
            information_value_state=dict(data["information_value_state"]),
            cost_state=dict(data["cost_state"]),
            unknown_cost_factors={
                key: tuple(value)
                for key, value in data["unknown_cost_factors"].items()},
            capacity=data["capacity"],
            parent_research_refs=tuple(data["parent_research_refs"]),
            frozen_at=data["frozen_at"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            freeze_identity=data["freeze_identity"],
        )


__all__ = [
    "AGENDA_FREEZE_ID_PREFIX",
    "AgendaEntry",
    "AgendaFreeze",
    "RESEARCH_AGENDA_ID_DIGEST_CHARS",
    "RESEARCH_AGENDA_ID_PREFIX",
    "RESEARCH_AGENDA_SCHEMA_VERSION",
    "ResearchAgenda",
    "ResearchAgendaValidationError",
    "agenda_freeze_identity_for",
    "is_agenda_freeze_identity",
    "is_research_agenda_identity",
    "research_agenda_identity_for",
]
