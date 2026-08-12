"""
Bottleneck Detection and Next-Investigation Recommendation.

Examines existing research results to identify research-supported bottlenecks.
Does NOT invent conclusions — derives them from actual evidence.

Key design:
    - CROSS is never reported as a bottleneck itself — decompose into components
    - next-investigation is driven by the detected bottleneck, not random negatives
    - Uses question metadata (universes, fields, intent) for relationships
    - Distinguishes evidence from actionability
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question
from research_engine.v10.universes.models import Universe, QuestionStatus
from research_engine.v10.runner.primitive_mapping import QUESTION_PARAMETERS

_QUESTIONS_DIR = Path("reports/research/questions")


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def _question_components(q) -> list[str]:
    """Extract the bot components/domains a question tests."""
    components = []
    for u in q.required_universes:
        components.append(u.value)
    # Also derive from question fields/intent
    params = QUESTION_PARAMETERS.get(q.question_id, {})
    feature = params.get("feature_field", params.get("predicted_field", ""))
    if feature:
        components.append(f"field:{feature}")
    return components


def _question_domain_label(q) -> str:
    """Human-readable domain label for a question."""
    universes = [u.value for u in q.required_universes]
    if len(universes) == 1:
        return universes[0].capitalize()
    # Cross-domain: identify the interaction
    parts = [u[:4].capitalize() for u in universes]
    return " × ".join(parts)


def _derive_implicated_component(qid: str, q) -> str:
    """Determine what bot component a finding implicates."""
    decision = q.decision_enabled.lower()

    # Order matters: more specific matches first
    if "regime" in decision and ("gate" in decision or "adapt" in decision or "conservative" in decision):
        return "regime_adaptation"
    if "regime" in decision:
        return "regime_adaptation"
    if "htf" in decision or "alignment" in decision:
        return "htf_alignment"
    if "score" in decision and "threshold" in decision:
        return "scoring_thresholds"
    if "scoring" in decision or ("score" in decision and "weight" in decision):
        return "scoring_model"
    if "strategy" in decision and ("gate" in decision or "confidence" in decision):
        return "strategy_confidence"
    if "strategy" in decision or "family" in decision:
        return "strategy_selection"
    if "pattern" in decision and ("disable" in decision or "weight" in decision):
        return "pattern_selection"
    if "pattern" in decision:
        return "pattern_selection"
    if "session" in decision:
        return "session_filtering"
    if "stop" in decision or "sl" in decision:
        return "stop_placement"
    if "sizing" in decision or "position" in decision:
        return "position_sizing"
    if "exit" in decision or "trailing" in decision:
        return "exit_management"
    if "opportunity" in decision or "quality" in decision:
        return "opportunity_assessment"
    if "risk" in decision or "guard" in decision:
        return "risk_gates"
    if "execution" in decision and ("improve" in decision or "parameter" in decision):
        return "execution_parameters"
    if "edge" in decision and "lost" in decision:
        return "edge_leakage"
    if "execution" in decision:
        return "execution_quality"
    if "focus" in decision or "improvement" in decision:
        return "system_focus"
    if "trust" in decision:
        return "evidence_trust"
    return "general"


def _component_product_impact(component: str) -> str:
    """Explain what aspect of the bot this component constrains."""
    impacts = {
        "regime_adaptation": "The bot's ability to adapt behaviour to different market conditions — affects trade frequency, strategy selection, and when to be active.",
        "htf_alignment": "The bot's directional conviction from higher timeframes — affects trade direction confidence and entry quality.",
        "scoring_model": "The decision engine's ability to distinguish good from bad opportunities — directly affects hit rate.",
        "scoring_thresholds": "Whether the bot is accepting the right opportunities — too strict reduces frequency, too loose reduces quality.",
        "strategy_selection": "Which strategy family the bot deploys — affects whether the approach matches current conditions.",
        "strategy_confidence": "Whether strategy confidence correctly predicts outcome — affects position sizing and gating.",
        "pattern_selection": "Which candlestick patterns the bot uses as entry triggers — affects signal quality.",
        "session_filtering": "When the bot is active during the day — affects execution quality and opportunity volume.",
        "stop_placement": "Where the bot places stop losses — affects risk:reward and survival probability.",
        "position_sizing": "How large positions are — affects capital growth rate and drawdown.",
        "exit_management": "How the bot manages open positions — affects profit capture and loss limitation.",
        "opportunity_assessment": "How the bot scores and ranks opportunities — affects which trades are taken.",
        "risk_gates": "Whether risk guards are adding value or over-filtering — affects trade frequency.",
        "execution_quality": "How well the bot translates decisions into broker fills — affects slippage and realised edge.",
        "execution_parameters": "Whether execution differs by strategy/condition — affects specialised strategies.",
        "edge_leakage": "Where theoretical edge is lost between decision and realised outcome.",
        "system_focus": "What the overall system improvement priority should be.",
    }
    return impacts.get(component, "General system performance.")


# ═══════════════════════════════════════════════════════════════════════════════
# BOTTLENECK ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════


def analyse_bottleneck():
    """Analyse existing results for research-supported bottlenecks."""
    findings = _load_all_findings()

    # Classify each question's evidence
    evidence_items: list[dict] = []
    for qid, finding in findings.items():
        q = get_question(qid)
        if q is None:
            continue
        outcome = finding.get("outcome", "")
        confidence = finding.get("confidence", "")

        if confidence == "INSUFFICIENT" or outcome == "INCONCLUSIVE":
            evidence_items.append({
                "qid": qid, "q": q, "finding": finding,
                "type": "inconclusive",
            })
            continue

        if outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
            weight = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(confidence, 0)
            evidence_items.append({
                "qid": qid, "q": q, "finding": finding,
                "type": "negative", "weight": weight,
                "component": _derive_implicated_component(qid, q),
            })
        elif outcome in ("POSITIVE", "PREDICTIVE", "WELL_CALIBRATED"):
            evidence_items.append({
                "qid": qid, "q": q, "finding": finding,
                "type": "positive",
                "component": _derive_implicated_component(qid, q),
            })

    # Score by COMPONENT (not by raw domain category)
    component_scores: dict[str, float] = defaultdict(float)
    component_evidence: dict[str, list[dict]] = defaultdict(list)
    component_contradictions: dict[str, list[dict]] = defaultdict(list)

    for item in evidence_items:
        if item["type"] == "negative":
            comp = item["component"]
            component_scores[comp] += item["weight"]
            component_evidence[comp].append(item)
        elif item["type"] == "positive":
            comp = item["component"]
            component_contradictions[comp].append(item)

    # Sort by score
    ranked = sorted(component_scores.items(), key=lambda x: -x[1])

    # Collect inconclusive items
    inconclusive = [i for i in evidence_items if i["type"] == "inconclusive"]

    # ─── OUTPUT ───────────────────────────────────────────────────────────────
    # Status counts
    total = len(QUESTION_BANK)
    n_complete = sum(1 for i in evidence_items if i["type"] in ("negative", "positive"))
    n_inconclusive = len(inconclusive)
    n_blocked = sum(1 for q in QUESTION_BANK if q.status == QuestionStatus.BLOCKED)
    n_not_run = total - len(findings) - n_blocked

    print("CURRENT RESEARCH PICTURE")
    print("=" * 60)
    print(f"\n  Overall:")
    print(f"    {n_complete} COMPLETE")
    print(f"    {n_inconclusive} INCONCLUSIVE")
    print(f"    {n_blocked} BLOCKED")
    print(f"    {n_not_run} NOT RUN")

    # Primary bottleneck
    print(f"\n{'─'*60}")
    if not ranked or ranked[0][1] == 0:
        print("  PRIMARY BOTTLENECK: NONE IDENTIFIED")
        print("  All research domains show neutral or positive evidence.")
        return

    primary_comp, primary_score = ranked[0]
    primary_items = component_evidence[primary_comp]
    contradictions = component_contradictions.get(primary_comp, [])

    print(f"  PRIMARY BOTTLENECK")
    print(f"    Component: {primary_comp.replace('_', ' ').title()}")
    print(f"    Evidence score: {primary_score:.0f}")
    print()

    # Why
    print(f"  WHY")
    domains = set()
    for item in primary_items:
        domains.update(u.value for u in item["q"].required_universes)
    print(f"    {len(primary_items)} research questions show negative evidence")
    print(f"    Domains involved: {', '.join(sorted(domains))}")
    print()

    # Supporting questions
    print(f"  SUPPORTING QUESTIONS")
    for item in primary_items:
        print(f"    {item['qid']:<10} — {item['q'].title}")
        print(f"      Result: {item['finding'].get('outcome')} ({item['finding'].get('confidence')})")
        print(f"      Domain: {_question_domain_label(item['q'])}")
    print()

    # Contradictory
    if contradictions:
        print(f"  CONTRADICTORY EVIDENCE")
        for item in contradictions:
            print(f"    {item['qid']}: {item['finding'].get('outcome')} ({item['finding'].get('confidence')})")
        print()

    # Unresolved
    related_inconclusive = [
        i for i in inconclusive
        if _is_related_to_component(i["q"], primary_comp, primary_items)
    ]
    if related_inconclusive:
        print(f"  UNRESOLVED QUESTIONS (related to bottleneck)")
        for item in related_inconclusive:
            print(f"    {item['qid']} — {item['q'].title}")
        print()

    # Actionability
    if primary_score >= 6 and not contradictions:
        actionability = "MEDIUM"
    elif primary_score >= 9:
        actionability = "HIGH"
    elif primary_score >= 3:
        actionability = "LOW"
    else:
        actionability = "INSUFFICIENT"

    uncertainty = "HIGH" if related_inconclusive else "MEDIUM" if contradictions else "LOW"

    print(f"  ACTIONABILITY: {actionability}")
    print(f"  UNCERTAINTY: {uncertainty}")

    # Product impact
    print(f"\n  PRODUCT IMPACT")
    print(f"    {_component_product_impact(primary_comp)}")

    # Secondary components
    if len(ranked) > 1:
        print(f"\n  SECONDARY COMPONENTS")
        for comp, score in ranked[1:3]:
            if score > 0:
                items = component_evidence[comp]
                print(f"    {comp.replace('_', ' ').title()} (score: {score:.0f})")
                print(f"      Evidence: {', '.join(i['qid'] for i in items[:3])}")

    # Next investigation
    print(f"\n{'─'*60}")
    print(f"  NEXT BEST INVESTIGATION")
    next_qs = _derive_next_from_bottleneck(primary_comp, primary_items, inconclusive, findings)
    for i, (qid, reason) in enumerate(next_qs[:5], 1):
        q = get_question(qid)
        print(f"    {i}. {qid} — {q.title if q else '?'}")
        print(f"       Why: {reason}")
    print()

    # Expected decision from investigation
    if next_qs:
        print(f"  EXPECTED DECISION")
        print(f"    If evidence confirms {primary_comp.replace('_', ' ')} is the problem:")
        print(f"      → Candidate optimisation in {primary_comp.replace('_', ' ')}")
        print(f"    If evidence does NOT confirm:")
        if len(ranked) > 1:
            print(f"      → Investigate {ranked[1][0].replace('_', ' ')} next")
        else:
            print(f"      → Gather more data before acting")
    print()

    # Optimisation status
    print(f"  OPTIMISATION STATUS")
    if actionability in ("HIGH",) and uncertainty == "LOW":
        print(f"    READY FOR CANDIDATE OPTIMISATION")
        print(f"    Area: {primary_comp.replace('_', ' ')}")
    else:
        print(f"    DO NOT CHANGE YET")
        if related_inconclusive:
            print(f"    Reason: {len(related_inconclusive)} unresolved related questions")
        elif contradictions:
            print(f"    Reason: contradictory evidence exists")
        else:
            print(f"    Reason: evidence strength insufficient")


# ═══════════════════════════════════════════════════════════════════════════════
# NEXT INVESTIGATION (bottleneck-driven)
# ═══════════════════════════════════════════════════════════════════════════════


def recommend_next():
    """Recommend next research investigation — driven by the current bottleneck."""
    findings = _load_all_findings()

    # First determine the bottleneck
    evidence_items = []
    component_scores: dict[str, float] = defaultdict(float)
    component_evidence: dict[str, list[dict]] = defaultdict(list)
    inconclusive_items: list[dict] = []

    for qid, finding in findings.items():
        q = get_question(qid)
        if q is None:
            continue
        outcome = finding.get("outcome", "")
        confidence = finding.get("confidence", "")

        if confidence == "INSUFFICIENT" or outcome == "INCONCLUSIVE":
            inconclusive_items.append({"qid": qid, "q": q, "finding": finding})
            continue

        if outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
            weight = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(confidence, 0)
            comp = _derive_implicated_component(qid, q)
            component_scores[comp] += weight
            component_evidence[comp].append({"qid": qid, "q": q, "finding": finding})

    ranked = sorted(component_scores.items(), key=lambda x: -x[1])
    primary_comp = ranked[0][0] if ranked else ""
    primary_items = component_evidence.get(primary_comp, [])

    print("NEXT RESEARCH")
    print("=" * 60)

    if not primary_comp:
        print("  No bottleneck detected. Run targeted questions to build evidence.")
        return

    print(f"\n  CURRENT BOTTLENECK: {primary_comp.replace('_', ' ').title()}")
    print(f"  Evidence strength: {component_scores.get(primary_comp, 0):.0f}")
    print(f"  Product impact: {_component_product_impact(primary_comp)[:80]}")
    print()

    # Derive recommended questions
    next_qs = _derive_next_from_bottleneck(primary_comp, primary_items, inconclusive_items, findings)

    if not next_qs:
        print("  No further investigation needed — evidence is resolved.")
        return

    print("  QUESTIONS TO RUN")
    for i, (qid, reason) in enumerate(next_qs[:7], 1):
        q = get_question(qid)
        print(f"    {i}. {qid} — {q.title if q else '?'}")
        print(f"       Why: {reason}")
        print()

    # Expected decision
    print("  EXPECTED DECISION")
    print(f"    If these questions produce clear evidence:")
    print(f"      → Create candidate optimisation for {primary_comp.replace('_', ' ')}")
    print(f"    If evidence remains inconclusive:")
    print(f"      → Gather more trading data, then rerun")
    if len(ranked) > 1:
        print(f"    If evidence points elsewhere:")
        print(f"      → Investigate {ranked[1][0].replace('_', ' ')} next")


def _derive_next_from_bottleneck(
    primary_comp: str,
    primary_items: list[dict],
    inconclusive_items: list[dict],
    findings: dict,
) -> list[tuple[str, str]]:
    """Derive next-investigation priorities from the current bottleneck."""
    recommendations: list[tuple[int, str, str]] = []  # (priority, qid, reason)

    # 1. Inconclusive questions DIRECTLY related to the bottleneck
    for item in inconclusive_items:
        if _is_related_to_component(item["q"], primary_comp, primary_items):
            recommendations.append((
                10, item["qid"],
                f"Directly related to '{primary_comp}' bottleneck — resolves uncertainty"
            ))

    # 2. Questions that distinguish competing explanations
    # (other inconclusive questions sharing universes with bottleneck questions)
    bottleneck_universes = set()
    for item in primary_items:
        bottleneck_universes.update(u.value for u in item["q"].required_universes)

    for item in inconclusive_items:
        qid = item["qid"]
        if any((10, qid, _) in recommendations for _ in [""]):
            continue  # Already added
        if qid in [r[1] for r in recommendations]:
            continue
        q_universes = set(u.value for u in item["q"].required_universes)
        if q_universes & bottleneck_universes:
            recommendations.append((
                7, item["qid"],
                f"Shares domain ({', '.join(q_universes & bottleneck_universes)}) with bottleneck"
            ))

    # 3. Questions that validate the bottleneck's strongest evidence
    for item in primary_items:
        qid = item["qid"]
        confidence = item["finding"].get("confidence", "")
        if confidence == "MEDIUM":
            recommendations.append((
                5, qid,
                f"Confirm negative finding with more data (currently MEDIUM confidence)"
            ))

    # 4. Never-run questions related to the bottleneck
    for q in QUESTION_BANK:
        if q.question_id not in findings and q.status != QuestionStatus.BLOCKED:
            if _is_related_to_component(q, primary_comp, primary_items):
                recommendations.append((
                    3, q.question_id,
                    f"Never executed — may provide additional bottleneck evidence"
                ))

    # Deduplicate and sort by priority
    seen = set()
    unique = []
    for prio, qid, reason in sorted(recommendations, key=lambda x: -x[0]):
        if qid not in seen:
            seen.add(qid)
            unique.append((qid, reason))

    return unique


def _is_related_to_component(q, primary_comp: str, primary_items: list[dict]) -> bool:
    """Determine if a question is related to the implicated component."""
    # Check if the question's own component matches
    q_comp = _derive_implicated_component(q.question_id, q)
    if q_comp == primary_comp:
        return True

    # Check if it shares universes with bottleneck questions
    bottleneck_universes = set()
    for item in primary_items:
        bottleneck_universes.update(u.value for u in item["q"].required_universes)

    q_universes = set(u.value for u in q.required_universes)
    # Must share at least one universe AND the question tests something related
    if q_universes & bottleneck_universes:
        # Additional check: does the question's intent relate?
        intent = q.research_intent.lower()
        comp_terms = primary_comp.replace("_", " ").split()
        if any(term in intent for term in comp_terms):
            return True

    return False


def _load_all_findings() -> dict[str, dict]:
    """Load all latest.json findings."""
    findings = {}
    if not _QUESTIONS_DIR.exists():
        return findings
    for q in QUESTION_BANK:
        path = _QUESTIONS_DIR / q.question_id / "latest.json"
        if path.exists():
            try:
                findings[q.question_id] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return findings
