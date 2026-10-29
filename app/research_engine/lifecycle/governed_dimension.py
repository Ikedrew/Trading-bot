"""
Governed Research Dimension v1 — the authoritative unit of Stage ③ segmentation.

Stage ③ / Wave 1. Wave 0 established the generated-research identity namespace
and deliberately reserved `dimension_ref` as `None` ("explicitly not governed
yet"). This module fills in exactly that extension point with a real, governed,
deterministically identifiable research dimension.

A dimension is an *authoritative description of how one segmentation variable is
obtained*. It answers "which field, from which authoritative source, under which
epoch/currentness rule" — never "which cells should be investigated".

A dimension is:
    - RESEARCH DESCRIPTION ONLY. Zero production, risk, sizing, configuration,
      baseline-activation or promotion authority. It is not a question, not an
      experiment, not a candidate, and it never executes.
    - GENERIC. Nothing here is hardcoded to regime/horizon. Any governed
      variable (symbol, session, execution quality, risk state, ...) is
      expressible through the same fields.
    - ADMISSION-CONTROLLED. Existing data (or a key discovered by
      `available_dimensions()`) never makes a dimension authoritative. Admission
      is explicit and requires an authoritative coverage record.

FOUR DISTINCT STATES (deliberately never collapsed)
---------------------------------------------------
    A. the field exists                      — says nothing about authority
    B. the field has an authoritative source — `GovernedDimension.authority`
    C. the field has usable coverage        — `DimensionCoverage`
    D. the dimension is admitted for
       generated research                    — `DimensionRegistry.admit()`

A dimension is only admitted (D) when B and C are both established. This layer
REFERENCES the repository's existing evidence machinery — `EvidenceAuthority`
and `EvidenceProducer` from `research_engine.registry.research_question_models`,
and the `lineage_coverage()` counters in `research_engine.evidence.base` — and
never re-implements a second evidence or readiness system.

IDENTITY
--------
`semantic_identity` is the SHA-256 digest of the canonical JSON encoding of
`semantic_material()`: the dimension key, the definition version, the governed
epoch requirement, the optional governed value domain, and the FULL authoritative
`EvidenceAuthority` (dataset, schema version, producer, field path, declared
semantic meaning, currentness eligibility).

Consequences, which are the point of the model:
    - presentation is NOT semantics. `label` is absent from the identity
      material, so renaming a dimension cannot silently redefine science;
    - authority is semantics. Changing the dataset, producer, field path,
      declared meaning, currentness rule, epoch requirement or governed value
      domain produces a different `semantic_identity` and `dimension_identity`;
    - insertion order is not identity: material is a mapping and is canonically
      encoded with sorted keys, so two equivalent dimensions proposed in any
      order resolve to the same identity.

FAIL CLOSED
-----------
Missing authority (no dataset, no field path, no declared semantic meaning) or
non-canonical material is rejected. Wave 1 never reconstructs a missing
historical dimension value, never infers authority from a field name, and never
treats non-null data as authoritative merely because it exists.

NO PRODUCTION AUTHORITY
-----------------------
This module imports no runner, no orchestrator, no governance gate, no candidate
machinery and no production configuration. It performs no I/O and writes nothing
at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)

# ─── Versions (clean reset: starts at 1, never > 1) ──────────────────────────

DIMENSION_SCHEMA_VERSION: int = 1

# ─── Namespace ──────────────────────────────────────────────────────────────
#
# `DIM-` is disjoint from the reserved `GEN-` research namespace and from every
# canonical programme prefix. It also matches the Wave 0 `dimension_ref` token
# grammar, so a governed reference can be carried by an existing Wave 0
# generated research record without any change to the Wave 0 identity contract.

DIMENSION_ID_PREFIX = "DIM-"
DIMENSION_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EPOCH_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DIM_ID_RE = re.compile(
    rf"^{re.escape(DIMENSION_ID_PREFIX)}[0-9A-F]{{{DIMENSION_ID_DIGEST_CHARS}}}$"
)



# ─── Errors (fail closed, never silently repaired) ───────────────────────────


class GovernedDimensionError(RuntimeError):
    """Base failure for the governed dimension/interaction algebra."""


class DimensionValidationError(GovernedDimensionError):
    """Dimension material is invalid, incomplete or not canonically encodable."""


class DimensionIdentityConflict(GovernedDimensionError):
    """A governed key/identity is being redefined with different semantics."""


class DimensionCoverageError(GovernedDimensionError):
    """Authoritative coverage/readiness could not be established."""


# ─── Canonical encoding helpers (reusing the Wave 0 encoder) ────────────────


def _encode(value: Any, label: str) -> str:
    """Canonical JSON via the Wave 0 encoder, re-typed as a dimension failure."""
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise DimensionValidationError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode(material, "semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DimensionValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise DimensionValidationError(f"{label} must not have surrounding whitespace")
    return value


def _normalise_declared_values(values: Any) -> tuple[str, ...] | None:
    """Normalise an optional governed value domain to canonical sorted order.

    `None` means "no governed domain is declared"; the layer never hardcodes a
    vocabulary such as TRENDING/RANGING and never requires one.
    """
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise DimensionValidationError(
            "declared_values must be a sequence of strings or None")
    declared = tuple(_require_text(item, "declared_value") for item in values)
    if len(set(declared)) != len(declared):
        raise DimensionValidationError("declared_values must not contain duplicates")
    return tuple(sorted(declared))



# ─── Coverage / readiness ───────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionCoverage:
    """
    Deterministic, serialisable authoritative coverage/readiness for a dimension.

    This is states B and C of the four-state ladder, expressed once. It holds
    counts and booleans only: no inferred authority, no reconstructed history,
    and no feasibility threshold for future research (Wave 2 owns that).
    """

    observations_checked: int
    observations_with_authoritative_value: int
    authority_satisfied: bool
    currentness_satisfied: bool
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.observations_checked < 0 or self.observations_with_authoritative_value < 0:
            raise DimensionValidationError("observation counts must not be negative")
        if self.observations_with_authoritative_value > self.observations_checked:
            raise DimensionValidationError(
                "observations_with_authoritative_value must not exceed observations_checked")

    @property
    def coverage_fraction(self) -> float:
        """Fraction of checked observations carrying an authoritative value."""
        if self.observations_checked == 0:
            return 0.0
        return self.observations_with_authoritative_value / self.observations_checked

    @property
    def has_coverage(self) -> bool:
        """Usable coverage: authority AND currentness AND a non-empty denominator."""
        return (
            self.authority_satisfied
            and self.currentness_satisfied
            and self.observations_checked > 0
            and self.observations_with_authoritative_value > 0
            and self.rejection_reason is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations_checked": self.observations_checked,
            "observations_with_authoritative_value": self.observations_with_authoritative_value,
            "coverage_fraction": self.coverage_fraction,
            "authority_satisfied": self.authority_satisfied,
            "currentness_satisfied": self.currentness_satisfied,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DimensionCoverage":
        if not isinstance(data, Mapping):
            raise DimensionValidationError("coverage must be a mapping")
        required = {
            "observations_checked",
            "observations_with_authoritative_value",
            "authority_satisfied",
            "currentness_satisfied",
            "rejection_reason",
        }
        missing = sorted(required - set(data))
        unknown = sorted(set(data) - required - {"coverage_fraction"})
        if missing or unknown:
            raise DimensionValidationError(
                f"coverage fields are not exact (missing={missing}, unknown={unknown})")
        if not isinstance(data["authority_satisfied"], bool) or not isinstance(
                data["currentness_satisfied"], bool):
            raise DimensionValidationError(
                "coverage authority/currentness flags must be booleans")
        coverage = cls(
            observations_checked=data["observations_checked"],
            observations_with_authoritative_value=data["observations_with_authoritative_value"],
            authority_satisfied=data["authority_satisfied"],
            currentness_satisfied=data["currentness_satisfied"],
            rejection_reason=data["rejection_reason"],
        )
        # `coverage_fraction` is derived, but if it is persisted it must agree
        # exactly: a drifted denominator is a fail-closed integrity failure.
        if "coverage_fraction" in data:
            presented = data["coverage_fraction"]
            if not isinstance(presented, (int, float)) or isinstance(presented, bool):
                raise DimensionValidationError("coverage_fraction must be a number")
            if float(presented) != coverage.coverage_fraction:
                raise DimensionValidationError(
                    "persisted coverage_fraction does not match the observation counts")
        return coverage




@dataclass(frozen=True)
class DimensionReadiness:
    """The B/C/D verdict for one dimension: covered and admitted, or blocked."""

    dimension_identity: str
    dimension_key: str
    coverage: DimensionCoverage
    admitted: bool
    blocking_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_identity": self.dimension_identity,
            "dimension_key": self.dimension_key,
            "coverage": self.coverage.to_dict(),
            "admitted": self.admitted,
            "blocking_reason": self.blocking_reason,
        }


def coverage_from_lineage(
    dimension: "GovernedDimension",
    lineage: Mapping[str, Any] | None,
    *,
    authority_satisfied: bool | None = None,
    rejection_reason: str | None = None,
) -> DimensionCoverage:
    """
    Build coverage by CONSUMING the repository's existing lineage counters.

    `lineage` is the mapping returned by
    `research_engine.evidence.base.lineage_coverage(records, (field_path,))`.
    This function reads that existing accounting instead of counting records a
    second time, and never opens a dataset, a network path or a store itself.

    Fail closed: absent lineage, an absent key entry, a non-integer count, a
    missing authority, an ineligible currentness rule, or an explicit rejection
    reason all yield a NON-usable `DimensionCoverage`.
    """
    field_path = dimension.authority.field_path
    satisfied = (
        dimension.authority_satisfied if authority_satisfied is None
        else bool(authority_satisfied)
    )

    reason: str | None = rejection_reason
    checked = 0
    authoritative = 0

    if not satisfied:
        reason = reason or f"no authoritative source for {field_path!r}"
    if not dimension.currentness_satisfied:
        reason = reason or "authority is not current-eligible for this epoch"
    if lineage is None:
        reason = reason or "no lineage coverage supplied for the governed field path"
    else:
        raw_total = lineage.get("total_records")
        entry = (lineage.get("key_coverage") or {}).get(field_path)
        if not isinstance(raw_total, int) or isinstance(raw_total, bool) or raw_total < 0:
            reason = reason or "lineage coverage did not report a usable total_records"
        elif not isinstance(entry, Mapping):
            reason = reason or (
                f"lineage coverage has no key entry for governed field path {field_path!r}")
        else:
            non_empty = entry.get("non_empty")
            if (not isinstance(non_empty, int) or isinstance(non_empty, bool)
                    or non_empty < 0 or non_empty > raw_total):
                reason = reason or "lineage coverage reported a non-usable non_empty count"
            else:
                checked = raw_total
                authoritative = non_empty
                if raw_total == 0:
                    reason = reason or "no observations were checked for this dimension"
                elif non_empty == 0:
                    reason = reason or "no observation carries an authoritative dimension value"

    return DimensionCoverage(
        observations_checked=checked,
        observations_with_authoritative_value=authoritative,
        authority_satisfied=satisfied,
        currentness_satisfied=dimension.currentness_satisfied,
        rejection_reason=reason,
    )



# ─── Dimension ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GovernedDimension:
    """
    An immutable, governed research dimension with a deterministic identity.

    `label` is presentation only and is deliberately NOT part of the identity
    material, so a cosmetic rename cannot silently redefine a scientific
    dimension. Everything that changes what the dimension MEANS — the
    authoritative dataset, producer, field path, declared meaning, currentness
    rule, epoch requirement and governed value domain — IS part of the identity.
    """

    dimension_key: str
    authority: EvidenceAuthority
    label: str = ""
    required_epoch: str | None = None
    declared_values: tuple[str, ...] | None = None
    schema_version: int = DIMENSION_SCHEMA_VERSION
    semantic_identity: str = ""
    dimension_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "declared_values", _normalise_declared_values(self.declared_values))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = dimension_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise DimensionIdentityConflict(
                    "presented semantic identity does not match the governed material for "
                    f"dimension {self.dimension_key!r}")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.dimension_identity:
            if self.dimension_identity != expected_id:
                raise DimensionIdentityConflict(
                    f"presented dimension identity {self.dimension_identity!r} does not match "
                    f"the governed material for dimension {self.dimension_key!r}")
        else:
            object.__setattr__(self, "dimension_identity", expected_id)

    # ─── Construction ───────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        dimension_key: str,
        authority: EvidenceAuthority,
        label: str = "",
        required_epoch: str | None = None,
        declared_values: Sequence[str] | None = None,
    ) -> "GovernedDimension":
        """Build a governed dimension. Identity is derived, never supplied."""
        return cls(
            dimension_key=dimension_key,
            authority=authority,
            label=label,
            required_epoch=required_epoch,
            declared_values=tuple(declared_values) if declared_values is not None else None,
        )


    # ─── Identity material ──────────────────────────────────────────────

    def semantic_material(self) -> dict[str, Any]:
        """The frozen semantic material that defines scientific identity."""
        return {
            "kind": "governed_dimension",
            "schema_version": self.schema_version,
            "dimension_key": self.dimension_key,
            "required_epoch": self.required_epoch,
            "declared_values": (
                list(self.declared_values) if self.declared_values is not None else None),
            "authority": _authority_material(self.authority),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_key": self.dimension_key,
            "label": self.label,
            "required_epoch": self.required_epoch,
            "declared_values": (
                list(self.declared_values) if self.declared_values is not None else None),
            "schema_version": self.schema_version,
            "semantic_identity": self.semantic_identity,
            "dimension_identity": self.dimension_identity,
            "authority": _authority_material(self.authority),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GovernedDimension":
        if not isinstance(data, Mapping):
            raise DimensionValidationError("persisted dimension must be a mapping")
        expected = {
            "dimension_key", "label", "required_epoch", "declared_values",
            "schema_version", "semantic_identity", "dimension_identity", "authority",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise DimensionValidationError(
                f"persisted dimension fields are not exact (missing={missing}, unknown={unknown})")
        authority = data["authority"]
        if not isinstance(authority, Mapping):
            raise DimensionValidationError("persisted authority must be a mapping")
        declared = data["declared_values"]
        return cls(
            dimension_key=data["dimension_key"],
            label=data["label"],
            required_epoch=data["required_epoch"],
            declared_values=tuple(declared) if declared is not None else None,
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            dimension_identity=data["dimension_identity"],
            authority=_authority_from_material(authority),
        )


    # ─── Authority facts ────────────────────────────────────────────────

    @property
    def authority_satisfied(self) -> bool:
        """True only when a real, declared, semantic authority exists (state B)."""
        authority = self.authority
        if not isinstance(authority, EvidenceAuthority):
            return False
        producer = authority.producer
        if isinstance(producer, EvidenceProducer):
            producer_ok = True
        elif producer is None or isinstance(producer, str):
            producer_ok = producer is None or bool(producer.strip())
        else:
            producer_ok = False
        return bool(
            isinstance(authority.dataset, str) and authority.dataset.strip()
            and producer_ok
            and isinstance(authority.field_path, str) and authority.field_path.strip()
            and isinstance(authority.semantic_meaning, str) and authority.semantic_meaning.strip()
        )

    @property
    def currentness_satisfied(self) -> bool:
        return bool(getattr(self.authority, "current_eligibility", False))

    def is_declared(self, value: Any) -> bool:
        """Whether a slice value is inside this dimension's governed domain.

        Returns True when no domain is declared: this layer never invents a
        vocabulary, and Wave 2 owns any value-level feasibility rule.
        """
        if self.declared_values is None:
            return True
        return isinstance(value, str) and value in self.declared_values

    def coverage(self, lineage: Mapping[str, Any] | None, **kwargs: Any) -> DimensionCoverage:
        """Authoritative coverage for this dimension from existing lineage data."""
        return coverage_from_lineage(self, lineage, **kwargs)

    # ─── Validation ─────────────────────────────────────────────────────

    def _validate(self) -> "GovernedDimension":
        if self.schema_version != DIMENSION_SCHEMA_VERSION:
            raise DimensionValidationError(
                f"dimension schema_version must be {DIMENSION_SCHEMA_VERSION} (clean reset), "
                f"got {self.schema_version!r}")
        key = _require_text(self.dimension_key, "dimension_key")
        if not _KEY_RE.match(key):
            raise DimensionValidationError(
                f"dimension_key must be a lower_snake_case identity key, got {key!r}")
        if not isinstance(self.authority, EvidenceAuthority):
            raise DimensionValidationError(
                f"authority must be an EvidenceAuthority, got {type(self.authority).__name__}")
        if not isinstance(self.label, str):
            raise DimensionValidationError("label must be a string")
        if self.required_epoch is not None:
            epoch = _require_text(self.required_epoch, "required_epoch")
            if not _EPOCH_RE.match(epoch):
                raise DimensionValidationError(
                    f"required_epoch must be an upper-case epoch token, got {epoch!r}")
        if not self.authority_satisfied:
            raise DimensionValidationError(
                f"dimension {self.dimension_key!r} has no authoritative source: dataset, "
                "field_path and semantic_meaning are all required (authority is never "
                "inferred from a field name)")
        # Fail closed on material that cannot be canonically encoded.
        _encode(self.semantic_material(), "dimension semantic material")
        return self



def dimension_identity_for(semantic_identity: str) -> str:
    """Derive the governed display identity deterministically from a digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise DimensionValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{DIMENSION_ID_PREFIX}{semantic_identity[:DIMENSION_ID_DIGEST_CHARS].upper()}"


def is_dimension_identity(value: Any) -> bool:
    """True only for IDs inside the governed dimension namespace."""
    return isinstance(value, str) and bool(_DIM_ID_RE.match(value))


def _authority_material(authority: EvidenceAuthority) -> dict[str, Any]:
    """Canonical, JSON-native form of the authoritative source."""
    producer = authority.producer
    if isinstance(producer, EvidenceProducer):
        producer_value: str | None = producer.value
    elif producer is None:
        producer_value = None
    elif isinstance(producer, str):
        producer_value = producer
    else:
        raise DimensionValidationError(
            f"evidence producer must be an EvidenceProducer, got {type(producer).__name__}")
    return {
        "dataset": authority.dataset,
        "schema_version": authority.schema_version,
        "producer": producer_value,
        "field_path": authority.field_path,
        "semantic_meaning": authority.semantic_meaning,
        "current_eligibility": bool(authority.current_eligibility),
    }


def _authority_from_material(material: Mapping[str, Any]) -> EvidenceAuthority:
    expected = {
        "dataset", "schema_version", "producer", "field_path",
        "semantic_meaning", "current_eligibility",
    }
    missing = sorted(expected - set(material))
    unknown = sorted(set(material) - expected)
    if missing or unknown:
        raise DimensionValidationError(
            f"persisted authority fields are not exact (missing={missing}, unknown={unknown})")
    producer_value = material["producer"]
    producer: EvidenceProducer | None
    if producer_value is None:
        producer = None
    else:
        try:
            producer = EvidenceProducer(producer_value)
        except ValueError as exc:
            raise DimensionValidationError(
                f"unknown evidence producer: {producer_value!r}") from exc
    return EvidenceAuthority(
        dataset=material["dataset"],
        schema_version=material["schema_version"],
        producer=producer,
        field_path=material["field_path"],
        semantic_meaning=material["semantic_meaning"],
        current_eligibility=bool(material["current_eligibility"]),
    )



# ─── Discovery (NOT admission) ──────────────────────────────────────────────


def candidate_dimension_keys(events: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """
    Suggest candidate dimension keys from event data. NEVER admits anything.

    Delegates to the existing `available_dimensions()` substrate in
    `research_engine.v10.research_intelligence.question_discovery` and returns
    only the discovered KEYS, in deterministic order. Discovered field names are
    not authority: each candidate still needs an explicit `EvidenceAuthority`
    and explicit registry admission before it can enter an interaction.
    """
    from research_engine.v10.research_intelligence.question_discovery import (
        QuestionDiscovery,
    )

    discovered = QuestionDiscovery(list(events)).available_dimensions()
    return tuple(sorted(discovered))


__all__ = [
    "DIMENSION_ID_DIGEST_CHARS",
    "DIMENSION_ID_PREFIX",
    "DIMENSION_SCHEMA_VERSION",
    "DimensionCoverage",
    "DimensionCoverageError",
    "DimensionIdentityConflict",
    "DimensionReadiness",
    "DimensionValidationError",
    "GovernedDimension",
    "GovernedDimensionError",
    "candidate_dimension_keys",
    "coverage_from_lineage",
    "dimension_identity_for",
    "is_dimension_identity",
]
