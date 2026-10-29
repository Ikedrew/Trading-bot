"""D4: leakage-safe pre-decision score-threshold predictive validation.

D4 asks whether the CANONICAL PRE-DECISION score ranks/separates subsequent
realised trade quality, and whether a score threshold selected on earlier
(discovery) opportunities is supported on later (validation) unseen
opportunities.

CANONICAL SCORE AUTHORITY
-------------------------
The single canonical pre-decision score is ``decision_trace.score_strategy``:
the strategy-weighted confluence score that the production decision gate
actually acts on (``core/pipeline/shadow_rooms.py`` gates the would-trade
decision on ``score_strategy >= 0.40``; the confluence engine's ``final_score``
is persisted as this strategy-weighted score).  In the decision-trace producer
the generic ``score`` alias falls back INTO ``score_strategy``
(``score_strategy = engine_result.get("score_strategy", engine_result.get("score", 0.0))``),
confirming they are the same production authority.

``score_neutral`` is a SEPARATE baseline diagnostic (global-weight "baseline
truth"), NOT the decision authority.  ``confidence``, ``p_success`` (D2's
probability-estimator input), and ``predicted_ev_r`` (D3's EV measure) are
DISTINCT fields and are explicitly rejected as substitutes.  D4 uses exactly
one authority and fails closed on any alias substitution or conflict.

This runner never modifies production score generation.  One CURRENT canonical
opportunity is one independent observation; account fanout and repeated
horizons are collapsed before any statistic.  Scores are joined one-to-one to
subsequent shadow outcomes and evaluated on a strictly later chronological
partition.  The score threshold is selected on the discovery partition only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from statistics import median
from typing import Any, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)
from research_engine.experiments.market_prediction_rw2 import (
    build_opportunity_observations,
)

D4_REPORT_FILENAME = "d4_score_threshold_validation_v1.json"

# The exact canonical pre-decision score authority. No alias may substitute it.
SCORE_AUTHORITY_FIELD = "score_strategy"
SCORE_AUTHORITY_VERSION = "strategy_weighted_confluence_score_v1"
SCORE_AUTHORITY_PROVENANCE = (
    "decision_trace.score_strategy — strategy-weighted confluence score; the "
    "production would-trade decision gate acts on this field "
    "(shadow_rooms score_strategy >= 0.40). score_neutral is a separate "
    "baseline diagnostic and is not the decision authority."
)

# Aliases that must NEVER be treated as the canonical pre-decision score.
_REJECTED_SCORE_ALIASES = frozenset({
    "score_neutral", "confidence", "p_success", "predicted_ev_r",
    "overall_score", "signal_score", "final_rank_score",
})

# Post-outcome fields must never enter the predictor.
_POST_OUTCOME_NAMES = frozenset({
    "realised_r", "r_multiple", "pnl_r_multiple", "mfe", "mfe_r", "mae",
    "mae_r", "close_reason", "exit_reason", "future_path", "future_regime",
    "broker_pnl", "won", "outcome_r",
})

MINIMUM_TOTAL = 100
MINIMUM_DISCOVERY = 60
MINIMUM_VALIDATION = 40
MINIMUM_GROUP = 15


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _current(record: dict[str, Any]) -> bool:
    epoch = record.get("data_epoch", record.get("epoch"))
    if epoch is not None:
        return _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}
    try:
        from core.production_data_contract import current_schema
        return record.get("schema_version") == current_schema("decision_trace")
    except (KeyError, TypeError):
        return False


def canonical_score(record: dict[str, Any]) -> float | None:
    """Return the ONE canonical pre-decision score (score_strategy) or None.

    Reads exactly ``decision_trace.score_strategy``.  It never reads
    ``score_neutral``, ``confidence``, ``p_success``, ``predicted_ev_r`` or any
    other alias, and never substitutes one for a missing authority.  Post-outcome
    fields are irrelevant here and can never become the score.
    """
    if not isinstance(record, dict):
        return None
    if SCORE_AUTHORITY_FIELD not in record:
        return None
    return _finite(record.get(SCORE_AUTHORITY_FIELD))


def _score_row(record: dict[str, Any]) -> tuple[str, datetime | None, float | None]:
    """Extract (opportunity, pre-decision timestamp, canonical score)."""
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    opportunity = _text(record.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id"))
    timestamp = _time(record.get("timestamp_utc"))
    score = canonical_score(record)
    return opportunity, timestamp, score


def _pre_decision_context(record: dict[str, Any]) -> tuple[str, str]:
    """Same-opportunity PRE-DECISION context (H4 regime, market phase/state)."""
    regime = _text(record.get("regime") or record.get("h4_regime")) or "UNKNOWN"
    phase = _text(record.get("market_phase") or record.get("market_state")) or "UNKNOWN"
    return regime, phase


def build_paired_scores(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair one PRE-DECISION canonical score per canonical opportunity with its
    subsequent realised R outcome.

    Account fanout and repeated horizons collapse within canonical opportunity;
    missing outcomes are excluded (never imputed); conflicting scores,
    conflicting outcomes, or post-decision chronology fail closed.
    """
    outcomes, outcome_diagnostics = build_opportunity_observations(shadow_records)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}

    score_groups: dict[str, set[tuple[datetime, float]]] = defaultdict(set)
    context_by_id: dict[str, set[tuple[str, str]]] = defaultdict(set)
    excluded_non_current = excluded_missing_score = 0
    for record in decision_records:
        if not _current(record):
            excluded_non_current += 1
            continue
        opportunity, timestamp, score = _score_row(record)
        if not opportunity or timestamp is None or score is None:
            excluded_missing_score += 1
            continue
        score_groups[opportunity].add((timestamp, score))
        context_by_id[opportunity].add(_pre_decision_context(record))

    paired: list[dict[str, Any]] = []
    conflicts: list[str] = list(outcome_diagnostics.get("ambiguous_opportunities", ()))
    missing_outcome = unmatched_score = 0
    for opportunity, scores in sorted(score_groups.items()):
        if len(scores) != 1:
            conflicts.append(opportunity)
            continue
        score_time, score = next(iter(scores))
        outcome = outcome_by_id.get(opportunity)
        if outcome is None:
            unmatched_score += 1
            continue
        if outcome.outcome_r is None:
            missing_outcome += 1
            continue
        if score_time > outcome.decision_time:
            conflicts.append(opportunity)
            continue
        contexts = context_by_id.get(opportunity, set())
        regime, phase = next(iter(contexts)) if len(contexts) == 1 else ("UNKNOWN", "UNKNOWN")
        paired.append({
            "canonical_opportunity_id": opportunity,
            "timestamp": score_time,
            "score": score,
            "outcome_r": outcome.outcome_r,
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
            "h4_regime": regime,
            "market_phase": phase,
        })
    paired.sort(key=lambda row: (row["timestamp"], row["canonical_opportunity_id"]))
    return paired, {
        **outcome_diagnostics,
        "score_opportunities": len(score_groups),
        "paired_opportunities": len(paired),
        "excluded_non_current_scores": excluded_non_current,
        "excluded_missing_score": excluded_missing_score,
        "missing_outcomes_excluded": missing_outcome,
        "unmatched_scores": unmatched_score,
        "conflicting_opportunities": tuple(sorted(set(conflicts))),
    }


def chronological_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic EARLIER=discovery / LATER=validation split by timestamp."""
    unique_times = sorted({row["timestamp"] for row in rows})
    if len(unique_times) < 2:
        return rows, []
    index = max(1, min(len(unique_times) - 1, int(len(unique_times) * 0.60)))
    boundary = unique_times[index]
    return ([row for row in rows if row["timestamp"] < boundary],
            [row for row in rows if row["timestamp"] >= boundary])


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None}
    outcomes = [row["outcome_r"] for row in rows]
    return {
        "n": len(rows),
        "mean_r": sum(outcomes) / len(outcomes),
        "median_r": median(outcomes),
        "win_rate": sum(1 for row in rows if row["outcome_r"] > 0) / len(rows),
    }


def _rank_relationship(rows: list[dict[str, Any]]) -> float | None:
    """Spearman-style rank correlation between score and realised R (no SciPy).

    Deterministic; ties get average ranks.  Returns None if fewer than 3 rows or
    zero variance in either variable.
    """
    if len(rows) < 3:
        return None
    scores = [row["score"] for row in rows]
    outcomes = [row["outcome_r"] for row in rows]
    if len(set(scores)) < 2 or len(set(outcomes)) < 2:
        return None
    score_ranks = _average_ranks(scores)
    outcome_ranks = _average_ranks(outcomes)
    n = len(rows)
    mean_rank = (n + 1) / 2.0
    cov = sum((score_ranks[i] - mean_rank) * (outcome_ranks[i] - mean_rank) for i in range(n))
    var_s = sum((r - mean_rank) ** 2 for r in score_ranks)
    var_o = sum((r - mean_rank) ** 2 for r in outcome_ranks)
    if var_s <= 0.0 or var_o <= 0.0:
        return None
    return cov / math.sqrt(var_s * var_o)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0  # 1-based average rank for the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def select_threshold_from_discovery(discovery: list[dict[str, Any]]) -> tuple[float | None, str, dict[str, Any]]:
    """Select a score threshold using the DISCOVERY partition ONLY.

    Objective: maximise discovery expectancy separation (mean R above minus mean
    R below the threshold), subject to each discovery side retaining at least
    MINIMUM_GROUP observations.  Candidate thresholds are the distinct discovery
    score quantiles at deciles (a small deterministic set derived only from
    discovery).  Ties break on the smaller threshold value (deterministic).

    The validation partition is NEVER consulted here.
    """
    diagnostics: dict[str, Any] = {"candidates": [], "objective": "max_discovery_expectancy_separation"}
    if len(discovery) < 2 * MINIMUM_GROUP:
        return None, "insufficient_discovery_for_threshold", diagnostics
    scores = sorted(row["score"] for row in discovery)
    # Deterministic small candidate set: decile score values within discovery.
    candidate_values = sorted({
        scores[min(len(scores) - 1, max(0, int(len(scores) * q / 10.0)))]
        for q in range(1, 10)
    })
    best: tuple[float, float] | None = None  # (threshold, separation)
    for threshold in candidate_values:
        above = [r for r in discovery if r["score"] >= threshold]
        below = [r for r in discovery if r["score"] < threshold]
        if len(above) < MINIMUM_GROUP or len(below) < MINIMUM_GROUP:
            continue
        mean_above = sum(r["outcome_r"] for r in above) / len(above)
        mean_below = sum(r["outcome_r"] for r in below) / len(below)
        separation = mean_above - mean_below
        diagnostics["candidates"].append({
            "threshold": threshold, "n_above": len(above), "n_below": len(below),
            "separation_r": separation,
        })
        if best is None or separation > best[1] + 1e-12 or (
            abs(separation - best[1]) <= 1e-12 and threshold < best[0]
        ):
            best = (threshold, separation)
    if best is None:
        return None, "no_candidate_satisfied_discovery_group_minimum", diagnostics
    return best[0], "max_discovery_expectancy_separation", diagnostics


def _context_diagnostics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Per-context-cell diagnostics on the SAME paired opportunities.

    Cells with fewer than MINIMUM_GROUP paired opportunities are marked
    explicitly insufficient and are never used to draw a conclusion.
    """
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cells[row[key]].append(row)
    diagnostics: dict[str, Any] = {}
    for cell, cell_rows in sorted(cells.items()):
        stats = _group_stats(cell_rows)
        stats["sufficient"] = stats["n"] >= MINIMUM_GROUP
        diagnostics[cell] = stats
    return diagnostics


def _classify_finding(
    status: str,
    discovery_separation: float | None,
    validation_difference: float | None,
) -> str:
    """Research finding class (NOT a production instruction)."""
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    if discovery_separation is None or validation_difference is None:
        return "INSUFFICIENT_EVIDENCE"
    # A meaningful directional effect must exist in discovery to talk about
    # persistence at all.
    if discovery_separation <= 0.0:
        return "NO_SIGNAL"
    # Same direction and materially positive on later unseen validation.
    if validation_difference > 0.0:
        return "VALIDATED_DIRECTIONAL_SIGNAL"
    return "DISCOVERY_ONLY"


def analyse(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    paired, diagnostics = build_paired_scores(decision_records, shadow_records)
    discovery, validation = chronological_split(paired)
    threshold, threshold_provenance, threshold_diagnostics = select_threshold_from_discovery(discovery)

    score_values = [row["score"] for row in paired]
    outcome_values = [row["outcome_r"] for row in paired]
    score_distribution = {
        "min": min(score_values) if score_values else None,
        "median": median(score_values) if score_values else None,
        "max": max(score_values) if score_values else None,
    }
    outcome_distribution = {
        "mean_r": (sum(outcome_values) / len(outcome_values)) if outcome_values else None,
        "median_r": median(outcome_values) if outcome_values else None,
        "min_r": min(outcome_values) if outcome_values else None,
        "max_r": max(outcome_values) if outcome_values else None,
        "win_rate": (sum(1 for v in outcome_values if v > 0) / len(outcome_values)) if outcome_values else None,
    }
    rank_relationship = _rank_relationship(paired)

    discovery_separation = None
    if threshold is not None and discovery:
        d_above = [r for r in discovery if r["score"] >= threshold]
        d_below = [r for r in discovery if r["score"] < threshold]
        if d_above and d_below:
            discovery_separation = (
                sum(r["outcome_r"] for r in d_above) / len(d_above)
                - sum(r["outcome_r"] for r in d_below) / len(d_below)
            )

    if threshold is not None:
        above = [r for r in validation if r["score"] >= threshold]
        below = [r for r in validation if r["score"] < threshold]
    else:
        above, below = [], []
    above_stats = _group_stats(above)
    below_stats = _group_stats(below)
    validation_difference = (
        above_stats["mean_r"] - below_stats["mean_r"]
        if above_stats["mean_r"] is not None and below_stats["mean_r"] is not None
        else None
    )

    if diagnostics["conflicting_opportunities"]:
        status = "BLOCKED"
        reason = "Conflicting score, outcome, or chronology for a canonical opportunity"
    elif len(paired) < MINIMUM_TOTAL or len(discovery) < MINIMUM_DISCOVERY or len(validation) < MINIMUM_VALIDATION:
        status = "WAITING_DATA"
        reason = (
            f"paired/discovery/validation={len(paired)}/{len(discovery)}/{len(validation)}; "
            f"need {MINIMUM_TOTAL}/{MINIMUM_DISCOVERY}/{MINIMUM_VALIDATION}"
        )
    elif threshold is None:
        status = "WAITING_DATA"
        reason = f"No discovery-supported threshold: {threshold_provenance}"
    elif above_stats["n"] < MINIMUM_GROUP or below_stats["n"] < MINIMUM_GROUP:
        status = "WAITING_DATA"
        reason = (
            f"validation above/below threshold={above_stats['n']}/{below_stats['n']}; "
            f"need >= {MINIMUM_GROUP} in each group"
        )
    else:
        status = "COMPLETE"
        reason = "Chronological pre-decision score-threshold evaluation completed on later unseen opportunities"

    finding = _classify_finding(status, discovery_separation, validation_difference)
    regime_diagnostics = _context_diagnostics(paired, "h4_regime")
    phase_diagnostics = _context_diagnostics(paired, "market_phase")

    overall = {
        "canonical_question": "D4",
        "research_classification": "observational_predictive_score_threshold",
        "causal_claim": "NONE — observational predictive validation of a pre-decision score threshold, not a policy optimality proof",
        "unit_of_analysis": "one canonical_opportunity_id",
        "score_authority": {
            "field": SCORE_AUTHORITY_FIELD,
            "version": SCORE_AUTHORITY_VERSION,
            "provenance": SCORE_AUTHORITY_PROVENANCE,
            "rejected_aliases": sorted(_REJECTED_SCORE_ALIASES),
        },
        "evidence_authority": "CURRENT decision_trace.score_strategy paired to CURRENT shadow realised R",
        "evidence_epoch": "CURRENT",
        "score_distribution": score_distribution,
        "realised_r_distribution": outcome_distribution,
        "score_outcome_rank_relationship": rank_relationship,
        "threshold": {
            "value": threshold,
            "version": SCORE_AUTHORITY_VERSION,
            "provenance": threshold_provenance,
            "selection_method": "maximise discovery expectancy separation with >=15 per discovery side; deterministic decile candidates; smaller-threshold tie-break",
            "selected_from": "discovery_partition_only",
            "authoritative": False,
            "candidates": threshold_diagnostics.get("candidates", []),
        },
        "discovery": {"n": len(discovery), "expectancy_separation_r": discovery_separation},
        "later_unseen_validation": {
            "n": len(validation),
            "above_threshold": above_stats,
            "below_threshold": below_stats,
            "expectancy_difference_r": validation_difference,
        },
        "context_diagnostics": {
            "h4_regime": regime_diagnostics,
            "market_phase": phase_diagnostics,
            "note": "Context cells are diagnostics on the same paired opportunities; cells with n<15 are marked insufficient and never over-interpreted. Context does not inflate independent n.",
        },
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "Observational predictive validation only; no causal or optimality claim.",
            "COMPLETE means the chronological evaluation ran validly, not that the threshold is profitable.",
            "The live production score threshold is NOT changed by this research.",
            "Insufficient context cells (n<15) are reported but not interpreted.",
        ],
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": MINIMUM_TOTAL,
            "minimum_discovery": MINIMUM_DISCOVERY,
            "minimum_validation": MINIMUM_VALIDATION,
            "minimum_per_group": MINIMUM_GROUP,
        },
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    if status == "COMPLETE":
        outcome_evaluation = (
            f"[{finding}] validation above-threshold mean R {above_stats['mean_r']:+.4f} vs "
            f"below {below_stats['mean_r']:+.4f} (difference {validation_difference:+.4f}R)"
        )
    else:
        outcome_evaluation = f"{status}: {reason}"

    return build_report(
        question_id="D4",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": "decision_trace+shadow_trades", "sample_size": len(paired), "independent_observations": len(paired)},
        fingerprint=build_fingerprint(len(paired), max(0, diagnostics["score_opportunities"] - len(paired)), "decision_trace+shadow_trades", confidence, "CURRENT"),
        recommendation=(f"OBSERVATIONAL FINDING: {outcome_evaluation}" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "The ONLY predictor is the canonical pre-decision score decision_trace.score_strategy.",
            "score_neutral, confidence, p_success, predicted_ev_r and other aliases are rejected as substitutes.",
            "Score timestamp must be <= outcome chronology; post-outcome fields can never become the predictor.",
            "R>0 is the win label for descriptive win rate; missing R is excluded, never imputed.",
            "Account fanout and repeated shadow horizons are collapsed within canonical opportunity.",
            "Threshold is selected on discovery only; validation never selects or optimises its own threshold.",
            "Context cells with n<15 are reported as insufficient and never over-interpreted.",
            "Production score generation is never modified; the live threshold is never changed.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "D4",
            "report_filename": D4_REPORT_FILENAME, "evidence_epoch": "CURRENT",
            "score_authority_field": SCORE_AUTHORITY_FIELD,
            "score_authority_version": SCORE_AUTHORITY_VERSION,
            "partition": "deterministic chronological 60/40 by timestamp groups",
            "threshold_provenance": threshold_provenance,
        },
    )


def run_d4() -> dict[str, Any]:
    from research_engine.data_access.s3_source import get_default_source
    decisions = get_default_source().read_dataset("decision_trace")
    return analyse(decisions, load_shadow_trades(epoch="CURRENT"))
