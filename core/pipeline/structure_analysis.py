"""Bias FSM through structural guards, alignment, and chosen Signal (pre-confirmation)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from core.engine_state import EngineState
from core.pipeline.dashboard import record_rejection
from core.pipeline.finish_params import FinishParams
from core.pipeline.strategy_detection import DetectionBundle
from core.pipeline_types import StructureResult
from data.mt5_data import Candle
from strategy.signals import Signal, Side

logger = logging.getLogger(__name__)


def bias_metrics_for_side(
    candles: list[Candle],
    closed_i: int,
    side: Side,
    bar_signals: list[Signal],
) -> tuple[int, bool, float]:
    aligned_signals = [s for s in bar_signals if s.side == side]

    structure_alignment = 0
    continuation_signal = 0
    lack_of_reversal = 0
    if closed_i >= 1:
        prev_candle = candles[closed_i - 1]
        curr_candle = candles[closed_i]
        if side == Side.BUY:
            if curr_candle.high > prev_candle.high:
                structure_alignment += 1
            if curr_candle.low > prev_candle.low:
                structure_alignment += 1
            if curr_candle.close >= prev_candle.low:
                lack_of_reversal = 1
        elif side == Side.SELL:
            if curr_candle.low < prev_candle.low:
                structure_alignment += 1
            if curr_candle.high < prev_candle.high:
                structure_alignment += 1
            if curr_candle.close <= prev_candle.high:
                lack_of_reversal = 1

    if aligned_signals:
        continuation_signal += 1
    if closed_i >= 2:
        pullback_candle = candles[closed_i - 1]
        continuation_candle = candles[closed_i]
        if side == Side.BUY:
            if pullback_candle.close < pullback_candle.open and continuation_candle.close > continuation_candle.open:
                continuation_signal += 1
        elif side == Side.SELL:
            if pullback_candle.close > pullback_candle.open and continuation_candle.close < continuation_candle.open:
                continuation_signal += 1

    validation_score = structure_alignment + continuation_signal + lack_of_reversal
    structure_ok = structure_alignment >= 1 and lack_of_reversal == 1
    confluence_score = 0.0
    if structure_ok:
        confluence_score += 2.0
    if aligned_signals:
        confluence_score += 2.0
    if continuation_signal >= 2:
        confluence_score += 1.0

    return validation_score, structure_ok, confluence_score


def calculate_bias_stability_score(state: EngineState, closed_i: int) -> float:
    window = 20
    assert state.bias_flip_bars is not None
    while state.bias_flip_bars and (closed_i - state.bias_flip_bars[0]) > window:
        state.bias_flip_bars.popleft()

    flips = len(state.bias_flip_bars)
    max_tolerable_flips = 4
    stability = 1.0 - min(flips / max_tolerable_flips, 1.0)
    return max(0.0, min(1.0, stability))


def in_recent_failure_zone(state: EngineState, price: float, now_s: float) -> bool:
    zone_ttl_s = 1800.0
    zone_half_width = 0.0002
    assert state.last_failed_setups is not None
    for ts, low, high, _reason in state.last_failed_setups:
        if now_s - ts > zone_ttl_s:
            continue
        if (low - zone_half_width) <= price <= (high + zone_half_width):
            return True
    return False


def write_structure_snapshot(
    layer_structure: StructureResult,
    state: EngineState,
    *,
    raw_bias: Side | None,
    bias_validation_score: int,
    structure_ok: bool,
    confluence_score: float,
    bias_window_phase: str,
    confluence_threshold_dynamic: float,
    evaluation_bias_snap: Side | None,
    can_trade_snap: bool | None = None,
    failure_zone_blocked_snap: bool = False,
    aligned_list: list[Signal] | None = None,
    chosen_signal: Signal | None = None,
) -> None:
    layer_structure.evaluated = True
    layer_structure.raw_bias_from_setup = raw_bias
    layer_structure.bias_validation_score = bias_validation_score
    layer_structure.structure_ok = structure_ok
    layer_structure.bias_structure_confluence = confluence_score
    layer_structure.bias_window_phase = bias_window_phase
    layer_structure.confluence_threshold_dynamic = confluence_threshold_dynamic
    layer_structure.bias_phase = state.bias_phase
    layer_structure.bias_confluence_threshold = state.bias_confluence_threshold
    layer_structure.current_bias = state.current_bias
    layer_structure.bias_strength = state.bias_strength
    layer_structure.bias_age_seconds = state.bias_age_seconds
    layer_structure.evaluation_bias = evaluation_bias_snap
    if can_trade_snap is not None:
        layer_structure.can_trade_bias = bool(can_trade_snap)
    layer_structure.failure_zone_blocked = failure_zone_blocked_snap
    if aligned_list is not None:
        layer_structure.bias_aligned_signals = list(aligned_list)
    if chosen_signal is not None:
        layer_structure.chosen_signal = chosen_signal


@dataclass(frozen=True)
class StructureContinue:
    signal: Signal
    evaluation_bias: Side
    bias_validation_score: int
    structure_ok: bool
    stability_score: float
    can_trade_bias: bool


@dataclass(frozen=True)
class StructureStepResult:
    """Either a structural halt proposal, or continuation payload for confirmations."""

    halt: FinishParams | None
    continuity: StructureContinue | None


def run_structure_analysis(
    *,
    candles: list[Candle],
    closed_i: int,
    current_time_s: float,
    state: EngineState,
    detection: DetectionBundle,
    layer_structure: StructureResult,
    regime_state_for_finish: Callable[[], str],
    symbol: str = "",
) -> StructureStepResult:
    """
    Bias BUILDING→CONFIRMED FSM and price-structure gates through chosen aligned Signal.
    `regime_state_for_finish` supplies regime_state fields for structural halt FinishParams (replay-stable).
    """
    # ─── Capture pre-state for BIAS_CHANGE event detection ────────────
    _prev_bias_phase = state.bias_phase
    _prev_bias = state.current_bias.value if state.current_bias else None
    _prev_bias_strength = state.bias_strength
    # ─── END pre-state capture ────────────────────────────────────────

    raw_bias = detection.raw_bias_from_setup
    bar_signals = detection.bar_signals
    pattern_names = detection.pattern_names
    bias_window_phase_str = detection.bias_window_phase
    confluence_threshold_dynamic = detection.confluence_threshold_dynamic

    selected_bias = state.current_bias if state.current_bias is not None else raw_bias
    bias_validation_score = 0
    structure_ok = False
    confluence_score = 0.0
    if selected_bias is not None:
        bias_validation_score, structure_ok, confluence_score = bias_metrics_for_side(
            candles,
            closed_i,
            selected_bias,
            bar_signals,
        )

    if state.bias_phase == "EXPIRED":
        if raw_bias is None:
            state.current_bias = None
            state.bias_confirmation_count = 0
            state.bias_contradiction_count = 0
            write_structure_snapshot(
                layer_structure,
                state,
                raw_bias=raw_bias,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                confluence_score=confluence_score,
                bias_window_phase=bias_window_phase_str,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                evaluation_bias_snap=None,
            )
            record_rejection("bias")
            return StructureStepResult(
                halt=FinishParams(
                    should_trade=False,
                    reason="bias_expired",
                    bias=None,
                    bias_phase=state.bias_phase,
                    bias_window_phase=bias_window_phase_str,
                    confluence_threshold_dynamic=confluence_threshold_dynamic,
                ),
                continuity=None,
            )
        state.current_bias = raw_bias
        state.bias_phase = "BUILDING"
        state.last_bias_time = current_time_s
        state.bias_age_seconds = 0.0
        state.bias_confirmation_count = 0
        state.bias_contradiction_count = 0
        state.bias_strength = max(state.bias_strength, 35.0)
        selected_bias = state.current_bias
        assert selected_bias is not None
        bias_validation_score, structure_ok, confluence_score = bias_metrics_for_side(
            candles,
            closed_i,
            selected_bias,
            bar_signals,
        )

    if state.bias_phase == "BUILDING":
        contradiction = raw_bias is not None and raw_bias != state.current_bias
        meets_building_gate = structure_ok and confluence_score >= state.bias_confluence_threshold

        if contradiction:
            state.bias_contradiction_count += 1
            state.bias_confirmation_count = 0
            state.bias_strength = max(0.0, state.bias_strength - 15.0)
        elif meets_building_gate:
            state.bias_confirmation_count += 1
            state.bias_contradiction_count = 0
            state.bias_strength = min(100.0, state.bias_strength + 12.0)
        else:
            state.bias_confirmation_count = 0
            state.bias_strength = max(0.0, state.bias_strength - 5.0)

        if (
            state.bias_confirmation_count >= state.bias_confirmation_required
            and state.bias_contradiction_count == 0
            and meets_building_gate
        ):
            state.bias_phase = "CONFIRMED"
            state.last_bias_time = current_time_s
            state.bias_age_seconds = 0.0
            state.bias_lock_until_candle = closed_i + state.bias_lock_candles
            state.bias_lock_until_time = current_time_s + state.bias_lock_seconds
            state.bias_strength = min(100.0, max(state.bias_strength, 65.0))
        else:
            write_structure_snapshot(
                layer_structure,
                state,
                raw_bias=raw_bias,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                confluence_score=confluence_score,
                bias_window_phase=bias_window_phase_str,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                evaluation_bias_snap=state.current_bias,
            )
            record_rejection("bias")
            return StructureStepResult(
                halt=FinishParams(
                    should_trade=False,
                    reason="bias_building",
                    bias=state.current_bias,
                    patterns=pattern_names,
                    bias_phase=state.bias_phase,
                    bias_validation_score=bias_validation_score,
                    structure_ok=structure_ok,
                    bias_strength=state.bias_strength,
                    bias_age_seconds=state.bias_age_seconds,
                    bias_window_phase=bias_window_phase_str,
                    confluence_threshold_dynamic=confluence_threshold_dynamic,
                    regime_state=regime_state_for_finish(),
                ),
                continuity=None,
            )

    can_trade_bias = state.bias_phase == "CONFIRMED"
    evaluation_bias = state.current_bias
    if state.bias_phase == "CONFIRMED":
        confirmed_side = state.current_bias
        assert confirmed_side is not None
        lock_active = closed_i < state.bias_lock_until_candle and current_time_s < state.bias_lock_until_time
        opposite_strength = 0.0
        if raw_bias is not None and raw_bias != confirmed_side:
            opposite_score, opposite_structure_ok, opposite_confluence = bias_metrics_for_side(
                candles,
                closed_i,
                raw_bias,
                bar_signals,
            )
            if opposite_structure_ok and opposite_confluence >= state.bias_confluence_threshold:
                opposite_strength = min(100.0, float(opposite_score * 20))

        too_old = state.bias_age_seconds >= state.bias_expiry_seconds
        current_score, current_structure_ok, _ = bias_metrics_for_side(
            candles,
            closed_i,
            confirmed_side,
            bar_signals,
        )
        structural_break = not current_structure_ok and current_score <= 1
        invalidated = too_old or structural_break or opposite_strength > state.bias_opposite_strength_threshold

        if invalidated:
            evaluation_bias = state.current_bias
            state.bias_phase = "EXPIRED"
            state.current_bias = None
            state.bias_confirmation_count = 0
            state.bias_contradiction_count = 0
            state.bias_lock_until_candle = -1
            state.bias_lock_until_time = 0.0
            state.bias_strength = max(0.0, state.bias_strength - 20.0)
            can_trade_bias = False

        if lock_active:
            eb = evaluation_bias
            assert eb is not None
            bias_validation_score, structure_ok, _ = bias_metrics_for_side(
                candles,
                closed_i,
                eb,
                bar_signals,
            )

    write_structure_snapshot(
        layer_structure,
        state,
        raw_bias=raw_bias,
        bias_validation_score=bias_validation_score,
        structure_ok=structure_ok,
        confluence_score=confluence_score,
        bias_window_phase=bias_window_phase_str,
        confluence_threshold_dynamic=confluence_threshold_dynamic,
        evaluation_bias_snap=evaluation_bias,
        can_trade_snap=can_trade_bias,
    )

    stability_score = calculate_bias_stability_score(state, closed_i)
    layer_structure.stability_score = stability_score
    if stability_score < 0.35:
        record_rejection("bias")
        return StructureStepResult(
            halt=FinishParams(
                should_trade=False,
                reason="bias_unstable",
                bias=evaluation_bias,
                patterns=pattern_names,
                bias_phase=state.bias_phase,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                bias_strength=state.bias_strength,
                bias_age_seconds=state.bias_age_seconds,
                bias_window_phase=bias_window_phase_str,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                regime_state=regime_state_for_finish(),
            ),
            continuity=None,
        )

    current_close = candles[closed_i].close
    if in_recent_failure_zone(state, current_close, current_time_s):
        write_structure_snapshot(
            layer_structure,
            state,
            raw_bias=raw_bias,
            bias_validation_score=bias_validation_score,
            structure_ok=structure_ok,
            confluence_score=confluence_score,
            bias_window_phase=bias_window_phase_str,
            confluence_threshold_dynamic=confluence_threshold_dynamic,
            evaluation_bias_snap=evaluation_bias,
            can_trade_snap=can_trade_bias,
            failure_zone_blocked_snap=True,
        )
        record_rejection("pattern")
        return StructureStepResult(
            halt=FinishParams(
                should_trade=False,
                reason="setup_repeated_in_failure_zone",
                bias=evaluation_bias,
                patterns=pattern_names,
                bias_phase=state.bias_phase,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                bias_strength=state.bias_strength,
                bias_age_seconds=state.bias_age_seconds,
                bias_window_phase=bias_window_phase_str,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                regime_state=regime_state_for_finish(),
            ),
            continuity=None,
        )

    aligned = [s for s in bar_signals if evaluation_bias is not None and s.side == evaluation_bias]
    if not aligned:
        write_structure_snapshot(
            layer_structure,
            state,
            raw_bias=raw_bias,
            bias_validation_score=bias_validation_score,
            structure_ok=structure_ok,
            confluence_score=confluence_score,
            bias_window_phase=bias_window_phase_str,
            confluence_threshold_dynamic=confluence_threshold_dynamic,
            evaluation_bias_snap=evaluation_bias,
            can_trade_snap=can_trade_bias,
            aligned_list=[],
        )
        record_rejection("pattern")
        return StructureStepResult(
            halt=FinishParams(
                should_trade=False,
                reason="pattern_insufficient_strength",
                bias=evaluation_bias,
                patterns=pattern_names,
                bias_phase=state.bias_phase,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                bias_strength=state.bias_strength,
                bias_age_seconds=state.bias_age_seconds,
                bias_window_phase=bias_window_phase_str,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                regime_state=regime_state_for_finish(),
            ),
            continuity=None,
        )

    signal = sorted(aligned, key=lambda s: s.pattern)[0]
    write_structure_snapshot(
        layer_structure,
        state,
        raw_bias=raw_bias,
        bias_validation_score=bias_validation_score,
        structure_ok=structure_ok,
        confluence_score=confluence_score,
        bias_window_phase=bias_window_phase_str,
        confluence_threshold_dynamic=confluence_threshold_dynamic,
        evaluation_bias_snap=evaluation_bias,
        can_trade_snap=can_trade_bias,
        aligned_list=aligned,
        chosen_signal=signal,
    )

    assert evaluation_bias is not None

    return StructureStepResult(
        halt=None,
        continuity=StructureContinue(
            signal=signal,
            evaluation_bias=evaluation_bias,
            bias_validation_score=bias_validation_score,
            structure_ok=structure_ok,
            stability_score=stability_score,
            can_trade_bias=can_trade_bias,
        ),
    )
