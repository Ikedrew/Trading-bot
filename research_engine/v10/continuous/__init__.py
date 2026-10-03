"""
Continuous Research Operation.

Orchestrates the complete research lifecycle as a repeatable,
governed, resumable cycle that composes existing Items 8-11.

Components:
    - orchestrator: ContinuousResearchOrchestrator
    - state: Persistent cycle state
    - trigger: Data readiness / change detection
"""

from research_engine.v10.continuous.orchestrator import ContinuousResearchOrchestrator
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierCycleResult,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.canonical_question_cycle import (
    CanonicalQuestionCycleError,
    run_canonical_question_cycle,
)
from research_engine.v10.continuous.state import CycleState, CycleStatus, TriggerStatus

__all__ = [
    "ContinuousResearchOrchestrator",
    "CanonicalQuestionCycleError",
    "FrontierCycleResult",
    "CycleState",
    "CycleStatus",
    "TriggerStatus",
    "run_frontier_snapshot_cycle",
    "run_canonical_question_cycle",
]
