"""
Edge Attribution Analyser — Discovers conditions with positive expectancy.

Performs:
    - Single-feature breakdown (regime, pattern, session, etc.)
    - Multi-dimensional analysis (pattern × regime, pattern × session)
    - Feature importance ranking
    - Pattern dependency analysis
    - Statistical protection against false discoveries
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from research_engine.edge_attribution.models import (
    EdgeAttributionRecord,
    ConditionPerformance,
    FeatureImportance,
    _compute_stats,
    _MIN_SAMPLES_LOW,
    _MIN_SAMPLES_MEDIUM,
)

logger = logging.getLogger(__name__)


@dataclass
class EdgeAnalysisResult:
    """Complete edge attribution analysis result."""
    total_records: int = 0

    # Single feature breakdowns
    single_features: dict[str, list[ConditionPerformance]] = field(default_factory=dict)

    # Multi-dimensional (combinations)
    combinations: list[dict[str, Any]] = field(default_factory=list)

    # Feature importance ranking
    importance: list[FeatureImportance] = field(default_factory=list)

    # Pattern dependency
    pattern_dependency: list[dict[str, Any]] = field(default_factory=list)

    # Edge candidates (positive EV with sufficient confidence)
    edge_candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "single_features": {k: [c.to_dict() for c in v] for k, v in self.single_features.items()},
            "combinations": self.combinations,
            "importance": [i.to_dict() for i in self.importance],
            "pattern_dependency": self.pattern_dependency,
            "edge_candidates": self.edge_candidates,
            "warnings": self.warnings,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE FEATURE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

_FEATURES_TO_ANALYSE = [
    "regime", "pattern", "session", "direction", "market_state",
    "htf_alignment_bin", "trend_alignment_bin", "bias_alignment_bin",
    "score_bin", "confirmation_bin", "symbol", "strategy",
]


def _analyse_single_feature(
    feature: str,
    records: list[EdgeAttributionRecord],
) -> list[ConditionPerformance]:
    """Breakdown by one feature's values."""
    groups: dict[str, list[float]] = defaultdict(list)
    for r in records:
        val = getattr(r, feature, "UNKNOWN")
        groups[val].append(r.result_r)

    results = []
    for val, rs in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(rs) >= _MIN_SAMPLES_LOW:
            results.append(ConditionPerformance(feature=feature, value=val, stats=_compute_stats(rs)))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-DIMENSIONAL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

_COMBINATIONS = [
    ("pattern", "regime"),
    ("pattern", "session"),
    ("pattern", "htf_alignment_bin"),
    ("pattern", "trend_alignment_bin"),
    ("regime", "session"),
    ("regime", "htf_alignment_bin"),
    ("direction", "regime"),
]


def _analyse_combinations(records: list[EdgeAttributionRecord]) -> list[dict[str, Any]]:
    """Multi-dimensional breakdowns."""
    results = []
    for feat_a, feat_b in _COMBINATIONS:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in records:
            val_a = getattr(r, feat_a, "?")
            val_b = getattr(r, feat_b, "?")
            key = f"{val_a} + {val_b}"
            groups[key].append(r.result_r)

        for key, rs in sorted(groups.items(), key=lambda x: -len(x[1])):
            if len(rs) >= _MIN_SAMPLES_LOW:
                stats = _compute_stats(rs)
                results.append({
                    "features": f"{feat_a} × {feat_b}",
                    "value": key,
                    **stats,
                })

    # Sort by EV descending
    results.sort(key=lambda x: x.get("ev", 0), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE IMPORTANCE
# ═══════════════════════════════════════════════════════════════════════════════

def _rank_importance(single_features: dict[str, list[ConditionPerformance]]) -> list[FeatureImportance]:
    """Rank features by the spread between best and worst condition EV."""
    rankings = []
    for feature, conditions in single_features.items():
        if len(conditions) < 2:
            continue
        evs = [(c.value, c.stats.get("ev", 0), c.stats.get("n", 0)) for c in conditions if c.stats.get("n", 0) >= _MIN_SAMPLES_LOW]
        if len(evs) < 2:
            continue

        best = max(evs, key=lambda x: x[1])
        worst = min(evs, key=lambda x: x[1])
        spread = best[1] - worst[1]

        impact = "HIGH" if spread > 0.20 else "MEDIUM" if spread > 0.10 else "LOW"
        reliable = best[2] >= _MIN_SAMPLES_MEDIUM

        rankings.append(FeatureImportance(
            feature=feature, impact=impact, ev_spread=spread,
            best_value=best[0], best_ev=best[1],
            worst_value=worst[0], worst_ev=worst[1],
            reliable=reliable,
        ))

    rankings.sort(key=lambda x: x.ev_spread, reverse=True)
    return rankings


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════

def _analyse_pattern_dependency(records: list[EdgeAttributionRecord]) -> list[dict[str, Any]]:
    """Deep pattern analysis — best/worst conditions per pattern."""
    pattern_groups: dict[str, list[EdgeAttributionRecord]] = defaultdict(list)
    for r in records:
        pattern_groups[r.pattern].append(r)

    results = []
    for pattern, recs in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
        if len(recs) < _MIN_SAMPLES_LOW:
            continue

        all_rs = [r.result_r for r in recs]
        overall = _compute_stats(all_rs)

        # Best/worst sub-conditions
        sub_conditions: dict[str, list[float]] = defaultdict(list)
        for r in recs:
            sub_conditions[f"regime={r.regime}"].append(r.result_r)
            sub_conditions[f"session={r.session}"].append(r.result_r)
            sub_conditions[f"htf={r.htf_alignment_bin}"].append(r.result_r)
            sub_conditions[f"trend={r.trend_alignment_bin}"].append(r.result_r)

        best_cond = None
        worst_cond = None
        best_ev = -999.0
        worst_ev = 999.0

        for cond, rs in sub_conditions.items():
            if len(rs) < _MIN_SAMPLES_LOW:
                continue
            ev = _compute_stats(rs)["ev"]
            if ev > best_ev:
                best_ev = ev
                best_cond = cond
            if ev < worst_ev:
                worst_ev = ev
                worst_cond = cond

        # Without-top condition analysis
        if best_cond:
            without_best = [r.result_r for r in recs if not _matches_condition(r, best_cond)]
            without_stats = _compute_stats(without_best) if without_best else {"ev": 0, "n": 0, "total_r": 0}
        else:
            without_stats = overall

        results.append({
            "pattern": pattern,
            "overall": overall,
            "best_condition": best_cond,
            "best_condition_ev": round(best_ev, 4) if best_cond else None,
            "worst_condition": worst_cond,
            "worst_condition_ev": round(worst_ev, 4) if worst_cond else None,
            "without_best_condition": without_stats,
            "edge_survives_removal": without_stats.get("ev", 0) > 0,
        })

    return results


def _matches_condition(record: EdgeAttributionRecord, condition: str) -> bool:
    """Check if a record matches a condition string like 'regime=TRENDING'."""
    parts = condition.split("=", 1)
    if len(parts) != 2:
        return False
    feat, val = parts
    mapping = {
        "regime": record.regime,
        "session": record.session,
        "htf": record.htf_alignment_bin,
        "trend": record.trend_alignment_bin,
    }
    return mapping.get(feat, "") == val


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def run_edge_analysis(records: list[EdgeAttributionRecord]) -> EdgeAnalysisResult:
    """Run full edge attribution analysis."""
    result = EdgeAnalysisResult(total_records=len(records))

    if len(records) < _MIN_SAMPLES_LOW:
        result.conclusion = "Insufficient records for analysis."
        result.confidence = "INSUFFICIENT"
        return result

    # Single feature breakdowns
    for feature in _FEATURES_TO_ANALYSE:
        conditions = _analyse_single_feature(feature, records)
        if conditions:
            result.single_features[feature] = conditions

    # Multi-dimensional
    result.combinations = _analyse_combinations(records)

    # Feature importance
    result.importance = _rank_importance(result.single_features)

    # Pattern dependency
    result.pattern_dependency = _analyse_pattern_dependency(records)

    # Edge candidates: positive EV with at least MEDIUM confidence
    for feature, conditions in result.single_features.items():
        for c in conditions:
            if c.stats.get("ev", 0) > 0.05 and c.stats.get("n", 0) >= _MIN_SAMPLES_MEDIUM:
                result.edge_candidates.append({
                    "type": "single",
                    "feature": feature,
                    "value": c.value,
                    **c.stats,
                })

    for combo in result.combinations[:20]:
        if combo.get("ev", 0) > 0.10 and combo.get("n", 0) >= _MIN_SAMPLES_MEDIUM:
            result.edge_candidates.append({"type": "combination", **combo})

    result.edge_candidates.sort(key=lambda x: x.get("ev", 0), reverse=True)

    # Warnings
    small_sample_edges = [c for c in result.edge_candidates if c.get("n", 0) < _MIN_SAMPLES_MEDIUM * 2]
    if small_sample_edges:
        result.warnings.append(f"{len(small_sample_edges)} edge candidates have < 40 samples (overfitting risk)")

    single_pattern = [pd for pd in result.pattern_dependency if pd.get("overall", {}).get("ev", 0) > 0.1 and not pd.get("edge_survives_removal")]
    if single_pattern:
        result.warnings.append(f"{len(single_pattern)} patterns lose edge when best sub-condition removed (fragile)")

    # Confidence
    if len(records) >= 500:
        result.confidence = "HIGH"
    elif len(records) >= 100:
        result.confidence = "MEDIUM"
    else:
        result.confidence = "LOW"

    # Conclusion
    positive_edges = [c for c in result.edge_candidates if c.get("confidence") in ("HIGH", "MEDIUM")]
    if positive_edges:
        top = positive_edges[0]
        result.conclusion = (
            f"Found {len(positive_edges)} edge candidates. "
            f"Strongest: {top.get('feature', top.get('features', '?'))}={top.get('value', '?')} "
            f"(EV={top.get('ev', 0):+.3f}R, n={top.get('n', 0)}). "
            f"Warnings: {len(result.warnings)}."
        )
    else:
        result.conclusion = "No statistically reliable edge candidates found in current data."

    logger.info("[EDGE_ATTR] records=%d candidates=%d warnings=%d confidence=%s",
                len(records), len(result.edge_candidates), len(result.warnings), result.confidence)
    return result
