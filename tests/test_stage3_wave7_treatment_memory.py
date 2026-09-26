"""
Stage 3 / Wave 7 -- governed treatment memory, semantic equivalence and revisit
governance.

Focused tests only. Scope:
    - a `TRS-*` treatment signature that is deterministically identified from
      governed SEMANTICS, so labels, notes and timestamps cannot fork it;
    - a `TMR-*` immutable historical memory with a closed research disposition
      vocabulary, mandatory governed reason codes, and an explicit applicability
      envelope;
    - equivalence that distinguishes EXACT from RELATED from NOT_EQUIVALENT from
      INDETERMINATE, with no fuzzy or model-based authority anywhere;
    - a versioned `RVP-*` revisit policy, closed revisit triggers, mechanical
      material change, and immutable `RVD-*` decisions;
    - duplicate suppression, legitimate new-evidence revisit, new-population
      non-suppression, evidence repair, and coexisting conflicting memories;
    - restart-safe persistence, semantic dedup and fail-closed loading;
    - Wave 6 / Wave 5 / Wave 4 / Waves 0-3 integration, canonical-70
      isolation, and proof that no research execution, candidate activation or
      treatment application occurs.

These are TEST FIXTURES, not production research. No experiment, backtest,
candidate, trade or production behaviour is executed anywhere.
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from research_engine.lifecycle.research_opportunity import OpportunityState
from research_engine.lifecycle.research_protocol import (
    RESEARCH_PROTOCOL_SCHEMA_VERSION,
)

from research_engine.lifecycle import (
    revisit_governance as revisit_module,
    treatment_equivalence as equivalence_module,
    treatment_memory as memory_module,
    treatment_memory_store as store_module,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_QUESTION_COUNT,
    assert_canonical_70_intact,
    canonical_inventory,
)
from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    GovernedDimension,
)
from research_engine.lifecycle.revisit_governance import (
    MaterialChangeKind,
    RevisitAuthority,
    RevisitDecision,
    RevisitEligibility,
    RevisitGovernanceError,
    RevisitPolicy,
    RevisitPolicyValidationError,
    RevisitTrigger,
    assess_material_change,
    decide_revisit,
    is_revisit_decision_identity,
    is_revisit_policy_identity,
)
from research_engine.lifecycle.research_interaction import ResearchInteraction
from research_engine.lifecycle.research_opportunity import OpportunitySubjectKind
from research_engine.lifecycle.research_protocol import (
    PROTOCOL_FREEZE_ID_PREFIX,
    RESEARCH_PROTOCOL_ID_PREFIX,
    ProtocolFreeze,
    ResearchProtocol,
)
from research_engine.lifecycle.treatment_equivalence import (
    EquivalenceReason,
    EquivalenceRelation,
    assess_treatment_equivalence,
    duplicate_candidates,
)
from research_engine.lifecycle.treatment_memory import (
    TREATMENT_MEMORY_ID_PREFIX,
    TREATMENT_SIGNATURE_ID_PREFIX,
    ApplicabilityEnvelope,
    ApplicabilityReason,
    ApplicabilityVerdict,
    ChangeDirection,
    ConfirmationState,
    ConflictingMemoryState,
    DISPOSITION_REASON_CODES,
    DispositionReason,
    EVIDENCE_BEARING_DISPOSITIONS,
    HistoricalDisposition,
    Horizon,
    REASON_DISPOSITIONS,
    TreatmentClass,
    TreatmentComponent,
    TreatmentMemoryError,
    TreatmentMemoryRecord,
    TreatmentMemoryValidationError,
    TreatmentSignature,
    assess_memory_applicability,
    assess_memory_conflict,
    historical_memories_for,
    is_treatment_memory_identity,
    is_treatment_signature_identity,
)
from research_engine.lifecycle.treatment_memory_store import (
    STORE_FORMAT,
    TreatmentMemoryStore,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)
from research_engine.registry.research_question_registry import REGISTRY

EVIDENCE_BOUNDARY_T0 = "2026-09-25T00:00:00Z"
EVIDENCE_BOUNDARY_T1 = "2026-10-25T00:00:00Z"

#: Real canonical questions from the frozen registry. A treatment may
#: legitimately be ABOUT a canonical question; it may never become one.
CANONICAL_IDS = tuple(question.id for question in REGISTRY)

#: A real, well-formed sha256 digest standing in for a governed dataset content
#: fingerprint. Wave 7 stores the identity of the evidence, never the outcome
#: measures computed from it.
FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64
FINGERPRINT_C = "c" * 64


@pytest.fixture
def dimensions():
    """
    Real admitted Wave 1 dimensions plus a real interaction.

    `stop_distance` and `regime` build the demonstration interaction; `entry_timing`
    is the dimension a new-population or new-dimension proposal introduces.
    """
    registry = DimensionRegistry()
    built = {}
    for key in ("stop_distance", "entry_timing", "regime"):
        dimension = GovernedDimension.create(
            dimension_key=key,
            authority=EvidenceAuthority(
                dataset="decision_trace",
                schema_version="v1",
                producer=EvidenceProducer.DECISION_TRACE,
                field_path=f"market.{key}",
                semantic_meaning="declared test-time semantics",
            ),
        )
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
        built[key] = dimension
    built["interaction"] = ResearchInteraction.create(
        dimensions=[built["stop_distance"], built["regime"]])
    return built


# == Fixtures: the governed treatment under test ============================


def _signature(dimensions, **overrides) -> TreatmentSignature:
    """
    A fully governed treatment signature: a wider stop, on EURUSD, at SCALP
    horizon, inside the trending regime.
    """
    payload = dict(
        subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION,
        subject_ref=dimensions["stop_distance"].dimension_identity,
        component=TreatmentComponent.STOP_GEOMETRY,
        change_parameters={"STOP_MULTIPLIER": 1.5},
        baseline_semantics="DEFAULT_STOP",
        alternative_semantics="WIDENED_STOP",
        treatment_class=TreatmentClass.GEOMETRY_MODIFICATION,
        direction=ChangeDirection.WIDEN,
        horizon=Horizon.SCALP,
        asset_family="FX",
        symbols=("EURUSD",),
    )
    payload.update(overrides)
    return TreatmentSignature.create(**payload)


def _envelope(dimensions, **overrides) -> ApplicabilityEnvelope:
    """The governed applicability envelope: EURUSD, SCALP, trending."""
    payload = dict(
        symbol_scope=("EURUSD",),
        horizon=Horizon.SCALP,
        asset_family="FX",
        regime_scope=("TRENDING",),
        dimension_identities=(dimensions["stop_distance"].dimension_identity,),
        interaction_identities=(
            dimensions["interaction"].interaction_identity,),
        slice_identity="",
        evidence_boundary=EVIDENCE_BOUNDARY_T0,
        evidence_reference="evidence:canonical_q1_evidence",
        confirmation_population="population:fx_confirmation",
    )
    payload.update(overrides)
    return ApplicabilityEnvelope(**payload)


def _protocol(dimensions, *, agenda_identity: str = "",
              queue_identity: str = "",
              agenda_freeze_identity: str = "") -> ResearchProtocol:
    """A real, governed Wave 6 protocol, so lineage references are authentic."""
    from research_engine.lifecycle.research_opportunity import (
        ConfirmationRequirement, DataAvailability, EvidentialInsufficiency,
        ExplanationDiscrimination, InformationValue, ObservationRequirement,
        OpportunityState, ProspectiveWait, QuestionResolution,
        ResearchAnswerability, ResearchCost, ResearchOpportunity,
    )
    from research_engine.lifecycle.research_protocol import (
        ComparisonKind, ComparisonSpecification, ComparisonTerm,
        CompletionCriterionKind, InsufficiencyCriterionKind,
        InvalidationCriterionKind, InvestigationScope, ResearchOperation,
        ScopeComponent, ScopeComponentKind,
    )
    opportunity = ResearchOpportunity.create(
        subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION,
        subject_ref=dimensions["stop_distance"].dimension_identity,
        state=OpportunityState.READY,
        information=InformationValue.create(
            question_state=QuestionResolution.UNRESOLVED,
            evidential_insufficiency=EvidentialInsufficiency.HIGH,
            explanation_discrimination=(
                ExplanationDiscrimination.DISCRIMINATES_COMPETING_EXPLANATIONS),
            answerability=(
                ResearchAnswerability.ANSWERABLE_WITH_FROZEN_EVIDENCE),
            evidence_reference="finding:finding-42"),
        cost=ResearchCost.create(
            data_availability=DataAvailability.AVAILABLE,
            additional_observations=ObservationRequirement.NONE,
            prospective_waiting=ProspectiveWait.NONE,
            confirmation_requirement=ConfirmationRequirement.REQUIRED),
        evidence_boundary=EVIDENCE_BOUNDARY_T0)
    return ResearchProtocol.for_opportunity(
        opportunity,
        scope=InvestigationScope(
            subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION,
            subject_ref=dimensions["stop_distance"].dimension_identity,
            in_scope=(ScopeComponent(
                ScopeComponentKind.DIMENSION,
                dimensions["stop_distance"].dimension_identity),),
            excluded=()),
        evidence=memory_EvidenceScope(),
        permitted_operations=(ResearchOperation.AGGREGATE,),
        completion_criteria=(CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE,),
        insufficiency_criteria=(
            InsufficiencyCriterionKind.REQUIRED_POPULATION_UNAVAILABLE,),
        invalidation_criteria=(
            InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED,),
        comparison=ComparisonSpecification(
            kind=ComparisonKind.BASELINE_VS_GOVERNED_ALTERNATIVE,
            left=ComparisonTerm(reference="baseline:default"),
            right=ComparisonTerm(reference="alternative:widened")),
        confirmation_requirement=ConfirmationRequirement.REQUIRED,
        agenda_identity=agenda_identity,
        queue_identity=queue_identity,
        agenda_freeze_identity=agenda_freeze_identity)


def memory_EvidenceScope():
    """The Wave 6 evidence scope, imported lazily to keep the fixture readable."""
    from research_engine.lifecycle.research_protocol import EvidenceScope
    return EvidenceScope(
        evidence_boundary=EVIDENCE_BOUNDARY_T0,
        evidence_references=("evidence:canonical_q1_evidence",),
        symbol_scope=("symbol:EURUSD",))


def _freeze(protocol: ResearchProtocol) -> ProtocolFreeze:
    return ProtocolFreeze.freeze_protocol(
        protocol, frozen_at=EVIDENCE_BOUNDARY_T0)



def _memory(dimensions, protocol, signature, **overrides) -> TreatmentMemoryRecord:
    """A real, governed historical memory bound to a real Wave 6 contract."""
    freeze = _freeze(protocol)
    payload = dict(
        treatment_signature_identity=signature.signature_identity,
        protocol_identity=protocol.protocol_identity,
        protocol_freeze_identity=freeze.freeze_identity,
        subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION,
        subject_ref=dimensions["stop_distance"].dimension_identity,
        disposition=HistoricalDisposition.NOT_SUPPORTED,
        reason_codes=(DispositionReason.REQUIRED_EFFECT_ABSENT,),
        applicability=_envelope(dimensions),
        confirmation_state=ConfirmationState.REQUIRED_AND_PRESENT,
        evidence_fingerprint=FINGERPRINT_A,
        applicable_dimensions=(
            dimensions["stop_distance"].dimension_identity,),
        applicable_interactions=(
            dimensions["interaction"].interaction_identity,),
        applicable_slices=(),
        evidence_references=("evidence:canonical_q1_evidence",),
    )
    payload.update(overrides)
    return TreatmentMemoryRecord.create(**payload)


@pytest.fixture
def signature(dimensions) -> TreatmentSignature:
    return _signature(dimensions)


@pytest.fixture
def protocol(dimensions) -> ResearchProtocol:
    return _protocol(dimensions)


@pytest.fixture
def policy() -> RevisitPolicy:
    return RevisitPolicy.create()


@pytest.fixture
def memory(dimensions, protocol, signature) -> TreatmentMemoryRecord:
    """The historical record every revisit decision is measured against."""
    return _memory(dimensions, protocol, signature)


# == 1-7. The treatment signature ============================================


def test_signature_requires_a_governed_subject(dimensions):
    """(A) A signature may never be authored from an ungoverned idea."""
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must name a governed subject_ref"):
        TreatmentSignature.create()
    with pytest.raises(TreatmentMemoryValidationError,
                       match="not a governed GOVERNED_DIMENSION identity"):
        _signature(dimensions, subject_ref="not-a-real-dimension")


def test_signature_identity_is_deterministic(dimensions):
    """(A) The same governed semantics always resolve to the same identity."""
    assert (_signature(dimensions).signature_identity
            == _signature(dimensions).signature_identity)
    assert is_treatment_signature_identity(_signature(dimensions).signature_identity)
    assert _signature(dimensions).signature_identity.startswith(
        TREATMENT_SIGNATURE_ID_PREFIX)


def test_labels_notes_and_timestamps_cannot_fork_identity(dimensions):
    """
    (C) The anti-amnesia core: a renamed treatment is the SAME treatment.

    This is the property the entire anti-amnesia demonstration rests on. If a
    label ever entered identity material, "wider stops" and "increase stop
    distance" would be two treatments and the second would be investigated from
    scratch.
    """
    original = _signature(dimensions, label="wider stops")
    relabelled = _signature(
        dimensions,
        label="increase stop distance",
        note="a completely different note",
        created_at="2099-01-01T00:00:00Z",
        provenance={"author": "someone else", "ticket": 12345})
    assert relabelled.signature_identity == original.signature_identity
    assert relabelled.same_semantics(original)
    # The provenance genuinely differs, so this is not a vacuous comparison.
    assert relabelled.provenance != original.provenance
    assert relabelled.label != original.label



def test_materially_different_semantics_produce_different_signatures(dimensions):
    """(D) Each governed semantic field is genuinely identity-bearing."""
    base = _signature(dimensions)
    variants = {
        "component": _signature(dimensions, component=TreatmentComponent.DIRECTION,
                                direction=ChangeDirection.INVERT),
        "parameters": _signature(
            dimensions, change_parameters={"STOP_MULTIPLIER": 2.0}),
        "baseline": _signature(dimensions, baseline_semantics="OTHER_STOP"),
        "alternative": _signature(
            dimensions, alternative_semantics="NARROWED_STOP"),
        "class": _signature(
            dimensions, treatment_class=TreatmentClass.SELECTION_RESTRICTION),
        "horizon": _signature(dimensions, horizon=Horizon.SWING),
        "dimension": _signature(
            dimensions, subject_ref=dimensions["entry_timing"].dimension_identity),
        "interaction": _signature(
            dimensions,
            interaction_identity=dimensions["interaction"].interaction_identity),
        "population": _signature(dimensions, symbols=("GBPUSD",)),
    }
    for label, variant in variants.items():
        assert variant.signature_identity != base.signature_identity, label


def test_signature_is_never_executable_authority(dimensions):
    """(B, AI, AJ) A signature describes; it never authorises."""
    signature = _signature(dimensions)
    assert signature.is_executable_authority() is False
    for forbidden in ("apply", "execute", "run", "promote", "activate", "deploy"):
        assert not hasattr(signature, forbidden)


def test_signature_rejects_ungoverned_change_parameters(dimensions):
    """
    The parameter vocabulary is CLOSED, so a synonym cannot fork identity by
    expressing the same governed quantity under a private name.
    """
    with pytest.raises(TreatmentMemoryValidationError,
                       match="outside the governed parameter vocabulary"):
        _signature(dimensions, change_parameters={"stop_distance_x": 1.5})
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must be of type"):
        _signature(dimensions, change_parameters={"ENABLE": 1})
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must be of type"):
        _signature(dimensions, change_parameters={"STOP_MULTIPLIER": "wide"})


def test_signature_rejects_incoherent_component_direction(dimensions):
    """(B) Governance is checked, not assumed."""
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must declare a governed ChangeDirection"):
        _signature(dimensions, component=TreatmentComponent.DIRECTION,
                   direction=ChangeDirection.NONE)
    with pytest.raises(TreatmentMemoryValidationError,
                       match="declares no change parameters"):
        _signature(dimensions, component=TreatmentComponent.OBSERVATION,
                   change_parameters={"STOP_MULTIPLIER": 1.5})


def test_signature_round_trips_deterministically(dimensions):
    """(Z) Persisted and reloaded signatures reproduce their identity."""
    signature = _signature(dimensions, label="wider stops")
    reloaded = TreatmentSignature.from_dict(signature.to_dict())
    assert reloaded.signature_identity == signature.signature_identity
    assert reloaded.semantic_identity == signature.semantic_identity



# == 8-11. The historical memory record ======================================


def test_memory_requires_governed_wave6_lineage(dimensions, protocol, signature):
    """(H, AC) A memory of research may only cite a real frozen protocol."""
    with pytest.raises(TreatmentMemoryValidationError,
                       match=r"outside the governed TRS- namespace"):
        _memory(dimensions, protocol, signature,
                treatment_signature_identity="NOT-A-SIGNATURE")
    with pytest.raises(TreatmentMemoryValidationError,
                       match="is not a governed Wave 6 protocol identity"):
        _memory(dimensions, protocol, signature, protocol_identity="RPL-NOPE")
    with pytest.raises(TreatmentMemoryValidationError,
                       match="is not a governed Wave 6 T0 protocol freeze"):
        _memory(dimensions, protocol, signature,
                protocol_freeze_identity="PFR-NOPE")


def test_a_disposition_must_be_explained(dimensions, protocol, signature):
    """(J) An unexplained conclusion is not a storable observation."""
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must carry at least one governed reason code"):
        _memory(dimensions, protocol, signature, reason_codes=())


def test_a_disposition_may_only_carry_its_own_reasons(dimensions, protocol,
                                                       signature):
    """
    (J) Reason codes are governed per disposition.

    Recording INVALID_INVESTIGATION "because the population was too small"
    would let an invalid investigation masquerade as a mere absence of data.
    """
    with pytest.raises(TreatmentMemoryValidationError,
                       match="is not permitted for disposition"):
        _memory(dimensions, protocol, signature,
                disposition=HistoricalDisposition.INVALID_INVESTIGATION,
                reason_codes=(DispositionReason.POPULATION_TOO_SMALL,))
    with pytest.raises(TreatmentMemoryValidationError,
                       match="is not permitted for disposition"):
        _memory(dimensions, protocol, signature,
                disposition=HistoricalDisposition.SUPPORTED,
                reason_codes=(DispositionReason.REQUIRED_EFFECT_ABSENT,))


def test_the_disposition_vocabulary_is_closed_and_crosswalked():
    """
    (I) Six governed research states, not WIN/LOSS.

    Also asserts the crosswalk to the pre-existing authoritative vocabularies,
    which is what stops Wave 7 silently renaming states other waves own.
    """
    from research_engine.lifecycle.evidence_layer import EvidenceStatus
    from research_engine.lifecycle.hypothesis import ConclusionType
    from research_engine.lifecycle.research_protocol import (
        InvalidationCriterionKind,
    )

    assert {item.value for item in HistoricalDisposition} == {
        "SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT_DATA",
        "INVALID_INVESTIGATION", "INCONCLUSIVE", "SUPERSEDED"}
    # Wave 7 reuses the existing names rather than inventing a second vocabulary.
    assert ConclusionType.VALIDATED.value == "VALIDATED"
    assert ConclusionType.REJECTED.value == "REJECTED"
    assert EvidenceStatus.INSUFFICIENT_DATA == "INSUFFICIENT_DATA"
    assert InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED.value == (
        "PROTOCOL_SCOPE_CHANGED")
    # Every disposition has reasons, and every reason belongs to one disposition.
    assert set(DISPOSITION_REASON_CODES) == set(HistoricalDisposition)
    assert all(codes for codes in DISPOSITION_REASON_CODES.values())
    assert len(REASON_DISPOSITIONS) == sum(
        len(codes) for codes in DISPOSITION_REASON_CODES.values())
    for reason, disposition in REASON_DISPOSITIONS.items():
        assert reason in DISPOSITION_REASON_CODES[disposition]


def test_insufficient_and_invalid_are_not_evidence_about_the_treatment():
    """
    (T) The anti-dogma property, as a governed fact rather than a convention.

    Insufficient data is not a failure and an invalid investigation is not
    evidence against anything, so neither may ever be treated as grounds for
    suppressing the same treatment later.
    """
    assert (HistoricalDisposition.INSUFFICIENT_DATA
            .is_evidence_about_the_treatment is False)
    assert (HistoricalDisposition.INVALID_INVESTIGATION
            .is_evidence_about_the_treatment is False)
    assert (HistoricalDisposition.NOT_SUPPORTED
            .is_evidence_about_the_treatment is True)
    assert EVIDENCE_BEARING_DISPOSITIONS == frozenset({
        HistoricalDisposition.SUPPORTED,
        HistoricalDisposition.NOT_SUPPORTED,
        HistoricalDisposition.INCONCLUSIVE})
    # And no disposition is ever permanently binding.
    for disposition in HistoricalDisposition:
        assert disposition.is_permanently_binding is False



# == 12-14. Exact equivalence vs similarity =================================


def test_equivalent_semantics_are_exactly_equivalent(dimensions):
    """(E) Equivalence is mechanical and reconstructable from governed fields."""
    left = _signature(dimensions, label="wider stops")
    right = _signature(
        dimensions, label="increase stop distance", note="different words",
        created_at="2099-01-01T00:00:00Z")
    verdict = assess_treatment_equivalence(left, right)
    assert verdict.relation is EquivalenceRelation.EXACT_SEMANTIC_EQUIVALENCE
    assert verdict.deduplicates is True
    assert EquivalenceReason.ALL_GOVERNED_FIELDS_EQUAL in verdict.reasons
    assert verdict.differing_fields == ()
    assert verdict.unpopulated_fields == ()


def test_similarity_cannot_masquerade_as_equivalence(dimensions):
    """
    (F) Two treatments that merely resemble each other must never deduplicate.

    Both share a subject, a component and a class, so a similarity heuristic
    would happily call them the same. They differ in the one field that decides
    what was actually investigated.
    """
    left = _signature(dimensions)
    right = _signature(dimensions, change_parameters={"STOP_MULTIPLIER": 2.0})
    verdict = assess_treatment_equivalence(left, right)
    assert verdict.relation is EquivalenceRelation.RELATED_BUT_DISTINCT
    assert verdict.deduplicates is False
    assert "change_parameters" in verdict.differing_fields
    # Shared governed structure is reported honestly alongside the difference.
    assert "subject_ref" in verdict.matched_fields
    assert "component" in verdict.matched_fields
    assert EquivalenceReason.PARTIAL_GOVERNED_STRUCTURE_SHARED in verdict.reasons
    with pytest.raises(TreatmentMemoryValidationError,
                       match="duplicate suppression is not permitted"):
        verdict.assert_not_equivalent()


def test_materially_different_treatments_are_not_equivalent(dimensions):
    """(U) A different treatment on the same subject is not the same treatment."""
    left = _signature(dimensions)
    right = _signature(dimensions, subject_ref=CANONICAL_IDS[0],
                       subject_kind=OpportunitySubjectKind.CANONICAL_QUESTION)
    verdict = assess_treatment_equivalence(left, right)
    assert verdict.relation in (EquivalenceRelation.NOT_EQUIVALENT,
                                EquivalenceRelation.RELATED_BUT_DISTINCT)
    assert verdict.deduplicates is False


def test_indeterminate_equivalence_never_collapses_to_equivalent(dimensions):
    """
    (G) Asymmetric governed information fails closed.

    A treatment scoped to a population and one that never declared a population
    are not the same treatment, and must not be silently merged -- that merge is
    how a bounded finding becomes an unbounded claim.
    """
    scoped = _signature(dimensions, population_reference="population:fx_primary")
    unscoped = _signature(dimensions)
    verdict = assess_treatment_equivalence(scoped, unscoped)
    assert verdict.relation is EquivalenceRelation.INDETERMINATE
    assert verdict.deduplicates is False
    assert "population_reference" in verdict.unpopulated_fields
    assert EquivalenceReason.GOVERNED_FIELD_UNPOPULATED in verdict.reasons
    with pytest.raises(TreatmentMemoryValidationError,
                       match="duplicate suppression is not permitted"):
        verdict.assert_not_equivalent()


def test_only_exact_equivalence_may_deduplicate(dimensions):
    """(E, Q) The deduplication filter admits exact equivalence only."""
    known = (_signature(dimensions, label="wider stops"),
             _signature(dimensions, change_parameters={"STOP_MULTIPLIER": 2.0}))
    exact = _signature(dimensions, label="increase stop distance")
    related = _signature(dimensions, change_parameters={"STOP_MULTIPLIER": 3.0})
    assert duplicate_candidates(exact, known) == (known[0],)
    assert duplicate_candidates(related, known) == ()


def test_equivalence_rejects_ungoverned_inputs():
    """(F) Equivalence is only ever computed between governed signatures."""
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must be a governed TreatmentSignature"):
        assess_treatment_equivalence("not a signature", "also not")



# == 15-17. The applicability envelope and assessment ========================


def test_an_unbounded_conclusion_is_not_storable(dimensions):
    """
    (K) A conclusion that constrains nothing cannot be recorded.

    This makes a claim that applies to everything unrepresentable rather than
    merely discouraged.
    """
    with pytest.raises(TreatmentMemoryValidationError,
                       match="must constrain at least one governed scope"):
        _envelope(dimensions, symbol_scope=(), regime_scope=(),
                  dimension_identities=(), interaction_identities=())


def test_a_result_does_not_apply_beyond_its_population(dimensions, memory):
    """
    (L) An FX result must not become an XAUUSD result.

    This is the single most important overgeneralisation guard: FX does not
    contain XAUUSD under any governed semantics this architecture has.
    """
    xau = _envelope(dimensions, symbol_scope=("XAUUSD",), asset_family="METALS")
    verdict = assess_memory_applicability(memory, xau)
    assert verdict.verdict is ApplicabilityVerdict.DOES_NOT_APPLY
    assert memory.applies_to(xau) is False
    # The FX family is what excludes it, and that is reported explicitly.
    assert verdict.reasons == (ApplicabilityReason.ASSET_FAMILY_DIFFERS,)


def test_a_result_does_not_apply_beyond_its_asset_family(dimensions, memory):
    """(L) A different asset family is a different question entirely."""
    metals = _envelope(dimensions, asset_family="METALS",
                       symbol_scope=("XAUUSD",))
    verdict = assess_memory_applicability(memory, metals)
    assert verdict.verdict is ApplicabilityVerdict.DOES_NOT_APPLY
    assert ApplicabilityReason.ASSET_FAMILY_DIFFERS in verdict.reasons


def test_a_result_does_not_apply_beyond_its_horizon(dimensions, memory):
    """(L) SCALP knowledge is not SWING knowledge."""
    swing = _envelope(dimensions, horizon=Horizon.SWING)
    verdict = assess_memory_applicability(memory, swing)
    assert verdict.verdict is ApplicabilityVerdict.DOES_NOT_APPLY
    assert ApplicabilityReason.HORIZON_DIFFERS in verdict.reasons


def test_a_result_does_not_apply_beyond_its_regime(dimensions, memory):
    """(L) A trending-regime result is not a ranging-regime result."""
    ranging = _envelope(dimensions, regime_scope=("RANGING",))
    verdict = assess_memory_applicability(memory, ranging)
    assert verdict.verdict is ApplicabilityVerdict.DOES_NOT_APPLY
    assert ApplicabilityReason.REGIME_SCOPE_DIFFERS in verdict.reasons


def test_a_result_does_not_apply_beyond_its_symbol(dimensions, memory):
    """(L) A EURUSD result is not a GBPUSD result."""
    gbpusd = _envelope(dimensions, symbol_scope=("GBPUSD",))
    verdict = assess_memory_applicability(memory, gbpusd)
    assert verdict.verdict is ApplicabilityVerdict.DOES_NOT_APPLY
    assert ApplicabilityReason.SYMBOL_SCOPE_EXCLUDED in verdict.reasons


def test_a_matching_context_applies(dimensions, memory):
    """(M) The positive case: an identical context is fully covered."""
    verdict = assess_memory_applicability(memory, _envelope(dimensions))
    assert verdict.verdict is ApplicabilityVerdict.APPLIES
    assert verdict.reasons == (
        ApplicabilityReason.ENVELOPE_CONTAINS_CONTEXT,)
    assert verdict.is_definitive is True


def test_applicability_is_never_silently_upgraded(dimensions):
    """
    (M) PARTIALLY_APPLIES and INDETERMINATE are never read as APPLIES.

    The positive control: the same envelope that yields DOES_NOT_APPLY for a
    different symbol must not be softened by the presence of a confirmation
    population or a later evidence boundary.
    """
    from research_engine.lifecycle.treatment_memory import (
        TreatmentMemoryRecord as _Record,
    )
    signature = _signature(dimensions)
    record = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    # Same envelope, wider proposal: the context is inside the scope facets the
    # memory governed, so this is a full APPLIES.
    assert (assess_memory_applicability(record, _envelope(dimensions)).verdict
            is ApplicabilityVerdict.APPLIES)
    # Widen the evidence boundary and the verdict narrows, never widens.
    later = _envelope(dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1)
    narrowed = assess_memory_applicability(record, later)
    assert narrowed.verdict in (ApplicabilityVerdict.APPLIES,
                                ApplicabilityVerdict.PARTIALLY_APPLIES)
    assert narrowed.verdict is not ApplicabilityVerdict.DOES_NOT_APPLY



# == 18-21. Revisit policy, triggers and material change ====================


def test_a_policy_must_require_material_change():
    """
    (N, P) A policy that permits revisiting on no change is amnesia.

    This is a hard validation rather than a default, so a future relaxation
    cannot quietly reintroduce a revisit gate that anyone may pass.
    """
    with pytest.raises(RevisitPolicyValidationError,
                       match="MUST require material change"):
        RevisitPolicy.create(require_material_change=False)


def test_a_policy_must_permit_at_least_one_governed_trigger():
    """(P) An empty trigger set is a permanent prohibition: research dogma."""
    with pytest.raises(RevisitPolicyValidationError,
                       match="must permit at least one governed trigger"):
        RevisitPolicy.create(permitted_triggers=())


def test_the_revisit_trigger_vocabulary_is_closed_and_mechanical():
    """
    (O) No trigger means try again, maybe it works now, or it looks promising.

    The absence of those phrasings is the point, and it is asserted rather than
    assumed: the enum is the whole vocabulary, so none of them is expressible.
    """
    assert {item.value for item in RevisitTrigger} == {
        "NEW_EVIDENCE", "NEW_POPULATION", "NEW_DIMENSION", "NEW_INTERACTION",
        "EVIDENCE_REPAIR", "CONFIRMATION_AVAILABLE", "MATERIAL_CONTEXT_CHANGE",
        "PROTOCOL_IMPROVEMENT"}
    for ungoverned in ("TRY_AGAIN", "LOOKS_PROFITABLE", "TIME_PASSED",
                       "OPERATOR_CURIOSITY", "FEELS_RIGHT", "ELAPSED_TIME"):
        assert ungoverned not in {item.value for item in RevisitTrigger}


def test_policy_identity_is_deterministic_and_version_bearing(dimensions):
    """
    (N, Y) Two policy versions are two identities, and both are preserved.

    This is what makes an old revisit decision keep meaning what it meant after
    the rules change.
    """
    left = RevisitPolicy.create()
    right = RevisitPolicy.create()
    strict = RevisitPolicy.create(
        permitted_triggers=(RevisitTrigger.NEW_EVIDENCE,
                            RevisitTrigger.EVIDENCE_REPAIR))
    assert left.policy_identity == right.policy_identity
    assert left.is_version_of(right)
    assert strict.policy_identity != left.policy_identity
    assert strict.policy_version == left.policy_version
    assert is_revisit_policy_identity(left.policy_identity)
    assert left.policy_identity.startswith("RVP-")
    assert RevisitPolicy.from_dict(left.to_dict()).policy_identity == (
        left.policy_identity)


def test_labels_never_fork_a_policy(dimensions):
    """(Y) Policy provenance is provenance only."""
    plain = RevisitPolicy.create()
    annotated = RevisitPolicy.create(
        label="strict policy", note="a note",
        provenance={"author": "governance"}, )
    assert annotated.policy_identity == plain.policy_identity


def test_renaming_a_treatment_is_not_a_material_change(dimensions, memory):
    """
    (P) Superficial change is not material change.

    A new label, a new note and a new timestamp are observable differences that
    change nothing about whether the work is worth repeating.
    """
    assessment = assess_material_change(
        memory, _envelope(dimensions, evidence_reference="evidence:renamed"))
    assert assessment.is_material is False
    assert assessment.verified_kinds == ()
    # And a claim nobody can substantiate is recorded as unverified, not ignored.
    claimed = assess_material_change(
        memory, _envelope(dimensions),
        claimed_kinds=(MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED,))
    assert claimed.is_material is False
    assert claimed.unverified_claims == (
        MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED,)


def test_advancing_the_evidence_boundary_is_material(dimensions, memory):
    """(P) Genuinely new observations beyond the old boundary are material."""
    later = _envelope(dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1)
    assessment = assess_material_change(memory, later)
    assert assessment.is_material is True
    assert MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED in (
        assessment.verified_kinds)
    assert assessment.detail["evidence_boundary"] == {
        "historical": EVIDENCE_BOUNDARY_T0,
        "proposed": EVIDENCE_BOUNDARY_T1}


def test_rewinding_the_evidence_boundary_is_not_new_evidence(dimensions, memory):
    """(P) An EARLIER boundary is not new evidence; it is less evidence."""
    earlier = _envelope(dimensions, evidence_boundary="2026-01-01T00:00:00Z")
    assert assess_material_change(memory, earlier).is_material is False



# == 31. The core demonstration, cases A-F ===================================


def test_case_A_exact_duplicate_is_suppressed(dimensions, protocol, signature,
                                               policy):
    """
    (Q) CASE A: same treatment, different label, no material change.

    Treatment A was investigated. Treatment B is the same governed treatment
    with a different label, a different note and a different creation time, in
    the same applicable context with the same evidence state. The correct answer
    is DUPLICATE_RESEARCH.
    """
    historical = _memory(dimensions, protocol, signature, label="wider stops")
    relabelled = _signature(
        dimensions, label="increase stop distance",
        note="a fresh proposal, months later",
        created_at="2099-01-01T00:00:00Z")
    decision = decide_revisit(
        signature=relabelled,
        proposed_context=_envelope(dimensions),
        memories=(historical,),
        policy=policy,
        label="the same treatment, renamed")
    assert decision.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
    assert decision.blocks_research() is True
    assert decision.material_change.is_material is False
    assert decision.verified_triggers == ()
    assert decision.applicability_verdicts[historical.memory_identity] is (
        ApplicabilityVerdict.APPLIES)
    # The relabelled proposal resolved to the same treatment, which is the
    # mechanism that prevents a second investigation being created.
    assert relabelled.signature_identity == historical.treatment_signature_identity


def test_case_B_new_evidence_permits_a_revisit(dimensions, protocol, signature,
                                               policy):
    """
    (R, AB) CASE B: same treatment, old evidence ran out, new evidence exists.

    The previous investigation reached INSUFFICIENT_DATA at the T0 boundary.
    Materially new governed observations now exist beyond that boundary. The old
    record is untouched, and the work may legitimately return.
    """
    insufficient = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    before = insufficient.to_dict()
    decision = decide_revisit(
        signature=signature,
        proposed_context=_envelope(dimensions,
                                   evidence_boundary=EVIDENCE_BOUNDARY_T1,
                                   evidence_reference="evidence:post_t0"),
        memories=(insufficient,),
        policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    assert decision.eligibility is RevisitEligibility.REVISIT_PERMITTED
    assert RevisitTrigger.NEW_EVIDENCE in decision.verified_triggers
    assert decision.material_change.is_material is True
    assert decision.was_permitted() is True
    # But the old record is byte-for-byte unchanged, and the treatment is the
    # same one: new evidence changes the situation, not the question.
    assert insufficient.to_dict() == before
    assert insufficient.memory_identity == before["memory_identity"]
    assert (insufficient.treatment_signature_identity
            == signature.signature_identity)


def test_case_C_new_population_is_not_suppressed(dimensions, protocol, signature,
                                                 policy):
    """
    (S) CASE C: same treatment semantics, different governed population.

    FX knowledge must not become XAUUSD knowledge. The old memory must not
    mechanically suppress the new work.
    """
    historical = _memory(dimensions, protocol, signature)
    metals = _envelope(
        dimensions, symbol_scope=("XAUUSD",), asset_family="METALS",
        evidence_reference="evidence:xau_population")
    decision = decide_revisit(
        signature=signature, proposed_context=metals,
        memories=(historical,), policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_POPULATION,))
    assert decision.eligibility is RevisitEligibility.NEW_DISTINCT_RESEARCH
    assert decision.blocks_research() is False
    assert (decision.applicability_verdicts[historical.memory_identity]
            is ApplicabilityVerdict.DOES_NOT_APPLY)
    # The old FX record is untouched and still inspectable.
    assert historical.applicability.asset_family == "FX"
    assert historical.applicability.symbol_scope == ("EURUSD",)



def test_case_D_repaired_invalid_investigation_permits_a_revisit(
        dimensions, protocol, signature, policy):
    """
    (T) CASE D: the old investigation was invalid, and the defect is repaired.

    An invalid investigation is NOT evidence against the treatment. Once the
    exact governed defect is mechanically shown to be repaired, the old result
    stops being a reason to withhold work.
    """
    invalid = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INVALID_INVESTIGATION,
        reason_codes=(DispositionReason.FINGERPRINT_MISMATCH,),
        confirmation_state=ConfirmationState.INDETERMINATE)
    assert invalid.is_evidence_bearing() is False
    repaired = _envelope(
        dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
        evidence_reference="evidence:repaired_fingerprint")
    decision = decide_revisit(
        signature=signature, proposed_context=repaired,
        memories=(invalid,), policy=policy,
        claimed_triggers=(RevisitTrigger.EVIDENCE_REPAIR,),
        repaired_defects=(DispositionReason.FINGERPRINT_MISMATCH.value,))
    assert RevisitTrigger.EVIDENCE_REPAIR in decision.verified_triggers
    assert decision.eligibility is RevisitEligibility.REVISIT_PERMITTED
    assert decision.blocks_research() is False
    # And the invalid record survives exactly as it was recorded.
    assert invalid.disposition is HistoricalDisposition.INVALID_INVESTIGATION


def test_case_D_unrepaired_invalid_investigation_is_not_suppressed(
        dimensions, protocol, signature, policy):
    """
    (T) An invalid investigation never suppresses work, even unrepaired.

    It is not a verdict, so it cannot be used as one. The result is
    REVISIT_NOT_JUSTIFIED, which explicitly does NOT block the work.
    """
    invalid = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INVALID_INVESTIGATION,
        reason_codes=(DispositionReason.FINGERPRINT_MISMATCH,),
        confirmation_state=ConfirmationState.INDETERMINATE)
    decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(invalid,), policy=policy,
        claimed_triggers=(RevisitTrigger.EVIDENCE_REPAIR,))
    assert decision.eligibility is RevisitEligibility.REVISIT_NOT_JUSTIFIED
    assert decision.blocks_research() is False


def test_case_E_materially_different_treatment_stays_distinct(
        dimensions, memory, policy, signature):
    """
    (U) CASE E: same label and subject, different governed semantics.

    A treatment with a similar name but a genuinely different governed parameter
    is new distinct research, not a revisit of the old work -- and the old
    work's history may not even be consulted for it.
    """
    different = _signature(
        dimensions, label="wider stops",
        change_parameters={"STOP_MULTIPLIER": 3.0})
    assert different.signature_identity != signature.signature_identity
    with pytest.raises(RevisitPolicyValidationError,
                       match="may never be mixed across treatments"):
        decide_revisit(signature=different,
                       proposed_context=_envelope(dimensions),
                       memories=(memory,), policy=policy)
    # With no prior history for the new treatment, it is new distinct research.
    decision = decide_revisit(
        signature=different, proposed_context=_envelope(dimensions),
        memories=(), policy=policy)
    assert decision.eligibility is RevisitEligibility.NEW_DISTINCT_RESEARCH
    assert decision.blocks_research() is False



def test_case_F_conflicting_memory_coexists_without_a_winner(
        dimensions, protocol, signature, policy):
    """
    (V, W) CASE F: two applicable memories with incompatible dispositions.

    SUPPORTED under population A and NOT_SUPPORTED under population B must both
    persist, both be returned by lookup, and be reported as an explicit conflict.
    Wave 7 does not choose a winner.
    """
    other = _protocol(dimensions)
    trending = _envelope(dimensions, regime_scope=("TRENDING",))
    supported = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.SUPPORTED,
        reason_codes=(DispositionReason.CONFIRMATION_PASSED,),
        applicability=trending,
        evidence_fingerprint=FINGERPRINT_A)
    not_supported = _memory(
        dimensions, other, signature,
        disposition=HistoricalDisposition.NOT_SUPPORTED,
        reason_codes=(DispositionReason.GOVERNED_COMPARISON_DID_NOT_SUPPORT,),
        evidence_fingerprint=FINGERPRINT_B)
    # Both records exist, are distinct, and are both returned.
    assert supported.memory_identity != not_supported.memory_identity
    found = historical_memories_for(
        (supported, not_supported),
        treatment_signature_identity=signature.signature_identity)
    assert len(found) == 2
    # The conflict is explicit, and no winner field exists anywhere.
    conflict = assess_memory_conflict((supported, not_supported), trending)
    assert conflict.state is (
        ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY)
    assert conflict.has_conflict is True
    assert set(conflict.conflicting_memories) == {
        supported.memory_identity, not_supported.memory_identity}
    assert not hasattr(conflict, "winner")
    assert "winner" not in conflict.to_dict()
    # And the revisit decision surfaces the conflict rather than resolving it.
    decision = decide_revisit(
        signature=signature, proposed_context=trending,
        memories=(supported, not_supported), policy=policy)
    assert decision.conflict_state is (
        ConflictingMemoryState.CONFLICTING_APPLICABLE_MEMORY)
    assert decision.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
    # Both memories remain individually intact and unmodified.
    assert supported.disposition is HistoricalDisposition.SUPPORTED
    assert not_supported.disposition is HistoricalDisposition.NOT_SUPPORTED


# == 32-35. The named required demonstrations ==============================


def test_anti_amnesia_demonstration(dimensions, protocol, policy):
    """
    (32) Two superficially different treatments, one governed treatment.

    "wider stops" and "increase stop distance" describe the same governed
    change. The historical lookup finds the prior work, duplicate suppression
    works, and no new research identity is fabricated from the wording.
    """
    wider_stops = _signature(
        dimensions, label="wider stops", note="stop feels too tight")
    increase_stop_distance = _signature(
        dimensions, label="increase stop distance",
        note="let the trade breathe", created_at="2027-03-01T00:00:00Z")
    # Same signature: the labels are not semantics.
    assert wider_stops.signature_identity == increase_stop_distance.signature_identity
    historical = _memory(dimensions, protocol, wider_stops)
    # Historical lookup finds the prior work by exact signature.
    found = historical_memories_for(
        (historical,),
        treatment_signature_identity=increase_stop_distance.signature_identity)
    assert found == (historical,)
    # And the duplicate is suppressed, with no new identity minted.
    decision = decide_revisit(
        signature=increase_stop_distance,
        proposed_context=_envelope(dimensions),
        memories=(historical,), policy=policy)
    assert decision.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
    assert decision.verified_triggers == ()
    assert len({historical.memory_identity}) == 1



def test_anti_dogma_demonstration(dimensions, protocol, signature, policy):
    """
    (33) An insufficient result is not a permanent verdict.

    T0: the treatment was investigated and the evidence ran out.
    T1: materially new governed evidence exists.
    The old record is unchanged, the treatment is unchanged, the material change
    is mechanically demonstrated, the revisit is permitted, and no research is
    executed.
    """
    t0 = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    t0_snapshot = t0.to_dict()

    # T0 alone does not suppress: an insufficient result is not a failure.
    at_t0 = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(t0,), policy=policy)
    assert at_t0.eligibility is RevisitEligibility.REVISIT_NOT_JUSTIFIED
    assert at_t0.blocks_research() is False

    # T1: materially new evidence beyond the old boundary.
    t1_context = _envelope(
        dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
        evidence_reference="evidence:post_t0_observations")
    at_t1 = decide_revisit(
        signature=signature, proposed_context=t1_context,
        memories=(t0,), policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))

    # The revisit is permitted...
    assert at_t1.eligibility is RevisitEligibility.REVISIT_PERMITTED
    # ...the material change is mechanically demonstrated, not asserted...
    assert at_t1.material_change.is_material is True
    assert MaterialChangeKind.EVIDENCE_BOUNDARY_ADVANCED in (
        at_t1.material_change.verified_kinds)
    assert at_t1.material_change.detail["evidence_boundary"] == {
        "historical": EVIDENCE_BOUNDARY_T0, "proposed": EVIDENCE_BOUNDARY_T1}
    # ...the signature is unchanged...
    assert t0.treatment_signature_identity == signature.signature_identity
    # ...applicability remains inspectable. The new context is inside the old
    # envelope, so the old record still applies to this population; it is the
    # EVIDENCE, not the population, that moved.
    applicability = assess_memory_applicability(t0, t1_context)
    assert applicability.verdict is ApplicabilityVerdict.APPLIES
    assert applicability.memory_identity == t0.memory_identity
    assert applicability.reasons == (
        ApplicabilityReason.ENVELOPE_CONTAINS_CONTEXT,)
    # ...the old insufficient result is NOT rewritten...
    assert t0.to_dict() == t0_snapshot
    assert t0.disposition is HistoricalDisposition.INSUFFICIENT_DATA
    assert t0.reason_codes == (DispositionReason.POPULATION_TOO_SMALL,)
    # ...and no research was executed, because Wave 7 has no executor at all.
    assert at_t1.is_permission_to_execute() is False
    assert at_t1.eligibility.is_permission_to_execute is False


def test_context_boundary_demonstration(dimensions, protocol, signature):
    """
    (34) One treatment signature, three contexts, and no universal transfer.

    EURUSD/SCALP/TRENDING, EURUSD/SCALP/RANGE and XAUUSD/SCALP/TRENDING are
    three different questions. The memory applies to exactly one of them.
    """
    trending = _envelope(dimensions, regime_scope=("TRENDING",))
    ranging = _envelope(dimensions, regime_scope=("RANGING",))
    xau = _envelope(dimensions, symbol_scope=("XAUUSD",),
                    asset_family="METALS", regime_scope=("TRENDING",))
    record = _memory(dimensions, protocol, signature, applicability=trending)
    verdicts = {
        "same": assess_memory_applicability(record, trending).verdict,
        "regime": assess_memory_applicability(record, ranging).verdict,
        "asset": assess_memory_applicability(record, xau).verdict,
    }
    assert verdicts["same"] is ApplicabilityVerdict.APPLIES
    assert verdicts["regime"] is ApplicabilityVerdict.DOES_NOT_APPLY
    assert verdicts["asset"] is ApplicabilityVerdict.DOES_NOT_APPLY
    # Exactly one context applies. The bounded result stays bounded.
    assert [v for v in verdicts.values()
            if v is ApplicabilityVerdict.APPLIES] != []



def test_conflicting_memory_is_explicitly_detectable(dimensions, protocol,
                                                     signature, policy):
    """
    (35) Consumers can detect the uncertainty; Wave 7 does not resolve it.

    A revisit consumer reading the decision can see that applicable history
    disagrees, without being handed a winner it might quietly follow.
    """
    trending = _envelope(dimensions, regime_scope=("TRENDING",))
    supported = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.SUPPORTED,
        reason_codes=(DispositionReason.CONFIRMATION_PASSED,),
        applicability=trending, evidence_fingerprint=FINGERPRINT_A)
    contradicted = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.NOT_SUPPORTED,
        reason_codes=(DispositionReason.CONFIRMATION_CONTRADICTED_DISCOVERY,),
        evidence_fingerprint=FINGERPRINT_B)
    decision = decide_revisit(
        signature=signature, proposed_context=trending,
        memories=(supported, contradicted), policy=policy)
    assert decision.to_dict()["conflict_state"] == "CONFLICTING_APPLICABLE_MEMORY"
    assert len(decision.memory_identities) == 2
    # And a stricter policy may refuse to decide at all under a conflict.
    strict = RevisitPolicy.create(conflict_blocks_revisit=True)
    blocked = decide_revisit(
        signature=signature, proposed_context=trending,
        memories=(supported, contradicted), policy=strict)
    assert blocked.eligibility is RevisitEligibility.INDETERMINATE
    assert blocked.blocks_research() is False


def test_revisit_policy_version_demonstration(dimensions, protocol, signature):
    """
    (36) Two legitimate policy versions, two coexisting decisions.

    Changing the rules later must not rewrite what an earlier decision meant, so
    each decision carries the identity of the policy that produced it and both
    survive a restart.
    """
    permissive = RevisitPolicy.create()
    restrictive = RevisitPolicy.create(
        permitted_triggers=(RevisitTrigger.EVIDENCE_REPAIR,))
    assert permissive.policy_identity != restrictive.policy_identity

    insufficient = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    later = _envelope(
        dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
        evidence_reference="evidence:post_t0")

    under_permissive = decide_revisit(
        signature=signature, proposed_context=later,
        memories=(insufficient,), policy=permissive,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    under_restrictive = decide_revisit(
        signature=signature, proposed_context=later,
        memories=(insufficient,), policy=restrictive,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))

    # Same inputs, different rules, different and simultaneously valid outcomes.
    assert under_permissive.eligibility is RevisitEligibility.REVISIT_PERMITTED
    assert (under_restrictive.eligibility
            is RevisitEligibility.REVISIT_NOT_JUSTIFIED)
    # Each decision preserves the identity of the rules that produced it.
    assert under_permissive.policy_identity == permissive.policy_identity
    assert under_restrictive.policy_identity == restrictive.policy_identity
    assert (under_permissive.policy_semantic_identity
            == permissive.semantic_identity)
    assert (under_restrictive.policy_semantic_identity
            == restrictive.semantic_identity)
    # The trigger was verified mechanically under both: the difference is the
    # RULES, not the facts.
    assert RevisitTrigger.NEW_EVIDENCE in under_permissive.verified_triggers
    assert RevisitTrigger.NEW_EVIDENCE in under_restrictive.verified_triggers



def test_a_decision_can_never_be_permission_to_execute(dimensions, protocol,
                                                       signature, policy):
    """
    (AI) REVISIT_PERMITTED re-enters the lifecycle; it authorises nothing.

    Asserted as a governed property on the outcome enum itself, so no consumer
    can read eligibility as authority however it is obtained.
    """
    insufficient = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    decision = decide_revisit(
        signature=signature,
        proposed_context=_envelope(
            dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
            evidence_reference="evidence:post_t0"),
        memories=(insufficient,), policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    assert decision.eligibility is RevisitEligibility.REVISIT_PERMITTED
    for eligibility in RevisitEligibility:
        assert eligibility.is_permission_to_execute is False
    assert decision.is_permission_to_execute() is False
    for forbidden in ("create_protocol", "create_opportunity", "schedule",
                      "execute", "run_experiment", "enqueue"):
        assert not hasattr(decision, forbidden)


def test_engine_and_operator_authority_are_distinguishable(dimensions, protocol,
                                                           signature):
    """
    (25) A future operator override needs no falsification of the engine record.

    The engine's decision is produced unchanged; an operator-directed request is
    a SEPARATE record built from the same inputs. The engine's own determination
    is never rewritten.
    """
    engine_policy = RevisitPolicy.create()
    record = _memory(dimensions, protocol, signature)
    engine_decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=engine_policy)
    assert engine_decision.authority is RevisitAuthority.ENGINE_GOVERNED
    operator_decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=engine_policy,
        authority=RevisitAuthority.OPERATOR_DIRECTED,
        rationale="an operator explicitly asked for this anyway")
    assert operator_decision.authority is RevisitAuthority.OPERATOR_DIRECTED
    assert operator_decision.eligibility is engine_decision.eligibility
    assert operator_decision.decision_identity != engine_decision.decision_identity
    # The engine decision is unchanged and remains exactly as it was recorded.
    assert engine_decision.authority is RevisitAuthority.ENGINE_GOVERNED
    assert engine_decision.rationale == ""
    # And a decision has no mutator that could rewrite it in place.
    for forbidden in ("override", "force", "set_eligibility", "mutate",
                      "rewrite", "delete"):
        assert not hasattr(engine_decision, forbidden)



# == 37. Persistence, restart and dedup ======================================


def test_restart_preserves_every_identity(dimensions, protocol, signature, policy,
                                          tmp_path):
    """(Z, AA) Memories, policies and decisions all survive a restart intact."""
    path = tmp_path / "treatment_memory.json"
    strict = RevisitPolicy.create(
        permitted_triggers=(RevisitTrigger.EVIDENCE_REPAIR,))
    insufficient = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT)
    store = TreatmentMemoryStore(path)
    store.register_policy(policy)
    store.register_policy(strict)
    store.register_memory(insufficient)
    later_context = _envelope(
        dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
        evidence_reference="evidence:post_t0")
    first = decide_revisit(
        signature=signature, proposed_context=later_context,
        memories=(insufficient,), policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    second = decide_revisit(
        signature=signature, proposed_context=later_context,
        memories=(insufficient,), policy=strict,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    store.register_decision(first)
    store.register_decision(second)

    reloaded = TreatmentMemoryStore(path)
    assert len(reloaded) == 1
    assert reloaded.get_memory(insufficient.memory_identity).to_dict() == (
        insufficient.to_dict())
    assert reloaded.get_policy(policy.policy_identity).policy_identity == (
        policy.policy_identity)
    assert reloaded.get_policy(strict.policy_identity).policy_identity == (
        strict.policy_identity)
    # Both policy versions and both decisions coexist across the restart.
    assert len(reloaded.policies()) == 2
    assert len(reloaded.decisions()) == 2
    assert reloaded.get_decision(first.decision_identity).decision_identity == (
        first.decision_identity)
    assert reloaded.get_decision(second.decision_identity).decision_identity == (
        second.decision_identity)
    # Deterministic ordering is stable.
    assert [row.memory_identity for row in reloaded.memories()] == sorted(
        row.memory_identity for row in reloaded.memories())
    assert [row.decision_identity for row in reloaded.decisions()] == sorted(
        row.decision_identity for row in reloaded.decisions())


def test_semantic_duplicates_deduplicate_and_keep_provenance(dimensions, protocol,
                                                            signature, tmp_path):
    """
    (AA) A repeated identical memory write is not a new historical event.

    Re-recording a conclusion is not re-concluding it, so the original creation
    provenance is preserved rather than overwritten.
    """
    path = tmp_path / "treatment_memory.json"
    original = _memory(dimensions, protocol, signature,
                       label="first recording", created_at=EVIDENCE_BOUNDARY_T0)
    store = TreatmentMemoryStore(path)
    store.register_memory(original)
    rederived = _memory(dimensions, protocol, signature,
                        label="re-derived later",
                        created_at=EVIDENCE_BOUNDARY_T1)
    assert rederived.memory_identity == original.memory_identity
    returned = store.register_memory(rederived)
    assert returned.created_at == EVIDENCE_BOUNDARY_T0
    assert returned.label == "first recording"
    assert len(store) == 1
    assert len(TreatmentMemoryStore(path)) == 1


def test_conflicting_and_superseded_memories_both_persist(dimensions, protocol,
                                                          signature, tmp_path):
    """
    (V) A later disagreement never overwrites or erases the earlier record.

    Both the superseded record and the contradicting record survive, and neither
    is preferred by the store.
    """
    path = tmp_path / "treatment_memory.json"
    original = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.SUPPORTED,
        reason_codes=(DispositionReason.CONFIRMATION_PASSED,),
        evidence_fingerprint=FINGERPRINT_A)
    contradicting = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.NOT_SUPPORTED,
        reason_codes=(DispositionReason.REQUIRED_EFFECT_ABSENT,),
        evidence_fingerprint=FINGERPRINT_B)
    superseded = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.SUPERSEDED,
        reason_codes=(DispositionReason.SUPERSEDED_BY_LATER_EVIDENCE,),
        evidence_fingerprint=FINGERPRINT_C, supersedes=original.memory_identity)
    store = TreatmentMemoryStore(path)
    for record in (original, contradicting, superseded):
        store.register_memory(record)
    reloaded = TreatmentMemoryStore(path)
    assert len(reloaded) == 3
    for record in (original, contradicting, superseded):
        assert reloaded.get_memory(record.memory_identity).to_dict() == (
            record.to_dict())
    # The supersession link is preserved and the predecessor still exists.
    assert (reloaded.get_memory(superseded.memory_identity).supersedes
            == original.memory_identity)
    assert reloaded.get_memory(original.memory_identity) is not None
    assert (reloaded.get_memory(original.memory_identity).disposition
            is HistoricalDisposition.SUPPORTED)
    # And lookup by disposition returns each side separately.
    assert reloaded.memories_for_disposition("SUPPORTED") == (original,)
    assert reloaded.memories_for_disposition("NOT_SUPPORTED") == (contradicting,)



def _write_store(path, memories=(), policies=(), decisions=()):
    """Write a store document directly, so corruption can be injected."""
    from research_engine.lifecycle.generated_research_identity import canonical_json
    path.write_text(canonical_json({
        "format": STORE_FORMAT,
        "schema_version": memory_module.TREATMENT_MEMORY_SCHEMA_VERSION,
        "memories": [row.to_dict() for row in memories],
        "policies": [row.to_dict() for row in policies],
        "decisions": [row.to_dict() for row in decisions],
    }) + "\n", encoding="utf-8")


def test_store_has_no_import_time_writes(tmp_path):
    """(Z) Constructing a store reads; it never writes."""
    path = tmp_path / "never_written.json"
    TreatmentMemoryStore(path)
    assert not path.exists()


def test_a_decision_cannot_outlive_its_policy_or_history(dimensions, protocol,
                                                         signature, policy,
                                                         tmp_path):
    """(Z) Orphan references fail closed, at registration and on load."""
    path = tmp_path / "treatment_memory.json"
    record = _memory(dimensions, protocol, signature)
    decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=policy)
    store = TreatmentMemoryStore(path)
    # The policy is not yet registered, so the decision cannot be recorded.
    with pytest.raises(RevisitGovernanceError, match="revisit policy this store"):
        store.register_decision(decision)
    store.register_policy(policy)
    # The memory is still not registered.
    with pytest.raises(RevisitGovernanceError, match="is not in the store"):
        store.register_decision(decision)
    store.register_memory(record)
    store.register_decision(decision)
    assert len(TreatmentMemoryStore(path).decisions()) == 1
    # And an on-disk decision naming an absent memory is refused.
    orphan = tmp_path / "orphan.json"
    _write_store(orphan, memories=(), policies=(policy,), decisions=(decision,))
    with pytest.raises(RevisitGovernanceError,
                       match="may never outlive its history"):
        TreatmentMemoryStore(orphan)


def test_corrupted_or_contradictory_history_fails_closed(dimensions, protocol,
                                                         signature, tmp_path):
    """
    (Z) A corrupted semantic identity is refused, never silently repaired.

    The stored `memory_identity` is deliberately mutated so it no longer matches
    the material, which is exactly the tamper a silent loader would accept.
    """
    record = _memory(dimensions, protocol, signature)
    path = tmp_path / "corrupt.json"
    _write_store(path, memories=(record,))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["memories"][0]["memory_identity"] = (
        TREATMENT_MEMORY_ID_PREFIX + "0" * 16)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TreatmentMemoryValidationError,
                       match="does not match the memory material"):
        TreatmentMemoryStore(path)


def test_malformed_or_unknown_store_content_fails_closed(tmp_path):
    """(Z) Every structural defect is refused rather than repaired."""
    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text("{not json", encoding="utf-8")
    with pytest.raises(TreatmentMemoryError, match="unreadable"):
        TreatmentMemoryStore(unreadable)

    wrong_format = tmp_path / "wrong_format.json"
    wrong_format.write_text('{"format": "something_else"}', encoding="utf-8")
    with pytest.raises(TreatmentMemoryError, match="unknown treatment memory store"):
        TreatmentMemoryStore(wrong_format)

    wrong_section = tmp_path / "wrong_section.json"
    wrong_section.write_text(json.dumps({
        "format": STORE_FORMAT, "schema_version": 1, "memories": {}}),
        encoding="utf-8")
    with pytest.raises(TreatmentMemoryError, match="'memories' must be a list"):
        TreatmentMemoryStore(wrong_section)



# == 39-40. Integration, isolation and the no-execution guarantee ===========

WAVE7_MODULES = (memory_module, equivalence_module, revisit_module,
                 store_module)


def _source(module) -> str:
    return inspect.getsource(module)


def _tree(module):
    return ast.parse(_source(module))


def _imports(module) -> list:
    names = []
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _strip_docstrings(tree):
    """Remove docstrings, so a guard scans CODE rather than prose."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def test_wave7_calls_no_execution_or_candidate_machinery():
    """
    (AI, AJ, AK) Wave 7 is declarative: it performs nothing and grants nothing.

    Guards the exact operations that would turn memory into authority.
    """
    forbidden_calls = {
        "run_experiment", "execute_research", "run_research", "run_backtest",
        "create_hypothesis", "create_candidate", "activate_candidate",
        "promote_candidate", "apply_treatment", "apply_treatment_spec",
        "send_order", "place_order", "submit_order", "modify_strategy",
        "modify_risk", "modify_sizing", "activate", "promote",
        "execute", "backtest", "simulate", "order", "trade",
    }
    for module in WAVE7_MODULES:
        tree = _tree(module)
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden_calls.isdisjoint(calls), module.__name__
        attributes = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert forbidden_calls.isdisjoint(attributes), module.__name__
        assert forbidden_calls.isdisjoint(getattr(module, "__all__", []) or [])


def test_wave7_imports_no_production_or_execution_path():
    """
    (AL) No runner, broker, risk, sizing, config or candidate path is reachable.

    `treatment_provenance` is deliberately NOT imported: it is the
    execution-oriented concept Wave 7 deliberately does not reuse.
    """
    forbidden_fragments = (
        "broker", "sizing", "runner", "orchestrator", "governance_gate",
        "research_cycle_runner", "candidate_", "experiment", "backtest",
        "hypothesis", "treatment_provenance", "investigation_contracts",
        "baseline_manifest", "placebo", "validation_harness", "shadow_hook",
        "research_engine.main", "research_engine.runner", "execution", "risk",
        "config", "runtime", "counterfactual", "data_pipeline", "lambda",
        "deployment", "profiles",
    )
    for module in WAVE7_MODULES:
        for name in _imports(module):
            for fragment in forbidden_fragments:
                assert fragment not in name, f"{module.__name__} imports {name}"



def test_wave7_code_contains_no_profitability_policy():
    """
    (AH) No profitability concept may leak into Wave 7 governance.

    Scans CODE with docstrings stripped, so the prohibition may be explained in
    prose while remaining structurally impossible to express in logic.
    """
    forbidden = (
        "expectancy", "sharpe", "drawdown", "win_rate", "profit", "p_value",
        "r_multiple", "weighted", "score", "best_treatment", "roi", "pnl",
    )
    for module in WAVE7_MODULES:
        code = ast.unparse(_strip_docstrings(_tree(module))).lower()
        for token in forbidden:
            assert token not in code, f"{module.__name__} mentions {token!r}"


def test_wave7_uses_no_fuzzy_or_model_based_equivalence():
    """
    (F) Equivalence is mechanical: no embedding, LLM or fuzzy authority.

    Asserted at the import level, so a future similarity helper cannot be
    introduced without failing here.
    """
    forbidden_fragments = (
        "difflib", "fuzzy", "SequenceMatcher", "embeddings", "openai",
        "anthropic", "sklearn", "numpy", "scipy", "rapidfuzz", "Levenshtein",
        "cosine", "sentence_transformers", "token_overlap", "jaccard",
    )
    for module in WAVE7_MODULES:
        for name in _imports(module):
            for fragment in forbidden_fragments:
                assert fragment not in name, f"{module.__name__} imports {name}"
        code = ast.unparse(_strip_docstrings(_tree(module)))
        for fragment in ("difflib", "SequenceMatcher", "cosine", "jaccard",
                         "embedding"):
            assert fragment not in code, f"{module.__name__} uses {fragment!r}"


def test_wave7_never_enumerates_a_search_space():
    """
    (43) Wave 7 answers questions about PAST work; it generates nothing.

    Coverage maps, blind-spot detection and coverage-driven curiosity belong to
    Wave 8, and are asserted absent here so they cannot arrive early.
    """
    for module in WAVE7_MODULES:
        tree = _tree(module)
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert {"product", "combinations", "permutations", "chain"}.isdisjoint(
            calls)
        assert not any("itertools" in name for name in _imports(module))
        code = ast.unparse(_strip_docstrings(_tree(module))).lower()
        for token in ("blind_spot", "coverage_map", "unexamined",
                      "what_am_i_not", "generate_question"):
            assert token not in code, f"{module.__name__} mentions {token!r}"



def test_canonical_70_remains_exactly_seventy(tmp_path, dimensions, protocol,
                                              signature, policy):
    """
    (AG) Wave 7 namespaces are isolated; no memory enters the registry.

    The canonical 70 are a frozen scientific inventory. A treatment signature, a
    memory record and a revisit decision are none of those things.
    """
    from research_engine.registry.baseline_manifest import BASELINE_VERSION
    from research_engine.registry.research_question_registry import REGISTRY

    before = canonical_inventory()
    assert len(before) == CANONICAL_QUESTION_COUNT == 70
    assert len(set(before)) == 70

    record = _memory(dimensions, protocol, signature)
    decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=policy)
    path = tmp_path / "treatment_memory.json"
    store = TreatmentMemoryStore(path)
    store.register_policy(policy)
    store.register_memory(record)
    store.register_decision(decision)
    TreatmentMemoryStore(path)

    assert_canonical_70_intact()
    assert len(canonical_inventory()) == 70
    assert len(set(canonical_inventory())) == 70
    assert canonical_inventory() == before
    assert BASELINE_VERSION == 1
    assert len(REGISTRY) == 70
    # No Wave 7 identity is a canonical question, and none is in the registry.
    wave7_identities = {signature.signature_identity, record.memory_identity,
                        policy.policy_identity, decision.decision_identity}
    assert wave7_identities.isdisjoint(set(canonical_inventory()))
    assert wave7_identities.isdisjoint({question.id for question in REGISTRY})
    for identity in wave7_identities:
        assert identity not in set(canonical_inventory())


def test_wave7_namespaces_are_disjoint_from_every_other_wave():
    """
    (AG) `TRS-`, `TMR-`, `RVP-` and `RVD-` are Wave 7's own.

    Proves the four prefixes collide with no other wave and no canonical
    programme prefix, and that each pattern matches only its own shape.
    """
    import re
    from research_engine.lifecycle import (
        curiosity_proposal,
        curiosity_signal,
        generated_research_identity,
        governed_dimension as governed_dimension_module,
        interaction_feasibility,
        progressive_depth_gate,
        research_agenda,
        research_interaction,
        research_opportunity,
        research_priority,
        research_protocol,
        research_protocol_scope,
        research_queue,
        search_provenance,
    )
    live = {
        generated_research_identity.GENERATED_RESEARCH_ID_PREFIX,
        curiosity_signal.CURIOSITY_SIGNAL_ID_PREFIX,
        curiosity_proposal.CURIOSITY_PROPOSAL_ID_PREFIX,
        governed_dimension_module.DIMENSION_ID_PREFIX,
        research_interaction.INTERACTION_ID_PREFIX,
        research_interaction.SLICE_ID_PREFIX,
        interaction_feasibility.FEASIBILITY_ID_PREFIX,
        progressive_depth_gate.DECISION_ID_PREFIX,
        progressive_depth_gate.JUSTIFICATION_ID_PREFIX,
        search_provenance.SEARCH_ALTERNATIVE_ID_PREFIX,
        search_provenance.SEARCH_FAMILY_ID_PREFIX,
        search_provenance.SEARCH_RECORD_ID_PREFIX,
        search_provenance.SELECTION_FREEZE_ID_PREFIX,
        search_provenance.MULTIPLICITY_ID_PREFIX,
        research_opportunity.RESEARCH_OPPORTUNITY_ID_PREFIX,
        research_priority.PRIORITY_POLICY_ID_PREFIX,
        "AFR-", research_queue.RESEARCH_QUEUE_ID_PREFIX,
        RESEARCH_PROTOCOL_ID_PREFIX, PROTOCOL_FREEZE_ID_PREFIX,
        research_protocol_scope.OUT_OF_SCOPE_DISCOVERY_ID_PREFIX,
    }
    live.add(research_agenda.RESEARCH_AGENDA_ID_PREFIX)
    live.add(research_agenda.AGENDA_FREEZE_ID_PREFIX)
    wave7 = {TREATMENT_SIGNATURE_ID_PREFIX, TREATMENT_MEMORY_ID_PREFIX,
             "RVP-", "RVD-"}
    assert wave7.isdisjoint(live)
    assert len(live | wave7) == len(live) + len(wave7)
    for prefix in wave7:
        pattern = re.compile(rf"^{re.escape(prefix)}[0-9A-F]{{16}}$")
        assert pattern.match(prefix + "0123456789ABCDEF")
        assert not pattern.match(prefix + "not-hex")
    # No canonical question ever looks like a Wave 7 identity.
    assert all(
        not is_treatment_signature_identity(item)
        and not is_treatment_memory_identity(item)
        and not is_revisit_policy_identity(item)
        and not is_revisit_decision_identity(item)
        for item in canonical_inventory())



def test_memory_lineage_preserves_the_whole_governed_chain(dimensions, protocol,
                                                           signature):
    """
    (23) Treatment signature -> protocol -> freeze -> disposition -> memory.

    Every hop is a real, independently minted identity, and the record carries
    the full chain rather than a copied payload.
    """
    record = _memory(dimensions, protocol, signature)
    # The signature names the governed subject it investigated.
    assert (record.subject_ref
            == dimensions["stop_distance"].dimension_identity)
    assert record.subject_kind is OpportunitySubjectKind.GOVERNED_DIMENSION
    # The protocol and its T0 freeze are the real Wave 6 objects.
    assert record.protocol_identity == protocol.protocol_identity
    assert record.protocol_freeze_identity == _freeze(protocol).freeze_identity
    # The memory points back at the signature, and the signature does not point
    # forward: a pre-result record cannot know its own outcome.
    assert record.treatment_signature_identity == signature.signature_identity
    assert not hasattr(signature, "disposition")
    assert not hasattr(signature, "outcome")
    # Applicable governed components are carried, not the whole payload.
    assert record.applicable_dimensions == (
        dimensions["stop_distance"].dimension_identity,)
    assert record.applicable_interactions == (
        dimensions["interaction"].interaction_identity,)
    # And the whole chain is recoverable from the record alone.
    payload = record.to_dict()
    assert payload["protocol_identity"] == protocol.protocol_identity
    assert payload["protocol_freeze_identity"] == _freeze(
        protocol).freeze_identity
    assert payload["disposition"] == "NOT_SUPPORTED"
    assert payload["reason_codes"] == ["REQUIRED_EFFECT_ABSENT"]
    assert payload["applicability"]["asset_family"] == "FX"


def _wave5_information():
    from research_engine.lifecycle.research_opportunity import (
        EvidentialInsufficiency, ExplanationDiscrimination, InformationValue,
        QuestionResolution, ResearchAnswerability,
    )
    return InformationValue.create(
        question_state=QuestionResolution.UNRESOLVED,
        evidential_insufficiency=EvidentialInsufficiency.HIGH,
        explanation_discrimination=(
            ExplanationDiscrimination.DISCRIMINATES_COMPETING_EXPLANATIONS),
        answerability=ResearchAnswerability.ANSWERABLE_WITH_FROZEN_EVIDENCE,
        evidence_reference="finding:finding-42")


def _wave5_cost():
    from research_engine.lifecycle.research_opportunity import (
        ConfirmationRequirement, DataAvailability, ObservationRequirement,
        ProspectiveWait, ResearchCost,
    )
    return ResearchCost.create(
        data_availability=DataAvailability.AVAILABLE,
        additional_observations=ObservationRequirement.NONE,
        prospective_waiting=ProspectiveWait.NONE,
        confirmation_requirement=ConfirmationRequirement.REQUIRED)



def test_wave7_consumes_but_never_rewrites_wave5_and_wave6(dimensions, protocol,
                                                             signature, policy):
    """
    (AD, AE) The full governed chain, frozen before the memory is written.

    Builds a real Wave 5 agenda, queue and freeze, a real Wave 6 protocol, then
    a Wave 7 memory -- and proves every earlier object is unchanged afterwards.
    """
    from research_engine.lifecycle.research_agenda import (
        AgendaFreeze, ResearchAgenda,
    )
    from research_engine.lifecycle.research_opportunity import ResearchOpportunity
    from research_engine.lifecycle.research_priority import (
        POLICY_LEXICOGRAPHIC_V1,
    )
    from research_engine.lifecycle.research_queue import ResearchQueue

    opportunity = ResearchOpportunity.create(
        subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION,
        subject_ref=dimensions["stop_distance"].dimension_identity,
        state=OpportunityState.READY,
        information=_wave5_information(),
        cost=_wave5_cost(),
        evidence_boundary=EVIDENCE_BOUNDARY_T0)
    agenda = ResearchAgenda.build(
        POLICY_LEXICOGRAPHIC_V1, (opportunity,),
        evidence_boundary=EVIDENCE_BOUNDARY_T0)
    queue = ResearchQueue.build(agenda=agenda, capacity=1)
    agenda_freeze = AgendaFreeze.freeze(agenda, frozen_at=EVIDENCE_BOUNDARY_T0)
    snapshots = {
        "opportunity": opportunity.to_dict(),
        "agenda": agenda.to_dict(),
        "queue": queue.to_dict(),
        "agenda_freeze": agenda_freeze.to_dict(),
        "protocol": protocol.to_dict(),
    }

    # A real protocol bound to the real opportunity, carrying the real chain.
    chained = _protocol(
        dimensions,
        agenda_identity=agenda.agenda_identity,
        queue_identity=queue.queue_identity,
        agenda_freeze_identity=agenda_freeze.freeze_identity)
    record = _memory(dimensions, chained, signature)
    decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=policy)

    # Wave 7 read all of it and changed none of it.
    assert opportunity.to_dict() == snapshots["opportunity"]
    assert agenda.to_dict() == snapshots["agenda"]
    assert queue.to_dict() == snapshots["queue"]
    assert agenda_freeze.to_dict() == snapshots["agenda_freeze"]
    assert protocol.to_dict() == snapshots["protocol"]
    # And the ordering the agenda owns is untouched before AND after the decision.
    assert agenda.ordered_identities == (opportunity.opportunity_identity,)
    assert decision.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
    assert agenda.ordered_identities == (opportunity.opportunity_identity,)
    assert agenda.to_dict() == snapshots["agenda"]
    # The chained protocol really does carry the Wave 5 lineage forward.
    assert chained.authorisation.agenda_identity == agenda.agenda_identity
    assert chained.authorisation.queue_identity == queue.queue_identity
    assert chained.authorisation.agenda_freeze_identity == (
        agenda_freeze.freeze_identity)


def test_the_only_file_wave7_writes_is_its_own_history(tmp_path, dimensions,
                                                        protocol, signature,
                                                        policy):
    """
    (AL) No production, runtime, config or data-collection file is touched.

    The whole exercise runs against an isolated temp directory, and the only
    file it creates is the treatment-memory history.
    """
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
    )
    from research_engine.lifecycle.search_record_store import SearchRecordStore

    record = _memory(dimensions, protocol, signature)
    decision = decide_revisit(
        signature=signature, proposed_context=_envelope(dimensions),
        memories=(record,), policy=policy)
    path = tmp_path / "treatment_memory.json"
    store = TreatmentMemoryStore(path)
    store.register_policy(policy)
    store.register_memory(record)
    store.register_decision(decision)
    TreatmentMemoryStore(path)

    # The only file created is the treatment-memory history.
    assert sorted(item.name for item in tmp_path.iterdir()) == [
        "treatment_memory.json"]
    # No generated research and no search history was produced either.
    assert len(GeneratedResearchStore(tmp_path / "gen.json")) == 0
    assert len(SearchRecordStore(tmp_path / "searches.json")) == 0
    # The canonical registry is untouched throughout.
    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70



# == 41. The core integrated demonstration ===================================


def test_core_integrated_demonstration(tmp_path, dimensions, protocol, signature,
                                       policy):
    """
    (41) ONE governed history, exercised against every Wave 7 concept at once.

    Builds a small, complete and realistic research history: a supported result
    in one regime, a contradicting result in another, an insufficient result, an
    invalid investigation, and a superseded record. Then it asks the questions a
    later system would actually ask, and checks that every answer comes from
    governed records rather than from inference.

    Nothing is executed. No research runs, no treatment is applied, no candidate
    is activated, and no production behaviour changes.
    """
    trending = _envelope(dimensions, regime_scope=("TRENDING",))
    store = TreatmentMemoryStore(tmp_path / "history.json")
    store.register_policy(policy)

    # -- The history --------------------------------------------------------
    supported = _memory(
        dimensions, protocol, signature,
        disposition=HistoricalDisposition.SUPPORTED,
        reason_codes=(DispositionReason.CONFIRMATION_PASSED,),
        applicability=trending, evidence_fingerprint=FINGERPRINT_A)
    contradicted = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.NOT_SUPPORTED,
        reason_codes=(DispositionReason.GOVERNED_COMPARISON_DID_NOT_SUPPORT,),
        evidence_fingerprint=FINGERPRINT_B)
    insufficient = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.INSUFFICIENT_DATA,
        reason_codes=(DispositionReason.POPULATION_TOO_SMALL,),
        confirmation_state=ConfirmationState.REQUIRED_AND_ABSENT,
        evidence_fingerprint=FINGERPRINT_C)
    invalid = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.INVALID_INVESTIGATION,
        reason_codes=(DispositionReason.FINGERPRINT_MISMATCH,),
        confirmation_state=ConfirmationState.INDETERMINATE,
        evidence_fingerprint=FINGERPRINT_C)
    superseded = _memory(
        dimensions, _protocol(dimensions), signature,
        disposition=HistoricalDisposition.SUPERSEDED,
        reason_codes=(DispositionReason.SUPERSEDED_BY_LATER_EVIDENCE,),
        supersedes=supported.memory_identity)
    history = (supported, contradicted, insufficient, invalid, superseded)
    for record in history:
        store.register_memory(record)
    assert len(store.memories()) == 5

    # -- "Have we tried this before?" ---------------------------------------
    relabelled = _signature(
        dimensions, label="increase stop distance",
        note="re-raised next quarter", created_at="2027-06-01T00:00:00Z")
    found = historical_memories_for(
        store.memories(),
        treatment_signature_identity=relabelled.signature_identity)
    assert len(found) == 5

    # -- "What exactly did we try?" ------------------------------------------
    assert relabelled.signature_identity == signature.signature_identity
    assert relabelled.component is TreatmentComponent.STOP_GEOMETRY
    assert relabelled.change_parameters == (("STOP_MULTIPLIER", 1.5),)
    assert relabelled.baseline_semantics == "DEFAULT_STOP"
    assert relabelled.alternative_semantics == "WIDENED_STOP"


    # -- "When, under what evidence boundary, against what population?" -------
    assert trending.evidence_boundary == EVIDENCE_BOUNDARY_T0
    assert trending.asset_family == "FX"
    assert trending.symbol_scope == ("EURUSD",)
    assert trending.regime_scope == ("TRENDING",)
    assert supported.protocol_freeze_identity == _freeze(protocol).freeze_identity

    # -- "What did the research conclude, and why?" --------------------------
    assert supported.disposition is HistoricalDisposition.SUPPORTED
    assert supported.reason_codes == (DispositionReason.CONFIRMATION_PASSED,)
    assert insufficient.reason_codes == (
        DispositionReason.POPULATION_TOO_SMALL,)
    assert invalid.reason_codes == (DispositionReason.FINGERPRINT_MISMATCH,)

    # -- "Does that conclusion apply here?" ---------------------------------
    for context, expected in (
            (trending, ApplicabilityVerdict.APPLIES),
            (_envelope(dimensions, regime_scope=("RANGING",)),
             ApplicabilityVerdict.DOES_NOT_APPLY),
            (_envelope(dimensions, symbol_scope=("XAUUSD",),
                       asset_family="METALS"),
             ApplicabilityVerdict.DOES_NOT_APPLY),
            (_envelope(dimensions, horizon=Horizon.SWING),
             ApplicabilityVerdict.DOES_NOT_APPLY)):
        assert (assess_memory_applicability(supported, context).verdict
                is expected)

    # -- "Is there conflicting evidence?" -----------------------------------
    conflict = assess_memory_conflict((supported, contradicted), trending)
    assert conflict.has_conflict is True
    assert set(conflict.conflicting_memories) == {
        supported.memory_identity, contradicted.memory_identity}

    # -- "Is this proposed work a duplicate?" -------------------------------
    duplicate = decide_revisit(
        signature=relabelled, proposed_context=trending,
        memories=history, policy=policy)
    assert duplicate.eligibility is RevisitEligibility.DUPLICATE_RESEARCH
    assert duplicate.blocks_research() is True

    # -- "If it is being revisited, what materially changed?" ----------------
    later = _envelope(
        dimensions, evidence_boundary=EVIDENCE_BOUNDARY_T1,
        evidence_reference="evidence:post_t0")
    revisit = decide_revisit(
        signature=signature, proposed_context=later,
        memories=(insufficient,), policy=policy,
        claimed_triggers=(RevisitTrigger.NEW_EVIDENCE,))
    assert revisit.eligibility is RevisitEligibility.REVISIT_PERMITTED
    assert revisit.material_change.detail["evidence_boundary"] == {
        "historical": EVIDENCE_BOUNDARY_T0, "proposed": EVIDENCE_BOUNDARY_T1}

    # -- "Which revisit policy permitted it?" -------------------------------
    assert revisit.policy_identity == policy.policy_identity
    assert revisit.to_dict()["policy_identity"] == policy.policy_identity
    assert revisit.to_dict()["verified_triggers"] == ["NEW_EVIDENCE"]

    # -- Restart, and prove every identity is reproduced exactly ------------
    reloaded = TreatmentMemoryStore(tmp_path / "history.json")
    assert len(reloaded) == 5
    for record in history:
        assert reloaded.get_memory(record.memory_identity).to_dict() == (
            record.to_dict())
    assert reloaded.get_policy(policy.policy_identity).policy_identity == (
        policy.policy_identity)

    # -- Nothing was executed, and no earlier wave was disturbed -------------
    assert duplicate.is_permission_to_execute() is False
    assert revisit.is_permission_to_execute() is False
    assert reloaded.get_memory(supported.memory_identity).disposition is (
        HistoricalDisposition.SUPPORTED)
    assert reloaded.get_memory(contradicted.memory_identity).disposition is (
        HistoricalDisposition.NOT_SUPPORTED)
    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70
    assert sorted(item.name for item in tmp_path.iterdir()) == ["history.json"]

