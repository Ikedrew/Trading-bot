"""Focused governance tests for frozen HD11/HD12 learning/adaptation contracts."""
from __future__ import annotations
import research_engine.registry.learning_adaptation_adjudication as A
from research_engine.registry.master_repair_ledger import HUMAN_SEMANTIC_DECISIONS as _H
from research_engine.registry.master_repair_ledger import REPAIR_WAVES as _W
from research_engine.registry.master_repair_ledger import STRUCTURALLY_NON_OPERATIONAL_IDS as _N
from research_engine.registry.master_repair_ledger import operational_baseline as _base
from research_engine.registry.research_question_registry import REGISTRY_BY_ID as _R
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_DESIGNS as _D
from research_engine.runner_discovery import discover_runners as _disc
def test_a_versions():
 assert A.TARGETS == ("L1", "L2", "L3", "L4", "L6", "L7")
 assert A.FULL["version"] == A.LEARNING_VERSION
 assert A.HD11["version"] == A.HD11_VERSION
 assert A.HD12["version"] == A.HD12_VERSION
 assert _H["HD11"].implementation_blocked_until_decision is False
 assert _H["HD12"].implementation_blocked_until_decision is False
 assert _W["RW10"].implemented is False and _W["RW11"].implemented is False
def test_b_distinct():
 assert A.L1_DISTINCT == "E2 pooled expectancy"
 assert A.L3_DISTINCT == "D1 attribution"
 assert A.L3_TESTS == ("weight", "regime", "mapping")
 assert A.FUTURE_RUNNERS["L1"]["report"] == "l1_pattern_degradation.json"
 assert A.FUTURE_L3["report"] == "l3_architecture_assumption_validity.json"
 assert A.FUTURE_L7["report"] == "l7_adaptation_evidence.json"
 assert A.L1_ESTIMAND.startswith("late-minus-early")
 assert A.L7_OPTION.startswith("A ")
 assert _D["L6"].lifecycle == "PROPOSED"
def test_c_forbidden():
 assert A.FORBIDDEN_SOURCES["L1"] == ("q5_pattern_degradation.json",)
 assert A.FORBIDDEN_L2 == ("q15_learning_velocity.json",)
 assert A.FORBIDDEN_L3 == ("q1_component_reward.json",)
 assert A.FORBIDDEN_L4 == ("q17_drawdown_precursors.json",)
 assert A.FORBIDDEN_SOURCES["L7"] == ("l7_shadow_ab_validation.json",)
 assert A.E2_NEVER_L1.startswith("E2") and A.D1_NEVER_L3.startswith("D1")
 assert A.COUNTS_NEVER.startswith("trade") and A.LEGACY7_BANNED.startswith("chronological")
 assert A.PROPOSED_NEVER.startswith("PROPOSED")
def test_d_crosscut():
 assert "CURRENT" in A.CROSS_CURRENT and "VALID_CURRENT" in A.CROSS_COMPLETE
 assert "never define N" in A.CROSS_FANOUT
 assert "never defaulted to zero" in A.CROSS_MISSING
 assert A.CROSS_NULL.startswith("Sufficient") and "production" in A.CROSS_PROD
 assert A.SELF_STATUS_FORBIDDEN is True
def test_e_still_58():
 assert _base() == (58, 12)
 assert set(A.TARGETS) <= set(_N)
 assert "L6" not in _disc() and _R["L6"].runner_module == ""
 assert _R["L1"].report_filename == "q5_pattern_degradation.json"
 assert _R["L3"].report_filename == "q1_component_reward.json"

