"""
Control Centre Status Generator.

Produces a concise, navigable status document from the control plane state.
This is the human-readable index of the entire research system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research_engine.v10.control_plane.models import (
    ControlPlaneState,
    QuestionLifecycle,
)


def generate_status_text(state: ControlPlaneState) -> str:
    """Generate a concise control centre status document."""
    lines = []
    lines.append("RESEARCH ENGINE CONTROL CENTRE")
    lines.append("=" * 50)
    lines.append("")

    # Engine
    lines.append("ENGINE")
    lines.append(f"    Version:   {state.engine_version}")
    lines.append(f"    Updated:   {state.last_updated or 'never'}")
    lines.append(f"    Last run:  {state.last_run_timestamp or 'none'}")
    lines.append("")

    # Universes
    lines.append("UNIVERSES")
    for u in state.universes:
        status_icon = "+" if u.status == "VALID" else "!" if u.status == "DEGRADED" else "X"
        lines.append(f"    {status_icon} {u.universe_id:<12} {u.record_count:>6} records  [{u.status}]")
    if not state.universes:
        lines.append("    (not indexed)")
    lines.append("")

    # Populations
    lines.append("POPULATIONS")
    lines.append(f"    Valid:     {state.populations_valid}")
    lines.append(f"    Empty:     {state.populations_empty}")
    lines.append(f"    Degraded:  {state.populations_degraded}")
    lines.append("")

    # Questions
    lines.append("QUESTIONS")
    lines.append(f"    Active:    {state.questions_active}")
    lines.append(f"    Run:       {state.questions_run}")
    lines.append(f"    Blocked:   {state.questions_blocked}")
    lines.append(f"    Candidate: {state.questions_candidate}")
    lines.append(f"    Archived:  {state.questions_archived}")
    lines.append("")

    # Latest run
    if state.latest_run:
        r = state.latest_run
        lines.append("LATEST RUN")
        lines.append(f"    Run ID:        {r.run_id}")
        lines.append(f"    Timestamp:     {r.timestamp}")
        lines.append(f"    Evaluated:     {r.questions_requested}")
        lines.append(f"    Completed:     {r.questions_executed}")
        lines.append(f"    Inconclusive:  {r.questions_inconclusive}")
        lines.append(f"    Blocked:       {r.questions_blocked}")
        lines.append(f"    Findings:      {r.findings_generated}")
        lines.append(f"    Anomalies:     {r.anomalies_detected}")
        lines.append(f"    Exceptional:   {r.exceptional_views}")
        lines.append(f"    Duration:      {r.duration_seconds:.1f}s")
        lines.append("")

    # Question development
    lines.append("QUESTION DEVELOPMENT")
    lines.append(f"    Candidates:    {len(state.candidate_questions)}")
    lines.append(f"    Gaps:          {state.gaps_discovered}")
    lines.append("")

    # Growth limits
    gl = state.growth_limits
    lines.append("GROWTH LIMITS")
    lines.append(f"    Max active:    {gl.max_active_questions}")
    lines.append(f"    Max new/run:   {gl.max_new_questions_per_run}")
    lines.append(f"    Auto-activate: {gl.auto_activate_questions}")
    lines.append(f"    Auto-optimise: {gl.auto_optimise}")
    lines.append("")

    # Four-angle breakdown
    if state.questions:
        lines.append("FOUR-ANGLE BREAKDOWN")
        angle_counts: dict[str, int] = {}
        for q in state.questions:
            for part in q.angle_primary.split("+"):
                angle_counts[part] = angle_counts.get(part, 0) + 1
        for angle in ("EXEC", "DECI", "MARK", "STRA"):
            count = angle_counts.get(angle, 0)
            lines.append(f"    {angle:<12} {count} questions")
        lines.append("")

    # Next action
    lines.append("NEXT ACTION")
    if not state.universes:
        lines.append("    -> Index universes")
    elif state.questions_run == 0:
        lines.append("    -> Execute first research run")
    elif state.latest_run and state.latest_run.questions_blocked > 0:
        lines.append(f"    -> Resolve {state.latest_run.questions_blocked} blocked questions")
    else:
        lines.append("    -> Review findings and run next iteration")
    lines.append("")

    return "\n".join(lines)


def generate_status_file(state: ControlPlaneState, path: Path | None = None) -> Path:
    """Write the status document to a file."""
    output = path or Path("reports/research/control_centre_status.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generate_status_text(state), encoding="utf-8")
    return output
