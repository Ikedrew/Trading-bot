"""Bar-level confirmation for a concrete Signal."""

from __future__ import annotations

from core.pipeline_types import ConfirmationResult as _PipelineConfirmationResult
from core.pipeline.filter_stats import pipeline_stats
from core.pipeline.dashboard import record_rejection
from data.mt5_data import Candle
from strategy.signal_orchestrator import confirm_signal, confirm_signal_detailed, ConfirmationStrength
from strategy.signals import Signal


def run_confirmations(*, signal: Signal, candles: list[Candle], layer_confirmation: _PipelineConfirmationResult, symbol: str = "") -> tuple[bool, str]:
    # Use detailed confirmation for graded quality information
    detail = confirm_signal_detailed(signal, candles)

    layer_confirmation.evaluated = True
    layer_confirmation.signal = signal
    layer_confirmation.passed = detail.confirmed
    layer_confirmation.reason = detail.reason
    layer_confirmation.strength = detail.strength.value
    layer_confirmation.body_pct = detail.body_pct
    layer_confirmation.wick_ratio = detail.wick_ratio
    layer_confirmation.close_location = detail.close_location

    pipeline_stats.record("confirmation", passed=detail.confirmed)
    if not detail.confirmed:
        record_rejection("confirmation")

    # ─── UNIFIED EVENT STREAM: PATTERN lifecycle (Layer 3) ────────────
    # Emit CONFIRMED or INVALIDATED as the next lifecycle step after DETECTED
    if symbol:
        try:
            from core.event_stream import emit_pattern_detected
            _lifecycle = "CONFIRMED" if detail.confirmed else "INVALIDATED"
            emit_pattern_detected(symbol, {
                "lifecycle": _lifecycle,
                "pattern": signal.pattern,
                "side": signal.side.value if signal.side else None,
                "bar_index": signal.bar_index,
                "confirmation_strength": detail.strength.value,
                "confirmation_reason": detail.reason,
                "body_pct": detail.body_pct,
                "wick_ratio": detail.wick_ratio,
                "close_location": detail.close_location,
            }, source="confirmations")
        except Exception:
            pass  # Event emission must never affect confirmation logic
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────────

    return detail.confirmed, detail.reason
