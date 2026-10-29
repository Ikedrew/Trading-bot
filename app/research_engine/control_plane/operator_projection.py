"""Canonical operator projection and presentation renderers.

This module consumes only canonical ``QuestionState`` objects.  It performs no
research calculation, data loading, candidate mutation, or governance writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from typing import Any, Iterable


_STATE_ORDER = (
    "COMPLETE", "READY", "WAITING_DATA", "BLOCKED",
    "NO_RUNNER", "ERROR", "UNKNOWN",
)


def build_operator_projection(states: Iterable[Any]) -> dict[str, Any]:
    """Build the single serializable projection used by both renderers."""
    state_list = list(states)
    questions = [state.to_dict() for state in state_list]
    counts = Counter(question["state_status"] for question in questions)
    unmapped = sorted({
        candidate_id
        for question in questions
        for candidate_id in question.get("unmapped_candidate_ids", [])
    })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canonical_registry": "research_engine.registry.research_question_registry.REGISTRY",
        "total_questions": len(questions),
        "state_counts": {status: counts.get(status, 0) for status in _STATE_ORDER},
        "report_counts": {
            "authoritative": sum(bool(q.get("authoritative_report")) for q in questions),
            "invalidated": sum(q.get("invalidated_report_count", 0) for q in questions),
            "stale": sum(q.get("stale_report_count", 0) for q in questions),
            "legacy": sum(q.get("legacy_report_count", 0) for q in questions),
        },
        "governance_counts": {
            "questions_with_candidates": sum(q.get("candidate_count", 0) > 0 for q in questions),
            "unmapped_candidates": len(unmapped),
            "approved": sum(q.get("decision_status") == "APPROVED" for q in questions),
            "deployed": sum(q.get("production_application_status") == "DEPLOYED" for q in questions),
            "verified": sum(q.get("production_application_status") == "VERIFIED" for q in questions),
        },
        "unmapped_candidate_ids": unmapped,
        "questions": questions,
    }


def render_terminal(projection: dict[str, Any]) -> str:
    """Render a compact terminal view from a canonical projection."""
    counts = projection["state_counts"]
    lines = [
        "=" * 100,
        "CANONICAL RESEARCH ENGINE",
        "=" * 100,
        f"Questions: {projection['total_questions']} | "
        + " | ".join(f"{status}: {counts[status]}" for status in _STATE_ORDER),
        f"Registry: {projection['canonical_registry']}",
        "-" * 100,
        f"{'ID':<10} {'STATE':<13} {'SAMPLE':>8}  {'RUNNER':<10} {'REPORT':<14} QUESTION",
        "-" * 100,
    ]
    for question in projection["questions"]:
        sample = question.get("current_sample_size")
        sample_text = "?" if sample is None else str(sample)
        lines.append(
            f"{question['question_id']:<10} {question['state_status']:<13} "
            f"{sample_text:>8}  {question['runner_status']:<10} "
            f"{question['report_validity']:<14} {question['title']}"
        )
        if question["state_status"] != "COMPLETE":
            lines.append(f"  reason: {question['readiness_reason']}")
    return "\n".join(lines)


def render_html(projection: dict[str, Any]) -> str:
    """Render a self-contained HTML cockpit from the same projection."""
    counts = projection["state_counts"]
    cards = "".join(
        f'<div class="card"><strong>{escape(status)}</strong><span>{counts[status]}</span></div>'
        for status in _STATE_ORDER
    )
    rows = []
    for question in projection["questions"]:
        sample = question.get("current_sample_size")
        sample_text = "UNKNOWN" if sample is None else str(sample)
        rows.append(
            "<tr>"
            f"<td>{escape(question['question_id'])}</td>"
            f"<td>{escape(question['title'])}</td>"
            f"<td>{escape(question['state_status'])}</td>"
            f"<td>{escape(sample_text)}</td>"
            f"<td>{escape(question['runner_status'])}</td>"
            f"<td>{escape(question['report_validity'])}</td>"
            f"<td>{escape(question['readiness_reason'])}</td>"
            f"<td>{escape(question['next_action'])}</td>"
            "</tr>"
        )
    payload = escape(json.dumps(projection, sort_keys=True), quote=False)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Canonical Research Engine Cockpit</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}}
h1{{margin-bottom:4px}} .meta{{color:#94a3b8;margin-bottom:20px}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}} .card{{background:#1e293b;padding:12px 16px;border-radius:8px}}
.card span{{display:block;font-size:1.5rem}} table{{border-collapse:collapse;width:100%;background:#111827}}
th,td{{border:1px solid #334155;padding:7px;text-align:left;vertical-align:top}} th{{background:#1e293b;position:sticky;top:0}}
tr:nth-child(even){{background:#172033}} .payload{{display:none}}
</style></head><body>
<h1>Canonical Research Engine Cockpit</h1>
<div class="meta">{projection['total_questions']} questions · {escape(projection['canonical_registry'])}</div>
<div class="cards">{cards}</div>
<table><thead><tr><th>ID</th><th>Question</th><th>State</th><th>Sample</th><th>Runner</th><th>Report</th><th>Reason</th><th>Next action</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<script id="canonical-projection" type="application/json" class="payload">{payload}</script>
</body></html>"""
