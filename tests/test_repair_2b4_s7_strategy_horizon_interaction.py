"""Focused Repair 2B.4 S7 full-factorial interaction contracts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from core.horizon.horizon_models import TradeHorizon
from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
from research_engine.control_plane.report_ownership import canonical_report_owner, resolve_report_ownership
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.horizon_expectancy import run_s6
from research_engine.experiments.strategy_expectancy import ACTIVE_FAMILIES, LEGACY_TAXONOMY
from research_engine.experiments.strategy_horizon_interaction import (
    ALPHA,
    MIN_CELL_OPPORTUNITIES,
    MIN_OVERALL_OPPORTUNITIES,
    REPORT_FILENAME,
    _cell_counts,
    _chi_square_survival,
    _holm_adjust,
    _select_maximal_grid,
    run_s7,
)
from research_engine.experiments.strategy_identity_expectancy import CANONICAL_HORIZONS, run_s5
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
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


HORIZONS = tuple(horizon.value for horizon in TradeHorizon)


def _row(opportunity: str, family: str, horizon: str, value: float, **identity_changes) -> dict:
    shadow_id = "nshadow_" + hashlib.sha256(
        f"{opportunity}|{family}|{horizon}".encode()
    ).hexdigest()[:16]
    identity = {
        "trade_id": shadow_id,
        "shadow_trade_id": shadow_id,
        "canonical_opportunity_id": opportunity,
        "entity_id": f"entity-{opportunity}",
        "symbol": "EURUSD",
        "strategy_id": family,
        "shadow_type": "PRIMARY_HORIZON_SIMULATION" if horizon == HORIZONS[0] else "HORIZON_ALTERNATIVE",
        "evaluated_horizon": horizon,
        "trade_horizon": horizon,
    }
    identity.update(identity_changes)
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "source_schema_version": "shadow_runtime_v1",
        "identity": identity,
        "decision_snapshot": {
            "pattern": "HAMMER", "strategy": family,
            "trade_horizon": horizon, "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": value},
    }


def _balanced(per_family: int = 30, *, interaction: bool = False) -> list[dict]:
    rows = []
    for family_index, family in enumerate(ACTIVE_FAMILIES):
        for index in range(per_family):
            opportunity = f"{family}-{index}"
            for horizon_index, horizon in enumerate(HORIZONS):
                noise = (((index * 7 + horizon_index * 3) % 11) - 5) * 0.03
                interaction_effect = (
                    1.2 if interaction and family_index == 1 and horizon_index == 2 else 0.0
                )
                value = 0.1 * family_index + 0.2 * horizon_index + noise + interaction_effect
                rows.append(_row(opportunity, family, horizon, value))
    return rows


def _write_dependencies(path, rows):
    (path / "s5_strategy_identity_expectancy.json").write_text(
        json.dumps(run_s5(rows)), encoding="utf-8"
    )
    (path / "s6_horizon_expectancy.json").write_text(
        json.dumps(run_s6(rows)), encoding="utf-8"
    )


def test_taxonomies_and_hd07_contract_are_exact():
    assert ACTIVE_FAMILIES == tuple(family.value for family in StrategyFamily if family != StrategyFamily.NONE)
    assert StrategyFamily.NONE.value not in ACTIVE_FAMILIES
    assert HORIZONS == CANONICAL_HORIZONS == ("SCALP", "INTRADAY", "EXTENDED")
    assert LEGACY_TAXONOMY and S7_HD07_ADJUDICATED_CONTRACT["historical_strategy_mapping_inferred"] is False
    decision = HUMAN_SEMANTIC_DECISIONS["HD07"]
    assert decision.implementation_blocked_until_decision is False
    assert decision.recommended_default.startswith("ADJUDICATED:")


def test_current_evidence_filters_stale_unknown_none_and_legacy_taxonomy():
    rows = _balanced()
    baseline = run_s7(rows)
    stale = deepcopy(rows[0])
    stale["schema_version"] = "shadow_trades_v0"
    bad = [
        _row("none", "NONE", HORIZONS[0], 99),
        _row("legacy", LEGACY_TAXONOMY[0], HORIZONS[0], 99),
        _row("horizon", ACTIVE_FAMILIES[0], "UNKNOWN", 99),
    ]
    report = run_s7(rows + [stale] + bad)
    base_component = baseline["fingerprint"]["evidence_provenance"]["components"][0]
    component = report["fingerprint"]["evidence_provenance"]["components"][0]
    assert component["digest"] == base_component["digest"]
    assert component["schema"] == "shadow_trades_v1"
    assert report["overall"]["exclusions"]["strategy_family_none"] == 1
    # Historical/unknown identities fail the authoritative schema gate before
    # S7 selection and therefore appear in incompatible provenance accounting.
    assert (
        component["epoch_counts"]["TRANSITIONAL"]
        + component["epoch_counts"]["LEGACY"]
        + component["epoch_counts"]["INCOMPATIBLE"]
    ) == 3


def test_repeated_rows_are_retained_clustered_and_weight_one():
    report = run_s7(_balanced())
    population = report["overall"]["population"]
    assert population == {
        "total_distinct_eligible_opportunities": 180,
        "analytical_distinct_opportunities": 180,
        "analytical_repeated_row_count": 540,
    }
    weights = report["overall"]["estimator"]["opportunity_total_weights"]
    assert len(weights) == 180
    assert all(math.isclose(value, 1.0) for value in weights.values())


def test_duplicate_horizon_and_account_fanout_cannot_inflate_opportunities():
    rows = _balanced()
    duplicate = deepcopy(rows[0])
    duplicate["identity"]["shadow_trade_id"] = "nshadow_aaaaaaaaaaaaaaaa"
    duplicate["identity"]["trade_id"] = "nshadow_aaaaaaaaaaaaaaaa"
    duplicate["account_id"] = "second-account"
    report = run_s7(rows + [duplicate])
    assert report["overall"]["population"]["total_distinct_eligible_opportunities"] == 179
    assert report["overall"]["exclusions"]["duplicate_or_conflicting_horizon_simulation"] == 4


def test_full_grid_and_deterministic_bounded_grid_selection():
    full_counts = {(family, horizon): 30 for family in ACTIVE_FAMILIES for horizon in HORIZONS}
    assert _select_maximal_grid(full_counts) == (ACTIVE_FAMILIES, HORIZONS)
    bounded = dict(full_counts)
    for family in ACTIVE_FAMILIES:
        bounded[(family, HORIZONS[2])] = 29
    assert _select_maximal_grid(bounded) == (ACTIVE_FAMILIES, (HORIZONS[0], HORIZONS[1]))


def test_grid_tie_uses_canonical_order_and_outcomes_cannot_affect_it():
    counts = {(family, horizon): 0 for family in ACTIVE_FAMILIES for horizon in HORIZONS}
    for family in ACTIVE_FAMILIES[:2]:
        for horizon in HORIZONS[:2]:
            counts[(family, horizon)] = 30
    for family in ACTIVE_FAMILIES[2:4]:
        for horizon in HORIZONS[1:]:
            counts[(family, horizon)] = 30
    assert _select_maximal_grid(counts) == (ACTIVE_FAMILIES[:2], HORIZONS[:2])
    rows = _balanced()
    changed = deepcopy(rows)
    for row in changed:
        row["simulated_outcome"]["pnl_r_multiple"] *= -37
    assert run_s7(rows)["overall"]["selected_grid"] == run_s7(changed)["overall"]["selected_grid"]


def test_no_valid_two_by_two_and_below_150_are_insufficient():
    counts = {(family, horizon): 0 for family in ACTIVE_FAMILIES for horizon in HORIZONS}
    for index, family in enumerate(ACTIVE_FAMILIES):
        counts[(family, HORIZONS[index % 3])] = 30
    assert _select_maximal_grid(counts) is None
    report = run_s7(_balanced(24))
    assert report["overall"]["population"]["total_distinct_eligible_opportunities"] == 144
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["classification"] == "INSUFFICIENT_EVIDENCE"


def test_additive_data_completes_as_no_reliable_interaction():
    report = run_s7(_balanced())
    omnibus = report["overall"]["omnibus_test"]
    assert report["status"] == "COMPLETE"
    assert report["overall"]["classification"] == "NO_RELIABLE_INTERACTION"
    assert omnibus["degrees_of_freedom"] == 10
    assert omnibus["alpha"] == ALPHA
    assert omnibus["p_value"] > ALPHA
    assert omnibus["reliable_interaction"] is False
    assert all(not item["inferential_claim_permitted"] for item in report["overall"]["followup_contrasts"]["results"])


def test_genuine_interaction_rejects_jointly_and_enables_holm_claims():
    report = run_s7(_balanced(interaction=True))
    omnibus = report["overall"]["omnibus_test"]
    followups = report["overall"]["followup_contrasts"]
    assert report["status"] == "COMPLETE"
    assert report["overall"]["classification"] == "RELIABLE_INTERACTION"
    assert omnibus["p_value"] <= ALPHA
    assert omnibus["reliable_interaction"] is True
    assert followups["declared_count"] == 18
    assert len(followups["results"]) == 18
    assert all(item["holm_adjusted_p_value"] is not None for item in followups["results"])
    assert any(item["inferential_claim_permitted"] for item in followups["results"])


def test_bounded_two_by_two_grid_has_one_df_and_two_followups():
    rows = []
    for family_index, family in enumerate(ACTIVE_FAMILIES[:2]):
        for index in range(30):
            opportunity = f"bounded-{family}-{index}"
            for horizon_index, horizon in enumerate(HORIZONS[:2]):
                noise = (((index * 5 + horizon_index * 2) % 9) - 4) * 0.04
                interaction = 0.8 if family_index == 1 and horizon_index == 1 else 0.0
                rows.append(_row(opportunity, family, horizon, noise + interaction))
    for family_index, family in enumerate(ACTIVE_FAMILIES[2:]):
        for index in range(23):
            rows.append(_row(
                f"sparse-{family}-{index}", family,
                HORIZONS[(family_index + index) % len(HORIZONS)], 0.01 * index,
            ))
    report = run_s7(rows)
    assert report["overall"]["population"]["total_distinct_eligible_opportunities"] == 152
    assert report["overall"]["selected_grid"]["dimensions"] == [2, 2]
    assert report["overall"]["omnibus_test"]["degrees_of_freedom"] == 1
    assert report["overall"]["followup_contrasts"]["declared_count"] == 2
    assert len(report["overall"]["followup_contrasts"]["results"]) == 2
    assert report["fingerprint"]["records_used"] == 120
    assert report["overall"]["cells"]["excluded_cell_reasons"]


def test_chi_square_survival_calculates_known_joint_test_probability():
    # Chi-square with two df has survival exp(-x/2).
    assert math.isclose(_chi_square_survival(4.0, 2), math.exp(-2.0), rel_tol=1e-12)


def test_interaction_result_and_provenance_are_order_invariant_and_change_sensitive():
    rows = _balanced(interaction=True)
    first = run_s7(rows)
    reordered = run_s7(list(reversed(deepcopy(rows))))
    assert first["overall"]["omnibus_test"] == reordered["overall"]["omnibus_test"]
    assert first["fingerprint"]["evidence_provenance"]["digest"] == reordered["fingerprint"]["evidence_provenance"]["digest"]
    changed = deepcopy(rows)
    changed[0]["simulated_outcome"]["pnl_r_multiple"] += 0.5
    changed_report = run_s7(changed)
    assert changed_report["fingerprint"]["evidence_provenance"]["digest"] != first["fingerprint"]["evidence_provenance"]["digest"]


def test_followups_are_only_within_strategy_horizon_pairs_and_bounded():
    report = run_s7(_balanced(interaction=True))
    results = report["overall"]["followup_contrasts"]["results"]
    assert {(item["left_horizon"], item["right_horizon"]) for item in results} == {
        (HORIZONS[0], HORIZONS[1]), (HORIZONS[0], HORIZONS[2]),
        (HORIZONS[1], HORIZONS[2]),
    }
    assert {item["strategy_family"] for item in results} == set(ACTIVE_FAMILIES)
    assert report["overall"]["selected_grid"]["claim_boundary"].startswith("claims apply only")
    estimator = report["overall"]["estimator"]
    assert estimator["naive_row_independent_covariance_used"] is False
    assert "within canonical opportunity" in estimator["cluster_score_aggregation"]


def test_holm_known_values_monotonic_ties_and_one_global_family():
    raw = [0.01, 0.04, 0.03, 0.01]
    adjusted = _holm_adjust(raw)
    assert adjusted == [0.04, 0.06, 0.06, 0.04]
    ordered = sorted(zip(raw, adjusted))
    assert [item[1] for item in ordered] == sorted(item[1] for item in ordered)
    report = run_s7(_balanced(interaction=True))
    family = report["overall"]["followup_contrasts"]
    assert family["multiplicity"] == "one global Holm step-down family"
    assert family["omnibus_in_holm_family"] is False


def test_report_owns_exact_selected_grid_provenance():
    rows = _balanced()
    report = run_s7(rows)
    component = report["fingerprint"]["evidence_provenance"]["components"][0]
    assert component["selection"] == "CURRENT_SUBSET"
    assert component["records_used"] == 540
    assert component["digest_algorithm"] == "sha256"
    assert report["overall"]["exclusions"]["outside_selected_grid"] == 0


def test_s7_is_sole_owner_and_relabelled_report_is_rejected():
    assert canonical_report_owner(REPORT_FILENAME) == "S7"
    assert resolve_report_ownership(REPORT_FILENAME, "S7").allowed is True
    for other in ("S5", "S6", "E3", "Q24"):
        assert resolve_report_ownership(REPORT_FILENAME, other).allowed is False
    report = run_s7(_balanced())
    validity, _ = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="S7")
    assert validity == ReportValidity.VALID_CURRENT
    report["question_id"] = "S6"
    validity, _ = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="S7")
    assert validity == ReportValidity.INVALIDATED


def test_readiness_insufficient_and_both_complete_results(tmp_path):
    rows = _balanced()
    insufficient_rows = deepcopy(rows)
    # Preserve S5/S6's >=20 cells and >=30/family gates, but leave no S7-valid
    # 2x2 grid because only one horizon per family reaches S7's cell threshold.
    insufficient_rows = [
        row for row in insufficient_rows
        if HORIZONS.index(row["identity"]["evaluated_horizon"])
        == ACTIVE_FAMILIES.index(row["identity"]["strategy_id"]) % len(HORIZONS)
        or int(row["identity"]["canonical_opportunity_id"].rsplit("-", 1)[1]) < 20
    ]
    _write_dependencies(tmp_path, insufficient_rows)
    insufficient = run_s7(insufficient_rows)
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(insufficient), encoding="utf-8")
    state = build_question_state(
        "S7", reports_dir=tmp_path, evidence_source={"shadow_trades": insufficient_rows}
    )
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.readiness_status == ReadinessStatus.WAITING_DATA
    assert state.latest_finding == ""
    _write_dependencies(tmp_path, rows)
    for interaction, classification in ((False, "NO_RELIABLE_INTERACTION"), (True, "RELIABLE_INTERACTION")):
        report = run_s7(_balanced(interaction=interaction))
        (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
        state = build_question_state("S7", reports_dir=tmp_path, evidence_source={"shadow_trades": rows})
        assert report["overall"]["classification"] == classification
        assert state.readiness_status == ReadinessStatus.COMPLETE


def test_missing_report_with_missing_evidence_uses_existing_blocked_semantics(tmp_path):
    state = build_question_state("S7", reports_dir=tmp_path, evidence_source={})
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.MISSING
    assert state.readiness_status == ReadinessStatus.BLOCKED


def test_registry_runner_ownership_and_structural_completion_are_current():
    question = REGISTRY_BY_ID["S7"]
    assert question.runner_module == "research_engine.experiments.strategy_horizon_interaction"
    assert question.runner_function == "run_s7"
    assert question.report_filename == REPORT_FILENAME
    assert "S7" in discover_runners()
    assert "S7" in STRUCTURALLY_OPERATIONAL_IDS
    assert "S7" not in WAVE_A_NO_RUNNER_TARGETS
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert operational_baseline() == (45, 25)


def test_registry_baseline_versions_and_s5_s6_contracts_remain_unchanged():
    assert len(REGISTRY) == len({question.id for question in REGISTRY}) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    assert {definition.definition_version for definition in build_definitions_from_registry(REGISTRY).values()} == {1}
    assert REGISTRY_BY_ID["S5"].report_filename == "s5_strategy_identity_expectancy.json"
    assert REGISTRY_BY_ID["S6"].report_filename == "s6_horizon_expectancy.json"
    assert WAVE_A_NO_RUNNER_DESIGNS["S7"].report_identity == REPORT_FILENAME
