"""Focused PORT-1 selection-competitiveness (selected-vs-best-available) tests."""
from __future__ import annotations

import unittest.mock as mock

import pytest

import research_engine.experiments.portfolio_ranking as pr
from research_engine.experiments.portfolio_ranking import (
    PORT1_COMPETITIVENESS_VERSION,
    PORT1_REPORT_FILENAME,
    _PORT1_REGRET_TOLERANCE_R,
    build_selection_cycles,
    run_port_1,
    _port1_split,
    _port1_stats,
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


def _decision(opp: str, symbol: str, cycle_id, corr: str, ts: str) -> dict:
    return {
        "cycle_id": cycle_id, "symbol": symbol, "decision": "EXECUTE",
        "correlation_id": corr, "canonical_opportunity_id": opp, "timestamp": ts,
    }


def _shadow(opp: str, outcome: float | None) -> dict:
    return {
        "canonical_opportunity_id": opp,
        "identity": {"shadow_type": ""},
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _cand(symbol: str, rank: int, selected: bool = False) -> dict:
    return {
        "symbol": symbol, "rank_position": rank,
        "selection_status": "SELECTED" if selected else "REJECTED",
        "rank_score": 1.0 / rank,
    }


def _cycle(cycle_id, ts, candidates):
    return {"cycle_id": cycle_id, "timestamp": ts, "candidates": candidates}


def _two_candidate_cycle(i, *, selected_rank, rank1_r, rank2_r):
    """Cycle with a rank-1 and rank-2 candidate; selected is rank `selected_rank`.
    Returns (ranking, decisions, shadows)."""
    ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
    opp1, opp2 = f"EURUSD*{i}*P", f"GBPUSD*{i}*P"
    cands = [
        _cand("EURUSD", 1, selected=(selected_rank == 1)),
        _cand("GBPUSD", 2, selected=(selected_rank == 2)),
    ]
    ranking = _cycle(i, ts, cands)
    decisions = [
        _decision(opp1, "EURUSD", i, f"COR-{i}-1", ts),
        _decision(opp2, "GBPUSD", i, f"COR-{i}-2", ts),
    ]
    shadows = [_shadow(opp1, rank1_r), _shadow(opp2, rank2_r)]
    return ranking, decisions, shadows


def _population(n=120, *, selected_rank=1, competitive=True):
    """n cycles. When competitive, the selected candidate matches the rank-1
    comparator's outcome (regret ~0); otherwise selected (rank2) underperforms."""
    rankings, decisions, shadows = [], [], []
    for i in range(n):
        if competitive:
            # selected == rank1, both outcomes equal -> regret 0.
            rank1_r, rank2_r = (1.0, -1.0)
            sr = 1
        else:
            # selected == rank2 and underperforms the rank1 comparator.
            rank1_r, rank2_r = (1.0, -1.0)
            sr = 2
        ranking, ds, ss = _two_candidate_cycle(i, selected_rank=sr, rank1_r=rank1_r, rank2_r=rank2_r)
        rankings.append(ranking)
        decisions.extend(ds)
        shadows.extend(ss)
    return rankings, decisions, shadows


def _run(rankings, decisions, shadows):
    with mock.patch.object(pr, "persist_report", lambda *a, **k: None), \
         mock.patch.object(pr, "update_knowledge_map", lambda *a, **k: None), \
         mock.patch.object(pr, "load_quarantine_boundary", _boundary):
        return pr.run_port_1(rankings, decisions, [], shadows)


# 1 + 2 + 3. Hypothesis / population / unit explicit.
def test_port1_canonical_contract_explicit():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    overall = report["overall"]
    assert overall["canonical_question"] == "PORT-1"
    assert "competitive" in overall["hypothesis"].lower()
    assert "one valid portfolio selection cycle" in overall["unit_of_analysis"]


# 4 + 5 + 6 + 7 + 8. Selected identity comes from pre-outcome authority and
# cannot be substituted by rank==1 / first row / execution / realised outcome.
def test_selected_identity_is_selection_status_not_rank_or_outcome():
    rankings, decisions, shadows = _population(2, competitive=False)  # selected=rank2
    cycles, _ = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    # Selected opportunity is the rank-2 GBPUSD (selection_status SELECTED),
    # NOT rank-1, NOT the first candidate row, NOT the higher-outcome one.
    assert all(c["selected_opportunity_id"].startswith("GBPUSD") for c in cycles)
    assert all(c["comparator_opportunity_id"].startswith("EURUSD") for c in cycles)


def test_no_selected_status_means_no_selected_cycle():
    ts = "2026-01-01T00:00:00+00:00"
    # No candidate has SELECTED -> cycle has no selected identity.
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1), _cand("GBPUSD", 2)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    shadows = [_shadow("EURUSD*1*P", 1.0), _shadow("GBPUSD*1*P", -1.0)]
    cycles, diagnostics = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert cycles == []
    assert diagnostics["cycles_with_selection"] == 0


# 9 + 10. Best comparator is pre-outcome rank_position==1, not future best R.
def test_comparator_is_rank1_not_future_best_outcome():
    ts = "2026-01-01T00:00:00+00:00"
    # rank-1 has a WORSE outcome than rank-2; comparator must still be rank-1.
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 2)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    shadows = [_shadow("EURUSD*1*P", -1.0), _shadow("GBPUSD*1*P", 5.0)]
    cycles, _ = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert len(cycles) == 1
    assert cycles[0]["comparator_opportunity_id"].startswith("EURUSD")  # rank1, not the 5.0 winner
    assert cycles[0]["comparator_r"] == -1.0


# 11 + 12 + 13. Selected and comparator paired within same cycle; correct opps.
def test_selected_and_comparator_paired_same_cycle():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_cycle(7, ts, [_cand("EURUSD", 1), _cand("GBPUSD", 2, selected=True)])]
    decisions = [_decision("EURUSD*7*P", "EURUSD", 7, "C1", ts), _decision("GBPUSD*7*P", "GBPUSD", 7, "C2", ts)]
    shadows = [_shadow("EURUSD*7*P", 2.0), _shadow("GBPUSD*7*P", 0.5)]
    cycles, _ = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert len(cycles) == 1
    c = cycles[0]
    assert c["selected_opportunity_id"] == "GBPUSD*7*P" and c["selected_r"] == 0.5
    assert c["comparator_opportunity_id"] == "EURUSD*7*P" and c["comparator_r"] == 2.0


# 14 + 15 + 16. Missing selected/comparator outcome excludes the cycle; never imputed.
def test_missing_outcome_excludes_cycle():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 2)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    # Selected (rank1 EURUSD) has no outcome -> cycle excluded.
    shadows = [_shadow("EURUSD*1*P", None), _shadow("GBPUSD*1*P", 1.0)]
    cycles, diagnostics = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert cycles == []
    assert diagnostics["incomplete_missing_outcome_cycles"] == 1


# 17 + 18. Account fanout / repeated horizons cannot inflate cycle n.
def test_fanout_and_horizons_do_not_inflate_cycle_n():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 2)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    dup = _shadow("EURUSD*1*P", 1.0); dup["identity"]["shadow_type"] = "ALT"
    shadows = [_shadow("EURUSD*1*P", 1.0), dup, _shadow("GBPUSD*1*P", -1.0)]
    cycles, diagnostics = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert len(cycles) == 1
    assert diagnostics["paired_cycles"] == 1


# 19 + 20. Conflicting selected / comparator identity fails closed.
def test_conflicting_selected_identity_fails_closed():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 2, selected=True)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    shadows = [_shadow("EURUSD*1*P", 1.0), _shadow("GBPUSD*1*P", -1.0)]
    cycles, diagnostics = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert cycles == []
    assert "1" in diagnostics["conflicting_cycles"]


def test_conflicting_comparator_identity_fails_closed():
    ts = "2026-01-01T00:00:00+00:00"
    # Two rank-1 candidates -> ambiguous best-available comparator.
    rankings = [_cycle(1, ts, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 1)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts)]
    shadows = [_shadow("EURUSD*1*P", 1.0), _shadow("GBPUSD*1*P", -1.0)]
    cycles, diagnostics = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    assert cycles == []
    assert "1" in diagnostics["conflicting_cycles"]


# 21 + 22 + 23 + 24. Regret sign/units; mean/median; selected-vs-comparator diff.
def test_regret_sign_units_and_stats_correct():
    ts0, ts1 = "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"
    # Cycle A: selected rank2 (R=0.0), comparator rank1 (R=2.0) -> regret +2.0
    rA = _cycle(1, ts0, [_cand("EURUSD", 1), _cand("GBPUSD", 2, selected=True)])
    dA = [_decision("EURUSD*1*P", "EURUSD", 1, "C1", ts0), _decision("GBPUSD*1*P", "GBPUSD", 1, "C2", ts0)]
    sA = [_shadow("EURUSD*1*P", 2.0), _shadow("GBPUSD*1*P", 0.0)]
    # Cycle B: selected rank1 (R=1.0) == comparator rank1 -> regret 0.0
    rB = _cycle(2, ts1, [_cand("EURUSD", 1, selected=True), _cand("GBPUSD", 2)])
    dB = [_decision("EURUSD*2*P", "EURUSD", 2, "C3", ts1), _decision("GBPUSD*2*P", "GBPUSD", 2, "C4", ts1)]
    sB = [_shadow("EURUSD*2*P", 1.0), _shadow("GBPUSD*2*P", -1.0)]
    cycles, _ = build_selection_cycles([rA, rB], dA + dB, [], sA + sB, _boundary())
    regrets = sorted(c["selection_regret_r"] for c in cycles)
    assert regrets == [0.0, 2.0]  # comparator_r - selected_r
    stats = _port1_stats(cycles)
    assert stats["mean_regret_r"] == pytest.approx(1.0)
    assert stats["median_regret_r"] == pytest.approx(1.0)
    assert stats["selected_minus_comparator_mean_r"] == pytest.approx(-1.0)


# 25. Competitiveness metric explicitly versioned.
def test_competitiveness_metric_versioned():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    metric = report["overall"]["competitiveness_metric"]
    assert metric["version"] == PORT1_COMPETITIVENESS_VERSION
    assert metric["tolerance_r"] == _PORT1_REGRET_TOLERANCE_R
    assert "not tuned on validation" in metric["tolerance_provenance"]


# 26 + 27. Chronology deterministic; validation cannot tune its own threshold.
def test_chronology_deterministic_and_fixed_tolerance():
    rankings, decisions, shadows = _population()
    cycles, _ = build_selection_cycles(rankings, decisions, [], shadows, _boundary())
    discovery, validation = _port1_split(cycles)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(c["_order"] for c in discovery) < min(c["_order"] for c in validation)
    # Tolerance is a module constant, not derived from validation.
    import research_engine.experiments.portfolio_ranking as m
    assert isinstance(m._PORT1_REGRET_TOLERANCE_R, float)


# 28. Insufficient sample -> WAITING_DATA/equivalent.
def test_insufficient_sample_waits():
    rankings, decisions, shadows = _population(20)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "INSUFFICIENT_DATA"


# 29. Negative selection result (regret) can still COMPLETE.
def test_regret_result_can_complete():
    rankings, decisions, shadows = _population(160, competitive=False)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {
        "SELECTION_REGRET_SIGNAL", "NO_RELIABLE_SELECTION_ADVANTAGE", "DISCOVERY_ONLY",
    }


# SELECTION_COMPETITIVE reachable.
def test_selection_competitive_reachable():
    rankings, decisions, shadows = _population(160, competitive=True)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "SELECTION_COMPETITIVE"


# 30 + 31. D6 report cannot complete PORT-1 and vice versa (distinct owners).
def test_reports_cannot_cross_complete():
    d6, port1 = REGISTRY_BY_ID["D6"], REGISTRY_BY_ID["PORT-1"]
    assert d6.report_filename == "d6_portfolio_ranking.json"
    assert port1.report_filename == PORT1_REPORT_FILENAME == "port1_portfolio_selection.json"
    assert d6.report_filename != port1.report_filename
    assert sum(q.report_filename == port1.report_filename for q in REGISTRY) == 1
    assert sum(q.report_filename == d6.report_filename for q in REGISTRY) == 1
    assert d6.runner_function != port1.runner_function


# Unique PORT-1 report ownership + VALID definition.
def test_port1_unique_report_and_complete_definition():
    question = REGISTRY_BY_ID["PORT-1"]
    assert question.runner_function == "run_port_1"
    assert question.report_filename == PORT1_REPORT_FILENAME
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["PORT-1"]) == "VALID"


# PORT-1 runner answers PORT-1 (selection), not D6 (rank ordering).
def test_port1_runner_answers_selection_not_ranking():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    assert report["overall"]["canonical_question"] == "PORT-1"
    assert "competitiveness_metric" in report["overall"]
    assert "rank_signal" not in report["overall"]  # that is D6's headline


# 32 + 33 + 34 + 35 + 36 + 37. Ledger 38/70; D6 op; OPP-1/P1 non-op; RW4 in progress; RW1-3 complete.
def test_ledger_derives_38_and_rw4_in_progress():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (38, 32)
    assert MASTER_REPAIR_LEDGER["PORT-1"].structurally_operational
    assert MASTER_REPAIR_LEDGER["D6"].structurally_operational
    assert REPAIR_WAVES["RW4"].implemented is False
    assert set(REPAIR_WAVES["RW4"].direct_gain) == {"OPP-1", "P1"}
    for qid in ("OPP-1", "P1"):
        assert qid in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "PORT-1" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    for wave_id in ("RW1", "RW2", "RW3"):
        assert REPAIR_WAVES[wave_id].implemented is True


# 38 (proxy). Existing operational questions remain operational.
def test_existing_operational_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D6", "D2", "D3", "D4", "D5", "X5", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational
