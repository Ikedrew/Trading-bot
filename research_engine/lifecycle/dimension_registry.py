"""
Governed Dimension Registry v1 — explicit admission of research dimensions.

Stage ③ / Wave 1. `DimensionRegistry` is the only way a `GovernedDimension`
becomes part of the generated-research language. It is deliberately NOT a
dictionary of everything an event happens to contain.

TWO TIERS, NEVER COLLAPSED
--------------------------
    registered  — the governed definition is known (semantic identity bound)
    admitted    — the definition is admitted FOR GENERATED RESEARCH, which
                  additionally requires authoritative coverage (state C)

A discovered field, or a dimension built in a notebook, is `registered` at most
until someone explicitly supplies authoritative coverage and calls `admit()`.
A registered-but-unadmitted dimension can never enter an interaction.

FAIL CLOSED
-----------
    - registering a key already bound to a DIFFERENT semantic identity raises
      `DimensionIdentityConflict`; a governed key is never redefined;
    - registering an equivalent definition deduplicates and returns the already
      registered instance (re-registration is not re-creation);
    - admitting without usable coverage raises `DimensionCoverageError`.

NO GENERATION
-------------
This registry NEVER proposes a dimension, never enumerates the registry into
combinations, and never produces a research question. It is a lookup and
admission authority only. Wave 2 owns feasibility and expansion decisions.

NO PRODUCTION AUTHORITY
-----------------------
Registration and admission are inert description updates. No runner, no
experiment, no candidate, no baseline and no production configuration is
reachable from this module. There is no import-time write and no persistence
store: the registry is an in-memory authority, and every dimension carries its
own canonical, deterministic serialisation (`GovernedDimension.to_dict`).
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    DimensionCoverageError,
    DimensionIdentityConflict,
    DimensionReadiness,
    DimensionValidationError,
    GovernedDimension,
)


def _require_instance(dimension: Any) -> GovernedDimension:
    if not isinstance(dimension, GovernedDimension):
        raise DimensionValidationError(
            f"expected GovernedDimension, got {type(dimension).__name__}")
    return dimension


class DimensionRegistry:
    """
    Governed, deterministic registry of research dimensions.

    Retrieval is by governed key or by semantic identity. Listing is always in
    deterministic (sorted) order. Nothing is admitted implicitly.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, GovernedDimension] = {}
        self._by_identity: dict[str, GovernedDimension] = {}
        self._admitted: dict[str, DimensionCoverage] = {}

    # ─── Registration ───────────────────────────────────────────────────

    def register(self, dimension: GovernedDimension) -> GovernedDimension:
        """
        Explicitly register a governed dimension definition.

        Semantic deduplication: an equivalent definition returns the instance
        already held, so a dimension proposed twice is one dimension. A key
        already bound to different semantics fails closed.
        """
        if not isinstance(dimension, GovernedDimension):
            raise DimensionValidationError(
                f"expected GovernedDimension, got {type(dimension).__name__}")
        existing = self._by_key.get(dimension.dimension_key)
        if existing is not None:
            if existing.semantic_identity != dimension.semantic_identity:
                raise DimensionIdentityConflict(
                    f"governed dimension key {dimension.dimension_key!r} is already bound to "
                    f"semantics {existing.dimension_identity}; refusing to redefine it as "
                    f"{dimension.dimension_identity}")
            return existing
        self._by_key[dimension.dimension_key] = dimension
        self._by_identity[dimension.semantic_identity] = dimension
        return dimension

    # ─── Admission ─────────────────────────────────────────────────────

    def admit(
        self,
        dimension: GovernedDimension,
        coverage: DimensionCoverage,
    ) -> GovernedDimension:
        """
        Admit a registered dimension for generated research.

        Requires usable authoritative coverage. A dimension is never admitted
        merely because a field exists, because data is non-null, or because it
        was discovered by `available_dimensions()`.
        """
        registered = self._by_key.get(dimension.dimension_key)
        if registered is None:
            raise DimensionValidationError(
                f"dimension {dimension.dimension_key!r} must be registered before admission")
        if registered.semantic_identity != dimension.semantic_identity:
            raise DimensionIdentityConflict(
                f"dimension {dimension.dimension_key!r} was redefined since registration; "
                "admission requires the registered governed definition")
        if not isinstance(coverage, DimensionCoverage):
            raise DimensionValidationError(
                f"coverage must be a DimensionCoverage, got {type(coverage).__name__}")
        if not coverage.has_coverage:
            raise DimensionCoverageError(
                f"dimension {dimension.dimension_key!r} cannot be admitted: "
                f"{coverage.rejection_reason or 'authoritative coverage is not usable'}")
        self._admitted[dimension.semantic_identity] = coverage
        return registered


    # ─── Introspection ─────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, dimension_key: object) -> bool:
        return dimension_key in self._by_key

    def __iter__(self) -> Iterator[GovernedDimension]:
        return iter(self.all())

    def all(self) -> tuple[GovernedDimension, ...]:
        """Every registered dimension, deterministically ordered by key."""
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_key))

    def get(self, dimension_key: str) -> GovernedDimension | None:
        return self._by_key.get(dimension_key)

    def get_by_identity(self, semantic_identity: str) -> GovernedDimension | None:
        return self._by_identity.get(semantic_identity)

    def registered(self) -> tuple[GovernedDimension, ...]:
        return self.all()

    def admitted(self) -> tuple[GovernedDimension, ...]:
        """Only dimensions admitted for generated research, ordered by key."""
        return tuple(self._by_identity[identity] for identity in sorted(self._admitted))

    def admitted_keys(self) -> tuple[str, ...]:
        return tuple(dimension.dimension_key for dimension in self.admitted())

    def is_registered(self, dimension: GovernedDimension | str) -> bool:
        key = dimension if isinstance(dimension, str) else _require_instance(
            dimension).dimension_key
        return key in self._by_key

    def is_admitted(self, dimension: GovernedDimension | str) -> bool:
        key = dimension if isinstance(dimension, str) else _require_instance(
            dimension).dimension_key
        held = self._by_key.get(key)
        return held is not None and held.semantic_identity in self._admitted

    def coverage_of(self, dimension: GovernedDimension | str) -> DimensionCoverage | None:
        key = dimension if isinstance(dimension, str) else _require_instance(
            dimension).dimension_key
        held = self._by_key.get(key)
        if held is None:
            return None
        return self._admitted.get(held.semantic_identity)

    def readiness(
        self,
        dimension: GovernedDimension | str,
        coverage: DimensionCoverage | None = None,
    ) -> DimensionReadiness:
        """
        The deterministic B/C/D verdict for one dimension, with a blocking reason.

        `coverage` defaults to the coverage supplied at admission. When a caller
        supplies a candidate coverage record that is not usable, the result is
        blocked and the reason is explicit.
        """
        key = dimension if isinstance(dimension, str) else _require_instance(
            dimension).dimension_key
        held = self._by_key.get(key)
        if held is None:
            raise DimensionValidationError(
                f"dimension {key!r} is not registered in this registry")
        effective = coverage if coverage is not None else self._admitted.get(
            held.semantic_identity)
        if effective is None:
            blocked = DimensionCoverage(
                observations_checked=0,
                observations_with_authoritative_value=0,
                authority_satisfied=held.authority_satisfied,
                currentness_satisfied=held.currentness_satisfied,
                rejection_reason="no authoritative coverage supplied",
            )
            return DimensionReadiness(
                dimension_identity=held.dimension_identity,
                dimension_key=held.dimension_key,
                coverage=blocked,
                admitted=False,
                blocking_reason=blocked.rejection_reason,
            )
        admitted = effective.has_coverage and self.is_admitted(held)
        return DimensionReadiness(
            dimension_identity=held.dimension_identity,
            dimension_key=held.dimension_key,
            coverage=effective,
            admitted=admitted,
            blocking_reason=None if admitted else (
                effective.rejection_reason
                or "dimension is not admitted for generated research"),
        )

    def assert_admitted(self, dimensions: Iterable[GovernedDimension | str]) -> None:
        """
        Fail closed unless every named dimension is admitted for generated research.

        This is the admission gate an interaction must pass.
        """
        for dimension in dimensions:
            key = (
                dimension if isinstance(dimension, str)
                else _require_instance(dimension).dimension_key
            )
            if not self.is_registered(key):
                raise DimensionValidationError(
                    f"dimension {key!r} is not a registered governed dimension")
            if not self.is_admitted(key):
                raise DimensionCoverageError(
                    f"dimension {key!r} is not admitted for generated research; an "
                    "interaction may only reference admitted governed dimensions")


    # ─── Serialisation (deterministic, no store, no import-time write) ──

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": [dimension.to_dict() for dimension in self.all()],
            "admitted": [
                {
                    "semantic_identity": identity,
                    "coverage": self._admitted[identity].to_dict(),
                }
                for identity in sorted(self._admitted)
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DimensionRegistry":
        """
        Rebuild a registry from canonical serialisation. Fails closed on
        malformed, unknown or conflicting content; never silently repairs.
        """
        if not isinstance(data, Mapping):
            raise DimensionValidationError("persisted registry must be a mapping")
        expected = {"dimensions", "admitted"}
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise DimensionValidationError(
                f"persisted registry fields are not exact (missing={missing}, unknown={unknown})")
        rows = data["dimensions"]
        admitted_rows = data["admitted"]
        if not isinstance(rows, list) or not isinstance(admitted_rows, list):
            raise DimensionValidationError(
                "persisted registry 'dimensions' and 'admitted' must be lists")
        registry = cls()
        for row in rows:
            registry.register(GovernedDimension.from_dict(row))
        for row in admitted_rows:
            if not isinstance(row, Mapping) or set(row) != {"semantic_identity", "coverage"}:
                raise DimensionValidationError(
                    "persisted admission rows must be exactly {semantic_identity, coverage}")
            dimension = registry.get_by_identity(row["semantic_identity"])
            if dimension is None:
                raise DimensionIdentityConflict(
                    "persisted admission references an unregistered dimension identity: "
                    f"{row['semantic_identity']!r}")
            registry.admit(dimension, DimensionCoverage.from_dict(row["coverage"]))
        return registry


__all__ = [
    "DimensionRegistry",
]
