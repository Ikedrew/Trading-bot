"""Bounded research coverage, provenance, blind spots, queries and deltas."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.curiosity_signal import CuriositySignal, CuriositySignalType
from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_observation_space import (
    EvidenceCapability, ObservationCell, ObservationSpace,
    is_observation_cell_identity, is_observation_policy_identity,
    is_observation_space_identity,
)
from research_engine.lifecycle.research_protocol import ProtocolFreeze, ResearchProtocol
from research_engine.lifecycle.research_queue import ResearchQueue
from research_engine.lifecycle.search_provenance import SearchRecord
from research_engine.lifecycle.treatment_memory import (
    ApplicabilityEnvelope, ApplicabilityVerdict, TreatmentMemoryRecord,
    assess_memory_applicability, assess_memory_conflict,
)

INVENTORY_ID_PREFIX, COVERAGE_SNAPSHOT_ID_PREFIX, BLIND_SPOT_ID_PREFIX = "INV-", "CVS-", "BSP-"
_HEX = re.compile(r"^[0-9a-f]{64}$")


class ResearchCoverageError(RuntimeError): pass
class ResearchCoverageValidationError(ResearchCoverageError): pass
class ResearchCoverageIdentityConflict(ResearchCoverageError): pass


def _digest(v: Any) -> str: return hashlib.sha256(canonical_json(v).encode()).hexdigest()
def _identity(prefix: str, v: str) -> str:
    if not isinstance(v, str) or not _HEX.fullmatch(v): raise ResearchCoverageValidationError("semantic identity must be sha256 hex")
    return prefix + v[:16].upper()
def inventory_identity_for(v: str) -> str: return _identity(INVENTORY_ID_PREFIX, v)
def coverage_snapshot_identity_for(v: str) -> str: return _identity(COVERAGE_SNAPSHOT_ID_PREFIX, v)
def blind_spot_identity_for(v: str) -> str: return _identity(BLIND_SPOT_ID_PREFIX, v)
def _is(prefix: str, v: Any) -> bool: return isinstance(v, str) and bool(re.fullmatch(re.escape(prefix) + r"[0-9A-F]{16}", v))
def is_inventory_identity(v: Any) -> bool: return _is(INVENTORY_ID_PREFIX, v)
def is_coverage_snapshot_identity(v: Any) -> bool: return _is(COVERAGE_SNAPSHOT_ID_PREFIX, v)
def is_blind_spot_identity(v: Any) -> bool: return _is(BLIND_SPOT_ID_PREFIX, v)


def _refs(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)): raise ResearchCoverageValidationError(f"{name} must be a sequence")
    out = tuple(sorted(values))
    if len(out) != len(set(out)) or any(not isinstance(v, str) or not v or v != v.strip() for v in out):
        raise ResearchCoverageValidationError(f"{name} contains invalid/duplicate references")
    return out


@dataclass(frozen=True)
class ResearchInventoryBoundary:
    """The complete, finite material inspected by one coverage claim."""
    observation_space_identity: str
    question_inventory: tuple[str, ...] = ()
    evidence_inventory: tuple[str, ...] = ()
    search_inventory: tuple[str, ...] = ()
    opportunity_inventory: tuple[str, ...] = ()
    agenda_inventory: tuple[str, ...] = ()
    queue_inventory: tuple[str, ...] = ()
    protocol_inventory: tuple[str, ...] = ()
    protocol_freeze_inventory: tuple[str, ...] = ()
    treatment_memory_inventory: tuple[str, ...] = ()
    revisit_inventory: tuple[str, ...] = ()
    evidence_fingerprint: str = ""
    semantic_identity: str = field(init=False)
    inventory_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not is_observation_space_identity(self.observation_space_identity): raise ResearchCoverageValidationError("invalid observation_space_identity")
        for name in ("question_inventory", "evidence_inventory", "search_inventory", "opportunity_inventory",
                     "agenda_inventory", "queue_inventory", "protocol_inventory", "protocol_freeze_inventory",
                     "treatment_memory_inventory", "revisit_inventory"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        if self.evidence_fingerprint and (not isinstance(self.evidence_fingerprint, str) or self.evidence_fingerprint != self.evidence_fingerprint.strip()): raise ResearchCoverageValidationError("invalid evidence_fingerprint")
        sem = _digest(self.semantic_material()); object.__setattr__(self, "semantic_identity", sem); object.__setattr__(self, "inventory_identity", inventory_identity_for(sem))

    def semantic_material(self) -> dict[str, Any]:
        return {"kind": "research_inventory_boundary", "schema_version": 1, "observation_space_identity": self.observation_space_identity,
                **{n: list(getattr(self, n)) for n in ("question_inventory", "evidence_inventory", "search_inventory", "opportunity_inventory", "agenda_inventory", "queue_inventory", "protocol_inventory", "protocol_freeze_inventory", "treatment_memory_inventory", "revisit_inventory")},
                "evidence_fingerprint": self.evidence_fingerprint}
    def to_dict(self) -> dict[str, Any]: return {**self.semantic_material(), "semantic_identity": self.semantic_identity, "inventory_identity": self.inventory_identity}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ResearchInventoryBoundary":
        x = dict(d); sem, iid = x.pop("semantic_identity", ""), x.pop("inventory_identity", "")
        if x.pop("kind", None) != "research_inventory_boundary" or x.pop("schema_version", None) != 1: raise ResearchCoverageValidationError("invalid inventory payload")
        obj = cls(**x)
        if sem != obj.semantic_identity or iid != obj.inventory_identity: raise ResearchCoverageIdentityConflict("inventory identity mismatch")
        return obj


class CoverageState(str, Enum):
    OBSERVED_AND_RESEARCHED = "OBSERVED_AND_RESEARCHED"
    OBSERVED_NOT_RESEARCHED = "OBSERVED_NOT_RESEARCHED"
    RESEARCHED_INSUFFICIENT = "RESEARCHED_INSUFFICIENT"
    QUESTION_EXISTS_NO_EVIDENCE = "QUESTION_EXISTS_NO_EVIDENCE"
    PARTIALLY_RESEARCHED = "PARTIALLY_RESEARCHED"
    HISTORICALLY_RESEARCHED_CONTEXT_CHANGED = "HISTORICALLY_RESEARCHED_CONTEXT_CHANGED"
    CONFLICTING_RESEARCH = "CONFLICTING_RESEARCH"
    KNOWN_GAP_RESEARCH_PENDING = "KNOWN_GAP_RESEARCH_PENDING"
    BLOCKED = "BLOCKED"
    NOT_OBSERVABLE = "NOT_OBSERVABLE"
    NEVER_EXAMINED = "NEVER_EXAMINED"


@dataclass(frozen=True)
class CoverageEvidence:
    """Independent facts; no flag is inferred from another category."""
    evidence_refs: tuple[str, ...] = ()
    question_refs: tuple[str, ...] = ()
    research_attempt_refs: tuple[str, ...] = ()
    search_refs: tuple[str, ...] = ()
    opportunity_refs: tuple[str, ...] = ()
    active_research_refs: tuple[str, ...] = ()
    protocol_refs: tuple[str, ...] = ()
    frozen_protocol_refs: tuple[str, ...] = ()
    conclusion_refs: tuple[str, ...] = ()
    applicable_memory_refs: tuple[str, ...] = ()
    non_applicable_memory_refs: tuple[str, ...] = ()
    conflicting_memory_refs: tuple[str, ...] = ()
    revisit_refs: tuple[str, ...] = ()
    evidence_insufficient: bool = False
    partial_coverage: bool = False
    blocked: bool = False
    structurally_formulable: bool = True
    curiosity_excluded: bool = False

    def __post_init__(self) -> None:
        for name in ("evidence_refs", "question_refs", "research_attempt_refs", "search_refs", "opportunity_refs",
                     "active_research_refs", "protocol_refs", "frozen_protocol_refs", "conclusion_refs",
                     "applicable_memory_refs", "non_applicable_memory_refs", "conflicting_memory_refs", "revisit_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        for name in ("evidence_insufficient", "partial_coverage", "blocked", "structurally_formulable", "curiosity_excluded"):
            if type(getattr(self, name)) is not bool: raise ResearchCoverageValidationError(f"{name} must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {n: list(getattr(self, n)) for n in ("evidence_refs", "question_refs", "research_attempt_refs", "search_refs", "opportunity_refs", "active_research_refs", "protocol_refs", "frozen_protocol_refs", "conclusion_refs", "applicable_memory_refs", "non_applicable_memory_refs", "conflicting_memory_refs", "revisit_refs")} | {n: getattr(self, n) for n in ("evidence_insufficient", "partial_coverage", "blocked", "structurally_formulable", "curiosity_excluded")}


def classify_coverage(cell: ObservationCell, evidence: CoverageEvidence) -> CoverageState:
    if cell.evidence_capability is EvidenceCapability.CURRENTLY_UNOBSERVABLE: return CoverageState.NOT_OBSERVABLE
    if evidence.conflicting_memory_refs: return CoverageState.CONFLICTING_RESEARCH
    if evidence.active_research_refs and not (evidence.conclusion_refs or evidence.applicable_memory_refs): return CoverageState.KNOWN_GAP_RESEARCH_PENDING
    if evidence.blocked: return CoverageState.BLOCKED
    if evidence.evidence_insufficient and (evidence.research_attempt_refs or evidence.search_refs): return CoverageState.RESEARCHED_INSUFFICIENT
    if evidence.non_applicable_memory_refs and not evidence.applicable_memory_refs: return CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED
    if evidence.partial_coverage: return CoverageState.PARTIALLY_RESEARCHED
    if evidence.conclusion_refs or evidence.applicable_memory_refs: return CoverageState.OBSERVED_AND_RESEARCHED
    if evidence.research_attempt_refs or evidence.search_refs: return CoverageState.PARTIALLY_RESEARCHED
    if evidence.question_refs and not evidence.evidence_refs: return CoverageState.QUESTION_EXISTS_NO_EVIDENCE
    if evidence.evidence_refs: return CoverageState.OBSERVED_NOT_RESEARCHED
    return CoverageState.NEVER_EXAMINED


@dataclass(frozen=True)
class CellCoverage:
    cell_identity: str
    state: CoverageState
    inventory_identity: str
    evidence: CoverageEvidence

    def __post_init__(self) -> None:
        if not is_observation_cell_identity(self.cell_identity) or not is_inventory_identity(self.inventory_identity): raise ResearchCoverageValidationError("coverage has invalid identity reference")
        if isinstance(self.state, str): object.__setattr__(self, "state", CoverageState(self.state))
    def to_dict(self) -> dict[str, Any]: return {"cell_identity": self.cell_identity, "state": self.state.value, "inventory_identity": self.inventory_identity, "evidence": self.evidence.to_dict()}
    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CellCoverage": return cls(d["cell_identity"], CoverageState(d["state"]), d["inventory_identity"], CoverageEvidence(**d["evidence"]))


class BlindSpotClass(str, Enum):
    DATA_WITHOUT_RESEARCH = "DATA_WITHOUT_RESEARCH"
    QUESTION_WITHOUT_EVIDENCE = "QUESTION_WITHOUT_EVIDENCE"
    NEVER_EXAMINED = "NEVER_EXAMINED"
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    CONFLICTING_KNOWLEDGE = "CONFLICTING_KNOWLEDGE"
    STALE_APPLICABILITY = "STALE_APPLICABILITY"
    BLOCKED_GAP = "BLOCKED_GAP"
    OBSERVABILITY_GAP = "OBSERVABILITY_GAP"


_BLIND_FOR_STATE = {
    CoverageState.OBSERVED_NOT_RESEARCHED: BlindSpotClass.DATA_WITHOUT_RESEARCH,
    CoverageState.QUESTION_EXISTS_NO_EVIDENCE: BlindSpotClass.QUESTION_WITHOUT_EVIDENCE,
    CoverageState.NEVER_EXAMINED: BlindSpotClass.NEVER_EXAMINED,
    CoverageState.PARTIALLY_RESEARCHED: BlindSpotClass.PARTIAL_COVERAGE,
    CoverageState.RESEARCHED_INSUFFICIENT: BlindSpotClass.PARTIAL_COVERAGE,
    CoverageState.CONFLICTING_RESEARCH: BlindSpotClass.CONFLICTING_KNOWLEDGE,
    CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED: BlindSpotClass.STALE_APPLICABILITY,
    CoverageState.BLOCKED: BlindSpotClass.BLOCKED_GAP,
    CoverageState.NOT_OBSERVABLE: BlindSpotClass.OBSERVABILITY_GAP,
}


@dataclass(frozen=True)
class BlindSpot:
    cell_identity: str
    coverage_snapshot_identity: str
    observation_space_identity: str
    inventory_identity: str
    blind_spot_class: BlindSpotClass
    reason_code: str
    semantic_identity: str = field(init=False)
    blind_spot_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not is_observation_cell_identity(self.cell_identity) or not is_coverage_snapshot_identity(self.coverage_snapshot_identity) or not is_observation_space_identity(self.observation_space_identity) or not is_inventory_identity(self.inventory_identity): raise ResearchCoverageValidationError("blind spot carries malformed references")
        if isinstance(self.blind_spot_class, str): object.__setattr__(self, "blind_spot_class", BlindSpotClass(self.blind_spot_class))
        if self.reason_code != self.blind_spot_class.value: raise ResearchCoverageValidationError("blind spot reason must be its closed class code")
        sem = _digest(self.semantic_material()); object.__setattr__(self, "semantic_identity", sem); object.__setattr__(self, "blind_spot_identity", blind_spot_identity_for(sem))

    def semantic_material(self) -> dict[str, Any]: return {"kind": "blind_spot", "schema_version": 1, "cell_identity": self.cell_identity, "coverage_snapshot_identity": self.coverage_snapshot_identity, "observation_space_identity": self.observation_space_identity, "inventory_identity": self.inventory_identity, "blind_spot_class": self.blind_spot_class.value, "reason_code": self.reason_code}
    def to_dict(self) -> dict[str, Any]: return {**self.semantic_material(), "semantic_identity": self.semantic_identity, "blind_spot_identity": self.blind_spot_identity}


@dataclass(frozen=True)
class CoverageSnapshot:
    observation_space: ObservationSpace
    inventory: ResearchInventoryBoundary
    classifications: tuple[CellCoverage, ...]
    semantic_identity: str
    coverage_snapshot_identity: str
    observed_at: str = ""

    @classmethod
    def construct(cls, space: ObservationSpace, inventory: ResearchInventoryBoundary,
                  evidence_by_cell: Mapping[str, CoverageEvidence] | None = None, *, observed_at: str = "") -> "CoverageSnapshot":
        if inventory.observation_space_identity != space.observation_space_identity: raise ResearchCoverageValidationError("inventory belongs to another observation space")
        supplied = dict(evidence_by_cell or {})
        valid = {c.cell_identity for c in space.cells}
        if not set(supplied).issubset(valid): raise ResearchCoverageValidationError("coverage supplied for a cell outside OBS")
        rows = tuple(CellCoverage(c.cell_identity, classify_coverage(c, supplied.get(c.cell_identity, CoverageEvidence())), inventory.inventory_identity, supplied.get(c.cell_identity, CoverageEvidence())) for c in space.cells)
        rows = tuple(sorted(rows, key=lambda r: r.cell_identity))
        material = {"kind": "coverage_snapshot", "schema_version": 1, "observation_space_identity": space.observation_space_identity,
                    "policy_identity": space.policy.policy_identity, "inventory_identity": inventory.inventory_identity,
                    "evidence_fingerprint": inventory.evidence_fingerprint, "classifications": [r.to_dict() for r in rows]}
        sem = _digest(material); return cls(space, inventory, rows, sem, coverage_snapshot_identity_for(sem), observed_at)

    build = construct
    def __post_init__(self) -> None:
        if self.inventory.observation_space_identity != self.observation_space.observation_space_identity: raise ResearchCoverageValidationError("snapshot boundary mismatch")
        if not _HEX.fullmatch(self.semantic_identity) or not is_coverage_snapshot_identity(self.coverage_snapshot_identity): raise ResearchCoverageValidationError("invalid snapshot identities")

    def semantic_material(self) -> dict[str, Any]: return {"kind": "coverage_snapshot", "schema_version": 1, "observation_space_identity": self.observation_space.observation_space_identity, "policy_identity": self.observation_space.policy.policy_identity, "inventory_identity": self.inventory.inventory_identity, "evidence_fingerprint": self.inventory.evidence_fingerprint, "classifications": [r.to_dict() for r in self.classifications]}
    def to_dict(self) -> dict[str, Any]: return {**self.semantic_material(), "observation_space": self.observation_space.to_dict(), "inventory": self.inventory.to_dict(), "semantic_identity": self.semantic_identity, "coverage_snapshot_identity": self.coverage_snapshot_identity, "observed_at": self.observed_at}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CoverageSnapshot":
        expected = {"kind", "schema_version", "observation_space_identity", "policy_identity",
                    "inventory_identity", "evidence_fingerprint", "classifications",
                    "observation_space", "inventory", "semantic_identity",
                    "coverage_snapshot_identity", "observed_at"}
        if not isinstance(d, Mapping) or set(d) != expected:
            raise ResearchCoverageValidationError("unexpected coverage snapshot fields")
        space, inv = ObservationSpace.from_dict(d["observation_space"]), ResearchInventoryBoundary.from_dict(d["inventory"])
        evidence = {r["cell_identity"]: CoverageEvidence(**r["evidence"]) for r in d["classifications"]}
        obj = cls.construct(space, inv, evidence, observed_at=d.get("observed_at", ""))
        if (d["kind"] != "coverage_snapshot" or d["schema_version"] != 1
                or d["observation_space_identity"] != space.observation_space_identity
                or d["policy_identity"] != space.policy.policy_identity
                or d["inventory_identity"] != inv.inventory_identity
                or d["evidence_fingerprint"] != inv.evidence_fingerprint
                or d["classifications"] != [r.to_dict() for r in obj.classifications]
                or d["semantic_identity"] != obj.semantic_identity
                or d["coverage_snapshot_identity"] != obj.coverage_snapshot_identity):
            raise ResearchCoverageIdentityConflict("snapshot material or identity mismatch")
        return obj

    def coverage_for(self, cell_identity: str) -> CellCoverage | None: return next((r for r in self.classifications if r.cell_identity == cell_identity), None)
    def cells_by_state(self, state: CoverageState | str) -> tuple[ObservationCell, ...]:
        state = CoverageState(state); ids = {r.cell_identity for r in self.classifications if r.state is state}; return tuple(c for c in self.observation_space.cells if c.cell_identity in ids)
    def cells_by_subject(self, value: str) -> tuple[ObservationCell, ...]: return tuple(c for c in self.observation_space.cells if c.subject_identity == value)
    def cells_by_dimension(self, value: str) -> tuple[ObservationCell, ...]: return tuple(c for c in self.observation_space.cells if value in c.dimension_identities)
    def cells_by_interaction(self, value: str) -> tuple[ObservationCell, ...]: return tuple(c for c in self.observation_space.cells if c.interaction_identity == value)
    def cells_by_population(self, value: str) -> tuple[ObservationCell, ...]: return tuple(c for c in self.observation_space.cells if c.population_identity == value)
    def cells_by_horizon(self, value: str) -> tuple[ObservationCell, ...]: return tuple(c for c in self.observation_space.cells if c.horizon == value)
    def evidence_without_research(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.OBSERVED_NOT_RESEARCHED)
    def question_without_evidence(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.QUESTION_EXISTS_NO_EVIDENCE)
    def never_examined(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.NEVER_EXAMINED)
    def conflicting_research(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.CONFLICTING_RESEARCH)
    def observability_gaps(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.NOT_OBSERVABLE)
    def active_research_pending(self) -> tuple[ObservationCell, ...]: return self.cells_by_state(CoverageState.KNOWN_GAP_RESEARCH_PENDING)

    def blind_spots(self) -> tuple[BlindSpot, ...]:
        return tuple(BlindSpot(r.cell_identity, self.coverage_snapshot_identity, self.observation_space.observation_space_identity,
                               self.inventory.inventory_identity, _BLIND_FOR_STATE[r.state], _BLIND_FOR_STATE[r.state].value)
                     for r in self.classifications if r.state in _BLIND_FOR_STATE)

    def blind_spots_by_class(self, value: BlindSpotClass | str) -> tuple[BlindSpot, ...]:
        value = BlindSpotClass(value); return tuple(b for b in self.blind_spots() if b.blind_spot_class is value)

    def summary(self) -> dict[str, int]:
        rows = self.classifications
        return {"total_governed_cells": len(rows),
                "observable_cells": sum(r.state is not CoverageState.NOT_OBSERVABLE for r in rows),
                "cells_with_evidence": sum(bool(r.evidence.evidence_refs) for r in rows),
                "researched_cells": sum(bool(r.evidence.research_attempt_refs or r.evidence.search_refs or r.evidence.applicable_memory_refs) for r in rows),
                "concluded_cells": sum(bool(r.evidence.conclusion_refs or r.evidence.applicable_memory_refs) for r in rows),
                "unresolved_cells": sum(r.state is not CoverageState.OBSERVED_AND_RESEARCHED for r in rows),
                "insufficient_cells": sum(r.state is CoverageState.RESEARCHED_INSUFFICIENT for r in rows),
                "never_examined_cells": sum(r.state is CoverageState.NEVER_EXAMINED for r in rows),
                "conflicting_cells": sum(r.state is CoverageState.CONFLICTING_RESEARCH for r in rows),
                "blocked_cells": sum(r.state is CoverageState.BLOCKED for r in rows),
                "observability_gaps": sum(r.state is CoverageState.NOT_OBSERVABLE for r in rows),
                "active_research_gaps": sum(r.state is CoverageState.KNOWN_GAP_RESEARCH_PENDING for r in rows)}

    def dimension_coverage(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        by_id = {r.cell_identity: r for r in self.classifications}
        for c in self.observation_space.cells:
            for dim in c.dimension_identities:
                row = result.setdefault(dim, {"valid_observable_cells": 0, "research_history_cells": 0})
                if c.evidence_capability is EvidenceCapability.OBSERVABLE: row["valid_observable_cells"] += 1
                ev = by_id[c.cell_identity].evidence
                if ev.research_attempt_refs or ev.search_refs or ev.applicable_memory_refs: row["research_history_cells"] += 1
        return dict(sorted(result.items()))

    def interaction_coverage(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}; by_id = {r.cell_identity: r for r in self.classifications}
        for c in self.observation_space.cells:
            if not c.interaction_identity: continue
            row = result.setdefault(c.interaction_identity, {"valid_observable_cells": 0, "research_history_cells": 0})
            if c.evidence_capability is EvidenceCapability.OBSERVABLE: row["valid_observable_cells"] += 1
            ev = by_id[c.cell_identity].evidence
            if ev.research_attempt_refs or ev.search_refs or ev.applicable_memory_refs: row["research_history_cells"] += 1
        return dict(sorted(result.items()))

    def bounded_observation_report(self) -> dict[str, tuple[str, ...]]:
        represented = tuple(r.cell_identity for r in self.classifications if r.state is CoverageState.OBSERVED_AND_RESEARCHED)
        unexamined = tuple(r.cell_identity for r in self.classifications if r.state in (CoverageState.OBSERVED_NOT_RESEARCHED, CoverageState.NEVER_EXAMINED))
        unobservable = tuple(r.cell_identity for r in self.classifications if r.state is CoverageState.NOT_OBSERVABLE)
        return {"OBSERVABLE_AND_REPRESENTED": represented, "OBSERVABLE_BUT_UNEXAMINED": unexamined,
                "CONCEPTUAL_BUT_CURRENTLY_UNOBSERVABLE": unobservable, "INVALID_NONEXISTENT_COMBINATION": ()}


class CoverageDeltaKind(str, Enum):
    NEWLY_OBSERVABLE = "NEWLY_OBSERVABLE"; NEWLY_RESEARCHED = "NEWLY_RESEARCHED"; NEWLY_CONCLUDED = "NEWLY_CONCLUDED"
    BECAME_INSUFFICIENT = "BECAME_INSUFFICIENT"; NEW_CONFLICT = "NEW_CONFLICT"; CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    NEW_BLIND_SPOT = "NEW_BLIND_SPOT"; BLIND_SPOT_RESOLVED = "BLIND_SPOT_RESOLVED"; APPLICABILITY_CHANGED = "APPLICABILITY_CHANGED"
    NEW_ACTIVE_RESEARCH = "NEW_ACTIVE_RESEARCH"; NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True)
class CoverageDelta:
    before_snapshot_identity: str
    after_snapshot_identity: str
    changes: tuple[tuple[str, CoverageDeltaKind], ...]


def compare_coverage(before: CoverageSnapshot, after: CoverageSnapshot) -> CoverageDelta:
    old, new = {r.cell_identity: r for r in before.classifications}, {r.cell_identity: r for r in after.classifications}
    changes: list[tuple[str, CoverageDeltaKind]] = []
    blind_states = set(_BLIND_FOR_STATE)
    for cid in sorted(set(old) | set(new)):
        a, b = old.get(cid), new.get(cid)
        if not a or not b: changes.append((cid, CoverageDeltaKind.NEW_BLIND_SPOT if b else CoverageDeltaKind.BLIND_SPOT_RESOLVED)); continue
        events: list[CoverageDeltaKind] = []
        old_research = bool(a.evidence.research_attempt_refs or a.evidence.search_refs or a.evidence.applicable_memory_refs)
        new_research = bool(b.evidence.research_attempt_refs or b.evidence.search_refs or b.evidence.applicable_memory_refs)
        old_concluded = bool(a.evidence.conclusion_refs or a.evidence.applicable_memory_refs)
        new_concluded = bool(b.evidence.conclusion_refs or b.evidence.applicable_memory_refs)
        if a.state is CoverageState.NOT_OBSERVABLE and b.state is not CoverageState.NOT_OBSERVABLE: events.append(CoverageDeltaKind.NEWLY_OBSERVABLE)
        if not old_research and new_research: events.append(CoverageDeltaKind.NEWLY_RESEARCHED)
        if not old_concluded and new_concluded: events.append(CoverageDeltaKind.NEWLY_CONCLUDED)
        if a.state is not CoverageState.RESEARCHED_INSUFFICIENT and b.state is CoverageState.RESEARCHED_INSUFFICIENT: events.append(CoverageDeltaKind.BECAME_INSUFFICIENT)
        if a.state is not CoverageState.CONFLICTING_RESEARCH and b.state is CoverageState.CONFLICTING_RESEARCH: events.append(CoverageDeltaKind.NEW_CONFLICT)
        if a.state is CoverageState.CONFLICTING_RESEARCH and b.state is not CoverageState.CONFLICTING_RESEARCH: events.append(CoverageDeltaKind.CONFLICT_RESOLVED)
        if not a.evidence.active_research_refs and b.evidence.active_research_refs: events.append(CoverageDeltaKind.NEW_ACTIVE_RESEARCH)
        if ((a.state is CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED) != (b.state is CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED)): events.append(CoverageDeltaKind.APPLICABILITY_CHANGED)
        if a.state not in blind_states and b.state in blind_states: events.append(CoverageDeltaKind.NEW_BLIND_SPOT)
        if a.state in blind_states and b.state not in blind_states: events.append(CoverageDeltaKind.BLIND_SPOT_RESOLVED)
        changes.extend((cid, event) for event in events)
    if not changes: changes.append(("", CoverageDeltaKind.NO_CHANGE))
    return CoverageDelta(before.coverage_snapshot_identity, after.coverage_snapshot_identity, tuple(changes))


def admit_coverage_curiosity(snapshot: CoverageSnapshot, *, existing_signal_identities: Iterable[str] = ()) -> tuple[CuriositySignal, ...]:
    """Return Wave-3 signals only; never creates Wave-5/6 work."""
    existing = set(existing_signal_identities); by_cell = {r.cell_identity: r for r in snapshot.classifications}; signals = []
    for blind in snapshot.blind_spots():
        row = by_cell[blind.cell_identity]
        if row.evidence.active_research_refs or row.evidence.applicable_memory_refs or row.evidence.curiosity_excluded or not row.evidence.structurally_formulable: continue
        cell = snapshot.observation_space.cell(blind.cell_identity)
        signal = CuriositySignal.create(signal_type=CuriositySignalType.FINDING_EVIDENCE,
            source_ref=f"blind_spot:{blind.blind_spot_identity}", source_identity=blind.blind_spot_identity,
            target_kind="coverage_cell", target_ref=blind.cell_identity, reason_code=blind.reason_code,
            evidence_reference=f"coverage_snapshot:{snapshot.coverage_snapshot_identity}", evidence_boundary=snapshot.inventory.inventory_identity,
            source_provenance={"blind_spot_identity": blind.blind_spot_identity, "observation_cell_identity": blind.cell_identity,
                               "coverage_snapshot_identity": snapshot.coverage_snapshot_identity,
                               "observation_space_identity": snapshot.observation_space.observation_space_identity,
                               "inventory_identity": snapshot.inventory.inventory_identity, "blind_spot_class": blind.blind_spot_class.value},
            dimension_ref=cell.dimension_identities[0] if cell and cell.dimension_identities else None)
        if signal.signal_identity not in existing:
            existing.add(signal.signal_identity); signals.append(signal)
    return tuple(signals)


def bounded_negative_claim(snapshot: CoverageSnapshot, cell_identity: str) -> str:
    if snapshot.observation_space.cell(cell_identity) is None: raise ResearchCoverageValidationError("cannot make a coverage claim outside OBS")
    row = snapshot.coverage_for(cell_identity)
    return f"No matching governed research exists within {snapshot.inventory.inventory_identity}." if row and row.state in (CoverageState.OBSERVED_NOT_RESEARCHED, CoverageState.QUESTION_EXISTS_NO_EVIDENCE, CoverageState.NEVER_EXAMINED) else f"Coverage is {row.state.value} within {snapshot.inventory.inventory_identity}."


def wave4_search_refs(records: Iterable[SearchRecord]) -> tuple[str, ...]:
    """Read authoritative Wave 4 identities; a search remains only a search."""
    rows = tuple(records)
    if any(not isinstance(row, SearchRecord) for row in rows):
        raise ResearchCoverageValidationError("Wave 4 provenance requires SearchRecord objects")
    return tuple(sorted(row.search_identity for row in rows))


def wave5_active_research_refs(queues: Iterable[ResearchQueue]) -> tuple[str, ...]:
    """Read the already-ranked queue; Wave 8 never reranks or creates work."""
    rows = tuple(queues)
    if any(not isinstance(row, ResearchQueue) for row in rows):
        raise ResearchCoverageValidationError("active work requires ResearchQueue objects")
    return tuple(sorted({identity for row in rows for identity in row.queued_identities}))


def wave6_protocol_refs(protocols: Iterable[ResearchProtocol], freezes: Iterable[ProtocolFreeze] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return intent/history identities without manufacturing conclusions."""
    protocol_rows, freeze_rows = tuple(protocols), tuple(freezes)
    if any(not isinstance(row, ResearchProtocol) for row in protocol_rows) or any(not isinstance(row, ProtocolFreeze) for row in freeze_rows):
        raise ResearchCoverageValidationError("protocol provenance requires Wave 6 objects")
    return (tuple(sorted(row.protocol_identity for row in protocol_rows)),
            tuple(sorted(row.freeze_identity for row in freeze_rows)))


def wave7_memory_refs(memories: Iterable[TreatmentMemoryRecord], context: ApplicabilityEnvelope) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Apply Wave 7's governed scope and conflict authorities, never labels."""
    rows = tuple(memories)
    if any(not isinstance(row, TreatmentMemoryRecord) for row in rows) or not isinstance(context, ApplicabilityEnvelope):
        raise ResearchCoverageValidationError("memory provenance requires Wave 7 records and context")
    assessments = tuple((row, assess_memory_applicability(row, context)) for row in rows)
    applicable = tuple(sorted(row.memory_identity for row, result in assessments if result.verdict is ApplicabilityVerdict.APPLIES))
    outside = tuple(sorted(row.memory_identity for row, result in assessments if result.verdict is not ApplicabilityVerdict.APPLIES))
    conflict = assess_memory_conflict(rows, context)
    return applicable, outside, tuple(sorted(conflict.conflicting_memories))
