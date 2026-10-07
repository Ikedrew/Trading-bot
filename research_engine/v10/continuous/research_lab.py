"""Authoritative Research Lab read model.

Derives ALL current research state from the ``unified_research_projection_v1``
stored by ``ResearchProjectionStore``.  No legacy JSON reports, no Stage 4
control-plane state, no raw question-cycle directories are consulted here.

ONE truth source.  Every operator-facing question is answerable from a single
``load_lab_view()`` call.

Operator questions answered:
  WHAT DO WE KNOW NOW?          → canonical_questions (COMPLETE/NEGATIVE_RESULT)
  WHAT CHANGED?                 → what_changed
  WHAT IS WAITING?              → questions with work_state WAITING*
  WHAT IS BLOCKED?              → questions with work_state BLOCKED
  WHAT IS BEING DEEPLY WORKED?  → deep_research_queue running job
  WHAT IS STALE (DEEP PENDING)? → questions with execution_freshness DEEP_STALE
  WHAT CANDIDATES EXIST?        → candidates
  WHAT IS BEING VALIDATED?      → investigations_and_work_queues.validation_queue
  HOW FAR BEHIND?               → research_lag
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.v10.continuous.research_projection import (
    PROJECTION_SCHEMA,
    ResearchProjectionError,
    ResearchProjectionStore,
)


LAB_VIEW_SCHEMA = "research_lab_view_v1"
DEFAULT_PROJECTION_DIRECTORY = Path("reports/research/continuous_projection")

_CANONICAL_COUNT = 70


class ResearchLabError(RuntimeError):
    """The Research Lab could not produce a view from the authoritative projection."""


def _questions_by_state(
    canonical: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "complete": [],
        "waiting": [],
        "blocked": [],
        "deep_pending": [],
        "deep_stale": [],
        "fresh": [],
        "other": [],
    }
    for row in canonical:
        ws = str(row.get("work_state") or "")
        ef = str(row.get("execution_freshness") or "")
        status = str((row.get("result") or {}).get("status") or "")
        if status in {"COMPLETE", "NEGATIVE_RESULT"}:
            buckets["complete"].append(dict(row))
        elif ws in {"BLOCKED", "IMPLEMENTATION_BLOCKED"}:
            buckets["blocked"].append(dict(row))
        elif ws in {"WAITING_FOR_DATA", "INSUFFICIENT_DATA", "CANNOT_KNOW_YET"}:
            buckets["waiting"].append(dict(row))
        elif ws == "DEEP_PENDING":
            buckets["deep_pending"].append(dict(row))
        elif ef == "DEEP_STALE":
            buckets["deep_stale"].append(dict(row))
        elif ws == "FRESH":
            buckets["fresh"].append(dict(row))
        else:
            buckets["other"].append(dict(row))
    return buckets


def build_lab_view(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Build the canonical lab view from an already-loaded unified projection.

    Raises ResearchLabError if the projection is not a valid
    unified_research_projection_v1.
    """
    if projection.get("projection_schema") != PROJECTION_SCHEMA:
        raise ResearchLabError(
            "LAB_PROJECTION_SCHEMA_INVALID:"
            + str(projection.get("projection_schema") or "MISSING"))
    canonical = projection.get("canonical_questions") or []
    if len(canonical) != _CANONICAL_COUNT:
        raise ResearchLabError(
            "LAB_CANONICAL_COUNT_INVALID:" + str(len(canonical)))

    by_state = _questions_by_state(canonical)
    frontier = dict(projection.get("data_frontier") or {})
    lag = dict(projection.get("research_lag") or {})
    queues = dict(projection.get("investigations_and_work_queues") or {})
    changed = dict(projection.get("what_changed") or {})

    # Determine execution freshness for the whole system.
    pending_deep = int(lag.get("pending_deep_jobs") or 0)
    running_deep = int(lag.get("running_deep_jobs") or 0)
    lag_epochs = int(lag.get("lag_epochs") or 0)
    if running_deep > 0:
        execution_freshness = "DEEP_WORK_RUNNING"
    elif pending_deep > 0:
        execution_freshness = "DEEP_WORK_PENDING"
    elif lag_epochs == 0:
        execution_freshness = "CURRENT"
    else:
        execution_freshness = "DEEP_WORK_LAGGING"

    # Research lag in human-readable form.
    lag_seconds = float(lag.get("lag_seconds") or 0.0)
    if lag_seconds >= 3600:
        lag_human = f"{lag_seconds / 3600:.1f}h"
    elif lag_seconds >= 60:
        lag_human = f"{lag_seconds / 60:.0f}m"
    elif lag_seconds > 0:
        lag_human = f"{lag_seconds:.0f}s"
    else:
        lag_human = "current"

    return {
        "lab_view_schema": LAB_VIEW_SCHEMA,
        # ── Identity ────────────────────────────────────────────────────────
        "projection_version": str(projection.get("projection_version") or ""),
        "continuous_cycle_id": str(projection.get("continuous_cycle_id") or ""),
        "snapshot_id": str(frontier.get("snapshot_id") or ""),
        "investigation_epoch": str(frontier.get("investigation_epoch") or ""),
        "frontier_start": str(frontier.get("frontier_start") or ""),
        "frontier_end": str(frontier.get("frontier_end") or ""),
        "predecessor_projection_version": str(
            projection.get("predecessor_projection_version") or ""),
        # ── Execution freshness ─────────────────────────────────────────────
        "execution_freshness": execution_freshness,
        "lag_epochs": lag_epochs,
        "lag_seconds": lag_seconds,
        "lag_human": lag_human,
        "pending_deep_jobs": pending_deep,
        "running_deep_jobs": running_deep,
        "currently_running_deep_job": lag.get("currently_running_deep_job"),
        "failed_deep_jobs": list(lag.get("failed_deep_jobs") or []),
        "backpressure_active": bool(lag.get("backpressure_active")),
        # ── What we know ────────────────────────────────────────────────────
        "canonical_question_count": len(canonical),
        "complete_count": len(by_state["complete"]),
        "blocked_count": len(by_state["blocked"]),
        "waiting_count": len(by_state["waiting"]),
        "deep_pending_count": len(by_state["deep_pending"]),
        "canonical_questions": canonical,  # full rows for programmatic access
        "questions_by_state": by_state,
        # ── What changed ────────────────────────────────────────────────────
        "what_changed": changed,
        "changed_datasets": list(frontier.get("changed_datasets") or []),
        # ── Scientific state ────────────────────────────────────────────────
        "findings": list(projection.get("findings") or []),
        "hypotheses": list(projection.get("hypotheses") or []),
        "candidates": list(projection.get("candidates") or []),
        # ── Q71+ (generated research, separate from canonical 70) ──────────
        "generated_questions": list(projection.get("generated_questions") or []),
        "generated_question_count": len(
            projection.get("generated_questions") or []),
        # ── Validation / investigation queues ───────────────────────────────
        "validation_queue": list(queues.get("validation_queue") or []),
        "deep_research_queue": list(queues.get("deep_research_queue") or []),
        "review_required": list(queues.get("review_required") or []),
        # ── Full research lag ───────────────────────────────────────────────
        "research_lag": lag,
    }


def load_lab_view(
    projection_directory: Path | str = DEFAULT_PROJECTION_DIRECTORY,
) -> dict[str, Any]:
    """Load the latest authoritative projection and return the lab view.

    Raises ResearchLabError if no projection has been published yet or if the
    schema is invalid.
    """
    store = ResearchProjectionStore(projection_directory)
    try:
        projection = store.load_latest()
    except ResearchProjectionError as exc:
        raise ResearchLabError("LAB_PROJECTION_UNREADABLE:" + str(exc)) from exc
    if projection is None:
        raise ResearchLabError("LAB_NO_PROJECTION_PUBLISHED_YET")
    return build_lab_view(projection)


def render_lab_terminal(view: dict[str, Any]) -> str:
    """Compact terminal render of the authoritative lab view."""
    lines = [
        "=" * 100,
        "AUTHORITATIVE RESEARCH LAB  —  unified_research_projection_v1",
        "=" * 100,
        f"Projection : {view['projection_version']}",
        f"Cycle      : {view['continuous_cycle_id']}",
        f"Snapshot   : {view['snapshot_id']}",
        f"Epoch      : {view['frontier_start']} → {view['frontier_end']}",
        f"Freshness  : {view['execution_freshness']}  "
        f"(lag {view['lag_human']}, {view['lag_epochs']} epoch(s), "
        f"{view['pending_deep_jobs']} pending deep jobs)",
        "-" * 100,
        f"Canonical 70:  "
        f"{view['complete_count']} complete  "
        f"{view['blocked_count']} blocked  "
        f"{view['waiting_count']} waiting  "
        f"{view['deep_pending_count']} deep-pending",
        f"Q71+ generated questions: {view['generated_question_count']}",
        f"Findings: {len(view['findings'])}  "
        f"Hypotheses: {len(view['hypotheses'])}  "
        f"Candidates: {len(view['candidates'])}",
        "-" * 100,
    ]
    if view["changed_datasets"]:
        lines.append("Changed datasets: " + ", ".join(view["changed_datasets"]))
    what = view.get("what_changed") or {}
    changed_qs = what.get("questions_changed") or []
    if changed_qs:
        lines.append("Changed questions this cycle: " + ", ".join(str(q) for q in changed_qs))
    if view["blocked_count"]:
        lines.append("")
        lines.append("BLOCKED:")
        for row in view["questions_by_state"]["blocked"]:
            reason = (row.get("result") or {}).get("blocked_reason") or (
                row.get("result") or {}).get("failure_reason") or "—"
            lines.append(f"  {row.get('question_id','?'):8s}  {reason}")
    if view["deep_pending_count"] or view["pending_deep_jobs"]:
        lines.append("")
        lines.append("DEEP WORK PENDING:")
        for row in view["questions_by_state"]["deep_pending"]:
            lines.append(
                f"  {row.get('question_id','?'):8s}  "
                f"job={row.get('deep_work_job_id','?')}")
        if view.get("currently_running_deep_job"):
            lines.append(
                f"  (running: {view['currently_running_deep_job']})")
    if view["candidates"]:
        lines.append("")
        lines.append("CANDIDATES:")
        for c in view["candidates"]:
            lines.append(
                f"  {c.get('candidate_id','?'):32s}  "
                f"status={c.get('status','?')}")
    if view.get("review_required"):
        lines.append("")
        lines.append("REVIEW REQUIRED: " + ", ".join(str(r) for r in view["review_required"]))
    lines.append("=" * 100)
    return "\n".join(lines)


__all__ = [
    "LAB_VIEW_SCHEMA",
    "ResearchLabError",
    "build_lab_view",
    "load_lab_view",
    "render_lab_terminal",
]
