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

# D6 canonical rank-ordering contract (distinct from PORT-1 selected-vs-best).
D6_REPORT_FILENAME = "d6_portfolio_ranking.json"
_D6_MIN_TOTAL = 100        # distinct paired canonical opportunities
_D6_MIN_DISCOVERY = 60
_D6_MIN_VALIDATION = 40
_D6_MIN_BUCKET = 15        # per rank bucket used for a directional claim
_D6_RANK_SIGNAL_MIN = 0.10  # |Spearman| below this = no reliable rank signal


def _d6_time(value: Any):
    """Parse a timestamp (iso or epoch) to a comparable UTC datetime or None."""
    from datetime import datetime, timezone
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Deterministic Spearman rank correlation; None if <3 or zero variance."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx = _average_ranks(xs)
    ry = _average_ranks(ys)
    n = len(xs)
    mean_rank = (n + 1) / 2.0
    cov = sum((rx[i] - mean_rank) * (ry[i] - mean_rank) for i in range(n))
    var_x = sum((r - mean_rank) ** 2 for r in rx)
    var_y = sum((r - mean_rank) ** 2 for r in ry)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    return cov / math.sqrt(var_x * var_y)


def build_rank_observations(
    portfolio_rankings: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    trade_truth: list[dict[str, Any]],
    shadow_trades: list[dict[str, Any]],
    boundary: "QuarantineBoundary",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """SHARED PURE CALCULATION: one rank-ordering observation per canonical
    opportunity, paired to its CURRENT shadow (counterfactual) realised R.

    This helper is deliberately interpretation-free so D6 (rank-ordering) and a
    future PORT-1 refactor could both reuse the pairing WITHOUT sharing question
    ownership or completion. It returns rows and diagnostics only.

    - Independent unit = one canonical_opportunity_id. Account fanout and
      repeated horizons collapse; conflicting rank_position or outcome for one
      canonical opportunity fails closed.
    - Missing outcome is excluded (never imputed to 0R/loss/success).
    - Ambiguous/missing canonical lineage is excluded, never guessed.
    """
    from collections import defaultdict

    decision_idx = build_decision_index(decisions)
    truth_idx = index_by(trade_truth, lambda r: str(
        deep_get(r, "identity", "correlation_id") or r.get("correlation_id", "")
    ) or "")

    # candidate rows grouped by canonical opportunity (pre-outcome rank/time)
    grouped: dict[str, dict[str, set]] = defaultdict(lambda: {"rank": set(), "time": set(), "outcome": set(), "cycles": set()})
    total_candidate_rows = 0
    excluded_no_lineage = 0
    for ranking in portfolio_rankings:
        for cand in ranking.get("candidates", []):
            total_candidate_rows += 1
            outcome = join_candidate_outcome(cand, ranking, decision_idx, truth_idx, shadow_trades, boundary)
            cand["_outcome"] = outcome
            opp = str(outcome.get("canonical_opportunity_id") or "")
            if not opp:
                excluded_no_lineage += 1
                continue
            pos = cand.get("rank_position")
            if pos is None:
                excluded_no_lineage += 1
                continue
            grouped[opp]["rank"].add(int(pos))
            grouped[opp]["cycles"].add(ranking.get("cycle_id"))
            ledger_time = outcome.get("decision_time") or ranking.get("timestamp") or ranking.get("cycle_id")
            grouped[opp]["time"].add(str(ledger_time))
            r = outcome_value(cand, precedence="shadow")
            if r is not None:
                grouped[opp]["outcome"].add(round(float(r), 6))

    rows: list[dict[str, Any]] = []
    conflicts: list[str] = []
    missing_outcome = 0
    for opp in sorted(grouped):
        info = grouped[opp]
        # Conflicting pre-outcome rank membership for one opportunity -> fail closed.
        if len(info["rank"]) != 1:
            conflicts.append(opp)
            continue
        # Conflicting realised outcomes (beyond one collapsed value) -> fail closed.
        if len(info["outcome"]) > 1:
            conflicts.append(opp)
            continue
        if len(info["outcome"]) == 0:
            missing_outcome += 1
            continue
        rank_position = next(iter(info["rank"]))
        outcome_r = next(iter(info["outcome"]))
        order_key = sorted(info["time"])[0]
        rows.append({
            "canonical_opportunity_id": opp,
            "rank_position": rank_position,
            "outcome_r": outcome_r,
            "won": 1.0 if outcome_r > 0 else 0.0,
            "_order": order_key,
        })
    rows.sort(key=lambda row: (row["_order"], row["canonical_opportunity_id"]))
    diagnostics = {
        "total_candidate_rows": total_candidate_rows,
        "distinct_opportunities": len(grouped),
        "paired_opportunities": len(rows),
        "missing_outcomes_excluded": missing_outcome,
        "excluded_missing_lineage": excluded_no_lineage,
        "conflicting_opportunities": tuple(sorted(set(conflicts))),
    }
    return rows, diagnostics


def _d6_chronological_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique = sorted({row["_order"] for row in rows})
    if len(unique) < 2:
        return rows, []
    index = max(1, min(len(unique) - 1, int(len(unique) * 0.60)))
    boundary_key = unique[index]
    return ([r for r in rows if r["_order"] < boundary_key],
            [r for r in rows if r["_order"] >= boundary_key])


def _d6_bucket_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import defaultdict
    cells: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        cells[row["rank_position"]].append(row["outcome_r"])
    result: dict[str, Any] = {}
    for pos in sorted(cells):
        values = cells[pos]
        result[str(pos)] = {
            "n": len(values),
            "mean_r": round(sum(values) / len(values), 4),
            "median_r": round(statistics.median(values), 4),
            "win_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
            "sufficient": len(values) >= _D6_MIN_BUCKET,
        }
    return result


def _d6_rank_signal(rows: list[dict[str, Any]]) -> float | None:
    """Spearman between BETTER rank (−rank_position) and realised R.

    Positive => better-ranked (lower rank_position) candidates realise higher R.
    """
    if len(rows) < 3:
        return None
    better_rank = [-float(row["rank_position"]) for row in rows]
    realised = [row["outcome_r"] for row in rows]
    return spearman(better_rank, realised)


def _d6_classify(status: str, discovery_signal: float | None, validation_signal: float | None) -> str:
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    if discovery_signal is None or validation_signal is None:
        return "NO_RELIABLE_RANK_SIGNAL"
    discovery_useful = abs(discovery_signal) >= _D6_RANK_SIGNAL_MIN
    if not discovery_useful:
        return "NO_RELIABLE_RANK_SIGNAL"
    # A materially negative validation signal is an inverse (harmful) ordering.
    if validation_signal <= -_D6_RANK_SIGNAL_MIN and discovery_signal > 0:
        return "INVERSE_RANK_SIGNAL"
    same_direction = (discovery_signal > 0) == (validation_signal > 0)
    if not same_direction or abs(validation_signal) < _D6_RANK_SIGNAL_MIN:
        return "DISCOVERY_ONLY"
    if discovery_signal > 0:
        return "RANK_ORDERING_PREDICTS_OUTCOME"
    return "INVERSE_RANK_SIGNAL"


# ==============================================================================
# D6 - Candidate rank-ordering predicts outcome (canonical; owns d6 report)
# ==============================================================================


def run_portfolio_ranking(
    portfolio_rankings: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    trade_truth: list[dict[str, Any]] | None = None,
    shadow_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    D6: Does candidate RANK ORDERING (by rank_position) predict subsequent
    realised/shadow R across rank positions — do higher-ranked candidates
    genuinely outperform lower-ranked ones, and does that persist on later
    unseen cycles?

    Distinct from PORT-1 (selected-vs-best). PRIMARY evidence =
    ``portfolio_rankings``; outcomes are joined per canonical opportunity.
    One canonical_opportunity_id is one independent observation; account fanout
    and repeated horizons collapse; missing outcomes are excluded. Research
    only — never modifies production ranking/selection.
    """
    if portfolio_rankings is None:
        portfolio_rankings = load_portfolio_rankings()
    if decisions is None:
        decisions = load_decision_ledger()
    if trade_truth is None:
        trade_truth = load_trade_truth()
    if shadow_trades is None:
        shadow_trades = ingest_completed_shadow_trades()

    if portfolio_rankings is None:
        portfolio_rankings = load_portfolio_rankings()
    if decisions is None:
        decisions = load_decision_ledger()
    if trade_truth is None:
        trade_truth = load_trade_truth()
    if shadow_trades is None:
        shadow_trades = ingest_completed_shadow_trades()

    boundary = load_quarantine_boundary()
    ranking_rows = len(portfolio_rankings)

    rows, diagnostics = build_rank_observations(
        portfolio_rankings, decisions, trade_truth, shadow_trades, boundary,
    )
    discovery, validation = _d6_chronological_split(rows)

    paired = len(rows)
    discovery_signal = _d6_rank_signal(discovery)
    validation_signal = _d6_rank_signal(validation)
    overall_signal = _d6_rank_signal(rows)
    all_r = [row["outcome_r"] for row in rows]

    if diagnostics["conflicting_opportunities"]:
        status = ReadinessStatus.BLOCKED
        reason = "Conflicting rank membership or outcome for a canonical opportunity"
    elif paired < _D6_MIN_TOTAL or len(discovery) < _D6_MIN_DISCOVERY or len(validation) < _D6_MIN_VALIDATION:
        status = ReadinessStatus.INSUFFICIENT_DATA
        reason = (
            f"paired/discovery/validation={paired}/{len(discovery)}/{len(validation)}; "
            f"need {_D6_MIN_TOTAL}/{_D6_MIN_DISCOVERY}/{_D6_MIN_VALIDATION}"
        )
    else:
        status = ReadinessStatus.COMPLETE
        reason = "Chronological candidate rank-ordering vs realised-R evaluation completed on later unseen cycles"

    status_name = getattr(status, "value", status)
    finding = _d6_classify(status_name if isinstance(status_name, str) else str(status), discovery_signal, validation_signal)

    overall = {
        "canonical_question": "D6",
        "hypothesis": "Candidate rank ordering (rank_position) predicts subsequent realised/shadow R; better-ranked candidates outperform, and it persists on later unseen cycles.",
        "research_classification": "observational_rank_ordering_vs_outcome",
        "causal_claim": "NONE — observational; shadow outcomes are counterfactual/simulated, not proof production ranking causes the outcome",
        "distinct_from_port1": "PORT-1 asks selected-vs-best per cycle; D6 asks whether rank ORDERING predicts outcomes across positions. Separate runners and reports; neither completes the other.",
        "unit_of_analysis": "one canonical_opportunity_id (candidate); account fanout and repeated horizons collapse",
        "evidence_authority": "portfolio_rankings (rank_position) joined via decision_ledger to CURRENT shadow simulated_outcome.pnl_r_multiple",
        "evidence_epoch": "CURRENT",
        "portfolio_rankings_read": ranking_rows,
        "distinct_opportunities": diagnostics["distinct_opportunities"],
        "paired_opportunities": paired,
        "missing_outcome_count": diagnostics["missing_outcomes_excluded"],
        "rank_signal": {
            "definition": "Spearman between better-rank (-rank_position) and realised R; positive => better rank predicts higher R",
            "overall": overall_signal,
            "discovery": discovery_signal,
            "later_unseen_validation": validation_signal,
            "signal_threshold": _D6_RANK_SIGNAL_MIN,
        },
        "outcome_distribution": {
            "mean_r": round(sum(all_r) / len(all_r), 4) if all_r else None,
            "median_r": round(statistics.median(all_r), 4) if all_r else None,
            "win_rate": round(sum(1 for v in all_r if v > 0) / len(all_r), 4) if all_r else None,
        },
        "per_rank_outcome": _d6_bucket_stats(rows),
        "discovery": {"n": len(discovery)},
        "later_unseen_validation": {"n": len(validation)},
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "Shadow outcomes are counterfactual/simulated, not broker truth.",
            "COMPLETE means the chronological rank-ordering evaluation ran validly, not that the ranker is good.",
            "One canonical opportunity is one observation; account fanout and repeated horizons never inflate n.",
            "Missing outcomes are excluded, never imputed; conflicting rank/outcome fails closed.",
            "Rank buckets with n<15 are reported but not used for a directional claim.",
            "Research only; production ranking/selection is never modified. Distinct from PORT-1.",
        ],
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": _D6_MIN_TOTAL,
            "minimum_discovery": _D6_MIN_DISCOVERY,
            "minimum_validation": _D6_MIN_VALIDATION,
            "minimum_bucket": _D6_MIN_BUCKET,
        },
        "quarantine_boundary": boundary.describe(),
    }

    confidence = "MEDIUM" if status == ReadinessStatus.COMPLETE else "INSUFFICIENT_DATA"
    if status == ReadinessStatus.COMPLETE:
        recommendation = f"OBSERVATIONAL FINDING [{finding}]: validation rank signal {validation_signal:+.4f} (discovery {discovery_signal:+.4f})"
    else:
        recommendation = f"{getattr(status, 'value', status)}: {reason}"

    report = build_report(
        question_id="D6", status=status,
        overall=overall, confidence=confidence,
        dataset={
            "source": "portfolio_rankings+shadow_trades",
            "sample_size": paired,
            "independent_observations": paired,
            "quarantine_boundary": boundary.describe(),
        },
        fingerprint=build_fingerprint(
            paired, max(0, diagnostics["distinct_opportunities"] - paired),
            source="portfolio_rankings", validation_score=confidence, epoch="CURRENT",
        ),
        recommendation=recommendation,
        assumptions=[
            "Primary evidence = portfolio_rankings V1 (rank_position); outcomes joined via decision_ledger to CURRENT shadow R.",
            "One canonical_opportunity_id is one independent observation; account fanout and repeated horizons collapse first.",
            "Rank ordering is pre-outcome; membership is never derived from realised outcome.",
            "Missing outcomes are excluded, never imputed; conflicting rank/outcome fails closed.",
            "Deterministic chronological discovery/validation; the rank relationship must persist on later unseen cycles.",
            "D6 (rank-ordering) is distinct from PORT-1 (selected-vs-best); neither report completes the other.",
        ],
        provenance={
            "experiment_module": "research_engine.experiments.portfolio_ranking",
            "registry_id": "D6",
            "function": "run_portfolio_ranking",
            "report_filename": D6_REPORT_FILENAME,
            "partition": "deterministic chronological 60/40 by cycle time",
            "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
        },
    )

    persist_report(report, D6_REPORT_FILENAME)
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