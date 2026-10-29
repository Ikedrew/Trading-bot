"""
Curiosity Signal v1 -- the governed, EVIDENCE-DRIVEN input to autonomous research
curiosity.

Stage 3 / Wave 3. Curiosity MAY propose. It may NOT approve itself. This module
is the narrow seam that lets machine-produced evidence cause a NEW governed
research question to exist outside the frozen canonical 70, WITHOUT a human
writing the question.

THE CORE PRINCIPLE
==================
    CURIOSITY MAY PROPOSE.
    CURIOSITY MAY NOT APPROVE ITSELF.

A `CuriositySignal` is a *machine* assertion that something in governed evidence
is worth asking about. It is deliberately the smallest contract that can carry
enough governed information for a generator to act, and no more: signal type,
source reference/identity/provenance, subject/target, implicated governed
dimension, parent interaction/research, evidence reference, evidence boundary,
and a CLOSED machine-readable reason.

NO FREE TEXT IS AUTHORITY
========================
`note` and `observed_at` exist, and BOTH are deliberately ABSENT from
`semantic_material()`. A note may accompany a signal; a note can never create
one. There is no code path that builds a signal from prose: `source_ref`,
`source_identity`, `evidence_reference`, `evidence_boundary` and `reason_code`
are all mandatory, and a missing or malformed one fails closed.

FAIL CLOSED ON MISSING PROVENANCE
=================================
If a source machine event does not carry its own identity, the adapter does not
invent one. `signal_from_finding_trigger` and
`signal_from_candidate_reconsideration` both raise rather than fill in a
plausible-looking substitute.

DETERMINISM
===========
`signal_identity` is `CSN-` + 16 uppercase hex characters of a SHA-256 digest
over the canonical JSON encoding of `semantic_material()`. It never depends on a
creation time, an insertion order, a counter or a random UUID. Two equivalent
signals resolve to the same identity, which is what makes downstream
deduplication work.

NO GENERATION, NO APPROVAL, NO PRODUCTION AUTHORITY
===================================================
This module generates NOTHING. It does not propose questions, does not evaluate
Wave 1 or Wave 2, does not create hypotheses, candidates or findings, does not
run research, and has no path to production configuration, risk, sizing,
execution, baseline activation or candidate promotion. It imports no runner, no
orchestrator, no `GovernanceGate`, performs no I/O, and writes nothing at import
time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchValidationError,
    canonical_json,
)
from research_engine.lifecycle.governed_dimension import is_dimension_identity
from research_engine.lifecycle.research_interaction import is_interaction_identity

# -- Versions (clean reset: starts at 1, never > 1) ---------------------------

CURIOSITY_SIGNAL_SCHEMA_VERSION: int = 1

# -- Namespace ----------------------------------------------------------------
#
# `CSN-` (curiosity signal) is disjoint from the reserved `GEN-` research
# namespace, from the Wave 1 `DIM-`/`IXN-`/`SLC-` namespaces, from the Wave 2
# `FSP-`/`EVD-`/`DEC-`/`EXP-` namespaces and from every canonical programme
# prefix. A signal is never a research question.

CURIOSITY_SIGNAL_ID_PREFIX = "CSN-"
CURIOSITY_SIGNAL_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_CSN_ID_RE = re.compile(
    rf"^{re.escape(CURIOSITY_SIGNAL_ID_PREFIX)}"
    rf"[0-9A-F]{{{CURIOSITY_SIGNAL_ID_DIGEST_CHARS}}}$"
)
# The governed `<kind>:<id>` reference grammar, restated locally so this module
# never has to reach into the Wave 0 private regexes.
_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
# A CLOSED machine-readable reason: SHOUTY_SNAKE_CASE. Prose is not a reason.
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


# -- Errors (fail closed, never silently repaired) -----------------------------


class CuriositySignalError(RuntimeError):
    """Base failure for the curiosity signal layer."""


class CuriositySignalValidationError(CuriositySignalError):
    """Signal material is invalid, incomplete or not canonically encodable."""


class CuriosityProvenanceError(CuriositySignalValidationError):
    """
    A required source identity / evidence reference is missing or fabricated.

    Raised instead of inventing provenance, so a signal can never be minted out
    of nothing.
    """


class CuriosityRecursionError(CuriositySignalValidationError):
    """A signal/proposal would parent itself or duplicate its own ancestry."""


# -- Signal type --------------------------------------------------------------


class CuriositySignalType(str, Enum):
    """
    The CLOSED set of machine events that may seed research curiosity.

    This is an identity taxonomy, not a behaviour switch. Wave 3 implements two
    adapters (finding/evidence, candidate reconsideration) and no others; a
    member that has no adapter simply cannot produce a signal.
    """

    FINDING_EVIDENCE = "FINDING_EVIDENCE"
    CANDIDATE_RECONSIDERATION = "CANDIDATE_RECONSIDERATION"


def is_curiosity_signal_identity(value: Any) -> bool:
    """True only for IDs inside the reserved curiosity-signal namespace."""
    return isinstance(value, str) and bool(_CSN_ID_RE.match(value))


def signal_identity_for(semantic_identity: str) -> str:
    """Derive the governed signal identity deterministically from a digest."""
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise CuriositySignalValidationError(
            "semantic identity must be a lowercase 64-character sha256 hex digest")
    return (
        f"{CURIOSITY_SIGNAL_ID_PREFIX}"
        f"{semantic_identity[:CURIOSITY_SIGNAL_ID_DIGEST_CHARS].upper()}"
    )


# -- Canonical encoding helpers ----------------------------------------------


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except GeneratedResearchValidationError as exc:
        raise CuriositySignalValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "curiosity signal semantic material").encode("utf-8")
    ).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CuriositySignalValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise CuriositySignalValidationError(
            f"{label} must not have surrounding whitespace")
    return value


def _require_provenance(value: Any, label: str) -> str:
    """Provenance is stricter than ordinary text: a missing identity is fatal."""
    if not isinstance(value, str) or not value.strip():
        raise CuriosityProvenanceError(
            f"{label} is required: a curiosity signal may not be created without "
            f"its own source identity, and an identity is never fabricated")
    if value != value.strip():
        raise CuriosityProvenanceError(f"{label} must not have surrounding whitespace")
    return value


def _require_reference(value: Any, label: str) -> str:
    text = _require_provenance(value, label)
    if not _REFERENCE_RE.match(text):
        raise CuriositySignalValidationError(
            f"{label} must be a governed '<kind>:<id>' reference, got {text!r}")
    return text


def _require_reason(value: Any, label: str) -> str:
    text = _require_provenance(value, label)
    if not _REASON_RE.match(text):
        raise CuriositySignalValidationError(
            f"{label} must be a CLOSED machine-readable SHOUTY_SNAKE_CASE code; "
            f"free text is not authority, got {text!r}")
    return text


def _require_token(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _TOKEN_RE.match(text):
        raise CuriositySignalValidationError(
            f"{label} must be a governed token, got {text!r}")
    return text


def _normalise_refs(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise CuriositySignalValidationError(f"{label} must be a sequence of references")
    refs = [_require_reference(item, label) for item in values]
    if len(set(refs)) != len(refs):
        raise CuriositySignalValidationError(f"{label} must not contain duplicates")
    return tuple(sorted(refs))


def _coerce_attr(source: Any, name: str, *, label: str, required: bool) -> Any:
    """Read a machine identity off an existing object without ever inventing it."""
    value = getattr(source, name, None)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise CuriosityProvenanceError(
                f"source object is missing {label!r} ({name}); provenance is required "
                f"and is never fabricated")
        return None
    return value


# -- The signal ---------------------------------------------------------------


@dataclass(frozen=True)
class CuriositySignal:
    """
    An immutable, deterministically identified, EVIDENCE-DRIVEN curiosity input.

    A signal is not a research question, a hypothesis, a finding, a candidate or
    production authority. It is the governed assertion that a machine event
    happened and that it is worth asking a new question about. A generator
    turns it into a proposal; Wave 2 decides whether that proposal may become
    investigation-ready generated research.

    Identity material (hashed into `semantic_identity`): signal type, source
    reference/identity/provenance, target, reason code, evidence reference,
    evidence boundary, dimension reference, parent interaction, parent research
    references and the destructive component material.

    NOT identity material: `note` and `observed_at`. A timestamp is provenance,
    never scientific identity, so an identical evidence signal re-discovered
    months later resolves to the same identity and therefore deduplicates.
    """

    signal_type: CuriositySignalType
    source_ref: str
    source_identity: str
    target_kind: str
    target_ref: str
    reason_code: str
    evidence_reference: str
    evidence_boundary: str
    source_provenance: Mapping[str, Any] = field(default_factory=dict)
    dimension_ref: str | None = None
    parent_interaction_ref: str | None = None
    parent_research_refs: tuple[str, ...] = ()
    component_ref: str | None = None
    component_kind: str | None = None
    baseline_ref: str | None = None
    note: str = ""                 # provenance only; NOT identity
    observed_at: str = ""          # provenance only; NOT identity
    schema_version: int = CURIOSITY_SIGNAL_SCHEMA_VERSION
    semantic_identity: str = ""
    signal_identity: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.signal_type, str) and not isinstance(
                self.signal_type, CuriositySignalType):
            object.__setattr__(
                self, "signal_type", CuriositySignalType(self.signal_type))
        object.__setattr__(self, "parent_research_refs", _normalise_refs(
            self.parent_research_refs, "parent_research_refs"))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = signal_identity_for(expected)
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or self.semantic_identity != expected:
                raise CuriositySignalValidationError(
                    "presented semantic identity does not match the signal material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.signal_identity:
            if self.signal_identity != expected_id:
                raise CuriositySignalValidationError(
                    f"presented signal identity {self.signal_identity!r} does not match "
                    "the signal material")
        else:
            object.__setattr__(self, "signal_identity", expected_id)

    # -- Construction ------------------------------------------------------

    @classmethod
    def create(cls, **kwargs: Any) -> "CuriositySignal":
        """Build a curiosity signal. Identity is derived, never supplied."""
        return cls(**kwargs)

    # -- Identity material / serialisation ---------------------------------

    def semantic_material(self) -> dict[str, Any]:
        """
        The frozen scientific material of this signal.

        `note` and `observed_at` are ABSENT by design: notes are not authority
        and a timestamp is provenance, not identity.
        """
        return {
            "kind": "curiosity_signal",
            "schema_version": self.schema_version,
            "signal_type": self.signal_type.value,
            "source_ref": self.source_ref,
            "source_identity": self.source_identity,
            "source_provenance": dict(self.source_provenance),
            "target": {"kind": self.target_kind, "ref": self.target_ref},
            "reason_code": self.reason_code,
            "evidence_reference": self.evidence_reference,
            "evidence_boundary": self.evidence_boundary,
            "dimension_ref": self.dimension_ref,
            "parent_interaction_ref": self.parent_interaction_ref,
            "parent_research_refs": list(self.parent_research_refs),
            "component": None if self.component_ref is None else {
                "ref": self.component_ref,
                "kind": self.component_kind,
                "baseline_ref": self.baseline_ref,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_material(),
            "note": self.note,
            "observed_at": self.observed_at,
            "semantic_identity": self.semantic_identity,
            "signal_identity": self.signal_identity,
        }

    # -- Validation --------------------------------------------------------

    def _validate(self) -> "CuriositySignal":
        if self.schema_version != CURIOSITY_SIGNAL_SCHEMA_VERSION:
            raise CuriositySignalValidationError(
                f"curiosity signal schema_version must be {CURIOSITY_SIGNAL_SCHEMA_VERSION} "
                f"(clean reset), got {self.schema_version!r}")
        if not isinstance(self.signal_type, CuriositySignalType):
            raise CuriositySignalValidationError(
                f"signal_type must be a CuriositySignalType, got {self.signal_type!r}")

        _require_reference(self.source_ref, "source_ref")
        identity = _require_provenance(self.source_identity, "source_identity")
        # The reference and the identity are BOUND: the reference must actually
        # point at the machine event whose identity is recorded. A reference to
        # one event carrying the identity of another is fabricated provenance.
        if self.source_ref.split(":", 1)[1] != identity:
            raise CuriosityProvenanceError(
                f"source_ref {self.source_ref!r} does not address source_identity "
                f"{identity!r}; provenance may not be fabricated")

        _require_token(self.target_kind, "target_kind")
        _require_text(self.target_ref, "target_ref")
        _require_reason(self.reason_code, "reason_code")
        _require_reference(self.evidence_reference, "evidence_reference")
        _require_text(self.evidence_boundary, "evidence_boundary")

        if not isinstance(self.source_provenance, Mapping):
            raise CuriositySignalValidationError("source_provenance must be a mapping")
        _encode(self.source_provenance, "source_provenance")

        if self.dimension_ref is not None and not is_dimension_identity(self.dimension_ref):
            raise CuriositySignalValidationError(
                f"dimension_ref must be a governed Wave 1 dimension identity, got "
                f"{self.dimension_ref!r}")
        if self.parent_interaction_ref is not None and not is_interaction_identity(
                self.parent_interaction_ref):
            raise CuriositySignalValidationError(
                f"parent_interaction_ref must be a governed Wave 1 interaction identity, "
                f"got {self.parent_interaction_ref!r}")

        # Destructive material is all-or-nothing: a component without a category
        # or without a baseline has not been specified precisely enough to be
        # challenged.
        if self.component_ref is None:
            if self.component_kind is not None or self.baseline_ref is not None:
                raise CuriositySignalValidationError(
                    "component_kind/baseline_ref may only accompany a component_ref")
        else:
            _require_provenance(self.component_ref, "component_ref")
            _require_reason(self.component_kind, "component_kind")
            _require_provenance(self.baseline_ref, "baseline_ref")

        if self.note and not isinstance(self.note, str):
            raise CuriositySignalValidationError("note must be a string")
        if self.observed_at:
            _require_text(self.observed_at, "observed_at")
        _encode(self.semantic_material(), "curiosity signal semantic material")
        return self



# -- Adapters -----------------------------------------------------------------
#
# Each adapter is a NARROW seam over an EXISTING substrate. Neither redesigns
# its source system, neither is a global hook into it, and neither is the only
# way an existing system can be used. Wave 3 deliberately does NOT replace the
# existing FindingTrigger -> Hypothesis path; it only proves that a governed
# generated-research question can ALSO originate from that same evidence.


#: The CLOSED mapping from an existing `TriggerCategory` to a governed curiosity
#: reason code. This is a taxonomy translation, not a detector: it never reads a
#: metric, never ranks anything, and never invents a reason outside the closed
#: set.
_FINDING_REASON_BY_CATEGORY: Mapping[str, str] = {
    "POOR_PATTERN_PERFORMANCE": "UNRESOLVED_PATTERN_BEHAVIOUR",
    "STRONG_PATTERN_PERFORMANCE": "UNRESOLVED_PATTERN_BEHAVIOUR",
    "DIRECTION_ASYMMETRY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "REGIME_ANOMALY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "SYMBOL_ANOMALY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "TEMPORAL_INSTABILITY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "EXECUTION_ANOMALY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "GEOMETRY_ANOMALY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "SCORE_MONOTONICITY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "KNOWLEDGE_CONTRADICTION": "KNOWLEDGE_CONTRADICTION_UNRESOLVED",
    "EXIT_INEFFICIENCY": "UNRESOLVED_EXIT_BEHAVIOUR",
    "GUARD_VALUE_NEGATIVE": "GUARD_MAY_DESTROY_EDGE",
    "DRAWDOWN_APPROACHING": "RISK_CONTROL_MAY_BE_MISALIGNED",
    "SESSION_DEGRADATION": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "SPREAD_ANOMALY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "SLIPPAGE_DETERIORATION": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "HORIZON_QUALITY": "CONDITIONAL_BEHAVIOUR_UNRESOLVED",
    "SR_DIVERGENCE": "KNOWLEDGE_CONTRADICTION_UNRESOLVED",
    "STRATEGY_DEGRADATION": "STRATEGY_COMPONENT_MAY_BE_DEGRADED",
    "RISK_SIZING_ANOMALY": "RISK_CONTROL_MAY_BE_MISALIGNED",
}


def _coerce_category_value(trigger: Any) -> str:
    """Read the existing trigger's own governed category value."""
    category = getattr(trigger, "category", None)
    value = getattr(category, "value", category)
    if not isinstance(value, str) or not value.strip():
        raise CuriosityProvenanceError(
            "finding trigger carries no governed category value; a curiosity signal "
            "may not be derived from an unidentifiable trigger")
    return value


def signal_from_finding_trigger(
    trigger: Any,
    *,
    target_kind: str,
    target_ref: str,
    evidence_boundary: str | None = None,
    reason_code: str | None = None,
    evidence_reference: str | None = None,
    dimension_ref: str | None = None,
    parent_interaction_ref: str | None = None,
    parent_research_refs: tuple[str, ...] = (),
    component_ref: str | None = None,
    component_kind: str | None = None,
    baseline_ref: str | None = None,
    note: str = "",
) -> CuriositySignal:
    """
    Adapter A: existing finding/evidence substrate -> `CuriositySignal`.

    PROVENANCE IS PRESERVED, NOT REBUILT. The trigger's own `trigger_id`,
    `finding_id`, `source`, category and machine counters are carried through
    verbatim into `source_provenance`, and the signal's `source_ref` addresses
    the trigger's own identity. Nothing is invented: a trigger without a
    `trigger_id`/`finding_id`/`source` fails closed, because an evidence signal
    with no addressable source is not evidence.

    `reason_code` defaults to the CLOSED translation of the trigger category. A
    caller may supply a different closed code; free text is rejected by the
    signal's own validation.
    """
    trigger_id = _coerce_attr(trigger, "trigger_id", label="trigger_id", required=True)
    finding_id = _coerce_attr(trigger, "finding_id", label="finding_id", required=True)
    source = _coerce_attr(trigger, "source", label="source", required=True)
    category = _coerce_category_value(trigger)
    evidence = getattr(trigger, "evidence", {}) or {}
    if not isinstance(evidence, Mapping):
        raise CuriosityProvenanceError(
            "finding trigger evidence provenance must be a mapping")
    evidence = dict(evidence)
    _encode(evidence, "finding trigger evidence provenance")
    source_boundary = evidence.get("evidence_as_of")
    if source_boundary is not None and (
            not isinstance(source_boundary, str) or not source_boundary.strip()):
        raise CuriosityProvenanceError(
            "finding trigger evidence_as_of is present but invalid")
    if evidence_boundary is None:
        if not source_boundary:
            raise CuriosityProvenanceError(
                "finding trigger supplies no evidence_as_of boundary and no explicit "
                "evidence_boundary was provided")
        evidence_boundary = source_boundary
    elif source_boundary and evidence_boundary != source_boundary:
        raise CuriosityProvenanceError(
            "explicit evidence_boundary conflicts with the finding trigger evidence_as_of")

    resolved_reason = reason_code or _FINDING_REASON_BY_CATEGORY.get(category)
    if resolved_reason is None:
        raise CuriosityProvenanceError(
            f"finding trigger category {category!r} has no governed curiosity reason "
            f"code; an ungoverned category may not seed a research proposal")

    sample_size = getattr(trigger, "sample_size", None)
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 0:
        sample_size = None

    resolved_parents = list(parent_research_refs)
    question_id = evidence.get("question_id")
    if question_id:
        if not isinstance(question_id, str) or not question_id.strip():
            raise CuriosityProvenanceError("finding question_id provenance is invalid")
        resolved_parents.append(f"research:{question_id}")

    return CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref=f"finding:{trigger_id}",
        source_identity=trigger_id,
        source_provenance={
            "adapter": "finding_trigger",
            "trigger_id": trigger_id,
            "finding_id": finding_id,
            "source": source,
            "category": category,
            "sample_size": sample_size,
            "confidence": getattr(trigger, "confidence", None) or "",
            "baseline_epoch": getattr(trigger, "baseline_epoch", None) or "",
            "evidence": evidence,
            "dataset_fingerprint": evidence.get("dataset_fingerprint", ""),
            "evidence_as_of": source_boundary or evidence_boundary,
            "source_datasets": evidence.get("source_datasets", []),
            "question_id": question_id or "",
            "experiment_id": evidence.get("experiment_id", ""),
        },
        target_kind=target_kind,
        target_ref=target_ref,
        reason_code=resolved_reason,
        evidence_reference=(
            evidence_reference if evidence_reference is not None else f"finding:{finding_id}"),
        evidence_boundary=evidence_boundary,
        dimension_ref=dimension_ref,
        parent_interaction_ref=parent_interaction_ref,
        parent_research_refs=tuple(sorted(set(resolved_parents))),
        component_ref=component_ref,
        component_kind=component_kind,
        baseline_ref=baseline_ref,
        note=note,
        observed_at=getattr(trigger, "detected_at", None) or "",
    )


# -- Adapter B: candidate reconsideration -------------------------------------


#: The CLOSED mapping from an existing `ReconsiderationStatus` to a governed
#: curiosity reason code. A candidate outcome is a SIGNAL that something deserves
#: a question; it is never, by itself, an authorisation of any explanation.
_RECONSIDERATION_REASON_BY_STATUS: Mapping[str, str] = {
    "ELIGIBLE_FOR_RECONSIDERATION": "CANDIDATE_OUTCOME_REQUIRES_EXPLANATION",
    "NOT_ELIGIBLE": "CANDIDATE_OUTCOME_NOT_ANSWERED",
    "FRESH_EVIDENCE_REQUIRED": "CANDIDATE_OUTCOME_REQUIRES_FRESH_EVIDENCE",
    "INDETERMINATE": "CANDIDATE_OUTCOME_INDETERMINATE",
}

#: Reconsideration reason codes that represent a HUMAN decision. A human
#: rejection is carried through as provenance and can never be upgraded,
#: softened or overridden by curiosity.
_HUMAN_DECISION_REASON_CODES: frozenset[str] = frozenset({
    "HUMAN_REJECTION_NOT_OVERRIDDEN",
    "HUMAN_REJECTION",
})


def signal_from_candidate_reconsideration(
    decision: Any,
    *,
    target_kind: str,
    target_ref: str,
    evidence_boundary: str,
    reason_code: str | None = None,
    evidence_reference: str | None = None,
    dimension_ref: str | None = None,
    parent_interaction_ref: str | None = None,
    parent_research_refs: tuple[str, ...] = (),
    component_ref: str | None = None,
    component_kind: str | None = None,
    baseline_ref: str | None = None,
    note: str = "",
) -> CuriositySignal:
    """
    Adapter B: existing candidate reconsideration/baseline-change outcome ->
    `CuriositySignal`.

    A failed, rejected or changed candidate MAY motivate a question such as "why
    did this treatment fail?" -- but the outcome supplies a SIGNAL only. This
    adapter never selects the explanation, never retries the candidate, never
    activates it, and never touches a human decision.

    HUMAN REJECTION IS NEVER OVERRIDDEN. If the decision carries a governed
    human-decision reason code, that code is recorded in `source_provenance` and
    the derived reason is forced to the explicit
    `CANDIDATE_REJECTED_BY_HUMAN` code, so a human rejection can never be
    laundered into a neutral research question.
    """
    reconsideration_id = _coerce_attr(
        decision, "reconsideration_id", label="reconsideration_id", required=True)
    candidate_id = _coerce_attr(
        decision, "candidate_id", label="candidate_id", required=True)
    required_provenance: dict[str, Any] = {}
    for field_name in (
            "historical_baseline_id", "historical_baseline_config_hash",
            "target_baseline_id", "target_baseline_config_hash", "impact_id",
            "candidate_treatment_id", "historical_candidate_status",
            "historical_evaluation_outcome"):
        value = _coerce_attr(
            decision, field_name, label=field_name, required=True)
        if value == "?":
            raise CuriosityProvenanceError(
                f"candidate reconsideration {field_name} is unresolved; exact provenance "
                "is required before curiosity may use the outcome")
        required_provenance[field_name] = value

    status_value = getattr(decision, "status", None)
    status = getattr(status_value, "value", status_value)
    if not isinstance(status, str) or not status.strip():
        raise CuriosityProvenanceError(
            "candidate reconsideration decision carries no governed status; an "
            "unidentifiable outcome may not seed a research proposal")

    reason_codes = getattr(decision, "reason_codes", ()) or ()
    reason_codes = tuple(str(code) for code in reason_codes)
    human_rejected = any(code in _HUMAN_DECISION_REASON_CODES for code in reason_codes)

    if human_rejected:
        resolved_reason = "CANDIDATE_REJECTED_BY_HUMAN"
    else:
        resolved_reason = reason_code or _RECONSIDERATION_REASON_BY_STATUS.get(status)
    if resolved_reason is None:
        raise CuriosityProvenanceError(
            f"candidate reconsideration status {status!r} has no governed curiosity "
            f"reason code; an ungoverned outcome may not seed a research proposal")

    return CuriositySignal.create(
        signal_type=CuriositySignalType.CANDIDATE_RECONSIDERATION,
        source_ref=f"candidate:{reconsideration_id}",
        source_identity=reconsideration_id,
        source_provenance={
            "adapter": "candidate_reconsideration",
            "reconsideration_id": reconsideration_id,
            "candidate_id": candidate_id,
            "status": status,
            "reason_codes": list(reason_codes),
            "human_decision_present": bool(human_rejected),
            "candidate_treatment_id": (
                required_provenance["candidate_treatment_id"]),
            "historical_baseline_id": (
                required_provenance["historical_baseline_id"]),
            "historical_baseline_config_hash": (
                required_provenance["historical_baseline_config_hash"]),
            "target_baseline_id": (
                required_provenance["target_baseline_id"]),
            "target_baseline_config_hash": (
                required_provenance["target_baseline_config_hash"]),
            "impact_id": required_provenance["impact_id"],
            "continuity_id": getattr(decision, "continuity_id", None) or "",
            "historical_evaluation_outcome": (
                required_provenance["historical_evaluation_outcome"]),
            "historical_candidate_status": (
                required_provenance["historical_candidate_status"]),
            "fresh_evidence_required": bool(
                getattr(decision, "fresh_evidence_required", False)),
            "limitations": list(getattr(decision, "limitations", ()) or ()),
        },
        target_kind=target_kind,
        target_ref=target_ref,
        reason_code=resolved_reason,
        evidence_reference=(
            evidence_reference if evidence_reference is not None
            else f"candidate:{candidate_id}"),
        evidence_boundary=evidence_boundary,
        dimension_ref=dimension_ref,
        parent_interaction_ref=parent_interaction_ref,
        parent_research_refs=tuple(parent_research_refs),
        component_ref=component_ref,
        component_kind=component_kind,
        baseline_ref=baseline_ref,
        note=note,
    )


__all__ = [
    "CURIOSITY_SIGNAL_ID_DIGEST_CHARS",
    "CURIOSITY_SIGNAL_ID_PREFIX",
    "CURIOSITY_SIGNAL_SCHEMA_VERSION",
    "CuriosityProvenanceError",
    "CuriosityRecursionError",
    "CuriositySignal",
    "CuriositySignalError",
    "CuriositySignalType",
    "CuriositySignalValidationError",
    "is_curiosity_signal_identity",
    "signal_from_candidate_reconsideration",
    "signal_from_finding_trigger",
    "signal_identity_for",
]

