"""
Eligibility Evidence Freeze v1 -- the T0 evidence boundary for one decision.

Stage 3 / Wave 2. Representation (Wave 1) and support requirements exist; what
is still missing is a FROZEN, reproducible statement of *which* evidence a
single eligibility decision was made against. Without that, a later
discovery/confirmation split (Wave 4) and any audit of a PERMIT would be
unreproducible.

This module is that freeze, and only that:

    CommonSupport               authoritative, JOINTLY ELIGIBLE population count
    CellSupport                 per-cell usable counts for declared cells
    EligibilityEvidenceFreeze   immutable snapshot binding both to T0

REUSE, NOT REINVENTION
=======================
    - `EvidenceAuthority` / `JoinContract` come from
      `research_engine.registry.research_question_models`;
    - `DatasetFingerprint` / `build_dataset_fingerprint` come from
      `research_engine.lifecycle.dataset_fingerprint`, the repository's existing
      immutable population-identity contract. Wave 2 REUSES it rather than
      inventing a weaker duplicate;
    - `lineage_coverage` from `research_engine.evidence.base` is the accounting
      substrate; `common_support_from_lineage` reads its already-computed
      per-field coverage and requires the caller's own joint-population
      contract to prove the intersection.

COMMON SUPPORT IS NOT THE PRODUCT OF MARGINAL COUNTS
====================================================
`regime` covering 10,000 records and `horizon` covering 10,000 records does NOT
prove 10,000 jointly usable observations. This module therefore distinguishes,
mechanically:

    marginal counts -- per-dimension coverage from the existing lineage
                       accounting. Recorded as context. NEVER sufficient.
    joint counts    -- the caller's authoritative jointly eligible/common
                       population, expressed through an existing population
                       contract (`joint_population_ref`) and an explicit
                       eligibility predicate. This is the only count the gate
                       treats as common support.

`CommonSupport` is constructed ONLY from a joint population. There is no
constructor that accepts marginal counts, and `common_support_from_lineage`
refuses marginal-only lineage rather than multiplying, summing or min()-ing
marginal counts into a fabricated intersection.

NO INVENTED VALUES
==================
Missing historical fields are never reconstructed, missing values are never
inferred, and a non-null field is never treated as evidence authority. A cell
with no observed count is reported as unproven/unsupported, never as
zero-supported-and-therefore-fine.

T0 IS PROVENANCE, NOT IDENTITY
==============================
`t0` (decision timestamp) is recorded but is deliberately ABSENT from
`semantic_material()`. Identity comes from the evidence population, the dataset
content identity, the coverage boundary and the declared requirements.
Re-deciding the same evidence tomorrow yields the same evidence identity;
moving the boundary or altering the fingerprint does not.

NO PRODUCTION AUTHORITY
-----------------------
This module performs no I/O, opens no dataset, starts no network call, executes
no research, creates no research question and writes nothing at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from research_engine.lifecycle.dataset_fingerprint import (
    DatasetFingerprint,
    build_dataset_fingerprint,
)
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import GovernedDimension
from research_engine.lifecycle.interaction_feasibility import (
    FEASIBILITY_SCHEMA_VERSION,
    FeasibilitySpecification,
    FeasibilityValidationError,
    is_feasibility_identity,
)
from research_engine.lifecycle.research_interaction import (
    INTERACTION_ID_PREFIX,
    InteractionSlice,
    ResearchInteraction,
    is_interaction_identity,
    is_slice_identity,
)
from research_engine.registry.research_question_models import JoinContract

# --- Versions (clean reset: starts at 1, never > 1) -------------------------

EVIDENCE_FREEZE_SCHEMA_VERSION: int = 1

# --- Namespace --------------------------------------------------------------
#
# `EVD-` is disjoint from `GEN-`, `DIM-`, `IXN-`, `SLC-`, `FSP-` and every
# canonical programme prefix. A freeze identity is a provenance identity, never
# a research question and never a production artefact.

EVIDENCE_FREEZE_ID_PREFIX = "EVD-"
_EVIDENCE_FREEZE_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_EVD_ID_RE = re.compile(
    rf"^{re.escape(EVIDENCE_FREEZE_ID_PREFIX)}"
    rf"[0-9A-F]{{{_EVIDENCE_FREEZE_ID_DIGEST_CHARS}}}$")
_REF_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:-]+$")


class EvidenceFreezeError(RuntimeError):
    """Evidence/freeze material is invalid, incomplete or unprovable."""


# --- Canonical encoding helpers (reusing the Wave 0 encoder) ----------------


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise EvidenceFreezeError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode(material, "semantic material").encode("utf-8")).hexdigest()


def evidence_freeze_identity_for(semantic_identity: str) -> str:
    """Derive the governed evidence-freeze identity from a semantic digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise EvidenceFreezeError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return (
        f"{EVIDENCE_FREEZE_ID_PREFIX}"
        f"{semantic_identity[:_EVIDENCE_FREEZE_ID_DIGEST_CHARS].upper()}"
    )


def is_evidence_freeze_identity(value: Any) -> bool:
    """True only for IDs inside the eligibility evidence-freeze namespace."""
    return isinstance(value, str) and bool(_EVD_ID_RE.match(value))


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceFreezeError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise EvidenceFreezeError(f"{label} must not have surrounding whitespace")
    return value


def _require_ref(value: Any, label: str) -> str:
    """A governed `kind:token` reference to an existing population/dataset."""
    text = _require_text(value, label)
    if not _REF_RE.match(text):
        raise EvidenceFreezeError(
            f"{label} must be a governed 'kind:token' reference, got {text!r}")
    return text


def _require_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceFreezeError(f"{label} must be an integer, got {value!r}")
    if value < 0:
        raise EvidenceFreezeError(f"{label} must not be negative, got {value!r}")
    return int(value)


def _canonical_dimensions(
    dimensions: Iterable[GovernedDimension],
) -> tuple[GovernedDimension, ...]:
    """Canonical dimension order. A set, never a caller sequence order."""
    if isinstance(dimensions, (str, bytes)) or not isinstance(
            dimensions, (list, tuple, set, frozenset)):
        raise EvidenceFreezeError("dimensions must be a sequence of GovernedDimension")
    items = tuple(dimensions)
    for item in items:
        if not isinstance(item, GovernedDimension):
            raise EvidenceFreezeError(
                f"expected a GovernedDimension, got {type(item).__name__}")
    if not items:
        raise EvidenceFreezeError(
            "evidence must be declared over at least one governed dimension")
    identities = [d.dimension_identity for d in items]
    if len(set(identities)) != len(identities):
        raise EvidenceFreezeError("a dimension may not be repeated in one evidence record")
    return tuple(sorted(items, key=lambda d: d.dimension_identity))


def _join_contract_from_dict(data: Mapping[str, Any]) -> JoinContract:
    """Rebuild a `JoinContract` from its existing canonical serialisation."""
    if not isinstance(data, Mapping):
        raise EvidenceFreezeError("persisted join_contract must be a mapping")
    keys = data.get("join_keys", ())
    if isinstance(keys, (str, bytes)) or not isinstance(keys, (list, tuple)):
        raise EvidenceFreezeError("persisted join_contract join_keys must be a list")
    return JoinContract(
        join_keys=tuple(keys),
        cardinality=data.get("cardinality", "many_to_one"),
        conflict_policy=data.get("conflict_policy", "reject"),
        description=data.get("description", ""),
    )


def _canonical_cells(cells: Iterable[CellSupport]) -> tuple[CellSupport, ...]:
    """Canonical cell order: sorted by slice identity, never by caller order.

    A repeated cell is rejected outright rather than deduplicated, so a
    double-counted cell cannot inflate coverage.
    """
    if isinstance(cells, (str, bytes)) or not isinstance(cells, (list, tuple)):
        raise EvidenceFreezeError("cell_support must be a sequence of CellSupport")
    items = tuple(cells)
    for item in items:
        if not isinstance(item, CellSupport):
            raise EvidenceFreezeError(
                f"expected a CellSupport, got {type(item).__name__}")
    identities = [cell.slice_identity for cell in items]
    if len(set(identities)) != len(identities):
        duplicates = sorted({i for i in identities if identities.count(i) > 1})
        raise EvidenceFreezeError(f"cell_support must not repeat a cell: {duplicates}")
    return tuple(sorted(items, key=lambda cell: cell.slice_identity))


def _rebuild_interaction(
    interaction_identity: str,
    dimensions: Sequence[GovernedDimension],
) -> ResearchInteraction:
    """Rebuild the frozen interaction and verify it matches the frozen identity."""
    if not is_interaction_identity(interaction_identity):
        raise EvidenceFreezeError(
            f"persisted interaction_identity {interaction_identity!r} is not a governed "
            "interaction identity")
    interaction = ResearchInteraction.create(tuple(dimensions))
    if interaction.interaction_identity != interaction_identity:
        raise EvidenceFreezeError(
            "persisted interaction_identity does not match the persisted dimension set")
    return interaction



# --- Common (joint) support -------------------------------------------------


@dataclass(frozen=True)
class CommonSupport:
    """
    The authoritative JOINTLY ELIGIBLE observation count for one interaction.

    Constructed ONLY from a joint population. There is deliberately no
    constructor, keyword or classmethod that accepts marginal per-dimension
    counts: this object cannot represent `min(a, b)` or `a * b` pretending to be
    an intersection.

    `marginal_observations` is CONTEXT ONLY. It is the per-dimension coverage
    the existing lineage accounting reported, kept so a reader can see why the
    joint count is so much smaller. It never satisfies a support requirement.
    """

    joint_population_ref: str
    joint_usable_observations: int
    dimensions: tuple[GovernedDimension, ...]
    dataset_fingerprint: DatasetFingerprint | None = None
    marginal_observations: Mapping[str, int] | None = None
    join_contract: JoinContract | None = None
    joinability_proven: bool = False
    chronology_reference: str = ""
    schema_version: int = EVIDENCE_FREEZE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensions", _canonical_dimensions(self.dimensions))
        object.__setattr__(
            self, "joint_population_ref",
            _require_ref(self.joint_population_ref, "joint_population_ref"))
        object.__setattr__(
            self, "joint_usable_observations",
            _require_count(self.joint_usable_observations, "joint_usable_observations"))
        if self.marginal_observations is not None:
            marginal = {}
            for key, value in dict(self.marginal_observations).items():
                dimension_key = _require_text(key, "marginal_observations key")
                marginal[dimension_key] = _require_count(
                    value, f"marginal observation count for {dimension_key!r}")
            object.__setattr__(self, "marginal_observations", dict(sorted(marginal.items())))
        if self.chronology_reference:
            object.__setattr__(
                self, "chronology_reference",
                _require_text(self.chronology_reference, "chronology_reference"))
        self._validate()

    # --- Construction ----------------------------------------------------

    @classmethod
    def from_joint_population(
        cls,
        joint_population_ref: str,
        joint_usable_observations: int,
        dimensions: Sequence[GovernedDimension],
        *,
        dataset_fingerprint: DatasetFingerprint | None = None,
        marginal_observations: Mapping[str, int] | None = None,
        join_contract: JoinContract | None = None,
        joinability_proven: bool = False,
        chronology_reference: str = "",
    ) -> "CommonSupport":
        """
        Declare common support from an AUTHORITATIVE joint population.

        The caller asserts that `joint_usable_observations` counts observations
        eligible for EVERY dimension of the interaction simultaneously, and that
        the population is identified by `joint_population_ref`. Wave 2 verifies
        and freezes that assertion; it does not compute the intersection itself
        and it never fabricates one from marginals.
        """
        return cls(
            joint_population_ref=joint_population_ref,
            joint_usable_observations=joint_usable_observations,
            dimensions=tuple(dimensions),
            dataset_fingerprint=dataset_fingerprint,
            marginal_observations=marginal_observations,
            join_contract=join_contract,
            joinability_proven=joinability_proven,
            chronology_reference=chronology_reference,
        )

    # --- Views ----------------------------------------------------------

    @property
    def has_joint_support(self) -> bool:
        """Common support exists and is usable. Never derived from marginals."""
        return (
            self.joint_population_ref.strip() != ""
            and self.joint_usable_observations > 0
        )

    @property
    def dimension_keys(self) -> tuple[str, ...]:
        return tuple(d.dimension_key for d in self.dimensions)

    @property
    def uses_multiple_datasets(self) -> bool:
        """Whether the interaction spans more than one authoritative dataset."""
        return len({d.authority.dataset for d in self.dimensions}) > 1

    # --- Identity material ----------------------------------------------

    @staticmethod
    def _fingerprint_material(
        fingerprint: DatasetFingerprint | None,
    ) -> dict[str, Any] | None:
        """
        The CONTENT identity of a population fingerprint.

        `generated_timestamp` is deliberately EXCLUDED: it records WHEN the
        fingerprint was computed, not WHAT it identifies. Including it would make
        the frozen evidence identity move every time the same population was
        re-fingerprinted, which would destroy decision reproducibility.
        """
        if fingerprint is None:
            return None
        return {
            "dataset_id": fingerprint.dataset_id,
            "dataset_version": fingerprint.dataset_version,
            "fingerprint_algorithm": fingerprint.fingerprint_algorithm,
            "content_hash": fingerprint.content_hash,
            "observation_count": fingerprint.observation_count,
            "first_timestamp": fingerprint.first_timestamp,
            "last_timestamp": fingerprint.last_timestamp,
            "population": fingerprint.population,
            "schema_version": fingerprint.schema_version,
            "symbols": list(fingerprint.symbols),
            "filters_applied": list(fingerprint.filters_applied),
        }

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "common_support",
            "schema_version": self.schema_version,
            "joint_population_ref": self.joint_population_ref,
            "joint_usable_observations": self.joint_usable_observations,
            "dimensions": [d.semantic_material() for d in self.dimensions],
            "dataset_fingerprint": self._fingerprint_material(self.dataset_fingerprint),
            "join_contract": (
                self.join_contract.to_dict() if self.join_contract is not None else None),
            "joinability_proven": bool(self.joinability_proven),
            "chronology_reference": self.chronology_reference,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "marginal_observations": (
                dict(self.marginal_observations)
                if self.marginal_observations is not None else None),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommonSupport":
        """Strict deserialisation. Unknown/missing content fails closed."""
        if not isinstance(data, Mapping):
            raise EvidenceFreezeError(
                f"persisted common support must be a mapping, got {type(data).__name__}")
        # Marginal coverage is CONTEXT and is deliberately absent from the frozen
        # semantic material, so a persisted freeze need not carry it. A carried
        # value is ignored rather than promoted into the frozen evidence.
        data = {key: value for key, value in data.items() if key != "marginal_observations"}
        expected = {
            "kind", "schema_version", "joint_population_ref", "joint_usable_observations",
            "dimensions", "dataset_fingerprint", "join_contract", "joinability_proven",
            "chronology_reference",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EvidenceFreezeError(
                f"persisted common support fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "common_support":
            raise EvidenceFreezeError(
                f"persisted common support kind must be 'common_support', got {data['kind']!r}")
        rows = data["dimensions"]
        if not isinstance(rows, list):
            raise EvidenceFreezeError("persisted common support 'dimensions' must be a list")
        fingerprint = data["dataset_fingerprint"]
        if fingerprint is not None and not isinstance(fingerprint, Mapping):
            raise EvidenceFreezeError("persisted dataset_fingerprint must be a mapping or None")
        join_contract = data["join_contract"]
        return cls(
            joint_population_ref=data["joint_population_ref"],
            joint_usable_observations=data["joint_usable_observations"],
            dimensions=tuple(GovernedDimension.from_dict(row) for row in rows),
            dataset_fingerprint=(
                DatasetFingerprint.from_dict(dict(fingerprint))
                if fingerprint is not None else None),
            # Marginal coverage is never restored: it is context, not frozen
            # evidence, and restoring it would imply it was part of the freeze.
            marginal_observations=None,
            join_contract=(
                _join_contract_from_dict(join_contract) if join_contract is not None else None),
            joinability_proven=bool(data["joinability_proven"]),
            chronology_reference=data["chronology_reference"],
            schema_version=data["schema_version"],
        )

    # --- Validation -----------------------------------------------------

    def _validate(self) -> "CommonSupport":
        if self.schema_version != EVIDENCE_FREEZE_SCHEMA_VERSION:
            raise EvidenceFreezeError(
                f"common support schema_version must be {EVIDENCE_FREEZE_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not isinstance(self.joinability_proven, bool):
            raise EvidenceFreezeError("joinability_proven must be a boolean")
        if self.join_contract is not None and not isinstance(self.join_contract, JoinContract):
            raise EvidenceFreezeError(
                f"join_contract must be a JoinContract, got {type(self.join_contract).__name__}")
        if self.joinability_proven and self.uses_multiple_datasets:
            if self.join_contract is None:
                raise EvidenceFreezeError(
                    "an interaction spanning multiple datasets cannot claim proven "
                    "joinability without a governed JoinContract")
        if self.dataset_fingerprint is not None and not isinstance(
                self.dataset_fingerprint, DatasetFingerprint):
            raise EvidenceFreezeError(
                "dataset_fingerprint must be a DatasetFingerprint, got "
                f"{type(self.dataset_fingerprint).__name__}")
        _encode(self.semantic_material(), "common support semantic material")
        return self


# --- Cell / slice support ---------------------------------------------------


@dataclass(frozen=True)
class CellSupport:
    """
    Usable support for ONE explicitly declared analytical cell.

    A cell is an `InteractionSlice` from Wave 1: an already-explicit, already
    admitted cell. This class never enumerates cells, never expands a value
    domain and never creates a slice. It records what the caller observed for a
    cell somebody else declared.

    `required_observations` is a PER-CELL threshold supplied by the proposal or
    its feasibility specification. It is `None` when no governed threshold was
    supplied -- and it is never replaced by a default.
    """

    slice_identity: str
    slice_description: str
    usable_observations: int
    required_observations: int | None = None
    schema_version: int = EVIDENCE_FREEZE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not is_slice_identity(self.slice_identity):
            raise EvidenceFreezeError(
                "cell support must reference a governed Wave 1 slice identity, got "
                f"{self.slice_identity!r}")
        object.__setattr__(
            self, "slice_description",
            _require_text(self.slice_description, "slice_description"))
        object.__setattr__(
            self, "usable_observations",
            _require_count(self.usable_observations, "usable_observations"))
        if self.required_observations is not None:
            object.__setattr__(
                self, "required_observations",
                _require_count(self.required_observations, "required_observations"))
        if self.schema_version != EVIDENCE_FREEZE_SCHEMA_VERSION:
            raise EvidenceFreezeError(
                f"cell support schema_version must be {EVIDENCE_FREEZE_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")

    @classmethod
    def for_slice(
        cls,
        cell: InteractionSlice,
        usable_observations: int,
        *,
        required_observations: int | None = None,
    ) -> "CellSupport":
        """Record observed support for an EXPLICITLY declared Wave 1 slice."""
        if not isinstance(cell, InteractionSlice):
            raise EvidenceFreezeError(
                f"expected an InteractionSlice, got {type(cell).__name__}")
        return cls(
            slice_identity=cell.slice_identity,
            slice_description=str(cell),
            usable_observations=usable_observations,
            required_observations=required_observations,
        )

    @property
    def meets_requirement(self) -> bool:
        """Whether the cell meets its own governed requirement."""
        if self.required_observations is None:
            return True
        return self.usable_observations >= self.required_observations

    @property
    def requirement_is_declared(self) -> bool:
        return self.required_observations is not None

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "cell_support",
            "schema_version": self.schema_version,
            "slice_identity": self.slice_identity,
            "usable_observations": self.usable_observations,
            "required_observations": self.required_observations,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "slice_description": self.slice_description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CellSupport":
        if not isinstance(data, Mapping):
            raise EvidenceFreezeError(
                f"persisted cell support must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "slice_identity", "slice_description",
            "usable_observations", "required_observations",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EvidenceFreezeError(
                f"persisted cell support fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "cell_support":
            raise EvidenceFreezeError(
                f"persisted cell support kind must be 'cell_support', got {data['kind']!r}")
        return cls(
            slice_identity=data["slice_identity"],
            slice_description=data["slice_description"],
            usable_observations=data["usable_observations"],
            required_observations=data["required_observations"],
            schema_version=data["schema_version"],
        )


# --- The freeze -------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityEvidenceFreeze:
    """
    The immutable T0 evidence snapshot ONE eligibility decision is made against.

    Identity material (hashed into `evidence_identity`):
        interaction identity, common-support material (joint population ref,
        joint usable count, dimensions, dataset fingerprint, join contract,
        joinability, chronology reference), per-cell support, the evidence
        boundary, the feasibility specification identity and the parent
        justification identity.

    NOT identity material: `t0`. A timestamp alone is never enough, and moving
    the decision time does not silently redefine the evidence. Conversely,
    altering the dataset fingerprint, the joint population reference, the joint
    count, the cell support or the evidence boundary DOES change the identity,
    because those are exactly the inputs a later re-decision must reproduce.

    The freeze is immutable and is never persisted by this module: the caller
    owns storage, and there is no import-time write.
    """

    interaction: ResearchInteraction
    common_support: CommonSupport
    specification: FeasibilitySpecification
    evidence_boundary: str
    cell_support: tuple[CellSupport, ...] = ()
    parent_justification_identity: str | None = None
    t0: str = ""
    schema_version: int = EVIDENCE_FREEZE_SCHEMA_VERSION
    semantic_identity: str = ""
    evidence_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cell_support", _canonical_cells(self.cell_support))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = evidence_freeze_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise EvidenceFreezeError(
                    "presented semantic identity does not match the frozen evidence material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.evidence_identity:
            if self.evidence_identity != expected_id:
                raise EvidenceFreezeError(
                    f"presented evidence identity {self.evidence_identity!r} does not match "
                    "the frozen evidence material")
        else:
            object.__setattr__(self, "evidence_identity", expected_id)

    # --- Construction ----------------------------------------------------

    @classmethod
    def create(
        cls,
        interaction: ResearchInteraction,
        common_support: CommonSupport,
        specification: FeasibilitySpecification,
        *,
        evidence_boundary: str,
        cell_support: Iterable[CellSupport] = (),
        parent_justification_identity: str | None = None,
        t0: str = "",
    ) -> "EligibilityEvidenceFreeze":
        """
        Freeze the evidence for ONE explicitly proposed interaction.

        `evidence_boundary` is mandatory and is part of identity. `t0` is
        provenance only. There is no code path that builds a freeze without an
        evidence boundary, and none that fills a missing dataset fingerprint.
        """
        return cls(
            interaction=interaction,
            common_support=common_support,
            specification=specification,
            evidence_boundary=evidence_boundary,
            cell_support=tuple(cell_support),
            parent_justification_identity=parent_justification_identity,
            t0=t0,
        )

    # --- Views ----------------------------------------------------------

    @property
    def depth(self) -> int:
        return self.interaction.depth

    @property
    def interaction_identity(self) -> str:
        return self.interaction.interaction_identity

    @property
    def dataset_fingerprint(self) -> DatasetFingerprint | None:
        return self.common_support.dataset_fingerprint

    @property
    def is_complete(self) -> bool:
        """A freeze usable for a decision: boundary + fingerprint identity.

        Joint SUPPORT sufficiency is judged separately by the gate's support
        stage, so that "no joint observations" is reported as
        COMMON_SUPPORT_UNPROVEN rather than as a generic incomplete freeze.

        Incompleteness is never silently repaired. An incomplete freeze is
        reported as EVIDENCE_FREEZE_UNAVAILABLE by the gate, i.e. BLOCKED.
        """
        return (
            self.evidence_boundary.strip() != ""
            and self.common_support.dataset_fingerprint is not None
        )

    @property
    def under_supported_cells(self) -> tuple[CellSupport, ...]:
        """Declared cells that fail their own declared requirement."""
        return tuple(cell for cell in self.cell_support if not cell.meets_requirement)

    # --- Identity material ----------------------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen evidence material. `t0` is deliberately ABSENT: a decision
        timestamp is provenance, not scientific identity.
        """
        return {
            "kind": "eligibility_evidence_freeze",
            "schema_version": self.schema_version,
            "interaction_identity": self.interaction.interaction_identity,
            "common_support": self.common_support.semantic_material(),
            "cell_support": [cell.semantic_material() for cell in self.cell_support],
            "specification_identity": self.specification.feasibility_identity,
            "evidence_boundary": self.evidence_boundary,
            "parent_justification_identity": self.parent_justification_identity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            # The full declared requirements travel with the freeze so a reload
            # restores the exact specification rather than a placeholder.
            "specification": self.specification.to_dict(),
            "t0": self.t0,
            "semantic_identity": self.semantic_identity,
            "evidence_identity": self.evidence_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EligibilityEvidenceFreeze":
        """Strict deserialisation. Malformed or missing material fails closed."""
        if not isinstance(data, Mapping):
            raise EvidenceFreezeError(
                f"persisted evidence freeze must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "interaction_identity", "common_support",
            "cell_support", "specification_identity", "specification",
            "evidence_boundary", "parent_justification_identity", "t0",
            "semantic_identity", "evidence_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EvidenceFreezeError(
                f"persisted evidence freeze fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "eligibility_evidence_freeze":
            raise EvidenceFreezeError(
                "persisted evidence freeze kind must be 'eligibility_evidence_freeze', "
                f"got {data['kind']!r}")
        if not is_feasibility_identity(data["specification_identity"]):
            raise EvidenceFreezeError(
                "persisted specification_identity is not a governed feasibility identity")
        cells = data["cell_support"]
        if not isinstance(cells, list):
            raise EvidenceFreezeError("persisted evidence freeze 'cell_support' must be a list")
        common = data["common_support"]
        if not isinstance(common, Mapping):
            raise EvidenceFreezeError("persisted common_support must be a mapping")
        specification = data["specification"]
        if not isinstance(specification, Mapping):
            raise EvidenceFreezeError("persisted specification must be a mapping")
        support = CommonSupport.from_dict(common)
        restored_spec = FeasibilitySpecification.from_dict(specification)
        if restored_spec.feasibility_identity != data["specification_identity"]:
            raise EvidenceFreezeError(
                "persisted specification does not match the frozen specification identity")
        interaction = _rebuild_interaction(
            data["interaction_identity"], support.dimensions)
        return cls(
            interaction=interaction,
            common_support=support,
            specification=restored_spec,
            evidence_boundary=data["evidence_boundary"],
            cell_support=tuple(CellSupport.from_dict(row) for row in cells),
            parent_justification_identity=data["parent_justification_identity"],
            t0=data["t0"],
            schema_version=data["schema_version"],
        )

    # --- Validation -----------------------------------------------------

    def _validate(self) -> "EligibilityEvidenceFreeze":
        if self.schema_version != EVIDENCE_FREEZE_SCHEMA_VERSION:
            raise EvidenceFreezeError(
                f"evidence freeze schema_version must be {EVIDENCE_FREEZE_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not isinstance(self.interaction, ResearchInteraction):
            raise EvidenceFreezeError(
                f"interaction must be a ResearchInteraction, got "
                f"{type(self.interaction).__name__}")
        if not isinstance(self.common_support, CommonSupport):
            raise EvidenceFreezeError(
                f"common_support must be a CommonSupport, got "
                f"{type(self.common_support).__name__}")
        if not isinstance(self.specification, FeasibilitySpecification):
            raise EvidenceFreezeError(
                f"specification must be a FeasibilitySpecification, got "
                f"{type(self.specification).__name__}")
        object.__setattr__(
            self, "evidence_boundary",
            _require_text(self.evidence_boundary, "evidence_boundary"))
        if self.t0:
            object.__setattr__(self, "t0", _require_text(self.t0, "t0"))
        if self.parent_justification_identity is not None:
            object.__setattr__(
                self, "parent_justification_identity",
                _require_text(
                    self.parent_justification_identity, "parent_justification_identity"))
        # The frozen support must describe exactly the frozen interaction.
        support_identities = tuple(
            sorted(d.dimension_identity for d in self.common_support.dimensions))
        if support_identities != tuple(sorted(self.interaction.dimension_identities)):
            raise EvidenceFreezeError(
                "frozen common support must cover exactly the proposed interaction's "
                "dimensions")
        _encode(self.semantic_material(), "evidence freeze semantic material")
        return self


# --- Reuse of the existing lineage accounting -------------------------------


def marginal_coverage_from_lineage(
    dimensions: Sequence[GovernedDimension],
    lineage: Mapping[str, Any] | None,
) -> dict[str, int]:
    """
    Read the repository's EXISTING per-field lineage counts as CONTEXT.

    `lineage` is the mapping produced by
    `research_engine.evidence.base.lineage_coverage(records, keys)`. This helper
    only re-reads that accounting for each governed field path; it opens no
    dataset, counts nothing a second time and repairs nothing.

    The result is marginal coverage. It is NEVER sufficient evidence of common
    support and is never used by the gate to satisfy a requirement. Fail closed:
    an absent lineage, an absent key entry or a non-integer count yields 0.
    """
    counts: dict[str, int] = {}
    for dimension in dimensions:
        field_path = dimension.authority.field_path
        entry = ((lineage or {}).get("key_coverage") or {}).get(field_path)
        non_empty = entry.get("non_empty") if isinstance(entry, Mapping) else None
        if isinstance(non_empty, bool) or not isinstance(non_empty, int) or non_empty < 0:
            counts[dimension.dimension_key] = 0
        else:
            counts[dimension.dimension_key] = int(non_empty)
    return dict(sorted(counts.items()))


def common_support_from_lineage(
    dimensions: Sequence[GovernedDimension],
    lineage: Mapping[str, Any] | None,
    *,
    joint_population_ref: str,
    joint_usable_observations: int,
    dataset_fingerprint: DatasetFingerprint | None = None,
    join_contract: JoinContract | None = None,
    joinability_proven: bool = False,
    chronology_reference: str = "",
) -> CommonSupport:
    """
    Build common support from the caller's JOINT population, enriched with the
    repository's existing marginal lineage accounting.

    The joint count is supplied by the caller from an authoritative jointly
    eligible population. The `lineage` argument contributes CONTEXT ONLY: it is
    recorded as `marginal_observations` and can never raise the joint count.

    A caller that has only marginal counts has no common support and must not
    call this at all -- there is no fallback that derives an intersection.
    """
    return CommonSupport.from_joint_population(
        joint_population_ref=joint_population_ref,
        joint_usable_observations=joint_usable_observations,
        dimensions=dimensions,
        dataset_fingerprint=dataset_fingerprint,
        marginal_observations=marginal_coverage_from_lineage(dimensions, lineage),
        join_contract=join_contract,
        joinability_proven=joinability_proven,
        chronology_reference=chronology_reference,
    )


def fingerprint_for_records(
    records: Sequence[Mapping[str, Any]],
    *,
    dataset_id: str = "eligibility_population",
    population: str = "",
    **kwargs: Any,
) -> DatasetFingerprint:
    """
    Reuse the repository's existing population fingerprint for the frozen set.

    Delegates to `research_engine.lifecycle.dataset_fingerprint
    .build_dataset_fingerprint` rather than hashing a weaker duplicate here.
    """
    return build_dataset_fingerprint(
        [dict(record) for record in records],
        dataset_id=dataset_id,
        population=population,
        **kwargs,
    )


__all__ = [
    "CellSupport",
    "CommonSupport",
    "EVIDENCE_FREEZE_ID_PREFIX",
    "EVIDENCE_FREEZE_SCHEMA_VERSION",
    "EligibilityEvidenceFreeze",
    "EvidenceFreezeError",
    "common_support_from_lineage",
    "evidence_freeze_identity_for",
    "fingerprint_for_records",
    "is_evidence_freeze_identity",
    "marginal_coverage_from_lineage",
]
