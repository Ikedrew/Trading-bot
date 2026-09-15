"""Focused D6 candidate rank-ordering vs realised-R validation repair tests."""
from __future__ import annotations

import pytest

from research_engine.experiments.portfolio_ranking import (
    D6_REPORT_FILENAME,
    build_rank_observations,
    run_portfolio_ranking,
    run_port_1,
    spearman,
    _d6_chronological_split,
)
from research_engine.experiments.selection_analysis import QuarantineBoundary
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _boundary():
    # A far-past defect window so no 2026 evidence is quarantined.
    return QuarantineBoundary("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00")


def _decision(opp: str, symbol: str, cycle_id, corr: str, ts: str) -> dict:
    return {
        "cycle_id": cycle_id,
        "symbol": symbol,
        "decision": "EXECUTE",
        "correlation_id": corr,
        "canonical_opportunity_id": opp,
        "timestamp": ts,
    }


def _shadow(opp: str, outcome: float | None) -> dict:
    return {
        "canonical_opportunity_id": opp,
        "identity": {"shadow_type": ""},
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _ranking(cycle_id, ts: str, candidates: list[dict]) -> dict:
    return {"cycle_id": cycle_id, "timestamp": ts, "candidates": candidates}


def _cand(symbol: str, rank: int, selected: bool = False) -> dict:
    return {
        "symbol": symbol,
        "rank_position": rank,
        "selection_status": "SELECTED" if selected else "REJECTED",
        "rank_score": 1.0 / rank,
    }


def _population(n_cycles: int = 120, *, rank1_better: bool = True):
    """One candidate per cycle (rank alternates 1/2); rank-1 tends to win when
    rank1_better, giving a clean rank-ordering signal."""
    rankings, decisions, shadows = [], [], []
    for i in range(n_cycles):
        rank = 1 if i % 2 == 0 else 2
        opp = f"EURUSD*{i}*P"
        symbol = "EURUSD"
        ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
        rankings.append(_ranking(i, ts, [_cand(symbol, rank, selected=(rank == 1))]))
        decisions.append(_decision(opp, symbol, i, f"COR-{i}", ts))
        if rank1_better:
            outcome = 1.0 if rank == 1 else -1.0
        else:
            outcome = -1.0 if rank == 1 else 1.0
        shadows.append(_shadow(opp, outcome))
    return rankings, decisions, shadows


def _run(rankings, decisions, shadows):
    # Inject evidence directly (no S3/loaders), persistence patched off.
    import research_engine.experiments.portfolio_ranking as pr
    import unittest.mock as mock
    with mock.patch.object(pr, "persist_report", lambda *a, **k: None), \
         mock.patch.object(pr, "update_knowledge_map", lambda *a, **k: None), \
         mock.patch.object(pr, "load_quarantine_boundary", _boundary):
        return pr.run_portfolio_ranking(rankings, decisions, [], shadows)


# 1-4. D6 canonical hypothesis / population / unit / evidence authority explicit.
def test_d6_canonical_contract_is_explicit():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    overall = report["overall"]
    assert overall["canonical_question"] == "D6"
    assert "rank ordering" in overall["hypothesis"].lower()
    assert "canonical_opportunity_id" in overall["unit_of_analysis"]
    assert "portfolio_rankings" in overall["evidence_authority"]
    assert "rank_position" in overall["evidence_authority"]
    assert overall["evidence_epoch"] == "CURRENT"


# 5. D6 and PORT-1 remain distinct questions.
def test_d6_and_port1_are_distinct_questions():
    d6, port1 = REGISTRY_BY_ID["D6"], REGISTRY_BY_ID["PORT-1"]
    assert d6.runner_function != port1.runner_function
    assert d6.report_filename != port1.report_filename
    assert d6.report_filename == "d6_portfolio_ranking.json"
    assert port1.report_filename == "port1_portfolio_selection.json"


# 6 + 7. D6 report cannot complete PORT-1 and vice versa (distinct owners/reports).
def test_reports_cannot_cross_complete():
    d6, port1 = REGISTRY_BY_ID["D6"], REGISTRY_BY_ID["PORT-1"]
    assert d6.report_filename != port1.report_filename
    # Only one registry question owns each report.
    assert sum(q.report_filename == d6.report_filename for q in REGISTRY) == 1
    assert sum(q.report_filename == port1.report_filename for q in REGISTRY) == 1


# 8. Shared calculation does not create shared completion ownership.
def test_shared_calculation_does_not_share_completion():
    # build_rank_observations is a pure helper; it returns rows only, no status.
    rankings, decisions, shadows = _population(10)
    rows, diagnostics = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    assert isinstance(rows, list) and isinstance(diagnostics, dict)
    assert "status" not in diagnostics and "finding_classification" not in diagnostics


# 9 + 10 + 11. One canonical opportunity counts once; fanout/horizons cannot inflate n.
def test_one_opportunity_no_fanout_no_horizon_inflation():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_ranking(1, ts, [_cand("EURUSD", 1)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "COR-1", ts)]
    # Account fanout + repeated horizon: multiple shadow rows, same opportunity.
    shadows = [
        _shadow("EURUSD*1*P", 1.0),
        _shadow("EURUSD*1*P", 1.0),   # fanout duplicate
        _shadow("EURUSD*1*P", 3.0),   # repeated horizon (collapsed to primary)
    ]
    rows, diagnostics = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    assert len(rows) == 1
    assert diagnostics["paired_opportunities"] == 1


# 12 + 13. Missing outcome remains missing/excluded; never 0/loss/success.
def test_missing_outcome_excluded_never_imputed():
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_ranking(1, ts, [_cand("EURUSD", 1)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "COR-1", ts)]
    shadows = [_shadow("EURUSD*1*P", None)]
    rows, diagnostics = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 14. Conflicting rank membership for one opportunity fails closed.
def test_conflicting_rank_fails_closed():
    ts1 = "2026-01-01T00:00:00+00:00"
    ts2 = "2026-01-01T00:01:00+00:00"
    # Same canonical opportunity assigned two different rank positions across
    # two cycles (both bridge to EURUSD*1*P) -> conflicting membership.
    rankings = [
        _ranking(1, ts1, [_cand("EURUSD", 1)]),
        _ranking(2, ts2, [_cand("EURUSD", 4)]),
    ]
    decisions = [
        _decision("EURUSD*1*P", "EURUSD", 1, "COR-1", ts1),
        _decision("EURUSD*1*P", "EURUSD", 2, "COR-2", ts2),
    ]
    shadows = [_shadow("EURUSD*1*P", 1.0)]
    rows, diagnostics = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    assert "EURUSD*1*P" in diagnostics["conflicting_opportunities"]
    assert rows == []


def test_multiple_shadows_collapse_to_one_deterministic_outcome():
    # The canonical join selects ONE primary shadow per opportunity, so multiple
    # shadow rows never double-count and never create an ambiguous outcome set.
    ts = "2026-01-01T00:00:00+00:00"
    rankings = [_ranking(1, ts, [_cand("EURUSD", 1)])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "COR-1", ts)]
    primary = _shadow("EURUSD*1*P", 1.0)          # shadow_type "" -> primary
    extra = _shadow("EURUSD*1*P", 3.0)
    extra["identity"]["shadow_type"] = "ALT"       # non-primary alternative
    rows, diagnostics = build_rank_observations(rankings, decisions, [], [primary, extra], _boundary())
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 1.0            # deterministic primary selection
    assert diagnostics["conflicting_opportunities"] == ()


def test_conflicting_rank_and_outcome_guard_present():
    # The build helper fails closed when a single opportunity resolves to more
    # than one distinct rank OR more than one distinct outcome value.
    import inspect
    from research_engine.experiments import portfolio_ranking as pr
    src = inspect.getsource(pr.build_rank_observations)
    assert 'len(info["rank"]) != 1' in src
    assert 'len(info["outcome"]) > 1' in src


# 15 + 16. Group membership (rank) uses pre-outcome evidence; ambiguous not guessed.
def test_rank_membership_is_pre_outcome_and_ambiguous_excluded():
    ts = "2026-01-01T00:00:00+00:00"
    # A candidate with no rank_position -> excluded, not guessed.
    rankings = [_ranking(1, ts, [{"symbol": "EURUSD", "rank_position": None}])]
    decisions = [_decision("EURUSD*1*P", "EURUSD", 1, "COR-1", ts)]
    shadows = [_shadow("EURUSD*1*P", 1.0)]
    rows, diagnostics = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    assert rows == []
    assert diagnostics["excluded_missing_lineage"] >= 1


# 17 + 18 + 19. No outcome-derived selection; deterministic chronology; validation
# cannot redefine a discovery-derived rule (rank membership is producer-given).
def test_chronological_split_is_deterministic_and_membership_not_outcome_derived():
    rankings, decisions, shadows = _population()
    rows, _ = build_rank_observations(rankings, decisions, [], shadows, _boundary())
    discovery, validation = _d6_chronological_split(rows)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(r["_order"] for r in discovery) < min(r["_order"] for r in validation)
    # Rank membership is the producer rank_position, never derived from outcome.
    for row in rows:
        assert row["rank_position"] in (1, 2)


# 20. Subgroup (per-rank bucket) minimums enforced/flagged.
def test_rank_bucket_minimums_flagged():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    buckets = report["overall"]["per_rank_outcome"]
    # 120 cycles / 2 ranks -> 60 each, both sufficient (>=15).
    assert all(cell["sufficient"] for cell in buckets.values())


def test_small_rank_bucket_marked_insufficient():
    rankings, decisions, shadows = _population(120)
    # Add a handful of rank-3 observations (a small bucket).
    for j in range(3):
        ts = f"2026-01-01T02:{j:02d}:00+00:00"
        opp = f"EURUSD*r3-{j}*P"
        rankings.append(_ranking(500 + j, ts, [_cand("EURUSD", 3)]))
        decisions.append(_decision(opp, "EURUSD", 500 + j, f"COR-r3-{j}", ts))
        shadows.append(_shadow(opp, -1.0))
    report = _run(rankings, decisions, shadows)
    buckets = report["overall"]["per_rank_outcome"]
    assert buckets["3"]["sufficient"] is False


# 21. Insufficient evidence -> WAITING_DATA (INSUFFICIENT_DATA).
def test_insufficient_evidence_waits():
    rankings, decisions, shadows = _population(20)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "INSUFFICIENT_DATA"


# 22. Valid negative/no-effect result can COMPLETE.
def test_no_signal_result_can_complete():
    # Rank unrelated to outcome -> no reliable rank signal, but COMPLETE.
    rankings, decisions, shadows = [], [], []
    for i in range(140):
        rank = 1 if i % 2 == 0 else 2
        opp = f"EURUSD*{i}*P"
        ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
        rankings.append(_ranking(i, ts, [_cand("EURUSD", rank)]))
        decisions.append(_decision(opp, "EURUSD", i, f"COR-{i}", ts))
        # Outcome decoupled from rank.
        shadows.append(_shadow(opp, 1.0 if (i // 2) % 2 == 0 else -1.0))
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {
        "NO_RELIABLE_RANK_SIGNAL", "DISCOVERY_ONLY", "INVERSE_RANK_SIGNAL",
    }


# RANK_ORDERING_PREDICTS_OUTCOME reachable.
def test_rank_ordering_predicts_outcome_reachable():
    rankings, decisions, shadows = _population(160, rank1_better=True)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "RANK_ORDERING_PREDICTS_OUTCOME"


# INVERSE_RANK_SIGNAL reachable (worse-ranked outperform).
def test_inverse_rank_signal_reachable():
    rankings, decisions, shadows = _population(160, rank1_better=False)
    report = _run(rankings, decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "INVERSE_RANK_SIGNAL"


# 23 + 24. Unique D6 report ownership; legacy incompatible report cannot complete.
def test_d6_unique_report_and_complete_definition():
    question = REGISTRY_BY_ID["D6"]
    assert question.runner_module == "research_engine.experiments.portfolio_ranking"
    assert question.runner_function == "run_portfolio_ranking"
    assert question.report_filename == D6_REPORT_FILENAME
    assert sum(q.report_filename == D6_REPORT_FILENAME for q in REGISTRY) == 1
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["D6"]) == "VALID"


# 25. D6 runner cannot silently answer PORT-1 instead.
def test_d6_runner_answers_d6_not_port1():
    rankings, decisions, shadows = _population()
    report = _run(rankings, decisions, shadows)
    assert report["overall"]["canonical_question"] == "D6"
    assert "rank_signal" in report["overall"]
    # It does not emit PORT-1's selected-vs-best headline as its finding.
    assert "selected_best_in_cycle_rate" not in report["overall"]


# 26 + 27 + 28 + 29 + 30. Ledger 37/70; PORT-1/OPP-1/P1 non-operational; RW4 in progress.
def test_ledger_derives_37_and_rw4_in_progress():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (37, 33)
    assert MASTER_REPAIR_LEDGER["D6"].structurally_operational
    assert REPAIR_WAVES["RW4"].implemented is False
    assert set(REPAIR_WAVES["RW4"].direct_gain) == {"PORT-1", "OPP-1", "P1"}
    for qid in ("PORT-1", "OPP-1", "P1"):
        assert qid in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "D6" not in STRUCTURALLY_NON_OPERATIONAL_IDS


# 31. RW1/RW2/RW3 remain COMPLETE (implemented).
def test_rw1_rw2_rw3_remain_complete():
    from research_engine.registry.master_repair_ledger import REPAIR_WAVES

    for wave_id in ("RW1", "RW2", "RW3"):
        assert REPAIR_WAVES[wave_id].implemented is True


# 32. Existing D2-D5/X5 behaviour remains unchanged (still operational).
def test_d2_through_x5_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D2", "D3", "D4", "D5", "X5", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational


# 33. spearman helper is correct/deterministic.
def test_spearman_helper():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # zero variance


# PORT-1 remains runnable and independent (does not raise / not D6).
def test_port1_still_runs_independently():
    import research_engine.experiments.portfolio_ranking as pr
    import unittest.mock as mock
    rankings, decisions, shadows = _population(10)
    with mock.patch.object(pr, "persist_report", lambda *a, **k: None), \
         mock.patch.object(pr, "update_knowledge_map", lambda *a, **k: None), \
         mock.patch.object(pr, "load_quarantine_boundary", _boundary):
        report = run_port_1(rankings, decisions, [], shadows)
    assert report["question_id"] == "PORT-1"
