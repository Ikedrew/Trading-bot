"""
Research Operations — Top-level operational report.

Answers: What did we test? What did we find? What should I do next?
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

_REPORTS_DIR = "reports/research"


def generate_operational_report(
    universe_file: str | None = None,
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """Generate the top-level V10 research operational report."""
    rep_dir = Path(reports_dir or _REPORTS_DIR)
    rep_dir.mkdir(parents=True, exist_ok=True)

    # Gather data from existing components
    universe_info = _get_universe_info(universe_file)
    campaign_info = _get_campaign_info()
    candidate_info = _get_candidate_info()
    shadow_info = _get_shadow_info()

    report = {
        "generated_utc": timestamp_now(),
        "dataset": universe_info,
        "campaigns": campaign_info,
        "candidates": candidate_info,
        "shadow": shadow_info,
    }

    # Persist
    (rep_dir / "operational_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "operational_report.md").write_text(_build_md(report), encoding="utf-8")
    return report


def _get_universe_info(universe_file: str | None) -> dict[str, Any]:
    path = Path(universe_file or "data/research/research_universe.jsonl")
    if not path.exists():
        return {"status": "NOT_FOUND", "trades": 0}
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"status": "AVAILABLE", "trades": len(lines), "path": str(path)}


def _get_campaign_info() -> dict[str, Any]:
    from research_engine.v10.campaigns import CampaignRegistry
    reg = CampaignRegistry()
    return {"registered": len(reg.list_campaigns()), "ids": reg.campaign_ids}


def _get_candidate_info() -> dict[str, Any]:
    try:
        from research_engine.v10.candidates import CandidateRegistry
        reg = CandidateRegistry()
        all_c = reg.list_all()
        active = reg.list_active()
        return {
            "total": len(all_c),
            "active": len(active),
            "active_ids": [c.candidate_id for c in active],
        }
    except Exception:
        return {"total": 0, "active": 0, "active_ids": []}


def _get_shadow_info() -> dict[str, Any]:
    try:
        from research_engine.v10.shadow import ShadowRegistry
        reg = ShadowRegistry()
        active = reg.list_active()
        return {
            "active_shadows": len(active),
            "shadow_ids": [c.shadow_id for c in active],
        }
    except Exception:
        return {"active_shadows": 0, "shadow_ids": []}


def _build_md(report: dict) -> str:
    md = []
    md.append("# V10 Research Operational Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append("")

    ds = report["dataset"]
    md.append(f"## Dataset")
    md.append(f"- Status: {ds['status']}")
    md.append(f"- Trades: {ds.get('trades', 0)}")
    md.append("")

    camp = report["campaigns"]
    md.append(f"## Campaigns")
    md.append(f"- Registered: {camp['registered']}")
    md.append(f"- IDs: {', '.join(camp.get('ids', []))}")
    md.append("")

    cand = report["candidates"]
    md.append(f"## Candidates")
    md.append(f"- Total: {cand['total']}")
    md.append(f"- Active: {cand['active']}")
    if cand.get("active_ids"):
        for cid in cand["active_ids"]:
            md.append(f"  - {cid}")
    md.append("")

    shadow = report["shadow"]
    md.append(f"## Shadow Tests")
    md.append(f"- Active: {shadow['active_shadows']}")
    md.append("")
    md.append("---")
    return "\n".join(md)
