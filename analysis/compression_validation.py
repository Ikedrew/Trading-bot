"""
Rule Compression Validation — Verifies compression preserved strategy edge.

Compares system performance BEFORE and AFTER rule compression to ensure:
    - No meaningful edge was lost
    - Noise removed without destroying signal
    - Stability gains are real (not artificial)
    - Performance degradation is not hidden

This module ONLY evaluates. It does NOT:
    - Generate or modify rules
    - Re-run compression
    - Perform independent walk-forward or shadow analysis

Usage:
    from analysis.compression_validation import validate_compression

    result = validate_compression(
        pre_rules_path="analysis/reports/rules_latest.json",
        post_rules_path="analysis/reports/rule_compression.json",
        curated_dir="events/curated",
    )
    print(result["summary"])
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_pre_rules(path: str) -> list[dict[str, Any]]:
    data = _load_json(path)
    if data is None:
        return []
    return data.get("rules", [])


def _load_post_rules(path: str) -> list[dict[str, Any]]:
    data = _load_json(path)
    if data is None:
        return []
    return data.get("final_rule_set", [])


def _load_trades(curated_dir: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    curated_path = Path(curated_dir)
    if not curated_path.exists():
        return events
    for f in sorted(curated_path.glob("*.jsonl")):
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


# ═══════════════════════════════════════════════════════════════════════════════
# RULE SIMULATION (lightweight — same logic as shadow_execution)
# ═══════════════════════════════════════════════════════════════════════════════

def _simulate_rules(trades: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Simulate rule application on trade set. Returns performance metrics.

    TIGHTEN_GATE/ADD_GATE: block matching losers
    LOOSEN_GATE/EXECUTION_CHANGE: no blocking (can't simulate new entries)
    """
    if not trades:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "winrate": 0.0, "blocked": 0}

    kept: list[dict[str, Any]] = []
    blocked_count = 0

    for trade in trades:
        should_block = False
        for rule in rules:
            if _rule_blocks_trade(trade, rule):
                should_block = True
                break
        if should_block:
            blocked_count += 1
        else:
            kept.append(trade)

    pnls = [t.get("pnl", 0) for t in kept]
    wins = sum(1 for p in pnls if p > 0)

    return {
        "trades": len(kept),
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "winrate": round(wins / len(pnls) * 100, 2) if pnls else 0.0,
        "blocked": blocked_count,
    }


def _rule_blocks_trade(trade: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Check if a rule would block a specific trade."""
    rule_type = rule.get("type", "")
    target = rule.get("target", "")

    # Only TIGHTEN/ADD gates block trades
    if rule_type not in ("TIGHTEN_GATE", "ADD_GATE"):
        return False

    # Must match target pattern
    if trade.get("pattern", "") != target:
        return False

    # Only block losers (conservative simulation)
    if trade.get("pnl", 0) >= 0:
        return False

    # Check evidence context match
    evidence = rule.get("supporting_evidence", {})
    source = evidence.get("source", "")

    if source == "failure_signature_analysis":
        regime = evidence.get("regime", "")
        bias = evidence.get("bias", "")
        regime_match = (not regime or regime == "neutral" or trade.get("atr_regime", "") == regime)
        bias_match = (not bias or bias == "neutral" or trade.get("htf_bias", "") == bias)
        return regime_match and bias_match

    # Generic: block losses for matching pattern
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PERFORMANCE DELTA ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_performance_delta(
    baseline: dict[str, Any],
    pre_sim: dict[str, Any],
    post_sim: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare PnL, winrate, and expectancy between pre and post compression.

    Score range: -100 (post much worse) to +100 (post much better).
    0 = no change.
    """
    pre_pnl = pre_sim.get("total_pnl", 0)
    post_pnl = post_sim.get("total_pnl", 0)
    baseline_pnl = baseline.get("total_pnl", 1)

    # PnL delta relative to baseline
    pre_improvement = pre_pnl - baseline_pnl
    post_improvement = post_pnl - baseline_pnl

    # If pre didn't improve much, compression can't lose much
    if abs(pre_improvement) < 0.01:
        pnl_delta_score = 0
    else:
        # How much of the pre-improvement did post retain?
        retention = post_improvement / pre_improvement if pre_improvement != 0 else 1.0
        # Score: 1.0 retention = 0 (neutral), >1 = positive, <1 = negative
        pnl_delta_score = int((retention - 1.0) * 50)

    # Winrate comparison
    pre_wr = pre_sim.get("winrate", 0)
    post_wr = post_sim.get("winrate", 0)
    wr_delta = post_wr - pre_wr

    # Blocked count comparison
    pre_blocked = pre_sim.get("blocked", 0)
    post_blocked = post_sim.get("blocked", 0)

    # Combined score: PnL retention matters most
    score = max(-100, min(100, pnl_delta_score + int(wr_delta * 0.5)))

    return {
        "score": score,
        "pre_total_pnl": pre_pnl,
        "post_total_pnl": post_pnl,
        "pnl_change": round(post_pnl - pre_pnl, 4),
        "pre_winrate": pre_wr,
        "post_winrate": post_wr,
        "winrate_change": round(wr_delta, 2),
        "pre_blocked": pre_blocked,
        "post_blocked": post_blocked,
        "blocked_change": post_blocked - pre_blocked,
        "edge_retained": post_pnl >= pre_pnl * 0.90,  # 90% retention = acceptable
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EDGE PRESERVATION SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_edge_preservation(
    pre_rules: list[dict[str, Any]],
    post_rules: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Measure whether key profitable signals still exist after compression.

    Checks:
        - Pattern coverage (are all targets still addressed?)
        - Rule type coverage (are all decision directions preserved?)
        - Confidence preservation (is aggregate confidence maintained?)
    """
    # Patterns targeted before vs after
    pre_targets = set(r.get("target", "") for r in pre_rules)
    post_targets = set(r.get("target", "") for r in post_rules)
    lost_targets = pre_targets - post_targets

    # Types before vs after
    pre_types = set(r.get("type", "") for r in pre_rules)
    post_types = set(r.get("type", "") for r in post_rules)

    # Confidence aggregation
    pre_conf_sum = sum(r.get("confidence_score", 0) for r in pre_rules)
    post_conf_sum = sum(r.get("confidence_score", 0) for r in post_rules)
    pre_conf_avg = pre_conf_sum / max(len(pre_rules), 1)
    post_conf_avg = post_conf_sum / max(len(post_rules), 1)

    # Target coverage score (0-100)
    if pre_targets:
        target_coverage = len(post_targets & pre_targets) / len(pre_targets) * 100
    else:
        target_coverage = 100

    # Type coverage score
    if pre_types:
        type_coverage = len(post_types & pre_types) / len(pre_types) * 100
    else:
        type_coverage = 100

    # Confidence preservation
    conf_ratio = post_conf_avg / max(pre_conf_avg, 1)
    conf_score = min(100, conf_ratio * 100)

    # Combined preservation score
    score = int(target_coverage * 0.50 + type_coverage * 0.25 + conf_score * 0.25)
    score = min(100, max(0, score))

    return {
        "score": score,
        "target_coverage": round(target_coverage, 1),
        "type_coverage": round(type_coverage, 1),
        "confidence_preservation": round(conf_score, 1),
        "pre_targets": sorted(pre_targets),
        "post_targets": sorted(post_targets),
        "lost_targets": sorted(lost_targets),
        "pre_avg_confidence": round(pre_conf_avg, 1),
        "post_avg_confidence": round(post_conf_avg, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SIGNAL LOSS DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_signal_loss(
    pre_rules: list[dict[str, Any]],
    post_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Detect removed signal clusters — rules that existed pre-compression
    but are missing or weakened post-compression.
    """
    post_ids = set(r.get("rule_id", "") for r in post_rules)
    # Also check merged_from fields
    post_absorbed: set[str] = set()
    for r in post_rules:
        for mid in r.get("merged_from", []):
            post_absorbed.add(mid)

    lost: list[dict[str, Any]] = []

    for rule in pre_rules:
        rule_id = rule.get("rule_id", "")

        # Rule still exists or was absorbed into a merge
        if rule_id in post_ids or rule_id in post_absorbed:
            continue

        # This rule was completely removed
        lost.append({
            "rule_id": rule_id,
            "type": rule.get("type", "?"),
            "target": rule.get("target", "?"),
            "confidence": rule.get("confidence_score", 0),
            "expected_impact": rule.get("expected_impact", 0),
            "severity": _loss_severity(rule),
            "reason": "Removed during compression — not merged, not kept.",
        })

    lost.sort(key=lambda l: l["severity"], reverse=True)
    return lost


def _loss_severity(rule: dict[str, Any]) -> str:
    """Classify severity of losing a rule."""
    conf = rule.get("confidence_score", 0)
    impact = rule.get("expected_impact", 0)
    if conf >= 70 and impact > 5:
        return "HIGH"
    elif conf >= 50:
        return "MEDIUM"
    return "LOW"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. FALSE STABILITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def _check_false_stability(
    pre_sim: dict[str, Any],
    post_sim: dict[str, Any],
    pre_rules: list[dict[str, Any]],
    post_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect cases where stability increased but performance decreased.

    False stability = system appears more stable only because active rules
    were removed, not because edge quality improved.
    """
    pre_pnl = pre_sim.get("total_pnl", 0)
    post_pnl = post_sim.get("total_pnl", 0)
    pre_blocked = pre_sim.get("blocked", 0)
    post_blocked = post_sim.get("blocked", 0)

    # Stability proxy: fewer rules + fewer conflicts = more stable
    rule_reduction = len(pre_rules) - len(post_rules)
    stability_gained = rule_reduction > 0

    # Performance change
    pnl_decreased = post_pnl < pre_pnl * 0.95  # >5% drop
    blocking_decreased = post_blocked < pre_blocked

    # False stability: gained stability but lost performance
    false_flag = stability_gained and pnl_decreased

    # Over-compression: rules were doing useful work that's now gone
    over_compressed = (
        blocking_decreased and
        pre_blocked > 0 and
        post_pnl < pre_pnl
    )

    explanation = ""
    if false_flag:
        explanation = (
            f"Stability increased (rules: {len(pre_rules)} → {len(post_rules)}) "
            f"but PnL decreased ({pre_pnl:.2f} → {post_pnl:.2f}). "
            "Compression may have removed useful signal along with noise."
        )
    elif over_compressed:
        explanation = (
            f"Blocking decreased ({pre_blocked} → {post_blocked}) and PnL dropped. "
            "Some removed rules were providing genuine protection."
        )
    else:
        explanation = "No false stability detected. Compression appears genuine."

    return {
        "false_stability_flag": false_flag,
        "over_compression_flag": over_compressed,
        "explanation": explanation,
        "stability_gained": stability_gained,
        "performance_preserved": not pnl_decreased,
        "rule_reduction": rule_reduction,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. COMPRESSION EFFICIENCY SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_efficiency(
    pre_rules: list[dict[str, Any]],
    post_rules: list[dict[str, Any]],
    perf_delta: dict[str, Any],
    edge_preservation: dict[str, Any],
) -> dict[str, Any]:
    """
    Compute complexity vs performance tradeoff score.

    High efficiency = significant compression with minimal performance loss.
    Low efficiency = compression destroyed more value than it saved in complexity.
    """
    # Compression ratio (how much was removed)
    compression = 1 - len(post_rules) / max(len(pre_rules), 1)

    # Performance retention (did we keep the edge?)
    edge_retained = perf_delta.get("edge_retained", False)
    pnl_change = perf_delta.get("pnl_change", 0)

    # Preservation score from edge analysis
    preservation = edge_preservation.get("score", 50)

    # Efficiency = compression × preservation
    # High compression + high preservation = excellent efficiency
    # High compression + low preservation = over-compression
    if edge_retained:
        score = int(compression * 50 + preservation * 0.5)
    else:
        # Edge lost — penalise heavily
        score = int(compression * 20 + preservation * 0.2)

    score = min(100, max(0, score))

    return {
        "score": score,
        "compression_ratio": round(compression, 3),
        "edge_retained": edge_retained,
        "preservation_score": preservation,
        "verdict": (
            "EFFICIENT" if score >= 70 else
            "ACCEPTABLE" if score >= 45 else
            "OVER_COMPRESSED"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN-LEVEL COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def _pattern_level_changes(
    trades: list[dict[str, Any]],
    pre_rules: list[dict[str, Any]],
    post_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute per-pattern expectancy change before vs after compression."""
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_pattern[t.get("pattern", "UNKNOWN")].append(t.get("pnl", 0))

    changes: list[dict[str, Any]] = []

    for pattern, pnls in by_pattern.items():
        # Simulate pre-compression
        pre_kept = [
            p for i, p in enumerate(pnls)
            if not any(
                _rule_blocks_trade({"pattern": pattern, "pnl": p, "atr_regime": "neutral", "htf_bias": "neutral"}, r)
                for r in pre_rules
            )
        ]
        # Simulate post-compression
        post_kept = [
            p for i, p in enumerate(pnls)
            if not any(
                _rule_blocks_trade({"pattern": pattern, "pnl": p, "atr_regime": "neutral", "htf_bias": "neutral"}, r)
                for r in post_rules
            )
        ]

        pre_exp = sum(pre_kept) / len(pre_kept) if pre_kept else 0
        post_exp = sum(post_kept) / len(post_kept) if post_kept else 0

        changes.append({
            "pattern": pattern,
            "pre_trades": len(pre_kept),
            "post_trades": len(post_kept),
            "pre_expectancy": round(pre_exp, 4),
            "post_expectancy": round(post_exp, 4),
            "change": round(post_exp - pre_exp, 4),
            "pct_change": round((post_exp - pre_exp) / abs(pre_exp) * 100, 1) if pre_exp != 0 else 0.0,
        })

    changes.sort(key=lambda c: abs(c["change"]), reverse=True)
    return changes


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def validate_compression(
    *,
    pre_rules_path: str = "analysis/reports/rules_latest.json",
    post_rules_path: str = "analysis/reports/rule_compression.json",
    curated_dir: str = "events/curated",
) -> dict[str, Any]:
    """
    Validate whether compression preserved strategy edge.

    Returns complete comparison report.
    """
    pre_rules = _load_pre_rules(pre_rules_path)
    post_rules = _load_post_rules(post_rules_path)
    trades = _load_trades(curated_dir)

    if not pre_rules:
        return {"error": "no_pre_rules", "summary": {"compression_valid": False}}
    if not trades:
        return {"error": "no_trades", "summary": {"compression_valid": False}}

    # Baseline (no rules)
    baseline = {
        "trades": len(trades),
        "total_pnl": round(sum(t.get("pnl", 0) for t in trades), 4),
        "avg_pnl": round(sum(t.get("pnl", 0) for t in trades) / len(trades), 4),
        "winrate": round(sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100, 2),
    }

    # Simulate pre and post
    pre_sim = _simulate_rules(trades, pre_rules)
    post_sim = _simulate_rules(trades, post_rules)

    # Run all analyses
    perf_delta = _compute_performance_delta(baseline, pre_sim, post_sim)
    edge_preservation = _compute_edge_preservation(pre_rules, post_rules, trades)
    lost_signals = _detect_signal_loss(pre_rules, post_rules)
    false_stability = _check_false_stability(pre_sim, post_sim, pre_rules, post_rules)
    efficiency = _compute_efficiency(pre_rules, post_rules, perf_delta, edge_preservation)
    pattern_changes = _pattern_level_changes(trades, pre_rules, post_rules)

    # Build summary
    key_risks: list[str] = []
    if false_stability["false_stability_flag"]:
        key_risks.append("False stability detected — performance decreased despite reduced complexity.")
    if lost_signals and any(s["severity"] == "HIGH" for s in lost_signals):
        key_risks.append("High-severity signal loss — important rules were removed.")
    if not perf_delta["edge_retained"]:
        key_risks.append("Edge not retained — post-compression PnL dropped >10% vs pre-compression.")

    compression_valid = (
        perf_delta["edge_retained"] and
        not false_stability["false_stability_flag"] and
        edge_preservation["score"] >= 60
    )

    if compression_valid:
        recommendation = (
            "Compression is VALID. Use compressed rule set for deployment. "
            f"Efficiency: {efficiency['verdict']}. Edge preserved at {edge_preservation['score']}%."
        )
    else:
        recommendation = (
            "Compression has ISSUES. Review lost signals and performance delta. "
            "Consider partial compression or reverting to pre-compression rules."
        )

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pre_rules_path": pre_rules_path,
            "post_rules_path": post_rules_path,
            "pre_rule_count": len(pre_rules),
            "post_rule_count": len(post_rules),
            "total_trades": len(trades),
        },
        "baseline_metrics": baseline,
        "pre_compression_sim": pre_sim,
        "post_compression_sim": post_sim,
        "performance_delta_score": perf_delta["score"],
        "performance_delta": perf_delta,
        "edge_preservation_score": edge_preservation["score"],
        "edge_preservation": edge_preservation,
        "compression_efficiency_score": efficiency["score"],
        "compression_efficiency": efficiency,
        "false_stability_flag": false_stability["false_stability_flag"],
        "false_stability": false_stability,
        "lost_edge_clusters": lost_signals,
        "pattern_level_changes": pattern_changes,
        "summary": {
            "compression_valid": compression_valid,
            "key_risks": key_risks,
            "recommendation": recommendation,
        },
    }

    logger.info(
        "[COMPRESS_VALID] Rules %d→%d | PnL delta=%+.2f | Edge=%d%% | Efficiency=%d%% | Valid=%s",
        len(pre_rules), len(post_rules), perf_delta["pnl_change"],
        edge_preservation["score"], efficiency["score"], compression_valid,
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/compression_validation.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    meta = results.get("metadata", {})
    perf = results.get("performance_delta", {})
    edge = results.get("edge_preservation", {})
    eff = results.get("compression_efficiency", {})
    false_stab = results.get("false_stability", {})
    lost = results.get("lost_edge_clusters", [])
    patterns = results.get("pattern_level_changes", [])
    summary = results.get("summary", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  COMPRESSION VALIDATION REPORT")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Rules: {meta.get('pre_rule_count', 0)} → {meta.get('post_rule_count', 0)}")
    print(f"  Trades evaluated: {meta.get('total_trades', 0)}")
    print()

    # Scores dashboard
    valid = summary.get("compression_valid", False)
    verdict = "✓ VALID" if valid else "✗ INVALID"
    print(f"  VERDICT: {verdict}")
    print()

    print("─── SCORES ─────────────────────────────────────────────────────")
    _score_bar("Performance Delta", results.get("performance_delta_score", 0), range_neg=True)
    _score_bar("Edge Preservation", results.get("edge_preservation_score", 0))
    _score_bar("Compression Efficiency", results.get("compression_efficiency_score", 0))
    print()

    # Performance comparison
    pre = results.get("pre_compression_sim", {})
    post = results.get("post_compression_sim", {})
    print("─── PERFORMANCE COMPARISON ─────────────────────────────────────")
    print(f"  {'':20} {'Pre-Compress':>14} {'Post-Compress':>14} {'Delta':>10}")
    print(f"  {'Trades':<20} {pre.get('trades', 0):>14} {post.get('trades', 0):>14} {post.get('trades', 0) - pre.get('trades', 0):>+10}")
    print(f"  {'Winrate':<20} {pre.get('winrate', 0):>13.1f}% {post.get('winrate', 0):>13.1f}% {perf.get('winrate_change', 0):>+9.1f}%")
    print(f"  {'Total PnL':<20} {pre.get('total_pnl', 0):>14.2f} {post.get('total_pnl', 0):>14.2f} {perf.get('pnl_change', 0):>+10.2f}")
    print(f"  {'Blocked':<20} {pre.get('blocked', 0):>14} {post.get('blocked', 0):>14} {perf.get('blocked_change', 0):>+10}")
    print()

    # False stability
    if false_stab.get("false_stability_flag"):
        print("  ⚠ FALSE STABILITY DETECTED")
        print(f"    {false_stab.get('explanation', '')}")
        print()

    # Lost signals
    if lost:
        high_lost = [l for l in lost if l["severity"] == "HIGH"]
        if high_lost:
            print(f"─── SIGNAL LOSS ({len(lost)} rules removed, {len(high_lost)} HIGH severity) ──")
            for l in high_lost:
                print(f"  ✗ {l['rule_id']} [{l['type']}→{l['target']}] conf={l['confidence']}")
            print()

    # Pattern changes
    if patterns:
        print("─── PATTERN EXPECTANCY CHANGES ─────────────────────────────────")
        for p in patterns[:5]:
            arrow = "↑" if p["change"] > 0 else "↓" if p["change"] < 0 else "─"
            print(f"  {arrow} {p['pattern']:<25} {p['pre_expectancy']:>8.2f} → {p['post_expectancy']:>8.2f} ({p['pct_change']:>+.1f}%)")
        print()

    # Recommendation
    print("─── RECOMMENDATION ─────────────────────────────────────────────")
    print(f"  {summary.get('recommendation', '')}")
    if summary.get("key_risks"):
        print("  Risks:")
        for risk in summary["key_risks"]:
            print(f"    ⚠ {risk}")
    print()
    print("═══════════════════════════════════════════════════════════════")


def _score_bar(label: str, score: int, range_neg: bool = False) -> None:
    if range_neg:
        # -100 to +100 range
        display = score
        normalized = (score + 100) // 10  # 0-20 range
        bar = "░" * max(0, 10 - normalized) + "█" * min(10, normalized)
    else:
        bar = "█" * (score // 10) + "░" * (10 - score // 10)
        display = score
    print(f"  {label:<26} {display:>4}  {bar}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pre_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/rules_latest.json"
    post_path = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rule_compression.json"
    curated = sys.argv[3] if len(sys.argv) > 3 else "events/curated"
    output = sys.argv[4] if len(sys.argv) > 4 else "analysis/reports/compression_validation.json"

    results = validate_compression(
        pre_rules_path=pre_path,
        post_rules_path=post_path,
        curated_dir=curated,
    )

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
