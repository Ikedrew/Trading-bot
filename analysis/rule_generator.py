"""
Auto Rule Generation Layer — Evidence-based strategy rule proposals.

Converts statistical insights from strategy_replay into structured rule
suggestions with evidence backing. Does NOT apply rules automatically.

Pipeline:
    DATA → REPLAY → INSIGHTS → RULE PROPOSALS (this module)

This system:
    - Produces rule suggestions ranked by expected impact
    - Attaches statistical evidence to each proposal
    - Includes risk notes and overfitting warnings
    - Never modifies trading logic directly
    - Never deletes patterns or disables systems

Rule Types:
    ADD_GATE        — Introduce a new condition required for entry
    TIGHTEN_GATE    — Make existing condition stricter
    LOOSEN_GATE     — Reduce restriction where profitable trades are filtered
    EXECUTION_CHANGE — Modify timing or execution logic

Usage:
    from analysis.rule_generator import generate_rules, print_rules
    from analysis.strategy_replay import run_full_analysis

    report = run_full_analysis()
    rules = generate_rules(report)
    print_rules(rules)
    export_rules(rules, "analysis/reports/rules_latest.json")
"""

from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RULE TYPES
# ═══════════════════════════════════════════════════════════════════════════════

RULE_TYPES = ("ADD_GATE", "TIGHTEN_GATE", "LOOSEN_GATE", "EXECUTION_CHANGE")


# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLDS (guard against overfitting)
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum trades required to consider a pattern/context statistically meaningful
MIN_SAMPLE_SIZE = 10

# Minimum trades for edge detection (more conservative)
MIN_EDGE_TRADES = 15

# Minimum winrate difference to propose a gate change (percentage points)
MIN_WINRATE_DELTA = 5.0

# Minimum PnL difference to propose a gate change
MIN_PNL_DELTA_PCT = 15.0  # 15% above/below baseline

# Maximum rules to generate per run
MAX_RULES = 10

# Minimum confidence to include a rule in output
MIN_CONFIDENCE = 25


# ═══════════════════════════════════════════════════════════════════════════════
# RULE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_rules(report: dict[str, Any]) -> dict[str, Any]:
    """
    Generate rule proposals from a strategy replay report.

    Args:
        report: Full analysis report from run_full_analysis()

    Returns:
        Rule generation output:
            - metadata: Generation parameters
            - rules: Ranked list of rule proposals
            - summary: Top insights summary
            - warnings: Overfitting risk assessment
    """
    if "error" in report:
        return {"error": report["error"], "rules": []}

    metadata = report.get("metadata", {})
    pattern_exp = report.get("pattern_expectancy", [])
    edge_matrix = report.get("contextual_edge_matrix", {})
    failures = report.get("failure_signatures", [])
    edges = report.get("hidden_edges", [])

    # Generate candidates from each analysis source
    candidates: list[dict[str, Any]] = []

    candidates.extend(_rules_from_failure_signatures(failures, metadata))
    candidates.extend(_rules_from_hidden_edges(edges, metadata))
    candidates.extend(_rules_from_contextual_matrix(edge_matrix, metadata))
    candidates.extend(_rules_from_pattern_expectancy(pattern_exp, metadata))

    # Deduplicate by rule_id
    seen_ids: set[str] = set()
    unique_rules: list[dict[str, Any]] = []
    for rule in candidates:
        if rule["rule_id"] not in seen_ids:
            seen_ids.add(rule["rule_id"])
            unique_rules.append(rule)

    # Filter by minimum confidence
    qualified = [r for r in unique_rules if r["confidence_score"] >= MIN_CONFIDENCE]

    # Sort by priority: confidence * expected_impact
    qualified.sort(key=lambda r: r["confidence_score"] * r.get("expected_impact", 1.0), reverse=True)

    # Cap at MAX_RULES
    final_rules = qualified[:MAX_RULES]

    # Assign priority ranks
    for rank, rule in enumerate(final_rules, 1):
        rule["priority_rank"] = rank

    # Build output
    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_report": metadata.get("generated_at", "unknown"),
            "total_trades_analysed": metadata.get("total_trades", 0),
            "candidates_generated": len(candidates),
            "rules_qualified": len(qualified),
            "rules_output": len(final_rules),
            "thresholds": {
                "min_sample_size": MIN_SAMPLE_SIZE,
                "min_edge_trades": MIN_EDGE_TRADES,
                "min_winrate_delta": MIN_WINRATE_DELTA,
                "min_confidence": MIN_CONFIDENCE,
                "max_rules": MAX_RULES,
            },
        },
        "rules": final_rules,
        "summary": _build_rule_summary(final_rules),
        "warnings": _build_warnings(final_rules, metadata),
    }

    logger.info(
        "[RULE_GEN] Generated %d rules from %d candidates (total trades: %d)",
        len(final_rules), len(candidates), metadata.get("total_trades", 0),
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# RULE GENERATORS (by source)
# ═══════════════════════════════════════════════════════════════════════════════

def _rules_from_failure_signatures(
    failures: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate TIGHTEN_GATE rules from loss clusters."""
    rules = []
    total_trades = metadata.get("total_trades", 1)

    for failure in failures:
        loss_count = failure.get("loss_count", 0)
        if loss_count < MIN_SAMPLE_SIZE:
            continue

        pattern = failure.get("pattern", "UNKNOWN")
        regime = failure.get("regime", "neutral")
        bias = failure.get("bias", "neutral")
        avg_loss = failure.get("avg_loss", 0)
        pct_losses = failure.get("pct_of_all_losses", 0)

        # Rule: tighten entry for this pattern in this regime/bias context
        condition_parts = []
        if regime != "neutral":
            condition_parts.append(f"atr_regime != '{regime}'")
        if bias != "neutral":
            condition_parts.append(f"htf_bias != '{bias}'")

        # If both are neutral, the failure is contextless — suggest stricter confirmation
        if not condition_parts:
            condition = f"IF pattern == '{pattern}': REQUIRE additional_confirmation_strength > current"
            rule_type = "TIGHTEN_GATE"
            expected_effect = (
                f"Reduce losses in {pattern} by requiring stronger entry confirmation. "
                f"Currently {loss_count} losses averaging {avg_loss:.2f} per trade."
            )
        else:
            condition = f"IF pattern == '{pattern}': BLOCK WHEN {' AND '.join(condition_parts)}"
            rule_type = "ADD_GATE"
            expected_effect = (
                f"Block {pattern} entries when {' and '.join(condition_parts)}. "
                f"Expected to prevent {loss_count} losses ({pct_losses:.1f}% of all losses)."
            )

        # Confidence: higher loss count + higher % of losses = more confident
        confidence = min(90, int(
            (min(loss_count, 50) / 50 * 40) +  # sample size component
            (pct_losses / 100 * 40) +           # impact component
            10                                   # base
        ))

        rule_id = _make_rule_id("failure", pattern, regime, bias)
        rules.append({
            "rule_id": rule_id,
            "type": rule_type,
            "target": pattern,
            "condition": condition,
            "expected_effect": expected_effect,
            "expected_impact": abs(avg_loss) * loss_count / total_trades,
            "supporting_evidence": {
                "source": "failure_signature_analysis",
                "pattern": pattern,
                "regime": regime,
                "bias": bias,
                "loss_count": loss_count,
                "avg_loss": avg_loss,
                "total_loss": failure.get("total_loss", 0),
                "pct_of_all_losses": pct_losses,
                "max_loss": failure.get("max_loss", 0),
            },
            "risk_notes": (
                "Tightening gates may also filter out some winning trades in this context. "
                "Verify that winners in the same regime/bias are not disproportionately affected. "
                f"Current dataset has {loss_count} losses — sufficient for initial signal, "
                "but recommend 50+ trades before live deployment."
            ),
            "confidence_score": confidence,
        })

    return rules


def _rules_from_hidden_edges(
    edges: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate LOOSEN_GATE or ADD_GATE rules from high-confidence edge zones."""
    rules = []

    for edge in edges:
        trades = edge.get("trades", 0)
        if trades < MIN_EDGE_TRADES:
            continue

        context = edge.get("context", "")
        dimension = edge.get("dimension", "")
        parts = edge.get("context_parts", [])
        avg_pnl = edge.get("avg_pnl", 0)
        winrate = edge.get("winrate", 0)
        baseline_wr = edge.get("baseline_winrate", 0)
        edge_wr = edge.get("edge_winrate", 0)
        confidence_raw = edge.get("confidence", 0)

        if edge_wr < MIN_WINRATE_DELTA:
            continue

        pattern = parts[0] if parts else "UNKNOWN"
        context_vals = parts[1:] if len(parts) > 1 else []

        # Determine rule type based on dimension
        if "liquidity" in dimension or "bos" in dimension:
            # Structure-based edge → execution change or loosen gate
            rule_type = "LOOSEN_GATE"
            condition = (
                f"IF pattern == '{pattern}' AND context == [{', '.join(context_vals)}]: "
                f"ALLOW with reduced confluence threshold"
            )
            expected_effect = (
                f"Loosen entry requirements for {pattern} in context [{context}]. "
                f"This subset shows {winrate:.1f}% winrate vs {baseline_wr:.1f}% baseline "
                f"(+{edge_wr:.1f}pp edge) over {trades} trades."
            )
        else:
            # Regime/bias edge → prioritise this combination
            rule_type = "ADD_GATE"
            condition = (
                f"IF pattern == '{pattern}': PRIORITISE WHEN context == [{', '.join(context_vals)}]"
            )
            expected_effect = (
                f"Prioritise {pattern} entries when {context}. "
                f"Avg PnL {avg_pnl:.2f} vs baseline {edge.get('baseline_avg_pnl', 0):.2f}. "
                f"Winrate {winrate:.1f}% vs {baseline_wr:.1f}% ({trades} trades)."
            )

        confidence = min(85, int(confidence_raw * 100))

        rule_id = _make_rule_id("edge", dimension, *parts)
        rules.append({
            "rule_id": rule_id,
            "type": rule_type,
            "target": pattern,
            "condition": condition,
            "expected_effect": expected_effect,
            "expected_impact": avg_pnl - edge.get("baseline_avg_pnl", 0),
            "supporting_evidence": {
                "source": "hidden_edge_detection",
                "dimension": dimension,
                "context": context,
                "trades": trades,
                "avg_pnl": avg_pnl,
                "winrate": winrate,
                "baseline_winrate": baseline_wr,
                "edge_winrate": edge_wr,
                "confidence_raw": confidence_raw,
            },
            "risk_notes": (
                f"Edge detected over {trades} trades. "
                f"{'Sample size is moderate — monitor for stability.' if trades < 30 else 'Sample size is reasonable.'} "
                "Edge may degrade in different market conditions or with regime changes. "
                "Recommend shadow-testing for 2+ weeks before live activation."
            ),
            "confidence_score": confidence,
        })

    return rules


def _rules_from_contextual_matrix(
    edge_matrix: dict[str, list[dict[str, Any]]],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate rules from contextual edge matrix — focus on underperformance."""
    rules = []
    baseline_wr = metadata.get("overall_winrate", 0)
    total_trades = metadata.get("total_trades", 1)

    for dimension, groups in edge_matrix.items():
        if not groups:
            continue

        # Find the worst-performing contexts per dimension
        for group in groups:
            trades = group.get("trades", 0)
            if trades < MIN_SAMPLE_SIZE:
                continue

            winrate = group.get("winrate", 0)
            avg_pnl = group.get("avg_pnl", 0)
            pattern = group.get("pattern", "UNKNOWN")
            context = group.get("context", "")

            # Look for significant underperformance
            if avg_pnl < 0 and winrate < baseline_wr - MIN_WINRATE_DELTA:
                # This context hurts performance → tighten gate
                rule_type = "TIGHTEN_GATE"
                condition = (
                    f"IF pattern == '{pattern}' AND {dimension.split('_x_')[1]} == '{context}': "
                    f"INCREASE confluence_threshold by 10%"
                )
                expected_effect = (
                    f"Raise entry bar for {pattern} when {dimension.split('_x_')[1]}={context}. "
                    f"Currently {winrate:.1f}% WR vs {baseline_wr:.1f}% system average "
                    f"(avg PnL: {avg_pnl:.2f}, {trades} trades)."
                )

                confidence = min(70, int(
                    (min(trades, 40) / 40 * 35) +  # sample size
                    (min(abs(baseline_wr - winrate), 30) / 30 * 25) +  # magnitude
                    10
                ))

                rule_id = _make_rule_id("matrix_under", pattern, dimension, context)
                rules.append({
                    "rule_id": rule_id,
                    "type": rule_type,
                    "target": pattern,
                    "condition": condition,
                    "expected_effect": expected_effect,
                    "expected_impact": abs(avg_pnl) * trades / total_trades,
                    "supporting_evidence": {
                        "source": "contextual_edge_matrix",
                        "dimension": dimension,
                        "pattern": pattern,
                        "context": context,
                        "trades": trades,
                        "winrate": winrate,
                        "avg_pnl": avg_pnl,
                        "baseline_winrate": baseline_wr,
                    },
                    "risk_notes": (
                        f"Based on {trades} trades in this context. "
                        "Tightening may reduce overall trade frequency. "
                        "Verify that the context variable is consistently measurable in live trading."
                    ),
                    "confidence_score": confidence,
                })

    return rules


def _rules_from_pattern_expectancy(
    patterns: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate rules from pattern expectancy — flag underperformers and suggest increases."""
    rules = []
    baseline_wr = metadata.get("overall_winrate", 0)
    total_trades = metadata.get("total_trades", 1)

    if not patterns:
        return rules

    # Compute baseline avg_pnl
    total_pnl = sum(p.get("total_pnl", 0) for p in patterns)
    baseline_avg = total_pnl / total_trades if total_trades > 0 else 0

    for pat in patterns:
        trades = pat.get("trades", 0)
        if trades < MIN_SAMPLE_SIZE:
            continue

        pattern_name = pat.get("pattern", "UNKNOWN")
        if pattern_name == "UNKNOWN":
            continue

        avg_pnl = pat.get("avg_pnl", 0)
        winrate = pat.get("winrate", 0)
        profit_factor = pat.get("profit_factor", 0)

        # High-performing pattern with small sample → suggest increased allocation
        if (avg_pnl > baseline_avg * 1.3 and winrate > baseline_wr + 5
                and trades < total_trades * 0.1):
            rule_type = "LOOSEN_GATE"
            condition = (
                f"IF pattern == '{pattern_name}': "
                f"REDUCE minimum_confluence_score by 5-10%"
            )
            expected_effect = (
                f"Allow more {pattern_name} trades by reducing entry threshold. "
                f"Pattern shows {winrate:.1f}% WR, avg PnL {avg_pnl:.2f} "
                f"(+{((avg_pnl/baseline_avg - 1) * 100):.0f}% vs baseline) "
                f"but only {trades}/{total_trades} of total volume."
            )

            confidence = min(65, int(
                (min(trades, 30) / 30 * 30) +
                (min((avg_pnl / baseline_avg - 1) * 100, 50) / 50 * 25) +
                10
            ))

            rule_id = _make_rule_id("expectancy_loosen", pattern_name)
            rules.append({
                "rule_id": rule_id,
                "type": rule_type,
                "target": pattern_name,
                "condition": condition,
                "expected_effect": expected_effect,
                "expected_impact": (avg_pnl - baseline_avg) * 0.5,  # Conservative estimate
                "supporting_evidence": {
                    "source": "pattern_expectancy",
                    "pattern": pattern_name,
                    "trades": trades,
                    "winrate": winrate,
                    "avg_pnl": avg_pnl,
                    "profit_factor": profit_factor if profit_factor != float("inf") else 999,
                    "baseline_avg_pnl": baseline_avg,
                    "baseline_winrate": baseline_wr,
                },
                "risk_notes": (
                    f"Pattern has only {trades} trades in dataset. "
                    "Small sample sizes can show deceptively high performance. "
                    "Recommend minimum 30 trades before loosening gates. "
                    "Monitor for degradation weekly after any change."
                ),
                "confidence_score": confidence,
            })

    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY & WARNINGS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_rule_summary(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Build executive summary of generated rules."""
    if not rules:
        return {"insight": "No statistically significant rules detected.", "actions": []}

    # Group by type
    by_type: dict[str, int] = {}
    for rule in rules:
        rt = rule.get("type", "UNKNOWN")
        by_type[rt] = by_type.get(rt, 0) + 1

    top_rule = rules[0]
    summary = {
        "total_rules": len(rules),
        "by_type": by_type,
        "top_rule": {
            "id": top_rule["rule_id"],
            "type": top_rule["type"],
            "target": top_rule["target"],
            "confidence": top_rule["confidence_score"],
            "summary": top_rule["expected_effect"][:150],
        },
        "strongest_insights": [],
    }

    # Extract top 2 insights
    for rule in rules[:2]:
        summary["strongest_insights"].append(
            f"[{rule['type']}] {rule['target']}: {rule['condition'][:100]}"
        )

    return summary


def _build_warnings(rules: list[dict[str, Any]], metadata: dict[str, Any]) -> list[str]:
    """Build overfitting risk warnings."""
    warnings = []
    total_trades = metadata.get("total_trades", 0)

    # Dataset size warning
    if total_trades < 100:
        warnings.append(
            f"CRITICAL: Only {total_trades} trades in dataset. "
            "Rules generated from <100 trades have high overfitting risk. "
            "Collect more data before acting on any suggestions."
        )
    elif total_trades < 500:
        warnings.append(
            f"CAUTION: {total_trades} trades provides moderate confidence. "
            "Prefer rules with 30+ supporting trades. "
            "Shadow-test all changes for 1+ month before live deployment."
        )

    # Low-sample rules warning
    low_sample_rules = [r for r in rules if r.get("supporting_evidence", {}).get("trades", 0) < 30]
    if low_sample_rules:
        warnings.append(
            f"{len(low_sample_rules)} rule(s) based on <30 trades. "
            "These are directional signals only — not deployment-ready. "
            "Wait for more data to confirm."
        )

    # Multiple rules on same target
    targets = [r["target"] for r in rules]
    duplicates = [t for t in set(targets) if targets.count(t) > 2]
    if duplicates:
        warnings.append(
            f"Multiple rules target the same pattern(s): {duplicates}. "
            "Avoid stacking contradictory rules. Implement one at a time and re-evaluate."
        )

    # General overfitting disclaimer
    warnings.append(
        "STANDARD DISCLAIMER: All rules are based on historical correlation, not causation. "
        "Market conditions change. Implement rules in shadow mode first, "
        "measure impact over 50+ new trades, then promote to production."
    )

    return warnings


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _make_rule_id(*parts: str) -> str:
    """Generate a stable, deterministic rule ID from components."""
    raw = "|".join(str(p) for p in parts)
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    prefix = parts[0] if parts else "rule"
    return f"{prefix}_{h}"


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_rules(output: dict[str, Any], path: str = "analysis/reports/rules_latest.json") -> str:
    """Export rule generation output to JSON file."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("[RULE_GEN] Rules exported to %s", filepath)
    return str(filepath)


def print_rules(output: dict[str, Any]) -> None:
    """Print a human-readable summary of generated rules."""
    meta = output.get("metadata", {})
    rules = output.get("rules", [])
    summary = output.get("summary", {})
    warnings = output.get("warnings", [])

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  AUTO RULE GENERATION — PROPOSALS")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Generated:  {meta.get('generated_at', '?')}")
    print(f"  Trades:     {meta.get('total_trades_analysed', 0)}")
    print(f"  Candidates: {meta.get('candidates_generated', 0)}")
    print(f"  Qualified:  {meta.get('rules_qualified', 0)}")
    print(f"  Output:     {meta.get('rules_output', 0)} rules")
    print()

    if not rules:
        print("  No rules generated — insufficient data or no significant patterns.")
        print()
        return

    # Rules table
    print("─── PROPOSED RULES (ranked by priority) ─────────────────────────")
    print()

    for rule in rules:
        rank = rule.get("priority_rank", "?")
        confidence = rule.get("confidence_score", 0)
        bar = "█" * (confidence // 10) + "░" * (10 - confidence // 10)

        print(f"  #{rank}  [{rule['type']}]  Confidence: {confidence}/100 {bar}")
        print(f"      Target:    {rule['target']}")
        print(f"      Condition: {rule['condition'][:80]}")
        print(f"      Effect:    {rule['expected_effect'][:100]}")

        evidence = rule.get("supporting_evidence", {})
        trades = evidence.get("trades", evidence.get("loss_count", 0))
        wr = evidence.get("winrate", evidence.get("avg_loss", "?"))
        print(f"      Evidence:  {trades} trades | source: {evidence.get('source', '?')}")
        print(f"      Risk:      {rule.get('risk_notes', '')[:80]}")
        print()

    # Warnings
    if warnings:
        print("─── OVERFITTING WARNINGS ────────────────────────────────────────")
        for w in warnings:
            print(f"  ⚠ {w}")
        print()

    # Summary
    if summary.get("strongest_insights"):
        print("─── TOP INSIGHTS ───────────────────────────────────────────────")
        for insight in summary["strongest_insights"]:
            print(f"  → {insight}")
        print()

    print("═══════════════════════════════════════════════════════════════")
    print("  IMPORTANT: These are proposals only. Do NOT apply automatically.")
    print("  Shadow-test each rule change and measure impact before deployment.")
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load analysis report
    report_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/latest.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rules_latest.json"

    print(f"[RULE_GEN] Loading report: {report_path}")

    report_file = Path(report_path)
    if not report_file.exists():
        # Run analysis first
        print("[RULE_GEN] Report not found — running analysis first...")
        from analysis.strategy_replay import run_full_analysis
        report = run_full_analysis()
    else:
        with open(report_file, "r", encoding="utf-8") as f:
            report = json.load(f)

    # Generate rules
    output = generate_rules(report)

    # Display
    print_rules(output)

    # Export
    export_rules(output, output_path)
    print(f"\n  Rules saved to: {output_path}")
