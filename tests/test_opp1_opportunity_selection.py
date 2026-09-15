"""Focused OPP-1 promoted-vs-rejected opportunity expectancy validation tests."""
from __future__ import annotations

import unittest.mock as mock

import pytest

import research_engine.experiments.opportunity_selection as opp
from research_engine.experiments.opportunity_selection import (
    OPP1_REPORT_FILENAME,
    build_opp1_observations,
    classify_opportunity_membership,
    run_opp_1,
    _opp1_group_stats,
    _opp1_split,
)
from research_engine.experiments.selection_analysis import QuarantineBoundary
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _boundary():
    return QuarantineBoundary("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00")


def _hc(opp_id: str, status: str) -> dict:
    return {"canonical_opportunity_id": opp_id, "selection_status": status}


def _shadow(opp_id: str, outcome, i: int = 0, *, horizon: str = "SCALP", shadow_id: str | None = None) -> dict:
    ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
    return {
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": opp_id,
            "entity_id": f"ent-{opp_id}",
            "shadow_trade_id": shadow_id or f"nshadow_{opp_id}",
            "symbol": "EURUSD",
            "evaluated_horizon": horizon,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": ts,
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
            "pattern": "PIN_BAR",
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _population(n=120, *, promoted_better=True):
    """n canonical opportunities alternating promoted/rejected; when
    promoted_better, promoted opps win and rejected opps lose."""
    hcs, shadows = [], []
    for i in range(n):
        opp_id = f"canon-{i:04d}"
        promoted = (i % 2 == 0)
        hcs.append(_hc(opp_id, "SELECTED" if promoted else "REJECTED"))
        if promoted_better:
            outcome = 1.0 if promoted else -1.0
        else:
            outcome = -1.0 if promoted else 1.0
        shadows.append(_shadow(opp_id, outcome, i))
    return hcs, shadows


def _run(hcs, shadows, opportunities=None, assessments=None):
    with mock.patch.object(opp, "load_quarantine_boundary", _boundary):
        return opp.run_opp_1(
            opportunities=opportunities or [],
            assessments=assessments or [],
            horizon_candidates=hcs,
            shadow_trades=shadows,
        )


# 1 + 2 + 3. Hypothesis / population / unit explicit.
def test_opp1_canonical_contract_explicit():
    hcs, shadows = _population()
    report = _run(hcs, shadows)
    o = report["overall"]
    assert o["canonical_question"] == "OPP-1"
    assert "promoted" in o["hypothesis"].lower() and "rejected" in o["hypothesis"].lower()
    assert "canonical_opportunity_id" in o["unit_of_analysis"]


# 4 + 5. canonical_opportunity_id is the identity; no legacy opportunity_id fallback.
def test_membership_keyed_by_canonical_id_only():
    membership, conflicts = classify_opportunity_membership([
        _hc("canon-1", "SELECTED"),
        {"opportunity_id": "legacy-2", "selection_status": "SELECTED"},  # no canonical id -> ignored
    ])
    assert membership == {"canon-1": "PROMOTED"}
    assert conflicts == []


# 6 + 7 + 8. Account fanout / repeated horizons cannot inflate n; one opp once.
def test_fanout_and_horizons_do_not_inflate_n():
    hcs = [_hc("canon-1", "SELECTED")]
    primary = _shadow("canon-1", 1.0, 0, horizon="SCALP", shadow_id="a")
    fanout = _shadow("canon-1", 1.0, 0, horizon="SCALP", shadow_id="b")  # account fanout
    horizon = _shadow("canon-1", 3.0, 0, horizon="EXTENDED", shadow_id="c")  # repeated horizon
    rows, diagnostics = build_opp1_observations(hcs, [primary, fanout, horizon])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0  # mean of 1.0 and 3.0 across horizons
    assert diagnostics["paired_opportunities"] == 1


# 9 + 10 + 11. Correct opportunity pairs to its own outcome; no positional/symbol join.
def test_correct_opportunity_pairs_to_own_outcome():
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-2", "REJECTED")]
    shadows = [_shadow("canon-1", 2.0, 0), _shadow("canon-2", -2.0, 1)]
    rows, _ = build_opp1_observations(hcs, shadows)
    by_id = {r["canonical_opportunity_id"]: r for r in rows}
    assert by_id["canon-1"]["outcome_r"] == 2.0 and by_id["canon-1"]["group"] == "PROMOTED"
    assert by_id["canon-2"]["outcome_r"] == -2.0 and by_id["canon-2"]["group"] == "REJECTED"


# 12 + 13 + 14 + 15 + 16. Missing outcome excluded / diagnostic / never 0/loss/success.
def test_missing_outcome_excluded_never_imputed():
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-2", "REJECTED")]
    # canon-2 has NO shadow outcome at all.
    shadows = [_shadow("canon-1", 1.0, 0)]
    rows, diagnostics = build_opp1_observations(hcs, shadows)
    assert len(rows) == 1
    assert rows[0]["canonical_opportunity_id"] == "canon-1"
    assert diagnostics["missing_outcomes_excluded"] == 1
    # No imputed 0.0/loss present for the missing opportunity.
    assert all(r["canonical_opportunity_id"] != "canon-2" for r in rows)


def test_missing_none_outcome_excluded():
    hcs = [_hc("canon-1", "SELECTED")]
    shadows = [_shadow("canon-1", None, 0)]
    rows, diagnostics = build_opp1_observations(hcs, shadows)
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 17 + 18. Explicit realised 0.0R is VALID and included; distinguishable from missing.
def test_explicit_zero_is_valid_and_distinct_from_missing():
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-2", "REJECTED")]
    shadows = [_shadow("canon-1", 0.0, 0), _shadow("canon-2", None, 1)]
    rows, diagnostics = build_opp1_observations(hcs, shadows)
    # canon-1 with explicit 0.0R is kept; canon-2 (missing) is excluded.
    assert len(rows) == 1
    assert rows[0]["canonical_opportunity_id"] == "canon-1"
    assert rows[0]["outcome_r"] == 0.0
    assert rows[0]["won"] == 0.0  # 0.0 is not a win, but IS a valid observation
    assert diagnostics["missing_outcomes_excluded"] == 1


# 19. Invalid/non-numeric outcome excluded.
def test_invalid_outcome_excluded():
    hcs = [_hc("canon-1", "SELECTED")]
    bad = _shadow("canon-1", 0.0, 0)
    bad["simulated_outcome"]["pnl_r_multiple"] = "not-a-number"
    rows, diagnostics = build_opp1_observations(hcs, [bad])
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 20. Conflicting membership fails closed (both promoted and rejected).
def test_conflicting_membership_fails_closed():
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-1", "REJECTED")]
    shadows = [_shadow("canon-1", 1.0, 0)]
    rows, diagnostics = build_opp1_observations(hcs, shadows)
    assert rows == []
    assert "canon-1" in diagnostics["conflicting_opportunities"]


# 21. Duplicate compatible evidence collapses deterministically.
def test_duplicate_compatible_evidence_collapses():
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-1", "SELECTED")]  # same class twice
    shadows = [_shadow("canon-1", 1.0, 0)]
    membership, conflicts = classify_opportunity_membership(hcs)
    assert membership == {"canon-1": "PROMOTED"}
    assert conflicts == []


# 22 + 23 + 24. Outcome-dependent mean/median/win-rate use only valid paired outcomes.
def test_stats_use_only_valid_paired_outcomes():
    rows = [
        {"group": "PROMOTED", "outcome_r": 2.0, "won": 1.0},
        {"group": "PROMOTED", "outcome_r": 0.0, "won": 0.0},
        {"group": "PROMOTED", "outcome_r": -1.0, "won": 0.0},
    ]
    stats = _opp1_group_stats(rows)
    assert stats["n"] == 3
    assert stats["mean_r"] == pytest.approx((2.0 + 0.0 - 1.0) / 3.0, abs=1e-4)
    assert stats["median_r"] == 0.0
    assert stats["win_rate"] == pytest.approx(1 / 3, abs=1e-4)


# 25 + 26. Population/subgroup membership cannot use realised outcome.
def test_membership_is_pre_outcome_not_from_realised_r():
    # Two promoted opps with opposite outcomes still both PROMOTED (membership
    # comes from selection_status, not the realised sign).
    hcs = [_hc("canon-1", "SELECTED"), _hc("canon-2", "SELECTED")]
    shadows = [_shadow("canon-1", 5.0, 0), _shadow("canon-2", -5.0, 1)]
    rows, _ = build_opp1_observations(hcs, shadows)
    assert {r["group"] for r in rows} == {"PROMOTED"}


# 27. Sparse subgroup cannot make a directional claim (marked insufficient).
def test_sparse_subgroup_insufficient():
    hcs, shadows = [], []
    for i in range(120):
        opp_id = f"canon-{i:04d}"
        promoted = (i % 2 == 0)
        hcs.append(_hc(opp_id, "SELECTED" if promoted else "REJECTED"))
        s = _shadow(opp_id, 1.0 if promoted else -1.0, i)
        # A rare regime cell with only a couple members.
        if i in (0, 2):
            s["decision_snapshot"]["h4_regime"] = "TRANSITIONAL"
        shadows.append(s)
    report = _run(hcs, shadows)
    cells = report["overall"]["context_diagnostics"]["h4_regime"]
    assert cells["TRANSITIONAL"]["sufficient"] is False
    assert cells["TRENDING"]["sufficient"] is True


# 28 + 29. Chronology deterministic; validation cannot redefine discovery.
def test_chronology_deterministic():
    hcs, shadows = _population()
    rows, _ = build_opp1_observations(hcs, shadows)
    discovery, validation = _opp1_split(rows)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(r["_order"] for r in discovery) < min(r["_order"] for r in validation)


# 30. Insufficient evidence -> WAITING_DATA (INSUFFICIENT_DATA).
def test_insufficient_evidence_waits():
    hcs, shadows = _population(20)
    report = _run(hcs, shadows)
    assert report["status"] == "INSUFFICIENT_DATA"


# 31. Valid negative/no-signal result can COMPLETE.
def test_no_signal_result_can_complete():
    hcs, shadows = [], []
    for i in range(140):
        opp_id = f"canon-{i:04d}"
        promoted = (i % 2 == 0)
        hcs.append(_hc(opp_id, "SELECTED" if promoted else "REJECTED"))
        # Outcome decoupled from promotion.
        shadows.append(_shadow(opp_id, 1.0 if (i // 2) % 2 == 0 else -1.0, i))
    report = _run(hcs, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {
        "NO_RELIABLE_SELECTION_SIGNAL", "DISCOVERY_ONLY",
        "PROMOTED_UNDERPERFORMS_REJECTED", "PROMOTED_OUTPERFORMS_REJECTED",
    }


# PROMOTED_OUTPERFORMS_REJECTED reachable.
def test_promoted_outperforms_reachable():
    hcs, shadows = _population(160, promoted_better=True)
    report = _run(hcs, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "PROMOTED_OUTPERFORMS_REJECTED"


# PROMOTED_UNDERPERFORMS_REJECTED reachable.
def test_promoted_underperforms_reachable():
    hcs, shadows = _population(160, promoted_better=False)
    report = _run(hcs, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "PROMOTED_UNDERPERFORMS_REJECTED"


# 32 + 33. Unique OPP-1 report ownership; legacy/other reports cannot complete it.
def test_opp1_unique_report_and_complete_definition():
    question = REGISTRY_BY_ID["OPP-1"]
    assert question.runner_function == "run_opp_1"
    assert question.report_filename == OPP1_REPORT_FILENAME == "opp1_opportunity_selection.json"
    assert sum(q.report_filename == OPP1_REPORT_FILENAME for q in REGISTRY) == 1
    d6, port1 = REGISTRY_BY_ID["D6"], REGISTRY_BY_ID["PORT-1"]
    assert question.report_filename != d6.report_filename
    assert question.report_filename != port1.report_filename
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["OPP-1"]) == "VALID"


# 34 + 35 + 36 + 37 + 38 + 39. Ledger 39/70; D6/PORT-1 op; P1 non-op; RW4 in progress.
def test_ledger_derives_39_and_rw4_in_progress():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (39, 31)
    assert MASTER_REPAIR_LEDGER["OPP-1"].structurally_operational
    assert MASTER_REPAIR_LEDGER["D6"].structurally_operational
    assert MASTER_REPAIR_LEDGER["PORT-1"].structurally_operational
    assert REPAIR_WAVES["RW4"].implemented is False
    assert set(REPAIR_WAVES["RW4"].direct_gain) == {"P1"}
    assert "P1" in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "OPP-1" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    for wave_id in ("RW1", "RW2", "RW3"):
        assert REPAIR_WAVES[wave_id].implemented is True


# 40 (proxy). Existing operational questions remain operational.
def test_existing_operational_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D6", "PORT-1", "D2", "D3", "D4", "D5", "X5", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational
