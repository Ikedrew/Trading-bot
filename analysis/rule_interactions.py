"""
Rule Interaction & Conflict Safety Layer — Detects instability from rule combinations.

Analyses relationships between generated trading rules to identify:
    - Conflicting rules (opposing effects on same target)
    - Redundant rules (overlapping logic producing same outcome)
    - Overlapping domain clusters (multiple rules in same decision space)
    - Rule stacking risk (compounding filter density)

This module ONLY analyses. It does NOT:
    - Perform walk-forward analysis
    - Run shadow execution
    - Generate or modify rules
    - Change trading logic

Usage:
    from analysis.rule_interactions import run_interaction_analysis

    result = run_interaction_analysis(rules_path="analysis/reports/rules_latest.json")
    print(result["system_risk"])
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
# SAFETY THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

RISK_THRESHOLD_UNSTABLE = 70         # System unstable above this
MAX_RULES_SAME_DECISION = 3          # High risk if exceeded
REDUNDANCY_SIMILARITY_THRESHOLD = 70  # % overlap to flag as redundant
CONFLICT_SEVERITY_HIGH = 75
CONFLICT_SEVERITY_MEDIUM = 50


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_rules(rules_path: str) -> list[dict[str, Any]]:
    """Load generated rules from JSON file."""
    path = Path(rules_path)
    if not path.exists():
        logger.warning("[INTERACT] Rules file not found: %s", rules_path)
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = data.get("rules", [])
    logger.info("[INTERACT] Loaded %d rules from %s", len(rules), rules_path)
    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# RULE FINGERPRINTING (for similarity comparison)
# ═══════════════════════════════════════════════════════════════════════════════

def _fingerprint(rule: dict[str, Any]) -> dict[str, Any]:
    """Extract normalised decision-space fingerprint from a rule."""
    evidence = rule.get("supporting_evidence", {})
    return {
        "target": rule.get("target", ""),
        "type": rule.get("type", ""),
        "source": evidence.get("source", ""),
        "dimension": evidence.get("dimension", ""),
        "regime": evidence.get("regime", ""),
        "bias": evidence.get("bias", ""),
        "context": evidence.get("context", ""),
        "pattern": evidence.get("pattern", rule.get("target", "")),
    }


def _effect_direction(rule: dict[str, Any]) -> str:
    """Classify whether a rule restricts or permits trades."""
    rt = rule.get("type", "")
    if rt in ("TIGHTEN_GATE", "ADD_GATE"):
        return "RESTRICT"
    elif rt in ("LOOSEN_GATE",):
        return "PERMIT"
    elif rt == "EXECUTION_CHANGE":
        return "MODIFY"
    return "UNKNOWN"


def _decision_domain(rule: dict[str, Any]) -> tuple[str, str, str]:
    """Return the decision domain a rule operates in: (pattern, stage, context)."""
    target = rule.get("target", "UNKNOWN")
    rt = rule.get("type", "")
    evidence = rule.get("supporting_evidence", {})

    # Stage classification
    if rt in ("ADD_GATE", "TIGHTEN_GATE"):
        stage = "filtering"
    elif rt == "LOOSEN_GATE":
        stage = "entry"
    elif rt == "EXECUTION_CHANGE":
        stage = "execution"
    else:
        stage = "unknown"

    # Context from evidence
    context = evidence.get("dimension", evidence.get("source", "general"))

    return (target, stage, context)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CONFLICT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_conflicts(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Detect conflicting rule pairs.

    Conflict occurs when:
        - Two rules target the same pattern
        - They have opposing effects (one restricts, other permits)
        - They operate in overlapping condition space
    """
    conflicts: list[dict[str, Any]] = []

    for i, rule_a in enumerate(rules):
        for j, rule_b in enumerate(rules):
            if j <= i:
                continue

            conflict = _check_pair_conflict(rule_a, rule_b)
            if conflict:
                conflicts.append(conflict)

    # Sort by severity
    conflicts.sort(key=lambda c: c["severity"], reverse=True)
    return conflicts


def _check_pair_conflict(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> dict[str, Any] | None:
    """Check if two rules conflict."""
    target_a = rule_a.get("target", "")
    target_b = rule_b.get("target", "")

    # Must affect same target
    if target_a != target_b:
        return None

    dir_a = _effect_direction(rule_a)
    dir_b = _effect_direction(rule_b)

    # ─── Direct opposition: RESTRICT vs PERMIT on same target ─────────
    if (dir_a == "RESTRICT" and dir_b == "PERMIT") or (dir_a == "PERMIT" and dir_b == "RESTRICT"):
        # Check if they share condition space
        fp_a = _fingerprint(rule_a)
        fp_b = _fingerprint(rule_b)
        overlap = _compute_fingerprint_overlap(fp_a, fp_b)

        if overlap > 30:  # Meaningful overlap in condition space
            severity = min(100, int(50 + overlap * 0.5))
            return {
                "rule_a": rule_a.get("rule_id", "?"),
                "rule_b": rule_b.get("rule_id", "?"),
                "target": target_a,
                "direction_a": dir_a,
                "direction_b": dir_b,
                "type_a": rule_a.get("type", "?"),
                "type_b": rule_b.get("type", "?"),
                "conflict_type": "opposing_direction",
                "condition_overlap_pct": overlap,
                "severity": severity,
                "resolution": (
                    f"Rules have opposing effects on '{target_a}'. "
                    f"One restricts while other permits in overlapping conditions. "
                    f"Choose the rule with higher confidence or combine into conditional logic."
                ),
            }

    # ─── Threshold contradiction: both restrict but different thresholds ──
    if dir_a == "RESTRICT" and dir_b == "RESTRICT":
        type_a = rule_a.get("type", "")
        type_b = rule_b.get("type", "")
        if type_a == "TIGHTEN_GATE" and type_b == "ADD_GATE":
            # ADD_GATE adds new condition, TIGHTEN strengthens existing
            # If same source, may compound excessively
            src_a = rule_a.get("supporting_evidence", {}).get("source", "")
            src_b = rule_b.get("supporting_evidence", {}).get("source", "")
            if src_a == src_b:
                return {
                    "rule_a": rule_a.get("rule_id", "?"),
                    "rule_b": rule_b.get("rule_id", "?"),
                    "target": target_a,
                    "direction_a": dir_a,
                    "direction_b": dir_b,
                    "type_a": type_a,
                    "type_b": type_b,
                    "conflict_type": "compounding_restriction",
                    "condition_overlap_pct": 80,
                    "severity": CONFLICT_SEVERITY_MEDIUM,
                    "resolution": (
                        f"Both rules restrict '{target_a}' from same evidence source. "
                        f"Risk of over-filtering. Consolidate into single rule."
                    ),
                }

    return None


def _compute_fingerprint_overlap(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> int:
    """Compute % overlap between two rule fingerprints (0-100)."""
    fields = ("target", "source", "dimension", "regime", "bias", "context", "pattern")
    matches = 0
    compared = 0

    for field in fields:
        val_a = str(fp_a.get(field, "")).strip()
        val_b = str(fp_b.get(field, "")).strip()

        if not val_a and not val_b:
            continue  # Both empty — skip

        compared += 1
        if val_a == val_b:
            matches += 1

    if compared == 0:
        return 0

    return int(matches / compared * 100)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REDUNDANCY DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_redundancies(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Detect clusters of redundant rules.

    Redundancy occurs when:
        - Same target + same type + same/similar condition space
        - >70% fingerprint similarity
        - Produce overlapping effect
    """
    clusters: list[dict[str, Any]] = []
    assigned: set[str] = set()

    for i, rule_a in enumerate(rules):
        rid_a = rule_a.get("rule_id", f"r{i}")
        if rid_a in assigned:
            continue

        cluster_members = [rid_a]
        cluster_rules = [rule_a]

        for j, rule_b in enumerate(rules):
            if j <= i:
                continue
            rid_b = rule_b.get("rule_id", f"r{j}")
            if rid_b in assigned:
                continue

            similarity = _compute_rule_similarity(rule_a, rule_b)
            if similarity >= REDUNDANCY_SIMILARITY_THRESHOLD:
                cluster_members.append(rid_b)
                cluster_rules.append(rule_b)
                assigned.add(rid_b)

        if len(cluster_members) > 1:
            assigned.add(rid_a)
            # Pick highest confidence rule as canonical
            best = max(cluster_rules, key=lambda r: r.get("confidence_score", 0))

            clusters.append({
                "cluster_id": f"redundancy_{hashlib.md5('|'.join(cluster_members).encode()).hexdigest()[:8]}",
                "rules": cluster_members,
                "count": len(cluster_members),
                "target": rule_a.get("target", "?"),
                "type": rule_a.get("type", "?"),
                "similarity_score": _compute_rule_similarity(cluster_rules[0], cluster_rules[1]),
                "canonical_rule": best.get("rule_id", "?"),
                "recommendation": (
                    f"Consolidate {len(cluster_members)} rules into single rule "
                    f"(keep '{best.get('rule_id', '?')}' — highest confidence "
                    f"at {best.get('confidence_score', 0)}/100)."
                ),
            })

    return clusters


def _compute_rule_similarity(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> int:
    """Compute similarity score (0-100) between two rules."""
    score = 0
    max_score = 0

    # Same target (25 points)
    max_score += 25
    if rule_a.get("target") == rule_b.get("target"):
        score += 25

    # Same type (25 points)
    max_score += 25
    if rule_a.get("type") == rule_b.get("type"):
        score += 25

    # Same evidence source (20 points)
    max_score += 20
    src_a = rule_a.get("supporting_evidence", {}).get("source", "")
    src_b = rule_b.get("supporting_evidence", {}).get("source", "")
    if src_a and src_a == src_b:
        score += 20

    # Same dimension (15 points)
    max_score += 15
    dim_a = rule_a.get("supporting_evidence", {}).get("dimension", "")
    dim_b = rule_b.get("supporting_evidence", {}).get("dimension", "")
    if dim_a and dim_a == dim_b:
        score += 15

    # Similar confidence (15 points — within 10 of each other)
    max_score += 15
    conf_a = rule_a.get("confidence_score", 0)
    conf_b = rule_b.get("confidence_score", 0)
    if abs(conf_a - conf_b) <= 10:
        score += 15

    return int(score / max_score * 100) if max_score > 0 else 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. OVERLAP DOMAIN CLUSTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_domain_clusters(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group rules by decision domain (pattern × stage × context).

    Clusters with 3+ rules represent high-density decision points
    that may cause filter compounding.
    """
    # Group by domain
    domain_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for rule in rules:
        domain = _decision_domain(rule)
        domain_groups[domain].append(rule)

    clusters: list[dict[str, Any]] = []

    for domain, group_rules in domain_groups.items():
        if len(group_rules) < 2:
            continue  # Single rule in domain — no interaction

        pattern, stage, context = domain
        rule_ids = [r.get("rule_id", "?") for r in group_rules]

        # Risk score: more rules = higher risk, opposing directions = higher risk
        directions = [_effect_direction(r) for r in group_rules]
        has_opposing = ("RESTRICT" in directions and "PERMIT" in directions)

        density = len(group_rules)
        risk = min(100, int(
            (min(density, 5) / 5 * 50) +                  # Density component
            (30 if has_opposing else 0) +                  # Conflict component
            (20 if density >= MAX_RULES_SAME_DECISION else 0)  # Threshold breach
        ))

        clusters.append({
            "cluster_id": f"domain_{hashlib.md5(str(domain).encode()).hexdigest()[:8]}",
            "domain": {"pattern": pattern, "stage": stage, "context": context},
            "rules": rule_ids,
            "count": len(rule_ids),
            "directions": dict(zip(rule_ids, directions)),
            "has_opposing_directions": has_opposing,
            "risk_score": risk,
            "recommendation": _cluster_recommendation(density, has_opposing, pattern, stage),
        })

    # Sort by risk score
    clusters.sort(key=lambda c: c["risk_score"], reverse=True)
    return clusters


def _cluster_recommendation(density: int, has_opposing: bool, pattern: str, stage: str) -> str:
    """Generate recommendation for a domain cluster."""
    if has_opposing:
        return (
            f"CONFLICT in {pattern}/{stage}: opposing rule directions detected. "
            f"Resolve by choosing one direction or adding explicit priority ordering."
        )
    if density >= MAX_RULES_SAME_DECISION:
        return (
            f"SATURATION in {pattern}/{stage}: {density} rules in same decision space. "
            f"Consolidate into 1-2 composite rules to avoid filter compounding."
        )
    return (
        f"MODERATE overlap in {pattern}/{stage}: {density} rules. "
        f"Monitor for compounding effects during shadow testing."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RULE STACK RISK ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_system_risk(
    rules: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    redundancies: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Evaluate full rule set interaction and compute system risk score.

    Components:
        - Conflict density (high-severity conflicts)
        - Redundancy waste (overlapping rules)
        - Domain saturation (rules per decision point)
        - Total rule count pressure
    """
    # ─── Component scores ─────────────────────────────────────────────

    # 1. Conflict score (0-40): high-severity conflicts are dangerous
    high_conflicts = sum(1 for c in conflicts if c.get("severity", 0) >= CONFLICT_SEVERITY_HIGH)
    med_conflicts = sum(1 for c in conflicts if CONFLICT_SEVERITY_MEDIUM <= c.get("severity", 0) < CONFLICT_SEVERITY_HIGH)
    conflict_score = min(40, high_conflicts * 20 + med_conflicts * 10)

    # 2. Redundancy score (0-20): wasted rule space increases complexity
    redundant_rules = sum(c.get("count", 0) - 1 for c in redundancies)  # Extra beyond canonical
    redundancy_score = min(20, redundant_rules * 5)

    # 3. Saturation score (0-25): high-risk clusters
    saturated_clusters = sum(1 for c in clusters if c.get("risk_score", 0) >= 50)
    saturation_score = min(25, saturated_clusters * 10)

    # 4. Volume pressure (0-15): too many rules = complexity risk
    if len(rules) > 10:
        volume_score = 15
    elif len(rules) > 7:
        volume_score = 10
    elif len(rules) > 5:
        volume_score = 5
    else:
        volume_score = 0

    # Combined risk score
    risk_score = min(100, conflict_score + redundancy_score + saturation_score + volume_score)
    instability = risk_score > RISK_THRESHOLD_UNSTABLE

    # ─── Build recommendations ────────────────────────────────────────
    recommendations: list[str] = []

    if instability:
        recommendations.append(
            f"CRITICAL: System risk score {risk_score}/100 exceeds threshold ({RISK_THRESHOLD_UNSTABLE}). "
            "Rule set is unstable. Reduce active rules before deployment."
        )

    if high_conflicts > 0:
        recommendations.append(
            f"Resolve {high_conflicts} high-severity conflict(s) before any rule goes live."
        )

    if redundant_rules > 0:
        recommendations.append(
            f"Consolidate {redundant_rules} redundant rule(s) into canonical versions."
        )

    if saturated_clusters > 0:
        recommendations.append(
            f"{saturated_clusters} domain cluster(s) are over-saturated. "
            "Merge rules or add priority ordering."
        )

    if not recommendations:
        recommendations.append("Rule set appears safe. No critical interactions detected.")

    return {
        "rule_stack_risk_score": risk_score,
        "instability_flag": instability,
        "risk_level": "CRITICAL" if risk_score > 70 else "WARNING" if risk_score > 40 else "LOW",
        "components": {
            "conflict_score": conflict_score,
            "redundancy_score": redundancy_score,
            "saturation_score": saturation_score,
            "volume_score": volume_score,
        },
        "stats": {
            "total_rules": len(rules),
            "high_severity_conflicts": high_conflicts,
            "medium_severity_conflicts": med_conflicts,
            "redundant_rules": redundant_rules,
            "saturated_clusters": saturated_clusters,
        },
        "recommendations": recommendations,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_interaction_analysis(
    *,
    rules_path: str = "analysis/reports/rules_latest.json",
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run complete rule interaction and conflict safety analysis.

    Args:
        rules_path: Path to generated rules JSON file
        rules: Direct rule list (overrides rules_path if provided)

    Returns:
        {
            "metadata": {...},
            "conflicts": [{rule_a, rule_b, conflict_type, severity}],
            "redundancies": [{cluster_id, rules, similarity_score}],
            "interaction_clusters": [{cluster_id, rules, risk_score}],
            "system_risk": {rule_stack_risk_score, instability_flag},
        }
    """
    if rules is None:
        rules = _load_rules(rules_path)

    if not rules:
        return {
            "metadata": {"error": "no_rules", "rules_path": rules_path},
            "conflicts": [],
            "redundancies": [],
            "interaction_clusters": [],
            "system_risk": {
                "rule_stack_risk_score": 0,
                "instability_flag": False,
                "risk_level": "LOW",
                "recommendations": ["No rules to analyse."],
            },
        }

    # Run all analyses
    conflicts = _detect_conflicts(rules)
    redundancies = _detect_redundancies(rules)
    clusters = _detect_domain_clusters(rules)
    system_risk = _compute_system_risk(rules, conflicts, redundancies, clusters)

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rules_path": rules_path,
            "total_rules_analysed": len(rules),
            "unique_targets": len(set(r.get("target", "") for r in rules)),
            "unique_types": len(set(r.get("type", "") for r in rules)),
        },
        "conflicts": conflicts,
        "redundancies": redundancies,
        "interaction_clusters": clusters,
        "system_risk": system_risk,
    }

    logger.info(
        "[INTERACT] Analysis complete — %d rules, %d conflicts, %d redundancies, "
        "%d clusters, risk=%d/100",
        len(rules), len(conflicts), len(redundancies),
        len(clusters), system_risk["rule_stack_risk_score"],
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/rule_interactions.json") -> str:
    """Export interaction analysis results to JSON file."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("[INTERACT] Results exported to %s", filepath)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    """Print human-readable interaction analysis summary."""
    meta = results.get("metadata", {})
    conflicts = results.get("conflicts", [])
    redundancies = results.get("redundancies", [])
    clusters = results.get("interaction_clusters", [])
    risk = results.get("system_risk", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  RULE INTERACTION & CONFLICT ANALYSIS")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Generated:  {meta.get('generated_at', '?')}")
    print(f"  Rules:      {meta.get('total_rules_analysed', 0)}")
    print(f"  Targets:    {meta.get('unique_targets', 0)}")
    print(f"  Types:      {meta.get('unique_types', 0)}")
    print()

    # System risk
    score = risk.get("rule_stack_risk_score", 0)
    level = risk.get("risk_level", "?")
    flag = "⚠ UNSTABLE" if risk.get("instability_flag") else "✓ STABLE"
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    print(f"─── SYSTEM RISK: {score}/100 [{level}] {flag} ──────────────────")
    print(f"  {bar}")
    components = risk.get("components", {})
    print(f"    Conflicts:   {components.get('conflict_score', 0)}/40")
    print(f"    Redundancy:  {components.get('redundancy_score', 0)}/20")
    print(f"    Saturation:  {components.get('saturation_score', 0)}/25")
    print(f"    Volume:      {components.get('volume_score', 0)}/15")
    print()

    # Conflicts
    if conflicts:
        print(f"─── CONFLICTS ({len(conflicts)}) ──────────────────────────────────────")
        for c in conflicts:
            sev_bar = "●" * (c["severity"] // 20) + "○" * (5 - c["severity"] // 20)
            print(f"  {sev_bar} [{c['severity']}/100] {c['rule_a']} ↔ {c['rule_b']}")
            print(f"        Type: {c['conflict_type']} | Target: {c['target']}")
            print(f"        {c['resolution'][:80]}")
            print()
    else:
        print("─── CONFLICTS: None detected ✓ ─────────────────────────────────")
        print()

    # Redundancies
    if redundancies:
        print(f"─── REDUNDANCIES ({len(redundancies)}) ────────────────────────────────")
        for r in redundancies:
            print(f"  Cluster: {r['cluster_id']} ({r['count']} rules, similarity={r['similarity_score']}%)")
            print(f"    Rules: {r['rules']}")
            print(f"    → {r['recommendation'][:80]}")
            print()
    else:
        print("─── REDUNDANCIES: None detected ✓ ──────────────────────────────")
        print()

    # Domain clusters
    high_risk_clusters = [c for c in clusters if c["risk_score"] >= 50]
    if high_risk_clusters:
        print(f"─── HIGH-RISK CLUSTERS ({len(high_risk_clusters)}) ─────────────────────")
        for c in high_risk_clusters:
            d = c["domain"]
            print(f"  {c['cluster_id']} (risk={c['risk_score']}/100)")
            print(f"    Domain: {d['pattern']} / {d['stage']} / {d['context']}")
            print(f"    Rules:  {c['rules']} {'⚡ OPPOSING' if c['has_opposing_directions'] else ''}")
            print(f"    → {c['recommendation'][:80]}")
            print()

    # Recommendations
    recs = risk.get("recommendations", [])
    if recs:
        print("─── RECOMMENDATIONS ────────────────────────────────────────────")
        for rec in recs:
            print(f"  • {rec}")
        print()

    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rules_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/rules_latest.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rule_interactions.json"

    results = run_interaction_analysis(rules_path=rules_path)

    if results.get("metadata", {}).get("error"):
        print(f"ERROR: {results['metadata']['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output_path)
    print(f"  Report saved to: {output_path}")
