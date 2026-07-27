"""
Question Registry — Priority-classified experiment catalogue.

Every research question has:
    - A unique ID (Q1-Q19)
    - A priority level (P0-P3)
    - Data source requirements
    - Implementation status
    - A runner function (when implemented)

Priority Classification:
    P0 — Directly improves next-trade probability or sizing
    P1 — Identifies systematic edge decay or regime misclassification
    P2 — Improves execution quality or reduces friction
    P3 — Deepens understanding without immediate tactical value
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable


class Priority(IntEnum):
    """Research question priority level."""
    P0 = 0  # Directly improves next-trade probability or sizing
    P1 = 1  # Edge decay / regime misclassification detection
    P2 = 2  # Execution quality improvement
    P3 = 3  # Deep understanding without immediate tactical value


class Status(str):
    """Implementation status."""
    READY = "ready"           # Experiment implemented and runnable
    BLOCKED = "blocked"       # Requires data that doesn't exist yet
    NOT_IMPLEMENTED = "not_implemented"  # Code not written yet
    DEPRECATED = "deprecated"  # Superseded or no longer relevant


@dataclass
class ResearchQuestion:
    """A single research question in the registry."""
    id: str
    priority: Priority
    question: str
    data_sources: list[str]
    status: str
    runner: str | None = None  # Module path to experiment runner (e.g., "experiments.shadow_validation")
    blocker: str = ""          # If status=BLOCKED, describe what's missing
    notes: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

QUESTIONS: list[ResearchQuestion] = [
    # ─── P0: Decision Quality & Edge ──────────────────────────────────
    ResearchQuestion(
        id="Q1",
        priority=Priority.P0,
        question="Which scoring components predict actual R-multiples?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.READY,
        runner="experiments.component_reward",
        notes="Joins decision_trace components with shadow trade R-multiples via correlation_id",
    ),
    ResearchQuestion(
        id="Q2",
        priority=Priority.P0,
        question="What is the optimal score threshold by regime?",
        data_sources=["decision_trace", "shadow_trades", "decision_ledger"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q3",
        priority=Priority.P0,
        question="Which terminal stages have the highest missed-opportunity cost?",
        data_sources=["decision_trace"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q4",
        priority=Priority.P0,
        question="Is the engine's confidence calibrated?",
        data_sources=["decision_ledger", "shadow_trades", "trade_truth"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q5",
        priority=Priority.P0,
        question="What patterns degrade over time?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.NOT_IMPLEMENTED,
    ),

    # ─── P1: Regime & Market Structure ────────────────────────────────
    ResearchQuestion(
        id="Q6",
        priority=Priority.P1,
        question="Does the regime classifier agree with realised outcomes?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q7",
        priority=Priority.P1,
        question="Which sessions produce the best edge?",
        data_sources=["execution_context", "shadow_trades", "trade_truth"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q8",
        priority=Priority.P1,
        question="How does HTF alignment affect outcomes?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q9",
        priority=Priority.P1,
        question="What spread/volatility conditions produce the best fills?",
        data_sources=["execution_context", "execution_results", "trade_truth"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q10",
        priority=Priority.P1,
        question="Are guard rejections improving or degrading system performance?",
        data_sources=["decision_ledger"],
        status=Status.NOT_IMPLEMENTED,
        blocker="Requires forward shadow projection for blocked trades",
    ),

    # ─── P2: Execution Quality ────────────────────────────────────────
    ResearchQuestion(
        id="Q11",
        priority=Priority.P2,
        question="What is the true slippage model per symbol per session?",
        data_sources=["execution_results", "execution_context"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q12",
        priority=Priority.P2,
        question="Is the broker rejecting orders in predictable patterns?",
        data_sources=["execution_results"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q13",
        priority=Priority.P2,
        question="What is the optimal trade duration?",
        data_sources=["shadow_trades"],
        status=Status.NOT_IMPLEMENTED,
    ),

    # ─── P3: Deep Understanding ───────────────────────────────────────
    ResearchQuestion(
        id="Q14",
        priority=Priority.P3,
        question="What causal chains produce the best trades?",
        data_sources=["trade_truth_graph", "trade_truth"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q15",
        priority=Priority.P3,
        question="How does the engine learn over time?",
        data_sources=["learning"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q16",
        priority=Priority.P0,
        question="What is the correlation between shadow and live outcomes?",
        data_sources=["shadow_trades", "trade_truth"],
        status=Status.BLOCKED,
        runner="experiments.shadow_validation",
        blocker="Requires matched live trades (system has not yet executed with identity propagation active)",
        notes="Implemented but awaiting first live trade with correlation_id in Trade Truth",
    ),
    ResearchQuestion(
        id="Q17",
        priority=Priority.P3,
        question="What market conditions precede system drawdowns?",
        data_sources=["execution_context", "trade_truth"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q18",
        priority=Priority.P3,
        question="Are there symbols that should be removed or added?",
        data_sources=["shadow_trades", "decision_ledger"],
        status=Status.NOT_IMPLEMENTED,
    ),
    ResearchQuestion(
        id="Q19",
        priority=Priority.P0,
        question="What is the system's true edge expressed as expected value?",
        data_sources=["shadow_trades"],
        status=Status.READY,
        runner="experiments.expected_value",
        notes="Answerable now from shadow trades alone (469+ records available)",
    ),

    # ─── P0: Probability / EV ─────────────────────────────────────────
    ResearchQuestion(
        id="Q20",
        priority=Priority.P0,
        question="Is score calibrated to observed outcomes?",
        data_sources=["decision_trace", "shadow_trades", "research_shadow_trades"],
        status=Status.READY,
        runner="experiments.score_calibration",
        notes=(
            "Compares raw_score and calibrated_probability (from ProbabilityEstimator) "
            "against actual shadow trade win rates. Determines whether the ScoreCalibrator "
            "identity mapping should be replaced with empirical calibration. "
            "Produces: calibration error, reliability assessment, PROMOTE/KEEP/INSUFFICIENT recommendation."
        ),
    ),
    ResearchQuestion(
        id="Q21",
        priority=Priority.P0,
        question="Does calibrated probability improve EV decisions?",
        data_sources=["shadow_trades", "decision_trace"],
        status=Status.READY,
        runner="experiments.research_runner",
        notes="Compares current vs calibrated probability impact on trade selection.",
    ),
    ResearchQuestion(
        id="Q22",
        priority=Priority.P0,
        question="What EV threshold maximises expectancy?",
        data_sources=["shadow_trades"],
        status=Status.READY,
        runner="experiments.research_runner",
        notes="Tests different score/EV thresholds against shadow outcomes.",
    ),
    ResearchQuestion(
        id="Q23",
        priority=Priority.P1,
        question="Which regimes actually produce edge?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.READY,
        runner="experiments.research_runner",
        notes="Regime × outcome analysis for regime-conditional policy.",
    ),
    ResearchQuestion(
        id="Q24",
        priority=Priority.P1,
        question="Which strategies contain real expectancy?",
        data_sources=["decision_trace", "shadow_trades"],
        status=Status.READY,
        runner="experiments.research_runner",
        notes="Strategy activation × outcome for strategy-specific edge.",
    ),
    ResearchQuestion(
        id="Q25",
        priority=Priority.P1,
        question="Where does the bot perform best (symbol/session)?",
        data_sources=["shadow_trades"],
        status=Status.READY,
        runner="experiments.research_runner",
        notes="Symbol and session edge analysis for universe selection.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

def get_question(question_id: str) -> ResearchQuestion | None:
    """Look up a question by ID."""
    for q in QUESTIONS:
        if q.id == question_id:
            return q
    return None


def get_by_priority(priority: Priority) -> list[ResearchQuestion]:
    """Get all questions at a given priority level."""
    return [q for q in QUESTIONS if q.priority == priority]


def get_ready() -> list[ResearchQuestion]:
    """Get all questions with status=READY (runnable now)."""
    return [q for q in QUESTIONS if q.status == Status.READY]


def get_blocked() -> list[ResearchQuestion]:
    """Get all questions that are blocked on prerequisites."""
    return [q for q in QUESTIONS if q.status == Status.BLOCKED]


def get_next_experiment() -> ResearchQuestion | None:
    """
    Get the highest-priority ready experiment.

    Selection order: lowest priority number (P0 first), then by Q number.
    """
    ready = get_ready()
    if not ready:
        return None
    ready.sort(key=lambda q: (q.priority, q.id))
    return ready[0]


def summary() -> dict[str, Any]:
    """Registry summary statistics."""
    return {
        "total": len(QUESTIONS),
        "by_priority": {
            "P0": len(get_by_priority(Priority.P0)),
            "P1": len(get_by_priority(Priority.P1)),
            "P2": len(get_by_priority(Priority.P2)),
            "P3": len(get_by_priority(Priority.P3)),
        },
        "by_status": {
            "ready": len(get_ready()),
            "blocked": len(get_blocked()),
            "not_implemented": len([q for q in QUESTIONS if q.status == Status.NOT_IMPLEMENTED]),
        },
        "next_experiment": get_next_experiment().id if get_next_experiment() else None,
    }
