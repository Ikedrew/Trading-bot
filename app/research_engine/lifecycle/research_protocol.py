"""
Research Protocol v1 -- the governed contract for an INTENDED investigation.

Stage 3 / Wave 6. Wave 5 answers "What should receive research attention?". Wave 6
answers the strictly later question: "What EXACTLY does investigating that mean,
and what is investigating it allowed to mean, in total, before any result exists?"

A `ResearchProtocol` is the frozen, deterministically identified specification of
one intended investigation. It is written BEFORE any result is observed, and it is
the only thing a future research executor would be permitted to interpret.

THE PROTOCOL IS NOT A RUNNER
============================
A protocol states what an investigation means. It does not perform it. Nothing in
this module runs an experiment, a backtest, a research cycle or a simulation,
creates or promotes a candidate, applies a treatment, touches a broker, risk,
sizing, strategy or production configuration, or writes to any production, runtime,
configuration or data-collection path. Wave 6 is declarative, and it imports no
such path.

IT IS NEVER FREE-FLOATING
=========================
A protocol is only constructible against an authoritative Wave 5 `ROP-*` research
opportunity. `ResearchProtocol.for_opportunity(...)` is the binding constructor and
`ResearchProtocol.assert_binds_to(opportunity)` is the mechanical proof; the
persistence store requires the opportunity object to be presented at registration.
There is deliberately no free-text subject and no arbitrary-subject bypass: the
subject is carried verbatim from the governed opportunity, and its namespace was
already validated by Wave 5.

    ROP-*  ->  AGD-*  ->  QUE-*  ->  AFR-*  ->  RPL-*  ->  PFR-*

Each link is optional EXCEPT the opportunity, and the links that ARE supplied must
be internally consistent (a queue or an agenda freeze without its agenda is
rejected). An opportunity that legitimately never entered a queue can still be
authorised, because Wave 5 does not require every opportunity to be queued.

DISCOVERY NEVER EXPANDS A PROTOCOL
==================================
The single most important rule in this module. If a future investigation discovers
something outside its declared scope, the answer is never "widen the protocol". The
answer is: a NEW governed research work item through the existing Wave 3+ curiosity
lifecycle. `ScopeChangePolicy` encodes this as identity material --
`discovery_expands_protocol` MUST be False -- and `research_protocol_scope` turns it
into a mechanical assertion. A changed specification is always a NEW `RPL-*`
identity with explicit supersession, so the original investigation stays
permanently inspectable.

DETERMINISM
-----------
`semantic_identity` is the SHA-256 digest of the canonical JSON encoding of
`semantic_material()`. Identity material is the SEMANTICS of the investigation:
version, authorisation, subject, evidence scope, evidence boundary, governed
dimensions/interactions/slices, permitted operations, prohibited operations,
comparison, completion/insufficiency/invalidation criteria, confirmation
requirement, scope-change policy and supersession.

`label`, `note`, `provenance`, `created_at` and `status` are PROVENANCE ONLY and are
deliberately absent from identity material. Renaming a protocol, timestamping it or
moving it DRAFT -> VALIDATED -> FROZEN therefore cannot rewrite science, while
changing a single governed element -- a dimension, a comparison, a criterion, an
operation -- necessarily produces a different identity.

NO PRODUCTION AUTHORITY
-----------------------
No runner, orchestrator, governance gate, broker, risk, sizing, candidate machinery
or production configuration is imported or reachable. No I/O at import time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.research_agenda import (
    is_agenda_freeze_identity,
    is_research_agenda_identity,
)
from research_engine.lifecycle.research_interaction import (
    is_interaction_identity,
    is_slice_identity,
)
from research_engine.lifecycle.research_opportunity import (
    WAVE4_SUBJECT_KINDS,
    ConfirmationRequirement,
    OpportunityState,
    OpportunitySubjectKind,
    ResearchOpportunity,
    is_research_opportunity_identity,
    is_wave4_identity,
)
from research_engine.lifecycle.research_queue import is_research_queue_identity
from research_engine.lifecycle.search_provenance import (
    ConfirmationPolicy,
    DiscoveryPopulation,
)

RESEARCH_PROTOCOL_SCHEMA_VERSION: int = 1

# `RPL-` (research protocol) and `PFR-` (protocol freeze) are Wave 6's own
# namespaces, disjoint from `ROP-`, `POL-`, `AGD-`, `AFR-`, `QUE-`, every Wave 0-4
# namespace (`GEN-`, `CSN-`, `PRP-`, `DIM-`, `IXN-`, `SLC-`, `FSP-`, `EVD-`, `DEC-`,
# `EXP-`, `ALT-`, `FAM-`, `SRC-`, `FRZ-`, `MUL-`) and every canonical programme
# prefix. Verified free before adoption.
RESEARCH_PROTOCOL_ID_PREFIX = "RPL-"
PROTOCOL_FREEZE_ID_PREFIX = "PFR-"
RESEARCH_PROTOCOL_ID_DIGEST_CHARS = 16

_ID_DIGEST_CHARS = RESEARCH_PROTOCOL_ID_DIGEST_CHARS
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_RPL_ID_RE = re.compile(
    rf"^{re.escape(RESEARCH_PROTOCOL_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_PROTOCOL_ID_DIGEST_CHARS}}}$")
_PFR_ID_RE = re.compile(
    rf"^{re.escape(PROTOCOL_FREEZE_ID_PREFIX)}"
    rf"[0-9A-F]{{{RESEARCH_PROTOCOL_ID_DIGEST_CHARS}}}$")
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


# ═══ Errors (fail closed, never silently repaired) ═══════════════════════════


class ResearchProtocolError(RuntimeError):
    """Base failure for the Wave 6 governed research protocol layer."""


class ResearchProtocolValidationError(ResearchProtocolError):
    """Protocol material is invalid, incomplete or not canonically encodable."""


class ProtocolAuthorisationError(ResearchProtocolValidationError):
    """A protocol is not (or no longer) provably bound to governed research work."""


class ProhibitedAuthorityError(ResearchProtocolValidationError):
    """A protocol asked for authority that Stage 3 must never grant."""


class ProtocolNamespaceViolation(ResearchProtocolValidationError):
    """A presented identity is outside every governed namespace."""


# ═══ Helpers ═════════════════════════════════════════════════════════════════


def is_research_protocol_identity(value: Any) -> bool:
    """True only for IDs inside the reserved research-protocol namespace."""
    return isinstance(value, str) and bool(_RPL_ID_RE.match(value))


def is_protocol_freeze_identity(value: Any) -> bool:
    """True only for IDs inside the reserved protocol-freeze namespace."""
    return isinstance(value, str) and bool(_PFR_ID_RE.match(value))


def _identity_for(prefix: str, semantic_identity: str, label: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise ResearchProtocolValidationError(
            f"{label} must be a lowercase 64-character sha256 hex digest")
    return f"{prefix}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def research_protocol_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        RESEARCH_PROTOCOL_ID_PREFIX, semantic_identity, "protocol semantic identity")


def protocol_freeze_identity_for(semantic_identity: str) -> str:
    return _identity_for(
        PROTOCOL_FREEZE_ID_PREFIX, semantic_identity, "protocol freeze semantic identity")


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise ResearchProtocolValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "semantic material").encode("utf-8")).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchProtocolValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ResearchProtocolValidationError(
            f"{label} must not have surrounding whitespace")
    return value


def _require_reference(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REFERENCE_RE.match(text):
        raise ResearchProtocolValidationError(
            f"{label} must be a governed 'scheme:value' reference, got {text!r}")
    return text


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise ResearchProtocolValidationError(
            f"{label} must be a governed token, got {text!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _REASON_RE.match(text):
        raise ResearchProtocolValidationError(
            f"{label} must be a CLOSED SHOUTY_SNAKE_CASE code; free text is not "
            f"authority, got {text!r}")
    return text


def _refs(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchProtocolValidationError(f"{label} must be a sequence of references")
    refs = [_require_reference(item, f"{label} entry") for item in values]
    if len(set(refs)) != len(refs):
        raise ResearchProtocolValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(refs))


def _coerce_enum(value: Any, member: Any, label: str) -> Any:
    """Admit a governed enum member or its exact value. Never a near miss."""
    if isinstance(value, member):
        return value
    try:
        return member(value)
    except (ValueError, TypeError, KeyError) as exc:
        raise ResearchProtocolValidationError(
            f"{label} {value!r} is not a governed {member.__name__} member; the "
            f"vocabulary is closed: {[item.value for item in member]}") from exc


def _require_instance(value: Any, member: Any, label: str) -> Any:
    """Admit a genuine dataclass instance, never a look-alike or a mapping."""
    if not isinstance(value, member):
        raise ResearchProtocolValidationError(
            f"expected {member.__name__} for {label}, got {type(value).__name__}")
    return value



# ═══ Allowed research operations (a CLOSED governed vocabulary) ══════════════


class ResearchOperation(str, Enum):
    """
    The complete, closed set of research ACTIVITIES a protocol may put in scope.

    Wave 6 does not perform any of these. It governs them: a protocol declares
    which kinds of analysis its investigation is permitted to perform, and
    `research_protocol_scope.assert_operation_permitted` fails closed on anything
    else. The vocabulary is an enum, so an arbitrary operation string can never
    become executable authority -- it is rejected at construction.
    """

    DESCRIBE = "DESCRIBE"
    AGGREGATE = "AGGREGATE"
    SLICE = "SLICE"
    COMPARE = "COMPARE"
    COUNTERFACTUAL_EVALUATION = "COUNTERFACTUAL_EVALUATION"
    EXCURSION_ANALYSIS = "EXCURSION_ANALYSIS"
    DISTRIBUTION_ANALYSIS = "DISTRIBUTION_ANALYSIS"
    DEPENDENCY_ANALYSIS = "DEPENDENCY_ANALYSIS"

    @property
    def is_counterfactual(self) -> bool:
        """
        True for the operation that evaluates a counterfactual.

        Such an operation evaluates what WOULD have happened. It never applies
        anything: Stage 3 has no authority to apply a treatment, and a
        counterfactual is explicitly not one.
        """
        return self is ResearchOperation.COUNTERFACTUAL_EVALUATION


#: The complete governed operation vocabulary, in a stable published order.
RESEARCH_OPERATIONS: tuple[ResearchOperation, ...] = tuple(ResearchOperation)


# ═══ Prohibited operations (globally forbidden in Stage 3) ═══════════════════


class ProhibitedOperation(str, Enum):
    """
    Authority a Stage 3 research protocol must NEVER be able to grant.

    These are not "discouraged": a protocol requesting any of them fails closed
    and is rejected, and the forbidden entry is never silently removed to let
    construction continue. Wave 6 itself holds no such authority either, which is
    why these are prohibitions on the CONTRACT rather than on the code.
    """

    PRODUCTION_TRADING = "PRODUCTION_TRADING"
    BROKER_INTERACTION = "BROKER_INTERACTION"
    STRATEGY_MUTATION = "STRATEGY_MUTATION"
    RISK_MUTATION = "RISK_MUTATION"
    SIZING_MUTATION = "SIZING_MUTATION"
    CANDIDATE_ACTIVATION = "CANDIDATE_ACTIVATION"
    CANDIDATE_PROMOTION = "CANDIDATE_PROMOTION"
    PRODUCTION_CONFIGURATION_CHANGE = "PRODUCTION_CONFIGURATION_CHANGE"
    AUTONOMOUS_TREATMENT_APPLICATION = "AUTONOMOUS_TREATMENT_APPLICATION"


#: Every globally forbidden operation. A protocol may forbid more; it may never
#: forbid fewer.
GLOBALLY_PROHIBITED_OPERATIONS: frozenset[ProhibitedOperation] = frozenset(
    ProhibitedOperation)


# ═══ Protocol lifecycle status ═══════════════════════════════════════════════


class ProtocolStatus(str, Enum):
    """
    The lifecycle of an INVESTIGATION CONTRACT -- never of its outcome.

    There is deliberately no `SUCCESS`, `FAILED`, `PROFITABLE` or `PROMOTED`
    member. Wave 6 governs intent; whether an investigation later succeeded is a
    different concern that no later result may write back into this record.

    `status` is PROVENANCE ONLY: it is absent from identity material, so moving a
    protocol DRAFT -> VALIDATED -> FROZEN cannot change what it means.
    """

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    FROZEN = "FROZEN"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (ProtocolStatus.SUPERSEDED, ProtocolStatus.CANCELLED)


#: The only legal forward transitions. A frozen protocol can never be reopened;
#: changing an investigation produces a NEW protocol identity, not an edit.
PROTOCOL_STATUS_TRANSITIONS: Mapping[ProtocolStatus, tuple[ProtocolStatus, ...]] = {
    ProtocolStatus.DRAFT: (ProtocolStatus.VALIDATED, ProtocolStatus.CANCELLED),
    ProtocolStatus.VALIDATED: (ProtocolStatus.FROZEN, ProtocolStatus.CANCELLED),
    ProtocolStatus.FROZEN: (ProtocolStatus.SUPERSEDED, ProtocolStatus.CANCELLED),
    ProtocolStatus.SUPERSEDED: (),
    ProtocolStatus.CANCELLED: (),
}


class SupersessionReason(str, Enum):
    """
    Governed reason codes for replacing a protocol with a new one.

    There is deliberately no free-text member. "We changed our minds" is not a
    governed reason, and an ungoverned reason could not be compared, audited or
    reconstructed later.
    """

    SCOPE_CORRECTED = "SCOPE_CORRECTED"
    EVIDENCE_BOUNDARY_CORRECTED = "EVIDENCE_BOUNDARY_CORRECTED"
    COMPARISON_CORRECTED = "COMPARISON_CORRECTED"
    EVIDENCE_SCOPE_CORRECTED = "EVIDENCE_SCOPE_CORRECTED"
    COMPLETION_CRITERIA_CORRECTED = "COMPLETION_CRITERIA_CORRECTED"
    CONFIRMATION_REQUIREMENT_CORRECTED = "CONFIRMATION_REQUIREMENT_CORRECTED"
    PERMITTED_OPERATIONS_CORRECTED = "PERMITTED_OPERATIONS_CORRECTED"
    POPULATION_CORRECTED = "POPULATION_CORRECTED"
    AUTHORISATION_WITHDRAWN = "AUTHORISATION_WITHDRAWN"



# ═══ Completion, insufficiency and invalidation criteria ════════════════════
#
# These three are deliberately SEPARATE vocabularies, because they answer three
# different questions and collapsing them is how "we looked long enough" becomes
# an answer:
#
#   completion      -- the investigation is SUFFICIENTLY investigated;
#   insufficiency   -- the investigation CANNOT answer, honestly;
#   invalidation    -- the investigation itself is VOID and must not be read.
#
# NO STATISTICAL THRESHOLD IS INVENTED
# ------------------------------------
# Wave 6 introduces no significance level, no effect size, no confidence
# interval and no pass/fail score, because the architecture does not already
# justify one. A criterion that needs a number and has none simply has no number:
# nothing here can express a threshold that the repository has not justified.


class CompletionCriterionKind(str, Enum):
    """
    The closed set of ways a protocol's investigation can be COMPLETE.

    These are deterministic, governed conditions -- not vague ones. "Stop when
    enough has been learned" is not expressible, because no member of this
    vocabulary means it.
    """

    MINIMUM_OBSERVATIONS_MET = "MINIMUM_OBSERVATIONS_MET"
    REQUIRED_EVIDENCE_COVERAGE = "REQUIRED_EVIDENCE_COVERAGE"
    REQUIRED_SLICES_EVALUATED = "REQUIRED_SLICES_EVALUATED"
    REQUIRED_COMPARISONS_COMPLETED = "REQUIRED_COMPARISONS_COMPLETED"
    ALL_DECLARED_OPERATIONS_COMPLETED = "ALL_DECLARED_OPERATIONS_COMPLETED"
    CONFIRMATION_STATE_REACHED = "CONFIRMATION_STATE_REACHED"
    REQUIRED_EVIDENCE_REFERENCES_AVAILABLE = "REQUIRED_EVIDENCE_REFERENCES_AVAILABLE"


class InsufficiencyCriterionKind(str, Enum):
    """
    The closed set of reasons an investigation may be UNANSWERABLE.

    This is the vocabulary that lets a future executor distinguish "INSUFFICIENT
    EVIDENCE TO ANSWER" from "THE ANSWER IS NEGATIVE". Wave 6 only defines the
    contract; it never decides which one applies.
    """

    REQUIRED_POPULATION_UNAVAILABLE = "REQUIRED_POPULATION_UNAVAILABLE"
    MINIMUM_OBSERVATIONS_UNAVAILABLE = "MINIMUM_OBSERVATIONS_UNAVAILABLE"
    REQUIRED_COMPARISON_SIDE_ABSENT = "REQUIRED_COMPARISON_SIDE_ABSENT"
    REQUIRED_DIMENSION_NOT_OBSERVABLE = "REQUIRED_DIMENSION_NOT_OBSERVABLE"
    PROSPECTIVE_CONFIRMATION_NOT_YET_ACCUMULATED = (
        "PROSPECTIVE_CONFIRMATION_NOT_YET_ACCUMULATED")
    EVIDENCE_FINGERPRINT_MISMATCH = "EVIDENCE_FINGERPRINT_MISMATCH"
    EVIDENCE_BOUNDARY_MISMATCH = "EVIDENCE_BOUNDARY_MISMATCH"


class InvalidationCriterionKind(str, Enum):
    """
    The closed set of conditions that make an investigation VOID.

    An invalidated protocol must not quietly degrade into a weaker, still-valid
    investigation. It fails closed: the record is refused rather than trimmed.
    """

    EVIDENCE_BOUNDARY_VIOLATED = "EVIDENCE_BOUNDARY_VIOLATED"
    POST_T0_EVIDENCE_IMPROPERLY_INCLUDED = "POST_T0_EVIDENCE_IMPROPERLY_INCLUDED"
    REQUIRED_PROVENANCE_MISSING = "REQUIRED_PROVENANCE_MISSING"
    DATASET_FINGERPRINT_MISMATCH = "DATASET_FINGERPRINT_MISMATCH"
    PROTOCOL_SCOPE_CHANGED = "PROTOCOL_SCOPE_CHANGED"
    COMPARISON_SPECIFICATION_CHANGED = "COMPARISON_SPECIFICATION_CHANGED"
    MULTIPLICITY_PROVENANCE_CONTRADICTED = "MULTIPLICITY_PROVENANCE_CONTRADICTED"
    REQUIRED_PARENT_IDENTITY_MISSING = "REQUIRED_PARENT_IDENTITY_MISSING"
    PROHIBITED_OPERATION_REQUESTED = "PROHIBITED_OPERATION_REQUESTED"
    AUTHORISATION_WITHDRAWN = "AUTHORISATION_WITHDRAWN"



def _criterion_list(values: Any, member: Any, label: str) -> tuple[Any, ...]:
    """
    Canonicalise a criterion list: closed-vocabulary, sorted, non-empty, unique.

    NON-EMPTY is the point. A protocol that declares no completion criterion, or
    no insufficiency criterion, or no invalidation criterion is exactly the
    protocol that can be closed on a whim -- so each list must be populated and
    the omission is not available as an option.
    """
    if values is None or (isinstance(values, (list, tuple)) and not values):
        raise ResearchProtocolValidationError(
            f"{label} must not be empty; an investigation with no declared {label} "
            f"cannot be governed, closed or refused")
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchProtocolValidationError(
            f"{label} must be a sequence of governed {member.__name__} members")
    items = [_coerce_enum(item, member, f"{label} entry") for item in values]
    names = [item.value for item in items]
    if len(set(names)) != len(names):
        raise ResearchProtocolValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(items, key=lambda item: item.value))


def _operation_list(values: Any, label: str) -> tuple[ResearchOperation, ...]:
    """Canonicalise a permitted-operation list: closed vocabulary, non-empty."""
    if values is None or (isinstance(values, (list, tuple)) and not values):
        raise ResearchProtocolValidationError(
            f"{label} must not be empty; a protocol that permits no research "
            f"activity is not an investigation contract")
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchProtocolValidationError(
            f"{label} must be a sequence of governed ResearchOperation members")
    items = [_coerce_enum(item, ResearchOperation, f"{label} entry") for item in values]
    names = [item.value for item in items]
    if len(set(names)) != len(names):
        raise ResearchProtocolValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(items, key=lambda item: item.value))


def _prohibited_list(values: Any, label: str) -> tuple[ProhibitedOperation, ...]:
    """
    Canonicalise a prohibited-operation list and refuse to under-prohibit.

    Every globally forbidden operation MUST be present. A protocol that omits one
    is rejected -- the omission is never repaired by silently adding the missing
    entry to the stored record, because a stored record that reads "I may promote
    a candidate" is a materially different (and unacceptable) claim from one that
    never mentions promotion.
    """
    if values is None or (isinstance(values, (list, tuple)) and not values):
        raise ResearchProtocolValidationError(
            f"{label} must not be empty; Stage 3 always forbids globally "
            f"prohibited authority")
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchProtocolValidationError(
            f"{label} must be a sequence of governed ProhibitedOperation members")
    items = [_coerce_enum(item, ProhibitedOperation, f"{label} entry")
             for item in values]
    names = {item.value for item in items}
    if len(names) != len(items):
        raise ResearchProtocolValidationError(f"{label} must not contain duplicates")
    missing = sorted(item.value for item in GLOBALLY_PROHIBITED_OPERATIONS
                     if item not in items)
    if missing:
        raise ResearchProtocolValidationError(
            f"{label} is missing globally prohibited operations: {missing}. Stage 3 "
            f"research protocols must forbid all of "
            f"{sorted(item.value for item in GLOBALLY_PROHIBITED_OPERATIONS)}")
    return tuple(sorted(items, key=lambda item: item.value))



# ═══ Evidence scope ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EvidenceScope:
    """
    Exactly which evidence the investigation is permitted to use.

    This REUSES the authoritative Wave 4 objects rather than restating them:

        `discovery_population` is a real Wave 4 `DiscoveryPopulation`, so the
        frozen discovery evidence and its `DatasetFingerprint` travel intact and
        the protocol cannot claim a different dataset than the one searched;

        `confirmation_policy` is a real Wave 4 `ConfirmationPolicy`, so the
        confirmation route is preserved exactly as governed -- including the
        honest `PROSPECTIVE_AFTER_T0` case where no holdout exists.

    NO HOLDOUT IS EVER FABRICATED. A discovery population also presented as the
    untouched confirmation evidence is rejected here, exactly as Wave 4 rejects it,
    and a protocol with no discovery population says so honestly by leaving
    `discovery_population_identity` empty.

    `symbol_scope` is a governed reference list rather than free text, so a
    protocol says `symbol:EURUSD` and not "the FX pair".
    """

    evidence_boundary: str
    discovery_population: DiscoveryPopulation | None = None
    confirmation_policy: ConfirmationPolicy | None = None
    symbol_scope: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    dataset_fingerprint_identity: str = ""
    discovery_population_identity: str = ""
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol_scope", _refs(
            self.symbol_scope, "symbol_scope"))
        object.__setattr__(self, "evidence_references", _refs(
            self.evidence_references, "evidence_references"))
        self._validate()
        _encode(self.semantic_material(), "evidence scope semantic material")

    def _validate(self) -> "EvidenceScope":
        if self.schema_version != RESEARCH_PROTOCOL_SCHEMA_VERSION:
            raise ResearchProtocolValidationError(
                f"evidence scope schema_version must be "
                f"{RESEARCH_PROTOCOL_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        _require_text(self.evidence_boundary, "evidence_boundary")
        if self.discovery_population is not None and not isinstance(
                self.discovery_population, DiscoveryPopulation):
            raise ResearchProtocolValidationError(
                f"discovery_population must be a Wave 4 DiscoveryPopulation, got "
                f"{type(self.discovery_population).__name__}; a protocol may not "
                f"invent its own evidence representation")
        if self.confirmation_policy is not None and not isinstance(
                self.confirmation_policy, ConfirmationPolicy):
            raise ResearchProtocolValidationError(
                f"confirmation_policy must be a Wave 4 ConfirmationPolicy, got "
                f"{type(self.confirmation_policy).__name__}; the confirmation route "
                f"is governed, not protocol-local")
        if self.discovery_population is not None:
            # The protocol may not claim a different population or fingerprint
            # than the Wave 4 record it is bound to.
            if (self.discovery_population_identity
                    and self.discovery_population_identity
                    != self.discovery_population.population_identity):
                raise ResearchProtocolValidationError(
                    "declared discovery_population_identity contradicts the bound "
                    "Wave 4 discovery population")
            if self.discovery_population.evidence_boundary != self.evidence_boundary:
                raise ResearchProtocolValidationError(
                    "the bound discovery population's evidence boundary contradicts "
                    "the protocol evidence boundary; a protocol may not widen or "
                    "narrow the frozen evidence of the search it authorises")
        if self.discovery_population_identity and self.discovery_population is None:
            _require_reference(
                self.discovery_population_identity, "discovery_population_identity")
        if self.dataset_fingerprint_identity and not _HEX64_RE.match(
                self.dataset_fingerprint_identity):
            raise ResearchProtocolValidationError(
                "dataset_fingerprint_identity must be a lowercase 64-character "
                "sha256 hex digest")
        if (self.confirmation_policy is not None
                and self.discovery_population is not None):
            # Wave 4 already refuses a discovery population presented as an
            # untouched holdout; re-checking here keeps the invariant local to
            # the protocol that carries it forward.
            self.confirmation_policy.assert_disjoint_from(self.discovery_population)
        if not (self.discovery_population is not None
                or self.discovery_population_identity
                or self.evidence_references):
            raise ResearchProtocolValidationError(
                "an evidence scope must name the evidence it may use: a discovery "
                "population, a governed population reference, or explicit evidence "
                "references. 'No evidence' is never a valid scope.")
        return self


    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_evidence_scope",
            "schema_version": self.schema_version,
            "evidence_boundary": self.evidence_boundary,
            "discovery_population": (
                None if self.discovery_population is None
                else self.discovery_population.semantic_material()),
            "confirmation_policy": (
                None if self.confirmation_policy is None
                else self.confirmation_policy.semantic_material()),
            "symbol_scope": list(self.symbol_scope),
            "evidence_references": list(self.evidence_references),
            "dataset_fingerprint_identity": self.dataset_fingerprint_identity,
            "discovery_population_identity": self.discovery_population_identity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "discovery_population": (
                None if self.discovery_population is None
                else self.discovery_population.to_dict()),
            "confirmation_policy": (
                None if self.confirmation_policy is None
                else self.confirmation_policy.to_dict()),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceScope":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                f"persisted evidence scope must be a mapping, got "
                f"{type(data).__name__}")
        population = data.get("discovery_population")
        policy = data.get("confirmation_policy")
        return cls(
            evidence_boundary=data["evidence_boundary"],
            discovery_population=(
                None if population is None
                else DiscoveryPopulation.from_dict(dict(population))),
            confirmation_policy=(
                None if policy is None
                else ConfirmationPolicy.from_dict(dict(policy))),
            symbol_scope=tuple(data.get("symbol_scope", ())),
            evidence_references=tuple(data.get("evidence_references", ())),
            dataset_fingerprint_identity=data.get("dataset_fingerprint_identity", ""),
            discovery_population_identity=data.get(
                "discovery_population_identity", ""),
            schema_version=data.get(
                "schema_version", RESEARCH_PROTOCOL_SCHEMA_VERSION),
        )


# ═══ Investigation scope ═════════════════════════════════════════════════════


class ScopeComponentKind(str, Enum):
    """
    The kinds of governed element an investigation may place in scope.

    Every entry is a real Wave 1 identity -- `DIM-*`, `IXN-*` or `SLC-*` -- so
    the scope reuses the existing dimension/interaction/slice algebra instead of
    restating it as prose. A protocol can never say "the stop-distance dimension"
    in free text; it can only name the governed `DIM-*` identity, and the
    namespace is enforced at construction.

    This is also how a protocol expresses population segmentation such as symbol
    class, horizon or regime: each is an ADMITTED governed dimension from Wave 1,
    so the protocol inherits that dimension's authority rather than inventing a
    second, weaker notion of "FX" or "SCALP".
    """

    DIMENSION = "DIMENSION"
    INTERACTION = "INTERACTION"
    SLICE = "SLICE"


_SCOPE_IDENTITY_PREDICATES = {
    ScopeComponentKind.DIMENSION: is_dimension_identity,
    ScopeComponentKind.INTERACTION: is_interaction_identity,
    ScopeComponentKind.SLICE: is_slice_identity,
}


def _canonical_components(values: Any, label: str) -> tuple["ScopeComponent", ...]:
    """
    Order-insensitive, duplicate-free component list.

    Caller order is never semantics: two protocols that name the same scope in a
    different order are the same protocol, because the components are sorted by
    `(kind, identity)` before anything is hashed.
    """
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ResearchProtocolValidationError(
            f"{label} must be a sequence of ScopeComponent entries")
    items: list[ScopeComponent] = []
    for entry in values:
        if not isinstance(entry, ScopeComponent):
            raise ResearchProtocolValidationError(
                f"{label} entries must be ScopeComponent instances, got "
                f"{type(entry).__name__}")
        items.append(entry)
    keys = [(item.kind.value, item.identity) for item in items]
    if len(set(keys)) != len(keys):
        raise ResearchProtocolValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(items, key=lambda item: (item.kind.value, item.identity)))



@dataclass(frozen=True)
class ScopeComponent:
    """
    ONE governed element of an investigation's scope, positively or negatively.

    `in_scope` components are what the investigation MAY examine. `excluded`
    components are what it MAY NOT -- naming them explicitly is the point: an
    excluded `DIM-*` is a mechanical refusal in `assert_scope_consistent`, not an
    omission a future reader has to infer.
    """

    kind: ScopeComponentKind
    identity: str
    in_scope: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _coerce_enum(
            self.kind, ScopeComponentKind, "scope component kind"))
        self._validate()
        _encode(self.semantic_material(), "scope component semantic material")

    def _validate(self) -> "ScopeComponent":
        if not isinstance(self.kind, ScopeComponentKind):
            raise ProtocolNamespaceViolation(
                f"scope component kind must be governed, got {self.kind!r}")
        if not isinstance(self.in_scope, bool):
            raise ResearchProtocolValidationError(
                f"scope component in_scope must be a boolean, got {self.in_scope!r}")
        predicate = _SCOPE_IDENTITY_PREDICATES[self.kind]
        if not predicate(self.identity):
            raise ProtocolNamespaceViolation(
                f"scope component {self.identity!r} is not a governed "
                f"{self.kind.value} identity; investigation scope reuses the Wave 1 "
                f"dimension/interaction/slice algebra and never accepts free text")
        return self

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_scope_component",
            "schema_version": RESEARCH_PROTOCOL_SCHEMA_VERSION,
            "component_kind": self.kind.value,
            "identity": self.identity,
            "in_scope": self.in_scope,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.semantic_material()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopeComponent":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted scope component must be a mapping")
        return cls(
            kind=ScopeComponentKind(data["component_kind"]),
            identity=data["identity"],
            in_scope=data["in_scope"],
        )


@dataclass(frozen=True)
class InvestigationScope:
    """
    WHAT THE INVESTIGATION IS ABOUT -- and what it is not.

    `subject_kind` / `subject_ref` are copied VERBATIM from the bound Wave 5
    opportunity: the subject namespace was already validated by Wave 5, and
    re-deriving it here would create a second, weaker answer to "what is this
    about?". `assert_binds_to(opportunity)` re-proves the equality, so a protocol
    can never drift onto a subject its authorisation does not cover.

    `in_scope` must be non-empty. A protocol that investigates nothing is not a
    protocol; it is an ungoverned idea wearing a protocol's name.
    """

    subject_kind: OpportunitySubjectKind
    subject_ref: str
    in_scope: tuple[ScopeComponent, ...] = ()
    excluded: tuple[ScopeComponent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_kind", _coerce_enum(
            self.subject_kind, OpportunitySubjectKind, "investigation subject kind"))
        object.__setattr__(self, "in_scope", _canonical_components(
            self.in_scope, "in_scope"))
        object.__setattr__(self, "excluded", _canonical_components(
            self.excluded, "excluded"))
        self._validate()
        _encode(self.semantic_material(), "investigation scope semantic material")

    def _validate(self) -> "InvestigationScope":
        _require_text(self.subject_ref, "investigation subject_ref")
        if not self.in_scope:
            raise ResearchProtocolValidationError(
                "an investigation scope must declare at least one in-scope governed "
                "component; a protocol that examines nothing is not an investigation")
        in_scope_keys = {(item.kind, item.identity) for item in self.in_scope}
        excluded_keys = {(item.kind, item.identity) for item in self.excluded}
        overlap = sorted(in_scope_keys & excluded_keys)
        if overlap:
            raise ResearchProtocolValidationError(
                f"a component may not be both in scope and excluded: {overlap}")
        return self

    def in_scope_identities(self) -> tuple[str, ...]:
        return tuple(item.identity for item in self.in_scope)

    def excluded_identities(self) -> tuple[str, ...]:
        return tuple(item.identity for item in self.excluded)

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_investigation_scope",
            "schema_version": RESEARCH_PROTOCOL_SCHEMA_VERSION,
            "subject_kind": self.subject_kind.value,
            "subject_ref": self.subject_ref,
            "in_scope": [item.semantic_material() for item in self.in_scope],
            "excluded": [item.semantic_material() for item in self.excluded],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "in_scope": [item.to_dict() for item in self.in_scope],
            "excluded": [item.to_dict() for item in self.excluded],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InvestigationScope":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted investigation scope must be a mapping")
        return cls(
            subject_kind=OpportunitySubjectKind(data["subject_kind"]),
            subject_ref=data["subject_ref"],
            in_scope=tuple(ScopeComponent.from_dict(row) for row in data["in_scope"]),
            excluded=tuple(ScopeComponent.from_dict(row) for row in data["excluded"]),
        )



# ═══ Comparison specification ════════════════════════════════════════════════


class ComparisonKind(str, Enum):
    """
    The closed set of comparisons a protocol may pre-declare.

    Every member is a COMPARISON, evaluated as analysis. None of them is a
    treatment, and `COUNTERFACTUAL_WITHOUT_TREATMENT` explicitly evaluates what
    would have happened WITHOUT applying anything -- the reason it is a separate,
    named kind rather than an alias for `BASELINE_VS_GOVERNED_ALTERNATIVE`.
    """

    BASELINE_VS_GOVERNED_ALTERNATIVE = "BASELINE_VS_GOVERNED_ALTERNATIVE"
    DIMENSION_A_VS_DIMENSION_B = "DIMENSION_A_VS_DIMENSION_B"
    SLICE_A_VS_SLICE_B = "SLICE_A_VS_SLICE_B"
    COUNTERFACTUAL_WITHOUT_TREATMENT = "COUNTERFACTUAL_WITHOUT_TREATMENT"
    HISTORICAL_VS_PROSPECTIVE = "HISTORICAL_VS_PROSPECTIVE"
    NONE = "NONE"


@dataclass(frozen=True)
class ComparisonTerm:
    """
    ONE side of a pre-declared comparison.

    `label` is a governed token, not prose, and is provenance only: the scientific
    content of a term is its `reference` plus the governed identities it names.
    `required` records whether a missing side makes the whole investigation
    INSUFFICIENT rather than merely incomplete, which is the distinction the
    insufficiency criteria later depend on.
    """

    reference: str
    label: str = ""
    identity: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        _require_reference(self.reference, "comparison term reference")
        if self.label:
            _require_token(self.label, "comparison term label")
        if self.identity and not is_wave4_identity(self.identity):
            raise ProtocolNamespaceViolation(
                f"comparison term identity {self.identity!r} is not a governed Wave 4 "
                f"identity; a comparison side must name governed search material")
        if not isinstance(self.required, bool):
            raise ResearchProtocolValidationError(
                f"comparison term required must be a boolean, got {self.required!r}")
        _encode(self.semantic_material(), "comparison term semantic material")

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_comparison_term",
            "schema_version": RESEARCH_PROTOCOL_SCHEMA_VERSION,
            "reference": self.reference,
            "identity": self.identity,
            "required": self.required,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_material(), "label": self.label}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComparisonTerm":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted comparison term must be a mapping")
        return cls(
            reference=data["reference"],
            label=data.get("label", ""),
            identity=data.get("identity", ""),
            required=data.get("required", True),
        )



@dataclass(frozen=True)
class ComparisonSpecification:
    """
    WHAT THE INVESTIGATION WILL COMPARE -- decided before any result exists.

    The specification is identity material, so changing the comparison after
    seeing an answer cannot be done quietly: it produces a different protocol
    identity and, if the original was already frozen, an explicit supersession.

    `NONE` is an honest, first-class value meaning "this investigation makes no
    comparison". It is not a default to be filled in later, and a non-`NONE` kind
    with a missing side is rejected rather than completed on read.
    """

    kind: ComparisonKind
    left: ComparisonTerm | None = None
    right: ComparisonTerm | None = None
    rationale_code: str = ""
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _coerce_enum(
            self.kind, ComparisonKind, "comparison kind"))
        if self.rationale_code:
            object.__setattr__(self, "rationale_code", _require_reason(
                self.rationale_code, "comparison rationale_code"))
        self._validate()
        _encode(self.semantic_material(), "comparison specification semantic material")

    def _validate(self) -> "ComparisonSpecification":
        if self.kind is ComparisonKind.NONE:
            if self.left is not None or self.right is not None:
                raise ResearchProtocolValidationError(
                    "a comparison of kind NONE may not carry a term")
            return self
        for name, term in (("left", self.left), ("right", self.right)):
            if not isinstance(term, ComparisonTerm):
                raise ResearchProtocolValidationError(
                    f"a {self.kind.value} comparison requires a {name} "
                    f"ComparisonTerm, got {type(term).__name__}; a half-specified "
                    f"comparison may not be completed after results are seen")
        if self.left.reference == self.right.reference:
            raise ResearchProtocolValidationError(
                "a comparison must name two different sides; comparing a governed "
                "alternative with itself is not a comparison")
        if (self.left.identity and self.right.identity
                and self.left.identity == self.right.identity):
            raise ResearchProtocolValidationError(
                f"a {self.kind.value} comparison must name two different governed "
                f"identities")
        return self

    @property
    def is_declared(self) -> bool:
        return self.kind is not ComparisonKind.NONE

    def applies_treatment(self) -> bool:
        """
        Always False, structurally.

        No comparison kind in Stage 3 applies a treatment. A counterfactual is
        evaluated, never imposed, and this accessor exists so a future executor has
        a single place to ask and can never be told "yes".
        """
        return False

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_comparison_specification",
            "schema_version": self.schema_version,
            "comparison_kind": self.kind.value,
            "left": None if self.left is None else self.left.semantic_material(),
            "right": None if self.right is None else self.right.semantic_material(),
            "rationale_code": self.rationale_code,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "left": None if self.left is None else self.left.to_dict(),
            "right": None if self.right is None else self.right.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComparisonSpecification":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted comparison specification must be a mapping")
        left, right = data.get("left"), data.get("right")
        return cls(
            kind=ComparisonKind(data["comparison_kind"]),
            left=None if left is None else ComparisonTerm.from_dict(dict(left)),
            right=None if right is None else ComparisonTerm.from_dict(dict(right)),
            rationale_code=data.get("rationale_code", ""),
            schema_version=data.get(
                "schema_version", RESEARCH_PROTOCOL_SCHEMA_VERSION),
        )


class EscalationRoute(str, Enum):
    """
    The governed path an out-of-scope discovery MUST take.

    `CURIOSITY_LIFECYCLE` names the EXISTING Wave 3 route: a governed curiosity
    signal, then a proposal, then Wave 2 feasibility and eligibility, then a Wave
    5 opportunity, then a new protocol. There is deliberately no
    `EXPAND_CURRENT_PROTOCOL` member, because that is the behaviour this entire
    rule exists to forbid.
    """

    CURIOSITY_LIFECYCLE = "CURIOSITY_LIFECYCLE"


class EscalationRoute(str, Enum):
    """
    The governed path an out-of-scope discovery MUST take.

    `CURIOSITY_LIFECYCLE` names the EXISTING Wave 3 route: a governed curiosity
    signal, then a proposal, then Wave 2 feasibility and eligibility, then a Wave
    5 opportunity, then a new protocol. There is deliberately no
    `EXPAND_CURRENT_PROTOCOL` member, because that is the behaviour this entire
    rule exists to forbid.
    """

    CURIOSITY_LIFECYCLE = "CURIOSITY_LIFECYCLE"


# ═══ Scope-change and discovery policy ══════════════════════════════════════


@dataclass(frozen=True)
class ScopeChangePolicy:
    """
    THE RULE THAT DISCOVERY NEVER EXPANDS A PROTOCOL.

    An investigation will, if it is any use, turn up something outside its declared
    scope. The governed answer is always: a NEW piece of governed research work
    through the existing Wave 3+ curiosity lifecycle -- never a widened `RPL-*`.

    `discovery_expands_protocol` is identity material precisely so that a protocol
    claiming expansion is a DIFFERENT protocol, and one that claims expansion is
    REJECTED here rather than accepted and quietly ignored. A protocol cannot widen
    itself by declaring the right to.

    `escalation_route` is the governed path an out-of-scope discovery must take. It
    is a closed reference, and `CURIOSITY_LIFECYCLE` names the existing Wave 3
    signal/proposal route rather than inventing a new mechanism.
    """

    discovery_expands_protocol: bool = False
    escalation_route: EscalationRoute = EscalationRoute.CURIOSITY_LIFECYCLE
    require_new_protocol_for_change: bool = True
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("discovery_expands_protocol", "require_new_protocol_for_change"):
            if not isinstance(getattr(self, name), bool):
                raise ResearchProtocolValidationError(
                    f"scope change policy {name} must be a boolean, got "
                    f"{getattr(self, name)!r}")
        object.__setattr__(self, "escalation_route", _coerce_enum(
            self.escalation_route, EscalationRoute, "scope change escalation_route"))
        if self.discovery_expands_protocol:
            raise ResearchProtocolValidationError(
                "a Stage 3 research protocol may never authorise discovery to expand "
                "its own scope; an out-of-scope discovery becomes NEW governed "
                "research work through the Wave 3 curiosity lifecycle")
        if not self.require_new_protocol_for_change:
            raise ResearchProtocolValidationError(
                "a Stage 3 research protocol may never permit its own specification "
                "to be changed in place; a changed investigation is a NEW protocol "
                "identity with explicit supersession")
        _encode(self.semantic_material(), "scope change policy semantic material")

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_scope_change_policy",
            "schema_version": self.schema_version,
            "discovery_expands_protocol": self.discovery_expands_protocol,
            "escalation_route": self.escalation_route.value,
            "require_new_protocol_for_change": self.require_new_protocol_for_change,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.semantic_material()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopeChangePolicy":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted scope change policy must be a mapping")
        return cls(
            discovery_expands_protocol=data["discovery_expands_protocol"],
            escalation_route=EscalationRoute(data["escalation_route"]),
            require_new_protocol_for_change=data["require_new_protocol_for_change"],
            schema_version=data.get(
                "schema_version", RESEARCH_PROTOCOL_SCHEMA_VERSION),
        )



# ═══ Authorisation ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProtocolAuthorisation:
    """
    THE PROOF THAT THIS INVESTIGATION IS ALLOWED TO EXIST.

    The `ROP-*` opportunity is the one mandatory link: without it there is no
    governed research opportunity to investigate, and therefore no protocol. The
    `AGD-*` / `QUE-*` / `AFR-*` links are recorded whenever the corresponding Wave 5
    state exists, so the full path

        ROP-* -> AGD-* -> QUE-* -> AFR-* -> RPL-*

    survives into the protocol. They are individually optional because Wave 5 does
    not require every opportunity to be queued, but they are NOT free: a queue
    identity or an agenda-freeze identity presented without its agenda is rejected,
    so a protocol can never imply a governance path that does not exist.

    `opportunity_state` carries the Wave 5 state forward VERBATIM. A protocol may
    only be authored over `READY` work: BLOCKED, WAITING_DATA, REFUSED, DEFERRED and
    COMPLETE opportunities are structurally impossible investigations, and refusing
    to authorise them is Wave 5's verdict, not Wave 6's to overturn.
    """

    opportunity_identity: str
    opportunity_state: str
    agenda_identity: str = ""
    queue_identity: str = ""
    agenda_freeze_identity: str = ""
    parent_research_refs: tuple[str, ...] = ()
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_research_refs", _refs(
            self.parent_research_refs, "parent_research_refs"))
        self._validate()
        _encode(self.semantic_material(), "authorisation semantic material")

    def _validate(self) -> "ProtocolAuthorisation":
        if not is_research_opportunity_identity(self.opportunity_identity):
            raise ProtocolAuthorisationError(
                f"a research protocol must bind an authoritative Wave 5 `ROP-*` "
                f"opportunity, got {self.opportunity_identity!r}; an ungoverned idea "
                f"may never be pre-registered as an investigation")
        if not isinstance(self.opportunity_state, str):
            raise ProtocolAuthorisationError("opportunity_state must be a string")
        if self.opportunity_state != OpportunityState.READY.value:
            raise ProtocolAuthorisationError(
                f"a research protocol may only be authored over a Wave 5 READY "
                f"opportunity, got {self.opportunity_state!r}; the other Wave 5 states "
                f"are governed verdicts that Wave 6 does not overturn")
        for name, predicate, prefix in (
            ("agenda_identity", is_research_agenda_identity, "AGD-"),
            ("queue_identity", is_research_queue_identity, "QUE-"),
            ("agenda_freeze_identity", is_agenda_freeze_identity, "AFR-"),
        ):
            value = getattr(self, name)
            if value and not predicate(value):
                raise ProtocolNamespaceViolation(
                    f"authorisation {name} {value!r} is not a governed {prefix} "
                    f"identity")
        if (self.queue_identity or self.agenda_freeze_identity) and (
                not self.agenda_identity):
            raise ProtocolAuthorisationError(
                "a queue or agenda-freeze identity may not be presented without the "
                "agenda identity that produced it; a protocol may not imply a Wave 5 "
                "governance path that does not exist")
        return self

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "protocol_authorisation",
            "schema_version": self.schema_version,
            "opportunity_identity": self.opportunity_identity,
            "opportunity_state": self.opportunity_state,
            "agenda_identity": self.agenda_identity,
            "queue_identity": self.queue_identity,
            "agenda_freeze_identity": self.agenda_freeze_identity,
            "parent_research_refs": list(self.parent_research_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.semantic_material()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtocolAuthorisation":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                "persisted authorisation must be a mapping")
        return cls(
            opportunity_identity=data["opportunity_identity"],
            opportunity_state=data["opportunity_state"],
            agenda_identity=data.get("agenda_identity", ""),
            queue_identity=data.get("queue_identity", ""),
            agenda_freeze_identity=data.get("agenda_freeze_identity", ""),
            parent_research_refs=tuple(data.get("parent_research_refs", ())),
            schema_version=data.get(
                "schema_version", RESEARCH_PROTOCOL_SCHEMA_VERSION),
        )



# ═══ The governed research protocol ═════════════════════════════════════════


@dataclass(frozen=True)
class ResearchProtocol:
    """
    ONE intended investigation, completely specified BEFORE its result exists.

    IDENTITY MATERIAL (hashed): protocol version, authorisation, investigation
    scope, evidence scope, permitted operations, prohibited operations, the
    comparison specification, completion / insufficiency / invalidation criteria,
    the confirmation requirement, the scope-change policy, the Wave 4 search
    provenance, and supersession.

    NOT IDENTITY MATERIAL (provenance only): `label`, `note`, `provenance`,
    `created_at` and `status`. A note cannot change what an investigation means,
    and neither can a clock nor a lifecycle transition.

    Consequently, and this is the property the whole module exists for:

      - changing a dimension, a comparison, an operation or a criterion
        necessarily produces a DIFFERENT `RPL-*` identity;
      - changing only a label, a note or a timestamp produces the SAME identity.

    A frozen protocol is immutable. `supersede()` does not edit it: it builds a
    NEW protocol carrying `supersedes`/`supersession_reason`, leaving the original
    permanently inspectable exactly as it was.
    """

    authorisation: ProtocolAuthorisation
    scope: InvestigationScope
    evidence: EvidenceScope
    permitted_operations: tuple[ResearchOperation, ...]
    completion_criteria: tuple[CompletionCriterionKind, ...]
    insufficiency_criteria: tuple[InsufficiencyCriterionKind, ...]
    invalidation_criteria: tuple[InvalidationCriterionKind, ...]
    comparison: ComparisonSpecification
    confirmation_requirement: ConfirmationRequirement
    scope_change_policy: ScopeChangePolicy = field(
        default_factory=ScopeChangePolicy)
    prohibited_operations: tuple[ProhibitedOperation, ...] = field(
        default_factory=lambda: tuple(sorted(
            ProhibitedOperation, key=lambda item: item.value)))
    protocol_version: int = 1
    search_provenance: Mapping[str, Any] | None = None
    supersedes: str = ""
    supersession_reason: SupersessionReason | None = None
    status: ProtocolStatus = ProtocolStatus.DRAFT
    label: str = ""                        # provenance only; NOT identity
    note: str = ""                         # provenance only; NOT identity
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""                   # provenance only; NOT identity
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION
    semantic_identity: str = ""
    protocol_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "permitted_operations", _operation_list(
            self.permitted_operations, "permitted_operations"))
        object.__setattr__(self, "prohibited_operations", _prohibited_list(
            self.prohibited_operations, "prohibited_operations"))
        object.__setattr__(self, "completion_criteria", _criterion_list(
            self.completion_criteria, CompletionCriterionKind,
            "completion_criteria"))
        object.__setattr__(self, "insufficiency_criteria", _criterion_list(
            self.insufficiency_criteria, InsufficiencyCriterionKind,
            "insufficiency_criteria"))
        object.__setattr__(self, "invalidation_criteria", _criterion_list(
            self.invalidation_criteria, InvalidationCriterionKind,
            "invalidation_criteria"))
        if isinstance(self.confirmation_requirement, str) and not isinstance(
                self.confirmation_requirement, ConfirmationRequirement):
            object.__setattr__(self, "confirmation_requirement", _coerce_enum(
                self.confirmation_requirement, ConfirmationRequirement,
                "confirmation_requirement"))
        object.__setattr__(self, "scope_change_policy", _require_instance(
            self.scope_change_policy, ScopeChangePolicy, "scope_change_policy"))
        object.__setattr__(self, "status", _coerce_enum(
            self.status, ProtocolStatus, "protocol status"))
        if self.supersession_reason is not None:
            object.__setattr__(self, "supersession_reason", _coerce_enum(
                self.supersession_reason, SupersessionReason,
                "supersession_reason"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = research_protocol_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchProtocolValidationError(
                    "presented protocol semantic identity does not match the protocol "
                    "material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.protocol_identity:
            if self.protocol_identity != expected_id:
                raise ResearchProtocolValidationError(
                    f"presented protocol identity {self.protocol_identity!r} does not "
                    f"match the protocol material")
        else:
            object.__setattr__(self, "protocol_identity", expected_id)


    @classmethod
    def for_opportunity(
        cls,
        opportunity: ResearchOpportunity,
        *,
        scope: InvestigationScope,
        evidence: EvidenceScope,
        permitted_operations: tuple[ResearchOperation, ...],
        completion_criteria: tuple[CompletionCriterionKind, ...],
        insufficiency_criteria: tuple[InsufficiencyCriterionKind, ...],
        invalidation_criteria: tuple[InvalidationCriterionKind, ...],
        comparison: ComparisonSpecification,
        confirmation_requirement: ConfirmationRequirement,
        scope_change_policy: ScopeChangePolicy | None = None,
        prohibited_operations: tuple[ProhibitedOperation, ...] | None = None,
        agenda_identity: str = "",
        queue_identity: str = "",
        agenda_freeze_identity: str = "",
        protocol_version: int = 1,
        supersedes: str = "",
        supersession_reason: SupersessionReason | None = None,
        label: str = "",
        note: str = "",
        provenance: Mapping[str, Any] | None = None,
        created_at: str = "",
    ) -> "ResearchProtocol":
        """
        The ONLY construction path: bind a protocol to a governed opportunity.

        The subject, the Wave 5 state and the Wave 4 provenance are taken from the
        OPPORTUNITY rather than from the caller, so a protocol cannot be pointed at
        a subject its authorisation does not cover, and cannot re-state a Wave 4
        search block the opportunity does not carry.
        """
        if not isinstance(opportunity, ResearchOpportunity):
            raise ProtocolAuthorisationError(
                f"expected a governed ResearchOpportunity, got "
                f"{type(opportunity).__name__}")
        return cls(
            authorisation=ProtocolAuthorisation(
                opportunity_identity=opportunity.opportunity_identity,
                opportunity_state=opportunity.state.value,
                agenda_identity=agenda_identity,
                queue_identity=queue_identity,
                agenda_freeze_identity=agenda_freeze_identity,
                parent_research_refs=opportunity.parent_research_refs,
            ),
            scope=scope,
            evidence=evidence,
            permitted_operations=permitted_operations,
            completion_criteria=completion_criteria,
            insufficiency_criteria=insufficiency_criteria,
            invalidation_criteria=invalidation_criteria,
            comparison=comparison,
            confirmation_requirement=confirmation_requirement,
            search_provenance=opportunity.search_provenance,
            scope_change_policy=(
                scope_change_policy if scope_change_policy is not None
                else ScopeChangePolicy()),
            **({} if prohibited_operations is None
               else {"prohibited_operations": prohibited_operations}),
            protocol_version=protocol_version,
            supersedes=supersedes,
            supersession_reason=supersession_reason,
            label=label,
            note=note,
            provenance=dict(provenance or {}),
            created_at=created_at,
        )

    def _validate(self) -> "ResearchProtocol":
        if self.schema_version != RESEARCH_PROTOCOL_SCHEMA_VERSION:
            raise ResearchProtocolValidationError(
                f"research protocol schema_version must be "
                f"{RESEARCH_PROTOCOL_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if isinstance(self.protocol_version, bool) or not isinstance(
                self.protocol_version, int):
            raise ResearchProtocolValidationError(
                f"protocol_version must be an integer, got {self.protocol_version!r}")
        if self.protocol_version < 1:
            raise ResearchProtocolValidationError(
                f"protocol_version must be at least 1, got {self.protocol_version}")
        _require_instance(self.authorisation, ProtocolAuthorisation, "authorisation")
        _require_instance(self.scope, InvestigationScope, "scope")
        _require_instance(self.evidence, EvidenceScope, "evidence")
        _require_instance(self.comparison, ComparisonSpecification, "comparison")
        if not isinstance(self.confirmation_requirement, ConfirmationRequirement):
            raise ResearchProtocolValidationError(
                f"confirmation_requirement must be a governed Wave 5 "
                f"ConfirmationRequirement, got {self.confirmation_requirement!r}")

        # A protocol may not simultaneously permit and prohibit the same thing.
        overlap = sorted({item.value for item in self.permitted_operations} & {
            item.value for item in self.prohibited_operations})
        if overlap:
            raise ProhibitedAuthorityError(
                f"operations may not be both permitted and prohibited: {overlap}")

        # The Wave 4 provenance, when present, is copied verbatim and fully.
        if self.search_provenance is not None:
            self._validate_wave4_provenance()

        # A protocol carrying a Wave 4 discovery population must say which one.
        if (self.evidence.discovery_population is not None
                and not self.evidence.discovery_population_identity):
            raise ResearchProtocolValidationError(
                "a protocol carrying a Wave 4 discovery population must also declare "
                "its population identity")

        # Supersession is either absent or complete, and never self-referential.
        if self.supersedes and not is_research_protocol_identity(self.supersedes):
            raise ProtocolNamespaceViolation(
                f"supersedes {self.supersedes!r} is not a governed `RPL-*` protocol "
                f"identity")
        if bool(self.supersedes) != bool(self.supersession_reason):
            raise ResearchProtocolValidationError(
                "a protocol that supersedes another must declare a governed "
                "supersession_reason, and one that does not supersede must not carry a "
                "reason")
        if self.supersedes and self.supersedes == self.protocol_identity:
            raise ResearchProtocolValidationError("a protocol may not supersede itself")
        _encode(self.semantic_material(), "research protocol semantic material")
        return self


    def _validate_wave4_provenance(self) -> None:
        """
        The Wave 4 block is copied verbatim; a protocol never reconstructs it.

        A protocol contradiction with frozen Wave 4 provenance must fail closed, so
        a re-pointed selection freeze, a dropped multiplicity denominator, a
        weakened confirmation route or a re-dated evidence boundary is rejected
        rather than tolerated.
        """
        if not isinstance(self.search_provenance, Mapping):
            raise ResearchProtocolValidationError(
                f"search_provenance must be a mapping, got "
                f"{type(self.search_provenance).__name__}")
        required = (
            "search_record_id", "search_family_id", "selection_freeze_id",
            "alternatives_considered", "multiplicity_family_size",
            "multiplicity_rule", "correction_method", "discovery_population_identity",
            "discovery_fingerprint_identity", "discovery_evidence_boundary",
            "selection_criterion", "confirmation_policy",
        )
        missing = [key for key in required if key not in self.search_provenance]
        if missing:
            raise ResearchProtocolValidationError(
                f"search_provenance is missing required Wave 4 keys: {missing}; a "
                f"protocol may not omit the multiplicity or freeze provenance it was "
                f"built from")
        for key in ("search_record_id", "search_family_id", "selection_freeze_id"):
            if not is_wave4_identity(self.search_provenance[key]):
                raise ProtocolNamespaceViolation(
                    f"search_provenance[{key!r}] is not a governed Wave 4 identity: "
                    f"{self.search_provenance[key]!r}")
        # A protocol that carries a Wave 4 selection freeze must actually be about
        # one; otherwise the provenance would be fabricated.
        if (self.scope.subject_kind in WAVE4_SUBJECT_KINDS
                and self.scope.subject_ref
                != self.search_provenance["selection_freeze_id"]):
            raise ProtocolAuthorisationError(
                "the investigation subject does not match the Wave 4 selection freeze "
                "the protocol claims to authorise")
        # The confirmation route in the Wave 4 block and in the evidence scope must
        # agree; a protocol may not quietly weaken a governed route.
        if (self.evidence.confirmation_policy is not None
                and self.evidence.confirmation_policy.semantic_material()
                != self.search_provenance["confirmation_policy"]):
            raise ResearchProtocolValidationError(
                "the protocol's evidence-scope confirmation policy contradicts the "
                "frozen Wave 4 confirmation policy it carries")
        # The frozen evidence boundary travels with the search.
        if (self.evidence.evidence_boundary
                != self.search_provenance["discovery_evidence_boundary"]):
            raise ResearchProtocolValidationError(
                "the protocol's evidence boundary contradicts the frozen Wave 4 "
                "discovery evidence boundary; a protocol may not re-date the evidence "
                "its search was run on")
        # The discovery population, when bound, must be the frozen one.
        if (self.evidence.discovery_population is not None
                and self.evidence.discovery_population.population_identity
                != self.search_provenance["discovery_population_identity"]):
            raise ResearchProtocolValidationError(
                "the bound discovery population contradicts the frozen Wave 4 discovery "
                "population identity")

    # ─── Identity ────────────────────────────────────────────────────────

    def semantic_material(self) -> dict[str, Any]:
        """
        The EXACT identity material of an investigation.

        Everything a reader would need to know what this investigation means, and
        nothing a reader would need in order to reconstruct WHEN or WHO wrote it.
        `label`, `note`, `provenance`, `created_at` and `status` are absent by
        design.
        """
        return {
            "kind": "research_protocol",
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "authorisation": self.authorisation.semantic_material(),
            "scope": self.scope.semantic_material(),
            "evidence": self.evidence.semantic_material(),
            "permitted_operations": [
                item.value for item in self.permitted_operations],
            "prohibited_operations": [
                item.value for item in self.prohibited_operations],
            "comparison": self.comparison.semantic_material(),
            "completion_criteria": [item.value for item in self.completion_criteria],
            "insufficiency_criteria": [
                item.value for item in self.insufficiency_criteria],
            "invalidation_criteria": [
                item.value for item in self.invalidation_criteria],
            "confirmation_requirement": self.confirmation_requirement.value,
            "scope_change_policy": self.scope_change_policy.semantic_material(),
            "search_provenance": (
                None if self.search_provenance is None
                else dict(self.search_provenance)),
            "supersedes": self.supersedes,
            "supersession_reason": (
                None if self.supersession_reason is None
                else self.supersession_reason.value),
        }


    # ─── Binding proof ───────────────────────────────────────────────────

    def assert_binds_to(
            self, opportunity: ResearchOpportunity) -> "ResearchProtocol":
        """
        Fail closed unless this protocol is provably about THIS opportunity.

        This is the mechanical form of "a protocol may never be free-floating". It
        re-proves the opportunity identity, the Wave 5 state, the subject, the
        evidence boundary and the Wave 4 provenance block, so a protocol that was
        detached from, or re-pointed at, its authorisation cannot be registered,
        frozen or compared against a governed agenda.
        """
        if not isinstance(opportunity, ResearchOpportunity):
            raise ProtocolAuthorisationError(
                f"expected a governed ResearchOpportunity, got "
                f"{type(opportunity).__name__}")
        if (self.authorisation.opportunity_identity
                != opportunity.opportunity_identity):
            raise ProtocolAuthorisationError(
                f"protocol {self.protocol_identity} authorises "
                f"{self.authorisation.opportunity_identity}, not "
                f"{opportunity.opportunity_identity}")
        if self.authorisation.opportunity_state != opportunity.state.value:
            raise ProtocolAuthorisationError(
                f"protocol {self.protocol_identity} records Wave 5 state "
                f"{self.authorisation.opportunity_state!r}, but the opportunity is "
                f"{opportunity.state.value!r}")
        if self.scope.subject_kind is not opportunity.subject_kind:
            raise ProtocolAuthorisationError(
                f"protocol subject kind {self.scope.subject_kind.value!r} does not "
                f"match the opportunity's {opportunity.subject_kind.value!r}")
        if self.scope.subject_ref != opportunity.subject_ref:
            raise ProtocolAuthorisationError(
                f"protocol subject {self.scope.subject_ref!r} is not the governed "
                f"subject {opportunity.subject_ref!r} of its opportunity; a protocol may "
                f"never be re-pointed at a different subject")
        if ((self.search_provenance or None)
                != (opportunity.search_provenance or None)):
            raise ProtocolAuthorisationError(
                "protocol Wave 4 provenance does not match the opportunity's; a "
                "protocol may not alter, omit or re-point search history")
        if self.evidence.evidence_boundary != opportunity.evidence_boundary:
            raise ProtocolAuthorisationError(
                f"protocol evidence boundary {self.evidence.evidence_boundary!r} "
                f"contradicts the opportunity's {opportunity.evidence_boundary!r}")
        if self.authorisation.parent_research_refs != tuple(
                opportunity.parent_research_refs):
            raise ProtocolAuthorisationError(
                "protocol parent research references contradict the opportunity's")
        return self


    # ─── Supersession (never in-place mutation) ──────────────────────────

    def supersede(
        self,
        *,
        reason: SupersessionReason,
        opportunity: ResearchOpportunity,
        scope: InvestigationScope | None = None,
        evidence: EvidenceScope | None = None,
        permitted_operations: tuple[ResearchOperation, ...] | None = None,
        comparison: ComparisonSpecification | None = None,
        completion_criteria: tuple[CompletionCriterionKind, ...] | None = None,
        insufficiency_criteria: tuple[
            InsufficiencyCriterionKind, ...] | None = None,
        invalidation_criteria: tuple[InvalidationCriterionKind, ...] | None = None,
        confirmation_requirement: ConfirmationRequirement | None = None,
        protocol_version: int | None = None,
        label: str = "",
        note: str = "",
        created_at: str = "",
    ) -> "ResearchProtocol":
        """
        Produce a NEW protocol that supersedes this one. NEVER an edit.

        The superseded protocol is returned untouched and stays permanently
        inspectable: the new record carries `supersedes=<this identity>` and a
        governed reason, is a different `RPL-*` identity, and is a strict successor
        in the history. There is deliberately no parameter that could rewrite the
        original, so a post-hoc change is always visible as a new record rather than
        a quiet redefinition of the old one.

        `reason` is a CLOSED governed code. "We changed our minds after seeing the
        result" is not expressible, and that is intentional: a changed protocol must
        never masquerade as the original historical investigation.
        """
        if self.status is ProtocolStatus.SUPERSEDED:
            raise ResearchProtocolValidationError(
                f"protocol {self.protocol_identity} is already SUPERSEDED and may not "
                f"be superseded again")
        return ResearchProtocol.for_opportunity(
            opportunity,
            scope=scope if scope is not None else self.scope,
            evidence=evidence if evidence is not None else self.evidence,
            permitted_operations=(
                permitted_operations if permitted_operations is not None
                else self.permitted_operations),
            completion_criteria=(
                completion_criteria if completion_criteria is not None
                else self.completion_criteria),
            insufficiency_criteria=(
                insufficiency_criteria if insufficiency_criteria is not None
                else self.insufficiency_criteria),
            invalidation_criteria=(
                invalidation_criteria if invalidation_criteria is not None
                else self.invalidation_criteria),
            comparison=comparison if comparison is not None else self.comparison,
            confirmation_requirement=(
                confirmation_requirement if confirmation_requirement is not None
                else self.confirmation_requirement),
            agenda_identity=self.authorisation.agenda_identity,
            queue_identity=self.authorisation.queue_identity,
            agenda_freeze_identity=self.authorisation.agenda_freeze_identity,
            protocol_version=(
                protocol_version if protocol_version is not None
                else self.protocol_version + 1),
            supersedes=self.protocol_identity,
            supersession_reason=reason,
            label=label or self.label,
            note=note,
            created_at=created_at,
        )

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def with_status(self, status: ProtocolStatus | str) -> "ResearchProtocol":
        """
        Move along the governed lifecycle.

        `status` is provenance only, so the identity is UNCHANGED by this call: a
        reader can tell that a protocol reached FROZEN without that fact redefining
        what the protocol investigates. Only the forward transitions in
        `PROTOCOL_STATUS_TRANSITIONS` are legal: a FROZEN protocol can never be
        reopened, because reopening it is how a post-hoc rewrite would begin.
        """
        target = _coerce_enum(status, ProtocolStatus, "protocol status")
        if target is self.status:
            return self
        if target not in PROTOCOL_STATUS_TRANSITIONS[self.status]:
            raise ResearchProtocolValidationError(
                f"illegal protocol status transition {self.status.value} -> "
                f"{target.value}; the permitted transitions are "
                f"{[item.value for item in PROTOCOL_STATUS_TRANSITIONS[self.status]]}. "
                f"A frozen protocol is never reopened and a superseded protocol is "
                f"never revived; a changed investigation is a NEW protocol.")
        return replace(self, status=target)


    # ─── Serialisation ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "status": self.status.value,
            "label": self.label,
            "note": self.note,
            "provenance": dict(self.provenance or {}),
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "protocol_identity": self.protocol_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchProtocol":
        """
        Strict deserialisation. Corrupt or self-contradictory content fails closed
        rather than being repaired, because a silently repaired protocol is a
        protocol that no longer says what it says.
        """
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                f"persisted protocol must be a mapping, got {type(data).__name__}")
        expected = {
            "kind", "schema_version", "protocol_version", "authorisation", "scope",
            "evidence", "permitted_operations", "prohibited_operations",
            "comparison", "completion_criteria", "insufficiency_criteria",
            "invalidation_criteria", "confirmation_requirement", "scope_change_policy",
            "search_provenance", "supersedes", "supersession_reason", "status",
            "label", "note", "provenance", "created_at", "semantic_identity",
            "protocol_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchProtocolValidationError(
                f"persisted protocol missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchProtocolValidationError(
                f"persisted protocol has unknown fields: {unknown}")
        if data["kind"] != "research_protocol":
            raise ResearchProtocolValidationError(
                f"persisted protocol kind must be 'research_protocol', got "
                f"{data['kind']!r}")
        for name in ("permitted_operations", "prohibited_operations",
                     "completion_criteria", "insufficiency_criteria",
                     "invalidation_criteria"):
            if not isinstance(data[name], list):
                raise ResearchProtocolValidationError(
                    f"persisted protocol {name} must be a list")
        for name in ("authorisation", "scope", "evidence", "comparison",
                     "scope_change_policy"):
            if not isinstance(data[name], Mapping):
                raise ResearchProtocolValidationError(
                    f"persisted protocol {name} must be a mapping")
        try:
            return cls(
                authorisation=ProtocolAuthorisation.from_dict(
                    dict(data["authorisation"])),
                scope=InvestigationScope.from_dict(dict(data["scope"])),
                evidence=EvidenceScope.from_dict(dict(data["evidence"])),
                permitted_operations=tuple(data["permitted_operations"]),
                completion_criteria=tuple(data["completion_criteria"]),
                insufficiency_criteria=tuple(data["insufficiency_criteria"]),
                invalidation_criteria=tuple(data["invalidation_criteria"]),
                comparison=ComparisonSpecification.from_dict(dict(data["comparison"])),
                confirmation_requirement=ConfirmationRequirement(
                    data["confirmation_requirement"]),
                scope_change_policy=ScopeChangePolicy.from_dict(
                    dict(data["scope_change_policy"])),
                prohibited_operations=tuple(data["prohibited_operations"]),
                protocol_version=data["protocol_version"],
                search_provenance=data["search_provenance"],
                supersedes=data["supersedes"],
                supersession_reason=(
                    None if data["supersession_reason"] is None
                    else SupersessionReason(data["supersession_reason"])),
                status=ProtocolStatus(data["status"]),
                label=data["label"],
                note=data["note"],
                provenance=dict(data["provenance"]),
                created_at=data["created_at"],
                schema_version=data["schema_version"],
                semantic_identity=data["semantic_identity"],
                protocol_identity=data["protocol_identity"],
            )
        except (ResearchProtocolError, TypeError, ValueError, KeyError) as exc:
            raise ResearchProtocolValidationError(
                f"persisted protocol is malformed: {exc}") from exc



# ═══ The protocol freeze (T0) ═══════════════════════════════════════════════


@dataclass(frozen=True)
class ProtocolFreeze:
    """
    PROOF THAT THIS WAS THE INVESTIGATION CONTRACT BEFORE THE RESULT EXISTED.

    The Wave 5 `AFR-*` freeze answers "what research was next"; this `PFR-*` freeze
    answers the strictly deeper question "what did investigating it mean". It binds
    the full governed specification -- scope, evidence, allowed operations,
    comparison, completion / insufficiency / invalidation criteria and the
    confirmation requirement -- to the T0 moment.

    `frozen_at` is PROVENANCE ONLY and is deliberately ABSENT from identity
    material, exactly as in Wave 5: re-freezing a semantically identical protocol at
    a later moment reproduces the SAME freeze identity. That is what makes the
    snapshot verifiable rather than merely recorded -- a later result that changed
    the investigation would have to produce a different `PFR-*`.
    """

    protocol_identity: str
    opportunity_identity: str
    agenda_identity: str
    agenda_freeze_identity: str
    queue_identity: str
    evidence_boundary: str
    evidence_scope_identity: str
    scope_identity: str
    permitted_operations: tuple[str, ...]
    prohibited_operations: tuple[str, ...]
    comparison_identity: str
    completion_criteria: tuple[str, ...]
    insufficiency_criteria: tuple[str, ...]
    invalidation_criteria: tuple[str, ...]
    confirmation_requirement: str
    scope_change_policy_identity: str
    protocol_version: int
    supersedes: str = ""
    frozen_at: str = ""                 # provenance only; NOT identity
    note: str = ""                      # provenance only; NOT identity
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION
    semantic_identity: str = ""
    freeze_identity: str = ""

    def __post_init__(self) -> None:
        for name in (
            "permitted_operations", "prohibited_operations", "completion_criteria",
            "insufficiency_criteria", "invalidation_criteria",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = protocol_freeze_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise ResearchProtocolValidationError(
                    "presented protocol freeze semantic identity does not match the "
                    "frozen protocol material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.freeze_identity:
            if self.freeze_identity != expected_id:
                raise ResearchProtocolValidationError(
                    f"presented protocol freeze identity {self.freeze_identity!r} does "
                    f"not match the frozen protocol material")
        else:
            object.__setattr__(self, "freeze_identity", expected_id)

    @classmethod
    def freeze_protocol(
        cls,
        protocol: ResearchProtocol,
        *,
        frozen_at: str = "",
        note: str = "",
    ) -> "ProtocolFreeze":
        """
        Freeze a protocol at T0, before any subsequent result exists.

        Every element is COPIED from the protocol, so a freeze can never be a
        paraphrase, a summary or a re-derivation: it is the protocol's own governed
        material, bound to the T0 moment.
        """
        if not isinstance(protocol, ResearchProtocol):
            raise ResearchProtocolValidationError(
                f"expected ResearchProtocol, got {type(protocol).__name__}")
        # The tuples are stored in the protocol's own canonical order so that a
        # re-derived freeze compares equal to the persisted one field for field.
        return cls(
            protocol_identity=protocol.protocol_identity,
            opportunity_identity=protocol.authorisation.opportunity_identity,
            agenda_identity=protocol.authorisation.agenda_identity,
            agenda_freeze_identity=protocol.authorisation.agenda_freeze_identity,
            queue_identity=protocol.authorisation.queue_identity,
            evidence_boundary=protocol.evidence.evidence_boundary,
            evidence_scope_identity=_digest(protocol.evidence.semantic_material()),
            scope_identity=_digest(protocol.scope.semantic_material()),
            permitted_operations=tuple(
                item.value for item in protocol.permitted_operations),
            prohibited_operations=tuple(
                item.value for item in protocol.prohibited_operations),
            comparison_identity=_digest(protocol.comparison.semantic_material()),
            completion_criteria=tuple(
                item.value for item in protocol.completion_criteria),
            insufficiency_criteria=tuple(
                item.value for item in protocol.insufficiency_criteria),
            invalidation_criteria=tuple(
                item.value for item in protocol.invalidation_criteria),
            confirmation_requirement=protocol.confirmation_requirement.value,
            scope_change_policy_identity=_digest(
                protocol.scope_change_policy.semantic_material()),
            protocol_version=protocol.protocol_version,
            supersedes=protocol.supersedes,
            frozen_at=frozen_at,
            note=note,
        )


    def semantic_material(self) -> dict[str, Any]:
        """Identity material of the freeze. `frozen_at` and `note` are absent."""
        return {
            "kind": "protocol_freeze",
            "schema_version": self.schema_version,
            "protocol_identity": self.protocol_identity,
            "opportunity_identity": self.opportunity_identity,
            "agenda_identity": self.agenda_identity,
            "agenda_freeze_identity": self.agenda_freeze_identity,
            "queue_identity": self.queue_identity,
            "evidence_boundary": self.evidence_boundary,
            "evidence_scope_identity": self.evidence_scope_identity,
            "scope_identity": self.scope_identity,
            "permitted_operations": list(self.permitted_operations),
            "prohibited_operations": list(self.prohibited_operations),
            "comparison_identity": self.comparison_identity,
            "completion_criteria": list(self.completion_criteria),
            "insufficiency_criteria": list(self.insufficiency_criteria),
            "invalidation_criteria": list(self.invalidation_criteria),
            "confirmation_requirement": self.confirmation_requirement,
            "scope_change_policy_identity": self.scope_change_policy_identity,
            "protocol_version": self.protocol_version,
            "supersedes": self.supersedes,
        }

    def _validate(self) -> "ProtocolFreeze":
        if self.schema_version != RESEARCH_PROTOCOL_SCHEMA_VERSION:
            raise ResearchProtocolValidationError(
                f"protocol freeze schema_version must be "
                f"{RESEARCH_PROTOCOL_SCHEMA_VERSION} (clean reset), got "
                f"{self.schema_version!r}")
        if not is_research_protocol_identity(self.protocol_identity):
            raise ProtocolNamespaceViolation(
                f"a protocol freeze must bind a governed `RPL-*` protocol identity, got "
                f"{self.protocol_identity!r}")
        if not is_research_opportunity_identity(self.opportunity_identity):
            raise ProtocolNamespaceViolation(
                f"a protocol freeze must bind a governed `ROP-*` opportunity identity, "
                f"got {self.opportunity_identity!r}")
        for name, value, predicate in (
            ("agenda_identity", self.agenda_identity, is_research_agenda_identity),
            ("agenda_freeze_identity", self.agenda_freeze_identity,
             is_agenda_freeze_identity),
            ("queue_identity", self.queue_identity, is_research_queue_identity),
        ):
            if value and not predicate(value):
                raise ProtocolNamespaceViolation(
                    f"protocol freeze {name} {value!r} is not a governed identity")
        if (self.queue_identity or self.agenda_freeze_identity) and (
                not self.agenda_identity):
            raise ResearchProtocolValidationError(
                "a protocol freeze may not carry a queue or agenda-freeze identity "
                "without the agenda identity that produced it")
        _require_text(self.evidence_boundary, "protocol freeze evidence_boundary")
        for name in ("evidence_scope_identity", "scope_identity",
                     "comparison_identity", "scope_change_policy_identity"):
            value = getattr(self, name)
            if not _HEX64_RE.match(value or ""):
                raise ResearchProtocolValidationError(
                    f"protocol freeze {name} must be a lowercase 64-character sha256 hex "
                    f"digest, got {value!r}")

        for name in ("permitted_operations", "prohibited_operations",
                     "completion_criteria", "insufficiency_criteria",
                     "invalidation_criteria"):
            values = getattr(self, name)
            if not values:
                raise ResearchProtocolValidationError(
                    f"protocol freeze {name} may not be empty; a freeze that omits the "
                    f"governed material is not a freeze of that material")
            if len(set(values)) != len(values):
                raise ResearchProtocolValidationError(
                    f"protocol freeze {name} must not contain duplicates")
        missing = sorted(item.value for item in GLOBALLY_PROHIBITED_OPERATIONS
                         if item.value not in self.prohibited_operations)
        if missing:
            raise ResearchProtocolValidationError(
                f"protocol freeze does not forbid globally prohibited operations: "
                f"{missing}")
        if (isinstance(self.protocol_version, bool)
                or not isinstance(self.protocol_version, int)
                or self.protocol_version < 1):
            raise ResearchProtocolValidationError(
                f"protocol freeze protocol_version must be a positive integer, got "
                f"{self.protocol_version!r}")
        if self.supersedes and not is_research_protocol_identity(self.supersedes):
            raise ProtocolNamespaceViolation(
                f"protocol freeze supersedes {self.supersedes!r} is not a governed "
                f"`RPL-*` identity")
        _encode(self.semantic_material(), "protocol freeze semantic material")
        return self

    def assert_belongs_to(self, protocol: ResearchProtocol) -> "ProtocolFreeze":
        """
        Fail closed if this freeze does not describe THIS protocol.

        The expected freeze is RE-DERIVED from the protocol rather than trusted, so
        a freeze can never drift from the contract it claims to have frozen -- which
        is precisely the failure a post-hoc rewrite would produce.
        """
        if not isinstance(protocol, ResearchProtocol):
            raise ResearchProtocolValidationError(
                f"expected ResearchProtocol, got {type(protocol).__name__}")
        expected = ProtocolFreeze.freeze_protocol(protocol)
        for name, value in expected.semantic_material().items():
            if name in ("schema_version", "kind"):
                continue
            # `semantic_material()` renders sequence fields as lists for
            # canonical encoding, while the record holds tuples; compare the
            # persisted value in that same rendered form.
            actual = getattr(self, name)
            if isinstance(actual, tuple):
                actual = list(actual)
            if actual != value:
                raise ResearchProtocolValidationError(
                    f"protocol freeze {self.freeze_identity} field {name!r} contradicts "
                    f"the protocol it claims to freeze ({protocol.protocol_identity})")
        return self


    # ─── Serialisation ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "frozen_at": self.frozen_at,
            "note": self.note,
            "semantic_identity": self.semantic_identity,
            "freeze_identity": self.freeze_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtocolFreeze":
        if not isinstance(data, Mapping):
            raise ResearchProtocolValidationError(
                f"persisted protocol freeze must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "protocol_identity", "opportunity_identity",
            "agenda_identity", "agenda_freeze_identity", "queue_identity",
            "evidence_boundary", "evidence_scope_identity", "scope_identity",
            "permitted_operations", "prohibited_operations", "comparison_identity",
            "completion_criteria", "insufficiency_criteria", "invalidation_criteria",
            "confirmation_requirement", "scope_change_policy_identity",
            "protocol_version", "supersedes", "frozen_at", "note",
            "semantic_identity", "freeze_identity",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise ResearchProtocolValidationError(
                f"persisted protocol freeze missing fields: {missing}")
        unknown = sorted(set(data) - expected)
        if unknown:
            raise ResearchProtocolValidationError(
                f"persisted protocol freeze has unknown fields: {unknown}")
        if data["kind"] != "protocol_freeze":
            raise ResearchProtocolValidationError(
                f"persisted protocol freeze kind must be 'protocol_freeze', got "
                f"{data['kind']!r}")
        for name in ("permitted_operations", "prohibited_operations",
                     "completion_criteria", "insufficiency_criteria",
                     "invalidation_criteria"):
            if not isinstance(data[name], list):
                raise ResearchProtocolValidationError(
                    f"persisted protocol freeze {name} must be a list")
        return cls(
            protocol_identity=data["protocol_identity"],
            opportunity_identity=data["opportunity_identity"],
            agenda_identity=data["agenda_identity"],
            agenda_freeze_identity=data["agenda_freeze_identity"],
            queue_identity=data["queue_identity"],
            evidence_boundary=data["evidence_boundary"],
            evidence_scope_identity=data["evidence_scope_identity"],
            scope_identity=data["scope_identity"],
            permitted_operations=tuple(data["permitted_operations"]),
            prohibited_operations=tuple(data["prohibited_operations"]),
            comparison_identity=data["comparison_identity"],
            completion_criteria=tuple(data["completion_criteria"]),
            insufficiency_criteria=tuple(data["insufficiency_criteria"]),
            invalidation_criteria=tuple(data["invalidation_criteria"]),
            confirmation_requirement=data["confirmation_requirement"],
            scope_change_policy_identity=data["scope_change_policy_identity"],
            protocol_version=data["protocol_version"],
            supersedes=data["supersedes"],
            frozen_at=data["frozen_at"],
            note=data["note"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            freeze_identity=data["freeze_identity"],
        )


__all__ = [
    "CompletionCriterionKind",
    "ComparisonKind",
    "ComparisonSpecification",
    "ComparisonTerm",
    "EvidenceScope",
    "EscalationRoute",
    "GLOBALLY_PROHIBITED_OPERATIONS",
    "InsufficiencyCriterionKind",
    "InvalidationCriterionKind",
    "InvestigationScope",
    "PROTOCOL_FREEZE_ID_PREFIX",
    "PROTOCOL_STATUS_TRANSITIONS",
    "ProhibitedAuthorityError",
    "ProhibitedOperation",
    "ProtocolAuthorisation",
    "ProtocolAuthorisationError",
    "ProtocolFreeze",
    "ProtocolNamespaceViolation",
    "ProtocolStatus",
    "RESEARCH_OPERATIONS",
    "RESEARCH_PROTOCOL_ID_DIGEST_CHARS",
    "RESEARCH_PROTOCOL_ID_PREFIX",
    "RESEARCH_PROTOCOL_SCHEMA_VERSION",
    "ResearchOperation",
    "ResearchProtocol",
    "ResearchProtocolError",
    "ResearchProtocolValidationError",
    "ScopeChangePolicy",
    "ScopeComponent",
    "ScopeComponentKind",
    "SupersessionReason",
    "is_protocol_freeze_identity",
    "is_research_protocol_identity",
    "protocol_freeze_identity_for",
    "research_protocol_identity_for",
]

