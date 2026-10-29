"""
StateSnapshot — Immutable view of EngineState for read-only evaluation.

This dataclass captures all EngineState fields needed by evaluation stages.
It is frozen (immutable) to guarantee no evaluation stage can accidentally
modify trading state during decision computation.

Ownership: core/state/snapshot.py
Created by: process_bar (after state preparation phase)
Consumed by: evaluation stages (read-only)
Must NOT contain: decision logic, scoring logic, trade gates
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy.signals import Side


@dataclass(frozen=True)
class StateSnapshot:
    """
    Immutable point-in-time view of EngineState.

    Created once per bar evaluation after all state preparation
    (FSM transitions, memory updates, config sync) completes.

    No evaluation stage may modify this object.
    """

    # Bias FSM output
    bias_phase: str
    current_bias: Side | None
    bias_strength: float
    bias_age_seconds: float
    bias_confirmation_count: int
    bias_contradiction_count: int

    # Market memory
    regime_state: str
    last_sweep_high: float | None
    last_sweep_low: float | None
    last_strong_impulse_direction: Side | None

    # Timing
    current_time: float
    last_bias_time: float | None

    # Scoring inputs
    volatility_filter: float

    # Execution tracking (read by trade_quality)
    last_trade_side: str | None
    last_trade_bar: int | None
    last_successful_open_mono: float | None

    # Stability input
    bias_flip_bars_count: int

    # Pre-computed convenience
    can_trade_bias: bool

    # Lock state
    bias_lock_until_candle: int
    bias_lock_until_time: float

    # ─── STRUCTURE COHESION (rolling score system) ────────────────────
    structure_score: float = 0.0
    structure_regime: str = "WEAK"

    # ─── FEATURE-DERIVED FIELDS (from FeatureBundle) ──────────────────
    # These replace FSM-derived structure proxies for voter consumption.
    m5_atr_14: float = 0.0
    m5_atr_ratio: float = 1.0
    candle_overlap_ratio: float = 0.0
    spread: float = 0.0
    m5_swing_high_count: int = 0
    m5_swing_low_count: int = 0
    m5_structure_clarity: float = 0.0
    feature_sweep_high: float | None = None
    feature_sweep_low: float | None = None

    @staticmethod
    def from_state(state) -> "StateSnapshot":
        """
        Create an immutable snapshot from a live EngineState.
        Does NOT include feature-derived fields (use from_state_and_features instead).
        """
        return StateSnapshot(
            bias_phase=state.bias_phase,
            current_bias=state.current_bias,
            bias_strength=state.bias_strength,
            bias_age_seconds=state.bias_age_seconds,
            bias_confirmation_count=state.bias_confirmation_count,
            bias_contradiction_count=state.bias_contradiction_count,
            regime_state=state.regime_state,
            last_sweep_high=state.last_sweep_high,
            last_sweep_low=state.last_sweep_low,
            last_strong_impulse_direction=state.last_strong_impulse_direction,
            current_time=state.current_time,
            last_bias_time=state.last_bias_time,
            volatility_filter=state.volatility_filter,
            last_trade_side=state.last_trade_side,
            last_trade_bar=state.last_trade_bar,
            last_successful_open_mono=state.last_successful_open_mono,
            bias_flip_bars_count=len(state.bias_flip_bars) if state.bias_flip_bars else 0,
            can_trade_bias=(state.bias_phase == "CONFIRMED"),
            bias_lock_until_candle=state.bias_lock_until_candle,
            bias_lock_until_time=state.bias_lock_until_time,
            structure_score=state.structure_score,
            structure_regime=state.structure_regime,
        )

    @staticmethod
    def from_state_and_features(state, features) -> "StateSnapshot":
        """
        Create snapshot combining FSM state + pure market features.

        Args:
            state: EngineState (for FSM/bias fields)
            features: FeatureBundle (for market-derived signals)
        """
        return StateSnapshot(
            bias_phase=state.bias_phase,
            current_bias=state.current_bias,
            bias_strength=state.bias_strength,
            bias_age_seconds=state.bias_age_seconds,
            bias_confirmation_count=state.bias_confirmation_count,
            bias_contradiction_count=state.bias_contradiction_count,
            regime_state=state.regime_state,
            last_sweep_high=state.last_sweep_high,
            last_sweep_low=state.last_sweep_low,
            last_strong_impulse_direction=state.last_strong_impulse_direction,
            current_time=state.current_time,
            last_bias_time=state.last_bias_time,
            volatility_filter=state.volatility_filter,
            last_trade_side=state.last_trade_side,
            last_trade_bar=state.last_trade_bar,
            last_successful_open_mono=state.last_successful_open_mono,
            bias_flip_bars_count=len(state.bias_flip_bars) if state.bias_flip_bars else 0,
            can_trade_bias=(state.bias_phase == "CONFIRMED"),
            bias_lock_until_candle=state.bias_lock_until_candle,
            bias_lock_until_time=state.bias_lock_until_time,
            structure_score=state.structure_score,
            structure_regime=state.structure_regime,
            # Feature-derived fields
            m5_atr_14=features.m5_atr_14,
            m5_atr_ratio=features.m5_atr_ratio,
            candle_overlap_ratio=features.candle_overlap_ratio,
            spread=features.spread,
            m5_swing_high_count=features.m5_swing_high_count,
            m5_swing_low_count=features.m5_swing_low_count,
            m5_structure_clarity=features.m5_structure_clarity,
            feature_sweep_high=features.last_sweep_high,
            feature_sweep_low=features.last_sweep_low,
        )
