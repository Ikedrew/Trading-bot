"""
SL/TP rule registry — maps pattern names to pure level-building functions.

No order placement. No MT5 access. Deterministic and stateless.
Adding a new pattern requires ONLY: adding a builder function + registering it in SLTP_RULES.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable

from data.mt5_data import Candle
from strategy.signals import Side, Signal
from patterns.ids import (
    HAMMER, HANGING_MAN, INVERTED_HAMMER, SHOOTING_STAR,
    BULLISH_ENGULFING, BEARISH_ENGULFING, TWEEZER_TOP, TWEEZER_BOTTOM,
    MORNING_STAR, EVENING_STAR,
    THREE_WHITE_SOLDIERS, THREE_BLACK_CROWS, THREE_INSIDE_UP, THREE_INSIDE_DOWN,
)

logger = logging.getLogger(__name__)


# ─── REJECTION REASONS (stable identifiers) ──────────────────────────────────

REJECT_INVALID_INDEX = "INVALID_INDEX"
REJECT_UNSUPPORTED_PATTERN = "UNSUPPORTED_PATTERN"
REJECT_ZERO_RISK_DISTANCE = "ZERO_RISK_DISTANCE"


# ─── LOG SEVERITY CLASSIFICATION ─────────────────────────────────────────────

# ERROR: system integrity problems (should not happen in normal operation)
_ERROR_REASONS = frozenset({REJECT_UNSUPPORTED_PATTERN, REJECT_INVALID_INDEX})

# WARNING: unexpected but survivable (spread distortion, geometry edge cases)
_WARNING_REASONS = frozenset({REJECT_ZERO_RISK_DISTANCE, "ZERO_RISK_AT_EXECUTION_ENTRY", "FIXED_LOT_ZERO"})

# DEBUG: expected high-frequency filtering (normal market conditions)
# Everything not in ERROR or WARNING defaults to DEBUG


# ─── LOG THROTTLING ──────────────────────────────────────────────────────────

_THROTTLE_SECONDS = 30.0  # Suppress repeated same-reason logs for this duration
_last_log_times: dict[str, float] = {}
_suppressed_counts: dict[str, int] = {}
_SUMMARY_INTERVAL_SECONDS = 300.0  # Emit aggregated summary every 5 minutes
_last_summary_time: float = 0.0


@dataclass(frozen=True)
class RiskRejection:
    """Structured rejection record for audit/observability."""
    reason: str
    pattern: str
    symbol: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _log_rejection(rejection: RiskRejection) -> None:
    """Emit structured rejection log with severity levels and throttling."""
    global _last_summary_time
    reason = rejection.reason
    now = _time.time()

    # Determine severity
    if reason in _ERROR_REASONS:
        log_fn = logger.error
    elif reason in _WARNING_REASONS:
        log_fn = logger.warning
    else:
        log_fn = logger.debug

    # Throttle: suppress repeated same-reason logs
    last_logged = _last_log_times.get(reason, 0.0)
    if now - last_logged < _THROTTLE_SECONDS:
        _suppressed_counts[reason] = _suppressed_counts.get(reason, 0) + 1
        return

    # Emit log (first occurrence or after throttle window)
    _last_log_times[reason] = now
    suppressed = _suppressed_counts.pop(reason, 0)
    suffix = f" (suppressed={suppressed})" if suppressed > 0 else ""
    log_fn(
        "[RISK_REJECTED] reason=%s pattern=%s symbol=%s metadata=%s%s",
        rejection.reason, rejection.pattern, rejection.symbol, rejection.metadata, suffix,
    )

    # Periodic aggregated summary
    if now - _last_summary_time >= _SUMMARY_INTERVAL_SECONDS:
        _last_summary_time = now
        _emit_rejection_summary()


def _emit_rejection_summary() -> None:
    """Emit periodic aggregated rejection summary."""
    # Import here to avoid circular dependency
    from risk.manager import get_rejection_metrics
    metrics = get_rejection_metrics()
    if metrics:
        parts = " ".join(f"{k}={v}" for k, v in sorted(metrics.items()))
        logger.info("[RISK_REJECTION_SUMMARY] %s", parts)


# ─── CONFIG ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LevelConfig:
    """Config values needed by SL/TP builders."""
    base_rr: float
    rr3_patterns: frozenset[str]
    sl_buffer: float
    min_rr: float


# ─── SL/TP BUILDER FUNCTIONS (pure, stateless) ───────────────────────────────

def _buy_low_buffer(signal: Signal, candle: Candle, cfg: LevelConfig) -> tuple[float, float] | None:
    """BUY pattern: SL at candle.low - buffer."""
    entry = candle.close
    sl = candle.low - cfg.sl_buffer
    rr = _compute_rr(signal.pattern, cfg)
    risk = entry - sl
    if risk <= 0:
        _log_rejection(RiskRejection(
            reason=REJECT_ZERO_RISK_DISTANCE, pattern=signal.pattern,
            metadata={"entry": entry, "sl": sl, "risk": risk, "side": "BUY"},
        ))
        return None
    return sl, entry + risk * rr


def _buy_low_no_buffer(signal: Signal, candle: Candle, cfg: LevelConfig) -> tuple[float, float] | None:
    """BUY pattern: SL at candle.low (no buffer)."""
    entry = candle.close
    sl = candle.low
    rr = _compute_rr(signal.pattern, cfg)
    risk = entry - sl
    if risk <= 0:
        _log_rejection(RiskRejection(
            reason=REJECT_ZERO_RISK_DISTANCE, pattern=signal.pattern,
            metadata={"entry": entry, "sl": sl, "risk": risk, "side": "BUY"},
        ))
        return None
    return sl, entry + risk * rr


def _sell_high_buffer(signal: Signal, candle: Candle, cfg: LevelConfig) -> tuple[float, float] | None:
    """SELL pattern: SL at candle.high + buffer."""
    entry = candle.close
    sl = candle.high + cfg.sl_buffer
    rr = _compute_rr(signal.pattern, cfg)
    risk = sl - entry
    if risk <= 0:
        _log_rejection(RiskRejection(
            reason=REJECT_ZERO_RISK_DISTANCE, pattern=signal.pattern,
            metadata={"entry": entry, "sl": sl, "risk": risk, "side": "SELL"},
        ))
        return None
    return sl, entry - risk * rr


def _sell_high_no_buffer(signal: Signal, candle: Candle, cfg: LevelConfig) -> tuple[float, float] | None:
    """SELL pattern: SL at candle.high (no buffer)."""
    entry = candle.close
    sl = candle.high
    rr = _compute_rr(signal.pattern, cfg)
    risk = sl - entry
    if risk <= 0:
        _log_rejection(RiskRejection(
            reason=REJECT_ZERO_RISK_DISTANCE, pattern=signal.pattern,
            metadata={"entry": entry, "sl": sl, "risk": risk, "side": "SELL"},
        ))
        return None
    return sl, entry - risk * rr


def _compute_rr(pattern: str, cfg: LevelConfig) -> float:
    """Compute effective RR for a pattern."""
    pattern_rr = 3.0 if pattern in cfg.rr3_patterns else cfg.base_rr
    return max(float(cfg.min_rr), float(pattern_rr))


# ─── SL/TP RULE REGISTRY ─────────────────────────────────────────────────────

SltpBuilder = Callable[[Signal, Candle, LevelConfig], tuple[float, float] | None]

SLTP_RULES: dict[str, SltpBuilder] = {
    # BUY patterns
    BULLISH_ENGULFING: _buy_low_no_buffer,
    TWEEZER_BOTTOM: _buy_low_buffer,
    MORNING_STAR: _buy_low_buffer,
    THREE_WHITE_SOLDIERS: _buy_low_no_buffer,
    THREE_INSIDE_UP: _buy_low_buffer,
    HAMMER: _buy_low_buffer,
    INVERTED_HAMMER: _buy_low_buffer,
    # SELL patterns
    BEARISH_ENGULFING: _sell_high_no_buffer,
    TWEEZER_TOP: _sell_high_buffer,
    EVENING_STAR: _sell_high_buffer,
    THREE_BLACK_CROWS: _sell_high_no_buffer,
    THREE_INSIDE_DOWN: _sell_high_buffer,
    SHOOTING_STAR: _sell_high_buffer,
    HANGING_MAN: _sell_high_buffer,
}

SUPPORTED_PATTERNS: frozenset[str] = frozenset(SLTP_RULES.keys())


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def build_sl_tp(
    signal: Signal,
    candles: list[Candle],
    *,
    base_rr: float,
    rr3_patterns: frozenset[str],
    sl_buffer: float,
    min_rr: float,
) -> tuple[float, float] | None:
    """
    Returns (sl, tp) in price via registry lookup.
    Logs structured rejection for every failure path.
    """
    if signal.bar_index < 0 or signal.bar_index >= len(candles):
        _log_rejection(RiskRejection(
            reason=REJECT_INVALID_INDEX, pattern=signal.pattern,
            metadata={"bar_index": signal.bar_index, "candle_count": len(candles)},
        ))
        return None

    builder = SLTP_RULES.get(signal.pattern)
    if builder is None:
        _log_rejection(RiskRejection(
            reason=REJECT_UNSUPPORTED_PATTERN, pattern=signal.pattern,
            metadata={"supported_count": len(SLTP_RULES)},
        ))
        return None

    candle = candles[signal.bar_index]
    cfg = LevelConfig(
        base_rr=base_rr,
        rr3_patterns=rr3_patterns,
        sl_buffer=sl_buffer,
        min_rr=min_rr,
    )
    return builder(signal, candle, cfg)


# ─── RISK COVERAGE VALIDATION ─────────────────────────────────────────────────

def validate_risk_coverage(*, strict: bool = False) -> None:
    """
    Verify every registered pattern has a corresponding SL/TP rule.
    Call once at startup after patterns are loaded.
    """
    from patterns.registry import pattern_names, count

    registered = set(pattern_names())
    supported = set(SUPPORTED_PATTERNS)
    missing = registered - supported

    total_registered = count()
    total_supported = len(supported & registered)

    logger.info(
        "[RISK_COVERAGE] registered_patterns=%d supported_in_risk=%d missing=%d",
        total_registered, total_supported, len(missing),
    )

    if not missing:
        logger.info("[RISK_COVERAGE] COMPLETE — all patterns have SL/TP rules")
        return

    logger.warning(
        "[RISK_PATTERN_UNSUPPORTED] Missing SL/TP rules for: %s", missing,
    )

    if strict:
        raise RuntimeError(
            f"Risk coverage incomplete: {', '.join(sorted(missing))} missing SL/TP rules"
        )
