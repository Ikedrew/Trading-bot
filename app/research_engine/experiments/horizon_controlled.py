"""
Horizon Controlled Experiments — Single-variable isolation for exit research.

Four experiments, each changing EXACTLY ONE variable while holding all others constant:
    A) Duration: same entry/SL/TP, vary max_bars
    B) Stop distance: same entry/TP/duration, vary SL
    C) Target distance: same entry/SL/duration, vary TP
    D) Exit policy: same entry, vary exit mechanism (trailing vs fixed)

All experiments use bar-by-bar trade_state_progression — no look-ahead bias.
All use CURRENT-epoch data only.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    load_shadow_trades,
    persist_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _paired_significance(control: list[float], variant: list[float]) -> dict[str, Any]:
    """Paired t-test on same-trade comparisons."""
    if len(control) != len(variant) or len(control) < 10:
        return {"t_stat": 0.0, "p_approx": 1.0, "significant": False, "n": len(control)}

    diffs = [v - c for v, c in zip(variant, control)]
    n = len(diffs)
    mean_d = statistics.mean(diffs)
    std_d = statistics.stdev(diffs) if n > 1 else 1.0
    se = std_d / math.sqrt(n)
    t_stat = mean_d / se if se > 0 else 0.0
    z = abs(t_stat)
    p_approx = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))

    return {
        "t_stat": round(t_stat, 4),
        "p_approx": round(p_approx, 8),
        "significant_05": p_approx < 0.05,
        "significant_01": p_approx < 0.01,
        "mean_improvement": round(mean_d, 4),
        "ci_95_low": round(mean_d - 1.96 * se, 4),
        "ci_95_high": round(mean_d + 1.96 * se, 4),
        "n": n,
    }


def _simulate_exit(progression: list[dict], max_bars: int, sl_r: float, tp_r: float) -> dict[str, Any]:
    """
    Simulate a trade exit using bar-by-bar progression with specified parameters.

    Sequential only — no look-ahead. Uses R at each bar close.
    SL triggers at <= -sl_r. TP triggers at >= +tp_r. Timeout at max_bars.
    """
    for bar_data in progression[:max_bars]:
        bar_r = float(bar_data.get("r", 0))
        bar_num = int(bar_data.get("bar", 0))

        if bar_r <= -sl_r:
            return {"exit_r": -sl_r, "exit_reason": "stop_loss", "bars": bar_num}
        if bar_r >= tp_r:
            return {"exit_r": tp_r, "exit_reason": "take_profit", "bars": bar_num}

    # Timeout: exit at last bar's R
    if progression[:max_bars]:
        last_r = float(progression[min(max_bars - 1, len(progression) - 1)].get("r", 0))
        return {"exit_r": last_r, "exit_reason": "timeout", "bars": min(max_bars, len(progression))}

    return {"exit_r": 0.0, "exit_reason": "no_data", "bars": 0}


def _simulate_trailing(progression: list[dict], max_bars: int, sl_r: float,
                        activation_r: float, trail_distance_r: float) -> dict[str, Any]:
    """Simulate trailing stop exit — sequential, no look-ahead."""
    trailing_active = False
    trailing_level = -999.0
    peak_r = 0.0

    for bar_data in progression[:max_bars]:
        bar_r = float(bar_data.get("r", 0))
        bar_num = int(bar_data.get("bar", 0))

        # SL check
        if bar_r <= -sl_r:
            return {"exit_r": -sl_r, "exit_reason": "stop_loss", "bars": bar_num}

        # Update peak
        if bar_r > peak_r:
            peak_r = bar_r

        # Activation
        if not trailing_active and peak_r >= activation_r:
            trailing_active = True
            trailing_level = peak_r - trail_distance_r

        # Trail update
        if trailing_active:
            new_level = peak_r - trail_distance_r
            if new_level > trailing_level:
                trailing_level = new_level
            if bar_r <= trailing_level:
                return {"exit_r": trailing_level, "exit_reason": "trailing_stop", "bars": bar_num}

    # Timeout
    if progression[:max_bars]:
        last_r = float(progression[min(max_bars - 1, len(progression) - 1)].get("r", 0))
        if trailing_active:
            return {"exit_r": max(last_r, trailing_level), "exit_reason": "trailing_timeout", "bars": min(max_bars, len(progression))}
        return {"exit_r": last_r, "exit_reason": "timeout", "bars": min(max_bars, len(progression))}

    return {"exit_r": 0.0, "exit_reason": "no_data", "bars": 0}


def _variant_stats(r_values: list[float]) -> dict[str, Any]:
    """Compute summary stats for one variant."""
    n = len(r_values)
    if n == 0:
        return {"n": 0, "ev": 0.0, "wr": 0.0, "pf": 0.0}
    ev = statistics.mean(r_values)
    wr = sum(1 for r in r_values if r > 0) / n
    gw = sum(r for r in r_values if r > 0)
    gl = abs(sum(r for r in r_values if r < 0))
    pf = gw / gl if gl > 0 else 0.0
    return {"n": n, "ev": round(ev, 4), "wr": round(wr, 4), "pf": round(pf, 4)}


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT A: DURATION TEST
# ═══════════════════════════════════════════════════════════════════════════════


def run_experiment_a_duration(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Experiment A: Does trade duration affect expectancy?

    Control: Same entry, same SL (1.0R), same TP (unreachable/none).
    Variable: max_bars (60, 120, 180, 300, 480).
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades(epoch="CURRENT")

    # Filter trades with progression data
    valid = [r for r in shadow_trades if r.get("simulated_outcome", {}).get("trade_state_progression")]
    n = len(valid)

    if n < 50:
        return build_report(
            question_id="EX_A", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} trades with progression data"},
            confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades_current", "sample_size": n},
            fingerprint=build_fingerprint(n, len(shadow_trades) - n, epoch="CURRENT"),
            recommendation="WAIT",
        )

    # Fixed parameters (control)
    SL_R = 1.0  # Standard SL at -1R
    TP_R = 99.0  # Unreachable TP (effectively no TP — only duration matters)

    # Test durations
    durations = [20, 40, 60, 120, 180, 300]

    results = {}
    for max_bars in durations:
        variant_r = []
        for trade in valid:
            prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
            exit_result = _simulate_exit(prog, max_bars, SL_R, TP_R)
            variant_r.append(exit_result["exit_r"])
        results[max_bars] = _variant_stats(variant_r)
        results[max_bars]["r_values"] = variant_r

    # Baseline (60 bars = current)
    baseline_r = results[60]["r_values"]

    # Paired tests vs baseline
    comparisons = {}
    for max_bars in durations:
        if max_bars == 60:
            continue
        sig = _paired_significance(baseline_r, results[max_bars]["r_values"])
        comparisons[max_bars] = {
            "vs_baseline": sig,
            "stats": {k: v for k, v in results[max_bars].items() if k != "r_values"},
        }

    # Clean r_values from results for serialisation
    summary = {mb: {k: v for k, v in stats.items() if k != "r_values"} for mb, stats in results.items()}

    return build_report(
        question_id="EX_A",
        status=ReadinessStatus.COMPLETE,
        overall={
            "experiment": "DURATION_TEST",
            "control": {"SL": SL_R, "TP": "unreachable", "variable": "max_bars"},
            "variants": summary,
            "comparisons_vs_60bars": comparisons,
            "finding": f"Duration test on {n} trades. Best: max_bars={max(summary, key=lambda k: summary[k]['ev'])}",
        },
        confidence="HIGH" if n >= 200 else "MEDIUM",
        dataset={"source": "shadow_trades_current", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, epoch="CURRENT"),
        recommendation="MONITOR",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT B: STOP DISTANCE TEST
# ═══════════════════════════════════════════════════════════════════════════════


def run_experiment_b_stop_distance(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Experiment B: Does SL distance affect expectancy?

    Control: Same entry, same TP (unreachable), same duration (60 bars).
    Variable: SL distance (0.5R, 0.75R, 1.0R, 1.5R, 2.0R).
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades(epoch="CURRENT")

    valid = [r for r in shadow_trades if r.get("simulated_outcome", {}).get("trade_state_progression")]
    n = len(valid)

    if n < 50:
        return build_report(
            question_id="EX_B", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} trades"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades_current", "sample_size": n},
            fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="WAIT",
        )

    MAX_BARS = 60
    TP_R = 99.0  # Unreachable
    sl_levels = [0.5, 0.75, 1.0, 1.5, 2.0]

    results = {}
    for sl in sl_levels:
        variant_r = []
        for trade in valid:
            prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
            exit_result = _simulate_exit(prog, MAX_BARS, sl, TP_R)
            variant_r.append(exit_result["exit_r"])
        results[sl] = _variant_stats(variant_r)
        results[sl]["r_values"] = variant_r

    baseline_r = results[1.0]["r_values"]
    comparisons = {}
    for sl in sl_levels:
        if sl == 1.0:
            continue
        sig = _paired_significance(baseline_r, results[sl]["r_values"])
        comparisons[sl] = {"vs_baseline_1R": sig, "stats": {k: v for k, v in results[sl].items() if k != "r_values"}}

    summary = {sl: {k: v for k, v in s.items() if k != "r_values"} for sl, s in results.items()}

    return build_report(
        question_id="EX_B", status=ReadinessStatus.COMPLETE,
        overall={
            "experiment": "STOP_DISTANCE_TEST",
            "control": {"max_bars": MAX_BARS, "TP": "unreachable", "variable": "SL_distance_R"},
            "variants": summary, "comparisons_vs_1R": comparisons,
            "finding": f"SL distance test on {n} trades.",
        },
        confidence="HIGH" if n >= 200 else "MEDIUM",
        dataset={"source": "shadow_trades_current", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="MONITOR",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT C: TARGET DISTANCE TEST
# ═══════════════════════════════════════════════════════════════════════════════


def run_experiment_c_target_distance(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Experiment C: Does TP distance affect expectancy?

    Control: Same entry, same SL (1.0R), same duration (60 bars).
    Variable: TP distance (0.25R, 0.5R, 0.75R, 1.0R, 1.5R, 2.0R, 3.0R).
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades(epoch="CURRENT")

    valid = [r for r in shadow_trades if r.get("simulated_outcome", {}).get("trade_state_progression")]
    n = len(valid)

    if n < 50:
        return build_report(
            question_id="EX_C", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} trades"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades_current", "sample_size": n},
            fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="WAIT",
        )

    MAX_BARS = 60
    SL_R = 1.0
    tp_levels = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]

    results = {}
    for tp in tp_levels:
        variant_r = []
        for trade in valid:
            prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
            exit_result = _simulate_exit(prog, MAX_BARS, SL_R, tp)
            variant_r.append(exit_result["exit_r"])
        results[tp] = _variant_stats(variant_r)
        results[tp]["r_values"] = variant_r

    # Baseline: current system (TP=99, effectively unreachable)
    baseline_variant = []
    for trade in valid:
        prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
        exit_result = _simulate_exit(prog, MAX_BARS, SL_R, 99.0)
        baseline_variant.append(exit_result["exit_r"])

    comparisons = {}
    for tp in tp_levels:
        sig = _paired_significance(baseline_variant, results[tp]["r_values"])
        comparisons[tp] = {"vs_no_tp": sig, "stats": {k: v for k, v in results[tp].items() if k != "r_values"}}

    summary = {tp: {k: v for k, v in s.items() if k != "r_values"} for tp, s in results.items()}

    return build_report(
        question_id="EX_C", status=ReadinessStatus.COMPLETE,
        overall={
            "experiment": "TARGET_DISTANCE_TEST",
            "control": {"max_bars": MAX_BARS, "SL": SL_R, "variable": "TP_distance_R"},
            "baseline_no_tp_ev": round(statistics.mean(baseline_variant), 4),
            "variants": summary, "comparisons_vs_no_tp": comparisons,
            "finding": f"TP distance test on {n} trades.",
        },
        confidence="HIGH" if n >= 200 else "MEDIUM",
        dataset={"source": "shadow_trades_current", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="MONITOR",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT D: EXIT POLICY TEST
# ═══════════════════════════════════════════════════════════════════════════════


def run_experiment_d_exit_policy(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Experiment D: Does exit management policy affect expectancy?

    Control: Current exit (SL=1R, no TP, timeout at 60 bars).
    Variants: Trailing stop with different configurations.
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades(epoch="CURRENT")

    valid = [r for r in shadow_trades if r.get("simulated_outcome", {}).get("trade_state_progression")]
    n = len(valid)

    if n < 50:
        return build_report(
            question_id="EX_D", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} trades"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades_current", "sample_size": n},
            fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="WAIT",
        )

    MAX_BARS = 60
    SL_R = 1.0

    # Control: current system (SL + timeout, no trailing, no TP)
    control_r = []
    for trade in valid:
        prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
        exit_result = _simulate_exit(prog, MAX_BARS, SL_R, 99.0)
        control_r.append(exit_result["exit_r"])

    # Trailing variants
    trail_configs = [
        {"activation": 0.25, "trail": 0.10, "label": "act0.25_trail0.10"},
        {"activation": 0.50, "trail": 0.10, "label": "act0.50_trail0.10"},
        {"activation": 0.50, "trail": 0.25, "label": "act0.50_trail0.25"},
        {"activation": 0.75, "trail": 0.25, "label": "act0.75_trail0.25"},
        {"activation": 1.00, "trail": 0.50, "label": "act1.00_trail0.50"},
    ]

    results = {"control": _variant_stats(control_r)}
    comparisons = {}

    for config in trail_configs:
        variant_r = []
        for trade in valid:
            prog = trade.get("simulated_outcome", {}).get("trade_state_progression", [])
            exit_result = _simulate_trailing(
                prog, MAX_BARS, SL_R, config["activation"], config["trail"]
            )
            variant_r.append(exit_result["exit_r"])

        label = config["label"]
        results[label] = _variant_stats(variant_r)
        sig = _paired_significance(control_r, variant_r)
        comparisons[label] = {"vs_control": sig, "config": config}

    return build_report(
        question_id="EX_D", status=ReadinessStatus.COMPLETE,
        overall={
            "experiment": "EXIT_POLICY_TEST",
            "control": {"SL": SL_R, "TP": "none", "max_bars": MAX_BARS, "policy": "SL+timeout"},
            "variable": "exit_management_policy",
            "variants": results, "comparisons": comparisons,
            "finding": f"Exit policy test on {n} trades. Control EV={results['control']['ev']}R.",
        },
        confidence="HIGH" if n >= 200 else "MEDIUM",
        dataset={"source": "shadow_trades_current", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, epoch="CURRENT"), recommendation="MONITOR",
    )
