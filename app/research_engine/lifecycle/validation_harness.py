"""
Validation Harness — Statistical validation for experiment results.

Orchestrates:
    - Bootstrap confidence intervals
    - Permutation tests (direction label shuffle)
    - Chronological OOS split
    - Symbol robustness (leave-one-out)
    - Temporal stability (bucket analysis)
    - Outlier influence analysis

Calls into existing evidence_maturity.py for governance classification.

This module NEVER modifies production V10.
"""

from __future__ import annotations

import random
import statistics
from typing import Any


def bootstrap_ci(values: list[float], *, n: int = 2000, ci: float = 0.90,
                 seed: int = 42) -> tuple[float | None, float | None]:
    """Compute bootstrap confidence interval for the mean."""
    if len(values) < 3:
        return None, None
    rng = random.Random(seed)
    means = sorted([statistics.mean(rng.choices(values, k=len(values))) for _ in range(n)])
    lo_idx = int((1 - ci) / 2 * n)
    hi_idx = int((1 + ci) / 2 * n)
    return means[lo_idx], means[hi_idx]


def permutation_test(group_a: list[float], group_b: list[float], *,
                     n_perms: int = 5000, seed: int = 42) -> float:
    """
    Two-sample permutation test.
    
    Tests whether mean(group_a) > mean(group_b) by chance.
    Returns p-value (fraction of permutations where shuffled Δ ≥ observed Δ).
    
    IMPORTANT: group_a and group_b must be INDEPENDENT samples.
    If they are paired observations (same records, different treatment),
    use permutation_test_paired() instead.
    """
    if not group_a or not group_b:
        return 1.0
    if len(group_a) != len(group_b) or group_a == group_b:
        # Proceed with standard two-sample test
        pass
    observed_delta = statistics.mean(group_a) - statistics.mean(group_b)
    combined = list(group_a) + list(group_b)
    n_a = len(group_a)
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perms):
        shuffled = list(combined)
        rng.shuffle(shuffled)
        perm_delta = statistics.mean(shuffled[:n_a]) - statistics.mean(shuffled[n_a:])
        if perm_delta >= observed_delta:
            count += 1
    return count / n_perms


def permutation_test_paired(
    treatment_outcomes: list[float],
    control_outcomes: list[float],
    *,
    n_perms: int = 5000,
    seed: int = 42,
) -> float:
    """
    Paired permutation test for direction-inversion experiments.
    
    For each observation i, we have:
        treatment_outcomes[i] = R from inverted direction
        control_outcomes[i] = R from original direction
    
    Null hypothesis: The direction label (original vs inverted) does not matter.
    Under the null, for each pair we randomly assign which outcome is "treatment"
    and which is "control" (equivalent to randomly flipping the sign of each
    paired difference).
    
    Test statistic: mean of paired differences (treatment - control).
    
    Statistical rationale:
        - Each observation acts as its own control (same entry, same candles)
        - The null randomises the direction assignment per observation
        - This preserves the paired structure and tests whether inversion
          SYSTEMATICALLY improves outcomes across the population
        - No look-ahead: the permutation only shuffles labels, not outcomes
    
    Returns p-value (fraction of permutations where randomised mean difference 
    ≥ observed mean difference).
    
    Raises ValueError if inputs are invalid (empty, unequal length, identical).
    """
    if not treatment_outcomes or not control_outcomes:
        raise ValueError("permutation_test_paired: both groups must be non-empty")
    if len(treatment_outcomes) != len(control_outcomes):
        raise ValueError(
            f"permutation_test_paired: groups must have equal length "
            f"(got {len(treatment_outcomes)} vs {len(control_outcomes)})"
        )

    n = len(treatment_outcomes)
    
    # Compute paired differences
    diffs = [treatment_outcomes[i] - control_outcomes[i] for i in range(n)]
    
    # Check that treatment and control are not identical
    if all(d == 0 for d in diffs):
        raise ValueError(
            "permutation_test_paired: all paired differences are zero — "
            "treatment and control are identical (invalid null model)"
        )
    
    observed_mean_diff = statistics.mean(diffs)
    
    # Under the null: for each pair, randomly assign the sign of the difference
    # (equivalent to randomly swapping treatment/control for each observation)
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perms):
        # Randomly flip each difference's sign
        perm_diffs = [d * (1 if rng.random() < 0.5 else -1) for d in diffs]
        perm_mean = statistics.mean(perm_diffs)
        if perm_mean >= observed_mean_diff:
            count += 1

    return count / n_perms


def oos_split(records: list[dict[str, Any]], *, time_field: str = "time",
              train_fraction: float = 0.6) -> tuple[list[dict], list[dict]]:
    """Chronological train/test split. No shuffling — preserves time ordering."""
    sorted_recs = sorted(records, key=lambda r: r.get(time_field, 0))
    split_idx = int(len(sorted_recs) * train_fraction)
    return sorted_recs[:split_idx], sorted_recs[split_idx:]


def symbol_robustness(records: list[dict[str, Any]], r_field: str = "r_multiple",
                      symbol_field: str = "symbol", min_per_symbol: int = 5
                      ) -> dict[str, Any]:
    """
    Analyse symbol distribution and leave-one-out robustness.
    
    Returns:
        symbols_positive: count of symbols with mean R > 0
        symbols_total: count of symbols with enough data
        survives_best_removal: whether removing best symbol keeps mean > 0
        per_symbol: dict of symbol → (n, mean_r, total_r)
    """
    from collections import defaultdict
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for r in records:
        sym = r.get(symbol_field, "")
        val = r.get(r_field)
        if sym and val is not None:
            by_symbol[sym].append(val)

    per_symbol = {}
    for sym, vals in by_symbol.items():
        if len(vals) >= min_per_symbol:
            per_symbol[sym] = {"n": len(vals), "mean_r": statistics.mean(vals), "total_r": sum(vals)}

    symbols_positive = sum(1 for s in per_symbol.values() if s["mean_r"] > 0)
    symbols_total = len(per_symbol)

    # Leave-one-out: remove the best-performing symbol
    survives = False
    if per_symbol:
        best_sym = max(per_symbol, key=lambda s: per_symbol[s]["total_r"])
        remaining = [r.get(r_field) for r in records
                     if r.get(symbol_field) != best_sym and r.get(r_field) is not None]
        survives = statistics.mean(remaining) > 0 if remaining else False

    return {
        "symbols_positive": symbols_positive,
        "symbols_total": symbols_total,
        "survives_best_removal": survives,
        "per_symbol": per_symbol,
    }


def temporal_stability(records: list[dict[str, Any]], *, n_buckets: int = 5,
                       time_field: str = "time", r_field: str = "r_multiple"
                       ) -> dict[str, Any]:
    """
    Analyse temporal stability across chronological buckets.
    
    Returns:
        periods_positive: count of periods with mean R > 0
        periods_total: n_buckets
        per_period: list of (n, mean_r) per bucket
    """
    sorted_recs = sorted(records, key=lambda r: r.get(time_field, 0))
    n = len(sorted_recs)
    bucket_size = max(1, n // n_buckets)

    per_period = []
    for i in range(n_buckets):
        chunk = sorted_recs[i * bucket_size:(i + 1) * bucket_size]
        vals = [r.get(r_field) for r in chunk if r.get(r_field) is not None]
        if vals:
            per_period.append({"n": len(vals), "mean_r": statistics.mean(vals)})
        else:
            per_period.append({"n": 0, "mean_r": 0})

    periods_positive = sum(1 for p in per_period if p["mean_r"] > 0)

    return {
        "periods_positive": periods_positive,
        "periods_total": n_buckets,
        "per_period": per_period,
    }


def outlier_influence(values: list[float]) -> dict[str, Any]:
    """
    Analyse whether a small number of large winners drive the result.
    
    Returns:
        survives_top10: mean still positive after removing top 10
        survives_top20: mean still positive after removing top 20
        top10_contribution_pct: % of total R from top 10 winners
    """
    if not values:
        return {"survives_top10": False, "survives_top20": False, "top10_contribution_pct": 0}

    sorted_desc = sorted(values, reverse=True)
    total = sum(values)

    top10_sum = sum(sorted_desc[:10])
    top10_pct = (top10_sum / total * 100) if total != 0 else 0

    after_10 = sorted_desc[10:]
    after_20 = sorted_desc[20:]

    return {
        "survives_top10": statistics.mean(after_10) > 0 if after_10 else False,
        "survives_top20": statistics.mean(after_20) > 0 if after_20 else False,
        "top10_contribution_pct": round(top10_pct, 1),
    }


def compute_full_validation(
    results: list[dict[str, Any]],
    *,
    r_field: str = "r_multiple",
    time_field: str = "time",
    symbol_field: str = "symbol",
    validation_spec: Any = None,
) -> dict[str, Any]:
    """
    Run the complete validation suite on a set of experiment results.
    
    Returns a dict with all validation metrics needed for governance.
    """
    vals = [r.get(r_field) for r in results if r.get(r_field) is not None]
    if not vals:
        return {"valid": False, "reason": "no_data"}

    # Basic metrics
    ci_lo, ci_hi = bootstrap_ci(vals)
    sym_rob = symbol_robustness(results, r_field=r_field, symbol_field=symbol_field)
    temp_stab = temporal_stability(results, time_field=time_field, r_field=r_field)
    outliers = outlier_influence(vals)

    # OOS split
    train, test = oos_split(results, time_field=time_field)
    train_vals = [r.get(r_field) for r in train if r.get(r_field) is not None]
    test_vals = [r.get(r_field) for r in test if r.get(r_field) is not None]
    oos_ci_lo, oos_ci_hi = bootstrap_ci(test_vals) if test_vals else (None, None)

    return {
        "n": len(vals),
        "mean_r": statistics.mean(vals),
        "median_r": statistics.median(vals),
        "std_dev": statistics.stdev(vals) if len(vals) > 1 else 0,
        "total_r": sum(vals),
        "win_rate": sum(1 for v in vals if v > 0) / len(vals),
        "ci_lower": ci_lo,
        "ci_upper": ci_hi,
        "oos_n": len(test_vals),
        "oos_mean_r": statistics.mean(test_vals) if test_vals else 0,
        "oos_ci_lower": oos_ci_lo,
        "oos_ci_upper": oos_ci_hi,
        **sym_rob,
        **temp_stab,
        **outliers,
    }
