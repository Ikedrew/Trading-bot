"""Confluence tally, volatility penalisation, and score threshold veto (unchanged rules)."""

from __future__ import annotations

import logging
from typing import Any

from core.pipeline.finish_params import FinishParams
from core.pipeline.dashboard import record_rejection
from core.pipeline.structure_confidence import compute_structure_modifier
from core.pipeline_types import ScoreResult
from core.state.snapshot import StateSnapshot
from data.mt5_data import Candle
from strategy.signals import Side, Signal

_logger = logging.getLogger(__name__)


def calculate_confluence(signal: Signal, bias: Side | None, ema_ok: bool, chop_ok: bool, confirmed: bool, *, confirmation_strength: str = "STRONG") -> float:
    score = 2.0  # Bias alignment (mandatory gate already handled before scoring).

    strong_patterns = {
        "BULLISH_ENGULFING",
        "BEARISH_ENGULFING",
        "EVENING_STAR",
        "MORNING_STAR",
    }
    weak_patterns = {
        "THREE_BLACK_CROWS",
        "THREE_WHITE_SOLDIERS",
        "TWEEZER_TOP",
        "TWEEZER_BOTTOM",
        "HAMMER",
        "HANGING_MAN",
        "INVERTED_HAMMER",
        "SHOOTING_STAR",
    }

    if signal.pattern in strong_patterns:
        score += 2
    elif signal.pattern in weak_patterns:
        score += 1

    if ema_ok:
        score += 1

    # Graded confirmation bonus (replaces binary confirmed +1)
    _CONFIRMATION_WEIGHTS = {
        "STRONG": 1.0,
        "WEAK": 0.5,
        "INVALID": 0.0,
    }
    score += _CONFIRMATION_WEIGHTS.get(confirmation_strength, 1.0 if confirmed else 0.0)

    return score


def volatility_penalty(candles: list[Candle], closed_i: int) -> float:
    # [CALIBRATION TEST] Penalty reduced by 50% — revert after 100 cycles
    lookback = 5
    if closed_i < lookback:
        return 0.0

    recent = candles[closed_i - lookback : closed_i]
    sum_range = sum((c.high - c.low) for c in recent)
    if sum_range <= 0:
        return -1.5  # was -3.0

    net_move = abs(recent[-1].close - recent[0].open)
    ratio = net_move / sum_range

    if ratio < 0.15:
        return -1.5  # was -3.0
    if ratio < 0.25:
        return -1.0  # was -2.0
    if ratio < 0.35:
        return -0.5  # was -1.0
    return 0.0


def run_scoring_engine(
    *,
    signal: Signal,
    evaluation_bias: Side,
    trend_aligned: bool,
    candles: list[Candle],
    closed_i: int,
    snapshot: "StateSnapshot",
    config: Any,
    stability_score: float,
    bias_window_phase: str,
    confluence_threshold_dynamic: float,
    pattern_names: list[str],
    bias_validation_score: int,
    structure_ok: bool,
    layer_score: ScoreResult,
    regime_state: str,
    htf_score_adjustment: float = 0.0,
    htf_min_score_adjustment: float = 0.0,
    confirmation_strength: str = "STRONG",
    symbol: str = "",
) -> FinishParams | None:
    """Populate ScoreResult; return threshold-halt FinishParams when legacy would veto."""

    base_score = calculate_confluence(
        signal, evaluation_bias, trend_aligned, True, True,
        confirmation_strength=confirmation_strength,
    )
    vol_penalty = volatility_penalty(candles, closed_i)
    # NOTE: vol_penalty is stored in layer_score.volatility_penalty (below)
    # and applied to EngineState via StateDelta by the caller — NOT mutated here.
    bias_age_weight = max(0.7, 1.0 - min(snapshot.bias_age_seconds / 7200.0, 0.3))
    time_decay_multiplier = max(0.75, 1.0 - min(snapshot.bias_age_seconds / 10800.0, 0.25))
    regime_bonus = 0.5 if snapshot.last_strong_impulse_direction == snapshot.current_bias else 0.0
    sweep_bonus = 0.5 if (
        (snapshot.current_bias == Side.BUY and snapshot.last_sweep_low is not None)
        or (snapshot.current_bias == Side.SELL and snapshot.last_sweep_high is not None)
    ) else 0.0

    score = (base_score * bias_age_weight * time_decay_multiplier) + vol_penalty + regime_bonus + sweep_bonus

    # MTF: Apply higher-timeframe score adjustment
    score += htf_score_adjustment

    # Structure confidence modifier — OBSERVATIONAL ONLY (primary influence moved to ConfluenceEngine SWM)
    structure_modifier = compute_structure_modifier(
        snapshot.structure_score, snapshot.structure_regime
    )
    _logger.debug(
        "[STRUCTURE_MODIFIER_OBS] structure_score=%.3f structure_regime=%s "
        "modifier=%.3f (observational — not applied here, SWM in ConfluenceEngine is authoritative)",
        snapshot.structure_score,
        snapshot.structure_regime,
        structure_modifier,
    )

    score_int = int(score)
    breakdown: dict[str, float | str] = {
        "base_score": round(base_score, 3),
        "bias_age_weight": round(bias_age_weight, 3),
        "time_decay_multiplier": round(time_decay_multiplier, 3),
        "volatility_penalty": round(vol_penalty, 3),
        "regime_bonus": round(regime_bonus, 3),
        "sweep_bonus": round(sweep_bonus, 3),
        "structure_modifier": round(structure_modifier, 3),
        "confirmation_strength": confirmation_strength,
        "bias_window_phase": bias_window_phase,
        "confluence_threshold_dynamic": round(confluence_threshold_dynamic, 3),
        "final_score": round(score, 3),
    }

    min_score = max(float(getattr(config, "MIN_SCORE_TO_TRADE", 5)), confluence_threshold_dynamic)

    # MTF: Apply higher-timeframe minimum score threshold adjustment
    min_score += htf_min_score_adjustment

    soft_floor = 4.5
    high_stability_threshold = 0.8
    in_soft_zone = soft_floor <= score < min_score
    allow_soft_entry = in_soft_zone and stability_score >= high_stability_threshold

    layer_score.evaluated = True
    layer_score.base_score = base_score
    layer_score.volatility_penalty = vol_penalty
    layer_score.bias_age_weight = bias_age_weight
    layer_score.time_decay_multiplier = time_decay_multiplier
    layer_score.regime_bonus = regime_bonus
    layer_score.sweep_bonus = sweep_bonus
    layer_score.final_score = score
    layer_score.score_int = score_int
    layer_score.min_score_threshold = min_score
    layer_score.soft_floor = soft_floor
    layer_score.in_soft_zone = in_soft_zone
    layer_score.allow_soft_entry = allow_soft_entry
    layer_score.stability_at_score = stability_score
    layer_score.breakdown = breakdown

    # ─── UNIFIED EVENT STREAM: CONFLUENCE_SCORE (Layer 5) ─────────────
    # Full breakdown with contributing signals and explicit reasoning chain.
    # Enables: "which component drove the score?" and "why did it pass/fail?"
    try:
        from core.event_stream import emit_confluence_score
        _decision_reason = "passed_threshold"
        if score < min_score and not allow_soft_entry:
            _decision_reason = "below_threshold"
        elif allow_soft_entry:
            _decision_reason = "soft_entry_allowed"

        emit_confluence_score(symbol, {
            "score": round(score, 3),
            "score_int": score_int,
            "min_score_threshold": round(min_score, 3),
            "passed": score >= min_score or allow_soft_entry,
            "decision_reason": _decision_reason,
            "breakdown": breakdown,
            "contributing_signals": {
                "pattern": signal.pattern,
                "pattern_strength": "STRONG" if signal.pattern in {"BULLISH_ENGULFING", "BEARISH_ENGULFING", "EVENING_STAR", "MORNING_STAR"} else "WEAK",
                "side": signal.side.value if signal.side else None,
                "trend_aligned": trend_aligned,
                "confirmation_strength": confirmation_strength,
                "bias_direction": evaluation_bias.value if evaluation_bias else None,
                "bias_window_phase": bias_window_phase,
            },
            "modifiers": {
                "bias_age_weight": round(bias_age_weight, 3),
                "time_decay_multiplier": round(time_decay_multiplier, 3),
                "volatility_penalty": round(vol_penalty, 3),
                "regime_bonus": round(regime_bonus, 3),
                "sweep_bonus": round(sweep_bonus, 3),
                "htf_score_adjustment": round(htf_score_adjustment, 3),
                "structure_modifier_obs": round(structure_modifier, 3),
            },
            "stability_score": round(stability_score, 3),
            "soft_zone": in_soft_zone,
        }, source="scoring_engine")
    except Exception:
        pass  # Event emission must never affect scoring
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────────

    if score < min_score and not allow_soft_entry:
        layer_score.passed_threshold = False
        record_rejection("score")
        return FinishParams(
            should_trade=False,
            reason=f"confluence_score_below_threshold (score={score:.2f})",
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

    layer_score.passed_threshold = True
    return None

