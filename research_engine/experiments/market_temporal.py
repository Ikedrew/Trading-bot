"""
WAVE 7 / Post-Wave-10 — M5 / M8 Temporal Market Analysis.

ARCHITECTURAL INVESTIGATION: Does either question require a temporal
(sequence-aware) market universe?

M5: "Phase transitions predict drawdown"
    → Uses a derived realised-R strategy drawdown curve from chronologically
      closed shadow outcomes. This is NOT account equity and NOT mark-to-market
      drawdown.

M8: "Phase transition behaviour"
    → "Do market phase transitions predict future trade outcomes?"
    → TEMPORAL_UNIVERSE_OPTIONAL — can be answered with a windowed
      "recent_transition" feature on each trade row, derived from the
      decision context embedded in shadow_trades.

This module provides the analysis for M5 and M8 using trade-level temporal
features derived from shadow_trades decision snapshots and completed outcomes.

No production/writer schemas are modified.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from research_engine.data_quality.classifier import DataEpoch, classify_record
from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# M5 — PHASE TRANSITIONS VS REALISED R-DRAWDOWN
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M5 = (
    "Do rapid phase transitions predict drawdown periods?"
)

_M5_LOOKAHEAD_TRADES = 10
_M5_MIN_RECORDS = 30
_M5_MIN_TRANSITIONS = 3


def run_m5() -> dict[str, Any]:
    """
    Run M5: Phase transitions predict drawdown.

    The canonical research quantity is realised strategy drawdown in R,
    derived from chronologically closed shadow outcomes. It does not claim
    account-equity or mark-to-market drawdown: open-trade adverse excursion
    is captured elsewhere as MAE/path evidence and cannot be turned into a
    true account-equity curve without timestamped account equity/floating P&L.
    """
    question_id = "M5"
    all_records = load_shadow_trades(include_all_epochs=True)
    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    rows, excluded_unusable, duplicate_count = _build_m5_rows(current)
    total_excluded = len(all_records) - len(current) + excluded_unusable + duplicate_count

    if not rows:
        return _m5_insufficient(
            records_used=0,
            records_excluded=total_excluded,
            reason="No CURRENT closed shadow outcomes with market_phase, timestamps, and R outcome",
        )

    observations = _build_m5_observations(rows)
    transition_obs = [o for o in observations if o["phase_transition"]]
    transition_count = len(transition_obs)

    if len(rows) < _M5_MIN_RECORDS or transition_count < _M5_MIN_TRANSITIONS:
        report = _m5_insufficient(
            records_used=len(rows),
            records_excluded=total_excluded,
            reason=(
                f"Usable rows={len(rows)} (need {_M5_MIN_RECORDS}) and "
                f"phase transitions={transition_count} (need {_M5_MIN_TRANSITIONS})"
            ),
        )
        report["overall"]["phase_transitions"] = transition_count
        report["overall"]["drawdown_curve_preview"] = _curve_preview(rows)
        return report

    transition_dd = [o["future_max_drawdown_increase_r"] for o in transition_obs]
    non_transition_dd = [
        o["future_max_drawdown_increase_r"]
        for o in observations
        if not o["phase_transition"]
    ]

    transition_mean = _mean(transition_dd)
    non_transition_mean = _mean(non_transition_dd)
    uplift = (
        round(transition_mean - non_transition_mean, 4)
        if transition_mean is not None and non_transition_mean is not None
        else None
    )

    overall = {
        "question": CANONICAL_INTENT_M5,
        "drawdown_definition": (
            "realised strategy drawdown in cumulative R from closed shadow "
            "outcomes; not realised account equity and not mark-to-market "
            "account equity"
        ),
        "source_datasets": ["shadow_runtime", "research_shadow_trades"],
        "derived_or_observed": "DERIVED_FROM_CLOSED_OUTCOME_R",
        "drawdown_metric": "future_max_drawdown_increase_r",
        "future_window": f"next {_M5_LOOKAHEAD_TRADES} closed outcomes strictly after transition time",
        "records_used": len(rows),
        "phase_transitions": transition_count,
        "temporal_universe_required": True,
        "temporal_universe_available": True,
        "phase_transition_alignment": "phase at decision time T -> outcomes with close time strictly after T",
        "transition_drawdown": _summary(transition_dd),
        "non_transition_drawdown": _summary(non_transition_dd),
        "transition_minus_non_transition_mean_r": uplift,
        "sample_sufficiency": {
            "minimum_records": _M5_MIN_RECORDS,
            "minimum_transitions": _M5_MIN_TRANSITIONS,
            "usable_records": len(rows),
            "transition_observations": transition_count,
        },
        "clean_evidence_epoch": "CURRENT",
        "limitations": [
            "Closed-outcome R curve misses temporary open-trade adverse excursion.",
            "No claim is made about account-equity or floating-PnL drawdown.",
            "Multiple simultaneous shadow positions are counted as separate strategy outcomes, not as account exposure.",
        ],
    }

    return build_report(
        question_id=question_id,
        status="COMPLETE",
        overall=overall,
        confidence=_m5_confidence(len(rows)),
        dataset={
            "source": "shadow_runtime_v1(ingested)+research_shadow_trades",
            "sample_size": len(rows),
            "phase_transitions": transition_count,
            "duplicate_outcomes_excluded": duplicate_count,
        },
        fingerprint=build_fingerprint(
            records_used=len(rows),
            records_excluded=total_excluded,
            source="shadow_trades",
            validation_score=_m5_confidence(len(rows)),
            epoch="CURRENT",
        ),
        recommendation="FINDING: phase-transition realised-R drawdown profile computed",
        assumptions=[
            "Drawdown is computed from cumulative realised R over completed shadow outcomes.",
            "The curve is ordered by canonical close/outcome timestamp with decision timestamp fallback.",
            "A phase transition at T is compared only to outcomes strictly after T.",
        ],
        warnings=[
            "This is not mark-to-market account-equity drawdown.",
            "Closed outcomes cannot show temporary intra-trade drawdown that later recovered.",
        ],
        provenance={
            "experiment_module": __name__,
            "registry_id": question_id,
            "canonical_intent": CANONICAL_INTENT_M5,
            "temporal_verdict": "TEMPORAL_UNIVERSE_REQUIRED_AND_DERIVED",
            "clean_evidence_epoch": "CURRENT",
        },
    )


def _m5_insufficient(records_used: int, records_excluded: int, reason: str) -> dict[str, Any]:
    confidence = _m5_confidence(records_used)
    return build_report(
        question_id="M5",
        status="INSUFFICIENT_DATA",
        overall={
            "question": CANONICAL_INTENT_M5,
            "drawdown_definition": (
                "realised strategy drawdown in cumulative R from closed shadow "
                "outcomes; not realised account equity and not mark-to-market "
                "account equity"
            ),
            "source_datasets": ["shadow_runtime", "research_shadow_trades"],
            "derived_or_observed": "DERIVED_FROM_CLOSED_OUTCOME_R",
            "records_used": records_used,
            "phase_transitions": 0,
            "temporal_universe_required": True,
            "temporal_universe_available": records_used > 0,
            "phase_transition_alignment": "phase at decision time T -> outcomes with close time strictly after T",
            "future_window": f"next {_M5_LOOKAHEAD_TRADES} closed outcomes strictly after transition time",
            "drawdown_metric": "future_max_drawdown_increase_r",
            "sample_sufficiency": {
                "minimum_records": _M5_MIN_RECORDS,
                "minimum_transitions": _M5_MIN_TRANSITIONS,
                "usable_records": records_used,
                "reason": reason,
            },
            "clean_evidence_epoch": "CURRENT",
            "limitations": [
                "Closed-outcome R curve is not account-equity drawdown.",
                "No timestamped floating/account-equity observations are consumed.",
            ],
        },
        confidence=confidence,
        dataset={
            "source": "shadow_runtime_v1(ingested)+research_shadow_trades",
            "sample_size": records_used,
        },
        fingerprint=build_fingerprint(
            records_used=records_used,
            records_excluded=records_excluded,
            source="shadow_trades",
            validation_score=confidence,
            epoch="CURRENT",
        ),
        recommendation="WAIT",
        warnings=[reason, "This is not mark-to-market account-equity drawdown."],
        provenance={
            "experiment_module": __name__,
            "registry_id": "M5",
            "canonical_intent": CANONICAL_INTENT_M5,
            "temporal_verdict": "TEMPORAL_UNIVERSE_REQUIRED_AND_DERIVED",
            "clean_evidence_epoch": "CURRENT",
        },
    )


def _build_m5_rows(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded = 0
    duplicates = 0

    for rec in records:
        ident = rec.get("identity", {}) or {}
        snap = rec.get("decision_snapshot", {}) or {}
        sim = rec.get("simulated_outcome", {}) or {}
        key = (
            ident.get("shadow_trade_id")
            or ident.get("trade_id")
            or f"{ident.get('canonical_opportunity_id')}:{ident.get('evaluated_horizon')}:{ident.get('shadow_type')}"
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(str(key))

        phase = _extract_market_phase(rec)
        decision_time = _extract_entry_time(rec)
        outcome_time = _extract_outcome_time(rec)
        r = _extract_r_multiple(rec)
        if not phase or decision_time is None or outcome_time is None or r is None:
            excluded += 1
            continue
        if outcome_time <= decision_time:
            excluded += 1
            continue

        rows.append({
            "key": str(key),
            "symbol": ident.get("symbol", "") or rec.get("symbol", ""),
            "phase": phase,
            "decision_time": decision_time,
            "outcome_time": outcome_time,
            "r": r,
            "cumulative_r_before": 0.0,
            "cumulative_r_after": 0.0,
            "peak_r_after": 0.0,
            "drawdown_r_after": 0.0,
            "phase_transition": None,
        })

    rows.sort(key=lambda r: (r["outcome_time"], r["decision_time"], r["key"]))
    cumulative = 0.0
    peak = 0.0
    for row in rows:
        row["cumulative_r_before"] = round(cumulative, 4)
        cumulative += row["r"]
        peak = max(peak, cumulative)
        row["cumulative_r_after"] = round(cumulative, 4)
        row["peak_r_after"] = round(peak, 4)
        row["drawdown_r_after"] = round(max(0.0, peak - cumulative), 4)

    by_symbol = sorted(rows, key=lambda r: (r["symbol"], r["decision_time"], r["key"]))
    previous_phase_by_symbol: dict[str, str] = {}
    for row in by_symbol:
        prev = previous_phase_by_symbol.get(row["symbol"])
        row["phase_transition"] = _infer_phase_transition({"decision_snapshot": {"market_phase": row["phase"]}}, prev)
        previous_phase_by_symbol[row["symbol"]] = row["phase"]

    return rows, excluded, duplicates


def _build_m5_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        future = [r for r in rows if r["outcome_time"] > row["decision_time"]][:_M5_LOOKAHEAD_TRADES]
        if not future:
            continue
        known_before = [r for r in rows if r["outcome_time"] <= row["decision_time"]]
        baseline = known_before[-1]["drawdown_r_after"] if known_before else 0.0
        future_max = max(r["drawdown_r_after"] for r in future)
        observations.append({
            "key": row["key"],
            "symbol": row["symbol"],
            "phase": row["phase"],
            "phase_transition": row["phase_transition"],
            "decision_time": row["decision_time"],
            "baseline_drawdown_r_at_signal": round(baseline, 4),
            "future_window_records": len(future),
            "future_max_drawdown_r": round(future_max, 4),
            "future_max_drawdown_increase_r": round(max(0.0, future_max - baseline), 4),
        })
    return observations


def _extract_outcome_time(record: dict[str, Any]) -> float | None:
    sim = record.get("simulated_outcome", {}) or {}
    ts = (
        sim.get("exit_timestamp")
        or sim.get("exit_time")
        or record.get("exit_timestamp")
        or _extract_entry_time(record)
    )
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None
    return None


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "max": None}
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _m5_confidence(n: int) -> str:
    if n >= 200:
        return "HIGH"
    if n >= 50:
        return "MEDIUM"
    if n >= _M5_MIN_RECORDS:
        return "LOW"
    return "INSUFFICIENT_DATA"


def _curve_preview(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": r["key"],
            "outcome_time": r["outcome_time"],
            "cumulative_r_after": r["cumulative_r_after"],
            "peak_r_after": r["peak_r_after"],
            "drawdown_r_after": r["drawdown_r_after"],
        }
        for r in rows[:5]
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# M8 — TEMPORAL VERDICT + WINDOWED ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

# M8 VERDICT:
#   Question: "Do market phase transitions (e.g. IMPULSE→EXHAUSTION)
#              predict future trade outcomes?"
#   TEMPORAL_UNIVERSE_OPTIONAL: The question asks whether trades entered
#     near a phase transition have different outcomes. This can be answered
#     with a trade-level approach: evaluate whether a "recent_transition"
#     feature (derived from the decision context at trade time) has
#     predictive power for R-multiple.
#
#   A true temporal universe (ordered market observations, not just trades)
#   WOULD provide richer analysis (transition probabilities, persistence,
#   duration in state), but the core question can be answered correctly
#   using the existing row-oriented shadow_trades universe with an
#   augmented feature.
#
#   Decision: NO temporal universe built for M8. The windowed approach
#   below answers the canonical question.

CANONICAL_INTENT_M8 = (
    "Do market phase transitions (e.g. IMPULSE→EXHAUSTION) "
    "predict future trade outcomes?"
)

# Known phase-to-phase transitions considered significant
_SIGNIFICANT_TRANSITIONS = frozenset({
    "IMPULSE→EXHAUSTION",
    "IMPULSE→CONSOLIDATION",
    "EXHAUSTION→REVERSAL",
    "EXHAUSTION→PULLBACK",
    "CONSOLIDATION→IMPULSE",
    "CONSOLIDATION→EXHAUSTION",
    "PULLBACK→IMPULSE",
    "REVERSAL→IMPULSE",
})

# Phase pairs that represent actual transitions (not same-phase)
_TRANSITION_PAIRS = {
    ("IMPULSE", "EXHAUSTION"),
    ("IMPULSE", "CONSOLIDATION"),
    ("IMPULSE", "PULLBACK"),
    ("EXHAUSTION", "REVERSAL"),
    ("EXHAUSTION", "PULLBACK"),
    ("EXHAUSTION", "CONSOLIDATION"),
    ("EXHAUSTION", "IMPULSE"),
    ("CONSOLIDATION", "IMPULSE"),
    ("CONSOLIDATION", "EXHAUSTION"),
    ("CONSOLIDATION", "PULLBACK"),
    ("PULLBACK", "IMPULSE"),
    ("PULLBACK", "CONSOLIDATION"),
    ("PULLBACK", "EXHAUSTION"),
    ("REVERSAL", "IMPULSE"),
    ("REVERSAL", "CONSOLIDATION"),
}


def _infer_phase_transition(
    record: dict[str, Any],
    prev_phase: str | None,
) -> str | None:
    """
    Determine if a phase transition occurred for a trade record.

    Uses the record's decision_snapshot market_phase and compares
    to a previous phase (if available from sequence context).

    When no previous phase is available, returns None.
    """
    current_phase = _extract_market_phase(record)
    if not current_phase or not prev_phase:
        return None
    if current_phase == prev_phase:
        return None
    transition = f"{prev_phase}→{current_phase}"
    return transition if transition in _SIGNIFICANT_TRANSITIONS else None


def _extract_market_phase(record: dict[str, Any]) -> str:
    """Extract market phase from a shadow trade record."""
    ds = record.get("decision_snapshot", {}) or {}
    val = (
        ds.get("market_phase")
        or ds.get("phase")
        or record.get("market_phase")
        or record.get("phase")
    )
    if val in ("IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"):
        return val
    return ""


def _extract_entry_time(record: dict[str, Any]) -> float | None:
    """Extract entry time from a shadow trade record."""
    identity = record.get("identity", {}) or {}
    ds = record.get("decision_snapshot", {}) or {}
    ts = (
        ds.get("entry_time")
        or identity.get("entry_time")
        or record.get("entry_time")
        or record.get("timestamp_utc")
    )
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None
    return None


def _extract_r_multiple(record: dict[str, Any]) -> float | None:
    """Extract R-multiple outcome."""
    outcome = record.get("simulated_outcome", {}) or {}
    for key in ("pnl_r_multiple", "r_multiple"):
        if key in outcome and outcome[key] is not None:
            try:
                return float(outcome[key])
            except (TypeError, ValueError):
                return None
    return None


def run_m8() -> dict[str, Any]:
    """
    Run M8: Phase transition behaviour.

    Uses a windowed approach: trades are grouped by symbol and ordered
    by entry_time. A "recent_transition" flag is computed per trade
    based on whether the market_phase changed from the previous trade.

    The analysis then compares R-multiple outcomes for:
        - Trades with a recent phase transition
        - Trades without a recent phase transition
        - Specific transition types (e.g. IMPULSE→EXHAUSTION)

    This correctly answers the canonical question without requiring
    a full temporal market universe.
    """
    question_id = "M8"
    all_records = load_shadow_trades()

    # Filter to CURRENT epoch
    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Sort by symbol and entry_time for sequence context
    def _sort_key(rec: dict[str, Any]) -> tuple:
        identity = rec.get("identity", {}) or {}
        sym = identity.get("symbol", "") or rec.get("symbol", "")
        entry_time = _extract_entry_time(rec) or 0.0
        return (sym, entry_time)

    sorted_records = sorted(current, key=_sort_key)

    # Group by symbol and compute transitions
    transition_records = []
    no_transition_records = []
    transition_types: dict[str, list[float]] = defaultdict(list)
    previous_phase_by_symbol: dict[str, str] = {}

    for rec in sorted_records:
        identity = rec.get("identity", {}) or {}
        symbol = identity.get("symbol", "") or rec.get("symbol", "")
        current_phase = _extract_market_phase(rec)
        r = _extract_r_multiple(rec)

        if not current_phase or r is None:
            continue

        prev_phase = previous_phase_by_symbol.get(symbol)
        transition = _infer_phase_transition(rec, prev_phase)

        if transition:
            transition_records.append(rec)
            transition_types[transition].append(r)
        else:
            no_transition_records.append(rec)

        # Update previous phase for next trade in this symbol
        previous_phase_by_symbol[symbol] = current_phase

    # Compute transition vs no-transition metrics
    def _compute_stats(r_vals: list[float]) -> dict[str, Any]:
        if not r_vals:
            return {"count": 0, "mean_r": 0, "win_rate": 0}
        n = len(r_vals)
        wins = [v for v in r_vals if v > 0]
        mean_r = sum(r_vals) / n
        win_rate = len(wins) / n
        return {
            "count": n,
            "mean_r": round(mean_r, 4),
            "win_rate": round(win_rate, 4),
            "wins": len(wins),
            "losses": n - len(wins),
        }

    transition_stats = _compute_stats(
        [r for rec in transition_records for r in [_extract_r_multiple(rec)] if r is not None]
    )
    no_transition_stats = _compute_stats(
        [r for rec in no_transition_records for r in [_extract_r_multiple(rec)] if r is not None]
    )

    # Per-transition-type stats
    transition_type_stats = {}
    for ttype, r_vals in sorted(transition_types.items()):
        transition_type_stats[ttype] = _compute_stats(r_vals)

    total_analysed = transition_stats["count"] + no_transition_stats["count"]

    # Assess edge
    if total_analysed == 0:
        edge, confidence = "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"
    elif total_analysed >= 200:
        confidence = "HIGH"
    elif total_analysed >= 50:
        confidence = "MEDIUM"
    elif total_analysed >= 20:
        confidence = "LOW"
    else:
        confidence = "INSUFFICIENT_DATA"

    # Compare transition vs no-transition
    transition_better = transition_stats["mean_r"] > no_transition_stats["mean_r"]
    if transition_stats["count"] > 0 and no_transition_stats["count"] > 0:
        if transition_better and confidence in ("HIGH", "MEDIUM"):
            edge = "POSITIVE_EDGE"
        elif not transition_better and confidence in ("HIGH", "MEDIUM"):
            edge = "NEGATIVE_EDGE"
        else:
            edge = "MARGINAL_EDGE"
    else:
        edge = "INSUFFICIENT_DATA"

    overall = {
        "question": CANONICAL_INTENT_M8,
        "method": "windowed_trade_level",
        "temporal_universe_used": False,
        "temporal_universe_verdict": "TEMPORAL_UNIVERSE_OPTIONAL",
        "transition_analysis": {
            "with_transition": transition_stats,
            "without_transition": no_transition_stats,
            "transition_types": transition_type_stats,
            "transition_count": len(transition_types),
        },
        "transition_better_than_no_transition": transition_better
        if transition_stats["count"] > 0 and no_transition_stats["count"] > 0
        else None,
        "edge_assessment": edge,
        "limitation": (
            "Windowed approach uses trade-level sequence context. "
            "A true temporal market universe (ordered observations, "
            "not just trades) would provide richer transition analysis "
            "including transition probabilities and state persistence."
        ),
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed,
        "transition_records": transition_stats["count"],
        "no_transition_records": no_transition_stats["count"],
    }

    fingerprint = build_fingerprint(
        records_used=total_analysed,
        records_excluded=excluded,
        source="shadow_trades",
        validation_score=confidence,
    )

    return build_report(
        question_id=question_id,
        status="COMPLETE" if total_analysed > 0 else "INSUFFICIENT_DATA",
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=fingerprint,
        recommendation=edge,
        warnings=(
            [f"{excluded} LEGACY/TRANSITIONAL records excluded"]
            if excluded > 0
            else []
        ),
        provenance={
            "experiment_module": __name__,
            "registry_id": question_id,
            "canonical_intent": CANONICAL_INTENT_M8,
            "temporal_verdict": "TEMPORAL_UNIVERSE_OPTIONAL",
            "approach": "windowed_trade_level_transition_feature",
        },
    )
