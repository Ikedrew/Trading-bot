"""
Stage 3 / Wave 5 -- Research Agenda, Prioritisation and Bounded Queue.

Focused tests only. Scope:
    - governed `ROP-*` research opportunities bound to EXISTING governed
      research identities, with deterministic semantic identity;
    - honest, separate, inspectable information value and research cost, with
      UNKNOWN handled as UNKNOWN rather than optimistically;
    - explicit, deterministic dependencies that fail closed on cycles and
      dangling references;
    - an explicit, versioned, identity-bearing `POL-*` prioritisation policy
      that is lexicographic, non-profitable and deterministically tie-broken;
    - a governed `AGD-*` agenda that retains its WHOLE considered population and
      is independent of caller ordering;
    - a bounded `QUE-*` queue that never promotes non-executable work and never
      truncates silently;
    - an `AFR-*` T0 freeze that a later result cannot rewrite;
    - restart-safe persistence, semantic dedup and fail-closed loading;
    - the five-opportunity core anti-bias demonstration;
    - the explicit "profit-like metadata cannot change priority" test;
    - the dependency-graph test and the policy-change history test;
    - no research execution, no candidate creation, canonical-70 isolation.

These are TEST FIXTURES, not production research. No experiment, backtest,
hypothesis, candidate, trade or production behaviour is executed anywhere.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from research_engine.lifecycle import (
    research_agenda as agenda_module,
    research_agenda_store as store_module,
    research_opportunity as opportunity_module,
    research_priority as priority_module,
    research_queue as queue_module,
)
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_QUESTION_COUNT,
    CANONICAL_QUESTION_IDS,
    assert_canonical_70_intact,
    canonical_inventory,
)
from research_engine.lifecycle.progressive_depth_gate import EligibilityState
from research_engine.lifecycle.research_agenda import (
    AGENDA_FREEZE_ID_PREFIX,
    RESEARCH_AGENDA_ID_PREFIX,
    AgendaFreeze,
    ResearchAgenda,
    ResearchAgendaValidationError,
    is_agenda_freeze_identity,
    is_research_agenda_identity,
)
from research_engine.lifecycle.research_agenda_store import (
    STORE_FORMAT,
    ResearchAgendaStore,
)
from research_engine.lifecycle.research_opportunity import (
    RESEARCH_OPPORTUNITY_ID_PREFIX,
    ConfirmationRequirement,
    CostBand,
    DataAvailability,
    EvidentialInsufficiency,
    ExplanationDiscrimination,
    InformationBand,
    InformationValue,
    ObservationRequirement,
    OpportunityState,
    OpportunitySubjectKind,
    ProspectiveWait,
    QuestionResolution,
    ResearchAnswerability,
    ResearchCost,
    ResearchDependencyCycleError,
    ResearchOpportunity,
    ResearchOpportunityNamespaceViolation,
    ResearchOpportunityValidationError,
    assert_acyclic_dependencies,
    is_research_opportunity_identity,
    opportunity_state_from_eligibility,
    unlock_map,
    unmet_dependencies,
)
from research_engine.lifecycle.research_priority import (
    POLICY_INFORMATION_FIRST_V1,
    POLICY_LEXICOGRAPHIC_V1,
    PRIORITY_POLICY_ID_PREFIX,
    PriorityAssessment,
    PriorityKey,
    PrioritisationPolicy,
    ResearchPriorityError,
    UnknownPriorityCriterionError,
    built_in_policy,
    is_priority_policy_identity,
    order_opportunities,
)
from research_engine.lifecycle.research_queue import (
    EXCLUSION_CAPACITY,
    EXCLUSION_NOT_EXECUTABLE,
    RESEARCH_QUEUE_ID_PREFIX,
    ResearchQueue,
    ResearchQueueValidationError,
    is_research_queue_identity,
)
from research_engine.registry.research_question_registry import REGISTRY

EVIDENCE_BOUNDARY = "2026-09-25T00:00:00Z"
T0 = "2026-09-25T09:00:00Z"

#: Five REAL canonical questions from the frozen registry.
CANONICAL_IDS = tuple(question.id for question in REGISTRY)[:5]



# ═══ Fixtures: governed opportunity construction ═══════════════════════════


def _info(
        state: QuestionResolution = QuestionResolution.UNRESOLVED,
        insufficiency: EvidentialInsufficiency = EvidentialInsufficiency.HIGH,
        discrimination: ExplanationDiscrimination = (
            ExplanationDiscrimination.DISCRIMINATES_COMPETING_EXPLANATIONS),
        answerability: ResearchAnswerability = (
            ResearchAnswerability.ANSWERABLE_WITH_FROZEN_EVIDENCE),
) -> InformationValue:
    return InformationValue.create(
        question_state=state,
        evidential_insufficiency=insufficiency,
        explanation_discrimination=discrimination,
        answerability=answerability,
        evidence_reference="finding:finding-42",
    )


def _cost(
        availability: DataAvailability = DataAvailability.AVAILABLE,
        observations: ObservationRequirement = ObservationRequirement.NONE,
        waiting: ProspectiveWait = ProspectiveWait.NONE,
        confirmation: ConfirmationRequirement = ConfirmationRequirement.NONE,
        alternatives: int | None = 1,
        depth: int | None = 0,
) -> ResearchCost:
    return ResearchCost.create(
        data_availability=availability,
        additional_observations=observations,
        prospective_waiting=waiting,
        confirmation_requirement=confirmation,
        alternative_count=alternatives,
        dependency_depth=depth,
    )


def _opportunity(
        subject_ref: str,
        state: OpportunityState,
        *,
        subject_kind: OpportunitySubjectKind = (
            OpportunitySubjectKind.CANONICAL_QUESTION),
        information: InformationValue | None = None,
        cost: ResearchCost | None = None,
        reason_codes: tuple[str, ...] = (),
        depends_on: tuple[str, ...] = (),
        unlocks: tuple[str, ...] = (),
        search_provenance=None,
        **overrides,
) -> ResearchOpportunity:
    return ResearchOpportunity.create(
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        state=state,
        information=information if information is not None else _info(),
        cost=cost if cost is not None else _cost(),
        evidence_boundary=EVIDENCE_BOUNDARY,
        reason_codes=reason_codes,
        depends_on=depends_on,
        unlocks=unlocks,
        search_provenance=search_provenance,
        **overrides,
    )


def _agenda(population, policy: PrioritisationPolicy = POLICY_LEXICOGRAPHIC_V1,
            **kwargs) -> ResearchAgenda:
    return ResearchAgenda.build(
        policy, population, evidence_boundary=EVIDENCE_BOUNDARY, **kwargs)


def _force_depends_on(item: ResearchOpportunity, dependency: str):
    """
    Bypass construction to inject a dependency edge, for cycle tests only.

    A genuine identity cycle is a fixed point of the identity derivation, so it
    cannot be built through the public constructor. The cycle guard is defensive
    code that must still be proven, so the edge is injected directly. Nothing
    else in this test module bypasses construction.
    """
    forced = copy.copy(item)
    object.__setattr__(forced, "depends_on", (dependency,))
    return forced


# ═══ The five legitimate research opportunities ════════════════════════════


@pytest.fixture
def five():
    """
    FIVE legitimate, distinct research opportunities with honest, mixed states:

        1. READY       -- answerable now, high value of learning, cheap;
        2. READY       -- the same, competing on information vs. dependency scope;
        3. BLOCKED     -- structurally impossible: no evidence freeze;
        4. WAITING     -- structurally impossible: needs more observations;
        5. REFUSED     -- terminally refused by the governed depth gate.

    All five are legitimate governed research. All five are retained.
    """
    first = _opportunity(CANONICAL_IDS[0], OpportunityState.READY)
    second = _opportunity(
        CANONICAL_IDS[1], OpportunityState.READY,
        information=_info(
            insufficiency=EvidentialInsufficiency.PARTIAL,
            discrimination=ExplanationDiscrimination.SINGLE_HYPOTHESIS_ONLY),
        cost=_cost(alternatives=3))
    third = _opportunity(
        CANONICAL_IDS[2], OpportunityState.BLOCKED,
        reason_codes=("EVIDENCE_FREEZE_UNAVAILABLE",))
    fourth = _opportunity(
        CANONICAL_IDS[3], OpportunityState.WAITING_DATA,
        reason_codes=("ADDITIONAL_OBSERVATIONS_REQUIRED",),
        cost=_cost(observations=ObservationRequirement.ADDITIONAL_REQUIRED))
    fifth = _opportunity(
        CANONICAL_IDS[4], OpportunityState.REFUSED,
        reason_codes=("EXPANSION_NOT_JUSTIFIED",),
        information=_info(insufficiency=EvidentialInsufficiency.LOW))
    return (first, second, third, fourth, fifth)



# ═══ 1-6. Opportunity identity, namespace, state ══════════════════════════


def test_opportunity_identity_is_deterministic(five):
    """(A) The same governed material always yields the same `ROP-*` identity."""
    first = five[0]
    again = _opportunity(CANONICAL_IDS[0], OpportunityState.READY)
    assert again.opportunity_identity == first.opportunity_identity
    assert first.opportunity_identity.startswith(RESEARCH_OPPORTUNITY_ID_PREFIX)
    assert is_research_opportunity_identity(first.opportunity_identity)


def test_timestamps_labels_and_notes_are_provenance_not_identity(five):
    """(M) Runtime metadata can never change an opportunity's identity."""
    first = five[0]
    noisy = replace(
        first, created_at="2099-01-01T00:00:00Z", label="the best idea ever",
        note="looked profitable", provenance={"runtime": "diag", "sharpe": 3.1})
    assert noisy.opportunity_identity == first.opportunity_identity
    assert noisy.semantic_identity == first.semantic_identity
    assert noisy.tie_break_key() == first.tie_break_key()


def test_opportunity_binds_to_existing_governed_identity(five):
    """(B) Every subject resolves inside an authoritative existing namespace."""
    for item in five:
        assert item.subject_ref in canonical_inventory()
        assert item.subject_kind is OpportunitySubjectKind.CANONICAL_QUESTION


def test_free_form_text_is_never_a_governed_opportunity():
    """(B) An opportunity can never be created from an ungoverned idea."""
    with pytest.raises(ResearchOpportunityNamespaceViolation, match="not a governed"):
        _opportunity("looks like a profitable pattern", OpportunityState.READY)
    with pytest.raises(ResearchOpportunityNamespaceViolation, match="not a governed"):
        _opportunity(
            "Q71", OpportunityState.READY,
            subject_kind=OpportunitySubjectKind.GOVERNED_DIMENSION)


def test_wave2_states_are_reused_not_renamed():
    """(D) The Wave 5 vocabulary maps onto the EXISTING Wave 2 lifecycle."""
    for state in (EligibilityState.PERMIT, EligibilityState.WAITING_DATA,
                  EligibilityState.BLOCKED, EligibilityState.REFUSE):
        mapped = opportunity_state_from_eligibility(state)
        assert mapped.eligibility_state() is state
    assert opportunity_state_from_eligibility(EligibilityState.PERMIT) is (
        OpportunityState.READY)
    # The two agenda-layer-only states have no Wave 2 equivalent.
    assert OpportunityState.DEFERRED.eligibility_state() is None
    assert OpportunityState.COMPLETE.eligibility_state() is None


def test_non_ready_state_requires_a_governed_reason_code():
    """(D) A silent "not now" is not auditable and is rejected."""
    with pytest.raises(ResearchOpportunityValidationError, match="reason code"):
        _opportunity(CANONICAL_IDS[0], OpportunityState.BLOCKED)
    with pytest.raises(ResearchOpportunityValidationError, match="reason code"):
        _opportunity(
            CANONICAL_IDS[0], OpportunityState.READY, reason_codes=("NOT_NOW",))


def test_a_resolved_question_cannot_be_ready_research():
    """(D) Re-queuing an already-resolved question would be re-litigation."""
    with pytest.raises(ResearchOpportunityValidationError, match="RESOLVED"):
        _opportunity(
            CANONICAL_IDS[0], OpportunityState.READY,
            information=_info(state=QuestionResolution.RESOLVED))


def test_a_refused_opportunity_is_not_also_dependency_blocked():
    """(D) Refusal is terminal; it is not a euphemism for "waiting"."""
    with pytest.raises(ResearchOpportunityValidationError, match="terminally refused"):
        _opportunity(
            CANONICAL_IDS[0], OpportunityState.REFUSED,
            reason_codes=("EXPANSION_NOT_JUSTIFIED",),
            depends_on=("evidence:holdout-9",))



# ═══ 7-8. Information value and cost are honest and separate ═══════════════


def test_information_value_is_ordinal_and_table_derived():
    """(F) Information value is a published table lookup, not a score."""
    high = _info()
    assert high.band() is InformationBand.HIGH
    assert high.rank() < _info(insufficiency=EvidentialInsufficiency.LOW).rank()
    # No floating-point composite anywhere: the ordinals are small integers.
    assert all(isinstance(value, int) for value in (high.rank(),))


def test_information_value_never_reads_profitability():
    """(M) There is no profitability input on the information-value model."""
    forbidden = {
        "profit", "expected_pnl", "sharpe", "win_rate", "expectancy",
        "drawdown", "p_value", "profit_probability", "score", "weight"}
    assert forbidden.isdisjoint(set(InformationValue.__dataclass_fields__))  # type: ignore[attr-defined]
    assert forbidden.isdisjoint(set(ResearchCost.__dataclass_fields__))      # type: ignore[attr-defined]


def test_cost_is_separate_from_information_value(five):
    """(F) Value and cost are two independent, separately inspectable concepts."""
    item = five[1]
    assert isinstance(item.information, InformationValue)
    assert isinstance(item.cost, ResearchCost)
    # The very same information value can carry a very different cost.
    expensive = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY,
        information=item.information,
        cost=_cost(observations=ObservationRequirement.ADDITIONAL_REQUIRED,
                   waiting=ProspectiveWait.REQUIRED, alternatives=5))
    assert expensive.information.band() == item.information.band()
    assert expensive.cost.band() is not item.cost.band()


def test_unknown_cost_is_never_treated_as_cheap():
    """(G) UNKNOWN cost is the WORST band, never silently zero."""
    cheap = _cost()
    assert cheap.band() is CostBand.CHEAP
    unknown = _cost(availability=DataAvailability.UNKNOWN)
    assert unknown.band() is CostBand.UNKNOWN
    assert unknown.is_unknown()
    assert unknown.rank() > cheap.rank()
    # Every single UNKNOWN factor is enough, and is named.
    for partial in (
            _cost(observations=ObservationRequirement.UNKNOWN),
            _cost(waiting=ProspectiveWait.UNKNOWN),
            _cost(confirmation=ConfirmationRequirement.UNKNOWN),
            _cost(alternatives=None),
            _cost(depth=None)):
        assert partial.band() is CostBand.UNKNOWN, partial
    assert "DATA_AVAILABILITY" in unknown.unknown_factors()
    assert "ALTERNATIVE_COUNT" in _cost(alternatives=None).unknown_factors()


def test_unknown_information_value_ranks_last():
    """(G) UNKNOWN value of learning is explicit and ranks after every known band."""
    unknown = _info(answerability=ResearchAnswerability.NOT_YET_DETERMINABLE)
    assert unknown.band() is InformationBand.UNKNOWN
    assert unknown.is_unknown()
    assert unknown.rank() > _info().rank()
    assert unknown.rank() > _info(insufficiency=EvidentialInsufficiency.LOW).rank()


# ═══ 9-10. Dependencies are explicit and cycles fail closed ════════════════


def test_dependency_graph_is_preserved_and_derived(five):
    """(H) Unlock scope is DERIVED from the graph, never merely asserted."""
    first, second = five[0], five[1]
    blocked = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY,
        depends_on=(first.opportunity_identity,))
    dependent = _opportunity(
        CANONICAL_IDS[1], OpportunityState.READY,
        depends_on=(blocked.opportunity_identity,))
    population = (first, second, blocked, dependent)
    unlocks = unlock_map(population)
    assert unlocks[first.opportunity_identity] == (blocked.opportunity_identity,)
    assert unlocks[blocked.opportunity_identity] == (dependent.opportunity_identity,)
    assert unlocks[dependent.opportunity_identity] == ()


def test_circular_dependencies_fail_closed(five):
    """(F) A cycle can never become executable, so it is rejected."""
    left, right = five[0], five[1]
    # Injected edges: left -> right and right -> left. A genuine identity cycle
    # is a fixed point of the identity derivation and cannot be constructed
    # through the public API, so the guard is exercised directly.
    cyclic_left = _force_depends_on(left, right.opportunity_identity)
    cyclic_right = _force_depends_on(right, left.opportunity_identity)
    with pytest.raises(ResearchDependencyCycleError, match="cycle"):
        assert_acyclic_dependencies((cyclic_left, cyclic_right))
    # And a self-cycle is rejected too.
    with pytest.raises(ResearchDependencyCycleError, match="cycle"):
        assert_acyclic_dependencies((
            _force_depends_on(left, left.opportunity_identity),))


def test_unresolvable_reference_dependency_never_counts_as_satisfied(five):
    """(H) A dependency on work Wave 5 cannot see is never assumed complete."""
    item = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY, depends_on=("evidence:holdout-9",))
    assert unmet_dependencies(item, (item,)) == ("evidence:holdout-9",)
    agenda = _agenda((item,))
    assert agenda.executable_entries == ()
    assert agenda.blocked_entries[0].unmet_dependencies == ("evidence:holdout-9",)


def test_dangling_opportunity_dependency_fails_closed():
    """(F) An agenda may not rank work whose blocker it cannot see."""
    orphan = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY,
        depends_on=("ROP-0000000000000000",))
    with pytest.raises(ResearchOpportunityValidationError, match="not present"):
        _agenda((orphan,))


def test_claimed_unlock_must_match_the_graph(five):
    """(H) A researcher cannot claim their work unblocks something it does not."""
    liar = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY,
        unlocks=("ROP-0000000000000000",))
    with pytest.raises(ResearchOpportunityValidationError, match="claims to unlock"):
        _agenda((liar, five[1]))



# ═══ 11-14. Prioritisation policy is explicit, versioned and deterministic ══


def test_policy_identity_is_versioned_and_identity_bearing():
    """(G) The rule is itself an identity, so changing it changes history."""
    policy = POLICY_LEXICOGRAPHIC_V1
    assert policy.policy_identity.startswith(PRIORITY_POLICY_ID_PREFIX)
    assert is_priority_policy_identity(policy.policy_identity)
    assert built_in_policy(policy.policy_version) is policy
    # A renamed version is a different policy, not a relabelled one. The derived
    # identity fields must be cleared, because a presented identity that no
    # longer matches the material is itself a fail-closed error.
    relabelled = replace(policy, policy_version="W5-LEX-V2",
                         semantic_identity="", policy_identity="")
    assert relabelled.policy_identity != policy.policy_identity
    # Carrying the OLD identity over the NEW material is rejected outright.
    with pytest.raises(ResearchPriorityError, match="semantic identity"):
        replace(policy, policy_version="W5-LEX-V2")
    # A reordered criterion list is a different policy too. The leading
    # executability gate may never be moved -- that is the point of it.
    swapped = list(policy.criteria)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    reordered = PrioritisationPolicy.create(
        policy_version=policy.policy_version, criteria=tuple(swapped))
    assert reordered.policy_identity != policy.policy_identity
    with pytest.raises(ResearchPriorityError, match="must begin with"):
        PrioritisationPolicy.create(
            policy_version=policy.policy_version,
            criteria=(policy.criteria[1], policy.criteria[0], *policy.criteria[2:]))


def test_policy_rejects_a_non_governed_criterion():
    """(G) A caller cannot invent a criterion -- notably not "profitability"."""
    with pytest.raises(UnknownPriorityCriterionError, match="not a governed"):
        PrioritisationPolicy.create(
            policy_version="W5-HACK-V1",
            criteria=(PriorityKey.EXECUTABILITY, "EXPECTED_PROFIT"))
    with pytest.raises(UnknownPriorityCriterionError, match="not a governed"):
        PrioritisationPolicy.create(
            policy_version="W5-HACK-V2",
            criteria=(PriorityKey.EXECUTABILITY, "sharpe"))


def test_policy_must_lead_with_the_executability_gate():
    """(G/L) Non-executable work can never outrank executable work."""
    with pytest.raises(ResearchPriorityError, match="must begin with"):
        PrioritisationPolicy.create(
            policy_version="W5-BAD-V1",
            criteria=(PriorityKey.INFORMATION_BAND, PriorityKey.EXECUTABILITY))
    with pytest.raises(ResearchPriorityError, match="at least one criterion"):
        PrioritisationPolicy.create(policy_version="W5-EMPTY-V1", criteria=())
    with pytest.raises(ResearchPriorityError, match="may not repeat"):
        PrioritisationPolicy.create(
            policy_version="W5-DUP-V1",
            criteria=(PriorityKey.EXECUTABILITY, PriorityKey.EXECUTABILITY))


def test_policy_ordering_is_stable_under_input_order(five):
    """(B/J) Caller order cannot alter the ordering, the identity, or the tie-break."""
    forward = order_opportunities(POLICY_LEXICOGRAPHIC_V1, five)
    backward = order_opportunities(POLICY_LEXICOGRAPHIC_V1, tuple(reversed(five)))
    assert [item.opportunity_identity for item, _ in forward] == [
        item.opportunity_identity for item, _ in backward]
    assert all(a.ordinals() == b.ordinals() for (_, a), (_, b) in
               zip(forward, backward))


def test_ties_are_resolved_by_semantic_identity_not_randomness(five):
    """(J) Ties fall to the opportunity's own identity -- never a coin flip."""
    # Two structurally indistinguishable READY opportunities.
    twin_one = _opportunity(CANONICAL_IDS[0], OpportunityState.READY)
    twin_two = _opportunity(CANONICAL_IDS[1], OpportunityState.READY)
    # Align everything but the subject so only the tie-break can separate them.
    aligned = [
        replace(twin_one, information=twin_two.information, cost=twin_two.cost),
        replace(twin_two, information=twin_two.information, cost=twin_two.cost),
    ]
    ordered = order_opportunities(POLICY_LEXICOGRAPHIC_V1, aligned)
    assert ordered[0][1].ordinals() == ordered[1][1].ordinals()
    expected = sorted(item.opportunity_identity for item in aligned)
    assert [item.opportunity_identity for item, _ in ordered] == expected
    # Re-ordering the same population gives the identical result.
    again = order_opportunities(POLICY_LEXICOGRAPHIC_V1, tuple(reversed(aligned)))
    assert [item.opportunity_identity for item, _ in again] == expected


def test_every_priority_component_remains_visible(five):
    """(G) No opaque composite score: every ordinal is inspectable per item."""
    agenda = _agenda(five)
    criteria = POLICY_LEXICOGRAPHIC_V1.ordered_criteria()
    for entry in agenda.entries:
        names = [name for name, _ in entry.assessment.components]
        assert names == list(criteria)
        assert entry.assessment.ordinals() == agenda.entry_for(
            entry.opportunity_identity).assessment.ordinals()
    # The rationale is carried in the persisted record, not recomputed on read.
    payload = agenda.to_dict()
    assert payload["ordering"][0]["criteria"][0]["criterion"] == "EXECUTABILITY"



# ═══ 15-18. The agenda retains the whole population ══════════════════════


def test_agenda_retains_every_considered_opportunity(five):
    """(A/H) All five survive; nothing is silently dropped."""
    agenda = _agenda(five)
    assert len(agenda.population) == 5
    assert len(agenda.entries) == 5
    assert sorted(agenda.ordered_identities) == sorted(
        item.opportunity_identity for item in five)
    # And every one is classified, executable or not.
    assert (len(agenda.executable_entries) + len(agenda.blocked_entries)) == 5


def test_agenda_identity_includes_the_whole_population(five):
    """(H/I) Dropping an inconvenient lower-ranked item changes the identity."""
    full = _agenda(five)
    # Drop the LAST-considered item -- the "inconvenient" one.
    dropped = _agenda(five[:-1])
    assert dropped.agenda_identity != full.agenda_identity
    assert len(dropped.population) == 4
    # Dropping any other item changes it too.
    for index in range(4):
        reduced = _agenda(five[:index] + five[index + 1:])
        assert reduced.agenda_identity != full.agenda_identity


def test_agenda_rejects_a_population_with_a_dropped_entry(five):
    """(H) A truncated agenda is unrepresentable, not merely discouraged."""
    agenda = _agenda(five)
    with pytest.raises(ResearchAgendaValidationError, match="WHOLE considered"):
        ResearchAgenda(
            policy=agenda.policy,
            evidence_boundary=agenda.evidence_boundary,
            entries=agenda.entries[:-1],
            population=agenda.population,
        )


def test_agenda_identity_and_order_ignore_caller_order(five):
    """(J) Caller input order cannot alter agenda semantics."""
    forward = _agenda(five)
    backward = _agenda(tuple(reversed(five)))
    shuffled = _agenda((five[3], five[0], five[4], five[1], five[2]))
    assert forward.agenda_identity == backward.agenda_identity == shuffled.agenda_identity
    assert forward.ordered_identities == backward.ordered_identities
    assert forward.ordered_identities == shuffled.ordered_identities
    assert forward.semantic_identity == shuffled.semantic_identity


def test_agenda_timestamps_and_notes_are_provenance_only(five):
    """(L) A semantically identical agenda replays to the same identity."""
    original = _agenda(five, created_at=T0)
    later = _agenda(five, created_at="2099-01-01T00:00:00Z", note="revised opinion")
    assert later.agenda_identity == original.agenda_identity
    assert later.created_at != original.created_at


def test_agenda_records_the_policy_in_provenance(five):
    """(G) The rule that produced the order is visible on the record."""
    agenda = _agenda(five, POLICY_INFORMATION_FIRST_V1)
    assert agenda.policy.policy_version == "W5-INFO-FIRST-V1"
    assert agenda.to_dict()["ordered_criteria"] == list(
        POLICY_INFORMATION_FIRST_V1.ordered_criteria())
    assert agenda.to_dict()["policy_identity"] == (
        POLICY_INFORMATION_FIRST_V1.policy_identity)


def test_agenda_requires_a_population_and_a_boundary(five):
    """(H) An agenda over nothing would be indistinguishable from no agenda."""
    with pytest.raises(ResearchAgendaValidationError, match="at least one"):
        _agenda(())
    with pytest.raises(ResearchAgendaValidationError, match="evidence_boundary"):
        ResearchAgenda.build(POLICY_LEXICOGRAPHIC_V1, five, evidence_boundary="  ")


def test_agenda_rejects_a_tampered_ordering(five):
    """(H) The stored ordering must be the ordering the policy produces."""
    agenda = _agenda(five)
    swapped = list(agenda.entries)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ResearchAgendaValidationError, match="dense, ordered"):
        ResearchAgenda(
            policy=agenda.policy,
            evidence_boundary=agenda.evidence_boundary,
            entries=tuple(swapped),
            population=agenda.population,
        )



# ═══ 19-22. Bounded queue: deterministic, executable-only, never silent ════


def test_queue_admits_only_structurally_executable_work(five):
    """(D/E/L) READY work is queued; BLOCKED/WAITING/REFUSED never is."""
    agenda = _agenda(five)
    queue = ResearchQueue.build(agenda, capacity=5)
    assert {entry.state for entry in queue.entries} <= {"READY"}
    assert all(entry.executable for entry in queue.entries)
    assert {item.state for item in agenda.blocked_entries} == {
        OpportunityState.BLOCKED.value, OpportunityState.WAITING_DATA.value,
        OpportunityState.REFUSED.value}
    # Every non-ready item is retained as a recorded exclusion.
    assert len(queue.non_executable_exclusions) == 3
    assert {item.state for item in queue.non_executable_exclusions} == {
        "BLOCKED", "WAITING_DATA", "REFUSED"}
    assert all(item.reason == EXCLUSION_NOT_EXECUTABLE
               for item in queue.non_executable_exclusions)


def test_queue_is_deterministic_and_ignores_caller_order(five):
    """(K) The bounded queue is a deterministic function of the agenda."""
    forward = ResearchQueue.build(_agenda(five), capacity=2)
    backward = ResearchQueue.build(_agenda(tuple(reversed(five))), capacity=2)
    assert forward.queue_identity == backward.queue_identity
    assert forward.queued_identities == backward.queued_identities
    assert forward.queue_identity.startswith(RESEARCH_QUEUE_ID_PREFIX)
    assert is_research_queue_identity(forward.queue_identity)
    # A third build reproduces it again.
    assert ResearchQueue.build(_agenda(five), capacity=2).queue_identity == (
        forward.queue_identity)


def test_queue_capacity_change_is_provenance_visible(five):
    """(M) Changing the capacity rule is visible in identity and provenance."""
    small = ResearchQueue.build(_agenda(five), capacity=1)
    large = ResearchQueue.build(_agenda(five), capacity=5)
    assert small.queue_identity != large.queue_identity
    assert small.capacity == 1 and large.capacity == 5
    assert small.to_dict()["capacity"] == 1
    assert large.to_dict()["capacity"] == 5
    # The same agenda, two different bounded views of the SAME research.
    assert small.agenda_identity == large.agenda_identity


def test_queue_never_truncates_silently(five):
    """(M) Every item excluded by capacity is retained with its reason."""
    agenda = _agenda(five)
    executable = agenda.executable_entries
    assert len(executable) >= 2
    bounded = ResearchQueue.build(agenda, capacity=1)
    assert len(bounded.entries) == 1
    excluded = bounded.capacity_exclusions
    assert len(excluded) == len(executable) - 1
    assert {item.opportunity_identity for item in excluded} == {
        entry.opportunity_identity for entry in executable[1:]}
    assert all(item.reason == EXCLUSION_CAPACITY for item in excluded)
    # Every agenda entry is accounted for: queued OR excluded, never neither.
    accounted = set(bounded.queued_identities) | {
        item.opportunity_identity for item in bounded.exclusions}
    assert accounted == set(agenda.ordered_identities)


def test_queue_rejects_an_unbounded_or_absurd_capacity(five):
    """(M) Capacity is a governed, explicit input, never an inferred default."""
    agenda = _agenda(five)
    for bad in (0, -1, True, 2.0, "2", None):
        with pytest.raises(ResearchQueueValidationError, match="capacity"):
            ResearchQueue.build(agenda, capacity=bad)


def test_queue_rejects_a_non_executable_entry(five):
    """(L) Structurally impossible work may not be presented as actionable."""
    agenda = _agenda(five)
    blocked = next(entry for entry in agenda.entries if entry.state == "BLOCKED")
    with pytest.raises(ResearchQueueValidationError, match="may never enter"):
        ResearchQueue(
            agenda_identity=agenda.agenda_identity,
            policy_identity=agenda.policy.policy_identity,
            policy_version=agenda.policy.policy_version,
            capacity=5,
            entries=(blocked,),
            exclusions=(),
            evidence_boundary=agenda.evidence_boundary,
        )


def test_queue_asserts_it_belongs_to_its_agenda(five):
    """(K) A queue may not be reinterpreted against a different agenda."""
    first = _agenda(five)
    second = _agenda(five[:-1])
    queue = ResearchQueue.build(first, capacity=2)
    queue.assert_belongs_to(first)
    with pytest.raises(ResearchQueueValidationError, match="binds agenda"):
        queue.assert_belongs_to(second)



# ═══ 23-25. T0 agenda freeze ═══════════════════════════════════════════════


def test_agenda_freeze_is_deterministic_and_timestamp_free(five):
    """(N) Re-freezing identical agenda state reproduces the same identity."""
    agenda = _agenda(five)
    early = AgendaFreeze.freeze(agenda, capacity=2, frozen_at=T0)
    late = AgendaFreeze.freeze(agenda, capacity=2, frozen_at="2099-01-01T00:00:00Z")
    assert early.freeze_identity == late.freeze_identity
    assert early.freeze_identity.startswith(AGENDA_FREEZE_ID_PREFIX)
    assert is_agenda_freeze_identity(early.freeze_identity)
    assert early.frozen_at != late.frozen_at


def test_agenda_freeze_binds_the_whole_t0_decision(five):
    """(N) The T0 record binds policy, population, order, state and capacity."""
    agenda = _agenda(five)
    freeze = AgendaFreeze.freeze(agenda, capacity=2, frozen_at=T0)
    assert freeze.agenda_identity == agenda.agenda_identity
    assert freeze.policy_identity == agenda.policy.policy_identity
    assert freeze.ordered_identities == agenda.ordered_identities
    assert sorted(freeze.considered_population) == sorted(
        item.opportunity_identity for item in five)
    assert freeze.executable_identities == tuple(
        entry.opportunity_identity for entry in agenda.executable_entries)
    assert freeze.capacity == 2
    assert freeze.evidence_boundary == agenda.evidence_boundary
    # Dependency, information and cost state are all bound, per item.
    assert set(freeze.dependency_state) == set(freeze.considered_population)
    assert set(freeze.information_value_state) == set(freeze.considered_population)
    assert set(freeze.cost_state) == set(freeze.considered_population)
    assert set(freeze.unknown_cost_factors) == set(freeze.considered_population)
    assert freeze.information_value_state[five[0].opportunity_identity] == "HIGH"


def test_a_later_result_cannot_rewrite_the_t0_freeze(five):
    """(N) A new result is a NEW agenda/freeze, never an edit of the old one."""
    original_agenda = _agenda(five)
    original_freeze = AgendaFreeze.freeze(original_agenda, capacity=2, frozen_at=T0)
    # A later state transition: the REFUSED work is now formally COMPLETE.
    later_population = (
        five[0], five[1], five[2], five[3],
        _opportunity(CANONICAL_IDS[4], OpportunityState.COMPLETE,
                     reason_codes=("ANSWERED_BY_GOVERNED_RESEARCH",)))
    later_agenda = _agenda(later_population)
    later_freeze = AgendaFreeze.freeze(later_agenda, capacity=2, frozen_at=T0)
    # The historical record is untouched, and the new state is a new identity.
    assert later_freeze.freeze_identity != original_freeze.freeze_identity
    assert later_freeze.agenda_identity != original_agenda.agenda_identity
    assert original_freeze.ordered_identities == _agenda(five).ordered_identities
    assert original_freeze.executable_identities == tuple(
        entry.opportunity_identity for entry in original_agenda.executable_entries)


def test_agenda_freeze_must_belong_to_its_agenda(five):
    """(N) A T0 snapshot may not be re-pointed at a different agenda."""
    first = _agenda(five)
    second = _agenda(five[:-1])
    freeze = AgendaFreeze.freeze(first, capacity=2, frozen_at=T0)
    freeze.assert_belongs_to(first)
    with pytest.raises(ResearchAgendaValidationError, match="binds agenda"):
        freeze.assert_belongs_to(second)


def test_agenda_freeze_capacity_change_changes_the_freeze(five):
    """(N) The capacity rule in force at T0 is itself part of the T0 record."""
    agenda = _agenda(five)
    one = AgendaFreeze.freeze(agenda, capacity=1, frozen_at=T0)
    five_capacity = AgendaFreeze.freeze(agenda, capacity=5, frozen_at=T0)
    assert one.freeze_identity != five_capacity.freeze_identity
    assert AgendaFreeze.freeze(agenda, capacity=None, frozen_at=T0).capacity is None



# ═══ 26-28. Restart-safe persistence, dedup, fail-closed loading ═══════════


def test_restart_reproduces_identities_and_order(five, tmp_path):
    """(L/O) A restart reproduces the agenda, queue and freeze exactly."""
    path = tmp_path / "agenda.json"
    agenda = _agenda(five, created_at=T0)
    queue = ResearchQueue.build(agenda, capacity=2, created_at=T0)
    freeze = AgendaFreeze.freeze(agenda, capacity=2, frozen_at=T0)
    store = ResearchAgendaStore(path)
    store.register_agenda(agenda)
    store.register_queue(queue)
    store.register_freeze(freeze)

    for _ in range(2):
        reloaded = ResearchAgendaStore(path)
        restored = reloaded.get_agenda(agenda.agenda_identity)
        assert restored is not None
        assert restored.agenda_identity == agenda.agenda_identity
        assert restored.ordered_identities == agenda.ordered_identities
        assert restored.policy.policy_identity == agenda.policy.policy_identity
        assert [item.opportunity_identity for item in restored.population] == [
            item.opportunity_identity for item in agenda.population]
        restored_queue = reloaded.get_queue(queue.queue_identity)
        assert restored_queue.queued_identities == queue.queued_identities
        assert len(restored_queue.exclusions) == len(queue.exclusions)
        assert restored_queue.capacity == queue.capacity
        restored_freeze = reloaded.get_freeze(freeze.freeze_identity)
        assert restored_freeze.freeze_identity == freeze.freeze_identity
        assert restored_freeze.ordered_identities == freeze.ordered_identities
        assert restored_freeze.information_value_state == freeze.information_value_state


def test_identical_state_deduplicates_and_preserves_creation_provenance(
        five, tmp_path):
    """(O) Re-deriving an agenda is not re-creating it."""
    path = tmp_path / "agenda.json"
    original = _agenda(five, created_at=T0)
    store = ResearchAgendaStore(path)
    store.register_agenda(original)
    assert len(store) == 1

    later = _agenda(five, created_at="2099-01-01T00:00:00Z")
    for _ in range(3):
        persisted = ResearchAgendaStore(path).register_agenda(later)
        assert persisted.created_at == T0
        assert persisted.agenda_identity == original.agenda_identity
    assert len(ResearchAgendaStore(path)) == 1


def test_policy_change_creates_new_history_rather_than_rewriting(five, tmp_path):
    """(P) Neither agenda overwrites the other; both remain reloadable."""
    path = tmp_path / "agenda.json"
    under_first = _agenda(five, POLICY_LEXICOGRAPHIC_V1, created_at=T0)
    under_second = _agenda(five, POLICY_INFORMATION_FIRST_V1, created_at=T0)
    assert under_first.agenda_identity != under_second.agenda_identity

    store = ResearchAgendaStore(path)
    store.register_agenda(under_first)
    store.register_agenda(under_second)
    assert len(store) == 2
    assert {policy.policy_version for policy in store.policies()} == {
        "W5-LEX-V1", "W5-INFO-FIRST-V1"}

    reloaded = ResearchAgendaStore(path)
    first = reloaded.get_agenda(under_first.agenda_identity)
    second = reloaded.get_agenda(under_second.agenda_identity)
    assert first is not None and second is not None
    assert first.policy.policy_version == "W5-LEX-V1"
    assert second.policy.policy_version == "W5-INFO-FIRST-V1"
    # The OLD agenda is exactly the original, not the new interpretation.
    assert first.to_dict() == under_first.to_dict()
    assert second.to_dict() == under_second.to_dict()
    assert first.to_dict()["policy_version"] == "W5-LEX-V1"
    assert second.to_dict()["policy_version"] == "W5-INFO-FIRST-V1"



def test_corrupt_or_contradictory_persisted_agenda_fails_closed(five, tmp_path):
    """(O) Corruption is never silently repaired or reinterpreted."""
    from research_engine.lifecycle.research_opportunity import ResearchAgendaError

    good = ResearchAgendaStore(tmp_path / "seed.json").register_agenda(
        _agenda(five)).to_dict()
    path = tmp_path / "agenda.json"

    def write(document):
        path.write_text(json.dumps(document), encoding="utf-8")

    # Wrong document format.
    write({"format": "something_else", "agendas": [good]})
    with pytest.raises(ResearchAgendaError, match="unknown research agenda store"):
        ResearchAgendaStore(path)

    # Unsupported schema version. Every section marker must be present and equal.
    seed = ResearchAgendaStore(tmp_path / "seed.json")._document()

    def document(**overrides):
        base = dict(seed)
        base.update(overrides)
        return base

    write(document(agenda_schema_version=99))
    with pytest.raises(ResearchAgendaError, match="agenda_schema_version"):
        ResearchAgendaStore(path)
    # A document missing a version marker is incomplete, not merely unsupported.
    write({"format": STORE_FORMAT, "agendas": [good]})
    with pytest.raises(ResearchAgendaError, match="missing 'opportunity_schema_version'"):
        ResearchAgendaStore(path)

    # A missing mandatory field.
    broken = dict(good)
    broken.pop("ordering")
    write(document(agendas=[broken]))
    with pytest.raises(ResearchAgendaValidationError, match="missing fields"):
        ResearchAgendaStore(path)

    # A dropped opportunity: the stored ordering no longer covers the population.
    tampered = json.loads(json.dumps(good))
    tampered["population"] = tampered["population"][:-1]
    write(document(agendas=[tampered]))
    with pytest.raises(ResearchAgendaValidationError):
        ResearchAgendaStore(path)

    # A tampered derived band that no longer matches the information value.
    projection = json.loads(json.dumps(good))
    projection["population"][0]["information_value_provenance"]["derived_band"] = "HIGH"
    projection["population"][0]["information_value"]["answerability"] = (
        "NOT_YET_DETERMINABLE")
    write(document(agendas=[projection]))
    with pytest.raises(ResearchAgendaValidationError):
        ResearchAgendaStore(path)

    # Unreadable content.
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ResearchAgendaError, match="unreadable"):
        ResearchAgendaStore(path)

    # A persisted identity that no longer matches its content.
    mismatched = json.loads(json.dumps(good))
    mismatched["agenda_identity"] = "AGD-0000000000000000"
    write(document(agendas=[mismatched]))
    with pytest.raises(ResearchAgendaError):
        ResearchAgendaStore(path)


def test_orphaned_freeze_and_queue_fail_closed(five, tmp_path):
    """(O) A snapshot may never outlive or precede the agenda it describes."""
    agenda = _agenda(five)
    freeze = AgendaFreeze.freeze(agenda, capacity=2, frozen_at=T0)
    queue = ResearchQueue.build(agenda, capacity=2)

    empty = ResearchAgendaStore(tmp_path / "empty.json")
    with pytest.raises(ResearchAgendaValidationError, match="already-recorded agenda"):
        empty.register_freeze(freeze)
    with pytest.raises(ResearchQueueValidationError, match="already-recorded agenda"):
        empty.register_queue(queue)

    path = tmp_path / "agenda.json"
    document = {"format": STORE_FORMAT, "agenda_schema_version": 1,
                "opportunity_schema_version": 1, "policy_schema_version": 1,
                "queue_schema_version": 1, "agendas": [], "opportunities": [],
                "policies": [], "freezes": [freeze.to_dict()], "queues": []}
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResearchAgendaValidationError, match="never outlive its agenda"):
        ResearchAgendaStore(path)
    document["freezes"] = []
    document["queues"] = [queue.to_dict()]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResearchQueueValidationError, match="never outlive its agenda"):
        ResearchAgendaStore(path)


def test_store_has_no_import_time_writes(tmp_path):
    """(O) Importing Wave 5 writes nothing; registration is explicit."""
    import subprocess
    import sys

    target = tmp_path / "should_not_exist.json"
    result = subprocess.run(
        [sys.executable, "-c",
         "import research_engine.lifecycle.research_opportunity;"
         "import research_engine.lifecycle.research_priority;"
         "import research_engine.lifecycle.research_agenda;"
         "import research_engine.lifecycle.research_queue;"
         "import research_engine.lifecycle.research_agenda_store as s;"
         f"s.ResearchAgendaStore({str(target)!r});print('imported')"],
        capture_output=True, text=True, cwd=str(Path.cwd()))
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
    assert not target.exists()



# ═══ The dependency-focused test (A blocks B and C; D independent; E waiting) ═


def test_dependency_focused_scenario():
    """
    Research A blocks B and C; D is independent; E is waiting for more data.

    No research is executed and nothing is marked COMPLETE by running it. The
    scenario is proven with fixtures and state transitions only.
    """
    a = _opportunity(CANONICAL_IDS[0], OpportunityState.READY)
    b = _opportunity(
        CANONICAL_IDS[1], OpportunityState.READY,
        depends_on=(a.opportunity_identity,))
    c = _opportunity(
        CANONICAL_IDS[2], OpportunityState.READY,
        depends_on=(a.opportunity_identity,))
    d = _opportunity(CANONICAL_IDS[3], OpportunityState.READY)
    e = _opportunity(
        CANONICAL_IDS[4], OpportunityState.WAITING_DATA,
        reason_codes=("ADDITIONAL_OBSERVATIONS_REQUIRED",))
    population = (a, b, c, d, e)

    # (1) The dependency graph is preserved, exactly as declared.
    assert b.depends_on == (a.opportunity_identity,)
    assert c.depends_on == (a.opportunity_identity,)
    assert d.depends_on == ()
    assert unlock_map(population)[a.opportunity_identity] == tuple(sorted(
        (b.opportunity_identity, c.opportunity_identity)))

    # (2) B and C cannot bypass their dependency: A leads, B and C follow.
    agenda = _agenda(population)
    assert agenda.ordered_identities[0] == a.opportunity_identity
    assert agenda.entry_for(b.opportunity_identity).unmet_dependencies == (
        a.opportunity_identity,)
    assert agenda.entry_for(c.opportunity_identity).unmet_dependencies == (
        a.opportunity_identity,)

    # (3) Queue construction is deterministic, and admits only the unblocked.
    queue = ResearchQueue.build(agenda, capacity=5)
    assert set(queue.queued_identities) == {
        a.opportunity_identity, d.opportunity_identity}
    assert ResearchQueue.build(_agenda(tuple(reversed(population))),
                               capacity=5).queue_identity == queue.queue_identity

    # (4) E remains WAITING and is never queued. B and C are READY but blocked by
    # A, so they are recorded as non-executable too -- state alone does not
    # promote work into the queue.
    assert agenda.opportunity_for(e.opportunity_identity).state is (
        OpportunityState.WAITING_DATA)
    assert e.opportunity_identity not in queue.queued_identities
    assert {item.state for item in queue.non_executable_exclusions} == {
        "WAITING_DATA", "READY"}
    assert all(
        item.unmet_dependencies == (a.opportunity_identity,)
        for item in queue.non_executable_exclusions if item.state == "READY")

    # (5) A becoming resolved is visible in a FUTURE agenda, not a rewrite.
    # A state change is a new scientific state, so it is a new identity: the
    # later agenda is a genuinely different, later record of the same programme.
    done = _opportunity(
        CANONICAL_IDS[0], OpportunityState.COMPLETE,
        reason_codes=("ANSWERED_BY_GOVERNED_RESEARCH",))
    later_b = _opportunity(
        CANONICAL_IDS[1], OpportunityState.READY,
        depends_on=(done.opportunity_identity,))
    later_c = _opportunity(
        CANONICAL_IDS[2], OpportunityState.READY,
        depends_on=(done.opportunity_identity,))
    later = _agenda((done, later_b, later_c, d, e))
    assert later.agenda_identity != agenda.agenda_identity
    # With A resolved, B and C are unblocked and become executable.
    assert later.entry_for(later_b.opportunity_identity).unmet_dependencies == ()
    assert later.entry_for(later_c.opportunity_identity).unmet_dependencies == ()
    assert {later_b.opportunity_identity, later_c.opportunity_identity} <= {
        entry.opportunity_identity for entry in later.executable_entries}
    later_queue = ResearchQueue.build(later, capacity=5)
    assert {later_b.opportunity_identity, later_c.opportunity_identity} <= set(
        later_queue.queued_identities)
    # The historical agenda is untouched, and its queue still excludes B and C.
    assert agenda.ordered_identities[0] == a.opportunity_identity
    assert agenda.entry_for(b.opportunity_identity).unmet_dependencies == (
        a.opportunity_identity,)
    assert b.opportunity_identity not in set(queue.queued_identities)
    assert a.opportunity_identity in agenda.ordered_identities


# ═══ The priority-policy-change test ═══════════════════════════════════════


def test_two_legitimate_policies_produce_two_histories(five, tmp_path):
    """
    The same population under two legitimate policies yields two internally
    deterministic, mutually non-overwriting agendas.
    """
    path = tmp_path / "agenda.json"
    first = _agenda(five, POLICY_LEXICOGRAPHIC_V1, created_at=T0)
    second = _agenda(five, POLICY_INFORMATION_FIRST_V1, created_at=T0)

    # Each is internally deterministic.
    assert first.agenda_identity == _agenda(five, POLICY_LEXICOGRAPHIC_V1).agenda_identity
    assert second.agenda_identity == _agenda(
        five, POLICY_INFORMATION_FIRST_V1).agenda_identity

    # Policy identity is provenance-visible on both.
    assert first.to_dict()["policy_version"] == "W5-LEX-V1"
    assert second.to_dict()["policy_version"] == "W5-INFO-FIRST-V1"
    assert first.to_dict()["policy_identity"] != second.to_dict()["policy_identity"]

    # The agenda identities differ, so a rule change cannot masquerade as one
    # historical agenda.
    assert first.agenda_identity != second.agenda_identity

    # Neither overwrites the other in persistence; both remain reloadable.
    store = ResearchAgendaStore(path)
    store.register_agenda(first)
    store.register_agenda(second)
    reloaded = ResearchAgendaStore(path)
    assert len(reloaded) == 2
    assert reloaded.get_agenda(first.agenda_identity).policy.policy_version == "W5-LEX-V1"
    assert reloaded.get_agenda(
        second.agenda_identity).policy.policy_version == "W5-INFO-FIRST-V1"
    assert reloaded.get_agenda(first.agenda_identity).to_dict() == first.to_dict()



# ═══ Wave 4 integration: search/multiplicity provenance stays intact ════════


@pytest.fixture
def wave4_freeze():
    """A REAL Wave 4 search record, family and T0 selection freeze."""
    from research_engine.lifecycle.curiosity_proposal import GeneratedResearchProposal
    from research_engine.lifecycle.curiosity_signal import (
        CuriositySignal,
        CuriositySignalType,
    )
    from research_engine.lifecycle.dimension_registry import DimensionRegistry
    from research_engine.lifecycle.eligibility_evidence_freeze import (
        fingerprint_for_records,
    )
    from research_engine.lifecycle.governed_dimension import (
        DimensionCoverage,
        GovernedDimension,
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
    from research_engine.lifecycle.curiosity_generator import propose_expansion
    from research_engine.registry.research_question_models import (
        EvidenceAuthority,
        EvidenceProducer,
    )

    registry = DimensionRegistry()
    dimensions = []
    for key in ("anchor", "horizon", "volatility", "session", "seed"):
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
        dimensions.append(dimension)

    def proposal(key):
        signal = CuriositySignal.create(
            signal_type=CuriositySignalType.FINDING_EVIDENCE,
            source_ref="finding:TRG-0001", source_identity="TRG-0001",
            source_provenance={"adapter": "finding_trigger", "trigger_id": "TRG-0001"},
            target_kind="research_subject", target_ref="exit_behaviour",
            reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
            evidence_reference="finding:finding-42",
            evidence_boundary=EVIDENCE_BOUNDARY,
            dimension_ref=registry.get(key).dimension_identity)
        return propose_expansion(signal, registry)

    fingerprint = fingerprint_for_records(
        [{"market.regime": "TRENDING", "t": index} for index in range(500)],
        dataset_id="wave5_agenda_population", population="joint_eligible")
    discovery = DiscoveryPopulation(
        population_identity="population:joint_v1",
        evidence_boundary=EVIDENCE_BOUNDARY,
        dataset_fingerprint=fingerprint)

    outcomes = (
        ("anchor", AlternativeEvaluation.STATISTICALLY_EVALUATED, EligibilityState.PERMIT,
         ("NOT_SELECTED_BY_GOVERNED_PROCESS",)),
        ("horizon", AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
         EligibilityState.PERMIT, ()),
        ("volatility", AlternativeEvaluation.NOT_EVALUATED, EligibilityState.BLOCKED,
         ("EVIDENCE_FREEZE_UNAVAILABLE",)),
        ("session", AlternativeEvaluation.NOT_EVALUATED, EligibilityState.REFUSE,
         ("EXPANSION_NOT_JUSTIFIED",)),
        ("seed", AlternativeEvaluation.NOT_EVALUATED, EligibilityState.WAITING_DATA,
         ("ADDITIONAL_OBSERVATIONS_REQUIRED",)),
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
                               "governed_keys": ["anchor", "horizon", "volatility",
                                                  "session", "seed"],
                               "selection_criterion_supplied_by":
                                   "governed_research_process"},
            multiplicity_rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES,
            alternatives=alternatives),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME", created_at=T0)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=ConfirmationPolicy(
            kind=ConfirmationKind.PROSPECTIVE_AFTER_T0, reference=f"boundary:{T0}"),
        selected_at=T0)
    return record, freeze, derive_multiplicity(record), search_provenance_payload(
        record, freeze), GeneratedResearchProposal



def test_wave4_selection_becomes_governed_research_work(wave4_freeze):
    """(Q) A Wave 4 selection may become research, keeping ALL its provenance."""
    record, freeze, multiplicity, payload, _ = wave4_freeze
    work = _opportunity(
        freeze.freeze_identity, OpportunityState.READY,
        subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
        cost=_cost(alternatives=record.derived_family_size, depth=0),
        search_provenance=payload)
    # The whole Wave 4 provenance block travels with the work.
    assert work.search_provenance["search_record_id"] == record.search_identity
    assert work.search_provenance["search_family_id"] == record.family_identity
    assert work.search_provenance["selection_freeze_id"] == freeze.freeze_identity
    assert work.search_provenance["multiplicity_family_size"] == (
        record.derived_family_size)
    assert work.search_provenance["multiplicity_rule"] == (
        record.family.multiplicity_rule.value)
    assert work.search_provenance["correction_method"] == freeze.correction_method
    assert work.search_provenance["confirmation_policy"] == (
        freeze.confirmation_policy.semantic_material())
    # The alternative count flows into the honest COST representation.
    assert work.cost.alternative_count == record.derived_family_size

    agenda = _agenda((work,))
    assert agenda.executable_entries[0].opportunity_identity == (
        work.opportunity_identity)
    # And the Wave 4 records themselves are unchanged by the agenda.
    assert record.derived_family_size == 5
    assert len(record.alternatives) == 5
    assert record.family.multiplicity_rule.value == "ALL_CONSIDERED_ALTERNATIVES"
    assert freeze.multiplicity_family_size == 5
    assert multiplicity.corrected_alpha == pytest.approx(0.05 / 5)


def test_wave4_provenance_may_not_be_re_pointed_or_omitted(wave4_freeze):
    """(Q) Wave 5 consumes Wave 4 records; it never reconstructs or edits them."""
    record, freeze, _multiplicity, payload, _ = wave4_freeze
    # Omitted provenance: a Wave 4 subject may never be a bare identity.
    with pytest.raises(ResearchOpportunityValidationError, match="search_provenance"):
        _opportunity(
            freeze.freeze_identity, OpportunityState.READY,
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE)
    # Re-pointed provenance: another search's block cannot be attached.
    other = dict(payload)
    other["selection_freeze_id"] = "FRZ-0000000000000000"
    with pytest.raises(ResearchOpportunityValidationError, match="not match|re-pointed"):
        _opportunity(
            freeze.freeze_identity, OpportunityState.READY,
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
            search_provenance=other)
    # Fabricated search history: provenance on a non-Wave-4 subject is rejected.
    with pytest.raises(ResearchOpportunityValidationError, match="fabricate"):
        _opportunity(
            CANONICAL_IDS[0], OpportunityState.READY, search_provenance=payload)
    # Incomplete provenance: a missing key fails closed.
    incomplete = dict(payload)
    incomplete.pop("multiplicity_family_size")
    with pytest.raises(ResearchOpportunityValidationError, match="missing required keys"):
        _opportunity(
            freeze.freeze_identity, OpportunityState.READY,
            subject_kind=OpportunitySubjectKind.SEARCH_SELECTION_FREEZE,
            search_provenance=incomplete)
    assert record.alternatives_considered == 5


def test_wave5_does_not_alter_any_wave4_module():
    """(Q) Wave 5 is purely additive: no Wave 4 source file is modified."""
    from research_engine.lifecycle import (
        search_multiplicity,
        search_provenance,
        search_record_store,
    )
    for module in (search_provenance, search_multiplicity, search_record_store):
        source = inspect.getsource(module)
        for fragment in ("research_opportunity", "research_priority",
                         "research_agenda", "research_queue"):
            assert fragment not in source
    assert search_provenance.SEARCH_PROVENANCE_SCHEMA_VERSION == 1


def test_waves_0_to_3_identities_remain_authoritative():
    """(R) Wave 5 mints no replacement identity for any earlier wave."""
    from research_engine.lifecycle import (
        curiosity_generator,
        curiosity_proposal,
        curiosity_signal,
        dimension_registry,
        generated_research_identity,
        governed_dimension,
        progressive_depth_gate,
        research_interaction,
    )
    for module in (generated_research_identity, governed_dimension,
                   research_interaction, progressive_depth_gate,
                   curiosity_signal, curiosity_proposal, curiosity_generator):
        source = inspect.getsource(module)
        for fragment in ("research_opportunity", "research_priority",
                         "research_agenda", "research_queue"):
            assert fragment not in source
    assert generated_research_identity.GENERATED_RESEARCH_ID_PREFIX == "GEN-"
    assert governed_dimension.DIMENSION_ID_PREFIX == "DIM-"
    assert research_interaction.INTERACTION_ID_PREFIX == "IXN-"
    assert progressive_depth_gate.DECISION_ID_PREFIX == "DEC-"
    assert curiosity_proposal.CURIOSITY_PROPOSAL_ID_PREFIX == "PRP-"
    assert curiosity_signal.CURIOSITY_SIGNAL_ID_PREFIX == "CSN-"
    # The Wave 2 vocabulary Wave 5 reuses is still exactly Wave 2's.
    assert [state.value for state in EligibilityState] == [
        "PERMIT", "WAITING_DATA", "BLOCKED", "REFUSE"]
    assert dimension_registry.DimensionRegistry is not None



# ═══ Canonical-70 isolation ════════════════════════════════════════════════


def test_canonical_70_remains_exactly_seventy(five, tmp_path):
    """(S) Wave 5 uses separate namespaces and never appends to the registry."""
    from research_engine.registry.baseline_manifest import BASELINE_VERSION
    before = canonical_inventory()
    agenda = _agenda(five)
    store = ResearchAgendaStore(tmp_path / "agenda.json")
    store.register_agenda(agenda)
    store.register_agenda(_agenda(five, POLICY_INFORMATION_FIRST_V1))
    store.register_queue(ResearchQueue.build(agenda, capacity=2))

    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70
    assert len(set(canonical_inventory())) == 70
    assert canonical_inventory() == before
    assert BASELINE_VERSION == 1
    # Wave 5 identities are not canonical question IDs and never enter the
    # canonical registry APIs.
    identities = [item.opportunity_identity for item in five]
    identities += [agenda.agenda_identity,
                   ResearchQueue.build(agenda, capacity=2).queue_identity,
                   AgendaFreeze.freeze(agenda, capacity=2).freeze_identity,
                   POLICY_LEXICOGRAPHIC_V1.policy_identity]
    assert set(identities).isdisjoint(set(canonical_inventory()))


# ═══ No autonomous research execution, no production authority ══════════════


def _source(module) -> str:
    return inspect.getsource(module)


def _tree(module):
    return ast.parse(_source(module))


def _code_only(module) -> str:
    """Module source with every docstring removed, so prose may not satisfy a check."""
    tree = _tree(module)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


WAVE5_MODULES = (opportunity_module, priority_module, agenda_module,
                 queue_module, store_module)



def test_wave5_calls_no_research_execution_or_candidate_machinery():
    """(T/U) The queue is a governed ORDER of work, never permission to run it."""
    forbidden_calls = {
        "run_experiment", "execute_research", "run_research", "create_hypothesis",
        "create_candidate", "activate_candidate", "promote_candidate",
        "apply_treatment", "send_order", "place_order", "modify_strategy",
        "modify_risk", "modify_sizing", "approve", "can_promote",
        "register_candidate", "submit_order", "execute", "run",
    }
    for module in WAVE5_MODULES:
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


def test_wave5_imports_no_production_or_execution_path():
    """(V) No runner, broker, risk, sizing, config or candidate import is reachable."""
    forbidden_fragments = (
        "broker", "risk", "sizing", "production", "runner", "orchestrator",
        "experiment", "candidate", "governance_gate", "research_cycle_runner",
        "hypothesis", "treatment", "baseline_manifest", "research_question_registry",
        "config", "shadow", "counterfactual", "validation_harness",
    )
    for module in WAVE5_MODULES:
        imports: list[str] = []
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for name in imports:
            for fragment in forbidden_fragments:
                assert fragment not in name, f"{module.__name__} imports {name}"


def test_wave5_source_contains_no_profitability_ranking():
    """(M) No profitability vocabulary and no opaque score anywhere in Wave 5."""
    forbidden = (
        "expectancy", "sharpe", "drawdown", "win_rate", "profit", "p_value",
        "r_multiple", "weighted", "normalise", "normalize", "random",
    )
    for module in WAVE5_MODULES:
        code = _code_only(module)
        for token in forbidden:
            assert token not in code, f"{module.__name__} mentions {token!r}"


def test_wave5_never_enumerates_a_cartesian_search_space():
    """Wave 5 orders a SUPPLIED population; it never generates one."""
    for module in WAVE5_MODULES:
        tree = _tree(module)
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert {"product", "combinations", "permutations", "chain"}.isdisjoint(calls)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any("itertools" in name for name in imports)
        assert "DimensionRegistry" not in imports


def test_building_an_agenda_executes_nothing(five, tmp_path):
    """(N/O/P) The only thing that changes is the agenda history itself."""
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
    )
    from research_engine.lifecycle.search_record_store import SearchRecordStore

    gen_store = GeneratedResearchStore(tmp_path / "gen.json")
    search_store = SearchRecordStore(tmp_path / "searches.json")
    store = ResearchAgendaStore(tmp_path / "agenda.json")
    agenda = _agenda(five, created_at=T0)
    queue = ResearchQueue.build(agenda, capacity=2, created_at=T0)
    store.register_agenda(agenda)
    store.register_queue(queue)
    store.register_freeze(AgendaFreeze.freeze(agenda, capacity=2, frozen_at=T0))

    assert len(gen_store) == 0            # no generated research minted
    assert len(search_store) == 0          # no search executed or re-registered
    assert len(store) == 1
    # No experiment identifier exists anywhere: the queue records ORDER, not
    # execution.
    assert "experiment_id" not in json.dumps(agenda.to_dict())
    assert "experiment_id" not in json.dumps(queue.to_dict())
    assert_canonical_70_intact()



# ═══ THE CORE FIVE-OPPORTUNITY ANTI-BIAS DEMONSTRATION ════════════════════


def test_core_five_opportunity_agenda_demonstration(five, tmp_path):
    """
    The end-to-end Wave 5 demonstration, criteria A through P.

    A. all 5 are retained;              B. ordering is deterministic;
    C. caller input order is irrelevant;  D. READY work can be queued;
    E. non-READY work cannot become executable queue work;
    F. information value and cost are separate inspectable concepts;
    G. unknown cost is not treated as zero;
    H. dependency-unlocking behaviour is represented;
    I. dropping an opportunity changes agenda identity;
    J. changing the policy changes agenda identity;
    K. changing queue capacity is provenance-visible;
    L. identical semantic state across restart reproduces identities/order;
    M. profitability-like metadata cannot change priority;
    N. no experiment is run;             O. no candidate is created;
    P. canonical 70 remain unchanged.
    """
    path = tmp_path / "agenda.json"

    # A. All five legitimate opportunities are retained.
    agenda = _agenda(five, created_at=T0)
    assert len(agenda.population) == len(agenda.entries) == 5
    assert sorted(agenda.ordered_identities) == sorted(
        item.opportunity_identity for item in five)

    # B/C. Ordering is deterministic and independent of caller input order.
    for permutation in (five, tuple(reversed(five)),
                        (five[2], five[4], five[0], five[3], five[1])):
        assert _agenda(permutation).agenda_identity == agenda.agenda_identity
        assert _agenda(permutation).ordered_identities == agenda.ordered_identities
    assert _agenda(five).agenda_identity == agenda.agenda_identity

    # D/E. Only READY work is queued; the other three are recorded exclusions.
    queue = ResearchQueue.build(agenda, capacity=5, created_at=T0)
    assert {entry.state for entry in queue.entries} == {"READY"}
    assert {item.state for item in queue.non_executable_exclusions} == {
        "BLOCKED", "WAITING_DATA", "REFUSED"}
    for item in five[2:]:
        entry = agenda.entry_for(item.opportunity_identity)
        assert entry.executable is False
        assert entry.reason_codes == item.reason_codes

    # F. Information value and cost are separate, inspectable concepts.
    first_entry = agenda.entries[0]
    first_item = agenda.opportunity_for(first_entry.opportunity_identity)
    assert first_entry.information_band == first_item.information.band().value
    assert first_entry.cost_band == first_item.cost.band().value
    assert first_item.information.band() is not first_item.cost.band()

    # G. UNKNOWN cost is recorded, visible, and never treated as cheap.
    unknown_item = _opportunity(
        CANONICAL_IDS[0], OpportunityState.READY,
        cost=_cost(availability=DataAvailability.UNKNOWN))
    unknown_entry = _agenda((unknown_item,)).entries[0]
    assert unknown_entry.cost_band == "UNKNOWN"
    assert unknown_entry.unknown_cost_factors == ("DATA_AVAILABILITY",)
    assert unknown_item.cost.rank() > _cost().rank()

    # H. Dependency-unlocking behaviour is represented, derived and visible.
    unlocker = _opportunity(CANONICAL_IDS[0], OpportunityState.READY)
    dependent = _opportunity(
        CANONICAL_IDS[1], OpportunityState.READY,
        depends_on=(unlocker.opportunity_identity,))
    assert unlock_map((unlocker, dependent))[
        unlocker.opportunity_identity] == (dependent.opportunity_identity,)
    graph = _agenda((unlocker, dependent))
    # The DEPENDENT is blocked and names its blocker; the UNLOCKER carries the
    # downstream scope it unlocks.
    assert graph.entry_for(dependent.opportunity_identity).executable is False
    assert graph.entry_for(dependent.opportunity_identity).unlocks == ()
    assert graph.entry_for(dependent.opportunity_identity).unmet_dependencies == (
        unlocker.opportunity_identity,)
    assert graph.entry_for(unlocker.opportunity_identity).unlocks == (
        dependent.opportunity_identity,)

    # I. Dropping an inconvenient lower-ranked opportunity changes the identity.
    for index in range(5):
        reduced = _agenda(five[:index] + five[index + 1:])
        assert reduced.agenda_identity != agenda.agenda_identity

    # J. Changing the prioritisation policy changes the agenda identity.
    other_policy = _agenda(five, POLICY_INFORMATION_FIRST_V1, created_at=T0)
    assert other_policy.agenda_identity != agenda.agenda_identity

    # K. Changing queue capacity is provenance-visible, and truncation is recorded.
    narrow = ResearchQueue.build(agenda, capacity=1, created_at=T0)
    assert narrow.queue_identity != queue.queue_identity
    assert narrow.to_dict()["capacity"] == 1
    assert queue.to_dict()["capacity"] == 5
    assert narrow.capacity_exclusions
    assert set(narrow.queued_identities) | {
        item.opportunity_identity for item in narrow.exclusions} == set(
            agenda.ordered_identities)

    # L. Identical semantic state across a restart reproduces identities and order.
    freeze = AgendaFreeze.freeze(agenda, capacity=5, frozen_at=T0)
    store = ResearchAgendaStore(path)
    store.register_agenda(agenda)
    store.register_queue(queue)
    store.register_freeze(freeze)
    reloaded = ResearchAgendaStore(path)
    restored = reloaded.get_agenda(agenda.agenda_identity)
    assert restored.agenda_identity == agenda.agenda_identity
    assert restored.ordered_identities == agenda.ordered_identities
    assert reloaded.get_queue(queue.queue_identity).queued_identities == (
        queue.queued_identities)
    assert reloaded.get_freeze(freeze.freeze_identity).freeze_identity == (
        freeze.freeze_identity)

    # M. Profitability-like metadata outside the governed priority material
    #    cannot change identity, ordering or queue membership.
    greedy = tuple(
        replace(item, label="most profitable", note="expected_pnl 99999",
                provenance={"sharpe": 9.9, "win_rate": 0.97, "profit": 1e9})
        for item in five)
    greedy_agenda = _agenda(greedy, created_at=T0)
    assert greedy_agenda.agenda_identity == agenda.agenda_identity
    assert greedy_agenda.ordered_identities == agenda.ordered_identities
    assert ResearchQueue.build(
        greedy_agenda, capacity=5).queue_identity == queue.queue_identity

    # N/O. No experiment is run and no candidate is created, activated or promoted.
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
    )
    assert len(GeneratedResearchStore(tmp_path / "gen.json")) == 0
    serialised = (json.dumps(agenda.to_dict()) + json.dumps(queue.to_dict())).lower()
    for token in ("experiment_id", "candidate_id", "create_candidate",
                  "activate", "promote", "treatment", "run_experiment"):
        assert token not in serialised

    # P. The canonical 70 are unchanged, and agenda work was never appended to it.
    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70
    assert len(set(canonical_inventory())) == 70
    assert set(canonical_inventory()) == set(CANONICAL_QUESTION_IDS)
    assert all(item.subject_ref in canonical_inventory() for item in five)


# __APPEND_ANCHOR__
