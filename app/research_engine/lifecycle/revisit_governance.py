"""
Revisit Governance v1 -- when previously investigated work may legitimately
return to the research lifecycle.

Stage 3 / Wave 7. `treatment_memory` records what happened. This module decides
what may legitimately happen NEXT, and it exists because the two failure modes
pull in opposite directions:

    SUPPRESS EVERYTHING (research amnesia)
        Never re-run a treatment, however much the evidence has changed. The
        system re-derives the same idea, cannot find it, and investigates it
        again from scratch.

    ALLOW EVERYTHING (research dogma)
        Treat a prior NOT_SUPPORTED result as a permanent verdict. The system
        refuses to look again even when the evidence was invalid, the population
        was different, or the question was never actually answered.

THE RULE THIS MODULE ENCODES
============================
A revisit requires a GOVERNED MATERIAL CHANGE. Not a wish, not elapsed time,
not curiosity, not a hunch that it might work this time. A closed vocabulary of
triggers, each mechanically verifiable from governed fields, and a versioned
policy that decides which triggers are sufficient.

WHAT IS DELIBERATELY NOT A TRIGGER
==================================
Try again. Maybe it works now. It looks promising. It has been a while. These
are not expressible, because `RevisitTrigger` is a closed enum with no member
that means any of them. A human who wants work done anyway is a real and
legitimate need, so it is modelled as `RevisitAuthority.OPERATOR_DIRECTED` --
recorded DISTINCTLY, as separate provenance that never falsifies or overwrites
the engine's own decision.

REVISIT_PERMITTED IS NOT PERMISSION TO EXECUTE
=============================================
It means only that the work may legitimately RE-ENTER the governed research
lifecycle. It creates no protocol, no opportunity, no agenda entry and no
experiment. Wave 7 decides eligibility; Waves 5 and 6 decide what happens next,
and a future executor decides whether anything runs at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Tuple

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.treatment_memory import (
    TREATMENT_MEMORY_SCHEMA_VERSION,
    ApplicabilityEnvelope,
    ApplicabilityVerdict,
    ConflictingMemoryState,
    HistoricalDisposition,
    TreatmentMemoryError,
    TreatmentMemoryRecord,
    TreatmentMemoryValidationError,
    TreatmentSignature,
    assess_memory_applicability,
    is_treatment_memory_identity,
    is_treatment_signature_identity,
)

# -- Versions ----------------------------------------------------------------
REVISIT_POLICY_VERSION: int = 1

# -- Namespaces --------------------------------------------------------------
# `RVP-` (revisit policy) and `RVD-` (revisit decision) are new and disjoint
# from every other governed namespace, including Wave 7's own `TRS-`/`TMR-`.
# A policy is not a research question, a decision is not a protocol, and neither
# is ever a canonical identity.
REVISIT_POLICY_ID_PREFIX = "RVP-"
REVISIT_DECISION_ID_PREFIX = "RVD-"
_ID_DIGEST_CHARS = 16

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_RVP_ID_RE = re.compile(
    rf"^{re.escape(REVISIT_POLICY_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_RVD_ID_RE = re.compile(
    rf"^{re.escape(REVISIT_DECISION_ID_PREFIX)}[0-9A-F]{{{_ID_DIGEST_CHARS}}}$")
_SEMANTIC_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class RevisitGovernanceError(TreatmentMemoryError):
    """Base failure for the revisit-governance layer."""


class RevisitPolicyValidationError(RevisitGovernanceError):
    """Revisit policy or decision material is invalid or ungoverned."""


def is_revisit_policy_identity(value: Any) -> bool:
    """True only for IDs inside the reserved revisit-policy namespace."""
    return isinstance(value, str) and bool(_RVP_ID_RE.match(value))


def is_revisit_decision_identity(value: Any) -> bool:
    """True only for IDs inside the reserved revisit-decision namespace."""
    return isinstance(value, str) and bool(_RVD_ID_RE.match(value))


def _identity_for(prefix: str, semantic_identity: str, label: str) -> str:
    if not isinstance(semantic_identity, str) or not _HEX64_RE.match(semantic_identity):
        raise RevisitPolicyValidationError(
            f"{label} semantic identity must be a lowercase 64-character sha256 "
            f"hex digest, got {semantic_identity!r}")
    return f"{prefix}{semantic_identity[:_ID_DIGEST_CHARS].upper()}"


def _encode(value: Any, label: str) -> str:
    try:
        return canonical_json(value)
    except Exception as exc:  # noqa: BLE001 - re-raised as a governed failure
        raise RevisitPolicyValidationError(
            f"{label} is not canonically encodable: {exc}") from exc


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _encode(material, "semantic material").encode("utf-8")).hexdigest()


# == Revisit triggers ========================================================
#
# A CLOSED vocabulary. There is no member meaning try again, maybe it works now,
# it looks promising, or enough time has passed. Those are not mechanically
# checkable, and a revisit gate that accepts uncheckable reasons accepts any
# reason at all.


class RevisitTrigger(str, Enum):
    """
    The CLOSED set of governed circumstances that may justify a revisit.

    Each member is mechanically verifiable from governed fields by
    `verify_trigger`. None of them is a judgement, a hunch or a feeling.
    """

    #: Material evidence exists beyond the previous evidence boundary.
    NEW_EVIDENCE = "NEW_EVIDENCE"
    #: The proposed work concerns a population outside the previous envelope.
    NEW_POPULATION = "NEW_POPULATION"
    #: A newly governed dimension materially changes the research question.
    NEW_DIMENSION = "NEW_DIMENSION"
    #: A newly governed interaction changes the semantics of the question.
    NEW_INTERACTION = "NEW_INTERACTION"
    #: The previous investigation was invalid or evidence-deficient, and the
    #: exact defect is now mechanically shown to be repaired.
    EVIDENCE_REPAIR = "EVIDENCE_REPAIR"
    #: A confirmation population that did not exist now exists.
    CONFIRMATION_AVAILABLE = "CONFIRMATION_AVAILABLE"
    #: A governed contextual assumption materially changed.
    MATERIAL_CONTEXT_CHANGE = "MATERIAL_CONTEXT_CHANGE"
    #: The prior investigation was limited by a protocol deficiency the new
    #: protocol materially addresses.
    PROTOCOL_IMPROVEMENT = "PROTOCOL_IMPROVEMENT"


#: The triggers a policy may name. Exposed so a caller can validate membership
#: without reaching into the enum, and so a test can assert the two agree.
ALL_REVISIT_TRIGGERS: Tuple[RevisitTrigger, ...] = tuple(RevisitTrigger)


class MaterialChangeKind(str, Enum):
    """
    The CLOSED vocabulary of MATERIAL change.

    Materiality is determined from governed fields, never asserted in prose. A
    caller may CLAIM a change; the decision records the claim separately from
    what was mechanically verified, and only the verified set can permit a
    revisit.
    """

    EVIDENCE_BOUNDARY_ADVANCED = "EVIDENCE_BOUNDARY_ADVANCED"
    POPULATION_EXTENDED = "POPULATION_EXTENDED"
    DIMENSION_ADDED = "DIMENSION_ADDED"
    INTERACTION_ADDED = "INTERACTION_ADDED"
    EVIDENCE_DEFECT_REPAIRED = "EVIDENCE_DEFECT_REPAIRED"
    CONFIRMATION_POPULATION_ADDED = "CONFIRMATION_POPULATION_ADDED"
    EXECUTION_CONTEXT_CHANGED = "EXECUTION_CONTEXT_CHANGED"
    PROTOCOL_DEFICIENCY_ADDRESSED = "PROTOCOL_DEFICIENCY_ADDRESSED"


#: Changes that are NOT material, recorded as a closed set so a test can assert
#: the asymmetry. Every one of these is a real, observable difference that
#: changes nothing about whether the work is worth repeating.
NON_MATERIAL_CHANGES: frozenset = frozenset({
    "RENAME", "NOTE_EDIT", "NEW_TIMESTAMP", "REORDERED_INPUT",
    "EQUIVALENT_SERIALISATION", "REGENERATED_FINGERPRINT_TIMESTAMP",
})


class RevisitEligibility(str, Enum):
    """
    The CLOSED set of revisit outcomes.

    REVISIT_PERMITTED means only that the work may legitimately re-enter the
    governed research lifecycle. It grants no authority to run anything.
    """

    #: Exactly equivalent, applicable history, no material change.
    DUPLICATE_RESEARCH = "DUPLICATE_RESEARCH"
    #: A governed trigger is verified AND material change is established.
    REVISIT_PERMITTED = "REVISIT_PERMITTED"
    #: Material change was claimed but not mechanically established.
    REVISIT_NOT_JUSTIFIED = "REVISIT_NOT_JUSTIFIED"
    #: The proposal is not equivalent to any applicable history.
    NEW_DISTINCT_RESEARCH = "NEW_DISTINCT_RESEARCH"
    #: The available governed information is insufficient to decide.
    INDETERMINATE = "INDETERMINATE"

    @property
    def suppresses_research(self) -> bool:
        """
        Whether this outcome may stop the work from proceeding.

        True ONLY for DUPLICATE_RESEARCH. Notably False for REVISIT_NOT_JUSTIFIED
        and INDETERMINATE, which are not verdicts: refusing to proceed on the
        grounds that nobody could prove eligibility is how a legitimate
        question quietly dies.
        """
        return self is RevisitEligibility.DUPLICATE_RESEARCH

    @property
    def is_permission_to_execute(self) -> bool:
        """Permanently False for every member. Governed, not merely documented."""
        return False


class RevisitAuthority(str, Enum):
    """
    WHO decided, kept structurally distinct from WHAT was decided.

    ENGINE_GOVERNED is a mechanical determination under a frozen policy.
    OPERATOR_DIRECTED is a human explicitly asking for work despite a duplicate
    or no-revisit status.

    The separation is what lets a future operator override exist WITHOUT
    falsifying history: the engine's original decision is immutable and remains
    exactly as it was recorded, and the operator's request is additional,
    separately-attributed provenance that references it.
    """

    ENGINE_GOVERNED = "ENGINE_GOVERNED"
    OPERATOR_DIRECTED = "OPERATOR_DIRECTED"



# == The versioned revisit policy (RVP-*) ====================================

#: The default permitted trigger set. Every member is mechanically verifiable,
#: so a default policy cannot authorise an unverifiable revisit.
DEFAULT_PERMITTED_TRIGGERS: Tuple[RevisitTrigger, ...] = tuple(
    RevisitTrigger)

#: Dispositions that may be revisited at all under any policy. An INVALID or
#: INSUFFICIENT result is revisitable because it is not a verdict; a SUPPORTED
#: result is revisitable because a new population is still a new question.
REVISITABLE_DISPOSITIONS: frozenset = frozenset(HistoricalDisposition)


@dataclass(frozen=True)
class RevisitPolicy:
    """
    An explicit, VERSIONED, deterministically identified revisit policy.

    IDENTITY MATERIAL (hashed): policy version, the permitted trigger set, the
    dispositions eligible for revisit, whether conflicting applicable memory
    blocks a revisit, and the material-change requirement.

    NOT IDENTITY MATERIAL (provenance only): `label`, `note`, `provenance` and
    `created_at`.

    WHY A POLICY IS IDENTITY-BEARING
    ================================
    A revisit decision is only interpretable against the rules that produced
    it. Freezing the policy identity into every decision means that tightening
    or loosening the rules later cannot retroactively change what a past
    decision meant: the old decision still names the policy that produced it,
    and both coexist. This is the same discipline Wave 5 applied to the
    prioritisation policy and Wave 6 to the protocol.
    """

    policy_version: int = REVISIT_POLICY_VERSION
    permitted_triggers: Tuple[RevisitTrigger, ...] = DEFAULT_PERMITTED_TRIGGERS
    revisitable_dispositions: Tuple[HistoricalDisposition, ...] = tuple(
        sorted(REVISITABLE_DISPOSITIONS, key=lambda item: item.value))
    require_material_change: bool = True
    conflict_blocks_revisit: bool = False
    label: str = ""                        # provenance only; NOT identity
    note: str = ""                         # provenance only; NOT identity
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = TREATMENT_MEMORY_SCHEMA_VERSION
    semantic_identity: str = ""
    policy_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "permitted_triggers", self._canonical_triggers(
            self.permitted_triggers, "permitted_triggers"))
        object.__setattr__(self, "revisitable_dispositions",
                           self._canonical_dispositions(
                               self.revisitable_dispositions))
        if not isinstance(self.require_material_change, bool):
            raise RevisitPolicyValidationError(
                f"require_material_change must be a boolean, got "
                f"{self.require_material_change!r}")
        if not isinstance(self.conflict_blocks_revisit, bool):
            raise RevisitPolicyValidationError(
                f"conflict_blocks_revisit must be a boolean, got "
                f"{self.conflict_blocks_revisit!r}")
        if not self.require_material_change:
            raise RevisitPolicyValidationError(
                "a revisit policy MUST require material change; a policy that "
                "permits revisiting on no change at all is research amnesia "
                "with extra steps")
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = _identity_for(REVISIT_POLICY_ID_PREFIX, expected,
                                    "revisit policy")
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise RevisitPolicyValidationError(
                    "presented revisit policy semantic identity does not match "
                    "the policy material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.policy_identity:
            if self.policy_identity != expected_id:
                raise RevisitPolicyValidationError(
                    f"presented revisit policy identity "
                    f"{self.policy_identity!r} does not match the policy material")
        else:
            object.__setattr__(self, "policy_identity", expected_id)

    @staticmethod
    def _canonical_triggers(values: Any,
                            label: str = "permitted_triggers") -> Tuple:
        if values is None:
            return ()
        if isinstance(values, (str, bytes)) or not isinstance(
                values, (list, tuple, set, frozenset)):
            raise RevisitPolicyValidationError(
                f"{label} must be a sequence of RevisitTrigger members")
        items = tuple(
            value if isinstance(value, RevisitTrigger)
            else RevisitTrigger(value) for value in values)
        if len(set(items)) != len(items):
            raise RevisitPolicyValidationError(
                f"{label} must not contain duplicates")
        return tuple(sorted(items, key=lambda item: item.value))

    @staticmethod
    def _canonical_dispositions(values: Any) -> Tuple[HistoricalDisposition, ...]:
        if values is None:
            return ()
        if isinstance(values, (str, bytes)) or not isinstance(
                values, (list, tuple, set, frozenset)):
            raise RevisitPolicyValidationError(
                "revisitable_dispositions must be a sequence of "
                "HistoricalDisposition members")
        items = tuple(
            value if isinstance(value, HistoricalDisposition)
            else HistoricalDisposition(value) for value in values)
        if len(set(items)) != len(items):
            raise RevisitPolicyValidationError(
                "revisitable_dispositions must not contain duplicates")
        return tuple(sorted(items, key=lambda item: item.value))


    def _validate(self) -> "RevisitPolicy":
        if self.schema_version != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise RevisitPolicyValidationError(
                f"revisit policy schema_version must be "
                f"{TREATMENT_MEMORY_SCHEMA_VERSION}, got {self.schema_version!r}")
        if self.policy_version < 1:
            raise RevisitPolicyValidationError(
                f"revisit policy_version must be a positive integer, got "
                f"{self.policy_version!r}")
        if not self.permitted_triggers:
            raise RevisitPolicyValidationError(
                "a revisit policy must permit at least one governed trigger; an "
                "empty trigger set is a permanent prohibition, which is research "
                "dogma expressed as policy")
        for trigger in self.permitted_triggers:
            if trigger not in ALL_REVISIT_TRIGGERS:
                raise RevisitPolicyValidationError(
                    f"{trigger!r} is not a governed revisit trigger")
        for disposition in self.revisitable_dispositions:
            if not isinstance(disposition, HistoricalDisposition):
                raise RevisitPolicyValidationError(
                    f"{disposition!r} is not a governed historical disposition")
        _encode(self.semantic_material(), "revisit policy semantic material")
        return self

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict:
        return {
            "kind": "revisit_policy",
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "permitted_triggers": [item.value
                                   for item in self.permitted_triggers],
            "revisitable_dispositions": [item.value
                                         for item in self.revisitable_dispositions],
            "require_material_change": self.require_material_change,
            "conflict_blocks_revisit": self.conflict_blocks_revisit,
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "RevisitPolicy":
        return cls(**kwargs)

    def permits(self, trigger: RevisitTrigger) -> bool:
        """Whether this policy version treats `trigger` as sufficient."""
        return trigger in self.permitted_triggers

    def permits_disposition(self, disposition: HistoricalDisposition) -> bool:
        return disposition in self.revisitable_dispositions

    def is_version_of(self, other: "RevisitPolicy") -> bool:
        """Whether two policies are the same rules, ignoring provenance."""
        if not isinstance(other, RevisitPolicy):
            return False
        return self.semantic_identity == other.semantic_identity

    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            **self.semantic_material(),
            "label": self.label,
            "note": self.note,
            "provenance": dict(self.provenance),
            "semantic_identity": self.semantic_identity,
            "policy_identity": self.policy_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RevisitPolicy":
        if not isinstance(data, Mapping):
            raise RevisitPolicyValidationError(
                f"persisted revisit policy must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "policy_version", "permitted_triggers",
            "revisitable_dispositions", "require_material_change",
            "conflict_blocks_revisit", "label", "note", "provenance",
            "semantic_identity", "policy_identity",
        }
        if set(data) != expected:
            raise RevisitPolicyValidationError(
                f"persisted revisit policy fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        if data["kind"] != "revisit_policy":
            raise RevisitPolicyValidationError(
                f"persisted revisit policy kind must be 'revisit_policy', got "
                f"{data['kind']!r}")
        provenance = data["provenance"]
        if not isinstance(provenance, Mapping):
            raise RevisitPolicyValidationError(
                "persisted revisit policy 'provenance' must be a mapping")
        return cls(
            policy_version=data["policy_version"],
            permitted_triggers=tuple(data["permitted_triggers"]),
            revisitable_dispositions=tuple(data["revisitable_dispositions"]),
            require_material_change=data["require_material_change"],
            conflict_blocks_revisit=data["conflict_blocks_revisit"],
            label=data["label"],
            note=data["note"],
            provenance=dict(provenance),
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            policy_identity=data["policy_identity"],
        )



# == Material change =========================================================


@dataclass(frozen=True)
class MaterialChangeAssessment:
    """
    What materially changed between a historical memory and a proposal.

    `changed` is the mechanical verdict. `claimed` records what the proposer
    ASSERTED, so a claim and its verification are always separately inspectable
    and never conflated.
    """

    is_material: bool
    verified_kinds: Tuple[MaterialChangeKind, ...]
    claimed_kinds: Tuple[MaterialChangeKind, ...]
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def unverified_claims(self) -> Tuple[MaterialChangeKind, ...]:
        """Claims the proposer made that the governed fields do not support."""
        verified = set(self.verified_kinds)
        return tuple(item for item in self.claimed_kinds if item not in verified)

    def to_dict(self) -> dict:
        return {
            "is_material": self.is_material,
            "verified_kinds": [item.value for item in self.verified_kinds],
            "claimed_kinds": [item.value for item in self.claimed_kinds],
            "unverified_claims": [item.value for item in self.unverified_claims],
            "detail": dict(self.detail),
        }


def assess_material_change(
        memory: TreatmentMemoryRecord,
        proposal_context: ApplicabilityEnvelope,
        *,
        claimed_kinds: Iterable[MaterialChangeKind] = (),
        repaired_defects: Iterable[str] = (),
) -> MaterialChangeAssessment:
    """
    Did something GOVERNED materially change between `memory` and the proposal?

    Every check below reads a governed field. None of them reads a label, a
    note, a timestamp, a reordering or a serialisation, which is precisely why
    a renamed treatment with a fresh `created_at` is correctly reported as NOT
    materially changed.

    The `EVIDENCE_DEFECT_REPAIRED` check is the anti-dogma mechanism: an
    INVALID_INVESTIGATION is not evidence against a treatment, so once the
    specific governed defect is shown to be repaired, the old invalid result
    stops being a reason to withhold work.
    """
    if not isinstance(memory, TreatmentMemoryRecord):
        raise RevisitPolicyValidationError(
            f"assess_material_change expects a TreatmentMemoryRecord, got "
            f"{type(memory).__name__}")
    if not isinstance(proposal_context, ApplicabilityEnvelope):
        raise RevisitPolicyValidationError(
            f"assess_material_change expects an ApplicabilityEnvelope, got "
            f"{type(proposal_context).__name__}")
    claimed = tuple(sorted(
        {item if isinstance(item, MaterialChangeKind)
         else MaterialChangeKind(item) for item in claimed_kinds},
        key=lambda item: item.value))
    repaired = frozenset(str(item) for item in repaired_defects)

    envelope = memory.applicability
    verified = []
    detail = {}

    if (proposal_context.evidence_boundary
            and proposal_context.evidence_boundary > envelope.evidence_boundary):
        verified.append(MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED)
        detail["evidence_boundary"] = {
            "historical": envelope.evidence_boundary,
            "proposed": proposal_context.evidence_boundary,
        }

    if (envelope.asset_family != proposal_context.asset_family
            or not set(envelope.symbol_scope).issubset(
                set(proposal_context.symbol_scope))):
        verified.append(MaterialChangeKind.POPULATION_EXTENDED)
        detail["population"] = {
            "historical_asset_family": envelope.asset_family,
            "proposed_asset_family": proposal_context.asset_family,
            "historical_symbols": list(envelope.symbol_scope),
            "proposed_symbols": list(proposal_context.symbol_scope),
        }

    if not set(envelope.dimension_identities).issuperset(
            set(proposal_context.dimension_identities)):
        verified.append(MaterialChangeKind.DIMENSION_ADDED)
        detail["dimensions"] = {
            "historical": list(envelope.dimension_identities),
            "proposed": list(proposal_context.dimension_identities),
        }

    if not set(envelope.interaction_identities).issuperset(
            set(proposal_context.interaction_identities)):
        verified.append(MaterialChangeKind.INTERACTION_ADDED)
        detail["interactions"] = {
            "historical": list(envelope.interaction_identities),
            "proposed": list(proposal_context.interaction_identities),
        }

    if (memory.disposition is HistoricalDisposition.INVALID_INVESTIGATION
            and repaired):
        verified.append(MaterialChangeKind.EVIDENCE_DEFECT_REPAIRED)
        detail["repaired_defects"] = sorted(repaired)

    if (not envelope.confirmation_population
            and proposal_context.confirmation_population):
        verified.append(MaterialChangeKind.CONFIRMATION_POPULATION_ADDED)
        detail["confirmation_population"] = (
            proposal_context.confirmation_population)

    if (envelope.execution_context
            and proposal_context.execution_context
            and envelope.execution_context
            != proposal_context.execution_context):
        verified.append(MaterialChangeKind.EXECUTION_CONTEXT_CHANGED)
        detail["execution_context"] = {
            "historical": envelope.execution_context,
            "proposed": proposal_context.execution_context,
        }

    return MaterialChangeAssessment(
        is_material=bool(verified),
        verified_kinds=tuple(sorted(verified, key=lambda item: item.value)),
        claimed_kinds=claimed,
        detail=detail,
    )



# == Trigger verification ====================================================
#
# Each trigger is verified MECHANICALLY from the governed fields of a memory
# and a proposal context. A caller may claim a trigger; only a verified trigger
# can permit a revisit, and the claim is preserved alongside the verification.


#: Which MATERIAL change each governed trigger names. A trigger is verified only
#: when its corresponding change was independently established, which is the
#: structural link between "something changed" and "this reason is justified".
TRIGGER_CHANGE_KINDS: Mapping[RevisitTrigger, MaterialChangeKind] = {
    RevisitTrigger.NEW_EVIDENCE: MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED,
    RevisitTrigger.NEW_POPULATION: MaterialChangeKind.POPULATION_EXTENDED,
    RevisitTrigger.NEW_DIMENSION: MaterialChangeKind.DIMENSION_ADDED,
    RevisitTrigger.NEW_INTERACTION: MaterialChangeKind.INTERACTION_ADDED,
    RevisitTrigger.EVIDENCE_REPAIR: MaterialChangeKind.EVIDENCE_DEFECT_REPAIRED,
    RevisitTrigger.CONFIRMATION_AVAILABLE: (
        MaterialChangeKind.CONFIRMATION_POPULATION_ADDED),
    RevisitTrigger.MATERIAL_CONTEXT_CHANGE: (
        MaterialChangeKind.EXECUTION_CONTEXT_CHANGED),
    RevisitTrigger.PROTOCOL_IMPROVEMENT: (
        MaterialChangeKind.PROTOCOL_DEFICIENCY_ADDRESSED),
}


def change_kind_for_trigger(trigger: RevisitTrigger) -> MaterialChangeKind:
    """The material change a governed trigger names. Total over the enum."""
    if not isinstance(trigger, RevisitTrigger):
        raise RevisitPolicyValidationError(
            f"change_kind_for_trigger expects a RevisitTrigger, got "
            f"{type(trigger).__name__}")
    return TRIGGER_CHANGE_KINDS[trigger]


def verify_trigger(
        trigger: RevisitTrigger,
        memory: TreatmentMemoryRecord,
        proposal_context: ApplicabilityEnvelope,
        change: MaterialChangeAssessment) -> bool:
    """
    Whether `trigger` is mechanically established for this memory and proposal.

    A trigger is verified only when the corresponding MATERIAL change was
    independently established by `assess_material_change`. That is the
    structural link between something changed and this named reason being
    justified: the trigger names WHY the change counts, and the change
    assessment proves THAT it happened.
    """
    return change_kind_for_trigger(trigger) in set(change.verified_kinds)



# == The immutable revisit decision (RVD-*) ==================================


@dataclass(frozen=True)
class RevisitDecision:
    """
    An immutable, deterministically identified revisit determination.

    IDENTITY MATERIAL (hashed): the treatment signature, the proposed context,
    the relevant memory identities, the policy identity AND the policy semantic
    identity, the claimed triggers, the verified triggers, the material-change
    verdict, the applicability verdicts, the conflict state, the eligibility,
    and the authority.

    NOT IDENTITY MATERIAL (provenance only): `label`, `rationale`, `provenance`
    and `created_at`.

    A decision has no mutator and no override method. An operator who wants work
    done anyway records a SEPARATE, separately-attributed
    `OperatorReinvestmentRequest` referencing this decision; the engine's
    original decision is never falsified or rewritten, which is what keeps the
    history honest.
    """

    treatment_signature_identity: str
    proposed_context: ApplicabilityEnvelope
    memory_identities: Tuple[str, ...]
    policy_identity: str
    policy_semantic_identity: str
    claimed_triggers: Tuple[RevisitTrigger, ...]
    verified_triggers: Tuple[RevisitTrigger, ...]
    material_change: MaterialChangeAssessment
    applicability_verdicts: Mapping[str, Any]
    conflict_state: ConflictingMemoryState
    eligibility: RevisitEligibility
    authority: RevisitAuthority = RevisitAuthority.ENGINE_GOVERNED
    label: str = ""                        # provenance only; NOT identity
    rationale: str = ""                    # provenance only; NOT identity
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""                   # provenance only; NOT identity
    schema_version: int = TREATMENT_MEMORY_SCHEMA_VERSION
    semantic_identity: str = ""
    decision_identity: str = ""


    def __post_init__(self) -> None:
        if not is_treatment_signature_identity(self.treatment_signature_identity):
            raise RevisitPolicyValidationError(
                f"treatment_signature_identity "
                f"{self.treatment_signature_identity!r} is outside the governed "
                f"TRS namespace")
        if not is_revisit_policy_identity(self.policy_identity):
            raise RevisitPolicyValidationError(
                f"policy_identity {self.policy_identity!r} is outside the "
                f"governed RVP namespace")
        if not _HEX64_RE.match(self.policy_semantic_identity):
            raise RevisitPolicyValidationError(
                f"policy_semantic_identity must be a lowercase 64-character "
                f"sha256 hex digest, got {self.policy_semantic_identity!r}")
        if not isinstance(self.proposed_context, ApplicabilityEnvelope):
            raise RevisitPolicyValidationError(
                f"proposed_context must be a governed ApplicabilityEnvelope, "
                f"got {type(self.proposed_context).__name__}")
        if not isinstance(self.material_change, MaterialChangeAssessment):
            raise RevisitPolicyValidationError(
                f"material_change must be a governed MaterialChangeAssessment, "
                f"got {type(self.material_change).__name__}")
        if not isinstance(self.applicability_verdicts, Mapping):
            raise RevisitPolicyValidationError(
                f"applicability_verdicts must be a mapping of memory identity to "
                f"verdict, got {type(self.applicability_verdicts).__name__}")
        object.__setattr__(self, "applicability_verdicts", dict(
            self.applicability_verdicts))
        object.__setattr__(self, "memory_identities", self._canonical_memories(
            self.memory_identities))
        object.__setattr__(self, "claimed_triggers", self._canonical_triggers(
            self.claimed_triggers, "claimed_triggers"))
        object.__setattr__(self, "verified_triggers", self._canonical_triggers(
            self.verified_triggers, "verified_triggers"))
        if isinstance(self.conflict_state, str):
            object.__setattr__(self, "conflict_state",
                               ConflictingMemoryState(self.conflict_state))
        if isinstance(self.eligibility, str):
            object.__setattr__(self, "eligibility",
                               RevisitEligibility(self.eligibility))
        if isinstance(self.authority, str):
            object.__setattr__(self, "authority",
                               RevisitAuthority(self.authority))
        self._validate()
        expected = _digest(self.semantic_material())
        expected_id = _identity_for(REVISIT_DECISION_ID_PREFIX, expected,
                                    "revisit decision")
        if self.semantic_identity:
            if not _HEX64_RE.match(self.semantic_identity) or (
                    self.semantic_identity != expected):
                raise RevisitPolicyValidationError(
                    "presented revisit decision semantic identity does not match "
                    "the decision material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.decision_identity:
            if self.decision_identity != expected_id:
                raise RevisitPolicyValidationError(
                    f"presented revisit decision identity "
                    f"{self.decision_identity!r} does not match the decision "
                    f"material")
        else:
            object.__setattr__(self, "decision_identity", expected_id)

    @staticmethod
    def _canonical_memories(values: Any) -> Tuple[str, ...]:
        if values is None:
            return ()
        if isinstance(values, (str, bytes)) or not isinstance(
                values, (list, tuple, set, frozenset)):
            raise RevisitPolicyValidationError(
                "memory_identities must be a sequence of TMR identities")
        items = []
        for item in values:
            if not is_treatment_memory_identity(item):
                raise RevisitPolicyValidationError(
                    f"memory identity {item!r} is outside the governed TMR "
                    f"namespace")
            items.append(item)
        if len(set(items)) != len(items):
            raise RevisitPolicyValidationError(
                "memory_identities must not contain duplicates")
        return tuple(sorted(items))

    @staticmethod
    def _canonical_triggers(values: Any, label: str) -> Tuple[RevisitTrigger, ...]:
        if values is None:
            return ()
        if isinstance(values, (str, bytes)) or not isinstance(
                values, (list, tuple, set, frozenset)):
            raise RevisitPolicyValidationError(
                f"{label} must be a sequence of RevisitTrigger members")
        items = tuple(
            value if isinstance(value, RevisitTrigger)
            else RevisitTrigger(value) for value in values)
        if len(set(items)) != len(items):
            raise RevisitPolicyValidationError(
                f"{label} must not contain duplicates")
        return tuple(sorted(items, key=lambda item: item.value))


    def _validate(self) -> "RevisitDecision":
        if self.schema_version != TREATMENT_MEMORY_SCHEMA_VERSION:
            raise RevisitPolicyValidationError(
                f"revisit decision schema_version must be "
                f"{TREATMENT_MEMORY_SCHEMA_VERSION}, got {self.schema_version!r}")
        if not set(self.applicability_verdicts).issubset(
                set(self.memory_identities)):
            raise RevisitPolicyValidationError(
                "every applicability verdict must name a memory in "
                "memory_identities")
        for identity, verdict in self.applicability_verdicts.items():
            if not isinstance(verdict, ApplicabilityVerdict):
                raise RevisitPolicyValidationError(
                    f"applicability verdict for {identity} must be an "
                    f"ApplicabilityVerdict, got {verdict!r}")
        if not set(self.verified_triggers).issubset(set(self.claimed_triggers)):
            raise RevisitPolicyValidationError(
                "every verified trigger must also have been claimed; a trigger "
                "cannot be verified that nobody asserted")
        if (self.eligibility is RevisitEligibility.REVISIT_PERMITTED
                and not self.verified_triggers):
            raise RevisitPolicyValidationError(
                "REVISIT_PERMITTED requires at least one mechanically verified "
                "trigger; eligibility may never rest on an assertion alone")
        if (self.eligibility is RevisitEligibility.REVISIT_PERMITTED
                and not self.material_change.is_material):
            raise RevisitPolicyValidationError(
                "REVISIT_PERMITTED requires established material change")
        if (self.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
                and self.material_change.is_material):
            raise RevisitPolicyValidationError(
                "DUPLICATE_RESEARCH is incompatible with established material "
                "change; a materially changed proposal is not a duplicate")
        if self.is_permission_to_execute():
            raise RevisitPolicyValidationError(
                "a revisit decision can never be permission to execute")
        _encode(self.semantic_material(), "revisit decision semantic material")
        return self

    def is_permission_to_execute(self) -> bool:
        """
        Permanently False, and consulted during validation.

        REVISIT_PERMITTED means the work may re-enter the governed research
        lifecycle. It authorises nothing: no protocol, no opportunity, no
        agenda entry, no experiment. Exposed as a method so a future consumer
        asking that question gets a governed NO rather than a guess.
        """
        return False

    # -- Identity -----------------------------------------------------------
    def semantic_material(self) -> dict:
        return {
            "kind": "revisit_decision",
            "schema_version": self.schema_version,
            "treatment_signature_identity": self.treatment_signature_identity,
            "proposed_context": self.proposed_context.semantic_material(),
            "memory_identities": list(self.memory_identities),
            "policy_identity": self.policy_identity,
            "policy_semantic_identity": self.policy_semantic_identity,
            "claimed_triggers": [item.value for item in self.claimed_triggers],
            "verified_triggers": [item.value for item in self.verified_triggers],
            "material_change": self.material_change.to_dict(),
            "applicability_verdicts": {
                identity: verdict.value
                for identity, verdict in self.applicability_verdicts.items()},
            "conflict_state": self.conflict_state.value,
            "eligibility": self.eligibility.value,
            "authority": self.authority.value,
        }

    @classmethod
    def create(cls, **kwargs: Any) -> "RevisitDecision":
        return cls(**kwargs)

    def was_permitted(self) -> bool:
        """Whether the work may legitimately re-enter the research lifecycle."""
        return self.eligibility is RevisitEligibility.REVISIT_PERMITTED

    def blocks_research(self) -> bool:
        """Whether this decision actually prevents work from proceeding."""
        return self.eligibility.suppresses_research


    # -- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            **self.semantic_material(),
            "material_change": {
                "is_material": self.material_change.is_material,
                "verified_kinds": [item.value
                                   for item in self.material_change.verified_kinds],
                "claimed_kinds": [item.value
                                   for item in self.material_change.claimed_kinds],
                "detail": dict(self.material_change.detail),
            },
            "label": self.label,
            "rationale": self.rationale,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "semantic_identity": self.semantic_identity,
            "decision_identity": self.decision_identity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RevisitDecision":
        if not isinstance(data, Mapping):
            raise RevisitPolicyValidationError(
                f"persisted revisit decision must be a mapping, got "
                f"{type(data).__name__}")
        expected = {
            "kind", "schema_version", "treatment_signature_identity",
            "proposed_context", "memory_identities", "policy_identity",
            "policy_semantic_identity", "claimed_triggers", "verified_triggers",
            "material_change", "applicability_verdicts", "conflict_state",
            "eligibility", "authority", "label", "rationale", "provenance",
            "created_at", "semantic_identity", "decision_identity",
        }
        if set(data) != expected:
            raise RevisitPolicyValidationError(
                f"persisted revisit decision fields must be exactly "
                f"{sorted(expected)}, got {sorted(data)}")
        if data["kind"] != "revisit_decision":
            raise RevisitPolicyValidationError(
                f"persisted revisit decision kind must be 'revisit_decision', "
                f"got {data['kind']!r}")
        change = data["material_change"]
        if not isinstance(change, Mapping):
            raise RevisitPolicyValidationError(
                "persisted revisit decision 'material_change' must be a mapping")
        provenance = data["provenance"]
        if not isinstance(provenance, Mapping):
            raise RevisitPolicyValidationError(
                "persisted revisit decision 'provenance' must be a mapping")
        return cls(
            treatment_signature_identity=data["treatment_signature_identity"],
            proposed_context=ApplicabilityEnvelope.from_dict(
                data["proposed_context"]),
            memory_identities=tuple(data["memory_identities"]),
            policy_identity=data["policy_identity"],
            policy_semantic_identity=data["policy_semantic_identity"],
            claimed_triggers=tuple(RevisitTrigger(item)
                                   for item in data["claimed_triggers"]),
            verified_triggers=tuple(RevisitTrigger(item)
                                    for item in data["verified_triggers"]),
            material_change=MaterialChangeAssessment(
                is_material=change["is_material"],
                verified_kinds=tuple(MaterialChangeKind(item) for item
                                     in change["verified_kinds"]),
                claimed_kinds=tuple(MaterialChangeKind(item) for item
                                    in change["claimed_kinds"]),
                detail=dict(change["detail"]),
            ),
            applicability_verdicts={
                identity: ApplicabilityVerdict(verdict)
                for identity, verdict
                in data["applicability_verdicts"].items()},
            conflict_state=ConflictingMemoryState(data["conflict_state"]),
            eligibility=RevisitEligibility(data["eligibility"]),
            authority=RevisitAuthority(data["authority"]),
            label=data["label"],
            rationale=data["rationale"],
            provenance=dict(provenance),
            created_at=data["created_at"],
            schema_version=data["schema_version"],
            semantic_identity=data["semantic_identity"],
            decision_identity=data["decision_identity"],
        )



# == The decision function ===================================================


def _conflict_state(
        applicable: Tuple[TreatmentMemoryRecord, ...],
) -> ConflictingMemoryState:
    """
    Whether the applicable memories contradict each other.

    Recorded, never resolved. When two applicable memories disagree about
    whether the treatment was supported, Wave 7 surfaces that disagreement
    exactly as it is: an explicit CONFLICTING_APPLICABLE_MEMORY state, with
    both records still present. Choosing between them is research, and research
    is not Wave 7's job.
    """
    from research_engine.lifecycle.treatment_memory import (
        CONFLICTING_DISPOSITION_PAIRS,
    )
    if len(applicable) < 2:
        return ConflictingMemoryState.CONSISTENT_MEMORY
    for index, left in enumerate(applicable):
        for right in applicable[index + 1:]:
            if frozenset({left.disposition,
                          right.disposition}) in CONFLICTING_DISPOSITION_PAIRS:
                return ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY
    return ConflictingMemoryState.CONSISTENT_MEMORY


def _no_applicable_decision(
        *,
        signature: TreatmentSignature,
        proposed_context: ApplicabilityEnvelope,
        rows: Tuple[TreatmentMemoryRecord, ...],
        verdicts: Mapping[str, Any],
        policy: RevisitPolicy,
        claimed: Tuple[RevisitTrigger, ...],
        authority: RevisitAuthority,
        label: str,
        rationale: str,
        provenance: Mapping[str, Any],
        created_at: str) -> RevisitDecision:
    """
    The NEW_DISTINCT_RESEARCH outcome: no memory applies to this context.

    This is the anti-overgeneralisation case. An FX memory must not suppress an
    XAUUSD proposal, so when nothing in history applies, the correct answer is
    that this is new work, not a duplicate and not a prohibited revisit.
    """
    return RevisitDecision(
        treatment_signature_identity=signature.signature_identity,
        proposed_context=proposed_context,
        memory_identities=tuple(row.memory_identity for row in rows),
        policy_identity=policy.policy_identity,
        policy_semantic_identity=policy.semantic_identity,
        claimed_triggers=claimed,
        verified_triggers=(),
        material_change=MaterialChangeAssessment(False, (), (), {}),
        applicability_verdicts=verdicts,
        conflict_state=ConflictingMemoryState.NO_MEMORY,
        eligibility=RevisitEligibility.NEW_DISTINCT_RESEARCH,
        authority=authority,
        label=label,
        rationale=rationale,
        provenance=dict(provenance),
        created_at=created_at,
    )



def decide_revisit(
        *,
        signature: TreatmentSignature,
        proposed_context: ApplicabilityEnvelope,
        memories: Iterable[TreatmentMemoryRecord],
        policy: RevisitPolicy,
        claimed_triggers: Iterable[RevisitTrigger] = (),
        repaired_defects: Iterable[str] = (),
        authority: RevisitAuthority = RevisitAuthority.ENGINE_GOVERNED,
        label: str = "",
        rationale: str = "",
        provenance: Mapping[str, Any] = None,
        created_at: str = "",
) -> RevisitDecision:
    """
    The single governed entry point: may this work return to the lifecycle?

    THE DECISION PROCEDURE, IN ORDER
    ================================
    1. Gather every memory for this exact treatment signature, and assess each
       memory's applicability to the proposed context.
    2. If NO memory applies, the work is NEW_DISTINCT_RESEARCH. The old history
       simply does not speak to this population.
    3. If nothing MATERIAL changed, and an applicable memory is real evidence
       about the treatment, the work is DUPLICATE_RESEARCH. Anti-amnesia.
    4. Otherwise verify each claimed trigger mechanically. If one is verified,
       permitted by the frozen policy, and the memory's disposition is
       revisitable, the work is REVISIT_PERMITTED. Anti-dogma.
    5. If material change was claimed but nothing verified, the work is
       REVISIT_NOT_JUSTIFIED. That does NOT suppress: a claim nobody could
       substantiate is not grounds for refusing the work.
    6. If the available information cannot decide, the result is INDETERMINATE,
       which also does not suppress.

    NO STEP EXECUTES ANYTHING. This function reads history and returns a
    determination. It never creates a protocol, an opportunity, an agenda entry
    or an experiment, and it never mutates a single memory.
    """
    claimed, rows, verdicts, applicable = _gather(
        signature=signature, proposed_context=proposed_context,
        memories=memories, policy=policy, claimed_triggers=claimed_triggers)
    if not applicable:
        return _no_applicable_decision(
            signature=signature, proposed_context=proposed_context, rows=rows,
            verdicts=verdicts, policy=policy, claimed=claimed, authority=authority,
            label=label, rationale=rationale, provenance=dict(provenance or {}),
            created_at=created_at)
    return _resolve(
        signature=signature, proposed_context=proposed_context, rows=rows,
        verdicts=verdicts, applicable=applicable, policy=policy, claimed=claimed,
        repaired_defects=repaired_defects, authority=authority, label=label,
        rationale=rationale, provenance=dict(provenance or {}),
        created_at=created_at)



def _gather(*, signature, proposed_context, memories, policy, claimed_triggers):
    """
    Validate the inputs and collect the applicable history.

    Every argument is type-checked and fail-closed here, before any reasoning
    happens, so an ungoverned object can never reach the decision logic. Memory
    belonging to a DIFFERENT treatment signature is rejected outright: mixing
    histories across treatments would let one treatment's evidence suppress
    another's revisit.
    """
    if not isinstance(signature, TreatmentSignature):
        raise RevisitPolicyValidationError(
            f"decide_revisit expects a governed TreatmentSignature, got "
            f"{type(signature).__name__}")
    if not isinstance(policy, RevisitPolicy):
        raise RevisitPolicyValidationError(
            f"decide_revisit expects a governed RevisitPolicy, got "
            f"{type(policy).__name__}")
    if not isinstance(proposed_context, ApplicabilityEnvelope):
        raise RevisitPolicyValidationError(
            f"decide_revisit expects a governed ApplicabilityEnvelope, got "
            f"{type(proposed_context).__name__}")

    claimed = RevisitPolicy._canonical_triggers(claimed_triggers, "claimed_triggers")
    rows = tuple(memories)
    for row in rows:
        if not isinstance(row, TreatmentMemoryRecord):
            raise RevisitPolicyValidationError(
                f"decide_revisit expects TreatmentMemoryRecord objects, got "
                f"{type(row).__name__}")
        if row.treatment_signature_identity != signature.signature_identity:
            raise RevisitPolicyValidationError(
                f"memory {row.memory_identity} belongs to treatment signature "
                f"{row.treatment_signature_identity}, not the proposed "
                f"{signature.signature_identity}; revisit history may never be "
                f"mixed across treatments")
    rows = tuple(sorted(rows, key=lambda item: item.memory_identity))

    verdicts = {row.memory_identity: assess_memory_applicability(
        row, proposed_context).verdict for row in rows}
    applicable = [row for row in rows
                  if verdicts[row.memory_identity] is ApplicabilityVerdict.APPLIES]
    return claimed, rows, verdicts, applicable


def _resolve(*, signature, proposed_context, rows, verdicts, applicable, policy,
             claimed, repaired_defects, authority, label, rationale, provenance,
             created_at) -> RevisitDecision:
    """
    Apply the eligibility rules to applicable history.

    Material change is assessed against the most relevant applicable memory,
    chosen in deterministic identity order, so the same inputs always produce
    the same decision regardless of the order the caller supplied memories in.
    """
    primary = applicable[0]
    # The proposer's claims are recorded in the trigger vocabulary, so they are
    # translated to the material-change vocabulary the assessment speaks. A claim
    # with no matching change kind is still recorded, as an unverified claim.
    claimed_changes = tuple(sorted(
        {change_kind_for_trigger(trigger) for trigger in claimed},
        key=lambda item: item.value))
    change = assess_material_change(
        primary, proposed_context, claimed_kinds=claimed_changes,
        repaired_defects=repaired_defects)
    verified = tuple(trigger for trigger in claimed
                     if verify_trigger(trigger, primary, proposed_context, change))
    conflict_state = _conflict_state(tuple(applicable))

    if policy.conflict_blocks_revisit and (
            conflict_state is ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY):
        eligibility = RevisitEligibility.INDETERMINATE
    elif not change.is_material:
        evidence_bearing = [row for row in applicable if row.is_evidence_bearing()]
        eligibility = (RevisitEligibility.DUPLICATE_RESEARCH if evidence_bearing
                       else RevisitEligibility.REVISIT_NOT_JUSTIFIED)
    else:
        justified = tuple(
            trigger for trigger in verified
            if policy.permits(trigger)
            and policy.permits_disposition(primary.disposition))
        eligibility = (RevisitEligibility.REVISIT_PERMITTED if justified
                       else RevisitEligibility.REVISIT_NOT_JUSTIFIED)

    return RevisitDecision(
        treatment_signature_identity=signature.signature_identity,
        proposed_context=proposed_context,
        memory_identities=tuple(row.memory_identity for row in rows),
        policy_identity=policy.policy_identity,
        policy_semantic_identity=policy.semantic_identity,
        claimed_triggers=claimed,
        verified_triggers=verified,
        material_change=change,
        applicability_verdicts=verdicts,
        conflict_state=conflict_state,
        eligibility=eligibility,
        authority=authority,
        label=label,
        rationale=rationale,
        provenance=dict(provenance),
        created_at=created_at,
    )


__all__ = [
    "ALL_REVISIT_TRIGGERS",
    "DEFAULT_PERMITTED_TRIGGERS",
    "MaterialChangeAssessment",
    "MaterialChangeKind",
    "NON_MATERIAL_CHANGES",
    "REVISIT_DECISION_ID_PREFIX",
    "REVISIT_POLICY_ID_PREFIX",
    "REVISIT_POLICY_VERSION",
    "REVISITABLE_DISPOSITIONS",
    "RevisitAuthority",
    "RevisitDecision",
    "RevisitEligibility",
    "RevisitGovernanceError",
    "RevisitPolicy",
    "RevisitPolicyValidationError",
    "RevisitTrigger",
    "assess_material_change",
    "decide_revisit",
    "is_revisit_decision_identity",
    "is_revisit_policy_identity",
    "verify_trigger",
]

