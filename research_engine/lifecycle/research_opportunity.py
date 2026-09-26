"""
Research Opportunity v1 -- the governed unit of research ATTENTION.

Stage 3 / Wave 5. Waves 0-4 built the machinery that makes a piece of research
*legitimate* (identity, dimensions, depth eligibility, curiosity, search
provenance, multiplicity). What none of them can answer is:

    Given several legitimate unresolved research opportunities, what should the
    research system investigate NEXT, and why?

Wave 5 is a governance layer over ATTENTION. This module is the bottom of it: an
immutable, deterministically identified opportunity that a later agenda may
consider. It is not an executor, a scheduler or a trading optimiser.

WHAT AN OPPORTUNITY IS
----------------------
A *governed* assertion that an already-governed research subject may legitimately
consume future research resources. It therefore:

    - MUST reference an existing governed research identity (never free text
      alone): a canonical question, a generated research identity, a governed
      dimension, a research interaction, a curiosity proposal, a Wave 2
      eligibility decision, a Wave 4 search record or a Wave 4 T0 selection
      freeze;
    - records WHY it currently deserves, or does not deserve, attention, using
      the Wave 2 lifecycle vocabulary rather than a new one;
    - records its INFORMATION VALUE (the value of LEARNING) and its RESEARCH COST
      as two separate, inspectable, ordinal concepts;
    - records explicit, deterministic dependencies on other opportunities.

WHAT AN OPPORTUNITY IS NOT
--------------------------
    - It is NOT a research question, a hypothesis, a finding or a candidate.
    - It is NOT an experiment, a backtest or a result.
    - It is NOT a priority. It carries the INPUTS to prioritisation; the agenda
      owns the ORDER.
    - It has ZERO production authority.

INFORMATION VALUE IS NOT EXPECTED PROFIT
========================================
`InformationValue` is the value of RESOLVING uncertainty. It is a CLOSED set of
governed ordinal factors, combined through an explicit, published table
(`INFORMATION_BAND_TABLE`) into an ordinal band. It NEVER reads, derives or
approximates expected P&L, win rate, Sharpe, expectancy, drawdown, profit
probability or "likely winner". There is no weighted composite score and no
floating-point priority: fabricating that precision would be dishonest.

UNKNOWN IS HONEST
-----------------
`ResearchAnswerability.NOT_YET_DETERMINABLE` and every `*.UNKNOWN` cost factor
produce an explicit UNKNOWN band. UNKNOWN is never silently coerced to a
favourable value:

    - information UNKNOWN ranks AFTER every known information band;
    - cost UNKNOWN ranks AFTER CHEAP (unknown cost is never treated as zero).

A missing fact is recorded as missing, and is visible in the identity.

DEPENDENCIES
------------
`depends_on` names other OPPORTUNITY identities (or a governed reference) that
must be resolved first. It is part of the opportunity's semantic material, so a
changed dependency is a different opportunity -- there is no way to quietly
re-point a dependency and keep an identity. Cycles are rejected by
`assert_acyclic_dependencies`, which fails closed.

DETERMINISM
-----------
`semantic_identity` is the SHA-256 digest of `semantic_material()`. `created_at`
and `provenance` are provenance only: they are persisted, but they are ABSENT
from identity material, so re-discovering an opportunity months later -- possibly
with unrelated runtime metadata attached -- resolves to the same opportunity.

NO PRODUCTION AUTHORITY
-----------------------
This module performs no I/O, imports no runner, orchestrator, broker, risk,
sizing, baseline or candidate machinery, and writes nothing at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.curiosity_proposal import (
    is_curiosity_proposal_identity,
)
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_QUESTION_IDS,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.progressive_depth_gate import (
    EligibilityState,
    is_decision_identity,
)
from research_engine.lifecycle.research_interaction import is_interaction_identity
from research_engine.lifecycle.search_provenance import (
    is_alternative_identity,
    is_multiplicity_identity,
    is_search_family_identity,
    is_search_record_identity,
    is_selection_freeze_identity,
)

RESEARCH_OPPORTUNITY_SCHEMA_VERSION: int = 1

# `ROP-` (research opportunity) is disjoint from `GEN-`, `CSN-`, `PRP-`, `DIM-`,
# `IXN-`, `SLC-`, `FSP-`, `EVD-`, `DEC-`, `EXP-`, `ALT-`, `FAM-`, `SRC-`, `FRZ-`,
# `MUL-` and from every canonical programme prefix. An opportunity is never a
# research question and is never a `GEN-*` identity.
RESEARCH_OPPORTUNITY_ID_PREFIX = "ROP-"
RESEARCH_OPPORTUNITY_ID_DIGEST_CHARS = 16

_ID_DIGEST_CHARS = RESEARCH_OPPORTUNITY_ID_DIGEST_CHARS
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ROP_ID_RE = re.compile(
    rf"^{re.escape(RESEARCH_OPPORTUNITY_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_OPPORTUNITY_ID_DIGEST_CHARS}}}$")
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class ResearchAgendaError(RuntimeError):
    """Base failure for the Wave 5 research agenda / prioritisation layer."""


class ResearchOpportunityValidationError(ResearchAgendaError):
    """Opportunity material is invalid, incomplete or not canonically encodable."""


class ResearchOpportunityNamespaceViolation(ResearchAgendaError):
    """A presented subject or identity is outside every governed namespace."""


class ResearchDependencyCycleError(ResearchOpportunityValidationError):
    """The opportunity dependency graph is circular. Fails closed, always."""


def is_research_opportunity_identity(value: Any) -> bool:
    """True only for IDs inside the reserved research-opportunity namespace."""
    return isinstance(value, str) and bool(_ROP_ID_RE.match(value))


def is_wave4_identity(value: Any) -> bool:
    """True for any governed Wave 4 identity (ALT/FAM/SRC/FRZ/MUL)."""
    return (is_alternative_identity(value) or is_search_family_identity(value)
            or is_search_record_identity(value)
            or is_selection_freeze_identity(value) or is_multiplicity_identity(value))


def research_opportunity_identity_for(semantic_identity: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise ResearchOpportunityValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return f"{RESEARCH_OPPORTUNITY_ID_PREFIX}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise ResearchOpportunityValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchOpportunityValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ResearchOpportunityValidationError(
            f"{label} must not have surrounding whitespace")
    return value


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise ResearchOpportunityValidationError(
            f"{label} is not a governed token: {text!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REASON_RE.match(text):
        raise ResearchOpportunityValidationError(
            f"{label} must be a CLOSED machine-readable SHOUTY_SNAKE_CASE code; free "
            f"text is not authority, got {text!r}")
    return text


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise ResearchOpportunityValidationError(
            f"{label} must be a governed 'scheme:value' reference, got {text!r}")
    return text


def _reasons(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchOpportunityValidationError(
            f"{label} must be a sequence of codes")
    codes = [_require_reason(item, f"{label} entry") for item in values]
    if len(set(codes)) != len(codes):
        raise ResearchOpportunityValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(codes))


def _optional_count(value: Any, label: str) -> int | None:
    """`None` is a first-class, honest value: the count is UNKNOWN, not zero."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchOpportunityValidationError(
            f"{label} must be an integer or None (UNKNOWN), got {value!r}")
    if value < 0:
        raise ResearchOpportunityValidationError(
            f"{label} must not be negative, got {value!r}")
    return int(value)


def _dependencies(values: Any, label: str) -> tuple[str, ...]:
    """
    Normalise a dependency list: sorted, duplicate-free, deterministic.

    An entry is either a governed `ROP-*` opportunity identity (the blocked work
    is itself a governed opportunity) or a governed `scheme:value` reference to
    research governed elsewhere. Both must be machine identities, so "wait until
    the idea is good enough" can never be expressed as a dependency.
    """
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchOpportunityValidationError(
            f"{label} must be a sequence of governed identities or references")
    entries: list[str] = []
    for item in values:
        text = _require_text(item, f"{label} entry")
        if not (is_research_opportunity_identity(text) or _REFERENCE_RE.match(text)):
            raise ResearchOpportunityValidationError(
                f"{label} entry must be a `ROP-*` opportunity identity or a governed "
                f"'scheme:value' reference, got {text!r}")
        entries.append(text)
    if len(set(entries)) != len(entries):
        raise ResearchOpportunityValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(entries))


def _refs(values: Any, label: str) -> tuple[str, ...]:
    """Normalise a parent-research reference list: sorted and duplicate-free."""
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchOpportunityValidationError(
            f"{label} must be a sequence of references")
    refs = [_require_reference(item, f"{label} entry") for item in values]
    if len(set(refs)) != len(refs):
        raise ResearchOpportunityValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(refs))


def _is_canonical_question(value: Any) -> bool:
    """
    True only for a member of the FROZEN canonical 70-question inventory.

    Read-only: the canonical registry is never appended to, never rewritten and
    never imported here for mutation. A research opportunity may legitimately be
    ABOUT a canonical question; it may never become one.
    """
    return isinstance(value, str) and value in CANONICAL_QUESTION_IDS


class OpportunityState(str, Enum):
    """
    Why an opportunity currently does -- or does not -- deserve research attention.

    `READY`, `WAITING_DATA`, `BLOCKED` and `REFUSED` are the EXISTING Wave 2
    eligibility states. Wave 5 does not duplicate Wave 2 state machinery to
    rename it: `opportunity_state_from_eligibility` is the single mapping, and
    `OpportunityState.eligibility_state()` is its exact inverse.

    `DEFERRED` and `COMPLETE` are genuinely agenda-layer states with no Wave 2
    eligibility equivalent (a deliberate deferral, and research that is no
    longer required). They are declared here rather than invented ad hoc in the
    agenda, so the whole vocabulary stays inspectable in one place.
    """

    READY = "READY"
    WAITING_DATA = "WAITING_DATA"
    BLOCKED = "BLOCKED"
    DEFERRED = "DEFERRED"
    REFUSED = "REFUSED"
    COMPLETE = "COMPLETE"

    @property
    def is_executable_research(self) -> bool:
        """
        Only `READY` is research work that may be queued.

        Structurally impossible work is NEVER executable, so the bounded queue
        can never silently promote it.
        """
        return self is OpportunityState.READY

    def eligibility_state(self) -> EligibilityState | None:
        """The exact Wave 2 equivalent, or `None` for agenda-layer-only states."""
        return _ELIGIBILITY_FOR_OPPORTUNITY.get(self)


_ELIGIBILITY_FOR_OPPORTUNITY: Mapping[OpportunityState, EligibilityState] = {
    OpportunityState.READY: EligibilityState.PERMIT,
    OpportunityState.WAITING_DATA: EligibilityState.WAITING_DATA,
    OpportunityState.BLOCKED: EligibilityState.BLOCKED,
    OpportunityState.REFUSED: EligibilityState.REFUSE,
}

_OPPORTUNITY_FOR_ELIGIBILITY: Mapping[EligibilityState, OpportunityState] = {
    EligibilityState.PERMIT: OpportunityState.READY,
    EligibilityState.WAITING_DATA: OpportunityState.WAITING_DATA,
    EligibilityState.BLOCKED: OpportunityState.BLOCKED,
    EligibilityState.REFUSE: OpportunityState.REFUSED,
}

#: Ordered executable class. A non-executable class may NEVER precede a
#: structurally executable one, whatever the policy's remaining keys say.
EXECUTABILITY_CLASS_ORDER: Mapping[OpportunityState, int] = {
    OpportunityState.READY: 0,
    OpportunityState.DEFERRED: 1,
    OpportunityState.WAITING_DATA: 2,
    OpportunityState.BLOCKED: 3,
    OpportunityState.REFUSED: 4,
    OpportunityState.COMPLETE: 5,
}


def opportunity_state_from_eligibility(
        state: EligibilityState | str) -> OpportunityState:
    """
    Map an EXISTING Wave 2 eligibility state onto the Wave 5 state.

    Wave 5 does not re-derive eligibility: it reads the governed decision and
    carries the verdict forward verbatim, so a Wave 2 refusal can never be
    relabelled as deferred research.
    """
    try:
        resolved = (
            state if isinstance(state, EligibilityState) else EligibilityState(state))
    except ValueError as exc:
        raise ResearchOpportunityValidationError(
            f"unknown eligibility state: {state!r}") from exc
    try:
        return _OPPORTUNITY_FOR_ELIGIBILITY[resolved]
    except KeyError as exc:  # pragma: no cover - the mapping is closed
        raise ResearchOpportunityValidationError(
            f"eligibility state {resolved.value} has no Wave 5 equivalent") from exc


class OpportunitySubjectKind(str, Enum):
    """
    The closed set of authoritative, already-governed research identities an
    opportunity may legitimately be about.

    There is deliberately no `FREE_TEXT` member: an opportunity can never be
    created from an ungoverned idea.
    """

    CANONICAL_QUESTION = "CANONICAL_QUESTION"
    GENERATED_RESEARCH = "GENERATED_RESEARCH"
    GOVERNED_DIMENSION = "GOVERNED_DIMENSION"
    RESEARCH_INTERACTION = "RESEARCH_INTERACTION"
    CURIOSITY_PROPOSAL = "CURIOSITY_PROPOSAL"
    ELIGIBILITY_DECISION = "ELIGIBILITY_DECISION"
    SEARCH_RECORD = "SEARCH_RECORD"
    SEARCH_SELECTION_FREEZE = "SEARCH_SELECTION_FREEZE"


_SUBJECT_PREDICATES = {
    OpportunitySubjectKind.CANONICAL_QUESTION: _is_canonical_question,
    OpportunitySubjectKind.GENERATED_RESEARCH: is_generated_research_id,
    OpportunitySubjectKind.GOVERNED_DIMENSION: is_dimension_identity,
    OpportunitySubjectKind.RESEARCH_INTERACTION: is_interaction_identity,
    OpportunitySubjectKind.CURIOSITY_PROPOSAL: is_curiosity_proposal_identity,
    OpportunitySubjectKind.ELIGIBILITY_DECISION: is_decision_identity,
    OpportunitySubjectKind.SEARCH_RECORD: is_search_record_identity,
    OpportunitySubjectKind.SEARCH_SELECTION_FREEZE: is_selection_freeze_identity,
}

#: The Wave 4 provenance block MUST be carried when the subject is a Wave 4
#: record, so the search identity, family identity, multiplicity denominator,
#: T0 freeze and confirmation policy travel with the research work.
WAVE4_SUBJECT_KINDS = (
    OpportunitySubjectKind.SEARCH_RECORD,
    OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
)

_REQUIRED_WAVE4_PROVENANCE_KEYS = (
    "search_record_id",
    "search_family_id",
    "selection_freeze_id",
    "alternatives_considered",
    "multiplicity_family_size",
    "multiplicity_rule",
    "correction_method",
    "discovery_population_identity",
    "discovery_fingerprint_identity",
    "discovery_evidence_boundary",
    "selection_criterion",
    "confirmation_policy",
)


class QuestionResolution(str, Enum):
    """Whether the governed question itself is still open. Never a priority."""

    UNRESOLVED = "UNRESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    RESOLVED = "RESOLVED"


class EvidentialInsufficiency(str, Enum):
    """How badly the current evidence fails to answer the question."""

    HIGH = "HIGH"
    PARTIAL = "PARTIAL"
    LOW = "LOW"


class ExplanationDiscrimination(str, Enum):
    """Whether a result would distinguish COMPETING governed explanations."""

    DISCRIMINATES_COMPETING_EXPLANATIONS = "DISCRIMINATES_COMPETING_EXPLANATIONS"
    SINGLE_HYPOTHESIS_ONLY = "SINGLE_HYPOTHESIS_ONLY"


class ResearchAnswerability(str, Enum):
    """
    Whether the question can be answered from evidence that exists NOW.

    `NOT_YET_DETERMINABLE` is the honest unknown; it yields an UNKNOWN
    information band rather than an optimistic guess.
    """

    ANSWERABLE_WITH_FROZEN_EVIDENCE = "ANSWERABLE_WITH_FROZEN_EVIDENCE"
    REQUIRES_PROSPECTIVE_EVIDENCE = "REQUIRES_PROSPECTIVE_EVIDENCE"
    NOT_YET_DETERMINABLE = "NOT_YET_DETERMINABLE"


class InformationBand(str, Enum):
    """
    The ordinal research-information band. NOT a score and NOT a probability.

    `UNKNOWN` is a first-class member: an unanswerable-by-honesty value that
    ranks after every known band. See `INFORMATION_BAND_RANK`.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


#: The complete, published derivation from governed factors to information band.
#: It is a TABLE, not a formula, precisely so that no false numerical precision
#: is invented and every band is mechanically reconstructable by a reader.
INFORMATION_BAND_TABLE: Mapping[
    tuple[QuestionResolution, EvidentialInsufficiency, ExplanationDiscrimination],
    InformationBand,
] = {
    ("UNRESOLVED", "HIGH", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.HIGH,
    ("UNRESOLVED", "HIGH", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.MEDIUM,
    ("UNRESOLVED", "PARTIAL", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.MEDIUM,
    ("UNRESOLVED", "PARTIAL", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.MEDIUM,
    ("UNRESOLVED", "LOW", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.MEDIUM,
    ("UNRESOLVED", "LOW", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.LOW,
    ("PARTIALLY_RESOLVED", "HIGH", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.MEDIUM,
    ("PARTIALLY_RESOLVED", "HIGH", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.MEDIUM,
    ("PARTIALLY_RESOLVED", "PARTIAL", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.MEDIUM,
    ("PARTIALLY_RESOLVED", "PARTIAL", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.LOW,
    ("PARTIALLY_RESOLVED", "LOW", "DISCRIMINATES_COMPETING_EXPLANATIONS"): InformationBand.LOW,
    ("PARTIALLY_RESOLVED", "LOW", "SINGLE_HYPOTHESIS_ONLY"): InformationBand.LOW,
}

#: Ascending rank. UNKNOWN is LAST: an opportunity whose value of learning is
#: not knowable never displaces one whose value IS knowable.
INFORMATION_BAND_RANK: Mapping[InformationBand, int] = {
    InformationBand.HIGH: 0,
    InformationBand.MEDIUM: 1,
    InformationBand.LOW: 2,
    InformationBand.UNKNOWN: 3,
}


@dataclass(frozen=True)
class InformationValue:
    """
    The value of LEARNING from this research -- never the value of trading it.

    Identity material: the four governed factors. `evidence_reference` and
    `note` are provenance only.

    There is no expected-P&L input, no weight, no normaliser and no composite
    score. The band is a pure table lookup over the three discriminative
    factors, with `answerability` short-circuiting to UNKNOWN.
    """

    question_state: QuestionResolution
    evidential_insufficiency: EvidentialInsufficiency
    explanation_discrimination: ExplanationDiscrimination
    answerability: ResearchAnswerability
    evidence_reference: str = ""
    note: str = ""                    # provenance only; NOT identity
    schema_version: int = RESEARCH_OPPORTUNITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, member in (
            ("question_state", QuestionResolution),
            ("evidential_insufficiency", EvidentialInsufficiency),
            ("explanation_discrimination", ExplanationDiscrimination),
            ("answerability", ResearchAnswerability),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and not isinstance(value, member):
                object.__setattr__(self, name, member(value))
        self._validate()

    @classmethod
    def create(cls, **kwargs: Any) -> "InformationValue":
        """Build an information value. Identity-bearing factors are required."""
        return cls(**kwargs)

    def band(self) -> InformationBand:
        """The derived, table-based information band. Inspectable, never opaque."""
        if self.answerability is ResearchAnswerability.NOT_YET_DETERMINABLE:
            return InformationBand.UNKNOWN
        if self.question_state is QuestionResolution.RESOLVED:
            return InformationBand.LOW
        return INFORMATION_BAND_TABLE[(
            self.question_state, self.evidential_insufficiency,
            self.explanation_discrimination)]

    def rank(self) -> int:
        """Ascending ordinal rank. UNKNOWN is the LAST (worst) rank."""
        return INFORMATION_BAND_RANK[self.band()]

    def is_unknown(self) -> bool:
        return self.band() is InformationBand.UNKNOWN

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_information_value",
            "schema_version": self.schema_version,
            "question_state": self.question_state.value,
            "evidential_insufficiency": self.evidential_insufficiency.value,
            "explanation_discrimination": self.explanation_discrimination.value,
            "answerability": self.answerability.value,
        }

    def _validate(self) -> "InformationValue":
        if self.schema_version != RESEARCH_OPPORTUNITY_SCHEMA_VERSION:
            raise ResearchOpportunityValidationError(
                f"information value schema_version must be "
                f"{RESEARCH_OPPORTUNITY_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        for name, member in (
            ("question_state", QuestionResolution),
            ("evidential_insufficiency", EvidentialInsufficiency),
            ("explanation_discrimination", ExplanationDiscrimination),
            ("answerability", ResearchAnswerability),
        ):
            if not isinstance(getattr(self, name), member):
                raise ResearchOpportunityValidationError(
                    f"{name} must be a governed member, got {getattr(self, name)!r}")
        if self.evidence_reference:
            _require_reference(
                self.evidence_reference, "information evidence_reference")
        _encode(self.semantic_material(), "information value semantic material")
        return self


    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "evidence_reference": self.evidence_reference,
            "note": self.note,
            "band": self.band().value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InformationValue":
        if not isinstance(data, Mapping):
            raise ResearchOpportunityValidationError(
                f"persisted information value must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "question_state", "evidential_insufficiency",
            "explanation_discrimination", "answerability", "evidence_reference", "note",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchOpportunityValidationError(
                f"persisted information value missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchOpportunityValidationError(
                f"persisted information value has unknown fields: {unknown}")
        if data["kind"] != "research_information_value":
            raise ResearchOpportunityValidationError(
                f"persisted information value kind must be "
                f"'research_information_value', got {data['kind']!r}")
        return cls(
            question_state=QuestionResolution(data["question_state"]),
            evidential_insufficiency=EvidentialInsufficiency(
                data["evidential_insufficiency"]),
            explanation_discrimination=ExplanationDiscrimination(
                data["explanation_discrimination"]),
            answerability=ResearchAnswerability(data["answerability"]),
            evidence_reference=data["evidence_reference"],
            note=data["note"],
            schema_version=data["schema_version"],
        )


class DataAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ObservationRequirement(str, Enum):
    NONE = "NONE"
    ADDITIONAL_REQUIRED = "ADDITIONAL_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ProspectiveWait(str, Enum):
    """Whether the answer can only exist AFTER a governed waiting period."""

    NONE = "NONE"
    REQUIRED = "REQUIRED"
    UNKNOWN = "UNKNOWN"


class ConfirmationRequirement(str, Enum):
    NONE = "NONE"
    REQUIRED = "REQUIRED"
    UNKNOWN = "UNKNOWN"


class CostBand(str, Enum):
    """
    The ordinal research-cost band. NOT money and NOT a duration.

    `UNKNOWN` is a first-class member and -- crucially -- ranks AFTER `CHEAP`.
    Unknown cost is never silently treated as zero cost.
    """

    CHEAP = "CHEAP"
    MODERATE = "MODERATE"
    EXPENSIVE = "EXPENSIVE"
    UNKNOWN = "UNKNOWN"


#: Ascending rank. Note `UNKNOWN` is LAST: an unknown cost is the worst case for
#: scheduling purposes, because it cannot be honestly compared to a known cost.
COST_BAND_RANK: Mapping[CostBand, int] = {
    CostBand.CHEAP: 0,
    CostBand.MODERATE: 1,
    CostBand.EXPENSIVE: 2,
    CostBand.UNKNOWN: 3,
}

#: How many individual cost factors must be non-trivial before the band escalates.
COST_ESCALATION_THRESHOLD: Mapping[CostBand, int] = {
    CostBand.CHEAP: 1,
    CostBand.MODERATE: 2,
    CostBand.EXPENSIVE: 3,
}


@dataclass(frozen=True)
class ResearchCost:
    """
    The EXPECTED COST of doing this research -- separate from its value.

    Identity material: the four categorical factors, the alternative count and
    the dependency depth. `note` is provenance only.

    Every factor can honestly be UNKNOWN. UNKNOWN dominates: if any factor is
    unknown the whole cost band is UNKNOWN, so a half-estimated cost can never
    masquerade as a cheap or an expensive one.
    """

    data_availability: DataAvailability
    additional_observations: ObservationRequirement
    prospective_waiting: ProspectiveWait
    confirmation_requirement: ConfirmationRequirement
    alternative_count: int | None = None      # None == UNKNOWN
    dependency_depth: int | None = None       # None == UNKNOWN
    note: str = ""                            # provenance only; NOT identity
    schema_version: int = RESEARCH_OPPORTUNITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, member in (
            ("data_availability", DataAvailability),
            ("additional_observations", ObservationRequirement),
            ("prospective_waiting", ProspectiveWait),
            ("confirmation_requirement", ConfirmationRequirement),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and not isinstance(value, member):
                object.__setattr__(self, name, member(value))
        object.__setattr__(
            self, "alternative_count", _optional_count(
                self.alternative_count, "research cost alternative_count"))
        object.__setattr__(
            self, "dependency_depth", _optional_count(
                self.dependency_depth, "research cost dependency_depth"))
        self._validate()

    @classmethod
    def create(cls, **kwargs: Any) -> "ResearchCost":
        """Build a research cost. Identity-bearing factors are required."""
        return cls(**kwargs)

    def unknown_factors(self) -> tuple[str, ...]:
        """Which factors are honestly unknown. Always inspectable, never hidden."""
        unknown: list[str] = []
        if self.data_availability is DataAvailability.UNKNOWN:
            unknown.append("DATA_AVAILABILITY")
        if self.additional_observations is ObservationRequirement.UNKNOWN:
            unknown.append("ADDITIONAL_OBSERVATIONS")
        if self.prospective_waiting is ProspectiveWait.UNKNOWN:
            unknown.append("PROSPECTIVE_WAITING")
        if self.confirmation_requirement is ConfirmationRequirement.UNKNOWN:
            unknown.append("CONFIRMATION_REQUIREMENT")
        if self.alternative_count is None:
            unknown.append("ALTERNATIVE_COUNT")
        if self.dependency_depth is None:
            unknown.append("DEPENDENCY_DEPTH")
        return tuple(sorted(unknown))

    def load_points(self) -> int:
        """The number of non-trivial cost factors. Inspectable, never hidden."""
        points = 0
        if self.data_availability in (
                DataAvailability.PARTIAL, DataAvailability.UNAVAILABLE):
            points += 1
        if self.additional_observations is ObservationRequirement.ADDITIONAL_REQUIRED:
            points += 1
        if self.prospective_waiting is ProspectiveWait.REQUIRED:
            points += 1
        if self.confirmation_requirement is ConfirmationRequirement.REQUIRED:
            points += 1
        if self.alternative_count is not None and self.alternative_count > 1:
            points += 1
        if self.dependency_depth is not None and self.dependency_depth > 0:
            points += 1
        return points

    def band(self) -> CostBand:
        """The derived cost band. Any UNKNOWN factor makes the whole band UNKNOWN."""
        if self.unknown_factors():
            return CostBand.UNKNOWN
        points = self.load_points()
        if points >= COST_ESCALATION_THRESHOLD[CostBand.EXPENSIVE]:
            return CostBand.EXPENSIVE
        if points >= COST_ESCALATION_THRESHOLD[CostBand.MODERATE]:
            return CostBand.MODERATE
        return CostBand.CHEAP

    def rank(self) -> int:
        """Ascending ordinal rank. UNKNOWN ranks AFTER CHEAP: never zero cost."""
        return COST_BAND_RANK[self.band()]

    def is_unknown(self) -> bool:
        return self.band() is CostBand.UNKNOWN


    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_cost",
            "schema_version": self.schema_version,
            "data_availability": self.data_availability.value,
            "additional_observations": self.additional_observations.value,
            "prospective_waiting": self.prospective_waiting.value,
            "confirmation_requirement": self.confirmation_requirement.value,
            "alternative_count": self.alternative_count,
            "dependency_depth": self.dependency_depth,
        }

    def _validate(self) -> "ResearchCost":
        if self.schema_version != RESEARCH_OPPORTUNITY_SCHEMA_VERSION:
            raise ResearchOpportunityValidationError(
                f"research cost schema_version must be "
                f"{RESEARCH_OPPORTUNITY_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        for name, member in (
            ("data_availability", DataAvailability),
            ("additional_observations", ObservationRequirement),
            ("prospective_waiting", ProspectiveWait),
            ("confirmation_requirement", ConfirmationRequirement),
        ):
            if not isinstance(getattr(self, name), member):
                raise ResearchOpportunityValidationError(
                    f"{name} must be a governed member, got {getattr(self, name)!r}")
        _encode(self.semantic_material(), "research cost semantic material")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "note": self.note,
            "band": self.band().value,
            "unknown_factors": list(self.unknown_factors()),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchCost":
        if not isinstance(data, Mapping):
            raise ResearchOpportunityValidationError(
                f"persisted research cost must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "data_availability", "additional_observations",
            "prospective_waiting", "confirmation_requirement", "alternative_count",
            "dependency_depth", "note",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchOpportunityValidationError(
                f"persisted research cost missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchOpportunityValidationError(
                f"persisted research cost has unknown fields: {unknown}")
        if data["kind"] != "research_cost":
            raise ResearchOpportunityValidationError(
                f"persisted research cost kind must be 'research_cost', got {data['kind']!r}")
        return cls(
            data_availability=DataAvailability(data["data_availability"]),
            additional_observations=ObservationRequirement(data["additional_observations"]),
            prospective_waiting=ProspectiveWait(data["prospective_waiting"]),
            confirmation_requirement=ConfirmationRequirement(
                data["confirmation_requirement"]),
            alternative_count=data["alternative_count"],
            dependency_depth=data["dependency_depth"],
            note=data["note"],
            schema_version=data["schema_version"],
        )


@dataclass(frozen=True)
class ResearchOpportunity:
    """
    One governed piece of research that may legitimately consume attention.

    It binds an EXISTING governed subject (never free text) to:

        the current lifecycle state, in the Wave 2 vocabulary;
        its information value and its research cost, as two separate concepts;
        its explicit dependencies on other opportunities;
        the evidence boundary the state was decided at;
        the Wave 4 search/freeze/multiplicity provenance, when the subject is a
        Wave 4 record.

    IDENTITY MATERIAL (hashed): subject kind/ref, state, reason codes,
    information value, cost, dependencies, unlocks, evidence boundary, parent
    research references, Wave 4 provenance.

    NOT IDENTITY MATERIAL: `label`, `note`, `created_at`, `provenance`. A
    timestamp, a label or unrelated runtime metadata can never make two
    semantically identical opportunities different, and can never change where
    one is ranked.
    """

    subject_kind: OpportunitySubjectKind
    subject_ref: str
    state: OpportunityState
    information: InformationValue
    cost: ResearchCost
    evidence_boundary: str
    reason_codes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    unlocks: tuple[str, ...] = ()
    parent_research_refs: tuple[str, ...] = ()
    search_provenance: Mapping[str, Any] | None = None
    label: str = ""                        # provenance only; NOT identity
    note: str = ""                         # provenance only; NOT identity
    provenance: Mapping[str, Any] = frozenset()   # provenance only; NOT identity
    created_at: str = ""                   # provenance only; NOT identity
    schema_version: int = RESEARCH_OPPORTUNITY_SCHEMA_VERSION
    semantic_identity: str = ""
    opportunity_identity: str = ""

    def __post_init__(self) -> None:
        for name, member in (
            ("subject_kind", OpportunitySubjectKind),
            ("state", OpportunityState),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and not isinstance(value, member):
                object.__setattr__(self, name, member(value))
        object.__setattr__(self, "reason_codes", _reasons(
            self.reason_codes, "reason_codes"))
        object.__setattr__(self, "depends_on", _dependencies(
            self.depends_on, "depends_on"))
        object.__setattr__(self, "unlocks", _dependencies(self.unlocks, "unlocks"))
        object.__setattr__(self, "parent_research_refs", _refs(
            self.parent_research_refs, "parent_research_refs"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = research_opportunity_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchOpportunityValidationError(
                    "presented research opportunity semantic identity does not match the "
                    "opportunity material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.opportunity_identity:
            if self.opportunity_identity != expected_id:
                raise ResearchOpportunityValidationError(
                    f"presented opportunity identity {self.opportunity_identity!r} does "
                    f"not match the opportunity material")
        else:
            object.__setattr__(self, "opportunity_identity", expected_id)

    @classmethod
    def create(cls, **kwargs: Any) -> "ResearchOpportunity":
        """Build an opportunity. Identity is derived, never supplied."""
        return cls(**kwargs)

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "research_opportunity",
            "schema_version": self.schema_version,
            "subject": {"kind": self.subject_kind.value, "ref": self.subject_ref},
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
            "information_value": self.information.semantic_material(),
            "research_cost": self.cost.semantic_material(),
            "evidence_boundary": self.evidence_boundary,
            "depends_on": list(self.depends_on),
            "unlocks": list(self.unlocks),
            "parent_research_refs": list(self.parent_research_refs),
            "search_provenance": (
                None if self.search_provenance is None
                else dict(self.search_provenance)),
        }


    def _validate(self) -> "ResearchOpportunity":
        if self.schema_version != RESEARCH_OPPORTUNITY_SCHEMA_VERSION:
            raise ResearchOpportunityValidationError(
                f"research opportunity schema_version must be "
                f"{RESEARCH_OPPORTUNITY_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if not isinstance(self.subject_kind, OpportunitySubjectKind):
            raise ResearchOpportunityValidationError(
                f"subject_kind must be a governed OpportunitySubjectKind, got "
                f"{self.subject_kind!r}")
        if not isinstance(self.state, OpportunityState):
            raise ResearchOpportunityValidationError(
                f"state must be a governed OpportunityState, got {self.state!r}")
        if not isinstance(self.information, InformationValue):
            raise ResearchOpportunityValidationError(
                f"expected InformationValue, got {type(self.information).__name__}")
        if not isinstance(self.cost, ResearchCost):
            raise ResearchOpportunityValidationError(
                f"expected ResearchCost, got {type(self.cost).__name__}")
        subject_ref = _require_text(self.subject_ref, "subject_ref")

        # The subject must resolve inside its declared governed namespace. A
        # free-form idea is never a research opportunity.
        if not _SUBJECT_PREDICATES[self.subject_kind](subject_ref):
            raise ResearchOpportunityNamespaceViolation(
                f"subject_ref {subject_ref!r} is not a governed "
                f"{self.subject_kind.value} identity; a research opportunity must bind "
                f"to existing governed research state, never to free text")

        _require_text(self.evidence_boundary, "evidence_boundary")

        # A non-READY state MUST carry at least one governed reason code; a
        # silent "not now" is not auditable. A READY state must not carry one.
        if self.state is not OpportunityState.READY and not self.reason_codes:
            raise ResearchOpportunityValidationError(
                f"a {self.state.value} opportunity must carry at least one governed "
                f"reason code explaining why it is not READY")
        if self.state is OpportunityState.READY and self.reason_codes:
            raise ResearchOpportunityValidationError(
                f"a READY opportunity must not carry deferral/refusal reason codes; got "
                f"{list(self.reason_codes)}")

        # A structurally complete question is not unresolved research.
        if (self.state is OpportunityState.READY
                and self.information.question_state is QuestionResolution.RESOLVED):
            raise ResearchOpportunityValidationError(
                "a RESOLVED question cannot be READY research; it is COMPLETE or "
                "NO_LONGER_REQUIRED work, and re-queuing it would be re-litigation")

        # Refusal is terminal: a REFUSED item is not "waiting on" anything.
        if self.state is OpportunityState.REFUSED and self.depends_on:
            raise ResearchOpportunityValidationError(
                "a REFUSED opportunity is terminally refused and may not also be "
                "recorded as dependency-blocked")

        self._validate_wave4_provenance(subject_ref)
        _encode(self.semantic_material(), "research opportunity semantic material")
        return self

    def _validate_wave4_provenance(self, subject_ref: str) -> None:
        """
        A Wave 4 subject MUST carry the whole Wave 4 provenance block.

        This is what keeps the search identity, family identity, multiplicity
        denominator, T0 freeze and confirmation policy bound to the research
        work. Wave 5 copies that block verbatim and never edits it: alternative
        membership, the multiplicity denominator and the Wave 4 identities are
        not Wave 5's to change.
        """
        if self.subject_kind not in WAVE4_SUBJECT_KINDS:
            if self.search_provenance is not None:
                raise ResearchOpportunityValidationError(
                    "search_provenance is only carried by a Wave 4 subject; attaching it "
                    "elsewhere would fabricate search history")
            return
        if not isinstance(self.search_provenance, Mapping):
            raise ResearchOpportunityValidationError(
                f"a {self.subject_kind.value} opportunity must carry the Wave 4 "
                f"search_provenance block; search history is never reconstructed or "
                f"invented by Wave 5")
        missing = [key for key in _REQUIRED_WAVE4_PROVENANCE_KEYS
                   if key not in self.search_provenance]
        if missing:
            raise ResearchOpportunityValidationError(
                f"Wave 4 search_provenance is missing required keys: {missing}")
        expected_subject = (
            self.search_provenance["selection_freeze_id"]
            if self.subject_kind is OpportunitySubjectKind.SEARCH_SELECTION_FREEZE
            else self.search_provenance["search_record_id"])
        if expected_subject != subject_ref:
            raise ResearchOpportunityValidationError(
                f"Wave 4 search_provenance names {expected_subject!r} but the opportunity "
                f"subject is {subject_ref!r}; search provenance may not be re-pointed")
        for key in ("search_record_id", "search_family_id", "selection_freeze_id"):
            if not is_wave4_identity(self.search_provenance[key]):
                raise ResearchOpportunityValidationError(
                    f"Wave 4 search_provenance[{key!r}] is not a governed Wave 4 "
                    f"identity: {self.search_provenance[key]!r}")


    # -- Views (never a priority; the agenda owns the order) -----------------
    @property
    def is_executable_research(self) -> bool:
        """Only READY work may enter the executable ordering."""
        return self.state.is_executable_research

    def has_reason(self, code: str) -> bool:
        return _require_reason(code, "reason code") in self.reason_codes

    def tie_break_key(self) -> str:
        """
        The deterministic final tie-break key: the opportunity's own identity.

        Two opportunities that are genuinely indistinguishable under the policy
        are ordered by this value, so the ordering is total, reproducible, and
        never dependent on caller order, insertion order or a random tie-break.
        """
        return self.opportunity_identity

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "label": self.label,
            "note": self.note,
            "provenance": dict(self.provenance or {}),
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "opportunity_identity": self.opportunity_identity,
            "information_value_provenance": {
                "evidence_reference": self.information.evidence_reference,
                "note": self.information.note,
                "derived_band": self.information.band().value,
            },
            "cost_provenance": {
                "note": self.cost.note,
                "derived_band": self.cost.band().value,
                "unknown_factors": list(self.cost.unknown_factors()),
            },
        }


    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchOpportunity":
        """Strict deserialisation. Unknown/missing/corrupt content fails closed."""
        if not isinstance(data, Mapping):
            raise ResearchOpportunityValidationError(
                f"persisted opportunity must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "subject", "state", "reason_codes",
            "information_value", "research_cost", "evidence_boundary", "depends_on",
            "unlocks", "parent_research_refs", "search_provenance", "label", "note",
            "provenance", "created_at", "semantic_identity", "opportunity_identity",
            "information_value_provenance", "cost_provenance",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchOpportunityValidationError(
                f"persisted opportunity missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchOpportunityValidationError(
                f"persisted opportunity has unknown fields: {unknown}")
        if data["kind"] != "research_opportunity":
            raise ResearchOpportunityValidationError(
                f"persisted opportunity kind must be 'research_opportunity', got "
                f"{data['kind']!r}")
        subject = data["subject"]
        if not isinstance(subject, Mapping) or set(subject) != {"kind", "ref"}:
            raise ResearchOpportunityValidationError(
                "persisted opportunity 'subject' must be exactly {kind, ref}")
        info_prov = data["information_value_provenance"]
        cost_prov = data["cost_provenance"]
        for name, block in (("information_value_provenance", info_prov),
                            ("cost_provenance", cost_prov)):
            if not isinstance(block, Mapping):
                raise ResearchOpportunityValidationError(
                    f"persisted opportunity {name} must be a mapping")
        try:
            information = InformationValue.from_dict(
                {**data["information_value"],
                 "evidence_reference": info_prov["evidence_reference"],
                 "note": info_prov["note"]})
            cost = ResearchCost.from_dict(
                {**data["research_cost"], "note": cost_prov["note"]})
            record = cls(
                subject_kind=OpportunitySubjectKind(subject["kind"]),
                subject_ref=subject["ref"],
                state=OpportunityState(data["state"]),
                information=information,
                cost=cost,
                evidence_boundary=data["evidence_boundary"],
                reason_codes=tuple(data["reason_codes"]),
                depends_on=tuple(data["depends_on"]),
                unlocks=tuple(data["unlocks"]),
                parent_research_refs=tuple(data["parent_research_refs"]),
                search_provenance=data["search_provenance"],
                label=data["label"],
                note=data["note"],
                provenance=data["provenance"],
                created_at=data["created_at"],
                schema_version=data["schema_version"],
                semantic_identity=data["semantic_identity"],
                opportunity_identity=data["opportunity_identity"],
            )
        except (ResearchAgendaError, TypeError, ValueError, KeyError) as exc:
            raise ResearchOpportunityValidationError(
                f"persisted opportunity is malformed: {exc}") from exc
        # Derived bands are projections, never identity: a tampered projection
        # must not be able to rewrite the scientific record.
        if info_prov.get("derived_band") != record.information.band().value:
            raise ResearchOpportunityValidationError(
                f"persisted derived information band {info_prov.get('derived_band')!r} "
                f"does not match the recorded information value")
        if cost_prov.get("derived_band") != record.cost.band().value:
            raise ResearchOpportunityValidationError(
                f"persisted derived cost band {cost_prov.get('derived_band')!r} does not "
                f"match the recorded research cost")
        if list(cost_prov.get("unknown_factors", ())) != list(
                record.cost.unknown_factors()):
            raise ResearchOpportunityValidationError(
                "persisted unknown cost factors do not match the recorded research cost")
        return record


def canonical_opportunities(
        opportunities: Iterable[Any]) -> tuple[ResearchOpportunity, ...]:
    """
    The canonical, input-order-independent population.

    Caller order is NOT scientific order: it is not a priority, not a creation
    sequence and not evidence. Sorting by opportunity identity means two
    equivalent callers who list the same work in different orders produce the
    identical population, and therefore the identical agenda.
    """
    if opportunities is None or isinstance(opportunities, (str, bytes)):
        raise ResearchOpportunityValidationError(
            "an opportunity population must be a sequence of ResearchOpportunity")
    if not isinstance(opportunities, (list, tuple)):
        raise ResearchOpportunityValidationError(
            f"an opportunity population must be a sequence, got "
            f"{type(opportunities).__name__}")
    items: list[ResearchOpportunity] = []
    for item in opportunities:
        if not isinstance(item, ResearchOpportunity):
            raise ResearchOpportunityValidationError(
                f"expected ResearchOpportunity, got {type(item).__name__}")
        items.append(item)
    identities = [item.opportunity_identity for item in items]
    duplicates = sorted({i for i in identities if identities.count(i) > 1})
    if duplicates:
        raise ResearchOpportunityValidationError(
            f"an opportunity population may not contain the same opportunity twice: "
            f"{duplicates}")
    return tuple(sorted(items, key=lambda item: item.opportunity_identity))


def assert_acyclic_dependencies(
        opportunities: Iterable[ResearchOpportunity],
) -> tuple[ResearchOpportunity, ...]:
    """
    Fail closed on a circular dependency.

    A cycle means "A waits for B and B waits for A": no ordering can ever make
    either actionable, so allowing it would let structurally impossible work
    masquerade as research that is merely late. Detected by an explicit DFS with
    colouring over the canonical population, so the failure is deterministic.
    """
    population = canonical_opportunities(opportunities)
    by_identity = {item.opportunity_identity: item for item in population}

    # A dependency on a sibling that is not in the population is a DANGLING
    # reference, not a free pass: the blocking work is invisible to the agenda.
    for item in population:
        for dependency in item.depends_on:
            if (is_research_opportunity_identity(dependency)
                    and dependency not in by_identity):
                raise ResearchOpportunityValidationError(
                    f"opportunity {item.opportunity_identity} depends on {dependency}, "
                    f"which is not present in the considered population; an agenda may "
                    f"not rank work whose blocker it cannot see")

    UNVISITED, ACTIVE, DONE = 0, 1, 2
    colour: dict[str, int] = {
        item.opportunity_identity: UNVISITED for item in population}

    def visit(identity: str, trail: tuple[str, ...]) -> None:
        colour[identity] = ACTIVE
        for dependency in by_identity[identity].depends_on:
            if not is_research_opportunity_identity(dependency):
                continue
            state = colour[dependency]
            if state == ACTIVE:
                start = trail.index(dependency) if dependency in trail else 0
                cycle = list(trail[start:]) + [dependency]
                raise ResearchDependencyCycleError(
                    f"research opportunity dependencies form a cycle: "
                    f"{' -> '.join(cycle)}; a circular dependency can never become "
                    f"executable and fails closed")
            if state == UNVISITED:
                visit(dependency, trail + (dependency,))
        colour[identity] = DONE

    for item in population:
        if colour[item.opportunity_identity] == UNVISITED:
            visit(item.opportunity_identity, (item.opportunity_identity,))
    return population


def dependency_depths(
        opportunities: Iterable[ResearchOpportunity]) -> dict[str, int]:
    """
    How many DEPENDENCY HOPS away each opportunity is from the start of its own
    chain. An independent item is depth 0.

    This is a COUNT of governed edges, so it is honest: it is never an estimate
    of importance, urgency or value.
    """
    population = assert_acyclic_dependencies(opportunities)
    edges = {item.opportunity_identity: item.depends_on for item in population}
    depths: dict[str, int] = {}

    def depth_of(identity: str) -> int:
        if identity in depths:
            return depths[identity]
        deps = [d for d in edges[identity] if is_research_opportunity_identity(d)]
        depths[identity] = 0 if not deps else 1 + max(depth_of(d) for d in deps)
        return depths[identity]

    for item in population:
        depth_of(item.opportunity_identity)
    return depths


def unlock_map(
        opportunities: Iterable[ResearchOpportunity]) -> dict[str, tuple[str, ...]]:
    """
    The INVERSE dependency view: which opportunities each item unblocks.

    Derived from the population, never asserted by hand, so the declared
    `unlocks` field of an opportunity can never contradict the graph the agenda
    actually uses. A declared `unlocks` entry the graph does not support is a
    validation error: a researcher cannot claim their work unblocks something it
    does not.
    """
    population = assert_acyclic_dependencies(opportunities)
    identities = {item.opportunity_identity for item in population}
    derived: dict[str, set[str]] = {
        item.opportunity_identity: set() for item in population}
    for item in population:
        for dependency in item.depends_on:
            if is_research_opportunity_identity(dependency) and dependency in identities:
                derived[dependency].add(item.opportunity_identity)
    for item in population:
        claimed = {u for u in item.unlocks if is_research_opportunity_identity(u)}
        unsupported = sorted(claimed - derived[item.opportunity_identity])
        if unsupported:
            raise ResearchOpportunityValidationError(
                f"opportunity {item.opportunity_identity} claims to unlock "
                f"{unsupported}, which it does not dependency-block; dependency impact "
                f"is derived, never asserted")
    return {key: tuple(sorted(value)) for key, value in sorted(derived.items())}


def unmet_dependencies(
        opportunity: ResearchOpportunity,
        population: Iterable[ResearchOpportunity]) -> tuple[str, ...]:
    """
    Which of this item's dependencies are NOT YET RESOLVED in the population.

    A dependency is resolved only when the blocking research is formally
    COMPLETE -- i.e. the governed state that says it is no longer required. A
    dependency that is merely READY is NOT resolved: it is work that has not
    happened yet, so a dependent item may not bypass it and pretend otherwise.

    This is what makes dependency unlocking MECHANICAL: an opportunity is only
    executable when this is empty, and it becomes executable -- visibly, in a NEW
    agenda -- once a prior opportunity is no longer blocking. The old agenda is
    never rewritten.
    """
    by_identity = {item.opportunity_identity: item for item in population}
    unmet: list[str] = []
    for dependency in opportunity.depends_on:
        if not is_research_opportunity_identity(dependency):
            # A reference to research governed elsewhere is unresolved by
            # definition: Wave 5 cannot see it, so it must not assume it is done.
            unmet.append(dependency)
            continue
        blocker = by_identity.get(dependency)
        if blocker is None or blocker.state is not OpportunityState.COMPLETE:
            unmet.append(dependency)
    return tuple(sorted(unmet))


__all__ = [
    "COST_BAND_RANK",
    "COST_ESCALATION_THRESHOLD",
    "ConfirmationRequirement",
    "CostBand",
    "DataAvailability",
    "EXECUTABILITY_CLASS_ORDER",
    "EvidentialInsufficiency",
    "ExplanationDiscrimination",
    "INFORMATION_BAND_RANK",
    "INFORMATION_BAND_TABLE",
    "InformationBand",
    "InformationValue",
    "ObservationRequirement",
    "OpportunityState",
    "OpportunitySubjectKind",
    "ProspectiveWait",
    "QuestionResolution",
    "RESEARCH_OPPORTUNITY_ID_DIGEST_CHARS",
    "RESEARCH_OPPORTUNITY_ID_PREFIX",
    "RESEARCH_OPPORTUNITY_SCHEMA_VERSION",
    "ResearchAgendaError",
    "ResearchAnswerability",
    "ResearchCost",
    "ResearchDependencyCycleError",
    "ResearchOpportunity",
    "ResearchOpportunityNamespaceViolation",
    "ResearchOpportunityValidationError",
    "WAVE4_SUBJECT_KINDS",
    "assert_acyclic_dependencies",
    "canonical_opportunities",
    "dependency_depths",
    "is_research_opportunity_identity",
    "is_wave4_identity",
    "opportunity_state_from_eligibility",
    "research_opportunity_identity_for",
    "unlock_map",
    "unmet_dependencies",
]
