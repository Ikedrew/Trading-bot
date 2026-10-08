"""Regression proof for autonomous Q71+ question generation and lifecycle."""
from __future__ import annotations

import json

import pytest

from research_engine.lifecycle.generated_research_isolation import canonical_inventory
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.lifecycle.research_coverage import (
    CoverageEvidence, CoverageSnapshot, ResearchInventoryBoundary,
)
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore
from research_engine.lifecycle.research_observation_space import (
    CompatibilityRule, EvidenceCapability, ObservationCellDeclaration,
    ObservationSpace, ObservationSpaceConstructionPolicy,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.q71_orchestration import (
    Q71OrchestrationError, run_q71_orchestration,
)


DIMENSION = "DIM-AAAAAAAAAAAAAAAA"


def _space(count: int = 1) -> ObservationSpace:
    policy = ObservationSpaceConstructionPolicy(
        allowed_subject_kinds=("CANONICAL_QUESTION",),
        allowed_dimensions=(DIMENSION,), allowed_populations=("shadow",),
        allowed_horizons=("SHORT",), allowed_evidence_classes=("EVENTS",),
        compatibility_rules=(CompatibilityRule(
            "q71_audit", subject_kinds=("CANONICAL_QUESTION",),
            dimension_identities=(DIMENSION,)),),
    )
    declarations = tuple(ObservationCellDeclaration(
        subject_kind="CANONICAL_QUESTION",
        subject_identity=canonical_inventory()[index],
        population_identity="shadow", horizon="SHORT", evidence_class="EVENTS",
        dimension_identities=(DIMENSION,),
        evidence_capability=EvidenceCapability.OBSERVABLE,
    ) for index in range(count))
    return ObservationSpace.construct(policy, declarations)


def _snapshot(space: ObservationSpace, evidence_by_cell, *, epoch: int) -> CoverageSnapshot:
    inventory = ResearchInventoryBoundary(
        space.observation_space_identity,
        question_inventory=tuple(BASELINE_QUESTION_IDS),
        evidence_inventory=tuple(sorted({
            ref for evidence in evidence_by_cell.values()
            for ref in evidence.evidence_refs
        })),
        evidence_fingerprint=f"AUDIT-FP-{epoch}",
    )
    return CoverageSnapshot.construct(
        space, inventory, evidence_by_cell,
        observed_at=f"2026-10-07T00:0{epoch}:00+00:00")


def _stores(tmp_path):
    return (
        ResearchCoverageStore(tmp_path / "coverage.json"),
        GeneratedResearchStore(tmp_path / "generated.json"),
        ResearchAgendaStore(tmp_path / "agenda.json"),
        tmp_path / "q71.json",
    )


def _run(stores, **kwargs):
    coverage, generated, agenda, state = stores
    return run_q71_orchestration(
        snapshot_id=kwargs.pop("snapshot_id", "FRONTIER-1"),
        coverage_store=coverage, generated_store=generated,
        agenda_store=agenda, state_path=state, **kwargs)


def test_a_b_c_autonomous_generation_no_gap_and_structural_dedup(tmp_path):
    space = _space(2)
    cells = space.cells
    stores = _stores(tmp_path / "with-gaps")
    stores[0].register(_snapshot(space, {
        cells[0].cell_identity: CoverageEvidence(evidence_refs=("EVD-A",)),
        cells[1].cell_identity: CoverageEvidence(evidence_refs=("EVD-B",)),
    }, epoch=1))

    first = _run(stores, evaluator_registry={"EVENTS": "audit.events.v1"})
    assert len(first["new_question_ids"]) == 2
    assert len(stores[1]) == 2
    assert {row["status"] for row in first["generated_questions"]} == {"QUEUED"}
    assert all(row["evidence_requirements"] and row["depends_on"] == []
               for row in first["generated_questions"])

    # Equivalent governed evidence is the same scientific question population,
    # regardless of a repeated autonomous cycle.
    repeated = _run(stores, evaluator_registry={"EVENTS": "audit.events.v1"})
    assert repeated["new_question_ids"] == []
    assert len(stores[1]) == 2
    assert {row["generated_question_id"] for row in repeated["generated_questions"]} == \
        set(first["new_question_ids"])

    no_gap = _stores(tmp_path / "no-gap")
    no_gap[0].register(_snapshot(space, {
        cell.cell_identity: CoverageEvidence(
            evidence_refs=(f"EVD-{index}",), conclusion_refs=(f"CON-{index}",))
        for index, cell in enumerate(cells)
    }, epoch=1))
    empty = _run(no_gap, evaluator_registry={"EVENTS": "audit.events.v1"})
    assert empty["new_question_ids"] == []
    assert empty["generated_questions"] == []


def test_d_e_missing_data_and_missing_evaluator_are_distinct(tmp_path):
    space = _space(2)
    cells = space.cells
    stores = _stores(tmp_path)
    stores[0].register(_snapshot(space, {
        cells[0].cell_identity: CoverageEvidence(question_refs=("Q-GAP",)),
        cells[1].cell_identity: CoverageEvidence(evidence_refs=("EVD-PRESENT",)),
    }, epoch=1))

    result = _run(stores, evaluator_registry={cells[0].cell_identity: "audit.events.v1"})
    by_cell = {row["source_opportunity"].split(":", 1)[1]: row
               for row in result["generated_questions"]}
    assert by_cell[cells[0].cell_identity]["status"] == "WAITING_FOR_DATA"
    assert by_cell[cells[0].cell_identity]["evidence_availability"] is False
    assert by_cell[cells[1].cell_identity]["status"] == "MISSING_EVALUATOR"
    assert by_cell[cells[1].cell_identity]["evidence_availability"] is True
    assert result["queue"] == []


def test_f_g_reentry_on_evidence_and_retirement_on_resolution(tmp_path):
    space = _space()
    cell = space.cells[0]
    stores = _stores(tmp_path)
    stores[0].register(_snapshot(space, {
        cell.cell_identity: CoverageEvidence(question_refs=("Q-GAP",)),
    }, epoch=1))
    waiting = _run(stores, evaluator_registry={"EVENTS": "audit.events.v1"})
    question_id = waiting["new_question_ids"][0]
    assert waiting["generated_questions"][0]["status"] == "WAITING_FOR_DATA"

    stores[0].register(_snapshot(space, {
        cell.cell_identity: CoverageEvidence(evidence_refs=("EVD-ARRIVED",)),
    }, epoch=2))
    ready = _run(stores, snapshot_id="FRONTIER-2",
                 evaluator_registry={"EVENTS": "audit.events.v1"})
    assert ready["new_question_ids"] == []
    assert ready["generated_questions"][0]["generated_question_id"] == question_id
    assert ready["generated_questions"][0]["status"] == "QUEUED"
    assert ready["generated_questions"][0]["reentry_count"] == 1

    stores[0].register(_snapshot(space, {
        cell.cell_identity: CoverageEvidence(
            evidence_refs=("EVD-ARRIVED",), research_attempt_refs=("TRY-1",),
            conclusion_refs=("CON-1",)),
    }, epoch=3))
    retired = _run(stores, snapshot_id="FRONTIER-3",
                   evaluator_registry={"EVENTS": "audit.events.v1"})
    assert retired["retired_question_ids"] == [question_id]
    assert retired["generated_questions"][0]["status"] == "RETIRED"
    assert retired["generated_questions"][0]["retirement_reason"] == \
        "RESEARCH_GAP_RESOLVED"
    assert retired["queue"] == []

    persisted = json.loads(stores[3].read_text(encoding="utf-8"))
    assert persisted["question_states"][question_id]["status"] == "RETIRED"
    assert len(persisted["question_states"][question_id]["evidence_history"]) == 3


def test_h_bounds_invalid_input_and_canonical_70_is_untouched(tmp_path):
    before = tuple(BASELINE_QUESTION_IDS)
    stores = _stores(tmp_path)
    with pytest.raises(Q71OrchestrationError, match="Q71_CAPACITY_OUT_OF_RANGE"):
        _run(stores, capacity=0)
    assert tuple(BASELINE_QUESTION_IDS) == before
    assert len(before) == 70
    assert not stores[1].path.exists()
