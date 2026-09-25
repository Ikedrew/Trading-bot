"""
Stage 3 / Wave 2 -- Progressive Depth & Evidence Feasibility Gate.

Focused tests only. Scope:
    - the four states PERMIT / WAITING_DATA / BLOCKED / REFUSE, never collapsed;
    - common support proven by a JOINT population, never by marginal counts;
    - per-cell support that a pooled sample cannot hide;
    - explicit, governed feasibility thresholds with deterministic identity;
    - progressive depth 1 -> 2 -> 3 -> deeper, recursively and without a
      maximum depth or any depth-skipping;
    - the mechanical parent/child invariant (parent + exactly one dimension);
    - governed expansion justification, and proof that a huge sample alone can
      never authorise a deeper interaction;
    - the immutable T0 evidence freeze and its deterministic identity;
    - no generated question, no automatic child, no search API, canonical-70
      isolation, and no production authority.

These are test fixtures, NOT production research questions. No research,
experiment, hypothesis, finding, candidate or production behaviour is executed.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json

import pytest

from research_engine.lifecycle import (
    eligibility_evidence_freeze as freeze_module,
    interaction_feasibility as feasibility_module,
    progressive_depth_gate as gate_module,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.eligibility_evidence_freeze import (
    CellSupport,
    CommonSupport,
    EligibilityEvidenceFreeze,
    EvidenceFreezeError,
    common_support_from_lineage,
    fingerprint_for_records,
    is_evidence_freeze_identity,
    marginal_coverage_from_lineage,
)
from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    assert_canonical_70_intact,
    assert_generated_not_in_canonical_apis,
    canonical_inventory,
)
from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    GovernedDimension,
)
from research_engine.lifecycle.interaction_feasibility import (
    FEASIBILITY_ID_PREFIX,
    FEASIBILITY_SCHEMA_VERSION,
    EligibilityReasonCode,
    FeasibilitySpecification,
    FeasibilityValidationError,
    is_feasibility_identity,
    reason_class,
)
from research_engine.lifecycle.progressive_depth_gate import (
    DECISION_ID_PREFIX,
    ELIGIBILITY_SCHEMA_VERSION,
    EligibilityDecision,
    EligibilityGateError,
    EligibilityReason,
    EligibilityState,
    ExpansionJustification,
    ExpansionJustificationType,
    evaluate_interaction_eligibility,
    is_decision_identity,
    is_justification_identity,
    parent_step_is_valid,
)
from research_engine.lifecycle.research_interaction import (
    InteractionSlice,
    ResearchInteraction,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
    JoinContract,
)

EVIDENCE_BOUNDARY = "2026-09-25T00:00:00Z"
T0 = "2026-09-25T09:00:00Z"


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


def _admitted_registry() -> DimensionRegistry:
    registry = DimensionRegistry()
    for key, field_path in (
        ("regime", "market.regime"),
        ("horizon", "decision.trade_horizon"),
        ("strategy_family", "decision.strategy"),
        ("session", "market.session"),
    ):
        dimension = _dimension(key, field_path)
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    return registry


@pytest.fixture
def registry() -> DimensionRegistry:
    return _admitted_registry()


def _fingerprint(seed: int = 1):
    """A real population fingerprint from the repository's existing contract."""
    return fingerprint_for_records(
        [{"market.regime": "TRENDING", "t": index} for index in range(500 + seed)],
        dataset_id="wave2_test_population",
        population="joint_eligible",
    )


def _spec(**overrides) -> FeasibilitySpecification:
    base = dict(spec_key="wave2_explicit", min_common_observations=60)
    base.update(overrides)
    return FeasibilitySpecification.create(**base)


def _support(
    interaction: ResearchInteraction,
    registry: DimensionRegistry,
    *,
    joint: int = 300,
    population_ref: str = "population:joint_v1",
    fingerprint=None,
    **kwargs,
) -> CommonSupport:
    return common_support_from_lineage(
        interaction.dimensions,
        # Marginal lineage is deliberately LARGE and DIFFERENT from the joint
        # count, so no test can accidentally pass on marginal arithmetic.
        {"total_records": 10000,
         "key_coverage": {d.authority.field_path: {"non_empty": 10000}
                          for d in interaction.dimensions}},
        joint_population_ref=population_ref,
        joint_usable_observations=joint,
        dataset_fingerprint=fingerprint if fingerprint is not None else _fingerprint(),
        **kwargs,
    )


def _freeze(
    interaction: ResearchInteraction,
    registry: DimensionRegistry,
    *,
    spec: FeasibilitySpecification | None = None,
    cell_support: tuple[CellSupport, ...] = (),
    parent_justification_identity: str | None = None,
    t0: str = T0,
    boundary: str = EVIDENCE_BOUNDARY,
    **support_kwargs,
) -> EligibilityEvidenceFreeze:
    return EligibilityEvidenceFreeze.create(
        interaction,
        _support(interaction, registry, **support_kwargs),
        spec if spec is not None else _spec(),
        evidence_boundary=boundary,
        cell_support=cell_support,
        parent_justification_identity=parent_justification_identity,
        t0=t0,
    )


def _justification(
    parent: ResearchInteraction,
    child: ResearchInteraction,
    registry: DimensionRegistry,
    *,
    added_key: str,
    unresolved: bool = True,
    justification_type: ExpansionJustificationType = (
        ExpansionJustificationType.UNRESOLVED_BETWEEN_SLICE_HETEROGENEITY),
    note: str = "",
) -> ExpansionJustification:
    added_identity = next(
        identity for identity in set(child.dimension_identities)
        - set(parent.dimension_identities)
    )
    assert registry.get(added_key) is not None
    assert registry.get(added_key).dimension_identity == added_identity
    return ExpansionJustification.create(
        parent_interaction_identity=parent.interaction_identity,
        parent_depth=parent.depth,
        child_interaction_identity=child.interaction_identity,
        added_dimension_key=added_key,
        added_dimension_identity=added_identity,
        justification_type=justification_type,
        evidence_reference="finding:wave2-test",
        unresolved_structure=unresolved,
        evidence_boundary=EVIDENCE_BOUNDARY,
        note=note,
    )


def _permitted(registry: DimensionRegistry, keys):
    """Evaluate ONE interaction and assert it is permitted; return (ix, decision)."""
    interaction = ResearchInteraction.admitted_from(registry, keys)
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry)
    assert decision.state is EligibilityState.PERMIT, decision.reason_codes
    return interaction, decision



# --- 1-2. Depth 1 with sufficient / insufficient evidence -------------------


def test_valid_1d_proposal_with_sufficient_evidence_is_permitted(registry):
    """(1) valid 1D + sufficient governed evidence -> PERMIT, with no parent."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry)
    assert decision.state is EligibilityState.PERMIT
    assert decision.permitted and not decision.waiting
    assert decision.reasons == ()
    assert decision.depth == 1
    # A 1D interaction is permitted WITHOUT any parent or justification.
    assert decision.parent_justification_identity is None
    assert is_decision_identity(decision.decision_identity)


def test_valid_1d_proposal_with_insufficient_evidence_is_waiting_data(registry):
    """(2) valid 1D + 23 of the required 60 usable observations -> WAITING_DATA."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry, joint=23), registry)
    assert decision.state is EligibilityState.WAITING_DATA
    assert decision.has_reason(EligibilityReasonCode.COMMON_SUPPORT_INSUFFICIENT)
    reason = decision.reasons[0]
    assert (reason.observed, reason.required) == (23, 60)


# --- 3. Missing / unadmitted authority -> BLOCKED ---------------------------


def test_unadmitted_dimension_is_blocked():
    """(3a) a registered-but-unadmitted dimension cannot be decided upon."""
    local = DimensionRegistry()
    local.register(_dimension("regime", "market.regime"))   # registered, NOT admitted
    interaction = ResearchInteraction.create([local.get("regime")])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, local), local)
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.DIMENSION_NOT_ADMITTED)
    # Data existing, or a field being present, never substitutes for admission.
    assert interaction.depth == 1


def test_missing_producer_authority_is_blocked():
    """(3b) with no governed registry, admission/authority cannot be proven."""
    empty_registry = DimensionRegistry()
    interaction = ResearchInteraction.create([_dimension("regime", "market.regime")])
    decision = evaluate_interaction_eligibility(
        interaction,
        EligibilityEvidenceFreeze.create(
            interaction,
            CommonSupport.from_joint_population(
                "population:joint_v1", 300, interaction.dimensions,
                dataset_fingerprint=_fingerprint()),
            _spec(),
            evidence_boundary=EVIDENCE_BOUNDARY,
        ),
        empty_registry,
    )
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.DIMENSION_NOT_ADMITTED)


def test_historical_only_authority_is_blocked_for_currentness():
    """(3c) a non-current-eligible authority is CURRENTNESS_FAILED."""
    historical = GovernedDimension.create(
        dimension_key="legacy_regime",
        authority=EvidenceAuthority(
            dataset="decision_trace",
            schema_version="v1",
            producer=EvidenceProducer.DECISION_TRACE,
            field_path="market.legacy_regime",
            semantic_meaning="historical only semantics",
            current_eligibility=False,
        ),
    )
    local = DimensionRegistry()
    local.register(historical)
    # Admission is decided by the COVERAGE record; the authority's own
    # current-eligibility rule is what the gate separately verifies.
    local.admit(historical, DimensionCoverage(1000, 900, True, True))
    interaction = ResearchInteraction.create([historical])
    decision = evaluate_interaction_eligibility(
        interaction,
        EligibilityEvidenceFreeze.create(
            interaction,
            CommonSupport.from_joint_population(
                "population:joint_v1", 300, interaction.dimensions,
                dataset_fingerprint=_fingerprint()),
            _spec(),
            evidence_boundary=EVIDENCE_BOUNDARY,
        ),
        local,
    )
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.CURRENTNESS_FAILED)


def test_multi_dataset_interaction_without_join_contract_is_blocked(registry):
    """(3d) spanning two datasets with no governed JoinContract -> BLOCKED."""
    execution = GovernedDimension.create(
        dimension_key="execution_quality",
        authority=EvidenceAuthority(
            dataset="execution_results_v1",
            schema_version="v1",
            producer=EvidenceProducer.EXECUTION_RESULTS,
            field_path="execution.quality",
            semantic_meaning="declared execution quality",
        ),
    )
    registry.register(execution)
    registry.admit(execution, DimensionCoverage(1000, 900, True, True))
    interaction = ResearchInteraction.admitted_from(
        registry, ["regime", "execution_quality"])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry)
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.JOINABILITY_UNPROVEN)

    # With a governed JoinContract and proven joinability, the same interaction
    # passes the structural joinability stage (it still needs a parent below 2D).
    permitted = evaluate_interaction_eligibility(
        interaction,
        _freeze(interaction, registry, join_contract=JoinContract(
            join_keys=("correlation_id",), cardinality="many_to_one"),
            joinability_proven=True),
        registry,
    )
    assert not permitted.has_reason(EligibilityReasonCode.JOINABILITY_UNPROVEN)
    assert permitted.state is EligibilityState.REFUSE


# --- 4-6. Common support is JOINT, never marginal ---------------------------


def test_marginal_dimension_counts_cannot_substitute_for_common_support(registry):
    """(4) 10,000 marginal for each dimension is NOT 10,000 jointly usable."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    lineage = {"total_records": 10000,
               "key_coverage": {"market.regime": {"non_empty": 10000},
                                "decision.trade_horizon": {"non_empty": 10000}}}
    marginal = marginal_coverage_from_lineage(interaction.dimensions, lineage)
    assert marginal == {"horizon": 10000, "regime": 10000}

    # 11 jointly eligible observations against a 10,000 marginal for BOTH
    # dimensions: the marginals are enormous, the joint support is tiny.
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry, joint=11), registry)
    assert decision.state is EligibilityState.WAITING_DATA
    assert decision.has_reason(EligibilityReasonCode.COMMON_SUPPORT_INSUFFICIENT)
    assert decision.reasons[0].observed == 11


def test_marginal_counts_are_context_only_and_never_satisfy_a_requirement(
        registry):
    """The recorded marginal counts cannot raise the joint count."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    support = _support(interaction, registry, joint=11)
    assert support.marginal_observations == {"horizon": 10000, "regime": 10000}
    assert support.joint_usable_observations == 11
    # Marginal coverage is absent from the frozen semantic material entirely.
    assert "marginal_observations" not in support.semantic_material()


def test_unproven_common_support_is_blocked(registry):
    """(5) a joint population with zero usable observations -> BLOCKED."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry, joint=0), registry)
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.COMMON_SUPPORT_UNPROVEN)
    # BLOCKED, not WAITING_DATA: the population itself is unproven, not short.
    assert not decision.waiting


def test_proven_but_insufficient_common_support_is_waiting_data(registry):
    """(6) 23 / required 60 eligible observations -> WAITING_DATA."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry, joint=23), registry)
    assert decision.state is EligibilityState.WAITING_DATA
    assert decision.reasons[0].observed == 23
    assert decision.reasons[0].required == 60


def test_common_support_requires_a_governed_population_reference(registry):
    """A joint count with no population identity cannot establish common support."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    with pytest.raises(EvidenceFreezeError):
        CommonSupport.from_joint_population(
            "not-a-governed-reference", 300, interaction.dimensions,
            dataset_fingerprint=_fingerprint())



# --- 7-8. Cell support ------------------------------------------------------


def test_pooled_support_cannot_hide_under_supported_required_cells(registry):
    """(7) 300 pooled observations with a 4-observation cell -> WAITING_DATA."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    trending = InteractionSlice.create(interaction, {"regime": "TRENDING",
                                                    "horizon": "SCALP"})
    ranging = InteractionSlice.create(interaction, {"regime": "RANGING",
                                                   "horizon": "SCALP"})
    cell_support = (
        CellSupport.for_slice(trending, 296, required_observations=30),
        # The pooled sample is huge; this one cell has almost no usable support.
        CellSupport.for_slice(ranging, 4, required_observations=30),
    )
    decision = evaluate_interaction_eligibility(
        interaction,
        _freeze(interaction, registry, joint=300, cell_support=cell_support),
        registry,
    )
    assert decision.state is EligibilityState.WAITING_DATA
    assert decision.has_reason(EligibilityReasonCode.CELL_SUPPORT_INSUFFICIENT)
    reason = decision.reasons[0]
    assert (reason.observed, reason.required) == (4, 30)
    assert reason.subject == ranging.slice_identity


def test_insufficient_required_cell_support_is_waiting_data_not_blocked(registry):
    """(8) an under-supported cell is WAITING_DATA; the path itself is valid."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    cell = InteractionSlice.create(interaction, {"regime": "TRENDING",
                                                "horizon": "SCALP"})
    decision = evaluate_interaction_eligibility(
        interaction,
        _freeze(interaction, registry, joint=300,
                cell_support=(CellSupport.for_slice(cell, 12, required_observations=30),)),
        registry,
    )
    assert decision.state is EligibilityState.WAITING_DATA
    assert not decision.blocked and not decision.refused


def test_cell_support_without_any_declared_requirement_fails_closed(registry):
    """(18) no threshold is invented: an undeclared cell requirement is refused."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    cell = InteractionSlice.create(interaction, {"regime": "TRENDING",
                                                "horizon": "SCALP"})
    decision = evaluate_interaction_eligibility(
        interaction,
        _freeze(interaction, registry, joint=300,
                cell_support=(CellSupport.for_slice(cell, 300),)),
        registry,
    )
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.FEASIBILITY_REQUIREMENT_UNDECLARED)


def test_specification_slice_minimum_applies_to_every_declared_cell(registry):
    """A specification-level per-slice minimum governs cells that omit their own."""
    # Depth 1, so this test isolates the cell-support stage from the depth rule.
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    cell = InteractionSlice.create(interaction, {"regime": "TRENDING"})
    decision = evaluate_interaction_eligibility(
        interaction,
        _freeze(interaction, registry, joint=300,
                spec=_spec(min_common_observations=60, min_slice_observations=50),
                cell_support=(CellSupport.for_slice(cell, 300),)),
        registry,
    )
    assert decision.state is EligibilityState.PERMIT


# --- 9-14. Progressive depth and the parent/child invariant -----------------


def test_valid_2d_child_without_expansion_justification_is_refused(registry):
    """(9) a valid 2D child with a permitted parent but NO justification -> REFUSE."""
    parent, parent_decision = _permitted(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry,
        parent=parent, parent_decision=parent_decision)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(
        EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED)
    # A REFUSE is a valid scientific result, not an error, and is NOT waiting.
    assert not decision.waiting and not decision.blocked


def test_valid_2d_child_with_parent_and_justification_is_permitted(registry):
    """(10) valid parent + valid justification + sufficient support -> PERMIT."""
    parent, parent_decision = _permitted(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    justification = _justification(parent, child, registry, added_key="horizon")
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry,
        parent=parent, parent_decision=parent_decision, justification=justification)
    assert decision.state is EligibilityState.PERMIT
    assert decision.depth == 2
    assert decision.parent_justification_identity == (
        justification.justification_identity)


def test_2d_child_without_any_parent_is_refused_as_parent_required(registry):
    """A depth-2 proposal with no parent at all is REFUSE / PARENT_REQUIRED."""
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(EligibilityReasonCode.PARENT_REQUIRED)


def test_parent_must_have_been_permitted_to_justify_a_child(registry):
    """A parent that was only WAITING_DATA cannot justify expansion."""
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    waiting = evaluate_interaction_eligibility(
        parent, _freeze(parent, registry, joint=10), registry)
    assert waiting.state is EligibilityState.WAITING_DATA
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    justification = _justification(parent, child, registry, added_key="horizon")
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry,
        parent=parent, parent_decision=waiting, justification=justification)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(EligibilityReasonCode.PARENT_NOT_ELIGIBLE)



def test_dimension_swap_parent_child_relationship_is_rejected(registry):
    """(11) parent `regime` -> child `horizon x strategy_family` is INVALID."""
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    _, parent_decision = _permitted(registry, ["regime"])
    swapped = ResearchInteraction.admitted_from(
        registry, ["horizon", "strategy_family"])
    assert not parent_step_is_valid(parent, swapped)
    justification = ExpansionJustification.create(
        parent_interaction_identity=parent.interaction_identity,
        parent_depth=1,
        child_interaction_identity=swapped.interaction_identity,
        added_dimension_key="horizon",
        added_dimension_identity=registry.get("horizon").dimension_identity,
        justification_type=ExpansionJustificationType.CONDITIONAL_INSTABILITY,
        evidence_reference="finding:wave2-test",
        unresolved_structure=True,
        evidence_boundary=EVIDENCE_BOUNDARY,
    )
    decision = evaluate_interaction_eligibility(
        swapped, _freeze(swapped, registry), registry,
        parent=parent, parent_decision=parent_decision, justification=justification)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(EligibilityReasonCode.INVALID_DEPTH_STEP)


def test_depth_skipping_from_1d_to_3d_is_impossible(registry):
    """(12) 1D -> 3D is mechanically impossible, whatever the justification."""
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    _, parent_decision = _permitted(registry, ["regime"])
    child = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    assert child.depth == 3
    assert not parent_step_is_valid(parent, child)
    justification = ExpansionJustification.create(
        parent_interaction_identity=parent.interaction_identity,
        parent_depth=1,
        child_interaction_identity=child.interaction_identity,
        added_dimension_key="horizon",
        added_dimension_identity=registry.get("horizon").dimension_identity,
        justification_type=ExpansionJustificationType.RESIDUAL_STRUCTURE,
        evidence_reference="finding:wave2-test",
        unresolved_structure=True,
        evidence_boundary=EVIDENCE_BOUNDARY,
    )
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry,
        parent=parent, parent_decision=parent_decision, justification=justification)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(EligibilityReasonCode.INVALID_DEPTH_STEP)
    # A child that DROPS a parent dimension is equally invalid.
    dropped = ResearchInteraction.admitted_from(registry, ["horizon", "strategy_family"])
    assert not parent_step_is_valid(parent, dropped)


def test_valid_3d_progression_with_justification_is_permitted(registry):
    """(13) 1D -> 2D -> 3D, each step justified, ends at PERMIT."""
    one, one_decision = _permitted(registry, ["regime"])
    two = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    two_justification = _justification(one, two, registry, added_key="horizon")
    two_decision = evaluate_interaction_eligibility(
        two, _freeze(two, registry), registry, parent=one,
        parent_decision=one_decision, justification=two_justification)
    assert two_decision.state is EligibilityState.PERMIT

    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    three_justification = _justification(two, three, registry,
                                         added_key="strategy_family")
    three_decision = evaluate_interaction_eligibility(
        three, _freeze(three, registry), registry, parent=two,
        parent_decision=two_decision, justification=three_justification)
    assert three_decision.state is EligibilityState.PERMIT
    assert three_decision.depth == 3
    assert parent_step_is_valid(two, three)


def test_huge_sample_alone_cannot_authorise_3d(registry):
    """(14) 3D with an enormous sample but no justification -> REFUSE."""
    one, one_decision = _permitted(registry, ["regime"])
    two = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    two_justification = _justification(one, two, registry, added_key="horizon")
    two_decision = evaluate_interaction_eligibility(
        two, _freeze(two, registry), registry, parent=one,
        parent_decision=one_decision, justification=two_justification)

    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    # A truly enormous sample, and no expansion justification at all.
    decision = evaluate_interaction_eligibility(
        three, _freeze(three, registry, joint=10_000_000), registry,
        parent=two, parent_decision=two_decision)
    assert decision.state is EligibilityState.REFUSE
    assert decision.has_reason(
        EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED)
    # And with a justification that explicitly reports NO unresolved structure.
    no_unresolved = _justification(two, three, registry,
                                   added_key="strategy_family", unresolved=False)
    refused = evaluate_interaction_eligibility(
        three, _freeze(three, registry, joint=10_000_000), registry,
        parent=two, parent_decision=two_decision, justification=no_unresolved)
    assert refused.state is EligibilityState.REFUSE
    assert refused.has_reason(EligibilityReasonCode.EXPANSION_NOT_SUPPORTED)



# --- 15. Depth > 3 uses the same recursive rule ------------------------------


def test_depth_beyond_three_uses_the_same_recursive_rule(registry):
    """(15) depth 4 follows exactly the depth-2/3 rules; no special case exists."""
    source = inspect.getsource(gate_module)
    # AST-level proof: no comparison in executable code branches on a depth VALUE.
    tree = ast.parse(source)
    depth_value_comparisons = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(comparator, ast.Constant) and comparator.value in (2, 3, 4, 5)
            and isinstance(node.left, ast.Attribute) and node.left.attr == "depth"
            for comparator in node.comparators
        )
    ]
    assert depth_value_comparisons == []
    # No maximum-depth constant and no per-depth special case.
    for forbidden in ("MAX_DEPTH", "MAX_INTERACTION_DEPTH", "max_depth"):
        assert forbidden not in source
        assert not hasattr(gate_module, forbidden)

    one, one_decision = _permitted(registry, ["regime"])
    two = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    two_decision = evaluate_interaction_eligibility(
        two, _freeze(two, registry), registry, parent=one,
        parent_decision=one_decision,
        justification=_justification(one, two, registry, added_key="horizon"))
    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    three_decision = evaluate_interaction_eligibility(
        three, _freeze(three, registry), registry, parent=two,
        parent_decision=two_decision,
        justification=ExpansionJustification.create(
            parent_interaction_identity=two.interaction_identity,
            parent_depth=2,
            child_interaction_identity=three.interaction_identity,
            added_dimension_key="strategy_family",
            added_dimension_identity=registry.get(
                "strategy_family").dimension_identity,
            justification_type=ExpansionJustificationType.EXPLICIT_FOLLOW_UP,
            evidence_reference="finding:wave2-test",
            unresolved_structure=True,
            evidence_boundary=EVIDENCE_BOUNDARY))
    assert three_decision.state is EligibilityState.PERMIT

    # Depth 4: no justification -> the SAME EXPANSION_JUSTIFICATION_REQUIRED.
    four = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family", "session"])
    assert four.depth == 4
    unjustified = evaluate_interaction_eligibility(
        four, _freeze(four, registry), registry, parent=three,
        parent_decision=three_decision)
    assert unjustified.state is EligibilityState.REFUSE
    assert unjustified.has_reason(
        EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED)

    # Depth 4: skipping 2D -> 4D is mechanically impossible.
    assert not parent_step_is_valid(two, four)

    # Depth 4: with a valid justification it is permitted, like any other depth.
    justified = evaluate_interaction_eligibility(
        four, _freeze(four, registry), registry, parent=three,
        parent_decision=three_decision,
        justification=_justification(three, four, registry, added_key="session"))
    assert justified.state is EligibilityState.PERMIT
    assert justified.depth == 4


# --- 16-18. Feasibility specification identity -------------------------------


def test_equivalent_feasibility_specs_share_one_identity():
    """(16) equivalent specifications -> identical identity, any construction."""
    left = _spec(min_common_observations=60, min_slice_observations=30)
    right = FeasibilitySpecification.create(
        "wave2_explicit", min_slice_observations=30, min_common_observations=60,
        label="A completely different label")
    assert left.feasibility_identity == right.feasibility_identity
    assert left.semantic_identity == right.semantic_identity
    assert is_feasibility_identity(left.feasibility_identity)
    assert left.feasibility_identity.startswith(FEASIBILITY_ID_PREFIX)


def test_different_thresholds_produce_different_spec_identities():
    """(17) 60 vs 61 observations, 30 vs 40 per slice: four distinct identities."""
    base = _spec(min_common_observations=60, min_slice_observations=30)
    identities = {
        base.feasibility_identity,
        _spec(min_common_observations=61,
              min_slice_observations=30).feasibility_identity,
        _spec(min_common_observations=60,
              min_slice_observations=40).feasibility_identity,
        _spec(min_common_observations=61,
              min_slice_observations=40).feasibility_identity,
    }
    assert len(identities) == 4


def test_no_silent_or_default_scientific_threshold_exists():
    """(18) every threshold defaults to None and is part of the identity."""
    bare = FeasibilitySpecification.create("wave2_no_thresholds")
    assert bare.min_common_observations is None
    assert bare.min_slice_observations is None
    assert bare.required_common_coverage is None
    assert bare.require_all_declared_cells is False
    assert bare.chronology_requirement is None
    assert bare.declares_no_thresholds
    # A specification with no requirements is NOT the same specification as one
    # that declares a minimum.
    assert (bare.feasibility_identity
            != _spec(min_common_observations=1).feasibility_identity)
    # No numeric threshold constant is hardcoded anywhere in the Wave 2 modules.
    for module in (feasibility_module, freeze_module, gate_module):
        source = inspect.getsource(module)
        assert "min_common_observations=60" not in source
        assert "min_slice_observations=30" not in source
        assert "DEFAULT_MIN" not in source
        assert "default_min" not in source
    # A nonsensical threshold is rejected rather than silently corrected.
    with pytest.raises(FeasibilityValidationError):
        _spec(min_common_observations=0)
    with pytest.raises(FeasibilityValidationError):
        _spec(required_common_coverage=1.5)
    with pytest.raises(FeasibilityValidationError):
        _spec(chronology_requirement="SOMETIME")


# --- 19-23. The T0 evidence freeze ------------------------------------------


def test_evidence_freeze_is_immutable(registry):
    """(19) a frozen snapshot cannot be mutated after construction."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    freeze = _freeze(interaction, registry)
    with pytest.raises(dataclasses.FrozenInstanceError):
        freeze.evidence_boundary = "2099-01-01T00:00:00Z"
    with pytest.raises(dataclasses.FrozenInstanceError):
        freeze.t0 = "2099-01-01T00:00:00Z"
    with pytest.raises(dataclasses.FrozenInstanceError):
        freeze.common_support.joint_usable_observations = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        freeze.specification.min_common_observations = 1
    assert freeze.evidence_identity == _freeze(interaction, registry).evidence_identity


def test_freeze_identity_covers_evidence_boundary_and_population_not_timestamp(
        registry):
    """(20) T0 alone is never enough; the boundary and population are hashed."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    base = _freeze(interaction, registry, t0="2026-01-01T00:00:00Z")
    later = _freeze(interaction, registry, t0="2030-12-31T23:59:59Z")
    # A different T0 alone does NOT change the frozen evidence identity.
    assert base.evidence_identity == later.evidence_identity
    assert base.t0 != later.t0

    # Moving the EVIDENCE BOUNDARY does.
    moved = _freeze(interaction, registry, boundary="2026-09-26T00:00:00Z")
    assert moved.evidence_identity != base.evidence_identity

    # Changing the JOINT POPULATION does.
    other_population = _freeze(
        interaction, registry, population_ref="population:joint_v2")
    assert other_population.evidence_identity != base.evidence_identity

    # Changing the DATASET FINGERPRINT does.
    other_fingerprint = _freeze(
        interaction, registry, fingerprint=_fingerprint(seed=99))
    assert other_fingerprint.evidence_identity != base.evidence_identity

    # Changing the JOINT COUNT does.
    other_count = _freeze(interaction, registry, joint=299)
    assert other_count.evidence_identity != base.evidence_identity

    assert is_evidence_freeze_identity(base.evidence_identity)


def test_equivalent_frozen_inputs_produce_a_deterministic_decision(registry):
    """(21) re-deciding identical frozen inputs yields the identical decision."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    first = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry, t0=T0)
    second = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry,
        t0="2027-05-05T05:05:05Z")
    assert first.decision_identity == second.decision_identity
    assert first == dataclasses.replace(second, t0=first.t0)
    assert is_decision_identity(first.decision_identity)
    assert first.decision_identity.startswith(DECISION_ID_PREFIX)
    # A genuinely different verdict over the same evidence is a different decision.
    refused = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry, joint=1), registry, t0=T0)
    assert refused.decision_identity != first.decision_identity



def test_altered_evidence_changes_the_frozen_evidence_and_the_decision(registry):
    """(22) a different fingerprint/boundary changes the freeze and the decision."""
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    parent_decision = evaluate_interaction_eligibility(
        parent, _freeze(parent, registry), registry)
    justification = _justification(parent, child, registry, added_key="horizon")

    original = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry, parent=parent,
        parent_decision=parent_decision, justification=justification)
    assert original.state is EligibilityState.PERMIT

    altered = evaluate_interaction_eligibility(
        child, _freeze(child, registry, fingerprint=_fingerprint(seed=7)),
        registry, parent=parent, parent_decision=parent_decision,
        justification=justification)
    assert altered.evidence_identity != original.evidence_identity
    assert altered.decision_identity != original.decision_identity
    # The scientific verdict is unchanged: only the provenance identity moved.
    assert altered.state is EligibilityState.PERMIT


def test_malformed_or_missing_freeze_evidence_fails_closed(registry):
    """(23) a freeze cannot be built without a boundary, a population or identity."""
    interaction = ResearchInteraction.admitted_from(registry, ["regime"])
    with pytest.raises(EvidenceFreezeError):
        _freeze(interaction, registry, boundary="   ")
    with pytest.raises(EvidenceFreezeError):
        _freeze(interaction, registry, population_ref="")
    # A freeze whose support does not cover exactly the proposed interaction.
    other = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    with pytest.raises(EvidenceFreezeError):
        EligibilityEvidenceFreeze.create(
            interaction, _support(other, registry), _spec(),
            evidence_boundary=EVIDENCE_BOUNDARY)
    # The gate refuses a freeze that does not describe the proposal at hand.
    decision = evaluate_interaction_eligibility(
        interaction, _freeze(other, registry), registry)
    assert decision.state is EligibilityState.BLOCKED
    assert decision.has_reason(EligibilityReasonCode.EVIDENCE_FREEZE_UNAVAILABLE)
    # A decision cannot be presented with a forged identity.
    original = evaluate_interaction_eligibility(
        interaction, _freeze(interaction, registry), registry)
    payload = json.loads(json.dumps(original.to_dict()))
    payload["decision_identity"] = "DEC-0000000000000000"
    with pytest.raises(EligibilityGateError):
        EligibilityDecision.from_dict(payload)
    assert EligibilityDecision.from_dict(original.to_dict()) == original


# --- 24. The four states remain distinct ------------------------------------


def test_blocked_waiting_refuse_and_permit_remain_distinct(registry):
    """(24) four different situations, four different states and reason classes."""
    one = ResearchInteraction.admitted_from(registry, ["regime"])
    two = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    parent, parent_decision = _permitted(registry, ["regime"])

    permit = evaluate_interaction_eligibility(
        one, _freeze(one, registry), registry)
    waiting = evaluate_interaction_eligibility(
        one, _freeze(one, registry, joint=23), registry)
    blocked = evaluate_interaction_eligibility(
        one, _freeze(one, registry, joint=0), registry)
    refused = evaluate_interaction_eligibility(
        two, _freeze(two, registry), registry, parent=parent,
        parent_decision=parent_decision)

    assert (permit.state, waiting.state, blocked.state, refused.state) == (
        EligibilityState.PERMIT, EligibilityState.WAITING_DATA,
        EligibilityState.BLOCKED, EligibilityState.REFUSE)
    assert len({permit.state, waiting.state, blocked.state, refused.state}) == 4
    assert len({permit.decision_identity, waiting.decision_identity,
                blocked.decision_identity, refused.decision_identity}) == 4
    assert permit.reasons == ()
    for decision in (waiting, blocked, refused):
        assert decision.reasons
        assert decision.reasons[0].state_class == decision.state.value
    # The taxonomy itself never collapses the three non-PERMIT classes.
    assert reason_class(EligibilityReasonCode.AUTHORITY_MISSING) == "BLOCKED"
    assert reason_class(EligibilityReasonCode.CELL_SUPPORT_INSUFFICIENT) == (
        "WAITING_DATA")
    assert reason_class(EligibilityReasonCode.EXPANSION_NOT_SUPPORTED) == "REFUSE"
    # A hand-built decision whose reasons contradict its state is rejected.
    with pytest.raises(EligibilityGateError):
        EligibilityDecision(
            interaction_identity=one.interaction_identity,
            depth=1,
            state=EligibilityState.PERMIT,
            reasons=(EligibilityReason(
                code=EligibilityReasonCode.AUTHORITY_MISSING, detail="mismatch"),),
            evidence_identity=_freeze(one, registry).evidence_identity,
            specification_identity=_spec().feasibility_identity,
        )



# --- 25-27. No generation, no automatic child, no search --------------------


def test_no_generated_research_record_is_created_by_feasibility_evaluation(
        registry):
    """(25) evaluating eligibility never registers a GEN-* research record."""
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
        STORE_FORMAT,
    )
    import inspect as _inspect

    # The gate reaches neither the Wave 0 store nor any record-creation API.
    gate_source = _inspect.getsource(gate_module)
    assert "GeneratedResearchRecord" not in gate_source
    assert "GeneratedResearchProposal" not in gate_source
    assert "GeneratedResearchStore" not in gate_source
    for forbidden in ("register", "create_record", "append", "save"):
        assert not hasattr(gate_module, forbidden)

    # A full evaluation cycle leaves the canonical registry and any store intact.
    from research_engine.registry.research_question_registry import REGISTRY
    before = len(REGISTRY)
    parent, parent_decision = _permitted(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    evaluate_interaction_eligibility(
        three, _freeze(three, registry, joint=10_000_000), registry,
        parent=child, parent_decision=parent_decision)
    assert len(REGISTRY) == before == 70

    # A store opened around the evaluation stays empty.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = GeneratedResearchStore(f"{tmp}/generated.json")
        evaluate_interaction_eligibility(
            three, _freeze(three, registry), registry, parent=child,
            parent_decision=parent_decision,
            justification=_justification(child, three, registry,
                                         added_key="strategy_family"))
        assert store.all() == ()
        assert not store.path.exists()


def test_no_automatic_child_interaction_is_generated(registry):
    """(26) a PERMIT never constructs the next interaction itself."""
    parent, parent_decision = _permitted(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry, parent=parent,
        parent_decision=parent_decision,
        justification=_justification(parent, child, registry, added_key="horizon"))
    assert decision.state is EligibilityState.PERMIT
    # The decision names identities only. It holds no interaction object and
    # cannot be used to obtain one.
    assert isinstance(decision.interaction_identity, str)
    assert not isinstance(decision, ResearchInteraction)
    assert not hasattr(decision, "dimensions")
    assert not hasattr(decision, "child")
    assert not hasattr(decision, "next_interaction")
    # Wave 1's own prohibition still holds for the 3D case.
    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    refused = evaluate_interaction_eligibility(
        three, _freeze(three, registry), registry, parent=child,
        parent_decision=child_decision(registry, child, parent, parent_decision))
    assert refused.state is EligibilityState.REFUSE
    assert not hasattr(refused, "dimensions")


def child_decision(registry, child, parent, parent_decision):
    return evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry, parent=parent,
        parent_decision=parent_decision,
        justification=_justification(parent, child, registry, added_key="horizon"))


def _code_symbols(module) -> frozenset[str]:
    """
    Every identifier a module actually EXECUTES: names, attributes, imports,
    call targets and defined callables. Docstrings and comments are excluded, so
    prose that merely *mentions* a forbidden concept is not mistaken for using
    it.
    """
    symbols: set[str] = set()
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, ast.alias):
            symbols.add(node.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    return frozenset(symbols)


def test_no_cartesian_or_search_api_exists():
    """(27) no enumeration, ranking, fallback or search entry point exists."""
    forbidden = (
        "itertools", "product", "combinations", "permutations", "cartesian",
        "all_interactions", "all_slices", "generate_interactions",
        "expand_interactions", "discover_interactions", "suggest", "propose",
        "rank", "search", "candidates_for", "best_interaction", "next_dimension",
        "try_another", "fallback", "most_profitable", "enumerate_values",
    )
    for module in (feasibility_module, freeze_module, gate_module):
        symbols = _code_symbols(module)
        for symbol in forbidden:
            assert symbol not in symbols, f"{module.__name__} must not use {symbol!r}"
            assert not hasattr(module, symbol)
            assert symbol not in getattr(module, "__all__", ())

    gate_symbols = _code_symbols(gate_module)
    # The gate never enumerates the registry; it asks about NAMED dimensions only.
    for enumerator in ("all", "keys", "registered", "get_by_identity", "coverage_of"):
        assert enumerator not in gate_symbols
    assert "is_admitted" in gate_symbols and "is_registered" in gate_symbols
    # `.admitted` appears only as the Wave 1 DimensionReadiness verdict field,
    # never as a registry enumeration.
    tree = ast.parse(inspect.getsource(gate_module))
    admitted_attributes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "admitted"
    ]
    assert admitted_attributes
    for node in admitted_attributes:
        assert isinstance(node.value, ast.Name) and node.value.id == "readiness"


# --- 28-31. Regressions, isolation and production boundaries -----------------


def test_wave1_and_wave0_focused_suites_still_pass():
    """(28-29) Wave 1 and Wave 0 committed suites remain green."""
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_stage3_wave1_governed_dimensions.py",
         "tests/test_stage3_wave0_generated_research.py"],
        cwd=str(repo_root), capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout[-4000:]


def test_canonical_70_isolation_remains_intact(registry):
    """(30) no Wave 2 identity enters the canonical programme or its APIs."""
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    from research_engine.registry.research_question_registry import (
        REGISTRY,
        get_question,
    )

    assert_canonical_70_intact()
    assert len(canonical_inventory()) == 70
    assert len(REGISTRY) == 70
    assert tuple(BASELINE_QUESTION_IDS) == canonical_inventory()

    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    justification = _justification(parent, child, registry, added_key="horizon")
    decision = evaluate_interaction_eligibility(
        child, _freeze(child, registry), registry, parent=parent,
        parent_decision=evaluate_interaction_eligibility(
            parent, _freeze(parent, registry), registry),
        justification=justification)

    wave2_identities = [
        _spec().feasibility_identity,
        _freeze(parent, registry).evidence_identity,
        justification.justification_identity,
        decision.decision_identity,
        parent.interaction_identity,
        child.interaction_identity,
        registry.get("regime").dimension_identity,
    ]
    for identity in wave2_identities:
        assert identity not in canonical_inventory()
        assert get_question(identity) is None
    # None of them is a generated research ID either.
    for identity in wave2_identities:
        assert not is_generated_research_id(identity)
    assert_generated_not_in_canonical_apis([])
    assert len(REGISTRY) == 70


def test_no_production_runtime_config_or_data_collection_integration():
    """(31) Wave 2 has zero production, risk, sizing or config reachability."""
    forbidden_modules = (
        "risk", "execution", "runtime", "broker", "core.pipeline",
        "orchestrator", "governance_gate", "research_cycle_runner",
        "candidate_activation_gate", "baseline_manifest", "config",
    )
    for module in (feasibility_module, freeze_module, gate_module):
        symbols = _code_symbols(module)
        for forbidden in forbidden_modules:
            assert forbidden not in symbols, f"{module.__name__} reaches {forbidden!r}"
        source = inspect.getsource(module)
        for forbidden in ("place_order", "size_position", "apply_", "activate",
                          "promote", "baseline", "submit_order"):
            assert forbidden not in _code_symbols(module)
        # No import-time or runtime I/O, and no path construction.
        assert "Path(" not in source
        assert "open(" not in source
        assert "write_text" not in source
        assert "requests" not in symbols
        assert "urllib" not in symbols
        assert not hasattr(module, "write_at_import")

    # The gate result carries research-eligibility vocabulary only.
    single = ResearchInteraction.admitted_from(
        _single_dimension_registry(), ["regime"])
    decision = EligibilityDecision(
        interaction_identity=single.interaction_identity,
        depth=1,
        state=EligibilityState.PERMIT,
        reasons=(),
        evidence_identity=_freeze(
            single, _single_dimension_registry()).evidence_identity,
        specification_identity=_spec().feasibility_identity,
    )
    payload = decision.to_dict()
    for forbidden_key in ("apply_to_trading", "position_size", "risk_limit",
                          "activation", "promotion", "runner_module",
                          "runner_function", "order"):
        assert forbidden_key not in payload
    assert set(payload) == {
        "kind", "schema_version", "interaction_identity", "depth", "state", "reasons",
        "evidence_identity", "specification_identity", "parent_justification_identity",
        "t0", "semantic_identity", "decision_identity"}


def _single_dimension_registry() -> DimensionRegistry:
    """A minimal registry with exactly one admitted governed dimension."""
    registry = DimensionRegistry()
    dimension = _dimension("regime", "market.regime")
    registry.register(dimension)
    registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    return registry


# --- CORE STAGE 3 SAFETY DEMONSTRATION ---------------------------------------


def test_signoff_1d_permitted_2d_justified_3d_refused_with_no_question_created(
        registry):
    """
    The core Stage 3 Wave 2 sign-off scenario.

        1. construct valid ADMITTED 1D evidence and PERMIT it;
        2. provide a justified 2D expansion and PERMIT it;
        3. propose a 3D interaction with sufficient raw AND common sample, but
           with NO valid unresolved-heterogeneity / expansion justification;
        4. assert the exact result is REFUSE;
        5. assert no 3D research question is generated or executed.

    A huge sample is explicitly NOT sufficient: step 3 uses 10,000,000 jointly
    eligible observations against a required 60.
    """
    from research_engine.lifecycle.generated_research_store import (
        GeneratedResearchStore,
    )
    from research_engine.registry.research_question_registry import REGISTRY
    import tempfile

    # --- step 1: valid admitted 1D evidence -> PERMIT --------------------
    one = ResearchInteraction.admitted_from(registry, ["regime"])
    one_decision = evaluate_interaction_eligibility(
        one, _freeze(one, registry, joint=300), registry, t0=T0)
    assert one_decision.state is EligibilityState.PERMIT
    assert one_decision.depth == 1

    # --- step 2: justified 2D expansion -> PERMIT ------------------------
    two = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    two_justification = _justification(one, two, registry, added_key="horizon")
    two_decision = evaluate_interaction_eligibility(
        two, _freeze(two, registry, joint=300), registry, parent=one,
        parent_decision=one_decision, justification=two_justification, t0=T0)
    assert two_decision.state is EligibilityState.PERMIT
    assert two_decision.depth == 2

    # --- step 3: 3D with enormous support, NO expansion justification -----
    three = ResearchInteraction.admitted_from(
        registry, ["regime", "horizon", "strategy_family"])
    huge = _freeze(three, registry, joint=10_000_000)
    assert huge.common_support.joint_usable_observations == 10_000_000
    assert huge.common_support.marginal_observations == {
        "horizon": 10000, "regime": 10000, "strategy_family": 10000}

    with tempfile.TemporaryDirectory() as tmp:
        store = GeneratedResearchStore(f"{tmp}/generated.json")
        registry_size_before = len(REGISTRY)

        decision = evaluate_interaction_eligibility(
            three, huge, registry, parent=two, parent_decision=two_decision,
            t0=T0)

        # --- step 4: the exact result is REFUSE ---------------------------
        assert decision.state is EligibilityState.REFUSE
        assert decision.state is EligibilityState.REFUSE
        assert decision.depth == 3
        assert decision.has_reason(
            EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED)
        assert not decision.permitted
        assert not decision.waiting
        assert not decision.blocked
        # The refusal is a scientific result, and it is reproducible.
        repeat = evaluate_interaction_eligibility(
            three, _freeze(three, registry, joint=10_000_000), registry,
            parent=two, parent_decision=two_decision, t0=T0)
        assert repeat.state is EligibilityState.REFUSE
        assert repeat.decision_identity == decision.decision_identity

        # And even WITH a justification that reports no unresolved structure,
        # the answer is still REFUSE -- a huge sample cannot buy depth.
        stale = _justification(two, three, registry,
                               added_key="strategy_family", unresolved=False)
        still_refused = evaluate_interaction_eligibility(
            three, huge, registry, parent=two, parent_decision=two_decision,
            justification=stale, t0=T0)
        assert still_refused.state is EligibilityState.REFUSE
        assert still_refused.has_reason(EligibilityReasonCode.EXPANSION_NOT_SUPPORTED)

        # --- step 5: no 3D research question was generated or executed ---
        assert store.all() == ()
        assert not store.path.exists()
        assert len(REGISTRY) == registry_size_before == 70
        for identity in (three.interaction_identity, huge.evidence_identity,
                         decision.decision_identity, stale.justification_identity):
            assert not is_generated_research_id(identity)
            assert identity not in canonical_inventory()
        # The decision carries identities only; it cannot become a question.
        assert isinstance(decision.interaction_identity, str)
        assert not hasattr(decision, "question_id")
        assert not hasattr(decision, "research_kind")
        assert not hasattr(decision, "dimensions")

        # --- and with a VALID justification the very same 3D does PERMIT ---
        valid = _justification(two, three, registry, added_key="strategy_family")
        permitted = evaluate_interaction_eligibility(
            three, huge, registry, parent=two, parent_decision=two_decision,
            justification=valid, t0=T0)
        assert permitted.state is EligibilityState.PERMIT
        assert permitted.decision_identity != decision.decision_identity
        # Even a PERMIT creates nothing.
        assert store.all() == ()
        assert len(REGISTRY) == 70


