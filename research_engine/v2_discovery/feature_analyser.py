"""
Feature Analyser — CQ1: Which individual context variables predict outcome?

For each categorical and continuous feature in V2Opportunity data, computes:
    - Sample size per category/bucket
    - Win rate
    - Raw EV (mean R-multiple)
    - Cost-adjusted EV (raw EV minus spread cost in R)
    - Confidence interval (95%)
    - Statistical significance (p-value vs baseline)

Safety:
    - Never modifies trades or execution
    - Requires minimum sample size (default: 30)
    - CURRENT epoch filtering
    - Pure statistical analysis on linked observations
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

MIN_SAMPLE_SIZE = 30
CONFIDENCE_LEVEL = 0.95
# Z-score for 95% CI
_Z_95 = 1.96
# Default spread cost in R (from CE1 research: spread ~48% of risk)
DEFAULT_SPREAD_COST_R = 0.48


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Categorical features to test
CATEGORICAL_FEATURES = [
    "h4_regime",
    "h4_trend_direction",
    "h4_structure_state",
    "h4_volatility_state",
    "h1_bias",
    "h1_bos_confirmed",
    "h1_bos_direction",
    "h1_structure_type",
    "near_support",
    "near_resistance",
    "order_block_present",
    "m15_structure_state",
    "pattern_detected",
    "pattern_direction",
    "session",
    "proposed_direction",
]

# Continuous features to bucket and test
CONTINUOUS_FEATURES = [
    "pattern_quality",
    "spread_atr_ratio",
    "atr",
    "volatility",
    "candle_range",
    "body_ratio",
    "wick_ratio",
    "risk_distance_pips",
    "m15_rejection_strength",
    "m15_displacement",
]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FeatureResult:
    """Result for one feature category/bucket."""
    feature_name: str
    category: str
    sample_size: int
    win_rate: float
    raw_ev: float
    cost_adjusted_ev: float
    ci_lower: float
    ci_upper: float
    std_dev: float
    p_value: float
    significant: bool
    spread_cost_r: float = DEFAULT_SPREAD_COST_R


@dataclass
class FeatureAnalysis:
    """Full analysis for one feature across all categories."""
    feature_name: str
    feature_type: str  # "categorical" or "continuous"
    total_sample: int
    baseline_ev: float
    baseline_win_rate: float
    results: list[FeatureResult] = field(default_factory=list)
    best_category: str = ""
    best_ev: float = 0.0
    predictive: bool = False  # Any category significantly better than baseline


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def analyse_features(
    records: list[dict[str, Any]],
    *,
    min_sample: int = MIN_SAMPLE_SIZE,
    spread_cost_r: float = DEFAULT_SPREAD_COST_R,
    categorical_features: list[str] | None = None,
    continuous_features: list[str] | None = None,
) -> list[FeatureAnalysis]:
    """
    Analyse all features for predictive value.

    Args:
        records: List of linked V2Opportunity dicts (must have outcome_recorded=True)
        min_sample: Minimum sample size per category
        spread_cost_r: Spread cost in R-multiples to subtract
        categorical_features: Override default categorical feature list
        continuous_features: Override default continuous feature list

    Returns:
        List of FeatureAnalysis results sorted by best cost-adjusted EV.
    """
    # Filter to linked records only
    linked = [r for r in records if r.get("outcome_recorded") or r.get("_linkage", {}).get("linked")]
    if not linked:
        logger.info("[CQ1] No linked records available for analysis")
        return []

    # Compute baseline
    outcomes = _extract_outcomes(linked)
    if not outcomes:
        return []

    baseline_ev = _mean(outcomes)
    baseline_win_rate = sum(1 for o in outcomes if o > 0) / len(outcomes)

    cat_feats = categorical_features or CATEGORICAL_FEATURES
    cont_feats = continuous_features or CONTINUOUS_FEATURES

    results: list[FeatureAnalysis] = []

    # Analyse categorical features
    for feat in cat_feats:
        analysis = _analyse_categorical(
            linked, feat, baseline_ev, baseline_win_rate, min_sample, spread_cost_r
        )
        if analysis:
            results.append(analysis)

    # Analyse continuous features (bucketed into quartiles)
    for feat in cont_feats:
        analysis = _analyse_continuous(
            linked, feat, baseline_ev, baseline_win_rate, min_sample, spread_cost_r
        )
        if analysis:
            results.append(analysis)

    # Sort by best cost-adjusted EV descending
    results.sort(key=lambda a: a.best_ev, reverse=True)

    return results


def get_significant_features(
    analyses: list[FeatureAnalysis],
) -> list[FeatureAnalysis]:
    """Filter to only features with at least one significant category."""
    return [a for a in analyses if a.predictive]


def summarise_top_features(
    analyses: list[FeatureAnalysis], top_n: int = 10
) -> list[dict[str, Any]]:
    """Return top N features as summary dicts for reporting."""
    return [
        {
            "feature": a.feature_name,
            "type": a.feature_type,
            "best_category": a.best_category,
            "best_ev": round(a.best_ev, 4),
            "baseline_ev": round(a.baseline_ev, 4),
            "total_sample": a.total_sample,
            "predictive": a.predictive,
            "categories_tested": len(a.results),
        }
        for a in analyses[:top_n]
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _analyse_categorical(
    records: list[dict],
    feature: str,
    baseline_ev: float,
    baseline_win_rate: float,
    min_sample: int,
    spread_cost_r: float,
) -> FeatureAnalysis | None:
    """Analyse a single categorical feature."""
    # Group by category
    groups: dict[str, list[float]] = {}
    for rec in records:
        val = rec.get(feature)
        if val is None or val == "":
            continue
        cat = str(val)
        outcome = _get_outcome(rec)
        if outcome is None:
            continue
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(outcome)

    if not groups:
        return None

    total_sample = sum(len(v) for v in groups.values())
    analysis = FeatureAnalysis(
        feature_name=feature,
        feature_type="categorical",
        total_sample=total_sample,
        baseline_ev=baseline_ev,
        baseline_win_rate=baseline_win_rate,
    )

    best_ev = -999.0
    best_cat = ""

    for cat, outcomes in groups.items():
        if len(outcomes) < min_sample:
            continue

        result = _compute_category_stats(
            feature, cat, outcomes, baseline_ev, spread_cost_r
        )
        analysis.results.append(result)

        if result.cost_adjusted_ev > best_ev:
            best_ev = result.cost_adjusted_ev
            best_cat = cat

    if analysis.results:
        analysis.best_category = best_cat
        analysis.best_ev = best_ev
        analysis.predictive = any(r.significant and r.cost_adjusted_ev > 0 for r in analysis.results)

    return analysis


def _analyse_continuous(
    records: list[dict],
    feature: str,
    baseline_ev: float,
    baseline_win_rate: float,
    min_sample: int,
    spread_cost_r: float,
) -> FeatureAnalysis | None:
    """Analyse a continuous feature by quartile buckets."""
    values: list[tuple[float, float]] = []
    for rec in records:
        val = rec.get(feature)
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        outcome = _get_outcome(rec)
        if outcome is None:
            continue
        values.append((fval, outcome))

    if len(values) < min_sample * 2:
        return None

    # Sort by feature value and split into quartiles
    values.sort(key=lambda x: x[0])
    n = len(values)
    quartile_size = n // 4

    if quartile_size < min_sample:
        # Fall back to halves
        halves = [values[: n // 2], values[n // 2:]]
        buckets = {"LOW": halves[0], "HIGH": halves[1]}
    else:
        buckets = {
            "Q1": values[:quartile_size],
            "Q2": values[quartile_size: 2 * quartile_size],
            "Q3": values[2 * quartile_size: 3 * quartile_size],
            "Q4": values[3 * quartile_size:],
        }

    total_sample = sum(len(v) for v in buckets.values())
    analysis = FeatureAnalysis(
        feature_name=feature,
        feature_type="continuous",
        total_sample=total_sample,
        baseline_ev=baseline_ev,
        baseline_win_rate=baseline_win_rate,
    )

    best_ev = -999.0
    best_cat = ""

    for cat, bucket_values in buckets.items():
        outcomes = [o for _, o in bucket_values]
        if len(outcomes) < min_sample:
            continue

        result = _compute_category_stats(
            feature, cat, outcomes, baseline_ev, spread_cost_r
        )
        analysis.results.append(result)

        if result.cost_adjusted_ev > best_ev:
            best_ev = result.cost_adjusted_ev
            best_cat = cat

    if analysis.results:
        analysis.best_category = best_cat
        analysis.best_ev = best_ev
        analysis.predictive = any(r.significant and r.cost_adjusted_ev > 0 for r in analysis.results)

    return analysis


def _compute_category_stats(
    feature: str,
    category: str,
    outcomes: list[float],
    baseline_ev: float,
    spread_cost_r: float,
) -> FeatureResult:
    """Compute stats for one category/bucket."""
    n = len(outcomes)
    raw_ev = _mean(outcomes)
    cost_ev = raw_ev - spread_cost_r
    win_rate = sum(1 for o in outcomes if o > 0) / n if n > 0 else 0.0
    std = _std(outcomes)

    # 95% confidence interval
    se = std / math.sqrt(n) if n > 0 else 0.0
    ci_lower = raw_ev - _Z_95 * se
    ci_upper = raw_ev + _Z_95 * se

    # Significance: is this category's EV different from baseline?
    # Two-sided z-test: (category_ev - baseline) / SE
    if se > 0:
        z_stat = (raw_ev - baseline_ev) / se
        # Approximate p-value from z (two-tailed)
        p_value = _z_to_p(abs(z_stat))
    else:
        p_value = 1.0

    significant = p_value < (1 - CONFIDENCE_LEVEL)

    return FeatureResult(
        feature_name=feature,
        category=category,
        sample_size=n,
        win_rate=round(win_rate, 4),
        raw_ev=round(raw_ev, 4),
        cost_adjusted_ev=round(cost_ev, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        std_dev=round(std, 4),
        p_value=round(p_value, 6),
        significant=significant,
        spread_cost_r=spread_cost_r,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_outcomes(records: list[dict]) -> list[float]:
    """Extract outcome R-multiples from linked records."""
    results = []
    for r in records:
        o = _get_outcome(r)
        if o is not None:
            results.append(o)
    return results


def _get_outcome(rec: dict) -> float | None:
    """Get outcome R-multiple from a record."""
    # Try direct field first
    raw_r = rec.get("outcome_raw_r")
    if raw_r is not None:
        try:
            return float(raw_r)
        except (TypeError, ValueError):
            pass
    # Try _linkage
    linkage = rec.get("_linkage", {})
    result_r = linkage.get("result_r")
    if result_r is not None:
        try:
            return float(result_r)
        except (TypeError, ValueError):
            pass
    return None


def _mean(values: list[float]) -> float:
    """Compute mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    """Compute sample standard deviation."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _z_to_p(z: float) -> float:
    """
    Approximate two-tailed p-value from z-score.
    Uses the rational approximation for the normal CDF.
    """
    if z < 0:
        z = -z
    # Abramowitz & Stegun approximation 26.2.17
    p = 0.2316419
    b1 = 0.319381530
    b2 = -0.356563782
    b3 = 1.781477937
    b4 = -1.821255978
    b5 = 1.330274429

    t = 1.0 / (1.0 + p * z)
    t2 = t * t
    t3 = t2 * t
    t4 = t3 * t
    t5 = t4 * t

    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cdf = 1.0 - pdf * (b1 * t + b2 * t2 + b3 * t3 + b4 * t4 + b5 * t5)

    # Two-tailed
    return 2.0 * (1.0 - cdf)
