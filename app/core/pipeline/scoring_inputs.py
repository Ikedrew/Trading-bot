"""
Scoring Inputs — Independent threshold and phase computation.

Phase 3 extraction: recomputes the same values as run_strategy_detection()
but in a standalone module for comparison and eventual replacement.

Does NOT replace run_strategy_detection() yet.
Does NOT affect live decisions.
Exists for dual-output validation (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.pipeline.bias_thresholds import bias_window_phase, dynamic_confluence_threshold


@dataclass(frozen=True)
class ScoringInputs:
    """Pre-computed scoring parameters independent of run_strategy_detection()."""
    bias_window_phase: str                # "early" / "optimal" / "late"
    confluence_threshold_dynamic: float   # adjusted min_score
    bias_confluence_threshold: float      # base threshold from config/state


def compute_scoring_inputs(state: Any, config: Any) -> ScoringInputs:
    """
    Compute scoring threshold inputs independently.

    Replicates Jobs 5+6 from run_strategy_detection():
    - bias_window_phase calculation
    - dynamic_confluence_threshold calculation

    Pure computation. No state mutation. No side effects.

    Args:
        state: EngineState (read-only)
        config: Config module (read-only)

    Returns:
        ScoringInputs with phase and threshold values.
    """
    # Sync thresholds from config (same as run_strategy_detection does)
    base_threshold = float(getattr(config, "BIAS_CONFLUENCE_THRESHOLD", getattr(state, "bias_confluence_threshold", 4.0)))
    expiry = float(getattr(config, "BIAS_EXPIRY_SECONDS", getattr(state, "bias_expiry_seconds", 7200.0)))
    age = float(getattr(state, "bias_age_seconds", 0.0))

    phase = bias_window_phase(age, expiry)
    threshold = dynamic_confluence_threshold(base_threshold, age, expiry)

    return ScoringInputs(
        bias_window_phase=phase,
        confluence_threshold_dynamic=threshold,
        bias_confluence_threshold=base_threshold,
    )
