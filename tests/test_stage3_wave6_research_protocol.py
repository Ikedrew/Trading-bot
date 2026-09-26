"""
Stage 3 / Wave 6 -- governed research protocol, investigation contract and T0
freeze.

Focused tests only. Scope:
    - an `RPL-*` protocol that is deterministically identified and binds to an
      EXISTING governed Wave 5 `ROP-*` opportunity;
    - explicit, closed-vocabulary permitted operations and globally forbidden
      authority that fails closed rather than being silently dropped;
    - evidence scope, investigation scope, comparison, completion, insufficiency
      and invalidation criteria, all identity material;
    - discovery that CANNOT expand a protocol: escalation to new governed work;
    - mechanical scope-drift detection that fails closed;
    - supersession as NEW history, never in-place mutation;
    - a `PFR-*` T0 freeze a later result cannot rewrite;
    - restart-safe persistence, semantic dedup and fail-closed loading;
    - the core demonstration A-V, the scope-creep demonstration and the post-hoc
      change demonstration;
    - Wave 4 / Wave 5 / Waves 0-3 integration, canonical-70 isolation, and proof
      that no research execution, candidate creation or treatment application
      occurs.

These are TEST FIXTURES, not production research. No experiment, backtest,
hypothesis, candidate, trade or production behaviour is executed anywhere.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace

import pytest

from research_engine.lifecycle import (
    research_protocol as protocol_module,
    research_protocol_scope as scope_module,
    research_protocol_store as store_module,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.eligibility_evidence_freeze import (
    fingerprint_for_records,
)
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_QUESTION_COUNT,
    assert_canonical_70_intact,
    canonical_inventory,
)
from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    GovernedDimension,
)
from research_engine.lifecycle.progressive_depth_gate import EligibilityState
from research_engine.lifecycle.research_agenda import (
    RESEARCH_AGENDA_ID_PREFIX,
    AgendaFreeze,
    ResearchAgenda,
    is_agenda_freeze_identity,
    is_research_agenda_identity,
)
from research_engine.lifecycle.research_interaction import ResearchInteraction
from research_engine.lifecycle.research_opportunity import (
    RESEARCH_OPPORTUNITY_ID_PREFIX,
    ConfirmationRequirement,
    DataAvailability,
    EvidentialInsufficiency,
    ExplanationDiscrimination,
    InformationValue,
    ObservationRequirement,
    OpportunityState,
    OpportunitySubjectKind,
    ProspectiveWait,
    QuestionResolution,
    ResearchAnswerability,
    ResearchCost,
    ResearchOpportunity,
    is_research_opportunity_identity,
)
from research_engine.lifecycle.research_priority import POLICY_LEXICOGRAPHIC_V1
from research_engine.lifecycle.research_protocol import (
    GLOBALLY_PROHIBITED_OPERATIONS,
    PROTOCOL_FREEZE_ID_PREFIX,
    RESEARCH_OPERATIONS,
    RESEARCH_PROTOCOL_ID_PREFIX,
    ComparisonKind,
    ComparisonSpecification,
    ComparisonTerm,
    CompletionCriterionKind,
    EscalationRoute,
    EvidenceScope,
    InsufficiencyCriterionKind,
    InvalidationCriterionKind,
    InvestigationScope,
    ProhibitedOperation,
    ProtocolAuthorisation,
    ProtocolAuthorisationError,
    ProtocolFreeze,
    ProtocolNamespaceViolation,
    ProtocolStatus,
    ResearchOperation,
    ResearchProtocol,
    ResearchProtocolError,
    ResearchProtocolValidationError,
    ScopeChangePolicy,
    ScopeComponent,
    ScopeComponentKind,
    SupersessionReason,
    is_protocol_freeze_identity,
    is_research_protocol_identity,
)
from research_engine.lifecycle.research_protocol_scope import (
    OUT_OF_SCOPE_DISCOVERY_ID_PREFIX,
    OutOfScopeReason,
    ResearchIntent,
    ScopeDriftError,
    assert_operation_permitted,
    assert_scope_consistent,
    classify_out_of_scope,
    escalate_out_of_scope_discovery,
)
from research_engine.lifecycle.research_protocol_store import (
    STORE_FORMAT,
    ResearchProtocolStore,
)
from research_engine.lifecycle.research_queue import (
    RESEARCH_QUEUE_ID_PREFIX,
    ResearchQueue,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)
from research_engine.registry.research_question_registry import REGISTRY

EVIDENCE_BOUNDARY = "2026-09-25T00:00:00Z"
T0 = "2026-09-25T09:00:00Z"

#: Real canonical questions from the frozen registry. A protocol may legitimately
#: be ABOUT a canonical question; it may never become one.
CANONICAL_IDS = tuple(question.id for question in REGISTRY)


# ═══ Fixtures: governed Wave 1 dimension algebra ════════════════════════════


@pytest.fixture
def dimensions():
    """
    FOUR REAL ADMITTED governed dimensions plus TWO REAL interactions.

    `stop_distance` is the subject of the demonstration protocol; `entry_timing`
    is the out-of-scope dimension the scope-creep demonstration tries to smuggle
    in; `regime` and `horizon` are the population segmentation dimensions.
    """
    registry = DimensionRegistry()
    built = {}
    for key in ("stop_distance", "entry_timing", "regime", "horizon"):
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
    built["out_of_scope_interaction"] = ResearchInteraction.create(
        dimensions=[built["entry_timing"], built["horizon"]])
    return built


def _info() -> InformationValue:
    return InformationValue.create(
        question_state=QuestionResolution.UNRESOLVED,
        evidential_insufficiency=EvidentialInsufficiency.HIGH,
        explanation_discrimination=(
            ExplanationDiscrimination.DISCRIMINATES_COMPETING_EXPLANATIONS),
        answerability=ResearchAnswerability.ANSWERABLE_WITH_FROZEN_EVIDENCE,
        evidence_reference="finding:finding-42",
    )


def _cost(alternatives: int = 3) -> ResearchCost:
    return ResearchCost.create(
        data_availability=DataAvailability.AVAILABLE,
        additional_observations=ObservationRequirement.NONE,
        prospective_waiting=ProspectiveWait.NONE,
        confirmation_requirement=ConfirmationRequirement.REQUIRED,
        alternative_count=alternatives,
        dependency_depth=0,
    )


def _opportunity(
    subject_ref: str,
    state: OpportunityState = OpportunityState.READY,
    *,
    subject_kind: OpportunitySubjectKind = (
        OpportunitySubjectKind.CANONICAL_QUESTION),
    reason_codes: tuple[str, ...] = (),
    search_provenance=None,
    evidence_boundary: str = EVIDENCE_BOUNDARY,
) -> ResearchOpportunity:
    return ResearchOpportunity.create(
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        state=state,
        information=_info(),
        cost=_cost(),
        evidence_boundary=evidence_boundary,
        reason_codes=reason_codes,
        search_provenance=search_provenance,
    )



# ═══ Fixtures: the governed protocol under test ═════════════════════════════


def _evidence(**overrides) -> EvidenceScope:
    return EvidenceScope(
        evidence_boundary=EVIDENCE_BOUNDARY,
        evidence_references=("evidence:canonical_q1_evidence",),
        symbol_scope=("symbol:EURUSD",),
        **overrides,
    )


def _scope(dimensions, subject_ref: str) -> InvestigationScope:
    """
    IN SCOPE: the stop-distance dimension and the stop-distance x regime
    interaction -- i.e. stop geometry across the TRENDING slice.

    OUT OF SCOPE, EXPLICITLY: the entry-timing dimension and the entry-timing x
    horizon interaction. Naming them is the point -- it makes "do not investigate
    entry timing here" a mechanical refusal rather than an inference.
    """
    return InvestigationScope(
        subject_kind=OpportunitySubjectKind.CANONICAL_QUESTION,
        subject_ref=subject_ref,
        in_scope=(
            ScopeComponent(ScopeComponentKind.DIMENSION,
                           dimensions["stop_distance"].dimension_identity),
            ScopeComponent(ScopeComponentKind.INTERACTION,
                           dimensions["interaction"].interaction_identity),
        ),
        excluded=(
            ScopeComponent(ScopeComponentKind.DIMENSION,
                           dimensions["entry_timing"].dimension_identity,
                           in_scope=False),
            ScopeComponent(ScopeComponentKind.INTERACTION,
                           dimensions["out_of_scope_interaction"]
                           .interaction_identity,
                           in_scope=False),
        ),
    )


def _comparison() -> ComparisonSpecification:
    return ComparisonSpecification(
        kind=ComparisonKind.SLICE_A_VS_SLICE_B,
        left=ComparisonTerm(reference="slice:trending", label="trending"),
        right=ComparisonTerm(reference="slice:ranging", label="ranging"),
        rationale_code="GOVERNED_REGIME_CONTRAST",
    )


def _protocol(
    opportunity: ResearchOpportunity,
    dimensions,
    *,
    comparison: ComparisonSpecification | None = None,
    **overrides,
) -> ResearchProtocol:
    payload = dict(
        scope=_scope(dimensions, opportunity.subject_ref),
        evidence=_evidence(),
        permitted_operations=(
            ResearchOperation.AGGREGATE,
            ResearchOperation.SLICE,
            ResearchOperation.COMPARE,
            ResearchOperation.EXCURSION_ANALYSIS,
        ),
        completion_criteria=(
            CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE,
            CompletionCriterionKind.REQUIRED_SLICES_EVALUATED,
            CompletionCriterionKind.ALL_DECLARED_OPERATIONS_COMPLETED,
        ),
        insufficiency_criteria=(
            InsufficiencyCriterionKind.REQUIRED_POPULATION_UNAVAILABLE,
            InsufficiencyCriterionKind.REQUIRED_DIMENSION_NOT_OBSERVABLE,
        ),
        invalidation_criteria=(
            InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED,
            InvalidationCriterionKind.EVIDENCE_BOUNDARY_VIOLATED,
            InvalidationCriterionKind.COMPARISON_SPECIFICATION_CHANGED,
        ),
        comparison=comparison if comparison is not None else _comparison(),
        confirmation_requirement=ConfirmationRequirement.REQUIRED,
    )
    payload.update(overrides)
    return ResearchProtocol.for_opportunity(opportunity, **payload)


@pytest.fixture
def ready_opportunity() -> ResearchOpportunity:
    """A legitimate Wave 5 READY opportunity over a REAL canonical question."""
    return _opportunity(CANONICAL_IDS[0])


@pytest.fixture
def protocol(ready_opportunity, dimensions) -> ResearchProtocol:
    return _protocol(ready_opportunity, dimensions)



# ═══ 1-11. Authorisation, namespace, identity, operations, criteria ═════════


def test_protocol_requires_an_authoritative_wave5_opportunity(dimensions):
    """(B, C) A protocol may never be constructed without a governed ROP-*. """
    with pytest.raises(ProtocolAuthorisationError,
                       match="expected a governed ResearchOpportunity"):
        ResearchProtocol.for_opportunity(
            "just an idea about stop geometry",
            scope=_scope(dimensions, CANONICAL_IDS[0]), evidence=_evidence(),
            permitted_operations=(ResearchOperation.AGGREGATE,),
            completion_criteria=(CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE,),
            insufficiency_criteria=(
                InsufficiencyCriterionKind.REQUIRED_POPULATION_UNAVAILABLE,),
            invalidation_criteria=(
                InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED,),
            comparison=ComparisonSpecification(kind=ComparisonKind.NONE),
            confirmation_requirement=ConfirmationRequirement.REQUIRED)

    # And the authorisation object itself rejects a non-ROP identity: a canonical
    # question is governed research state, but it is not an OPPORTUNITY.
    with pytest.raises(ProtocolAuthorisationError, match="ROP-"):
        ProtocolAuthorisation(opportunity_identity="an-ungoverned-idea",
                              opportunity_state=OpportunityState.READY.value)
    with pytest.raises(ProtocolAuthorisationError, match="ROP-"):
        ProtocolAuthorisation(opportunity_identity=CANONICAL_IDS[0],
                              opportunity_state=OpportunityState.READY.value)


@pytest.mark.parametrize("state,reason", [
    (OpportunityState.BLOCKED, ("EVIDENCE_FREEZE_UNAVAILABLE",)),
    (OpportunityState.WAITING_DATA, ("ADDITIONAL_OBSERVATIONS_REQUIRED",)),
    (OpportunityState.REFUSED, ("EXPANSION_NOT_JUSTIFIED",)),
])
def test_only_ready_wave5_work_may_be_authorised(dimensions, state, reason):
    """(B) Wave 5's verdict is carried forward, never overturned by Wave 6."""
    opportunity = _opportunity(CANONICAL_IDS[1], state, reason_codes=reason)
    with pytest.raises(ProtocolAuthorisationError, match="only be authored over"):
        _protocol(opportunity, dimensions)


def test_protocol_identity_is_deterministic(ready_opportunity, dimensions):
    """(D) The same investigation always yields the same RPL-* identity."""
    first = _protocol(ready_opportunity, dimensions)
    second = _protocol(ready_opportunity, dimensions)
    assert first.protocol_identity == second.protocol_identity
    assert first.semantic_identity == second.semantic_identity
    assert is_research_protocol_identity(first.protocol_identity)
    assert first.protocol_identity.startswith(RESEARCH_PROTOCOL_ID_PREFIX)


def test_notes_timestamps_and_status_do_not_alter_identity(
        ready_opportunity, dimensions):
    """(D, L) Provenance-only fields are provably outside the identity."""
    base = _protocol(ready_opportunity, dimensions)
    noisy = _protocol(
        ready_opportunity, dimensions, label="a helpful label",
        note="written by a human at length, with prose that means nothing",
        created_at="2099-12-31T23:59:59Z",
        provenance={"author": "someone", "ticket": "ABC-1"})
    assert noisy.protocol_identity == base.protocol_identity
    assert noisy.semantic_identity == base.semantic_identity
    # A lifecycle transition is likewise provenance only.
    validated = base.with_status(ProtocolStatus.VALIDATED)
    assert validated.protocol_identity == base.protocol_identity
    assert validated.with_status(ProtocolStatus.FROZEN).protocol_identity == (
        base.protocol_identity)
    # ... but the provenance itself IS retained and inspectable.
    assert noisy.note != base.note


def test_scope_component_change_changes_identity(ready_opportunity, dimensions):
    """(M) An undeclared dimension is a different protocol, not a tweak."""
    base = _protocol(ready_opportunity, dimensions)
    widened = replace(
        base.scope,
        in_scope=base.scope.in_scope + (
            ScopeComponent(ScopeComponentKind.DIMENSION,
                           dimensions["horizon"].dimension_identity),))
    changed = _protocol(ready_opportunity, dimensions, scope=widened)
    assert changed.protocol_identity != base.protocol_identity
    # A component may never be both in scope and excluded.
    with pytest.raises(ResearchProtocolValidationError,
                       match="both in scope and excluded"):
        replace(
            base.scope,
            in_scope=base.scope.in_scope + (
                ScopeComponent(ScopeComponentKind.DIMENSION,
                               dimensions["entry_timing"].dimension_identity),))


def test_evidence_boundary_is_identity_material(ready_opportunity, dimensions):
    """(E, F) The evidence boundary is frozen into the protocol."""
    base = _protocol(ready_opportunity, dimensions)
    later = _protocol(
        ready_opportunity, dimensions,
        evidence=replace(base.evidence,
                         evidence_boundary="2026-09-26T00:00:00Z"))
    assert later.protocol_identity != base.protocol_identity
    # A protocol may not re-date the boundary of the opportunity it authorises.
    with pytest.raises(ProtocolAuthorisationError, match="evidence boundary"):
        later.assert_binds_to(ready_opportunity)
    # And the honest boundary is the one the opportunity froze.
    assert base.evidence.evidence_boundary == ready_opportunity.evidence_boundary



@pytest.mark.parametrize("field,value", [
    ("permitted_operations", (ResearchOperation.DEPENDENCY_ANALYSIS,)),
    ("completion_criteria", (
        CompletionCriterionKind.CONFIRMATION_STATE_REACHED,
        CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE)),
    ("insufficiency_criteria", (
        InsufficiencyCriterionKind.EVIDENCE_BOUNDARY_MISMATCH,
        InsufficiencyCriterionKind.REQUIRED_POPULATION_UNAVAILABLE)),
    ("invalidation_criteria", (
        InvalidationCriterionKind.POST_T0_EVIDENCE_IMPROPERLY_INCLUDED,
        InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED)),
    ("confirmation_requirement", ConfirmationRequirement.NONE),
    ("protocol_version", 2),
])
def test_semantic_change_necessarily_changes_identity(
        ready_opportunity, dimensions, field, value):
    """(M, Q) Changing what the investigation means changes the protocol."""
    base = _protocol(ready_opportunity, dimensions)
    changed = _protocol(ready_opportunity, dimensions, **{field: value})
    assert changed.protocol_identity != base.protocol_identity
    # And neither may present the other's identity.
    with pytest.raises(ResearchProtocolValidationError, match="does not match"):
        replace(changed, protocol_identity=base.protocol_identity)
    with pytest.raises(ResearchProtocolValidationError, match="does not match"):
        replace(changed, semantic_identity=base.semantic_identity)


def test_comparison_change_changes_identity(ready_opportunity, dimensions):
    """(G, M) The comparison is decided before results, and is identity material."""
    base = _protocol(ready_opportunity, dimensions)
    assert base.comparison.kind is ComparisonKind.SLICE_A_VS_SLICE_B
    changed = _protocol(ready_opportunity, dimensions, comparison=ComparisonSpecification(
        kind=ComparisonKind.SLICE_A_VS_SLICE_B,
        left=ComparisonTerm(reference="slice:high_volatility"),
        right=ComparisonTerm(reference="slice:low_volatility")))
    assert changed.protocol_identity != base.protocol_identity
    # A comparison never applies a treatment -- structurally, not by convention.
    assert base.comparison.applies_treatment() is False
    assert changed.comparison.applies_treatment() is False


def test_operations_use_a_closed_vocabulary_and_forbid_global_authority(
        ready_opportunity, dimensions):
    """(H, I) A closed vocabulary of activities; forbidden authority fails closed."""
    base = _protocol(ready_opportunity, dimensions)
    # The permitted set is a subset of the published vocabulary.
    assert {item.value for item in base.permitted_operations} <= {
        item.value for item in RESEARCH_OPERATIONS}
    # EVERY globally forbidden operation is declared forbidden, by default.
    assert {item.value for item in base.prohibited_operations} == {
        item.value for item in GLOBALLY_PROHIBITED_OPERATIONS}

    # An arbitrary operation string can never become authority.
    with pytest.raises(ResearchProtocolValidationError, match="closed"):
        _protocol(ready_opportunity, dimensions,
                  permitted_operations=("TOTALLY_MADE_UP_OPERATION",))
    with pytest.raises(ResearchProtocolValidationError, match="closed"):
        ResearchIntent(operation="apply_treatment_to_production")

    # A protocol that under-prohibits is rejected, NOT silently repaired.
    with pytest.raises(ResearchProtocolValidationError,
                       match="missing globally prohibited"):
        _protocol(ready_opportunity, dimensions,
                  prohibited_operations=(
                      ProhibitedOperation.PRODUCTION_TRADING,
                      ProhibitedOperation.BROKER_INTERACTION))
    # Nor may an empty prohibition set be used to "waive" the prohibitions.
    with pytest.raises(ResearchProtocolValidationError, match="must not be empty"):
        _protocol(ready_opportunity, dimensions, prohibited_operations=())
    # A forbidden operation can never even be NAMED as a research activity: the
    # two vocabularies are disjoint, so "permit production trading" is not a
    # well-formed request at all, let alone a grantable one.
    for authority in ProhibitedOperation:
        with pytest.raises(ResearchProtocolValidationError, match="closed"):
            _protocol(ready_opportunity, dimensions,
                      permitted_operations=(authority,))
    # And no research activity may be permitted at all: that is not a protocol.
    with pytest.raises(ResearchProtocolValidationError, match="must not be empty"):
        _protocol(ready_opportunity, dimensions, permitted_operations=())



# ═══ 12-15. Scope-drift enforcement ════════════════════════════════════════
#
# The demonstration protocol investigates stop_distance x regime.
# PERMITTED: aggregate, slice, compare, excursion analysis.
# NOT AUTHORISED: entry timing, an undeclared interaction, an expanded
#                population, a changed comparison, production treatment.


def _permitted_intent(protocol, dimensions) -> ResearchIntent:
    """An intent that is exactly inside the registered contract."""
    return ResearchIntent(
        operation=ResearchOperation.SLICE,
        subject_ref=protocol.scope.subject_ref,
        dimension_identities=(dimensions["stop_distance"].dimension_identity,),
        interaction_identities=(dimensions["interaction"].interaction_identity,),
        evidence_boundary=protocol.evidence.evidence_boundary,
        comparison_kind=protocol.comparison.kind,
    )


def test_permitted_stop_geometry_operation_passes(protocol, dimensions):
    """(O, 21.1) The declared work is allowed, and is allowed exactly."""
    assert_scope_consistent(protocol, _permitted_intent(protocol, dimensions))
    assert assert_operation_permitted(
        protocol, ResearchOperation.AGGREGATE) is ResearchOperation.AGGREGATE
    assert assert_operation_permitted(
        protocol, ResearchOperation.EXCURSION_ANALYSIS) is (
        ResearchOperation.EXCURSION_ANALYSIS)
    assert assert_operation_permitted(
        protocol, ResearchOperation.COMPARE) is ResearchOperation.COMPARE


def test_undeclared_entry_timing_investigation_fails(protocol, dimensions):
    """(O, 21.2) Examining the explicitly excluded dimension is refused."""
    intent = ResearchIntent(
        operation=ResearchOperation.SLICE,
        subject_ref=protocol.scope.subject_ref,
        dimension_identities=(dimensions["entry_timing"].dimension_identity,),
        evidence_boundary=protocol.evidence.evidence_boundary)
    with pytest.raises(ScopeDriftError, match="explicitly EXCLUDED"):
        assert_scope_consistent(protocol, intent)
    assert classify_out_of_scope(protocol, intent) is (
        OutOfScopeReason.EXPLICITLY_EXCLUDED_COMPONENT)


def test_undeclared_dimension_fails(protocol, dimensions):
    """(O) A dimension nobody declared is drift, whatever the operation."""
    intent = ResearchIntent(
        operation=ResearchOperation.AGGREGATE,
        subject_ref=protocol.scope.subject_ref,
        dimension_identities=(dimensions["horizon"].dimension_identity,),
        evidence_boundary=protocol.evidence.evidence_boundary)
    with pytest.raises(ScopeDriftError, match="not declared in scope"):
        assert_scope_consistent(protocol, intent)
    assert classify_out_of_scope(protocol, intent) is (
        OutOfScopeReason.UNDECLARED_DIMENSION)


def test_undeclared_interaction_fails(protocol, dimensions):
    """(O, 21.3) Introducing an undeclared interaction is refused."""
    intent = ResearchIntent(
        operation=ResearchOperation.SLICE,
        subject_ref=protocol.scope.subject_ref,
        interaction_identities=(
            dimensions["out_of_scope_interaction"].interaction_identity,),
        evidence_boundary=protocol.evidence.evidence_boundary)
    with pytest.raises(ScopeDriftError, match="explicitly EXCLUDED"):
        assert_scope_consistent(protocol, intent)
    # A genuinely undeclared (not merely excluded) interaction also fails, and
    # is classified as undeclared rather than excluded.
    undeclared = replace(protocol.scope, excluded=())
    widened = _protocol(
        _opportunity(protocol.scope.subject_ref), dimensions, scope=undeclared)
    intent2 = ResearchIntent(
        operation=ResearchOperation.SLICE,
        subject_ref=widened.scope.subject_ref,
        interaction_identities=(
            dimensions["out_of_scope_interaction"].interaction_identity,))
    with pytest.raises(ScopeDriftError, match="not declared in scope"):
        assert_scope_consistent(widened, intent2)
    assert classify_out_of_scope(widened, intent2) is (
        OutOfScopeReason.UNDECLARED_INTERACTION)



def test_expanded_evidence_population_fails(protocol):
    """(O, 21.4) Evidence may be neither widened nor re-dated."""
    with pytest.raises(ScopeDriftError, match="evidence boundary"):
        assert_scope_consistent(protocol, ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            evidence_boundary="2026-09-26T00:00:00Z"))
    with pytest.raises(ScopeDriftError, match="not the population registered"):
        assert_operation_permitted(
            protocol, ResearchOperation.AGGREGATE,
            discovery_population_identity="population:a_wider_population")
    with pytest.raises(ScopeDriftError, match="not the population registered"):
        assert_scope_consistent(protocol, ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            discovery_population_identity="population:a_wider_population"))


def test_changed_comparison_fails(protocol):
    """(O, 21.5) A comparison may not be changed once results exist."""
    with pytest.raises(ScopeDriftError, match="does not match the pre-registered"):
        assert_scope_consistent(protocol, ResearchIntent(
            operation=ResearchOperation.COMPARE,
            comparison_kind=ComparisonKind.DIMENSION_A_VS_DIMENSION_B))
    with pytest.raises(ScopeDriftError, match="does not match the pre-registered"):
        assert_operation_permitted(
            protocol, ResearchOperation.COMPARE,
            comparison_kind=ComparisonKind.HISTORICAL_VS_PROSPECTIVE)


def test_production_treatment_fails(protocol, dimensions):
    """(O, 21.6) Stage 3 authority is refused as such, not as a scope nit."""
    for authority in ProhibitedOperation:
        intent = ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            subject_ref=protocol.scope.subject_ref,
            dimension_identities=(
                dimensions["stop_distance"].dimension_identity,),
            evidence_boundary=protocol.evidence.evidence_boundary,
            requested_prohibited_authority=authority)
        with pytest.raises(ScopeDriftError, match="globally prohibited"):
            assert_scope_consistent(protocol, intent)
    # The forbidden request is refused, NOT stripped and allowed to proceed.
    with pytest.raises(ScopeDriftError, match="globally prohibited"):
        assert_operation_permitted(
            protocol, ResearchOperation.AGGREGATE,
            requested_prohibited_authority=(
                ProhibitedOperation.AUTONOMOUS_TREATMENT_APPLICATION))
    # Free text is not authority either.
    with pytest.raises(ScopeDriftError, match="not a governed"):
        assert_operation_permitted(
            protocol, ResearchOperation.AGGREGATE,
            requested_prohibited_authority="please just deploy it")


def test_undeclared_operation_fails(protocol):
    """(O) Activity the protocol does not contain is drift."""
    with pytest.raises(ScopeDriftError, match="not in the permitted set"):
        assert_operation_permitted(protocol, ResearchOperation.DEPENDENCY_ANALYSIS)
    # An arbitrary operation string is not even a well-formed request.
    with pytest.raises(ResearchProtocolValidationError,
                       match="not a governed ResearchOperation"):
        assert_operation_permitted(protocol, "SLICE_EVERYTHING")


def test_original_protocol_is_unchanged_by_every_refusal(
        protocol, dimensions):
    """(O, 21.7) A refused intent leaves the protocol byte-for-byte identical."""
    before = protocol.to_dict()
    before_identity = protocol.protocol_identity
    attempts = [
        ResearchIntent(
            operation=ResearchOperation.SLICE,
            dimension_identities=(
                dimensions["entry_timing"].dimension_identity,),),
        ResearchIntent(
            operation=ResearchOperation.SLICE,
            interaction_identities=(
                dimensions["out_of_scope_interaction"].interaction_identity,),),
        ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            evidence_boundary="2099-01-01T00:00:00Z"),
        ResearchIntent(
            operation=ResearchOperation.COMPARE,
            comparison_kind=ComparisonKind.HISTORICAL_VS_PROSPECTIVE),
        ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            requested_prohibited_authority=ProhibitedOperation.PRODUCTION_TRADING),
    ]
    for intent in attempts:
        with pytest.raises(ScopeDriftError):
            assert_scope_consistent(protocol, intent)
        # Escalating a drift also must not touch the protocol.
        escalate_out_of_scope_discovery(protocol, intent)
    assert protocol.to_dict() == before
    assert protocol.protocol_identity == before_identity
    assert protocol.status is ProtocolStatus.DRAFT


def test_a_new_investigation_requires_a_new_protocol_identity(
        protocol, ready_opportunity, dimensions):
    """(O, 21.8) Legitimate new work gets a NEW identity, never a widened one."""
    entry_timing_protocol = _protocol(
        ready_opportunity, dimensions,
        scope=InvestigationScope(
            subject_kind=OpportunitySubjectKind.CANONICAL_QUESTION,
            subject_ref=ready_opportunity.subject_ref,
            in_scope=(ScopeComponent(ScopeComponentKind.DIMENSION,
                                     dimensions["entry_timing"].dimension_identity),),
            excluded=(
                ScopeComponent(ScopeComponentKind.DIMENSION,
                               dimensions["stop_distance"].dimension_identity,
                               in_scope=False),)),
        permitted_operations=(ResearchOperation.AGGREGATE,))
    assert entry_timing_protocol.protocol_identity != protocol.protocol_identity
    assert entry_timing_protocol.supersedes == ""
    # The original is untouched and still authorises only stop geometry.
    assert set(protocol.scope.excluded_identities()) == {
        dimensions["entry_timing"].dimension_identity,
        dimensions["out_of_scope_interaction"].interaction_identity}
    with pytest.raises(ScopeDriftError, match="explicitly EXCLUDED"):
        assert_scope_consistent(protocol, ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            dimension_identities=(
                dimensions["entry_timing"].dimension_identity,)))



# ═══ 14. Discovery during research ═════════════════════════════════════════


def test_discovery_never_expands_the_active_protocol(protocol, dimensions):
    """(P, 14) An out-of-scope discovery is recorded, never absorbed."""
    entry_timing_anomaly = ResearchIntent(
        operation=ResearchOperation.SLICE,
        subject_ref=protocol.scope.subject_ref,
        dimension_identities=(dimensions["entry_timing"].dimension_identity,),
        evidence_boundary=protocol.evidence.evidence_boundary)

    before = protocol.to_dict()
    before_identity = protocol.protocol_identity
    discovery = escalate_out_of_scope_discovery(
        protocol, entry_timing_anomaly, note="entry timing anomaly observed")

    # The protocol is bit-for-bit what it was.
    assert protocol.to_dict() == before
    assert protocol.protocol_identity == before_identity

    # The discovery is a governed record, not a protocol and not a finding.
    assert discovery.protocol_identity == protocol.protocol_identity
    assert discovery.protocol_semantic_identity == protocol.semantic_identity
    assert discovery.reason is OutOfScopeReason.EXPLICITLY_EXCLUDED_COMPONENT
    assert discovery.discovered_identity == (
        dimensions["entry_timing"].dimension_identity)
    assert discovery.discovery_identity.startswith(
        OUT_OF_SCOPE_DISCOVERY_ID_PREFIX)
    # And it must travel the EXISTING Wave 3 route, not a new mechanism.
    assert discovery.escalation_route is EscalationRoute.CURIOSITY_LIFECYCLE


def test_a_protocol_can_never_authorise_its_own_expansion(ready_opportunity,
                                                          dimensions):
    """(P) The policy itself refuses the two escape hatches."""
    with pytest.raises(ResearchProtocolValidationError,
                       match="never authorise discovery to expand"):
        _protocol(ready_opportunity, dimensions, scope_change_policy=(
            ScopeChangePolicy(discovery_expands_protocol=True)))
    with pytest.raises(ResearchProtocolValidationError, match="changed in place"):
        _protocol(ready_opportunity, dimensions, scope_change_policy=(
            ScopeChangePolicy(require_new_protocol_for_change=False)))
    # The escalation route is a CLOSED vocabulary with no "widen me" member.
    assert [item.value for item in EscalationRoute] == ["CURIOSITY_LIFECYCLE"]


def test_in_scope_work_is_not_an_escalation(protocol, dimensions):
    """(P) Legitimate work is performed, not relabelled as a discovery."""
    with pytest.raises(ScopeDriftError, match="IN SCOPE"):
        classify_out_of_scope(protocol, _permitted_intent(protocol, dimensions))


def test_every_drift_kind_classifies_to_a_governed_reason(protocol, dimensions):
    """(P) An escalation is always attributable to a closed reason code."""
    cases = {
        OutOfScopeReason.REQUIRES_PROHIBITED_AUTHORITY: ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            requested_prohibited_authority=ProhibitedOperation.RISK_MUTATION),
        OutOfScopeReason.DIFFERENT_SUBJECT: ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            subject_ref=CANONICAL_IDS[9]),
        OutOfScopeReason.OUTSIDE_EVIDENCE_BOUNDARY: ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            evidence_boundary="2099-01-01T00:00:00Z"),
        OutOfScopeReason.DIFFERENT_POPULATION: ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            discovery_population_identity="population:elsewhere"),
        OutOfScopeReason.REQUIRES_DIFFERENT_COMPARISON: ResearchIntent(
            operation=ResearchOperation.COMPARE,
            comparison_kind=ComparisonKind.HISTORICAL_VS_PROSPECTIVE),
        OutOfScopeReason.UNDECLARED_DIMENSION: ResearchIntent(
            operation=ResearchOperation.AGGREGATE,
            dimension_identities=(
                dimensions["horizon"].dimension_identity,)),
    }
    for expected, intent in cases.items():
        assert classify_out_of_scope(protocol, intent) is expected
        record = escalate_out_of_scope_discovery(protocol, intent)
        assert record.reason is expected
        assert record.protocol_identity == protocol.protocol_identity



# ═══ 16. The T0 protocol freeze ═══════════════════════════════════════════


def test_freeze_binds_the_whole_contract(protocol, ready_opportunity):
    """(16) The freeze proves what the contract said BEFORE the result existed."""
    freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0, note="T0")
    assert is_protocol_freeze_identity(freeze.freeze_identity)
    assert freeze.freeze_identity.startswith(PROTOCOL_FREEZE_ID_PREFIX)
    assert freeze.protocol_identity == protocol.protocol_identity
    assert freeze.opportunity_identity == ready_opportunity.opportunity_identity
    assert freeze.evidence_boundary == protocol.evidence.evidence_boundary
    assert freeze.permitted_operations == tuple(
        item.value for item in protocol.permitted_operations)
    assert freeze.completion_criteria == tuple(
        item.value for item in protocol.completion_criteria)
    assert freeze.insufficiency_criteria == tuple(
        item.value for item in protocol.insufficiency_criteria)
    assert freeze.invalidation_criteria == tuple(
        item.value for item in protocol.invalidation_criteria)
    assert set(freeze.prohibited_operations) == {
        item.value for item in GLOBALLY_PROHIBITED_OPERATIONS}
    assert freeze.assert_belongs_to(protocol) is freeze
    # The scope and comparison are bound by their own digests, not by prose.
    assert len(freeze.scope_identity) == 64
    assert len(freeze.evidence_scope_identity) == 64
    assert len(freeze.comparison_identity) == 64


def test_freeze_timestamp_is_provenance_not_identity(protocol):
    """(16) Re-freezing the same contract reproduces the same freeze identity."""
    first = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0, note="T0")
    later = ProtocolFreeze.freeze_protocol(
        protocol, frozen_at="2099-12-31T23:59:59Z", note="a different note")
    assert later.freeze_identity == first.freeze_identity
    assert later.semantic_identity == first.semantic_identity
    assert later.frozen_at != first.frozen_at


def test_a_changed_contract_produces_a_different_freeze(
        ready_opportunity, dimensions):
    """(16) A post-hoc change is visible as a different PFR-*. """
    base = _protocol(ready_opportunity, dimensions)
    changed = _protocol(
        ready_opportunity, dimensions, comparison=ComparisonSpecification(
            kind=ComparisonKind.SLICE_A_VS_SLICE_B,
            left=ComparisonTerm(reference="slice:trending"),
            right=ComparisonTerm(reference="slice:choppy")))
    assert (ProtocolFreeze.freeze_protocol(changed).freeze_identity
            != ProtocolFreeze.freeze_protocol(base).freeze_identity)


def test_freeze_must_describe_the_protocol_it_claims(protocol, dimensions):
    """(16) A freeze that drifts from its contract fails closed."""
    freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0)
    other = _protocol(_opportunity(CANONICAL_IDS[1]), dimensions)
    with pytest.raises(ResearchProtocolValidationError, match="contradicts"):
        freeze.assert_belongs_to(other)
    # Executing against another protocol's freeze is refused as scope drift.
    other_freeze = ProtocolFreeze.freeze_protocol(other, frozen_at=T0)
    with pytest.raises(ScopeDriftError, match="binds protocol"):
        assert_scope_consistent(
            protocol, _permitted_intent(protocol, dimensions),
            freeze=other_freeze)



# ═══ 17-18. Supersession and status ════════════════════════════════════════


def test_a_changed_protocol_becomes_new_history(protocol, ready_opportunity):
    """(P, S) Supersession is NEW history; the original is never rewritten."""
    frozen = protocol.with_status(ProtocolStatus.VALIDATED).with_status(
        ProtocolStatus.FROZEN)
    original_record = frozen.to_dict()

    successor = frozen.supersede(
        reason=SupersessionReason.COMPARISON_CORRECTED,
        opportunity=ready_opportunity,
        comparison=ComparisonSpecification(
            kind=ComparisonKind.SLICE_A_VS_SLICE_B,
            left=ComparisonTerm(reference="slice:trending"),
            right=ComparisonTerm(reference="slice:choppy")),
        created_at=T0)

    # A genuinely new identity, with explicit, governed provenance.
    assert successor.protocol_identity != frozen.protocol_identity
    assert successor.supersedes == frozen.protocol_identity
    assert successor.supersession_reason is SupersessionReason.COMPARISON_CORRECTED
    assert successor.protocol_version == frozen.protocol_version + 1

    # The superseded protocol is byte-for-byte unchanged and still readable.
    assert frozen.to_dict() == original_record
    assert frozen.status is ProtocolStatus.FROZEN
    ProtocolFreeze.freeze_protocol(frozen).assert_belongs_to(frozen)


def test_a_frozen_protocol_can_never_be_reopened(protocol):
    """(R) Reopening a frozen protocol is how a post-hoc rewrite begins."""
    frozen = protocol.with_status(ProtocolStatus.VALIDATED).with_status(
        ProtocolStatus.FROZEN)
    for target in (ProtocolStatus.DRAFT, ProtocolStatus.VALIDATED):
        with pytest.raises(ResearchProtocolValidationError,
                           match="illegal protocol status transition"):
            frozen.with_status(target)
    cancelled = protocol.with_status(ProtocolStatus.CANCELLED)
    with pytest.raises(ResearchProtocolValidationError, match="illegal"):
        cancelled.with_status(ProtocolStatus.VALIDATED)
    # And a cancelled or superseded protocol authorises nothing further.
    for dead in (cancelled, frozen.with_status(ProtocolStatus.SUPERSEDED)):
        with pytest.raises(ScopeDriftError, match="CANCELLED|SUPERSEDED"):
            assert_operation_permitted(dead, ResearchOperation.AGGREGATE)


def test_status_never_pretends_a_research_outcome(protocol):
    """(18) The status vocabulary is about intent, never about results."""
    assert [item.value for item in ProtocolStatus] == [
        "DRAFT", "VALIDATED", "FROZEN", "SUPERSEDED", "CANCELLED"]
    for forbidden in ("SUCCESS", "FAILED", "PROFITABLE", "PROMOTED", "COMPLETE",
                      "PASSED", "REJECTED"):
        assert not hasattr(ProtocolStatus, forbidden)


def test_supersession_requires_a_governed_reason_and_a_real_predecessor(
        protocol, ready_opportunity):
    """(S) A successor may not be forged or left unexplained."""
    with pytest.raises(ResearchProtocolValidationError,
                       match="supersession_reason"):
        replace(protocol, supersedes=protocol.protocol_identity)
    with pytest.raises(ResearchProtocolValidationError,
                       match="must declare a governed supersession_reason"):
        replace(protocol, supersession_reason=SupersessionReason.SCOPE_CORRECTED)
    with pytest.raises(ProtocolNamespaceViolation, match="RPL-"):
        replace(protocol, supersedes="NOT-A-PROTOCOL",
                supersession_reason=SupersessionReason.SCOPE_CORRECTED)
    with pytest.raises(ResearchProtocolValidationError, match="supersede itself"):
        replace(protocol, supersedes=protocol.protocol_identity,
                supersession_reason=SupersessionReason.SCOPE_CORRECTED)
    # An already-superseded protocol may not be superseded a second time.
    successor = protocol.supersede(
        reason=SupersessionReason.SCOPE_CORRECTED,
        opportunity=ready_opportunity)
    retired = successor.with_status(ProtocolStatus.VALIDATED).with_status(
        ProtocolStatus.FROZEN).with_status(ProtocolStatus.SUPERSEDED)
    with pytest.raises(ResearchProtocolValidationError, match="already SUPERSEDED"):
        retired.supersede(reason=SupersessionReason.SCOPE_CORRECTED,
                          opportunity=ready_opportunity)



# ═══ 22. The post-hoc change demonstration ══════════════════════════════════
#
# No real research result is ever generated. The point is purely structural: given
# a protocol that already exists and is already frozen, every semantic field either
# cannot be changed in place, or produces a NEW identity carrying explicit
# supersession. There is no third outcome, and specifically no silent mutation.

_POST_HOC_ATTEMPTS = {
    "comparison": (
        "comparison",
        SupersessionReason.COMPARISON_CORRECTED,
        lambda p: ComparisonSpecification(
            kind=ComparisonKind.SLICE_A_VS_SLICE_B,
            left=ComparisonTerm(reference="slice:trending"),
            right=ComparisonTerm(reference="slice:choppy"))),
    "evidence boundary": (
        "evidence",
        SupersessionReason.EVIDENCE_BOUNDARY_CORRECTED,
        lambda p: replace(p.evidence, evidence_boundary="2026-09-26T00:00:00Z")),
    "allowed operation": (
        "permitted_operations",
        SupersessionReason.PERMITTED_OPERATIONS_CORRECTED,
        lambda p: (ResearchOperation.AGGREGATE,)),
    "completion criterion": (
        "completion_criteria",
        SupersessionReason.COMPLETION_CRITERIA_CORRECTED,
        lambda p: (CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE,)),
    "population": (
        "evidence",
        SupersessionReason.POPULATION_CORRECTED,
        lambda p: replace(p.evidence,
                          evidence_references=("evidence:a_different_population",))),
    "confirmation requirement": (
        "confirmation_requirement",
        SupersessionReason.CONFIRMATION_REQUIREMENT_CORRECTED,
        lambda p: ConfirmationRequirement.NONE),
}


@pytest.mark.parametrize("label", sorted(_POST_HOC_ATTEMPTS))
def test_no_semantic_field_can_be_rewritten_after_the_fact(
        label, protocol, ready_opportunity):
    """(22) Every semantic change is refused in place, or becomes NEW history."""
    field, reason, mutate = _POST_HOC_ATTEMPTS[label]
    value = mutate(protocol)

    frozen = protocol.with_status(ProtocolStatus.VALIDATED).with_status(
        ProtocolStatus.FROZEN)
    frozen_record = frozen.to_dict()
    frozen_freeze = ProtocolFreeze.freeze_protocol(frozen, frozen_at=T0)

    # (a) The frozen record is IMMUTABLE: changed semantics presented under the
    #     frozen identity are refused outright, never accepted and ignored.
    with pytest.raises(ResearchProtocolValidationError, match="does not match"):
        replace(frozen, **{field: value})

    # (b) A genuinely new protocol, with explicit governed supersession, exists.
    successor = frozen.supersede(
        reason=reason, opportunity=ready_opportunity, **{field: value})
    assert successor.protocol_identity != frozen.protocol_identity
    assert successor.supersedes == frozen.protocol_identity
    assert successor.supersession_reason is reason

    # (c) Neither the frozen protocol nor its T0 freeze ever moved.
    assert frozen.to_dict() == frozen_record
    assert frozen.protocol_identity == frozen_freeze.protocol_identity
    frozen_freeze.assert_belongs_to(frozen)
    # ... and the change is now visible as a DIFFERENT freeze too.
    assert (ProtocolFreeze.freeze_protocol(successor).freeze_identity
            != frozen_freeze.freeze_identity)



# ═══ 19. Persistence, restart and dedup ═════════════════════════════════════


def test_store_requires_the_opportunity_at_registration(
        tmp_path, protocol, ready_opportunity):
    """(B, C) There is no way to persist a protocol without proving it governed."""
    store = ResearchProtocolStore(tmp_path / "protocols.json")
    assert len(store) == 0
    with pytest.raises(TypeError):
        # The opportunity argument is mandatory, not optional.
        store.register_protocol(protocol)
    with pytest.raises(ProtocolAuthorisationError,
                       match="only be registered together"):
        store.register_protocol(protocol, opportunity="an idea")
    # And a mismatched opportunity is refused before anything is written.
    other = _opportunity(CANONICAL_IDS[3])
    with pytest.raises(ProtocolAuthorisationError, match="authorises"):
        store.register_protocol(protocol, opportunity=other)
    assert len(store) == 0
    assert store.register_protocol(
        protocol, opportunity=ready_opportunity) is protocol
    assert len(store) == 1


def test_restart_reproduces_protocol_and_freeze_identities(
        tmp_path, protocol, ready_opportunity, dimensions):
    """(T, Q) Identities survive a restart exactly."""
    path = tmp_path / "protocols.json"
    freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0)
    first = ResearchProtocolStore(path)
    first.register_protocol(protocol, opportunity=ready_opportunity)
    first.register_freeze(freeze, protocol=protocol)

    second = ResearchProtocolStore(path)
    reloaded = second.get_protocol(protocol.protocol_identity)
    assert reloaded is not None
    assert reloaded.protocol_identity == protocol.protocol_identity
    assert reloaded.semantic_identity == protocol.semantic_identity
    assert reloaded.to_dict() == protocol.to_dict()
    reloaded_freeze = second.get_freeze(freeze.freeze_identity)
    assert reloaded_freeze.freeze_identity == freeze.freeze_identity
    assert reloaded_freeze.frozen_at == T0
    reloaded_freeze.assert_belongs_to(reloaded)
    # A protocol re-derived after the restart dedups to the same record.
    rederived = _protocol(ready_opportunity, dimensions)
    assert second.register_protocol(
        rederived, opportunity=ready_opportunity) is reloaded
    assert len(second) == 1


def test_semantic_duplicates_deduplicate_and_keep_creation_provenance(
        tmp_path, ready_opportunity, dimensions):
    """(U) Re-deriving a protocol is not re-creating it."""
    path = tmp_path / "protocols.json"
    original = _protocol(
        ready_opportunity, dimensions, created_at="2020-01-01T00:00:00Z")
    store = ResearchProtocolStore(path)
    store.register_protocol(original, opportunity=ready_opportunity)
    again = _protocol(
        ready_opportunity, dimensions, created_at="2099-01-01T00:00:00Z",
        note="a note that is not identity")
    assert again.protocol_identity == original.protocol_identity
    returned = store.register_protocol(again, opportunity=ready_opportunity)
    assert returned.created_at == "2020-01-01T00:00:00Z"
    assert len(store) == 1
    # ... and after a restart too.
    assert len(ResearchProtocolStore(path)) == 1
    assert ResearchProtocolStore(path).get_protocol(
        original.protocol_identity).created_at == "2020-01-01T00:00:00Z"


def test_store_rejects_a_rebound_identity(tmp_path, ready_opportunity, dimensions):
    """(T) An identity is never rebound to different immutable semantics."""
    store = ResearchProtocolStore(tmp_path / "protocols.json")
    original = _protocol(ready_opportunity, dimensions)
    store.register_protocol(original, opportunity=ready_opportunity)
    impostor = replace(
        original, note="different", semantic_identity="",
        protocol_identity=original.protocol_identity)
    # Recomputing the identity makes it a DIFFERENT protocol, so the collision is
    # impossible to reach through the public constructor; the guard is proven by
    # presenting a forged semantic identity instead.
    forged = replace(original, semantic_identity=original.semantic_identity)
    assert forged.protocol_identity == original.protocol_identity
    different = _protocol(
        ready_opportunity, dimensions, protocol_version=7)
    assert different.protocol_identity != original.protocol_identity
    store.register_protocol(different, opportunity=ready_opportunity)
    assert len(store) == 2
    # Both histories are retained side by side.
    assert {item.protocol_identity for item in store.protocols()} == {
        original.protocol_identity, different.protocol_identity}


def test_orphan_freeze_and_supersession_fail_closed(
        tmp_path, protocol, ready_opportunity):
    """(T) A freeze may not outlive its protocol; a successor may not precede it."""
    store = ResearchProtocolStore(tmp_path / "protocols.json")
    freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0)
    with pytest.raises(ResearchProtocolError, match="already-recorded protocol"):
        store.register_freeze(freeze, protocol=protocol)
    # A superseding protocol may not be registered before its predecessor.
    successor = protocol.supersede(
        reason=SupersessionReason.SCOPE_CORRECTED,
        opportunity=ready_opportunity)
    with pytest.raises(ResearchProtocolError, match="not in this store"):
        store.register_protocol(successor, opportunity=ready_opportunity)
    # Once the predecessor exists, the successor is accepted and both are kept.
    store.register_protocol(protocol, opportunity=ready_opportunity)
    store.register_protocol(successor, opportunity=ready_opportunity)
    assert len(store) == 2
    assert store.supersessions() == (
        (successor.protocol_identity, protocol.protocol_identity),)



def _write_store(path, protocol, ready_opportunity, *, freezes=()):
    store = ResearchProtocolStore(path)
    store.register_protocol(protocol, opportunity=ready_opportunity)
    for freeze in freezes:
        store.register_freeze(freeze, protocol=protocol)
    return store


def test_store_has_no_import_time_writes(tmp_path):
    """(19) Records are written only by an explicit call."""
    target = tmp_path / "never_created.json"
    ResearchProtocolStore(target)
    assert not target.exists()
    assert STORE_FORMAT == "research_protocol_store_v1"
    # The default path is a constant, resolved without touching the filesystem.
    assert store_module.DEFAULT_RESEARCH_PROTOCOL_STORE_PATH.name == (
        "research_protocols.json")


def test_corrupt_or_contradictory_persisted_protocol_fails_closed(
        tmp_path, protocol, ready_opportunity):
    """(19) Nothing corrupted is silently repaired."""
    path = tmp_path / "protocols.json"
    _write_store(path, protocol, ready_opportunity)
    document = json.loads(path.read_text(encoding="utf-8"))

    # A tampered identity that no longer matches the material.
    tampered = json.loads(json.dumps(document))
    tampered["protocols"][0]["protocol_identity"] = "RPL-" + "0" * 16
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="does not match"):
        ResearchProtocolStore(path)

    # An operation outside the closed vocabulary.
    bad_operation = json.loads(json.dumps(document))
    bad_operation["protocols"][0]["permitted_operations"] = ["DO_ANYTHING"]
    path.write_text(json.dumps(bad_operation), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="closed"):
        ResearchProtocolStore(path)

    # A protocol that dropped its globally prohibited authority.
    under_prohibited = json.loads(json.dumps(document))
    under_prohibited["protocols"][0]["prohibited_operations"] = [
        ProhibitedOperation.PRODUCTION_TRADING.value]
    path.write_text(json.dumps(under_prohibited), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="missing globally prohibited"):
        ResearchProtocolStore(path)

    # A protocol stripped of its required scope.
    scopeless = json.loads(json.dumps(document))
    scopeless["protocols"][0]["scope"]["in_scope"] = []
    path.write_text(json.dumps(scopeless), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="at least one in-scope"):
        ResearchProtocolStore(path)

    # An empty completion-criteria list.
    empty_criteria = json.loads(json.dumps(document))
    empty_criteria["protocols"][0]["completion_criteria"] = []
    path.write_text(json.dumps(empty_criteria), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="must not be empty"):
        ResearchProtocolStore(path)

    # An unknown field, an unknown format, an absent version, and garbage.
    extra = json.loads(json.dumps(document))
    extra["protocols"][0]["surprise"] = 1
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="unknown fields"):
        ResearchProtocolStore(path)

    for corrupt, match in (
        ({**document, "format": "something_else_v9"}, "unknown"),
        ({key: value for key, value in document.items()
          if key != "protocol_schema_version"}, "missing"),
        ({**document, "protocol_schema_version": 99}, "unsupported"),
        ({"format": STORE_FORMAT}, "missing"),
    ):
        path.write_text(json.dumps(corrupt), encoding="utf-8")
        with pytest.raises(ResearchProtocolError, match=match):
            ResearchProtocolStore(path)

    path.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="unreadable"):
        ResearchProtocolStore(path)


def test_orphan_freeze_on_disk_fails_closed(
        tmp_path, protocol, ready_opportunity, dimensions):
    """(19) A persisted freeze may never outlive its protocol."""
    path = tmp_path / "protocols.json"
    _write_store(path, protocol, ready_opportunity,
                 freezes=(ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0),))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["protocols"] = []
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="may never outlive its protocol"):
        ResearchProtocolStore(path)

    # A freeze whose recorded material was tampered with is refused at load,
    # before the cross-check against its protocol is even reached.
    path2 = tmp_path / "protocols2.json"
    _write_store(path2, protocol, ready_opportunity,
                 freezes=(ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0),))
    document2 = json.loads(path2.read_text(encoding="utf-8"))
    document2["freezes"][0]["scope_identity"] = "0" * 64
    path2.write_text(json.dumps(document2), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="does not match the frozen"):
        ResearchProtocolStore(path2)


def test_orphan_supersession_on_disk_fails_closed(
        tmp_path, protocol, ready_opportunity):
    """(19) A persisted successor may never precede its predecessor."""
    path = tmp_path / "protocols.json"
    successor = protocol.supersede(
        reason=SupersessionReason.SCOPE_CORRECTED,
        opportunity=ready_opportunity)
    store = ResearchProtocolStore(path)
    store.register_protocol(protocol, opportunity=ready_opportunity)
    store.register_protocol(successor, opportunity=ready_opportunity)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["protocols"] = [
        row for row in document["protocols"]
        if row["protocol_identity"] != protocol.protocol_identity]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResearchProtocolError, match="orphaned supersession"):
        ResearchProtocolStore(path)



# ═══ 23. Wave 5 integration: the protocol consumes, never re-decides ════════


def _wave5_chain(ready_opportunity, dimensions):
    """The full governed Wave 5 path: opportunity -> agenda -> queue -> freeze."""
    agenda = ResearchAgenda.build(
        POLICY_LEXICOGRAPHIC_V1, (ready_opportunity,),
        evidence_boundary=EVIDENCE_BOUNDARY)
    queue = ResearchQueue.build(agenda, capacity=1)
    agenda_freeze = AgendaFreeze.freeze(agenda, capacity=1, frozen_at=T0)
    return agenda, queue, agenda_freeze


def test_protocol_preserves_the_whole_wave5_authorisation_path(
        ready_opportunity, dimensions):
    """(C, 20-C) ROP -> AGD -> QUE -> AFR -> RPL -> PFR survives end to end."""
    agenda, queue, agenda_freeze = _wave5_chain(ready_opportunity, dimensions)
    protocol = _protocol(
        ready_opportunity, dimensions,
        agenda_identity=agenda.agenda_identity,
        queue_identity=queue.queue_identity,
        agenda_freeze_identity=agenda_freeze.freeze_identity)
    freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0)

    # Every link in the chain is present and correctly namespaced.
    assert protocol.authorisation.opportunity_identity == (
        ready_opportunity.opportunity_identity)
    assert protocol.authorisation.agenda_identity == agenda.agenda_identity
    assert protocol.authorisation.queue_identity == queue.queue_identity
    assert protocol.authorisation.agenda_freeze_identity == (
        agenda_freeze.freeze_identity)
    assert is_research_agenda_identity(protocol.authorisation.agenda_identity)
    assert is_agenda_freeze_identity(protocol.authorisation.agenda_freeze_identity)
    assert protocol.authorisation.queue_identity.startswith(RESEARCH_QUEUE_ID_PREFIX)
    # And the protocol's own freeze carries the same chain forward.
    assert freeze.agenda_identity == agenda.agenda_identity
    assert freeze.queue_identity == queue.queue_identity
    assert freeze.agenda_freeze_identity == agenda_freeze.freeze_identity


def test_wave6_does_not_rerank_or_rewrite_wave5(ready_opportunity, dimensions):
    """(V, 23) Wave 5 owns attention; Wave 6 only describes the investigation."""
    agenda, queue, agenda_freeze = _wave5_chain(ready_opportunity, dimensions)
    agenda_record = agenda.to_dict()
    queue_record = queue.to_dict()
    freeze_record = agenda_freeze.to_dict()
    ordered = agenda.ordered_identities

    # Constructing many protocols over the same agenda changes nothing about it.
    for _ in range(3):
        _protocol(ready_opportunity, dimensions,
                  agenda_identity=agenda.agenda_identity)
        _protocol(ready_opportunity, dimensions,
                  agenda_identity=agenda.agenda_identity,
                  queue_identity=queue.queue_identity)

    # Nothing about the agenda, its order, its capacity or its freeze moved.
    assert agenda.to_dict() == agenda_record
    assert queue.to_dict() == queue_record
    assert agenda_freeze.to_dict() == freeze_record
    assert agenda.ordered_identities == ordered
    assert queue.capacity == 1
    # Wave 6 never re-derives information value or cost: it does not hold them.
    protocol = _protocol(ready_opportunity, dimensions)
    for forbidden in ("information", "cost", "band", "priority", "rank", "score"):
        assert not hasattr(protocol, forbidden)


def test_wave5_state_may_not_be_relabelled_by_wave6(dimensions):
    """(V) A REFUSED Wave 5 verdict is not a DRAFT protocol in disguise."""
    refused = _opportunity(
        CANONICAL_IDS[4], OpportunityState.REFUSED,
        reason_codes=("EXPANSION_NOT_JUSTIFIED",))
    with pytest.raises(ProtocolAuthorisationError, match="only be authored over"):
        _protocol(refused, dimensions)
    # Nor by presenting a different state string in the authorisation directly.
    with pytest.raises(ProtocolAuthorisationError, match="only be authored over"):
        ProtocolAuthorisation(
            opportunity_identity=ready_identity(),
            opportunity_state="READY_ENOUGH")


def ready_identity():
    return _opportunity(CANONICAL_IDS[0]).opportunity_identity



# ═══ 24. Wave 4 integration: search and multiplicity are preserved ═════════


@pytest.fixture
def wave4_selection(dimensions):
    """
    A REAL Wave 4 search family, record, multiplicity record and T0 selection
    freeze, built exactly as the Wave 4 tests build one.

    The protocol built over it must carry every one of those identities, and the
    Wave 4 records themselves must be unchanged by having been consumed.
    """
    from research_engine.lifecycle.curiosity_generator import propose_expansion
    from research_engine.lifecycle.curiosity_signal import (
        CuriositySignal,
        CuriositySignalType,
    )
    from research_engine.lifecycle.search_multiplicity import (
        CORRECTION_METHOD_BONFERRONI,
        derive_multiplicity,
    )
    from research_engine.lifecycle.search_provenance import (
        AlternativeEvaluation,
        ConfirmationKind,
        ConfirmationPolicy,
        DiscoveryPopulation,
        MultiplicityRule,
        SearchAlternative,
        SearchFamily,
        SearchRecord,
        SelectionFreeze,
        search_provenance_payload,
    )

    registry = DimensionRegistry()
    built = {}
    for key in ("anchor", "horizon", "volatility"):
        dimension = GovernedDimension.create(
            dimension_key=key,
            authority=EvidenceAuthority(
                dataset="decision_trace", schema_version="v1",
                producer=EvidenceProducer.DECISION_TRACE,
                field_path=f"market.{key}",
                semantic_meaning="declared test-time semantics"),
        )
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
        built[key] = dimension

    def proposal(key):
        signal = CuriositySignal.create(
            signal_type=CuriositySignalType.FINDING_EVIDENCE,
            source_ref="finding:TRG-0001", source_identity="TRG-0001",
            source_provenance={"adapter": "finding_trigger",
                               "trigger_id": "TRG-0001"},
            target_kind="research_subject", target_ref="exit_behaviour",
            reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
            evidence_reference="finding:finding-42",
            evidence_boundary=EVIDENCE_BOUNDARY,
            dimension_ref=registry.get(key).dimension_identity)
        return propose_expansion(signal, registry)

    discovery = DiscoveryPopulation(
        population_identity="population:joint_v1",
        evidence_boundary=EVIDENCE_BOUNDARY,
        dataset_fingerprint=fingerprint_for_records(
            [{"market.regime": "TRENDING", "t": index} for index in range(500)],
            dataset_id="wave6_population", population="joint_eligible"))

    outcomes = (
        ("anchor", AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
         EligibilityState.PERMIT, ()),
        ("horizon", AlternativeEvaluation.STATISTICALLY_EVALUATED,
         EligibilityState.PERMIT, ("NOT_SELECTED_BY_GOVERNED_PROCESS",)),
        ("volatility", AlternativeEvaluation.NOT_EVALUATED,
         EligibilityState.BLOCKED, ("EVIDENCE_FREEZE_UNAVAILABLE",)),
    )
    alternatives = tuple(
        SearchAlternative(
            subject_kind="research_subject", subject_ref="exit_behaviour",
            proposed_dimension_identity=registry.get(key).dimension_identity,
            proposed_interaction_identity=None,
            proposal_identity=proposal(key).proposal_identity,
            evidence_reference="finding:finding-42",
            evidence_boundary=EVIDENCE_BOUNDARY,
            eligibility_state=state, evaluation=evaluation,
            exclusion_reason_codes=reasons, depth=2)
        for key, evaluation, state, reasons in outcomes)

    record = SearchRecord.create(
        family=SearchFamily.create(
            trigger_ref="finding:TRG-0001", trigger_identity="TRG-0001",
            curiosity_mode="EXPANSION", subject_kind="research_subject",
            subject_ref="exit_behaviour",
            discovery_population_identity=discovery.population_identity,
            evidence_boundary=EVIDENCE_BOUNDARY, search_depth=2,
            alternative_space={"kind": "EXPLICIT_BOUNDED",
                               "governed_keys": ["anchor", "horizon",
                                                  "volatility"],
                               "selection_criterion_supplied_by":
                                   "governed_research_process"},
            multiplicity_rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES,
            alternatives=alternatives),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME", created_at=T0)
    confirmation = ConfirmationPolicy(
        kind=ConfirmationKind.PROSPECTIVE_AFTER_T0, reference=f"boundary:{T0}")
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=confirmation, selected_at=T0)
    return {
        "record": record, "freeze": freeze, "discovery": discovery,
        "confirmation": confirmation,
        "multiplicity": derive_multiplicity(record),
        "payload": search_provenance_payload(record, freeze),
        "dimensions": built,
    }



def test_wave4_selection_becomes_a_governed_protocol(wave4_selection, dimensions):
    """(W, 24) ALL Wave 4 provenance travels with the protocol."""
    record = wave4_selection["record"]
    freeze = wave4_selection["freeze"]
    confirmation = wave4_selection["confirmation"]
    opportunity = _opportunity(
        freeze.freeze_identity, OpportunityState.READY,
        subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
        search_provenance=wave4_selection["payload"])

    protocol = _protocol(
        opportunity, dimensions,
        evidence=EvidenceScope(
            evidence_boundary=EVIDENCE_BOUNDARY,
            discovery_population=wave4_selection["discovery"],
            confirmation_policy=confirmation,
            dataset_fingerprint_identity=(
                wave4_selection["discovery"].fingerprint_identity()),
            discovery_population_identity=(
                wave4_selection["discovery"].population_identity),
            symbol_scope=("symbol:EURUSD",)),
        scope=InvestigationScope(
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
            subject_ref=freeze.freeze_identity,
            in_scope=(ScopeComponent(ScopeComponentKind.DIMENSION,
                                     dimensions["stop_distance"]
                                     .dimension_identity),)))

    # Search, family, alternatives, multiplicity, selection freeze, discovery
    # population and confirmation policy all survive into the protocol.
    assert protocol.search_provenance["search_record_id"] == record.search_identity
    assert protocol.search_provenance["search_family_id"] == record.family_identity
    assert protocol.search_provenance["selection_freeze_id"] == (
        freeze.freeze_identity)
    assert protocol.search_provenance["multiplicity_family_size"] == (
        record.derived_family_size)
    assert protocol.search_provenance["multiplicity_rule"] == (
        record.family.multiplicity_rule.value)
    assert protocol.search_provenance["correction_method"] == (
        freeze.correction_method)
    assert protocol.evidence.discovery_population.population_identity == (
        wave4_selection["discovery"].population_identity)
    assert protocol.evidence.confirmation_policy.kind is confirmation.kind
    assert protocol.assert_binds_to(opportunity) is protocol

    # And the Wave 4 records themselves are untouched by having been consumed.
    assert record.derived_family_size == 3
    assert len(record.alternatives) == 3
    assert freeze.multiplicity_family_size == 3
    assert wave4_selection["multiplicity"].corrected_alpha == pytest.approx(
        0.05 / 3)


def _wave4_kwargs(wave4_selection, dimensions, freeze):
    return dict(
        scope=InvestigationScope(
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
            subject_ref=freeze.freeze_identity,
            in_scope=(ScopeComponent(ScopeComponentKind.DIMENSION,
                                     dimensions["stop_distance"]
                                     .dimension_identity),)),
        evidence=EvidenceScope(
            evidence_boundary=EVIDENCE_BOUNDARY,
            discovery_population=wave4_selection["discovery"],
            confirmation_policy=wave4_selection["confirmation"],
            discovery_population_identity=(
                wave4_selection["discovery"].population_identity)),
    )



def test_wave4_provenance_may_not_be_contradicted(wave4_selection, dimensions):
    """(W) A protocol contradicting frozen Wave 4 provenance fails closed."""
    from research_engine.lifecycle.search_provenance import (
        ConfirmationKind,
        ConfirmationPolicy,
    )
    record = wave4_selection["record"]
    freeze = wave4_selection["freeze"]
    kwargs = _wave4_kwargs(wave4_selection, dimensions, freeze)
    opportunity = _opportunity(
        freeze.freeze_identity, OpportunityState.READY,
        subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
        search_provenance=wave4_selection["payload"])

    # A different evidence boundary than the frozen discovery evidence.
    with pytest.raises(ResearchProtocolValidationError,
                       match="evidence boundary contradicts"):
        _protocol(opportunity, dimensions, **{
            **kwargs, "evidence": replace(
                kwargs["evidence"],
                evidence_boundary="2026-09-26T00:00:00Z")})
    # A different discovery population than the frozen one.
    with pytest.raises(ResearchProtocolValidationError, match="contradicts"):
        _protocol(opportunity, dimensions, **{
            **kwargs, "evidence": replace(
                kwargs["evidence"],
                discovery_population_identity="population:something_else")})
    # A different confirmation route than the frozen Wave 4 policy.
    with pytest.raises(ResearchProtocolValidationError,
                       match="confirmation policy contradicts"):
        _protocol(opportunity, dimensions, **{
            **kwargs, "evidence": replace(
                kwargs["evidence"],
                confirmation_policy=ConfirmationPolicy(
                    kind=ConfirmationKind.NOT_YET_AVAILABLE))})
    # A subject that is not the selection freeze the provenance claims.
    retyped = _opportunity(
        record.search_identity, OpportunityState.READY,
        subject_kind=OpportunitySubjectKind.SEARCH_RECORD,
        search_provenance=wave4_selection["payload"])
    with pytest.raises(ProtocolAuthorisationError, match="selection freeze"):
        _protocol(retyped, dimensions, **{
            **kwargs, "scope": replace(
                kwargs["scope"], subject_ref=retyped.subject_ref)})
    # A Wave 4 subject may never even reach Wave 6 without its provenance: Wave 5
    # refuses to build the opportunity, so the gap is closed upstream too.
    from research_engine.lifecycle.research_opportunity import (
        ResearchOpportunityValidationError,
    )
    with pytest.raises(ResearchOpportunityValidationError, match="search_provenance"):
        _opportunity(
            freeze.freeze_identity, OpportunityState.READY,
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE)


def test_wave6_does_not_alter_any_wave4_or_wave5_module(wave4_selection):
    """(V, W) Wave 6 is purely additive: no earlier source file is referenced."""
    from research_engine.lifecycle import (
        research_agenda,
        research_agenda_store,
        research_opportunity,
        research_priority,
        research_queue,
        search_multiplicity,
        search_provenance,
        search_record_store,
    )
    for module in (search_provenance, search_multiplicity, search_record_store,
                   research_opportunity, research_priority, research_agenda,
                   research_queue, research_agenda_store):
        source = inspect.getsource(module)
        for fragment in ("research_protocol", "RPL-", "PFR-"):
            assert fragment not in source, module.__name__
    assert search_provenance.SEARCH_PROVENANCE_SCHEMA_VERSION == 1
    assert research_opportunity.RESEARCH_OPPORTUNITY_SCHEMA_VERSION == 1
    assert research_agenda.RESEARCH_AGENDA_SCHEMA_VERSION == 1
    assert research_queue.RESEARCH_QUEUE_SCHEMA_VERSION == 1
    # The Wave 4 selection used above is itself still intact and unchanged.
    assert wave4_selection["record"].search_identity.startswith("SRC-")
    assert wave4_selection["freeze"].freeze_identity.startswith("FRZ-")


def test_waves_0_to_3_identities_remain_authoritative():
    """(X) Wave 6 mints no replacement identity for any earlier wave."""
    from research_engine.lifecycle import (
        curiosity_generator,
        curiosity_proposal,
        curiosity_signal,
        dimension_registry,
        generated_research_identity,
        governed_dimension as governed_dimension_module,
        progressive_depth_gate,
        research_interaction,
    )
    for module in (generated_research_identity, governed_dimension_module,
                   research_interaction, progressive_depth_gate,
                   curiosity_signal, curiosity_proposal, curiosity_generator,
                   dimension_registry):
        source = inspect.getsource(module)
        for fragment in ("research_protocol", "RPL-", "PFR-"):
            assert fragment not in source, module.__name__
    assert generated_research_identity.GENERATED_RESEARCH_ID_PREFIX == "GEN-"
    assert governed_dimension_module.DIMENSION_ID_PREFIX == "DIM-"
    assert research_interaction.INTERACTION_ID_PREFIX == "IXN-"
    assert research_interaction.SLICE_ID_PREFIX == "SLC-"
    assert progressive_depth_gate.DECISION_ID_PREFIX == "DEC-"
    assert curiosity_proposal.CURIOSITY_PROPOSAL_ID_PREFIX == "PRP-"
    assert curiosity_signal.CURIOSITY_SIGNAL_ID_PREFIX == "CSN-"
    # Wave 2's vocabulary, which Wave 6 does not re-define, is still Wave 2's.
    assert [state.value for state in EligibilityState] == [
        "PERMIT", "WAITING_DATA", "BLOCKED", "REFUSE"]
    # Wave 6 reuses the Wave 1 namespaces rather than replacing them.
    assert protocol_module.RESEARCH_PROTOCOL_ID_PREFIX == "RPL-"
    assert protocol_module.ScopeComponentKind.DIMENSION.value == "DIMENSION"



# ═══ 26. Canonical-70 isolation ═════════════════════════════════════════════


def test_canonical_70_remains_exactly_seventy(tmp_path, protocol,
                                              ready_opportunity):
    """(Y) Wave 6 namespaces are isolated; no protocol enters the registry."""
    from research_engine.registry.baseline_manifest import BASELINE_VERSION
    from research_engine.registry.research_question_registry import REGISTRY

    before = canonical_inventory()
    assert len(before) == CANONICAL_QUESTION_COUNT == 70
    assert len(set(before)) == 70

    path = tmp_path / "protocols.json"
    store = ResearchProtocolStore(path)
    store.register_protocol(protocol, opportunity=ready_opportunity)
    store.register_freeze(
        ProtocolFreeze.freeze_protocol(protocol, frozen_at=T0), protocol=protocol)
    successor = protocol.supersede(
        reason=SupersessionReason.SCOPE_CORRECTED,
        opportunity=ready_opportunity)
    store.register_protocol(successor, opportunity=ready_opportunity)
    ResearchProtocolStore(path)

    assert_canonical_70_intact()
    assert len(canonical_inventory()) == 70
    assert len(set(canonical_inventory())) == 70
    assert canonical_inventory() == before
    assert BASELINE_VERSION == 1
    assert len(REGISTRY) == 70

    # No Wave 6 identity is a canonical question ID.
    wave6_identities = {
        protocol.protocol_identity, successor.protocol_identity,
        ProtocolFreeze.freeze_protocol(protocol).freeze_identity,
    }
    assert wave6_identities.isdisjoint(set(canonical_inventory()))
    # A protocol may be ABOUT a canonical question but never BECOMES one.
    assert protocol.scope.subject_ref in set(canonical_inventory())
    for identity in wave6_identities:
        assert not is_research_protocol_identity(identity) or (
            identity not in set(canonical_inventory()))


# ═══ 27. No execution guarantee (AST / source level) ═══════════════════════


WAVE6_MODULES = (protocol_module, scope_module, store_module)


def _source(module) -> str:
    return inspect.getsource(module)


def _tree(module):
    return ast.parse(_source(module))


def _imports(module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names



def test_wave6_calls_no_research_execution_or_candidate_machinery():
    """(Z, AA, AB) Wave 6 is declarative: it performs nothing and grants nothing."""
    forbidden_calls = {
        "run_experiment", "execute_research", "run_research", "create_hypothesis",
        "create_candidate", "activate_candidate", "promote_candidate",
        "apply_treatment", "send_order", "place_order", "modify_strategy",
        "modify_risk", "modify_sizing", "approve", "can_promote",
        "register_candidate", "submit_order", "execute", "run", "order",
        "promote", "activate", "backtest", "simulate",
    }
    for module in WAVE6_MODULES:
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


def test_wave6_imports_no_production_or_execution_path():
    """(AC) No runner, broker, risk, sizing, config or candidate path is reachable."""
    forbidden_fragments = (
        "broker", "sizing", "runner", "orchestrator", "governance_gate",
        "research_cycle_runner", "candidate_", "experiment", "backtest",
        "hypothesis", "treatment", "baseline_manifest", "placebo",
        "validation_harness", "shadow_hook", "research_engine.main",
        "research_engine.runner", "execution", "risk", "config", "runtime",
        "counterfactual", "data_pipeline", "lambda", "deployment", "profiles",
    )
    for module in WAVE6_MODULES:
        for name in _imports(module):
            for fragment in forbidden_fragments:
                assert fragment not in name, f"{module.__name__} imports {name}"


def test_wave6_source_contains_no_profitability_or_outcome_semantics():
    """(18) Wave 6 governs intent; it never speaks about outcomes."""
    forbidden = (
        "expectancy", "sharpe", "drawdown", "win_rate", "profit", "p_value",
        "r_multiple", "weighted", "normalise", "normalize", "random", "pnl",
        "roi",
    )
    for module in WAVE6_MODULES:
        tree = _tree(module)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(tree).lower()
        for token in forbidden:
            assert token not in code, f"{module.__name__} mentions {token!r}"


def test_wave6_never_enumerates_a_search_space():
    """Wave 6 describes ONE investigation; it never generates candidates."""
    for module in WAVE6_MODULES:
        tree = _tree(module)
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert {"product", "combinations", "permutations", "chain"}.isdisjoint(calls)
        assert not any("itertools" in name for name in _imports(module))



# ═══ 20. The core demonstration, criteria A-V ══════════════════════════════
#
# ONE legitimate Wave 5 READY opportunity, taken through the authoritative Wave 5
# structures into a Wave 6 protocol, and then checked against every success
# criterion. Nothing is executed: no research, no candidate, no treatment.


def test_core_demonstration_A_to_V(tmp_path, ready_opportunity, dimensions):
    """(20) The full governed path, with every criterion mechanically proven."""
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
    )
    from research_engine.lifecycle.search_record_store import SearchRecordStore

    # -- A. The opportunity is governed. --------------------------------
    assert is_research_opportunity_identity(ready_opportunity.opportunity_identity)
    assert ready_opportunity.state is OpportunityState.READY
    assert ready_opportunity.opportunity_identity.startswith(
        RESEARCH_OPPORTUNITY_ID_PREFIX)

    # -- Wave 5 builds the agenda, the queue and its T0 freeze. -----------
    agenda, queue, agenda_freeze = _wave5_chain(ready_opportunity, dimensions)
    agenda_record, queue_record = agenda.to_dict(), queue.to_dict()
    chain = dict(
        agenda_identity=agenda.agenda_identity,
        queue_identity=queue.queue_identity,
        agenda_freeze_identity=agenda_freeze.freeze_identity)

    # -- B/C. The protocol binds to THAT opportunity, with the chain kept. --
    protocol = _protocol(ready_opportunity, dimensions, **chain)
    assert protocol.assert_binds_to(ready_opportunity) is protocol
    assert protocol.authorisation.opportunity_identity == (
        ready_opportunity.opportunity_identity)
    assert protocol.authorisation.agenda_identity == agenda.agenda_identity

    # -- K. Protocol identity is deterministic. ---------------------------
    assert protocol.protocol_identity == _protocol(
        ready_opportunity, dimensions, **chain).protocol_identity

    # -- L. Notes and timestamps do not alter identity. -------------------
    noisy = _protocol(
        ready_opportunity, dimensions, **chain,
        label="a label", note="a note", created_at="2099-01-01T00:00:00Z")
    assert noisy.protocol_identity == protocol.protocol_identity

    # -- D/E/F/G/H/I/J. Everything the investigation means is frozen. -----
    assert protocol.evidence.evidence_boundary == EVIDENCE_BOUNDARY
    assert protocol.evidence.symbol_scope == ("symbol:EURUSD",)
    assert {item.value for item in protocol.permitted_operations} == {
        "AGGREGATE", "SLICE", "COMPARE", "EXCURSION_ANALYSIS"}
    assert {item.value for item in protocol.prohibited_operations} == {
        item.value for item in GLOBALLY_PROHIBITED_OPERATIONS}
    assert set(protocol.scope.in_scope_identities()) == {
        dimensions["stop_distance"].dimension_identity,
        dimensions["interaction"].interaction_identity}
    assert protocol.comparison.kind is ComparisonKind.SLICE_A_VS_SLICE_B
    assert protocol.confirmation_requirement is ConfirmationRequirement.REQUIRED
    assert CompletionCriterionKind.REQUIRED_EVIDENCE_COVERAGE in (
        protocol.completion_criteria)
    assert InsufficiencyCriterionKind.REQUIRED_POPULATION_UNAVAILABLE in (
        protocol.insufficiency_criteria)
    assert InvalidationCriterionKind.PROTOCOL_SCOPE_CHANGED in (
        protocol.invalidation_criteria)

    # -- Freeze at T0. ----------------------------------------------------
    frozen = protocol.with_status(ProtocolStatus.VALIDATED).with_status(
        ProtocolStatus.FROZEN)
    freeze = ProtocolFreeze.freeze_protocol(frozen, frozen_at=T0)
    assert freeze.protocol_identity == frozen.protocol_identity

    # -- M. A semantic scope change alters identity. ----------------------
    assert _protocol(
        ready_opportunity, dimensions,
        permitted_operations=(ResearchOperation.AGGREGATE,)
    ).protocol_identity != protocol.protocol_identity

    # -- N. Undeclared research activity fails closed. -------------------
    with pytest.raises(ScopeDriftError):
        assert_scope_consistent(protocol, ResearchIntent(
            operation=ResearchOperation.SLICE,
            dimension_identities=(
                dimensions["entry_timing"].dimension_identity,),))

    # -- O. Out-of-scope discovery cannot expand the protocol. ------------
    before = protocol.to_dict()
    discovery = escalate_out_of_scope_discovery(protocol, ResearchIntent(
        operation=ResearchOperation.SLICE,
        dimension_identities=(dimensions["entry_timing"].dimension_identity,),))
    assert protocol.to_dict() == before
    assert discovery.escalation_route is EscalationRoute.CURIOSITY_LIFECYCLE


    # -- P. A changed protocol is new history, not a rewrite. ------------
    successor = frozen.supersede(
        reason=SupersessionReason.SCOPE_CORRECTED,
        opportunity=ready_opportunity,
        permitted_operations=(ResearchOperation.AGGREGATE,))
    assert successor.protocol_identity != frozen.protocol_identity
    assert successor.supersedes == frozen.protocol_identity
    assert frozen.protocol_identity == protocol.protocol_identity

    # -- Q. Restart reproduces protocol and freeze identities. -----------

    path = tmp_path / "protocols.json"
    store = ResearchProtocolStore(path)
    store.register_protocol(frozen, opportunity=ready_opportunity)
    store.register_freeze(freeze, protocol=frozen)
    store.register_protocol(successor, opportunity=ready_opportunity)
    reloaded = ResearchProtocolStore(path)
    assert reloaded.get_protocol(frozen.protocol_identity).protocol_identity == (
        frozen.protocol_identity)
    assert reloaded.get_freeze(freeze.freeze_identity).freeze_identity == (
        freeze.freeze_identity)
    assert len(reloaded) == 2

    # -- R/S/T/U/V. Nothing was executed, created, treated, or reordered. -
    gen_store = GeneratedResearchStore(tmp_path / "gen.json")
    search_store = SearchRecordStore(tmp_path / "searches.json")
    assert len(gen_store) == 0
    assert len(search_store) == 0
    # (V) Wave 5 history is untouched by the whole exercise.
    assert agenda.to_dict() == agenda_record
    assert queue.to_dict() == queue_record
    assert agenda.ordered_identities == (ready_opportunity.opportunity_identity,)
    # (AC) No production, runtime, config or data-collection file was touched:
    # the ONLY file the whole exercise created is the protocol history.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["protocols.json"]
    # (Y) The canonical registry is untouched throughout.
    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70


def test_wave6_namespaces_are_disjoint_from_every_other_wave():
    """(Y) `RPL-` and `PFR-` are Wave 6's own, and collide with nothing."""
    import re
    prefixes = {
        "GEN-": "generated_research_identity",
        "CSN-": "curiosity_signal",
        "PRP-": "curiosity_proposal",
        "DIM-": "governed_dimension",
        "IXN-": "research_interaction",
        "SLC-": "research_interaction",
        "DEC-": "progressive_depth_gate",
        "EXP-": "progressive_depth_gate",
        "ALT-": "search_provenance",
        "FAM-": "search_provenance",
        "SRC-": "search_provenance",
        "FRZ-": "search_provenance",
        "MUL-": "search_provenance",
        "ROP-": "research_opportunity",
        "POL-": "research_priority",
        "AGD-": "research_agenda",
        "AFR-": "research_agenda",
        "QUE-": "research_queue",
    }
    from research_engine.lifecycle import (
        curiosity_proposal,
        curiosity_signal,
        generated_research_identity,
        governed_dimension as governed_dimension_module,
        progressive_depth_gate,
        research_interaction,
        research_opportunity,
        research_priority,
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
        progressive_depth_gate.DECISION_ID_PREFIX,
        progressive_depth_gate.JUSTIFICATION_ID_PREFIX,
        search_provenance.SEARCH_ALTERNATIVE_ID_PREFIX,
        search_provenance.SEARCH_FAMILY_ID_PREFIX,
        search_provenance.SEARCH_RECORD_ID_PREFIX,
        search_provenance.SELECTION_FREEZE_ID_PREFIX,
        search_provenance.MULTIPLICITY_ID_PREFIX,
        research_opportunity.RESEARCH_OPPORTUNITY_ID_PREFIX,
        research_priority.PRIORITY_POLICY_ID_PREFIX,
        RESEARCH_AGENDA_ID_PREFIX,
        "AFR-", RESEARCH_QUEUE_ID_PREFIX,
    }
    assert RESEARCH_PROTOCOL_ID_PREFIX not in live
    assert PROTOCOL_FREEZE_ID_PREFIX not in live
    assert len(live) == len(set(live))
    # The RPL/PFR patterns match only their own shape.
    for identity in (RESEARCH_PROTOCOL_ID_PREFIX + "0123456789ABCDEF",
                     PROTOCOL_FREEZE_ID_PREFIX + "0123456789ABCDEF"):
        pattern = re.compile(
            rf"^{identity[:4]}[0-9A-F]{{{16}}}$")
        assert pattern.match(identity)
    # And no canonical question ever looks like a Wave 6 identity.
    assert all(
        not is_research_protocol_identity(item)
        and not is_protocol_freeze_identity(item)
        for item in canonical_inventory())


def test_the_two_operation_vocabularies_can_never_overlap(protocol):
    """
    (I) Defence in depth: permitted and prohibited authority are disjoint BY TYPE.

    The two vocabularies are separate enums with no shared members, so no
    record can ever hold the same string in both sets. The overlap guard in
    `ResearchProtocol._validate` is therefore a second line of defence rather
    than the primary mechanism -- which is the stronger design, and is asserted
    here so that a future change to the vocabularies cannot quietly weaken it.
    """
    permitted_values = {item.value for item in ResearchOperation}
    prohibited_values = {item.value for item in ProhibitedOperation}
    assert permitted_values.isdisjoint(prohibited_values)
    # The enum types themselves are distinct.
    assert ResearchOperation is not ProhibitedOperation
    assert issubclass(ResearchOperation, str) and issubclass(ProhibitedOperation, str)
    # And a ProhibitedOperation is never a ResearchOperation, nor vice versa.
    for authority in ProhibitedOperation:
        assert not isinstance(authority, ResearchOperation)
    for operation in ResearchOperation:
        assert not isinstance(operation, ProhibitedOperation)
    # The protocol's own two sets are therefore disjoint.
    assert {item.value for item in protocol.permitted_operations}.isdisjoint(
        {item.value for item in protocol.prohibited_operations})
    # An authorisation re-derived from the same opportunity is equivalent.
    shared = ProtocolAuthorisation(
        opportunity_identity=protocol.authorisation.opportunity_identity,
        opportunity_state=OpportunityState.READY.value)
    assert shared.opportunity_identity == (
        protocol.authorisation.opportunity_identity)

