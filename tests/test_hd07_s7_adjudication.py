"""Governance-only acceptance tests for the adjudicated HD07/S7 contract."""
from __future__ import annotations

from math import comb
from pathlib import Path

from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    MASTER_REPAIR_LEDGER,
    STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import (
    S7_HD07_ADJUDICATED_CONTRACT,
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
)
from research_engine.runner_discovery import discover_runners


CONTRACT = S7_HD07_ADJUDICATED_CONTRACT


def test_hd07_is_adjudicated_and_no_longer_implementation_blocking():
    decision = HUMAN_SEMANTIC_DECISIONS["HD07"]
    assert decision.decision_id == "HD07"
    assert decision.affected_question_ids == ("S7",)
    assert decision.exact_decision.startswith("ADJUDICATED:")
    assert decision.recommended_default == "ADJUDICATED: use S7_HD07_ADJUDICATED_CONTRACT."
    assert decision.implementation_blocked_until_decision is False
    assert CONTRACT["status"] == "ADJUDICATED"


def test_full_factorial_estimator_weighting_cluster_and_covariance_are_frozen():
    assert CONTRACT["model"] == "R ~ StrategyFamily + Horizon + StrategyFamily:Horizon"
    assert CONTRACT["coding"] == "deterministic reference coding consistent with S5/S6"
    assert CONTRACT["cluster_field"] == "identity.canonical_opportunity_id"
    assert CONTRACT["opportunity_weight"] == "w_oh = 1 / k_o"
    assert CONTRACT["opportunity_total_weight"] == 1
    assert CONTRACT["covariance"] == "canonical-opportunity clustered sandwich"
    assert CONTRACT["bread"] == "A = X' W X"
    assert CONTRACT["cluster_score"] == "s_o = sum_rows_in_o(w_oh * x_oh * residual_oh)"
    assert CONTRACT["covariance_formula"] == "A^-1 (sum_o s_o s_o') A^-1"

    families, horizons = CONTRACT["full_grid_shape"]
    assert (families, horizons) == (6, 3)
    assert CONTRACT["full_grid_interaction_df"] == (families - 1) * (horizons - 1) == 10
    assert CONTRACT["subgrid_interaction_df"] == "(F - 1) * (H - 1)"


def test_global_null_wald_omnibus_and_alpha_are_frozen():
    assert CONTRACT["global_null"] == (
        "All StrategyFamily x horizon interaction terms for the evaluated "
        "sufficient grid are jointly zero."
    )
    assert CONTRACT["alternative"] == "At least one interaction component differs from zero."
    assert CONTRACT["omnibus_test"] == (
        "deterministic cluster-robust Wald-type joint interaction-block test"
    )
    assert CONTRACT["omnibus_alpha"] == 0.05
    assert CONTRACT["followup_gate"] == "omnibus p <= 0.05"


def test_followup_family_and_holm_multiplicity_are_exact():
    assert CONTRACT["followup_family"] == "within-strategy pairwise horizon contrasts only"
    assert CONTRACT["full_grid_followup_count"] == 6 * comb(3, 2) == 18
    assert CONTRACT["subgrid_followup_count"] == "F * C(H, 2)"
    assert CONTRACT["multiplicity_method"] == "Holm step-down family-wise error-rate control"
    assert CONTRACT["multiplicity_family"] == (
        "one global family across all predeclared follow-up contrasts"
    )
    assert CONTRACT["followup_alpha"] == 0.05
    assert CONTRACT["omnibus_in_holm_family"] is False
    assert set(CONTRACT["forbidden_followups"]) == {
        "pairwise strategies within horizons",
        "all cells against each other",
        "best-cell rankings",
        "arbitrary post-hoc contrasts",
    }
    assert CONTRACT["contrast_variance"] == "c' V_cluster c"
    assert CONTRACT["contrast_interval_95"] == "estimate +/- 1.96 * cluster-robust SE"


def test_sufficiency_grid_selection_and_claim_boundary_are_exact():
    assert CONTRACT["minimum_distinct_opportunities_overall"] == 150
    assert CONTRACT["minimum_distinct_opportunities_per_included_cell"] == 30
    assert CONTRACT["minimum_grid"] == (2, 2)
    assert CONTRACT["grid_objective"] == (
        "maximize included valid cells",
        "maximize included StrategyFamily values",
        "maximize included horizons",
        "tie-break by canonical StrategyFamily order then canonical horizon order",
    )
    assert CONTRACT["grid_selection_inputs"] == "pre-outcome identity and sufficiency counts only"
    assert "pnl_r_multiple" in CONTRACT["grid_selection_forbidden_inputs"]
    assert "p-values" in CONTRACT["grid_selection_forbidden_inputs"]
    assert "any outcome-derived quantity" in CONTRACT["grid_selection_forbidden_inputs"]
    assert CONTRACT["claim_boundary"] == (
        "claims apply only to the selected sufficient evaluated grid"
    )
    assert "may not be silently dropped" in CONTRACT["estimability_rule"]


def test_completion_provenance_and_frozen_runner_report_contracts_are_exact():
    assert CONTRACT["complete_results"] == (
        "RELIABLE_INTERACTION",
        "NO_RELIABLE_INTERACTION",
    )
    assert CONTRACT["insufficient_result"] == "INSUFFICIENT_EVIDENCE"
    assert set(CONTRACT["provenance_requirements"]) == {
        "CURRENT selection before analysis",
        "explicit stale/incompatible exclusions",
        "exact analytical subset attestation",
        "deterministic order-invariant digest",
        "analytical change sensitivity",
        "invalid subset/provenance fails closed",
    }
    design = WAVE_A_NO_RUNNER_DESIGNS["S7"]
    assert design.runner_specification.proposed_module == CONTRACT["proposed_module"]
    assert design.runner_specification.proposed_function == CONTRACT["proposed_function"]
    assert design.report_identity == CONTRACT["report_identity"]
    assert design.report_identity == "s7_strategy_horizon_interaction.json"
    assert design.human_semantic_decisions == (
        "ADJUDICATED HD07: use S7_HD07_ADJUDICATED_CONTRACT; implementation is no longer decision-blocked.",
    )


def test_s7_frozen_design_is_retained_after_implementation_at_45_of_70():
    question = REGISTRY_BY_ID["S7"]
    assert question.runner_module == CONTRACT["proposed_module"]
    assert question.runner_function == CONTRACT["proposed_function"]
    assert question.report_filename == CONTRACT["report_identity"]
    assert "S7" in discover_runners()
    assert "S7" not in WAVE_A_NO_RUNNER_TARGETS
    assert "S7" in STRUCTURALLY_OPERATIONAL_IDS
    assert canonical_report_owner(CONTRACT["report_identity"]) == "S7"
    assert Path("research_engine/experiments/strategy_horizon_interaction.py").exists()

    entry = MASTER_REPAIR_LEDGER["S7"]
    assert entry.structurally_operational is True
    assert entry.human_semantic_decision_required is False
    assert entry.gates.scientific_definition == "PASS"
    assert entry.gates.runner == "PASS"
    assert entry.gates.report_ownership == "PASS"
    assert operational_baseline() == (45, 25)


def test_registry_baseline_versions_and_s5_s6_state_are_unchanged():
    ids = tuple(question.id for question in REGISTRY)
    assert len(REGISTRY) == len(set(ids)) == 70
    assert ids == BASELINE_QUESTION_IDS
    definitions = build_definitions_from_registry(REGISTRY)
    assert all(definition.definition_version == 1 for definition in definitions.values())
    assert REGISTRY_BY_ID["S5"].runner_function == "run_s5"
    assert REGISTRY_BY_ID["S5"].report_filename == "s5_strategy_identity_expectancy.json"
    assert REGISTRY_BY_ID["S6"].runner_function == "run_s6"
    assert REGISTRY_BY_ID["S6"].report_filename == "s6_horizon_expectancy.json"
    assert {"S5", "S6"} <= set(STRUCTURALLY_OPERATIONAL_IDS)
