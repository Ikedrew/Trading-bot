"""
Research Intelligence — Report Builder.

Generates structured reports from experiment results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.research_intelligence.models import ExperimentResult


def build_summary_report(
    results: list[ExperimentResult],
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """
    Build a summary report from multiple experiment results.

    Creates:
        reports/research/questions/research_summary.json
        reports/research/questions/research_summary.md
    """
    rep_dir = Path(reports_dir or "reports/research/questions")
    rep_dir.mkdir(parents=True, exist_ok=True)

    supported = [r for r in results if r.recommendation == "SUPPORTED"]
    rejected = [r for r in results if r.recommendation == "REJECTED"]
    inconclusive = [r for r in results if r.recommendation == "INCONCLUSIVE"]
    errors = [r for r in results if r.error]

    report = {
        "generated_utc": timestamp_now(),
        "total_questions": len(results),
        "supported": len(supported),
        "rejected": len(rejected),
        "inconclusive": len(inconclusive),
        "errors": len(errors),
        "results": [r.to_dict() for r in results],
    }

    (rep_dir / "research_summary.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "research_summary.md").write_text(
        _build_markdown(report, results), encoding="utf-8"
    )

    return report


def _build_markdown(report: dict, results: list[ExperimentResult]) -> str:
    md = []
    md.append("# V10 Research Intelligence Summary")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Questions executed: {report['total_questions']}")
    md.append("")
    md.append(f"| Outcome | Count |")
    md.append(f"|---|---|")
    md.append(f"| Supported | {report['supported']} |")
    md.append(f"| Rejected | {report['rejected']} |")
    md.append(f"| Inconclusive | {report['inconclusive']} |")
    md.append(f"| Errors | {report['errors']} |")
    md.append("")

    md.append("## Results")
    md.append("")
    md.append("| ID | Name | N | Confidence | Recommendation |")
    md.append("|---|---|---|---|---|")
    for r in results:
        md.append(f"| {r.question_id} | {r.question_name} | {r.sample_size} | "
                  f"{r.confidence} | {r.recommendation} |")

    if results:
        md.append("")
        md.append("## Detail")
        md.append("")
        for r in results:
            if r.error:
                md.append(f"### {r.question_id}: {r.question_name} [ERROR]")
                md.append(f"  Error: {r.error}")
            else:
                md.append(f"### {r.question_id}: {r.question_name} [{r.recommendation}]")
                md.append(f"  Sample: {r.sample_size} | Confidence: {r.confidence}")
                if r.limitations:
                    md.append(f"  Limitations: {', '.join(r.limitations)}")
            md.append("")

    md.append("---")
    return "\n".join(md)
