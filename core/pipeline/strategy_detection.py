"""Raw directional setup + candle pattern enumeration for the closed bar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.engine_state import EngineState
from core.pipeline.bias_thresholds import bias_window_phase, dynamic_confluence_threshold
from core.pipeline_types import PatternResult
from data.mt5_data import Candle
from strategy.signal_orchestrator import detect_pattern
from strategy.setup import setup_bias
from strategy.signals import Signal, Side


@dataclass
class DetectionBundle:
    raw_bias_from_setup: Side | None
    bar_signals: list[Signal]
    pattern_names: list[str]
    bias_window_phase: str
    confluence_threshold_dynamic: float


def run_strategy_detection(
    *,
    candles: list[Candle],
    closed_i: int,
    config: Any,
    state: EngineState,
    layer_pattern: PatternResult,
    symbol: str = "",
) -> DetectionBundle:
    """Hydrate bias knobs from config, compute windows, populate PatternResult."""

    raw_bias = setup_bias(
        candles,
        closed_i,
        ma_period=config.SETUP_MA_PERIOD,
        min_distance_from_ma=config.SETUP_MIN_DISTANCE_FROM_MA,
    )

    bar_signals = detect_pattern(candles, closed_i)
    pattern_names = [s.pattern for s in bar_signals]

    state.bias_confirmation_required = int(getattr(config, "BIAS_CONFIRMATION_CANDLES", state.bias_confirmation_required))
    state.bias_confluence_threshold = float(getattr(config, "BIAS_CONFLUENCE_THRESHOLD", state.bias_confluence_threshold))
    state.bias_lock_candles = int(getattr(config, "BIAS_LOCK_CANDLES", state.bias_lock_candles))
    state.bias_lock_seconds = float(getattr(config, "BIAS_LOCK_SECONDS", state.bias_lock_seconds))
    state.bias_expiry_seconds = float(getattr(config, "BIAS_EXPIRY_SECONDS", state.bias_expiry_seconds))
    state.bias_opposite_strength_threshold = float(
        getattr(config, "BIAS_OPPOSITE_STRENGTH_THRESHOLD", state.bias_opposite_strength_threshold)
    )

    bwp = bias_window_phase(state.bias_age_seconds, state.bias_expiry_seconds)
    ctd = dynamic_confluence_threshold(
        state.bias_confluence_threshold,
        state.bias_age_seconds,
        state.bias_expiry_seconds,
    )

    layer_pattern.evaluated = True
    layer_pattern.raw_bias_from_setup = raw_bias
    layer_pattern.signals = list(bar_signals)
    layer_pattern.pattern_names = list(pattern_names)

    # ─── UNIFIED EVENT STREAM: PATTERN_DETECTED (Layer 3) ─────────────
    # Lifecycle state: DETECTED — pattern found on closed bar.
    # Will be followed by CONFIRMED or INVALIDATED from confirmations stage.
    if bar_signals:
        try:
            from core.event_stream import emit_pattern_detected
            for _sig in bar_signals:
                emit_pattern_detected(symbol, {
                    "lifecycle": "DETECTED",
                    "pattern": _sig.pattern,
                    "side": _sig.side.value if _sig.side else None,
                    "bar_index": _sig.bar_index if hasattr(_sig, 'bar_index') else closed_i,
                    "bar_time": _sig.bar_time if hasattr(_sig, 'bar_time') else None,
                    "confidence": _sig.confidence if hasattr(_sig, 'confidence') else 1.0,
                    "raw_bias": raw_bias.value if raw_bias else None,
                    "bias_alignment": (raw_bias == _sig.side) if raw_bias else False,
                    "total_patterns_on_bar": len(bar_signals),
                    "closed_i": closed_i,
                }, source="strategy_detection")
        except Exception:
            pass  # Event emission must never affect detection
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────────

    return DetectionBundle(
        raw_bias_from_setup=raw_bias,
        bar_signals=bar_signals,
        pattern_names=pattern_names,
        bias_window_phase=bwp,
        confluence_threshold_dynamic=ctd,
    )
