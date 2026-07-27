"""
Regime Shift Stress Testing Engine — Tests rule system under artificial market shifts.

Evaluates robustness of the compressed rule system by simulating unseen market
conditions through data transformations. Detects overfitting to historical
regime structure and identifies vulnerability to distribution shifts.

Regime Transformations:
    1. Volatility Shock — amplified PnL variance, noisy signals
    2. Trend Dominance — biased directional runs, reduced mean-reversion
    3. Liquidity Fragmentation — degraded sweeps and BOS reliability
    4. Low Volatility Chop — compressed range, increased false signals

This module ONLY stress-tests. It does NOT:
    - Generate or modify rules
    - Run walk-forward, shadow, or compression logic
    - Evaluate live trading performance

Usage:
    from analysis.regime_stress_test import run_stress_test

    result = run_stress_test(
        curated_dir="events/curated",
        rules_path="analysis/reports/rule_compression.json",
    )
    print(result["overall_robustness_score"])
"""

from __future__ import annotations

import json
import logging
import random
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Transformation intensity parameters
VOLATILITY_SHOCK_MULTIPLIER = 2.0      # 2x PnL variance
TREND_BIAS_FACTOR = 0.30               # 30% shift toward directional bias
LIQUIDITY_DEGRADATION_RATE = 0.40      # 40% of sweeps become unreliable
CHOP_COMPRESSION_FACTOR = 0.50         # Halve effective PnL magnitude

# Reproducibility
RANDOM_SEED = 42

# Robustness thresholds
ROBUSTNESS_HIGH = 70
ROBUSTNESS_LOW = 40
EDGE_DECAY_CRITICAL = 50  # % decay that signals failure


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_trades(curated_dir: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for f in sorted(Path(curated_dir).glob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("pnl", 0) != 0:
                        events.append(ev)
                except json.JSONDecodeError:
                    continue
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def _load_compressed_rules(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("final_rule_set", data.get("rules", []))


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME TRANSFORMATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _transform_volatility_shock(trades: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """
    Volatility Shock: amplify PnL variance, flip some winners to losers
    and vice versa at the margins. Simulates fast expansion/reversal.
    """
    transformed = []
    for t in trades:
        trade = deepcopy(t)
        pnl = trade.get("pnl", 0)

        # Amplify magnitude
        trade["pnl"] = pnl * (1 + rng.gauss(0, VOLATILITY_SHOCK_MULTIPLIER - 1))

        # 15% chance of sign flip on small trades (noise)
        if abs(pnl) < abs(sum(tt.get("pnl", 0) for tt in trades) / max(len(trades), 1)) * 0.5:
            if rng.random() < 0.15:
                trade["pnl"] = -trade["pnl"]

        # Shift regime context
        trade["atr_regime"] = "expansion"
        transformed.append(trade)

    return transformed


def _transform_trend_dominance(trades: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """
    Trend Dominance: bias toward directional moves. Winners in trend
    direction get amplified, counter-trend trades get penalised.
    """
    transformed = []
    dominant_bias = rng.choice(["bullish", "bearish"])

    for t in trades:
        trade = deepcopy(t)
        pnl = trade.get("pnl", 0)
        trade_bias = trade.get("htf_bias", "neutral")

        # Aligned with dominant trend: boost
        if trade_bias == dominant_bias:
            trade["pnl"] = pnl * (1 + TREND_BIAS_FACTOR) if pnl > 0 else pnl * (1 - TREND_BIAS_FACTOR * 0.5)
        elif trade_bias == "neutral":
            # Neutral in trending market: slight degradation
            trade["pnl"] = pnl * (1 - TREND_BIAS_FACTOR * 0.3)
        else:
            # Counter-trend: significant penalty
            trade["pnl"] = pnl * (1 - TREND_BIAS_FACTOR) if pnl > 0 else pnl * (1 + TREND_BIAS_FACTOR)

        trade["htf_bias"] = dominant_bias
        transformed.append(trade)

    return transformed


def _transform_liquidity_fragmentation(trades: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """
    Liquidity Fragmentation: degrade reliability of sweep/BOS signals.
    Some historically profitable sweep trades become losses.
    """
    transformed = []

    for t in trades:
        trade = deepcopy(t)
        pnl = trade.get("pnl", 0)

        # Degrade liquidity sweep reliability
        if trade.get("liquidity_swept", False):
            if rng.random() < LIQUIDITY_DEGRADATION_RATE:
                # Sweep was unreliable — flip winner to loser
                if pnl > 0:
                    trade["pnl"] = -abs(pnl) * rng.uniform(0.3, 0.8)
            trade["liquidity_swept"] = rng.random() > 0.5  # Random reliability

        # Degrade BOS confirmation
        if trade.get("bos_confirmed", False):
            if rng.random() < LIQUIDITY_DEGRADATION_RATE * 0.7:
                if pnl > 0:
                    trade["pnl"] = -abs(pnl) * rng.uniform(0.2, 0.6)
            trade["bos_confirmed"] = rng.random() > 0.4

        transformed.append(trade)

    return transformed


def _transform_low_vol_chop(trades: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """
    Low Volatility Chop: compress PnL magnitude, increase false signals,
    reduce follow-through. Many small losses from chop.
    """
    transformed = []

    for t in trades:
        trade = deepcopy(t)
        pnl = trade.get("pnl", 0)

        # Compress magnitude
        trade["pnl"] = pnl * CHOP_COMPRESSION_FACTOR

        # 25% of winners become small losses (fake signals)
        if pnl > 0 and rng.random() < 0.25:
            trade["pnl"] = -abs(pnl) * rng.uniform(0.1, 0.3)

        trade["atr_regime"] = "contraction"
        transformed.append(trade)

    return transformed


# Registry of all regime transformations
REGIME_TRANSFORMS = {
    "volatility_shock": {
        "fn": _transform_volatility_shock,
        "description": "Amplified variance, sign flips on marginal trades, fast reversals",
    },
    "trend_dominance": {
        "fn": _transform_trend_dominance,
        "description": "Directional bias, counter-trend penalty, reduced mean-reversion",
    },
    "liquidity_fragmentation": {
        "fn": _transform_liquidity_fragmentation,
        "description": "Degraded sweep/BOS reliability, false breakouts",
    },
    "low_vol_chop": {
        "fn": _transform_low_vol_chop,
        "description": "Compressed range, 25% false signals, reduced follow-through",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# RULE APPLICATION (lightweight simulation)
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_rules(trades: list[dict[str, Any]], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply compressed rules to filter trades. Returns kept trades."""
    if not rules:
        return trades

    kept = []
    for trade in trades:
        blocked = False
        for rule in rules:
            if _rule_blocks(trade, rule):
                blocked = True
                break
        if not blocked:
            kept.append(trade)
    return kept


def _rule_blocks(trade: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Check if a rule blocks a trade."""
    if rule.get("type", "") not in ("TIGHTEN_GATE", "ADD_GATE"):
        return False
    if trade.get("pattern", "") != rule.get("target", ""):
        return False
    if trade.get("pnl", 0) >= 0:
        return False

    evidence = rule.get("supporting_evidence", {})
    regime = evidence.get("regime", "")
    bias = evidence.get("bias", "")
    regime_match = (not regime or regime == "neutral" or trade.get("atr_regime", "") == regime)
    bias_match = (not bias or bias == "neutral" or trade.get("htf_bias", "") == bias)
    return regime_match and bias_match


def _compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute performance metrics for a trade set."""
    if not trades:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "winrate": 0.0}
    pnls = [t.get("pnl", 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trades": len(pnls),
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
        "winrate": round(wins / len(pnls) * 100, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STRESS TEST EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _run_single_regime(
    regime_name: str,
    trades: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    baseline_metrics: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """Run stress test for a single regime transformation."""
    transform_info = REGIME_TRANSFORMS[regime_name]
    transform_fn = transform_info["fn"]

    # Apply regime transformation to raw trades
    shifted_trades = transform_fn(trades, rng)

    # Apply rules to shifted data
    filtered_shifted = _apply_rules(shifted_trades, rules)

    # Compute metrics on shifted+filtered trades
    shifted_metrics = _compute_metrics(filtered_shifted)

    # Compute deltas vs baseline
    baseline_pnl = baseline_metrics.get("avg_pnl", 0)
    shifted_pnl = shifted_metrics.get("avg_pnl", 0)
    baseline_wr = baseline_metrics.get("winrate", 0)
    shifted_wr = shifted_metrics.get("winrate", 0)

    pnl_change = shifted_pnl - baseline_pnl
    winrate_change = shifted_wr - baseline_wr

    # Edge decay: how much of original edge was lost
    if baseline_pnl > 0:
        edge_decay = max(0, (1 - shifted_pnl / baseline_pnl) * 100)
    elif baseline_pnl < 0 and shifted_pnl < baseline_pnl:
        edge_decay = abs(shifted_pnl - baseline_pnl) / abs(baseline_pnl) * 100
    else:
        edge_decay = 0

    # Stability score (0-100): how well system held up
    # High stability = small changes from baseline
    pnl_stability = max(0, 100 - abs(pnl_change / max(abs(baseline_pnl), 0.01)) * 100)
    wr_stability = max(0, 100 - abs(winrate_change) * 2)
    stability_score = int(pnl_stability * 0.6 + wr_stability * 0.4)
    stability_score = min(100, max(0, stability_score))

    # Per-pattern breakdown
    pattern_impact = _pattern_breakdown(trades, shifted_trades, rules)

    return {
        "regime_type": regime_name,
        "description": transform_info["description"],
        "shifted_metrics": shifted_metrics,
        "pnl_change": round(pnl_change, 4),
        "winrate_change": round(winrate_change, 2),
        "edge_decay": round(edge_decay, 2),
        "stability_score": stability_score,
        "pattern_impact": pattern_impact,
    }


def _pattern_breakdown(
    original: list[dict[str, Any]],
    shifted: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute per-pattern sensitivity to regime shift."""
    from collections import defaultdict

    orig_by_pattern: dict[str, list[float]] = defaultdict(list)
    shift_by_pattern: dict[str, list[float]] = defaultdict(list)

    orig_filtered = _apply_rules(original, rules)
    shift_filtered = _apply_rules(shifted, rules)

    for t in orig_filtered:
        orig_by_pattern[t.get("pattern", "UNKNOWN")].append(t.get("pnl", 0))
    for t in shift_filtered:
        shift_by_pattern[t.get("pattern", "UNKNOWN")].append(t.get("pnl", 0))

    results = []
    all_patterns = set(list(orig_by_pattern.keys()) + list(shift_by_pattern.keys()))

    for pattern in sorted(all_patterns):
        orig_pnls = orig_by_pattern.get(pattern, [])
        shift_pnls = shift_by_pattern.get(pattern, [])

        orig_avg = sum(orig_pnls) / len(orig_pnls) if orig_pnls else 0
        shift_avg = sum(shift_pnls) / len(shift_pnls) if shift_pnls else 0

        results.append({
            "pattern": pattern,
            "original_avg_pnl": round(orig_avg, 4),
            "shifted_avg_pnl": round(shift_avg, 4),
            "pnl_sensitivity": round(shift_avg - orig_avg, 4),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_stress_test(
    *,
    curated_dir: str = "events/curated",
    rules_path: str = "analysis/reports/rule_compression.json",
    seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    """
    Run regime shift stress testing on compressed rule system.

    Args:
        curated_dir: Path to curated JSONL directory
        rules_path: Path to compressed rules JSON
        seed: Random seed for reproducibility

    Returns:
        Complete stress test report with robustness scores.
    """
    trades = _load_trades(curated_dir)
    rules = _load_compressed_rules(rules_path)

    if not trades:
        return {"error": "no_trades"}

    rng = random.Random(seed)

    # Baseline: rules applied to unmodified data
    baseline_filtered = _apply_rules(trades, rules)
    baseline_metrics = _compute_metrics(baseline_filtered)

    # Run each regime transformation
    regime_results: list[dict[str, Any]] = []
    for regime_name in REGIME_TRANSFORMS:
        result = _run_single_regime(regime_name, trades, rules, baseline_metrics, rng)
        regime_results.append(result)

    # Overall robustness: average stability across all regimes
    stability_scores = [r["stability_score"] for r in regime_results]
    overall_robustness = int(sum(stability_scores) / len(stability_scores)) if stability_scores else 0

    # Worst / best case
    worst = min(regime_results, key=lambda r: r["stability_score"])
    best = max(regime_results, key=lambda r: r["stability_score"])

    # Failure modes: regimes where edge_decay > critical threshold
    failure_modes: list[dict[str, Any]] = []
    for r in regime_results:
        if r["edge_decay"] >= EDGE_DECAY_CRITICAL:
            affected = [p["pattern"] for p in r["pattern_impact"] if p["pnl_sensitivity"] < 0]
            failure_modes.append({
                "regime": r["regime_type"],
                "failure_reason": f"Edge decay {r['edge_decay']:.1f}% exceeds critical threshold ({EDGE_DECAY_CRITICAL}%)",
                "affected_patterns": affected,
                "edge_decay": r["edge_decay"],
                "stability_score": r["stability_score"],
            })

    # Summary
    system_robust = overall_robustness >= ROBUSTNESS_LOW and len(failure_modes) == 0
    vulnerabilities: list[str] = []

    if worst["stability_score"] < ROBUSTNESS_LOW:
        vulnerabilities.append(f"Weak under {worst['regime_type']} (stability={worst['stability_score']})")
    for fm in failure_modes:
        vulnerabilities.append(f"Critical decay in {fm['regime']} ({fm['edge_decay']:.1f}%)")

    if overall_robustness >= ROBUSTNESS_HIGH:
        risk_level = "LOW"
    elif overall_robustness >= ROBUSTNESS_LOW:
        risk_level = "MODERATE"
    else:
        risk_level = "HIGH"

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "curated_dir": curated_dir,
            "rules_path": rules_path,
            "total_trades": len(trades),
            "rules_tested": len(rules),
            "regimes_tested": len(REGIME_TRANSFORMS),
            "random_seed": seed,
        },
        "baseline_metrics": baseline_metrics,
        "regime_results": regime_results,
        "overall_robustness_score": overall_robustness,
        "worst_case_regime": worst["regime_type"],
        "best_case_regime": best["regime_type"],
        "failure_modes": failure_modes,
        "summary": {
            "system_robust": system_robust,
            "key_vulnerabilities": vulnerabilities,
            "deployment_risk_level": risk_level,
        },
    }

    logger.info(
        "[STRESS] Complete — robustness=%d/100, worst=%s (%d), failures=%d, risk=%s",
        overall_robustness, worst["regime_type"], worst["stability_score"],
        len(failure_modes), risk_level,
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/regime_stress_test.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    meta = results.get("metadata", {})
    baseline = results.get("baseline_metrics", {})
    regimes = results.get("regime_results", [])
    robustness = results.get("overall_robustness_score", 0)
    failures = results.get("failure_modes", [])
    summary = results.get("summary", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  REGIME SHIFT STRESS TEST")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Trades: {meta.get('total_trades', 0)} | Rules: {meta.get('rules_tested', 0)} | Regimes: {meta.get('regimes_tested', 0)}")
    print()

    # Overall score
    bar = "█" * (robustness // 5) + "░" * (20 - robustness // 5)
    risk = summary.get("deployment_risk_level", "?")
    robust = "✓ ROBUST" if summary.get("system_robust") else "✗ VULNERABLE"
    print(f"  ROBUSTNESS: {robustness}/100  [{bar}]  {robust}")
    print(f"  Deployment risk: {risk}")
    print()

    # Baseline
    print(f"  Baseline: {baseline.get('trades', 0)} trades | WR {baseline.get('winrate', 0):.1f}% | Avg PnL {baseline.get('avg_pnl', 0):.2f}")
    print()

    # Per-regime table
    print("─── REGIME RESULTS ─────────────────────────────────────────────")
    print(f"  {'Regime':<26} {'Stability':>9} {'Δ PnL':>8} {'Δ WR':>7} {'Decay':>7}")
    print(f"  {'─'*26} {'─'*9} {'─'*8} {'─'*7} {'─'*7}")

    for r in sorted(regimes, key=lambda x: x["stability_score"]):
        stab = r["stability_score"]
        indicator = "✓" if stab >= ROBUSTNESS_HIGH else "~" if stab >= ROBUSTNESS_LOW else "✗"
        print(
            f"  {indicator} {r['regime_type']:<24} {stab:>6}/100 "
            f"{r['pnl_change']:>+7.2f} {r['winrate_change']:>+6.1f}% "
            f"{r['edge_decay']:>5.1f}%"
        )
    print()

    # Failure modes
    if failures:
        print(f"─── FAILURE MODES ({len(failures)}) ─────────────────────────────────")
        for fm in failures:
            print(f"  ✗ {fm['regime']}: {fm['failure_reason']}")
            if fm["affected_patterns"]:
                print(f"    Affected: {fm['affected_patterns']}")
        print()

    # Vulnerabilities
    vulns = summary.get("key_vulnerabilities", [])
    if vulns:
        print("─── VULNERABILITIES ────────────────────────────────────────────")
        for v in vulns:
            print(f"  ⚠ {v}")
        print()

    print(f"  Worst case: {results.get('worst_case_regime', '?')}")
    print(f"  Best case:  {results.get('best_case_regime', '?')}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    curated = sys.argv[1] if len(sys.argv) > 1 else "events/curated"
    rules = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rule_compression.json"
    output = sys.argv[3] if len(sys.argv) > 3 else "analysis/reports/regime_stress_test.json"

    results = run_stress_test(curated_dir=curated, rules_path=rules)

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
