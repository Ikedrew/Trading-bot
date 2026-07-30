"""
Context Combiner — CQ2: Which combinations of features create predictive value?

Tests economically meaningful hypotheses about feature combinations.
Does NOT brute-force all possible combinations.

Approach:
    1. Define hypothesis combinations (theory-driven, not data-mined)
    2. Filter records matching the combination
    3. Compute EV, win rate, significance vs baseline
    4. Require minimum sample size
    5. Apply out-of-sample validation (train/test split)

Safety:
    - Never modifies trades or execution
    - Controlled hypothesis testing only
    - Minimum sample requirements enforced
    - Out-of-sample validation prevents overfitting
    - Pure statistical analysis on linked observations
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

MIN_SAMPLE_SIZE = 30
VALIDATION_SPLIT = 0.3  # 30% holdout for out-of-sample
_Z_95 = 1.96
DEFAULT_SPREAD_COST_R = 0.48


# ═══════════════════════════════════════════════════════════════════════════════
# HYPOTHESIS DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CombinationHypothesis:
    """A pre-registered hypothesis about a feature combination."""
    hypothesis_id: str
    description: str
    filters: dict[str, Any]  # {feature_name: expected_value_or_callable}
    rationale: str = ""


# Economically meaningful combinations (theory-driven)
PREDEFINED_HYPOTHESES: list[CombinationHypothesis] = [
    CombinationHypothesis(
        hypothesis_id="COMBO_1",
        description="H4 trending + H1 aligned BOS + M15 structure + M5 confirmation",
        filters={
            "h4_regime": "TRENDING",
            "h1_bos_confirmed": True,
            "near_support": True,
            "pattern_detected": lambda v: v not in ("", None),
        },
        rationale="Multi-timeframe alignment: trend + structure break + location + trigger",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_2",
        description="H4 trending + H1 bias aligned + London session + low spread",
        filters={
            "h4_regime": "TRENDING",
            "h1_bias": lambda v: v in ("BULLISH", "BEARISH"),
            "session": "LONDON",
            "spread_atr_ratio": lambda v: v is not None and float(v) < 0.3,
        },
        rationale="Trend + directional bias + optimal session + low cost",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_3",
        description="H1 BOS + order block + M5 rejection pattern",
        filters={
            "h1_bos_confirmed": True,
            "order_block_present": True,
            "pattern_detected": lambda v: v not in ("", None),
        },
        rationale="Structure break + institutional level + trigger confirmation",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_4",
        description="H4 ranging + H1 at extremes + mean reversion setup",
        filters={
            "h4_regime": "RANGING",
            "near_support": True,
            "proposed_direction": "BUY",
        },
        rationale="Range-bound market + support level + buy direction = mean reversion",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_5",
        description="H4 ranging + H1 at resistance + sell",
        filters={
            "h4_regime": "RANGING",
            "near_resistance": True,
            "proposed_direction": "SELL",
        },
        rationale="Range-bound market + resistance level + sell direction = mean reversion",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_6",
        description="High pattern quality + structure alignment + London",
        filters={
            "pattern_quality": lambda v: v is not None and float(v) >= 0.7,
            "near_support": True,
            "session": "LONDON",
        },
        rationale="Best trigger quality + location + optimal liquidity",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_7",
        description="H1 CHOCH + H4 reversal + M5 pattern",
        filters={
            "h1_choch_detected": True,
            "h4_trend_direction": lambda v: v in ("BULLISH", "BEARISH"),
            "pattern_detected": lambda v: v not in ("", None),
        },
        rationale="Change of character signals potential reversal with multi-TF confirmation",
    ),
    CombinationHypothesis(
        hypothesis_id="COMBO_8",
        description="Low volatility + tight spread + structure location",
        filters={
            "volatility": lambda v: v is not None and float(v) < 0.4,
            "spread_atr_ratio": lambda v: v is not None and float(v) < 0.25,
            "near_support": True,
        },
        rationale="Calm market + low cost + defined level = cleaner signal",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CombinationResult:
    """Result for one hypothesis combination."""
    hypothesis_id: str
    description: str
    rationale: str
    # In-sample stats
    in_sample_n: int
    in_sample_ev: float
    in_sample_cost_ev: float
    in_sample_win_rate: float
    # Out-of-sample stats
    out_sample_n: int
    out_sample_ev: float
    out_sample_cost_ev: float
    out_sample_win_rate: float
    # Combined significance
    ci_lower: float
    ci_upper: float
    p_value: float
    significant: bool
    # Validation
    validated: bool  # out-of-sample confirms in-sample direction
    degradation: float  # how much worse is OOS vs IS


@dataclass
class CombinationAnalysis:
    """Full combination analysis report."""
    total_records: int
    baseline_ev: float
    hypotheses_tested: int
    results: list[CombinationResult] = field(default_factory=list)
    validated_combinations: int = 0
    best_combination: str = ""
    best_validated_ev: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def analyse_combinations(
    records: list[dict[str, Any]],
    *,
    hypotheses: list[CombinationHypothesis] | None = None,
    min_sample: int = MIN_SAMPLE_SIZE,
    spread_cost_r: float = DEFAULT_SPREAD_COST_R,
    validation_split: float = VALIDATION_SPLIT,
) -> CombinationAnalysis:
    """
    Test pre-registered hypotheses about feature combinations.

    Args:
        records: Linked V2Opportunity dicts
        hypotheses: Override default hypotheses (for testing)
        min_sample: Minimum sample per split
        spread_cost_r: Spread cost in R
        validation_split: Fraction held out for validation

    Returns:
        CombinationAnalysis with results for each hypothesis.
    """
    linked = [r for r in records if _get_outcome(r) is not None]
    if not linked:
        return CombinationAnalysis(
            total_records=0, baseline_ev=0.0, hypotheses_tested=0)

    # Baseline
    outcomes = [_get_outcome(r) for r in linked]
    baseline_ev = sum(outcomes) / len(outcomes)

    hyps = hypotheses or PREDEFINED_HYPOTHESES

    analysis = CombinationAnalysis(
        total_records=len(linked),
        baseline_ev=round(baseline_ev, 4),
        hypotheses_tested=len(hyps),
    )

    best_val_ev = -999.0

    for hyp in hyps:
        # Filter records matching this hypothesis
        matching = [r for r in linked if _matches_hypothesis(r, hyp)]

        if len(matching) < min_sample * 2:
            # Not enough data for train/test split
            continue

        # Split into in-sample and out-of-sample
        split_idx = int(len(matching) * (1 - validation_split))
        in_sample = matching[:split_idx]
        out_sample = matching[split_idx:]

        if len(in_sample) < min_sample or len(out_sample) < max(10, min_sample // 3):
            continue

        # Compute stats
        is_outcomes = [_get_outcome(r) for r in in_sample]
        oos_outcomes = [_get_outcome(r) for r in out_sample]

        is_ev = _mean(is_outcomes)
        oos_ev = _mean(oos_outcomes)
        is_cost_ev = is_ev - spread_cost_r
        oos_cost_ev = oos_ev - spread_cost_r

        is_wr = sum(1 for o in is_outcomes if o > 0) / len(is_outcomes)
        oos_wr = sum(1 for o in oos_outcomes if o > 0) / len(oos_outcomes)

        # Combined significance (full sample)
        all_outcomes = is_outcomes + oos_outcomes
        std = _std(all_outcomes)
        n = len(all_outcomes)
        se = std / math.sqrt(n) if n > 0 else 0.0
        full_ev = _mean(all_outcomes)

        ci_lower = full_ev - _Z_95 * se
        ci_upper = full_ev + _Z_95 * se

        if se > 0:
            z_stat = (full_ev - baseline_ev) / se
            p_value = _z_to_p(abs(z_stat))
        else:
            p_value = 1.0

        significant = p_value < 0.05

        # Validation: does OOS confirm IS direction?
        validated = (is_cost_ev > 0 and oos_cost_ev > 0) or (is_cost_ev > baseline_ev and oos_cost_ev > baseline_ev)
        degradation = is_cost_ev - oos_cost_ev if is_cost_ev != 0 else 0.0

        result = CombinationResult(
            hypothesis_id=hyp.hypothesis_id,
            description=hyp.description,
            rationale=hyp.rationale,
            in_sample_n=len(in_sample),
            in_sample_ev=round(is_ev, 4),
            in_sample_cost_ev=round(is_cost_ev, 4),
            in_sample_win_rate=round(is_wr, 4),
            out_sample_n=len(out_sample),
            out_sample_ev=round(oos_ev, 4),
            out_sample_cost_ev=round(oos_cost_ev, 4),
            out_sample_win_rate=round(oos_wr, 4),
            ci_lower=round(ci_lower, 4),
            ci_upper=round(ci_upper, 4),
            p_value=round(p_value, 6),
            significant=significant,
            validated=validated,
            degradation=round(degradation, 4),
        )

        analysis.results.append(result)

        if validated and oos_cost_ev > best_val_ev:
            best_val_ev = oos_cost_ev
            analysis.best_combination = hyp.hypothesis_id
            analysis.best_validated_ev = round(oos_cost_ev, 4)

    analysis.validated_combinations = sum(1 for r in analysis.results if r.validated)

    # Sort by OOS cost-adjusted EV
    analysis.results.sort(key=lambda r: r.out_sample_cost_ev, reverse=True)

    return analysis


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _matches_hypothesis(record: dict, hyp: CombinationHypothesis) -> bool:
    """Check if a record matches all filters in a hypothesis."""
    for feature, expected in hyp.filters.items():
        val = record.get(feature)
        if callable(expected):
            try:
                if not expected(val):
                    return False
            except Exception:
                return False
        else:
            if val != expected:
                return False
    return True


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
