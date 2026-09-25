"""
Stage ③ / Wave 1 — Governed Dimension / Interaction / Slice Algebra.

Focused tests only. Scope:
    - deterministic dimension identity and semantic separation;
    - governed registration, semantic dedup and fail-closed conflicts;
    - authoritative coverage/readiness representation and fail-closed admission;
    - generic 1D / 2D / 3D / deeper interactions with order-insensitive identity;
    - deterministic, order-independent slice identity;
    - the mechanical QUESTION != INTERACTION != SLICE distinction;
    - Wave 0 back-compat: an existing generated record may reference a governed
      interaction, and existing Wave 0 records/identities are untouched;
    - the absence of any Cartesian enumeration API;
    - canonical-70 isolation and the absence of production authority.

These are test fixtures, NOT production research questions. No research,
experiment, hypothesis, finding, candidate or production behaviour is executed.
"""

from __future__ import annotations

import inspect
import json

import pytest

from research_engine.lifecycle import (
    dimension_registry as registry_module,
    governed_dimension as dimension_module,
    research_interaction as interaction_module,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    GENERATED_RESEARCH_SCHEMA_VERSION,
    GeneratedResearchIdentityConflict,
    GeneratedResearchKind,
    GeneratedResearchProposal,
    GeneratedResearchRecord,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    assert_canonical_70_intact,
    assert_generated_research_id_isolated,
    assert_generated_research_isolated,
    canonical_inventory,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.governed_dimension import (
    DIMENSION_SCHEMA_VERSION,
    DimensionCoverage,
    DimensionCoverageError,
    DimensionIdentityConflict,
    DimensionValidationError,
    GovernedDimension,
    candidate_dimension_keys,
    is_dimension_identity,
)
from research_engine.lifecycle.research_interaction import (
    INTERACTION_SCHEMA_VERSION,
    SLICE_ID_PREFIX,
    InteractionSlice,
    InteractionValidationError,
    ResearchInteraction,
    is_interaction_identity,
    is_slice_identity,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)

# ─── Governed test-dimension fixtures (NOT production research questions) ────


def _authority(
    field_path: str,
    *,
    dataset: str = "decision_trace",
    producer: EvidenceProducer = EvidenceProducer.DECISION_TRACE,
    meaning: str = "declared decision-time semantics",
    current: bool = True,
) -> EvidenceAuthority:
    return EvidenceAuthority(
        dataset=dataset,
        schema_version="v1",
        producer=producer,
        field_path=field_path,
        semantic_meaning=meaning,
        current_eligibility=current,
    )


def _dimension(
    key: str,
    field_path: str,
    *,
    producer: EvidenceProducer = EvidenceProducer.DECISION_TRACE,
    label: str = "",
    **kwargs,
) -> GovernedDimension:
    return GovernedDimension.create(
        dimension_key=key,
        authority=_authority(field_path, producer=producer, **kwargs),
        label=label or key.replace("_", " ").title(),
    )


def _code_symbols(module) -> frozenset[str]:
    """
    Every identifier a module actually EXECUTES: names, attributes, imports,
    call targets and defined callables. Docstrings and comments are excluded, so
    prose that merely *mentions* a forbidden concept is not mistaken for using it.
    """
    import ast

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
        elif isinstance(node, ast.ClassDef):
            symbols.add(node.name)
    return frozenset(symbols)


_FIXTURE_DIMENSIONS: tuple[GovernedDimension, ...] = (
    _dimension("regime", "market.regime"),
    _dimension("horizon", "decision.trade_horizon"),
    _dimension("strategy_family", "decision.strategy"),
    _dimension("session", "market.session"),
    _dimension(
        "execution_quality", "execution.quality",
        producer=EvidenceProducer.EXECUTION_RESULTS),
    _dimension(
        "risk_state", "risk.state",
        producer=EvidenceProducer.RISK_DEVIATION),
)


@pytest.fixture
def registry() -> DimensionRegistry:
    """Six governed fixture dimensions: regime, horizon, strategy, session, exec, risk."""
    reg = DimensionRegistry()
    for dimension in _FIXTURE_DIMENSIONS:
        reg.register(dimension)
    return reg


@pytest.fixture
def admitted() -> DimensionRegistry:
    """An INDEPENDENT registry with every fixture dimension admitted for research."""
    reg = DimensionRegistry()
    for dimension in _FIXTURE_DIMENSIONS:
        reg.register(dimension)
        reg.admit(dimension, DimensionCoverage(
            observations_checked=200,
            observations_with_authoritative_value=180,
            authority_satisfied=True,
            currentness_satisfied=True,
        ))
    return reg



# ─── 1/2. Dimension identity: determinism and semantic separation ────────────


def test_dimension_identity_is_deterministic_and_authority_shaped():
    """(1, 2) Equivalent semantics -> same identity; changed authority -> new identity."""
    first = _dimension("regime", "market.regime")
    second = _dimension("regime", "market.regime")
    assert first.semantic_identity == second.semantic_identity
    assert first.dimension_identity == second.dimension_identity
    assert first == second
    assert DIMENSION_SCHEMA_VERSION == 1

    # Cosmetic presentation is not science: a rename does not redefine it.
    renamed = _dimension("regime", "market.regime", label="H4 Regime Classifier")
    assert renamed.dimension_identity == first.dimension_identity

    # Authority is science: every material change is a different dimension.
    for changed in (
        _dimension("regime", "market.h4_regime"),
        _dimension("regime", "market.regime", dataset="shadow_trades"),
        _dimension(
            "regime", "market.regime", producer=EvidenceProducer.SHADOW_TRADES),
        _dimension("regime", "market.regime", meaning="a different declared meaning"),
        _dimension("regime", "market.regime", current=False),
    ):
        assert changed.dimension_identity != first.dimension_identity, changed

    assert is_dimension_identity(first.dimension_identity)
    assert not is_generated_research_id(first.dimension_identity)


def test_dimension_is_immutable_and_rejects_a_forged_identity():
    dimension = _dimension("regime", "market.regime")
    with pytest.raises(Exception):
        dimension.dimension_identity = "DIM-0000000000000000"   # type: ignore[misc]
    with pytest.raises(DimensionIdentityConflict):
        GovernedDimension(
            dimension_key="regime",
            authority=dimension.authority,
            dimension_identity="DIM-0000000000000000",
        )
    with pytest.raises(DimensionIdentityConflict):
        GovernedDimension(
            dimension_key="regime",
            authority=dimension.authority,
            semantic_identity="0" * 64,
        )
    # A version bump is refused: definition version is frozen at 1.
    with pytest.raises(DimensionValidationError):
        GovernedDimension(
            dimension_key="regime",
            authority=dimension.authority,
            schema_version=2,
        )



# ─── 3/4/5. Registration, semantic dedup, conflicting identity ──────────────


def test_governed_registration_dedup_and_conflict_fail_closed(registry: DimensionRegistry):
    """(3, 4, 5) Explicit registration, semantic dedup, no silent redefinition."""
    assert registry.keys() == (
        "execution_quality", "horizon", "regime", "risk_state",
        "session", "strategy_family")
    assert len(registry) == 6
    assert registry.get("regime").dimension_key == "regime"
    assert registry.get_by_identity(
        registry.get("regime").semantic_identity) is registry.get("regime")

    # (4) Semantic dedup: an equivalent re-registration is the same dimension.
    equivalent = _dimension("regime", "market.regime", label="Different label")
    assert registry.register(equivalent) is registry.get("regime")
    assert len(registry) == 6

    # (5) Conflicting identity fails closed: a governed key is never redefined.
    with pytest.raises(DimensionIdentityConflict):
        registry.register(_dimension("regime", "market.h4_regime"))
    assert registry.get("regime").authority.field_path == "market.regime"

    # A discovered field is never admitted merely by being discovered.
    discovered = candidate_dimension_keys([
        {"market": {"regime": "TRENDING"}, "strategy": {"family": "FAMILY_A"}},
    ])
    assert "regime" in discovered
    assert "volatility" not in registry
    with pytest.raises(DimensionValidationError):
        ResearchInteraction.admitted_from(registry, ["volatility"])



# ─── 7/8. Coverage, readiness and fail-closed authority ────────────────────


def test_coverage_readiness_representation_and_missing_authority_fails_closed(
    registry: DimensionRegistry,
):
    """(7, 8) Coverage is deterministic metadata; missing authority fails closed."""
    from research_engine.evidence.base import lineage_coverage

    # Coverage is derived by CONSUMING the repository's existing lineage counters.
    records = [
        {"market": {"regime": "TRENDING"}},
        {"market": {"regime": "RANGING"}},
        {"market": {}},
    ]
    lineage = lineage_coverage(records, ("market.regime",))
    coverage = registry.get("regime").coverage(lineage)
    assert coverage.observations_checked == 3
    assert coverage.observations_with_authoritative_value == 2
    assert coverage.coverage_fraction == pytest.approx(2 / 3)
    assert coverage.authority_satisfied is True
    assert coverage.currentness_satisfied is True
    assert coverage.rejection_reason is None
    assert coverage.has_coverage is True

    # A field that exists but has no usable authority is NOT ready.
    missing = registry.get("regime").coverage(None)
    assert missing.has_coverage is False
    assert missing.rejection_reason
    readiness = registry.readiness("regime", missing)
    assert readiness.admitted is False
    assert readiness.blocking_reason

    # Currentness is authoritative: an ineligible authority is never ready.
    stale = _dimension("regime", "market.regime", current=False)
    assert stale.currentness_satisfied is False
    assert stale.coverage(lineage).has_coverage is False

    # (8) Missing authority fails closed at construction: it cannot even be defined.
    for dataset, field_path, meaning in (
        ("", "market.regime", "m"),
        ("decision_trace", "", "m"),
        ("decision_trace", "market.regime", ""),
    ):
        with pytest.raises(DimensionValidationError):
            GovernedDimension.create(
                dimension_key="regime",
                authority=_authority(
                    field_path, dataset=dataset, meaning=meaning),
            )

    # Admitting without usable coverage fails closed.
    registry.admit(registry.get("regime"), coverage)
    for unusable in (
        DimensionCoverage(0, 0, True, True),
        DimensionCoverage(10, 0, True, True),
        DimensionCoverage(10, 10, False, True),
        DimensionCoverage(10, 10, True, False),
        DimensionCoverage(10, 10, True, True, rejection_reason="blocked upstream"),
    ):
        with pytest.raises(DimensionCoverageError):
            registry.admit(registry.get("horizon"), unusable)
    assert registry.is_admitted("horizon") is False



# ─── 6. Admission gate on interactions ─────────────────────────────────────


def test_unadmitted_or_ungoverned_dimension_cannot_enter_an_admitted_interaction(
    registry: DimensionRegistry,
    admitted: DimensionRegistry,
):
    """(6) Registration is not admission; only admitted dimensions may interact."""
    assert registry.is_registered("horizon") is True
    assert registry.is_admitted("horizon") is False
    with pytest.raises(DimensionCoverageError):
        ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    with pytest.raises(DimensionCoverageError):
        ResearchInteraction.admitted_from(registry, ["horizon"])
    with pytest.raises(DimensionValidationError):
        ResearchInteraction.admitted_from(registry, ["not_a_dimension"])
    # The admitted registry passes the same gate.
    assert ResearchInteraction.admitted_from(admitted, ["regime", "horizon"]).depth == 2



# ─── 9-14. Interactions: depth, genericity, order, duplicates ───────────────


def test_generic_interaction_depths_one_two_three_and_deeper(admitted: DimensionRegistry):
    """(9, 10, 12, 13) One API represents 1D, 2D, 3D and deeper generically."""
    one = ResearchInteraction.admitted_from(admitted, ["regime"])
    two = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    three = ResearchInteraction.admitted_from(
        admitted, ["regime", "horizon", "strategy_family"])
    four = ResearchInteraction.admitted_from(
        admitted, ["regime", "horizon", "strategy_family", "session"])

    assert (one.depth, two.depth, three.depth, four.depth) == (1, 2, 3, 4)
    assert INTERACTION_SCHEMA_VERSION == 1
    assert one.interaction_identity != two.interaction_identity
    assert two.interaction_identity != three.interaction_identity
    assert three.interaction_identity != four.interaction_identity

    # The same generic API carries an execution x risk interaction. Canonical
    # order is by governed identity, so it is deliberately not key-alphabetical.
    execution_risk = ResearchInteraction.admitted_from(
        admitted, ["execution_quality", "risk_state"])
    execution_risk_reversed = ResearchInteraction.admitted_from(
        admitted, ["risk_state", "execution_quality"])
    assert execution_risk.depth == 2
    assert set(execution_risk.dimension_keys) == {"execution_quality", "risk_state"}
    assert execution_risk == execution_risk_reversed
    assert execution_risk.interaction_identity == execution_risk_reversed.interaction_identity
    assert execution_risk.interaction_identity not in {
        one.interaction_identity, two.interaction_identity, three.interaction_identity}

    for interaction in (one, two, three, four, execution_risk):
        assert is_interaction_identity(interaction.interaction_identity)
        assert interaction.semantic_material()["schema_version"] == 1
        assert not is_generated_research_id(interaction.interaction_identity)
        assert len(interaction.dimension_identities) == interaction.depth


def test_reversed_caller_order_deduplicates_to_one_interaction(admitted: DimensionRegistry):
    """(11) `horizon x regime` and `regime x horizon` are one interaction."""
    forward = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    reversed_ = ResearchInteraction.admitted_from(admitted, ["horizon", "regime"])
    third_order = ResearchInteraction.create(
        [admitted.get("horizon"), admitted.get("regime")])

    assert forward == reversed_ == third_order
    assert forward.interaction_identity == reversed_.interaction_identity
    assert forward.semantic_identity == third_order.semantic_identity
    assert forward.interaction_identity != ""
    # Order-insensitivity is by construction, not by coincidence of sorting:
    # the identity material is the sorted dimension identity set.
    assert list(forward.semantic_material()["dimension_identities"]) == sorted(
        forward.dimension_identities)
    # A 3D interaction is likewise order-insensitive at every position.
    assert (
        ResearchInteraction.admitted_from(
            admitted, ["regime", "horizon", "strategy_family"]).interaction_identity
        == ResearchInteraction.admitted_from(
            admitted, ["strategy_family", "regime", "horizon"]).interaction_identity
    )


def test_duplicate_dimension_inside_an_interaction_is_rejected(
    admitted: DimensionRegistry,
):
    """(14) A repeated dimension is an error, not a silent dedup."""
    regime = admitted.get("regime")
    with pytest.raises(InteractionValidationError):
        ResearchInteraction.create([regime, regime])
    with pytest.raises(InteractionValidationError):
        ResearchInteraction.admitted_from(admitted, ["regime", "regime"])
    with pytest.raises(InteractionValidationError):
        ResearchInteraction.create([])
    # The rejected constructions changed nothing: the registry and a valid
    # interaction are unaffected.
    assert admitted.is_admitted("regime") is True
    assert ResearchInteraction.admitted_from(
        admitted, ["regime", "horizon"]).depth == 2



# ─── 15-20. Slices: deterministic identity and exact assignment ─────────────


def test_slice_identity_is_deterministic_and_assignment_order_independent(
    admitted: DimensionRegistry,
):
    """(15, 16) Order-free deterministic identity; same cell == same slice."""
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    trending_scalp = InteractionSlice.create(
        interaction, {"regime": "TRENDING", "horizon": "SCALP"})
    reordered = InteractionSlice.create(
        interaction, [("horizon", "SCALP"), ("regime", "TRENDING")])
    mapping_reordered = InteractionSlice.create(
        interaction, {"horizon": "SCALP", "regime": "TRENDING"})

    assert trending_scalp == reordered == mapping_reordered
    assert trending_scalp.slice_identity == reordered.slice_identity
    assert trending_scalp.semantic_identity == reordered.semantic_identity
    assert is_slice_identity(trending_scalp.slice_identity)
    assert trending_scalp.slice_identity.startswith(SLICE_ID_PREFIX)
    assert trending_scalp.interaction_identity == interaction.interaction_identity
    assert trending_scalp.depth == 2
    assert trending_scalp.value_of("regime") == "TRENDING"
    assert trending_scalp.value_of("horizon") == "SCALP"

    # (20) A different cell value is a different slice.
    ranging_scalp = InteractionSlice.create(
        interaction, {"regime": "RANGING", "horizon": "SCALP"})
    trending_intraday = InteractionSlice.create(
        interaction, {"regime": "TRENDING", "horizon": "INTRADAY"})
    identities = {
        trending_scalp.slice_identity,
        ranging_scalp.slice_identity,
        trending_intraday.slice_identity,
    }
    assert len(identities) == 3

    # A slice identity is bound to its parent interaction: the same value pair in
    # a different interaction is a different cell.
    other = ResearchInteraction.admitted_from(admitted, ["horizon", "regime"])
    assert other.interaction_identity == interaction.interaction_identity
    different_parent = InteractionSlice.create(
        ResearchInteraction.admitted_from(admitted, ["regime", "execution_quality"]),
        {"regime": "TRENDING", "execution_quality": "GOOD"},
    )
    assert different_parent.slice_identity != trending_scalp.slice_identity
    assert InteractionSlice.create(other, {"regime": "TRENDING", "horizon": "SCALP"}
                                   ).slice_identity == trending_scalp.slice_identity


def test_slice_rejects_missing_extra_unknown_and_duplicate_assignments(
    admitted: DimensionRegistry,
):
    """(17, 18, 19) A cell is exactly its interaction: no more, no less, no twice."""
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    one = ResearchInteraction.admitted_from(admitted, ["regime"])

    # (17) Missing assignment.
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(interaction, {"regime": "TRENDING"})
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(one, {})

    # (18) Extra dimension.
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(
            one, {"regime": "TRENDING", "horizon": "SCALP"})

    # (19) Unknown dimension.
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(one, {"mystery": "TRENDING"})

    # Duplicate assignment is rejected rather than last-write-wins.
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(
            one, [("regime", "TRENDING"), ("regime", "RANGING")])

    # Null and non-deterministic values are rejected.
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(one, {"regime": None})
    with pytest.raises(InteractionValidationError):
        InteractionSlice.create(one, {"regime": {"a", "b"}})



# ─── 21/22. One interaction, many slices, no question proliferation ─────────


def test_one_interaction_supports_many_slices_without_question_proliferation(
    admitted: DimensionRegistry, tmp_path,
):
    """(21, 22) Cells never mint research questions or `GEN-*` identities."""
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    cells = [
        InteractionSlice.create(interaction, {"regime": r, "horizon": h})
        for r in ("TRENDING", "RANGING", "TRANSITIONAL")
        for h in ("SCALP", "INTRADAY", "EXTENDED")
    ]
    assert len(cells) == 9
    assert len({cell.slice_identity for cell in cells}) == 9
    assert {cell.interaction_identity for cell in cells} == {interaction.interaction_identity}

    # A slice is a CELL, not a question: it holds no generated research ID and
    # writing a store full of slices mints nothing.
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    for cell in cells:
        assert not is_generated_research_id(cell.slice_identity)
        assert not is_generated_research_id(cell.semantic_identity)
        assert not is_interaction_identity(cell.slice_identity)
        assert "generated_research_id" not in cell.to_dict()
        assert "research_kind" not in cell.to_dict()
    assert len(store) == 0
    assert not (tmp_path / "generated_research.json").exists()


# ─── 9. QUESTION != INTERACTION != SLICE ───────────────────────────────────


def test_question_interaction_and_slice_are_mechanically_distinct(
    admitted: DimensionRegistry, tmp_path,
):
    """(9) A question owns ONE interaction; the interaction owns MANY slices."""
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    proposal = GeneratedResearchProposal(
        research_kind=GeneratedResearchKind.INTERACTION,
        trigger_ref="finding:FT-1",
        target_kind="EXIT_POLICY",
        target_ref="exit_policy_performance",
        dimension_ref=interaction.interaction_identity,
    )
    record = GeneratedResearchRecord.create(
        proposal, created_at="2026-01-01T00:00:00+00:00")
    slices = [
        InteractionSlice.create(interaction, {"regime": r, "horizon": h})
        for r in ("TRENDING", "RANGING")
        for h in ("SCALP", "INTRADAY")
    ]

    # One question identity, one interaction, four cells.
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    assert store.register(proposal).generated_research_id == record.generated_research_id
    assert len(store) == 1

    # (22) Slices never receive `GEN-*` IDs, in any namespace.
    for cell in slices:
        assert not cell.slice_identity.startswith(GENERATED_RESEARCH_ID_PREFIX)
        assert not is_generated_research_id(cell.slice_identity)
    assert len(store) == 1
    assert record.dimension_ref == interaction.interaction_identity
    assert record.dimension_ref not in {cell.slice_identity for cell in slices}



# ─── 23. Wave 0 integration and back-compat ────────────────────────────────


def test_wave0_record_can_reference_an_interaction_without_breaking_wave0(
    admitted: DimensionRegistry, tmp_path,
):
    """(23) The reserved Wave 0 extension point, used without redefining it."""
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    base = dict(
        research_kind=GeneratedResearchKind.INTERACTION,
        trigger_ref="finding:FT-1",
        target_kind="EXIT_POLICY",
        target_ref="exit_policy_performance",
    )

    # A pre-Wave-1 record: `dimension_ref=None` remains fully valid, and its
    # semantic identity is unchanged by Wave 1.
    legacy_proposal = GeneratedResearchProposal(**base, dimension_ref=None)
    legacy = GeneratedResearchRecord.create(
        legacy_proposal, created_at="2026-01-01T00:00:00+00:00")
    assert legacy.dimension_ref is None
    assert legacy.generated_research_id.startswith(GENERATED_RESEARCH_ID_PREFIX)
    assert GENERATED_RESEARCH_SCHEMA_VERSION == 1
    assert GeneratedResearchRecord.from_dict(legacy.to_dict()) == legacy

    # A new record referencing the governed interaction is a DISTINCT identity.
    referenced = GeneratedResearchRecord.create(
        GeneratedResearchProposal(**base, dimension_ref=interaction.interaction_identity),
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert referenced.dimension_ref == interaction.interaction_identity
    assert referenced.semantic_identity != legacy.semantic_identity
    assert referenced.generated_research_id != legacy.generated_research_id
    assert GeneratedResearchRecord.from_dict(referenced.to_dict()) == referenced

    # Both coexist in one store: the old record is not mutated or reinterpreted.
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    assert store.register(legacy_proposal).generated_research_id == legacy.generated_research_id
    assert store.register(GeneratedResearchProposal(
        **base, dimension_ref=interaction.interaction_identity)).generated_research_id == (
            referenced.generated_research_id)
    assert len(store) == 2

    # Restart-safe: both identities survive a reload unchanged. `created_at` is
    # provenance owned by the store (it keeps the FIRST registration stamp), so
    # the reload is compared on scientific identity, not on wall-clock time.
    reloaded = GeneratedResearchStore(tmp_path / "generated_research.json")
    for original in (legacy, referenced):
        stored = reloaded.get(original.generated_research_id)
        assert stored is not None
        assert stored.semantic_identity == original.semantic_identity
        assert stored.dimension_ref == original.dimension_ref
        assert stored.created_at == reloaded.register(
            GeneratedResearchProposal(
                research_kind=original.research_kind,
                trigger_ref=original.trigger_ref,
                target_kind=original.target_kind,
                target_ref=original.target_ref,
                dimension_ref=original.dimension_ref,
            )).created_at
        assert stored.validate() == stored

    # A dimension identity is a legal Wave 0 `dimension_ref` too.
    single = GeneratedResearchRecord.create(GeneratedResearchProposal(
        **base, dimension_ref=admitted.get("regime").dimension_identity))
    assert single.dimension_ref == admitted.get("regime").dimension_identity

    # Forged identity material still fails closed.
    tampered = dict(legacy.to_dict(), dimension_ref=interaction.interaction_identity)
    with pytest.raises(GeneratedResearchIdentityConflict):
        GeneratedResearchRecord.from_dict(tampered)


# ─── 24. No Cartesian enumeration (hard invariant) ─────────────────────────


def test_no_cartesian_enumeration_api_exists():
    """(24) Nothing generates combinations, pairs, triples or exhaustive slices."""
    forbidden = (
        "itertools", "product", "combinations", "permutations", "combinations_with_replacement",
        "cartesian", "all_interactions", "all_slices", "generate_interactions",
        "pairs", "triples", "expand", "expand_interactions", "enumerate_values",
        "discover_interactions", "suggest", "propose",
    )
    for module in (dimension_module, registry_module, interaction_module):
        symbols = _code_symbols(module)
        for symbol in forbidden:
            assert symbol not in symbols, f"{module.__name__} must not use {symbol!r}"
            assert not hasattr(module, symbol)
        assert symbol not in getattr(module, "__all__", ())

    # The registry lists what was explicitly registered; it never combines them.
    registry = DimensionRegistry()
    for key, field_path in (
        ("regime", "market.regime"),
        ("horizon", "decision.trade_horizon"),
        ("strategy_family", "decision.strategy"),
    ):
        registry.register(_dimension(key, field_path))
    assert len(registry) == 3
    assert not any(
        callable(getattr(registry, name, None))
        for name in ("interactions", "all_interactions", "slices", "expand", "cartesian")
    )



# ─── 25. Canonical-70 isolation ────────────────────────────────────────────


def test_canonical_70_isolation_remains_intact(admitted: DimensionRegistry):
    """(25) Generated dimension/interaction/slice identities never enter canonical APIs."""
    from research_engine.registry.research_question_registry import (
        REGISTRY,
        get_question,
    )

    assert_canonical_70_intact()
    inventory = canonical_inventory()
    assert len(inventory) == 70

    governed = []
    for dimension in admitted.all():
        governed.append(dimension.dimension_identity)
    interaction = ResearchInteraction.admitted_from(
        admitted, ["regime", "horizon", "strategy_family"])
    governed.append(interaction.interaction_identity)
    for regime in ("TRENDING", "RANGING"):
        for horizon in ("SCALP", "INTRADAY"):
            governed.append(InteractionSlice.create(
                interaction,
                {"regime": regime, "horizon": horizon, "strategy_family": "FAMILY_A"},
            ).slice_identity)

    # None of them is a canonical ID, and none is visible through canonical APIs.
    for identity in governed:
        assert identity not in inventory
        assert get_question(identity) is None
    assert len(REGISTRY) == 70
    assert_canonical_70_intact()

    # The full Wave 0 isolation guard passes with these governed identities in play.
    record = GeneratedResearchRecord.create(GeneratedResearchProposal(
        research_kind=GeneratedResearchKind.INTERACTION,
        trigger_ref="finding:FT-1",
        target_kind="EXIT_POLICY",
        target_ref="exit_policy_performance",
        dimension_ref=interaction.interaction_identity,
    ))
    assert_generated_research_id_isolated(record.generated_research_id)
    assert_generated_research_isolated([record.generated_research_id])

    # Canonical definition versions remain 1 and the baseline is untouched.
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    assert tuple(BASELINE_QUESTION_IDS) == inventory


# ─── 26. Zero production / runtime authority ───────────────────────────────


def test_no_production_or_runtime_integration_exists(admitted: DimensionRegistry):
    """(26) Dimensions/interactions/slices reach no production surface."""
    forbidden = (
        "governance_gate", "GovernanceGate", "candidate_activation_gate",
        "candidate_promotion", "production_adapter", "research_cycle_runner",
        "orchestrator", "position_sizing", "risk_config", "baseline_activation",
        "run_experiment", "apply_treatment", "discover_questions", "generate_questions",
        "agenda", "prioritise", "prioritize", "subprocess", "requests", "boto3",
        "open", "execute", "write_text", "os", "boto", "s3", "http",
    )
    for module in (dimension_module, registry_module, interaction_module):
        symbols = _code_symbols(module)
        for symbol in forbidden:
            assert symbol not in symbols, f"{module.__name__} must not use {symbol!r}"

    # The persisted artefacts are inert descriptions: no runner, state or config.
    interaction = ResearchInteraction.admitted_from(admitted, ["regime", "horizon"])
    cell = InteractionSlice.create(interaction, {"regime": "TRENDING", "horizon": "SCALP"})
    assert set(interaction.to_dict()) == {
        "schema_version", "depth", "dimension_identities", "dimension_keys",
        "semantic_identity", "interaction_identity", "dimensions"}
    assert set(cell.to_dict()) == {
        "schema_version", "slice_identity", "semantic_identity", "interaction_identity",
        "depth", "assignments"}
    for forbidden_key in ("runner_module", "runner_function", "report_filename",
                          "status", "lifecycle_state", "priority", "decision"):
        assert forbidden_key not in interaction.to_dict()
        assert forbidden_key not in cell.to_dict()

    # No import-time write: importing these modules creates nothing on disk.
    for module in (dimension_module, registry_module, interaction_module):
        assert not hasattr(module, "write_at_import")
        assert "Path(" not in inspect.getsource(module)



# ─── Deterministic serialisation / restart-safe registry round-trip ─────────


def test_registry_and_identity_serialisation_are_deterministic(
    admitted: DimensionRegistry, tmp_path,
):
    """Canonical serialisation round-trips; persistence preserves identity."""
    payload = admitted.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert admitted.to_dict() == payload          # deterministic, order-stable

    rebuilt = DimensionRegistry.from_dict(payload)
    assert rebuilt.to_dict() == payload
    for original in admitted.all():
        restored = rebuilt.get(original.dimension_key)
        assert restored is not None
        assert restored.dimension_identity == original.dimension_identity
        assert restored.semantic_identity == original.semantic_identity
        assert restored == original
    assert rebuilt.admitted_keys() == admitted.admitted_keys()

    # Identity survives a JSON file round-trip (restart-safe, no store required).
    store_path = tmp_path / "dimension_registry.json"
    store_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    reloaded = DimensionRegistry.from_dict(
        json.loads(store_path.read_text(encoding="utf-8")))
    assert reloaded.get("regime").dimension_identity == (
        admitted.get("regime").dimension_identity)

    # Malformed / conflicting persisted content fails closed.
    with pytest.raises(DimensionValidationError):
        DimensionRegistry.from_dict({"dimensions": [], "admitted": [], "extra": 1})
    with pytest.raises(DimensionValidationError):
        DimensionRegistry.from_dict({"dimensions": {}, "admitted": []})
    with pytest.raises(DimensionIdentityConflict):
        DimensionRegistry.from_dict({
            "dimensions": [], "admitted": [{"semantic_identity": "0" * 64, "coverage": {
                "observations_checked": 1, "observations_with_authoritative_value": 1,
                "authority_satisfied": True, "currentness_satisfied": True,
                "rejection_reason": None}}]})

    # A drifted derived fraction is an integrity failure, not a silent repair.
    with pytest.raises(DimensionValidationError):
        DimensionCoverage.from_dict({
            "observations_checked": 100, "observations_with_authoritative_value": 90,
            "authority_satisfied": True, "currentness_satisfied": True,
            "rejection_reason": None, "coverage_fraction": 0.5})
    assert DimensionCoverage.from_dict(DimensionCoverage(
        100, 90, True, True).to_dict()) == DimensionCoverage(100, 90, True, True)
