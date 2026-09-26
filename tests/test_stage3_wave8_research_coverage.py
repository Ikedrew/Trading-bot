"""Stage III / Wave 8 recovery: finite observation and bounded coverage."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_DEFINITION_VERSION, CANONICAL_QUESTION_COUNT,
    canonical_identity_snapshot, canonical_inventory,
)
from research_engine.lifecycle.research_coverage import (
    BlindSpotClass, CoverageDeltaKind, CoverageEvidence, CoverageSnapshot,
    CoverageState, ResearchCoverageIdentityConflict, ResearchCoverageValidationError, ResearchInventoryBoundary,
    admit_coverage_curiosity, bounded_negative_claim, compare_coverage,
)
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore
from research_engine.lifecycle.research_observation_space import (
    CompatibilityRelation, CompatibilityRule, EvidenceCapability,
    ObservationCellDeclaration, ObservationSpace,
    ObservationSpaceConstructionPolicy, UnsupportedCartesianCombination,
)

DIM_A = "DIM-AAAAAAAAAAAAAAAA"
DIM_B = "DIM-BBBBBBBBBBBBBBBB"
IXN = "IXN-AAAAAAAAAAAAAAAA"
SLC = "SLC-AAAAAAAAAAAAAAAA"


def declaration(index: int, *, observable: bool = True, population: str = "shadow", dimension: str = DIM_A) -> ObservationCellDeclaration:
    return ObservationCellDeclaration(
        subject_kind="CANONICAL_QUESTION", subject_identity=canonical_inventory()[index],
        population_identity=population, horizon="SHORT", evidence_class="EVENTS",
        dimension_identities=(dimension,),
        evidence_capability=EvidenceCapability.OBSERVABLE if observable else EvidenceCapability.CURRENTLY_UNOBSERVABLE,
    )


def policy(**overrides) -> ObservationSpaceConstructionPolicy:
    values = dict(
        allowed_subject_kinds=("CANONICAL_QUESTION",), allowed_dimensions=(DIM_A,),
        allowed_populations=("shadow",), allowed_horizons=("SHORT",),
        allowed_evidence_classes=("EVENTS",),
        compatibility_rules=(CompatibilityRule("explicit", subject_kinds=("CANONICAL_QUESTION",), dimension_identities=(DIM_A,)),),
    )
    values.update(overrides)
    return ObservationSpaceConstructionPolicy(**values)


def space(count: int = 11) -> ObservationSpace:
    declarations = [declaration(i, observable=i != 9) for i in range(count)]
    return ObservationSpace.construct(policy(), declarations)


def inventory(observation_space: ObservationSpace) -> ResearchInventoryBoundary:
    return ResearchInventoryBoundary(
        observation_space.observation_space_identity,
        question_inventory=("Q-1",), evidence_inventory=("EVD-1",),
        search_inventory=("SRC-1",), opportunity_inventory=("ROP-1",),
        agenda_inventory=("AGD-1",), queue_inventory=("QUE-1",),
        protocol_inventory=("RPL-1",), protocol_freeze_inventory=("PFR-1",),
        treatment_memory_inventory=("TMR-1",), revisit_inventory=("RVD-1",),
        evidence_fingerprint="FP-1")


def demonstration():
    obs = space()
    by_subject = {c.subject_identity: c.cell_identity for c in obs.cells}
    ids = [by_subject[canonical_inventory()[i]] for i in range(11)]
    facts = {
        ids[0]: CoverageEvidence(evidence_refs=("EVD-A",), research_attempt_refs=("TRY-A",), conclusion_refs=("CON-A",)),
        ids[1]: CoverageEvidence(evidence_refs=("EVD-B",), research_attempt_refs=("TRY-B",), evidence_insufficient=True),
        ids[2]: CoverageEvidence(evidence_refs=("EVD-C",)),
        ids[3]: CoverageEvidence(question_refs=("Q-D",)),
        ids[4]: CoverageEvidence(),
        ids[5]: CoverageEvidence(evidence_refs=("EVD-F",), research_attempt_refs=("TRY-F",), partial_coverage=True),
        ids[6]: CoverageEvidence(applicable_memory_refs=("TMR-G1", "TMR-G2"), conflicting_memory_refs=("TMR-G1", "TMR-G2")),
        ids[7]: CoverageEvidence(non_applicable_memory_refs=("TMR-H",), revisit_refs=("RVD-H",)),
        ids[8]: CoverageEvidence(question_refs=("Q-I",), active_research_refs=("QUE-I",), protocol_refs=("RPL-I",), frozen_protocol_refs=("PFR-I",)),
        ids[9]: CoverageEvidence(question_refs=("Q-J",)),
        ids[10]: CoverageEvidence(applicable_memory_refs=("TMR-K",)),
    }
    return obs, ids, facts, CoverageSnapshot.construct(obs, inventory(obs), facts)


def test_policy_identity_is_deterministic_and_provenance_is_excluded():
    left, right = policy(label="one", note="a", created_at="T0"), policy(label="two", note="b", created_at="T1")
    assert left.policy_identity == right.policy_identity
    assert policy(max_interaction_depth=2).policy_identity != left.policy_identity


def test_observation_space_and_cells_are_deterministic_and_universe_sensitive():
    one = space(2); reordered = ObservationSpace.construct(policy(), reversed([declaration(0), declaration(1)]))
    assert one.observation_space_identity == reordered.observation_space_identity
    assert [c.cell_identity for c in one.cells] == [c.cell_identity for c in reordered.cells]
    assert space(3).observation_space_identity != one.observation_space_identity


def test_unsupported_cartesian_unknown_population_and_dimension_fail_closed():
    with pytest.raises(UnsupportedCartesianCombination): ObservationSpace.construct(policy(), [declaration(1, population="live")])
    with pytest.raises(UnsupportedCartesianCombination): ObservationSpace.construct(policy(allowed_dimensions=(DIM_A, DIM_B)), [declaration(1, dimension=DIM_B)])


def test_multiple_rules_do_not_treat_a_nonmatch_as_incompatible():
    rules = (CompatibilityRule("narrow", subject_identities=(canonical_inventory()[2],)),
             CompatibilityRule("actual", subject_identities=(canonical_inventory()[1],)))
    governed = policy(compatibility_rules=rules)
    assert governed.evaluate(declaration(1)) is CompatibilityRelation.COMPATIBLE


def test_explicit_incompatibility_and_interaction_depth_are_enforced():
    deny = CompatibilityRule("deny", CompatibilityRelation.INCOMPATIBLE, subject_identities=(canonical_inventory()[1],))
    with pytest.raises(UnsupportedCartesianCombination): ObservationSpace.construct(policy(compatibility_rules=(deny,)), [declaration(1)])
    interaction = ObservationCellDeclaration("CANONICAL_QUESTION", canonical_inventory()[12], "shadow", "SHORT", "EVENTS", (DIM_A, DIM_B), IXN, SLC)
    governed = policy(allowed_dimensions=(DIM_A, DIM_B), allowed_interactions=(IXN,), allowed_slices=(SLC,), max_interaction_depth=1,
                      compatibility_rules=(CompatibilityRule("ixn", interaction_identities=(IXN,), dimension_identities=(DIM_A, DIM_B)),))
    with pytest.raises(UnsupportedCartesianCombination): ObservationSpace.construct(governed, [interaction])


def test_inventory_is_deterministic_and_binds_every_inspected_layer():
    obs = space(1); one, two = inventory(obs), inventory(obs)
    assert one.inventory_identity == two.inventory_identity
    assert ResearchInventoryBoundary(obs.observation_space_identity, question_inventory=("Q-X",)).inventory_identity != one.inventory_identity


def test_core_a_to_j_classification_and_evidence_knowledge_separation():
    _, ids, _, snapshot = demonstration()
    states = {cid: snapshot.coverage_for(cid).state for cid in ids}
    expected = [CoverageState.OBSERVED_AND_RESEARCHED, CoverageState.RESEARCHED_INSUFFICIENT,
                CoverageState.OBSERVED_NOT_RESEARCHED, CoverageState.QUESTION_EXISTS_NO_EVIDENCE,
                CoverageState.NEVER_EXAMINED, CoverageState.PARTIALLY_RESEARCHED,
                CoverageState.CONFLICTING_RESEARCH, CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED,
                CoverageState.KNOWN_GAP_RESEARCH_PENDING, CoverageState.NOT_OBSERVABLE,
                CoverageState.OBSERVED_AND_RESEARCHED]
    assert [states[cid] for cid in ids] == expected
    assert states[ids[2]] is not CoverageState.OBSERVED_AND_RESEARCHED  # evidence != knowledge
    assert states[ids[3]] is not CoverageState.PARTIALLY_RESEARCHED     # question != attempt


def test_protocol_and_freeze_are_not_completion():
    obs = space(1); cid = obs.cells[0].cell_identity
    snap = CoverageSnapshot.construct(obs, inventory(obs), {cid: CoverageEvidence(protocol_refs=("RPL-X",), frozen_protocol_refs=("PFR-X",))})
    assert snap.coverage_for(cid).state is CoverageState.NEVER_EXAMINED
    assert not snap.coverage_for(cid).evidence.conclusion_refs


def test_unexamined_and_unobservable_are_mechanically_distinct():
    _, ids, _, snapshot = demonstration()
    assert snapshot.coverage_for(ids[4]).state is CoverageState.NEVER_EXAMINED
    assert snapshot.coverage_for(ids[9]).state is CoverageState.NOT_OBSERVABLE
    report = snapshot.bounded_observation_report()
    assert ids[4] in report["OBSERVABLE_BUT_UNEXAMINED"]
    assert ids[9] in report["CONCEPTUAL_BUT_CURRENTLY_UNOBSERVABLE"]


def test_blind_spots_are_closed_deterministic_and_only_for_valid_cells():
    obs, _, _, snapshot = demonstration(); first, second = snapshot.blind_spots(), snapshot.blind_spots()
    assert [b.blind_spot_identity for b in first] == [b.blind_spot_identity for b in second]
    assert all(obs.cell(b.cell_identity) is not None for b in first)
    assert {b.blind_spot_class for b in first}.issuperset({BlindSpotClass.DATA_WITHOUT_RESEARCH, BlindSpotClass.QUESTION_WITHOUT_EVIDENCE, BlindSpotClass.NEVER_EXAMINED, BlindSpotClass.CONFLICTING_KNOWLEDGE, BlindSpotClass.STALE_APPLICABILITY, BlindSpotClass.OBSERVABILITY_GAP})


def test_invalid_cartesian_combination_is_excluded_not_a_blind_spot():
    obs, _, _, snapshot = demonstration()
    invalid = declaration(69, population="live")
    assert not obs.contains(invalid)
    assert all(b.cell_identity != "invalid" for b in snapshot.blind_spots())


def test_negative_claim_is_bounded_to_inventory_and_refuses_outside_cell():
    _, ids, _, snapshot = demonstration()
    assert snapshot.inventory.inventory_identity in bounded_negative_claim(snapshot, ids[4])
    with pytest.raises(ResearchCoverageValidationError): bounded_negative_claim(snapshot, "OBC-FFFFFFFFFFFFFFFF")


def test_active_research_and_applicable_memory_suppress_curiosity():
    _, ids, _, snapshot = demonstration(); signals = admit_coverage_curiosity(snapshot)
    targets = {s.target_ref for s in signals}
    assert ids[8] not in targets and ids[10] not in targets
    assert all(type(s).__name__ == "CuriositySignal" for s in signals)


def test_curiosity_preserves_coverage_provenance_enters_wave3_and_deduplicates():
    _, _, _, snapshot = demonstration(); first = admit_coverage_curiosity(snapshot)
    assert first and all(s.signal_identity.startswith("CSN-") for s in first)
    provenance = first[0].source_provenance
    assert {"blind_spot_identity", "observation_cell_identity", "coverage_snapshot_identity", "observation_space_identity", "inventory_identity", "blind_spot_class"} <= set(provenance)
    assert admit_coverage_curiosity(snapshot, existing_signal_identities=[s.signal_identity for s in first]) == ()


def test_repeated_scans_are_finite_fixed_and_non_recursive():
    obs, _, _, snapshot = demonstration(); expected_blind = len(snapshot.blind_spots())
    seen: set[str] = set()
    for _ in range(20):
        signals = admit_coverage_curiosity(snapshot, existing_signal_identities=seen); seen.update(s.signal_identity for s in signals)
        assert len(obs.cells) == 11 and len(snapshot.blind_spots()) == expected_blind
    assert len(seen) <= expected_blind
    assert {c.population_identity for c in obs.cells} == {"shadow"}
    assert {d for c in obs.cells for d in c.dimension_identities} == {DIM_A}


def test_queries_summary_and_undercoverage_are_derived():
    obs, ids, _, snapshot = demonstration(); summary = snapshot.summary()
    assert summary["total_governed_cells"] == len(obs.cells) == 11
    assert summary["observability_gaps"] == 1 and summary["active_research_gaps"] == 1
    assert snapshot.cells_by_subject(obs.cells[0].subject_identity)
    assert len(snapshot.cells_by_dimension(DIM_A)) == 11
    assert snapshot.cells_by_population("shadow") == obs.cells
    assert ids[2] in {c.cell_identity for c in snapshot.cells_by_state(CoverageState.OBSERVED_NOT_RESEARCHED)}
    assert snapshot.dimension_coverage()[DIM_A] == {"valid_observable_cells": 10, "research_history_cells": 5}


def test_snapshot_identity_ignores_timestamp_and_history_is_immutable():
    obs = space(1); inv = inventory(obs); cid = obs.cells[0].cell_identity
    t0 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E-1",))}, observed_at="T0")
    same = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E-1",))}, observed_at="T9")
    t1 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E-1",), research_attempt_refs=("TRY-1",), conclusion_refs=("CON-1",))})
    assert t0.coverage_snapshot_identity == same.coverage_snapshot_identity != t1.coverage_snapshot_identity
    assert t0.coverage_for(cid).state is CoverageState.OBSERVED_NOT_RESEARCHED
    assert t1.coverage_for(cid).state is CoverageState.OBSERVED_AND_RESEARCHED


def test_coverage_delta_has_governed_events_and_no_change():
    obs = space(1); inv = inventory(obs); cid = obs.cells[0].cell_identity
    t0 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E",))})
    t1 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E",), research_attempt_refs=("T",), conclusion_refs=("C",))})
    assert compare_coverage(t0, t1).changes == ((cid, CoverageDeltaKind.NEWLY_RESEARCHED), (cid, CoverageDeltaKind.NEWLY_CONCLUDED), (cid, CoverageDeltaKind.BLIND_SPOT_RESOLVED))
    assert compare_coverage(t0, t0).changes == (("", CoverageDeltaKind.NO_CHANGE),)


def test_persistence_restart_semantic_dedup_and_historical_snapshots(tmp_path: Path):
    obs = space(1); inv = inventory(obs); cid = obs.cells[0].cell_identity
    t0 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E",))}, observed_at="T0")
    t1 = CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E",), conclusion_refs=("C",))})
    path = tmp_path / "coverage.json"; store = ResearchCoverageStore(path)
    assert store.register(t0) is t0; assert store.register(CoverageSnapshot.construct(obs, inv, {cid: CoverageEvidence(evidence_refs=("E",))}, observed_at="later")).coverage_snapshot_identity == t0.coverage_snapshot_identity
    store.register(t1); restarted = ResearchCoverageStore(path)
    assert len(restarted) == 2 and restarted.get(t0.coverage_snapshot_identity).coverage_for(cid).state is CoverageState.OBSERVED_NOT_RESEARCHED


def test_malformed_and_corrupted_store_fail_closed(tmp_path: Path):
    path = tmp_path / "coverage.json"; path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ResearchCoverageValidationError): ResearchCoverageStore(path)
    path.write_text(json.dumps({"format": "wrong", "schema_version": 1, "snapshots": []}), encoding="utf-8")
    with pytest.raises(ResearchCoverageValidationError): ResearchCoverageStore(path)


def test_persisted_classification_tampering_fails_closed(tmp_path: Path):
    obs = space(1); cid = obs.cells[0].cell_identity
    snapshot = CoverageSnapshot.construct(obs, inventory(obs), {cid: CoverageEvidence(evidence_refs=("E",))})
    path = tmp_path / "coverage.json"; store = ResearchCoverageStore(path); store.register(snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshots"][0]["classifications"][0]["state"] = CoverageState.OBSERVED_AND_RESEARCHED.value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResearchCoverageIdentityConflict): ResearchCoverageStore(path)


def test_canonical_70_is_untouched_and_contains_no_wave8_ids():
    values = canonical_inventory()
    assert CANONICAL_QUESTION_COUNT == len(values) == len(set(values)) == 70
    assert CANONICAL_DEFINITION_VERSION == 1 and set(canonical_identity_snapshot()["definition_versions"].values()) == {1}
    assert not any(v.startswith(("OBS-", "OBC-", "CVS-", "BSP-", "CSN-")) for v in values)


def test_wave8_has_no_execution_network_profitability_or_mutation_authority():
    root = Path("research_engine/lifecycle")
    files = [root / "research_observation_space.py", root / "research_coverage.py", root / "research_blind_spots.py", root / "research_coverage_store.py"]
    forbidden_imports = {"requests", "boto3", "research_cycle_runner", "orchestrator", "candidate_activation_gate"}
    forbidden_calls = {"run_experiment", "apply_treatment", "send_order", "activate_candidate", "promote_candidate"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {n.names[0].name.split(".")[-1] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and n.names}
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not imports & forbidden_imports and not calls & forbidden_calls
        source = path.read_text(encoding="utf-8").lower()
        assert not any(term in source for term in ("expected r", "expectancy", "profitability", "roi", "p&l"))
