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
from research_engine.v10.continuous.state import CycleState, CycleStatus, TriggerStatus

__all__ = [
    "ContinuousResearchOrchestrator",
    "CycleState",
    "CycleStatus",
    "TriggerStatus",
]
