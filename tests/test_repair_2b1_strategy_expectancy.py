"""Focused Repair 2B.1 strategy expectancy and governance contracts."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.models import ReportValidity, RunnerStatus
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.strategy_expectancy import (
    ACTIVE_FAMILIES,
    REPORT_FILENAME,
    run_e3,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    STRUCTURALLY_OPERATIONAL_IDS,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a5_definitions import WAVE_A5_OWNERSHIP
from research_engine.registry.wave_a5_definitions import (
    E3_ACTIVE_V10_STRATEGY_FAMILIES,
    E3_EXCLUDED_STRATEGY_FAMILY,
    E3_LEGACY_COMPATIBILITY_TAXONOMY,
    E3_SUFFICIENCY_CONTRACT,
)
from research_engine.registry.definition_validator import build_definitions_from_registry


def _record(
    opportunity: str,
    family: str,
    r_value: float = 1.0,
    *,
    shadow_type: str = "PRIMARY_HORIZON_SIMULATION",
) -> dict:
    token = sum((index + 1) * ord(char) for index, char in enumerate(opportunity))
    shadow_id = f"nshadow_{token:016x}"
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "source_schema_version": "shadow_runtime_v1",
        "identity": {
            "trade_id": shadow_id,
            "shadow_trade_id": shadow_id,
            "canonical_opportunity_id": opportunity,
            "entity_id": f"entity-{opportunity}",
            "symbol": "EURUSD",
            "strategy_id": family,
            "shadow_type": shadow_type,
            "evaluated_horizon": "SCALP",
            "trade_horizon": "SCALP",
        },
        "decision_snapshot": {
            "pattern": "HAMMER",
            "strategy": family,
            "trade_horizon": "SCALP",
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": r_value},
    }


def _sufficient_population() -> list[dict]:
    rows = []
    for family_index, family in enumerate(ACTIVE_FAMILIES):
        for index in range(30):
            r_value = 1.0 + family_index / 10 if index < 24 else -0.5
            rows.append(_record(f"{family}-{index}", family, r_value))
    return rows


def test_e3_complete_uses_all_six_v10_families_and_outcome_statistics():
    report = run_e3(_sufficient_population())

    assert report["question_id"] == "E3"
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["sample_size"] == 180
    assert report["overall"]["taxonomy"]["active_families"] == list(ACTIVE_FAMILIES)
    assert report["overall"]["taxonomy"]["legacy_mapping_inferred"] is False
    assert set(report["overall"]["families"]) == set(ACTIVE_FAMILIES)
    first = report["overall"]["families"][ACTIVE_FAMILIES[0]]
    assert first["n_distinct_canonical_opportunities"] == 30
    assert first["mean_r"] == pytest.approx(0.7)
    assert first["win_rate"] == pytest.approx(0.8)
    assert first["interval_95"]["method"] == "normal_approximation_sample_standard_error"
    assert first["classification"] == "POSITIVE_EVIDENCE"
    assert report["fingerprint"]["epoch"] == "CURRENT"
    assert report["fingerprint"]["records_used"] == 180


def test_none_unknown_legacy_and_alternative_rows_are_excluded_without_mapping():
    rows = _sufficient_population()
    rows.extend([
        _record("none", StrategyFamily.NONE.value),
        _record("unknown", "REVERSAL"),
        _record("alternative", StrategyFamily.MEAN_REVERSION.value,
                shadow_type="HORIZON_ALTERNATIVE"),
    ])
    report = run_e3(rows)

    exclusions = report["overall"]["exclusions"]
    assert exclusions["strategy_family_none"] == 1
    assert exclusions["unknown_or_missing_strategy_family"] == 1
    assert exclusions["non_primary_horizon_simulation"] == 1
    assert report["dataset"]["sample_size"] == 180
    assert "REVERSAL" not in report["overall"]["families"]
    assert report["fingerprint"]["records_excluded"] == 3


def test_stale_and_duplicate_primary_rows_cannot_contribute_or_complete():
    rows = _sufficient_population()
    stale = _record("stale", StrategyFamily.FALSE_BREAK.value, 100.0)
    stale["schema_version"] = "shadow_trades_v0"
    duplicate = deepcopy(rows[0])
    duplicate["simulated_outcome"]["pnl_r_multiple"] = -100.0
    rows.extend([stale, duplicate])

    report = run_e3(rows)

    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["exclusions"]["duplicate_or_conflicting_primary_outcome"] == 2
    assert report["overall"]["exclusions"]["non_current_or_incompatible"] == 1
    assert report["dataset"]["sample_size"] == 179
    assert report["fingerprint"]["records_used"] == 179
    assert report["fingerprint"]["records_excluded"] == 3
    assert report["overall"]["families"][ACTIVE_FAMILIES[0]]["classification"] == "INSUFFICIENT_EVIDENCE"


def test_family_threshold_total_and_coverage_gates_remain_fail_closed():
    rows = _sufficient_population()
    report = run_e3(rows[:-1])
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["families"][ACTIVE_FAMILIES[-1]][
        "n_distinct_canonical_opportunities"
    ] == 29

    mostly_unknown = [
        _record(f"none-{index}", StrategyFamily.NONE.value) for index in range(200)
    ] + rows
    report = run_e3(mostly_unknown)
    assert report["overall"]["coverage"]["strategy_coverage"] < 0.50
    assert report["status"] == "INSUFFICIENT_DATA"


def test_digest_is_exact_and_deterministic_for_analysed_observations():
    rows = _sufficient_population()
    first = run_e3(rows)
    reordered = run_e3(list(reversed(rows)))
    changed_rows = deepcopy(rows)
    changed_rows[0]["simulated_outcome"]["pnl_r_multiple"] = 2.0
    changed = run_e3(changed_rows)

    assert first["fingerprint"]["evidence_provenance"] == reordered["fingerprint"]["evidence_provenance"]
    assert first["fingerprint"]["dataset_id"] != changed["fingerprint"]["dataset_id"]


def test_resolver_accepts_e3_and_q24_cannot_masquerade_as_e3():
    report = run_e3(_sufficient_population())
    validity, _ = resolve_report_validity(
        REPORT_FILENAME, report, expected_question_id="E3",
    )
    assert validity == ReportValidity.VALID_CURRENT

    q24 = deepcopy(report)
    q24["question_id"] = "Q24"
    validity, _ = resolve_report_validity(
        "q24_strategy_edge.json", q24, expected_question_id="E3",
    )
    assert validity == ReportValidity.INVALIDATED


def test_s1_is_explicit_alias_projection_and_has_no_runner_or_report(tmp_path):
    report = run_e3(_sufficient_population())
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    evidence = {"shadow_trades": _sufficient_population()}

    e3 = build_question_state("E3", reports_dir=tmp_path, evidence_source=evidence)
    s1 = build_question_state("S1", reports_dir=tmp_path, evidence_source=evidence)

    assert e3.state_status == "COMPLETE"
    assert s1.state_status == "COMPLETE"
    assert s1.identity_status == "SUPERSEDED_ALIAS"
    assert s1.scientific_owner_id == "E3"
    assert s1.runner_status == RunnerStatus.ALIAS
    assert s1.latest_result["scientific_owner_id"] == "E3"
    assert s1.authoritative_report is None
    assert REGISTRY_BY_ID["S1"].runner_module == ""
    assert REGISTRY_BY_ID["S1"].report_filename == ""


def test_static_governance_registry_and_ownership_are_adjudicated():
    hd01 = HUMAN_SEMANTIC_DECISIONS["HD01"]
    hd06 = HUMAN_SEMANTIC_DECISIONS["HD06"]
    relationship = WAVE_A5_OWNERSHIP[("E3", "S1")]

    assert hd01.implementation_blocked_until_decision is False
    assert hd01.exact_decision.startswith("ADJUDICATED: E3")
    assert "E3 owns" in hd01.recommended_default
    assert hd06.implementation_blocked_until_decision is False
    assert relationship.canonical_owners == (
        ("research_engine.experiments.strategy_expectancy.run_e3", "E3"),
        (REPORT_FILENAME, "E3"),
    )
    assert canonical_report_owner(REPORT_FILENAME) == "E3"
    assert E3_ACTIVE_V10_STRATEGY_FAMILIES == ACTIVE_FAMILIES
    assert E3_EXCLUDED_STRATEGY_FAMILY == "NONE"
    assert E3_LEGACY_COMPATIBILITY_TAXONOMY == (
        "REVERSAL", "CONTINUATION", "FALSE_BREAK",
    )
    assert E3_SUFFICIENCY_CONTRACT[
        "minimum_distinct_canonical_opportunities_per_family"
    ] == 30
    assert {"E3", "S1"} <= STRUCTURALLY_OPERATIONAL_IDS
    assert len(REGISTRY) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    definitions = build_definitions_from_registry(REGISTRY)
    assert all(definition.definition_version == 1 for definition in definitions.values())
    assert definitions["S1"].lifecycle_status.value == "SUPERSEDED"
    assert definitions["S1"].scientific_owner_id == "E3"
    assert REGISTRY_BY_ID["S5"].runner_module == (
        "research_engine.experiments.strategy_identity_expectancy"
    )
    assert REGISTRY_BY_ID["S5"].runner_function == "run_s5"
    assert REGISTRY_BY_ID["S5"].report_filename == "s5_strategy_identity_expectancy.json"
    assert REGISTRY_BY_ID["S6"].runner_module == "research_engine.experiments.horizon_expectancy"
    assert REGISTRY_BY_ID["S7"].runner_module == (
        "research_engine.experiments.strategy_horizon_interaction"
    )
