"""
State management — snapshot and delta types for read-only evaluation.

Architecture:
  1. State Preparation: FSM + memory updates mutate EngineState
  2. Snapshot Freeze: StateSnapshot.from_state(state) creates immutable view
  3. Evaluation: voters/stages read snapshot only
  4. Post-Decision: apply_delta(state, delta) applies collected mutations
"""

from core.state.snapshot import StateSnapshot
from core.state.delta import StateDelta, apply_delta

__all__ = ["StateSnapshot", "StateDelta", "apply_delta"]
