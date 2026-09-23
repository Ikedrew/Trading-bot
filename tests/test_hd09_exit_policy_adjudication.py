"""Focused governance tests for the frozen HD09 exit-policy contract."""
from __future__ import annotations

from dataclasses import fields

from core.shadow.assumptions import build_assumptions
from core.shadow.models import TIMEOUT_BARS
from research_engine.control_plane.exit_bar_path import ExitBarPathRecord
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.exit_policy_adjudication import (
    BASELINE_POLICY_ID,
    BASELINE_POLICY_V1,
    BASELINE_REPRODUCTION_CONTRACT,
    CANDIDATE_POLICIES_V1,
    CANDIDATE_REPLAY_CONTRACT,
    CLUSTERED_INFERENCE,
    COMMON_ANALYTICAL_CONTRACT,
    EX1_CONTRACT,
    EX2_CONTRACT,
    EX9_CONTRACT,
    EX10_CONTRACT,
    HD09_ADJUDICATED_CONTRACT,
    HETEROGENEITY_CONTRACT,
    REPORT_OWNERSHIP,
    REPORT_VALIDITY_CONTRACT,
    SAMPLE_AND_READINESS_CONTRACT,
)
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_TARGETS


TARGETS = ("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10")


def test_hd09_is_closed_by_one_named_complete_authority_with_targeted_operational_gain():
    decision = HUMAN_SEMANTIC_DECISIONS["HD09"]
    assert decision.affected_question_ids == TARGETS
    assert decision.implementation_blocked_until_decision is False
    assert decision.recommended_default == (
        "ADJUDICATED: use research_engine.registry.exit_policy_adjudication."
        "HD09_ADJUDICATED_CONTRACT."
    )
    assert HD09_ADJUDICATED_CONTRACT["implementation_blocked_until_decision"] is False
    assert operational_baseline() == (52, 18)


def test_baseline_v1_delegates_timeout_to_existing_shadow_authority():
    assert BASELINE_POLICY_ID == "SHADOW_BASELINE_V1"
    assert BASELINE_POLICY_V1["policy_version"] == 1
    assert BASELINE_POLICY_V1["authoritative_timeout_bars"] == TIMEOUT_BARS == {
        "SCALP": 9,
        "INTRADAY": 96,
        "EXTENDED": 864,
    }
    for horizon, timeout in TIMEOUT_BARS.items():
        basis = "ASK" if horizon != "INTRADAY" else "BID"
        assert build_assumptions(
            horizon=horizon, entry_price_basis=basis,
        )["timeout_bars"] == timeout
    assert BASELINE_POLICY_V1["fill_model"] == "EXACT_PRICE"
    assert BASELINE_POLICY_V1["same_bar_sl_tp_collision"] == "SL_FIRST"
    assert BASELINE_POLICY_V1["barrier_order"] == (
        "effective_protective_stop", "take_profit", "timeout",
    )
    assert "close" in BASELINE_POLICY_V1["timeout_price"]


def test_reproduction_is_exact_fail_closed_and_path_extension_is_satisfied():
    contract = BASELINE_REPRODUCTION_CONTRACT
    assert contract["required"] is True
    assert contract["no_approximation"] is True
    assert contract["exit_reason_rule"].startswith("exact")
    assert contract["bars_held_rule"] == "exact integer equality"
    assert "round(replay_R, 4)" in contract["r_rule"]
    assert contract["missing_field_rule"].endswith(
        "BASELINE_REPRODUCTION_AUTHORITY_MISSING"
    )
    assert "BASELINE_REPRODUCTION_FAILED" in contract["disagreement_rule"]
    required = set(contract["required_observed_fields"])
    extension = contract["exit_bar_path_v1_required_extension"]
    current_path_fields = {item.name for item in fields(ExitBarPathRecord)}
    assert set(extension["fields"]) <= current_path_fields
    assert not required - current_path_fields
    assert "exit_utc_epoch_s" in current_path_fields
    assert "analytical_digest" in extension["provenance_rule"]


def test_exactly_nine_frozen_candidates_and_replay_ordering():
    assert len(CANDIDATE_POLICIES_V1) == 9
    assert len({item["policy_id"] for item in CANDIDATE_POLICIES_V1}) == 9
    trailing = [item for item in CANDIDATE_POLICIES_V1 if item["policy_type"] == "TRAILING"]
    reduced = [item for item in CANDIDATE_POLICIES_V1 if item["policy_type"] == "REDUCED_TP"]
    timed = [item for item in CANDIDATE_POLICIES_V1 if item["policy_type"] == "TIME_CAP"]
    assert [(item["activation_r"], item["distance_r"]) for item in trailing] == [
        (0.25, 0.10), (0.50, 0.25), (1.00, 0.50),
    ]
    assert [item["target_cap_r"] for item in reduced] == [0.50, 1.00, 1.50]
    assert [item["bar_cap"] for item in timed] == [20, 60, 180]
    assert CANDIDATE_REPLAY_CONTRACT["vocabulary_closed"] is True
    assert CANDIDATE_REPLAY_CONTRACT["parameter_search_forbidden"] is True
    assert CANDIDATE_REPLAY_CONTRACT["trailing"]["effective_next_bar"] is True
    assert CANDIDATE_REPLAY_CONTRACT["trailing"]["activation_bar_rule"].endswith(
        "within that same bar"
    )
    assert CANDIDATE_REPLAY_CONTRACT["trailing"]["same_bar_trailing_tp_collision"].startswith(
        "effective protective stop first"
    )
    assert CANDIDATE_REPLAY_CONTRACT["time_cap"]["cap_bar_order"].startswith(
        "original SL first, original TP second"
    )


def test_pairing_clustering_weighting_and_inference_are_fully_frozen():
    assert COMMON_ANALYTICAL_CONTRACT["pairing_identity"] == (
        "shadow_trade_id", "canonical_opportunity_id", "trade_horizon",
    )
    assert COMMON_ANALYTICAL_CONTRACT["cluster_identity"] == "canonical_opportunity_id"
    assert COMMON_ANALYTICAL_CONTRACT["horizon_weight"] == "w_oh = 1 / k_o"
    assert "weights sum to one" in COMMON_ANALYTICAL_CONTRACT["horizon_weight_definition"]
    assert "account/broker" in COMMON_ANALYTICAL_CONTRACT["account_fanout_rule"]
    assert CLUSTERED_INFERENCE["finite_sample_correction"].startswith("none (CR0)")
    assert CLUSTERED_INFERENCE["reference_distribution"] == (
        "asymptotic standard normal for scalar contrasts"
    )
    assert CLUSTERED_INFERENCE["confidence_level"] == 0.95
    assert CLUSTERED_INFERENCE["alpha"] == 0.05
    assert CLUSTERED_INFERENCE["test_sidedness"] == "two-sided"


def test_ex1_and_ex2_estimands_multiplicity_and_null_completion_are_exact():
    assert EX1_CONTRACT["paired_endpoint"] == (
        "candidate simulated R - reproduced SHADOW_BASELINE_V1 R"
    )
    assert "exactly nine" in EX1_CONTRACT["holm_family"]
    assert "Holm-adjusted p <= 0.05" in EX1_CONTRACT["positive_improvement"]
    assert "COMPLETE" in EX1_CONTRACT["valid_null"]

    assert len(EX2_CONTRACT["candidates"]) == 3
    assert "including the reproduced baseline exit bar" in EX2_CONTRACT["mfe_window"]
    assert EX2_CONTRACT["eligibility"].endswith("strictly greater than zero")
    assert EX2_CONTRACT["paired_endpoint"] == (
        "candidate retention - baseline retention"
    )
    assert "exactly three" in EX2_CONTRACT["holm_family"]
    assert "COMPLETE" in EX2_CONTRACT["valid_null"]


def test_ex9_has_two_exact_endpoints_one_complete_18_test_family():
    endpoint_a = EX9_CONTRACT["endpoint_a"]
    endpoint_b = EX9_CONTRACT["endpoint_b"]
    assert endpoint_a["paired_endpoint"] == "candidate indicator - baseline indicator"
    assert endpoint_a["supported_reduction"].startswith("estimate < 0")
    assert "baseline exit_reason == timeout" in endpoint_b["population"]
    assert "baseline R < 0" in endpoint_b["population"]
    assert endpoint_b["candidate_endpoint"].startswith("1 iff candidate simulated R > 0")
    assert endpoint_b["minimum_distinct_canonical_opportunities"] == 30
    assert "exactly 18" in EX9_CONTRACT["holm_family"]
    assert EX9_CONTRACT["family_completeness"].startswith("all 18")
    assert "COMPLETE" in EX9_CONTRACT["valid_null"]


def test_later_heterogeneity_and_walk_forward_contracts_are_not_left_open():
    assert HETEROGENEITY_CONTRACT["questions"] == ("EX5", "EX6", "EX7", "EX8")
    assert HETEROGENEITY_CONTRACT["minimum_distinct_opportunities_per_candidate_dimension_cell"] == 30
    assert HETEROGENEITY_CONTRACT["minimum_levels"] == 2
    assert HETEROGENEITY_CONTRACT["dimensions"]["EX7"]["h4_fallback_forbidden"] is True
    assert "core.v10.strategy_family.StrategyFamily" in HETEROGENEITY_CONTRACT["dimensions"]["EX6"]["source"]
    assert HETEROGENEITY_CONTRACT["dimensions"]["EX6"]["excluded_level"] == "NONE"
    assert HETEROGENEITY_CONTRACT["valid_null"].endswith("COMPLETE")

    assert EX10_CONTRACT["fold_type"] == "five-fold expanding-window validation"
    assert EX10_CONTRACT["minimum_training_opportunities"] == 200
    assert EX10_CONTRACT["minimum_validation_opportunities_per_fold"] == 50
    assert EX10_CONTRACT["minimum_valid_folds"] == 5
    assert EX10_CONTRACT["minimum_total_distinct_opportunities"] == 450
    assert "training evidence only" in EX10_CONTRACT["selection"]
    assert "300 seconds" in EX10_CONTRACT["embargo"]
    assert EX10_CONTRACT["valid_null"].endswith("COMPLETE")


def test_readiness_validity_and_report_ownership_are_question_specific():
    assert SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"] == 0.95
    for qid in ("EX1", "EX2", "EX9"):
        assert SAMPLE_AND_READINESS_CONTRACT[qid]["minimum_paired_lifecycles"] == 200
        assert SAMPLE_AND_READINESS_CONTRACT[qid]["minimum_distinct_opportunities"] == 200
    assert SAMPLE_AND_READINESS_CONTRACT["EX9"][
        "endpoint_b_minimum_distinct_opportunities"
    ] == 30
    assert "baseline reproduction failure" in SAMPLE_AND_READINESS_CONTRACT["BLOCKED"]
    assert "insufficient governed" in SAMPLE_AND_READINESS_CONTRACT["WAITING_DATA"]
    assert "null/no-reliable-difference" in SAMPLE_AND_READINESS_CONTRACT["COMPLETE"]
    assert REPORT_VALIDITY_CONTRACT["compatibility_aliases"] == ()
    assert REPORT_VALIDITY_CONTRACT["reorder_invariance"] is True
    assert tuple(REPORT_OWNERSHIP) == TARGETS
    for qid, filename in REPORT_OWNERSHIP.items():
        assert REGISTRY_BY_ID[qid].report_filename == filename


def test_registry_versions_and_no_runner_baseline_remain_frozen():
    definitions = build_definitions_from_registry(REGISTRY)
    values = definitions.values() if isinstance(definitions, dict) else definitions
    assert len(REGISTRY) == len({question.id for question in REGISTRY}) == 70
    assert len(definitions) == 70
    assert {definition.definition_version for definition in values} == {1}
    assert WAVE_A_NO_RUNNER_TARGETS == {"L6", "G1", "G2", "G3"}
    assert operational_baseline() == (52, 18)
