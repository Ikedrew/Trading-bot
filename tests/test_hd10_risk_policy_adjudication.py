"""Focused governance tests for the frozen HD10 risk-policy contract."""
from __future__ import annotations

import hashlib
import json

from research_engine.registry.exit_policy_adjudication import (
    CLUSTERED_INFERENCE as HD09_CLUSTERED_INFERENCE,
    COMMON_ANALYTICAL_CONTRACT as HD09_COMMON_ANALYTICAL,
)
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    REPAIR_WAVES,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY_BY_ID
from research_engine.registry.risk_policy_adjudication import (
    ACCOUNT_GRAIN_CONTRACT,
    BASELINE_RISK_POLICY_ID,
    BASELINE_RISK_POLICY_V1,
    CLUSTERED_INFERENCE,
    GUARD_TAXONOMY_V1,
    HALT_THRESHOLD_GRID_V1,
    HD10_ADJUDICATED_CONTRACT,
    HD10_ADJUDICATION_VERSION,
    LEGACY_REPORT_COMPATIBILITY,
    PRODUCTION_APPLICATION_BOUNDARY,
    R1_CONTRACT,
    R2_CONTRACT,
    R3_CONTRACT,
    R4_CONTRACT,
    R5_CONTRACT,
    REPORT_OWNERSHIP,
    REPORT_VALIDITY_CONTRACT,
    RUIN_MODEL_V1,
    RUIN_MODEL_VERSION,
    SAMPLE_AND_READINESS_CONTRACT,
    SIZING_MODELS_V1,
    VALIDATION_CHRONOLOGY,
    baseline_risk_policy_identity_hash,
)

TARGETS = ("R1", "R2", "R3", "R4", "R5")
LEGACY_ARTIFACT = "q10_guard_efficacy.json"


def test_hd10_is_closed_by_one_named_complete_authority_with_no_operational_gain():
    decision = HUMAN_SEMANTIC_DECISIONS["HD10"]
    assert decision.affected_question_ids == TARGETS
    assert decision.implementation_blocked_until_decision is False
    assert decision.recommended_default == (
        "ADJUDICATED: use research_engine.registry.risk_policy_adjudication."
        "HD10_ADJUDICATED_CONTRACT."
    )
    assert decision.recommended_default.startswith("ADJUDICATED:")
    assert HD10_ADJUDICATED_CONTRACT["implementation_blocked_until_decision"] is False
    assert HD10_ADJUDICATED_CONTRACT["version"] == HD10_ADJUDICATION_VERSION
    assert HD10_ADJUDICATED_CONTRACT["option"] == "A"
    assert HD10_ADJUDICATED_CONTRACT["questions"] == TARGETS

    # Adjudication is governance only: no question becomes operational.
    assert operational_baseline() == (53, 17)
    for question_id in TARGETS:
        assert question_id in STRUCTURALLY_NON_OPERATIONAL_IDS, question_id

    wave = REPAIR_WAVES["RW9"]
    assert wave.human_decision_ids == ("HD10",)
    assert wave.implemented is False
    assert wave.implementation_evidence == ()
    assert wave.unlocked_not_yet_operational == ()
    assert "HD10_ADJUDICATED_CONTRACT" in wave.exit_gate
    assert HD10_ADJUDICATION_VERSION in wave.exit_gate


def test_baseline_identity_hash_is_deterministic_and_fails_closed():
    assert BASELINE_RISK_POLICY_ID == "RISK_POLICY_BASELINE_V1"
    assert BASELINE_RISK_POLICY_V1["policy_id"] == BASELINE_RISK_POLICY_ID
    assert BASELINE_RISK_POLICY_V1["policy_version"] == 1
    assert BASELINE_RISK_POLICY_V1["guard_taxonomy"] == tuple(
        guard["guard_id"] for guard in GUARD_TAXONOMY_V1
    )

    identity = BASELINE_RISK_POLICY_V1["identity_hash"]
    assert identity == baseline_risk_policy_identity_hash()
    assert baseline_risk_policy_identity_hash() == baseline_risk_policy_identity_hash()
    assert len(identity) == 16
    int(identity, 16)

    material = dict(BASELINE_RISK_POLICY_V1)
    material.pop("identity_hash")
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert identity == hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    assert baseline_risk_policy_identity_hash({**material, "policy_version": 2}) != identity

    assert "BASELINE_RISK_POLICY_AUTHORITY_MISSING" in BASELINE_RISK_POLICY_V1["missing_authority_rule"]
    assert "fail closed" in BASELINE_RISK_POLICY_V1["missing_authority_rule"]
    assert "no default policy is assumed" in BASELINE_RISK_POLICY_V1["missing_authority_rule"]
    assert "BASELINE_RISK_POLICY_IDENTITY_DRIFT" in BASELINE_RISK_POLICY_V1["drift_rule"]
    assert "may never stand in" in BASELINE_RISK_POLICY_V1["substitutions_forbidden"]


def test_r1_and_r2_have_distinct_estimands_reports_and_no_count_evidence():
    assert R1_CONTRACT["question_id"] == "R1"
    assert R2_CONTRACT["question_id"] == "R2"
    assert R1_CONTRACT["estimand_kind"] != R2_CONTRACT["estimand_kind"]
    assert R1_CONTRACT["unit_of_analysis"] == R2_CONTRACT["unit_of_analysis"] == "canonical_opportunity_id"

    assert "allowed-arm outcome mean" in R1_CONTRACT["primary_estimand"]
    assert "R-multiple difference" in R1_CONTRACT["primary_estimand"]
    assert "survival contrast" in R1_CONTRACT["secondary_estimand"]
    assert "RUIN_MODEL_V1" in R1_CONTRACT["secondary_estimand"]
    forbidden = " ".join(R1_CONTRACT["forbidden_endpoints"])
    assert "count of RISK_BLOCK" in forbidden
    assert "never evidence of benefit" in forbidden
    assert LEGACY_ARTIFACT in forbidden
    assert "observational" in R1_CONTRACT["claim_boundary"]

    assert R2_CONTRACT["guard_taxonomy"] == GUARD_TAXONOMY_V1
    assert len(R2_CONTRACT["guard_taxonomy"]) == 4
    assert len({guard["guard_id"] for guard in GUARD_TAXONOMY_V1}) == 4
    assert "only firing guard" in R2_CONTRACT["attribution_grain"]
    assert "multi-guard residual stratum" in R2_CONTRACT["attribution_grain"]
    assert "exactly the four declared guard contrasts" in R2_CONTRACT["multiplicity"]
    assert "RISK_BLOCK" in R2_CONTRACT["aggregate_forbidden"]
    assert "may never be inferred" in R2_CONTRACT["interaction_claims"]
    assert "RISK_BLOCK count" in R2_CONTRACT["no_borrowed_evidence"]

    # Separate canonical artifacts: R1 and R2 can never share one report.
    assert REPORT_OWNERSHIP["R1"] == "r1_risk_layer_effectiveness.json"
    assert REPORT_OWNERSHIP["R2"] == "r2_guard_attribution.json"
    assert REPORT_OWNERSHIP["R1"] != REPORT_OWNERSHIP["R2"]
    assert LEGACY_ARTIFACT not in set(REPORT_OWNERSHIP.values())
    assert LEGACY_REPORT_COMPATIBILITY[LEGACY_ARTIFACT] == ("Q10",)
    assert "never" in REPORT_VALIDITY_CONTRACT["compatibility_aliases"]

    # The registry still routes R1/R2 through the legacy artifact; RW9 must
    # re-point them at the adjudicated files above.
    for question_id in ("R1", "R2"):
        question = REGISTRY_BY_ID[question_id]
        assert question.report_filename == LEGACY_ARTIFACT
        assert question.legacy_ids == ("Q10",)


def test_r3_ruin_model_v1_is_frozen_and_reproducible():
    assert RUIN_MODEL_V1["model_version"] == RUIN_MODEL_VERSION == "ruin_model_v1"
    assert R3_CONTRACT["ruin_model"] is RUIN_MODEL_V1
    assert RUIN_MODEL_V1["minimum_samples"] == 50
    assert RUIN_MODEL_V1["risk_per_trade_pct"] == 0.01
    assert RUIN_MODEL_V1["ruin_threshold_pct"] == 0.50
    assert RUIN_MODEL_V1["acceptable_ruin_threshold"] == 0.05
    assert RUIN_MODEL_V1["monte_carlo"]["n_simulations"] == 10000
    assert RUIN_MODEL_V1["monte_carlo"]["trade_horizon"] == 5000
    assert "replacement" in RUIN_MODEL_V1["monte_carlo"]["estimator"]

    seed_contract = RUIN_MODEL_V1["seed_contract"]
    assert seed_contract["required"] is True
    assert isinstance(seed_contract["seed_base"], int)
    assert "random.Random(seed_base + s)" in seed_contract["per_simulation_seed"]
    assert "ascending" in seed_contract["ordering"]
    assert seed_contract["process_global_state_forbidden"] is True
    assert "reproduces" in RUIN_MODEL_V1["determinism_rule"]
    assert "exactly" in RUIN_MODEL_V1["determinism_rule"]
    assert "tolerance of 0.05" in RUIN_MODEL_V1["agreement_rule"]
    assert "invalidates the result" in RUIN_MODEL_V1["agreement_rule"]
    assert "analytical ruin probability" in R3_CONTRACT["required_outputs"]
    assert "no alternative ruin model" in R3_CONTRACT["no_alternative_model"]

    # The adjudicated coverage floor matches the registry's R3 rule.
    r3_rule = REGISTRY_BY_ID["R3"].validation_rules[0]
    assert r3_rule.field == "outcome_coverage"
    assert r3_rule.threshold == RUIN_MODEL_V1["outcome_coverage"]


def test_r4_grid_is_finite_frozen_chronological_and_research_only():
    assert HALT_THRESHOLD_GRID_V1 == (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
    assert len(HALT_THRESHOLD_GRID_V1) == 8
    assert list(HALT_THRESHOLD_GRID_V1) == sorted(HALT_THRESHOLD_GRID_V1)
    assert R4_CONTRACT["grid"] == HALT_THRESHOLD_GRID_V1
    assert R4_CONTRACT["grid_closed"] is True
    assert R4_CONTRACT["outcome_driven_expansion_forbidden"] is True
    assert R4_CONTRACT["parameter_search_forbidden"] is True

    assert "reaches or exceeds" in R4_CONTRACT["definitions"]["breach"]
    assert "regains the peak" in R4_CONTRACT["definitions"]["recovery"]
    assert "never imputed" in R4_CONTRACT["definitions"]["unrecovered_at_series_end"]
    assert "below 0.50" in R4_CONTRACT["halt_rule"]
    assert "grid maximum 0.50" in R4_CONTRACT["no_halt_fallback"]
    assert "half of the research halt threshold" in R4_CONTRACT["resume_rule"]

    simulation = R4_CONTRACT["simulation"]
    assert "strictly increasing canonical entry UTC" in simulation["order"]
    assert simulation["risk_per_trade_pct"] == RUIN_MODEL_V1["risk_per_trade_pct"]
    assert "at or after" in simulation["no_look_ahead"]
    assert len(R4_CONTRACT["required_outputs"]) == 4

    # R4 is research-only: it never becomes a runtime halt instruction.
    assert R4_CONTRACT["runtime_effect"] == "none"
    assert R4_CONTRACT["production_directive"] is False
    assert "never a runtime halt instruction" in R4_CONTRACT["claim_boundary"]


def test_r5_model_vocabulary_is_closed_and_objective_is_long_term_growth():
    families = tuple(dict.fromkeys(model["family"] for model in SIZING_MODELS_V1))
    assert families == R5_CONTRACT["declared_families"]
    assert set(families) == {"FIXED_RISK", "KELLY", "FRACTIONAL_KELLY", "FIXED_LOT", "DYNAMIC"}
    assert len(SIZING_MODELS_V1) == 8
    assert len({model["model_id"] for model in SIZING_MODELS_V1}) == 8
    assert R5_CONTRACT["vocabulary_closed"] is True

    fixed_lot = next(m for m in SIZING_MODELS_V1 if m["family"] == "FIXED_LOT")
    assert "constant canonical lot size" in fixed_lot["lot_rule"]
    fractional = [m for m in SIZING_MODELS_V1 if m["family"] == "FRACTIONAL_KELLY"]
    assert [m["kelly_multiplier"] for m in fractional] == [0.5, 0.25]

    assert "long-term growth" in R5_CONTRACT["objective"]
    assert "geometric mean" in R5_CONTRACT["objective"]
    forbidden = " ".join(R5_CONTRACT["forbidden_objectives"])
    assert "Sharpe" in forbidden
    assert "sharpe_approx" in forbidden
    assert "never the selection criterion" in forbidden

    assert "0.30" in R5_CONTRACT["eligibility"]
    assert "RUIN_MODEL_V1" in R5_CONTRACT["eligibility"]
    assert R5_CONTRACT["max_acceptable_drawdown"] == 0.30
    assert R5_CONTRACT["kelly_formula"] == "f* = (p * b - q) / b with b = avg_win_r / avg_loss_r and q = 1 - p"
    assert "ineligible" in R5_CONTRACT["negative_kelly_rule"]
    assert "may not feed live risk or lot sizing" in R5_CONTRACT["no_live_feedback"]


def test_multi_account_grain_prevents_fanout_pseudoreplication():
    assert ACCOUNT_GRAIN_CONTRACT["decision_grain"] == "canonical_opportunity_id"
    assert ACCOUNT_GRAIN_CONTRACT["required_grain_fields"] == ("canonical_opportunity_id", "account_id")
    assert "may never" in ACCOUNT_GRAIN_CONTRACT["account_fanout_rule"]
    assert "fanout" in ACCOUNT_GRAIN_CONTRACT["account_fanout_rule"]
    assert "one observation" in ACCOUNT_GRAIN_CONTRACT["account_fanout_rule"]
    assert "equal-weight mean" in ACCOUNT_GRAIN_CONTRACT["opportunity_outcome_rule"]
    assert "weights sum to one" in ACCOUNT_GRAIN_CONTRACT["weighting"]
    assert "never extra evidence" in ACCOUNT_GRAIN_CONTRACT["cross_account_heterogeneity"]
    assert ACCOUNT_GRAIN_CONTRACT["violation_state"].endswith("ACCOUNT_FANOUT_PSEUDOREPLICATION")

    # The risk contract cannot drift from the frozen exit-policy cluster rule.
    assert ACCOUNT_GRAIN_CONTRACT["decision_grain"] == HD09_COMMON_ANALYTICAL["cluster_identity"]
    shared_keys = (
        "finite_sample_correction",
        "reference_distribution",
        "confidence_level",
        "alpha",
        "test_sidedness",
    )
    assert {key: CLUSTERED_INFERENCE[key] for key in shared_keys} == {
        key: HD09_CLUSTERED_INFERENCE[key] for key in shared_keys
    }
    assert CLUSTERED_INFERENCE["minimum_clusters"] == SAMPLE_AND_READINESS_CONTRACT["common_cluster_minimum"]


def test_validation_chronology_is_frozen_and_dependency_ordered():
    assert VALIDATION_CHRONOLOGY["order"] == ("R3", "R4", "R5")
    assert VALIDATION_CHRONOLOGY["dependencies"]["R4"] == REGISTRY_BY_ID["R4"].depends_on == ("R3",)
    assert VALIDATION_CHRONOLOGY["dependencies"]["R5"] == REGISTRY_BY_ID["R5"].depends_on == ("E1", "R3")
    assert "registry dependency order is respected" in VALIDATION_CHRONOLOGY["rule"]
    assert "before R4 is evaluated, and R4 before R5" in VALIDATION_CHRONOLOGY["rule"]

    windows = VALIDATION_CHRONOLOGY["windows"]
    assert "earliest 70%" in windows["calibration"]
    assert "most recent 30%" in windows["validation"]
    assert "canonical entry-chronology order" in windows["calibration"]
    assert VALIDATION_CHRONOLOGY["minimum_validation_opportunities"] == 50
    assert "is excluded" in VALIDATION_CHRONOLOGY["purge"]
    assert "calibration evidence only" in VALIDATION_CHRONOLOGY["freeze_rule"]
    assert "invalidates the later question's result" in VALIDATION_CHRONOLOGY["no_retrofit"]


def test_readiness_reports_and_production_boundary_fail_closed():
    readiness = SAMPLE_AND_READINESS_CONTRACT
    assert readiness["self_declared_status_forbidden"] is True
    assert "null is COMPLETE" in readiness["COMPLETE"]
    assert "self-declared" in readiness["BLOCKED"]
    assert "insufficient governed coverage" in readiness["WAITING_DATA"]
    for question_id in TARGETS:
        assert question_id in readiness
    assert readiness["R3"]["outcome_coverage"] == RUIN_MODEL_V1["outcome_coverage"]
    assert readiness["R1"]["lineage_coverage"] == REGISTRY_BY_ID["R1"].validation_rules[0].threshold
    assert readiness["R2"]["outcome_coverage"] == REGISTRY_BY_ID["R2"].validation_rules[1].threshold

    # R3-R5 keep their existing canonical artifacts; R1/R2 must be re-pointed.
    for question_id in ("R3", "R4", "R5"):
        assert REPORT_OWNERSHIP[question_id] == REGISTRY_BY_ID[question_id].report_filename
    assert REPORT_VALIDITY_CONTRACT["research_only"] is True
    assert "never share an artifact" in REPORT_VALIDITY_CONTRACT["sole_owner"]
    assert any("identity hash" in item for item in REPORT_VALIDITY_CONTRACT["required_provenance"])

    boundary = PRODUCTION_APPLICATION_BOUNDARY
    assert boundary["authority"] == "research only"
    assert any("halt" in item for item in boundary["may_not"])
    assert any("risk-per-trade" in item for item in boundary["may_not"])
    assert "rollback plan" in boundary["application_requires"]
    assert "do not establish" in boundary["claim_boundary"]
    assert HD10_ADJUDICATED_CONTRACT["claim_boundary"] == boundary["claim_boundary"]

    # The single aggregated authority exposes every frozen block unchanged.
    assert HD10_ADJUDICATED_CONTRACT["baseline"] is BASELINE_RISK_POLICY_V1
    assert HD10_ADJUDICATED_CONTRACT["r1"] is R1_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["r2"] is R2_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["ruin_model"] is RUIN_MODEL_V1
    assert HD10_ADJUDICATED_CONTRACT["r3"] is R3_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["r4"] is R4_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["r5"] is R5_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["account_grain"] is ACCOUNT_GRAIN_CONTRACT
    assert HD10_ADJUDICATED_CONTRACT["validation_chronology"] is VALIDATION_CHRONOLOGY
    assert HD10_ADJUDICATED_CONTRACT["production_boundary"] is PRODUCTION_APPLICATION_BOUNDARY
    assert len(HD10_ADJUDICATED_CONTRACT["principles"]) == 7
