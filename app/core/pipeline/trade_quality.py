"""Post-confirmation chop/cooldown/trend gates and post-score exposure guards (unchanged)."""

from __future__ import annotations

from typing import Any

from core.pipeline.finish_params import FinishParams
from core.pipeline.filter_stats import pipeline_stats
from core.pipeline.dashboard import record_rejection
from core.pipeline_types import QualityResult
from core.state.snapshot import StateSnapshot
from data.mt5_data import Candle
from risk.guards import count_bot_positions
from strategy.chop_filter import passes_chop_filter
from strategy.signals import Signal, Side
from strategy.trend_filter import passes_trend_filter


def run_trade_quality_after_confirmation(
    *,
    candles: list[Candle],
    closed_i: int,
    config: Any,
    snapshot: StateSnapshot,
    signal: Signal,
    evaluation_bias: Side | None,
    pattern_names: list[str],
    bias_validation_score: int,
    structure_ok: bool,
    bias_window_phase: str,
    confluence_threshold_dynamic: float,
    layer_quality: QualityResult,
    chop_filter_enabled_fallback: bool,
    trend_filter_enabled_fallback: bool,
    regime_state: str,
) -> tuple[FinishParams | None, bool]:
    layer_quality.evaluated = True

    trend_aligned = True

    chop_cfg = getattr(config, "CHOP_FILTER_ENABLED", chop_filter_enabled_fallback)
    if chop_cfg:
        tradable = passes_chop_filter(
            candles,
            closed_i,
            lookback_bars=int(config.MARKET_FILTER_LOOKBACK),
            min_sum_range=float(config.MIN_SUM_RANGE_5BARS),
            chop_net_move_ratio=float(config.CHOP_NET_MOVE_RATIO),
            max_overlap_ratio=float(config.CHOP_OVERLAP_RATIO_MAX),
        )
        layer_quality.post_confirm_chop_filter_checked = True
        layer_quality.post_confirm_chop_filter_passed = tradable
        pipeline_stats.record("chop_filter", passed=tradable)
        if not tradable:
            record_rejection("chop")
            return (
                FinishParams(
                    should_trade=False,
                    reason="chop_filter",
                    signal=signal,
                    intent=None,
                    bias=evaluation_bias,
                    patterns=pattern_names,
                    score=0,
                    bias_phase=snapshot.bias_phase,
                    bias_validation_score=bias_validation_score,
                    structure_ok=structure_ok,
                    bias_strength=snapshot.bias_strength,
                    bias_age_seconds=snapshot.bias_age_seconds,
                    bias_window_phase=bias_window_phase,
                    confluence_threshold_dynamic=confluence_threshold_dynamic,
                    regime_state=regime_state,
                ),
                True,
            )

    if snapshot.last_trade_side == signal.side.value and snapshot.last_trade_bar is not None:
        if (closed_i - snapshot.last_trade_bar) < 3:
            layer_quality.direction_cooldown_veto = True
            record_rejection("direction_cooldown")
            return (
                FinishParams(
                    should_trade=False,
                    reason="direction_cooldown",
                    signal=signal,
                    intent=None,
                    bias=evaluation_bias,
                    patterns=pattern_names,
                    score=0,
                    bias_phase=snapshot.bias_phase,
                    bias_validation_score=bias_validation_score,
                    structure_ok=structure_ok,
                    bias_strength=snapshot.bias_strength,
                    bias_age_seconds=snapshot.bias_age_seconds,
                    bias_window_phase=bias_window_phase,
                    confluence_threshold_dynamic=confluence_threshold_dynamic,
                    regime_state=regime_state,
                ),
                trend_aligned
            )

    trend_cfg = getattr(config, "TREND_FILTER_ENABLED", trend_filter_enabled_fallback)
    if trend_cfg:
        ema_period = int(getattr(config, "TREND_EMA_PERIOD", 50))
        trend_aligned = passes_trend_filter(signal, candles, closed_i, period=ema_period)
        layer_quality.trend_filter_checked = True
        layer_quality.trend_aligned = trend_aligned
        pipeline_stats.record("trend_filter", passed=trend_aligned)
        if not trend_aligned:
            record_rejection("trend")
            return (
                FinishParams(
                    should_trade=False,
                    reason="trend_filter",
                    signal=signal,
                    intent=None,
                    bias=evaluation_bias,
                    patterns=pattern_names,
                    score=0,
                    bias_phase=snapshot.bias_phase,
                    bias_validation_score=bias_validation_score,
                    structure_ok=structure_ok,
                    bias_strength=snapshot.bias_strength,
                    bias_age_seconds=snapshot.bias_age_seconds,
                    bias_window_phase=bias_window_phase,
                    confluence_threshold_dynamic=confluence_threshold_dynamic,
                    regime_state=regime_state,
                ),
                trend_aligned,
            )

    return None, trend_aligned


def run_trade_quality_after_scoring(
    *,
    symbol: str,
    config: Any,
    current_time_s: float,
    snapshot: StateSnapshot,
    signal: Signal,
    evaluation_bias: Side | None,
    pattern_names: list[str],
    bias_validation_score: int,
    structure_ok: bool,
    bias_window_phase: str,
    confluence_threshold_dynamic: float,
    score_int: int,
    breakdown: dict[str, float | str] | None,
    can_trade_bias: bool,
    layer_quality: QualityResult,
    regime_state: str,
) -> FinishParams | None:
    if count_bot_positions(symbol, config.BOT_MAGIC) >= config.MAX_OPEN_POSITIONS:
        layer_quality.max_positions_blocked = True
        record_rejection("max_positions")
        return FinishParams(
            should_trade=False,
            reason="max_positions",
            signal=signal,
            intent=None,
            bias=evaluation_bias,
            patterns=pattern_names,
            score=score_int,
            bias_phase=snapshot.bias_phase,
            bias_validation_score=bias_validation_score,
            structure_ok=structure_ok,
            bias_strength=snapshot.bias_strength,
            bias_age_seconds=snapshot.bias_age_seconds,
            bias_window_phase=bias_window_phase,
            confluence_threshold_dynamic=confluence_threshold_dynamic,
            regime_state=regime_state,
            confluence_breakdown=breakdown,
        )

    if snapshot.last_successful_open_mono:
        if current_time_s - snapshot.last_successful_open_mono < config.COOLDOWN_SECONDS:
            layer_quality.cooldown_blocked = True
            record_rejection("cooldown")
            return FinishParams(
                should_trade=False,
                reason="cooldown",
                signal=signal,
                intent=None,
                bias=evaluation_bias,
                patterns=pattern_names,
                score=score_int,
                bias_phase=snapshot.bias_phase,
                bias_validation_score=bias_validation_score,
                structure_ok=structure_ok,
                bias_strength=snapshot.bias_strength,
                bias_age_seconds=snapshot.bias_age_seconds,
                bias_window_phase=bias_window_phase,
                confluence_threshold_dynamic=confluence_threshold_dynamic,
                regime_state=regime_state,
                confluence_breakdown=breakdown,
            )

    if not can_trade_bias or evaluation_bias is None:
        layer_quality.can_trade_bias_blocked = True
        record_rejection("bias_expired")
        return FinishParams(
            should_trade=False,
            reason="bias_expired",
            signal=signal,
            intent=None,
            bias=evaluation_bias,
            patterns=pattern_names,
            score=score_int,
            bias_phase=snapshot.bias_phase,
            bias_validation_score=bias_validation_score,
            structure_ok=structure_ok,
            bias_strength=snapshot.bias_strength,
            bias_age_seconds=snapshot.bias_age_seconds,
            bias_window_phase=bias_window_phase,
            confluence_threshold_dynamic=confluence_threshold_dynamic,
            regime_state=regime_state,
            confluence_breakdown=breakdown,
        )

    return None
