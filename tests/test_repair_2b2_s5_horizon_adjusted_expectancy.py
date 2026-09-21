"""Focused Repair 2B.2 S5 horizon-adjusted strategy-family expectancy contracts.

S5 asks which CURRENT V10 StrategyFamily values retain expectancy AFTER
accounting for the evaluated trade horizon (SCALP / INTRADAY / EXTENDED).
It is deliberately distinct from E3 (ordinary PRIMARY-only per-family
expectancy) and from S6/S7 (horizon effect / interaction).

The cluster-aware contracts below were frozen by the adjudicated HD06 design:
one canonical opportunity is one statistical cluster, horizon simulations are
repeated measurements, and sufficiency requires 100 distinct opportunities
overall / 30 per StrategyFamily / 20 in every required family-horizon cell.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from copy import deepcopy

import pytest

from core.horizon.horizon_models import TradeHorizon
from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.evidence_provenance import (
    attest_current_subset,
    select_current_evidence,
)
from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.strategy_expectancy import (
    ACTIVE_FAMILIES,
    ACTIVE_FAMILY_SET,
    LEGACY_TAXONOMY,
    REPORT_FILENAME as E3_REPORT_FILENAME,
    run_e3,
)
from research_engine.experiments.strategy_identity_expectancy import (
    CANONICAL_HORIZONS,
    HORIZON_SIMULATION_TYPES,
    MIN_CELL_OPPORTUNITIES,
    MIN_FAMILY_OPPORTUNITIES,
    MIN_OVERALL_OPPORTUNITIES,
    REPORT_FILENAME,
    REQUIRED_CELL_COUNT,
    S5_SUFFICIENCY_CONTRACT,
    Z_95,
    _cluster_opportunities,
    _select_horizon_rows,
    run_s5,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    HUMAN_SEMANTIC_DECISIONS,
    STRUCTURALLY_OPERATIONAL_IDS,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a5_definitions import WAVE_A5_OWNERSHIP
from research_engine.runner_discovery import discover_runners

HORIZONS = ("SCALP", "INTRADAY", "EXTENDED")
HORIZON_EFFECT = {"SCALP": 1.0, "INTRADAY": 0.0, "EXTENDED": -1.0}
FAMILY_EFFECT = {
    "LIQUIDITY_SWEEP_REVERSAL": 0.5,
    "FALSE_BREAK": -0.5,
    "TREND_CONTINUATION": 0.25,
    "BREAKOUT_EXPANSION": 0.0,
    "MEAN_REVERSION": -0.25,
    "RANGE_REACTION": 0.05,
}


def _shadow_id(opportunity: str, family: str, horizon: str) -> str:
    """Deterministic canonical nshadow_* identity (never process-hash dependent)."""
    digest = hashlib.sha256(f"{opportunity}|{family}|{horizon}".encode("utf-8")).hexdigest()
    return f"nshadow_{digest[:16]}"


def _row(
    opportunity: str,
    family: str,
    horizon: str,
    r_value: float,
    *,
    shadow_type: str | None = None,
) -> dict:
    """One completed horizon-simulation row in canonical shadow_trades_v1 shape."""
    if shadow_type is None:
        shadow_type = (
            "PRIMARY_HORIZON_SIMULATION" if horizon == "SCALP" else "HORIZON_ALTERNATIVE"
        )
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
            "shadow_type": shadow_type,
            "evaluated_horizon": horizon,
            "trade_horizon": horizon,
        },
        "decision_snapshot": {
            "pattern": "HAMMER",
            "strategy": family,
            "trade_horizon": horizon,
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": r_value},
    }


def _balanced_population(per_family: int = 60) -> list[dict]:
    """Every required 6x3 cell is supported by ``per_family`` distinct opportunities.

    Each opportunity contributes one row per horizon, carries an identical
    opportunity-level shock across its horizons (perfect within-cluster
    correlation), and the family/horizon effects are additive.  The
    opportunity-clustered adjusted family effect therefore recovers the family
    effect exactly while a row-level calculation cannot.
    """
    rows: list[dict] = []
    for family in ACTIVE_FAMILIES:
        for index in range(per_family):
            opportunity = f"{family}-{index}"
            shock = 0.3 if index % 2 == 0 else -0.3
            for horizon in HORIZONS:
                value = FAMILY_EFFECT[family] + HORIZON_EFFECT[horizon] + shock
                rows.append(_row(opportunity, family, horizon, round(value, 6)))
    return rows



# ─────────────────────────────────────────────────────────────────────────────
# A. TAXONOMY
# ─────────────────────────────────────────────────────────────────────────────


def test_a_six_v10_families_are_the_only_accepted_strategy_identity():
    assert ACTIVE_FAMILIES == (
        "LIQUIDITY_SWEEP_REVERSAL",
        "FALSE_BREAK",
        "TREND_CONTINUATION",
        "BREAKOUT_EXPANSION",
        "MEAN_REVERSION",
        "RANGE_REACTION",
    )
    assert ACTIVE_FAMILY_SET == frozenset(ACTIVE_FAMILIES)
    assert StrategyFamily.NONE.value == "NONE"
    assert StrategyFamily.NONE.value not in ACTIVE_FAMILY_SET
    assert len(ACTIVE_FAMILIES) == 6
    report = run_s5([])
    taxonomy = report["overall"]["taxonomy"]
    assert taxonomy["authority"] == "core.v10.strategy_family.StrategyFamily"
    assert taxonomy["active_families"] == list(ACTIVE_FAMILIES)
    assert taxonomy["excluded_value"] == "NONE"
    assert taxonomy["legacy_mapping_inferred"] is False
    assert set(report["overall"]["families"]) == set(ACTIVE_FAMILIES)


def test_a_none_and_historical_families_are_excluded_and_never_mapped():
    eligible, exclusions = _select_horizon_rows([
        _row("none-1", "NONE", "SCALP", 1.0),
        _row("legacy-1", "REVERSAL", "SCALP", 1.0),
        _row("legacy-2", "CONTINUATION", "SCALP", 1.0),
        _row("legacy-3", "FALSE_BREAK_HIST", "SCALP", 1.0),
        _row("ok-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ])
    assert [record["identity"]["canonical_opportunity_id"] for record in eligible] == ["ok-1"]
    assert exclusions["strategy_family_none"] == 1
    assert exclusions["unknown_or_missing_strategy_family"] == 3
    assert LEGACY_TAXONOMY == ("REVERSAL", "CONTINUATION", "FALSE_BREAK")

    report = run_s5([
        _row("legacy-1", "REVERSAL", "SCALP", 1.0),
        _row("legacy-2", "REVERSAL", "INTRADAY", 1.0),
    ])
    assert "REVERSAL" not in report["overall"]["families"]
    assert set(report["overall"]["families"]) == set(ACTIVE_FAMILIES)


def test_a_taxonomy_authority_is_shared_with_e3_and_not_redefined():
    from research_engine.registry.wave_a5_definitions import (
        E3_ACTIVE_V10_STRATEGY_FAMILIES,
        E3_EXCLUDED_STRATEGY_FAMILY,
        E3_LEGACY_COMPATIBILITY_TAXONOMY,
    )

    assert E3_ACTIVE_V10_STRATEGY_FAMILIES == ACTIVE_FAMILIES
    assert E3_EXCLUDED_STRATEGY_FAMILY == "NONE"
    assert E3_LEGACY_COMPATIBILITY_TAXONOMY == LEGACY_TAXONOMY


# ─────────────────────────────────────────────────────────────────────────────
# B. HORIZONS
# ─────────────────────────────────────────────────────────────────────────────


def test_b_canonical_horizons_come_from_the_authoritative_trade_horizon_enum():
    assert tuple(horizon.value for horizon in TradeHorizon) == HORIZONS
    assert CANONICAL_HORIZONS == HORIZONS
    report = run_s5([])
    horizon_taxonomy = report["overall"]["horizon_taxonomy"]
    assert horizon_taxonomy["authority"] == "core.horizon.horizon_models.TradeHorizon"
    assert horizon_taxonomy["canonical_horizons"] == list(HORIZONS)
    assert horizon_taxonomy["unsupported_horizons_bucketed"] is False


@pytest.mark.parametrize("horizon", HORIZONS)
def test_b_each_canonical_horizon_is_accepted(horizon):
    eligible, exclusions = _select_horizon_rows([
        _row(f"{horizon}-1", ACTIVE_FAMILIES[0], horizon, 1.0),
    ])
    assert len(eligible) == 1
    assert eligible[0]["identity"]["evaluated_horizon"] == horizon
    assert exclusions == {}


def test_b_unknown_horizon_is_excluded_by_count_and_never_bucketed():
    eligible, exclusions = _select_horizon_rows([
        _row("x-1", ACTIVE_FAMILIES[0], "WEEKLY", 1.0),
        _row("x-2", ACTIVE_FAMILIES[0], "", 1.0),
        _row("x-3", ACTIVE_FAMILIES[0], "SCALP_H1", 1.0),
        _row("ok-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ])
    assert [record["identity"]["canonical_opportunity_id"] for record in eligible] == ["ok-1"]
    assert exclusions["unknown_or_unsupported_horizon"] == 3
    assert sum(exclusions.values()) == 3


def test_b_only_completed_horizon_simulation_types_are_eligible():
    assert HORIZON_SIMULATION_TYPES == (
        "PRIMARY_HORIZON_SIMULATION",
        "HORIZON_ALTERNATIVE",
    )
    eligible, exclusions = _select_horizon_rows([
        _row("open-1", ACTIVE_FAMILIES[0], "SCALP", 1.0, shadow_type="OPEN"),
        _row("progress-1", ACTIVE_FAMILIES[0], "SCALP", 1.0, shadow_type="PROGRESS"),
        _row("close-1", ACTIVE_FAMILIES[0], "SCALP", 1.0, shadow_type="CLOSE"),
        _row("cand-1", ACTIVE_FAMILIES[0], "SCALP", 1.0, shadow_type="CANDIDATE_STRATEGY"),
        _row("primary-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
        _row("alt-1", ACTIVE_FAMILIES[1], "INTRADAY", 1.0, shadow_type="HORIZON_ALTERNATIVE"),
    ])
    assert sorted(
        record["identity"]["canonical_opportunity_id"] for record in eligible
    ) == ["alt-1", "primary-1"]
    assert exclusions["non_horizon_simulation_row"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# C. CURRENT EVIDENCE
# ─────────────────────────────────────────────────────────────────────────────


def test_c_normalized_shadow_trades_v1_is_the_authoritative_evidence_source():
    rows = [_row("c-1", ACTIVE_FAMILIES[0], "SCALP", 1.0)]
    selection = select_current_evidence("shadow_trades", rows)
    assert selection.component["source"] == "shadow_trades"
    assert selection.component["schema"] == "shadow_trades_v1"
    assert selection.component["selection"] == "CURRENT_ONLY"
    assert selection.component["records_used"] == 1
    assert selection.component["state"] == "CURRENT"


def test_c_stale_evidence_cannot_contribute_to_s5():
    rows = _balanced_population(per_family=30)
    stale = _row("stale-1", ACTIVE_FAMILIES[0], "SCALP", 100.0)
    stale["schema_version"] = "shadow_trades_v0"
    report = run_s5(rows + [stale])

    assert report["overall"]["exclusions"]["non_current_or_incompatible"] == 1
    assert report["fingerprint"]["records_used"] == len(rows)
    assert report["overall"]["families"][ACTIVE_FAMILIES[0]][
        "mean_r_rows"
    ] == pytest.approx(FAMILY_EFFECT[ACTIVE_FAMILIES[0]])


def test_c_legacy_shadow_datasets_cannot_satisfy_s5():
    from research_engine.control_plane.evidence_resolver import (
        canonical_evidence_source,
    )

    # A legacy/historical shadow dataset identity is not a resolvable canonical
    # evidence source at all.
    assert canonical_evidence_source("shadow_trades_2026-07-27") is None
    assert canonical_evidence_source("logs/shadow_trades") is None

    # A record carrying a legacy research-shadow schema cannot become CURRENT
    # shadow_trades_v1 evidence for S5.
    legacy = _row("legacy-1", ACTIVE_FAMILIES[0], "SCALP", 1.0)
    legacy["schema_version"] = "research_shadow_trades_v1"
    selection = select_current_evidence("shadow_trades", [legacy])
    assert selection.component["records_used"] == 0
    assert selection.component["state"] == "UNVERIFIED"

    report = run_s5([legacy])
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["distinct_canonical_opportunities"] == 0
    assert report["overall"]["exclusions"]["non_current_or_incompatible"] == 1


def test_c_s5_uses_horizon_rows_where_e3_uses_only_the_primary_row():
    rows = _balanced_population(per_family=30)
    s5_report = run_s5(rows)
    e3_report = run_e3(rows)

    # E3 keeps exactly one PRIMARY_HORIZON_SIMULATION per opportunity.
    assert e3_report["dataset"]["sample_size"] == 180
    # S5 legitimately retains all three governed horizon simulations.
    assert s5_report["overall"]["analysed_row_count"] == 540
    assert s5_report["overall"]["distinct_canonical_opportunities"] == 180
    assert s5_report["fingerprint"]["records_used"] == 540


def test_c_unreadable_or_non_finite_outcome_rows_are_excluded_by_count():
    eligible, exclusions = _select_horizon_rows([
        _row("r-1", ACTIVE_FAMILIES[0], "SCALP", float("nan")),
        _row("r-2", ACTIVE_FAMILIES[0], "SCALP", float("inf")),
        _row("r-3", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ])
    assert [record["identity"]["canonical_opportunity_id"] for record in eligible] == ["r-3"]
    assert exclusions["missing_or_non_finite_outcome"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# D. CLUSTERING — canonical_opportunity_id is the statistical cluster
# ─────────────────────────────────────────────────────────────────────────────


def test_d_canonical_opportunity_id_is_the_cluster_not_the_horizon_row():
    report = run_s5(_balanced_population(per_family=30))
    overall = report["overall"]

    assert overall["unit_of_analysis"] == (
        "one canonical_opportunity_id cluster; horizon simulations are repeated "
        "measurements and never independent opportunities"
    )
    # 6 families x 30 opportunities = 180 distinct opportunities ...
    assert overall["distinct_canonical_opportunities"] == 180
    # ... even though 540 horizon rows were analysed.
    assert overall["analysed_row_count"] == 540
    assert report["dataset"]["sample_size"] == 180
    assert report["dataset"]["analysed_row_count"] == 540
    for family in ACTIVE_FAMILIES:
        result = overall["families"][family]
        assert result["n_distinct_canonical_opportunities"] == 30
        assert result["row_count"] == 90
        for horizon in HORIZONS:
            assert result["horizon_coverage"][horizon][
                "n_distinct_canonical_opportunities"
            ] == 30


def test_d_cluster_helper_returns_one_cluster_per_opportunity():
    rows = _balanced_population(per_family=30)
    clusters, exclusions = _cluster_opportunities(rows)

    assert exclusions == {}
    assert len(clusters) == 180
    assert all(len(cluster["observations"]) == 3 for cluster in clusters)
    assert sorted(cluster["opportunity_id"] for cluster in clusters) == sorted(
        cluster["opportunity_id"] for cluster in clusters
    )
    first = next(cluster for cluster in clusters if cluster["opportunity_id"].endswith("-0"))
    assert first["family"] in ACTIVE_FAMILY_SET
    assert sorted(horizon for horizon, _ in first["observations"]) == sorted(HORIZONS)


def test_d_repeated_rows_cannot_inflate_the_distinct_opportunity_gates():
    # 34 distinct opportunities x 3 horizon rows = 102 rows (>=100 rows) but
    # only 34 distinct opportunities, so the overall gate must still fail.
    rows = [
        _row(f"{ACTIVE_FAMILIES[0]}-{index}", ACTIVE_FAMILIES[0], horizon, 0.5)
        for index in range(34)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["analysed_row_count"] == 102
    assert overall["distinct_canonical_opportunities"] == 34
    assert overall["sufficiency"]["overall_sufficient"] is False
    assert report["status"] == "INSUFFICIENT_DATA"


def test_d_duplicate_simulations_for_one_opportunity_fail_closed():
    rows = [
        _row("dup-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
        _row("dup-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
        _row("ok-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ]
    clusters, exclusions = _cluster_opportunities(rows)
    assert [cluster["opportunity_id"] for cluster in clusters] == ["ok-1"]
    assert exclusions["duplicate_or_conflicting_horizon_simulation"] == 2

    report = run_s5(rows)
    assert report["overall"]["exclusions"][
        "duplicate_or_conflicting_horizon_simulation"
    ] == 2
    assert report["overall"]["families"][ACTIVE_FAMILIES[0]][
        "n_distinct_canonical_opportunities"
    ] == 1


def test_d_uncertainty_respects_clustering_and_is_not_row_level():
    rows = _balanced_population(per_family=60)
    report = run_s5(rows)
    result = report["overall"]["families"][ACTIVE_FAMILIES[0]]
    adjusted = result["adjusted_effect"]

    # Every opportunity shares one +-0.3 shock across its three horizons, so the
    # opportunity-clustered standard error is exactly 0.3/sqrt(60 clusters).
    assert adjusted["standard_error"] == pytest.approx(0.3 / math.sqrt(60), abs=1e-6)
    assert adjusted["estimate_r"] == pytest.approx(
        FAMILY_EFFECT[ACTIVE_FAMILIES[0]], abs=1e-6
    )
    assert adjusted["interval_95"]["method"] == "opportunity_clustered_sandwich"
    assert adjusted["interval_95"]["cluster"] == "canonical_opportunity_id"
    assert adjusted["interval_95"]["lower_r"] == pytest.approx(
        adjusted["estimate_r"] - Z_95 * adjusted["standard_error"], abs=1e-6
    )
    assert adjusted["interval_95"]["upper_r"] == pytest.approx(
        adjusted["estimate_r"] + Z_95 * adjusted["standard_error"], abs=1e-6
    )

    # A pseudo-replicating row-level interval would have used 180 rows instead of
    # 60 clusters; the reported error must not equal that value.
    family_rows = [
        row["simulated_outcome"]["pnl_r_multiple"]
        for row in rows
        if row["identity"]["strategy_id"] == ACTIVE_FAMILIES[0]
    ]
    row_level_error = statistics.stdev(family_rows) / math.sqrt(len(family_rows))
    assert adjusted["standard_error"] != pytest.approx(row_level_error, abs=1e-6)


def test_d_uncertainty_shrinks_with_cluster_count_not_row_count():
    small = run_s5(_balanced_population(per_family=30))
    large = run_s5(_balanced_population(per_family=120))
    small_error = small["overall"]["families"][ACTIVE_FAMILIES[0]]["adjusted_effect"][
        "standard_error"
    ]
    large_error = large["overall"]["families"][ACTIVE_FAMILIES[0]]["adjusted_effect"][
        "standard_error"
    ]

    # 4x the distinct opportunities halves the clustered standard error.
    assert small["overall"]["distinct_canonical_opportunities"] == 180
    assert large["overall"]["distinct_canonical_opportunities"] == 720
    assert small_error / large_error == pytest.approx(2.0, abs=1e-3)


# ─────────────────────────────────────────────────────────────────────────────
# E. STRATEGY CONSISTENCY WITHIN A CLUSTER
# ─────────────────────────────────────────────────────────────────────────────


def test_e_strategy_identity_comes_from_the_frozen_shadow_identity_field():
    report = run_s5(_balanced_population(per_family=30))
    for family in ACTIVE_FAMILIES:
        result = report["overall"]["families"][family]
        assert result["n_distinct_canonical_opportunities"] == 30
    assert report["overall"]["families"][ACTIVE_FAMILIES[0]][
        "mean_r_rows"
    ] == pytest.approx(FAMILY_EFFECT[ACTIVE_FAMILIES[0]], abs=1e-6)


def test_e_conflicting_strategy_identity_within_one_opportunity_fails_closed():
    rows = [
        _row("conflict-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
        _row("conflict-1", ACTIVE_FAMILIES[1], "INTRADAY", 1.0),
        _row("ok-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ]
    clusters, exclusions = _cluster_opportunities(rows)
    assert [cluster["opportunity_id"] for cluster in clusters] == ["ok-1"]
    assert exclusions["conflicting_strategy_identity_within_opportunity"] == 2

    report = run_s5(rows)
    assert report["overall"]["exclusions"][
        "conflicting_strategy_identity_within_opportunity"
    ] == 2
    assert report["overall"]["distinct_canonical_opportunities"] == 1
    assert report["overall"]["families"][ACTIVE_FAMILIES[1]][
        "n_distinct_canonical_opportunities"
    ] == 0


def test_e_strategy_is_never_inferred_from_other_fields():
    # A row whose only strategy-like signal lives outside identity.strategy_id
    # (decision_snapshot.strategy, pattern, regime, horizon) has no accepted
    # strategy identity and is excluded rather than inferred.
    inferable = _row("infer-1", ACTIVE_FAMILIES[0], "SCALP", 1.0)
    inferable["identity"]["strategy_id"] = ""
    inferable["decision_snapshot"]["strategy"] = ACTIVE_FAMILIES[0]
    inferable["decision_snapshot"]["pattern"] = ACTIVE_FAMILIES[1]
    eligible, exclusions = _select_horizon_rows([inferable])
    assert eligible == []
    assert exclusions["unknown_or_missing_strategy_family"] == 1

    report = run_s5([inferable])
    assert report["overall"]["distinct_canonical_opportunities"] == 0


def test_e_missing_canonical_opportunity_id_is_excluded_by_count():
    eligible, exclusions = _select_horizon_rows([
        _row("", ACTIVE_FAMILIES[0], "SCALP", 1.0),
        _row("ok-1", ACTIVE_FAMILIES[0], "SCALP", 1.0),
    ])
    assert [record["identity"]["canonical_opportunity_id"] for record in eligible] == ["ok-1"]
    assert exclusions["missing_canonical_opportunity_id"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# F. ESTIMATOR — descriptive metrics and the horizon-adjusted family effect
# ─────────────────────────────────────────────────────────────────────────────


def _unbalanced_horizon_population() -> list[dict]:
    """All 6x3 cells support >=20 opportunities, but SCALP carries extra rows.

    The raw row mean is dominated by the (strongly horizon-shifted) SCALP rows,
    while the adjusted family effect equal-weights horizons and must therefore
    differ.  With a family effect of exactly zero and SCALP/EXTENDED offsets of
    +3/-3, the raw row mean is +1.2 while the adjusted effect is 0.0.
    """
    sharp = {"SCALP": 3.0, "INTRADAY": 0.0, "EXTENDED": -3.0}
    rows: list[dict] = []
    for family in ACTIVE_FAMILIES:
        for index in range(20):
            opportunity = f"{family}-base-{index}"
            for horizon in HORIZONS:
                rows.append(_row(opportunity, family, horizon, sharp[horizon]))
        for index in range(40):
            opportunity = f"{family}-extra-{index}"
            rows.append(_row(opportunity, family, "SCALP", sharp["SCALP"]))
    return rows


def test_f_descriptive_metrics_are_row_level_and_exact():
    rows = _balanced_population(per_family=30)
    report = run_s5(rows)
    result = report["overall"]["families"][ACTIVE_FAMILIES[0]]

    values = [
        round(FAMILY_EFFECT[ACTIVE_FAMILIES[0]] + HORIZON_EFFECT[horizon] + shock, 6)
        for shock in (0.3, -0.3)
        for horizon in HORIZONS
    ]
    assert result["row_count"] == 90
    assert result["mean_r_rows"] == pytest.approx(statistics.mean(values), abs=1e-6)
    assert result["median_r_rows"] == pytest.approx(statistics.median(values), abs=1e-6)
    assert result["win_rate_rows"] == pytest.approx(
        sum(value > 0 for value in values) / len(values), abs=1e-6
    )
    assert result["mean_r_rows"] == pytest.approx(0.5, abs=1e-6)
    # The descriptive raw mean is a separately named field, never the effect.
    assert "mean_r_rows" in result
    assert "adjusted_effect" in result


def test_f_adjusted_effect_accounts_for_horizon_and_differs_from_the_raw_mean():
    rows = _unbalanced_horizon_population()
    report = run_s5(rows)
    overall = report["overall"]

    assert report["status"] == "COMPLETE"
    assert overall["cells"]["insufficient_cells"] == []
    for family in ACTIVE_FAMILIES:
        result = overall["families"][family]
        raw_mean = result["mean_r_rows"]
        adjusted = result["adjusted_effect"]["estimate_r"]
        # The SCALP-only extra opportunities pull the raw row mean up to +1.2,
        # while the horizon-adjusted effect equal-weights horizons and is 0.0.
        assert raw_mean == pytest.approx(1.2, abs=1e-6)
        assert adjusted == pytest.approx(0.0, abs=1e-6)
        assert abs(raw_mean - adjusted) > 1.0
        # A positive raw row mean must not masquerade as positive evidence.
        assert result["adjusted_effect"]["classification"] == "NON_POSITIVE_EVIDENCE"


def test_f_adjusted_effect_recovers_the_family_effect_under_balanced_horizons():
    report = run_s5(_balanced_population(per_family=60))
    for family in ACTIVE_FAMILIES:
        adjusted = report["overall"]["families"][family]["adjusted_effect"]
        assert adjusted["estimate_r"] == pytest.approx(FAMILY_EFFECT[family], abs=1e-6)
    horizon_effects = report["overall"]["estimator"]["horizon_effects_r_vs_scalp"]
    assert horizon_effects == {"SCALP": 0.0, "INTRADAY": -1.0, "EXTENDED": -2.0}
    assert "weighted least squares" in report["overall"]["estimator"]["model"]
    assert "StrategyFamily" in report["overall"]["estimator"]["model"]
    assert "evaluated_horizon" in report["overall"]["estimator"]["model"]
    assert report["overall"]["estimand"] == "HORIZON_ADJUSTED_STRATEGY_FAMILY_EFFECT"


def test_f_estimator_is_deterministic_under_row_reordering():
    rows = _balanced_population(per_family=30)
    first = run_s5(rows)
    reordered = run_s5(list(reversed(deepcopy(rows))))

    assert first["fingerprint"]["evidence_provenance"] == reordered["fingerprint"][
        "evidence_provenance"
    ]
    assert first["fingerprint"]["dataset_id"] == reordered["fingerprint"]["dataset_id"]
    assert first["overall"]["distinct_canonical_opportunities"] == reordered["overall"][
        "distinct_canonical_opportunities"
    ]
    for family in ACTIVE_FAMILIES:
        left = first["overall"]["families"][family]["adjusted_effect"]
        right = reordered["overall"]["families"][family]["adjusted_effect"]
        assert left == right


def test_f_positive_raw_mean_alone_cannot_force_positive_evidence():
    rows = [
        _row(f"{family}-{index}", family, horizon, 0.6 if index % 2 == 0 else -0.4)
        for family in ACTIVE_FAMILIES
        for index in range(60)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    for family in ACTIVE_FAMILIES:
        result = report["overall"]["families"][family]
        assert result["mean_r_rows"] == pytest.approx(0.1, abs=1e-6)
        assert result["mean_r_rows"] > 0
        assert result["adjusted_effect"]["classification"] == "NON_POSITIVE_EVIDENCE"
        assert result["adjusted_effect"]["interval_95"]["lower_r"] <= 0


# ─────────────────────────────────────────────────────────────────────────────
# G. SUFFICIENCY — the frozen HD06 100 / 30 / 20 distinct-opportunity gates
# ─────────────────────────────────────────────────────────────────────────────


def test_g_thresholds_and_cell_contract_are_the_frozen_hd06_contract():
    assert MIN_OVERALL_OPPORTUNITIES == 100
    assert MIN_FAMILY_OPPORTUNITIES == 30
    assert MIN_CELL_OPPORTUNITIES == 20
    assert S5_SUFFICIENCY_CONTRACT == {
        "minimum_distinct_canonical_opportunities_overall": 100,
        "minimum_distinct_canonical_opportunities_per_family": 30,
        "minimum_distinct_canonical_opportunities_per_family_horizon_cell": 20,
        "required_family_horizon_cells": 18,
        "structural_cell_exemptions": 0,
        "uncertainty": (
            "95% opportunity-clustered sandwich interval (mean +/- 1.96 cluster-robust SEs)"
        ),
        "below_minimum": "INSUFFICIENT_EVIDENCE",
    }
    assert REQUIRED_CELL_COUNT == 6 * 3
    report = run_s5([])
    cells = report["overall"]["cells"]
    assert cells["required_cells"] == 18
    assert len(cells["cell_n_distinct_canonical_opportunities"]) == 18
    assert len(cells["insufficient_cells"]) == 18


def test_g_fewer_than_100_distinct_overall_opportunities_is_insufficient():
    rows = [
        _row(f"{family}-{index}", family, horizon, 0.5)
        for family in ACTIVE_FAMILIES
        for index in range(16)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["distinct_canonical_opportunities"] == 96
    assert overall["analysed_row_count"] == 288
    assert overall["sufficiency"]["overall_sufficient"] is False
    assert report["status"] == "INSUFFICIENT_DATA"
    for family in ACTIVE_FAMILIES:
        assert overall["families"][family]["adjusted_effect"][
            "classification"
        ] == "INSUFFICIENT_EVIDENCE"


def test_g_fewer_than_30_distinct_opportunities_for_a_family_is_insufficient():
    rows = [
        _row(f"{family}-{index}", family, horizon, 0.5)
        for family in ACTIVE_FAMILIES
        for index in range(25)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["distinct_canonical_opportunities"] == 150
    assert overall["sufficiency"]["overall_sufficient"] is True
    assert overall["sufficiency"]["families_sufficient"] is False
    assert overall["sufficiency"]["cells_sufficient"] is True
    assert report["status"] == "INSUFFICIENT_DATA"
    for family in ACTIVE_FAMILIES:
        assert overall["families"][family][
            "n_distinct_canonical_opportunities"
        ] == 25
        assert overall["families"][family]["adjusted_effect"][
            "classification"
        ] == "INSUFFICIENT_EVIDENCE"


def test_g_fewer_than_20_distinct_opportunities_in_a_cell_is_insufficient():
    rows = [
        _row(f"{family}-{index}", family, horizon, 0.5)
        for family in ACTIVE_FAMILIES
        for index in range(30)
        for horizon in (HORIZONS[:2] if family == ACTIVE_FAMILIES[0] else HORIZONS)
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["sufficiency"]["overall_sufficient"] is True
    assert overall["sufficiency"]["families_sufficient"] is True
    assert overall["sufficiency"]["cells_sufficient"] is False
    assert overall["cells"]["insufficient_cells"] == [f"{ACTIVE_FAMILIES[0]}|EXTENDED"]
    assert overall["cells"]["cell_n_distinct_canonical_opportunities"][
        f"{ACTIVE_FAMILIES[0]}|EXTENDED"
    ] == 0
    assert report["status"] == "INSUFFICIENT_DATA"
    # A sparse family cannot be silently omitted in order to reach COMPLETE.
    assert overall["families"][ACTIVE_FAMILIES[0]]["adjusted_effect"][
        "classification"
    ] == "INSUFFICIENT_EVIDENCE"


def test_g_repeated_rows_cannot_satisfy_any_distinct_opportunity_gate():
    rows = [
        _row(f"{ACTIVE_FAMILIES[0]}-{index}", ACTIVE_FAMILIES[0], horizon, 0.5)
        for index in range(34)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["analysed_row_count"] == 102
    assert overall["distinct_canonical_opportunities"] == 34
    assert overall["sufficiency"]["overall_sufficient"] is False
    assert overall["sufficiency"]["families_sufficient"] is False
    assert report["status"] == "INSUFFICIENT_DATA"


def test_g_all_sufficient_families_and_cells_permit_classification_and_complete():
    report = run_s5(_balanced_population(per_family=60))
    overall = report["overall"]

    assert overall["sufficiency"]["overall_sufficient"] is True
    assert overall["sufficiency"]["families_sufficient"] is True
    assert overall["sufficiency"]["cells_sufficient"] is True
    assert overall["estimator"]["estimable"] is True
    assert report["status"] == "COMPLETE"
    assert report["recommendation"] == "HORIZON_ADJUSTED_STRATEGY_FAMILY_CLASSIFIED"
    classifications = {
        family: overall["families"][family]["adjusted_effect"]["classification"]
        for family in ACTIVE_FAMILIES
    }
    assert set(classifications.values()) <= {
        "POSITIVE_EVIDENCE", "NON_POSITIVE_EVIDENCE",
    }
    assert classifications["LIQUIDITY_SWEEP_REVERSAL"] == "POSITIVE_EVIDENCE"
    assert classifications["FALSE_BREAK"] == "NON_POSITIVE_EVIDENCE"
    assert classifications["TREND_CONTINUATION"] == "POSITIVE_EVIDENCE"


def test_g_missing_family_population_makes_the_model_non_estimable_and_insufficient():
    # Only three of the six canonical families are present, so the additive
    # family model cannot be estimated and every family stays insufficient.
    rows = [
        _row(f"{family}-{index}", family, horizon, 0.5)
        for family in ACTIVE_FAMILIES[:3]
        for index in range(60)
        for horizon in HORIZONS
    ]
    report = run_s5(rows)
    overall = report["overall"]

    assert overall["distinct_canonical_opportunities"] == 180
    assert overall["estimator"]["estimable"] is False
    assert report["status"] == "INSUFFICIENT_DATA"
    for family in ACTIVE_FAMILIES:
        assert overall["families"][family]["adjusted_effect"][
            "classification"
        ] == "INSUFFICIENT_EVIDENCE"
        assert overall["families"][family]["adjusted_effect"]["estimate_r"] is None



# ─────────────────────────────────────────────────────────────────────────────
# H. PROVENANCE — the exact analysed S5 row population is attested
# ─────────────────────────────────────────────────────────────────────────────


def test_h_provenance_attests_the_exact_eligible_s5_row_population():
    rows = _balanced_population(per_family=30)
    # One extra eligible row-family that S5 must exclude before the estimator.
    excluded = _row("excluded-1", "NONE", "SCALP", 1.0)
    report = run_s5(rows + [excluded])
    provenance = report["fingerprint"]["evidence_provenance"]

    assert provenance["version"] == "evidence_provenance_v1"
    assert provenance["state"] == "CURRENT"
    assert provenance["epoch"] == "CURRENT"
    assert provenance["records_used"] == 540
    assert provenance["input_records"] == 541
    assert provenance["records_excluded"] == 1
    assert provenance["digest_algorithm"] == "sha256"
    assert len(provenance["digest"]) == 64
    component = provenance["components"][0]
    assert component["source"] == "shadow_trades"
    assert component["schema"] == "shadow_trades_v1"
    assert component["selection"] == "CURRENT_SUBSET"
    # The analytical evidence is NOT deduplicated to one row per opportunity.
    assert component["records_used"] == 540
    assert component["used_epoch_counts"] == {
        "CURRENT": 540, "TRANSITIONAL": 0, "LEGACY": 0, "INCOMPATIBLE": 0,
    }
    assert report["fingerprint"]["records_used"] == 540
    assert report["fingerprint"]["records_excluded"] == 1


def test_h_stale_rows_never_enter_the_analytical_digest():
    rows = _balanced_population(per_family=30)
    stale = _row("stale-1", ACTIVE_FAMILIES[0], "SCALP", 42.0)
    stale["schema_version"] = "shadow_trades_v0"
    baseline = run_s5(rows)
    report = run_s5(rows + [stale])
    baseline_provenance = baseline["fingerprint"]["evidence_provenance"]
    provenance = report["fingerprint"]["evidence_provenance"]

    # The analytical component digest covers only the exact CURRENT rows used,
    # so a stale row can never enter it and the used population is unchanged.
    assert provenance["components"][0]["digest"] == baseline_provenance["components"][0][
        "digest"
    ]
    assert provenance["components"][0]["used_epoch_counts"] == baseline_provenance[
        "components"
    ][0]["used_epoch_counts"]
    assert provenance["components"][0]["records_used"] == 540
    assert provenance["records_used"] == baseline_provenance["records_used"] == 540
    assert provenance["records_excluded"] == 1
    assert report["fingerprint"]["records_used"] == 540
    # The combined provenance digest (and therefore the dataset identity)
    # additionally binds the input/exclusion accounting, so it changes only
    # through that accounting, never through the analysed outcome values.
    assert provenance["digest"] != baseline_provenance["digest"]
    assert report["fingerprint"]["dataset_id"] != baseline["fingerprint"]["dataset_id"]
    assert provenance["components"][0]["epoch_counts"]["INCOMPATIBLE"] == 1
    assert baseline_provenance["components"][0]["epoch_counts"]["INCOMPATIBLE"] == 0
    assert report["overall"]["exclusions"]["non_current_or_incompatible"] == 1


def test_h_digest_is_deterministic_and_changes_with_the_analytical_evidence():
    rows = _balanced_population(per_family=30)
    first = run_s5(rows)
    reordered = run_s5(list(reversed(deepcopy(rows))))

    assert first["fingerprint"]["evidence_provenance"]["digest"] == reordered[
        "fingerprint"
    ]["evidence_provenance"]["digest"]
    assert first["fingerprint"]["dataset_id"] == reordered["fingerprint"]["dataset_id"]

    changed_rows = deepcopy(rows)
    changed_rows[0]["simulated_outcome"]["pnl_r_multiple"] = 99.0
    changed = run_s5(changed_rows)
    assert changed["fingerprint"]["evidence_provenance"]["digest"] != first[
        "fingerprint"
    ]["evidence_provenance"]["digest"]
    assert changed["fingerprint"]["dataset_id"] != first["fingerprint"]["dataset_id"]

    dropped = run_s5(rows[1:])
    assert dropped["fingerprint"]["evidence_provenance"]["digest"] != first[
        "fingerprint"
    ]["evidence_provenance"]["digest"]


def test_h_invalid_analytical_subset_fails_closed():
    rows = _balanced_population(per_family=30)
    # A fabricated/stale row cannot be attested as part of the CURRENT subset.
    fabricated = _row("fabricated-1", ACTIVE_FAMILIES[0], "SCALP", 9.9)
    fabricated["schema_version"] = "shadow_trades_v0"
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, rows + [fabricated])

    # A row that was never supplied cannot be attested either.
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows[:10], rows[:11])

    # A legitimate CURRENT subset is attested and used exactly.
    subset = rows[:6]
    selection = attest_current_subset("shadow_trades", rows, subset)
    assert selection.component["records_used"] == 6
    assert selection.component["state"] == "CURRENT"
    assert len(selection.records_for_analysis()) == 6



# ─────────────────────────────────────────────────────────────────────────────
# I. REPORT OWNERSHIP — S5 solely owns its canonical report
# ─────────────────────────────────────────────────────────────────────────────


def test_i_s5_solely_owns_its_canonical_report():
    assert REPORT_FILENAME == "s5_strategy_identity_expectancy.json"
    assert canonical_report_owner(REPORT_FILENAME) == "S5"
    assert REGISTRY_BY_ID["S5"].report_filename == REPORT_FILENAME

    decision = resolve_report_ownership(REPORT_FILENAME, "S5")
    assert decision.allowed is True
    assert decision.kind == "OWNED_BY_CANONICAL_QUESTION"
    assert decision.canonical_owner == "S5"


@pytest.mark.parametrize("other", ["E3", "S1", "S6", "S7", "Q24"])
def test_i_no_other_question_can_own_the_s5_report(other):
    decision = resolve_report_ownership(REPORT_FILENAME, other)
    assert decision.allowed is False
    assert decision.kind == "OWNERSHIP_DENIED"
    assert decision.canonical_owner == "S5"
    assert "AMBIGUOUS_REPORT_MAPPING" in decision.reason


def test_i_resolver_cannot_accept_a_relabelled_or_incompatible_s5_report():
    report = run_s5(_balanced_population(per_family=60))
    assert report["question_id"] == "S5"

    validity, _ = resolve_report_validity(
        REPORT_FILENAME, report, expected_question_id="S5",
    )
    assert validity == ReportValidity.VALID_CURRENT

    # A relabelled report cannot be accepted as S5 (or for the labels it claims).
    for label, expected in (("S6", "S5"), ("S7", "S5"), ("E3", "S5")):
        relabelled = deepcopy(report)
        relabelled["question_id"] = label
        validity, _ = resolve_report_validity(
            REPORT_FILENAME, relabelled, expected_question_id=expected,
        )
        assert validity == ReportValidity.INVALIDATED

    # S6/S7 asking for the S5 artifact are denied outright.
    for other in ("S6", "S7"):
        validity, _ = resolve_report_validity(
            REPORT_FILENAME, report, expected_question_id=other,
        )
        assert validity == ReportValidity.MISSING


def test_i_s5_report_identity_is_independent_of_e3():
    assert REPORT_FILENAME != E3_REPORT_FILENAME
    assert canonical_report_owner(E3_REPORT_FILENAME) == "E3"
    e3_owners = {artifact for artifact, owner in WAVE_A5_OWNERSHIP[("E3", "S1")].canonical_owners if owner == "E3"}
    assert E3_REPORT_FILENAME in e3_owners
    assert REPORT_FILENAME not in e3_owners

    s5_report = run_s5(_balanced_population(per_family=60))
    e3_report = run_e3(_balanced_population(per_family=60))
    assert s5_report["question_id"] != e3_report["question_id"]
    assert s5_report["overall"]["estimand"] != e3_report["overall"].get("estimand", "")
    assert s5_report["overall"]["estimand"] == "HORIZON_ADJUSTED_STRATEGY_FAMILY_EFFECT"



# ─────────────────────────────────────────────────────────────────────────────
# J. READINESS — implementation status and scientific completion stay distinct
# ─────────────────────────────────────────────────────────────────────────────


def _insufficient_population() -> list[dict]:
    """Six families x 25 opportunities: the 30-per-family gate is not met."""
    return [
        _row(f"{family}-{index}", family, horizon, 0.5 if index % 2 == 0 else -0.3)
        for family in ACTIVE_FAMILIES
        for index in range(25)
        for horizon in HORIZONS
    ]


def test_j_s5_declares_a_resolvable_runner_and_becomes_structurally_operational():
    assert REGISTRY_BY_ID["S5"].runner_module == (
        "research_engine.experiments.strategy_identity_expectancy"
    )
    assert REGISTRY_BY_ID["S5"].runner_function == "run_s5"
    assert "S5" in discover_runners()
    assert "S5" in STRUCTURALLY_OPERATIONAL_IDS


def test_j_below_threshold_evidence_is_waiting_data_not_a_finding(tmp_path):
    rows = _insufficient_population()
    report = run_s5(rows)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["recommendation"] == "WAIT_FOR_EVIDENCE"
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")

    state = build_question_state(
        "S5", reports_dir=tmp_path, evidence_source={"shadow_trades": rows},
    )

    # The runner exists and the CURRENT report is valid, but the scientific
    # result is not complete: the question waits for evidence.
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.state_status == "WAITING_DATA"
    assert state.readiness_status == ReadinessStatus.WAITING_DATA
    assert "100/30/20" in state.readiness_reason
    # No scientific finding is surfaced.
    assert state.latest_finding == ""


def test_j_sufficient_evidence_reaches_scientific_completion(tmp_path):
    rows = _balanced_population(per_family=60)
    report = run_s5(rows)
    assert report["status"] == "COMPLETE"
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")

    state = build_question_state(
        "S5", reports_dir=tmp_path, evidence_source={"shadow_trades": rows},
    )

    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.state_status == "COMPLETE"
    assert state.readiness_status == ReadinessStatus.COMPLETE


def test_j_declared_runner_and_report_are_required_for_readiness(tmp_path):
    state = build_question_state("S5", reports_dir=tmp_path, evidence_source={})
    assert state.runner_status == RunnerStatus.READY
    assert state.report_validity == ReportValidity.MISSING
    assert state.state_status == "BLOCKED"



# ─────────────────────────────────────────────────────────────────────────────
# K. NON-TARGET SAFETY — S6/S7 untouched, 2B.1 ownership preserved
# ─────────────────────────────────────────────────────────────────────────────


def test_k_later_repairs_keep_s6_and_s7_distinct():
    s6 = REGISTRY_BY_ID["S6"]
    assert s6.runner_module == "research_engine.experiments.horizon_expectancy"
    assert s6.runner_function == "run_s6"
    assert s6.report_filename == "s6_horizon_expectancy.json"
    assert "S6" in STRUCTURALLY_OPERATIONAL_IDS

    s7 = REGISTRY_BY_ID["S7"]
    assert s7.runner_module == "research_engine.experiments.strategy_horizon_interaction"
    assert s7.runner_function == "run_s7"
    assert s7.report_filename == "s7_strategy_horizon_interaction.json"
    assert "S7" in STRUCTURALLY_OPERATIONAL_IDS
    runners = discover_runners()
    assert "S6" in runners
    assert "S7" in runners


def test_k_e3_and_s1_ownership_is_unchanged_from_repair_2b1():
    relationship = WAVE_A5_OWNERSHIP[("E3", "S1")]
    assert relationship.canonical_owners == (
        ("research_engine.experiments.strategy_expectancy.run_e3", "E3"),
        ("e3_strategy_family_expectancy.json", "E3"),
    )
    assert canonical_report_owner(E3_REPORT_FILENAME) == "E3"
    assert REGISTRY_BY_ID["S1"].runner_module == ""
    assert REGISTRY_BY_ID["S1"].runner_function == ""
    assert REGISTRY_BY_ID["S1"].report_filename == ""
    assert E3_REPORT_FILENAME not in {
        canonical_report_owner(REPORT_FILENAME),
    }
    assert canonical_report_owner(REPORT_FILENAME) == "S5"
    assert "E3" in STRUCTURALLY_OPERATIONAL_IDS
    assert "S1" in STRUCTURALLY_OPERATIONAL_IDS

    definitions = build_definitions_from_registry(REGISTRY)
    assert definitions["S1"].lifecycle_status.value == "SUPERSEDED"
    assert definitions["S1"].scientific_owner_id == "E3"


def test_k_hd06_remains_adjudicated_and_unreopened():
    hd06 = HUMAN_SEMANTIC_DECISIONS["HD06"]
    assert hd06.affected_question_ids == ("S5", "S6")
    assert hd06.implementation_blocked_until_decision is False
    assert hd06.recommended_default == (
        "ADJUDICATED: use the proposed cluster-aware contracts in the frozen "
        "no-runner design."
    )
    assert "repeated horizons without pseudo-replication" in hd06.consequences[0]


def test_k_registry_baseline_and_definition_versions_are_unchanged():
    assert len(REGISTRY) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    assert len(BASELINE_QUESTION_IDS) == 70

    definitions = build_definitions_from_registry(REGISTRY)
    assert len(definitions) == 70
    assert all(definition.definition_version == 1 for definition in definitions.values())
    assert definitions["S5"].definition_version == 1


def test_k_s5_remains_operational_after_later_s6_s7_repairs():
    # Repairs 2B.3/2B.4 add S6/S7 without altering S5.
    assert len(STRUCTURALLY_OPERATIONAL_IDS) == 45
    assert "S5" in STRUCTURALLY_OPERATIONAL_IDS
    assert "S6" in STRUCTURALLY_OPERATIONAL_IDS
    assert "E3" in STRUCTURALLY_OPERATIONAL_IDS
    assert "S1" in STRUCTURALLY_OPERATIONAL_IDS
    assert "S7" in STRUCTURALLY_OPERATIONAL_IDS

