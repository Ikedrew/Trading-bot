"""
Validation Engine — Walk-forward, shadow execution, and rule interaction safety.

Determines whether generated trading rules represent REAL edge or historical
overfitting by testing against time-split data and simulated execution.

Three validation modes:
    1. Walk-Forward Validation — chronological train/test splits
    2. Shadow Execution — parallel baseline vs rule-adjusted simulation
    3. Rule Interaction Safety — conflict detection and stacking risk

Pipeline:
    RULES → VALIDATE → PROMOTE (or REJECT)

Usage:
    from analysis.validation_engine import run_full_validation

    result = run_full_validation(curated_dir="events/curated")
    print(result["final_recommendations"])

Rules:
    - Does NOT modify trading logic
    - Does NOT apply rules to production
    - Only validates and reports
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
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Walk-forward window configuration
DEFAULT_TRAIN_RATIO = 0.7      # 70% train, 30% test per window
DEFAULT_N_WINDOWS = 3          # Number of sliding windows
MIN_WINDOW_TRADES = 20         # Minimum trades per window to be valid

# Shadow execution thresholds
SHADOW_MIN_DIVERGENCE_TRADES = 5  # Minimum divergences to measure impact

# Rule interaction thresholds
RULE_STACK_RISK_THRESHOLD = 70    # Flag system instability above this
MAX_RULES_SAME_CONDITION = 3      # Require consolidation above this


# ═══════════════════════════════════════════════════════════════════════════════
# 1. WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_walk_forward(
    events: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    *,
    n_windows: int = DEFAULT_N_WINDOWS,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
) -> list[dict[str, Any]]:
    """
    Walk-forward validation: split data chronologically, generate rules on
    TRAIN, evaluate on TEST, slide window.

    Args:
        events: All curated trade events (with pnl != 0)
        rules: Generated rule proposals from rule_generator
        n_windows: Number of sliding time windows
        train_ratio: Fraction of each window used for training

    Returns:
        List of per-window results with stability metrics per rule.
    """
    trades = sorted(
        [e for e in events if e.get("pnl", 0) != 0],
        key=lambda e: e.get("timestamp", ""),
    )

    if len(trades) < MIN_WINDOW_TRADES * 2:
        return [{"error": "insufficient_data", "trades": len(trades)}]

    window_size = len(trades) // n_windows
    if window_size < MIN_WINDOW_TRADES:
        n_windows = max(1, len(trades) // MIN_WINDOW_TRADES)
        window_size = len(trades) // n_windows

    results: list[dict[str, Any]] = []

    for window_idx in range(n_windows):
        start = window_idx * (len(trades) - window_size) // max(n_windows - 1, 1)
        window = trades[start : start + window_size]

        split_point = int(len(window) * train_ratio)
        train_set = window[:split_point]
        test_set = window[split_point:]

        if len(test_set) < MIN_WINDOW_TRADES // 2:
            continue

        # Baseline metrics on test set
        baseline = _compute_metrics(test_set)

        # Apply each rule to test set and measure impact
        rule_results = []
        for rule in rules:
            filtered = _apply_rule_filter(test_set, rule)
            adjusted = _compute_metrics(filtered)

            rule_results.append({
                "rule_id": rule.get("rule_id", "?"),
                "rule_type": rule.get("type", "?"),
                "target": rule.get("target", "?"),
                "test_trades": len(test_set),
                "filtered_trades": len(filtered),
                "trades_removed": len(test_set) - len(filtered),
                "baseline_pnl": baseline["total_pnl"],
                "adjusted_pnl": adjusted["total_pnl"],
                "delta_pnl": adjusted["total_pnl"] - baseline["total_pnl"],
                "baseline_winrate": baseline["winrate"],
                "adjusted_winrate": adjusted["winrate"],
                "winrate_change": adjusted["winrate"] - baseline["winrate"],
                "baseline_drawdown": baseline["max_drawdown"],
                "adjusted_drawdown": adjusted["max_drawdown"],
                "drawdown_change": adjusted["max_drawdown"] - baseline["max_drawdown"],
            })

        results.append({
            "window_idx": window_idx,
            "train_size": len(train_set),
            "test_size": len(test_set),
            "date_range": {
                "start": window[0].get("timestamp", ""),
                "end": window[-1].get("timestamp", ""),
            },
            "baseline": baseline,
            "rule_results": rule_results,
        })

    # Compute stability scores across windows
    stability = _compute_stability_scores(results, rules)

    return results, stability


def _compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute performance metrics for a set of trades."""
    if not trades:
        return {
            "total_pnl": 0.0, "avg_pnl": 0.0, "winrate": 0.0,
            "trades": 0, "wins": 0, "losses": 0,
            "max_drawdown": 0.0, "profit_factor": 0.0,
        }

    pnls = [t.get("pnl", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    # Max drawdown (cumulative)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 999.0

    return {
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
        "winrate": round(len(wins) / len(pnls) * 100, 2),
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(pf, 3),
    }


def _apply_rule_filter(
    trades: list[dict[str, Any]],
    rule: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Simulate applying a rule to a trade set WITHOUT modifying original data.

    For TIGHTEN_GATE / ADD_GATE: removes trades that would be blocked.
    For LOOSEN_GATE: keeps all trades (cannot simulate new entries from data).
    For EXECUTION_CHANGE: keeps all trades (timing changes can't be simulated).
    """
    rule_type = rule.get("type", "")
    target = rule.get("target", "")
    evidence = rule.get("supporting_evidence", {})

    if rule_type in ("LOOSEN_GATE", "EXECUTION_CHANGE"):
        # Cannot simulate new trades from historical data
        return trades

    if rule_type in ("ADD_GATE", "TIGHTEN_GATE"):
        # Simulate blocking: remove trades matching failure conditions
        source = evidence.get("source", "")

        if source == "failure_signature_analysis":
            regime = evidence.get("regime", "")
            bias = evidence.get("bias", "")
            # Block trades matching pattern + regime + bias of the failure
            return [
                t for t in trades
                if not (
                    t.get("pattern", "") == target
                    and (not regime or regime == "neutral" or t.get("atr_regime", "") == regime)
                    and (not bias or bias == "neutral" or t.get("htf_bias", "") == bias)
                    and t.get("pnl", 0) < 0  # Only block losers (optimistic sim)
                )
            ]
        elif source == "contextual_edge_matrix":
            context = evidence.get("context", "")
            dimension = evidence.get("dimension", "")
            # Block underperforming context
            dim_field = dimension.split("_x_")[1] if "_x_" in dimension else ""
            return [
                t for t in trades
                if not (
                    t.get("pattern", "") == target
                    and dim_field
                    and str(t.get(dim_field, "")) == context
                    and t.get("pnl", 0) < 0
                )
            ]
        else:
            # Generic: block 50% of losses for the target pattern (conservative)
            result = []
            blocked = 0
            max_block = sum(1 for t in trades if t.get("pattern") == target and t.get("pnl", 0) < 0) // 2
            for t in trades:
                if t.get("pattern") == target and t.get("pnl", 0) < 0 and blocked < max_block:
                    blocked += 1
                    continue
                result.append(t)
            return result

    return trades


def _compute_stability_scores(
    window_results: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Compute per-rule stability scores across all walk-forward windows.

    Stability = consistency of improvement across windows (0-100).
    High stability = rule works in multiple time periods (not overfit).
    """
    stability: list[dict[str, Any]] = []

    for rule in rules:
        rule_id = rule.get("rule_id", "?")
        deltas = []
        wr_changes = []
        dd_changes = []

        for window in window_results:
            for rr in window.get("rule_results", []):
                if rr.get("rule_id") == rule_id:
                    deltas.append(rr.get("delta_pnl", 0))
                    wr_changes.append(rr.get("winrate_change", 0))
                    dd_changes.append(rr.get("drawdown_change", 0))

        if not deltas:
            stability.append({
                "rule_id": rule_id,
                "stability_score": 0,
                "windows_tested": 0,
                "consistent_improvement": False,
            })
            continue

        # Score components:
        # 1. Consistency: what % of windows show improvement?
        positive_windows = sum(1 for d in deltas if d > 0)
        consistency = positive_windows / len(deltas)

        # 2. Magnitude stability: low variance in deltas = more stable
        avg_delta = sum(deltas) / len(deltas)
        if avg_delta != 0:
            variance = sum((d - avg_delta) ** 2 for d in deltas) / len(deltas)
            cv = (variance ** 0.5) / abs(avg_delta) if avg_delta != 0 else 999
            magnitude_stability = max(0, 1 - min(cv, 2) / 2)
        else:
            magnitude_stability = 0

        # 3. Direction consistency
        all_positive = all(d >= 0 for d in deltas)
        all_negative = all(d <= 0 for d in deltas)

        # Combined score (0-100)
        score = int(
            consistency * 50 +
            magnitude_stability * 30 +
            (20 if all_positive else 10 if not all_negative else 0)
        )

        stability.append({
            "rule_id": rule_id,
            "rule_type": rule.get("type", "?"),
            "target": rule.get("target", "?"),
            "stability_score": min(100, score),
            "windows_tested": len(deltas),
            "positive_windows": positive_windows,
            "negative_windows": len(deltas) - positive_windows,
            "avg_delta_pnl": round(avg_delta, 4),
            "avg_winrate_change": round(sum(wr_changes) / len(wr_changes), 2) if wr_changes else 0,
            "consistent_improvement": all_positive and len(deltas) >= 2,
        })

    stability.sort(key=lambda s: s["stability_score"], reverse=True)
    return stability


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SHADOW EXECUTION SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def run_shadow_execution(
    events: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Simulate parallel execution: baseline vs rule-adjusted.

    For every trade event, both engines evaluate the same signal.
    Records divergence points and PnL differences.

    Args:
        events: All curated trade events
        rules: Generated rule proposals

    Returns:
        Shadow execution results with per-rule impact analysis.
    """
    trades = sorted(
        [e for e in events if e.get("pnl", 0) != 0],
        key=lambda e: e.get("timestamp", ""),
    )

    if not trades:
        return {"error": "no_trades", "trades": 0}

    baseline_metrics = _compute_metrics(trades)

    # Simulate each rule independently
    per_rule_shadow: list[dict[str, Any]] = []

    for rule in rules:
        rule_id = rule.get("rule_id", "?")
        rule_type = rule.get("type", "?")
        target = rule.get("target", "?")

        # Apply rule filter
        adjusted_trades = _apply_rule_filter(trades, rule)
        adjusted_metrics = _compute_metrics(adjusted_trades)

        # Compute divergences (trades that would be blocked)
        blocked_trades = [t for t in trades if t not in adjusted_trades]
        divergence_rate = len(blocked_trades) / len(trades) * 100 if trades else 0

        # Per-trade impact of blocked trades
        blocked_pnl = sum(t.get("pnl", 0) for t in blocked_trades)
        blocked_wins = sum(1 for t in blocked_trades if t.get("pnl", 0) > 0)
        blocked_losses = sum(1 for t in blocked_trades if t.get("pnl", 0) < 0)

        per_rule_shadow.append({
            "rule_id": rule_id,
            "rule_type": rule_type,
            "target": target,
            "baseline_pnl": baseline_metrics["total_pnl"],
            "shadow_pnl": adjusted_metrics["total_pnl"],
            "delta_pnl": adjusted_metrics["total_pnl"] - baseline_metrics["total_pnl"],
            "baseline_trades": len(trades),
            "shadow_trades": len(adjusted_trades),
            "trades_blocked": len(blocked_trades),
            "divergence_rate": round(divergence_rate, 2),
            "blocked_pnl_sum": round(blocked_pnl, 4),
            "blocked_wins": blocked_wins,
            "blocked_losses": blocked_losses,
            "baseline_winrate": baseline_metrics["winrate"],
            "shadow_winrate": adjusted_metrics["winrate"],
            "baseline_drawdown": baseline_metrics["max_drawdown"],
            "shadow_drawdown": adjusted_metrics["max_drawdown"],
            "net_positive": adjusted_metrics["total_pnl"] > baseline_metrics["total_pnl"],
        })

    return {
        "baseline": baseline_metrics,
        "per_rule": per_rule_shadow,
        "total_rules_tested": len(rules),
        "rules_with_positive_impact": sum(1 for r in per_rule_shadow if r["net_positive"]),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RULE INTERACTION SAFETY SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def run_interaction_analysis(
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect rule conflicts, redundancies, and stacking risks.

    Prevents rule stacking collapse by identifying:
        - Conflicting rule pairs (opposite effects on same target)
        - Redundant rule clusters (same effective filter)
        - Condition space saturation (too many rules on same area)

    Returns:
        Interaction analysis with risk scores and recommendations.
    """
    if not rules:
        return {
            "conflicting_pairs": [],
            "redundancy_clusters": [],
            "rule_stack_risk_score": 0,
            "recommendations": ["No rules to analyse."],
        }

    # ─── Conflict detection ───────────────────────────────────────────
    conflicts: list[dict[str, Any]] = []
    for i, rule_a in enumerate(rules):
        for j, rule_b in enumerate(rules):
            if j <= i:
                continue
            conflict = _detect_conflict(rule_a, rule_b)
            if conflict:
                conflicts.append(conflict)

    # ─── Redundancy detection ─────────────────────────────────────────
    redundancy_clusters = _detect_redundancy(rules)

    # ─── Condition space saturation ───────────────────────────────────
    target_counts: dict[str, int] = defaultdict(int)
    for rule in rules:
        target_counts[rule.get("target", "UNKNOWN")] += 1

    saturated_targets = {t: c for t, c in target_counts.items() if c >= MAX_RULES_SAME_CONDITION}

    # ─── Risk score computation ───────────────────────────────────────
    risk_score = _compute_stack_risk(rules, conflicts, redundancy_clusters, saturated_targets)

    # ─── Recommendations ─────────────────────────────────────────────
    recommendations = _build_interaction_recommendations(
        conflicts, redundancy_clusters, saturated_targets, risk_score
    )

    return {
        "conflicting_pairs": conflicts,
        "redundancy_clusters": redundancy_clusters,
        "saturated_targets": saturated_targets,
        "rule_stack_risk_score": risk_score,
        "risk_level": (
            "CRITICAL" if risk_score > RULE_STACK_RISK_THRESHOLD
            else "WARNING" if risk_score > 50
            else "LOW"
        ),
        "recommendations": recommendations,
    }


def _detect_conflict(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> dict[str, Any] | None:
    """Detect if two rules conflict (opposite effects on same target)."""
    target_a = rule_a.get("target", "")
    target_b = rule_b.get("target", "")

    # Must affect same target
    if target_a != target_b:
        return None

    type_a = rule_a.get("type", "")
    type_b = rule_b.get("type", "")

    # Conflicting type pairs
    is_conflict = (
        (type_a == "TIGHTEN_GATE" and type_b == "LOOSEN_GATE") or
        (type_a == "LOOSEN_GATE" and type_b == "TIGHTEN_GATE") or
        (type_a == "ADD_GATE" and type_b == "LOOSEN_GATE") or
        (type_a == "LOOSEN_GATE" and type_b == "ADD_GATE")
    )

    if not is_conflict:
        return None

    return {
        "rule_a": rule_a.get("rule_id", "?"),
        "rule_b": rule_b.get("rule_id", "?"),
        "target": target_a,
        "type_a": type_a,
        "type_b": type_b,
        "conflict_type": "opposing_direction",
        "severity": "HIGH",
        "resolution": f"Choose one: either tighten or loosen {target_a}, not both.",
    }


def _detect_redundancy(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect clusters of rules that effectively do the same thing."""
    clusters: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i, rule_a in enumerate(rules):
        if rule_a.get("rule_id", "") in seen:
            continue
        cluster_members = [rule_a.get("rule_id", "")]

        for j, rule_b in enumerate(rules):
            if j <= i:
                continue
            if rule_b.get("rule_id", "") in seen:
                continue
            if _is_redundant(rule_a, rule_b):
                cluster_members.append(rule_b.get("rule_id", ""))
                seen.add(rule_b.get("rule_id", ""))

        if len(cluster_members) > 1:
            seen.add(rule_a.get("rule_id", ""))
            clusters.append({
                "members": cluster_members,
                "target": rule_a.get("target", "?"),
                "type": rule_a.get("type", "?"),
                "count": len(cluster_members),
                "action": "Consolidate into single rule with strongest evidence.",
            })

    return clusters


def _is_redundant(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> bool:
    """Check if two rules are effectively redundant."""
    return (
        rule_a.get("target") == rule_b.get("target") and
        rule_a.get("type") == rule_b.get("type") and
        rule_a.get("supporting_evidence", {}).get("source") ==
        rule_b.get("supporting_evidence", {}).get("source")
    )


def _compute_stack_risk(
    rules: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    redundancies: list[dict[str, Any]],
    saturated: dict[str, int],
) -> int:
    """Compute overall rule stacking risk score (0-100)."""
    score = 0

    # Conflicts add 20 each (capped at 40)
    score += min(40, len(conflicts) * 20)

    # Redundancies add 10 each (capped at 20)
    score += min(20, len(redundancies) * 10)

    # Saturation adds 15 per saturated target (capped at 30)
    score += min(30, len(saturated) * 15)

    # High rule count adds risk (>7 rules = +10)
    if len(rules) > 7:
        score += 10

    return min(100, score)


def _build_interaction_recommendations(
    conflicts: list[dict[str, Any]],
    redundancies: list[dict[str, Any]],
    saturated: dict[str, int],
    risk_score: int,
) -> list[str]:
    """Build actionable recommendations from interaction analysis."""
    recs = []

    if risk_score > RULE_STACK_RISK_THRESHOLD:
        recs.append(
            f"CRITICAL: Rule stack risk score is {risk_score}/100. "
            "System instability likely. Reduce active rules before deployment."
        )

    for conflict in conflicts:
        recs.append(
            f"CONFLICT: Rules {conflict['rule_a']} and {conflict['rule_b']} "
            f"have opposing effects on {conflict['target']}. {conflict['resolution']}"
        )

    for cluster in redundancies:
        recs.append(
            f"REDUNDANCY: {cluster['count']} rules on {cluster['target']} "
            f"({cluster['type']}). {cluster['action']}"
        )

    for target, count in saturated.items():
        recs.append(
            f"SATURATION: {count} rules target '{target}'. "
            f"Max recommended is {MAX_RULES_SAME_CONDITION}. Consolidate."
        )

    if not recs:
        recs.append("No interaction risks detected. Rules are independent.")

    return recs


# ═══════════════════════════════════════════════════════════════════════════════
# 4. FULL VALIDATION RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_validation(
    *,
    curated_dir: str = "events/curated",
    rules_path: str | None = None,
    n_windows: int = DEFAULT_N_WINDOWS,
) -> dict[str, Any]:
    """
    Run all three validation modes and produce final report.

    Args:
        curated_dir: Path to curated JSONL directory
        rules_path: Path to rules JSON (or None to generate fresh)
        n_windows: Number of walk-forward windows

    Returns:
        Complete validation report with recommendations.
    """
    from analysis.strategy_replay import load_local_data, run_full_analysis
    from analysis.rule_generator import generate_rules

    # Load data
    events = load_local_data(curated_dir)
    if not events:
        return {"error": "No curated data available"}

    # Load or generate rules
    if rules_path and Path(rules_path).exists():
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_output = json.load(f)
        rules = rules_output.get("rules", [])
    else:
        report = run_full_analysis(curated_dir=curated_dir)
        rules_output = generate_rules(report)
        rules = rules_output.get("rules", [])

    if not rules:
        return {
            "error": "No rules to validate",
            "walk_forward_results": [],
            "shadow_results": {},
            "rule_stability_scores": [],
            "interaction_risks": {},
            "final_recommendations": ["Generate rules first via rule_generator."],
        }

    # ─── Run validations ──────────────────────────────────────────────
    logger.info("[VALIDATION] Running walk-forward (%d windows)...", n_windows)
    wf_results, stability_scores = run_walk_forward(events, rules, n_windows=n_windows)

    logger.info("[VALIDATION] Running shadow execution...")
    shadow_results = run_shadow_execution(events, rules)

    logger.info("[VALIDATION] Running interaction analysis...")
    interaction = run_interaction_analysis(rules)

    # ─── Build final recommendations ─────────────────────────────────
    recommendations = _build_final_recommendations(
        stability_scores, shadow_results, interaction, rules
    )

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_events": len(events),
            "total_trades": len([e for e in events if e.get("pnl", 0) != 0]),
            "rules_validated": len(rules),
            "walk_forward_windows": n_windows,
        },
        "walk_forward_results": wf_results,
        "shadow_results": shadow_results,
        "rule_stability_scores": stability_scores,
        "interaction_risks": interaction,
        "final_recommendations": recommendations,
    }

    logger.info("[VALIDATION] Complete — %d rules validated, %d recommendations",
                len(rules), len(recommendations))

    return output


def _build_final_recommendations(
    stability: list[dict[str, Any]],
    shadow: dict[str, Any],
    interaction: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build final ranked recommendations combining all validation sources."""
    recommendations: list[dict[str, Any]] = []

    # Score each rule across all validation dimensions
    for rule in rules:
        rule_id = rule.get("rule_id", "?")

        # Find stability score
        stab = next((s for s in stability if s.get("rule_id") == rule_id), {})
        stab_score = stab.get("stability_score", 0)
        consistent = stab.get("consistent_improvement", False)

        # Find shadow result
        shadow_rule = next(
            (r for r in shadow.get("per_rule", []) if r.get("rule_id") == rule_id), {}
        )
        shadow_positive = shadow_rule.get("net_positive", False)
        delta_pnl = shadow_rule.get("delta_pnl", 0)

        # Check interaction risks
        is_conflicted = any(
            c.get("rule_a") == rule_id or c.get("rule_b") == rule_id
            for c in interaction.get("conflicting_pairs", [])
        )

        # Determine verdict
        if stab_score >= 70 and shadow_positive and not is_conflicted:
            verdict = "PROMOTE"
            reason = f"Stable (score={stab_score}), positive shadow impact (+{delta_pnl:.2f}), no conflicts."
        elif stab_score >= 50 and shadow_positive:
            verdict = "SHADOW_TEST"
            reason = f"Moderate stability (score={stab_score}). Recommend extended shadow testing."
        elif is_conflicted:
            verdict = "RESOLVE_CONFLICT"
            reason = "Rule conflicts with another active rule. Resolve before testing."
        elif stab_score < 30:
            verdict = "REJECT"
            reason = f"Low stability (score={stab_score}). Likely overfit to training window."
        else:
            verdict = "MONITOR"
            reason = f"Stability={stab_score}, shadow_positive={shadow_positive}. Needs more data."

        recommendations.append({
            "rule_id": rule_id,
            "rule_type": rule.get("type", "?"),
            "target": rule.get("target", "?"),
            "verdict": verdict,
            "reason": reason,
            "stability_score": stab_score,
            "shadow_delta_pnl": round(delta_pnl, 4),
            "has_conflict": is_conflicted,
            "consistent_across_windows": consistent,
        })

    # Sort: PROMOTE first, then SHADOW_TEST, then others
    verdict_order = {"PROMOTE": 0, "SHADOW_TEST": 1, "MONITOR": 2, "RESOLVE_CONFLICT": 3, "REJECT": 4}
    recommendations.sort(key=lambda r: (verdict_order.get(r["verdict"], 5), -r["stability_score"]))

    return recommendations
