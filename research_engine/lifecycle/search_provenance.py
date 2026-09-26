"""
Search Provenance v1 -- immutable record of ONE governed multi-alternative search.

Stage 3 / Wave 4. Wave 3 lets curiosity react to ONE evidence signal. Wave 4
governs the case that actually creates the p-hacking failure mode: MULTIPLE
research alternatives were considered and one of them won.

THE CORE PRINCIPLE
==================
    THE SEARCH PROCESS IS PART OF THE SCIENTIFIC EVIDENCE.

    If N alternatives were considered in one discovery family, the system must
    retain N. The selected proposal must never be allowed to forget the
    alternatives that produced it.

WHAT THIS IS
------------
Four immutable, deterministically identified records:

    SearchAlternative  (ALT-)  ONE alternative that was genuinely considered
    SearchFamily       (FAM-)  the governed multiplicity problem itself
    SearchRecord       (SRC-)  ONE executed discovery event inside that family
    SelectionFreeze    (FRZ-)  the T0 freeze of what was selected

plus `DiscoveryPopulation` and `ConfirmationPolicy`, which keep the discovery
population and the confirmation population explicitly apart.

THE FAMILY EXISTS BEFORE THE WINNER
-----------------------------------
`SearchFamily` is derived from governed search SEMANTICS -- trigger, subject,
curiosity mode, parent research, search depth, discovery population, evidence
boundary, the governed alternative-space specification, the declared
multiplicity rule and the SET OF ALTERNATIVE IDENTITIES. It contains no
statistical result, no score and no winner. Two equivalent family
specifications resolve to the same family identity; a different discovery
population, a different boundary, a different search space or a different
alternative set resolves to a different one.

Because the family identity covers the alternative SET, a losing alternative can
never be dropped and still keep the same family identity.

THE EXACT MULTIPLICITY-ELIGIBILITY RULE
========================================
An alternative carries a recorded `evaluation` state that is INDEPENDENT of its
Wave 2 eligibility state, so eligibility classification can never erase a
tested loser and a pre-test refusal can never be relabelled as a statistical
loser:

    NOT_EVALUATED                        -- no statistical test was ever run
    STATISTICALLY_EVALUATED              -- a statistical test WAS run
    STATISTICALLY_EVALUATED_AND_SELECTED -- tested, and chosen

The family declares exactly one governed `MultiplicityRule`:

    STATISTICALLY_EVALUATED_ALTERNATIVES
        denominator = alternatives whose evaluation is not NOT_EVALUATED.
    ALL_CONSIDERED_ALTERNATIVES
        denominator = EVERY recorded alternative, including pre-test BLOCKED,
        WAITING_DATA and REFUSE alternatives.

Both are conservative, both are deterministic, and the rule is part of the
family identity, so it is frozen BEFORE selection and can never be swapped
after a winner is known.

Recorded Wave 2 outcomes are NEVER reinterpreted: a REFUSE alternative is
recorded REFUSE, a BLOCKED alternative is recorded BLOCKED, and a non-PERMIT
alternative is REQUIRED to be recorded NOT_EVALUATED -- a structural refusal
that claims to have been statistically tested is contradictory provenance and
fails closed.

NO SEARCH, NO AGENDA, NO RANKING (HARD INVARIANTS)
=================================================
This module enumerates nothing. It never reads the dimension registry to build a
search space, never forms a cartesian product, never loops over alternatives to
choose a "best" one and never ranks by expectancy, profit, win rate, Sharpe,
drawdown or p-value. It records the bounded family the governed research process
supplied. It is not the Wave 5 research agenda, and it is not an optimiser.

NO `GEN-*` IDENTITY
===================
`ALT-`, `FAM-`, `SRC-`, `FRZ-` and `MUL-` are Wave 4's own namespaces, disjoint
from `GEN-` and from every Wave 0-3 and canonical namespace. A search record is
NOT a generated research question, and no `GEN-*` identity is minted here.

NO PRODUCTION AUTHORITY
-----------------------
No runner, no orchestrator, no `GovernanceGate`, no execution, risk, sizing,
broker behaviour, production configuration, baseline activation or candidate
promotion is reachable from this module. It performs no I/O and writes nothing at
import time; the caller owns storage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.curiosity_proposal import (
    is_curiosity_proposal_identity,
)
from research_engine.lifecycle.dataset_fingerprint import DatasetFingerprint
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.progressive_depth_gate import EligibilityState
from research_engine.lifecycle.research_interaction import is_interaction_identity

# -- Versions (clean reset: starts at 1, never > 1) ---------------------------

SEARCH_PROVENANCE_SCHEMA_VERSION: int = 1

# -- Namespaces ---------------------------------------------------------------
#
# `ALT-` (alternative), `FAM-` (search family), `SRC-` (search record),
# `FRZ-` (selection freeze) and `MUL-` (multiplicity correction) are disjoint
# from `GEN-`, `CSN-`, `PRP-`, `DIM-`, `IXN-`, `SLC-`, `FSP-`, `EVD-`, `DEC-`,
# `EXP-` and from every canonical programme prefix. None of them is a research
# question and none of them is ever a `GEN-*` identity.

SEARCH_ALTERNATIVE_ID_PREFIX = "ALT-"
SEARCH_FAMILY_ID_PREFIX = "FAM-"
SEARCH_RECORD_ID_PREFIX = "SRC-"
SELECTION_FREEZE_ID_PREFIX = "FRZ-"
MULTIPLICITY_ID_PREFIX = "MUL-"

_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._:-]*$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

_ALT_ID_RE = re.compile(
    rf"^{re.escape(SEARCH_ALTERNATIVE_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_FAM_ID_RE = re.compile(
    rf"^{re.escape(SEARCH_FAMILY_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_SRC_ID_RE = re.compile(
    rf"^{re.escape(SEARCH_RECORD_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_FRZ_ID_RE = re.compile(
    rf"^{re.escape(SELECTION_FREEZE_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_MUL_ID_RE = re.compile(
    rf"^{re.escape(MULTIPLICITY_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")


# -- Errors (fail closed, never silently repaired) ---------------------------


class SearchProvenanceError(RuntimeError):
    """Base failure for the Wave 4 search-provenance layer."""


class SearchProvenanceValidationError(SearchProvenanceError):
    """Search / family / alternative / freeze material is invalid or incomplete."""


class SearchCompletenessError(SearchProvenanceValidationError):
    """The record cannot prove what was actually searched."""


class SearchIdentityConflict(SearchProvenanceError):
    """A presented search identity already exists with different immutable semantics."""


# -- Multiplicity rule --------------------------------------------------------


class MultiplicityRule(str, Enum):
    """
    The CLOSED set of governed Bonferroni-denominator rules.

    This is an identity taxonomy, not a preference. A caller declares ONE rule per
    family, and the rule is part of the family identity, so it is frozen before
    selection and cannot be changed because a winner would otherwise fail
    correction.
    """

    #: Denominator = alternatives that were actually statistically screened.
    STATISTICALLY_EVALUATED_ALTERNATIVES = "STATISTICALLY_EVALUATED_ALTERNATIVES"
    #: Denominator = EVERY alternative recorded as considered in the family.
    ALL_CONSIDERED_ALTERNATIVES = "ALL_CONSIDERED_ALTERNATIVES"


class AlternativeEvaluation(str, Enum):
    """
    How far ONE alternative actually got, recorded independently of Wave 2.

    This is deliberately NOT derived from `eligibility_state`. Deriving it would
    re-introduce exactly the failure Wave 4 exists to prevent: an alternative
    classified BLOCKED before any test would silently drop out of the correction
    denominator, and a statistically evaluated loser classified after testing
    would be erased.
    """

    #: No statistical test was ever run on this alternative.
    NOT_EVALUATED = "NOT_EVALUATED"
    #: A statistical test WAS run; the alternative was not selected.
    STATISTICALLY_EVALUATED = "STATISTICALLY_EVALUATED"
    #: A statistical test WAS run and this alternative was selected.
    STATISTICALLY_EVALUATED_AND_SELECTED = "STATISTICALLY_EVALUATED_AND_SELECTED"


#: Evaluation states that mean "a statistical test was actually run".
EVALUATED_STATES: tuple[AlternativeEvaluation, ...] = (
    AlternativeEvaluation.STATISTICALLY_EVALUATED,
    AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
)

#: The Wave 2 states that are structurally decided BEFORE any statistical test.
PRE_TEST_ELIGIBILITY_STATES: tuple[EligibilityState, ...] = (
    EligibilityState.BLOCKED,
    EligibilityState.WAITING_DATA,
    EligibilityState.REFUSE,
)



# -- Canonical encoding helpers ----------------------------------------------


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise SearchProvenanceValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "search provenance semantic material").encode("utf-8")
    ).hexdigest()


def _identity_for(prefix: str, semantic_identity: str, label: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise SearchProvenanceValidationError(
            f"{label} must be a lowercase 64-character sha256 hex digest")
    return f"{prefix}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchProvenanceValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise SearchProvenanceValidationError(
            f"{label} must not have surrounding whitespace")
    return value


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise SearchProvenanceValidationError(
            f"{label} must be a governed '<kind>:<token>' reference, got {text!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REASON_RE.match(text):
        raise SearchProvenanceValidationError(
            f"{label} must be a CLOSED machine-readable SHOUTY_SNAKE_CASE code; "
            f"free text is not authority, got {text!r}")
    return text


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise SearchProvenanceValidationError(
            f"{label} must be a governed token, got {text!r}")
    return text


def _require_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchProvenanceValidationError(f"{label} must be an integer, got {value!r}")
    if value < 0:
        raise SearchProvenanceValidationError(
            f"{label} must not be negative, got {value!r}")
    return int(value)


def _reasons(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise SearchProvenanceValidationError(f"{label} must be a sequence of codes")
    codes = [_require_reason(item, f"{label} entry") for item in values]
    if len(set(codes)) != len(codes):
        raise SearchProvenanceValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(codes))


def _refs(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise SearchProvenanceValidationError(f"{label} must be a sequence of references")
    refs = [_require_reference(item, f"{label} entry") for item in values]
    if len(set(refs)) != len(refs):
        raise SearchProvenanceValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(refs))


# -- Namespace predicates -----------------------------------------------------


def is_alternative_identity(value: Any) -> bool:
    """True only for IDs inside the reserved search-alternative namespace."""
    return isinstance(value, str) and bool(_ALT_ID_RE.match(value))


def is_search_family_identity(value: Any) -> bool:
    """True only for IDs inside the reserved search-family namespace."""
    return isinstance(value, str) and bool(_FAM_ID_RE.match(value))


def is_search_record_identity(value: Any) -> bool:
    """True only for IDs inside the reserved search-record namespace."""
    return isinstance(value, str) and bool(_SRC_ID_RE.match(value))


def is_selection_freeze_identity(value: Any) -> bool:
    """True only for IDs inside the reserved selection-freeze namespace."""
    return isinstance(value, str) and bool(_FRZ_ID_RE.match(value))


def is_multiplicity_identity(value: Any) -> bool:
    """True only for IDs inside the reserved multiplicity-correction namespace."""
    return isinstance(value, str) and bool(_MUL_ID_RE.match(value))


def alternative_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        SEARCH_ALTERNATIVE_ID_PREFIX, semantic_identity, "alternative semantic identity")


def search_family_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        SEARCH_FAMILY_ID_PREFIX, semantic_identity, "search family semantic identity")


def search_record_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        SEARCH_RECORD_ID_PREFIX, semantic_identity, "search record semantic identity")


def selection_freeze_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        SELECTION_FREEZE_ID_PREFIX, semantic_identity, "selection freeze semantic identity")


def multiplicity_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        MULTIPLICITY_ID_PREFIX, semantic_identity, "multiplicity semantic identity")


# -- Discovery / confirmation populations -------------------------------------


@dataclass(frozen=True)
class DiscoveryPopulation:
    """
    The FROZEN discovery population a search inspected alternatives on.

    A selected proposal may never later claim this same evidence as untouched
    confirmation evidence, so the population, its boundary and its dataset
    fingerprint are all mandatory and all part of the search identity.

    The `DatasetFingerprint` projection used for identity deliberately EXCLUDES
    `generated_timestamp`: when the fingerprint was computed is provenance, not
    science, so rebuilding the same population later cannot mint a second search.
    """

    population_identity: str
    evidence_boundary: str
    dataset_fingerprint: DatasetFingerprint
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self._validate()
        _encode(self.semantic_material(), "discovery population semantic material")

    # -- Identity -----------------------------------------------------------
    def dataset_fingerprint_material(self) -> dict[str, Any]:
        """The deterministic subset of the fingerprint used for scientific identity."""
        fingerprint = self.dataset_fingerprint
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
        }

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "discovery_population",
            "schema_version": self.schema_version,
            "population_identity": self.population_identity,
            "evidence_boundary": self.evidence_boundary,
            "dataset_fingerprint": self.dataset_fingerprint_material(),
        }

    def fingerprint_identity(self) -> str:
        """The frozen discovery fingerprint identity, bound by the T0 freeze."""
        return _digest({
            "kind": "discovery_dataset_fingerprint",
            **self.dataset_fingerprint_material(),
        })

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "DiscoveryPopulation":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"discovery population schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        _require_reference(self.population_identity, "discovery population_identity")
        _require_text(self.evidence_boundary, "discovery evidence_boundary")
        if not isinstance(self.dataset_fingerprint, DatasetFingerprint):
            raise SearchCompletenessError(
                "discovery dataset_fingerprint is mandatory and must be a "
                f"DatasetFingerprint, got {type(self.dataset_fingerprint).__name__}; "
                "a search may never run on unfingerprinted discovery evidence")
        return self

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "dataset_fingerprint": self.dataset_fingerprint.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiscoveryPopulation":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted discovery population must be a mapping")
        expected = {
            "kind", "schema_version", "population_identity", "evidence_boundary",
            "dataset_fingerprint",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise SearchProvenanceValidationError(
                "persisted discovery population fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "discovery_population":
            raise SearchProvenanceValidationError(
                "persisted discovery population kind must be 'discovery_population', "
                f"got {data['kind']!r}")
        fingerprint = data["dataset_fingerprint"]
        if not isinstance(fingerprint, Mapping):
            raise SearchCompletenessError(
                "persisted discovery dataset_fingerprint is mandatory")
        return cls(
            population_identity=data["population_identity"],
            evidence_boundary=data["evidence_boundary"],
            dataset_fingerprint=DatasetFingerprint.from_dict(dict(fingerprint)),
            schema_version=data["schema_version"],
        )


class ConfirmationKind(str, Enum):
    """Where confirmation evidence for a selected proposal may come from."""

    #: A genuinely untouched holdout population exists.
    EXISTING_UNTOUCHED_HOLDOUT = "EXISTING_UNTOUCHED_HOLDOUT"
    #: No holdout exists; confirmation must be prospective evidence after T0.
    PROSPECTIVE_AFTER_T0 = "PROSPECTIVE_AFTER_T0"
    #: No confirmation route has been established at all.
    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"


@dataclass(frozen=True)
class ConfirmationPolicy:
    """
    The declared confirmation route for a selected proposal.

    A holdout population may only be named when the caller asserts it was never
    touched by the discovery search. Naming the DISCOVERY population as a holdout
    is rejected. When no untouched holdout exists the honest answer is
    `PROSPECTIVE_AFTER_T0`: that is a valid record, not a failure, and no holdout
    is ever fabricated here.
    """

    kind: ConfirmationKind
    population_identity: str | None = None
    reference: str = ""
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.kind, str) and not isinstance(self.kind, ConfirmationKind):
            object.__setattr__(self, "kind", ConfirmationKind(self.kind))
        self._validate()
        _encode(self.semantic_material(), "confirmation policy semantic material")

    @property
    def requires_prospective_evidence(self) -> bool:
        return self.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0

    def _validate(self) -> "ConfirmationPolicy":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"confirmation policy schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if self.kind is ConfirmationKind.EXISTING_UNTOUCHED_HOLDOUT:
            if not self.population_identity:
                raise SearchProvenanceValidationError(
                    "an untouched holdout confirmation must name its population identity")
            _require_reference(self.population_identity, "confirmation population_identity")
            if not self.reference:
                raise SearchProvenanceValidationError(
                    "an untouched holdout confirmation must carry a governed reference "
                    "to the evidence proving the holdout was untouched")
        else:
            if self.population_identity is not None:
                raise SearchProvenanceValidationError(
                    f"{self.kind.value} confirmation may not name a confirmation "
                    "population; a holdout is never fabricated")
            if self.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0 and not self.reference:
                raise SearchProvenanceValidationError(
                    "prospective confirmation must reference the T0 boundary after which "
                    "new evidence may be collected")
        if self.reference:
            _require_reference(self.reference, "confirmation reference")
        return self

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "confirmation_policy",
            "schema_version": self.schema_version,
            "confirmation_kind": self.kind.value,
            "confirmation_population_identity": self.population_identity,
            "confirmation_reference": self.reference,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.semantic_material()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConfirmationPolicy":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted confirmation policy must be a mapping")
        expected = {
            "kind", "schema_version", "confirmation_kind",
            "confirmation_population_identity", "confirmation_reference",
        }
        if expected - set(data) or set(data) - expected:
            raise SearchProvenanceValidationError(
                "persisted confirmation policy fields are not exact")
        return cls(
            kind=ConfirmationKind(data["confirmation_kind"]),
            population_identity=data["confirmation_population_identity"],
            reference=data["confirmation_reference"],
            schema_version=data["schema_version"],
        )

    def assert_disjoint_from(self, discovery: DiscoveryPopulation) -> "ConfirmationPolicy":
        """A claimed untouched holdout may never be the discovery population."""
        if (self.kind is ConfirmationKind.EXISTING_UNTOUCHED_HOLDOUT
                and self.population_identity == discovery.population_identity):
            raise SearchCompletenessError(
                "the confirmation population is the discovery population; evidence "
                "already inspected during discovery can never be untouched confirmation "
                "evidence")
        return self


# -- The alternative ----------------------------------------------------------


@dataclass(frozen=True)
class SearchAlternative:
    """
    ONE alternative that was genuinely considered inside a governed search family.

    A losing, null, BLOCKED, WAITING_DATA or REFUSE alternative is recorded with
    exactly the same weight as the winner. An alternative is NEVER a `GEN-*`
    research question: search alternatives are not generated research identities,
    and no research question is minted for an alternative that lost.

    Identity material (hashed into `alternative_identity`) is the PRE-REGISTRATION
    material only: subject, proposed dimension / interaction / component, the Wave
    3 proposal identity it produced, and the evidence reference. Outcomes
    (eligibility state, decision identity, evaluation state, exclusion reasons)
    are recorded on the alternative but are deliberately NOT part of its identity,
    so re-classifying an alternative can never silently rewrite history.
    """

    subject_kind: str
    subject_ref: str
    proposed_dimension_identity: str | None = None
    proposed_interaction_identity: str | None = None
    proposed_component_ref: str | None = None
    proposal_identity: str | None = None
    evidence_reference: str = ""
    evidence_boundary: str = ""
    # -- Recorded outcomes (NOT identity material) --------------------------
    eligibility_state: EligibilityState | None = None
    decision_identity: str | None = None
    evaluation: AlternativeEvaluation = AlternativeEvaluation.NOT_EVALUATED
    exclusion_reason_codes: tuple[str, ...] = ()
    depth: int | None = None
    note: str = ""                 # provenance only; NOT identity
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION
    semantic_identity: str = ""
    alternative_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.eligibility_state, str):
            object.__setattr__(
                self, "eligibility_state", EligibilityState(self.eligibility_state))
        if isinstance(self.evaluation, str) and not isinstance(
                self.evaluation, AlternativeEvaluation):
            object.__setattr__(
                self, "evaluation", AlternativeEvaluation(self.evaluation))
        object.__setattr__(
            self, "exclusion_reason_codes", _reasons(
                self.exclusion_reason_codes, "exclusion_reason_codes"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = alternative_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise SearchProvenanceValidationError(
                    "presented alternative semantic identity does not match the "
                    "alternative material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.alternative_identity:
            if self.alternative_identity != expected_id:
                raise SearchProvenanceValidationError(
                    f"presented alternative identity {self.alternative_identity!r} does "
                    "not match the alternative material")
        else:
            object.__setattr__(self, "alternative_identity", expected_id)

    @classmethod
    def create(cls, **kwargs: Any) -> "SearchAlternative":
        """Build an alternative. Identity is derived, never supplied."""
        return cls(**kwargs)

    # -- Views --------------------------------------------------------------
    @property
    def is_evaluated(self) -> bool:
        """Whether a statistical test was actually run on this alternative."""
        return self.evaluation in EVALUATED_STATES

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "search_alternative",
            "schema_version": self.schema_version,
            "subject": {"kind": self.subject_kind, "ref": self.subject_ref},
            "proposed_dimension_identity": self.proposed_dimension_identity,
            "proposed_interaction_identity": self.proposed_interaction_identity,
            "proposed_component_ref": self.proposed_component_ref,
            "proposal_identity": self.proposal_identity,
            "evidence_reference": self.evidence_reference,
            "evidence_boundary": self.evidence_boundary,
        }

    def outcome_material(self) -> dict[str, Any]:
        """The recorded outcome, which IS part of the search record identity."""
        return {
            "alternative_identity": self.alternative_identity,
            "eligibility_state": (
                self.eligibility_state.value if self.eligibility_state is not None else None),
            "decision_identity": self.decision_identity,
            "evaluation": self.evaluation.value,
            "exclusion_reason_codes": list(self.exclusion_reason_codes),
            "depth": self.depth,
        }

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "SearchAlternative":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"search alternative schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        _require_token(self.subject_kind, "alternative subject_kind")
        _require_text(self.subject_ref, "alternative subject_ref")
        _require_reference(self.evidence_reference, "alternative evidence_reference")
        _require_text(self.evidence_boundary, "alternative evidence_boundary")
        if self.proposed_dimension_identity is not None and not is_dimension_identity(
                self.proposed_dimension_identity):
            raise SearchProvenanceValidationError(
                "proposed_dimension_identity must be a governed Wave 1 dimension identity, "
                f"got {self.proposed_dimension_identity!r}")
        if self.proposed_interaction_identity is not None and not is_interaction_identity(
                self.proposed_interaction_identity):
            raise SearchProvenanceValidationError(
                "proposed_interaction_identity must be a governed Wave 1 interaction "
                f"identity, got {self.proposed_interaction_identity!r}")
        if self.proposed_component_ref is not None:
            _require_reference(self.proposed_component_ref, "proposed_component_ref")
        if self.proposal_identity is not None and not is_curiosity_proposal_identity(
                self.proposal_identity):
            raise SearchProvenanceValidationError(
                "proposal_identity must be a governed Wave 3 proposal identity, got "
                f"{self.proposal_identity!r}; a search alternative is not a research "
                "question and may not carry a `GEN-*` identity")
        if not isinstance(self.evaluation, AlternativeEvaluation):
            raise SearchProvenanceValidationError(
                f"evaluation must be an AlternativeEvaluation, got {self.evaluation!r}")
        if self.eligibility_state is not None and not isinstance(
                self.eligibility_state, EligibilityState):
            raise SearchProvenanceValidationError(
                "eligibility_state must be a committed Wave 2 EligibilityState, got "
                f"{self.eligibility_state!r}")
        if self.depth is not None:
            _require_count(self.depth, "alternative depth")
            if self.depth < 1:
                raise SearchProvenanceValidationError("alternative depth must be >= 1")
        # A structural refusal is decided BEFORE any statistical test. Recording it
        # as evaluated would be contradictory provenance, and the record is
        # immutable: the distinction can never be repaired after the fact.
        if (self.eligibility_state in PRE_TEST_ELIGIBILITY_STATES
                and self.evaluation is not AlternativeEvaluation.NOT_EVALUATED):
            raise SearchCompletenessError(
                f"alternative recorded {self.eligibility_state.value} may not also be "
                f"recorded as {self.evaluation.value}: a structural Wave 2 refusal is "
                "decided before any statistical test and is never a statistical loser")
        if self.is_selected and self.eligibility_state is not EligibilityState.PERMIT:
            raise SearchCompletenessError(
                "only an authoritative Wave 2 PERMIT alternative may be selected; "
                f"got {self.eligibility_state}")
        _encode(self.semantic_material(), "search alternative semantic material")
        return self

    @property
    def is_selected(self) -> bool:
        return self.evaluation is AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED

    def counts_under(self, rule: MultiplicityRule) -> bool:
        """The EXACT, deterministic membership test for the Bonferroni denominator."""
        resolved = rule if isinstance(rule, MultiplicityRule) else MultiplicityRule(rule)
        if resolved is MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES:
            return True
        return self.is_evaluated

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "eligibility_state": (
                self.eligibility_state.value if self.eligibility_state is not None else None),
            "decision_identity": self.decision_identity,
            "evaluation": self.evaluation.value,
            "exclusion_reason_codes": list(self.exclusion_reason_codes),
            "depth": self.depth,
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "alternative_identity": self.alternative_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchAlternative":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted search alternative must be a mapping")
        expected = {
            "kind", "schema_version", "subject", "proposed_dimension_identity",
            "proposed_interaction_identity", "proposed_component_ref",
            "proposal_identity", "evidence_reference", "evidence_boundary",
            "eligibility_state", "decision_identity", "evaluation",
            "exclusion_reason_codes", "depth", "note", "semantic_identity",
            "alternative_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise SearchProvenanceValidationError(
                "persisted search alternative fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        subject = data["subject"]
        if not isinstance(subject, Mapping) or set(subject) != {"kind", "ref"}:
            raise SearchProvenanceValidationError(
                "persisted alternative subject must be {'kind','ref'}")
        reasons = data["exclusion_reason_codes"]
        if not isinstance(reasons, list):
            raise SearchProvenanceValidationError(
                "persisted exclusion_reason_codes must be a list")
        return cls(
            subject_kind=subject["kind"],
            subject_ref=subject["ref"],
            proposed_dimension_identity=data["proposed_dimension_identity"],
            proposed_interaction_identity=data["proposed_interaction_identity"],
            proposed_component_ref=data["proposed_component_ref"],
            proposal_identity=data["proposal_identity"],
            evidence_reference=data["evidence_reference"],
            evidence_boundary=data["evidence_boundary"],
            eligibility_state=(
                EligibilityState(data["eligibility_state"])
                if data["eligibility_state"] is not None else None),
            decision_identity=data["decision_identity"],
            evaluation=AlternativeEvaluation(data["evaluation"]),
            exclusion_reason_codes=tuple(reasons),
            depth=data["depth"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            alternative_identity=data["alternative_identity"],
        )


def _canonical_alternatives(alternatives: Any) -> tuple[SearchAlternative, ...]:
    if isinstance(alternatives, (str, bytes)) or not isinstance(
            alternatives, (list, tuple)):
        raise SearchCompletenessError(
            "alternatives must be a sequence of SearchAlternative")
    items = tuple(alternatives)
    if not items:
        raise SearchCompletenessError(
            "a governed search must record AT LEAST ONE alternative; a search with no "
            "recorded alternative cannot prove what was searched")
    for item in items:
        if not isinstance(item, SearchAlternative):
            raise SearchCompletenessError(
                f"expected SearchAlternative, got {type(item).__name__}")
    identities = [item.alternative_identity for item in items]
    duplicates = sorted({i for i in identities if identities.count(i) > 1})
    if duplicates:
        raise SearchCompletenessError(
            f"duplicate alternative identities in one search family: {duplicates}; a "
            "losing or repeated alternative may never be double-counted or collapsed")
    return tuple(sorted(items, key=lambda item: item.alternative_identity))


# -- The search family --------------------------------------------------------


@dataclass(frozen=True)
class SearchFamily:
    """
    The governed MULTIPLICITY PROBLEM: the set of alternatives that belong together.

    The family is derived from governed search semantics and the supplied bounded
    alternative space -- never from whichever alternative won:

        trigger / source identity
        research subject
        curiosity mode
        parent interaction / parent research
        search depth
        discovery population identity
        evidence boundary
        the governed alternative-space specification
        the declared multiplicity rule
        the SET OF ALTERNATIVE IDENTITIES

    Two equivalent family specifications resolve to the SAME family identity. A
    materially different discovery population, evidence boundary, search depth,
    curiosity mode, subject, trigger, parent, alternative space or alternative set
    resolves to a DIFFERENT family identity. Because the alternative set is part
    of the identity, a losing alternative can never be dropped and still keep the
    same family.

    This class does not decide which search to run, does not rank, and does not
    enumerate. It records the family the governed research process supplied.
    """

    trigger_ref: str
    trigger_identity: str
    curiosity_mode: str
    subject_kind: str
    subject_ref: str
    discovery_population_identity: str
    evidence_boundary: str
    search_depth: int
    alternative_space: Mapping[str, Any]
    multiplicity_rule: MultiplicityRule
    alternatives: tuple[SearchAlternative, ...]
    parent_interaction_identity: str | None = None
    parent_research_refs: tuple[str, ...] = ()
    label: str = ""                 # presentation only; NOT identity
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION
    semantic_identity: str = ""
    family_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.multiplicity_rule, str) and not isinstance(
                self.multiplicity_rule, MultiplicityRule):
            object.__setattr__(
                self, "multiplicity_rule", MultiplicityRule(self.multiplicity_rule))
        object.__setattr__(
            self, "alternatives", _canonical_alternatives(self.alternatives))
        object.__setattr__(
            self, "parent_research_refs", _refs(
                self.parent_research_refs, "parent_research_refs"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = search_family_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise SearchProvenanceValidationError(
                    "presented search family semantic identity does not match the "
                    "family material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.family_identity:
            if self.family_identity != expected_id:
                raise SearchProvenanceValidationError(
                    f"presented family identity {self.family_identity!r} does not match "
                    "the governed family material")
        else:
            object.__setattr__(self, "family_identity", expected_id)

    @classmethod
    def create(cls, **kwargs: Any) -> "SearchFamily":
        """Build a family. Identity is derived, never supplied."""
        return cls(**kwargs)

    # -- Views --------------------------------------------------------------
    @property
    def alternatives_considered(self) -> int:
        """Every alternative recorded as considered, winner included."""
        return len(self.alternatives)

    @property
    def multiplicity_eligible(self) -> tuple[SearchAlternative, ...]:
        """Exactly the alternatives that enter the Bonferroni denominator."""
        return tuple(
            item for item in self.alternatives
            if item.counts_under(self.multiplicity_rule))

    @property
    def derived_family_size(self) -> int:
        """
        The DERIVED multiplicity family size.

        This is the single authoritative derivation. It is a property of the
        RECORDED provenance under the DECLARED rule, so a caller can never assert
        `bonferroni_tests = 1` for a family of twenty eligible alternatives.
        """
        return len(self.multiplicity_eligible)

    @property
    def selected_alternatives(self) -> tuple[SearchAlternative, ...]:
        return tuple(item for item in self.alternatives if item.is_selected)

    def alternative_by_identity(self, identity: str) -> SearchAlternative:
        for item in self.alternatives:
            if item.alternative_identity == identity:
                return item
        raise SearchCompletenessError(
            f"alternative {identity!r} is not part of this search family")

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "search_family",
            "schema_version": self.schema_version,
            "trigger_ref": self.trigger_ref,
            "trigger_identity": self.trigger_identity,
            "curiosity_mode": self.curiosity_mode,
            "subject": {"kind": self.subject_kind, "ref": self.subject_ref},
            "parent_interaction_identity": self.parent_interaction_identity,
            "parent_research_refs": list(self.parent_research_refs),
            "search_depth": self.search_depth,
            "discovery_population_identity": self.discovery_population_identity,
            "evidence_boundary": self.evidence_boundary,
            "alternative_space": self.alternative_space,
            "multiplicity_rule": self.multiplicity_rule.value,
            # The SET of alternative identities: a losing alternative can never be
            # dropped and still keep the same family identity.
            "alternative_identities": sorted(
                item.alternative_identity for item in self.alternatives),
        }

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "SearchFamily":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"search family schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        _require_reference(self.trigger_ref, "family trigger_ref")
        _require_text(self.trigger_identity, "family trigger_identity")
        _require_token(self.curiosity_mode, "family curiosity_mode")
        _require_token(self.subject_kind, "family subject_kind")
        _require_text(self.subject_ref, "family subject_ref")
        _require_reference(
            self.discovery_population_identity, "family discovery_population_identity")
        _require_text(self.evidence_boundary, "family evidence_boundary")
        if isinstance(self.search_depth, bool) or not isinstance(self.search_depth, int):
            raise SearchProvenanceValidationError(
                f"search_depth must be an integer, got {self.search_depth!r}")
        if self.search_depth < 1:
            raise SearchProvenanceValidationError(
                f"search_depth must be >= 1, got {self.search_depth!r}")
        if not isinstance(self.multiplicity_rule, MultiplicityRule):
            raise SearchProvenanceValidationError(
                "multiplicity_rule must be a governed MultiplicityRule member")
        if not isinstance(self.alternative_space, Mapping) or not self.alternative_space:
            raise SearchCompletenessError(
                "a governed family must declare the alternative space / search "
                "specification it covers; an unbounded or unspecified family is "
                "incomplete provenance")
        if self.parent_interaction_identity is not None and not is_interaction_identity(
                self.parent_interaction_identity):
            raise SearchProvenanceValidationError(
                "parent_interaction_identity must be a governed Wave 1 interaction "
                f"identity, got {self.parent_interaction_identity!r}")

        for item in self.alternatives:
            # Contradictory subject or boundary across one family is unrepairable
            # provenance, not something to normalise away.
            if item.subject_kind != self.subject_kind or item.subject_ref != self.subject_ref:
                raise SearchCompletenessError(
                    f"alternative {item.alternative_identity} studies subject "
                    f"{item.subject_kind}:{item.subject_ref}, but the family studies "
                    f"{self.subject_kind}:{self.subject_ref}; contradictory subjects "
                    "fail closed")
            if item.evidence_boundary != self.evidence_boundary:
                raise SearchCompletenessError(
                    f"alternative {item.alternative_identity} carries evidence boundary "
                    f"{item.evidence_boundary!r}, but the family boundary is "
                    f"{self.evidence_boundary!r}; one family may not span two boundaries")
        interaction_depths = {
            item.depth for item in self.alternatives if item.depth is not None}
        if len(interaction_depths) > 1:
            raise SearchCompletenessError(
                f"one search family may not mix interaction depths: "
                f"{sorted(interaction_depths)}")
        if interaction_depths and next(iter(interaction_depths)) != self.search_depth:
            raise SearchCompletenessError(
                f"declared search_depth {self.search_depth} contradicts the recorded "
                f"alternative depth {next(iter(interaction_depths))}")
        _encode(self.semantic_material(), "search family semantic material")
        return self

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "alternatives": [item.to_dict() for item in self.alternatives],
            "label": self.label,
            "semantic_identity": self.semantic_identity,
            "family_identity": self.family_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchFamily":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted search family must be a mapping")
        expected = {
            "kind", "schema_version", "trigger_ref", "trigger_identity", "curiosity_mode",
            "subject", "parent_interaction_identity", "parent_research_refs", "search_depth",
            "discovery_population_identity", "evidence_boundary", "alternative_space",
            "multiplicity_rule", "alternative_identities", "alternatives", "label",
            "semantic_identity", "family_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise SearchProvenanceValidationError(
                "persisted search family fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "search_family":
            raise SearchProvenanceValidationError(
                f"persisted search family kind must be 'search_family', got {data['kind']!r}")
        subject = data["subject"]
        if not isinstance(subject, Mapping) or set(subject) != {"kind", "ref"}:
            raise SearchProvenanceValidationError(
                "persisted family subject must be {'kind','ref'}")
        rows = data["alternatives"]
        if not isinstance(rows, list):
            raise SearchCompletenessError("persisted family alternatives must be a list")
        space = data["alternative_space"]
        if not isinstance(space, Mapping):
            raise SearchCompletenessError("persisted alternative_space must be a mapping")
        parents = data["parent_research_refs"]
        if not isinstance(parents, list):
            raise SearchProvenanceValidationError(
                "persisted parent_research_refs must be a list")
        return cls(
            trigger_ref=data["trigger_ref"],
            trigger_identity=data["trigger_identity"],
            curiosity_mode=data["curiosity_mode"],
            subject_kind=subject["kind"],
            subject_ref=subject["ref"],
            discovery_population_identity=data["discovery_population_identity"],
            evidence_boundary=data["evidence_boundary"],
            search_depth=data["search_depth"],
            alternative_space=dict(space),
            multiplicity_rule=MultiplicityRule(data["multiplicity_rule"]),
            alternatives=tuple(SearchAlternative.from_dict(row) for row in rows),
            parent_interaction_identity=data["parent_interaction_identity"],
            parent_research_refs=tuple(parents),
            label=data["label"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            family_identity=data["family_identity"],
        )


# -- The search record --------------------------------------------------------


@dataclass(frozen=True)
class SearchRecord:
    """
    The immutable, deterministically identified record of ONE executed search.

    It preserves, in scientific identity material:

        the governed search family (and therefore the alternative space and the
        declared multiplicity rule);
        the frozen discovery population, its evidence boundary and its dataset
        fingerprint;
        EVERY alternative considered, with the exact Wave 2 state it reached and
        whether it was statistically evaluated;
        the selection boundary (T0) and the explicitly recorded selection
        criterion;
        the parent research / finding / candidate provenance.

    NOT identity material: `note` and `created_at`. A timestamp is provenance and
    can never make two scientifically equivalent searches different.
    """

    family: SearchFamily
    discovery: DiscoveryPopulation
    selected_proposal_identities: tuple[str, ...] = ()
    selection_boundary: str = ""
    selection_criterion: str = ""
    claimed_family_size: int | None = None
    parent_research_refs: tuple[str, ...] = ()
    note: str = ""                 # provenance only; NOT identity
    created_at: str = ""           # provenance only; NOT identity
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION
    semantic_identity: str = ""
    search_identity: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, SearchFamily):
            raise SearchProvenanceValidationError(
                f"expected SearchFamily, got {type(self.family).__name__}")
        if not isinstance(self.discovery, DiscoveryPopulation):
            raise SearchProvenanceValidationError(
                f"expected DiscoveryPopulation, got {type(self.discovery).__name__}")
        object.__setattr__(
            self, "selected_proposal_identities", tuple(
                sorted(self.selected_proposal_identities)))
        if not self.selected_proposal_identities:
            # The recorded selection IS the family's own selected state unless the
            # caller states it explicitly -- and an explicitly stated selection must
            # still name alternatives that are actually inside the family.
            object.__setattr__(
                self, "selected_proposal_identities", tuple(sorted(
                    item.proposal_identity
                    for item in self.family.selected_alternatives
                    if item.proposal_identity is not None)))
        object.__setattr__(
            self, "parent_research_refs", _refs(
                self.parent_research_refs, "parent_research_refs"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = search_record_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise SearchProvenanceValidationError(
                    "presented search record semantic identity does not match the "
                    "recorded search material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.search_identity:
            if self.search_identity != expected_id:
                raise SearchProvenanceValidationError(
                    f"presented search identity {self.search_identity!r} does not match "
                    "the recorded search material")
        else:
            object.__setattr__(self, "search_identity", expected_id)

    @classmethod
    def create(cls, **kwargs: Any) -> "SearchRecord":
        """Build a search record. Identity is derived, never supplied."""
        return cls(**kwargs)

    # -- Views --------------------------------------------------------------
    @property
    def alternatives(self) -> tuple[SearchAlternative, ...]:
        """EVERY alternative recorded, winner included. Never filtered."""
        return self.family.alternatives

    @property
    def alternatives_considered(self) -> int:
        return self.family.alternatives_considered

    @property
    def multiplicity_eligible(self) -> tuple[SearchAlternative, ...]:
        return self.family.multiplicity_eligible

    @property
    def derived_family_size(self) -> int:
        """The single authoritative derived Bonferroni denominator."""
        return self.family.derived_family_size

    @property
    def family_identity(self) -> str:
        return self.family.family_identity

    @property
    def selected_alternatives(self) -> tuple[SearchAlternative, ...]:
        return self.family.selected_alternatives
    @property
    def has_selection(self) -> bool:
        return bool(self.selected_proposal_identities)



    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "search_record",
            "schema_version": self.schema_version,
            "family_identity": self.family.family_identity,
            "discovery": self.discovery.semantic_material(),
            "alternatives": [item.outcome_material() for item in self.alternatives],
            "selected_proposal_identities": list(self.selected_proposal_identities),
            "selection_boundary": self.selection_boundary,
            "selection_criterion": self.selection_criterion,
            "parent_research_refs": list(self.parent_research_refs),
        }

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "SearchRecord":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"search record schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")

        # -- Discovery / confirmation separation ---------------------------
        if self.discovery.evidence_boundary != self.family.evidence_boundary:
            raise SearchCompletenessError(
                f"discovery evidence boundary {self.discovery.evidence_boundary!r} does "
                f"not match the governed family boundary {self.family.evidence_boundary!r}; "
                "evidence may not be silently substituted")
        if (self.discovery.population_identity
                != self.family.discovery_population_identity):
            raise SearchCompletenessError(
                f"discovery population {self.discovery.population_identity!r} does not "
                f"match the governed family population "
                f"{self.family.discovery_population_identity!r}")

        # -- Selection must live inside the recorded family ----------------
        family_proposals = {
            item.proposal_identity for item in self.alternatives
            if item.proposal_identity is not None}
        for proposal_identity in self.selected_proposal_identities:
            if not is_curiosity_proposal_identity(proposal_identity):
                raise SearchProvenanceValidationError(
                    "a selected proposal must be a governed Wave 3 proposal identity, "
                    f"got {proposal_identity!r}")
            if proposal_identity not in family_proposals:
                raise SearchCompletenessError(
                    f"selected proposal {proposal_identity} is not present in the "
                    "recorded alternatives; a winner-only record cannot prove what was "
                    "searched and fails closed")

        if self.has_selection:
            if not self.selection_boundary:
                raise SearchCompletenessError(
                    "a selection must be frozen at an explicit T0 selection boundary")
            _require_text(self.selection_boundary, "selection_boundary")
            if not self.selection_criterion:
                raise SearchCompletenessError(
                    "a selection must record the criterion that produced it explicitly; "
                    "Wave 4 records the supplied outcome and never invents a criterion")
            _require_token(self.selection_criterion, "selection_criterion")
            marked = {
                item.proposal_identity for item in self.family.selected_alternatives
                if item.proposal_identity is not None}
            if marked != set(self.selected_proposal_identities):
                raise SearchCompletenessError(
                    "the recorded selection and the alternatives' own selected state "
                    f"disagree ({sorted(marked)} vs "
                    f"{sorted(self.selected_proposal_identities)})")
        elif self.selection_criterion:
            raise SearchCompletenessError(
                "a selection criterion may only be recorded when a selection exists")

        # -- No silently erased losers -------------------------------------
        for item in self.alternatives:
            if item.proposal_identity in self.selected_proposal_identities:
                continue
            if not item.exclusion_reason_codes:
                raise SearchCompletenessError(
                    f"non-selected alternative {item.alternative_identity} records no "
                    "reason for non-selection; a losing or null alternative may never "
                    "be retained without its exclusion provenance")

        # -- The caller may never lie about the denominator ----------------
        derived = self.derived_family_size
        if self.claimed_family_size is not None:
            claimed = _require_count(self.claimed_family_size, "claimed_family_size")
            if claimed != derived:
                raise SearchCompletenessError(
                    f"claimed multiplicity family size {claimed} contradicts the "
                    f"{derived} derived from {self.alternatives_considered} recorded "
                    "alternatives; the derived value is the only one that may be used")
        if derived < 1:
            raise SearchCompletenessError(
                "no alternative in this family is multiplicity-eligible, so no corrected "
                "threshold exists; a selection can never be corrected against an empty "
                "denominator")
        _encode(self.semantic_material(), "search record semantic material")
        return self

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "family": self.family.to_dict(),
            "discovery": self.discovery.to_dict(),
            "claimed_family_size": self.derived_family_size,
            "note": self.note,
            "created_at": self.created_at,
            # The record's OWN identity is persisted so a reload can prove the
            # persisted content was never rewritten after it was written.
            "semantic_identity": self.semantic_identity,
            "search_identity": self.search_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchRecord":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted search record must be a mapping")
        expected = {
            "kind", "schema_version", "family_identity", "discovery", "alternatives",
            "selected_proposal_identities", "selection_boundary", "selection_criterion",
            "parent_research_refs", "family", "claimed_family_size", "note", "created_at",
            "semantic_identity", "search_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise SearchProvenanceValidationError(
                "persisted search record fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "search_record":
            raise SearchProvenanceValidationError(
                f"persisted search record kind must be 'search_record', got {data['kind']!r}")
        family = SearchFamily.from_dict(data["family"])
        if data["family_identity"] != family.family_identity:
            raise SearchCompletenessError(
                "persisted family_identity does not match the persisted family")
        selected = data["selected_proposal_identities"]
        if not isinstance(selected, list):
            raise SearchProvenanceValidationError(
                "persisted selected_proposal_identities must be a list")
        parents = data["parent_research_refs"]
        if not isinstance(parents, list):
            raise SearchProvenanceValidationError(
                "persisted parent_research_refs must be a list")
        return cls(
            family=family,
            discovery=DiscoveryPopulation.from_dict(data["discovery"]),
            selected_proposal_identities=tuple(selected),
            selection_boundary=data["selection_boundary"],
            selection_criterion=data["selection_criterion"],
            claimed_family_size=data["claimed_family_size"],
            parent_research_refs=tuple(parents),
            note=data["note"],
            created_at=data["created_at"],
            schema_version=data["schema_version"],
            # Re-binding to the PERSISTED identities turns any post-write edit
            # into a hard failure instead of a silently rewritten search.
            semantic_identity=data["semantic_identity"],
            search_identity=data["search_identity"],
        )


# -- The T0 selection freeze --------------------------------------------------


@dataclass(frozen=True)
class SelectionFreeze:
    """
    The immutable T0 freeze of WHAT was selected out of a governed family.

    It binds, in scientific identity material: the search identity, the family
    identity, the selected proposal identities, the discovery population identity,
    the frozen discovery fingerprint, the discovery evidence boundary, the number
    of alternatives considered, the DERIVED multiplicity family size, the
    correction method and the confirmation policy.

    `selected_at` is provenance and is deliberately ABSENT from identity: a
    timestamp alone is never scientific identity, so re-freezing the same
    selection at a different moment yields the same freeze identity.

    Once frozen, the alternatives of that historical family cannot silently change:
    any change produces a different family identity and therefore a different
    freeze identity. A materially different search is a NEW search record, a NEW
    family and a NEW freeze.
    """

    search_identity: str
    family_identity: str
    selected_proposal_identities: tuple[str, ...]
    discovery_population_identity: str
    discovery_fingerprint_identity: str
    discovery_evidence_boundary: str
    alternatives_considered: int
    multiplicity_family_size: int
    correction_method: str
    confirmation_policy: ConfirmationPolicy
    selected_at: str = ""           # provenance only; NOT identity
    schema_version: int = SEARCH_PROVENANCE_SCHEMA_VERSION
    semantic_identity: str = ""
    freeze_identity: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_policy, ConfirmationPolicy):
            raise SearchProvenanceValidationError(
                "expected ConfirmationPolicy, got "
                f"{type(self.confirmation_policy).__name__}")
        object.__setattr__(
            self, "selected_proposal_identities",
            tuple(sorted(self.selected_proposal_identities)))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = selection_freeze_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise SearchProvenanceValidationError(
                    "presented selection freeze semantic identity does not match the "
                    "frozen selection material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.freeze_identity:
            if self.freeze_identity != expected_id:
                raise SearchProvenanceValidationError(
                    f"presented freeze identity {self.freeze_identity!r} does not match "
                    "the frozen selection material")
        else:
            object.__setattr__(self, "freeze_identity", expected_id)

    # -- Construction -------------------------------------------------------
    @classmethod
    def freeze_selection(
        cls,
        record: SearchRecord,
        *,
        correction_method: str,
        confirmation_policy: ConfirmationPolicy,
        selected_at: str = "",
    ) -> "SelectionFreeze":
        """
        Freeze the selection of a governed search record at T0.

        `correction_method` is recorded EXACTLY as supplied by the governed
        research process. Wave 4 does not choose a correction method, does not
        invent an alpha and does not rank anything; it binds what it was given to
        the derived family size.
        """
        if not isinstance(record, SearchRecord):
            raise SearchProvenanceValidationError(
                f"expected SearchRecord, got {type(record).__name__}")
        if not record.has_selection:
            raise SearchCompletenessError(
                "a search with no selected proposal has nothing to freeze")
        if not isinstance(confirmation_policy, ConfirmationPolicy):
            raise SearchProvenanceValidationError(
                "a selection freeze requires an explicit ConfirmationPolicy")
        confirmation_policy.assert_disjoint_from(record.discovery)
        return cls(
            search_identity=record.search_identity,
            family_identity=record.family_identity,
            selected_proposal_identities=record.selected_proposal_identities,
            discovery_population_identity=record.discovery.population_identity,
            discovery_fingerprint_identity=record.discovery.fingerprint_identity(),
            discovery_evidence_boundary=record.discovery.evidence_boundary,
            alternatives_considered=record.alternatives_considered,
            multiplicity_family_size=record.derived_family_size,
            correction_method=_require_token(correction_method, "correction_method"),
            confirmation_policy=confirmation_policy,
            selected_at=_require_text(selected_at, "selected_at") if selected_at else "",
        )

    # -- Views --------------------------------------------------------------
    @property
    def corrected_alpha(self) -> float:
        """The corrected significance threshold implied by the frozen family size."""
        return 0.05 / self.multiplicity_family_size

    def assert_belongs_to(self, record: SearchRecord) -> "SelectionFreeze":
        """A freeze may only be presented against the exact record it froze."""
        if not isinstance(record, SearchRecord):
            raise SearchProvenanceValidationError(
                f"expected SearchRecord, got {type(record).__name__}")
        if (self.search_identity != record.search_identity
                or self.family_identity != record.family_identity):
            raise SearchCompletenessError(
                "the selection freeze does not belong to this search record; "
                "alternatives may not change silently after a T0 freeze")
        if self.alternatives_considered != record.alternatives_considered:
            raise SearchCompletenessError(
                f"the freeze recorded {self.alternatives_considered} alternatives "
                f"but the search now records {record.alternatives_considered}")
        if self.multiplicity_family_size != record.derived_family_size:
            raise SearchCompletenessError(
                f"the freeze bound family size {self.multiplicity_family_size} but the "
                f"record now derives {record.derived_family_size}")
        return self

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "selection_freeze",
            "schema_version": self.schema_version,
            "search_identity": self.search_identity,
            "family_identity": self.family_identity,
            "selected_proposal_identities": list(self.selected_proposal_identities),
            "discovery_population_identity": self.discovery_population_identity,
            "discovery_fingerprint_identity": self.discovery_fingerprint_identity,
            "discovery_evidence_boundary": self.discovery_evidence_boundary,
            "alternatives_considered": self.alternatives_considered,
            "multiplicity_family_size": self.multiplicity_family_size,
            "correction_method": self.correction_method,
            "confirmation_policy": self.confirmation_policy.semantic_material(),
        }

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "SelectionFreeze":
        if self.schema_version != SEARCH_PROVENANCE_SCHEMA_VERSION:
            raise SearchProvenanceValidationError(
                f"selection freeze schema_version must be "
                f"{SEARCH_PROVENANCE_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if not is_search_record_identity(self.search_identity):
            raise SearchProvenanceValidationError(
                "search_identity must be a governed search-record identity, got "
                f"{self.search_identity!r}")
        if not is_search_family_identity(self.family_identity):
            raise SearchProvenanceValidationError(
                "family_identity must be a governed search-family identity, got "
                f"{self.family_identity!r}")
        if not self.selected_proposal_identities:
            raise SearchCompletenessError("a selection freeze must name a selection")
        for proposal_identity in self.selected_proposal_identities:
            if not is_curiosity_proposal_identity(proposal_identity):
                raise SearchProvenanceValidationError(
                    "a frozen selection must name governed Wave 3 proposal identities, "
                    f"got {proposal_identity!r}")
        _require_reference(
            self.discovery_population_identity, "discovery_population_identity")
        _require_text(
            self.discovery_evidence_boundary, "discovery_evidence_boundary")
        if not isinstance(self.discovery_fingerprint_identity, str) or not _HEX64_RE.match(
                self.discovery_fingerprint_identity):
            raise SearchCompletenessError(
                "the discovery dataset fingerprint identity is mandatory and must be a "
                "frozen sha256 identity")
        considered = _require_count(
            self.alternatives_considered, "alternatives_considered")
        size = _require_count(self.multiplicity_family_size, "multiplicity_family_size")
        if considered < 1:
            raise SearchCompletenessError(
                "alternatives_considered must be >= 1; a search with no alternative "
                "cannot be frozen")
        if size < 1:
            raise SearchCompletenessError(
                "multiplicity_family_size must be >= 1; it is derived from the recorded "
                "search provenance and is never zero")
        if size > considered:
            raise SearchCompletenessError(
                f"multiplicity family size {size} exceeds the {considered} recorded "
                "alternatives; the denominator can never be larger than the family")
        _require_token(self.correction_method, "correction_method")
        if self.selected_at:
            _require_text(self.selected_at, "selected_at")
        _encode(self.semantic_material(), "selection freeze semantic material")
        return self



    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "confirmation_policy": self.confirmation_policy.to_dict(),
            "selected_at": self.selected_at,
            "semantic_identity": self.semantic_identity,
            "freeze_identity": self.freeze_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SelectionFreeze":
        if not isinstance(data, Mapping):
            raise SearchProvenanceValidationError(
                "persisted selection freeze must be a mapping")
        expected = {
            "kind", "schema_version", "search_identity", "family_identity",
            "selected_proposal_identities", "discovery_population_identity",
            "discovery_fingerprint_identity", "discovery_evidence_boundary",
            "alternatives_considered", "multiplicity_family_size", "correction_method",
            "confirmation_policy", "selected_at", "semantic_identity", "freeze_identity",
        }
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        if missing or unknown:
            raise SearchProvenanceValidationError(
                "persisted selection freeze fields are not exact "
                f"(missing={missing}, unknown={unknown})")
        if data["kind"] != "selection_freeze":
            raise SearchProvenanceValidationError(
                f"persisted selection freeze kind must be 'selection_freeze', got "
                f"{data['kind']!r}")
        selected = data["selected_proposal_identities"]
        if not isinstance(selected, list):
            raise SearchProvenanceValidationError(
                "persisted selected_proposal_identities must be a list")
        return cls(
            search_identity=data["search_identity"],
            family_identity=data["family_identity"],
            selected_proposal_identities=tuple(selected),
            discovery_population_identity=data["discovery_population_identity"],
            discovery_fingerprint_identity=data["discovery_fingerprint_identity"],
            discovery_evidence_boundary=data["discovery_evidence_boundary"],
            alternatives_considered=data["alternatives_considered"],
            multiplicity_family_size=data["multiplicity_family_size"],
            correction_method=data["correction_method"],
            confirmation_policy=ConfirmationPolicy.from_dict(data["confirmation_policy"]),
            selected_at=data["selected_at"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            freeze_identity=data["freeze_identity"],
        )


# -- Generated-research provenance binding ------------------------------------


def search_provenance_payload(
    record: SearchRecord,
    freeze: SelectionFreeze,
) -> dict[str, Any]:
    """
    The deterministic `search_provenance` block a generated research question
    carries when it was selected out of a multi-alternative Wave 4 search.

    This is a plain mapping, deliberately shaped to be merged into the EXISTING
    Wave 0 specification/provenance extension points. No Wave 0 identity is
    redefined: a generated question that never came from a multi-alternative
    search simply has no `search_provenance` block and stays valid, and no search
    provenance is retroactively fabricated for it.
    """
    if not isinstance(record, SearchRecord):
        raise SearchProvenanceValidationError(
            f"expected SearchRecord, got {type(record).__name__}")
    if not isinstance(freeze, SelectionFreeze):
        raise SearchProvenanceValidationError(
            f"expected SelectionFreeze, got {type(freeze).__name__}")
    freeze.assert_belongs_to(record)
    return {
        "schema_version": SEARCH_PROVENANCE_SCHEMA_VERSION,
        "search_record_id": record.search_identity,
        "search_family_id": record.family_identity,
        "selection_freeze_id": freeze.freeze_identity,
        "alternatives_considered": record.alternatives_considered,
        "multiplicity_family_size": record.derived_family_size,
        "multiplicity_rule": record.family.multiplicity_rule.value,
        "correction_method": freeze.correction_method,
        "discovery_population_identity": record.discovery.population_identity,
        "discovery_fingerprint_identity": record.discovery.fingerprint_identity(),
        "discovery_evidence_boundary": record.discovery.evidence_boundary,
        "selection_criterion": record.selection_criterion,
        "confirmation_policy": freeze.confirmation_policy.semantic_material(),
    }


__all__ = [
    "AlternativeEvaluation",
    "ConfirmationKind",
    "ConfirmationPolicy",
    "DiscoveryPopulation",
    "EVALUATED_STATES",
    "MULTIPLICITY_ID_PREFIX",
    "MultiplicityRule",
    "PRE_TEST_ELIGIBILITY_STATES",
    "SEARCH_ALTERNATIVE_ID_PREFIX",
    "SEARCH_FAMILY_ID_PREFIX",
    "SEARCH_PROVENANCE_SCHEMA_VERSION",
    "SEARCH_RECORD_ID_PREFIX",
    "SELECTION_FREEZE_ID_PREFIX",
    "SearchAlternative",
    "SearchCompletenessError",
    "SearchFamily",
    "SearchIdentityConflict",
    "SearchProvenanceError",
    "SearchProvenanceValidationError",
    "SearchRecord",
    "SelectionFreeze",
    "alternative_identity_for",
    "is_alternative_identity",
    "is_multiplicity_identity",
    "is_search_family_identity",
    "is_search_record_identity",
    "is_selection_freeze_identity",
    "multiplicity_identity_for",
    "search_family_identity_for",
    "search_provenance_payload",
    "search_record_identity_for",
    "selection_freeze_identity_for",
]
