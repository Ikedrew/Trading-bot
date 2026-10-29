"""Canonical Research Engine operator dashboard.

Both terminal and HTML modes follow one path:

    build_all_question_states -> build_operator_projection -> renderer

No legacy registry, audit dashboard, V10 question bank, or demo data is read.
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical Research Engine dashboard")
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate reports/research/cockpit.html instead of terminal output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
