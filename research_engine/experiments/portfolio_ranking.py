"""
D6 — Portfolio Ranking Quality (materially depends on portfolio_rankings V1).
PORT-1 — Portfolio Selection Quality.

Question D6:
    Does the portfolio ranker's ordering of candidates (by rank_position)
    match realised/shadow outcomes — i.e., do higher-ranked candidates
    genuinely outperform lower-ranked ones?

Question PORT-1:
    Did the portfolio/ranking layer select the best available opportunity
    from the ranked candidate set?

Both materially depend on ``portfolio_rankings`` as their PRIMARY evidence.
Shadow outcomes are joined from decision_ledger -> trade_truth / shadow_runtime
for outcome-bearing analysis.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.data_access.loaders import (
    load_decision_ledger,
    load_portfolio_rankings,
    load_trade_truth,
)
from research_engine.data_access.shadow_runtime_ingestion import (
    ingest_completed_shadow_trades,
)
from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    compute_confidence,
    persist_report,
    update_knowledge_map,
)
from research_engine.experiments.selection_analysis import (
    QuarantineBoundary,
    build_decision_index,
    build_selection_report,
    decision_key,
    deep_get,
    group_stats,
    index_by,
    join_candidate_outcome,
    load_quarantine_boundary,
    numeric,
    outcome_value,
)

_MIN_RANKING_ROWS = 10
_MIN_RANKED_CANDIDATES = 30
_MIN_SELECTION_CYCLES = 5

# ==============================================================================
# D6 - Portfolio Ranking Quality
# ==============================================================================


def run_portfolio_ranking(
    portfolio_rankings: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    trade_truth: list[dict[str, Any]] | None = None,
    shadow_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    D6: Portfolio Ranking Quality.

    PRIMARY evidence = ``portfolio_rankings``. The computation materially
    DEPENDS on portfolio_rankings rows - it reads cycle_id, rank_position,
    selection_status, rank_score, and each candidate's symbol/pattern/ev
    FROM portfolio_rankings. Shadow outcomes are joined where available.

    NO other dataset can substitute for portfolio_rankings here.
    """
    if portfolio_rankings is None:
        portfolio_rankings = load_portfolio_rankings()
    if decisions is None:
        decisions = load_decision_ledger()
    if trade_truth is None:
        trade_truth = load_trade_truth()
    if shadow_trades is None:
        shadow_trades = ingest_completed_shadow_trades()

    boundary = load_quarantine_boundary()

    # Prove portfolio_rankings was materially read
    if not portfolio_rankings:
        return build_report(
            question_id="D6", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": "No portfolio_rankings records available. "
                     "D6 depends on portfolio_rankings V1 as primary evidence.",
                     "portfolio_rankings_read": 0},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": 0, "portfolio_rankings_read": 0},
            fingerprint=build_fingerprint(0, 0, source="portfolio_rankings"),
            recommendation="WAIT",
            warnings=["No portfolio_rankings evidence - D6 cannot answer without it"],
        )

    ranking_rows = len(portfolio_rankings)
    total_candidate_rows = sum(len(r.get("candidates", [])) for r in portfolio_rankings)

    if total_candidate_rows < _MIN_RANKED_CANDIDATES:
        return build_report(
            question_id="D6", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={
                "reason": f"Only {total_candidate_rows} ranked candidates (need {_MIN_RANKED_CANDIDATES})",
                "portfolio_rankings_read": ranking_rows, "candidate_rows": total_candidate_rows,
            },
            confidence="INSUFFICIENT_DATA",
            dataset={
                "portfolio_rankings_read": ranking_rows,
                "candidate_rows": total_candidate_rows,
            },
            fingerprint=build_fingerprint(total_candidate_rows, 0, source="portfolio_rankings"),
            recommendation="WAIT",
        )

    decision_idx = build_decision_index(decisions)
    truth_idx = index_by(trade_truth, lambda r: str(
        deep_get(r, "identity", "correlation_id") or r.get("correlation_id", "")
    ) or "")

    rank_distribution: dict[int, int] = {}
    selected_positions: list[int] = []
    per_rank_r: dict[int, list[float]] = {}
    selection_regrets: list[float] = []
    best_vs_selected_correct = 0
    evaluable_selection_cycles = 0

    for ranking in portfolio_rankings:
        cycle_id = ranking.get("cycle_id")
        candidates = ranking.get("candidates", [])
        if not candidates:
            continue

        selected_candidate = None
        ranked_list: list[dict[str, Any]] = []

        for cand in candidates:
            pos = cand.get("rank_position", 0)
            rank_distribution[pos] = rank_distribution.get(pos, 0) + 1

            outcome = join_candidate_outcome(
                cand, ranking, decision_idx, truth_idx, shadow_trades, boundary,
            )
            cand["_outcome"] = outcome
            ranked_list.append(cand)

            if cand.get("selection_status") == "SELECTED":
                selected_candidate = cand

            r = outcome_value(cand, precedence="shadow")
            if r is not None:
                per_rank_r.setdefault(pos, []).append(r)

        if selected_candidate:
            sel_pos = selected_candidate.get("rank_position", 0)
            selected_positions.append(sel_pos)

            outcomes_with_r = [
                (c, outcome_value(c, precedence="shadow"))
                for c in ranked_list
                if c.get("_outcome", {}).get("has_shadow") or c.get("_outcome", {}).get("has_live")
            ]
            if len(outcomes_with_r) >= 2:
                evaluable_selection_cycles += 1
                best_outcome = max(outcomes_with_r, key=lambda x: x[1] if x[1] is not None else -999)
                selected_r = outcome_value(selected_candidate, precedence="shadow")
                best_r = best_outcome[1]
                if best_r is not None and selected_r is not None:
                    selection_regrets.append(best_r - selected_r)
                    if selected_r >= best_r - 0.01:
                        best_vs_selected_correct += 1

    n_selected = len(selected_positions)
    n_with_regret = len(selection_regrets)
    top1_selection_rate = sum(1 for p in selected_positions if p == 1) / max(n_selected, 1)
    avg_regret = statistics.mean(selection_regrets) if selection_regrets else None
    ranking_accuracy = best_vs_selected_correct / max(evaluable_selection_cycles, 1)

    rank_bucket_expectancy: dict[str, Any] = {}
    for pos in sorted(per_rank_r.keys()):
        rs = per_rank_r[pos]
        rank_bucket_expectancy[str(pos)] = {
            "n": len(rs),
            "mean_r": round(sum(rs) / len(rs), 4),
            "win_rate": round(sum(1 for v in rs if v > 0) / len(rs), 4),
        }

    has_shadow = sum(
        1 for c in _iter_candidates(portfolio_rankings)
        if c.get("_outcome", {}).get("has_shadow")
    )
    has_live = sum(
        1 for c in _iter_candidates(portfolio_rankings)
        if c.get("_outcome", {}).get("has_live")
    )
    matched_decision = sum(
        1 for c in _iter_candidates(portfolio_rankings)
        if c.get("_outcome", {}).get("matched_decision")
    )

    overall = {
        "portfolio_rankings_read": ranking_rows,
        "total_candidate_rows": total_candidate_rows,
        "ranking_cycles": ranking_rows,
        "selected_count": n_selected,
        "top1_selection_rate": round(top1_selection_rate, 4),
        "accuracy_best_selected": round(ranking_accuracy, 4),
        "avg_selection_regret": round(avg_regret, 4) if avg_regret is not None else None,
        "regret_evaluable_cycles": evaluable_selection_cycles,
        "rank_distribution": dict(sorted(rank_distribution.items())),
        "selected_rank_distribution": _bucket_positions(selected_positions),
        "per_rank_outcome": rank_bucket_expectancy,
        "outcome_join_coverage": {
            "matched_decision": matched_decision,
            "has_shadow_outcome": has_shadow,
            "has_live_outcome": has_live,
            "missing_outcome": total_candidate_rows - has_shadow,
        },
    }

    confidence = compute_confidence(n_selected, ranking_accuracy > 0.60)
    n_joinable = has_shadow + has_live
    if n_joinable < 5:
        confidence = "LOW"
        recommendation = "WAIT"
        finding = f"Insufficient outcome-bearing ranking pairs ({n_joinable})"
    elif ranking_accuracy >= 0.70 and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = (
            f"Ranking accuracy {ranking_accuracy:.0%}. "
            f"Top-1 selected {top1_selection_rate:.0%} of the time. "
            "Avg regret " + (f"{avg_regret:+.3f}R." if avg_regret is not None else "N/A.")
        )
    elif ranking_accuracy >= 0.50:
        recommendation = "MONITOR"
        finding = f"Ranking accuracy {ranking_accuracy:.0%} is fair. Avg regret " + (f"{avg_regret:+.3f}R." if avg_regret is not None else "N/A.")
    else:
        recommendation = "RECALIBRATE"
        finding = f"Ranking accuracy {ranking_accuracy:.0%} is poor. Avg regret " + (f"{avg_regret:+.3f}R." if avg_regret is not None else "N/A.")

    report = build_report(
        question_id="D6", status=ReadinessStatus.COMPLETE,
        overall=overall, confidence=confidence,
        dataset={
            "portfolio_rankings_rows": ranking_rows,
            "ranking_cycles": ranking_rows,
            "total_candidate_rows": total_candidate_rows,
            "selected_count": n_selected,
            "outcome_bearing_candidates": n_joinable,
            "quarantine_boundary": boundary.describe(),
        },
        fingerprint=build_fingerprint(
            total_candidate_rows, 0, source="portfolio_rankings",
            validation_score=confidence, epoch="CURRENT",
        ),
        recommendation=recommendation,
        assumptions=[
            "Primary evidence = portfolio_rankings V1 (required)",
            "Outcomes joined via decision_ledger (cycle_id, symbol) -> trade_truth / shadow_runtime",
            "Shadow outcome preferred for cross-candidate comparability",
            "Selection regret = best candidate shadow R - selected R (same cycle)",
            "Quarantine boundary applied to decision timestamps for contamination classification",
        ],
        provenance={
            "experiment_module": "research_engine.experiments.portfolio_ranking",
            "registry_id": "D6",
            "function": "run_portfolio_ranking",
            "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
        },
    )

    persist_report(report, "d6_portfolio_ranking.json")
    update_knowledge_map("D6", finding, recommendation)
    return report


# ==============================================================================
# PORT-1 - Portfolio Selection Quality
# ==============================================================================


def run_port_1(
    portfolio_rankings: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    trade_truth: list[dict[str, Any]] | None = None,
    shadow_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    PORT-1: Portfolio selection quality.

    Evaluates whether the SELECTED candidate (from each ranking cycle)
    was genuinely the best available choice. This is a portfolio-LEVEL
    question about the ranker's selection vs. the full candidate pool.

    PRIMARY evidence: portfolio_rankings. Materially depends on it.
    """
    if portfolio_rankings is None:
        portfolio_rankings = load_portfolio_rankings()
    if decisions is None:
        decisions = load_decision_ledger()
    if trade_truth is None:
        trade_truth = load_trade_truth()
    if shadow_trades is None:
        shadow_trades = ingest_completed_shadow_trades()

    boundary = load_quarantine_boundary()

    if not portfolio_rankings:
        return build_selection_report(
            question_id="PORT-1", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": "No portfolio_rankings evidence"},
            confidence="INSUFFICIENT_DATA",
            dataset={"portfolio_rankings_read": 0, "outcome_candidates": 0},
            recommendation="WAIT", source="portfolio_rankings",
            records_used=0, records_excluded=0,
            warnings=["No portfolio_rankings - PORT-1 cannot answer"],
            module="research_engine.experiments.portfolio_ranking",
        )

    decision_idx = build_decision_index(decisions)
    truth_idx = index_by(trade_truth, lambda r: str(
        deep_get(r, "identity", "correlation_id") or r.get("correlation_id", "")
    ) or "")

    cycles_with_selection = 0
    cycles_with_outcome = 0
    selected_best = 0
    selected_worse_than_rejected = 0
    regrets: list[float] = []
    selected_rs: list[float] = []
    best_available_rs: list[float] = []
    unselected_rs: list[float] = []

    for ranking in portfolio_rankings:
        cycle_id = ranking.get("cycle_id")
        candidates = ranking.get("candidates", [])
        if not candidates:
            continue

        selected_cand = None
        all_with_r: list[tuple[dict[str, Any], float]] = []

        for cand in candidates:
            outcome = join_candidate_outcome(
                cand, ranking, decision_idx, truth_idx, shadow_trades, boundary,
            )
            cand["_outcome"] = outcome
            r = outcome_value(cand, precedence="shadow")
            if r is not None:
                all_with_r.append((cand, r))
            if cand.get("selection_status") == "SELECTED":
                selected_cand = cand

        if not all_with_r:
            continue

        cycles_with_outcome += 1
        if selected_cand:
            cycles_with_selection += 1
            best_r = max(all_with_r, key=lambda x: x[1])[1]
            sel_r = outcome_value(selected_cand, precedence="shadow")
            if sel_r is not None:
                best_available_rs.append(best_r)
                selected_rs.append(sel_r)
                regrets.append(best_r - sel_r)
                if sel_r >= best_r - 0.001:
                    selected_best += 1
                if sel_r < best_r - 0.1:
                    selected_worse_than_rejected += 1

    n_with_data = len(selected_rs)
    n_regrets = len(regrets)

    if n_with_data < _MIN_SELECTION_CYCLES:
        return build_selection_report(
            question_id="PORT-1", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={
                "reason": f"Only {n_with_data} selection cycles with outcome evidence (need {_MIN_SELECTION_CYCLES})",
                "total_ranking_rows": len(portfolio_rankings),
                "outcome_bearing_cycles": cycles_with_outcome,
            },
            confidence="INSUFFICIENT_DATA",
            dataset={
                "portfolio_rankings_read": len(portfolio_rankings),
                "outcome_candidates": n_with_data,
            },
            recommendation="WAIT", source="portfolio_rankings",
            records_used=n_with_data,
            records_excluded=_total_candidates(portfolio_rankings) - n_with_data,
            warnings=[f"Insufficient outcome-bearing selection cycles: {n_with_data}"],
            module="research_engine.experiments.portfolio_ranking",
        )

    avg_regret = statistics.mean(regrets) if regrets else None
    selection_top1 = 0
    total_sel = 0
    for r in portfolio_rankings:
        for c in r.get("candidates", []):
            if c.get("selection_status") == "SELECTED":
                total_sel += 1
                if c.get("rank_position") == 1:
                    selection_top1 += 1
    top1_selection_rate = selection_top1 / max(total_sel, 1)

    overall = {
        "portfolio_rankings_read": len(portfolio_rankings),
        "total_candidates": _total_candidates(portfolio_rankings),
        "cycles_with_rankings": len(portfolio_rankings),
        "outcome_bearing_cycles": cycles_with_outcome,
        "selection_cycles_with_outcome": n_with_data,
        "top1_selection_rate": round(top1_selection_rate, 4),
        "selected_best_in_cycle_rate": round(selected_best / max(n_with_data, 1), 4),
        "selected_worse_than_rejected_rate": round(
            selected_worse_than_rejected / max(n_with_data, 1), 4,
        ),
        "avg_selection_regret": round(avg_regret, 4) if avg_regret is not None else None,
        "selected_outcome": group_stats(selected_rs),
        "best_available_outcome": group_stats(best_available_rs),
        "selected_vs_best_delta": {
            "mean_delta": round(avg_regret, 4) if avg_regret is not None else None,
            "n_comparable": n_regrets,
        },
        "quarantine_boundary": boundary.describe(),
    }

    confidence = compute_confidence(n_with_data, selected_best / max(n_with_data, 1) > 0.60)

    if confidence == "INSUFFICIENT_DATA":
        recommendation = "WAIT"
    elif selected_best / max(n_with_data, 1) >= 0.75 and avg_regret is not None and avg_regret <= 0.1:
        recommendation = "PROMOTE"
    elif selected_best / max(n_with_data, 1) >= 0.50:
        recommendation = "MONITOR"
    else:
        recommendation = "REJECT"

    report = build_selection_report(
        question_id="PORT-1", status=ReadinessStatus.COMPLETE,
        overall=overall, confidence=confidence,
        dataset={
            "portfolio_rankings_read": len(portfolio_rankings),
            "total_candidates": _total_candidates(portfolio_rankings),
            "outcome_bearing_candidates": n_with_data,
            "quarantine_boundary": boundary.describe(),
        },
        recommendation=recommendation, source="portfolio_rankings",
        records_used=n_with_data,
        records_excluded=_total_candidates(portfolio_rankings) - n_with_data,
        assumptions=[
            "Primary evidence = portfolio_rankings V1",
            "Outcome via decision_ledger bridge (cycle_id, symbol)",
            "Shadow outcome preferred - ensures rejected candidates are comparable",
            "Selection regret = best candidate shadow R - selected candidate shadow R",
            "Portfolio-level question about ranker selection quality",
        ],
        module="research_engine.experiments.portfolio_ranking",
    )

    persist_report(report, "port1_portfolio_selection.json")
    update_knowledge_map("PORT-1",
        f"Selected best in {selected_best}/{n_with_data} cycles. "
        f"Avg regret " + (f"{avg_regret:+.3f}R." if avg_regret is not None else "N/A.") + f" Top-1 rate {top1_selection_rate:.0%}.",
        recommendation)
    return report


# ==============================================================================
# INTERNAL HELPERS
# ==============================================================================


def _iter_candidates(data: list[dict[str, Any]]) -> Any:
    for ranking in data:
        for cand in ranking.get("candidates", []):
            yield cand


def _total_candidates(data: list[dict[str, Any]]) -> int:
    return sum(1 for _ in _iter_candidates(data))


def _bucket_positions(positions: list[int]) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for pos in positions:
        if pos == 1:
            buckets["rank_1"] = buckets.get("rank_1", 0) + 1
        elif pos == 2:
            buckets["rank_2"] = buckets.get("rank_2", 0) + 1
        elif pos == 3:
            buckets["rank_3"] = buckets.get("rank_3", 0) + 1
        elif pos <= 10:
            buckets["rank_4_10"] = buckets.get("rank_4_10", 0) + 1
        else:
            buckets["rank_10+"] = buckets.get("rank_10+", 0) + 1
    return buckets