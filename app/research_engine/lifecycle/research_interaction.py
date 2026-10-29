"""
Research Interaction & Slice v1 — governed interaction algebra for Stage ③.

Stage ③ / Wave 1. This module is the generic language for REPRESENTING an
interaction of governed dimensions and the analytical cells (slices) inside it.

QUESTION ≠ INTERACTION ≠ SLICE
==============================
    Generated research question  a `GEN-*` record: "Does regime x horizon
                               explain exit-policy performance?"
    Interaction                 `regime x horizon`: an ordered/canonical set of
                               governed dimensions with a deterministic identity.
    Slice                       one analytical cell: `TRENDING x SCALP`.

A generated question OWNS OR REFERENCES exactly ONE interaction. An interaction
may contain MANY slices. A slice NEVER receives a `GEN-*` identity: slices are
cells, not research questions, and minting one per cell would fabricate the very
question proliferation Stage ③ is built to avoid. A slice has its own
deterministic `slice_identity`, in the reserved `SLC-` namespace.

NO CARTESIAN ENUMERATION (HARD INVARIANT)
=========================================
This module contains NO combination generator: no `all_interactions()`, no
pair/triple generation, no `itertools.product`, no exhaustive slice expansion
over observed values, and no `slices()` convenience that multiplies dimensions
by value domains. An interaction is constructed EXPLICITLY by a caller, and a
slice is constructed EXPLICITLY by a caller. Wave 2 and Wave 3 decide what
deserves to exist.

GENERIC DEPTH
=============
Depth 1, 2, 3 and deeper are structurally permitted. There is no maximum-depth
constant and no interaction-specific special case: an interaction is a set of
admitted governed dimensions, whatever its size.

ORDER-INSENSITIVE IDENTITY
==========================
`regime x horizon` and `horizon x regime` are the same scientific interaction
unless repository evidence proves ordering has scientific meaning. Caller
argument order is therefore never part of the identity: dimension identities are
sorted before hashing, and the same construction yields the same
`interaction_identity` from any permutation. Duplicate dimensions inside one
interaction are rejected outright rather than deduplicated.

NO PRODUCTION AUTHORITY
-----------------------
An interaction and a slice are research descriptions. They hold no authority
over strategy execution, risk, position sizing, production configuration,
baseline activation or candidate promotion, they never run anything, and they
are not visible to any canonical API.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import (
    DimensionValidationError,
    GovernedDimension,
    is_dimension_identity,
)

# ─── Versions (clean reset: starts at 1, never > 1) ──────────────────────────

INTERACTION_SCHEMA_VERSION: int = 1

# ─── Namespaces ─────────────────────────────────────────────────────────────
#
# `IXN-` (interaction) and `SLC-` (slice) are disjoint from the reserved `GEN-`
# research namespace and from every canonical programme prefix. Both also match
# the Wave 0 `dimension_ref` token grammar, so a governed interaction can be
# referenced by an existing Wave 0 generated research record with no change to
# the Wave 0 identity contract. Neither is ever a generated research ID.

INTERACTION_ID_PREFIX = "IXN-"
SLICE_ID_PREFIX = "SLC-"
_INTERACTION_ID_DIGEST_CHARS = 16
_SLICE_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_INTERACTION_ID_RE = re.compile(
    rf"^{re.escape(INTERACTION_ID_PREFIX)}[0-9A-F]{{{_INTERACTION_ID_DIGEST_CHARS}}}$"
)
_SLICE_ID_RE = re.compile(
    rf"^{re.escape(SLICE_ID_PREFIX)}[0-9A-F]{{{_SLICE_ID_DIGEST_CHARS}}}$"
)
# A slice value must be a deterministically encodable scalar-ish JSON value.
_FORBIDDEN_VALUE_TYPES = (set, frozenset, bytes, bytearray)


class InteractionValidationError(DimensionValidationError):
    """Interaction or slice material is invalid or incomplete."""


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise InteractionValidationError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode(material, "identity material").encode("utf-8")).hexdigest()


def is_interaction_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(_INTERACTION_ID_RE.match(value))


def is_slice_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(_SLICE_ID_RE.match(value))



# ─── Interaction ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResearchInteraction:
    """
    An immutable, canonically ordered set of ADMITTED governed dimensions.

    Construction is explicit. This class never generates interactions, never
    enumerates the registry, and never decides that an interaction is worth
    investigating — that is Wave 2.
    """

    dimensions: tuple[GovernedDimension, ...]
    schema_version: int = INTERACTION_SCHEMA_VERSION
    semantic_identity: str = ""
    interaction_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensions", _canonical_dimensions(self.dimensions))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = interaction_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise InteractionValidationError(
                    "presented semantic identity does not match the governed dimension set")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.interaction_identity:
            if self.interaction_identity != expected_id:
                raise InteractionValidationError(
                    f"presented interaction identity {self.interaction_identity!r} does not "
                    "match the governed dimension set")
        else:
            object.__setattr__(self, "interaction_identity", expected_id)

    # ─── Construction ───────────────────────────────────────────────────

    @classmethod
    def create(cls, dimensions: Sequence[GovernedDimension]) -> "ResearchInteraction":
        """Build an interaction from EXPLICITLY chosen governed dimensions."""
        return cls(dimensions=tuple(dimensions))

    @classmethod
    def admitted_from(
        cls,
        registry: DimensionRegistry,
        dimension_keys: Sequence[str],
    ) -> "ResearchInteraction":
        """
        Build an interaction through the admission gate.

        Every named dimension must be an ADMITTED governed dimension in the
        registry: a registered-but-unadmitted dimension, an unknown key, or a
        discovered-but-unauthoritative field is rejected here and cannot enter
        an interaction.
        """
        if not isinstance(registry, DimensionRegistry):
            raise InteractionValidationError(
                f"expected a DimensionRegistry, got {type(registry).__name__}")
        if isinstance(dimension_keys, (str, bytes)):
            raise InteractionValidationError("dimension_keys must be a sequence of keys")
        keys = tuple(dimension_keys)
        registry.assert_admitted(keys)
        dimensions = tuple(_require_instance(registry.get(key), key) for key in keys)
        return cls(dimensions=dimensions)


    # ─── Views ──────────────────────────────────────────────────────────

    @property
    def depth(self) -> int:
        """Number of governed dimensions in this interaction (1, 2, 3, ...)."""
        return len(self.dimensions)

    @property
    def dimension_identities(self) -> tuple[str, ...]:
        """Dimension identities in canonical (sorted) order."""
        return tuple(sorted(d.dimension_identity for d in self.dimensions))

    @property
    def dimension_keys(self) -> tuple[str, ...]:
        """Dimension keys in canonical order, parallel to `dimensions`."""
        return tuple(d.dimension_key for d in self.dimensions)

    def has_dimension(self, dimension_key: str) -> bool:
        return any(d.dimension_key == dimension_key for d in self.dimensions)

    def dimension(self, dimension_key: str) -> GovernedDimension:
        for candidate in self.dimensions:
            if candidate.dimension_key == dimension_key:
                return candidate
        raise InteractionValidationError(
            f"dimension {dimension_key!r} is not part of this interaction")

    def semantic_material(self) -> dict[str, Any]:
        """The frozen semantic material defining the interaction identity."""
        return {
            "kind": "research_interaction",
            "schema_version": self.schema_version,
            "depth": self.depth,
            # Canonical sorted order: caller argument order is never identity.
            "dimension_identities": list(self.dimension_identities),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "depth": self.depth,
            "dimension_identities": list(self.dimension_identities),
            "dimension_keys": list(self.dimension_keys),
            "semantic_identity": self.semantic_identity,
            "interaction_identity": self.interaction_identity,
            "dimensions": [d.to_dict() for d in self.dimensions],
        }

    def __str__(self) -> str:
        return " x ".join(self.dimension_keys)

    # ─── Validation ─────────────────────────────────────────────────────

    def _validate(self) -> "ResearchInteraction":
        if self.schema_version != INTERACTION_SCHEMA_VERSION:
            raise InteractionValidationError(
                f"interaction schema_version must be {INTERACTION_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not self.dimensions:
            raise InteractionValidationError(
                "an interaction must contain at least one governed dimension")
        identities = [d.dimension_identity for d in self.dimensions]
        if len(set(identities)) != len(identities):
            duplicates = sorted({i for i in identities if identities.count(i) > 1})
            raise InteractionValidationError(
                f"an interaction must not repeat a dimension: {duplicates}")
        keys = [d.dimension_key for d in self.dimensions]
        if len(set(keys)) != len(keys):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            raise InteractionValidationError(
                f"an interaction must not repeat a dimension key: {duplicates}")
        for dimension in self.dimensions:
            if not is_dimension_identity(dimension.dimension_identity):
                raise InteractionValidationError(
                    f"dimension {dimension.dimension_key!r} has no governed identity")
        _encode(self.semantic_material(), "interaction semantic material")
        return self


def interaction_identity_for(semantic_identity: str) -> str:
    """Derive the governed interaction identity deterministically from a digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise InteractionValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{INTERACTION_ID_PREFIX}{semantic_identity[:_INTERACTION_ID_DIGEST_CHARS].upper()}"



# ─── Slice / Cell ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InteractionSlice:
    """
    One analytical cell inside ONE interaction. NEVER a research question.

    A slice is defined by its parent interaction identity plus exactly one value
    assignment per interaction dimension. It has its own deterministic
    `slice_identity` in the reserved `SLC-` namespace; it never receives a
    `GEN-*` generated research ID, and registering one slice must never imply a
    new research question.
    """

    interaction: ResearchInteraction
    assignments: tuple[tuple[str, Any], ...]
    schema_version: int = INTERACTION_SCHEMA_VERSION
    semantic_identity: str = ""
    slice_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", _canonical_assignments(
            self.interaction, self.assignments))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = slice_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise InteractionValidationError(
                    "presented semantic identity does not match the governed assignments")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.slice_identity:
            if self.slice_identity != expected_id:
                raise InteractionValidationError(
                    f"presented slice identity {self.slice_identity!r} does not match "
                    "the governed assignments")
        else:
            object.__setattr__(self, "slice_identity", expected_id)

    # ─── Construction ───────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        interaction: ResearchInteraction,
        assignments: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> "InteractionSlice":
        """
        Build one slice of `interaction` from explicit dimension->value assignment_items.

        Assignment order is irrelevant. Unknown, missing, duplicate and extra
        dimensions are all rejected: a slice is exactly one cell of exactly its
        parent interaction, never a superset or subset of it.
        """
        if not isinstance(interaction, ResearchInteraction):
            raise InteractionValidationError(
                f"expected a ResearchInteraction, got {type(interaction).__name__}")
        if isinstance(assignments, Mapping):
            assignment_items = tuple(assignments.items())
        else:
            assignment_items = _assignment_items(assignments)
        return cls(interaction=interaction, assignments=assignment_items)


    # ─── Views ──────────────────────────────────────────────────────────

    @property
    def interaction_identity(self) -> str:
        """The parent interaction this cell belongs to."""
        return self.interaction.interaction_identity

    @property
    def depth(self) -> int:
        return len(self.assignments)

    def value_of(self, dimension_key: str) -> Any:
        for key, value in self.assignments:
            if key == dimension_key:
                return value
        raise InteractionValidationError(
            f"dimension {dimension_key!r} is not assigned in this slice")

    def semantic_material(self) -> dict[str, Any]:
        """The frozen semantic material defining the slice identity."""
        return {
            "kind": "interaction_slice",
            "schema_version": self.schema_version,
            # Parent interaction identity + canonical assignments. A slice in a
            # different interaction is a different cell even with equal values.
            "interaction_identity": self.interaction_identity,
            # Mapping form: assignment order cannot reach the identity.
            "assignments": {key: value for key, value in self.assignments},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "slice_identity": self.slice_identity,
            "semantic_identity": self.semantic_identity,
            "interaction_identity": self.interaction_identity,
            "depth": self.depth,
            "assignments": [
                {
                    "dimension_key": key,
                    "dimension_identity": self.interaction.dimension(key).dimension_identity,
                    "value": value,
                }
                for key, value in self.assignments
            ],
        }

    def __str__(self) -> str:
        return " x ".join(f"{key}={value}" for key, value in self.assignments)

    # ─── Validation ─────────────────────────────────────────────────────

    def _validate(self) -> "InteractionSlice":
        if self.schema_version != INTERACTION_SCHEMA_VERSION:
            raise InteractionValidationError(
                f"slice schema_version must be {INTERACTION_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not is_interaction_identity(self.interaction_identity):
            raise InteractionValidationError(
                "slice requires a governed parent interaction identity")
        interaction_keys = set(self.interaction.dimension_keys)
        assigned = {key for key, _ in self.assignments}
        unknown = sorted(assigned - interaction_keys)
        if unknown:
            raise InteractionValidationError(
                f"slice assigns dimensions that are not part of the interaction: {unknown}")
        missing = sorted(interaction_keys - assigned)
        if missing:
            raise InteractionValidationError(
                "slice must assign every interaction dimension exactly once; missing: "
                f"{missing}")
        for key, value in self.assignments:
            if value is None:
                raise InteractionValidationError(
                    f"slice value for {key!r} must not be null; a cell cannot be 'unknown'")
            if isinstance(value, _FORBIDDEN_VALUE_TYPES):
                raise InteractionValidationError(
                    f"slice value for {key!r} is not deterministically encodable: "
                    f"{type(value).__name__}")
            if not self.interaction.dimension(key).is_declared(value):
                raise InteractionValidationError(
                    f"slice value {value!r} is outside the governed domain of dimension {key!r}")
        _encode(self.semantic_material(), "slice semantic material")
        return self


def slice_identity_for(semantic_identity: str) -> str:
    """Derive the deterministic slice identity from a slice digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise InteractionValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{SLICE_ID_PREFIX}{semantic_identity[:_SLICE_ID_DIGEST_CHARS].upper()}"



# ─── Canonicalisation helpers (order-insensitive, fail closed) ──────────────


def _require_instance(dimension: Any, key: str | None = None) -> GovernedDimension:
    if not isinstance(dimension, GovernedDimension):
        where = f" for key {key!r}" if key is not None else ""
        raise InteractionValidationError(
            f"expected a registered GovernedDimension{where}, got {type(dimension).__name__}")
    return dimension


def _canonical_dimensions(
    dimensions: Iterable[GovernedDimension],
) -> tuple[GovernedDimension, ...]:
    """
    Canonical order for an interaction's dimensions.

    Sorted by DIMENSION IDENTITY, not by caller argument order and not by
    insertion order. `horizon x regime` and `regime x horizon` therefore
    canonicalise to the same tuple and hash to the same interaction identity.
    """
    if isinstance(dimensions, (str, bytes)) or not isinstance(
            dimensions, (list, tuple, set, frozenset)):
        raise InteractionValidationError(
            "dimensions must be a sequence of GovernedDimension")
    items = tuple(_require_instance(item) for item in dimensions)
    return tuple(sorted(items, key=lambda d: d.dimension_identity))


def _assignment_items(assignments: Any) -> tuple[tuple[str, Any], ...]:
    """Normalise an assignment sequence to assignment_items, rejecting anything else."""
    if isinstance(assignments, (str, bytes)) or not isinstance(assignments, (list, tuple)):
        raise InteractionValidationError(
            "assignments must be a mapping or a sequence of (dimension_key, value) assignment_items")
    assignment_items: list[tuple[str, Any]] = []
    for item in assignments:
        if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)):
            raise InteractionValidationError(
                "each assignment must be a (dimension_key, value) pair")
        if len(item) != 2:
            raise InteractionValidationError(
                "each assignment must be a (dimension_key, value) pair of exactly two items")
        key = item[0]
        if not isinstance(key, str) or not key.strip():
            raise InteractionValidationError("assignment dimension key must be a non-empty string")
        assignment_items.append((key, item[1]))
    return tuple(assignment_items)


def _canonical_assignments(
    interaction: ResearchInteraction,
    assignments: Iterable[tuple[str, Any]],
) -> tuple[tuple[str, Any], ...]:
    """
    Canonical form of a slice's assignments: sorted by dimension key.

    Order cannot reach the identity, because the identity hashes a mapping. A
    REPEATED dimension is rejected outright (a cell assigns a dimension once),
    rather than silently last-write-wins.
    """
    if not isinstance(interaction, ResearchInteraction):
        raise InteractionValidationError(
            f"expected a ResearchInteraction, got {type(interaction).__name__}")
    assignment_items = _assignment_items(tuple(assignments))
    keys = [key for key, _ in assignment_items]
    if len(set(keys)) != len(keys):
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        raise InteractionValidationError(
            f"a slice must not assign a dimension more than once: {duplicates}")
    # Fail closed before sorting: values must be canonically encodable.
    for key, value in assignment_items:
        _encode(value, f"slice value for {key!r}")
    return tuple(sorted(assignment_items, key=lambda pair: pair[0]))


__all__ = [
    "INTERACTION_ID_PREFIX",
    "INTERACTION_SCHEMA_VERSION",
    "SLICE_ID_PREFIX",
    "InteractionSlice",
    "InteractionValidationError",
    "ResearchInteraction",
    "is_interaction_identity",
    "is_slice_identity",
    "interaction_identity_for",
    "slice_identity_for",
]
