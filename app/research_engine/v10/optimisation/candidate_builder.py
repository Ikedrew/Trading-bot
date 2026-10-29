"""
Optimisation Bridge — Candidate Builder.

Creates optimisation candidates from hypotheses.
Candidates are human-defined — the engine only structures and validates them.
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.optimisation.models import (
    OptimisationCandidate, ValidationPlan, classify_change_risk,
)


def build_candidate(
    candidate_id: str,
    hypothesis_id: str,
    baseline_id: str,
    component: str,
    changes: dict[str, Any],
    expected_outcome: str = "",
    notes: str = "",
) -> OptimisationCandidate:
    """
    Build a structured optimisation candidate.

    Requires a baseline_id — cannot create a candidate without a reference point.
    Automatically classifies change risk.
    """
    risk = classify_change_risk(changes)

    return OptimisationCandidate(
        candidate_id=candidate_id,
        hypothesis_id=hypothesis_id,
        baseline_id=baseline_id,
        component=component,
        changes=changes,
        expected_outcome=expected_outcome,
        risk_level=risk,
        notes=notes,
    )


def build_validation_plan(
    candidate: OptimisationCandidate,
    target_questions: list[str] | None = None,
    regression_questions: list[str] | None = None,
    minimum_sample: int = 20,
) -> ValidationPlan:
    """
    Build a validation plan for a candidate.

    Defines:
        - Which metrics to track
        - Target questions (intended improvement)
        - Regression questions (must not degrade)
        - Success/failure conditions
    """
    targets = target_questions or []
    regressions = regression_questions or ["E1", "R1", "M1", "D1"]

    return ValidationPlan(
        candidate_id=candidate.candidate_id,
        baseline_id=candidate.baseline_id,
        metrics=["expectancy_r", "profit_factor", "win_rate", "net_realised_pnl"],
        target_questions=targets,
        regression_questions=regressions,
        success_conditions={
            "expectancy_r": "> baseline",
            "profit_factor": ">= baseline",
        },
        failure_conditions={
            "expectancy_r": "< baseline - 0.2R",
            "major_regression": "any regression question shows > 0.25R deterioration",
        },
        minimum_sample=minimum_sample,
    )
