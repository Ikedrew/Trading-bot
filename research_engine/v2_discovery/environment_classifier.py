"""
Environment Classifier — CQ3: What conditions create favourable trade environments?

Identifies market conditions where positive outcomes are statistically more likely.
Analyses environmental factors (not directional signals) to find "permission to trade"
conditions.

Approach:
    1. Define environment dimensions (volatility, session, spread, structure)
    2. Segment records by environment state
    3. Compute outcome distribution per environment
    4. Identify environments with positive cost-adjusted EV
    5. Rank environments by favourability

Safety:
    - Never modifies trades or execution
    - Minimum sample requirements enforced
    - Identifies conditions only — does not create rules
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
_Z_95 = 1.96
DEFAULT_SPREAD_COST_R = 0.48


# ═══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT DIMENSIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Each dimension defines how to classify a record into an environment bucket
ENVIRONMENT_DIMENSIONS: list[dict[str, Any]] = [
    {
        "name": "volatility_regime",
        "description": "ATR-based volatility state",
        "classifier": lambda r: (
            "HIGH" if float(r.get("atr", 0) or 0) > 0.001
            else "LOW" if float(r.get("atr", 0) or 0) < 0.0005
            else "MEDIUM"
        ),
    },
    {
        "name": "spread_environment",
        "description": "Spread as fraction of risk",
        "classifier": lambda r: (
            "TIGHT" if float(r.get("spread_atr_ratio", 1) or 1) < 0.2
            else "WIDE" if float(r.get("spread_atr_ratio", 1) or 1) > 0.5
            else "NORMAL"
        ),
    },
    {
        "name": "session",
        "description": "Trading session",
        "classifier": lambda r: r.get("session", "UNKNOWN") or "UNKNOWN",
    },
    {
        "name": "h4_regime",
        "description": "H4 market regime",
        "classifier": lambda r: r.get("h4_regime", "") or "UNKNOWN",
    },
    {
        "name": "structure_proximity",
        "description": "Near key structure level",
        "classifier": lambda r: (
            "AT_SUPPORT" if r.get("near_support")
            else "AT_RESISTANCE" if r.get("near_resistance")
            else "NO_STRUCTURE"
        ),
    },
    {
        "name": "risk_geometry",
        "description": "Risk distance quality",
        "classifier": lambda r: (
            "TIGHT" if float(r.get("risk_distance_pips", 0) or 0) < 8
            else "WIDE" if float(r.get("risk_distance_pips", 0) or 0) > 15
            else "MODERATE"
        ),
    },
    {
        "name": "h1_alignment",
        "description": "H1 bias alignment with direction",
        "classifier": lambda r: _classify_h1_alignment(r),
    },
    {
        "name": "volatility_score",
        "description": "Tradability/volatility score bucket",
        "classifier": lambda r: (
            "HIGH_TRAD" if float(r.get("volatility", 0) or 0) > 0.7
            else "LOW_TRAD" if float(r.get("volatility", 0) or 0) < 0.3
            else "MED_TRAD"
        ),
    },
]


def _classify_h1_alignment(rec: dict) -> str:
    """Classify whether H1 bias aligns with proposed direction."""
    bias = (rec.get("h1_bias") or "").upper()
    direction = (rec.get("proposed_direction") or "").upper()
    if not bias or not direction or bias == "NEUTRAL":
        return "NEUTRAL"
    if (bias == "BULLISH" and direction == "BUY") or (bias == "BEARISH" and direction == "SELL"):
        return "ALIGNED"
    return "COUNTER"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EnvironmentBucket:
    """Stats for one environment state."""
    dimension: str
    state: str
    sample_size: int
    win_rate: float
    raw_ev: float
    cost_adjusted_ev: float
    ci_lower: float
    ci_upper: float
    p_value: float
    significant: bool
    favourable: bool  # positive cost-adjusted EV AND significant


@dataclass
class EnvironmentAnalysis:
    """Full environment classification results."""
    total_records: int
    baseline_ev: float
    baseline_cost_ev: float
    baseline_win_rate: float
    dimensions_analysed: int
    buckets: list[EnvironmentBucket] = field(default_factory=list)
    favourable_environments: list[EnvironmentBucket] = field(default_factory=list)
    unfavourable_environments: list[EnvironmentBucket] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def classify_environments(
    records: list[dict[str, Any]],
    *,
    min_sample: int = MIN_SAMPLE_SIZE,
    spread_cost_r: float = DEFAULT_SPREAD_COST_R,
    dimensions: list[dict[str, Any]] | None = None,
) -> EnvironmentAnalysis:
    """
    Classify market environments by favourability.

    Args:
        records: Linked V2Opportunity dicts
        min_sample: Minimum records per environment bucket
        spread_cost_r: Spread cost in R
        dimensions: Override default environment dimensions

    Returns:
        EnvironmentAnalysis with all buckets ranked.
    """
    linked = [r for r in records if _get_outcome(r) is not None]
    if not linked:
        return EnvironmentAnalysis(
            total_records=0, baseline_ev=0.0, baseline_cost_ev=0.0,
            baseline_win_rate=0.0, dimensions_analysed=0)

    outcomes = [_get_outcome(r) for r in linked]
    baseline_ev = _mean(outcomes)
    baseline_cost_ev = baseline_ev - spread_cost_r
    baseline_wr = sum(1 for o in outcomes if o > 0) / len(outcomes)

    dims = dimensions or ENVIRONMENT_DIMENSIONS

    analysis = EnvironmentAnalysis(
        total_records=len(linked),
        baseline_ev=round(baseline_ev, 4),
        baseline_cost_ev=round(baseline_cost_ev, 4),
        baseline_win_rate=round(baseline_wr, 4),
        dimensions_analysed=len(dims),
    )

    for dim in dims:
        name = dim["name"]
        classifier = dim["classifier"]

        # Group records by environment state
        groups: dict[str, list[float]] = {}
        for rec in linked:
            try:
                state = classifier(rec)
            except Exception:
                state = "ERROR"
            if state not in groups:
                groups[state] = []
            groups[state].append(_get_outcome(rec))

        # Analyse each state
        for state, state_outcomes in groups.items():
            if len(state_outcomes) < min_sample:
                continue

            bucket = _compute_bucket_stats(
                name, state, state_outcomes, baseline_ev, spread_cost_r
            )
            analysis.buckets.append(bucket)

            if bucket.favourable:
                analysis.favourable_environments.append(bucket)
            elif bucket.significant and bucket.cost_adjusted_ev < 0:
                analysis.unfavourable_environments.append(bucket)

    # Sort favourable by cost-adjusted EV descending
    analysis.favourable_environments.sort(
        key=lambda b: b.cost_adjusted_ev, reverse=True)
    # Sort unfavourable by cost-adjusted EV ascending (worst first)
    analysis.unfavourable_environments.sort(
        key=lambda b: b.cost_adjusted_ev)

    return analysis


def get_best_environments(
    analysis: EnvironmentAnalysis, top_n: int = 5
) -> list[dict[str, Any]]:
    """Return top N favourable environments as summary dicts."""
    return [
        {
            "dimension": b.dimension,
            "state": b.state,
            "cost_adjusted_ev": b.cost_adjusted_ev,
            "win_rate": b.win_rate,
            "sample_size": b.sample_size,
            "ci_lower": b.ci_lower,
            "ci_upper": b.ci_upper,
            "p_value": b.p_value,
        }
        for b in analysis.favourable_environments[:top_n]
    ]


def get_worst_environments(
    analysis: EnvironmentAnalysis, top_n: int = 5
) -> list[dict[str, Any]]:
    """Return top N unfavourable environments."""
    return [
        {
            "dimension": b.dimension,
            "state": b.state,
            "cost_adjusted_ev": b.cost_adjusted_ev,
            "win_rate": b.win_rate,
            "sample_size": b.sample_size,
        }
        for b in analysis.unfavourable_environments[:top_n]
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_bucket_stats(
    dimension: str,
    state: str,
    outcomes: list[float],
    baseline_ev: float,
    spread_cost_r: float,
) -> EnvironmentBucket:
    """Compute statistics for one environment bucket."""
    n = len(outcomes)
    raw_ev = _mean(outcomes)
    cost_ev = raw_ev - spread_cost_r
    win_rate = sum(1 for o in outcomes if o > 0) / n if n > 0 else 0.0
    std = _std(outcomes)

    se = std / math.sqrt(n) if n > 0 else 0.0
    ci_lower = raw_ev - _Z_95 * se
    ci_upper = raw_ev + _Z_95 * se

    if se > 0:
        z_stat = (raw_ev - baseline_ev) / se
        p_value = _z_to_p(abs(z_stat))
    else:
        p_value = 1.0

    significant = p_value < 0.05
    favourable = significant and cost_ev > 0

    return EnvironmentBucket(
        dimension=dimension,
        state=state,
        sample_size=n,
        win_rate=round(win_rate, 4),
        raw_ev=round(raw_ev, 4),
        cost_adjusted_ev=round(cost_ev, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        p_value=round(p_value, 6),
        significant=significant,
        favourable=favourable,
    )


def _get_outcome(rec: dict) -> float | None:
    """Get outcome R from record."""
    raw_r = rec.get("outcome_raw_r")
    if raw_r is not None:
        try:
            return float(raw_r)
        except (TypeError, ValueError):
            pass
    linkage = rec.get("_linkage", {})
    result_r = linkage.get("result_r")
    if result_r is not None:
        try:
            return float(result_r)
        except (TypeError, ValueError):
            pass
    return None


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _z_to_p(z: float) -> float:
    """Two-tailed p-value from z-score."""
    if z < 0:
        z = -z
    p = 0.2316419
    b1, b2, b3, b4, b5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    t = 1.0 / (1.0 + p * z)
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cdf = 1.0 - pdf * (b1 * t + b2 * t**2 + b3 * t**3 + b4 * t**4 + b5 * t**5)
    return 2.0 * (1.0 - cdf)
