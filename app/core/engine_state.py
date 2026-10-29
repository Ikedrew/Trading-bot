"""Mutable runtime state carried across bars (replay / live parity)."""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

from strategy.signals import Side

logger = logging.getLogger(__name__)

# Valid bias phases (state machine states)
_VALID_BIAS_PHASES = frozenset({"EXPIRED", "BUILDING", "CONFIRMED", "LOCKED"})

# Valid regime states (structural + directional variants)
_VALID_REGIME_STATES = frozenset({
    "RANGING", "TRENDING", "VOLATILE", "CHOPPY",
    "TREND_UP", "TREND_DOWN",
})


# ─── REGIME NORMALIZATION ─────────────────────────────────────────────────────

def normalize_regime(regime: str) -> tuple[str, str | None]:
    """
    Normalize a regime string into (market_structure, market_direction).

    Mappings:
        TREND_UP    → ("TRENDING", "UP")
        TREND_DOWN  → ("TRENDING", "DOWN")
        TRENDING    → ("TRENDING", None)
        RANGING     → ("RANGING", None)
        VOLATILE    → ("VOLATILE", None)
        CHOPPY      → ("CHOPPY", None)

    Returns ("RANGING", None) for unknown values (safe fallback).
    """
    if regime == "TREND_UP":
        return "TRENDING", "UP"
    if regime == "TREND_DOWN":
        return "TRENDING", "DOWN"
    if regime in ("TRENDING", "RANGING", "VOLATILE", "CHOPPY"):
        return regime, None
    logger.warning("[REGIME_NORMALIZE] unknown regime=%s defaulting to RANGING", regime)
    return "RANGING", None


@dataclass
class EngineState:
    last_successful_open_mono: float | None = None
    last_trade_side: str | None = None
    last_trade_bar: int | None = None

    current_bias: Side | None = None
    bias_age: int = 0
    bias_phase: str = "EXPIRED"
    bias_flip_bars: deque[int] | None = None
    bias_strength: float = 0.0
    bias_confirmation_score: float = 0.0
    bias_decay_rate: float = 4.0
    bias_confirmation_required: int = 3
    bias_confluence_threshold: float = 4.0
    bias_lock_candles: int = 3
    bias_lock_seconds: float = 900.0
    bias_expiry_seconds: float = 7200.0
    bias_opposite_strength_threshold: float = 60.0
    bias_confirmation_count: int = 0
    bias_contradiction_count: int = 0
    volatility_filter: float = 0.0
    bias_lock_until_candle: int = -1
    bias_lock_until_time: float = 0.0
    current_time: float = 0.0
    last_bias_time: float | None = None
    bias_age_seconds: float = 0.0
    regime_state: str = "RANGING"
    last_sweep_high: float | None = None
    last_sweep_low: float | None = None
    last_rejection_zone: tuple[float, float] | None = None
    last_failed_setups: deque[tuple[float, float, float, str]] | None = None
    last_strong_impulse_direction: Side | None = None

    # ─── DIVERGENCE DETECTION (bias FSM vs price reality) ─────────────────────
    divergence_flag: bool = False
    divergence_strength: int = 0
    divergence_streak: int = 0
    last_price_direction: str | None = None

    # ─── FLIP COOLDOWN (post-flip stabilisation window) ───────────────────────
    flip_cooldown_bars: int = 0
    cooldown_active: bool = False
    cooldown_mode: str = "NONE"         # "NONE" / "HARD" / "SOFT"

    # ─── REGIME CLASSIFICATION (read-only metadata) ───────────────────────────
    regime_label: str = "CHOPPING"      # TRENDING_STABLE / TRENDING_WEAKENING / CHOPPING / TRANSITIONAL / POST_FLIP_RECOVERY

    # ─── STRUCTURE COHESION SCORING (parallel to FSM, does not replace yet) ───
    structure_buffer: deque[float] | None = None
    structure_score: float = 0.0
    structure_regime: str = "WEAK"

    def __post_init__(self) -> None:
        if self.bias_flip_bars is None:
            self.bias_flip_bars = deque(maxlen=50)
        if self.last_failed_setups is None:
            self.last_failed_setups = deque(maxlen=20)
        if self.structure_buffer is None:
            from core.pipeline.structure_scoring import STRUCTURE_BUFFER_SIZE
            self.structure_buffer = deque(maxlen=STRUCTURE_BUFFER_SIZE)


# ─── NaN/Inf REPAIR UTILITY ───────────────────────────────────────────────────

def _repair_float(value: float, default: float = 0.0) -> float:
    """Return default if value is None, NaN, or Inf. Otherwise return value unchanged."""
    if value is None:
        return default
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return default
    return value


# ─── ENGINE STATE VALIDATION ──────────────────────────────────────────────────

def validate_engine_state(state: EngineState, *, symbol: str = "", cycle_id: int = 0, strict: bool = False) -> bool:
    """
    Validate and REPAIR EngineState invariants before bar processing.
    Returns True if valid (or repaired), False if unrecoverable issues detected.

    In non-strict mode: detects NaN/Inf in numeric fields and repairs them to safe
    defaults, preventing corrupted values from propagating into scoring.

    If strict=True, raises ValueError on first violation (no repair).

    O(1) checks only — no iteration over candle data.
    """
    issues: list[str] = []
    repaired = False

    # 1. State must not be None (caller responsibility, but guard anyway)
    if state is None:
        issues.append("state is None")
        if strict:
            raise ValueError(f"[ENGINE_STATE_INVALID] symbol={symbol} cycle={cycle_id} reason=state_is_None")
        logger.warning("[ENGINE_STATE_INVALID] symbol=%s cycle=%d reason=state_is_None", symbol, cycle_id)
        return False

    # 2. bias_phase must be a known value
    if state.bias_phase not in _VALID_BIAS_PHASES:
        issues.append(f"bias_phase={state.bias_phase!r} not in {_VALID_BIAS_PHASES}")

    # 3. regime_state must be a known value
    if state.regime_state not in _VALID_REGIME_STATES:
        issues.append(f"regime_state={state.regime_state!r} not in {_VALID_REGIME_STATES}")

    # 4. bias_strength — repair NaN/Inf, flag negative
    original = state.bias_strength
    state.bias_strength = _repair_float(state.bias_strength, 0.0)
    if state.bias_strength != original:
        issues.append(f"bias_strength={original} was NaN/Inf — repaired to 0.0")
        repaired = True
    elif state.bias_strength < 0.0:
        issues.append(f"bias_strength={state.bias_strength} is negative")

    # 5. bias_age_seconds — repair NaN/Inf, flag negative
    original = state.bias_age_seconds
    state.bias_age_seconds = _repair_float(state.bias_age_seconds, 0.0)
    if state.bias_age_seconds != original:
        issues.append(f"bias_age_seconds={original} was NaN/Inf — repaired to 0.0")
        repaired = True
    elif state.bias_age_seconds < 0.0:
        issues.append(f"bias_age_seconds={state.bias_age_seconds} is negative")

    # 6. current_bias must be None or a valid Side enum
    if state.current_bias is not None and not isinstance(state.current_bias, Side):
        issues.append(f"current_bias={state.current_bias!r} is not None or Side")

    # 7. last_strong_impulse_direction must be None or valid Side
    if state.last_strong_impulse_direction is not None and not isinstance(state.last_strong_impulse_direction, Side):
        issues.append(f"last_strong_impulse_direction={state.last_strong_impulse_direction!r} invalid")

    # 8. Impossible combination: bias_phase CONFIRMED but current_bias is None
    if state.bias_phase == "CONFIRMED" and state.current_bias is None:
        issues.append("bias_phase=CONFIRMED but current_bias=None")

    # 9. volatility_filter — repair NaN/Inf
    original = state.volatility_filter
    state.volatility_filter = _repair_float(state.volatility_filter, 0.0)
    if state.volatility_filter != original:
        issues.append(f"volatility_filter={original} was NaN/Inf — repaired to 0.0")
        repaired = True

    # 10. bias_confirmation_score — repair NaN/Inf
    original = state.bias_confirmation_score
    state.bias_confirmation_score = _repair_float(state.bias_confirmation_score, 0.0)
    if state.bias_confirmation_score != original:
        issues.append(f"bias_confirmation_score={original} was NaN/Inf — repaired to 0.0")
        repaired = True

    # Emit repair log if any fields were corrected
    if repaired:
        logger.warning(
            "[ENGINE_STATE_REPAIRED] symbol=%s cycle=%d fields_repaired=%s",
            symbol, cycle_id, "; ".join(i for i in issues if "repaired" in i),
        )

    if not issues:
        return True

    # Report non-repair issues
    non_repair_issues = [i for i in issues if "repaired" not in i]
    if non_repair_issues:
        reason = "; ".join(non_repair_issues)
        if strict:
            raise ValueError(f"[ENGINE_STATE_INVALID] symbol={symbol} cycle={cycle_id} reason={reason}")
        logger.warning("[ENGINE_STATE_INVALID] symbol=%s cycle=%d reason=%s", symbol, cycle_id, reason)

    # If only repairs were needed (no structural issues), state is now valid
    if not non_repair_issues:
        return True

    return False