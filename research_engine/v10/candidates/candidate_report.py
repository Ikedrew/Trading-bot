"""
Candidate Registry — Reporting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.v10.candidates.candidate_lifecycle import is_active

_REPORTS_DIR = "reports/research/candidates"


def generate_candidate_dashboard(
    candidates: list[CandidateRecord],
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """Generate candidate registry dashboard report."""
    rep_dir = Path(reports_dir or _REPORTS_DIR)
    rep_dir.mkdir(parents=True, exist_ok=True)

    active = [c for c in candidates if is_active(c.status)]
    accepted = [c for c in candidates if c.status == CandidateStatus.ACCEPTED]
    rejected = [c for c in candidates if c.status == CandidateStatus.REJECTED]

    report = {
        "generated_utc": timestamp_now(),
        "total_candidates": len(candidates),
        "active": len(active),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "archived": sum(1 for c in candidates if c.status == CandidateStatus.ARCHIVED),
        "by_status": {},
        "candidates": [_candidate_summary(c) for c in candidates],
    }

    for c in candidates:
        report["by_status"][c.status] = report["by_status"].get(c.status, 0) + 1

    (rep_dir / "candidate_registry_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "candidate_registry_report.md").write_text(
        _build_md(report, candidates), encoding="utf-8"
    )
    return report


def _candidate_summary(c: CandidateRecord) -> dict[str, Any]:
    last_val = c.validation_history[-1] if c.validation_history else None
    return {
        "candidate_id": c.candidate_id,
        "component": c.component,
        "status": c.status,
        "risk_level": c.risk_level,
        "validations": len(c.validation_history),
        "last_decision": last_val.decision if last_val else "",
        "last_confidence": last_val.confidence if last_val else "",
        "created_from": c.created_from_question,
    }


def _build_md(report: dict, candidates: list[CandidateRecord]) -> str:
    md = []
    md.append("# Candidate Registry Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append("")
    md.append(f"| Status | Count |")
    md.append(f"|---|---|")
    for status, count in sorted(report["by_status"].items()):
        md.append(f"| {status} | {count} |")
    md.append(f"| **Total** | **{report['total_candidates']}** |")
    md.append("")

    if candidates:
        md.append("## Candidates")
        md.append("")
        md.append("| ID | Component | Status | Risk | Validations | Last Decision |")
        md.append("|---|---|---|---|---|---|")
        for c in candidates:
            last = c.validation_history[-1].decision if c.validation_history else "-"
            md.append(f"| {c.candidate_id} | {c.component} | {c.status} | "
                      f"{c.risk_level} | {len(c.validation_history)} | {last} |")

    md.append("\n---")
    return "\n".join(md)
