"""Focused governance tests proving operational G3 still follows frozen HD15."""
from __future__ import annotations

import importlib.util

from research_engine.control_plane.models import ReportValidity
from research_engine.registry import research_validity_adjudication as A
from research_engine.registry.learning_adaptation_adjudication import (
    FUTURE_L6,
    HD12_VERSION,
    PROPOSED_NEVER,
)
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    MASTER_REPAIR_LEDGER,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
)


def test_hd15_population_is_exactly_69_nonself_canonical_questions():
    population = {question.id for question in REGISTRY if question.id != "G3"}
    assert len(REGISTRY) == 70
    assert len(population) == 69
    assert "G3" not in population
    assert "Exactly the 69" in A.ASSESSMENT_POPULATION
    assert "G3 is never" in A.ASSESSMENT_POPULATION
    for state in ("WAITING_DATA", "BLOCKED", "non-operational", "negative"):
        assert state in A.ASSESSMENT_POPULATION


def test_hd15_taxonomy_keeps_required_states_distinct_and_negative_valid():
    assert A.QUESTION_TRUST_STATES == (
        "TRUSTED_CURRENT_CONCLUSION",
        "WAITING_DATA",
        "BLOCKED",
        "NON_OPERATIONAL",
        "REPORT_NOT_VALID_CURRENT",
        "UNKNOWN",
    )
    assert "negative edge" in A.NEGATIVE_FINDING_SEMANTICS
    assert "TRUSTED_CURRENT_CONCLUSION" in A.NEGATIVE_FINDING_SEMANTICS
    assert "Finding direction or favourability is not a trust gate" in A.NEGATIVE_FINDING_SEMANTICS
    assert "stale" in A.CLASSIFICATION_PRECEDENCE
    assert ReportValidity.STALE.value != ReportValidity.VALID_CURRENT.value


def test_hd15_freezes_exact_canonical_input_authorities():
    expected = {
        "canonical_question_identity", "structural_operational_state",
        "report_ownership", "report_validity", "current_validity",
        "readiness_and_completion", "evidence_and_sample_sufficiency",
        "G1", "G2", "L6",
    }
    assert set(A.INPUT_AUTHORITIES) == expected
    assert A.INPUT_AUTHORITIES["canonical_question_identity"].endswith(".REGISTRY")
    assert "MASTER_REPAIR_LEDGER" in A.INPUT_AUTHORITIES["structural_operational_state"]
    assert "resolve_report_validity" in A.INPUT_AUTHORITIES["report_validity"]
    assert "VALID_CURRENT only" in A.INPUT_AUTHORITIES["current_validity"]
    forbidden = " ".join(A.INPUT_AUTHORITIES.values())
    assert "never raw row, trade, file, dashboard, R-multiple, or pattern counts" in forbidden


def test_hd15_g1_g2_and_l6_roles_are_exact_and_nonrecursive():
    assert "SUITABLE" in A.G1_ROLE and "never reruns or reinterprets" in A.G1_ROLE
    assert "LINEAGE_THRESHOLD_MET" in A.G2_ROLE
    assert "LINEAGE_THRESHOLD_NOT_MET" in A.G2_ROLE
    assert A.L6_DEPENDENCY_DECISION == "B"
    assert "MISSING_REQUIRED_DEPENDENCY" in A.L6_ROLE
    assert "RESEARCH_TRUST_NOT_YET_DEMONSTRATED" in A.L6_ROLE
    assert "never invents, reconstructs, defaults, or recursively invokes L6" in A.L6_ROLE
    assert FUTURE_L6["report"] == "l6_learning_cycle_validation.json"
    assert HD12_VERSION == "hd12_learning_cycle_confidence_v1"
    assert PROPOSED_NEVER == "PROPOSED design never completes L6"


def test_hd15_global_rule_is_categorical_fail_closed_without_weighting():
    assert A.RESULT_VOCABULARY == (
        "RESEARCH_TRUST_DEMONSTRATED",
        "RESEARCH_TRUST_NOT_YET_DEMONSTRATED",
        "EVALUATION_BLOCKED",
        "EVALUATION_UNKNOWN",
    )
    assert "all 69" in A.GLOBAL_TRUST_RULE
    assert "no scalar, weighting, points, or score" in A.GLOBAL_TRUST_RULE
    assert "WAITING_DATA" in A.GLOBAL_TRUST_RULE
    assert "BLOCKED" in A.GLOBAL_TRUST_RULE
    assert "unavailable L6" in A.GLOBAL_TRUST_RULE
    assert "prevents RESEARCH_TRUST_DEMONSTRATED" in A.WAITING_DATA_SEMANTICS
    assert "does not prevent G3 completion" in A.WAITING_DATA_SEMANTICS
    assert "distinct from EVALUATION_BLOCKED" in A.BLOCKED_SEMANTICS


def test_hd15_unknown_and_g3_level_blockage_cannot_complete():
    assert "never coerced" in A.UNKNOWN_SEMANTICS
    assert "prevents G3 COMPLETE" in A.UNKNOWN_SEMANTICS
    assert "EVALUATION_BLOCKED and EVALUATION_UNKNOWN never COMPLETE" in A.COMPLETION_SEMANTICS
    assert "RESEARCH_TRUST_NOT_YET_DEMONSTRATED may COMPLETE" in A.COMPLETION_SEMANTICS
    assert "Report existence or self-declared status never completes G3" in A.COMPLETION_SEMANTICS


def test_hd15_snapshot_manifest_digest_and_nonrecursion_are_frozen():
    snapshot = A.SNAPSHOT_CONTRACT
    for required in (
        "once and atomically", "exactly 69 ordered", "G1/G2/L6",
        "report owner/filename/status/validity", "SHA-256", "canonical UTF-8 JSON",
        "recursively sorted object keys", "mixed epochs", "self-reference",
    ):
        assert required in snapshot
    nonrecursive = A.NONRECURSION
    for forbidden in (
        "never reads a prior G3 report", "never includes G3", "dashboard-derived G3",
        "never invokes G1/G2/L6", "never mutates question state", "production",
    ):
        assert forbidden in nonrecursive


def test_hd15_frozen_identity_is_uniquely_implemented():
    assert A.FUTURE_IMPLEMENTATION == {
        "module": "research_engine.experiments.research_validity",
        "function": "run_g3",
        "report": "g3_research_validity.json",
        "owner": "G3",
    }
    proposed = WAVE_A_NO_RUNNER_DESIGNS["G3"]
    assert proposed.runner_specification.proposed_module == A.FUTURE_IMPLEMENTATION["module"]
    assert proposed.runner_specification.proposed_function == A.FUTURE_IMPLEMENTATION["function"]
    assert proposed.report_identity == A.FUTURE_IMPLEMENTATION["report"]
    assert sum(d.report_identity == "g3_research_validity.json" for d in WAVE_A_NO_RUNNER_DESIGNS.values()) == 1
    assert importlib.util.find_spec(A.FUTURE_IMPLEMENTATION["module"]) is not None
    assert REGISTRY_BY_ID["G3"].runner_module == A.FUTURE_IMPLEMENTATION["module"]
    assert REGISTRY_BY_ID["G3"].report_filename == A.FUTURE_IMPLEMENTATION["report"]
    assert WAVE_A_NO_RUNNER_TARGETS == {"L6"}


def test_hd15_ledger_is_adjudicated_and_g3_is_structurally_operational():
    decision = HUMAN_SEMANTIC_DECISIONS["HD15"]
    assert decision.exact_decision.startswith("ADJUDICATED:")
    assert not decision.implementation_blocked_until_decision
    assert not MASTER_REPAIR_LEDGER["G3"].human_semantic_decision_required
    assert MASTER_REPAIR_LEDGER["G3"].gates.scientific_definition == "PASS"
    assert MASTER_REPAIR_LEDGER["G3"].gates.completion_contract == "PASS"
    assert MASTER_REPAIR_LEDGER["G3"].gates.runner == "PASS"
    assert MASTER_REPAIR_LEDGER["G3"].gates.report_ownership == "PASS"
    assert "G3" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert operational_baseline() == (63, 7)
