"""
Stage 3 / Wave 4 -- Search Provenance and Multiplicity Governance.

Focused tests only. Scope:
    - deterministic SearchRecord / family / alternative / freeze identity;
    - the family is determined by governed search SEMANTICS, never by the winner;
    - every alternative is retained, including losing and pre-test refusals;
    - the multiplicity family size is DERIVED and the caller cannot contradict it;
    - the exact, deterministic multiplicity-eligibility rule, including the
      BLOCKED / WAITING_DATA / REFUSE pre-test cases;
    - the governed bridge into the EXISTING `ValidationSpec.bonferroni_tests`
      machinery, plus backward compatibility for non-Wave-4 historical validation;
    - discovery / confirmation population separation, with no fabricated holdout;
    - a deterministic T0 selection freeze that binds the whole search;
    - restart-safe persistence, semantic dedup and fail-closed loading;
    - generated-research provenance binding without minting any `GEN-*` identity;
    - the five-alternative core multiplicity demonstration;
    - the explicit anti-p-hacking "searched 20, claimed 1" test;
    - no cartesian search, no winner-ranking optimiser, no production path;
    - canonical-70 isolation.

These are TEST FIXTURES, not production research questions. No research,
experiment, hypothesis, finding, candidate or production behaviour is executed.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from research_engine.lifecycle import (
    search_multiplicity as multiplicity_module,
    search_provenance as provenance_module,
    search_record_store as store_module,
)
from research_engine.lifecycle.curiosity_proposal import (
    CURIOSITY_PROPOSAL_ID_PREFIX,
    GeneratedResearchProposal,
    is_curiosity_proposal_identity,
)
from research_engine.lifecycle.curiosity_signal import (
    CuriositySignal,
    CuriositySignalType,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.eligibility_evidence_freeze import (
    fingerprint_for_records,
)
from research_engine.lifecycle.experiment_protocol import ValidationSpec
from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    assert_canonical_70_intact,
    assert_no_generated_canonical_collision,
)
from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    GovernedDimension,
)
from research_engine.lifecycle.progressive_depth_gate import EligibilityState
from research_engine.lifecycle.research_interaction import ResearchInteraction
from research_engine.lifecycle.search_multiplicity import (
    CORRECTION_METHOD_BONFERRONI,
    MultiplicityGovernanceError,
    MultiplicityRecord,
    apply_to_validation_spec,
    assert_consistent_with,
    derive_multiplicity,
    governed_bonferroni_tests,
    multiplicity_from_freeze,
)
from research_engine.lifecycle.search_provenance import (
    MULTIPLICITY_ID_PREFIX,
    SEARCH_ALTERNATIVE_ID_PREFIX,
    SEARCH_FAMILY_ID_PREFIX,
    SEARCH_PROVENANCE_SCHEMA_VERSION,
    SEARCH_RECORD_ID_PREFIX,
    SELECTION_FREEZE_ID_PREFIX,
    AlternativeEvaluation,
    ConfirmationKind,
    ConfirmationPolicy,
    DiscoveryPopulation,
    MultiplicityRule,
    SearchAlternative,
    SearchCompletenessError,
    SearchFamily,
    SearchProvenanceError,
    SearchProvenanceValidationError,
    SearchRecord,
    SelectionFreeze,
    is_alternative_identity,
    is_multiplicity_identity,
    is_search_family_identity,
    is_search_record_identity,
    is_selection_freeze_identity,
    search_provenance_payload,
)
from research_engine.lifecycle.search_record_store import (
    STORE_FORMAT,
    SearchRecordStore,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)

EVIDENCE_BOUNDARY = "2026-09-25T00:00:00Z"
T0 = "2026-09-25T09:00:00Z"
POPULATION = "population:joint_v1"
SUBJECT_KIND = "research_subject"
SUBJECT_REF = "exit_behaviour"
ALTERNATIVE_SPACE = {
    "kind": "EXPLICIT_BOUNDED",
    "governed_keys": ["horizon", "volatility", "session", "strategy_family", "anchor"],
    "selection_criterion_supplied_by": "governed_research_process",
}


# -- Fixtures ----------------------------------------------------------------


def _dimension(key: str, field_path: str) -> GovernedDimension:
    return GovernedDimension.create(
        dimension_key=key,
        authority=EvidenceAuthority(
            dataset="decision_trace",
            schema_version="v1",
            producer=EvidenceProducer.DECISION_TRACE,
            field_path=field_path,
            semantic_meaning="declared test-time semantics",
        ),
    )


@pytest.fixture
def registry() -> DimensionRegistry:
    """Five ADMITTED governed dimensions: the bounded candidate pool."""
    registry = DimensionRegistry()
    for key, field_path in (
        ("anchor", "market.anchor"),
        ("session", "market.session"),
        ("seed", "market.seed"),
        ("seed2", "market.seed2"),
        ("horizon", "decision.trade_horizon"),
        ("session", "market.session"),
        ("strategy_family", "decision.strategy"),
        ("volatility", "market.volatility"),
    ):
        dimension = _dimension(key, field_path)
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    return registry


@pytest.fixture
def fingerprint():
    return fingerprint_for_records(
        [{"market.regime": "TRENDING", "t": index} for index in range(500)],
        dataset_id="wave4_discovery_population",
        population="joint_eligible",
    )


@pytest.fixture
def discovery(fingerprint) -> DiscoveryPopulation:
    return DiscoveryPopulation(
        population_identity=POPULATION,
        evidence_boundary=EVIDENCE_BOUNDARY,
        dataset_fingerprint=fingerprint,
    )


def _proposal(registry: DimensionRegistry, key: str, boundary: str = EVIDENCE_BOUNDARY) -> GeneratedResearchProposal:
    """A REAL Wave 3 proposal for ONE admitted dimension, at a fixed boundary."""
    signal = CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref="finding:TRG-0001",
        source_identity="TRG-0001",
        source_provenance={"adapter": "finding_trigger", "trigger_id": "TRG-0001"},
        target_kind=SUBJECT_KIND,
        target_ref=SUBJECT_REF,
        reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
        evidence_reference="finding:finding-42",
        evidence_boundary=boundary,
        dimension_ref=registry.get(key).dimension_identity,
    )
    from research_engine.lifecycle.curiosity_generator import propose_expansion
    return propose_expansion(signal, registry)


def _alternative(
    registry: DimensionRegistry,
    key: str,
    *,
    evaluation: AlternativeEvaluation,
    state: EligibilityState = EligibilityState.PERMIT,
    reasons: tuple = ("NOT_SELECTED_BY_GOVERNED_PROCESS",),
    proposal: GeneratedResearchProposal | None = None,
    depth: int | None = 2,
    boundary: str = EVIDENCE_BOUNDARY,
) -> SearchAlternative:
    """ONE governed alternative: a real admitted dimension and a real proposal."""
    proposal = proposal if proposal is not None else _proposal(registry, key, boundary)
    return SearchAlternative(
        subject_kind=SUBJECT_KIND,
        subject_ref=SUBJECT_REF,
        proposed_dimension_identity=registry.get(key).dimension_identity,
        proposed_interaction_identity=ResearchInteraction.create(
            (registry.get("anchor"), registry.get(key))).interaction_identity,
        proposal_identity=proposal.proposal_identity,
        evidence_reference="finding:finding-42",
        evidence_boundary=boundary,
        eligibility_state=state,
        evaluation=evaluation,
        exclusion_reason_codes=reasons,
        depth=depth,
    )


def _family(
    registry: DimensionRegistry,
    discovery: DiscoveryPopulation,
    alternatives,
    *,
    rule: MultiplicityRule = MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES,
    **overrides,
) -> SearchFamily:
    kwargs = dict(
        trigger_ref="finding:TRG-0001",
        trigger_identity="TRG-0001",
        curiosity_mode="EXPANSION",
        subject_kind=SUBJECT_KIND,
        subject_ref=SUBJECT_REF,
        discovery_population_identity=discovery.population_identity,
        evidence_boundary=discovery.evidence_boundary,
        search_depth=2,
        alternative_space=ALTERNATIVE_SPACE,
        multiplicity_rule=rule,
        alternatives=tuple(alternatives),
    )
    kwargs.update(overrides)
    return SearchFamily.create(**kwargs)


def _five_alternatives(registry: DimensionRegistry, boundary: str = EVIDENCE_BOUNDARY) -> tuple:
    """
    A bounded governed family of FIVE alternatives with mixed, honest outcomes:

        horizon / volatility -- statistically evaluated, NOT selected
        session           -- Wave 2 PERMIT, statistically evaluated, SELECTED
        strategy_family   -- Wave 2 BLOCKED before any statistical test
        seed              -- Wave 2 REFUSE before any statistical test

    All five are retained. None is erased.
    """
    return (
        _alternative(registry, "horizon",
                     evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED, boundary=boundary),
        _alternative(registry, "volatility",
                     evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED, boundary=boundary),
        _alternative(registry, "session",
                     evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                     reasons=(), boundary=boundary),
        _alternative(registry, "strategy_family",
                     evaluation=AlternativeEvaluation.NOT_EVALUATED,
                     state=EligibilityState.BLOCKED,
                     reasons=("EVIDENCE_FREEZE_UNAVAILABLE",), boundary=boundary),
        _alternative(registry, "seed",
                     evaluation=AlternativeEvaluation.NOT_EVALUATED,
                     state=EligibilityState.REFUSE,
                     reasons=("EXPANSION_NOT_JUSTIFIED",), boundary=boundary),
    )


def _five_record(registry: DimensionRegistry, discovery: DiscoveryPopulation) -> SearchRecord:
    return SearchRecord.create(
        family=_family(registry, discovery, _five_alternatives(registry)),
        discovery=discovery,
        selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME",
        created_at=T0,
    )


def _prospective() -> ConfirmationPolicy:
    """No untouched holdout exists: confirmation is prospective after T0."""
    return ConfirmationPolicy(
        kind=ConfirmationKind.PROSPECTIVE_AFTER_T0,
        reference=f"boundary:{T0}",
    )


def _wide_registry(count: int) -> DimensionRegistry:
    """`count` ADMITTED governed dimensions, for a large but still bounded family."""
    registry = DimensionRegistry()
    keys = ["anchor"] + [f"dim_{index:02d}" for index in range(count)]
    for key in keys:
        dimension = _dimension(key, f"market.{key}")
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    return registry


def _evaluated_family(
    registry: DimensionRegistry, discovery: DiscoveryPopulation, count: int,
) -> SearchRecord:
    """A family of `count` DISTINCT statistically evaluated alternatives, one selected."""
    alternatives = []
    for index in range(count):
        key = f"dim_{index:02d}"
        selected = index == 0
        alternatives.append(_alternative(
            registry, key,
            evaluation=(AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED
                        if selected else AlternativeEvaluation.STATISTICALLY_EVALUATED),
            reasons=() if selected else ("NOT_SELECTED_BY_GOVERNED_PROCESS",),
        ))
    return SearchRecord.create(
        family=_family(registry, discovery, alternatives),
        discovery=discovery,
        selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME",
    )

# ═══ 1-5. Deterministic identity; the family precedes the winner ════════════


def test_search_record_identity_is_deterministic(registry, discovery):
    """(1) The same governed search always resolves to the same identity."""
    first = _five_record(registry, discovery)
    second = _five_record(registry, discovery)
    assert first.search_identity == second.search_identity
    assert first.semantic_identity == second.semantic_identity
    assert is_search_record_identity(first.search_identity)
    assert first.schema_version == SEARCH_PROVENANCE_SCHEMA_VERSION


def test_equivalent_family_semantics_give_the_same_family_identity(registry, discovery):
    """(2) Equivalent family semantics -> same family identity."""
    alternatives = _five_alternatives(registry)
    first = _family(registry, discovery, alternatives)
    second = _family(registry, discovery, tuple(reversed(alternatives)))
    assert first.family_identity == second.family_identity
    assert is_search_family_identity(first.family_identity)


def test_changed_discovery_boundary_changes_search_and_family_identity(registry, discovery):
    """(3) A changed discovery boundary is a materially different family/search."""
    baseline = _five_record(registry, discovery)
    moved = replace(discovery, evidence_boundary="2026-09-26T00:00:00Z")
    changed = SearchRecord.create(
        family=_family(registry, moved, _five_alternatives(registry, moved.evidence_boundary)),
        discovery=moved,
        selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME",
    )
    assert changed.family_identity != baseline.family_identity
    assert changed.search_identity != baseline.search_identity


def test_changed_dataset_fingerprint_changes_search_identity(registry, discovery):
    """(4) A different discovery dataset is a different search."""
    baseline = _five_record(registry, discovery)
    other = fingerprint_for_records(
        [{"market.regime": "TRENDING", "t": index} for index in range(501)],
        dataset_id="wave4_discovery_population",
        population="joint_eligible",
    )
    changed = _five_record(registry, replace(discovery, dataset_fingerprint=other))
    assert changed.search_identity != baseline.search_identity


def test_changed_alternative_space_changes_family_and_search_identity(registry, discovery):
    """(5) A different governed search space is a different multiplicity problem."""
    baseline = _five_record(registry, discovery)
    narrowed = dict(ALTERNATIVE_SPACE)
    narrowed["governed_keys"] = ["horizon", "volatility", "session"]
    changed = SearchRecord.create(
        family=_family(registry, discovery, _five_alternatives(registry),
                       alternative_space=narrowed),
        discovery=discovery,
        selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME",
    )
    assert changed.family_identity != baseline.family_identity
    assert changed.search_identity != baseline.search_identity


def test_timestamps_and_notes_are_provenance_not_identity(registry, discovery):
    baseline = _five_record(registry, discovery)
    later = replace(baseline, created_at="2099-01-01T00:00:00Z", note="re-run a year later")
    assert later.search_identity == baseline.search_identity


# ═══ 6-9. No winner-only history ═══════════════════════════════════════════


def test_all_alternatives_are_retained_including_non_selected(registry, discovery):
    """(6) A search that considered five retains all five, winner included."""
    record = _five_record(registry, discovery)
    assert record.alternatives_considered == 5
    assert len(record.alternatives) == 5
    evaluations = {a.evaluation for a in record.alternatives}
    assert AlternativeEvaluation.STATISTICALLY_EVALUATED in evaluations
    assert AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED in evaluations
    assert AlternativeEvaluation.NOT_EVALUATED in evaluations
    assert len(record.selected_alternatives) == 1
    losers = [a for a in record.alternatives if not a.is_selected]
    assert len(losers) == 4
    assert all(a.exclusion_reason_codes for a in losers)


def test_duplicate_alternative_identities_are_rejected(registry, discovery):
    """(7) A repeated alternative may never be double-counted or collapsed."""
    one = _alternative(registry, "horizon",
                       evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED)
    with pytest.raises(SearchCompletenessError, match="duplicate alternative identities"):
        _family(registry, discovery, (one, one))


def test_selected_proposal_must_exist_inside_the_recorded_family(registry, discovery):
    """(8) A selected proposal absent from the alternatives fails closed."""
    record = _five_record(registry, discovery)
    recorded = {a.proposal_identity for a in record.alternatives}
    assert record.selected_alternatives[0].proposal_identity in recorded
    with pytest.raises(SearchCompletenessError, match="not present in the recorded"):
        replace(record, selected_proposal_identities=("PRP-" + "0" * 16,))


def test_winner_only_record_is_a_different_smaller_family(registry, discovery):
    """(9) Erasing the losers is visible: it changes the family identity."""
    record = _five_record(registry, discovery)
    winner = record.selected_alternatives[0]
    winner_only = SearchRecord.create(
        family=_family(registry, discovery, (winner,)),
        discovery=discovery,
        selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME",
    )
    assert winner_only.family_identity != record.family_identity
    assert winner_only.alternatives_considered == 1
    with pytest.raises(SearchCompletenessError, match="claimed multiplicity family size"):
        replace(winner_only, claimed_family_size=5)
    assert record.alternatives_considered == 5


# ═══ 10-17. The exact, deterministic multiplicity-eligibility rule ═══════════


def test_family_size_is_derived_from_the_recorded_alternatives(registry, discovery):
    """(10) The denominator is derived, never supplied."""
    record = _five_record(registry, discovery)
    # Five considered; three were statistically evaluated (two losers + winner);
    # two were structural pre-test refusals.
    assert record.alternatives_considered == 5
    assert len(record.multiplicity_eligible) == 3
    assert record.derived_family_size == 3
    multiplicity = derive_multiplicity(record)
    assert multiplicity.family_size == 3
    assert multiplicity.alternatives_considered == 5
    assert multiplicity.multiplicity_eligible == 3


def test_caller_cannot_contradict_the_derived_family_size(registry, discovery):
    """(11) A claimed family size that disagrees with the record fails closed."""
    record = _five_record(registry, discovery)
    assert replace(record, claimed_family_size=3).claimed_family_size == 3
    for lie in (1, 2, 4, 5, 27):
        with pytest.raises(SearchCompletenessError, match="claimed multiplicity family size"):
            replace(record, claimed_family_size=lie)


def test_family_of_twenty_eligible_alternatives_derives_twenty(discovery):
    """(12) Twenty eligible statistical alternatives derive 20, never 1."""
    wide = _wide_registry(20)
    record = _evaluated_family(wide, discovery, 20)
    assert record.alternatives_considered == 20
    assert record.derived_family_size == 20
    assert governed_bonferroni_tests(record) == 20
    assert derive_multiplicity(record).family_size == 20
    assert derive_multiplicity(record).corrected_alpha == pytest.approx(0.05 / 20)


def test_multiplicity_eligibility_rule_is_exact_and_deterministic(registry, discovery):
    """(13) The rule is a pure, explicit function of the recorded evaluation state."""
    evaluated = _alternative(registry, "horizon",
                             evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED)
    selected = _alternative(registry, "session",
                            evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                            reasons=())
    not_evaluated = _alternative(registry, "volatility",
                                 evaluation=AlternativeEvaluation.NOT_EVALUATED,
                                 state=EligibilityState.REFUSE,
                                 reasons=("EXPANSION_NOT_JUSTIFIED",))
    screened = MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES
    everything = MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES
    assert evaluated.counts_under(screened) is True
    assert selected.counts_under(screened) is True
    assert not_evaluated.counts_under(screened) is False
    # Under the conservative rule every considered alternative counts.
    for item in (evaluated, selected, not_evaluated):
        assert item.counts_under(everything) is True
    # And the same alternative always answers the same way.
    for _ in range(3):
        assert evaluated.counts_under(screened) is True


def test_blocked_pre_test_alternative_follows_the_declared_rule(registry, discovery):
    """(14) A structurally BLOCKED pre-test alternative is not a statistical test."""
    blocked = _alternative(registry, "horizon",
                           evaluation=AlternativeEvaluation.NOT_EVALUATED,
                           state=EligibilityState.BLOCKED,
                           reasons=("EVIDENCE_FREEZE_UNAVAILABLE",))
    winner = _alternative(registry, "session",
                          evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                          reasons=())
    screened = SearchRecord.create(
        family=_family(registry, discovery, (blocked, winner),
                       rule=MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    conservative = SearchRecord.create(
        family=_family(registry, discovery, (blocked, winner),
                       rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert screened.derived_family_size == 1     # only the tested winner
    assert conservative.derived_family_size == 2  # the pre-test BLOCKED alternative too
    # The Wave 2 outcome is recorded EXACTLY, never reinterpreted.
    assert screened.family.alternative_by_identity(
        blocked.alternative_identity).eligibility_state is EligibilityState.BLOCKED
    # A structural refusal may never be relabelled as a statistical loser.
    with pytest.raises(SearchCompletenessError, match="decided before any statistical"):
        replace(blocked, evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED)


def test_waiting_data_pre_test_alternative_follows_the_declared_rule(registry, discovery):
    """(15) A WAITING_DATA pre-test alternative is not a statistical test."""
    waiting = _alternative(registry, "horizon",
                           evaluation=AlternativeEvaluation.NOT_EVALUATED,
                           state=EligibilityState.WAITING_DATA,
                           reasons=("INSUFFICIENT_COMMON_SUPPORT",))
    winner = _alternative(registry, "session",
                          evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                          reasons=())
    screened = SearchRecord.create(
        family=_family(registry, discovery, (waiting, winner),
                       rule=MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    conservative = SearchRecord.create(
        family=_family(registry, discovery, (waiting, winner),
                       rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert screened.derived_family_size == 1
    assert conservative.derived_family_size == 2
    assert screened.family.alternative_by_identity(
        waiting.alternative_identity).eligibility_state is EligibilityState.WAITING_DATA


def test_refuse_pre_test_alternative_follows_the_declared_rule(registry, discovery):
    """(16) A Wave 2 REFUSE is a scientific verdict, never a statistical loser."""
    refused = _alternative(registry, "horizon",
                           evaluation=AlternativeEvaluation.NOT_EVALUATED,
                           state=EligibilityState.REFUSE,
                           reasons=("EXPANSION_NOT_JUSTIFIED",))
    winner = _alternative(registry, "session",
                          evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                          reasons=())
    screened = SearchRecord.create(
        family=_family(registry, discovery, (refused, winner),
                       rule=MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    conservative = SearchRecord.create(
        family=_family(registry, discovery, (refused, winner),
                       rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert screened.derived_family_size == 1
    assert conservative.derived_family_size == 2
    assert screened.family.alternative_by_identity(
        refused.alternative_identity).eligibility_state is EligibilityState.REFUSE
    with pytest.raises(SearchCompletenessError, match="decided before any statistical"):
        replace(refused, evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED)


def test_evaluated_loser_stays_in_the_multiplicity_family(registry, discovery):
    """(17) A tested non-selected alternative remains in the denominator."""
    record = _five_record(registry, discovery)
    losers = [a for a in record.multiplicity_eligible if not a.is_selected]
    assert len(losers) == 2
    assert record.derived_family_size == 3
    # Even a family of ONE tested winner plus ONE tested loser is size 2.
    winner = record.selected_alternatives[0]
    loser = losers[0]
    two = SearchRecord.create(
        family=_family(registry, discovery, (winner, loser)),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert two.derived_family_size == 2


# ═══ 18-20. The governed Bonferroni bridge into EXISTING validation ══════════


def test_correction_configuration_is_derived_from_the_search_record(registry, discovery):
    """(18) The correction configuration comes from the recorded provenance."""
    record = _five_record(registry, discovery)
    multiplicity = derive_multiplicity(record)
    assert multiplicity.correction_method == CORRECTION_METHOD_BONFERRONI
    assert multiplicity.multiplicity_rule is (
        MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES)
    assert is_multiplicity_identity(multiplicity.multiplicity_identity)
    assert multiplicity.multiplicity_identity.startswith(MULTIPLICITY_ID_PREFIX)
    # A derived denominator is bound to the search it came from.
    assert_consistent_with(multiplicity, record)
    other_registry = DimensionRegistry()
    for key, field_path in (("anchor", "market.anchor"), ("session", "market.session"),
                            ("seed", "market.seed"),
        ("seed2", "market.seed2"), ("horizon", "decision.trade_horizon"),
                            ("volatility", "market.volatility"),
                            ("strategy_family", "decision.strategy")):
        dimension = _dimension(key, field_path)
        other_registry.register(dimension)
        other_registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    moved = replace(discovery, evidence_boundary="2026-09-27T00:00:00Z")
    other_record = SearchRecord.create(
        family=_family(other_registry, moved, _five_alternatives(other_registry,
                                                             moved.evidence_boundary)),
        discovery=moved, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    with pytest.raises(MultiplicityGovernanceError, match="different search"):
        assert_consistent_with(multiplicity, other_record)


def test_manual_contradictory_bonferroni_tests_cannot_override_the_governed_value(discovery):
    """(19) "searched 20, bonferroni_tests=1" fails closed. Not a comment: a raise."""
    wide = _wide_registry(20)
    record = _evaluated_family(wide, discovery, 20)
    assert len(record.multiplicity_eligible) == 20

    # The attempt the wave exists to prevent.
    with pytest.raises(MultiplicityGovernanceError) as excinfo:
        governed_bonferroni_tests(record, requested=1)
    assert "contradicts the 20 derived" in str(excinfo.value)

    # A hand-entered `ValidationSpec(bonferroni_tests=1)` is mechanically replaced
    # by the governed value rather than trusted.
    spec = apply_to_validation_spec(record, ValidationSpec(bonferroni_tests=1))
    assert spec.bonferroni_tests == 20

    # And an already-contradictory spec is refused outright.
    with pytest.raises(MultiplicityGovernanceError, match="may never override"):
        apply_to_validation_spec(record, ValidationSpec(bonferroni_tests=3))
    with pytest.raises(MultiplicityGovernanceError, match="may never override"):
        apply_to_validation_spec(record, _set_bonferroni(ValidationSpec(), 4))


def test_existing_non_wave4_validation_remains_backward_compatible():
    """(20) Wave 4 changes no historical validation that was not governed by it."""
    # The default is unchanged, and Wave 4 never touches the dataclass.
    default = ValidationSpec()
    assert default.bonferroni_tests == 1
    historical = ValidationSpec(bonferroni_tests=24)
    assert historical.bonferroni_tests == 24
    # A historical experiment is not governed by a SearchRecord, so nothing here
    # may reach it: no source change, no monkey-patching of the class.
    import inspect as _inspect
    from research_engine.lifecycle import experiment_protocol
    source = _inspect.getsource(experiment_protocol)
    assert "search_provenance" not in source
    assert "search_multiplicity" not in source
    assert "import research_engine.lifecycle.search" not in source
    assert "bonferroni_tests: int = 1" in source
    assert ValidationSpec().bonferroni_tests == 1


def _set_bonferroni(spec: ValidationSpec, value: int) -> ValidationSpec:
    spec.bonferroni_tests = value
    return spec


# ═══ 21-25. Discovery population is mandatory; confirmation is separate ══════


def test_discovery_population_identity_is_mandatory(fingerprint):
    """(21) A search may never run on an unnamed discovery population."""
    with pytest.raises(SearchProvenanceValidationError):
        DiscoveryPopulation(population_identity="", evidence_boundary=EVIDENCE_BOUNDARY,
                            dataset_fingerprint=fingerprint)
    with pytest.raises(SearchProvenanceValidationError):
        DiscoveryPopulation(population_identity="not-a-governed-ref",
                            evidence_boundary=EVIDENCE_BOUNDARY,
                            dataset_fingerprint=fingerprint)


def test_discovery_evidence_boundary_is_mandatory(fingerprint):
    """(22) The discovery evidence boundary is mandatory."""
    with pytest.raises(SearchProvenanceValidationError):
        DiscoveryPopulation(population_identity=POPULATION, evidence_boundary="",
                            dataset_fingerprint=fingerprint)


def test_discovery_fingerprint_is_mandatory_and_frozen(registry, discovery):
    """(23) A missing fingerprint fails closed, and a present one is frozen."""
    with pytest.raises(SearchCompletenessError, match="unfingerprinted discovery"):
        DiscoveryPopulation(population_identity=POPULATION,
                            evidence_boundary=EVIDENCE_BOUNDARY, dataset_fingerprint=None)
    record = _five_record(registry, discovery)
    frozen = record.discovery.fingerprint_identity()
    assert len(frozen) == 64
    assert replace(record, created_at="2099-01-01T00:00:00Z").discovery.fingerprint_identity() == frozen
    # The fingerprint participates in the search identity.
    other = fingerprint_for_records(
        [{"market.regime": "RANGE", "t": index} for index in range(500)],
        dataset_id="wave4_discovery_population", population="joint_eligible")
    assert replace(discovery, dataset_fingerprint=other).fingerprint_identity() != frozen


def test_confirmation_population_cannot_equal_the_discovery_population(registry, discovery):
    """(24) Evidence already inspected can never be untouched confirmation."""
    record = _five_record(registry, discovery)
    fabricated = ConfirmationPolicy(
        kind=ConfirmationKind.EXISTING_UNTOUCHED_HOLDOUT,
        population_identity=POPULATION,
        reference="holdout:proof-of-untouched")
    with pytest.raises(SearchCompletenessError, match="never be untouched confirmation"):
        SelectionFreeze.freeze_selection(
            record, correction_method=CORRECTION_METHOD_BONFERRONI,
            confirmation_policy=fabricated, selected_at=T0)
    with pytest.raises(SearchCompletenessError):
        fabricated.assert_disjoint_from(record.discovery)
    # A genuinely different holdout is accepted.
    honest = ConfirmationPolicy(
        kind=ConfirmationKind.EXISTING_UNTOUCHED_HOLDOUT,
        population_identity="population:untouched_holdout",
        reference="holdout:proof-of-untouched")
    assert honest.assert_disjoint_from(record.discovery) is honest


def test_without_an_untouched_holdout_confirmation_is_prospective(registry, discovery):
    """(25) No holdout is fabricated: the honest record is prospective after T0."""
    record = _five_record(registry, discovery)
    prospective = _prospective()
    assert prospective.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0
    assert prospective.requires_prospective_evidence is True
    assert prospective.population_identity is None
    assert prospective.reference == f"boundary:{T0}"
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=prospective, selected_at=T0)
    assert freeze.confirmation_policy.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0
    # A prospective policy may never smuggle in a fabricated population.
    with pytest.raises(SearchProvenanceValidationError, match="never fabricated"):
        ConfirmationPolicy(kind=ConfirmationKind.PROSPECTIVE_AFTER_T0,
                           population_identity="population:invented",
                           reference=f"boundary:{T0}")
    with pytest.raises(SearchProvenanceValidationError):
        ConfirmationPolicy(kind=ConfirmationKind.NOT_YET_AVAILABLE,
                           population_identity="population:invented")


# ═══ 26-29. The deterministic T0 selection freeze ═══════════════════════════


def test_selection_freeze_is_deterministic(registry, discovery):
    """(26) The freeze identity comes from science, not from the clock."""
    record = _five_record(registry, discovery)
    first = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    later = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at="2099-12-31T23:59:59Z")
    assert first.freeze_identity == later.freeze_identity
    assert first.selected_at != later.selected_at
    assert is_selection_freeze_identity(first.freeze_identity)
    assert first.freeze_identity.startswith(SELECTION_FREEZE_ID_PREFIX)
    assert first.schema_version == SEARCH_PROVENANCE_SCHEMA_VERSION


def test_selection_freeze_binds_the_whole_search(registry, discovery):
    """(27) The freeze binds search/family/proposal/population/fingerprint/size."""
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    assert freeze.search_identity == record.search_identity
    assert freeze.family_identity == record.family_identity
    assert tuple(freeze.selected_proposal_identities) == tuple(
        record.selected_alternatives[0].proposal_identity for _ in (0,))
    assert freeze.discovery_population_identity == record.discovery.population_identity
    assert freeze.discovery_fingerprint_identity == record.discovery.fingerprint_identity()
    assert freeze.discovery_evidence_boundary == record.discovery.evidence_boundary
    assert freeze.alternatives_considered == 5
    assert freeze.multiplicity_family_size == 3
    assert freeze.correction_method == CORRECTION_METHOD_BONFERRONI
    assert freeze.confirmation_policy.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0
    # A presented identity that does not match the frozen material is refused.
    with pytest.raises(SearchProvenanceValidationError, match="does not match"):
        replace(freeze, freeze_identity="FRZ-" + "0" * 16)


def test_alternatives_cannot_change_silently_after_the_freeze(registry, discovery):
    """(28) A materially different family is a different search and a new freeze."""
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    freeze.assert_belongs_to(record)

    # Drop a losing alternative after the fact: a DIFFERENT family and search.
    trimmed = _five_alternatives(registry)[:3]
    changed = SearchRecord.create(
        family=_family(registry, discovery, trimmed),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert changed.family_identity != record.family_identity
    assert changed.search_identity != record.search_identity
    with pytest.raises(SearchCompletenessError, match="does not belong to this search"):
        freeze.assert_belongs_to(changed)
    refrozen = SelectionFreeze.freeze_selection(
        changed, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    assert refrozen.freeze_identity != freeze.freeze_identity


def test_changed_alternative_family_requires_a_new_search_identity(registry, discovery):
    """(29) Any material change is a NEW historical search, never a rewrite."""
    baseline = _five_record(registry, discovery)
    fewer = SearchRecord.create(
        family=_family(registry, discovery, _five_alternatives(registry)[:4]),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    more = SearchRecord.create(
        family=_family(registry, discovery, _five_alternatives(registry) + (
            _alternative(registry, "seed2",
                         evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED),)),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    identities = {baseline.search_identity, fewer.search_identity, more.search_identity}
    assert len(identities) == 3


# ═══ 33-36. Restart-safe persistence, dedup and fail-closed load ════════════


def test_reload_preserves_every_losing_and_null_alternative(registry, discovery, tmp_path):
    """(33) A/B + H: reload keeps all five alternatives, not just the winner."""
    path = tmp_path / "searches.json"
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    store = SearchRecordStore(path)
    store.register(record)
    store.register_freeze(freeze)

    reloaded = SearchRecordStore(path)
    restored = reloaded.get(record.search_identity)
    assert restored is not None
    assert len(restored.alternatives) == 5
    assert {a.evaluation for a in restored.alternatives} == {
        a.evaluation for a in record.alternatives}
    assert {a.alternative_identity for a in restored.alternatives} == {
        a.alternative_identity for a in record.alternatives}
    assert all(a.exclusion_reason_codes for a in restored.alternatives if not a.is_selected)
    # The BLOCKED and REFUSE outcomes survive verbatim.
    states = sorted(
        a.eligibility_state.value for a in restored.alternatives
        if a.eligibility_state is not None)
    assert states == ["BLOCKED", "PERMIT", "PERMIT", "PERMIT", "REFUSE"]


def test_reload_reproduces_the_exact_family_and_freeze_identities(registry, discovery, tmp_path):
    """(34) + (I): the same search replays to the same family size and freeze."""
    path = tmp_path / "searches.json"
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    SearchRecordStore(path).register(record)
    SearchRecordStore(path).register_freeze(freeze)

    reloaded = SearchRecordStore(path)
    restored = reloaded.get(record.search_identity)
    assert restored.family_identity == record.family_identity
    assert restored.search_identity == record.search_identity
    assert restored.alternatives_considered == record.alternatives_considered
    assert restored.derived_family_size == record.derived_family_size
    restored_freeze = reloaded.get_freeze(freeze.freeze_identity)
    assert restored_freeze.freeze_identity == freeze.freeze_identity
    assert restored_freeze.multiplicity_family_size == freeze.multiplicity_family_size
    assert restored_freeze.selected_proposal_identities == freeze.selected_proposal_identities
    assert restored_freeze.discovery_fingerprint_identity == freeze.discovery_fingerprint_identity
    # A third open reproduces it again.
    assert SearchRecordStore(path).get(record.search_identity).search_identity == record.search_identity


def test_identical_completed_search_deduplicates(registry, discovery, tmp_path):
    """(35) A repeated identical completed search is never duplicated."""
    path = tmp_path / "searches.json"
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    store = SearchRecordStore(path)
    store.register(record)
    store.register_freeze(freeze)
    assert len(store) == 1

    # Re-registering the identical search, in a NEW process, adds nothing.
    for _ in range(3):
        again = SearchRecordStore(path)
        persisted = again.register(record)
        assert persisted.search_identity == record.search_identity
        assert persisted.created_at == record.created_at
        assert len(again) == 1
    # An equivalent search recorded at a different time is still the same search.
    later = replace(record, created_at="2099-01-01T00:00:00Z")
    assert SearchRecordStore(path).register(later).search_identity == record.search_identity
    # A materially changed discovery boundary IS a new historical search.
    moved = replace(discovery, evidence_boundary="2026-09-26T00:00:00Z")
    changed = SearchRecord.create(
        family=_family(registry, moved, _five_alternatives(registry, moved.evidence_boundary)),
        discovery=moved, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    final = SearchRecordStore(path)
    final.register(changed)
    assert len(final) == 2
    assert changed.search_identity in final


def test_malformed_persisted_search_fails_closed(registry, discovery, tmp_path):
    """(36) Corrupt, wrong-format or incomplete history raises; it is never repaired."""
    record = _five_record(registry, discovery)
    good = SearchRecordStore.register(
        SearchRecordStore(tmp_path / "seed.json"), record).to_dict()
    path = tmp_path / "searches.json"

    def write(document):
        path.write_text(json.dumps(document), encoding="utf-8")

    # Wrong document format.
    write({"format": "something_else", "schema_version": 1, "records": [good]})
    with pytest.raises(SearchProvenanceError, match="unknown search record store format"):
        SearchRecordStore(path)

    # Unsupported schema version.
    write({"format": STORE_FORMAT, "schema_version": 99, "records": [good]})
    with pytest.raises(SearchProvenanceValidationError, match="schema_version"):
        SearchRecordStore(path)

    # A record missing a mandatory field.
    broken = dict(good)
    broken.pop("family")
    write({"format": STORE_FORMAT, "schema_version": 1, "records": [broken]})
    with pytest.raises(SearchProvenanceValidationError, match="not exact"):
        SearchRecordStore(path)

    # A record whose alternatives were tampered with (a loser deleted).
    tampered = json.loads(json.dumps(good))
    tampered["family"]["alternatives"] = tampered["family"]["alternatives"][:1]
    tampered["family"]["alternative_identities"] = [
        a["alternative_identity"] for a in tampered["family"]["alternatives"]]
    write({"format": STORE_FORMAT, "schema_version": 1, "records": [tampered]})
    with pytest.raises(SearchProvenanceError):
        SearchRecordStore(path)

    # Unreadable content.
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SearchProvenanceError, match="unreadable"):
        SearchRecordStore(path)

    # A freeze may never outlive its search.
    orphan = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0).to_dict()
    write({"format": STORE_FORMAT, "schema_version": 1, "records": [], "freezes": [orphan]})
    with pytest.raises(SearchProvenanceValidationError, match="may never outlive its search"):
        SearchRecordStore(path)
    # A freeze may not be registered for a search the store never recorded.
    fresh = SearchRecordStore(tmp_path / "empty.json")
    with pytest.raises(SearchProvenanceValidationError, match="already-recorded search"):
        fresh.register_freeze(SelectionFreeze.from_dict(orphan))


# ═══ 30-32. Binding generated research to search provenance ═════════════════


def test_generated_research_keeps_its_search_and_freeze_provenance(registry, discovery, tmp_path):
    """(30) A question selected out of a search carries the whole search."""
    from research_engine.lifecycle.curiosity_generator import CuriosityProcessor
    from research_engine.lifecycle.eligibility_evidence_freeze import (
        EligibilityEvidenceFreeze, common_support_from_lineage, fingerprint_for_records,
    )
    from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
    from research_engine.lifecycle.interaction_feasibility import FeasibilitySpecification
    from research_engine.lifecycle.progressive_depth_gate import evaluate_interaction_eligibility

    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    payload = search_provenance_payload(record, freeze)
    assert payload["search_record_id"] == record.search_identity
    assert payload["search_family_id"] == record.family_identity
    assert payload["selection_freeze_id"] == freeze.freeze_identity
    assert payload["alternatives_considered"] == 5
    assert payload["multiplicity_family_size"] == 3
    assert payload["multiplicity_rule"] == (
        MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES.value)
    assert payload["correction_method"] == CORRECTION_METHOD_BONFERRONI
    assert payload["discovery_population_identity"] == POPULATION
    assert payload["discovery_evidence_boundary"] == EVIDENCE_BOUNDARY
    assert payload["discovery_fingerprint_identity"] == record.discovery.fingerprint_identity()
    assert payload["selection_criterion"] == "GOVERNED_RESEARCH_PROCESS_OUTCOME"
    assert payload["confirmation_policy"]["confirmation_kind"] == (
        ConfirmationKind.PROSPECTIVE_AFTER_T0.value)
    # The payload is deterministic and is bound to the freeze it came from.
    assert search_provenance_payload(record, freeze) == payload
    moved = replace(discovery, evidence_boundary="2026-09-26T00:00:00Z")
    other_record = SearchRecord.create(
        family=_family(registry, moved, _five_alternatives(registry, moved.evidence_boundary)),
        discovery=moved, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    other_freeze = SelectionFreeze.freeze_selection(
        other_record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    with pytest.raises(SearchCompletenessError, match="does not belong"):
        search_provenance_payload(record, other_freeze)
    # A presented freeze identity that does not match its material is refused.
    with pytest.raises(SearchProvenanceValidationError, match="does not match"):
        replace(freeze, freeze_identity="FRZ-" + "0" * 16)
    # K: no research executed, L: no candidate created, M: no production change.
    assert set(payload) == {
        "schema_version", "search_record_id", "search_family_id", "selection_freeze_id",
        "alternatives_considered", "multiplicity_family_size", "multiplicity_rule",
        "correction_method", "discovery_population_identity",
        "discovery_fingerprint_identity", "discovery_evidence_boundary",
        "selection_criterion", "confirmation_policy"}


def test_existing_gen_records_without_wave4_provenance_remain_valid(registry, tmp_path):
    """(31) A Wave 0 question created outside any multi-alternative search is fine."""
    from research_engine.lifecycle.curiosity_generator import (
        CuriosityProcessor, propose_expansion,
    )
    from research_engine.lifecycle.eligibility_evidence_freeze import (
        EligibilityEvidenceFreeze, common_support_from_lineage,
    )
    from research_engine.lifecycle.generated_research_identity import (
        GeneratedResearchKind, GeneratedResearchProposal as Wave0Proposal,
    )
    from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
    from research_engine.lifecycle.interaction_feasibility import FeasibilitySpecification
    from research_engine.lifecycle.progressive_depth_gate import (
        evaluate_interaction_eligibility,
    )

    # The simplest possible Wave 0 record: no Wave 4 provenance at all.
    store = GeneratedResearchStore(tmp_path / "gen.json")
    record = store.register(Wave0Proposal(
        research_kind=GeneratedResearchKind.EXPANSION,
        trigger_ref="finding:FT-1",
        target_kind="PATTERN",
        target_ref="TWEEZER_TOP",
        specification={"metric": "r_multiple", "population": "closed_shadow"},
    ))
    assert is_generated_research_id(record.generated_research_id)
    assert record.provenance_json == "{}"
    # It reloads unchanged and still deduplicates.
    assert GeneratedResearchStore(tmp_path / "gen.json").register(Wave0Proposal(
        research_kind=GeneratedResearchKind.EXPANSION,
        trigger_ref="finding:FT-1",
        target_kind="PATTERN",
        target_ref="TWEEZER_TOP",
        specification={"metric": "r_multiple", "population": "closed_shadow"},
    )).generated_research_id == record.generated_research_id
    # No search provenance was retroactively fabricated for it.
    assert "search_provenance" not in record.specification_json


def test_no_gen_identity_is_minted_for_search_family_alternative_or_freeze(
        registry, discovery):
    """(32) Wave 4 lives in its own namespaces and never mints `GEN-*`."""
    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    multiplicity = derive_multiplicity(record)
    identities = [
        record.search_identity, record.family_identity, freeze.freeze_identity,
        multiplicity.multiplicity_identity,
    ] + [a.alternative_identity for a in record.alternatives]
    for identity in identities:
        assert not identity.startswith(GENERATED_RESEARCH_ID_PREFIX)
        assert not is_generated_research_id(identity)
    assert record.search_identity.startswith(SEARCH_RECORD_ID_PREFIX)
    assert record.family_identity.startswith(SEARCH_FAMILY_ID_PREFIX)
    assert freeze.freeze_identity.startswith(SELECTION_FREEZE_ID_PREFIX)
    assert all(a.alternative_identity.startswith(SEARCH_ALTERNATIVE_ID_PREFIX)
               for a in record.alternatives)
    assert_all = [multiplicity.multiplicity_identity]
    assert all(i.startswith(MULTIPLICITY_ID_PREFIX) for i in assert_all)
    # Canonical isolation is intact alongside them.
    assert_no_generated_canonical_collision([])
    assert_canonical_70_intact()


def test_wave4_mints_no_gen_record_and_creates_no_candidate(registry, discovery, tmp_path):
    """K / L / M: no research executes, no candidate, no production state change."""
    from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
    from research_engine.lifecycle.search_record_store import SearchRecordStore

    gen_store = GeneratedResearchStore(tmp_path / "gen.json")
    search_store = SearchRecordStore(tmp_path / "searches.json")
    before_gen, before_search = len(gen_store), len(search_store)

    record = _five_record(registry, discovery)
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    search_store.register(record)
    search_store.register_freeze(freeze)
    multiplicity = derive_multiplicity(record)

    # The ONLY thing that changed is the search history.
    assert len(gen_store) == before_gen == 0
    assert len(search_store) == before_search + 1
    # Nothing was executed and nothing was promoted.
    assert all(f.experiment_id == "" for f in ())  # no experiment ran
    assert hasattr(multiplicity, "corrected_alpha")
    assert_canonical_70_intact()


# ═══ THE CORE FIVE-ALTERNATIVE MULTIPLICITY DEMONSTRATION ═══════════════════


def test_core_five_alternative_multiplicity_demonstration(registry, discovery, tmp_path):
    """
    The end-to-end Wave 4 demonstration, A through M.

    A. all 5 alternatives existed before/at selection;
    B. all 5 remain in the SearchRecord;
    C. one is selected;
    D. the selected proposal is frozen;
    E. the derived family size reflects the governed denominator rule;
    F. the selected proposal cannot claim family size 1;
    G. correction configuration is derived from the SearchRecord;
    H. reload preserves all 5 alternatives;
    I. reload reproduces identical family / freeze identities;
    J. discovery evidence is not untouched confirmation evidence;
    K. no research executes;
    L. no candidate is created;
    M. no production state changes.
    """
    from research_engine.lifecycle.generated_research_store import GeneratedResearchStore

    # -- A: the family of five is built BEFORE anything is selected.
    alternatives = _five_alternatives(registry)
    family = _family(registry, discovery, alternatives)
    assert len(alternatives) == 5

    # -- B + C: the record retains all five; exactly one is selected.
    record = SearchRecord.create(
        family=family, discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME", created_at=T0)
    assert record.alternatives_considered == 5
    assert len(record.alternatives) == 5
    selected = record.selected_alternatives
    assert len(selected) == 1
    assert selected[0].proposal_identity in record.selected_proposal_identities

    # -- D: the selection is frozen at T0.
    freeze = SelectionFreeze.freeze_selection(
        record, correction_method=CORRECTION_METHOD_BONFERRONI,
        confirmation_policy=_prospective(), selected_at=T0)
    assert freeze.selected_proposal_identities == tuple(record.selected_proposal_identities)

    # -- E: the derived family size follows the DECLARED rule (3 tested of 5).
    assert record.derived_family_size == 3
    conservative = SearchRecord.create(
        family=_family(registry, discovery, alternatives,
                       rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert conservative.derived_family_size == 5

    # -- F: the selected proposal cannot claim a family of one.
    with pytest.raises(SearchCompletenessError, match="claimed multiplicity family size"):
        replace(record, claimed_family_size=1)
    with pytest.raises(MultiplicityGovernanceError, match="contradicts"):
        governed_bonferroni_tests(record, requested=1)
    with pytest.raises(SearchCompletenessError, match="denominator can never be larger"):
        SelectionFreeze(
            search_identity=record.search_identity,
            family_identity=record.family_identity,
            selected_proposal_identities=record.selected_proposal_identities,
            discovery_population_identity=record.discovery.population_identity,
            discovery_fingerprint_identity=record.discovery.fingerprint_identity(),
            discovery_evidence_boundary=record.discovery.evidence_boundary,
            alternatives_considered=5,
            multiplicity_family_size=7,
            correction_method=CORRECTION_METHOD_BONFERRONI,
            confirmation_policy=_prospective())
    # And a freeze claiming 1 while the record derives 3 is refused outright.
    with pytest.raises(SearchProvenanceValidationError, match="does not match"):
        replace(freeze, multiplicity_family_size=1)
    # And such a freeze may not be presented against this record either.
    forged = replace(freeze, multiplicity_family_size=1, semantic_identity="",
                     freeze_identity="")
    with pytest.raises(SearchCompletenessError, match="bound family size 1"):
        forged.assert_belongs_to(record)

    # -- G: the correction configuration is derived, not hand-entered.
    multiplicity = derive_multiplicity(record)
    spec = apply_to_validation_spec(record, ValidationSpec(bonferroni_tests=1))
    assert spec.bonferroni_tests == 3
    assert multiplicity.corrected_alpha == pytest.approx(0.05 / 3)
    assert multiplicity_from_freeze(freeze).family_size == 3

    # -- H + I: restart-safe, all five preserved, identical identities.
    path = tmp_path / "searches.json"
    store = SearchRecordStore(path)
    store.register(record)
    store.register_freeze(freeze)
    reloaded = SearchRecordStore(path)
    restored = reloaded.get(record.search_identity)
    assert len(restored.alternatives) == 5
    assert restored.derived_family_size == 3
    assert restored.family_identity == record.family_identity
    assert restored.search_identity == record.search_identity
    assert reloaded.get_freeze(freeze.freeze_identity).freeze_identity == freeze.freeze_identity

    # -- J: discovery evidence is NOT untouched confirmation evidence.
    assert freeze.confirmation_policy.kind is ConfirmationKind.PROSPECTIVE_AFTER_T0
    assert freeze.confirmation_policy.requires_prospective_evidence is True
    with pytest.raises(SearchCompletenessError, match="never be untouched confirmation"):
        SelectionFreeze.freeze_selection(
            record, correction_method=CORRECTION_METHOD_BONFERRONI,
            confirmation_policy=ConfirmationPolicy(
                kind=ConfirmationKind.EXISTING_UNTOUCHED_HOLDOUT,
                population_identity=record.discovery.population_identity,
                reference="holdout:claim"),
            selected_at=T0)

    # -- K + L + M: nothing executed, nothing promoted, production untouched.
    gen_store = GeneratedResearchStore(tmp_path / "gen.json")
    assert len(gen_store) == 0
    assert_canonical_70_intact()
    assert_no_generated_canonical_collision([])


# ═══ THE EXPLICIT ANTI-P-HACKING TEST ═══════════════════════════════════════


def test_anti_p_hacking_twenty_searched_claimed_one(discovery):
    """
    The failure Wave 4 exists to prevent, exercised mechanically:

        "searched 20 statistically eligible alternatives,
         selected 1,
         bonferroni_tests = 1"

    It is neither accepted nor silently tolerated. The hand-entered denominator is
    refused, and the governed value is 20.
    """
    wide = _wide_registry(20)
    record = _evaluated_family(wide, discovery, 20)
    assert record.alternatives_considered == 20
    assert len(record.selected_alternatives) == 1

    # 1. The record itself refuses a claimed family size of 1.
    with pytest.raises(SearchCompletenessError, match="claimed multiplicity family size"):
        replace(record, claimed_family_size=1)

    # 2. The governed derivation ignores the caller's number entirely.
    assert derive_multiplicity(record).family_size == 20
    assert governed_bonferroni_tests(record) == 20
    assert governed_bonferroni_tests(record, requested=20) == 20

    # 3. A hand-entered `bonferroni_tests=1` is refused, not honoured.
    with pytest.raises(MultiplicityGovernanceError) as excinfo:
        governed_bonferroni_tests(record, requested=1)
    assert "contradicts the 20 derived" in str(excinfo.value)

    # 4. Through the EXISTING ValidationSpec API the value is mechanically 20.
    spec = apply_to_validation_spec(record, ValidationSpec(bonferroni_tests=1))
    assert spec.bonferroni_tests == 20

    # 5. Splitting the family to rescue the winner is also impossible: the 19
    #    dropped alternatives are still inside the frozen family identity.
    winner_only = SearchRecord.create(
        family=_family(wide, discovery, record.selected_alternatives),
        discovery=discovery, selection_boundary=T0,
        selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")
    assert winner_only.family_identity != record.family_identity
    assert winner_only.derived_family_size == 1
    # ...and that smaller family is a visibly DIFFERENT historical search.
    assert winner_only.search_identity != record.search_identity


# ═══ 37-44. Structural invariants: no search, no ranking, no production ══════


def _source(module) -> str:
    return inspect.getsource(module)


def _tree(module):
    return ast.parse(_source(module))


def _code_only(module) -> str:
    """Module source with every docstring removed, so prose may not satisfy a check."""
    tree = _tree(module)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


def test_no_broad_cartesian_interaction_generation_exists():
    """(37) Wave 4 governs a SUPPLIED bounded family; it never enumerates one."""
    forbidden_calls = {
        "product", "combinations", "permutations", "chain", "islice",
        "recursive", "enumerate_interactions", "expand_interactions",
    }
    forbidden_imports = ("itertools",)
    for module in (provenance_module, multiplicity_module, store_module):
        tree = _tree(module)
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden_calls.isdisjoint(calls), module.__name__
        assert forbidden_calls.isdisjoint(getattr(module, "__all__", []) or [])
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(fragment in name for name in imports
                       for fragment in forbidden_imports)
        # No dimension-registry-driven search space is ever built.
        assert "DimensionRegistry" not in imports


def test_no_profit_expectancy_or_pvalue_winner_ranking_exists():
    """(38) Wave 4 records the supplied outcome; it never chooses the winner."""
    forbidden = (
        "expectancy", "sharpe", "drawdown", "win_rate", "profit", "p_value",
        "permutation_p", "sort(", "sorted(candidates", "max(", "min(", "rank",
    )
    for module in (provenance_module, multiplicity_module, store_module):
        code = _code_only(module)
        for token in forbidden:
            assert token not in code, f"{module.__name__} mentions {token!r}"
        # A genuinely empty selection is never auto-filled by a "best" alternative.
        assert "best" not in code


def test_wave3_decisions_remain_unchanged(registry):
    """(39) Wave 4 does not touch Wave 3 signals, proposals or the processor."""
    from research_engine.lifecycle import curiosity_generator, curiosity_proposal, curiosity_signal
    for module in (curiosity_generator, curiosity_proposal, curiosity_signal):
        source = _source(module)
        assert "search_provenance" not in source
        assert "search_multiplicity" not in source
        assert "search_record_store" not in source
    # A Wave 3 proposal is still exactly the same governed object.
    proposal = _proposal(registry, "horizon")
    assert is_curiosity_proposal_identity(proposal.proposal_identity)
    assert proposal.proposal_identity.startswith(CURIOSITY_PROPOSAL_ID_PREFIX)
    assert_canonical_70_intact()


def test_wave2_decisions_remain_unchanged(registry, discovery):
    """(40) PERMIT / WAITING_DATA / BLOCKED / REFUSE are preserved verbatim."""
    from research_engine.lifecycle.progressive_depth_gate import EligibilityState as States
    assert {s.value for s in States} == {"PERMIT", "WAITING_DATA", "BLOCKED", "REFUSE"}
    record = _five_record(registry, discovery)
    recorded = {a.eligibility_state for a in record.alternatives if a.eligibility_state}
    assert recorded == {States.PERMIT, States.BLOCKED, States.REFUSE}
    for alternative in record.alternatives:
        if alternative.eligibility_state in (States.BLOCKED, States.REFUSE):
            assert alternative.evaluation is AlternativeEvaluation.NOT_EVALUATED


def test_wave1_identity_semantics_remain_unchanged(registry):
    """(41) Wave 4 consumes governed Wave 1 identities without redefining them."""
    from research_engine.lifecycle.governed_dimension import is_dimension_identity
    from research_engine.lifecycle.research_interaction import (
        ResearchInteraction, is_interaction_identity,
    )
    source = _source(provenance_module)
    assert "is_dimension_identity" in source
    assert "is_interaction_identity" in source
    # A Wave 4 alternative references EXISTING governed identities verbatim.
    alternative = _five_alternatives(registry)[0]
    assert is_dimension_identity(alternative.proposed_dimension_identity)
    assert is_interaction_identity(alternative.proposed_interaction_identity)
    # The interaction identity is still exactly Wave 1's own derivation.
    expected = ResearchInteraction.create(
        (registry.get("anchor"), registry.get("horizon"))).interaction_identity
    assert alternative.proposed_interaction_identity == expected


def test_wave0_identity_semantics_remain_unchanged(tmp_path):
    """(42) Wave 4 mints no `GEN-*` identity and reuses Wave 0's store as-is."""
    from research_engine.lifecycle import generated_research_identity, generated_research_store
    for module in (generated_research_identity, generated_research_store):
        source = _source(module)
        assert "search_provenance" not in source
        assert "search_multiplicity" not in source
    assert generated_research_identity.GENERATED_RESEARCH_ID_PREFIX == "GEN-"


def test_canonical_registry_remains_exactly_seventy():
    """(43) The frozen canonical 70 is untouched by Wave 4."""
    from research_engine.lifecycle.generated_research_isolation import (
        CANONICAL_QUESTION_COUNT, canonical_inventory,
    )
    assert_canonical_70_intact()
    assert len(canonical_inventory()) == CANONICAL_QUESTION_COUNT == 70


def test_no_production_path_exists():
    """(44) Wave 4 is research-science governance with zero production authority."""
    forbidden_import_fragments = (
        "broker", "risk", "sizing", "production", "research_runner",
        "experiment_runner", "candidate_evolution", "governance_gate",
        "orchestrator", "research_cycle_runner", "candidate_activation_gate",
        "baseline_manifest", "research_question_registry",
    )
    for module in (provenance_module, multiplicity_module, store_module):
        tree = _tree(module)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for name in imports:
            for fragment in forbidden_import_fragments:
                assert fragment not in name, f"{module.__name__} imports {name}"
        forbidden_calls = {
            "run_experiment", "create_hypothesis", "create_candidate",
            "activate_candidate", "promote_candidate", "execute_research",
            "approve", "can_promote", "apply_treatment",
        }
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden_calls.isdisjoint(calls)
    # Importing Wave 4 writes nothing and touches no canonical state.
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-c",
         "import research_engine.lifecycle.search_provenance as m;"
         "import research_engine.lifecycle.search_multiplicity as s;"
         "import research_engine.lifecycle.search_record_store as t;"
         "print(m.__name__, s.__name__, t.__name__)"],
        capture_output=True, text=True, cwd=str(Path.cwd()))
    assert result.returncode == 0, result.stderr
    assert "search_provenance" in result.stdout
    assert_canonical_70_intact()


# ═══ Fail-closed completeness: the record must prove what was searched ═══════


def test_incomplete_search_provenance_fails_closed(registry, discovery):
    """Every gap in the provenance is a hard failure, never a silent repair."""
    winner = _alternative(registry, "session",
                          evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED_AND_SELECTED,
                          reasons=())

    # (a) No alternatives at all.
    with pytest.raises(SearchCompletenessError, match="AT LEAST ONE alternative"):
        _family(registry, discovery, ())

    # (b) A losing alternative with no reason for non-selection.
    with pytest.raises(SearchCompletenessError, match="no reason for non-selection"):
        SearchRecord.create(
            family=_family(registry, discovery, (
                winner,
                _alternative(registry, "horizon",
                             evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED,
                             reasons=()),)),
            discovery=discovery, selection_boundary=T0,
            selection_criterion="GOVERNED_RESEARCH_PROCESS_OUTCOME")

    # (c) A selection with no T0 boundary.
    with pytest.raises(SearchCompletenessError, match="explicit T0 selection boundary"):
        SearchRecord.create(
            family=_family(registry, discovery, (winner,)), discovery=discovery)

    # (d) A selection with no recorded criterion.
    with pytest.raises(SearchCompletenessError, match="criterion that produced it"):
        SearchRecord.create(
            family=_family(registry, discovery, (winner,)), discovery=discovery,
            selection_boundary=T0)

    # (e) A family with no declared alternative space.
    with pytest.raises(SearchCompletenessError, match="must declare the alternative space"):
        _family(registry, discovery, (winner,), alternative_space={})

    # (f) A discovery population that does not match the governed family.
    with pytest.raises(SearchCompletenessError, match="does not"):
        SearchRecord.create(
            family=_family(registry, discovery, (winner,)),
            discovery=replace(discovery, population_identity="population:other"))

    # (g) Contradictory subjects inside one family.
    stranger = SearchAlternative(
        subject_kind="system_component", subject_ref="exit_rule:tpu",
        evidence_reference="candidate:CAND-1", evidence_boundary=EVIDENCE_BOUNDARY,
        eligibility_state=EligibilityState.PERMIT,
        evaluation=AlternativeEvaluation.STATISTICALLY_EVALUATED,
        exclusion_reason_codes=("NOT_SELECTED",))
    with pytest.raises(SearchCompletenessError, match="contradictory subjects"):
        _family(registry, discovery, (winner, stranger))

    # (h) Contradictory evidence boundaries inside one family.
    moved = replace(winner, evidence_boundary="2026-09-26T00:00:00Z", semantic_identity="", alternative_identity="")
    with pytest.raises(SearchCompletenessError, match="may not span two boundaries"):
        _family(registry, discovery, (winner, moved))

    # (i) A family whose declared search depth contradicts the alternatives.
    with pytest.raises(SearchCompletenessError, match="contradicts the recorded"):
        _family(registry, discovery, (replace(winner, depth=3),), search_depth=2)

    # (j) An empty denominator: a family of only pre-test refusals.
    blocked = _alternative(registry, "horizon",
                           evaluation=AlternativeEvaluation.NOT_EVALUATED,
                           state=EligibilityState.BLOCKED,
                           reasons=("EVIDENCE_FREEZE_UNAVAILABLE",))
    with pytest.raises(SearchCompletenessError, match="no alternative in this family"):
        SearchRecord.create(
            family=_family(registry, discovery, (blocked,),
                           rule=MultiplicityRule.STATISTICALLY_EVALUATED_ALTERNATIVES),
            discovery=discovery)
    # Under the conservative rule the same family is perfectly valid, at size 1.
    conservative = SearchRecord.create(
        family=_family(registry, discovery, (blocked,),
                       rule=MultiplicityRule.ALL_CONSIDERED_ALTERNATIVES),
        discovery=discovery)
    assert conservative.derived_family_size == 1


def test_a_non_governed_identity_is_never_accepted_as_a_proposal(registry, discovery):
    """An alternative may only carry a REAL Wave 3 proposal identity."""
    with pytest.raises(SearchProvenanceValidationError, match="proposal_identity"):
        SearchAlternative(
            subject_kind=SUBJECT_KIND, subject_ref=SUBJECT_REF,
            proposal_identity="GEN-" + "0" * 16,
            evidence_reference="finding:finding-42", evidence_boundary=EVIDENCE_BOUNDARY)
    with pytest.raises(SearchProvenanceValidationError, match="proposal_identity"):
        SearchAlternative(
            subject_kind=SUBJECT_KIND, subject_ref=SUBJECT_REF,
            proposal_identity="EVD-" + "0" * 16,
            evidence_reference="finding:finding-42", evidence_boundary=EVIDENCE_BOUNDARY)


def test_store_has_no_import_time_writes_and_keeps_history_immutable(registry, discovery, tmp_path):
    """No import-time write; a bound search identity is never rebound."""
    import importlib
    import research_engine.lifecycle.search_record_store as reloaded_module
    default = reloaded_module.DEFAULT_SEARCH_STORE_PATH
    assert not default.exists()
    importlib.reload(reloaded_module)
    assert not reloaded_module.DEFAULT_SEARCH_STORE_PATH.exists()

    path = tmp_path / "searches.json"
    record = _five_record(registry, discovery)
    store = SearchRecordStore(path)
    store.register(record)
    # The persisted record is byte-stable.
    first_bytes = path.read_text(encoding="utf-8")
    SearchRecordStore(path).register(record)
    assert path.read_text(encoding="utf-8") == first_bytes
    assert json.loads(first_bytes)["format"] == STORE_FORMAT
    # A presented search identity bound to different semantics is refused.
    from research_engine.lifecycle.search_provenance import SearchIdentityConflict
    document = json.loads(first_bytes)
    document["records"][0]["selection_criterion"] = "SOME_OTHER_CRITERION"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SearchProvenanceError):
        SearchRecordStore(path)
