"""
Rule Compression Engine — Reduces rule entropy and improves system coherence.

Transforms a raw rule set into a minimal, non-conflicting, high-signal system.

Pipeline:
    1. Cluster rules by similarity
    2. Detect redundancy within clusters
    3. Merge redundant rules into composites
    4. Resolve conflicts via priority scoring
    5. Compress into final optimised rule set

This module ONLY reduces rule complexity. It does NOT:
    - Generate new rules
    - Evaluate trading performance
    - Modify strategy logic
    - Run walk-forward or shadow analysis

Usage:
    from analysis.rule_compression import compress_rules

    result = compress_rules(rules_path="analysis/reports/rules_latest.json")
    print(result["compression_ratio"])
    print(result["final_rule_set"])
"""

from __future__ import annotations

import json
import logging
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

REDUNDANCY_THRESHOLD = 70          # % similarity to consider redundant
TARGET_COMPRESSION_MIN = 0.30      # Minimum 30% reduction target
TARGET_COMPRESSION_MAX = 0.60      # Maximum 60% reduction target


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_rules(rules_path: str) -> list[dict[str, Any]]:
    """Load rules from JSON file."""
    path = Path(rules_path)
    if not path.exists():
        logger.warning("[COMPRESS] Rules file not found: %s", rules_path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rules = data.get("rules", [])
    logger.info("[COMPRESS] Loaded %d rules from %s", len(rules), rules_path)
    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — RULE CLUSTERING
# ═══════════════════════════════════════════════════════════════════════════════

def _cluster_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group rules by similarity: target pattern × rule type × evidence source.

    Rules in the same cluster operate in the same decision space.
    """
    cluster_map: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for rule in rules:
        # Cluster key: (target, type, source)
        target = rule.get("target", "UNKNOWN")
        rtype = rule.get("type", "UNKNOWN")
        source = rule.get("supporting_evidence", {}).get("source", "unknown")
        key = f"{target}|{rtype}|{source}"
        cluster_map[key].append(rule)

    clusters = []
    for key, members in cluster_map.items():
        parts = key.split("|")
        clusters.append({
            "cluster_id": f"cluster_{hashlib.md5(key.encode()).hexdigest()[:8]}",
            "cluster_key": {"target": parts[0], "type": parts[1], "source": parts[2]},
            "rules": [r.get("rule_id", "?") for r in members],
            "rule_objects": members,
            "count": len(members),
        })

    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — REDUNDANCY DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_redundancy(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Within each cluster, detect functionally equivalent rules.

    Criteria:
        - >70% overlap in condition logic
        - Identical effect on trade decision
        - Same target + same market condition impact
    """
    redundant_groups: list[dict[str, Any]] = []

    for cluster in clusters:
        members = cluster.get("rule_objects", [])
        if len(members) < 2:
            continue

        # All rules in the same cluster (same target+type+source) are
        # candidates for redundancy
        similarities = []
        for i, rule_a in enumerate(members):
            for j, rule_b in enumerate(members):
                if j <= i:
                    continue
                sim = _compute_similarity(rule_a, rule_b)
                similarities.append((rule_a, rule_b, sim))

        # Group rules that are mutually redundant
        redundant_ids: set[str] = set()
        for rule_a, rule_b, sim in similarities:
            if sim >= REDUNDANCY_THRESHOLD:
                redundant_ids.add(rule_a.get("rule_id", ""))
                redundant_ids.add(rule_b.get("rule_id", ""))

        if len(redundant_ids) >= 2:
            redundant_members = [r for r in members if r.get("rule_id", "") in redundant_ids]
            avg_sim = sum(s for _, _, s in similarities if s >= REDUNDANCY_THRESHOLD) / max(
                sum(1 for _, _, s in similarities if s >= REDUNDANCY_THRESHOLD), 1
            )
            redundant_groups.append({
                "cluster_id": cluster["cluster_id"],
                "redundant_rule_ids": list(redundant_ids),
                "redundant_rules": redundant_members,
                "count": len(redundant_ids),
                "avg_similarity": round(avg_sim, 1),
            })

    return redundant_groups


def _compute_similarity(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> int:
    """Compute functional similarity (0-100) between two rules."""
    score = 0

    # Same target (20)
    if rule_a.get("target") == rule_b.get("target"):
        score += 20

    # Same type (20)
    if rule_a.get("type") == rule_b.get("type"):
        score += 20

    # Same evidence source (15)
    src_a = rule_a.get("supporting_evidence", {}).get("source", "")
    src_b = rule_b.get("supporting_evidence", {}).get("source", "")
    if src_a and src_a == src_b:
        score += 15

    # Same dimension (15)
    dim_a = rule_a.get("supporting_evidence", {}).get("dimension", "")
    dim_b = rule_b.get("supporting_evidence", {}).get("dimension", "")
    if dim_a and dim_a == dim_b:
        score += 15

    # Similar confidence (10)
    conf_a = rule_a.get("confidence_score", 0)
    conf_b = rule_b.get("confidence_score", 0)
    if abs(conf_a - conf_b) <= 10:
        score += 10

    # Same context pattern (20)
    ctx_a = rule_a.get("supporting_evidence", {}).get("context", "")
    ctx_b = rule_b.get("supporting_evidence", {}).get("context", "")
    if ctx_a and ctx_b:
        # Partial match on context tokens
        tokens_a = set(ctx_a.lower().split())
        tokens_b = set(ctx_b.lower().split())
        if tokens_a and tokens_b:
            overlap = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
            score += int(overlap * 20)

    return score


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — RULE MERGING
# ═══════════════════════════════════════════════════════════════════════════════

def _merge_redundant_rules(redundant_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge redundant rules into single composite rules.

    Merge strategy:
        - Preserve strictest constraint (for TIGHTEN rules)
        - Weighted average confidence (by sample size)
        - Combine supporting evidence
        - Remove duplicate logic paths
    """
    merged: list[dict[str, Any]] = []

    for group in redundant_groups:
        members = group.get("redundant_rules", [])
        if len(members) < 2:
            continue

        # Select canonical rule: highest confidence × largest sample
        scored = []
        for r in members:
            conf = r.get("confidence_score", 0)
            trades = r.get("supporting_evidence", {}).get("trades",
                     r.get("supporting_evidence", {}).get("loss_count", 0))
            scored.append((conf * max(trades, 1), r))

        scored.sort(key=lambda x: x[0], reverse=True)
        canonical = scored[0][1]
        absorbed = [s[1] for s in scored[1:]]

        # Weighted confidence average
        total_weight = 0
        weighted_conf = 0
        for r in members:
            trades = r.get("supporting_evidence", {}).get("trades",
                     r.get("supporting_evidence", {}).get("loss_count", 1))
            weighted_conf += r.get("confidence_score", 0) * trades
            total_weight += trades

        merged_confidence = int(weighted_conf / total_weight) if total_weight > 0 else canonical.get("confidence_score", 0)

        # Combine evidence contexts
        all_contexts = list(set(
            r.get("supporting_evidence", {}).get("context", "")
            for r in members if r.get("supporting_evidence", {}).get("context")
        ))
        all_dimensions = list(set(
            r.get("supporting_evidence", {}).get("dimension", "")
            for r in members if r.get("supporting_evidence", {}).get("dimension")
        ))

        # Build merged rule
        merged_rule = {
            "rule_id": f"merged_{canonical.get('rule_id', 'unknown')[:16]}",
            "type": canonical.get("type", ""),
            "target": canonical.get("target", ""),
            "condition": canonical.get("condition", ""),
            "expected_effect": canonical.get("expected_effect", ""),
            "confidence_score": merged_confidence,
            "merged_from": [r.get("rule_id", "") for r in members],
            "merge_count": len(members),
            "supporting_evidence": {
                **canonical.get("supporting_evidence", {}),
                "merged_contexts": all_contexts,
                "merged_dimensions": all_dimensions,
                "combined_sample_size": total_weight,
            },
            "risk_notes": (
                f"Merged from {len(members)} rules. "
                f"Canonical: {canonical.get('rule_id', '?')}. "
                f"Combined sample: {total_weight} trades. "
                f"Confidence: {merged_confidence}/100 (weighted average)."
            ),
        }
        merged.append(merged_rule)

    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — CONFLICT RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_conflicts(rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Detect and resolve conflicts in the rule set.

    Resolution priority:
        1. Higher confidence_score
        2. Larger sample size
        3. Stronger statistical support (pnl magnitude)
        4. TIGHTEN_GATE wins ties vs LOOSEN_GATE (conservative)

    Returns:
        (resolved_rules, conflict_resolutions)
    """
    # Group by target to find opposing rules
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in rules:
        by_target[rule.get("target", "UNKNOWN")].append(rule)

    kept_rules: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []

    for target, group in by_target.items():
        if len(group) == 1:
            kept_rules.append(group[0])
            continue

        # Separate by direction
        restrictors = [r for r in group if r.get("type") in ("TIGHTEN_GATE", "ADD_GATE")]
        permitters = [r for r in group if r.get("type") in ("LOOSEN_GATE",)]
        others = [r for r in group if r.get("type") not in ("TIGHTEN_GATE", "ADD_GATE", "LOOSEN_GATE")]

        # Others always pass through
        kept_rules.extend(others)

        if restrictors and permitters:
            # CONFLICT: opposing directions on same target
            # Pick winner by priority scoring
            all_conflicting = restrictors + permitters
            winner = _pick_conflict_winner(all_conflicting)
            losers = [r for r in all_conflicting if r.get("rule_id") != winner.get("rule_id")]

            kept_rules.append(winner)
            resolutions.append({
                "target": target,
                "conflict_type": "opposing_direction",
                "winner": winner.get("rule_id", "?"),
                "winner_type": winner.get("type", "?"),
                "winner_confidence": winner.get("confidence_score", 0),
                "removed": [r.get("rule_id", "?") for r in losers],
                "removed_types": [r.get("type", "?") for r in losers],
                "reason": _explain_resolution(winner, losers),
            })
        else:
            # No opposing conflict — keep all (may have multiple same-direction)
            # But if multiple same-direction, keep best only
            if len(restrictors) > 1:
                best = _pick_conflict_winner(restrictors)
                kept_rules.append(best)
                removed = [r for r in restrictors if r.get("rule_id") != best.get("rule_id")]
                if removed:
                    resolutions.append({
                        "target": target,
                        "conflict_type": "same_direction_redundancy",
                        "winner": best.get("rule_id", "?"),
                        "winner_type": best.get("type", "?"),
                        "winner_confidence": best.get("confidence_score", 0),
                        "removed": [r.get("rule_id", "?") for r in removed],
                        "removed_types": [r.get("type", "?") for r in removed],
                        "reason": "Multiple same-direction rules consolidated to strongest.",
                    })
            else:
                kept_rules.extend(restrictors)

            if len(permitters) > 1:
                best = _pick_conflict_winner(permitters)
                kept_rules.append(best)
                removed = [r for r in permitters if r.get("rule_id") != best.get("rule_id")]
                if removed:
                    resolutions.append({
                        "target": target,
                        "conflict_type": "same_direction_redundancy",
                        "winner": best.get("rule_id", "?"),
                        "winner_type": best.get("type", "?"),
                        "winner_confidence": best.get("confidence_score", 0),
                        "removed": [r.get("rule_id", "?") for r in removed],
                        "removed_types": [r.get("type", "?") for r in removed],
                        "reason": "Multiple same-direction rules consolidated to strongest.",
                    })
            else:
                kept_rules.extend(permitters)

    return kept_rules, resolutions


def _pick_conflict_winner(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the winning rule from conflicting candidates."""
    def priority_score(rule: dict[str, Any]) -> tuple:
        conf = rule.get("confidence_score", 0)
        evidence = rule.get("supporting_evidence", {})
        trades = evidence.get("trades", evidence.get("loss_count", 0))
        pnl_mag = abs(evidence.get("avg_pnl", evidence.get("avg_loss", 0)))
        # TIGHTEN/ADD wins ties (conservative bias)
        conservative_bonus = 5 if rule.get("type") in ("TIGHTEN_GATE", "ADD_GATE") else 0
        return (conf + conservative_bonus, trades, pnl_mag)

    return max(candidates, key=priority_score)


def _explain_resolution(winner: dict[str, Any], losers: list[dict[str, Any]]) -> str:
    """Generate human-readable explanation for conflict resolution."""
    w_conf = winner.get("confidence_score", 0)
    w_type = winner.get("type", "?")
    l_types = [r.get("type", "?") for r in losers]
    return (
        f"Kept {w_type} (confidence={w_conf}) over {len(losers)} opposing rule(s) "
        f"({', '.join(set(l_types))}). Resolution by priority: confidence > sample > magnitude."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — FINAL COMPRESSION
# ═══════════════════════════════════════════════════════════════════════════════

def _compress_final(resolved_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Final pass: ensure no overlapping decision pathways remain.

    Constraints:
        - One rule per unique (target × type) combination
        - No duplicate condition logic
        - Deduplication by rule_id
    """
    seen_ids: set[str] = set()
    seen_domains: set[str] = set()
    final: list[dict[str, Any]] = []

    # Sort by confidence descending (best rules first)
    sorted_rules = sorted(resolved_rules, key=lambda r: r.get("confidence_score", 0), reverse=True)

    for rule in sorted_rules:
        rule_id = rule.get("rule_id", "")

        # Dedup by ID
        if rule_id in seen_ids:
            continue

        # Dedup by domain (target × type)
        domain = f"{rule.get('target', '')}|{rule.get('type', '')}"
        if domain in seen_domains:
            continue

        seen_ids.add(rule_id)
        seen_domains.add(domain)
        final.append(rule)

    return final


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_metrics(
    original: list[dict[str, Any]],
    final: list[dict[str, Any]],
    redundant_groups: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute before/after system metrics."""
    orig_count = len(original)
    final_count = len(final)

    # Redundancy score: ratio of redundant rules in original set
    redundant_count = sum(g.get("count", 0) for g in redundant_groups)
    redundancy_before = int(redundant_count / max(orig_count, 1) * 100)
    redundancy_after = 0  # After compression, no redundancy should remain

    # Entropy: unique (target × type) combinations vs total rules
    # High entropy = many unique decision points (good)
    # Low entropy = many rules in same decision space (bad)
    orig_domains = set(f"{r.get('target', '')}|{r.get('type', '')}" for r in original)
    final_domains = set(f"{r.get('target', '')}|{r.get('type', '')}" for r in final)

    entropy_before = int(len(orig_domains) / max(orig_count, 1) * 100)
    entropy_after = int(len(final_domains) / max(final_count, 1) * 100)

    # Estimated stability improvement
    conflict_count_before = len(resolutions) + sum(
        1 for g in redundant_groups if g.get("count", 0) > 1
    )
    stability_improvement = min(60, int(
        (1 - final_count / max(orig_count, 1)) * 30 +  # Compression benefit
        conflict_count_before * 5 +                      # Resolved conflicts
        (entropy_after - entropy_before) * 0.5           # Entropy improvement
    ))

    return {
        "redundancy_score_before": redundancy_before,
        "redundancy_score_after": redundancy_after,
        "entropy_before": entropy_before,
        "entropy_after": entropy_after,
        "conflict_count_before": conflict_count_before,
        "conflict_count_after": 0,
        "stability_improvement_estimate": stability_improvement,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def compress_rules(
    *,
    rules_path: str = "analysis/reports/rules_latest.json",
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run the full rule compression pipeline.

    Args:
        rules_path: Path to generated rules JSON
        rules: Direct rule list (overrides rules_path)

    Returns:
        Complete compression output with metrics and final rule set.
    """
    if rules is None:
        rules = _load_rules(rules_path)

    if not rules:
        return {"error": "no_rules", "final_rule_set": []}

    original_count = len(rules)

    # Step 1: Cluster
    clusters = _cluster_rules(rules)

    # Step 2: Detect redundancy
    redundant_groups = _detect_redundancy(clusters)

    # Step 3: Merge redundant rules
    merged_rules = _merge_redundant_rules(redundant_groups)

    # Build working set: replace redundant groups with merged versions
    removed_ids: set[str] = set()
    for group in redundant_groups:
        for rid in group.get("redundant_rule_ids", []):
            removed_ids.add(rid)

    non_redundant = [r for r in rules if r.get("rule_id", "") not in removed_ids]
    working_set = non_redundant + merged_rules

    # Step 4: Resolve conflicts
    resolved, resolutions = _resolve_conflicts(working_set)

    # Step 5: Final compression
    final_set = _compress_final(resolved)

    # Compute metrics
    metrics = _compute_metrics(rules, final_set, redundant_groups, resolutions)

    compression_ratio = round(1 - len(final_set) / max(original_count, 1), 3)

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rules_path": rules_path,
        },
        "original_rule_count": original_count,
        "compressed_rule_count": len(final_set),
        "compression_ratio": compression_ratio,
        "clusters": [
            {k: v for k, v in c.items() if k != "rule_objects"}
            for c in clusters
        ],
        "merged_rules": [
            {"rule_id": m["rule_id"], "merged_from": m["merged_from"],
             "merge_count": m["merge_count"], "confidence": m["confidence_score"]}
            for m in merged_rules
        ],
        "resolved_rules": [r.get("rule_id", "?") for r in resolved],
        "removed_redundant_rules": sorted(removed_ids),
        "conflict_resolutions": resolutions,
        "system_metrics": metrics,
        "final_rule_set": final_set,
    }

    logger.info(
        "[COMPRESS] %d → %d rules (%.0f%% compression), %d conflicts resolved, "
        "%d redundancies merged",
        original_count, len(final_set), compression_ratio * 100,
        len(resolutions), len(merged_rules),
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/rule_compression.json") -> str:
    """Export compression results to JSON."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("[COMPRESS] Exported to %s", filepath)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    """Print human-readable compression summary."""
    orig = results.get("original_rule_count", 0)
    final = results.get("compressed_rule_count", 0)
    ratio = results.get("compression_ratio", 0)
    metrics = results.get("system_metrics", {})
    resolutions = results.get("conflict_resolutions", [])
    merged = results.get("merged_rules", [])
    final_set = results.get("final_rule_set", [])

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  RULE COMPRESSION ENGINE")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"  Original rules:    {orig}")
    print(f"  Compressed rules:  {final}")
    print(f"  Compression:       {ratio:.0%} reduction")
    print()

    # Before/after metrics
    print("─── SYSTEM METRICS (before → after) ────────────────────────────")
    print(f"  Redundancy:   {metrics.get('redundancy_score_before', 0)}% → {metrics.get('redundancy_score_after', 0)}%")
    print(f"  Entropy:      {metrics.get('entropy_before', 0)}% → {metrics.get('entropy_after', 0)}%")
    print(f"  Conflicts:    {metrics.get('conflict_count_before', 0)} → {metrics.get('conflict_count_after', 0)}")
    print(f"  Est. stability improvement: +{metrics.get('stability_improvement_estimate', 0)} points")
    print()

    # Merges
    if merged:
        print(f"─── MERGES ({len(merged)}) ──────────────────────────────────────────")
        for m in merged:
            print(f"  {m['rule_id']} ← merged from {m['merge_count']} rules (conf={m['confidence']})")
        print()

    # Conflict resolutions
    if resolutions:
        print(f"─── CONFLICT RESOLUTIONS ({len(resolutions)}) ──────────────────────")
        for r in resolutions:
            print(f"  Target: {r['target']}")
            print(f"    Winner: {r['winner']} ({r['winner_type']}, conf={r['winner_confidence']})")
            print(f"    Removed: {r['removed']}")
            print(f"    Reason: {r['reason'][:80]}")
            print()

    # Final rule set
    if final_set:
        print(f"─── FINAL RULE SET ({len(final_set)} rules) ─────────────────────────")
        for rule in final_set:
            conf = rule.get("confidence_score", 0)
            bar = "█" * (conf // 20) + "░" * (5 - conf // 20)
            print(f"  {bar} {rule.get('rule_id', '?'):<30} [{rule.get('type', '?')}] → {rule.get('target', '?')}")
        print()

    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rules_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/rules_latest.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rule_compression.json"

    results = compress_rules(rules_path=rules_path)

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output_path)
    print(f"  Report saved to: {output_path}")
