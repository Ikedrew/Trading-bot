"""
WAVE 8 — Strategy / Edge Depth Research Runners.

Implements:
    E4 — Strategy × pattern combinations (interaction effects)
    L5 — Model/strategy drift over time (temporal stability)

Each runner uses the canonical Research Engine data-access layer,
reads shadow_trades evidence, and returns a structured report.

No production/writer schemas are modified.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from research_engine.data_quality.classifier import DataEpoch, classify_record
from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_field(record: dict[str, Any], *keys: str) -> Any:
    """Extract a field from a shadow trade record, trying nested structures."""
    ds = record.get("decision_snapshot", {}) or {}
    identity = record.get("identity", {}) or {}
    for key in keys:
        for container in (ds, identity, record):
            val = container.get(key)
            if val is not None and val != "":
                return val
    return None


def _extract_r(record: dict[str, Any]) -> float | None:
    """Extract R-multiple from shadow trade record."""
    outcome = record.get("simulated_outcome", {}) or {}
    r = outcome.get("pnl_r_multiple") or outcome.get("r_multiple")
    if r is not None:
        try:
            return float(r)
        except (TypeError, ValueError):
            return None
    return None


def _extract_strategy(record: dict[str, Any]) -> str:
    """Extract canonical strategy."""
    val = _extract_field(record, "strategy", "strategy_id")
    if val in ("REVERSAL", "CONTINUATION", "FALSE_BREAK"):
        return val
    return ""


def _extract_pattern(record: dict[str, Any]) -> str:
    """Extract pattern identity."""
    val = _extract_field(record, "pattern", "pattern_detected")
    if val and isinstance(val, str) and len(val.strip()) > 0:
        return val
    return ""


def _extract_entry_time(record: dict[str, Any]) -> float | None:
    """Extract entry time (epoch seconds) from a shadow trade record."""
    val = _extract_field(
        record, "entry_time", "timestamp", "timestamp_utc"
    )
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    return None


def _segmented_stats(records: list[dict[str, Any]],
                     extractors: dict[str, callable]) -> dict[str, Any]:
    """
    Compute per-segment statistics for a list of records.
    Returns {segments: {key: {count, mean_r, win_rate, wins, losses}},
             total_analysed: int, overall_mean_r: float, overall_win_rate: float}
    """
    segments: dict[str, list[float]] = defaultdict(list)
    r_field = "pnl_r_multiple"
    used = 0

    for rec in records:
        key_parts = []
        valid = True
        for dim_name, extract_fn in extractors.items():
            val = extract_fn(rec)
            if not val:
                valid = False
                break
            key_parts.append(str(val))
        if not valid:
            continue
        r = _extract_r(rec)
        if r is None:
            continue
        segment_key = " | ".join(key_parts)
        segments[segment_key].append(r)
        used += 1

    result_segments = {}
    all_r = []
    for key, vals in sorted(segments.items()):
        n = len(vals)
        all_r.extend(vals)
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v <= 0]
        mean_r = sum(vals) / n if n else 0
        result_segments[key] = {
            "count": n,
            "mean_r": round(mean_r, 4),
            "win_rate": round(len(wins) / n, 4) if n else 0,
            "wins": len(wins),
            "losses": len(losses),
        }

    total_n = len(all_r)
    if total_n == 0:
        return {
            "segments": {},
            "total_analysed": 0,
            "overall_mean_r": 0,
            "overall_win_rate": 0,
        }

    overall_wins = sum(1 for r in all_r if r > 0)
    return {
        "segments": result_segments,
        "total_analysed": total_n,
        "overall_mean_r": round(sum(all_r) / total_n, 4),
        "overall_win_rate": round(overall_wins / total_n, 4),
    }


def _compute_segment_r(
    records: list[dict[str, Any]],
    field: str,
) -> dict[str, Any]:
    """Compute R-multiple stats segmented by a single categorical field."""
    r_by_segment: dict[str, list[float]] = defaultdict(list)
    used = 0
    for rec in records:
        val = _extract_field(rec, field)
        if not val:
            continue
        r = _extract_r(rec)
        if r is None:
            continue
        r_by_segment[str(val)].append(r)
        used += 1

    segments = {}
    all_r = []
    for seg, vals in sorted(r_by_segment.items()):
        n = len(vals)
        all_r.extend(vals)
        wins = [v for v in vals if v > 0]
        mean_r = sum(vals) / n if n else 0
        segments[seg] = {
            "count": n,
            "mean_r": round(mean_r, 4),
            "win_rate": round(len(wins) / n, 4) if n else 0,
            "wins": len(wins),
            "losses": n - len(wins),
        }

    total_n = len(all_r)
    if total_n == 0:
        return {
            "segments": {},
            "total_analysed": 0,
            "overall_mean_r": 0,
            "overall_win_rate": 0,
        }

    overall_wins = sum(1 for r in all_r if r > 0)
    return {
        "segments": segments,
        "total_analysed": total_n,
        "overall_mean_r": round(sum(all_r) / total_n, 4),
        "overall_win_rate": round(overall_wins / total_n, 4),
    }


def _assess_confidence(total_analysed: int) -> str:
    """Map sample size to confidence level using engine conventions."""
    if total_analysed >= 200:
        return "HIGH"
    elif total_analysed >= 50:
        return "MEDIUM"
    elif total_analysed >= 20:
        return "LOW"
    return "INSUFFICIENT_DATA"


def _assess_edge(
    segments: dict[str, dict[str, Any]],
    total_analysed: int,
) -> tuple[str, str]:
    """Assess overall edge direction and confidence."""
    if total_analysed == 0 or not segments:
        return "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"

    positive_segments = sum(
        1 for s in segments.values() if s.get("mean_r", 0) > 0
    )
    total_segments = len(segments)
    positive_pct = positive_segments / total_segments

    confidence = _assess_confidence(total_analysed)

    if positive_pct > 0.6:
        if confidence in ("HIGH", "MEDIUM"):
            return "POSITIVE_EDGE", confidence
        return "MARGINAL_EDGE", confidence
    elif positive_pct < 0.3:
        if confidence in ("HIGH", "MEDIUM"):
            return "NEGATIVE_EDGE", confidence
        return "MARGINAL_EDGE", confidence
    return "MIXED_EDGE", confidence


def _segment_dispersion(segments: dict) -> float:
    """Standard deviation of mean_r across segments."""
    means = [s.get("mean_r", 0) for s in segments.values()]
    if len(means) <= 1:
        return 0.0
    avg = sum(means) / len(means)
    var = sum((m - avg) ** 2 for m in means) / len(means)
    return var ** 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# E4 — Strategy × pattern combinations
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_E4 = (
    "Which strategy × pattern combinations produce edge? "
    "(e.g. REVERSAL + TWEEZER_TOP)"
    " — interaction effect between strategy family and pattern identity"
)


def run_e4() -> dict[str, Any]:
    """
    Run E4: Strategy × pattern combinations.

    Segments shadow trades by strategy AND pattern simultaneously,
    computing mean R-multiple per combination.

    This question is distinct from:
        E2 (pattern-only) — ignores strategy context
        E3 (strategy-only) — ignores pattern context

    E4 asks: does the same pattern behave differently under different
    strategies? e.g. HAMMER with REVERSAL may differ from HAMMER with
    CONTINUATION. This is a genuinely interaction-based question.
    """
    question_id = "E4"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    extractors = {
        "strategy": _extract_strategy,
        "pattern": _extract_pattern,
    }

    result = _segmented_stats(current, extractors)
    segments = result.get("segments", {})
    total_analysed = result.get("total_analysed", 0)

    edge, confidence = _assess_edge(segments, total_analysed)

    # Compare with strategy-only and pattern-only dispersion
    strategy_segs = _compute_segment_r(current, "strategy")
    pattern_segs = _compute_segment_r(current, "pattern")
    strategy_disp = _segment_dispersion(strategy_segs.get("segments", {}))
    pattern_disp = _segment_dispersion(pattern_segs.get("segments", {}))
    combined_disp = _segment_dispersion(segments)

    interaction_detected = combined_disp > max(strategy_disp, pattern_disp) * 1.15

    overall = {
        "question": CANONICAL_INTENT_E4,
        "segmentation": "strategy × pattern",
        "segments": segments,
        "strategy_only": {
            "segments": strategy_segs.get("segments", {}),
            "dispersion": round(strategy_disp, 4),
            "total_analysed": strategy_segs.get("total_analysed", 0),
        },
        "pattern_only": {
            "segments": pattern_segs.get("segments", {}),
            "dispersion": round(pattern_disp, 4),
            "total_analysed": pattern_segs.get("total_analysed", 0),
        },
        "interaction_analysis": {
            "combined_dispersion": round(combined_disp, 4),
            "interaction_detected": interaction_detected,
            "note": (
                "Interaction detected means strategy×pattern segmentation "
                "produces wider R-multiple dispersion than either strategy "
                "or pattern alone — suggesting the combo matters"
            ),
        },
        "edge_assessment": edge,
        "distinct_from": {
            "E2": "E2 asks pattern-only expectancy (ignores strategy)",
            "E3": "E3 asks strategy-only expectancy (ignores pattern)",
        },
        "total_combinations": len(segments),
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed,
        "total_loaded": current_count,
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
            "canonical_intent": CANONICAL_INTENT_E4,
            "analysis_type": "strategy × pattern interaction segmentation",
            "evidence_classification": "historical association (not causal proof)",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# L5 — Model drift detection (strategy temporal stability)
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_L5 = (
    "Do strategies lose predictive power over time? "
    "Measures whether strategy-level R-multiple differs materially "
    "between early and later time periods."
)

_OWNERSHIP_NOTE_L5 = (
    "L5 uniquely owns: 'Do strategies degrade over time?'\n"
    "L1 (pattern degradation) owns pattern-level temporal drift.\n"
    "L4 (market drift) owns market-context temporal change.\n"
    "E5 (out-of-sample) tests if overall edge survives unseen data.\n"
    "L5 fills the gap: strategy-level temporal stability.\n"
    "Overlap note: temporal analysis inherently touches concepts "
    "owned by L1 and L4, but L5's unit of analysis (strategy) "
    "is distinct from L1 (pattern) and L4 (market context)."
)


def run_l5() -> dict[str, Any]:
    """
    Run L5: Strategy temporal stability / drift detection.

    Chronologically splits shadow trades into early vs later halves
    per strategy. Reports whether each strategy's mean R-multiple
    materially changes between periods.

    Does NOT claim to prove drift direction — it reports descriptive
    change which can inform further investigation.

    Uses chronological ordering (entry_time) to avoid future leakage.
    """
    question_id = "L5"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Filter to records with both strategy and entry_time
    analysis_records = []
    for rec in current:
        strat = _extract_strategy(rec)
        entry_time = _extract_entry_time(rec)
        r = _extract_r(rec)
        if strat and entry_time is not None and r is not None:
            analysis_records.append({
                "strategy": strat,
                "entry_time": entry_time,
                "r": r,
            })

    total_analysed = len(analysis_records)

    if total_analysed == 0:
        overall = {
            "question": CANONICAL_INTENT_L5,
            "ownership_note": _OWNERSHIP_NOTE_L5,
            "result": "No analysable records (need strategy + entry_time + r)",
        }
        dataset = {"source": "shadow_trades", "sample_size": 0}
        fingerprint = build_fingerprint(
            records_used=0, records_excluded=excluded,
            source="shadow_trades", validation_score="INSUFFICIENT_DATA",
        )
        return build_report(
            question_id=question_id,
            status="INSUFFICIENT_DATA",
            overall=overall,
            confidence="INSUFFICIENT_DATA",
            dataset=dataset,
            fingerprint=fingerprint,
            recommendation="INSUFFICIENT_DATA",
            warnings=[f"{excluded} LEGACY/TRANSITIONAL records excluded"],
            provenance={
                "experiment_module": __name__,
                "registry_id": question_id,
                "canonical_intent": CANONICAL_INTENT_L5,
            },
        )

    # Chronological split per strategy
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for rec in analysis_records:
        by_strategy[rec["strategy"]].append(rec)

    # Sort each strategy's records by entry_time
    for strat, recs in by_strategy.items():
        recs.sort(key=lambda r: r["entry_time"])

    strategy_analysis = {}
    strategy_drift_found = False
    strategy_drift_details = []

    for strat, recs in sorted(by_strategy.items()):
        n = len(recs)
        if n < 20:
            strategy_analysis[strat] = {
                "count": n,
                "status": "insufficient_data",
                "note": "Fewer than 20 records — cannot assess temporal stability",
            }
            continue

        # Split into early (first 50%) and late (last 50%)
        mid = n // 2
        early = recs[:mid]
        late = recs[-mid:] if mid > 0 else recs

        early_r = [r["r"] for r in early]
        late_r = [r["r"] for r in late]

        def _stats(vals):
            if not vals:
                return {"n": 0, "mean_r": None, "win_rate": None}
            nv = len(vals)
            wins = sum(1 for v in vals if v > 0)
            return {
                "n": nv,
                "mean_r": round(sum(vals) / nv, 4),
                "win_rate": round(wins / nv, 4) if nv else 0,
            }

        early_stats = _stats(early_r)
        late_stats = _stats(late_r)

        change = (
            (late_stats["mean_r"] - early_stats["mean_r"])
            if early_stats["mean_r"] is not None and late_stats["mean_r"] is not None
            else None
        )

        changed = abs(change) > 0.2 if change is not None else False
        if changed:
            strategy_drift_found = True

        strat_result = {
            "count": n,
            "early_period": {
                "records": early_stats["n"],
                "mean_r": early_stats["mean_r"],
                "win_rate": early_stats["win_rate"],
                "start_time": recs[0]["entry_time"],
                "end_time": early[-1]["entry_time"],
            },
            "late_period": {
                "records": late_stats["n"],
                "mean_r": late_stats["mean_r"],
                "win_rate": late_stats["win_rate"],
                "start_time": late[0]["entry_time"],
                "end_time": recs[-1]["entry_time"],
            },
            "change_mean_r": round(change, 4) if change is not None else None,
            "material_change_detected": changed,
        }
        strategy_analysis[strat] = strat_result
        if changed:
            direction = "improving" if change > 0 else "degrading"
            strategy_drift_details.append(
                f"{strat}: mean_r changed by {change:+.4f} ({direction})"
            )

    # Determine overall edge role
    stable_strategies = sum(
        1 for v in strategy_analysis.values()
        if isinstance(v, dict) and v.get("material_change_detected") is False
    )
    drifting_strategies = sum(
        1 for v in strategy_analysis.values()
        if isinstance(v, dict) and v.get("material_change_detected") is True
    )

    if drifting_strategies > 0:
        if stable_strategies > drifting_strategies:
            edge = "PARTIAL_DRIFT"
        else:
            edge = "DRIFT_DETECTED"
    elif total_analysed >= 50:
        edge = "STABLE"
    else:
        edge = "INSUFFICIENT_DATA"

    confidence = _assess_confidence(total_analysed)
    if edge in ("DRIFT_DETECTED", "PARTIAL_DRIFT") and confidence in ("HIGH", "MEDIUM"):
        pass  # Keep as is
    elif edge == "STABLE" and confidence == "HIGH":
        pass  # Highest confidence result
    else:
        confidence = _assess_confidence(total_analysed)

    overall = {
        "question": CANONICAL_INTENT_L5,
        "ownership_note": _OWNERSHIP_NOTE_L5,
        "related_questions": {
            "L1": "Owns pattern-level temporal degradation",
            "L4": "Owns market-context temporal drift",
            "E5": "Owns out-of-sample walk-forward validation",
        },
        "method": "chronological_split_early_vs_late_per_strategy",
        "strategy_analysis": strategy_analysis,
        "drift_summary": {
            "total_strategies_analysed": len(strategy_analysis),
            "stable_count": stable_strategies,
            "drift_count": drifting_strategies,
            "drift_details": strategy_drift_details,
        },
        "overall_verdict": edge,
        "limitation": (
            "Chronological split is descriptive, not predictive. "
            "Detected drift indicates a historical change in observed "
            "outcomes, not necessarily a continuing trend. "
            "Small early/late cell sizes reduce reliability."
        ),
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed,
        "total_loaded": current_count,
    }

    fingerprint = build_fingerprint(
        records_used=total_analysed,
        records_excluded=excluded,
        source="shadow_trades",
        validation_score=confidence,
    )

    return build_report(
        question_id=question_id,
        status="COMPLETE" if total_analysed >= 20 else "INSUFFICIENT_DATA",
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
            "canonical_intent": CANONICAL_INTENT_L5,
            "method": "chronological_split",
            "evidence_classification": "historical descriptive (not predictive)",
            "ownership_disclaimer": _OWNERSHIP_NOTE_L5,
        },
    )