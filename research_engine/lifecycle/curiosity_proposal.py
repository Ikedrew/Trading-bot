"""
Generated Research Proposal v1 -- the immutable PRE-REGISTRATION curiosity output.

Stage 3 / Wave 3. A `GeneratedResearchProposal` is what curiosity PROPOSES. It is
deliberately not anything else:

    it is NOT a finding        (it asserts no truth and no result)
    it is NOT a hypothesis     (it states no claim and no test)
    it is NOT a candidate      (it proposes no treatment)
    it is NOT a `GEN-*` record (registration is a separate, governed step)
    it is NOT production authority of any kind

A proposal may only become investigation-ready generated research after the
committed Wave 2 gate returns PERMIT. This module therefore carries the
`proposed_interaction` when a proposal is interaction-shaped, so Wave 2 can be
handed exactly the proposal curiosity built -- never a substitute, never an
enumeration of alternatives.

DETERMINISM
===========
`proposal_identity` is `PRP-` + 16 uppercase hex characters of a SHA-256 digest
over the canonical JSON encoding of `semantic_material()`. Equivalent proposals
from equivalent evidence resolve to the same identity, so re-running a generator
over identical inputs cannot mint a second proposal. `note` and `created_at` are
provenance and are ABSENT from identity material.

NO ENUMERATION, NO SEARCH, NO PROFIT MINING (HARD INVARIANTS)
=============================================================
This module contains no combination generator, no cartesian expansion, no loop
over the dimension registry, no ranking of alternatives, no "try another
dimension" fallback, and no historical-return / expectancy / profitability
selection of any kind. It builds ONE proposal from ONE signal. Wave 4 governs
search provenance and multiplicity; this wave does not search.

NO PRODUCTION AUTHORITY
-----------------------
A proposal names a research subject. It has no path to strategy runtime, risk,
sizing, broker execution, production configuration, baseline activation or
candidate promotion, and it imports no runner, no orchestrator, no
`GovernanceGate`. It performs no I/O and writes nothing at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.curiosity_signal import (
    CuriosityRecursionError,
    CuriositySignal,
)
from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_SCHEMA_VERSION,
    GeneratedResearchKind,
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.progressive_depth_gate import (
    ExpansionJustification,
    is_justification_identity,
)
from research_engine.lifecycle.research_interaction import (
    ResearchInteraction,
    is_interaction_identity,
)

# -- Versions (clean reset: starts at 1, never > 1) ---------------------------

CURIOSITY_PROPOSAL_SCHEMA_VERSION: int = 1

# -- Namespace ----------------------------------------------------------------
#
# `PRP-` (proposal) is disjoint from `GEN-`/`CSN-` and from the Wave 1/Wave 2 and
# canonical namespaces. A proposal is never a generated research identity.

CURIOSITY_PROPOSAL_ID_PREFIX = "PRP-"
CURIOSITY_PROPOSAL_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PRP_ID_RE = re.compile(
    rf"^{re.escape(CURIOSITY_PROPOSAL_ID_PREFIX)}"
    rf"[0-9A-F]{{{CURIOSITY_PROPOSAL_ID_DIGEST_CHARS}}}$"
)
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


# -- Errors (fail closed, never silently repaired) -----------------------------


class CuriosityProposalError(RuntimeError):
    """Base failure for the curiosity proposal layer."""


class CuriosityProposalValidationError(CuriosityProposalError):
    """Proposal material is invalid, incomplete or not canonically encodable."""


# -- Curiosity mode -----------------------------------------------------------


class CuriosityMode(str, Enum):
    """
    The three governed ways curiosity may propose new research.

    These are a CLOSED taxonomy of intent, never a priority ordering and never a
    ranking: nothing in this module compares, scores or orders them.
    """

    #: "What relevant governed dimension am I not currently investigating?"
    EXPANSION = "EXPANSION"
    #: "What governed combination of dimensions may justify deeper investigation?"
    INTERACTION = "INTERACTION"
    #: "What existing component may deserve challenge or simplification?"
    DESTRUCTIVE = "DESTRUCTIVE"


#: How a proposal maps onto the committed Wave 0 research-kind taxonomy. This is
#: a pure label mapping; Wave 0 attaches no behaviour to any of these kinds.
_RESEARCH_KIND_BY_MODE: Mapping[CuriosityMode, GeneratedResearchKind] = {
    CuriosityMode.EXPANSION: GeneratedResearchKind.EXPANSION,
    CuriosityMode.INTERACTION: GeneratedResearchKind.INTERACTION,
    CuriosityMode.DESTRUCTIVE: GeneratedResearchKind.DESTRUCTIVE,
}


def research_kind_for_mode(mode: "CuriosityMode | str") -> GeneratedResearchKind:
    """The Wave 0 `GeneratedResearchKind` corresponding to a curiosity mode."""
    try:
        resolved = mode if isinstance(mode, CuriosityMode) else CuriosityMode(mode)
    except ValueError as exc:
        raise CuriosityProposalValidationError(
            f"unknown curiosity mode: {mode!r}") from exc
    return _RESEARCH_KIND_BY_MODE[resolved]


# -- Destructive target taxonomy (minimal, not the Wave 5 treatment system) ----


class DestructiveTargetKind(str, Enum):
    """
    The minimal governed CATEGORY vocabulary for a destructive research target.

    This is deliberately NOT the Wave 5 treatment taxonomy (ADD / MODIFY /
    CONDITION / DOWNWEIGHT / DISABLE / REMOVE). Wave 3 does not decide what would
    be DONE to a component; it only records that a component of one of these
    generic categories deserves to be challenged. The vocabulary is generic by
    construction -- it is not hardcoded to candlestick patterns, and no category
    is privileged over another.
    """

    PATTERN = "PATTERN"
    GUARD = "GUARD"
    FEATURE = "FEATURE"
    FILTER = "FILTER"
    SCORING_COMPONENT = "SCORING_COMPONENT"
    EXIT_RULE = "EXIT_RULE"
    STRATEGY_COMPONENT = "STRATEGY_COMPONENT"
    ASSUMPTION = "ASSUMPTION"
    RULE = "RULE"


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise CuriosityProposalValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "curiosity proposal semantic material").encode("utf-8")
    ).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CuriosityProposalValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise CuriosityProposalValidationError(
            f"{label} must not have surrounding whitespace")
    return value


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise CuriosityProposalValidationError(
            f"{label} must be a governed '<kind>:<id>' reference, got {text!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REASON_RE.match(text):
        raise CuriosityProposalValidationError(
            f"{label} must be a CLOSED machine-readable SHOUTY_SNAKE_CASE code; "
            f"free text is not authority, got {text!r}")
    return text


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise CuriosityProposalValidationError(
            f"{label} must be a governed token, got {text!r}")
    return text


def is_curiosity_proposal_identity(value: Any) -> bool:
    """True only for IDs inside the reserved curiosity-proposal namespace."""
    return isinstance(value, str) and bool(_PRP_ID_RE.match(value))


def proposal_identity_for(semantic_identity: str) -> str:
    """Derive the governed proposal identity deterministically from a digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise CuriosityProposalValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return (
        f"{CURIOSITY_PROPOSAL_ID_PREFIX}"
        f"{semantic_identity[:CURIOSITY_PROPOSAL_ID_DIGEST_CHARS].upper()}"
    )


# -- The proposal -------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedResearchProposal:
    """
    An immutable, deterministically identified research PROPOSAL.

    This is the pre-registration curiosity output. It is not a finding, not a
    hypothesis, not a candidate, not a `GEN-*` record and not production
    authority. It may only become investigation-ready after an authoritative
    eligibility decision returns PERMIT: the committed Wave 2 gate for governed
    interactions, or the narrow destructive-evidence gate for a component
    challenge that would be distorted by a fake interaction.

    Identity material: curiosity mode, the originating signal identity, the
    target, the generation reason, the evidence reference, the evidence
    boundary, the proposed interaction identity, the added/parent dimension
    identities, the governed expansion justification identity, the destructive
    target material, and the parent (ancestry) references.

    NOT identity material: `note` and `created_at` -- both are provenance only.
    """

    mode: CuriosityMode
    signal_identity: str
    target_kind: str
    target_ref: str
    reason_code: str
    evidence_reference: str
    evidence_boundary: str
    proposed_interaction: ResearchInteraction | None = None
    added_dimension_identity: str | None = None
    parent_interaction_identity: str | None = None
    parent_proposal_identity: str | None = None
    parent_decision_identity: str | None = None
    expansion_justification: ExpansionJustification | None = None
    component_ref: str | None = None
    component_kind: DestructiveTargetKind | str | None = None
    baseline_ref: str | None = None
    parent_research_refs: tuple[str, ...] = ()
    note: str = ""                 # provenance only; NOT identity
    created_at: str = ""           # provenance only; NOT identity
    schema_version: int = CURIOSITY_PROPOSAL_SCHEMA_VERSION
    definition_version: int = GENERATED_RESEARCH_SCHEMA_VERSION
    semantic_identity: str = ""
    proposal_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.mode, str) and not isinstance(self.mode, CuriosityMode):
            object.__setattr__(self, "mode", CuriosityMode(self.mode))
        if isinstance(self.component_kind, str) and not isinstance(
                self.component_kind, DestructiveTargetKind):
            object.__setattr__(
                self, "component_kind", DestructiveTargetKind(self.component_kind))
        object.__setattr__(self, "parent_research_refs", tuple(
            sorted(self.parent_research_refs)))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = proposal_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise CuriosityProposalValidationError(
                    "presented semantic identity does not match the proposal material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.proposal_identity:
            if self.proposal_identity != expected_id:
                raise CuriosityProposalValidationError(
                    f"presented proposal identity {self.proposal_identity!r} does not "
                    "match the proposal material")
        else:
            object.__setattr__(self, "proposal_identity", expected_id)

    # -- Construction ------------------------------------------------------

    @classmethod
    def create(cls, **kwargs: Any) -> "GeneratedResearchProposal":
        """Build a proposal. Identity is derived, never supplied."""
        return cls(**kwargs)

    # -- Views -------------------------------------------------------------

    @property
    def research_kind(self) -> GeneratedResearchKind:
        """The Wave 0 research-kind label for this proposal's curiosity mode."""
        return research_kind_for_mode(self.mode)

    @property
    def interaction_identity(self) -> str | None:
        """The governed identity of the interaction this proposal proposes."""
        if self.proposed_interaction is None:
            return None
        return self.proposed_interaction.interaction_identity

    @property
    def requires_wave2(self) -> bool:
        """
        Whether Wave 2 governs this proposal.

        A proposal that proposes a governed interaction must pass the Wave 2
        gate. A DESTRUCTIVE proposal challenges a COMPONENT and references no
        governed dimension interaction, so Wave 2's interaction-depth semantics
        do not apply. It still requires a separate authoritative four-state
        destructive eligibility decision before Wave 0 registration.
        """
        return self.proposed_interaction is not None

    def scientific_question_material(self) -> dict[str, Any]:
        """The question itself, excluding trigger, evidence and ancestry provenance."""
        return {
            "mode": self.mode.value,
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "proposed_interaction_identity": self.interaction_identity,
            "added_dimension_identity": self.added_dimension_identity,
            "component": None if self.component_ref is None else {
                "ref": self.component_ref,
                "kind": (
                    self.component_kind.value
                    if isinstance(self.component_kind, DestructiveTargetKind)
                    else self.component_kind),
                "baseline_ref": self.baseline_ref,
            },
        }

    # -- Identity material / serialisation ---------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen scientific material of this proposal.

        `note` and `created_at` are ABSENT by design: a note is not authority and
        a timestamp is provenance, not identity.
        """
        return {
            "kind": "curiosity_proposal",
            "schema_version": self.schema_version,
            "definition_version": self.definition_version,
            "mode": self.mode.value,
            "signal_identity": self.signal_identity,
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "reason_code": self.reason_code,
            "evidence_reference": self.evidence_reference,
            "evidence_boundary": self.evidence_boundary,
            "proposed_interaction_identity": self.interaction_identity,
            "added_dimension_identity": self.added_dimension_identity,
            "parent_interaction_identity": self.parent_interaction_identity,
            "parent_proposal_identity": self.parent_proposal_identity,
            "parent_decision_identity": self.parent_decision_identity,
            "expansion_justification_identity": (
                self.expansion_justification.justification_identity
                if self.expansion_justification is not None else None),
            "component": None if self.component_ref is None else {
                "ref": self.component_ref,
                "kind": (
                    self.component_kind.value
                    if isinstance(self.component_kind, DestructiveTargetKind)
                    else self.component_kind
                ),
                "baseline_ref": self.baseline_ref,
            },
            "parent_research_refs": list(self.parent_research_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "note": self.note,
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "proposal_identity": self.proposal_identity,
        }

    # -- Validation / recursion control ------------------------------------

    def _validate(self) -> "GeneratedResearchProposal":
        if self.schema_version != CURIOSITY_PROPOSAL_SCHEMA_VERSION:
            raise CuriosityProposalValidationError(
                f"curiosity proposal schema_version must be "
                f"{CURIOSITY_PROPOSAL_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if self.definition_version != GENERATED_RESEARCH_SCHEMA_VERSION:
            raise CuriosityProposalValidationError(
                f"definition_version must be {GENERATED_RESEARCH_SCHEMA_VERSION} "
                f"(clean reset), got {self.definition_version!r}")
        if not isinstance(self.mode, CuriosityMode):
            raise CuriosityProposalValidationError(
                f"mode must be a CuriosityMode, got {self.mode!r}")

        from research_engine.lifecycle.curiosity_signal import (
            is_curiosity_signal_identity,
        )
        if not is_curiosity_signal_identity(self.signal_identity):
            raise CuriosityProposalValidationError(
                f"signal_identity must be a governed curiosity-signal identity, got "
                f"{self.signal_identity!r}; a proposal may not exist without the "
                f"evidence signal that caused it")

        _require_token(self.target_kind, "target_kind")
        _require_text(self.target_ref, "target_ref")
        _require_reason(self.reason_code, "reason_code")
        _require_reference(self.evidence_reference, "evidence_reference")
        _require_text(self.evidence_boundary, "evidence_boundary")

        if self.proposed_interaction is not None and not isinstance(
                self.proposed_interaction, ResearchInteraction):
            raise CuriosityProposalValidationError(
                f"proposed_interaction must be a ResearchInteraction, got "
                f"{type(self.proposed_interaction).__name__}")
        if self.added_dimension_identity is not None and not is_dimension_identity(
                self.added_dimension_identity):
            raise CuriosityProposalValidationError(
                f"added_dimension_identity must be a governed dimension identity, got "
                f"{self.added_dimension_identity!r}")
        if self.parent_interaction_identity is not None and not is_interaction_identity(
                self.parent_interaction_identity):
            raise CuriosityProposalValidationError(
                f"parent_interaction_identity must be a governed interaction identity, "
                f"got {self.parent_interaction_identity!r}")
        if self.parent_proposal_identity is not None and not is_curiosity_proposal_identity(
                self.parent_proposal_identity):
            raise CuriosityProposalValidationError(
                f"parent_proposal_identity must be a governed curiosity-proposal identity, "
                f"got {self.parent_proposal_identity!r}")
        if self.parent_decision_identity is not None:
            from research_engine.lifecycle.progressive_depth_gate import (
                is_decision_identity,
            )
            if not is_decision_identity(self.parent_decision_identity):
                raise CuriosityProposalValidationError(
                    "parent_decision_identity must be a governed Wave 2 decision "
                    f"identity, got {self.parent_decision_identity!r}")
        if self.expansion_justification is not None:
            if not isinstance(self.expansion_justification, ExpansionJustification):
                raise CuriosityProposalValidationError(
                    f"expansion_justification must be an ExpansionJustification, got "
                    f"{type(self.expansion_justification).__name__}")
            if not is_justification_identity(
                    self.expansion_justification.justification_identity):
                raise CuriosityProposalValidationError(
                    "expansion_justification carries no governed justification identity")

        # --- Destructive material is all-or-nothing ----------------------
        if self.component_ref is None:
            if self.component_kind is not None or self.baseline_ref is not None:
                raise CuriosityProposalValidationError(
                    "component_kind/baseline_ref may only accompany a component_ref")
        else:
            _require_text(self.component_ref, "component_ref")
            _require_text(self.baseline_ref, "baseline_ref")
            if not isinstance(self.component_kind, DestructiveTargetKind):
                raise CuriosityProposalValidationError(
                    f"component_kind must be a DestructiveTargetKind, got "
                    f"{self.component_kind!r}")

        # --- Mode-specific coherence -------------------------------------
        if self.mode is CuriosityMode.DESTRUCTIVE:
            if self.proposed_interaction is not None:
                raise CuriosityProposalValidationError(
                    "a destructive proposal challenges a COMPONENT and must not propose "
                    "a governed dimension interaction")
            if self.component_ref is None:
                raise CuriosityProposalValidationError(
                    "a destructive proposal must identify the exact component it "
                    "challenges")
        else:
            # EXPANSION and INTERACTION are both dimension-grounded.
            if self.component_ref is not None:
                raise CuriosityProposalValidationError(
                    f"a {self.mode.value} proposal must not carry a destructive component "
                    f"target")
            if self.added_dimension_identity is None:
                raise CuriosityProposalValidationError(
                    f"a {self.mode.value} proposal must name the governed dimension whose "
                    f"evidence motivated it")
        if self.mode is CuriosityMode.INTERACTION:
            if self.parent_interaction_identity is None:
                raise CuriosityProposalValidationError(
                    "an INTERACTION proposal must name its exact parent interaction")
            if self.parent_decision_identity is None:
                raise CuriosityProposalValidationError(
                    "an INTERACTION proposal must preserve the exact PERMIT decision "
                    "for its parent interaction")
        elif self.parent_decision_identity is not None:
            raise CuriosityProposalValidationError(
                f"a {self.mode.value} proposal must not carry a parent Wave 2 decision")
        if self.mode is CuriosityMode.EXPANSION and self.expansion_justification is not None:
            raise CuriosityProposalValidationError(
                "an EXPANSION proposal adds a dimension to a subject and is not itself a "
                "depth expansion of a parent interaction; it must not carry a Wave 2 "
                "expansion justification")

        # --- Recursion control (smallest deterministic guard needed now) --
        # 1. Direct self-parenting is rejected outright.
        expected_id = proposal_identity_for(_digest(self.semantic_material()))
        if (self.parent_proposal_identity == expected_id
                or (self.proposal_identity
                    and self.parent_proposal_identity == self.proposal_identity)):
            raise CuriosityRecursionError(
                "a proposal may not be its own parent: research -> finding -> new "
                "question must be a genuinely new scientific question")
        # 2. A proposal that references its own originating signal as ancestry is
        #    a loop, not a derivation.
        if self.signal_identity in self.parent_research_refs:
            raise CuriosityRecursionError(
                "a proposal may not list its own originating signal in its ancestry")
        # 3. A child proposal semantically identical to its parent research is a
        #    duplicate, not new research.
        if len(set(self.parent_research_refs)) != len(self.parent_research_refs):
            raise CuriosityProposalValidationError(
                "parent_research_refs must not contain duplicates")
        for ref in self.parent_research_refs:
            _require_reference(ref, "parent_research_ref")
            if self.proposal_identity and ref == f"proposal:{self.proposal_identity}":
                raise CuriosityRecursionError(
                    "a proposal may not directly reference itself as a parent")
        # 4. A proposal must not propose the very interaction it descends from.
        if (self.proposed_interaction is not None
                and self.interaction_identity == self.parent_interaction_identity):
            raise CuriosityRecursionError(
                "a proposal may not propose the same interaction as its parent "
                "interaction: the child must be genuinely new research")

        if self.note and not isinstance(self.note, str):
            raise CuriosityProposalValidationError("note must be a string")
        if self.created_at and not isinstance(self.created_at, str):
            raise CuriosityProposalValidationError("created_at must be a string")
        _encode(self.semantic_material(), "curiosity proposal semantic material")
        return self


__all__ = [
    "CURIOSITY_PROPOSAL_ID_DIGEST_CHARS",
    "CURIOSITY_PROPOSAL_ID_PREFIX",
    "CURIOSITY_PROPOSAL_SCHEMA_VERSION",
    "CuriosityMode",
    "CuriosityProposalError",
    "CuriosityProposalValidationError",
    "DestructiveTargetKind",
    "GeneratedResearchProposal",
    "is_curiosity_proposal_identity",
    "proposal_identity_for",
    "research_kind_for_mode",
]

