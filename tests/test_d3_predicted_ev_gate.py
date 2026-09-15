"""Focused D3 predicted-EV gate repair tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_engine.experiments.d3_predicted_ev_gate import (
    D3_REPORT_FILENAME,
    PREDICTED_EV_VERSION,
    analyse,
    build_paired_predictions,
    chronological_split,
    predicted_ev_r,
    reward_r_from_geometry,
    select_research_threshold,
)
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
    """Well-formed 2R geometry population; alternating win/loss."""
    decisions, shadows = [], []
    for index in range(count):
        won = bool(index % 2)
        # entry=1.0, stop=0.99 (risk 0.01), target=1.02 (reward 0.02) -> reward_r=2.0
        decisions.append(_decision(index, probability=1.0 if won else 0.0))
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    return decisions, shadows


# 4 + 5. predicted_ev_r uses R units; known 2R example.
def test_predicted_ev_r_uses_r_units_known_2r_example():
    reward_r = reward_r_from_geometry({"entry_price": 1.0, "stop_price": 0.99, "target_price": 1.02})
    assert reward_r == pytest.approx(2.0)
    # p=0.5, reward_r=2.0 -> 0.5*2 - 0.5*1 = +0.5R
    assert predicted_ev_r(0.5, reward_r) == pytest.approx(0.5)
    # Raw price-distance EV would be 0.5*0.02 - 0.5*0.01 = +0.005 (NOT R units).
    assert predicted_ev_r(0.5, reward_r) != pytest.approx(0.005)


# 6. Invalid geometry fails closed.
@pytest.mark.parametrize("entry,stop,target", [
    (1.0, 1.0, 1.02),      # zero SL distance
    (1.0, 0.99, 1.0),      # zero TP distance
    (1.0, 1.01, 1.02),     # stop above entry while target above entry (same side)
    (None, 0.99, 1.02),    # missing entry
    (1.0, None, 1.02),     # missing stop
    (1.0, 0.99, None),     # missing target
    (float("inf"), 0.99, 1.02),  # non-finite
])
def test_invalid_geometry_fails_closed(entry, stop, target):
    assert reward_r_from_geometry({"entry_price": entry, "stop_price": stop, "target_price": target}) is None


def test_invalid_geometry_excluded_from_pairing():
    good = _decision(1, probability=0.6)
    bad = _decision(2, probability=0.6, stop=2.0, target=3.0)  # stop above entry (same side as target)
    rows, diagnostics = build_paired_predictions(
        [good, bad], [_shadow(1, outcome=1.0), _shadow(2, outcome=1.0)]
    )
    assert len(rows) == 1
    assert rows[0]["canonical_opportunity_id"] == "OPP-0001"
    assert diagnostics["excluded_invalid_geometry"] == 1


# 1 + 2 + 3. One opportunity = one observation; fanout/horizons cannot inflate n.
def test_one_canonical_opportunity_is_one_observation_no_fanout_no_horizon_inflation():
    decision = _decision(1, probability=0.8)
    first = _shadow(1, outcome=1.0, horizon="SCALP")
    duplicate = _shadow(1, outcome=1.0, horizon="SCALP")  # account fanout copy
    duplicate["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    alternative = _shadow(1, outcome=3.0, horizon="EXTENDED")  # repeated horizon
    alternative["identity"]["shadow_trade_id"] = "nshadow_eeeeeeeeeeeeeeee"
    rows, diagnostics = build_paired_predictions([decision], [first, duplicate, alternative])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0  # mean of SCALP(1.0) and EXTENDED(3.0)
    assert diagnostics["repeated_rows_collapsed"] == 2


# 7. Only pre-decision information enters predicted_ev_r.
def test_only_pre_decision_information_enters_predicted_ev():
    decision = _decision(1, probability=0.5)
    # Inject post-outcome fields that must be ignored by the prediction row.
    decision["realised_r"] = 5.0
    decision["r_multiple"] = 5.0
    rows, _ = build_paired_predictions([decision], [_shadow(1, outcome=-1.0)])
    assert len(rows) == 1
    # +0.5R from p=0.5, reward_r=2.0 — independent of the realised -1.0 outcome.
    assert rows[0]["predicted_ev_r"] == pytest.approx(0.5)


# 8. Missing outcome remains missing/excluded.
def test_missing_outcome_is_excluded_never_converted_to_loss():
    rows, diagnostics = build_paired_predictions([_decision(1, probability=0.7)], [_shadow(1, outcome=None)])
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 9 + 10. Conflicting outcomes / post-decision chronology fail closed.
def test_conflicting_and_post_decision_chronology_fail_closed():
    decisions, shadows = _population()
    conflicting = dict(decisions[3])
    conflicting["p_success"] = 0.4
    assert analyse([*decisions, conflicting], shadows)["status"] == "BLOCKED"
    late = list(decisions)
    late[3] = _decision(3, probability=1.0, offset_hours=1)  # prediction after outcome
    assert analyse(late, shadows)["status"] == "BLOCKED"


# 11. Deterministic chronological discovery/validation.
def test_chronological_discovery_validation_is_deterministic():
    decisions, shadows = _population()
    paired, _ = build_paired_predictions(decisions, shadows)
    discovery, validation = chronological_split(paired)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(row["timestamp"] for row in discovery) < min(row["timestamp"] for row in validation)
    assert {r["canonical_opportunity_id"] for r in discovery}.isdisjoint(
        r["canonical_opportunity_id"] for r in validation
    )


# 12. Validation data cannot select its own threshold.
def test_validation_cannot_select_its_own_threshold():
    decisions, shadows = _population()
    paired, _ = build_paired_predictions(decisions, shadows)
    discovery, validation = chronological_split(paired)
    threshold, provenance = select_research_threshold(discovery)
    # Re-selecting on a deliberately shifted validation set must not change the
    # discovery-derived threshold the runner uses.
    shifted = [{**row, "predicted_ev_r": row["predicted_ev_r"] + 100.0} for row in validation]
    threshold_from_validation, _ = select_research_threshold(shifted)
    assert threshold != threshold_from_validation
    assert provenance in {"research_break_even_r_0", "research_discovery_median_r"}


# 13. policy_trade_allowed is NOT treated as an isolated EV-gate treatment.
def test_policy_trade_allowed_is_not_treated_as_ev_gate_treatment():
    decisions, shadows = _population()
    for record in decisions:
        record["policy_trade_allowed"] = False  # would flip legacy treatment
    report = analyse(decisions, shadows)
    # Grouping is by predicted_ev_r threshold, independent of policy_trade_allowed.
    assert report["overall"]["ev_eligible_validation"]["n"] > 0
    assert report["overall"]["ev_ineligible_validation"]["n"] > 0
    assert "policy_trade_allowed" not in report["overall"]["threshold"]
    assert report["overall"]["causal_claim"].startswith("NONE")


# 14 + 15. D2 calibration provenance preserved; uncalibrated p_success not presented as calibrated EV.
def test_calibration_provenance_preserved_and_uncalibrated_not_presented_as_truth():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    overall = report["overall"]
    assert overall["p_success_model_version"] == "heuristic_score_v1"
    assert overall["probability_calibration_state"] == "UNCALIBRATED"
    assert overall["probability_calibration_authority"].startswith("D2")
    assert overall["predicted_ev_measure"]["version"] == PREDICTED_EV_VERSION
    assert "UNCALIBRATED" in report["recommendation"]
    # Never claims validated/calibrated EV.
    assert "VALIDATED" not in report["recommendation"].upper()


# 16 + 17. Unique report ownership; legacy score-threshold artifact cannot complete D3.
def test_d3_has_unique_canonical_runner_report_and_complete_definition():
    question = REGISTRY_BY_ID["D3"]
    assert question.runner_module == "research_engine.experiments.d3_predicted_ev_gate"
    assert question.runner_function == "run_d3"
    assert question.report_filename == D3_REPORT_FILENAME
    assert sum(item.report_filename == D3_REPORT_FILENAME for item in REGISTRY) == 1
    # The legacy descriptive score-threshold artifact must not own D3.
    assert question.report_filename != "q21_calibration_ev_impact.json"
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["D3"]) == "VALID"


# 18. WAITING_DATA / BLOCKED / COMPLETE remain distinct.
def test_states_remain_distinct():
    # WAITING_DATA: too few paired observations.
    waiting = analyse(
        [_decision(i, probability=0.6) for i in range(10)],
        [_shadow(i, outcome=1.0 if i % 2 else -1.0) for i in range(10)],
    )
    assert waiting["status"] == "WAITING_DATA"

    # COMPLETE: sufficient well-formed population with both groups populated.
    decisions, shadows = [], []
    for index in range(160):
        won = bool(index % 2)
        # Half get +EV geometry (reward_r 2.0), half get -EV via low reward_r.
        if index % 2 == 0:
            decisions.append(_decision(index, probability=0.2, target=1.005))  # reward_r 0.5 -> EV negative
        else:
            decisions.append(_decision(index, probability=0.9, target=1.03))   # reward_r 3.0 -> EV positive
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    complete = analyse(decisions, shadows)
    assert complete["status"] == "COMPLETE"
    assert complete["overall"]["expectancy_difference_r"] is not None

    # BLOCKED: mixed model versions.
    mixed_decisions, mixed_shadows = _population()
    mixed_decisions[5] = _decision(5, probability=0.0, version="other_model_v2")
    assert analyse(mixed_decisions, mixed_shadows)["status"] == "BLOCKED"


# 19 + 20 + 21. Ledger derives 33/37; D4/D5/X5 non-operational; RW1/RW2/D2 unchanged.
def test_ledger_derives_33_and_neighbours_unchanged():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (36, 34)
    assert MASTER_REPAIR_LEDGER["D3"].structurally_operational
    assert MASTER_REPAIR_LEDGER["D2"].structurally_operational
    # RW3 is COMPLETE after X5 (RW3.5); no RW3 target remains non-operational.
    assert REPAIR_WAVES["RW3"].implemented is True
    assert not {"D2", "D3", "D4", "D5", "X5"} & STRUCTURALLY_NON_OPERATIONAL_IDS
    # RW1/RW2 direct gains remain operational.
    for qid in ("D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational
