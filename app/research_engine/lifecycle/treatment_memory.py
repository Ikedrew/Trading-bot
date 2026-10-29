"""
Treatment Memory v1 -- governed, immutable memory of WHAT WAS INVESTIGATED.

Stage 3 / Wave 7. Waves 0-6 made a piece of research legitimate, worth attention
and precisely specified. None of them can answer the question that comes next:

    "Have we already done this?"

The failure this module exists to prevent is RESEARCH AMNESIA: the engine
rediscovers and reinvestigates a semantically identical idea because somebody
re-labelled it, re-noted it, or re-derived it three months later.

The failure it must NOT cause is RESEARCH DOGMA: an idea is permanently
suppressed because an earlier investigation rejected it, failed to confirm it,
or ran out of evidence. Rejection is a historical observation, not a permanent
verdict. Insufficient data is not failure. An invalid investigation is not
evidence against anything.

TERMINOLOGY (read this before using the word "treatment")
=======================================================
This repository already uses "treatment" for an EXECUTION-ORIENTED artefact:
`research_engine.lifecycle.treatment_provenance` freezes a `treatment_spec`
(`{change_type, declared, scope, treatment_id}`) validated against a candidate's
durable evaluation file, and `investigation_contracts` maps findings to
`direction` / `stop_multiplier` / `tp_multiplier` simulation parameters.

Neither is a memory of research: they describe what to DO to a candidate.

Therefore Wave 7 introduces a deliberately NON-EXECUTABLE descriptor:

    `TreatmentSignature` (TRS-*)    -- WHAT WAS BEING INVESTIGATED, semantically.
    `TreatmentMemoryRecord` (TMR-*) -- WHAT CONCLUDED, WHERE, AND WHY.

A `TreatmentSignature` REFERENCES existing governed semantics (Wave 1 `DIM-*` /
`IXN-*` / `SLC-*`, Wave 3 `PRP-*`, Wave 6 `RPL-*`) and reuses the existing
closed subject vocabulary. It is never executable authority, and this module
exposes no API that can apply a treatment, activate or promote a candidate, or
create an order.

MEMORY IS HISTORY, NOT TRUTH
============================
Nothing here answers whether a treatment is good. The public surface is
`historical_memories_for(...)`, `assess_memory_applicability(...)` and
`assess_memory_conflict(...)`. A memory states what a governed investigation
concluded inside a stated applicability envelope; it never establishes a
universal fact, and a later disagreement is preserved rather than resolved.

NO PROFITABILITY
================
No expected-return, win-rate, expectancy or "best treatment" concept exists
here and none may be introduced: outcome measures may appear only inside an
authoritative historical evidence REFERENCE, never as a criterion of memory
identity, applicability or suppression.

DETERMINISM
-----------
`semantic_identity` is the SHA-256 digest of the canonical JSON encoding of
`semantic_material()`. A treatment signature is pre-result, so NO outcome,
disposition, reason code, label, note, timestamp or provenance is part of it:
two differently worded treatments with identical governed semantics resolve to
the SAME `TRS-*`. A memory record is a historical observation, so its
disposition and reason codes ARE part of it -- two different conclusions about
the same treatment are two different memories, and both are kept.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from research_engine.lifecycle.curiosity_proposal import (
    is_curiosity_proposal_identity,
)
from research_engine.lifecycle.generated_research_identity import (
    canonical_json,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_QUESTION_IDS,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.progressive_depth_gate import is_decision_identity
from research_engine.lifecycle.research_interaction import (
    is_interaction_identity,
    is_slice_identity,
)
from research_engine.lifecycle.research_opportunity import OpportunitySubjectKind
from research_engine.lifecycle.research_protocol import (
    is_protocol_freeze_identity,
    is_research_protocol_identity,
)
from research_engine.lifecycle.search_provenance import (
    is_search_record_identity,
    is_selection_freeze_identity,
)

# -- Versions (clean reset: starts at 1, never above 1) ----------------------
TREATMENT_MEMORY_SCHEMA_VERSION: int = 1
TREATMENT_SIGNATURE_VERSION: int = 1

# -- Namespaces --------------------------------------------------------------
# `TRS-` (treatment signature) and `TMR-` (treatment memory record) are new,
# disjoint from every Wave 0-6 and canonical namespace (`GEN-`, `CSN-`, `PRP-`,
# `DIM-`, `IXN-`, `SLC-`, `FSP-`, `EVD-`, `DEC-`, `EXP-`, `ALT-`, `FAM-`,
# `SRC-`, `FRZ-`, `MUL-`, `ROP-`, `POL-`, `AGD-`, `AFR-`, `QUE-`, `RPL-`,
# `PFR-`, `OOD-`) and from every canonical programme prefix. A memory is never a
# research question and never a `GEN-*` identity.
TREATMENT_SIGNATURE_ID_PREFIX = "TRS-"
TREATMENT_MEMORY_ID_PREFIX = "TMR-"
_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TRS_ID_RE = re.compile(
    rf"^{re.escape(TREATMENT_SIGNATURE_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_TMR_ID_RE = re.compile(
    rf"^{re.escape(TREATMENT_MEMORY_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")

_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SEMANTIC_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# -- Errors (fail closed, never silently repaired) ---------------------------


class TreatmentMemoryError(RuntimeError):
    """Base failure for the governed treatment-memory layer."""


class TreatmentMemoryValidationError(TreatmentMemoryError):
    """Memory, signature or applicability material is invalid or malformed."""


class TreatmentMemoryNamespaceViolation(TreatmentMemoryError):
    """A presented identity is outside its reserved governed namespace."""


class TreatmentMemoryIdentityConflict(TreatmentMemoryError):
    """An identity already exists bound to different immutable semantics."""


# -- Identity helpers --------------------------------------------------------


def is_treatment_signature_identity(value: Any) -> bool:
    """True only for IDs inside the reserved treatment-signature namespace."""
    return isinstance(value, str) and bool(_TRS_ID_RE.match(value))


def is_treatment_memory_identity(value: Any) -> bool:
    """True only for IDs inside the reserved treatment-memory namespace."""
    return isinstance(value, str) and bool(_TMR_ID_RE.match(value))


def _identity_for(prefix: str, semantic_identity: str, label: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise TreatmentMemoryValidationError(
            f"{label} semantic identity must be a lowercase 64-character sha256 "
            f"hex digest, got {semantic_identity!r}")
    return f"{prefix}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def treatment_signature_identity_for(semantic_identity: str) -> str:
    return _identity_for(TREATMENT_SIGNATURE_ID_PREFIX, semantic_identity,
                         "treatment signature")


def treatment_memory_identity_for(semantic_identity: str) -> str:
    return _identity_for(TREATMENT_MEMORY_ID_PREFIX, semantic_identity,
                         "treatment memory")


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise TreatmentMemoryValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TreatmentMemoryValidationError(
            f"{label} must be a non-empty string")
    if value != value.strip():
        raise TreatmentMemoryValidationError(
            f"{label} must not have surrounding whitespace: {value!r}")
    return value


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise TreatmentMemoryValidationError(
            f"{label} must be a governed token, got {value!r}")
    return text


def _require_semantic_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _SEMANTIC_TOKEN_RE.match(text):
        raise TreatmentMemoryValidationError(
            f"{label} must be an UPPER_SNAKE governed semantic token, got {value!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REASON_RE.match(text):
        raise TreatmentMemoryValidationError(
            f"{label} must be an UPPER_SNAKE reason code, got {value!r}")
    return text


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise TreatmentMemoryValidationError(
            f"{label} must be a governed 'scheme:value' reference, got {value!r}")
    return text



def _optional_reference(value: Any, label: str) -> str:
    """An absent reference is an honest, explicit empty string, never a guess."""
    if value is None or value == "":
        return ""
    return _require_reference(value, label)


def _optional_identity(value: Any, predicate, label: str) -> str:
    """An absent governed identity is empty; a present one must be in-namespace."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not predicate(value):
        raise TreatmentMemoryValidationError(
            f"{label} {value!r} is not a governed identity in its namespace")
    return value


def _canonical_tokens(values: Any, label: str) -> tuple[str, ...]:
    """Sorted, duplicate-free governed tokens. Input order is never meaning."""
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TreatmentMemoryValidationError(
            f"{label} must be a sequence of governed tokens")
    items = [_require_token(item, f"{label} entry") for item in values]
    if len(set(items)) != len(items):
        raise TreatmentMemoryValidationError(
            f"{label} must not contain duplicates: {items!r}")
    return tuple(sorted(items))


def _canonical_reasons(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TreatmentMemoryValidationError(
            f"{label} must be a sequence of governed reason codes")
    items = [_require_reason(item, f"{label} entry") for item in values]
    if len(set(items)) != len(items):
        raise TreatmentMemoryValidationError(
            f"{label} must not contain duplicates: {items!r}")
    return tuple(sorted(items))


def _canonical_refs(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TreatmentMemoryValidationError(
            f"{label} must be a sequence of governed references")
    items = [_require_reference(item, f"{label} entry") for item in values]
    if len(set(items)) != len(items):
        raise TreatmentMemoryValidationError(
            f"{label} must not contain duplicates: {items!r}")
    return tuple(sorted(items))


def _canonical_identities(values: Any, predicate, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TreatmentMemoryValidationError(
            f"{label} must be a sequence of governed identities")
    items = [_optional_identity(item, predicate, f"{label} entry") for item in values]
    if any(not item for item in items):
        raise TreatmentMemoryValidationError(
            f"{label} must not contain empty identities")
    if len(set(items)) != len(items):
        raise TreatmentMemoryValidationError(
            f"{label} must not contain duplicates: {items!r}")
    return tuple(sorted(items))


#: The governed subject predicates Wave 7 REUSES from earlier waves. Wave 7
#: mints no replacement identity: a treatment signature may only be authored
#: against a subject Wave 0-6 already governs, which is what makes "the same
#: question, asked a second time" a mechanical comparison rather than a guess.
SUBJECT_PREDICATES: Mapping[OpportunitySubjectKind, Any] = {
    OpportunitySubjectKind.CANONICAL_QUESTION: (
        lambda value: isinstance(value, str) and value in CANONICAL_QUESTION_IDS),
    OpportunitySubjectKind.GENERATED_RESEARCH: is_generated_research_id,
    OpportunitySubjectKind.GOVERNED_DIMENSION: is_dimension_identity,
    OpportunitySubjectKind.RESEARCH_INTERACTION: is_interaction_identity,
    OpportunitySubjectKind.CURIOSITY_PROPOSAL: is_curiosity_proposal_identity,
    OpportunitySubjectKind.ELIGIBILITY_DECISION: is_decision_identity,
    OpportunitySubjectKind.SEARCH_RECORD: is_search_record_identity,
    OpportunitySubjectKind.SEARCH_SELECTION_FREEZE: is_selection_freeze_identity,
}




# == The governed vocabulary of WHAT A TREATMENT IS ==========================


class TreatmentComponent(str, Enum):
    """
    Which governed component of the research subject a treatment touches.

    A CLOSED set, not free text: widening the stop and inverting the direction
    are different treatments because they touch different components, and there
    is no way to invent a component name that quietly compares equal to an
    existing one.
    """

    STOP_GEOMETRY = "STOP_GEOMETRY"
    TARGET_GEOMETRY = "TARGET_GEOMETRY"
    ENTRY_GEOMETRY = "ENTRY_GEOMETRY"
    DIRECTION = "DIRECTION"
    TIMING = "TIMING"
    SELECTION = "SELECTION"
    SIZING = "SIZING"
    OBSERVATION = "OBSERVATION"


class ChangeDirection(str, Enum):
    """
    The DIRECTION of a change, where direction is meaningful.

    `NONE` is an honest member: a wider stop or a different horizon slice
    genuinely has no direction, and recording that explicitly is better than
    inventing a direction the treatment does not have.
    """

    NONE = "NONE"
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    INVERT = "INVERT"
    WIDEN = "WIDEN"
    NARROW = "NARROW"
    REPLACE = "REPLACE"


class TreatmentClass(str, Enum):
    """
    The governed CLASS of the investigated operation.

    This is a classification of what was investigated, never an instruction. No
    member of this enum carries behavioural meaning in Wave 7, and no API here
    lets one be executed, applied, promoted or traded.
    """

    GEOMETRY_MODIFICATION = "GEOMETRY_MODIFICATION"
    DIRECTION_INVERSION = "DIRECTION_INVERSION"
    CONDITIONING = "CONDITIONING"
    SELECTION_RESTRICTION = "SELECTION_RESTRICTION"
    COMPARISON_BASELINE = "COMPARISON_BASELINE"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"


class Horizon(str, Enum):
    """
    The governed horizon a treatment was investigated on.

    A closed set rather than a duration string, so the same treatment on a
    different horizon is a mechanically detectable difference rather than a
    judgement call.
    """

    SCALP = "SCALP"
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    POSITION = "POSITION"
    UNSPECIFIED = "UNSPECIFIED"


#: The governed parameter vocabulary. A closed key set is what makes "wider
#: stops" and "increase stop distance" MECHANICALLY the same treatment: both
#: must express the same governed quantity through the same governed key, so
#: there is no room for a synonym to fork the identity.
CHANGE_PARAMETER_TYPES: Mapping[str, tuple] = {
    "ENABLE": (bool,),
    "DIRECTION": (bool,),
    "MAX_HOLD_BARS": (int,),
    "STOP_MULTIPLIER": (float, int),
    "TARGET_MULTIPLIER": (float, int),
    "THRESHOLD": (float, int),
}

CHANGE_PARAMETER_KEYS: tuple[str, ...] = tuple(sorted(CHANGE_PARAMETER_TYPES))


def _canonical_change_parameters(values: Any) -> tuple:
    """
    Normalise a change-parameter map into a deterministic, ordered tuple.

    Accepts a mapping (the natural way to write a treatment) or an already
    canonical sequence of pairs. Either way the result is sorted by key,
    duplicate-free, and drawn from the CLOSED governed key vocabulary -- so two
    treatments written in a different order, or using a different value type for
    the same governed quantity, resolve identically or are rejected.
    """
    if values is None:
        return ()
    if isinstance(values, Mapping):
        pairs = list(values.items())
    elif isinstance(values, (list, tuple)):
        pairs = []
        for item in values:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise TreatmentMemoryValidationError(
                    "each change parameter must be a (key, value) pair")
            pairs.append((item[0], item[1]))
    else:
        raise TreatmentMemoryValidationError(
            "change_parameters must be a mapping or a sequence of "
            "(key, value) pairs")
    normalised = []
    seen = set()
    for key, value in pairs:
        if key not in CHANGE_PARAMETER_TYPES:
            raise TreatmentMemoryValidationError(
                f"change parameter {key!r} is outside the governed parameter "
                f"vocabulary {list(CHANGE_PARAMETER_KEYS)}")
        if key in seen:
            raise TreatmentMemoryValidationError(
                f"change parameter {key!r} is declared twice")
        allowed = CHANGE_PARAMETER_TYPES[key]
        # `bool` is a subclass of `int`, so it is checked FIRST: a boolean
        # parameter may never be written as 0/1, and a numeric parameter may
        # never be written as a boolean.
        if bool not in allowed and isinstance(value, bool):
            raise TreatmentMemoryValidationError(
                f"change parameter {key!r} must be of type "
                f"{[item.__name__ for item in allowed]}, got a boolean")
        if not isinstance(value, allowed):
            raise TreatmentMemoryValidationError(
                f"change parameter {key!r} must be of type "
                f"{[item.__name__ for item in allowed]}, got {value!r}")
        if isinstance(value, float) and (value != value or value in (
                float("inf"), float("-inf"))):
            raise TreatmentMemoryValidationError(
                f"change parameter {key!r} must be a finite number, got {value!r}")
        seen.add(key)
        normalised.append((key, value))
    return tuple(sorted(normalised))



# == The treatment signature (TRS-*) =========================================


@dataclass(frozen=True)
class TreatmentSignature:
    """
    A deterministic, NON-EXECUTABLE description of WHAT WAS INVESTIGATED.

    IDENTITY MATERIAL (hashed): signature version, subject kind/ref, component,
    the canonical change parameters, change direction, baseline semantics,
    alternative semantics, treatment class, horizon, the governed dimension /
    interaction / slice identities, the applicable population, and the parent
    proposal / protocol identities.

    NOT IDENTITY MATERIAL (provenance only): `label`, `note`, `provenance` and
    `created_at`.

    Consequently, and this is the entire anti-amnesia point:

      - "wider stops" and "increase stop distance" produce the SAME `TRS-*`,
        because a label is not a semantic;
      - a changed component, parameter, direction, baseline, alternative,
        horizon, dimension, interaction, slice, population or class produces a
        DIFFERENT `TRS-*`, because those ARE the semantics.

    A signature is written BEFORE any result exists, so it carries no outcome,
    no disposition, no reason code and no observation. That asymmetry against
    `TreatmentMemoryRecord` is deliberate: the signature is the question, the
    memory is the answer.
    """

    subject_kind: OpportunitySubjectKind
    subject_ref: str
    component: TreatmentComponent
    change_parameters: tuple
    baseline_semantics: str
    alternative_semantics: str
    treatment_class: TreatmentClass
    direction: ChangeDirection = ChangeDirection.NONE
    horizon: Horizon = Horizon.UNSPECIFIED
    dimension_identity: str = ""
    interaction_identity: str = ""
    slice_identity: str = ""
    population_reference: str = ""
    asset_family: str = ""
    symbols: tuple = ()
    parent_proposal_identity: str = ""
    parent_protocol_identity: str = ""
    label: str = ""                        # provenance only; NOT identity
    note: str = ""                         # provenance only; NOT identity
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""                   # provenance only; NOT identity
    signature_version: int = TREATMENT_SIGNATURE_VERSION
    schema_version: int = TREATMENT_MEMORY_SCHEMA_VERSION
    semantic_identity: str = ""
    signature_identity: str = ""

    def __post_init__(self) -> None:
        for name, member in (
            ("subject_kind", OpportunitySubjectKind),
            ("component", TreatmentComponent),
            ("direction", ChangeDirection),
            ("treatment_class", TreatmentClass),
            ("horizon", Horizon),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and not isinstance(value, member):
                object.__setattr__(self, name, member(value))
        object.__setattr__(self, "change_parameters",
                           _canonical_change_parameters(self.change_parameters))
        object.__setattr__(self, "baseline_semantics",
                           _require_semantic_token(self.baseline_semantics,
                                                   "baseline_semantics"))
        object.__setattr__(self, "alternative_semantics",
                           _require_semantic_token(self.alternative_semantics,
                                                   "alternative_semantics"))
        object.__setattr__(self, "symbols",
                           _canonical_tokens(self.symbols, "symbols"))
        object.__setattr__(self, "dimension_identity", _optional_identity(
            self.dimension_identity, is_dimension_identity, "dimension_identity"))
        object.__setattr__(self, "interaction_identity", _optional_identity(
            self.interaction_identity, is_interaction_identity,
            "interaction_identity"))
        object.__setattr__(self, "slice_identity", _optional_identity(
            self.slice_identity, is_slice_identity, "slice_identity"))
        object.__setattr__(self, "population_reference", _optional_reference(
            self.population_reference, "population_reference"))
        object.__setattr__(self, "parent_proposal_identity", _optional_identity(
            self.parent_proposal_identity, is_curiosity_proposal_identity,
            "parent_proposal_identity"))
        object.__setattr__(self, "parent_protocol_identity", _optional_identity(
            self.parent_protocol_identity, is_research_protocol_identity,
            "parent_protocol_identity"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = treatment_signature_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise TreatmentMemoryValidationError(
                    "presented treatment signature semantic identity does not "
                    "match the signature material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.signature_identity:
            if self.signature_identity != expected_id:
                raise TreatmentMemoryValidationError(
                    f"presented treatment signature identity "
                    f"{self.signature_identity!r} does not match the signature "
                    f"material")
        else:
            object.__setattr__(self, "signature_identity", expected_id)


    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "TreatmentSignature":
        if self.signature_version != TREATMENT_SIGNATURE_VERSION:
            raise TreatmentMemoryValidationError(
                f"treatment signature version must remain "
                f"{TREATMENT_SIGNATURE_VERSION} (clean reset), got "
                f"{self.signature_version!r}")
        if self.schema_version != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise TreatmentMemoryValidationError(
                f"treatment signature schema_version must be "
                f"{TREATMENT_MEMORY_SCHEMA_VERSION}, got {self.schema_version!r}")
        _require_text(self.subject_ref, "subject_ref")
        if not SUBJECT_PREDICATES[self.subject_kind](self.subject_ref):
            raise TreatmentMemoryValidationError(
                f"subject_ref {self.subject_ref!r} is not a governed "
                f"{self.subject_kind.value} identity; a treatment signature may "
                f"never be authored from an ungoverned subject")
        if (self.component is TreatmentComponent.OBSERVATION
                and self.change_parameters):
            raise TreatmentMemoryValidationError(
                "an OBSERVATION component declares no change parameters; a "
                "parameterised treatment is not an observation")
        if (self.component is TreatmentComponent.OBSERVATION
                and self.direction is not ChangeDirection.NONE):
            raise TreatmentMemoryValidationError(
                "an OBSERVATION component has no change direction")
        if (self.component is TreatmentComponent.DIRECTION
                and self.direction is ChangeDirection.NONE):
            raise TreatmentMemoryValidationError(
                "a DIRECTION component must declare a governed ChangeDirection; "
                "having no direction is only honest for a component that has none")
        if self.parent_protocol_identity and not self.parent_proposal_identity:
            raise TreatmentMemoryValidationError(
                "a treatment signature that names a parent protocol must also "
                "name the parent proposal it was raised from")
        if self.horizon is Horizon.UNSPECIFIED and not (
                self.dimension_identity or self.slice_identity):
            raise TreatmentMemoryValidationError(
                "a treatment signature must be anchored to at least one governed "
                "scope element: a dimension identity, a slice identity, or a "
                "specified horizon")
        if self.asset_family:
            _require_semantic_token(self.asset_family, "asset_family")
        _encode(self.semantic_material(), "treatment signature semantic material")
        return self

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict:
        return {
            "kind": "treatment_signature",
            "schema_version": self.schema_version,
            "signature_version": self.signature_version,
            "subject_kind": self.subject_kind.value,
            "subject_ref": self.subject_ref,
            "component": self.component.value,
            "change_parameters": [[key, value]
                                  for key, value in self.change_parameters],
            "direction": self.direction.value,
            "baseline_semantics": self.baseline_semantics,
            "alternative_semantics": self.alternative_semantics,
            "treatment_class": self.treatment_class.value,
            "horizon": self.horizon.value,
            "dimension_identity": self.dimension_identity,
            "interaction_identity": self.interaction_identity,
            "slice_identity": self.slice_identity,
            "population_reference": self.population_reference,
            "asset_family": self.asset_family,
            "symbols": list(self.symbols),
            "parent_proposal_identity": self.parent_proposal_identity,
            "parent_protocol_identity": self.parent_protocol_identity,
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "TreatmentSignature":
        """Explicit construction. `subject_ref` may never be omitted."""
        if "subject_ref" not in kwargs:
            raise TreatmentMemoryValidationError(
                "a treatment signature must name a governed subject_ref")
        return cls(**kwargs)

    # -- Views (never a verdict, never a priority) ---------------------------
    def is_observation_only(self) -> bool:
        return self.component is TreatmentComponent.OBSERVATION

    def is_executable_authority(self) -> bool:
        """
        Permanently False. A signature describes what was studied.

        Exposed as a method rather than left implicit so that a consumer asking
        whether this may be applied gets a governed NO rather than a missing
        attribute.
        """
        return False

    def same_semantics(self, other: "TreatmentSignature") -> bool:
        """Exact governed-semantic identity test. Never a similarity judgement."""
        if not isinstance(other, TreatmentSignature):
            return False
        return self.semantic_identity == other.semantic_identity


    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            **self.semantic_material(),
            "change_parameters": [{"key": key, "value": value}
                                  for key, value in self.change_parameters],
            "label": self.label,
            "note": self.note,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "signature_identity": self.signature_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TreatmentSignature":
        if not isinstance(data, Mapping):
            raise TreatmentMemoryValidationError(
                f"persisted treatment signature must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "signature_version", "subject_kind",
            "subject_ref", "component", "change_parameters", "direction",
            "baseline_semantics", "alternative_semantics", "treatment_class",
            "horizon", "dimension_identity", "interaction_identity",
            "slice_identity", "population_reference", "asset_family", "symbols",
            "parent_proposal_identity", "parent_protocol_identity", "label",
            "note", "provenance", "created_at", "semantic_identity",
            "signature_identity",
        }
        if set(data) != expected:
            raise TreatmentMemoryValidationError(
                f"persisted treatment signature fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        if data["kind"] != "treatment_signature":
            raise TreatmentMemoryValidationError(
                f"persisted treatment signature kind must be "
                f"'treatment_signature', got {data['kind']!r}")
        raw = data["change_parameters"]
        if not isinstance(raw, list):
            raise TreatmentMemoryValidationError(
                "persisted treatment signature 'change_parameters' must be a list")
        parameters = []
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {"key", "value"}:
                raise TreatmentMemoryValidationError(
                    "each persisted change parameter must be exactly "
                    "{key, value}")
            parameters.append((item["key"], item["value"]))
        provenance = data["provenance"]
        if not isinstance(provenance, Mapping):
            raise TreatmentMemoryValidationError(
                "persisted treatment signature 'provenance' must be a mapping")
        return cls(
            subject_kind=OpportunitySubjectKind(data["subject_kind"]),
            subject_ref=data["subject_ref"],
            component=TreatmentComponent(data["component"]),
            change_parameters=tuple(parameters),
            direction=ChangeDirection(data["direction"]),
            baseline_semantics=data["baseline_semantics"],
            alternative_semantics=data["alternative_semantics"],
            treatment_class=TreatmentClass(data["treatment_class"]),
            horizon=Horizon(data["horizon"]),
            dimension_identity=data["dimension_identity"],
            interaction_identity=data["interaction_identity"],
            slice_identity=data["slice_identity"],
            population_reference=data["population_reference"],
            asset_family=data["asset_family"],
            symbols=tuple(data["symbols"]),
            parent_proposal_identity=data["parent_proposal_identity"],
            parent_protocol_identity=data["parent_protocol_identity"],
            label=data["label"],
            note=data["note"],
            provenance=dict(provenance),
            created_at=data["created_at"],
            signature_version=data["signature_version"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            signature_identity=data["signature_identity"],
        )




# == Historical disposition: what a governed investigation CONCLUDED =========
#
# Deliberately NOT WIN/LOSS and NOT PROFITABLE/UNPROFITABLE. Research history
# has states a binary outcome cannot express, and each of them carries a
# different obligation for future work.


class HistoricalDisposition(str, Enum):
    """
    The CLOSED set of research dispositions a historical memory may record.

    The semantics that matter for future research:

      - SUPPORTED: the governed comparison supported the hypothesis within its
        stated envelope. It says NOTHING outside that envelope.
      - NOT_SUPPORTED: the governed comparison did not support the hypothesis
        within its stated envelope. It is NOT a permanent prohibition, and it is
        NOT a claim about any other population.
      - INSUFFICIENT_DATA: the governed evidence could not answer the question.
        This is emphatically NOT a failed treatment; it is an open question that
        never received enough evidence.
      - INVALID_INVESTIGATION: the investigation did not comply with its own
        governed contract. It is NOT evidence about the treatment at all, in
        either direction.
      - INCONCLUSIVE: the investigation ran validly but the evidence
        discriminated between competing explanations insufficiently.
      - SUPERSEDED: a later governed investigation replaced this conclusion. The
        superseded record is PRESERVED, never rewritten.

    CROSSWALK to the existing authoritative vocabularies, so Wave 7 duplicates
    nothing:

        hypothesis.ConclusionType.VALIDATED   <-> SUPPORTED
        hypothesis.ConclusionType.REJECTED    <-> NOT_SUPPORTED
        hypothesis.ConclusionType.INCONCLUSIVE <-> INCONCLUSIVE
        hypothesis.ConclusionType.SUPERSEDED  <-> SUPERSEDED
        evidence_layer.EvidenceStatus.INSUFFICIENT_DATA
                                             <-> INSUFFICIENT_DATA
        research_protocol.InvalidationCriterionKind (a violation occurred)
                                             <-> INVALID_INVESTIGATION

    INVALID_INVESTIGATION has no pre-existing single-name equivalent, which is
    precisely why forcing it into an existing enum would have made an invalid
    investigation indistinguishable from a failure.
    """

    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID_INVESTIGATION = "INVALID_INVESTIGATION"
    INCONCLUSIVE = "INCONCLUSIVE"
    SUPERSEDED = "SUPERSEDED"

    @property
    def is_evidence_about_the_treatment(self) -> bool:
        """
        Whether this disposition is evidence ABOUT the treatment semantics.

        INSUFFICIENT_DATA and INVALID_INVESTIGATION are not. Suppressing work
        because the evidence ran out, or because the investigation broke its own
        contract, is exactly the research-dogma failure Wave 7 exists to
        prevent, so this is a governed mechanical property rather than a note.
        """
        return self in EVIDENCE_BEARING_DISPOSITIONS

    @property
    def is_permanently_binding(self) -> bool:
        """
        Permanently False for every member.

        A historical conclusion is not a permanent prohibition, and this is
        exposed as a governed property so no consumer can quietly treat a
        disposition as a durable verdict.
        """
        return False


#: Dispositions that carry evidence about the treatment itself.
EVIDENCE_BEARING_DISPOSITIONS: frozenset = frozenset({
    HistoricalDisposition.SUPPORTED,
    HistoricalDisposition.NOT_SUPPORTED,
    HistoricalDisposition.INCONCLUSIVE,
})


class ConfirmationState(str, Enum):
    """
    The confirmation state that EXISTED at the time of the investigation.

    Reuses the Wave 4 ConfirmationPolicy concept rather than replacing it: this
    is the STATE that policy produced, and it is identity material because
    confirmed and never-confirmable are different historical facts.
    """

    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED_AND_PRESENT = "REQUIRED_AND_PRESENT"
    REQUIRED_AND_ABSENT = "REQUIRED_AND_ABSENT"
    REQUIRED_AND_FALSIFIED = "REQUIRED_AND_FALSIFIED"
    INDETERMINATE = "INDETERMINATE"


# == Reason codes: WHY a disposition was reached =============================
#
# A disposition without a reason is an unexplained assertion, so the vocabulary
# is closed, every code is a governed token, and the mapping from disposition to
# permitted reason codes is itself data a test can assert.


class DispositionReason(str, Enum):
    """The CLOSED vocabulary of governed reasons a disposition was reached."""

    # SUPPORTED
    CONFIRMATION_PASSED = "CONFIRMATION_PASSED"
    REQUIRED_COMPARISON_COMPLETED = "REQUIRED_COMPARISON_COMPLETED"
    EVIDENCE_CRITERIA_SATISFIED = "EVIDENCE_CRITERIA_SATISFIED"

    # NOT_SUPPORTED
    GOVERNED_COMPARISON_DID_NOT_SUPPORT = "GOVERNED_COMPARISON_DID_NOT_SUPPORT"
    CONFIRMATION_CONTRADICTED_DISCOVERY = "CONFIRMATION_CONTRADICTED_DISCOVERY"
    REQUIRED_EFFECT_ABSENT = "REQUIRED_EFFECT_ABSENT"

    # INSUFFICIENT_DATA
    POPULATION_TOO_SMALL = "POPULATION_TOO_SMALL"
    COMPARISON_SIDE_MISSING = "COMPARISON_SIDE_MISSING"
    CONFIRMATION_UNAVAILABLE = "CONFIRMATION_UNAVAILABLE"
    REQUIRED_SLICE_ABSENT = "REQUIRED_SLICE_ABSENT"

    # INVALID_INVESTIGATION
    PROTOCOL_VIOLATED = "PROTOCOL_VIOLATED"
    EVIDENCE_BOUNDARY_BREACHED = "EVIDENCE_BOUNDARY_BREACHED"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    SCOPE_DRIFT_OCCURRED = "SCOPE_DRIFT_OCCURRED"

    # INCONCLUSIVE
    COMPETING_EXPLANATIONS_UNRESOLVED = "COMPETING_EXPLANATIONS_UNRESOLVED"
    CONTEXT_DEPENDENT_RESULT = "CONTEXT_DEPENDENT_RESULT"

    # SUPERSEDED
    SUPERSEDED_BY_LATER_EVIDENCE = "SUPERSEDED_BY_LATER_EVIDENCE"
    SUPERSEDED_BY_LATER_PROTOCOL = "SUPERSEDED_BY_LATER_PROTOCOL"
    SUPERSEDED_BY_NEW_POPULATION = "SUPERSEDED_BY_NEW_POPULATION"


#: Which reason codes each disposition is permitted to carry. A memory that
#: records a disposition for an unrelated reason, or gives no reason at all, is
#: rejected rather than stored.
DISPOSITION_REASON_CODES: Mapping[HistoricalDisposition, frozenset] = {
    HistoricalDisposition.SUPPORTED: frozenset({
        DispositionReason.CONFIRMATION_PASSED,
        DispositionReason.REQUIRED_COMPARISON_COMPLETED,
        DispositionReason.EVIDENCE_CRITERIA_SATISFIED,
    }),
    HistoricalDisposition.NOT_SUPPORTED: frozenset({
        DispositionReason.GOVERNED_COMPARISON_DID_NOT_SUPPORT,
        DispositionReason.CONFIRMATION_CONTRADICTED_DISCOVERY,
        DispositionReason.REQUIRED_EFFECT_ABSENT,
    }),
    HistoricalDisposition.INSUFFICIENT_DATA: frozenset({
        DispositionReason.POPULATION_TOO_SMALL,
        DispositionReason.COMPARISON_SIDE_MISSING,
        DispositionReason.CONFIRMATION_UNAVAILABLE,
        DispositionReason.REQUIRED_SLICE_ABSENT,
    }),
    HistoricalDisposition.INVALID_INVESTIGATION: frozenset({
        DispositionReason.PROTOCOL_VIOLATED,
        DispositionReason.EVIDENCE_BOUNDARY_BREACHED,
        DispositionReason.FINGERPRINT_MISMATCH,
        DispositionReason.SCOPE_DRIFT_OCCURRED,
    }),
    HistoricalDisposition.INCONCLUSIVE: frozenset({
        DispositionReason.COMPETING_EXPLANATIONS_UNRESOLVED,
        DispositionReason.CONTEXT_DEPENDENT_RESULT,
    }),
    HistoricalDisposition.SUPERSEDED: frozenset({
        DispositionReason.SUPERSEDED_BY_LATER_EVIDENCE,
        DispositionReason.SUPERSEDED_BY_LATER_PROTOCOL,
        DispositionReason.SUPERSEDED_BY_NEW_POPULATION,
    }),
}

#: The inverse index, so no reason code can exist without exactly one
#: disposition. A test asserts this bijection, which stops a future reason code
#: being added that no disposition could ever legitimately carry.
REASON_DISPOSITIONS: Mapping[DispositionReason, HistoricalDisposition] = {
    reason: disposition
    for disposition, reasons in DISPOSITION_REASON_CODES.items()
    for reason in reasons
}




# == The applicability envelope ==============================================
#
# A historical result must not apply everywhere. A treatment rejected on
# EURUSD + SCALP + TRENDING must never silently become a claim that it never
# works, and an FX finding must never silently become an XAUUSD finding unless
# governed semantics explicitly establish that relationship.


@dataclass(frozen=True)
class ApplicabilityEnvelope:
    """
    The governed boundary INSIDE WHICH a historical conclusion holds.

    Every field is identity material of the memory that carries it, because the
    applicability boundary is part of what the conclusion MEANT. A conclusion
    without a boundary is not storable: `_validate` rejects an envelope that
    constrains nothing, which makes an unconditional claim unrepresentable
    rather than merely discouraged.

    Facets this architecture cannot currently govern (broker/execution context,
    for example) are present as explicit empty strings: an honest "not governed
    here", never a fabricated value.
    """

    symbol_scope: tuple
    horizon: Horizon
    asset_family: str
    regime_scope: tuple
    dimension_identities: tuple
    interaction_identities: tuple
    slice_identity: str
    evidence_boundary: str
    evidence_reference: str
    confirmation_population: str = ""
    execution_context: str = ""
    schema_version: int = TREATMENT_MEMORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol_scope",
                           _canonical_tokens(self.symbol_scope, "symbol_scope"))
        object.__setattr__(self, "regime_scope",
                           _canonical_tokens(self.regime_scope, "regime_scope"))
        object.__setattr__(self, "dimension_identities", _canonical_identities(
            self.dimension_identities, is_dimension_identity,
            "dimension_identities"))
        object.__setattr__(self, "interaction_identities", _canonical_identities(
            self.interaction_identities, is_interaction_identity,
            "interaction_identities"))
        object.__setattr__(self, "slice_identity", _optional_identity(
            self.slice_identity, is_slice_identity, "slice_identity"))
        if isinstance(self.horizon, str) and not isinstance(self.horizon, Horizon):
            object.__setattr__(self, "horizon", Horizon(self.horizon))
        _require_semantic_token(self.asset_family, "asset_family")
        _require_text(self.evidence_boundary, "evidence_boundary")
        _require_reference(self.evidence_reference, "evidence_reference")
        object.__setattr__(self, "confirmation_population",
                           _optional_reference(self.confirmation_population,
                                               "confirmation_population"))
        object.__setattr__(self, "execution_context",
                           _optional_reference(self.execution_context,
                                               "execution_context"))
        self._validate()
        _encode(self.semantic_material(), "applicability envelope")

    def _validate(self) -> "ApplicabilityEnvelope":
        if self.schema_version != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise TreatmentMemoryValidationError(
                f"applicability envelope schema_version must be "
                f"{TREATMENT_MEMORY_SCHEMA_VERSION}, got {self.schema_version!r}")
        if not (self.symbol_scope or self.dimension_identities
                or self.interaction_identities or self.slice_identity
                or self.regime_scope):
            raise TreatmentMemoryValidationError(
                "an applicability envelope must constrain at least one governed "
                "scope element; an unbounded conclusion is not storable, because "
                "a claim that applies to everything is not a research finding")
        return self

    def semantic_material(self) -> dict:
        return {
            "kind": "applicability_envelope",
            "schema_version": self.schema_version,
            "symbol_scope": list(self.symbol_scope),
            "horizon": self.horizon.value,
            "asset_family": self.asset_family,
            "regime_scope": list(self.regime_scope),
            "dimension_identities": list(self.dimension_identities),
            "interaction_identities": list(self.interaction_identities),
            "slice_identity": self.slice_identity,
            "evidence_boundary": self.evidence_boundary,
            "evidence_reference": self.evidence_reference,
            "confirmation_population": self.confirmation_population,
            "execution_context": self.execution_context,
        }

    def to_dict(self) -> dict:
        return self.semantic_material()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApplicabilityEnvelope":
        if not isinstance(data, Mapping):
            raise TreatmentMemoryValidationError(
                f"persisted applicability envelope must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "symbol_scope", "horizon", "asset_family",
            "regime_scope", "dimension_identities", "interaction_identities",
            "slice_identity", "evidence_boundary", "evidence_reference",
            "confirmation_population", "execution_context",
        }
        if set(data) != expected:
            raise TreatmentMemoryValidationError(
                f"persisted applicability envelope fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        if data["kind"] != "applicability_envelope":
            raise TreatmentMemoryValidationError(
                f"persisted applicability envelope kind must be "
                f"'applicability_envelope', got {data['kind']!r}")
        return cls(
            symbol_scope=tuple(data["symbol_scope"]),
            horizon=Horizon(data["horizon"]),
            asset_family=data["asset_family"],
            regime_scope=tuple(data["regime_scope"]),
            dimension_identities=tuple(data["dimension_identities"]),
            interaction_identities=tuple(data["interaction_identities"]),
            slice_identity=data["slice_identity"],
            evidence_boundary=data["evidence_boundary"],
            evidence_reference=data["evidence_reference"],
            confirmation_population=data["confirmation_population"],
            execution_context=data["execution_context"],
            schema_version=data["schema_version"],
        )

    def contains(self, other: "ApplicabilityEnvelope") -> bool:
        """
        Whether `other` lies inside this envelope.

        A total, mechanical containment test over the governed scope facets.
        Deliberately conservative: an empty facet on this envelope means the
        facet was never governed here, and it does NOT widen the envelope.
        Silently widening is how a bounded result becomes an unbounded claim.
        """
        if not isinstance(other, ApplicabilityEnvelope):
            raise TreatmentMemoryValidationError(
                f"contains expects a governed ApplicabilityEnvelope, got "
                f"{type(other).__name__}")
        if self.asset_family != other.asset_family:
            return False
        if self.horizon is not other.horizon:
            return False
        if not set(self.symbol_scope).issuperset(other.symbol_scope):
            return False
        if not set(self.regime_scope).issuperset(other.regime_scope):
            return False
        if not set(self.dimension_identities).issuperset(
                other.dimension_identities):
            return False
        if not set(self.interaction_identities).issuperset(
                other.interaction_identities):
            return False
        if self.slice_identity and self.slice_identity != other.slice_identity:
            return False
        return True




# == The treatment memory record (TMR-*) =====================================


@dataclass(frozen=True)
class TreatmentMemoryRecord:
    """
    ONE immutable historical observation: what was investigated, under which
    contract, against which evidence, and what was concluded -- and why.

    IDENTITY MATERIAL (hashed): schema version, the treatment signature, the
    protocol identity, the protocol freeze identity, the research subject, the
    applicability envelope, the evidence fingerprint, the confirmation state, the
    disposition, the reason codes, the applicable dimensions / interactions /
    slices, the supporting evidence references, the parent research references,
    and supersession.

    NOT IDENTITY MATERIAL (provenance only): `label`, `note`, `provenance` and
    `created_at`.

    WHY THE OUTCOME IS IN THE IDENTITY
    ----------------------------------
    A treatment signature is a pre-result question, so its outcome is excluded.
    A memory record is a historical observation OF a conclusion, so its outcome
    is included. Two conflicting conclusions about the same treatment are
    therefore two different `TMR-*` identities that both persist -- which is
    exactly what conflict preservation requires, expressed in the identity
    function rather than in a special case.

    A record is a closed historical fact. It exposes no mutator, no promotion,
    no application, and no way to become authority for anything.
    """

    treatment_signature_identity: str
    protocol_identity: str
    protocol_freeze_identity: str
    subject_kind: OpportunitySubjectKind
    subject_ref: str
    disposition: HistoricalDisposition
    reason_codes: tuple
    applicability: ApplicabilityEnvelope
    confirmation_state: ConfirmationState
    evidence_fingerprint: str
    applicable_dimensions: tuple
    applicable_interactions: tuple
    applicable_slices: tuple
    evidence_references: tuple
    parent_research_references: tuple = ()
    supersedes: str = ""
    label: str = ""                        # provenance only; NOT identity
    note: str = ""                         # provenance only; NOT identity
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""                   # provenance only; NOT identity
    schema_version: int = TREATMENT_MEMORY_SCHEMA_VERSION
    semantic_identity: str = ""
    memory_identity: str = ""

    def __post_init__(self) -> None:
        for name, member in (
            ("subject_kind", OpportunitySubjectKind),
            ("disposition", HistoricalDisposition),
            ("confirmation_state", ConfirmationState),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and not isinstance(value, member):
                object.__setattr__(self, name, member(value))
        if not isinstance(self.applicability, ApplicabilityEnvelope):
            raise TreatmentMemoryValidationError(
                f"applicability must be a governed ApplicabilityEnvelope, got "
                f"{type(self.applicability).__name__}; a memory record may never "
                f"carry an ad-hoc applicability boundary")
        object.__setattr__(self, "reason_codes", tuple(
            item if isinstance(item, DispositionReason)
            else DispositionReason(_require_reason(item, "reason_codes entry"))
            for item in _canonical_reasons(
                [getattr(code, "value", code) for code in self.reason_codes],
                "reason_codes")))
        object.__setattr__(self, "applicable_dimensions", _canonical_identities(
            self.applicable_dimensions, is_dimension_identity,
            "applicable_dimensions"))
        object.__setattr__(self, "applicable_interactions", _canonical_identities(
            self.applicable_interactions, is_interaction_identity,
            "applicable_interactions"))
        object.__setattr__(self, "applicable_slices", _canonical_identities(
            self.applicable_slices, is_slice_identity, "applicable_slices"))
        object.__setattr__(self, "evidence_references", _canonical_refs(
            self.evidence_references, "evidence_references"))
        object.__setattr__(self, "parent_research_references", _canonical_refs(
            self.parent_research_references, "parent_research_references"))
        object.__setattr__(self, "supersedes", _optional_identity(
            self.supersedes, is_treatment_memory_identity, "supersedes"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = treatment_memory_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise TreatmentMemoryValidationError(
                    "presented treatment memory semantic identity does not match "
                    "the memory material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.memory_identity:
            if self.memory_identity != expected_id:
                raise TreatmentMemoryValidationError(
                    f"presented treatment memory identity "
                    f"{self.memory_identity!r} does not match the memory material")
        else:
            object.__setattr__(self, "memory_identity", expected_id)

    # -- Validation ---------------------------------------------------------
    def _validate(self) -> "TreatmentMemoryRecord":
        if self.schema_version != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise TreatmentMemoryValidationError(
                f"treatment memory schema_version must be "
                f"{TREATMENT_MEMORY_SCHEMA_VERSION}, got {self.schema_version!r}")
        if not is_treatment_signature_identity(self.treatment_signature_identity):
            raise TreatmentMemoryValidationError(
                f"treatment_signature_identity "
                f"{self.treatment_signature_identity!r} is outside the governed "
                f"{TREATMENT_SIGNATURE_ID_PREFIX} namespace")
        if not is_research_protocol_identity(self.protocol_identity):
            raise TreatmentMemoryValidationError(
                f"protocol_identity {self.protocol_identity!r} is not a governed "
                f"Wave 6 protocol identity; a memory of research may never be "
                f"recorded against an ungoverned contract")
        if not is_protocol_freeze_identity(self.protocol_freeze_identity):
            raise TreatmentMemoryValidationError(
                f"protocol_freeze_identity {self.protocol_freeze_identity!r} is "
                f"not a governed Wave 6 T0 protocol freeze identity")
        if not SUBJECT_PREDICATES[self.subject_kind](self.subject_ref):
            raise TreatmentMemoryValidationError(
                f"subject_ref {self.subject_ref!r} is not a governed "
                f"{self.subject_kind.value} identity")

        # A disposition MUST be explained. This is the whole point of the
        # reason-code table: recording that something was not supported is not
        # an acceptable historical record on its own.
        if not self.reason_codes:
            raise TreatmentMemoryValidationError(
                "a historical disposition must carry at least one governed reason "
                "code; an unexplained conclusion is not a storable observation")
        permitted = DISPOSITION_REASON_CODES[self.disposition]
        for code in self.reason_codes:
            if code not in permitted:
                raise TreatmentMemoryValidationError(
                    f"reason code {code.value!r} is not permitted for disposition "
                    f"{self.disposition.value!r}; permitted codes are "
                    f"{sorted(item.value for item in permitted)}")

        if not _HEX64_RE.match(self.evidence_fingerprint):
            raise TreatmentMemoryValidationError(
                f"evidence_fingerprint must be a lowercase 64-character sha256 hex "
                f"digest identifying the dataset content actually used, got "
                f"{self.evidence_fingerprint!r}")
        if not self.evidence_references:
            raise TreatmentMemoryValidationError(
                "a memory record must reference the governed evidence that "
                "supports its disposition")
        if (self.disposition is HistoricalDisposition.SUPPORTED
                and self.confirmation_state
                is ConfirmationState.REQUIRED_AND_ABSENT):
            raise TreatmentMemoryValidationError(
                "a SUPPORTED disposition may not carry an absent confirmation "
                "state; that combination is how a discovery gets mistaken for a "
                "confirmed result")
        _encode(self.semantic_material(), "treatment memory semantic material")
        return self


    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict:
        return {
            "kind": "treatment_memory",
            "schema_version": self.schema_version,
            "treatment_signature_identity": self.treatment_signature_identity,
            "protocol_identity": self.protocol_identity,
            "protocol_freeze_identity": self.protocol_freeze_identity,
            "subject_kind": self.subject_kind.value,
            "subject_ref": self.subject_ref,
            "disposition": self.disposition.value,
            "reason_codes": [code.value for code in self.reason_codes],
            "applicability": self.applicability.semantic_material(),
            "confirmation_state": self.confirmation_state.value,
            "evidence_fingerprint": self.evidence_fingerprint,
            "applicable_dimensions": list(self.applicable_dimensions),
            "applicable_interactions": list(self.applicable_interactions),
            "applicable_slices": list(self.applicable_slices),
            "evidence_references": list(self.evidence_references),
            "parent_research_references": list(self.parent_research_references),
            "supersedes": self.supersedes,
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "TreatmentMemoryRecord":
        return cls(**kwargs)

    # -- Views (history, never truth) ----------------------------------------
    def is_evidence_bearing(self) -> bool:
        """
        Whether this record is evidence ABOUT the treatment.

        An insufficient-data or invalid-investigation record is not, and must
        never be used to suppress the same treatment later.
        """
        return self.disposition.is_evidence_about_the_treatment

    def applies_to(self, context: ApplicabilityEnvelope) -> bool:
        """
        Mechanical containment test. NOT a verdict and NOT a suppression.

        A False return means only that this record's governed boundary does not
        contain that context. It never means the treatment is bad.
        """
        return self.applicability.contains(context)

    def assert_not_suppression_authority(self) -> "TreatmentMemoryRecord":
        """
        Guard that this record is never treated as a permanent verdict.

        There is deliberately no is_treatment_good, is_treatment_bad,
        should_apply or is_profitable on this class. This method documents and
        enforces that absence, and returns the record so a caller can chain it.
        """
        return self


    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            **self.semantic_material(),
            "applicability": self.applicability.to_dict(),
            "label": self.label,
            "note": self.note,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "memory_identity": self.memory_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TreatmentMemoryRecord":
        if not isinstance(data, Mapping):
            raise TreatmentMemoryValidationError(
                f"persisted treatment memory must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "treatment_signature_identity",
            "protocol_identity", "protocol_freeze_identity", "subject_kind",
            "subject_ref", "disposition", "reason_codes", "applicability",
            "confirmation_state", "evidence_fingerprint", "applicable_dimensions",
            "applicable_interactions", "applicable_slices", "evidence_references",
            "parent_research_references", "supersedes", "label", "note",
            "provenance", "created_at", "semantic_identity", "memory_identity",
        }
        if set(data) != expected:
            raise TreatmentMemoryValidationError(
                f"persisted treatment memory fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        if data["kind"] != "treatment_memory":
            raise TreatmentMemoryValidationError(
                f"persisted treatment memory kind must be 'treatment_memory', "
                f"got {data['kind']!r}")
        provenance = data["provenance"]
        if not isinstance(provenance, Mapping):
            raise TreatmentMemoryValidationError(
                "persisted treatment memory 'provenance' must be a mapping")
        return cls(
            treatment_signature_identity=data["treatment_signature_identity"],
            protocol_identity=data["protocol_identity"],
            protocol_freeze_identity=data["protocol_freeze_identity"],
            subject_kind=OpportunitySubjectKind(data["subject_kind"]),
            subject_ref=data["subject_ref"],
            disposition=HistoricalDisposition(data["disposition"]),
            reason_codes=tuple(DispositionReason(code)
                               for code in data["reason_codes"]),
            applicability=ApplicabilityEnvelope.from_dict(data["applicability"]),
            confirmation_state=ConfirmationState(data["confirmation_state"]),
            evidence_fingerprint=data["evidence_fingerprint"],
            applicable_dimensions=tuple(data["applicable_dimensions"]),
            applicable_interactions=tuple(data["applicable_interactions"]),
            applicable_slices=tuple(data["applicable_slices"]),
            evidence_references=tuple(data["evidence_references"]),
            parent_research_references=tuple(data["parent_research_references"]),
            supersedes=data["supersedes"],
            label=data["label"],
            note=data["note"],
            provenance=dict(provenance),
            created_at=data["created_at"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            memory_identity=data["memory_identity"],
        )




# == Applicability assessment: does an old result apply HERE? ===============


class ApplicabilityVerdict(str, Enum):
    """
    The CLOSED vocabulary for whether a historical result applies to a context.

    PARTIALLY_APPLIES and INDETERMINATE are distinct on purpose and are never
    collapsed into APPLIES. A partial match is not a match, and an honest
    inability to decide is not a match either.
    """

    APPLIES = "APPLIES"
    PARTIALLY_APPLIES = "PARTIALLY_APPLIES"
    DOES_NOT_APPLY = "DOES_NOT_APPLY"
    INDETERMINATE = "INDETERMINATE"


class ApplicabilityReason(str, Enum):
    """The CLOSED vocabulary of governed reasons for an applicability verdict."""

    ENVELOPE_CONTAINS_CONTEXT = "ENVELOPE_CONTAINS_CONTEXT"
    SYMBOL_SCOPE_EXCLUDED = "SYMBOL_SCOPE_EXCLUDED"
    ASSET_FAMILY_DIFFERS = "ASSET_FAMILY_DIFFERS"
    HORIZON_DIFFERS = "HORIZON_DIFFERS"
    REGIME_SCOPE_DIFFERS = "REGIME_SCOPE_DIFFERS"
    DIMENSION_SCOPE_DIFFERS = "DIMENSION_SCOPE_DIFFERS"
    INTERACTION_SCOPE_DIFFERS = "INTERACTION_SCOPE_DIFFERS"
    SLICE_DIFFERS = "SLICE_DIFFERS"
    EVIDENCE_BOUNDARY_PRECEDES = "EVIDENCE_BOUNDARY_PRECEDES"
    CONFIRMATION_POPULATION_ABSENT = "CONFIRMATION_POPULATION_ABSENT"
    EXECUTION_CONTEXT_DIFFERS = "EXECUTION_CONTEXT_DIFFERS"
    PARTIAL_SCOPE_OVERLAP = "PARTIAL_SCOPE_OVERLAP"


@dataclass(frozen=True)
class ApplicabilityAssessment:
    """
    A deterministic, inspectable answer to whether an old result applies here.

    Carries the verdict AND every reason that produced it, so a reader never
    has to re-derive why an old finding was considered out of scope. There is
    intentionally no score, no confidence number and no ranking.
    """

    memory_identity: str
    verdict: ApplicabilityVerdict
    reasons: tuple

    @property
    def is_definitive(self) -> bool:
        """Only a fully-determined verdict may be acted on without review."""
        return self.verdict in (ApplicabilityVerdict.APPLIES,
                                ApplicabilityVerdict.DOES_NOT_APPLY)

    def to_dict(self) -> dict:
        return {
            "memory_identity": self.memory_identity,
            "verdict": self.verdict.value,
            "reasons": [reason.value for reason in self.reasons],
        }


def assess_memory_applicability(
        memory: "TreatmentMemoryRecord",
        context: ApplicabilityEnvelope,
) -> ApplicabilityAssessment:
    """
    Does `memory` apply to `context`?

    Mechanically: APPLIES only when the memory's governed envelope fully
    contains the context. Any facet the memory constrained and the context
    escapes yields DOES_NOT_APPLY, which is what stops a bounded result from
    silently generalising. A facet the memory left ungoverned that the context
    DID constrain yields PARTIALLY_APPLIES, because the memory never governed
    that dimension of the question.

    This function NEVER returns a verdict about whether a treatment is good. It
    returns only a statement about the scope of a historical conclusion.
    """
    if not isinstance(memory, TreatmentMemoryRecord):
        raise TreatmentMemoryValidationError(
            f"assess_memory_applicability expects a TreatmentMemoryRecord, got "
            f"{type(memory).__name__}")
    if not isinstance(context, ApplicabilityEnvelope):
        raise TreatmentMemoryValidationError(
            f"assess_memory_applicability expects an ApplicabilityEnvelope, got "
            f"{type(context).__name__}")

    envelope = memory.applicability
    if envelope.asset_family != context.asset_family:
        return ApplicabilityAssessment(
            memory.memory_identity, ApplicabilityVerdict.DOES_NOT_APPLY,
            (ApplicabilityReason.ASSET_FAMILY_DIFFERS,))
    if envelope.horizon is not context.horizon:
        return ApplicabilityAssessment(
            memory.memory_identity, ApplicabilityVerdict.DOES_NOT_APPLY,
            (ApplicabilityReason.HORIZON_DIFFERS,))

    excluded = []
    if envelope.symbol_scope and not set(envelope.symbol_scope).issuperset(
            context.symbol_scope):
        excluded.append(ApplicabilityReason.SYMBOL_SCOPE_EXCLUDED)
    if (envelope.regime_scope
            and not set(envelope.regime_scope).issuperset(
                context.regime_scope)):
        excluded.append(ApplicabilityReason.REGIME_SCOPE_DIFFERS)
    if (envelope.dimension_identities
            and not set(envelope.dimension_identities).issuperset(
                context.dimension_identities)):
        excluded.append(ApplicabilityReason.DIMENSION_SCOPE_DIFFERS)
    if (envelope.interaction_identities
            and not set(envelope.interaction_identities).issuperset(
                context.interaction_identities)):
        excluded.append(ApplicabilityReason.INTERACTION_SCOPE_DIFFERS)
    if (envelope.slice_identity
            and envelope.slice_identity != context.slice_identity):
        excluded.append(ApplicabilityReason.SLICE_DIFFERS)
    if excluded:
        return ApplicabilityAssessment(
            memory.memory_identity, ApplicabilityVerdict.DOES_NOT_APPLY,
            tuple(excluded))

    # The context lies inside every scope facet the memory governed. Anything the
    # memory left ungoverned that the context DID constrain is a partial match.
    if (not envelope.symbol_scope and context.symbol_scope) or (
            not envelope.regime_scope and context.regime_scope) or (
            not envelope.dimension_identities
            and context.dimension_identities) or (
            not envelope.slice_identity and context.slice_identity):
        return ApplicabilityAssessment(
            memory.memory_identity, ApplicabilityVerdict.PARTIALLY_APPLIES,
            (ApplicabilityReason.PARTIAL_SCOPE_OVERLAP,))

    # Scope holds. The remaining facets describe the EVIDENCE, and each that
    # differs narrows applicability without invalidating it.
    narrowed = []
    if (context.evidence_boundary
            and context.evidence_boundary < envelope.evidence_boundary):
        narrowed.append(ApplicabilityReason.EVIDENCE_BOUNDARY_PRECEDES)
    if (memory.disposition.is_evidence_about_the_treatment
            and not envelope.confirmation_population
            and not context.confirmation_population):
        narrowed.append(
            ApplicabilityReason.CONFIRMATION_POPULATION_ABSENT)
    if (envelope.execution_context and context.execution_context
            and envelope.execution_context != context.execution_context):
        narrowed.append(ApplicabilityReason.EXECUTION_CONTEXT_DIFFERS)
    if narrowed:
        return ApplicabilityAssessment(
            memory.memory_identity, ApplicabilityVerdict.PARTIALLY_APPLIES,
            tuple(narrowed))

    return ApplicabilityAssessment(
        memory.memory_identity, ApplicabilityVerdict.APPLIES,
        (ApplicabilityReason.ENVELOPE_CONTAINS_CONTEXT,))



# == Memory lookup: have we investigated this before? ========================
#
# Deterministic EXACT lookup only. There is no fuzzy, embedding, LLM or
# text-similarity search anywhere in this module, because deciding that two
# things look alike is precisely the judgement that produced research amnesia
# and research dogma in the first place.


def historical_memories_for(
        memories: Iterable,
        *,
        treatment_signature_identity: str = "",
        subject_ref: str = "",
        dimension_identity: str = "",
        interaction_identity: str = "",
        slice_identity: str = "",
        disposition=None,
) -> tuple:
    """
    Every historical memory matching the given EXACT governed criteria.

    Returns records ordered by memory identity, so the result is stable across
    runs. An empty criterion matches everything, which makes
    historical_memories_for(records) a legitimate full listing.

    Every criterion is an exact governed match. There is no ranking, no
    relevance score and no most-similar ordering, because a similarity ordering
    would be a similarity judgement wearing a deterministic hat.
    """
    rows = tuple(memories)
    for row in rows:
        if not isinstance(row, TreatmentMemoryRecord):
            raise TreatmentMemoryValidationError(
                f"historical_memories_for expects TreatmentMemoryRecord objects, "
                f"got {type(row).__name__}")
    if treatment_signature_identity:
        if not is_treatment_signature_identity(treatment_signature_identity):
            raise TreatmentMemoryValidationError(
                f"treatment_signature_identity "
                f"{treatment_signature_identity!r} is outside the governed "
                f"{TREATMENT_SIGNATURE_ID_PREFIX} namespace")
        rows = tuple(row for row in rows if (
            row.treatment_signature_identity == treatment_signature_identity))
    if subject_ref:
        _require_text(subject_ref, "subject_ref")
        rows = tuple(row for row in rows if row.subject_ref == subject_ref)
    if dimension_identity:
        if not is_dimension_identity(dimension_identity):
            raise TreatmentMemoryValidationError(
                f"dimension_identity {dimension_identity!r} is not a governed "
                f"Wave 1 dimension identity")
        rows = tuple(row for row in rows
                     if dimension_identity in row.applicable_dimensions)
    if interaction_identity:
        if not is_interaction_identity(interaction_identity):
            raise TreatmentMemoryValidationError(
                f"interaction_identity {interaction_identity!r} is not a "
                f"governed Wave 1 interaction identity")
        rows = tuple(row for row in rows
                     if interaction_identity in row.applicable_interactions)
    if slice_identity:
        if not is_slice_identity(slice_identity):
            raise TreatmentMemoryValidationError(
                f"slice_identity {slice_identity!r} is not a governed Wave 1 "
                f"slice identity")
        rows = tuple(row for row in rows
                     if slice_identity in row.applicable_slices)
    if disposition is not None:
        wanted = (disposition if isinstance(disposition, HistoricalDisposition)
                  else HistoricalDisposition(disposition))
        rows = tuple(row for row in rows if row.disposition is wanted)
    return tuple(sorted(rows, key=lambda row: row.memory_identity))



# == Conflicting historical memory ===========================================


class ConflictingMemoryState(str, Enum):
    """
    What Wave 7 knows about disagreement within its own history.

    CONFLICTING_APPLICABLE_MEMORY is the state that matters: two legitimate
    records, both applicable to the same context, with incompatible
    dispositions. Wave 7 RECORDS it and refuses to resolve it. Picking a winner
    would be research, and research is not Wave 7's job.
    """

    NO_MEMORY = "NO_MEMORY"
    CONSISTENT_MEMORY = "CONSISTENT_MEMORY"
    CONFLICTING_APPLICABLE_MEMORY = "CONFLICTING_APPLICABLE_MEMORY"
    INDETERMINATE = "INDETERMINATE"


#: Disposition pairs that are genuinely incompatible claims about the same
#: context. Recorded as data so a test can assert no pair is accidentally
#: treated as agreement.
CONFLICTING_DISPOSITION_PAIRS: frozenset = frozenset({
    frozenset({HistoricalDisposition.SUPPORTED,
               HistoricalDisposition.NOT_SUPPORTED}),
})


@dataclass(frozen=True)
class MemoryConflictAssessment:
    """
    The explicit, inspectable answer to whether history disagrees with itself.

    `conflicting_memories` names BOTH sides. There is deliberately no `winner`
    field, and adding one would be the exact failure Wave 7 must avoid.
    """

    state: ConflictingMemoryState
    applicable_memories: tuple
    conflicting_memories: tuple

    @property
    def has_conflict(self) -> bool:
        return self.state is ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "applicable_memories": list(self.applicable_memories),
            "conflicting_memories": list(self.conflicting_memories),
        }


def assess_memory_conflict(
        memories: Iterable,
        context: ApplicabilityEnvelope,
) -> MemoryConflictAssessment:
    """
    Does the applicable history contradict itself?

    Collects every memory that applies to `context`, then reports whether any
    pair of them carries incompatible dispositions.

    Wave 7 does NOT resolve the conflict, does NOT prefer the newer record, and
    does NOT prefer the stronger-sounding one. A caller that needs resolution
    must do more research, which is a governed protocol and a NEW memory, not a
    tie-break rule buried in the memory layer.
    """
    rows = historical_memories_for(memories)
    applicable = []
    indeterminate = []
    for row in rows:
        assessment = assess_memory_applicability(row, context)
        if assessment.verdict is ApplicabilityVerdict.APPLIES:
            applicable.append(row)
        elif assessment.verdict is ApplicabilityVerdict.INDETERMINATE:
            indeterminate.append(row.memory_identity)

    applicable_ids = tuple(sorted(row.memory_identity for row in applicable))
    if not applicable:
        state = (ConflictingMemoryState.INDETERMINATE if indeterminate
                 else ConflictingMemoryState.NO_MEMORY)
        return MemoryConflictAssessment(state, (), ())

    conflicting = set()
    for index, left in enumerate(applicable):
        for right in applicable[index + 1:]:
            if frozenset({left.disposition,
                          right.disposition}) in CONFLICTING_DISPOSITION_PAIRS:
                conflicting.add(left.memory_identity)
                conflicting.add(right.memory_identity)
    if conflicting:
        return MemoryConflictAssessment(
            ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY,
            applicable_ids, tuple(sorted(conflicting)))
    if indeterminate:
        return MemoryConflictAssessment(
            ConflictingMemoryState.INDETERMINATE, applicable_ids, ())
    return MemoryConflictAssessment(
        ConflictingMemoryState.CONSISTENT_MEMORY, applicable_ids, ())


__all__ = [
    "ApplicabilityAssessment",
    "ApplicabilityEnvelope",
    "ApplicabilityReason",
    "ApplicabilityVerdict",
    "CHANGE_PARAMETER_KEYS",
    "CHANGE_PARAMETER_TYPES",
    "CONFLICTING_DISPOSITION_PAIRS",
    "ChangeDirection",
    "ConfirmationState",
    "ConflictingMemoryState",
    "DISPOSITION_REASON_CODES",
    "DispositionReason",
    "EVIDENCE_BEARING_DISPOSITIONS",
    "HistoricalDisposition",
    "Horizon",
    "MemoryConflictAssessment",
    "REASON_DISPOSITIONS",
    "SUBJECT_PREDICATES",
    "TREATMENT_MEMORY_ID_PREFIX",
    "TREATMENT_MEMORY_SCHEMA_VERSION",
    "TREATMENT_SIGNATURE_ID_PREFIX",
    "TREATMENT_SIGNATURE_VERSION",
    "TreatmentClass",
    "TreatmentComponent",
    "TreatmentMemoryError",
    "TreatmentMemoryIdentityConflict",
    "TreatmentMemoryNamespaceViolation",
    "TreatmentMemoryRecord",
    "TreatmentMemoryValidationError",
    "TreatmentSignature",
    "assess_memory_applicability",
    "assess_memory_conflict",
    "historical_memories_for",
    "is_treatment_memory_identity",
    "is_treatment_signature_identity",
    "treatment_memory_identity_for",
    "treatment_signature_identity_for",
]

