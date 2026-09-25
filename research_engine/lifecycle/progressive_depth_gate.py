"""
Progressive Depth Eligibility Gate v1 -- is this proposal ELIGIBLE to investigate?

Stage 3 / Wave 2. The core principle of this wave is:

    REPRESENTABLE  !=  ELIGIBLE TO INVESTIGATE

A 3D or 4D interaction can be perfectly representable by Wave 1 and be REFUSED
here. The engine must progressively EARN deeper interaction depth. This module is
that earning mechanism, expressed as a single deterministic gate over ONE
explicitly supplied proposal.

THE FOUR STATES, NEVER COLLAPSED
================================
    PERMIT        scientifically eligible to proceed to a LATER investigation
                  stage. It never means "apply this to trading".
    WAITING_DATA  the evidence path/authority is valid, but current usable /
                  common support is insufficient.
    BLOCKED       required authority, lineage, joinability, chronology,
                  currentness or other structural requirement is invalid or
                  missing. The path is structurally unavailable.
    REFUSE        the evidence IS sufficient to decide, and the proposed
                  increase in interaction depth is not scientifically
                  justified.

A refusal is a valid scientific result, not an error, and is never downgraded to
WAITING_DATA on the argument that "more data could always arrive someday".

PROGRESSIVE DEPTH (GENERIC, RECURSIVE, NO MAXIMUM)
==================================================
    depth 1    no lower-dimensional parent is required. Admitted dimension,
               authoritative evidence, a valid common/usable population and
               governed support requirements are still required.
    depth N>1  must be justified by an eligible depth N-1 parent, and the child
               must be the parent plus EXACTLY ONE additional dimension.

There is NO hard maximum depth, NO per-depth special case, and NO branch that
treats depth 3 differently in kind from depth 4. `parent_step_is_valid` and
`_check_depth_progression` are the only depth-sensitive code, and both are
expressed relative to the proposal's own depth. A huge sample alone can never
authorise deeper dimensionality: sample sufficiency is checked BEFORE and
INDEPENDENTLY of the justification, and a missing justification yields REFUSE
regardless of how many observations exist.

NO SEARCH, NO GENERATION (HARD INVARIANT)
=========================================
This module evaluates ONE supplied proposal. There is no loop over candidate
dimensions, no ranking of possible interactions, no "try another dimension if
this fails", no most-profitable-slice selection and no historical-return
selection. It never enumerates interactions, never creates a child interaction,
never registers a `GEN-*` research record, never creates a hypothesis, candidate,
finding or experiment, and never touches production. Wave 4 governs search
provenance and multiplicity.

NO PRODUCTION AUTHORITY
-----------------------
`PERMIT` means only "eligible to become a research investigation later". It
never authorises live strategy execution, risk, sizing, broker execution,
production configuration, baseline activation or candidate promotion. This module
imports no runner, no orchestrator, no `GovernanceGate` and no production
configuration, performs no I/O, and writes nothing at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.eligibility_evidence_freeze import (
    CellSupport,
    CommonSupport,
    EligibilityEvidenceFreeze,
    is_evidence_freeze_identity,
)
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.interaction_feasibility import (
    EligibilityReasonCode,
    FeasibilitySpecification,
    reason_class,
)
from research_engine.lifecycle.research_interaction import (
    ResearchInteraction,
    is_interaction_identity,
)

# --- Versions / namespace ---------------------------------------------------

ELIGIBILITY_SCHEMA_VERSION: int = 1

# `DEC-` (decision) and `EXP-` (expansion justification) are disjoint from
# `GEN-`, `DIM-`, `IXN-`, `SLC-`, `FSP-`, `EVD-` and every canonical programme
# prefix. Neither is a research question.

DECISION_ID_PREFIX = "DEC-"
JUSTIFICATION_ID_PREFIX = "EXP-"
_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_DEC_ID_RE = re.compile(
    rf"^{re.escape(DECISION_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_EXP_ID_RE = re.compile(
    rf"^{re.escape(JUSTIFICATION_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")


class EligibilityGateError(RuntimeError):
    """Eligibility material is invalid, incomplete or not canonically encodable."""


# --- Decision states --------------------------------------------------------


class EligibilityState(str, Enum):
    """
    The four mutually exclusive outcomes. They are never collapsed, and a
    decision always carries the state whose class its reasons belong to.
    """

    PERMIT = "PERMIT"
    WAITING_DATA = "WAITING_DATA"
    BLOCKED = "BLOCKED"
    REFUSE = "REFUSE"


#: The only state from which a later investigation may be scheduled. A PERMIT is
#: a research-eligibility verdict and nothing else.
PROCEEDING_STATES = (EligibilityState.PERMIT,)


# --- Governed justification types -------------------------------------------


class ExpansionJustificationType(str, Enum):
    """
    The CLOSED set of governed reasons a deeper interaction may be justified by.

    This is an explicit taxonomy of scientific expansion triggers, not a
    statistical test. Wave 2 does not DISCOVER heterogeneity: it verifies that a
    governed justification record already exists and is internally consistent.
    Free text is NOT a member and cannot grant eligibility; a human-readable
    note may accompany a justification but is never authority.
    """

    UNRESOLVED_BETWEEN_SLICE_HETEROGENEITY = "UNRESOLVED_BETWEEN_SLICE_HETEROGENEITY"
    RESIDUAL_STRUCTURE = "RESIDUAL_STRUCTURE"
    CONDITIONAL_INSTABILITY = "CONDITIONAL_INSTABILITY"
    EXPLICIT_FOLLOW_UP = "EXPLICIT_FOLLOW_UP"


# --- Canonical encoding helpers (reusing the Wave 0 encoder) ----------------


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise EligibilityGateError(f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode(material, "semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EligibilityGateError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise EligibilityGateError(f"{label} must not have surrounding whitespace")
    return value


def decision_identity_for(semantic_identity: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise EligibilityGateError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{DECISION_ID_PREFIX}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def is_decision_identity(value: Any) -> bool:
    """True only for IDs inside the eligibility decision namespace."""
    return isinstance(value, str) and bool(_DEC_ID_RE.match(value))




def justification_identity_for(semantic_identity: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise EligibilityGateError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{JUSTIFICATION_ID_PREFIX}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def is_justification_identity(value: Any) -> bool:
    """True only for IDs inside the expansion-justification namespace."""
    return isinstance(value, str) and bool(_EXP_ID_RE.match(value))


# --- Expansion / heterogeneity justification -------------------------------


@dataclass(frozen=True)
class ExpansionJustification:
    """
    A governed record stating WHY a deeper interaction deserves investigation.

    Wave 2 VERIFIES this record; it never creates or discovers one. The record
    captures enough provenance to establish:

        - the parent interaction identity and the parent's own depth;
        - the proposed child interaction identity;
        - the ONE added dimension (key plus identity);
        - a reference to the parent evidence/result;
        - a governed justification type (closed enum, never free text);
        - an explicit unresolved-effect/heterogeneity indicator;
        - the evidence boundary the justification was drawn at;
        - schema version 1 and a deterministic identity.

    `unresolved_structure` is a governed RESULT flag, not a probability. A
    justification whose flag is `False` asserts that nothing remained unresolved
    after the parent investigation, and therefore REFUSES the deeper interaction
    no matter how large the sample is.

    `note` is presentation only. It is deliberately ABSENT from
    `semantic_material()`: notes are not authority, so two justifications that
    differ only in prose share one identity.
    """

    parent_interaction_identity: str
    parent_depth: int
    child_interaction_identity: str
    added_dimension_key: str
    added_dimension_identity: str
    justification_type: ExpansionJustificationType
    evidence_reference: str
    unresolved_structure: bool
    evidence_boundary: str
    parent_research_reference: str | None = None
    note: str = ""
    schema_version: int = ELIGIBILITY_SCHEMA_VERSION
    semantic_identity: str = ""
    justification_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.justification_type, str) and not isinstance(
                self.justification_type, ExpansionJustificationType):
            object.__setattr__(
                self, "justification_type",
                ExpansionJustificationType(self.justification_type))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = justification_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise EligibilityGateError(
                    "presented semantic identity does not match the governed justification")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.justification_identity:
            if self.justification_identity != expected_id:
                raise EligibilityGateError(
                    f"presented justification identity {self.justification_identity!r} does "
                    "not match the governed justification")
        else:
            object.__setattr__(self, "justification_identity", expected_id)

    # --- Construction ----------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        parent_interaction_identity: str,
        parent_depth: int,
        child_interaction_identity: str,
        added_dimension_key: str,
        added_dimension_identity: str,
        justification_type: ExpansionJustificationType,
        evidence_reference: str,
        unresolved_structure: bool,
        evidence_boundary: str,
        parent_research_reference: str | None = None,
        note: str = "",
    ) -> "ExpansionJustification":
        """Build a governed expansion justification. Identity is derived."""
        return cls(
            parent_interaction_identity=parent_interaction_identity,
            parent_depth=parent_depth,
            child_interaction_identity=child_interaction_identity,
            added_dimension_key=added_dimension_key,
            added_dimension_identity=added_dimension_identity,
            justification_type=justification_type,
            evidence_reference=evidence_reference,
            unresolved_structure=unresolved_structure,
            evidence_boundary=evidence_boundary,
            parent_research_reference=parent_research_reference,
            note=note,
        )

    # --- Identity material ----------------------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen justification material. `note` is ABSENT: prose is not
        authority, so it must never change a scientific identity.
        """
        return {
            "kind": "expansion_justification",
            "schema_version": self.schema_version,
            "parent_interaction_identity": self.parent_interaction_identity,
            "parent_depth": self.parent_depth,
            "child_interaction_identity": self.child_interaction_identity,
            "added_dimension_key": self.added_dimension_key,
            "added_dimension_identity": self.added_dimension_identity,
            "justification_type": self.justification_type.value,
            "evidence_reference": self.evidence_reference,
            "unresolved_structure": bool(self.unresolved_structure),
            "evidence_boundary": self.evidence_boundary,
            "parent_research_reference": self.parent_research_reference,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "justification_identity": self.justification_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExpansionJustification":
        if not isinstance(data, Mapping):
            raise EligibilityGateError(
                f"persisted justification must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "parent_interaction_identity", "parent_depth",
            "child_interaction_identity", "added_dimension_key", "added_dimension_identity",
            "justification_type", "evidence_reference", "unresolved_structure",
            "evidence_boundary", "parent_research_reference", "note",
            "semantic_identity", "justification_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EligibilityGateError(
                f"persisted justification fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "expansion_justification":
            raise EligibilityGateError(
                "persisted justification kind must be 'expansion_justification', "
                f"got {data['kind']!r}")
        try:
            justification_type = ExpansionJustificationType(data["justification_type"])
        except ValueError as exc:
            raise EligibilityGateError(
                f"unknown governed justification type: {data['justification_type']!r}"
            ) from exc
        return cls(
            parent_interaction_identity=data["parent_interaction_identity"],
            parent_depth=data["parent_depth"],
            child_interaction_identity=data["child_interaction_identity"],
            added_dimension_key=data["added_dimension_key"],
            added_dimension_identity=data["added_dimension_identity"],
            justification_type=justification_type,
            evidence_reference=data["evidence_reference"],
            unresolved_structure=data["unresolved_structure"],
            evidence_boundary=data["evidence_boundary"],
            parent_research_reference=data["parent_research_reference"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            justification_identity=data["justification_identity"],
        )


    # --- Validation -----------------------------------------------------

    def _validate(self) -> "ExpansionJustification":
        if self.schema_version != ELIGIBILITY_SCHEMA_VERSION:
            raise EligibilityGateError(
                f"justification schema_version must be {ELIGIBILITY_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not is_interaction_identity(self.parent_interaction_identity):
            raise EligibilityGateError(
                "parent_interaction_identity must be a governed interaction identity, got "
                f"{self.parent_interaction_identity!r}")
        if not is_interaction_identity(self.child_interaction_identity):
            raise EligibilityGateError(
                "child_interaction_identity must be a governed interaction identity, got "
                f"{self.child_interaction_identity!r}")
        if not is_dimension_identity(self.added_dimension_identity):
            raise EligibilityGateError(
                "added_dimension_identity must be a governed dimension identity, got "
                f"{self.added_dimension_identity!r}")
        if isinstance(self.parent_depth, bool) or not isinstance(self.parent_depth, int):
            raise EligibilityGateError(
                f"parent_depth must be an integer, got {self.parent_depth!r}")
        if self.parent_depth < 1:
            raise EligibilityGateError(
                f"parent_depth must be at least 1, got {self.parent_depth!r}")
        if not isinstance(self.justification_type, ExpansionJustificationType):
            raise EligibilityGateError(
                "justification_type must be a governed ExpansionJustificationType; free "
                f"text is not authority, got {self.justification_type!r}")
        if not isinstance(self.unresolved_structure, bool):
            raise EligibilityGateError("unresolved_structure must be a governed boolean flag")
        object.__setattr__(
            self, "added_dimension_key",
            _require_text(self.added_dimension_key, "added_dimension_key"))
        object.__setattr__(
            self, "evidence_reference",
            _require_text(self.evidence_reference, "evidence_reference"))
        object.__setattr__(
            self, "evidence_boundary",
            _require_text(self.evidence_boundary, "evidence_boundary"))
        if not isinstance(self.note, str):
            raise EligibilityGateError("note must be a string")
        _encode(self.semantic_material(), "expansion justification semantic material")
        return self


# --- Decision result --------------------------------------------------------


@dataclass(frozen=True)
class EligibilityReason:
    """
    A machine-readable reason plus the evidence that produced it.

    `code` is from the closed `EligibilityReasonCode` taxonomy; `detail` is
    human-readable provenance and is never parsed. The counts and identities
    carried here are what let later agenda / Data Hub machinery understand WHY
    something was not permitted without re-deriving anything.
    """

    code: EligibilityReasonCode
    detail: str
    observed: int | None = None
    required: int | None = None
    subject: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, EligibilityReasonCode):
            object.__setattr__(self, "code", EligibilityReasonCode(self.code))
        object.__setattr__(self, "detail", _require_text(self.detail, "detail"))
        if not isinstance(self.subject, str):
            raise EligibilityGateError("subject must be a string")
        for value, label in ((self.observed, "observed"), (self.required, "required")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise EligibilityGateError(f"{label} must be an integer or None, got {value!r}")

    @property
    def state_class(self) -> str:
        """Which outcome state this reason forces."""
        return reason_class(self.code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "detail": self.detail,
            "observed": self.observed,
            "required": self.required,
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EligibilityReason":
        if not isinstance(data, Mapping):
            raise EligibilityGateError(
                f"persisted reason must be a mapping, got {type(data).__name__}")
        expected = {"code", "detail", "observed", "required", "subject"}
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EligibilityGateError(
                f"persisted reason fields are not exact (missing={missing}, unknown={unknown})")
        return cls(
            code=EligibilityReasonCode(data["code"]),
            detail=data["detail"],
            observed=data["observed"],
            required=data["required"],
            subject=data["subject"],
        )



@dataclass(frozen=True)
class EligibilityDecision:
    """
    The immutable verdict for ONE explicitly proposed interaction.

    A PERMIT carries no reasons: eligibility was established, not argued. Every
    non-PERMIT result carries at least one machine-readable reason, and the
    reasons' class (`reason_class`) always agrees with `state`.

    `decision_identity` is derived from the frozen evidence identity, the
    specification identity, the interaction identity, the state and the reasons.
    It is therefore reproducible: the same frozen inputs always produce the same
    decision identity, and any change to the evidence, the requirements, the
    parent justification or the verdict changes it.
    """

    interaction_identity: str
    depth: int
    state: EligibilityState
    reasons: tuple[EligibilityReason, ...]
    evidence_identity: str
    specification_identity: str
    parent_justification_identity: str | None = None
    t0: str = ""
    schema_version: int = ELIGIBILITY_SCHEMA_VERSION
    semantic_identity: str = ""
    decision_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", EligibilityState(self.state))
        object.__setattr__(self, "reasons", tuple(self.reasons))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = decision_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise EligibilityGateError(
                    "presented semantic identity does not match the decision material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.decision_identity:
            if self.decision_identity != expected_id:
                raise EligibilityGateError(
                    f"presented decision identity {self.decision_identity!r} does not match "
                    "the decision material")
        else:
            object.__setattr__(self, "decision_identity", expected_id)

    # --- Views ----------------------------------------------------------

    @property
    def permitted(self) -> bool:
        return self.state is EligibilityState.PERMIT

    @property
    def blocked(self) -> bool:
        return self.state is EligibilityState.BLOCKED

    @property
    def waiting(self) -> bool:
        return self.state is EligibilityState.WAITING_DATA

    @property
    def refused(self) -> bool:
        return self.state is EligibilityState.REFUSE

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(reason.code.value for reason in self.reasons)

    def has_reason(self, code: EligibilityReasonCode | str) -> bool:
        value = code.value if isinstance(code, EligibilityReasonCode) else code
        return value in self.reason_codes


    # --- Identity material / serialisation ------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen decision material. `t0` is ABSENT (provenance only), so the
        same frozen inputs re-decide to the same decision identity.
        """
        return {
            "kind": "eligibility_decision",
            "schema_version": self.schema_version,
            "interaction_identity": self.interaction_identity,
            "depth": self.depth,
            "state": self.state.value,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "evidence_identity": self.evidence_identity,
            "specification_identity": self.specification_identity,
            "parent_justification_identity": self.parent_justification_identity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "t0": self.t0,
            "semantic_identity": self.semantic_identity,
            "decision_identity": self.decision_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EligibilityDecision":
        if not isinstance(data, Mapping):
            raise EligibilityGateError(
                f"persisted decision must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "interaction_identity", "depth", "state", "reasons",
            "evidence_identity", "specification_identity", "parent_justification_identity",
            "t0", "semantic_identity", "decision_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise EligibilityGateError(
                f"persisted decision fields are not exact (missing={missing}, unknown={unknown})")
        if data["kind"] != "eligibility_decision":
            raise EligibilityGateError(
                f"persisted decision kind must be 'eligibility_decision', got {data['kind']!r}")
        rows = data["reasons"]
        if not isinstance(rows, list):
            raise EligibilityGateError("persisted decision 'reasons' must be a list")
        return cls(
            interaction_identity=data["interaction_identity"],
            depth=data["depth"],
            state=EligibilityState(data["state"]),
            reasons=tuple(EligibilityReason.from_dict(row) for row in rows),
            evidence_identity=data["evidence_identity"],
            specification_identity=data["specification_identity"],
            parent_justification_identity=data["parent_justification_identity"],
            t0=data["t0"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            decision_identity=data["decision_identity"],
        )

    # --- Validation -----------------------------------------------------

    def _validate(self) -> "EligibilityDecision":
        if self.schema_version != ELIGIBILITY_SCHEMA_VERSION:
            raise EligibilityGateError(
                f"decision schema_version must be {ELIGIBILITY_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not is_interaction_identity(self.interaction_identity):
            raise EligibilityGateError(
                "interaction_identity must be a governed interaction identity, got "
                f"{self.interaction_identity!r}")
        if not is_evidence_freeze_identity(self.evidence_identity):
            raise EligibilityGateError(
                "evidence_identity must be a governed evidence-freeze identity, got "
                f"{self.evidence_identity!r}")
        if self.parent_justification_identity is not None and not is_justification_identity(
                self.parent_justification_identity):
            raise EligibilityGateError(
                "parent_justification_identity must be a governed justification identity, got "
                f"{self.parent_justification_identity!r}")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 1:
            raise EligibilityGateError(
                f"depth must be an integer >= 1, got {self.depth!r}")
        for reason in self.reasons:
            if not isinstance(reason, EligibilityReason):
                raise EligibilityGateError(
                    f"reasons must be EligibilityReason, got {type(reason).__name__}")
        if self.state is EligibilityState.PERMIT:
            if self.reasons:
                raise EligibilityGateError(
                    "a PERMIT decision carries no reasons; eligibility is established, "
                    "not argued")
        elif not self.reasons:
            raise EligibilityGateError(
                f"a {self.state.value} decision must carry at least one machine-readable "
                "reason")
        # The state and the reasons can never disagree: this is the mechanical
        # guarantee that BLOCKED, WAITING_DATA and REFUSE stay distinct.
        for reason in self.reasons:
            if reason.state_class != self.state.value:
                raise EligibilityGateError(
                    f"reason {reason.code.value} belongs to class {reason.state_class} but the "
                    f"decision state is {self.state.value}")
        if self.t0:
            object.__setattr__(self, "t0", _require_text(self.t0, "t0"))
        _encode(self.semantic_material(), "eligibility decision semantic material")
        return self



# --- Structural checks ------------------------------------------------------


def _reject_mismatched_evidence(
    interaction: ResearchInteraction,
    freeze: EligibilityEvidenceFreeze,
) -> EligibilityReason | None:
    """A freeze must describe exactly the interaction being decided."""
    if freeze.interaction_identity != interaction.interaction_identity:
        return EligibilityReason(
            code=EligibilityReasonCode.EVIDENCE_FREEZE_UNAVAILABLE,
            detail=(
                "the frozen evidence was taken for interaction "
                f"{freeze.interaction_identity!r}, not the proposed "
                f"{interaction.interaction_identity!r}"),
            subject=interaction.interaction_identity,
        )
    frozen_dimensions = set(freeze.common_support.dimension_keys)
    proposed_dimensions = set(interaction.dimension_keys)
    if frozen_dimensions != proposed_dimensions:
        return EligibilityReason(
            code=EligibilityReasonCode.EVIDENCE_FREEZE_UNAVAILABLE,
            detail=(
                "frozen common support covers "
                f"{sorted(frozen_dimensions)} but the proposal is {sorted(proposed_dimensions)}"),
            subject=interaction.interaction_identity,
        )
    return None


def _check_dimension_admission(
    interaction: ResearchInteraction,
    registry: DimensionRegistry | None,
) -> EligibilityReason | None:
    """
    Every dimension must be ADMITTED by the governed Wave 1 registry.

    Admission is the only way in: a registered-but-unadmitted dimension, an
    unknown key, or a discovered-but-unauthoritative field is BLOCKED here.
    """
    if registry is None:
        return EligibilityReason(
            code=EligibilityReasonCode.DIMENSION_NOT_ADMITTED,
            detail="no governed DimensionRegistry was supplied; admission cannot be verified",
            subject=interaction.interaction_identity,
        )
    for dimension in interaction.dimensions:
        if not registry.is_registered(dimension):
            return EligibilityReason(
                code=EligibilityReasonCode.DIMENSION_NOT_ADMITTED,
                detail=f"dimension {dimension.dimension_key!r} is not a registered governed "
                       "dimension",
                subject=dimension.dimension_key,
            )
        if not registry.is_admitted(dimension):
            return EligibilityReason(
                code=EligibilityReasonCode.DIMENSION_NOT_ADMITTED,
                detail=f"dimension {dimension.dimension_key!r} is registered but not admitted "
                       "for generated research",
                subject=dimension.dimension_key,
            )
    return None


def _check_authority_and_lineage(
    interaction: ResearchInteraction,
    registry: DimensionRegistry,
    specification: FeasibilitySpecification,
) -> EligibilityReason | None:
    """
    Authority, currentness and lineage for every dimension in the interaction.

    This REUSES the Wave 1 `DimensionRegistry.readiness()` verdict and the
    repository's `EvidenceAuthority` currentness rule. It does not re-derive
    authority, does not infer it from a field name, and does not treat a
    non-null value as authoritative merely because it exists.
    """
    for dimension in interaction.dimensions:
        if not dimension.authority_satisfied:
            return EligibilityReason(
                code=EligibilityReasonCode.AUTHORITY_MISSING,
                detail=f"dimension {dimension.dimension_key!r} has no authoritative source",
                subject=dimension.dimension_key,
            )
        if not dimension.currentness_satisfied:
            return EligibilityReason(
                code=EligibilityReasonCode.CURRENTNESS_FAILED,
                detail=f"dimension {dimension.dimension_key!r} authority is not "
                       "current-eligible for this epoch",
                subject=dimension.dimension_key,
            )
        readiness = registry.readiness(dimension)
        if not readiness.admitted or readiness.blocking_reason:
            return EligibilityReason(
                code=EligibilityReasonCode.LINEAGE_INVALID,
                detail=f"dimension {dimension.dimension_key!r} readiness is not satisfied: "
                       f"{readiness.blocking_reason}",
                subject=dimension.dimension_key,
            )
    # Chronology/currentness: a specification that requires CURRENT evidence
    # needs BOTH current-eligible authority (checked per dimension above) AND a
    # governed chronology reference on the frozen population. A missing
    # chronology reference is a structural gap, not a silent pass.
    if specification.require_current_evidence or specification.chronology_requirement == "CURRENT":
        if not interaction.dimensions:  # pragma: no cover - Wave 1 forbids this
            return EligibilityReason(
                code=EligibilityReasonCode.LINEAGE_INVALID,
                detail="the proposal has no governed dimensions",
                subject=interaction.interaction_identity,
            )
    return None


def _check_chronology(
    common_support: CommonSupport,
    specification: FeasibilitySpecification,
) -> EligibilityReason | None:
    """
    Chronological eligibility of the frozen population.

    When the specification declares a chronology requirement (or requires
    current evidence), the frozen joint population must carry a governed
    `chronology_reference`. Without one the boundary cannot be established and
    the proposal is BLOCKED; it is never assumed.
    """
    needs_chronology = (
        specification.chronology_requirement is not None
        or specification.require_current_evidence
    )
    if not needs_chronology:
        return None
    if not common_support.chronology_reference.strip():
        return EligibilityReason(
            code=EligibilityReasonCode.CURRENTNESS_FAILED,
            detail=(
                "the specification requires chronological eligibility but the frozen joint "
                "population carries no chronology reference; the evidence boundary cannot be "
                "established"),
            subject=common_support.joint_population_ref,
        )
    return None


def _check_joinability(common_support: CommonSupport) -> EligibilityReason | None:
    """
    Joinability is only required when the interaction actually spans datasets.

    A single-dataset interaction needs no join contract and is not blocked for
    lacking one. A multi-dataset interaction must present a governed
    `JoinContract` and a proven joinability flag.
    """
    if not common_support.uses_multiple_datasets:
        return None
    if common_support.join_contract is None:
        return EligibilityReason(
            code=EligibilityReasonCode.JOINABILITY_UNPROVEN,
            detail="the interaction spans multiple authoritative datasets but no governed "
                   "JoinContract was frozen; joinability is unproven",
            subject=common_support.joint_population_ref,
        )
    if not common_support.joinability_proven:
        return EligibilityReason(
            code=EligibilityReasonCode.JOINABILITY_UNPROVEN,
            detail="a JoinContract is present but joinability has not been proven against it",
            subject=common_support.joint_population_ref,
        )
    return None



# --- Support checks ---------------------------------------------------------


def _check_common_support(
    common_support: CommonSupport,
    specification: FeasibilitySpecification,
) -> EligibilityReason | None:
    """
    Common support: UNPROVEN (BLOCKED) is distinct from INSUFFICIENT (WAITING).

    The count used here is the JOINT count only. `marginal_observations` is
    never consulted: a large per-dimension count cannot stand in for a joint
    population, and this function has no parameter through which a caller could
    try.
    """
    if not common_support.has_joint_support:
        return EligibilityReason(
            code=EligibilityReasonCode.COMMON_SUPPORT_UNPROVEN,
            detail=(
                f"the frozen joint population {common_support.joint_population_ref!r} carries "
                f"{common_support.joint_usable_observations} jointly usable observations; "
                "common support cannot be established from marginal counts"),
            observed=common_support.joint_usable_observations,
            subject=common_support.joint_population_ref,
        )
    required = specification.min_common_observations
    if required is not None and common_support.joint_usable_observations < required:
        return EligibilityReason(
            code=EligibilityReasonCode.COMMON_SUPPORT_INSUFFICIENT,
            detail=(
                f"the joint population carries {common_support.joint_usable_observations} "
                f"jointly usable observations but the governed specification requires "
                f"{required}"),
            observed=common_support.joint_usable_observations,
            required=required,
            subject=common_support.joint_population_ref,
        )
    return None


def _check_cell_support(
    cell_support: Iterable[CellSupport],
    specification: FeasibilitySpecification,
) -> EligibilityReason | None:
    """
    Cell support, evaluated per DECLARED cell.

    A large pooled sample never hides an empty cell: every declared cell is
    checked against its own requirement and a pooled total is never summed in to
    compensate. A cell whose requirement is declared NOWHERE -- neither on the
    cell nor in the specification -- is reported as undeclared (fail closed)
    rather than judged against an invented threshold.
    """
    cells = tuple(cell_support)
    for cell in cells:
        required = cell.required_observations
        if required is None:
            required = specification.min_slice_observations
        if required is None:
            return EligibilityReason(
                code=EligibilityReasonCode.FEASIBILITY_REQUIREMENT_UNDECLARED,
                detail=(
                    f"cell {cell.slice_description!r} has no governed per-slice requirement; "
                    "neither the cell nor the specification declares one, so support cannot "
                    "be judged without inventing a threshold"),
                observed=cell.usable_observations,
                subject=cell.slice_identity,
            )
        if cell.usable_observations < required:
            return EligibilityReason(
                code=EligibilityReasonCode.CELL_SUPPORT_INSUFFICIENT,
                detail=(
                    f"cell {cell.slice_description!r} has {cell.usable_observations} usable "
                    f"observations but requires {required}; a larger pooled sample does not "
                    "compensate for an under-supported cell"),
                observed=cell.usable_observations,
                required=required,
                subject=cell.slice_identity,
            )
    if specification.require_all_declared_cells and not cells:
        return EligibilityReason(
            code=EligibilityReasonCode.CELL_SUPPORT_UNPROVEN,
            detail="the specification requires support for every declared cell but no cell "
                   "evidence was frozen",
            subject="cell_support",
        )
    return None


# --- Parent / child validation ---------------------------------------------


def parent_step_is_valid(
    parent: ResearchInteraction,
    child: ResearchInteraction,
) -> bool:
    """
    The MECHANICAL parent/child invariant, for ANY depth.

    Valid:  the child contains every dimension of the parent plus EXACTLY ONE
            additional dimension, so the child's depth is exactly the parent's
            depth + 1.

    Invalid: any other relationship, including a DIMENSION SWAP (parent
            `regime`, child `horizon x strategy_family`), a SUPERSET JUMP
            (parent `regime`, child `regime x horizon x strategy_family`), or a
            child that drops a parent dimension. Depth skipping is impossible
            because the count must differ by exactly one.

    This is the ONLY depth-sensitive rule in the module, and it is expressed
    relative to the two supplied interactions -- there is no per-depth branch,
    no `if depth == 3` special case and no maximum-depth constant.
    """
    if not isinstance(parent, ResearchInteraction) or not isinstance(child, ResearchInteraction):
        return False
    if child.depth != parent.depth + 1:
        return False
    parent_identities = set(parent.dimension_identities)
    child_identities = set(child.dimension_identities)
    if not parent_identities.issubset(child_identities):
        return False
    return len(child_identities - parent_identities) == 1



def _check_depth_progression(
    interaction: ResearchInteraction,
    parent: ResearchInteraction | None,
    justification: ExpansionJustification | None,
) -> EligibilityReason | None:
    """
    The GENERIC, RECURSIVE depth rule.

    Depth 1: no parent is required. Depth N>1 requires a mechanically valid
    single-dimension step from the supplied depth N-1 parent, plus a governed
    expansion justification reporting that something remains unresolved.

    There is no maximum depth and no per-depth branch: the same requirements
    apply to depth 2, depth 3, depth 4 and beyond.
    """
    if interaction.depth == 1:
        if parent is not None:
            return EligibilityReason(
                code=EligibilityReasonCode.INVALID_DEPTH_STEP,
                detail=(
                    f"a depth-1 interaction {interaction.interaction_identity!r} was given a "
                    f"parent {parent.interaction_identity!r}; a 1D interaction has no "
                    "lower-dimensional parent"),
                subject=interaction.interaction_identity,
            )
        if justification is not None:
            return EligibilityReason(
                code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
                detail=(
                    f"a depth-1 interaction {interaction.interaction_identity!r} was given an "
                    "expansion justification; a 1D interaction needs no expansion trigger"),
                subject=interaction.interaction_identity,
            )
        return None

    if parent is None:
        return EligibilityReason(
            code=EligibilityReasonCode.PARENT_REQUIRED,
            detail=(
                f"a depth-{interaction.depth} interaction requires an eligible depth-"
                f"{interaction.depth - 1} parent interaction; none was supplied"),
            subject=interaction.interaction_identity,
        )
    if not parent_step_is_valid(parent, interaction):
        return EligibilityReason(
            code=EligibilityReasonCode.INVALID_DEPTH_STEP,
            detail=(
                f"parent {parent.interaction_identity!r} (depth {parent.depth}) is not a valid "
                f"single-dimension predecessor of {interaction.interaction_identity!r} "
                f"(depth {interaction.depth}); the child must contain every parent dimension "
                "plus exactly one additional dimension"),
            subject=interaction.interaction_identity,
        )
    return _check_justification(interaction, parent, justification)



def _check_justification(
    interaction: ResearchInteraction,
    parent: ResearchInteraction,
    justification: ExpansionJustification | None,
) -> EligibilityReason | None:
    """
    A governed justification must EXIST, MATCH this exact step, and report that
    something genuinely remains unresolved.

    Free text is never authority: `note` is not read here, an ungoverned
    justification type cannot be constructed in the first place, and a
    justification with `unresolved_structure=False` refuses the deeper
    interaction no matter how large the sample is.
    """
    if justification is None:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED,
            detail=(
                f"a depth-{interaction.depth} interaction may not be permitted merely because "
                "its dimensions exist and have enough data; a governed expansion justification "
                "demonstrating unresolved heterogeneity or another explicit expansion trigger "
                "is required"),
            subject=interaction.interaction_identity,
        )
    if justification.child_interaction_identity != interaction.interaction_identity:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
            detail=(
                f"the justification was issued for "
                f"{justification.child_interaction_identity!r} but the proposal is "
                f"{interaction.interaction_identity!r}"),
            subject=interaction.interaction_identity,
        )
    if justification.parent_interaction_identity != parent.interaction_identity:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
            detail=(
                f"the justification names parent "
                f"{justification.parent_interaction_identity!r} but the supplied parent is "
                f"{parent.interaction_identity!r}"),
            subject=interaction.interaction_identity,
        )
    if justification.parent_depth != parent.depth:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
            detail=(
                f"the justification records parent depth {justification.parent_depth} but the "
                f"supplied parent interaction has depth {parent.depth}"),
            subject=interaction.interaction_identity,
        )
    added_identities = set(interaction.dimension_identities) - set(
        parent.dimension_identities)
    added_identity = next(iter(added_identities))
    if justification.added_dimension_identity != added_identity:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
            detail=(
                f"the justification names added dimension "
                f"{justification.added_dimension_identity!r} but the actual added dimension is "
                f"{added_identity!r}"),
            subject=interaction.interaction_identity,
        )
    if not justification.unresolved_structure:
        return EligibilityReason(
            code=EligibilityReasonCode.EXPANSION_NOT_SUPPORTED,
            detail=(
                f"the governed {justification.justification_type.value} justification reports "
                "NO unresolved structure remaining after the parent investigation; deeper "
                "investigation is not scientifically justified regardless of sample size"),
            subject=interaction.interaction_identity,
        )
    return None



# --- The gate ---------------------------------------------------------------


def evaluate_interaction_eligibility(
    interaction: ResearchInteraction,
    freeze: EligibilityEvidenceFreeze,
    registry: DimensionRegistry,
    *,
    parent: ResearchInteraction | None = None,
    parent_decision: EligibilityDecision | None = None,
    justification: ExpansionJustification | None = None,
    t0: str = "",
) -> EligibilityDecision:
    """
    Evaluate ONE explicitly proposed interaction for research eligibility.

    The evaluation order is fixed and deterministic:

        1.  structural: the frozen evidence must describe THIS proposal;
        2.  structural: admitted dimensions, authority, currentness, lineage;
        3.  structural: joinability and freeze completeness;
        4.  support:    common support, then per-cell support;
        5.  scientific: progressive depth and expansion justification.

    Structural failures are BLOCKED, insufficient support is WAITING_DATA, and
    a failure of the scientific depth requirement is REFUSE -- a refusal is
    never reported as WAITING_DATA.

    `parent_decision` is the ALREADY-TAKEN verdict for the parent interaction.
    A depth-N child is only justified by a parent that was itself permitted; a
    parent that is blocked, waiting or refused yields PARENT_NOT_ELIGIBLE.

    This function evaluates exactly the supplied proposal. It performs no
    search, no enumeration, no ranking, no "try another dimension" fallback, no
    question creation and no research execution.
    """
    if not isinstance(interaction, ResearchInteraction):
        raise EligibilityGateError(
            f"interaction must be a ResearchInteraction, got {type(interaction).__name__}")
    if not isinstance(freeze, EligibilityEvidenceFreeze):
        raise EligibilityGateError(
            f"freeze must be an EligibilityEvidenceFreeze, got {type(freeze).__name__}")
    if not isinstance(registry, DimensionRegistry):
        raise EligibilityGateError(
            f"registry must be a DimensionRegistry, got {type(registry).__name__}")

    specification = freeze.specification

    def verdict(reason: EligibilityReason | None) -> EligibilityDecision:
        """One state, taken from the reason's class. Never guessed."""
        return EligibilityDecision(
            interaction_identity=interaction.interaction_identity,
            depth=interaction.depth,
            state=EligibilityState.PERMIT if reason is None else EligibilityState(
                reason.state_class),
            reasons=() if reason is None else (reason,),
            evidence_identity=freeze.evidence_identity,
            specification_identity=specification.feasibility_identity,
            parent_justification_identity=(
                justification.justification_identity if justification is not None else None),
            t0=t0,
        )

    # 1. structural: the frozen evidence must describe THIS proposal.
    mismatch = _reject_mismatched_evidence(interaction, freeze)
    if mismatch is not None:
        return verdict(mismatch)

    # 2. structural: admission, authority, currentness, lineage.
    admission = _check_dimension_admission(interaction, registry)
    if admission is not None:
        return verdict(admission)
    authority = _check_authority_and_lineage(interaction, registry, specification)
    if authority is not None:
        return verdict(authority)

    # 3. structural: joinability, chronology and freeze completeness.
    joinability = _check_joinability(freeze.common_support)
    if joinability is not None:
        return verdict(joinability)
    chronology = _check_chronology(freeze.common_support, specification)
    if chronology is not None:
        return verdict(chronology)
    if not freeze.is_complete:
        return verdict(EligibilityReason(
            code=EligibilityReasonCode.EVIDENCE_FREEZE_UNAVAILABLE,
            detail=(
                "the frozen evidence is incomplete: an evidence boundary, a dataset "
                "fingerprint and an established joint population are all required before any "
                "eligibility decision can be made against it"),
            subject=freeze.evidence_identity,
        ))

    # 4. support: common support first, then per-cell support.
    common = _check_common_support(freeze.common_support, specification)
    if common is not None:
        return verdict(common)
    cells = _check_cell_support(freeze.cell_support, specification)
    if cells is not None:
        return verdict(cells)

    # 5. scientific: progressive depth, parent eligibility and justification.
    progression = _check_depth_progression(interaction, parent, justification)
    if progression is not None:
        return verdict(progression)
    if interaction.depth > 1 and parent_decision is not None and not parent_decision.permitted:
        return verdict(EligibilityReason(
            code=EligibilityReasonCode.PARENT_NOT_ELIGIBLE,
            detail=(
                f"the parent interaction {parent_decision.interaction_identity!r} was "
                f"{parent_decision.state.value}, not PERMIT, so it cannot justify a "
                f"depth-{interaction.depth} expansion"),
            subject=parent_decision.interaction_identity,
        ))
    return verdict(None)


__all__ = [
    "DECISION_ID_PREFIX",
    "ELIGIBILITY_SCHEMA_VERSION",
    "EligibilityDecision",
    "EligibilityGateError",
    "EligibilityReason",
    "EligibilityState",
    "ExpansionJustification",
    "ExpansionJustificationType",
    "JUSTIFICATION_ID_PREFIX",
    "PROCEEDING_STATES",
    "decision_identity_for",
    "evaluate_interaction_eligibility",
    "is_decision_identity",
    "is_justification_identity",
    "justification_identity_for",
    "parent_step_is_valid",
]
