from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from research_engine.control_plane.exit_bar_path import LifecyclePathSource
from research_engine.control_plane.exit_dimension_evidence import (
    PATTERN_SOURCE,
    REGIME_SOURCE,
    STRATEGY_SOURCE,
    build_exit_dimension_evidence_v1,
)
from research_engine.experiments import exit_policy_heterogeneity as hetero
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1


def _interaction_rows(levels=("A", "B"), n=30, changed=False):
    rows = []
    for level_index, level in enumerate(levels):
        for index in range(n):
            opportunity = f"{level}-{index}"
            for policy_index, policy in enumerate(CANDIDATE_POLICIES_V1):
                rows.append({
                    "candidate_policy_id": policy["policy_id"],
                    "dimension_level": level,
                    "lifecycle_identity": (f"s-{opportunity}", opportunity, "SCALP"),
                    "canonical_opportunity_id": opportunity,
                    "effect": 1.0 if changed and policy_index == 0 and level_index == 1 else 0.0,
                })
    return rows


def test_candidate_dimension_design_clustered_null_and_no_followups():
    result = hetero.fit_candidate_dimension_interaction(_interaction_rows(), ("A", "B"))
    assert len(result["cell_effects"]) == 18
    assert result["cluster_identity"] == "canonical_opportunity_id"
    assert result["omnibus"]["p_value"] == 1.0
    assert result["omnibus"]["reject_at_alpha_0_05"] is False
    assert result["followups"] == []


def test_omnibus_rejection_permits_one_global_holm_family():
    result = hetero.fit_candidate_dimension_interaction(
        _interaction_rows(changed=True), ("A", "B"),
    )
    assert result["omnibus"]["reject_at_alpha_0_05"] is True
    assert len(result["followups"]) == 9
    assert len(result["followup_family_order"]) == 9
    assert result["followups"][0]["holm_adjusted_p_value"] == 0.0


def test_account_fanout_duplicate_fails_closed():
    rows = _interaction_rows()
    rows.append(dict(rows[0]))
    with pytest.raises(hetero.HeterogeneityAnalysisError, match="fanout"):
        hetero.fit_candidate_dimension_interaction(rows, ("A", "B"))


def test_interaction_is_deterministic_under_input_reordering():
    rows = _interaction_rows(changed=True)
    assert hetero.fit_candidate_dimension_interaction(rows, ("A", "B")) == (
        hetero.fit_candidate_dimension_interaction(reversed(rows), ("A", "B"))
    )


def test_repeated_horizons_receive_opportunity_normalized_weight():
    rows = _interaction_rows(n=30)
    duplicate_horizon = dict(rows[0])
    duplicate_horizon["lifecycle_identity"] = ("second-shadow", rows[0]["canonical_opportunity_id"], "INTRADAY")
    duplicate_horizon["effect"] = 2.0
    rows.append(duplicate_horizon)
    result = hetero.fit_candidate_dimension_interaction(rows, ("A", "B"))
    first = result["cell_effects"][0]
    assert first["weighted_effect_estimate"] == pytest.approx(1.0 / 30.0)


def _populations(question_id="EX5", n=200, levels=("SCALP", "INTRADAY"), missing=False):
    baseline_records, candidate_records, dimension_records = [], [], []
    for index in range(n):
        identity = (f"s{index}", f"o{index}", levels[index % len(levels)] if question_id == "EX5" else "SCALP")
        baseline_records.append(SimpleNamespace(
            lifecycle_identity=identity,
            replay=SimpleNamespace(pnl_r_multiple=0.0),
        ))
        level = None if missing else levels[index % len(levels)]
        dimension_records.append(SimpleNamespace(
            lifecycle_identity=identity,
            canonical_opportunity_id=identity[1],
            trade_horizon=identity[2],
            strategy_family=level if question_id == "EX6" else "MEAN_REVERSION",
            market_regime=level if question_id == "EX7" else "TRENDING",
            candlestick_pattern=level if question_id == "EX8" else "HAMMER",
        ))
        for policy in CANDIDATE_POLICIES_V1:
            candidate_records.append(SimpleNamespace(
                lifecycle_identity=identity,
                canonical_opportunity_id=identity[1],
                candidate_policy_id=policy["policy_id"],
                candidate_r=0.0,
            ))
    path = SimpleNamespace(
        records=(), provenance={"digest": "path"},
        summary=SimpleNamespace(total_completed_lifecycles=n),
    )
    reproduction = SimpleNamespace(records=tuple(baseline_records), provenance={"digest": "base"})
    candidate = SimpleNamespace(
        records=tuple(candidate_records), provenance={"digest": "candidate"},
        summary=SimpleNamespace(candidate_eligible_lifecycles=n),
    )
    dimensions = SimpleNamespace(records=tuple(dimension_records), provenance={"digest": "dimensions"})
    return candidate, reproduction, path, dimensions


@pytest.fixture(autouse=True)
def _minimal_foundations(monkeypatch):
    monkeypatch.setattr(hetero, "_validate_foundations", lambda *args: None)
    monkeypatch.setattr(hetero, "validate_exit_dimension_evidence", lambda *args: None)


def test_thirty_opportunity_cell_and_two_level_gates_wait():
    report = hetero.analyse_ex5(*_populations(n=58))
    assert report["status"] == "WAITING_DATA"
    assert "SUFFICIENT_DIMENSION_LEVELS_BELOW_REQUIRED" in report["provenance"]["readiness"]["blockers"]


def test_valid_sufficient_heterogeneity_null_can_complete():
    report = hetero.analyse_ex5(*_populations())
    assert report["status"] == "COMPLETE"
    assert report["overall"]["omnibus_interaction_test"]["p_value"] == 1.0
    assert report["overall"]["followups"] == []


def test_ex5_canonical_horizon_counts_and_single_level_waiting():
    report = hetero.analyse_ex5(*_populations(levels=("SCALP",)))
    population = report["overall"]["population"]
    assert population["level_lifecycle_counts"]["SCALP"] == 200
    assert population["sufficient_levels"] == ["SCALP"]
    assert report["status"] == "WAITING_DATA"


def test_ex5_sufficient_multi_horizon_fixture_evaluates():
    report = hetero.analyse_ex5(*_populations())
    assert report["overall"]["population"]["sufficient_levels"] == ["SCALP", "INTRADAY"]
    assert report["status"] == "COMPLETE"


def test_ex6_uses_current_strategyfamily_and_excludes_none():
    candidate, reproduction, path, dimensions = _populations(
        "EX6", levels=("MEAN_REVERSION", "TREND_CONTINUATION"),
    )
    dimensions.records[0].strategy_family = "NONE"
    report = hetero.analyse_ex6(candidate, reproduction, path, dimensions)
    assert report["overall"]["population"]["dimension_source"] == STRATEGY_SOURCE
    assert report["overall"]["population"]["dimension_excluded_lifecycles"]["EXCLUDED_STRATEGY_FAMILY_NONE"] == 1


def test_ex6_legacy_taxonomy_cannot_substitute():
    candidate, reproduction, path, dimensions = _populations(
        "EX6", levels=("MEAN_REVERSION", "TREND_CONTINUATION"),
    )
    dimensions.records[0].strategy_family = "REVERSAL"
    report = hetero.analyse_ex6(candidate, reproduction, path, dimensions)
    assert report["overall"]["population"]["dimension_excluded_lifecycles"]["INVALID_ENTRY_DIMENSION_AUTHORITY"] == 1


def test_ex7_exact_open_regime_no_h4_fallback():
    report = hetero.analyse_ex7(*_populations("EX7", levels=("TRENDING", "RANGING")))
    assert report["overall"]["population"]["dimension_source"] == REGIME_SOURCE
    assert "h4" not in report["overall"]["population"]["dimension_source"].lower()


def test_ex7_invalid_or_missing_entry_regime_fails_closed_without_fallback():
    candidate, reproduction, path, dimensions = _populations("EX7", levels=("TRENDING", "RANGING"))
    dimensions.records[0].market_regime = "VOLATILE"
    report = hetero.analyse_ex7(candidate, reproduction, path, dimensions)
    assert report["overall"]["population"]["dimension_excluded_lifecycles"]["INVALID_ENTRY_DIMENSION_AUTHORITY"] == 1
    dimensions.records[1].market_regime = None
    report = hetero.analyse_ex7(candidate, reproduction, path, dimensions)
    assert report["overall"]["population"]["dimension_excluded_lifecycles"]["MISSING_ENTRY_DIMENSION_AUTHORITY"] == 1


def test_ex8_exact_open_pattern_and_cell_sufficiency():
    report = hetero.analyse_ex8(*_populations("EX8", levels=("HAMMER", "ENGULFING")))
    population = report["overall"]["population"]
    assert population["dimension_source"] == PATTERN_SOURCE
    assert population["sufficient_levels"] == ["ENGULFING", "HAMMER"]  # canonical sorted authority order
    assert report["status"] == "COMPLETE"


def test_ex8_missing_pattern_is_not_reconstructed_from_ohlc():
    report = hetero.analyse_ex8(*_populations("EX8", missing=True))
    assert report["status"] == "WAITING_DATA"
    assert report["overall"]["population"]["observed_levels"] == []


def test_dimension_extension_exact_sources_immutability_and_digest_sensitivity():
    opened = {
        "shadow_trade_id": "s1", "canonical_opportunity_id": "o1", "horizon": "SCALP",
        "live_facts": {"strategy": "MEAN_REVERSION", "regime": "RANGING", "pattern": "HAMMER"},
    }
    original = deepcopy(opened)
    path_record = SimpleNamespace(
        lifecycle_identity=("s1", "o1", "SCALP"), canonical_opportunity_id="o1",
        trade_horizon="SCALP", analytical_digest="path-row",
    )
    path = SimpleNamespace(records=(path_record,), provenance={"digest": "path", "analytical_digest": "path-a"})
    first = build_exit_dimension_evidence_v1((LifecyclePathSource(opened, {}, ()),), path)
    assert opened == original
    assert first.records[0].strategy_family == "MEAN_REVERSION"
    changed = deepcopy(opened); changed["live_facts"]["pattern"] = "DOJI"
    second = build_exit_dimension_evidence_v1((LifecyclePathSource(changed, {}, ()),), path)
    assert first.provenance["digest"] != second.provenance["digest"]


def test_invalid_dimension_provenance_returns_blocked(monkeypatch):
    monkeypatch.setattr(
        hetero, "validate_exit_dimension_evidence",
        lambda *args: (_ for _ in ()).throw(ValueError("invalid dimension provenance")),
    )
    report = hetero.analyse_ex5(*_populations())
    assert report["status"] == "BLOCKED"


def test_governed_report_digest_and_status_validation():
    report = hetero.analyse_ex5(*_populations())
    assert hetero.validate_governed_heterogeneity_report(report, "EX5")[0]
    report["status"] = "WAITING_DATA"
    assert not hetero.validate_governed_heterogeneity_report(report, "EX5")[0]


def test_registry_wires_only_ex5_through_ex8():
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID
    for question_id in hetero.TARGETS:
        assert REGISTRY_BY_ID[question_id].runner_module == hetero.__name__
    assert REGISTRY_BY_ID["EX10"].runner_module != hetero.__name__


def test_current_report_ownership_is_exact():
    from research_engine.control_plane.report_ownership import canonical_report_owner
    from research_engine.registry.exit_policy_adjudication import REPORT_OWNERSHIP

    for question_id in hetero.TARGETS:
        assert canonical_report_owner(REPORT_OWNERSHIP[question_id]) == question_id


def test_stale_or_tampered_report_cannot_establish_complete():
    from research_engine.control_plane.models import ReportValidity
    from research_engine.control_plane.report_resolver import resolve_report_validity

    report = hetero.analyse_ex5(*_populations())
    report["epoch"] = "STALE"
    validity, _ = resolve_report_validity(
        "ex5_horizon_exit.json", report, expected_question_id="EX5",
    )
    assert validity == ReportValidity.INVALIDATED


def test_registry_operational_state_follows_actual_governed_result():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    assert MASTER_REPAIR_LEDGER["EX7"].structurally_operational
    for question_id in ("EX5", "EX6", "EX8"):
        assert not MASTER_REPAIR_LEDGER[question_id].structurally_operational
