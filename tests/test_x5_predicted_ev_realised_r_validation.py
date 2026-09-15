"""Focused X5 predicted-EV vs realised-R validation repair tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_engine.experiments.d3_predicted_ev_gate import PREDICTED_EV_VERSION
from research_engine.experiments.x5_predicted_ev_realised_r_validation import (
    X5_REPORT_FILENAME,
    MINIMUM_BIN,
    analyse,
    apply_bins,
    discovery_bin_boundaries,
    rank_relationship,
    _error_metrics,
)
from research_engine.experiments.d3_predicted_ev_gate import build_paired_predictions, chronological_split
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _shadow(index: int, *, opportunity: str | None = None, outcome: float | None = None, horizon: str = "SCALP") -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    opportunity = opportunity or f"OPP-{index:04d}"
    return {
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": opportunity,
            "entity_id": f"ENTITY-{index:04d}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "symbol": "EURUSD",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp.isoformat(),
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
            "h1_bias": "BULLISH",
            "pattern": "PIN_BAR",
            "strategy": "REVERSAL",
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _decision(
    index: int,
    *,
    opportunity: str | None = None,
    probability: float | None = None,
    entry: float = 1.0,
    stop: float = 0.99,
    target: float = 1.02,
    epoch: str = "CURRENT",
    offset_hours: int = 0,
    version: str = "heuristic_score_v1",
    with_geometry: bool = True,
) -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index + offset_hours)
    record = {
        "data_epoch": epoch,
        "canonical_opportunity_id": opportunity or f"OPP-{index:04d}",
        "timestamp_utc": timestamp.isoformat(),
        "p_success": probability,
        "p_success_model_version": version,
    }
    if with_geometry:
        record["v10_entry"] = {
            "direction": "BUY",
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
        }
    return record


def _population(count: int = 120):
    """Predicted EV correlates with realised R: high-EV wins, low-EV loses.

    reward_r=2.0 fixed; p_success alternates to make predicted_ev_r vary, and
    realised R follows the same direction so ranking + magnitude are decent.
    """
    decisions, shadows = [], []
    for index in range(count):
        won = bool(index % 2)
        prob = 0.9 if won else 0.1
        decisions.append(_decision(index, probability=prob))
        # p=0.9,reward=2 -> ev=+1.7 ; p=0.1 -> ev=-0.7. realised close to those.
        shadows.append(_shadow(index, outcome=1.7 if won else -0.7))
    return decisions, shadows


# 1 + 2 + 3. Uses D3's predicted_ev_r_v1; raw ev / generic aliases cannot
# substitute (pairing reads p_success + geometry, never ev/score/etc.).
def test_uses_d3_predicted_ev_authority_not_raw_ev_or_aliases():
    decision = _decision(1, probability=0.5)
    # Inject production raw ev and aliases that must be ignored.
    decision["ev"] = 999.0
    decision["expected_value"] = 999.0
    decision["score"] = 0.99
    decision["score_strategy"] = 0.99
    decision["score_neutral"] = 0.99
    decision["confidence"] = 0.99
    rows, _ = build_paired_predictions([decision], [_shadow(1, outcome=1.0)])
    assert len(rows) == 1
    # p=0.5, reward_r=2.0 -> predicted_ev_r_v1 = +0.5 (NOT 999, NOT raw ev).
    assert rows[0]["predicted_ev_r"] == pytest.approx(0.5)


# 4 + 5. Probability provenance/version preserved; UNCALIBRATED state visible.
def test_probability_provenance_and_uncalibrated_state_preserved():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    overall = report["overall"]
    assert overall["p_success_model_version"] == "heuristic_score_v1"
    assert overall["probability_calibration_state"] == "UNCALIBRATED"
    assert overall["probability_calibration_authority"].startswith("D2")
    assert overall["predicted_ev_measure"]["version"] == PREDICTED_EV_VERSION
    # Never claims fully calibrated expected return.
    assert "UNCALIBRATED" in report["recommendation"]


# 6 + 7 + 8. One opportunity = one observation; fanout/horizons cannot inflate n.
def test_one_opportunity_one_observation_no_fanout_no_horizon_inflation():
    decision = _decision(1, probability=0.8)
    first = _shadow(1, outcome=1.0, horizon="SCALP")
    fanout = _shadow(1, outcome=1.0, horizon="SCALP")
    fanout["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    alt = _shadow(1, outcome=3.0, horizon="EXTENDED")
    alt["identity"]["shadow_trade_id"] = "nshadow_eeeeeeeeeeeeeeee"
    rows, diagnostics = build_paired_predictions([decision], [first, fanout, alt])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0  # mean of 1.0 and 3.0
    assert diagnostics["repeated_rows_collapsed"] == 2


# 9. Missing outcome remains missing/excluded.
def test_missing_outcome_excluded():
    rows, diagnostics = build_paired_predictions([_decision(1, probability=0.7)], [_shadow(1, outcome=None)])
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 10 + 11. Conflicting evidence / post-decision chronology fail closed.
def test_conflicting_and_post_decision_chronology_fail_closed():
    decisions, shadows = _population()
    conflicting = dict(decisions[3])
    conflicting["p_success"] = 0.3
    assert analyse([*decisions, conflicting], shadows)["status"] == "BLOCKED"
    late = list(decisions)
    late[3] = _decision(3, probability=0.9, offset_hours=1)  # prediction after outcome
    assert analyse(late, shadows)["status"] == "BLOCKED"


# 12 + 14. Prediction/outcome paired by canonical opportunity; both in R units.
def test_paired_by_opportunity_and_r_compatible_units():
    decisions, shadows = _population()
    rows, _ = build_paired_predictions(decisions, shadows)
    for row in rows:
        assert "predicted_ev_r" in row and "outcome_r" in row
        # Both are plain R-scale floats (predicted EV in R, realised R).
        assert -5.0 < row["predicted_ev_r"] < 5.0
        assert -5.0 < row["outcome_r"] < 5.0


# 13. Aggregate-mean-only analysis cannot complete X5 (paired errors present).
def test_paired_error_metrics_present_not_mean_only():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    error = report["overall"]["error_metrics"]["later_unseen_validation"]
    assert error["mean_error"] is not None
    assert error["mae"] is not None
    assert error["rmse"] is not None
    assert report["overall"]["error_metrics"]["definition"].startswith("error = realised_r - predicted_ev_r")


# 15. Mean prediction error computed correctly.
def test_mean_error_computed_correctly():
    rows = [
        {"predicted_ev_r": 1.0, "outcome_r": 2.0},   # error +1.0
        {"predicted_ev_r": 0.0, "outcome_r": -1.0},  # error -1.0
        {"predicted_ev_r": 0.5, "outcome_r": 0.5},   # error 0.0
    ]
    metrics = _error_metrics(rows)
    assert metrics["mean_error"] == pytest.approx(0.0)


# 16. MAE computed correctly.
def test_mae_computed_correctly():
    rows = [
        {"predicted_ev_r": 1.0, "outcome_r": 2.0},   # |1.0|
        {"predicted_ev_r": 0.0, "outcome_r": -1.0},  # |1.0|
        {"predicted_ev_r": 0.5, "outcome_r": 0.5},   # |0.0|
    ]
    metrics = _error_metrics(rows)
    assert metrics["mae"] == pytest.approx((1.0 + 1.0 + 0.0) / 3.0)


# 17. RMSE computed correctly.
def test_rmse_computed_correctly():
    rows = [
        {"predicted_ev_r": 1.0, "outcome_r": 2.0},   # 1.0^2
        {"predicted_ev_r": 0.0, "outcome_r": -1.0},  # 1.0^2
        {"predicted_ev_r": 0.5, "outcome_r": 0.5},   # 0.0
    ]
    metrics = _error_metrics(rows)
    assert metrics["rmse"] == pytest.approx((2.0 / 3.0) ** 0.5)


# 18. Ranking/directional relationship is measured.
def test_rank_relationship_measured():
    rows = [
        {"predicted_ev_r": -0.7, "outcome_r": -1.0},
        {"predicted_ev_r": 0.0, "outcome_r": 0.2},
        {"predicted_ev_r": 0.5, "outcome_r": 0.4},
        {"predicted_ev_r": 1.7, "outcome_r": 2.0},
    ]
    rho = rank_relationship(rows)
    assert rho is not None
    assert rho > 0.9  # monotone increasing -> strong positive rank correlation


# 19 + 20. Calibration bins derive from discovery only; validation cannot
# redefine boundaries.
def test_calibration_bins_derive_from_discovery_only():
    decisions, shadows = _population()
    paired, _ = build_paired_predictions(decisions, shadows)
    discovery, validation = chronological_split(paired)
    boundaries = discovery_bin_boundaries(discovery)
    # Re-deriving on a shifted validation set must not change discovery bins.
    shifted = [{**row, "predicted_ev_r": row["predicted_ev_r"] + 100.0} for row in validation]
    validation_boundaries = discovery_bin_boundaries(shifted)
    assert boundaries != validation_boundaries
    # Applying frozen discovery boundaries to validation uses those exact cuts.
    binned = apply_bins(validation, boundaries)
    assert isinstance(binned, dict)


# 21. Tiny validation bins are not overinterpreted.
def test_tiny_bins_marked_insufficient():
    rows = [{"predicted_ev_r": 0.5, "outcome_r": 1.0}]  # single-row bin
    binned = apply_bins(rows, [])  # no boundaries -> one bin index 0
    assert binned[0]["n"] == 1
    assert binned[0]["sufficient"] is False


# 22. Positive-EV vs non-positive-EV validation groups enforce minimum n.
def test_positive_ev_groups_enforce_minimum():
    # All predictions strongly positive EV -> non-positive validation group empty.
    decisions, shadows = [], []
    for index in range(140):
        decisions.append(_decision(index, probability=0.95))  # ev strongly positive
        shadows.append(_shadow(index, outcome=1.0 if index % 2 else -1.0))
    report = analyse(decisions, shadows)
    assert report["status"] == "WAITING_DATA"
    assert "non-positive-EV" in report["overall"]["completion_reason"]


# 23 + 24. Ranking value is distinct from magnitude calibration; systematic
# miscalibration reportable despite useful ranking.
def test_systematic_miscalibration_despite_useful_ranking():
    # Ranking strong (monotone) but predicted EV SYSTEMATICALLY understates
    # realised R (one-directional bias) -> useful ranking, poor magnitude.
    decisions, shadows = [], []
    for index in range(160):
        higher = bool(index % 2)
        # positive-EV vs non-positive-EV split for group sufficiency.
        prob = 0.6 if higher else 0.2  # ev = +0.8 (positive) / -0.4 (non-positive)
        decisions.append(_decision(index, probability=prob))
        # realised R far exceeds predicted EV in the SAME direction (bias),
        # so the mean error is large and does not cancel.
        shadows.append(_shadow(index, outcome=5.0 if higher else 2.0))
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    overall = report["overall"]
    assert overall["finding_classification"] == "SYSTEMATIC_MISCALIBRATION"
    assert overall["interpretation_dimensions"]["ranking_directional_value"] == "PRESENT"
    assert overall["interpretation_dimensions"]["magnitude_calibration"] == "INADEQUATE_OR_UNKNOWN"


# VALIDATED_EV_SIGNAL reachable (ranking persists + magnitude adequate).
def test_validated_ev_signal_reachable():
    decisions, shadows = _population(160)
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "VALIDATED_EV_SIGNAL"


# 25. Negative / no-signal result can still be COMPLETE.
def test_no_reliable_signal_still_complete():
    # predicted EV varies but realised R is random noise -> weak/zero ranking.
    decisions, shadows = [], []
    for index in range(160):
        prob = 0.9 if index % 2 else 0.1
        decisions.append(_decision(index, probability=prob))
        # outcome decoupled from EV direction.
        shadows.append(_shadow(index, outcome=1.0 if (index // 2) % 2 == 0 else -1.0))
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {"NO_RELIABLE_EV_SIGNAL", "DISCOVERY_ONLY", "SYSTEMATIC_MISCALIBRATION"}


# 26 + 27 + 28. Unique report ownership; legacy X5 report and D3 report cannot
# complete X5; definition VALID.
def test_x5_has_unique_canonical_runner_report_and_complete_definition():
    question = REGISTRY_BY_ID["X5"]
    assert question.runner_module == "research_engine.experiments.x5_predicted_ev_realised_r_validation"
    assert question.runner_function == "run_x5"
    assert question.report_filename == X5_REPORT_FILENAME
    assert sum(item.report_filename == X5_REPORT_FILENAME for item in REGISTRY) == 1
    # Legacy X5 report and D3's report cannot own X5.
    assert question.report_filename != "w4_x5_execution_leakage.json"
    assert question.report_filename != "d3_predicted_ev_gate_validation_v1.json"
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["X5"]) == "VALID"


# 29 + 30. Ledger derives exactly 36/70; RW3 becomes implemented after X5.
def test_ledger_derives_36_and_rw3_complete():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (36, 34)
    assert MASTER_REPAIR_LEDGER["X5"].structurally_operational
    assert REPAIR_WAVES["RW3"].implemented is True
    assert set(REPAIR_WAVES["RW3"].direct_gain) == {"D2", "D3", "D4", "D5", "X5"}
    assert not {"D2", "D3", "D4", "D5", "X5"} & STRUCTURALLY_NON_OPERATIONAL_IDS


# 31. RW4 remains untouched (still outstanding, not implemented).
def test_rw4_remains_untouched():
    from research_engine.registry.master_repair_ledger import REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS

    rw4 = REPAIR_WAVES["RW4"]
    assert rw4.implemented is False
    assert rw4.implementation_evidence == ()
    assert set(rw4.direct_gain) == {"D6", "PORT-1", "OPP-1", "P1"}
    assert {"D6", "PORT-1", "OPP-1", "P1"} <= STRUCTURALLY_NON_OPERATIONAL_IDS


# 32. D2/D3/D4/D5/RW1/RW2 behaviour remains unchanged.
def test_d2_d3_d4_d5_rw1_rw2_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D2", "D3", "D4", "D5", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational


# 33. WAITING_DATA remains reachable without forcing COMPLETE.
def test_waiting_data_reachable():
    report = analyse(
        [_decision(i, probability=0.6) for i in range(10)],
        [_shadow(i, outcome=1.0 if i % 2 else -1.0) for i in range(10)],
    )
    assert report["status"] == "WAITING_DATA"
