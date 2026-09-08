"""
WAVE 7 — Market / Context Depth Research Runners.

Implements M2, M3, M4, M6, M7, and M11 using the canonical Research Engine
data-access layer and report contract.

Each runner:
    - Reads shadow_trades (the active V1 evidence population)
    - Extracts canonical market/context fields from decision_snapshot
    - Computes structured metrics
    - Returns a report conforming to the canonical report contract

M5 and M8 are handled separately in market_temporal.py.

Quarantine safety:
    - Raw market evidence from completed shadow trades remains valid.
    - The forming-bar decision defect contaminated decision-derived assessments
      and strategy_observations, but the shadow_trades outcome records come
      from the shadow_runtime ingestion path which uses CLOSED-bar evidence only.
    - This module reads shadow_trades, not the quarantined datasets.

No production/writer schemas are modified.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
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
    """Extract a nested field from a shadow trade record."""
    decision_snapshot = record.get("decision_snapshot", {}) or {}
    identity = record.get("identity", {}) or {}
    for key in keys:
        # Try decision_snapshot first (canonical context fields)
        val = decision_snapshot.get(key)
        if val is not None and val != "":
            return val
        # Try identity
        val = identity.get(key)
        if val is not None and val != "":
            return val
        # Try top-level
        val = record.get(key)
        if val is not None and val != "":
            return val
    return None


def _extract_r_multiple(record: dict[str, Any]) -> float | None:
    """Extract R-multiple outcome from a shadow trade record."""
    outcome = record.get("simulated_outcome", {}) or {}
    r = outcome.get("pnl_r_multiple") or outcome.get("r_multiple")
    if r is not None:
        try:
            return float(r)
        except (TypeError, ValueError):
            return None
    return None


def _extract_strategy(record: dict[str, Any]) -> str:
    """Extract canonical strategy label."""
    val = _extract_field(record, "strategy", "strategy_id")
    if val and val in ("REVERSAL", "CONTINUATION", "FALSE_BREAK"):
        return val
    return ""


def _extract_regime(record: dict[str, Any]) -> str:
    """Extract H4 regime."""
    val = _extract_field(record, "h4_regime", "regime")
    if val in ("TRENDING", "RANGING", "TRANSITIONAL"):
        return val
    return ""


def _extract_market_phase(record: dict[str, Any]) -> str:
    """Extract market phase."""
    val = _extract_field(
        record, "market_phase", "phase", "h4_market_phase"
    )
    if val in ("IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"):
        return val
    return ""


def _extract_pattern(record: dict[str, Any]) -> str:
    """Extract pattern identity."""
    val = _extract_field(record, "pattern", "pattern_detected")
    if val and isinstance(val, str) and val.strip():
        return val
    return ""


def _extract_h1_bias(record: dict[str, Any]) -> str:
    """Extract H1 bias direction."""
    val = _extract_field(record, "h1_bias", "h1_dominant_trend")
    if val in ("BULLISH", "BEARISH", "NEUTRAL"):
        return val
    return ""


def _segment_r_multiples(
    records: list[dict[str, Any]],
    dimension_keys: list[str],
    extractors: dict[str, callable],
) -> dict[str, Any]:
    """
    Segment records by one or more dimension keys and compute R-multiple stats.

    Returns a dict with segments and aggregate metrics.
    """
    segments: dict[str, dict[str, Any]] = {}
    used_count = 0

    for rec in records:
        # Build segment key from extractors
        key_parts = []
        valid = True
        for dim in dimension_keys:
            fn = extractors.get(dim)
            if fn is None:
                valid = False
                break
            val = fn(rec)
            if not val:  # empty string = missing
                valid = False
                break
            key_parts.append(str(val))

        if not valid:
            continue

        r = _extract_r_multiple(rec)
        if r is None:
            continue

        segment_key = " | ".join(key_parts)
        if segment_key not in segments:
            segments[segment_key] = {
                "r_values": [],
                "count": 0,
            }
        segments[segment_key]["r_values"].append(r)
        segments[segment_key]["count"] += 1
        used_count += 1

    # Compute metrics per segment
    result_segments = {}
    total_r_values = []
    for key, data in sorted(segments.items()):
        r_values = data["r_values"]
        n = len(r_values)
        if n == 0:
            continue
        total_r_values.extend(r_values)
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r <= 0]
        mean_r = sum(r_values) / n
        win_rate = len(wins) / n if n else 0
        result_segments[key] = {
            "count": n,
            "mean_r": round(mean_r, 4),
            "win_rate": round(win_rate, 4),
            "wins": len(wins),
            "losses": len(losses),
        }

    # Aggregate
    total_n = len(total_r_values)
    if total_n == 0:
        return {
            "segments": {},
            "total_analysed": 0,
            "overall_mean_r": 0,
            "overall_win_rate": 0,
        }

    overall_mean = sum(total_r_values) / total_n
    overall_wins = sum(1 for r in total_r_values if r > 0)
    overall_win_rate = overall_wins / total_n

    return {
        "segments": result_segments,
        "total_analysed": total_n,
        "overall_mean_r": round(overall_mean, 4),
        "overall_win_rate": round(overall_win_rate, 4),
    }


def _compute_segment_r(
    records: list[dict[str, Any]],
    dimension_field: str,
) -> dict[str, Any]:
    """Compute R-multiple stats segmented by a single categorical field."""
    r_by_segment: dict[str, list[float]] = defaultdict(list)
    used = 0
    for rec in records:
        val = _extract_field(rec, dimension_field)
        if not val:
            continue
        r = _extract_r_multiple(rec)
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
        losses = [v for v in vals if v <= 0]
        mean_r = sum(vals) / n if n else 0
        segments[seg] = {
            "count": n,
            "mean_r": round(mean_r, 4),
            "win_rate": round(len(wins) / n, 4) if n else 0,
            "wins": len(wins),
            "losses": len(losses),
        }

    total_n = len(all_r)
    if total_n == 0:
        return {
            "segments": segments,
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


def _assess_edge(
    segments: dict[str, dict[str, Any]], total_analysed: int
) -> tuple[str, str]:
    """Assess overall edge and confidence from segment results."""
    if total_analysed == 0:
        return "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"

    # Check for positive edge in any segment
    positive_segments = sum(
        1 for s in segments.values() if s.get("mean_r", 0) > 0
    )
    total_segments = len(segments)

    if total_segments == 0:
        return "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"

    positive_pct = positive_segments / total_segments

    if total_analysed >= 200:
        confidence = "HIGH"
    elif total_analysed >= 50:
        confidence = "MEDIUM"
    elif total_analysed >= 20:
        confidence = "LOW"
    else:
        confidence = "INSUFFICIENT_DATA"

    if positive_pct > 0.6:
        if confidence in ("HIGH", "MEDIUM"):
            return "POSITIVE_EDGE", confidence
        return "MARGINAL_EDGE", confidence
    elif positive_pct < 0.3:
        if confidence in ("HIGH", "MEDIUM"):
            return "NEGATIVE_EDGE", confidence
        return "MARGINAL_EDGE", confidence
    else:
        return "MIXED_EDGE", confidence


# ═══════════════════════════════════════════════════════════════════════════════
# M2 — H4 regime edge by strategy
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M2 = (
    "Which H4 regimes produce edge for each strategy type?"
)


def run_m2() -> dict[str, Any]:
    """
    Run M2: H4 regime × strategy segmentation.

    Segments shadow trade outcomes by h4_regime and strategy,
    computing mean R-multiple and win rate per combination.
    This is a DESCRIPTIVE market question — it observes which
    regime+strategy combinations contain edge, independently
    of whether trades were actually executed.
    """
    question_id = "M2"
    all_records = load_shadow_trades()

    # Filter to CURRENT epoch only
    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Segment by regime × strategy
    extractors = {
        "regime": _extract_regime,
        "strategy": _extract_strategy,
    }

    result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "strategy"],
        extractors=extractors,
    )

    segments = result.get("segments", {})
    total_analysed = result.get("total_analysed", 0)

    edge, confidence = _assess_edge(segments, total_analysed)

    # Count regime-only coverage
    regimes_found = set()
    for key in segments:
        parts = key.split(" | ")
        if len(parts) > 0:
            regimes_found.add(parts[0])

    overall = {
        "question": CANONICAL_INTENT_M2,
        "segmentation": "h4_regime × strategy",
        "segments": segments,
        "regimes_found": sorted(regimes_found),
        "total_regime_strategy_combos": len(segments),
        "edge_assessment": edge,
        "total_records": total_records,
        "current_epoch_records": current_count,
        "records_with_regime_and_strategy": total_analysed,
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
            "canonical_intent": CANONICAL_INTENT_M2,
            "data_classification": "market_observation_with_outcome",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M3 — Phase improves prediction beyond regime
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M3 = (
    "Does market_phase improve prediction beyond H4 regime alone?"
)


def run_m3() -> dict[str, Any]:
    """
    Run M3: Phase improves prediction beyond regime.

    Compares explanatory power of regime-only vs regime+phase.
    If the regime+phase segmentation shows wider R-multiple dispersion
    than regime alone, phase adds predictive value.
    """
    question_id = "M3"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Regime-only segmentation
    regime_result = _compute_segment_r(current, "h4_regime")

    # Regime + Phase segmentation
    extractors = {
        "regime": _extract_regime,
        "phase": _extract_market_phase,
    }
    regime_phase_result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "phase"],
        extractors=extractors,
    )

    regime_only_segments = regime_result.get("segments", {})
    regime_phase_segments = regime_phase_result.get("segments", {})
    total_analysed_regime = regime_result.get("total_analysed", 0)
    total_analysed_combined = regime_phase_result.get("total_analysed", 0)

    # Compute dispersion metrics
    def _dispersion(seg_dict: dict) -> float:
        means = [s.get("mean_r", 0) for s in seg_dict.values()]
        if len(means) <= 1:
            return 0.0
        avg = sum(means) / len(means)
        variance = sum((m - avg) ** 2 for m in means) / len(means)
        return variance ** 0.5

    regime_dispersion = _dispersion(regime_only_segments)
    combined_dispersion = _dispersion(regime_phase_segments)

    phase_adds_value = combined_dispersion > regime_dispersion * 1.15

    # Find phase-only segments for comparison
    phase_segments = _compute_segment_r(current, "market_phase")

    edge, confidence = _assess_edge(
        regime_phase_segments, total_analysed_combined
    )

    overall = {
        "question": CANONICAL_INTENT_M3,
        "regime_only": {
            "segments": regime_only_segments,
            "total_analysed": total_analysed_regime,
            "dispersion": round(regime_dispersion, 4),
        },
        "regime_and_phase": {
            "segments": regime_phase_segments,
            "total_analysed": total_analysed_combined,
            "dispersion": round(combined_dispersion, 4),
        },
        "phase_only": {
            "segments": phase_segments.get("segments", {}),
            "total_analysed": phase_segments.get("total_analysed", 0),
        },
        "phase_adds_predictive_value": phase_adds_value,
        "regime_count": len(regime_only_segments),
        "regime_phase_combinations": len(regime_phase_segments),
        "edge_assessment": edge,
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed_combined,
        "regime_only_sample": total_analysed_regime,
    }

    fingerprint = build_fingerprint(
        records_used=total_analysed_combined,
        records_excluded=excluded,
        source="shadow_trades",
        validation_score=confidence,
    )

    return build_report(
        question_id=question_id,
        status="COMPLETE" if total_analysed_combined > 0 else "INSUFFICIENT_DATA",
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
            "canonical_intent": CANONICAL_INTENT_M3,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M4 — Regime × phase × strategy edge
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M4 = (
    "Which regime × phase × strategy combinations produce edge?"
)


def run_m4() -> dict[str, Any]:
    """
    Run M4: 3-way interaction of regime × phase × strategy.

    Segments by all three dimensions. Small cell counts are expected
    — this is inherently a data-hungry question.
    """
    question_id = "M4"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    extractors = {
        "regime": _extract_regime,
        "phase": _extract_market_phase,
        "strategy": _extract_strategy,
    }

    result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "phase", "strategy"],
        extractors=extractors,
    )

    segments = result.get("segments", {})
    total_analysed = result.get("total_analysed", 0)

    edge, confidence = _assess_edge(segments, total_analysed)

    # Count non-empty cells
    populated_combos = len(segments)
    expected_combos = 3 * 5 * 3  # 3 regimes × 5 phases × 3 strategies

    overall = {
        "question": CANONICAL_INTENT_M4,
        "segmentation": "h4_regime × market_phase × strategy",
        "segments": segments,
        "populated_combinations": populated_combos,
        "expected_combinations": expected_combos,
        "coverage_pct": round(populated_combos / expected_combos * 100, 1)
        if expected_combos > 0
        else 0,
        "edge_assessment": edge,
        "small_cell_warning": (
            "3-way interaction requires large N — cells with <5 records "
            "are statistically unreliable"
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
            "canonical_intent": CANONICAL_INTENT_M4,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M6 — Market phase expectancy
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M6 = (
    "Which market phases contain real edge?"
)


def run_m6() -> dict[str, Any]:
    """
    Run M6: Market phase expectancy.

    Segments shadow trades by market_phase and computes
    mean R-multiple and win rate per phase.
    """
    question_id = "M6"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    result = _compute_segment_r(current, "market_phase")
    segments = result.get("segments", {})
    total_analysed = result.get("total_analysed", 0)

    edge, confidence = _assess_edge(segments, total_analysed)

    overall = {
        "question": CANONICAL_INTENT_M6,
        "segmentation": "market_phase",
        "segments": segments,
        "phases_found": sorted(segments.keys()),
        "edge_assessment": edge,
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
            "canonical_intent": CANONICAL_INTENT_M6,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M7 — Regime + phase interaction
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M7 = (
    "Does combining regime and phase improve predictive power beyond either alone?"
)


def run_m7() -> dict[str, Any]:
    """
    Run M7: Regime + phase interaction vs either alone.

    Compares three models:
        1. Regime-only expectancy
        2. Phase-only expectancy
        3. Regime + phase combined expectancy
    """
    question_id = "M7"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Regime-only
    regime_result = _compute_segment_r(current, "h4_regime")
    regime_dispersion = _dispersion_from_segments(
        regime_result.get("segments", {})
    )

    # Phase-only
    phase_result = _compute_segment_r(current, "market_phase")
    phase_dispersion = _dispersion_from_segments(
        phase_result.get("segments", {})
    )

    # Combined
    extractors = {
        "regime": _extract_regime,
        "phase": _extract_market_phase,
    }
    combined_result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "phase"],
        extractors=extractors,
    )
    combined_dispersion = _dispersion_from_segments(
        combined_result.get("segments", {})
    )
    total_analysed = combined_result.get("total_analysed", 0)

    # Determine which adds more
    phase_over_regime = phase_dispersion > regime_dispersion
    combined_over_best = combined_dispersion > max(
        regime_dispersion, phase_dispersion
    )

    edge, confidence = _assess_edge(
        combined_result.get("segments", {}), total_analysed
    )

    overall = {
        "question": CANONICAL_INTENT_M7,
        "regime_only": {
            "segments": regime_result.get("segments", {}),
            "total_analysed": regime_result.get("total_analysed", 0),
            "dispersion": round(regime_dispersion, 4),
        },
        "phase_only": {
            "segments": phase_result.get("segments", {}),
            "total_analysed": phase_result.get("total_analysed", 0),
            "dispersion": round(phase_dispersion, 4),
        },
        "regime_and_phase": {
            "segments": combined_result.get("segments", {}),
            "total_analysed": total_analysed,
            "dispersion": round(combined_dispersion, 4),
        },
        "phase_adds_more_than_regime": phase_over_regime,
        "combined_adds_value": combined_over_best,
        "edge_assessment": edge,
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed,
        "regime_sample": regime_result.get("total_analysed", 0),
        "phase_sample": phase_result.get("total_analysed", 0),
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
            "canonical_intent": CANONICAL_INTENT_M7,
        },
    )


def _dispersion_from_segments(segments: dict) -> float:
    """Compute standard deviation of mean_r across segments."""
    means = [s.get("mean_r", 0) for s in segments.values()]
    if len(means) <= 1:
        return 0.0
    avg = sum(means) / len(means)
    variance = sum((m - avg) ** 2 for m in means) / len(means)
    return variance ** 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# M11 — Context predictive value vs pattern
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_M11 = (
    "Does market context provide more predictive value for trade outcomes "
    "than the pattern identity itself?"
)


def run_m11() -> dict[str, Any]:
    """
    Run M11: Context predictive value vs pattern.

    Compares:
        1. Pattern-only segmentation of R-multiple
        2. Context (regime + phase + bias) segmentation
        3. Context + pattern combined segmentation

    If context-alone dispersion > pattern-alone dispersion,
    context is more predictive.
    """
    question_id = "M11"
    all_records = load_shadow_trades()

    current = [r for r in all_records if classify_record(r) == DataEpoch.CURRENT]
    total_records = len(all_records)
    current_count = len(current)
    excluded = total_records - current_count

    # Pattern-only
    pattern_segments = _compute_segment_r(current, "pattern")
    pattern_disp = _dispersion_from_segments(
        pattern_segments.get("segments", {})
    )

    # Context-only (regime + phase + h1_bias)
    ctx_extractors = {
        "regime": _extract_regime,
        "phase": _extract_market_phase,
        "bias": _extract_h1_bias,
    }
    context_result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "phase", "bias"],
        extractors=ctx_extractors,
    )
    context_disp = _dispersion_from_segments(
        context_result.get("segments", {})
    )

    # Context + pattern combined
    combined_extractors = {
        "regime": _extract_regime,
        "phase": _extract_market_phase,
        "bias": _extract_h1_bias,
        "pattern": _extract_pattern,
    }
    combined_result = _segment_r_multiples(
        current,
        dimension_keys=["regime", "phase", "bias", "pattern"],
        extractors=combined_extractors,
    )
    combined_disp = _dispersion_from_segments(
        combined_result.get("segments", {})
    )
    total_analysed = combined_result.get("total_analysed", 0)

    context_more_predictive = context_disp > pattern_disp * 1.15
    combined_over_best = combined_disp > max(context_disp, pattern_disp)

    edge, confidence = _assess_edge(
        combined_result.get("segments", {}), total_analysed
    )

    overall = {
        "question": CANONICAL_INTENT_M11,
        "pattern_only": {
            "segments": pattern_segments.get("segments", {}),
            "total_analysed": pattern_segments.get("total_analysed", 0),
            "dispersion": round(pattern_disp, 4),
        },
        "context_only": {
            "segments": context_result.get("segments", {}),
            "total_analysed": context_result.get("total_analysed", 0),
            "dispersion": round(context_disp, 4),
        },
        "context_and_pattern": {
            "segments": combined_result.get("segments", {}),
            "total_analysed": total_analysed,
            "dispersion": round(combined_disp, 4),
        },
        "context_more_predictive_than_pattern": context_more_predictive,
        "combined_adds_value": combined_over_best,
        "edge_assessment": edge,
    }

    dataset = {
        "source": "shadow_trades",
        "sample_size": total_analysed,
        "pattern_sample": pattern_segments.get("total_analysed", 0),
        "context_sample": context_result.get("total_analysed", 0),
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
            "canonical_intent": CANONICAL_INTENT_M11,
        },
    )