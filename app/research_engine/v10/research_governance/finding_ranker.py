"""
Research Governance — Finding Ranker.

Prioritises research findings and tracks multiple comparison exposure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.research_governance.models import ResearchFinding


class FindingRanker:
    """
    Ranks research findings by actionability and evidence strength.

    Priority score = confidence_score * effect_weight * sample_weight
    """

    def rank(self, findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """
        Rank findings by priority (highest first).

        Assigns priority level and score to each finding.
        """
        for f in findings:
            score = self._compute_priority(f)
            f.priority_score = score
            if score >= 0.6:
                f.priority = "HIGH"
            elif score >= 0.35:
                f.priority = "MEDIUM"
            else:
                f.priority = "LOW"

        return sorted(findings, key=lambda f: f.priority_score, reverse=True)

    def _compute_priority(self, finding: ResearchFinding) -> float:
        """Compute priority score from finding attributes."""
        # Base = confidence score
        base = finding.confidence_score

        # Boost for clear conclusions
        if finding.status in ("SUPPORTED", "REJECTED"):
            base *= 1.2

        # Boost for larger effect
        if abs(finding.result_value) >= 0.3:
            base *= 1.1

        # Penalty for INCONCLUSIVE
        if finding.status == "INCONCLUSIVE":
            base *= 0.6

        # Penalty for insufficient sample
        if finding.sample_status == "INSUFFICIENT":
            base *= 0.3

        return round(min(base, 1.0), 3)


def rank_findings(findings: list[ResearchFinding]) -> list[ResearchFinding]:
    """Convenience function to rank findings."""
    return FindingRanker().rank(findings)


# ═══════════════════════════════════════════════════════════════
# MULTIPLE COMPARISON TRACKER
# ═══════════════════════════════════════════════════════════════

class MultipleComparisonTracker:
    """
    Tracks research exposure to guard against false discoveries.

    When many comparisons are made, the probability of spurious
    findings increases. This tracker provides awareness, not blocking.
    """

    def __init__(self):
        self._questions_tested: set[str] = set()
        self._segments_tested: set[str] = set()
        self._total_comparisons: int = 0

    def record(self, question_id: str, filters: dict[str, str]) -> None:
        """Record a comparison."""
        self._questions_tested.add(question_id)
        for k, v in filters.items():
            self._segments_tested.add(f"{k}={v}")
        self._total_comparisons += 1

    @property
    def exposure(self) -> dict[str, int]:
        return {
            "questions_tested": len(self._questions_tested),
            "segments_tested": len(self._segments_tested),
            "total_comparisons": self._total_comparisons,
        }

    def risk_level(self) -> str:
        """Assess multiple comparison risk."""
        if self._total_comparisons > 100:
            return "HIGH"
        elif self._total_comparisons > 30:
            return "MEDIUM"
        return "LOW"

    def generate_warning(self) -> str | None:
        """Generate warning message if exposure is high."""
        if self._total_comparisons > 50:
            return (
                f"Multiple comparison warning: {self._total_comparisons} comparisons made "
                f"across {len(self._questions_tested)} questions and "
                f"{len(self._segments_tested)} segments. "
                f"Some findings may be spurious."
            )
        return None


# ═══════════════════════════════════════════════════════════════
# GOVERNANCE REPORT
# ═══════════════════════════════════════════════════════════════

def generate_governance_report(
    findings: list[ResearchFinding],
    comparison_tracker: MultipleComparisonTracker | None = None,
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """Generate governance report from validated findings."""
    rep_dir = Path(reports_dir or "reports/research/governance")
    rep_dir.mkdir(parents=True, exist_ok=True)

    supported = [f for f in findings if f.status == "SUPPORTED"]
    rejected = [f for f in findings if f.status == "REJECTED"]
    inconclusive = [f for f in findings if f.status == "INCONCLUSIVE"]

    conf_dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        conf_dist[f.confidence_level] = conf_dist.get(f.confidence_level, 0) + 1

    report = {
        "generated_utc": timestamp_now(),
        "total_findings": len(findings),
        "supported": len(supported),
        "rejected": len(rejected),
        "inconclusive": len(inconclusive),
        "confidence_distribution": conf_dist,
        "comparison_exposure": comparison_tracker.exposure if comparison_tracker else {},
        "comparison_risk": comparison_tracker.risk_level() if comparison_tracker else "UNKNOWN",
        "comparison_warning": comparison_tracker.generate_warning() if comparison_tracker else None,
        "top_findings": [f.to_dict() for f in findings[:10]],
    }

    (rep_dir / "research_confidence_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "research_confidence_report.md").write_text(
        _build_md(report, findings), encoding="utf-8"
    )
    return report


def _build_md(report: dict, findings: list[ResearchFinding]) -> str:
    md = []
    md.append("# V10 Research Governance Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append(f"| Status | Count |")
    md.append(f"|---|---|")
    md.append(f"| Supported | {report['supported']} |")
    md.append(f"| Rejected | {report['rejected']} |")
    md.append(f"| Inconclusive | {report['inconclusive']} |")
    md.append(f"| **Total** | **{report['total_findings']}** |")
    md.append("")

    md.append("## Confidence Distribution")
    md.append("")
    for level, count in report["confidence_distribution"].items():
        md.append(f"- {level}: {count}")
    md.append("")

    if report.get("comparison_warning"):
        md.append("## Multiple Comparison Warning")
        md.append("")
        md.append(f"> {report['comparison_warning']}")
        md.append("")

    if findings:
        md.append("## Top Findings")
        md.append("")
        md.append("| # | Question | Status | Confidence | Effect | Sample | Priority |")
        md.append("|---|---|---|---|---|---|---|")
        for i, f in enumerate(findings[:10], 1):
            md.append(
                f"| {i} | {f.question_id}: {f.question_name[:25]} | {f.status} | "
                f"{f.confidence_level} | {f.result_value:+.3f} | {f.sample_size} | {f.priority} |"
            )

    md.append("")
    md.append("---")
    return "\n".join(md)
