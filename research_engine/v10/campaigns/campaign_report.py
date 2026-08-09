"""
Campaign Engine — Report generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.campaigns.models import CampaignResult

_REPORTS_DIR = "reports/research/campaigns"


def save_campaign_report(
    result: CampaignResult,
    reports_dir: str | None = None,
) -> dict[str, str]:
    """
    Save campaign result as JSON + Markdown report.

    Returns:
        {"json": path, "md": path}
    """
    rep_dir = Path(reports_dir or _REPORTS_DIR) / result.campaign_id
    rep_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = rep_dir / "summary.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")

    # Findings JSON
    findings_path = rep_dir / "findings.json"
    findings_path.write_text(
        json.dumps([f.to_dict() for f in result.findings], indent=2, default=str),
        encoding="utf-8",
    )

    # Markdown
    md_path = rep_dir / "report.md"
    md_path.write_text(_build_md(result), encoding="utf-8")

    return {"json": str(json_path), "md": str(md_path), "findings": str(findings_path)}


def _build_md(result: CampaignResult) -> str:
    md = []
    md.append(f"# {result.campaign_name}")
    md.append("")
    md.append(f"**Objective:** {result.objective}")
    md.append(f"**Timestamp:** {result.timestamp}")
    md.append(f"**Duration:** {result.execution_time_seconds:.1f}s")
    if result.filters_applied:
        md.append(f"**Filters:** {result.filters_applied}")
    md.append("")

    md.append("## Summary")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Questions executed | {result.questions_executed} |")
    md.append(f"| Questions failed | {result.questions_failed} |")
    md.append(f"| Total findings | {len(result.findings)} |")
    md.append(f"| High priority | {len(result.high_priority_findings)} |")
    md.append("")

    if result.recommendations:
        md.append("## Recommendations")
        md.append("")
        for i, rec in enumerate(result.recommendations, 1):
            md.append(f"{i}. {rec}")
        md.append("")

    if result.findings:
        md.append("## Findings (by priority)")
        md.append("")
        md.append("| # | Question | Domain | Confidence | Maturity | Decision | Effect |")
        md.append("|---|---|---|---|---|---|---|")
        for i, f in enumerate(result.findings[:15], 1):
            md.append(
                f"| {i} | {f.question_id}: {f.question_name[:20]} | {f.domain} | "
                f"{f.confidence} | {f.evidence_maturity} | {f.decision_status} | "
                f"{f.result_value:+.3f} |"
            )
        md.append("")

    if result.data_gaps:
        md.append("## Data Gaps")
        md.append("")
        for gap in result.data_gaps:
            md.append(f"- {gap}")
        md.append("")

    md.append("---")
    return "\n".join(md)
