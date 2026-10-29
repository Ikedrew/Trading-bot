"""RiskManager OrderIntent assembly — pure evaluation, mutations via StateDelta."""

from __future__ import annotations

from core.pipeline.finish_params import FinishParams
from core.pipeline_types import QualityResult
from core.state.delta import StateDelta
from core.state.snapshot import StateSnapshot
from data.mt5_data import Candle
from risk.manager import RiskManager
from strategy.signals import Signal, Side


def run_build_intent(
    *,
    risk: RiskManager,
    symbol: str,
    signal: Signal,
    candles: list[Candle],
    closed_i: int,
    bid: float,
    ask: float,
    current_time_s: float,
    snapshot: StateSnapshot,
    delta: StateDelta,
    evaluation_bias: Side | None,
    pattern_names: list[str],
    score_int: int,
    bias_validation_score: int,
    structure_ok: bool,
    bias_window_phase: str,
    confluence_threshold_dynamic: float,
    breakdown: dict[str, float | str] | None,
    regime_state: str,
    layer_quality: QualityResult,
    confirmation_strength: str | None = None,
    confirmation_body_pct: float | None = None,
    confirmation_wick_ratio: float | None = None,
    confirmation_close_location: float | None = None,
) -> FinishParams:
    layer_quality.intent_attempted = True
    intent = risk.build_intent(symbol, signal, candles, bid, ask)
    if intent is None:
        zone = (candles[closed_i].low, candles[closed_i].high)
        delta.last_rejection_zone = zone
        delta.failed_setup = (current_time_s, zone[0], zone[1], "risk_reject")
        layer_quality.intent_built_ok = False
        return FinishParams(
            should_trade=False,
            reason="risk_reject",
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
            confirmation_strength=confirmation_strength,
            confirmation_body_pct=confirmation_body_pct,
            confirmation_wick_ratio=confirmation_wick_ratio,
            confirmation_close_location=confirmation_close_location,
        )

    layer_quality.intent_built_ok = True
    delta.last_trade_side = signal.side.value
    delta.last_trade_bar = closed_i
    delta.bias_age_increment = 1
    return FinishParams(
        should_trade=True,
        reason="ok",
        signal=signal,
        intent=intent,
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
        confirmation_strength=confirmation_strength,
        confirmation_body_pct=confirmation_body_pct,
        confirmation_wick_ratio=confirmation_wick_ratio,
        confirmation_close_location=confirmation_close_location,
    )
