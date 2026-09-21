"""Focused Repair 2B.3 S6 strategy-adjusted horizon expectancy contracts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from core.horizon.horizon_models import TradeHorizon
from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.evidence_provenance import attest_current_subset
from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.horizon_expectancy import (
    REPORT_FILENAME,
    S6_SUFFICIENCY_CONTRACT,
    run_s6,
)
from research_engine.experiments.strategy_expectancy import ACTIVE_FAMILIES, LEGACY_TAXONOMY
from research_engine.experiments.strategy_identity_expectancy import (
    CANONICAL_HORIZONS,
    MIN_CELL_OPPORTUNITIES,
    MIN_FAMILY_OPPORTUNITIES,
    MIN_OVERALL_OPPORTUNITIES,
    _cluster_opportunities,
    _estimate_family_effects,
    _select_horizon_rows,
    run_s5,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
)
from research_engine.runner_discovery import discover_runners


HORIZONS = ("SCALP", "INTRADAY", "EXTENDED")
FAMILY_EFFECT = {family: (index - 2.5) * 0.2 for index, family in enumerate(ACTIVE_FAMILIES)}
HORIZON_EFFECT = {"SCALP": 0.8, "INTRADAY": 0.3, "EXTENDED": -0.2}


def _shadow_id(opportunity: str, family: str, horizon: str) -> str:
    digest = hashlib.sha256(f"{opportunity}|{family}|{horizon}".encode()).hexdigest()
    return f"nshadow_{digest[:16]}"


def _row(opportunity: str, family: str, horizon: str, value: float) -> dict:
    shadow_id = _shadow_id(opportunity, family, horizon)
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
            "shadow_type": (
                "PRIMARY_HORIZON_SIMULATION" if horizon == "SCALP"
                else "HORIZON_ALTERNATIVE"
            ),
            "evaluated_horizon": horizon,
            "trade_horizon": horizon,
        },
        "decision_snapshot": {
            "pattern": "HAMMER",
            "strategy": family,
            "trade_horizon": horizon,
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": value},
    }


def _balanced(per_family: int = 30, *, base: float = 0.0, shock: float = 0.2) -> list[dict]:
    rows: list[dict] = []
    for family in ACTIVE_FAMILIES:
        for index in range(per_family):
            opportunity = f"{family}-{index}"
            cluster_shock = shock if index % 2 == 0 else -shock
            for horizon in HORIZONS:
                value = base + FAMILY_EFFECT[family] + HORIZON_EFFECT[horizon] + cluster_shock
                rows.append(_row(opportunity, family, horizon, round(value, 6)))
    return rows


def _composition_biased() -> list[dict]:
    """Additive truth with deliberately different family composition by horizon."""
    rows: list[dict] = []
    for family_index, family in enumerate(ACTIVE_FAMILIES):
        for horizon_index, horizon in enumerate(HORIZONS):
            count = 20
            if family_index == 5 and horizon == "SCALP":
                count = 100
            if family_index == 0 and horizon == "EXTENDED":
                count = 100
            for index in range(count):
                opportunity = f"composition-{family}-{horizon}-{index}"
                noise = 0.1 if index % 2 == 0 else -0.1
                value = FAMILY_EFFECT[family] + HORIZON_EFFECT[horizon] + noise
                rows.append(_row(opportunity, family, horizon, value))
    return rows


def test_taxonomy_and_horizon_authorities_are_exact():
    report = run_s6([])
    assert tuple(StrategyFamily(item).value for item in ACTIVE_FAMILIES) == ACTIVE_FAMILIES
    assert len(ACTIVE_FAMILIES) == 6
    assert StrategyFamily.NONE.value not in ACTIVE_FAMILIES
    assert report["overall"]["taxonomy"]["legacy_compatibility_only"] == list(LEGACY_TAXONOMY)
    assert report["overall"]["taxonomy"]["legacy_mapping_inferred"] is False
    assert CANONICAL_HORIZONS == tuple(item.value for item in TradeHorizon) == HORIZONS
    assert report["overall"]["horizon_taxonomy"]["unsupported_horizons_bucketed"] is False


def test_none_legacy_families_and_unknown_horizons_are_excluded():
    rows = [
        _row("none", "NONE", "SCALP", 1.0),
        _row("legacy", "REVERSAL", "SCALP", 1.0),
        _row("unknown", ACTIVE_FAMILIES[0], "SWING", 1.0),
        _row("valid", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ]
    eligible, exclusions = _select_horizon_rows(rows)
    assert [row["identity"]["canonical_opportunity_id"] for row in eligible] == ["valid"]
    assert exclusions["strategy_family_none"] == 1
    assert exclusions["unknown_or_missing_strategy_family"] == 1
    assert exclusions["unknown_or_unsupported_horizon"] == 1


def test_current_normalized_evidence_and_repeated_horizons_are_retained():
    rows = _balanced(30)
    report = run_s6(rows)
    assert report["dataset"]["source"] == "shadow_trades_v1 normalized from shadow_runtime_v1"
    assert report["overall"]["analysed_row_count"] == 540
    assert report["overall"]["distinct_canonical_opportunities"] == 180
    assert report["fingerprint"]["records_used"] == 540
    assert report["overall"]["pairwise_availability"]["counts"] == {
        "SCALP|INTRADAY": 180,
        "SCALP|EXTENDED": 180,
        "INTRADAY|EXTENDED": 180,
    }


def test_stale_and_legacy_evidence_cannot_satisfy_s6():
    rows = _balanced(30)
    stale = _row("stale", ACTIVE_FAMILIES[0], "SCALP", 999.0)
    stale["schema_version"] = "shadow_trades_v0"
    report = run_s6(rows + [stale])
    assert report["fingerprint"]["records_used"] == len(rows)
    assert report["overall"]["exclusions"]["non_current_or_incompatible"] == 1

    legacy = _row("legacy", ACTIVE_FAMILIES[0], "SCALP", 1.0)
    legacy["schema_version"] = "research_shadow_trades_v1"
    legacy_report = run_s6([legacy])
    assert legacy_report["overall"]["distinct_canonical_opportunities"] == 0
    assert legacy_report["status"] == "INSUFFICIENT_DATA"


def test_cluster_identity_and_fail_closed_consistency():
    rows = [_row("same", ACTIVE_FAMILIES[0], horizon, 0.5) for horizon in HORIZONS]
    eligible, _ = _select_horizon_rows(rows)
    clusters, exclusions = _cluster_opportunities(eligible)
    assert len(clusters) == 1
    assert len(clusters[0]["observations"]) == 3

    conflict = rows + [_row("same", ACTIVE_FAMILIES[1], "SCALP", 0.4)]
    eligible, _ = _select_horizon_rows(conflict)
    clusters, exclusions = _cluster_opportunities(eligible)
    assert clusters == []
    assert exclusions["conflicting_strategy_identity_within_opportunity"] == 4


def test_duplicate_horizon_rows_fail_closed_and_cannot_inflate_counts():
    rows = _balanced(30)
    rows.append(deepcopy(rows[0]))
    report = run_s6(rows)
    assert report["overall"]["exclusions"]["duplicate_or_conflicting_horizon_simulation"] == 4
    assert report["overall"]["distinct_canonical_opportunities"] == 179
    assert report["overall"]["analysed_row_count"] == 541


def test_each_opportunity_has_total_estimator_weight_one():
    rows = [_row("three", ACTIVE_FAMILIES[0], horizon, 0.1) for horizon in HORIZONS]
    rows += [_row("one", ACTIVE_FAMILIES[1], "SCALP", 0.2)]
    eligible, _ = _select_horizon_rows(rows)
    clusters, _ = _cluster_opportunities(eligible)
    weights = {cluster["opportunity_id"]: sum(1 / len(cluster["observations"]) for _ in cluster["observations"]) for cluster in clusters}
    assert weights == {"one": 1.0, "three": 1.0}


def test_strategy_adjustment_differs_from_raw_composition_biased_means():
    report = run_s6(_composition_biased())
    assert report["status"] == "COMPLETE"
    horizons = report["overall"]["horizons"]
    assert horizons["SCALP"]["raw_mean_r"] != horizons["SCALP"]["adjusted_effect"]["estimate_r"]
    assert horizons["EXTENDED"]["raw_mean_r"] != horizons["EXTENDED"]["adjusted_effect"]["estimate_r"]
    expected_family_average = sum(FAMILY_EFFECT.values()) / len(FAMILY_EFFECT)
    for horizon in HORIZONS:
        assert horizons[horizon]["adjusted_effect"]["estimate_r"] == pytest.approx(
            HORIZON_EFFECT[horizon] + expected_family_average, abs=1e-6
        )


def test_s6_is_the_dual_marginal_not_s5_relabelled():
    rows = _balanced(30)
    s5 = run_s5(rows)
    s6 = run_s6(rows)
    assert s5["question_id"] == "S5" and s6["question_id"] == "S6"
    assert s5["overall"]["estimand"] == "HORIZON_ADJUSTED_STRATEGY_FAMILY_EFFECT"
    assert s6["overall"]["estimand"] == "STRATEGY_ADJUSTED_HORIZON_EFFECT"
    assert "families" in s5["overall"] and "horizons" in s6["overall"]


def test_estimator_and_results_are_deterministic_under_row_reordering():
    rows = _composition_biased()
    first = run_s6(rows)
    reordered = run_s6(list(reversed(deepcopy(rows))))
    assert first["overall"]["horizons"] == reordered["overall"]["horizons"]
    assert first["fingerprint"]["dataset_id"] == reordered["fingerprint"]["dataset_id"]


def test_cluster_aware_sandwich_uncertainty_is_emitted_for_each_horizon():
    report = run_s6(_balanced(30))
    for result in report["overall"]["horizons"].values():
        interval = result["adjusted_effect"]["interval_95"]
        assert interval["method"] == "opportunity_clustered_sandwich"
        assert interval["cluster"] == "canonical_opportunity_id"
        assert interval["z"] == 1.96
        estimate = result["adjusted_effect"]["estimate_r"]
        se = result["adjusted_effect"]["standard_error"]
        assert interval["lower_r"] == pytest.approx(estimate - 1.96 * se, abs=2e-6)
        assert interval["upper_r"] == pytest.approx(estimate + 1.96 * se, abs=2e-6)


def test_shared_fit_exposes_strategy_adjusted_horizon_contrasts():
    eligible, _ = _select_horizon_rows(_balanced(30))
    clusters, _ = _cluster_opportunities(eligible)
    fit = _estimate_family_effects(clusters)
    assert fit is not None
    assert set(fit["horizon_adjusted_effects"]) == set(HORIZONS)
    assert all(
        item["interval_95"]["method"] == "opportunity_clustered_sandwich"
        for item in fit["horizon_adjusted_effects"].values()
    )


def test_positive_point_estimate_without_positive_lower_bound_is_not_positive_evidence():
    rows = _balanced(30, base=-0.25, shock=5.0)
    report = run_s6(rows)
    intraday = report["overall"]["horizons"]["INTRADAY"]["adjusted_effect"]
    assert intraday["estimate_r"] > 0
    assert intraday["interval_95"]["lower_r"] <= 0
    assert intraday["classification"] == "NON_POSITIVE_EVIDENCE"


def test_below_100_overall_is_insufficient():
    report = run_s6(_balanced(10))
    assert report["overall"]["distinct_canonical_opportunities"] == 60
    assert report["overall"]["sufficiency"]["overall_sufficient"] is False
    assert report["status"] == "INSUFFICIENT_DATA"


def test_below_30_in_any_family_is_insufficient_even_above_100_overall():
    rows = _balanced(30)
    rows = [row for row in rows if not row["identity"]["canonical_opportunity_id"].endswith("-29") or row["identity"]["strategy_id"] != ACTIVE_FAMILIES[0]]
    report = run_s6(rows)
    assert report["overall"]["distinct_canonical_opportunities"] > 100
    assert report["overall"]["sufficiency"]["families_sufficient"] is False
    assert report["status"] == "INSUFFICIENT_DATA"


def test_below_20_in_any_required_cell_is_insufficient():
    rows = _balanced(30)
    rows = [
        row for row in rows
        if not (
            row["identity"]["strategy_id"] == ACTIVE_FAMILIES[0]
            and row["identity"]["evaluated_horizon"] == "EXTENDED"
            and int(row["identity"]["canonical_opportunity_id"].rsplit("-", 1)[1]) >= 19
        )
    ]
    report = run_s6(rows)
    assert report["overall"]["sufficiency"]["overall_sufficient"] is True
    assert report["overall"]["sufficiency"]["families_sufficient"] is True
    assert report["overall"]["cells"]["cell_n_distinct_canonical_opportunities"][f"{ACTIVE_FAMILIES[0]}|EXTENDED"] == 19
    assert report["status"] == "INSUFFICIENT_DATA"


def test_missing_required_horizon_is_insufficient_and_never_omitted():
    rows = [row for row in _balanced(30) if row["identity"]["evaluated_horizon"] != "EXTENDED"]
    report = run_s6(rows)
    assert set(report["overall"]["horizons"]) == set(HORIZONS)
    assert report["overall"]["horizons"]["EXTENDED"]["adjusted_effect"]["classification"] == "INSUFFICIENT_EVIDENCE"
    assert report["overall"]["sufficiency"]["all_horizons_present"] is False
    assert report["status"] == "INSUFFICIENT_DATA"


def test_sufficient_18_cell_population_permits_completion():
    report = run_s6(_balanced(30))
    assert report["status"] == "COMPLETE"
    assert report["overall"]["cells"]["required_cells"] == 18
    assert report["overall"]["cells"]["observed_cells"] == 18
    assert report["overall"]["cells"]["insufficient_cells"] == []
    assert all(
        result["adjusted_effect"]["classification"] in {"POSITIVE_EVIDENCE", "NON_POSITIVE_EVIDENCE"}
        for result in report["overall"]["horizons"].values()
    )


def test_exact_100_30_20_contract_is_frozen():
    assert MIN_OVERALL_OPPORTUNITIES == 100
    assert MIN_FAMILY_OPPORTUNITIES == 30
    assert MIN_CELL_OPPORTUNITIES == 20
    assert S6_SUFFICIENCY_CONTRACT["minimum_distinct_canonical_opportunities_overall"] == 100
    assert S6_SUFFICIENCY_CONTRACT["minimum_distinct_canonical_opportunities_per_family"] == 30
    assert S6_SUFFICIENCY_CONTRACT["minimum_distinct_canonical_opportunities_per_family_horizon_cell"] == 20
    assert S6_SUFFICIENCY_CONTRACT["required_family_horizon_cells"] == 18


def test_provenance_attests_exact_rows_and_excludes_stale_from_component_digest():
    rows = _balanced(30)
    stale = _row("stale", ACTIVE_FAMILIES[0], "SCALP", 42.0)
    stale["schema_version"] = "shadow_trades_v0"
    baseline = run_s6(rows)
    report = run_s6(rows + [stale])
    base_component = baseline["fingerprint"]["evidence_provenance"]["components"][0]
    component = report["fingerprint"]["evidence_provenance"]["components"][0]
    assert component["selection"] == "CURRENT_SUBSET"
    assert component["records_used"] == 540
    assert component["digest"] == base_component["digest"]
    assert report["fingerprint"]["records_excluded"] == 1


def test_provenance_digest_changes_with_analytical_evidence_and_is_order_invariant():
    rows = _balanced(30)
    first = run_s6(rows)
    reordered = run_s6(list(reversed(deepcopy(rows))))
    assert first["fingerprint"]["evidence_provenance"]["digest"] == reordered["fingerprint"]["evidence_provenance"]["digest"]
    changed_rows = deepcopy(rows)
    changed_rows[0]["simulated_outcome"]["pnl_r_multiple"] += 1.0
    changed = run_s6(changed_rows)
    assert changed["fingerprint"]["evidence_provenance"]["digest"] != first["fingerprint"]["evidence_provenance"]["digest"]


def test_invalid_provenance_subset_fails_closed():
    rows = _balanced(30)
    fabricated = _row("fabricated", ACTIVE_FAMILIES[0], "SCALP", 9.9)
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows[:10], rows[:10] + [fabricated])


def test_s6_solely_owns_its_distinct_report():
    assert REPORT_FILENAME == WAVE_A_NO_RUNNER_DESIGNS["S6"].report_identity
    assert canonical_report_owner(REPORT_FILENAME) == "S6"
    assert REGISTRY_BY_ID["S6"].report_filename == REPORT_FILENAME
    assert resolve_report_ownership(REPORT_FILENAME, "S6").allowed is True
    for other in ("S5", "S7", "E3", "Q24"):
        decision = resolve_report_ownership(REPORT_FILENAME, other)
        assert decision.allowed is False
        assert decision.canonical_owner == "S6"


def test_relabelled_s6_report_is_invalidated():
    report = run_s6(_balanced(30))
    validity, _ = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="S6")
    assert validity == ReportValidity.VALID_CURRENT
    relabelled = deepcopy(report)
    relabelled["question_id"] = "S5"
    validity, _ = resolve_report_validity(REPORT_FILENAME, relabelled, expected_question_id="S6")
    assert validity == ReportValidity.INVALIDATED


def test_runner_registry_and_structural_completion_are_current():
    question = REGISTRY_BY_ID["S6"]
    assert question.runner_module == "research_engine.experiments.horizon_expectancy"
    assert question.runner_function == "run_s6"
    assert "S6" in discover_runners()
    assert "S6" in STRUCTURALLY_OPERATIONAL_IDS
    assert operational_baseline() == (45, 25)


def test_insufficient_current_report_is_waiting_data_without_finding(tmp_path):
    rows = _balanced(10)
    report = run_s6(rows)
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    state = build_question_state("S6", reports_dir=tmp_path, evidence_source={"shadow_trades": rows})
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.state_status == "WAITING_DATA"
    assert state.readiness_status == ReadinessStatus.WAITING_DATA
    assert state.latest_finding == ""


def test_sufficient_current_report_is_complete(tmp_path):
    rows = _balanced(30)
    report = run_s6(rows)
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    state = build_question_state("S6", reports_dir=tmp_path, evidence_source={"shadow_trades": rows})
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.state_status == "COMPLETE"
    assert state.readiness_status == ReadinessStatus.COMPLETE


def test_missing_s6_report_keeps_implemented_runner_blocked(tmp_path):
    state = build_question_state("S6", reports_dir=tmp_path, evidence_source={})
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.MISSING
    assert state.state_status == "BLOCKED"


def test_non_target_contracts_and_integrity_are_preserved():
    assert REGISTRY_BY_ID["S5"].runner_function == "run_s5"
    assert REGISTRY_BY_ID["S5"].report_filename == "s5_strategy_identity_expectancy.json"
    assert REGISTRY_BY_ID["S7"].runner_module == "research_engine.experiments.strategy_horizon_interaction"
    assert REGISTRY_BY_ID["S7"].runner_function == "run_s7"
    assert REGISTRY_BY_ID["S7"].report_filename == "s7_strategy_horizon_interaction.json"
    assert "S7" in discover_runners()
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert set(WAVE_A_NO_RUNNER_DESIGNS) >= {"S5", "S6", "S7"}
    assert HUMAN_SEMANTIC_DECISIONS["HD06"].implementation_blocked_until_decision is False
    assert len(REGISTRY) == len(set(question.id for question in REGISTRY)) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    definitions = build_definitions_from_registry(REGISTRY)
    assert all(definition.definition_version == 1 for definition in definitions.values())
