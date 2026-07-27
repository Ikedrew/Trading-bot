"""Elapsed time, regime memory snapshots, optional pre-bias chop market filter."""

from __future__ import annotations

from typing import Any

from core.config import CHOP_FILTER_ENABLED
from core.engine_state import EngineState
from core.pipeline_types import ContextResult
from core.pipeline.filter_stats import pipeline_stats
from core.pipeline.dashboard import record_rejection
from data.mt5_data import Candle
from strategy.market_filter import passes_market_filter
from strategy.signals import Side


def update_market_memory(state: EngineState, candles: list[Candle], closed_i: int) -> None:
    lookback = 5
    if closed_i < lookback:
        return

    window = candles[closed_i - lookback : closed_i]
    current = candles[closed_i]
    max_high = max(c.high for c in window)
    min_low = min(c.low for c in window)

    if current.high > max_high:
        state.last_sweep_high = current.high
    if current.low < min_low:
        state.last_sweep_low = current.low

    net_move = abs(current.close - window[0].open)
    sum_range = sum((c.high - c.low) for c in window)
    ratio = (net_move / sum_range) if sum_range > 0 else 0.0

    if ratio > 0.45:
        if current.close > window[0].open:
            state.last_strong_impulse_direction = Side.BUY
            state.regime_state = "TREND_UP"
        else:
            state.last_strong_impulse_direction = Side.SELL
            state.regime_state = "TREND_DOWN"
    elif ratio < 0.2:
        state.regime_state = "RANGING"


def run_market_context(
    *,
    candles: list[Candle],
    closed_i: int,
    state: EngineState,
    config: Any,
    current_time_s: float,
    chop_filter_enabled_fallback: bool,
    layer_context: ContextResult,
    symbol: str = "",
) -> str | None:
    """
    Mirrors former process_bar prelude: clock, bias decay, memory, ContextResult fields,
    passes_market_filter when enabled.

    Returns a veto reason string to short-circuit the bar (legacy finish(False, reason)),
    or None to continue.
    """
    prev_time_s = state.current_time
    state.current_time = current_time_s
    elapsed_s = 0.0 if prev_time_s <= 0 else max(0.0, current_time_s - prev_time_s)

    if state.current_bias is not None:
        state.bias_age_seconds += elapsed_s
        state.bias_strength = max(
            0.0,
            state.bias_strength - (state.bias_decay_rate * max(1.0, elapsed_s / 60.0)),
        )

    update_market_memory(state, candles, closed_i)

    layer_context.evaluated = True
    layer_context.elapsed_s = elapsed_s
    layer_context.regime_state = state.regime_state
    layer_context.last_sweep_high = state.last_sweep_high
    layer_context.last_sweep_low = state.last_sweep_low
    layer_context.last_strong_impulse_direction = state.last_strong_impulse_direction

    chop_cfg = getattr(config, "CHOP_FILTER_ENABLED", chop_filter_enabled_fallback)
    if chop_cfg:
        allowed, mf_reason = passes_market_filter(
            candles,
            closed_i,
            lookback_bars=config.MARKET_FILTER_LOOKBACK,
            min_sum_range=config.MIN_SUM_RANGE_5BARS,
            chop_net_move_ratio=config.CHOP_NET_MOVE_RATIO,
        )
        layer_context.market_filter_checked = True
        layer_context.market_filter_passed = allowed
        layer_context.market_filter_reason = mf_reason
        pipeline_stats.record("market_filter", passed=allowed)
        if not allowed:
            record_rejection("market_filter")
            # ─── RISK_CHECK: market context veto ──────────────────────
            try:
                from core.event_stream import emit_risk_check
                emit_risk_check(symbol, {
                    "result": "REJECTED",
                    "guard": "market_context",
                    "reason": mf_reason,
                    "layer": "MARKET_FILTER",
                    "regime_state": state.regime_state,
                    "chop_filter_enabled": True,
                    "closed_i": closed_i,
                }, source="market_context")
            except Exception:
                pass
            # ─── END RISK_CHECK ───────────────────────────────────────
            return mf_reason
    else:
        layer_context.market_filter_checked = False
        layer_context.market_filter_passed = None
        layer_context.market_filter_reason = ""

    return None
