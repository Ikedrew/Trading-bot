"""Focused integrity tests for the planning-only Master Repair Ledger."""
from __future__ import annotations

from dataclasses import fields

from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    ALLOWED_GATE_STATUSES,
    HUMAN_SEMANTIC_DECISIONS,
    MASTER_REPAIR_LEDGER,
    OPERATIONAL_IDS,
    REPAIR_WAVES,
    STRUCTURAL_PASS_STATUSES,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    STRUCTURALLY_OPERATIONAL_IDS,
    GateStatus,
    blocker_counts,
    evidence_gap_counts,
    operational_baseline,
    projected_operational_counts,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a5_definitions import WAVE_A5_OWNERSHIP
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_TARGETS


EXPECTED_GATE_FIELDS = {
    "canonical_identity", "scientific_definition", "unit_of_analysis",
    "evidence_authority", "join_contract", "epoch_contract",
    "multi_account_contract", "repeated_measure_contract", "leakage_contract",
    "metric_contract", "sufficiency_contract", "runner", "report_ownership",
    "validity_contract", "completion_contract", "readiness_control",
    "continuous_loop_eligibility", "governance_contract",
}


def test_ledger_has_exactly_the_70_unique_canonical_ids():
    registry_ids = {question.id for question in REGISTRY}
    assert len(REGISTRY) == len(registry_ids) == 70
    assert len(MASTER_REPAIR_LEDGER) == 70
    assert set(MASTER_REPAIR_LEDGER) == registry_ids
    assert all(qid == entry.canonical_question_id for qid, entry in MASTER_REPAIR_LEDGER.items())


def test_every_entry_has_all_gate_fields_and_allowed_vocabulary():
    assert {field.name for field in fields(GateStatus)} == EXPECTED_GATE_FIELDS
    for entry in MASTER_REPAIR_LEDGER.values():
        gate_dict = entry.to_dict()["gates"]
        assert set(gate_dict) == EXPECTED_GATE_FIELDS
        assert set(gate_dict.values()) <= ALLOWED_GATE_STATUSES


def test_structural_operational_status_is_derived_consistently():
    derived = {
        qid
        for qid, entry in MASTER_REPAIR_LEDGER.items()
        if all(value in STRUCTURAL_PASS_STATUSES for value in entry.gates.values())
    }
    assert derived == set(STRUCTURALLY_OPERATIONAL_IDS) == set(OPERATIONAL_IDS)
    assert operational_baseline() == (
        len(OPERATIONAL_IDS),
        len(MASTER_REPAIR_LEDGER) - len(OPERATIONAL_IDS),
    )
    assert len(STRUCTURALLY_NON_OPERATIONAL_IDS) == len(MASTER_REPAIR_LEDGER) - len(OPERATIONAL_IDS)
    for entry in MASTER_REPAIR_LEDGER.values():
        assert entry.to_dict()["structurally_operational"] == entry.structurally_operational


def test_every_non_operational_entry_has_concrete_repair_accounting():
    vague = {"needs work", "fix runner", "investigate"}
    for qid in STRUCTURALLY_NON_OPERATIONAL_IDS:
        entry = MASTER_REPAIR_LEDGER[qid]
        assert entry.primary_blocker.strip()
        assert entry.primary_blocker_category.strip()
        assert entry.secondary_blockers
        assert entry.root_cause_cluster != "NONE"
        assert entry.repair_actions and all(action.strip() for action in entry.repair_actions)
        assert not any(action.strip().lower() in vague for action in entry.repair_actions)
        assert entry.verification_gate.strip()
        assert entry.verification_gate.lower() != "tests pass"
        assert entry.proposed_repair_wave in REPAIR_WAVES


def test_operational_questions_have_no_unnecessary_repair_work():
    for qid in STRUCTURALLY_OPERATIONAL_IDS:
        entry = MASTER_REPAIR_LEDGER[qid]
        assert not entry.primary_blocker
        assert not entry.primary_blocker_category
        assert not entry.secondary_blockers
        assert entry.root_cause_cluster == "NONE"
        assert not entry.repair_actions
        assert not entry.proposed_repair_wave


def test_primary_blocker_counts_cover_every_non_operational_question_once():
    counts = blocker_counts()
    assert set(counts) == {
        "chronology", "counterfactual design", "evidence authority", "no runner",
        "report ownership", "risk modelling", "runner mismatch",
    }
    assert counts["no runner"] == len(WAVE_A_NO_RUNNER_TARGETS)
    assert counts["report ownership"] == 2  # R1 and R2 remain distinct-owner work.
    assert sum(counts.values()) == len(STRUCTURALLY_NON_OPERATIONAL_IDS)


def test_repair_wave_dependencies_exist_and_are_acyclic():
    assert tuple(REPAIR_WAVES) == tuple(f"RW{i}" for i in range(1, 13))
    for wave in REPAIR_WAVES.values():
        assert all(dependency in REPAIR_WAVES for dependency in wave.prerequisites)
        assert wave.repair_wave_id not in wave.prerequisites

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(wave_id: str) -> None:
        assert wave_id not in visiting, f"cycle at {wave_id}"
        if wave_id in visited:
            return
        visiting.add(wave_id)
        for dependency in REPAIR_WAVES[wave_id].prerequisites:
            visit(dependency)
        visiting.remove(wave_id)
        visited.add(wave_id)

    for wave_id in REPAIR_WAVES:
        visit(wave_id)
    assert visited == set(REPAIR_WAVES)


def test_direct_gains_are_disjoint_and_cover_all_non_operational_ids():
    declared_gain: set[str] = set()
    outstanding_gain: set[str] = set()
    implemented_direct_gain: set[str] = set()
    partial_progress_gain: set[str] = set()
    for wave in REPAIR_WAVES.values():
        assert set(wave.direct_gain) <= set(wave.target_question_ids)
        assert set(wave.target_question_ids) - set(wave.direct_gain) <= set(STRUCTURALLY_OPERATIONAL_IDS)
        assert not declared_gain.intersection(wave.direct_gain)
        declared_gain.update(wave.direct_gain)
        if wave.implemented:
            # A satisfied wave's direct gain is already part of the derived baseline.
            assert set(wave.direct_gain) <= set(STRUCTURALLY_OPERATIONAL_IDS)
            implemented_direct_gain.update(wave.direct_gain)
        else:
            # A later wave may have banked surgical progress without satisfying
            # its complete exit gate; only its remaining gain is outstanding.
            partial_progress_gain.update(set(wave.direct_gain) & set(STRUCTURALLY_OPERATIONAL_IDS))
            outstanding_gain.update(set(wave.direct_gain) & set(STRUCTURALLY_NON_OPERATIONAL_IDS))
    assert implemented_direct_gain == {
        "D1", "E2", "M1", "M3", "M7", "M8", "M11", "D2", "D3", "D4", "D5", "X5",
        "D6", "PORT-1", "OPP-1", "P1",
    }
    assert partial_progress_gain == {"E3", "S1", "S5"}
    assert outstanding_gain == set(STRUCTURALLY_NON_OPERATIONAL_IDS)
    assert REPAIR_WAVES["RW2"].implemented is True
    assert set(REPAIR_WAVES["RW2"].direct_gain) == {"M1", "M3", "M7", "M8", "M11"}
    assert set(REPAIR_WAVES["RW2"].direct_gain) <= set(STRUCTURALLY_OPERATIONAL_IDS)


def test_cumulative_repair_plan_reconciles_exactly_to_70():
    projection = projected_operational_counts()
    covered = set(STRUCTURALLY_OPERATIONAL_IDS)
    expected = []
    for wave_id, wave in REPAIR_WAVES.items():
        if not wave.implemented:
            covered.update(wave.direct_gain)
        expected.append((wave_id, len(covered)))
    assert projection == tuple(expected)
    assert [count for _, count in projection] == sorted(count for _, count in projection)
    assert projection[-1][1] == len(MASTER_REPAIR_LEDGER) == 70


def test_unlocked_ids_are_not_claimed_as_early_direct_gain():
    for wave in REPAIR_WAVES.values():
        assert not set(wave.direct_gain).intersection(wave.unlocked_not_yet_operational)
    assert set(REPAIR_WAVES["RW1"].unlocked_not_yet_operational) == {
        "E3", "S1", "R1", "R2", "L1", "L3",
    }
    assert REPAIR_WAVES["RW5"].unlocked_not_yet_operational == ("S7",)
    assert REPAIR_WAVES["RW11"].unlocked_not_yet_operational == ("G3",)


def test_all_no_runner_ids_still_require_real_implementation():
    # S5 was implemented by Repair 2B.2 and is no longer an unimplemented target.
    assert WAVE_A_NO_RUNNER_TARGETS == {"S6", "S7", "X6", "L6", "G1", "G2", "G3"}
    for qid in WAVE_A_NO_RUNNER_TARGETS:
        entry = MASTER_REPAIR_LEDGER[qid]
        question = REGISTRY_BY_ID[qid]
        assert not entry.structurally_operational
        assert entry.primary_blocker_category == "no runner"
        assert entry.gates.runner == "FAIL"
        assert question.runner_module == question.runner_function == ""
        assert question.report_filename == ""


def test_a5_ownership_conclusions_are_preserved():
    e3_s1 = WAVE_A5_OWNERSHIP[("E3", "S1")]
    assert e3_s1.scientifically_equivalent
    assert e3_s1.canonical_owners == (
        ("research_engine.experiments.strategy_expectancy.run_e3", "E3"),
        ("e3_strategy_family_expectancy.json", "E3"),
    )
    assert set(e3_s1.relationship_types) == {"TRUE_ALIAS", "SUPERSEDED_IDENTITY", "ADJUDICATED"}
    assert not e3_s1.unresolved_reason
    assert "S1 owns no runner" in e3_s1.runner_ownership_status
    assert "S1 owns no report" in e3_s1.report_ownership_status
    for qid in ("E3", "S1"):
        entry = MASTER_REPAIR_LEDGER[qid]
        assert entry.structurally_operational
        assert entry.gates.canonical_identity == "PASS"
        assert entry.proposed_repair_wave == ""

    assert WAVE_A5_OWNERSHIP[("D6", "PORT-1")].highest_safe_sharing_level == "calculation"
    assert WAVE_A5_OWNERSHIP[("D1", "L3")].scientifically_equivalent is False
    assert WAVE_A5_OWNERSHIP[("E2", "L1")].scientifically_equivalent is False
    assert WAVE_A5_OWNERSHIP[("R1", "R2")].highest_safe_sharing_level == "helper"


def test_rw1_ownership_repair_preserves_historical_gain_and_later_progress():
    """RW1's direct gain stays exact while later waves may resolve unlocked IDs."""
    for qid in ("D1", "E2"):
        entry = MASTER_REPAIR_LEDGER[qid]
        assert entry.structurally_operational
        assert entry.current_definition_health in {"VALID", "VALID_WITH_WARNINGS"}
        assert entry.gates.scientific_definition == "PASS"
        assert entry.gates.runner == "PASS"
        assert entry.gates.report_ownership == "PASS"
        assert entry.to_dict()["gates"]["report_ownership"] == "PASS"
        assert entry.primary_blocker_category == ""
        assert entry.proposed_repair_wave == ""
        assert entry.repair_actions == ()
        assert entry.to_dict()["structurally_operational"] is True

    expected_blockers = {"L1": "chronology", "L3": "runner mismatch"}
    for qid, category in expected_blockers.items():
        entry = MASTER_REPAIR_LEDGER[qid]
        assert not entry.structurally_operational
        assert entry.gates.report_ownership == "FAIL"
        assert entry.primary_blocker_category == category
        assert entry.proposed_repair_wave == "RW10"

    for qid in ("E3", "S1"):
        entry = MASTER_REPAIR_LEDGER[qid]
        assert entry.structurally_operational
        assert entry.gates.report_ownership == "PASS"

    for qid in ("R1", "R2"):
        entry = MASTER_REPAIR_LEDGER[qid]
        assert not entry.structurally_operational
        assert entry.gates.report_ownership == "FAIL"


def test_rw1_and_rw2_are_recorded_as_implemented_waves_with_evidence():
    implemented = [wave_id for wave_id, wave in REPAIR_WAVES.items() if wave.implemented]
    assert implemented == ["RW1", "RW2", "RW3", "RW4"]

    wave = REPAIR_WAVES["RW1"]
    assert wave.direct_gain == ("D1", "E2")
    assert wave.target_question_ids == ("D1", "E2")
    assert wave.implementation_evidence
    assert all(item.strip() for item in wave.implementation_evidence)
    assert set(wave.unlocked_not_yet_operational) == {"E3", "S1", "R1", "R2", "L1", "L3"}
    for qid in {"R1", "R2", "L1", "L3"}:
        assert not MASTER_REPAIR_LEDGER[qid].structurally_operational
    for qid in {"E3", "S1"}:
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational

    rw2 = REPAIR_WAVES["RW2"]
    assert rw2.direct_gain == ("M1", "M3", "M7", "M8", "M11")
    assert rw2.implementation_evidence
    assert all(item.strip() for item in rw2.implementation_evidence)

    rw3 = REPAIR_WAVES["RW3"]
    assert rw3.implemented is True
    assert rw3.direct_gain == ("D2", "D3", "D4", "D5", "X5")
    assert set(rw3.direct_gain) <= set(STRUCTURALLY_OPERATIONAL_IDS)
    assert rw3.implementation_evidence
    assert all(item.strip() for item in rw3.implementation_evidence)

    rw4 = REPAIR_WAVES["RW4"]
    assert rw4.implemented is True
    assert rw4.direct_gain == ("D6", "PORT-1", "OPP-1", "P1")
    assert set(rw4.direct_gain) <= set(STRUCTURALLY_OPERATIONAL_IDS)
    assert rw4.implementation_evidence
    assert all(item.strip() for item in rw4.implementation_evidence)

    # Every later wave is still outstanding and records no implementation claims.
    for wave_id, other in REPAIR_WAVES.items():
        if wave_id in {"RW1", "RW2", "RW3", "RW4"}:
            continue
        assert not other.implemented
        assert other.implementation_evidence == ()


def test_evidence_gap_accounting_preserves_v1_freeze_and_g3_requirement():
    counts = evidence_gap_counts()
    assert counts["ALREADY_AVAILABLE"] == len(STRUCTURALLY_OPERATIONAL_IDS)
    assert counts["NEW_RESEARCH_EVIDENCE"] == 1
    assert counts["EXISTING_V1_CONTRACT_VIOLATION"] == 0
    assert counts["DERIVABLE"] == len(MASTER_REPAIR_LEDGER) - sum(
        value for key, value in counts.items() if key != "DERIVABLE"
    )
    assert MASTER_REPAIR_LEDGER["G3"].evidence_gap_classification == "NEW_RESEARCH_EVIDENCE"
    assert MASTER_REPAIR_LEDGER["G3"].gates.evidence_authority == "FUTURE_EVIDENCE_REQUIRED"
    assert REPAIR_WAVES["RW12"].new_research_evidence_required
    assert not any(
        entry.evidence_gap_classification == "EXISTING_V1_CONTRACT_VIOLATION"
        for entry in MASTER_REPAIR_LEDGER.values()
    )


def test_human_decisions_are_explicit_and_reference_real_targets():
    registry_ids = set(MASTER_REPAIR_LEDGER)
    assert set(HUMAN_SEMANTIC_DECISIONS) == {f"HD{i:02d}" for i in range(1, 16)}
    adjudicated = {"HD01", "HD02", "HD03", "HD06"}
    for decision_id, decision in HUMAN_SEMANTIC_DECISIONS.items():
        assert set(decision.affected_question_ids) <= registry_ids
        assert decision.exact_decision
        assert len(decision.available_options) >= 2
        assert len(decision.available_options) == len(decision.consequences)
        assert decision.recommended_default
        assert decision.implementation_blocked_until_decision is (decision_id not in adjudicated)
    for decision_id in {"HD01", "HD06"}:
        assert HUMAN_SEMANTIC_DECISIONS[decision_id].recommended_default.startswith("ADJUDICATED:")
    for wave in REPAIR_WAVES.values():
        assert all(decision_id in HUMAN_SEMANTIC_DECISIONS for decision_id in wave.human_decision_ids)


def test_each_wave_has_objective_exit_and_change_boundaries():
    for wave in REPAIR_WAVES.values():
        assert wave.root_cause
        assert wave.code_areas_expected_to_change
        assert wave.exit_gate
        assert "tests pass" not in wave.exit_gate.lower()


def test_master_ledger_import_does_not_change_definition_or_runtime_mappings():
    definitions = build_definitions_from_registry(REGISTRY)
    for question in REGISTRY:
        definition = definitions[question.id]
        assert definition.runner_module == question.runner_module
        assert definition.runner_function == question.runner_function
        assert definition.report_filename == question.report_filename
        assert definition.legacy_ids == question.legacy_ids
