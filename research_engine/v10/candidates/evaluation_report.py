"""
Candidate Evaluation Dashboard.

Provides a complete view of all optimisation candidates:
    - What exists and why
    - Evidence and validation results
    - Priority ranking
    - Health status
    - Recommended next actions

Usage:
    from research_engine.v10.candidates import CandidateEvaluationReport

    report = CandidateEvaluationReport()
    result = report.generate()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.v10.candidates.candidate_lifecycle import is_active
from research_engine.v10.candidates.candidate_registry import CandidateRegistry

_REPORTS_DIR = "reports/research"


# ═══════════════════════════════════════════════════════════════
# HEALTH STATUS
# ═══════════════════════════════════════════════════════════════

def _assess_health(candidate: CandidateRecord) -> str:
    """Determine candidate health status."""
    if candidate.status in (CandidateStatus.REJECTED, CandidateStatus.FAILED_VALIDATION):
        return "FAILED"
    if candidate.status == CandidateStatus.REGRESSION_DETECTED:
        return "FAILED"
    if candidate.status == CandidateStatus.READY_FOR_REVIEW:
        return "READY_FOR_REVIEW"
    if candidate.status == CandidateStatus.SHADOW_TESTING:
        return "VALIDATING"
    if candidate.status == CandidateStatus.VALIDATING:
        return "VALIDATING"
    if candidate.status == CandidateStatus.VALIDATED:
        return "HEALTHY"
    if candidate.status == CandidateStatus.ACCEPTED:
        return "HEALTHY"
    if candidate.status == CandidateStatus.PROPOSED:
        if not candidate.baseline_id:
            return "BLOCKED"
        return "WAITING_DATA"
    return "HEALTHY"


# ═══════════════════════════════════════════════════════════════
# PRIORITY SCORING
# ═══════════════════════════════════════════════════════════════

def _compute_priority(candidate: CandidateRecord) -> tuple[str, float, str]:
    """Compute priority level, score, and reason."""
    score = 0.0
    reasons = []

    # Evidence from validation history
    if candidate.validation_history:
        last = candidate.validation_history[-1]
        if last.decision == "IMPROVED":
            score += 0.4
            reasons.append("Validation showed improvement")
        if last.confidence == "HIGH":
            score += 0.2
        elif last.confidence == "MEDIUM":
            score += 0.1
        if last.sample_size >= 30:
            score += 0.1
        if last.expectancy_delta > 0.1:
            score += 0.15
            reasons.append(f"Strong effect (+{last.expectancy_delta:.2f}R)")

    # Risk level
    if candidate.risk_level == "LOW":
        score += 0.1
    elif candidate.risk_level == "HIGH":
        score -= 0.1

    # Status progression
    if candidate.status in (CandidateStatus.VALIDATED, CandidateStatus.READY_FOR_REVIEW):
        score += 0.15
        reasons.append("Advanced lifecycle stage")

    # Clamp
    score = min(max(score, 0.0), 1.0)

    if score >= 0.5:
        level = "HIGH"
    elif score >= 0.25:
        level = "MEDIUM"
    else:
        level = "LOW"

    reason = "; ".join(reasons) if reasons else "Awaiting evidence"
    return level, round(score, 3), reason


# ═══════════════════════════════════════════════════════════════
# NEXT ACTION
# ═══════════════════════════════════════════════════════════════

def _recommend_next_action(candidate: CandidateRecord) -> str:
    """Generate next action recommendation."""
    if candidate.status == CandidateStatus.PROPOSED:
        if not candidate.baseline_id:
            return "Create baseline snapshot before validation."
        return "Run historical validation against baseline."
    if candidate.status == CandidateStatus.VALIDATING:
        return "Awaiting validation completion."
    if candidate.status == CandidateStatus.VALIDATED:
        return "Consider shadow testing or proceed to review."
    if candidate.status == CandidateStatus.SHADOW_TESTING:
        return "Collect forward evidence from shadow execution."
    if candidate.status == CandidateStatus.READY_FOR_REVIEW:
        return "Human review required. Evaluate evidence and decide."
    if candidate.status == CandidateStatus.FAILED_VALIDATION:
        return "Investigate failure. Consider parameter adjustment or rejection."
    if candidate.status == CandidateStatus.REGRESSION_DETECTED:
        return "Regression detected. Investigate trade-offs or reject."
    if candidate.status == CandidateStatus.REJECTED:
        return "Rejected. Archive or investigate alternative approach."
    if candidate.status == CandidateStatus.ACCEPTED:
        return "Accepted. Monitor post-deployment performance."
    return "Review candidate status."


# ═══════════════════════════════════════════════════════════════
# EVALUATION REPORT
# ═══════════════════════════════════════════════════════════════

class CandidateEvaluationReport:
    """Generates the optimisation evaluation dashboard."""

    def __init__(self, registry: CandidateRegistry | None = None, reports_dir: str | None = None):
        self._registry = registry or CandidateRegistry()
        self._reports_dir = Path(reports_dir or _REPORTS_DIR)

    def generate(self) -> dict[str, Any]:
        """Generate the full evaluation report."""
        candidates = self._registry.list_all()

        # Counts by status
        counts = {}
        for c in candidates:
            counts[c.status] = counts.get(c.status, 0) + 1

        # Build candidate evaluations
        evaluations = []
        for c in candidates:
            health = _assess_health(c)
            priority_level, priority_score, priority_reason = _compute_priority(c)
            next_action = _recommend_next_action(c)

            evaluations.append({
                "candidate_id": c.candidate_id,
                "component": c.component,
                "status": c.status,
                "health": health,
                "priority": priority_level,
                "priority_score": priority_score,
                "priority_reason": priority_reason,
                "next_action": next_action,
                "risk_level": c.risk_level,
                "created_from_question": c.created_from_question,
                "created_from_campaign": c.created_from_campaign,
                "baseline_id": c.baseline_id,
                "change_definition": c.change_definition,
                "validations_count": len(c.validation_history),
                "last_validation": c.validation_history[-1].to_dict() if c.validation_history else None,
                "created_at": c.created_at,
            })

        # Sort by priority score descending
        evaluations.sort(key=lambda e: e["priority_score"], reverse=True)

        # Recommended actions (top actionable items)
        actions = []
        for e in evaluations:
            if e["health"] in ("WAITING_DATA", "HEALTHY") and e["status"] == CandidateStatus.PROPOSED:
                actions.append({"candidate": e["candidate_id"], "action": "Run validation", "reason": e["priority_reason"]})
            elif e["status"] == CandidateStatus.READY_FOR_REVIEW:
                actions.append({"candidate": e["candidate_id"], "action": "Human review", "reason": "Evidence complete"})
            elif e["status"] == CandidateStatus.VALIDATED:
                actions.append({"candidate": e["candidate_id"], "action": "Proceed to shadow testing or review", "reason": e["priority_reason"]})

        report = {
            "generated_utc": timestamp_now(),
            "total_candidates": len(candidates),
            "active_candidates": sum(1 for c in candidates if is_active(c.status)),
            "counts_by_status": counts,
            "candidates": evaluations,
            "recommended_actions": actions,
        }

        # Persist
        self._save(report)
        return report

    def _save(self, report: dict[str, Any]) -> None:
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        (self._reports_dir / "candidate_evaluation_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        (self._reports_dir / "candidate_evaluation_report.md").write_text(
            self._build_md(report), encoding="utf-8"
        )

    def _build_md(self, report: dict) -> str:
        md = []
        md.append("# Candidate Evaluation Dashboard")
        md.append("")
        md.append(f"Generated: {report['generated_utc']}")
        md.append(f"Total candidates: {report['total_candidates']}")
        md.append(f"Active: {report['active_candidates']}")
        md.append("")

        # Counts
        md.append("## Status Summary")
        md.append("")
        md.append("| Status | Count |")
        md.append("|---|---|")
        for status, count in sorted(report["counts_by_status"].items()):
            md.append(f"| {status} | {count} |")
        md.append("")

        # Candidates by priority
        md.append("## Candidates (by priority)")
        md.append("")
        md.append("| # | Candidate | Component | Status | Health | Priority | Next Action |")
        md.append("|---|---|---|---|---|---|---|")
        for i, e in enumerate(report["candidates"][:15], 1):
            md.append(
                f"| {i} | {e['candidate_id'][:25]} | {e['component'][:15]} | "
                f"{e['status']} | {e['health']} | {e['priority']} | {e['next_action'][:35]} |"
            )
        md.append("")

        # Recommended actions
        if report["recommended_actions"]:
            md.append("## Recommended Actions")
            md.append("")
            for a in report["recommended_actions"][:5]:
                md.append(f"- **{a['action']}**: {a['candidate']} — {a['reason']}")
            md.append("")

        md.append("---")
        return "\n".join(md)
