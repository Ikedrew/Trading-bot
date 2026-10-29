"""
Shadow Optimisation — Dashboard report generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.shadow.models import ShadowCandidate
from research_engine.v10.shadow.shadow_comparison import compute_shadow_metrics, evaluate_shadow_evidence
from research_engine.v10.shadow.shadow_registry import ShadowRegistry

_REPORTS_DIR = "reports/research/shadow"


def generate_shadow_dashboard(
    registry: ShadowRegistry,
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """Generate the shadow optimisation dashboard."""
    rep_dir = Path(reports_dir or _REPORTS_DIR)
    rep_dir.mkdir(parents=True, exist_ok=True)

    candidates = registry.list_all()
    entries = []

    for c in candidates:
        comparisons = registry.get_comparisons(c.shadow_id)
        evidence = evaluate_shadow_evidence(comparisons)
        metrics = evidence.get("metrics", {})

        entries.append({
            "shadow_id": c.shadow_id,
            "candidate_id": c.candidate_id,
            "baseline_id": c.baseline_id,
            "status": c.status,
            "observations": c.metrics.get("opportunities_seen", 0),
            "comparisons": len(comparisons),
            "evidence_maturity": evidence.get("maturity", ""),
            "confidence": evidence.get("confidence", ""),
            "decision": evidence.get("decision", ""),
            "next_step": evidence.get("next_step", ""),
            "baseline_expectancy": metrics.get("baseline", {}).get("expectancy_r", 0),
            "shadow_expectancy": metrics.get("shadow", {}).get("expectancy_r", 0),
            "delta_expectancy": metrics.get("delta", {}).get("expectancy_r", 0),
        })

    report = {
        "generated_utc": timestamp_now(),
        "total_shadows": len(candidates),
        "active": sum(1 for c in candidates if c.status == "ACTIVE"),
        "entries": entries,
    }

    (rep_dir / "shadow_dashboard.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "shadow_dashboard.md").write_text(_build_md(report), encoding="utf-8")
    return report


def _build_md(report: dict) -> str:
    md = []
    md.append("# Shadow Optimisation Dashboard")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Active shadows: {report['active']}/{report['total_shadows']}")
    md.append("")

    for e in report["entries"]:
        md.append(f"## {e['candidate_id']}")
        md.append("")
        md.append(f"- Shadow ID: {e['shadow_id']}")
        md.append(f"- Status: {e['status']}")
        md.append(f"- Comparisons: {e['comparisons']}")
        md.append(f"- Evidence: {e['evidence_maturity']}")
        md.append(f"- Confidence: {e['confidence']}")
        md.append(f"- Decision: {e['decision']}")
        md.append(f"- Baseline exp: {e['baseline_expectancy']:+.4f}R")
        md.append(f"- Shadow exp: {e['shadow_expectancy']:+.4f}R")
        md.append(f"- Delta: {e['delta_expectancy']:+.4f}R")
        md.append(f"- Next: {e['next_step']}")
        md.append("")

    md.append("---")
    return "\n".join(md)
