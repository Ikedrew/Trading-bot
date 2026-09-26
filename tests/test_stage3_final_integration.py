"""Final Stage III integration, authority, restart and freeze contract."""
from __future__ import annotations

import ast
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from research_engine.lifecycle.curiosity_generator import propose_expansion
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_DEFINITION_VERSION, canonical_identity_snapshot, canonical_inventory,
)
from research_engine.lifecycle.progressive_depth_gate import EligibilityState, evaluate_interaction_eligibility
from research_engine.lifecycle.research_coverage import (
    BlindSpotClass, CoverageDeltaKind, CoverageEvidence, CoverageSnapshot, CoverageState,
    ResearchInventoryBoundary, admit_coverage_curiosity, compare_coverage,
)
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore
from research_engine.lifecycle.research_observation_space import (
    CompatibilityRule, EvidenceCapability, ObservationCellDeclaration,
    ObservationSpace, ObservationSpaceConstructionPolicy,
    UnsupportedCartesianCombination,
)
from research_engine.lifecycle.stage3_freeze_manifest import (
    Stage3FreezeIdentityConflict, Stage3FreezeManifest, current_stage3_freeze_manifest,
)


def _helper(filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"stage3_final_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _coverage_fixture():
    wave4 = _helper("test_stage3_wave4_search_provenance.py")
    registry = wave4.registry.__wrapped__()
    dimension = registry.get("session")
    canonical = canonical_inventory()
    declarations = tuple(
        ObservationCellDeclaration(
            "CANONICAL_QUESTION", canonical[index], "joint_v1", "SHORT", "EVENTS",
            (dimension.dimension_identity,),
            evidence_capability=(EvidenceCapability.CURRENTLY_UNOBSERVABLE if index == 8 else EvidenceCapability.OBSERVABLE))
        for index in range(9))
    policy = ObservationSpaceConstructionPolicy(
        allowed_subject_kinds=("CANONICAL_QUESTION",), allowed_dimensions=(dimension.dimension_identity,),
        allowed_populations=("joint_v1",), allowed_horizons=("SHORT",), allowed_evidence_classes=("EVENTS",),
        compatibility_rules=(CompatibilityRule("explicit_stage3_final", subject_kinds=("CANONICAL_QUESTION",),
                                                dimension_identities=(dimension.dimension_identity,)),))
    space = ObservationSpace.construct(policy, declarations)
    cells = {cell.subject_identity: cell for cell in space.cells}
    ids = [cells[canonical[index]].cell_identity for index in range(9)]
    boundary = ResearchInventoryBoundary(
        space.observation_space_identity, question_inventory=(canonical[1], canonical[3]),
        evidence_inventory=("evidence:A", "evidence:C"), search_inventory=("SRC-fixture",),
        opportunity_inventory=("ROP-fixture",), agenda_inventory=("AGD-fixture",),
        queue_inventory=("QUE-fixture",), protocol_inventory=("RPL-fixture",),
        protocol_freeze_inventory=("PFR-fixture",), treatment_memory_inventory=("TMR-fixture",),
        revisit_inventory=("RVD-fixture",), evidence_fingerprint="sha256:stage3-final")
    facts = {
        ids[0]: CoverageEvidence(evidence_refs=("evidence:A",), conclusion_refs=("result:A",), applicable_memory_refs=("TMR-A",)),
        ids[1]: CoverageEvidence(question_refs=(canonical[1],), research_attempt_refs=("attempt:B",), evidence_insufficient=True),
        ids[2]: CoverageEvidence(evidence_refs=("evidence:C",)),
        ids[3]: CoverageEvidence(question_refs=(canonical[3],)),
        ids[4]: CoverageEvidence(question_refs=(canonical[4],), active_research_refs=("QUE-E",), opportunity_refs=("ROP-E",), protocol_refs=("RPL-E",)),
        ids[5]: CoverageEvidence(applicable_memory_refs=("TMR-F1", "TMR-F2"), conflicting_memory_refs=("TMR-F1", "TMR-F2")),
        ids[6]: CoverageEvidence(non_applicable_memory_refs=("TMR-G",)),
        ids[7]: CoverageEvidence(non_applicable_memory_refs=("TMR-H",), revisit_refs=("RVD-H",)),
        ids[8]: CoverageEvidence(question_refs=(canonical[8],)),
    }
    return wave4, registry, dimension, space, ids, boundary, facts, CoverageSnapshot.construct(space, boundary, facts)


def test_integrated_observation_coverage_curiosity_and_wave2_gate():
    wave4, registry, dimension, space, ids, boundary, facts, snapshot = _coverage_fixture()
    expected = (CoverageState.OBSERVED_AND_RESEARCHED, CoverageState.RESEARCHED_INSUFFICIENT,
                CoverageState.OBSERVED_NOT_RESEARCHED, CoverageState.QUESTION_EXISTS_NO_EVIDENCE,
                CoverageState.KNOWN_GAP_RESEARCH_PENDING, CoverageState.CONFLICTING_RESEARCH,
                CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED,
                CoverageState.HISTORICALLY_RESEARCHED_CONTEXT_CHANGED, CoverageState.NOT_OBSERVABLE)
    assert tuple(snapshot.coverage_for(cid).state for cid in ids) == expected
    blind = next(item for item in snapshot.blind_spots() if item.cell_identity == ids[2])
    assert blind.blind_spot_class is BlindSpotClass.DATA_WITHOUT_RESEARCH
    signal = next(item for item in admit_coverage_curiosity(snapshot) if item.target_ref == ids[2])
    assert signal.source_provenance == {
        "blind_spot_identity": blind.blind_spot_identity, "observation_cell_identity": ids[2],
        "coverage_snapshot_identity": snapshot.coverage_snapshot_identity,
        "observation_space_identity": space.observation_space_identity,
        "inventory_identity": boundary.inventory_identity, "blind_spot_class": "DATA_WITHOUT_RESEARCH"}
    proposal = propose_expansion(signal, registry)
    assert propose_expansion(signal, registry).proposal_identity == proposal.proposal_identity
    wave3 = _helper("test_stage3_wave3_governed_curiosity.py")
    permit = evaluate_interaction_eligibility(proposal.proposed_interaction, wave3._permitting_freeze(proposal.proposed_interaction), registry, t0=wave3.T0)
    waiting = evaluate_interaction_eligibility(proposal.proposed_interaction, wave3._permitting_freeze(proposal.proposed_interaction, joint=10), registry, t0=wave3.T0)
    blocked = evaluate_interaction_eligibility(proposal.proposed_interaction, wave3._permitting_freeze(proposal.proposed_interaction), DimensionRegistry(), t0=wave3.T0)
    assert (permit.state, waiting.state, blocked.state) == (EligibilityState.PERMIT, EligibilityState.WAITING_DATA, EligibilityState.BLOCKED)
    assert not any(item.target_ref == ids[4] for item in admit_coverage_curiosity(snapshot))
    assert {item.signal_identity for item in admit_coverage_curiosity(snapshot)} == {item.signal_identity for item in admit_coverage_curiosity(snapshot)}
    with pytest.raises(UnsupportedCartesianCombination):
        ObservationSpace.construct(space.policy, [replace(space.cells[0].declaration, population_identity="unsupported")])


def test_search_priority_protocol_memory_and_revisit_authorities(tmp_path: Path):
    wave6 = _helper("test_stage3_wave6_research_protocol.py")
    dimensions = wave6.dimensions.__wrapped__()
    selection = wave6.wave4_selection.__wrapped__(dimensions)
    freeze = selection["freeze"]
    opportunity = wave6._opportunity(freeze.freeze_identity, wave6.OpportunityState.READY,
        subject_kind=wave6.OpportunitySubjectKind.SEARCH_SELECTION_FREEZE, search_provenance=selection["payload"])
    from research_engine.lifecycle.research_agenda import AgendaFreeze, ResearchAgenda
    from research_engine.lifecycle.research_priority import POLICY_LEXICOGRAPHIC_V1
    from research_engine.lifecycle.research_queue import ResearchQueue
    from research_engine.lifecycle.research_protocol import ProtocolFreeze
    agenda = ResearchAgenda.build(POLICY_LEXICOGRAPHIC_V1, (opportunity,), evidence_boundary=wave6.EVIDENCE_BOUNDARY)
    queue = ResearchQueue.build(agenda, capacity=1)
    agenda_freeze = AgendaFreeze.freeze(agenda, capacity=1, frozen_at=wave6.T0)
    protocol = wave6._protocol(opportunity, dimensions, **wave6._wave4_kwargs(selection, dimensions, freeze),
                               agenda_identity=agenda.agenda_identity, queue_identity=queue.queue_identity,
                               agenda_freeze_identity=agenda_freeze.freeze_identity)
    protocol_freeze = ProtocolFreeze.freeze_protocol(protocol, frozen_at=wave6.T0)
    assert selection["multiplicity"].family_size == selection["record"].derived_family_size == 3
    assert protocol.search_provenance["multiplicity_family_size"] == 3
    assert queue.queued_identities == (opportunity.opportunity_identity,)
    before = protocol.to_dict()
    corrected = protocol.supersede(reason=wave6.SupersessionReason.SCOPE_CORRECTED,
                                   opportunity=opportunity, scope=replace(protocol.scope, excluded=()))
    assert protocol.to_dict() == before and corrected.protocol_identity != protocol.protocol_identity
    assert protocol_freeze.protocol_identity == protocol.protocol_identity

    from research_engine.lifecycle.search_record_store import SearchRecordStore
    from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
    from research_engine.lifecycle.research_protocol_store import ResearchProtocolStore
    search_store = SearchRecordStore(tmp_path / "search.json")
    search_store.register(selection["record"]); search_store.register_freeze(freeze)
    agenda_store = ResearchAgendaStore(tmp_path / "agenda.json")
    agenda_store.register_opportunity(opportunity); agenda_store.register_policy(POLICY_LEXICOGRAPHIC_V1)
    agenda_store.register_agenda(agenda); agenda_store.register_queue(queue); agenda_store.register_freeze(agenda_freeze)
    protocol_store = ResearchProtocolStore(tmp_path / "protocol.json")
    protocol_store.register_protocol(protocol, opportunity=opportunity)
    protocol_store.register_freeze(protocol_freeze, protocol=protocol)
    assert SearchRecordStore(tmp_path / "search.json").get(selection["record"].search_identity).to_dict() == selection["record"].to_dict()
    assert ResearchAgendaStore(tmp_path / "agenda.json").get_agenda(agenda.agenda_identity).ordered_identities == agenda.ordered_identities
    assert ResearchProtocolStore(tmp_path / "protocol.json").get_protocol(protocol.protocol_identity).to_dict() == protocol.to_dict()

    wave7 = _helper("test_stage3_wave7_treatment_memory.py")
    dims7 = wave7.dimensions.__wrapped__(); signature = wave7._signature(dims7, label="wider stops")
    protocol7 = wave7._protocol(dims7); memory = wave7._memory(dims7, protocol7, signature)
    relabelled = wave7._signature(dims7, label="increase stop distance", note="same semantics")
    policy = wave7.RevisitPolicy.create()
    duplicate = wave7.decide_revisit(signature=relabelled, proposed_context=wave7._envelope(dims7), memories=(memory,), policy=policy)
    revisit = wave7.decide_revisit(signature=signature,
        proposed_context=wave7._envelope(dims7, evidence_boundary=wave7.EVIDENCE_BOUNDARY_T1, evidence_reference="evidence:new"),
        memories=(wave7._memory(dims7, protocol7, signature, disposition=wave7.HistoricalDisposition.INSUFFICIENT_DATA,
                                reason_codes=(wave7.DispositionReason.POPULATION_TOO_SMALL,),
                                confirmation_state=wave7.ConfirmationState.REQUIRED_AND_ABSENT),),
        policy=policy, claimed_triggers=(wave7.RevisitTrigger.NEW_EVIDENCE,))
    assert relabelled.signature_identity == signature.signature_identity
    assert duplicate.eligibility is wave7.RevisitEligibility.DUPLICATE_RESEARCH
    assert revisit.eligibility is wave7.RevisitEligibility.REVISIT_PERMITTED and not hasattr(revisit, "execute")


def test_conflict_applicability_and_closed_coverage_loop():
    wave7 = _helper("test_stage3_wave7_treatment_memory.py")
    dimensions = wave7.dimensions.__wrapped__(); signature = wave7._signature(dimensions); protocol = wave7._protocol(dimensions)
    supported = wave7._memory(dimensions, protocol, signature, disposition=wave7.HistoricalDisposition.SUPPORTED,
                              reason_codes=(wave7.DispositionReason.CONFIRMATION_PASSED,), evidence_fingerprint=wave7.FINGERPRINT_A)
    opposed = wave7._memory(dimensions, wave7._protocol(dimensions), signature,
                            disposition=wave7.HistoricalDisposition.NOT_SUPPORTED,
                            reason_codes=(wave7.DispositionReason.REQUIRED_EFFECT_ABSENT,), evidence_fingerprint=wave7.FINGERPRINT_B)
    context = wave7._envelope(dimensions)
    conflict = wave7.assess_memory_conflict((supported, opposed), context)
    assert conflict.has_conflict and set(conflict.conflicting_memories) == {supported.memory_identity, opposed.memory_identity}
    assert wave7.assess_memory_applicability(supported, context).verdict is wave7.ApplicabilityVerdict.APPLIES
    assert wave7.assess_memory_applicability(supported, wave7._envelope(dimensions, regime_scope=("RANGING",))).verdict is wave7.ApplicabilityVerdict.DOES_NOT_APPLY
    assert wave7.assess_memory_applicability(supported, wave7._envelope(dimensions, symbol_scope=("XAUUSD",), asset_family="METALS")).verdict is wave7.ApplicabilityVerdict.DOES_NOT_APPLY

    _, _, _, space, ids, boundary, facts, t0 = _coverage_fixture()
    updated = dict(facts); updated[ids[2]] = CoverageEvidence(evidence_refs=("evidence:C",), research_attempt_refs=("attempt:C",), conclusion_refs=("result:C",), applicable_memory_refs=(supported.memory_identity,))
    t1 = CoverageSnapshot.construct(space, boundary, updated)
    assert t0.coverage_for(ids[2]).state is CoverageState.OBSERVED_NOT_RESEARCHED
    assert t1.coverage_for(ids[2]).state is CoverageState.OBSERVED_AND_RESEARCHED
    changes = compare_coverage(t0, t1).changes
    assert (ids[2], CoverageDeltaKind.NEWLY_RESEARCHED) in changes and (ids[2], CoverageDeltaKind.BLIND_SPOT_RESOLVED) in changes
    assert t0.coverage_snapshot_identity != t1.coverage_snapshot_identity
    assert ids[2] not in {signal.target_ref for signal in admit_coverage_curiosity(t1)}


def test_restart_reconstructs_coverage_memory_and_freeze(tmp_path: Path):
    wave7 = _helper("test_stage3_wave7_treatment_memory.py")
    dims = wave7.dimensions.__wrapped__(); signature = wave7._signature(dims); protocol = wave7._protocol(dims)
    memory = wave7._memory(dims, protocol, signature); policy = wave7.RevisitPolicy.create()
    decision = wave7.decide_revisit(signature=signature, proposed_context=wave7._envelope(dims), memories=(memory,), policy=policy)
    from research_engine.lifecycle.treatment_memory_store import TreatmentMemoryStore
    memory_store = TreatmentMemoryStore(tmp_path / "memory.json")
    memory_store.register_policy(policy); memory_store.register_memory(memory); memory_store.register_decision(decision)
    _, _, _, _, _, _, _, snapshot = _coverage_fixture()
    coverage_store = ResearchCoverageStore(tmp_path / "coverage.json"); coverage_store.register(snapshot)
    manifest = current_stage3_freeze_manifest(note="first", created_at="T0")
    (tmp_path / "freeze.json").write_text(json.dumps(manifest.to_dict(), sort_keys=True), encoding="utf-8")
    restarted_memory = TreatmentMemoryStore(tmp_path / "memory.json")
    restarted_coverage = ResearchCoverageStore(tmp_path / "coverage.json")
    restarted_manifest = Stage3FreezeManifest.from_dict(json.loads((tmp_path / "freeze.json").read_text(encoding="utf-8")))
    assert restarted_memory.get_memory(memory.memory_identity).to_dict() == memory.to_dict()
    assert restarted_memory.get_decision(decision.decision_identity).to_dict() == decision.to_dict()
    assert restarted_coverage.get(snapshot.coverage_snapshot_identity).to_dict() == snapshot.to_dict()
    assert restarted_manifest.freeze_identity == manifest.freeze_identity


def test_freeze_manifest_is_reproducible_strict_and_semantic():
    first = current_stage3_freeze_manifest(note="one", created_at="T0")
    second = current_stage3_freeze_manifest(note="two", created_at="T1")
    assert first.freeze_identity == second.freeze_identity
    assert Stage3FreezeManifest.from_dict(first.to_dict()).freeze_identity == first.freeze_identity
    changed = replace(first, architecture_version=2)
    assert changed.freeze_identity != first.freeze_identity
    payload = first.to_dict(); payload["canonical_question_count"] = 69
    with pytest.raises(Stage3FreezeIdentityConflict): Stage3FreezeManifest.from_dict(payload)
    prefixes = [row[0] for row in first.identity_namespaces]
    assert len(prefixes) == len(set(prefixes)) and first.canonical_question_count == 70


def test_canonical_authority_and_structured_knowledge_views():
    before = canonical_identity_snapshot(); _, _, _, _, ids, _, _, snapshot = _coverage_fixture()
    knows = snapshot.cells_by_state(CoverageState.OBSERVED_AND_RESEARCHED)
    does_not_know = tuple(snapshot.cells_by_state(state) for state in (CoverageState.RESEARCHED_INSUFFICIENT, CoverageState.CONFLICTING_RESEARCH))
    has_not_examined = snapshot.evidence_without_research()
    cannot_observe = snapshot.observability_gaps()
    investigating = snapshot.active_research_pending()
    assert knows and all(does_not_know) and has_not_examined and cannot_observe and investigating
    assert snapshot.coverage_for(ids[2]).state is CoverageState.OBSERVED_NOT_RESEARCHED
    assert canonical_identity_snapshot() == before and len(canonical_inventory()) == len(set(canonical_inventory())) == 70
    assert CANONICAL_DEFINITION_VERSION == 1


def test_stage3_sources_have_no_execution_or_production_authority():
    root = Path("research_engine/lifecycle")
    names = [module for _, module in current_stage3_freeze_manifest().authoritative_modules]
    forbidden_imports = {"broker", "execution", "experiment_runner", "research_cycle_runner", "candidate_activation_gate", "orchestrator"}
    forbidden_calls = {"send_order", "run_experiment", "apply_treatment", "activate_candidate", "promote_candidate"}
    for name in names:
        path = root / f"{name}.py"; tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports = {alias.name.split(".")[-1] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not imports & forbidden_imports and not calls & forbidden_calls
