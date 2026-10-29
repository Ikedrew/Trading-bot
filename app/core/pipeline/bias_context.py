"""
Bias Context — Independent directional context computation.

Phase 3 extraction: recomputes the same values as run_strategy_detection()
but in a standalone module for comparison and eventual replacement.

Does NOT replace run_strategy_detection() yet.
Does NOT affect live decisions.
Exists for dual-output validation (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data.mt5_data import Candle
from strategy.setup import setup_bias
from strategy.signals import Side


@dataclass(frozen=True)
class BiasContext:
    """Independent bias context computation result."""
    raw_bias: Side | None
    bias_phase: str           # EngineState.bias_phase at time of computation
    bias_age_seconds: float
    bias_strength: float
    bias_confirmation_count: int


def compute_bias_context(
    candles: list[Candle],
    closed_i: int,
    state: Any,
    config: Any,
) -> BiasContext:
    """
    Compute directional bias context independently of run_strategy_detection().

    This replicates Job 1 (setup_bias) and reads current FSM state.
    Does NOT mutate EngineState. Pure read + compute.

    Args:
        candles: Price data
        closed_i: Index of last closed bar
        state: EngineState (read-only access)
        config: Config module

    Returns:
        BiasContext with current bias assessment.
    """
    raw_bias = setup_bias(
        candles,
        closed_i,
        ma_period=int(getattr(config, "SETUP_MA_PERIOD", 10)),
        min_distance_from_ma=float(getattr(config, "SETUP_MIN_DISTANCE_FROM_MA", 0.00008)),
    )

    return BiasContext(
        raw_bias=raw_bias,
        bias_phase=state.bias_phase,
        bias_age_seconds=state.bias_age_seconds,
        bias_strength=state.bias_strength,
        bias_confirmation_count=state.bias_confirmation_count,
    )
