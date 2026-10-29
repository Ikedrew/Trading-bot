"""
Interaction Feasibility Specification v1 -- explicit support requirements.

Stage 3 / Wave 2. Wave 1 made an interaction REPRESENTABLE. This module fixes
what an interaction would need in order to be *eligible to investigate*, and
nothing else. It is a small, immutable, deterministically identifiable
specification of support requirements -- NOT a statistical framework, NOT a
default table, and NOT a source of scientific thresholds.

NO INVENTED THRESHOLDS (HARD INVARIANT)
=======================================
Every scientific threshold is `None` by default, and `None` means exactly one
thing: "this specification does not require it". There is no implicit minimum
sample size, no universal per-slice minimum, no default coverage fraction and
no default chronology rule anywhere in this module. A caller that needs a
threshold must supply one, or inherit one from an existing governed research
contract, and must say which specification key it is.

The same rule governs identity: `semantic_material()` contains the declared
thresholds, the flags and the specification key, and nothing else. Two
equivalent specifications therefore share one `feasibility_identity`; two
specifications that differ in ANY scientific threshold do not. Observed counts
never appear here -- they belong to the evidence snapshot, never to the
specification's identity.

MACHINE-READABLE REASONS
========================
`EligibilityReasonCode` is the small, closed taxonomy the gate reports with, so
later agenda / Data Hub machinery can read WHY an interaction was not permitted
without parsing prose. The four outcome states live in
`progressive_depth_gate.EligibilityState`; this module only names the reasons.

NO PRODUCTION AUTHORITY
-----------------------
This module holds no runner, no orchestrator, no governance gate, no candidate
machinery and no production configuration. It performs no I/O and writes nothing
at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)

# --- Versions (clean reset: starts at 1, never > 1) -------------------------

FEASIBILITY_SCHEMA_VERSION: int = 1

# --- Namespace --------------------------------------------------------------
#
# `FSP-` is disjoint from the reserved `GEN-` research namespace, from `DIM-`,
# `IXN-`, `SLC-`, `EVD-`, `EXP-`, `DEC-` and from every canonical programme
# prefix. A feasibility identity is never a research question.

FEASIBILITY_ID_PREFIX = "FSP-"
FEASIBILITY_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_FSP_ID_RE = re.compile(
    rf"^{re.escape(FEASIBILITY_ID_PREFIX)}[0-9A-F]{{{FEASIBILITY_ID_DIGEST_CHARS}}}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CHRONOLOGY_VALUES = ("CURRENT", "MIXED", "HISTORICAL")


class FeasibilityValidationError(RuntimeError):
    """Feasibility material is invalid, incomplete or not canonically encodable."""


# --- Machine-readable reason taxonomy ---------------------------------------


class EligibilityReasonCode(str, Enum):
    """
    Closed vocabulary for "why this interaction was not permitted".

    The value is the machine-readable code; the accompanying detail string is
    provenance for humans and is never parsed. Codes are deliberately few and
    stable, and they are never invented at runtime.
    """

    # --- BLOCKED-class: structurally invalid / unavailable evidence path ---
    DIMENSION_NOT_ADMITTED = "DIMENSION_NOT_ADMITTED"
    AUTHORITY_MISSING = "AUTHORITY_MISSING"
    CURRENTNESS_FAILED = "CURRENTNESS_FAILED"
    LINEAGE_INVALID = "LINEAGE_INVALID"
    JOINABILITY_UNPROVEN = "JOINABILITY_UNPROVEN"
    COMMON_SUPPORT_UNPROVEN = "COMMON_SUPPORT_UNPROVEN"
    CELL_SUPPORT_UNPROVEN = "CELL_SUPPORT_UNPROVEN"
    EVIDENCE_FREEZE_UNAVAILABLE = "EVIDENCE_FREEZE_UNAVAILABLE"
    FEASIBILITY_REQUIREMENT_UNDECLARED = "FEASIBILITY_REQUIREMENT_UNDECLARED"

    # --- WAITING_DATA-class: valid path, insufficient usable support ------
    COMMON_SUPPORT_INSUFFICIENT = "COMMON_SUPPORT_INSUFFICIENT"
    CELL_SUPPORT_INSUFFICIENT = "CELL_SUPPORT_INSUFFICIENT"

    # --- REFUSE-class: evidence sufficient, depth not justified -----------
    PARENT_REQUIRED = "PARENT_REQUIRED"
    PARENT_NOT_ELIGIBLE = "PARENT_NOT_ELIGIBLE"
    INVALID_DEPTH_STEP = "INVALID_DEPTH_STEP"
    EXPANSION_JUSTIFICATION_REQUIRED = "EXPANSION_JUSTIFICATION_REQUIRED"
    EXPANSION_NOT_SUPPORTED = "EXPANSION_NOT_SUPPORTED"


#: Which outcome state a reason code forces. This mapping is the mechanical
#: guarantee that BLOCKED, WAITING_DATA and REFUSE are never conflated, and
#: that a REFUSE-class reason is never reported as WAITING_DATA (or vice versa).
REASON_CLASS: Mapping[str, str] = {
    EligibilityReasonCode.DIMENSION_NOT_ADMITTED.value: "BLOCKED",
    EligibilityReasonCode.AUTHORITY_MISSING.value: "BLOCKED",
    EligibilityReasonCode.CURRENTNESS_FAILED.value: "BLOCKED",
    EligibilityReasonCode.LINEAGE_INVALID.value: "BLOCKED",
    EligibilityReasonCode.JOINABILITY_UNPROVEN.value: "BLOCKED",
    EligibilityReasonCode.COMMON_SUPPORT_UNPROVEN.value: "BLOCKED",
    EligibilityReasonCode.CELL_SUPPORT_UNPROVEN.value: "BLOCKED",
    EligibilityReasonCode.EVIDENCE_FREEZE_UNAVAILABLE.value: "BLOCKED",
    EligibilityReasonCode.FEASIBILITY_REQUIREMENT_UNDECLARED.value: "BLOCKED",
    EligibilityReasonCode.COMMON_SUPPORT_INSUFFICIENT.value: "WAITING_DATA",
    EligibilityReasonCode.CELL_SUPPORT_INSUFFICIENT.value: "WAITING_DATA",
    EligibilityReasonCode.PARENT_REQUIRED.value: "REFUSE",
    EligibilityReasonCode.PARENT_NOT_ELIGIBLE.value: "REFUSE",
    EligibilityReasonCode.INVALID_DEPTH_STEP.value: "REFUSE",
    EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED.value: "REFUSE",
    EligibilityReasonCode.EXPANSION_NOT_SUPPORTED.value: "REFUSE",
}


def reason_class(code: EligibilityReasonCode | str) -> str:
    """The outcome state a reason code belongs to (fail closed on unknown codes)."""
    value = code.value if isinstance(code, EligibilityReasonCode) else code
    if value not in REASON_CLASS:
        raise FeasibilityValidationError(f"unknown eligibility reason code: {value!r}")
    return REASON_CLASS[value]


# --- Canonical encoding helpers (reusing the Wave 0 encoder) ----------------


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise FeasibilityValidationError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode(material, "identity material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeasibilityValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise FeasibilityValidationError(f"{label} must not have surrounding whitespace")
    return value


def _optional_count(value: Any, label: str) -> int | None:
    """A declared minimum. `None` means "not required" -- never a default."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FeasibilityValidationError(f"{label} must be an integer or None, got {value!r}")
    if value < 1:
        raise FeasibilityValidationError(
            f"{label} must be at least 1 when declared, got {value!r}")
    return int(value)


def _optional_fraction(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeasibilityValidationError(f"{label} must be a number or None, got {value!r}")
    fraction = float(value)
    if not 0.0 < fraction <= 1.0:
        raise FeasibilityValidationError(
            f"{label} must be within (0.0, 1.0] when declared, got {value!r}")
    return fraction


def feasibility_identity_for(semantic_identity: str) -> str:
    """Derive the governed feasibility identity deterministically from a digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise FeasibilityValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{FEASIBILITY_ID_PREFIX}{semantic_identity[:FEASIBILITY_ID_DIGEST_CHARS].upper()}"


def is_feasibility_identity(value: Any) -> bool:
    """True only for IDs inside the feasibility specification namespace."""
    return isinstance(value, str) and bool(_FSP_ID_RE.match(value))


# --- Feasibility specification ----------------------------------------------


@dataclass(frozen=True)
class FeasibilitySpecification:
    """
    An immutable, deterministically identifiable set of SUPPORT requirements.

    The specification says what must be true; it never observes anything. It
    therefore contains no counts, no populations, no timestamps and no
    interaction identity: one specification legitimately governs many
    interactions, and its identity must not move when evidence changes.

    Declared requirements (all optional, none defaulted):
        min_common_observations   minimum jointly eligible/usable observations
        min_slice_observations    minimum usable observations per required cell
        required_common_coverage  minimum usable fraction of the checked set
        require_all_declared_cells every declared cell must be supported
        require_current_evidence  the frozen authority must be current-eligible
        chronology_requirement    governed epoch/chronology rule, or None
    """

    spec_key: str
    min_common_observations: int | None = None
    min_slice_observations: int | None = None
    required_common_coverage: float | None = None
    require_all_declared_cells: bool = False
    require_current_evidence: bool = False
    chronology_requirement: str | None = None
    label: str = ""                       # presentation only; NOT identity
    schema_version: int = FEASIBILITY_SCHEMA_VERSION
    semantic_identity: str = ""
    feasibility_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "min_common_observations",
            _optional_count(self.min_common_observations, "min_common_observations"))
        object.__setattr__(
            self, "min_slice_observations",
            _optional_count(self.min_slice_observations, "min_slice_observations"))
        object.__setattr__(
            self, "required_common_coverage",
            _optional_fraction(self.required_common_coverage, "required_common_coverage"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = feasibility_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise FeasibilityValidationError(
                    "presented semantic identity does not match the declared requirements")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.feasibility_identity:
            if self.feasibility_identity != expected_id:
                raise FeasibilityValidationError(
                    f"presented feasibility identity {self.feasibility_identity!r} does not "
                    "match the declared requirements")
        else:
            object.__setattr__(self, "feasibility_identity", expected_id)

    # --- Construction ----------------------------------------------------

    @classmethod
    def create(
        cls,
        spec_key: str,
        *,
        min_common_observations: int | None = None,
        min_slice_observations: int | None = None,
        required_common_coverage: float | None = None,
        require_all_declared_cells: bool = False,
        require_current_evidence: bool = False,
        chronology_requirement: str | None = None,
        label: str = "",
    ) -> "FeasibilitySpecification":
        """
        Build a feasibility specification. Identity is derived, never supplied.

        A caller wanting a threshold must pass it here. There is no code path in
        this module that supplies one on the caller's behalf.
        """
        return cls(
            spec_key=spec_key,
            min_common_observations=min_common_observations,
            min_slice_observations=min_slice_observations,
            required_common_coverage=required_common_coverage,
            require_all_declared_cells=require_all_declared_cells,
            require_current_evidence=require_current_evidence,
            chronology_requirement=chronology_requirement,
            label=label,
        )

    # --- Views ----------------------------------------------------------

    @property
    def declares_common_minimum(self) -> bool:
        return self.min_common_observations is not None

    @property
    def declares_slice_minimum(self) -> bool:
        return self.min_slice_observations is not None

    @property
    def declares_no_thresholds(self) -> bool:
        """True when this specification requires no numeric support at all."""
        return (
            self.min_common_observations is None
            and self.min_slice_observations is None
            and self.required_common_coverage is None
            and not self.require_all_declared_cells
        )

    # --- Identity material ----------------------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen semantic material defining the specification identity.

        `label` is absent (presentation is not semantics) and no observed count
        is present (observed counts belong to the evidence snapshot).
        """
        return {
            "kind": "feasibility_specification",
            "schema_version": self.schema_version,
            "spec_key": self.spec_key,
            "min_common_observations": self.min_common_observations,
            "min_slice_observations": self.min_slice_observations,
            "required_common_coverage": self.required_common_coverage,
            "require_all_declared_cells": bool(self.require_all_declared_cells),
            "require_current_evidence": bool(self.require_current_evidence),
            "chronology_requirement": self.chronology_requirement,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "label": self.label,
            "semantic_identity": self.semantic_identity,
            "feasibility_identity": self.feasibility_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeasibilitySpecification":
        """Strict deserialisation. Unknown/missing/conflicting content fails closed."""
        if not isinstance(data, Mapping):
            raise FeasibilityValidationError(
                f"persisted specification must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "spec_key", "min_common_observations",
            "min_slice_observations", "required_common_coverage",
            "require_all_declared_cells", "require_current_evidence",
            "chronology_requirement", "label", "semantic_identity",
            "feasibility_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise FeasibilityValidationError(
                f"persisted specification fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "feasibility_specification":
            raise FeasibilityValidationError(
                "persisted specification kind must be 'feasibility_specification', "
                f"got {data['kind']!r}")
        return cls(
            spec_key=data["spec_key"],
            min_common_observations=data["min_common_observations"],
            min_slice_observations=data["min_slice_observations"],
            required_common_coverage=data["required_common_coverage"],
            require_all_declared_cells=data["require_all_declared_cells"],
            require_current_evidence=data["require_current_evidence"],
            chronology_requirement=data["chronology_requirement"],
            label=data["label"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            feasibility_identity=data["feasibility_identity"],
        )

    # --- Validation -----------------------------------------------------

    def _validate(self) -> "FeasibilitySpecification":
        if self.schema_version != FEASIBILITY_SCHEMA_VERSION:
            raise FeasibilityValidationError(
                f"feasibility schema_version must be {FEASIBILITY_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        key = _require_text(self.spec_key, "spec_key")
        if not _KEY_RE.match(key):
            raise FeasibilityValidationError(
                f"spec_key must be a lower_snake_case governed key, got {key!r}")
        if not isinstance(self.require_all_declared_cells, bool):
            raise FeasibilityValidationError("require_all_declared_cells must be a boolean")
        if not isinstance(self.require_current_evidence, bool):
            raise FeasibilityValidationError("require_current_evidence must be a boolean")
        if not isinstance(self.label, str):
            raise FeasibilityValidationError("label must be a string")
        if self.chronology_requirement is not None:
            chronology = _require_text(self.chronology_requirement, "chronology_requirement")
            if chronology not in _CHRONOLOGY_VALUES:
                raise FeasibilityValidationError(
                    f"chronology_requirement must be one of {_CHRONOLOGY_VALUES}, "
                    f"got {chronology!r}")
        _encode(self.semantic_material(), "feasibility semantic material")
        return self


__all__ = [
    "FEASIBILITY_ID_DIGEST_CHARS",
    "FEASIBILITY_ID_PREFIX",
    "FEASIBILITY_SCHEMA_VERSION",
    "EligibilityReasonCode",
    "FeasibilitySpecification",
    "FeasibilityValidationError",
    "REASON_CLASS",
    "feasibility_identity_for",
    "is_feasibility_identity",
    "reason_class",
]
