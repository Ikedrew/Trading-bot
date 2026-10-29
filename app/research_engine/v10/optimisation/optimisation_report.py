"""
Optimisation Bridge — Report generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.optimisation.models import ResearchHypothesis, OptimisationCandidate, ValidationPlan

_REPORTS_DIR = "reports/research/optimisation"


def save_optimisation_report(
    hypotheses: list[ResearchHypothesis],
    candidates: list[OptimisationCandidate],
    plans: list[ValidationPlan] | None = None,
    reports_dir: str | None = None,
) -> dict[str, str]:
    """Save optimisation state as JSON + MD reports."""
    rep_dir = Path(reports_dir or _REPORTS_DIR)
    rep_dir.mkdir(parents=True, exist_ok=True)

    # Hypothesis report
    hyp_data = {"generated_utc": timestamp_now(), "hypotheses": [h.to_dict() for h in hypotheses]}
    hyp_path = rep_dir / "hypothesis_report.json"
    hyp_path.write_text(json.dumps(hyp_data, indent=2, default=str), encoding="utf-8")

    # Candidate report
    cand_data = {"generated_utc": timestamp_now(), "candidates": [c.to_dict() for c in candidates]}
    cand_path = rep_dir / "candidate_report.json"
    cand_path.write_text(json.dumps(cand_data, indent=2, default=str), encoding="utf-8")

    # Markdown summary
    md_path = rep_dir / "optimisation_summary.md"
    md_path.write_text(_build_md(hypotheses, candidates, plans or []), encoding="utf-8")

    return {"hypotheses": str(hyp_path), "candidates": str(cand_path), "summary": str(md_path)}


def _build_md(
    hypotheses: list[ResearchHypothesis],
    candidates: list[OptimisationCandidate],
    plans: list[ValidationPlan],
) -> str:
    md = []
    md.append("# V10 Optimisation Bridge Summary")
    md.append("")
    md.append(f"Generated: {timestamp_now()}")
    md.append("")

    md.append("## Hypotheses")
    md.append("")
    if hypotheses:
        md.append("| ID | Source | Status | Component | Confidence |")
        md.append("|---|---|---|---|---|")
        for h in hypotheses:
            md.append(f"| {h.hypothesis_id} | {h.source_question} | {h.status} | "
                      f"{h.target_component} | {h.confidence} |")
    else:
        md.append("No hypotheses generated.")
    md.append("")

    md.append("## Candidates")
    md.append("")
    if candidates:
        md.append("| ID | Baseline | Component | Risk | Status |")
        md.append("|---|---|---|---|---|")
        for c in candidates:
            md.append(f"| {c.candidate_id} | {c.baseline_id} | {c.component} | "
                      f"{c.risk_level} | {c.status} |")
    else:
        md.append("No candidates proposed.")
    md.append("")
    md.append("---")
    return "\n".join(md)
