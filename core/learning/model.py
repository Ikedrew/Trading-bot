"""
LearningRecord — Frozen observation from analysing a completed decision.

Answers: "Was the system's reasoning calibrated?"
NOT: "Did the trade win?"

This is a learning OBSERVATION, not a parameter update.
It does not modify weights, thresholds, or behaviour.

Calibration means:
    - When we said "high confidence" — were we right more often?
    - When we identified contradictions — did they materialise?
    - When uncertainty was high — were outcomes less predictable?
    - When evidence was strong — did direction hold?

NEVER:
    - Adjusts weights
    - Modifies thresholds
    - Changes trading behaviour
    - Reinforces luck
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LearningRecord:
    """
    Single learning observation from a completed decision cycle.

    Produced by analysing a historical decision + its actual outcome.
    Consumed by offline analysis, dashboards, and future calibration review.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    decision_id: str                    # Correlation ID linking to original decision

    # ─── WHAT WE BELIEVED ─────────────────────────────────────────────
    thesis: str                         # The primary thesis at decision time
    evidence_quality: float             # 0.0–1.0: how strong was supporting evidence?
    uncertainty_score: float            # 0.0–1.0: how uncertain were we?

    # ─── WHAT HAPPENED ────────────────────────────────────────────────
    outcome: str                        # "WIN" | "LOSS" | "BREAKEVEN" | "MISSED" | "BLOCKED"

    # ─── CALIBRATION ASSESSMENT ───────────────────────────────────────
    # Was the belief system calibrated for this decision?
    # "CALIBRATED" = confidence matched outcome probability
    # "OVERCONFIDENT" = high confidence but outcome failed
    # "UNDERCONFIDENT" = low confidence but outcome succeeded
    # "UNCERTAIN_CORRECT" = high uncertainty, outcome was indeed unpredictable
    # "UNCERTAIN_WRONG" = high uncertainty, but outcome was actually clear
    calibration_result: str

    # ─── INSIGHTS ─────────────────────────────────────────────────────
    # Human-readable observations about what this decision teaches.
    # Example: ["HTF alignment was correct predictor", "Uncertainty was justified"]
    insights: tuple[str, ...]

    # ─── METADATA ─────────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "decision_id": self.decision_id,
            "thesis": self.thesis,
            "evidence_quality": round(self.evidence_quality, 4),
            "uncertainty_score": round(self.uncertainty_score, 4),
            "outcome": self.outcome,
            "calibration_result": self.calibration_result,
            "insights": list(self.insights),
            "metadata": self.metadata,
        }
