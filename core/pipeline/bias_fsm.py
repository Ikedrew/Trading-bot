"""
Bias FSM — Multi-regime directional state machine with divergence + cooldown.

Single authority for writing bias state on sym_state.engine_state.

Lifecycle: EXPIRED → FORMING → CONFIRMING → CONFIRMED → WEAKENING → EXPIRED

Dual cooldown:
    HARD — post-flip stability window (all flips blocked)
    SOFT — regime instability mode (flips allowed with elevated threshold)

Regime classification (read-only metadata, does NOT influence scoring):
    TRENDING_STABLE / TRENDING_WEAKENING / CHOPPING / TRANSITIONAL / POST_FLIP_RECOVERY

Architecture:
    - Phase 3.6 (after scoring/ranking, before execution)
    - Writes ONLY: engine_state bias + divergence + cooldown + regime fields
    - Does NOT influence scoring, EV, ranking, or execution logic
    - Uses ONLY: raw price + FSM state (no circular feedback)

Design: deterministic, no learning, no adaptation.
"""

from __future__ import annotations

from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal, Side


# ─── FSM PARAMETERS ───────────────────────────────────────────────────────────

_CONFIRMATION_BARS_REQUIRED = 3
_CONFIRMING_BARS_REQUIRED = 2
_STRENGTH_INCREMENT = 15.0
_STRENGTH_DECAY_PER_BAR = 3.0
_STRENGTH_REINFORCEMENT = 8.0
_WEAKNESS_THRESHOLD = 25.0
_EXPIRY_THRESHOLD = 10.0
_FLIP_THRESHOLD = 2
_MAX_STRENGTH = 100.0
_INITIAL_STRENGTH = 35.0

# ─── DIVERGENCE PARAMETERS ────────────────────────────────────────────────────

_DIVERGENCE_SOFT_BARS = 3
_DIVERGENCE_HARD_BARS = 5
_DIVERGENCE_FLIP_THRESHOLD = 7
_DIVERGENCE_STRENGTH_REQUIRED = 50.0
_DIVERGENCE_DECAY_ON_AGREEMENT = 2

# ─── COOLDOWN PARAMETERS ──────────────────────────────────────────────────────

_HARD_COOLDOWN_MIN = 5
_HARD_COOLDOWN_MAX = 10
_SOFT_COOLDOWN_BARS = 8             # Duration of soft cooldown
_SOFT_FLIP_DIVERGENCE_MULT = 1.5    # Divergence threshold multiplier during soft cooldown
_SOFT_FLIP_MIN_STRENGTH = 60.0      # Min bias strength required to flip during soft cooldown
_SOFT_TRIGGER_CONTRADICTIONS = 4    # Contradictions without flip that trigger soft cooldown


# ─── MAIN FSM UPDATE ──────────────────────────────────────────────────────────

def update_bias_fsm(
    *,
    engine_state: Any,
    candles: list[Candle],
    closed_i: int,
    pattern: Signal | None,
    current_time_s: float,
) -> dict[str, Any]:
    """
    Evolve bias FSM + divergence + dual cooldown + regime classification.

    Flow:
        1. Decrement cooldown
        2. Compute bar direction
        3. Update divergence (price vs belief)
        4. Apply divergence consequences (respecting cooldown mode)
        5. Run core FSM transitions
        6. Classify regime (metadata)
        7. Write final state
    """
    # Read state
    phase = getattr(engine_state, "bias_phase", "EXPIRED")
    current_bias = getattr(engine_state, "current_bias", None)
    strength = getattr(engine_state, "bias_strength", 0.0)
    confirm_count = getattr(engine_state, "bias_confirmation_count", 0)
    contradict_count = getattr(engine_state, "bias_contradiction_count", 0)
    divergence_flag = getattr(engine_state, "divergence_flag", False)
    divergence_strength = getattr(engine_state, "divergence_strength", 0)
    divergence_streak = getattr(engine_state, "divergence_streak", 0)
    cooldown_active = getattr(engine_state, "cooldown_active", False)
    cooldown_mode = getattr(engine_state, "cooldown_mode", "NONE")
    flip_cooldown_bars = getattr(engine_state, "flip_cooldown_bars", 0)

    # ─── STEP 1: DECREMENT COOLDOWN ──────────────────────────────────
    if cooldown_active:
        flip_cooldown_bars -= 1
        if flip_cooldown_bars <= 0:
            cooldown_active = False
            cooldown_mode = "NONE"
            flip_cooldown_bars = 0

    # Compute directions
    bar_direction = _get_bar_direction(candles, closed_i)
    pattern_direction = pattern.side if pattern else None

    old_phase = phase
    old_bias = current_bias
    old_strength = strength
    transition = None
    divergence_event = None

    # ─── STEP 2: DIVERGENCE DETECTION ─────────────────────────────────
    if current_bias is not None and phase in ("CONFIRMED", "CONFIRMING", "WEAKENING"):
        price_agrees = _is_aligned(bar_direction, current_bias)

        if price_agrees:
            divergence_streak = max(0, divergence_streak - _DIVERGENCE_DECAY_ON_AGREEMENT)
            if divergence_streak == 0:
                divergence_flag = False
                divergence_strength = 0
        else:
            if bar_direction is not None:
                divergence_streak += 1
                divergence_strength += 1

        # Soft divergence flag
        if divergence_streak >= _DIVERGENCE_SOFT_BARS and strength >= _DIVERGENCE_STRENGTH_REQUIRED:
            divergence_flag = True
            divergence_event = "SOFT_DIVERGENCE"

        # Hard divergence → WEAKENING
        if divergence_streak >= _DIVERGENCE_HARD_BARS and phase == "CONFIRMED":
            engine_state.bias_phase = "WEAKENING"
            phase = "WEAKENING"
            transition = "CONFIRMED → WEAKENING (divergence)"
            divergence_event = "HARD_DIVERGENCE"

        # ─── FLIP LOGIC (respects cooldown mode) ─────────────────────
        flip_allowed = False
        if cooldown_mode == "NONE":
            flip_allowed = divergence_streak >= _DIVERGENCE_FLIP_THRESHOLD
        elif cooldown_mode == "SOFT":
            # Elevated threshold during soft cooldown
            elevated_threshold = int(_DIVERGENCE_FLIP_THRESHOLD * _SOFT_FLIP_DIVERGENCE_MULT)
            flip_allowed = (divergence_streak >= elevated_threshold and strength >= _SOFT_FLIP_MIN_STRENGTH)
        # HARD cooldown: flip_allowed stays False

        if flip_allowed and bar_direction is not None:
            engine_state.current_bias = bar_direction
            engine_state.bias_phase = "FORMING"
            engine_state.bias_strength = _INITIAL_STRENGTH
            engine_state.bias_confirmation_count = 1
            engine_state.bias_contradiction_count = 0
            engine_state.last_bias_time = current_time_s
            divergence_streak = 0
            divergence_strength = 0
            divergence_flag = False
            # Activate HARD cooldown after flip
            import random as _rnd
            cooldown_bars = _rnd.randint(_HARD_COOLDOWN_MIN, _HARD_COOLDOWN_MAX)
            cooldown_active = True
            cooldown_mode = "HARD"
            flip_cooldown_bars = cooldown_bars
            transition = f"DIVERGENCE FLIP → FORMING ({bar_direction.value}) [HARD cooldown={cooldown_bars}]"
            divergence_event = "FLIP"
            _write_state(engine_state, divergence_flag, divergence_strength, divergence_streak,
                         bar_direction, cooldown_active, cooldown_mode, flip_cooldown_bars)
            _update_age(engine_state, current_time_s)
            _classify_regime(engine_state, divergence_streak, divergence_flag, cooldown_active, cooldown_mode)
            return _build_log(old_phase, old_bias, old_strength, engine_state, bar_direction, pattern_direction, transition, divergence_event)
    else:
        divergence_streak = 0
        divergence_strength = 0
        divergence_flag = False

    # ─── STEP 3: SOFT COOLDOWN TRIGGER ────────────────────────────────
    # If repeated contradictions without a flip, activate soft cooldown
    if (contradict_count >= _SOFT_TRIGGER_CONTRADICTIONS
            and not cooldown_active
            and phase in ("CONFIRMED", "CONFIRMING")):
        cooldown_active = True
        cooldown_mode = "SOFT"
        flip_cooldown_bars = _SOFT_COOLDOWN_BARS

    # ─── STEP 4: CORE FSM TRANSITIONS ─────────────────────────────────
    phase = getattr(engine_state, "bias_phase", "EXPIRED")
    current_bias = getattr(engine_state, "current_bias", None)
    strength = getattr(engine_state, "bias_strength", 0.0)

    if phase == "EXPIRED":
        if pattern_direction is not None:
            engine_state.current_bias = pattern_direction
            engine_state.bias_phase = "FORMING"
            engine_state.bias_strength = _INITIAL_STRENGTH
            engine_state.bias_confirmation_count = 1
            engine_state.bias_contradiction_count = 0
            engine_state.last_bias_time = current_time_s
            engine_state.bias_age_seconds = 0.0
            transition = transition or "EXPIRED → FORMING"

    elif phase == "FORMING":
        aligned = _is_aligned(bar_direction, current_bias)
        pattern_aligned = pattern_direction == current_bias if pattern_direction else False
        if aligned or pattern_aligned:
            engine_state.bias_confirmation_count = confirm_count + 1
            engine_state.bias_strength = min(_MAX_STRENGTH, strength + _STRENGTH_INCREMENT * 0.5)
            engine_state.bias_contradiction_count = 0
            if engine_state.bias_confirmation_count >= _CONFIRMATION_BARS_REQUIRED:
                engine_state.bias_phase = "CONFIRMING"
                transition = transition or "FORMING → CONFIRMING"
        else:
            engine_state.bias_contradiction_count = contradict_count + 1
            engine_state.bias_strength = max(0.0, strength - _STRENGTH_DECAY_PER_BAR * 2)
            if engine_state.bias_contradiction_count >= _FLIP_THRESHOLD:
                engine_state.bias_phase = "EXPIRED"
                engine_state.current_bias = None
                engine_state.bias_strength = 0.0
                engine_state.bias_confirmation_count = 0
                engine_state.bias_contradiction_count = 0
                transition = transition or "FORMING → EXPIRED (contradicted)"

    elif phase == "CONFIRMING":
        aligned = _is_aligned(bar_direction, current_bias)
        if aligned:
            engine_state.bias_confirmation_count = confirm_count + 1
            engine_state.bias_strength = min(_MAX_STRENGTH, strength + _STRENGTH_INCREMENT)
            if engine_state.bias_confirmation_count >= (_CONFIRMATION_BARS_REQUIRED + _CONFIRMING_BARS_REQUIRED):
                engine_state.bias_phase = "CONFIRMED"
                engine_state.bias_strength = min(_MAX_STRENGTH, max(strength, 60.0))
                engine_state.last_bias_time = current_time_s
                transition = transition or "CONFIRMING → CONFIRMED"
        else:
            engine_state.bias_contradiction_count = contradict_count + 1
            engine_state.bias_strength = max(0.0, strength - _STRENGTH_DECAY_PER_BAR)
            if engine_state.bias_contradiction_count >= _FLIP_THRESHOLD:
                engine_state.bias_phase = "FORMING"
                engine_state.bias_confirmation_count = 0
                engine_state.bias_contradiction_count = 0
                transition = transition or "CONFIRMING → FORMING (contradicted)"

    elif phase == "CONFIRMED":
        aligned = _is_aligned(bar_direction, current_bias)
        if aligned:
            engine_state.bias_strength = min(_MAX_STRENGTH, strength + _STRENGTH_REINFORCEMENT)
            engine_state.bias_contradiction_count = 0
        else:
            engine_state.bias_strength = max(0.0, strength - _STRENGTH_DECAY_PER_BAR)
            engine_state.bias_contradiction_count = contradict_count + 1

        if engine_state.bias_strength < _WEAKNESS_THRESHOLD:
            engine_state.bias_phase = "WEAKENING"
            transition = transition or "CONFIRMED → WEAKENING"

        # Pattern-triggered flip (respects cooldown)
        if pattern_direction is not None and pattern_direction != current_bias:
            engine_state.bias_contradiction_count += 1
            can_flip = (cooldown_mode != "HARD") and (engine_state.bias_contradiction_count >= _FLIP_THRESHOLD + 1)
            if cooldown_mode == "SOFT":
                can_flip = can_flip and (strength >= _SOFT_FLIP_MIN_STRENGTH)
            if can_flip:
                engine_state.current_bias = pattern_direction
                engine_state.bias_phase = "FORMING"
                engine_state.bias_strength = _INITIAL_STRENGTH
                engine_state.bias_confirmation_count = 1
                engine_state.bias_contradiction_count = 0
                engine_state.last_bias_time = current_time_s
                import random as _rnd
                cooldown_bars = _rnd.randint(_HARD_COOLDOWN_MIN, _HARD_COOLDOWN_MAX)
                cooldown_active = True
                cooldown_mode = "HARD"
                flip_cooldown_bars = cooldown_bars
                transition = transition or f"CONFIRMED → FLIPPED → FORMING [HARD cooldown={cooldown_bars}]"

    elif phase == "WEAKENING":
        aligned = _is_aligned(bar_direction, current_bias)
        if aligned:
            engine_state.bias_strength = min(_MAX_STRENGTH, strength + _STRENGTH_REINFORCEMENT)
            engine_state.bias_contradiction_count = 0
            if engine_state.bias_strength >= _WEAKNESS_THRESHOLD:
                engine_state.bias_phase = "CONFIRMED"
                transition = transition or "WEAKENING → CONFIRMED (recovered)"
        else:
            engine_state.bias_strength = max(0.0, strength - _STRENGTH_DECAY_PER_BAR * 1.5)

        if engine_state.bias_strength <= _EXPIRY_THRESHOLD:
            engine_state.bias_phase = "EXPIRED"
            engine_state.current_bias = None
            engine_state.bias_strength = 0.0
            engine_state.bias_confirmation_count = 0
            engine_state.bias_contradiction_count = 0
            transition = transition or "WEAKENING → EXPIRED"

    # ─── STEP 5: WRITE STATE + CLASSIFY REGIME ────────────────────────
    _write_state(engine_state, divergence_flag, divergence_strength, divergence_streak,
                 bar_direction, cooldown_active, cooldown_mode, flip_cooldown_bars)
    _update_age(engine_state, current_time_s)
    _classify_regime(engine_state, divergence_streak, divergence_flag, cooldown_active, cooldown_mode)

    return _build_log(old_phase, old_bias, old_strength, engine_state, bar_direction, pattern_direction, transition, divergence_event)


# ─── REGIME CLASSIFICATION (metadata only — does NOT influence scoring) ───────

def _classify_regime(
    engine_state: Any,
    divergence_streak: int,
    divergence_flag: bool,
    cooldown_active: bool,
    cooldown_mode: str,
) -> None:
    """Assign regime_label based on current FSM + divergence + cooldown state."""
    phase = getattr(engine_state, "bias_phase", "EXPIRED")
    strength = getattr(engine_state, "bias_strength", 0.0)

    if cooldown_mode == "HARD":
        engine_state.regime_label = "TRANSITIONAL"
    elif phase == "CONFIRMED" and not divergence_flag and strength >= 50:
        engine_state.regime_label = "TRENDING_STABLE"
    elif phase == "CONFIRMED" and (divergence_flag or strength < 50):
        engine_state.regime_label = "TRENDING_WEAKENING"
    elif phase in ("FORMING", "CONFIRMING") and cooldown_mode == "NONE" and not cooldown_active:
        # Recently started forming without coming from a flip
        engine_state.regime_label = "CHOPPING"
    elif phase in ("FORMING", "CONFIRMING") and (cooldown_mode == "SOFT" or cooldown_active):
        engine_state.regime_label = "POST_FLIP_RECOVERY"
    elif phase == "WEAKENING":
        engine_state.regime_label = "TRENDING_WEAKENING"
    elif phase == "EXPIRED":
        engine_state.regime_label = "CHOPPING"
    else:
        engine_state.regime_label = "CHOPPING"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _write_state(
    engine_state: Any,
    divergence_flag: bool,
    divergence_strength: int,
    divergence_streak: int,
    bar_direction: Side | None,
    cooldown_active: bool,
    cooldown_mode: str,
    flip_cooldown_bars: int,
) -> None:
    """Write all FSM-managed fields to engine state."""
    engine_state.divergence_flag = divergence_flag
    engine_state.divergence_strength = divergence_strength
    engine_state.divergence_streak = divergence_streak
    engine_state.last_price_direction = bar_direction.value if bar_direction and hasattr(bar_direction, "value") else str(bar_direction)
    engine_state.cooldown_active = cooldown_active
    engine_state.cooldown_mode = cooldown_mode
    engine_state.flip_cooldown_bars = flip_cooldown_bars


def _update_age(engine_state: Any, current_time_s: float) -> None:
    """Update bias age seconds."""
    last_time = getattr(engine_state, "last_bias_time", None)
    if last_time and getattr(engine_state, "bias_phase", "EXPIRED") != "EXPIRED":
        engine_state.bias_age_seconds = current_time_s - last_time


def _build_log(
    old_phase: str,
    old_bias: Any,
    old_strength: float,
    engine_state: Any,
    bar_direction: Side | None,
    pattern_direction: Side | None,
    transition: str | None,
    divergence_event: str | None,
) -> dict[str, Any]:
    """Build structured log output."""
    return {
        "old_phase": old_phase,
        "new_phase": getattr(engine_state, "bias_phase", "?"),
        "old_bias": old_bias.value if old_bias and hasattr(old_bias, "value") else str(old_bias),
        "new_bias": engine_state.current_bias.value if getattr(engine_state, "current_bias", None) and hasattr(engine_state.current_bias, "value") else str(getattr(engine_state, "current_bias", None)),
        "old_strength": old_strength,
        "new_strength": getattr(engine_state, "bias_strength", 0.0),
        "transition": transition,
        "bar_direction": bar_direction.value if bar_direction else "NEUTRAL",
        "pattern_direction": pattern_direction.value if pattern_direction and hasattr(pattern_direction, "value") else str(pattern_direction),
        "divergence_flag": getattr(engine_state, "divergence_flag", False),
        "divergence_streak": getattr(engine_state, "divergence_streak", 0),
        "divergence_strength": getattr(engine_state, "divergence_strength", 0),
        "divergence_event": divergence_event,
        "cooldown_mode": getattr(engine_state, "cooldown_mode", "NONE"),
        "cooldown_bars_remaining": getattr(engine_state, "flip_cooldown_bars", 0),
        "regime_label": getattr(engine_state, "regime_label", "CHOPPING"),
    }


def _get_bar_direction(candles: list[Candle], closed_i: int) -> Side | None:
    """Determine bar directional bias from close vs open."""
    if closed_i < 0 or closed_i >= len(candles):
        return None
    c = candles[closed_i]
    body = c.close - c.open
    candle_range = c.high - c.low
    if candle_range <= 0:
        return None
    if abs(body) / candle_range < 0.30:
        return None
    return Side.BUY if body > 0 else Side.SELL


def _is_aligned(bar_direction: Side | None, bias: Any) -> bool:
    """Check if bar direction aligns with current bias."""
    if bar_direction is None or bias is None:
        return False
    if hasattr(bias, "value"):
        return bar_direction.value == bias.value
    if hasattr(bias, "name"):
        return bar_direction.name == bias.name
    return str(bar_direction) == str(bias)
