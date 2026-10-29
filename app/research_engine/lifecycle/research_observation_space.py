"""Finite governed observation universes (Stage III / Wave 8).

Cells are explicitly declared and admitted; this module never generates a
Cartesian product.  Missing compatibility authority fails closed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.generated_research_identity import is_generated_research_id
from research_engine.lifecycle.generated_research_isolation import canonical_inventory
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.curiosity_proposal import is_curiosity_proposal_identity
from research_engine.lifecycle.progressive_depth_gate import is_decision_identity
from research_engine.lifecycle.research_interaction import is_interaction_identity, is_slice_identity
from research_engine.lifecycle.research_opportunity import OpportunitySubjectKind
from research_engine.lifecycle.search_provenance import is_search_record_identity, is_selection_freeze_identity

OBSERVATION_SPACE_SCHEMA_VERSION = 1
OBSERVATION_POLICY_ID_PREFIX, OBSERVATION_RULE_ID_PREFIX = "OCP-", "OCR-"
OBSERVATION_SPACE_ID_PREFIX, OBSERVATION_CELL_ID_PREFIX = "OBS-", "OBC-"
UNKNOWN_COMPATIBILITY_TREATMENT = "REJECT"
UNAVAILABLE_EVIDENCE_TREATMENT = "OBSERVABILITY_GAP"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class ObservationSpaceError(RuntimeError): pass
class ObservationSpaceValidationError(ObservationSpaceError): pass
class ObservationSpaceIdentityConflict(ObservationSpaceError): pass
class UnsupportedCartesianCombination(ObservationSpaceValidationError): pass


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _identity(prefix: str, value: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ObservationSpaceValidationError("semantic identity must be sha256 hex")
    return prefix + value[:16].upper()


def observation_policy_identity_for(v: str) -> str: return _identity(OBSERVATION_POLICY_ID_PREFIX, v)
def observation_rule_identity_for(v: str) -> str: return _identity(OBSERVATION_RULE_ID_PREFIX, v)
def observation_space_identity_for(v: str) -> str: return _identity(OBSERVATION_SPACE_ID_PREFIX, v)
def observation_cell_identity_for(v: str) -> str: return _identity(OBSERVATION_CELL_ID_PREFIX, v)
def _is(prefix: str, v: Any) -> bool: return isinstance(v, str) and bool(re.fullmatch(re.escape(prefix) + r"[0-9A-F]{16}", v))
def is_observation_policy_identity(v: Any) -> bool: return _is(OBSERVATION_POLICY_ID_PREFIX, v)
def is_observation_rule_identity(v: Any) -> bool: return _is(OBSERVATION_RULE_ID_PREFIX, v)
def is_observation_space_identity(v: Any) -> bool: return _is(OBSERVATION_SPACE_ID_PREFIX, v)
def is_observation_cell_identity(v: Any) -> bool: return _is(OBSERVATION_CELL_ID_PREFIX, v)


def _text(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v or v != v.strip():
        raise ObservationSpaceValidationError(f"{name} must be non-empty trimmed text")
    return v


def _token(v: Any, name: str) -> str:
    v = _text(v, name)
    if not _TOKEN.fullmatch(v): raise ObservationSpaceValidationError(f"{name} is not a governed token")
    return v


def _tokens(values: Iterable[str], name: str, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)): raise ObservationSpaceValidationError(f"{name} must be a sequence")
    out = tuple(sorted(_token(v, name) for v in values))
    if len(out) != len(set(out)) or (required and not out):
        raise ObservationSpaceValidationError(f"{name} must be unique" + (" and non-empty" if required else ""))
    return out


def _ids(values: Iterable[str], predicate, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)): raise ObservationSpaceValidationError(f"{name} must be a sequence")
    out = tuple(sorted(values))
    if len(out) != len(set(out)) or any(not predicate(v) for v in out):
        raise ObservationSpaceValidationError(f"{name} contains an invalid identity")
    return out


_SUBJECT_VALIDATORS = {
    OpportunitySubjectKind.CANONICAL_QUESTION.value: lambda v: v in canonical_inventory(),
    OpportunitySubjectKind.GENERATED_RESEARCH.value: is_generated_research_id,
    OpportunitySubjectKind.GOVERNED_DIMENSION.value: is_dimension_identity,
    OpportunitySubjectKind.RESEARCH_INTERACTION.value: is_interaction_identity,
    OpportunitySubjectKind.CURIOSITY_PROPOSAL.value: is_curiosity_proposal_identity,
    OpportunitySubjectKind.ELIGIBILITY_DECISION.value: is_decision_identity,
    OpportunitySubjectKind.SEARCH_RECORD.value: is_search_record_identity,
    OpportunitySubjectKind.SEARCH_SELECTION_FREEZE.value: is_selection_freeze_identity,
}


class CompatibilityRelation(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


class EvidenceCapability(str, Enum):
    OBSERVABLE = "OBSERVABLE"
    CURRENTLY_UNOBSERVABLE = "CURRENTLY_UNOBSERVABLE"


@dataclass(frozen=True)
class ObservationCellDeclaration:
    subject_kind: str
    subject_identity: str
    population_identity: str
    horizon: str
    evidence_class: str
    dimension_identities: tuple[str, ...] = ()
    interaction_identity: str = ""
    slice_identity: str = ""
    evidence_capability: EvidenceCapability = EvidenceCapability.OBSERVABLE

    def __post_init__(self) -> None:
        for name in ("subject_kind", "population_identity", "horizon", "evidence_class"):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        object.__setattr__(self, "subject_identity", _text(self.subject_identity, "subject_identity"))
        validator = _SUBJECT_VALIDATORS.get(self.subject_kind)
        if validator is None or not validator(self.subject_identity):
            raise ObservationSpaceValidationError("subject_identity is not valid for subject_kind")
        object.__setattr__(self, "dimension_identities", _ids(self.dimension_identities, is_dimension_identity, "dimension_identities"))
        if self.interaction_identity and not is_interaction_identity(self.interaction_identity): raise ObservationSpaceValidationError("invalid interaction_identity")
        if self.slice_identity and not is_slice_identity(self.slice_identity): raise ObservationSpaceValidationError("invalid slice_identity")
        if self.slice_identity and not self.interaction_identity: raise ObservationSpaceValidationError("a slice requires an interaction")
        if isinstance(self.evidence_capability, str): object.__setattr__(self, "evidence_capability", EvidenceCapability(self.evidence_capability))

    @property
    def interaction_depth(self) -> int: return len(self.dimension_identities) if self.interaction_identity else 0

    def semantic_material(self) -> dict[str, Any]:
        return {"subject_kind": self.subject_kind, "subject_identity": self.subject_identity,
                "population_identity": self.population_identity, "horizon": self.horizon,
                "evidence_class": self.evidence_class, "dimension_identities": list(self.dimension_identities),
                "interaction_identity": self.interaction_identity, "slice_identity": self.slice_identity,
                "evidence_capability": self.evidence_capability.value}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObservationCellDeclaration": return cls(**dict(d))


@dataclass(frozen=True)
class CompatibilityRule:
    """A selector with a verdict. Non-matching rules do not veto another rule."""
    rule_token: str
    relation: CompatibilityRelation = CompatibilityRelation.COMPATIBLE
    subject_kinds: tuple[str, ...] = ()
    subject_identities: tuple[str, ...] = ()
    populations: tuple[str, ...] = ()
    horizons: tuple[str, ...] = ()
    evidence_classes: tuple[str, ...] = ()
    dimension_identities: tuple[str, ...] = ()
    interaction_identities: tuple[str, ...] = ()
    slice_identities: tuple[str, ...] = ()
    semantic_identity: str = field(init=False)
    rule_identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_token", _token(self.rule_token, "rule_token"))
        if isinstance(self.relation, str): object.__setattr__(self, "relation", CompatibilityRelation(self.relation))
        for name in ("subject_kinds", "subject_identities", "populations", "horizons", "evidence_classes"):
            object.__setattr__(self, name, _tokens(getattr(self, name), name))
        object.__setattr__(self, "dimension_identities", _ids(self.dimension_identities, is_dimension_identity, "dimension_identities"))
        object.__setattr__(self, "interaction_identities", _ids(self.interaction_identities, is_interaction_identity, "interaction_identities"))
        object.__setattr__(self, "slice_identities", _ids(self.slice_identities, is_slice_identity, "slice_identities"))
        if not any((self.subject_kinds, self.subject_identities, self.populations, self.horizons,
                    self.evidence_classes, self.dimension_identities, self.interaction_identities, self.slice_identities)):
            raise ObservationSpaceValidationError("compatibility rule requires a selector")
        semantic = _digest(self.semantic_material())
        object.__setattr__(self, "semantic_identity", semantic); object.__setattr__(self, "rule_identity", observation_rule_identity_for(semantic))

    def semantic_material(self) -> dict[str, Any]:
        return {"kind": "observation_compatibility_rule", "rule_token": self.rule_token, "relation": self.relation.value,
                "subject_kinds": list(self.subject_kinds), "subject_identities": list(self.subject_identities),
                "populations": list(self.populations), "horizons": list(self.horizons), "evidence_classes": list(self.evidence_classes),
                "dimension_identities": list(self.dimension_identities), "interaction_identities": list(self.interaction_identities),
                "slice_identities": list(self.slice_identities)}

    def matches(self, c: ObservationCellDeclaration) -> bool:
        pairs = ((self.subject_kinds, c.subject_kind), (self.subject_identities, c.subject_identity),
                 (self.populations, c.population_identity), (self.horizons, c.horizon),
                 (self.evidence_classes, c.evidence_class), (self.interaction_identities, c.interaction_identity),
                 (self.slice_identities, c.slice_identity))
        return all(not allowed or value in allowed for allowed, value in pairs) and (
            not self.dimension_identities or set(c.dimension_identities).issubset(self.dimension_identities))

    def to_dict(self) -> dict[str, Any]: return {**self.semantic_material(), "semantic_identity": self.semantic_identity, "rule_identity": self.rule_identity}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CompatibilityRule":
        x = dict(d); sem, rid = x.pop("semantic_identity", ""), x.pop("rule_identity", "")
        if x.pop("kind", None) != "observation_compatibility_rule": raise ObservationSpaceValidationError("invalid rule kind")
        obj = cls(**x)
        if sem and sem != obj.semantic_identity or rid and rid != obj.rule_identity: raise ObservationSpaceIdentityConflict("rule identity mismatch")
        return obj


def evaluate_compatibility(rules: Iterable[CompatibilityRule], cell: ObservationCellDeclaration | None = None, **kwargs: Any) -> CompatibilityRelation:
    cell = cell or ObservationCellDeclaration(**kwargs)
    verdicts = tuple(r.relation for r in rules if r.matches(cell))
    if CompatibilityRelation.COMPATIBLE in verdicts: return CompatibilityRelation.COMPATIBLE
    if CompatibilityRelation.INCOMPATIBLE in verdicts: return CompatibilityRelation.INCOMPATIBLE
    return CompatibilityRelation.UNKNOWN


@dataclass(frozen=True)
class ObservationSpaceConstructionPolicy:
    allowed_subject_kinds: tuple[str, ...]
    allowed_dimensions: tuple[str, ...]
    allowed_populations: tuple[str, ...]
    allowed_horizons: tuple[str, ...]
    allowed_evidence_classes: tuple[str, ...]
    compatibility_rules: tuple[CompatibilityRule, ...]
    allowed_interactions: tuple[str, ...] = ()
    allowed_slices: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    max_interaction_depth: int = 3
    unknown_compatibility_treatment: str = UNKNOWN_COMPATIBILITY_TREATMENT
    unavailable_evidence_treatment: str = UNAVAILABLE_EVIDENCE_TREATMENT
    policy_version: int = 1
    label: str = ""
    note: str = ""
    created_at: str = ""
    semantic_identity: str = field(init=False)
    policy_identity: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("allowed_subject_kinds", "allowed_populations", "allowed_horizons", "allowed_evidence_classes"):
            object.__setattr__(self, name, _tokens(getattr(self, name), name, True))
        if any(kind not in _SUBJECT_VALIDATORS for kind in self.allowed_subject_kinds):
            raise ObservationSpaceValidationError("allowed_subject_kinds contains an unknown Wave 5 subject kind")
        object.__setattr__(self, "allowed_dimensions", _ids(self.allowed_dimensions, is_dimension_identity, "allowed_dimensions"))
        object.__setattr__(self, "allowed_interactions", _ids(self.allowed_interactions, is_interaction_identity, "allowed_interactions"))
        object.__setattr__(self, "allowed_slices", _ids(self.allowed_slices, is_slice_identity, "allowed_slices"))
        rules = tuple(sorted(self.compatibility_rules, key=lambda r: r.rule_identity))
        if not rules or len({r.rule_identity for r in rules}) != len(rules): raise ObservationSpaceValidationError("compatibility_rules must be non-empty and unique")
        object.__setattr__(self, "compatibility_rules", rules); object.__setattr__(self, "exclusions", _tokens(self.exclusions, "exclusions"))
        if type(self.max_interaction_depth) is not int or self.max_interaction_depth < 1: raise ObservationSpaceValidationError("max_interaction_depth must be positive")
        if self.unknown_compatibility_treatment != UNKNOWN_COMPATIBILITY_TREATMENT: raise ObservationSpaceValidationError("UNKNOWN must fail closed")
        if self.unavailable_evidence_treatment != UNAVAILABLE_EVIDENCE_TREATMENT: raise ObservationSpaceValidationError("unavailable evidence must be an observability gap")
        if type(self.policy_version) is not int or self.policy_version < 1: raise ObservationSpaceValidationError("policy_version must be positive")
        semantic = _digest(self.semantic_material()); object.__setattr__(self, "semantic_identity", semantic); object.__setattr__(self, "policy_identity", observation_policy_identity_for(semantic))

    def semantic_material(self) -> dict[str, Any]:
        return {"kind": "observation_space_construction_policy", "schema_version": 1, "policy_version": self.policy_version,
                "allowed_subject_kinds": list(self.allowed_subject_kinds), "allowed_dimensions": list(self.allowed_dimensions),
                "allowed_interactions": list(self.allowed_interactions), "allowed_slices": list(self.allowed_slices),
                "allowed_populations": list(self.allowed_populations), "allowed_horizons": list(self.allowed_horizons),
                "allowed_evidence_classes": list(self.allowed_evidence_classes), "compatibility_rules": [r.to_dict() for r in self.compatibility_rules],
                "exclusions": list(self.exclusions), "max_interaction_depth": self.max_interaction_depth,
                "unknown_compatibility_treatment": self.unknown_compatibility_treatment,
                "unavailable_evidence_treatment": self.unavailable_evidence_treatment}

    def evaluate(self, c: ObservationCellDeclaration) -> CompatibilityRelation:
        invalid = (c.subject_kind not in self.allowed_subject_kinds or c.population_identity not in self.allowed_populations
                   or c.horizon not in self.allowed_horizons or c.evidence_class not in self.allowed_evidence_classes
                   or not set(c.dimension_identities).issubset(self.allowed_dimensions)
                   or bool(c.interaction_identity and c.interaction_identity not in self.allowed_interactions)
                   or bool(c.slice_identity and c.slice_identity not in self.allowed_slices)
                   or c.interaction_depth > self.max_interaction_depth
                   or any(v in self.exclusions for v in (c.subject_identity, c.population_identity, c.horizon, c.evidence_class, c.interaction_identity, c.slice_identity, *c.dimension_identities)))
        return CompatibilityRelation.INCOMPATIBLE if invalid else evaluate_compatibility(self.compatibility_rules, c)

    def admit(self, c: ObservationCellDeclaration) -> None:
        verdict = self.evaluate(c)
        if verdict is not CompatibilityRelation.COMPATIBLE: raise UnsupportedCartesianCombination(f"cell compatibility is {verdict.value}")

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_material(), "label": self.label, "note": self.note, "created_at": self.created_at,
                "semantic_identity": self.semantic_identity, "policy_identity": self.policy_identity}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObservationSpaceConstructionPolicy":
        x = dict(d); sem, pid = x.pop("semantic_identity", ""), x.pop("policy_identity", "")
        if x.pop("kind", None) != "observation_space_construction_policy" or x.pop("schema_version", None) != 1: raise ObservationSpaceValidationError("invalid policy payload")
        x["compatibility_rules"] = tuple(CompatibilityRule.from_dict(r) for r in x["compatibility_rules"]); obj = cls(**x)
        if sem != obj.semantic_identity or pid != obj.policy_identity: raise ObservationSpaceIdentityConflict("policy identity mismatch")
        return obj


@dataclass(frozen=True)
class ObservationCell:
    observation_space_identity: str
    declaration: ObservationCellDeclaration
    semantic_identity: str = field(init=False)
    cell_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not is_observation_space_identity(self.observation_space_identity): raise ObservationSpaceValidationError("invalid observation_space_identity")
        sem = _digest(self.semantic_material()); object.__setattr__(self, "semantic_identity", sem); object.__setattr__(self, "cell_identity", observation_cell_identity_for(sem))

    def semantic_material(self) -> dict[str, Any]: return {"kind": "observation_cell", "schema_version": 1, "observation_space_identity": self.observation_space_identity, **self.declaration.semantic_material()}
    def to_dict(self) -> dict[str, Any]: return {**self.semantic_material(), "semantic_identity": self.semantic_identity, "cell_identity": self.cell_identity}
    def __getattr__(self, name: str) -> Any:
        d = object.__getattribute__(self, "declaration")
        if hasattr(d, name): return getattr(d, name)
        raise AttributeError(name)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObservationCell":
        x = dict(d); sem, cid = x.pop("semantic_identity", ""), x.pop("cell_identity", "")
        if x.pop("kind", None) != "observation_cell" or x.pop("schema_version", None) != 1: raise ObservationSpaceValidationError("invalid cell payload")
        sid = x.pop("observation_space_identity"); obj = cls(sid, ObservationCellDeclaration.from_dict(x))
        if sem != obj.semantic_identity or cid != obj.cell_identity: raise ObservationSpaceIdentityConflict("cell identity mismatch")
        return obj


@dataclass(frozen=True)
class ObservationSpace:
    policy: ObservationSpaceConstructionPolicy
    cells: tuple[ObservationCell, ...]
    semantic_identity: str
    observation_space_identity: str
    label: str = ""
    created_at: str = ""

    @classmethod
    def construct(cls, policy: ObservationSpaceConstructionPolicy, declarations: Iterable[ObservationCellDeclaration], *, label: str = "", created_at: str = "") -> "ObservationSpace":
        declared = tuple(declarations)
        if not declared: raise ObservationSpaceValidationError("observation space must contain cells")
        for c in declared:
            if not isinstance(c, ObservationCellDeclaration): raise ObservationSpaceValidationError("cells must be explicit declarations")
            policy.admit(c)
        materials = sorted((c.semantic_material() for c in declared), key=canonical_json)
        if len({canonical_json(x) for x in materials}) != len(materials): raise ObservationSpaceValidationError("duplicate cell declaration")
        sem = _digest({"kind": "observation_space", "schema_version": 1, "policy_identity": policy.policy_identity, "cells": materials})
        sid = observation_space_identity_for(sem); cells = tuple(sorted((ObservationCell(sid, c) for c in declared), key=lambda c: c.cell_identity))
        return cls(policy, cells, sem, sid, label, created_at)

    build = construct

    def __post_init__(self) -> None:
        if not _HEX.fullmatch(self.semantic_identity) or not is_observation_space_identity(self.observation_space_identity): raise ObservationSpaceValidationError("invalid space identities")
        if any(c.observation_space_identity != self.observation_space_identity for c in self.cells): raise ObservationSpaceValidationError("foreign cell")

    def cell(self, identity: str) -> ObservationCell | None: return next((c for c in self.cells if c.cell_identity == identity), None)
    def contains(self, declaration: ObservationCellDeclaration) -> bool: return any(c.declaration == declaration for c in self.cells)
    def to_dict(self) -> dict[str, Any]: return {"kind": "observation_space", "schema_version": 1, "policy": self.policy.to_dict(), "cells": [c.to_dict() for c in self.cells], "semantic_identity": self.semantic_identity, "observation_space_identity": self.observation_space_identity, "label": self.label, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObservationSpace":
        if set(d) != {"kind", "schema_version", "policy", "cells", "semantic_identity", "observation_space_identity", "label", "created_at"}: raise ObservationSpaceValidationError("unexpected space fields")
        policy = ObservationSpaceConstructionPolicy.from_dict(d["policy"]); declarations = tuple(ObservationCell.from_dict(c).declaration for c in d["cells"])
        obj = cls.construct(policy, declarations, label=d["label"], created_at=d["created_at"])
        if d["kind"] != "observation_space" or d["schema_version"] != 1 or d["semantic_identity"] != obj.semantic_identity or d["observation_space_identity"] != obj.observation_space_identity: raise ObservationSpaceIdentityConflict("space identity mismatch")
        return obj
