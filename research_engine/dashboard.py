"""Canonical Research Engine operator dashboard.

Two authority surfaces are exposed:

1. Stage 4 control-plane projection  (legacy governance / readiness view):
       build_all_question_states -> build_operator_projection -> renderer
   Sourced from the certified Stage 4 scientific re-entry state.

2. Continuous research lab view (authoritative current truth):
       load_lab_view -> build_lab_view -> render_lab_terminal
   Sourced exclusively from unified_research_projection_v1.
   This is the single authoritative truth store for the Stage B continuous
   research loop.  It surfaces projection_version, snapshot/epoch identity,
   canonical 70 current results, Q71+ state separately, findings, hypotheses,
   candidates, validation state, BLOCKED/WAITING/INSUFFICIENT_DATA,
   work_state / execution_freshness, deep pending/running/stale state,
   research lag, and what changed this cycle.

The Stage 4 view remains available for compatibility.  For any question about
the CURRENT continuous research truth, prefer the lab view.

No legacy registry, audit dashboard, V10 question bank, or demo data is read
by either surface.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, Sequence

from research_engine.control_plane import build_all_question_states
from research_engine.control_plane.operator_projection import (
    build_operator_projection,
    render_html,
    render_terminal,
)
from research_engine.v10.continuous.research_lab import (
    ResearchLabError,
    build_lab_view,
    load_lab_view,
    render_lab_terminal,
)


_HTML_PATH = Path("reports/research/cockpit.html")


def generate_dashboard(states: Iterable[Any] | None = None) -> dict[str, Any]:
    """Build the canonical operator projection once."""
    canonical_states = list(states) if states is not None else build_all_question_states()
    return build_operator_projection(canonical_states)


def print_dashboard(projection: dict[str, Any] | None = None) -> None:
    """Print the canonical terminal dashboard."""
    canonical_projection = projection if projection is not None else generate_dashboard()
    print(render_terminal(canonical_projection))


def write_html_dashboard(
    projection: dict[str, Any],
    output_path: str | Path = _HTML_PATH,
) -> Path:
    """Write HTML from an already-built canonical projection."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(projection), encoding="utf-8")
    return path


def can_execute(question_id: str, projection: dict[str, Any] | None = None) -> bool:
    """Return true only for a canonically READY question."""
    canonical_projection = projection if projection is not None else generate_dashboard()
    return any(
        question["question_id"] == question_id and question["state_status"] == "READY"
        for question in canonical_projection["questions"]
    )


def get_execution_gate(
    question_id: str,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical state behind the operator execution gate."""
    canonical_projection = projection if projection is not None else generate_dashboard()
    question = next(
        (item for item in canonical_projection["questions"] if item["question_id"] == question_id),
        None,
    )
    if question is None:
        return {
            "allowed": False,
            "question_id": question_id,
            "status": "NOT_FOUND",
            "reason": f"Question {question_id} not in canonical registry",
        }
    return {
        "allowed": question["state_status"] == "READY",
        "question_id": question_id,
        "status": question["state_status"],
        "reason": question["readiness_reason"],
        "requirements": question["requirements"],
    }


def load_continuous_lab_view(
    projection_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Load the authoritative continuous research lab view.

    Derives state exclusively from unified_research_projection_v1.
    Raises ResearchLabError if no projection has been published.
    """
    kwargs: dict[str, Any] = {}
    if projection_directory is not None:
        kwargs["projection_directory"] = Path(projection_directory)
    return load_lab_view(**kwargs)


def print_continuous_lab(
    projection_directory: str | Path | None = None,
) -> None:
    """Print the authoritative continuous research lab view to stdout."""
    try:
        view = load_continuous_lab_view(projection_directory)
        print(render_lab_terminal(view))
    except ResearchLabError as exc:
        print(f"[RESEARCH LAB] No authoritative projection available: {exc}")
        print("Run run_continuous_research_cycle() at least once to publish a projection.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical Research Engine dashboard")
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate reports/research/cockpit.html instead of terminal output",
    )
    parser.add_argument(
        "--lab",
        action="store_true",
        help=(
            "Print the authoritative continuous research lab view "
            "(unified_research_projection_v1) instead of the Stage 4 cockpit"
        ),
    )
    parser.add_argument(
        "--projection-dir",
        default=None,
        help="Override default projection directory for --lab mode",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.lab:
        print_continuous_lab(args.projection_dir)
        return 0

    # Stage 4 control-plane cockpit (legacy governance / readiness view).
    # The canonical state and operator projection are each built exactly once.
    states = build_all_question_states()
    projection = build_operator_projection(states)
    if args.html:
        path = write_html_dashboard(projection)
        print(f"Canonical Research Engine cockpit generated: {path}")
    else:
        print(render_terminal(projection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
