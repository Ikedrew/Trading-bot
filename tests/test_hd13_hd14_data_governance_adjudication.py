"""Focused frozen-governance tests for HD13/G1 and HD14/G2."""
from __future__ import annotations

import importlib.util

import pytest

from research_engine.registry import data_governance_adjudication as A
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    MASTER_REPAIR_LEDGER,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_DESIGNS,
)


def test_hd13_freezes_exact_all70_population_and_requirement_model():
    assert len({q.id for q in REGISTRY}) == 70
    assert "Exactly the 70" in A.G1_POPULATION
    assert "including G1 and G2" in A.G1_POPULATION
    assert A.G1_REQUIREMENT_CATEGORIES == (
        "SCIENCE_CONTRACT", "SOURCE_AUTHORITY", "FIELD_COVERAGE",
        "JOINABILITY", "SAMPLE_SUFFICIENCY", "CURRENT_PROVENANCE",
    )
    assert set(A.G1_REQUIREMENT_CATEGORIES).isdisjoint(
        {"CONFIDENCE", "TRUTH", "PRODUCTION_POLICY"},
    )


def test_hd13_preserves_waiting_blocked_unknown_and_valid_unsuitable_completion():
    waiting = A.G1_WAITING_BLOCKED_UNKNOWN
    assert "only sample rules are INSUFFICIENT" in waiting
    assert "Known absence" in waiting and "FAIL, never UNKNOWN" in waiting
    assert "no UNKNOWN" in A.G1_COMPLETION
    assert "UNSUITABLE" in A.G1_COMPLETION
    assert "UNKNOWN is non-completable" in A.G1_COMPLETION
    assert "Never read its own report" in A.G1_SELF


def test_hd14_freezes_exhaustive_union_composite_join_and_taxonomy():
    assert "set-union" in A.G2_ELIGIBILITY
    assert "decision-only or outcome-only" in A.G2_ELIGIBILITY
    assert "(entity_id, canonical_opportunity_id)" in A.G2_CANONICAL_IDENTITY
    assert "never join or eligibility keys" in A.G2_CANONICAL_IDENTITY
    assert A.G2_CLASSIFICATIONS == ("VALID", "MISSING", "AMBIGUOUS", "CONFLICTING")
    taxonomy = A.G2_TAXONOMY
    for term in ("MISSING:", "AMBIGUOUS:", "CONFLICTING:"):
        assert term in taxonomy
    assert "not two opportunities" in A.G2_VALID_JOIN
    assert "never zero-filled" in A.G2_VALID_JOIN
    assert "D equals the sum" in A.G2_DENOMINATOR_ACCOUNTING
    assert "not silently dropped" in A.G2_DENOMINATOR_ACCOUNTING


def test_hd14_freezes_readiness_and_point_estimate_thresholds_only():
    assert A.G2_MINIMUM_ELIGIBLE_OPPORTUNITIES == 100
    assert A.G2_COVERAGE_THRESHOLD == 0.50
    assert "D>=100" in A.G2_READINESS_MINIMUM
    assert "coverage>=0.50" in A.G2_SCIENTIFIC_THRESHOLD
    assert "LINEAGE_THRESHOLD_NOT_MET is valid and COMPLETE" in A.G2_COMPLETION
    assert "no additional confidence-interval threshold" in A.G2_SCIENTIFIC_THRESHOLD


def test_shared_snapshot_contract_and_separate_report_ownership():
    assert "once and atomically" in A.G1_SNAPSHOT_CONTRACT
    assert "same fixed snapshot for all 70" in A.G1_SNAPSHOT_CONTRACT
    assert "No source is added, replaced, queried again" in A.G1_SNAPSHOT_CONTRACT
    assert "once and atomically" in A.G2_SNAPSHOT_CONTRACT
    assert "No live/runtime reconstruction" in A.G2_SNAPSHOT_CONTRACT
    assert "component digests" in A.G2_SNAPSHOT_CONTRACT
    assert "registry/definition digest" in A.G2_SNAPSHOT_CONTRACT
    assert "cannot complete the other" in A.SHARED_SEPARATION
    assert WAVE_A_NO_RUNNER_DESIGNS["G1"].report_identity == "g1_dataset_suitability.json"
    assert WAVE_A_NO_RUNNER_DESIGNS["G2"].report_identity == "g2_lineage_coverage.json"


def test_ledger_records_hd13_hd14_operational_without_g3_change():
    assert not HUMAN_SEMANTIC_DECISIONS["HD13"].implementation_blocked_until_decision
    assert not HUMAN_SEMANTIC_DECISIONS["HD14"].implementation_blocked_until_decision
    assert "ADJUDICATED" in HUMAN_SEMANTIC_DECISIONS["HD13"].exact_decision
    assert "ADJUDICATED" in HUMAN_SEMANTIC_DECISIONS["HD14"].exact_decision
    assert "HD15" in HUMAN_SEMANTIC_DECISIONS
    assert operational_baseline() == (62, 8)
    assert len(STRUCTURALLY_OPERATIONAL_IDS) == 62
    assert len(STRUCTURALLY_NON_OPERATIONAL_IDS) == 8
    assert len(MASTER_REPAIR_LEDGER) == 70
    assert {"G1", "G2"} <= set(STRUCTURALLY_OPERATIONAL_IDS)
    assert MASTER_REPAIR_LEDGER["G1"].human_semantic_decision_required is False
    assert MASTER_REPAIR_LEDGER["G2"].human_semantic_decision_required is False
    assert MASTER_REPAIR_LEDGER["G3"].human_semantic_decision_required is False


def test_g1_g2_frozen_identities_are_implemented_exactly():
    for module in (
        "research_engine.experiments.dataset_suitability",
        "research_engine.experiments.lineage_coverage",
    ):
        assert importlib.util.find_spec(module) is not None
    assert WAVE_A_NO_RUNNER_DESIGNS["G1"].runner_specification.proposed_module == (
        "research_engine.experiments.dataset_suitability"
    )
    assert WAVE_A_NO_RUNNER_DESIGNS["G1"].runner_specification.proposed_function == "run_g1"
    assert WAVE_A_NO_RUNNER_DESIGNS["G2"].runner_specification.proposed_module == (
        "research_engine.experiments.lineage_coverage"
    )
    assert WAVE_A_NO_RUNNER_DESIGNS["G2"].runner_specification.proposed_function == "run_g2"
