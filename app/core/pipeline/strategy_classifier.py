"""
Strategy Classifier — Maps detected patterns + market context into strategy types.

Pure classification logic. No scoring. No execution decisions.
Produces a strategy assignment (A/B/C) that downstream scoring uses
to select weight profiles.

Strategies:
    A = CONTINUATION — trade in direction of established trend/bias
    B = REVERSAL — trade against exhausted trend at structural boundary
    C = FALSE_BREAK — trade after failed breakout / liquidity sweep

This module is DETERMINISTIC. Same inputs → same classification.
No learning. No optimisation. No adaptive logic.

Stability design:
    - Each pattern has a PRIMARY strategy (no shared ownership)
    - Boundary thresholds use buffer zones (hysteresis)
    - Classification reflects structural change, not micro-noise
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from data.mt5_data import Candle
from strategy.signals import Signal, Side


# ─── STRATEGY TYPES ───────────────────────────────────────────────────────────

class StrategyType(str, Enum):
    """Three core strategy archetypes."""
    CONTINUATION = "A_CONTINUATION"
    REVERSAL = "B_REVERSAL"
    FALSE_BREAK = "C_FALSE_BREAK"


# ─── CLASSIFICATION OUTPUT ────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyClassification:
    """
    Result of strategy classification for one pattern + market context.

    Immutable. Read-only downstream.
    """
    strategy: StrategyType
    confidence: float          # 0.0–1.0 classification confidence
    reasoning: str             # Human-readable why this classification
    detected_pattern: str      # Original pattern name
    side: Side                 # Trade direction


# ─── PATTERN OWNERSHIP (NO OVERLAP — EACH PATTERN HAS ONE PRIMARY STRATEGY) ──
# Priority: FALSE_BREAK > REVERSAL > CONTINUATION
# A pattern's PRIMARY assignment determines which strategy it CAN activate.
# Context (bias, structure) determines whether it DOES activate.

# PRIMARY: FALSE_BREAK — sweep/rejection single-bar patterns
_PRIMARY_FALSE_BREAK = frozenset({
    "HAMMER",               # Wick rejection (down sweep → buy)
    "SHOOTING_STAR",        # Wick rejection (up sweep → sell)
    "INVERTED_HAMMER",      # Wick rejection variant
    "HANGING_MAN",          # Wick rejection variant
})

# PRIMARY: REVERSAL — multi-bar exhaustion patterns
_PRIMARY_REVERSAL = frozenset({
    "EVENING_STAR",         # 3-bar top reversal (exclusive to reversal)
    "MORNING_STAR",         # 3-bar bottom reversal (exclusive to reversal)
    "TWEEZER_TOP",          # Double-bar exhaustion top
    "TWEEZER_BOTTOM",       # Double-bar exhaustion bottom
})

# PRIMARY: CONTINUATION — momentum/engulfing patterns
_PRIMARY_CONTINUATION = frozenset({
    "BULLISH_ENGULFING",    # Momentum continuation (with-trend)
    "BEARISH_ENGULFING",    # Momentum continuation (with-trend)
    "THREE_WHITE_SOLDIERS", # Strong bullish momentum
    "THREE_BLACK_CROWS",    # Strong bearish momentum
})

# ─── SECONDARY ELIGIBILITY ────────────────────────────────────────────────────
# Patterns can ONLY be reclassified from their primary if structural evidence
# is overwhelming (score >= reclassification threshold). Otherwise, primary holds.

_RECLASSIFICATION_THRESHOLD = 0.70  # Must score >= this to override primary assignment

# FALSE_BREAK patterns CAN reclassify as REVERSAL if exhaustion is extreme
_FB_CAN_RECLASSIFY_TO_REVERSAL = _PRIMARY_FALSE_BREAK

# CONTINUATION patterns CAN reclassify as REVERSAL if counter-trend + exhaustion
_CONT_CAN_RECLASSIFY_TO_REVERSAL = frozenset({
    "BULLISH_ENGULFING",    # Counter-trend engulfing at extreme = reversal
    "BEARISH_ENGULFING",
})


# ─── HYSTERESIS BUFFERS ───────────────────────────────────────────────────────
# Prevent classification flipping at threshold boundaries.
# A value must cross threshold + buffer to change classification.

_BIAS_STRENGTH_WEAK_THRESHOLD = 35.0    # Below this = "weak" (reversal-eligible)
_BIAS_STRENGTH_STRONG_THRESHOLD = 50.0  # Above this = "strong" (continuation-favoured)
# Dead zone: 35–50 = ambiguous (uses primary pattern assignment, lower confidence)

_REVERSAL_DEVIATION_THRESHOLD = 1.5     # ATR-normalized deviation for reversal
_REVERSAL_DEVIATION_STRONG = 2.0        # Strong reversal signal

_FALSE_BREAK_SWEEP_THRESHOLD = 0.6      # Min score for false break classification
_REVERSAL_MIN_SCORE = 0.55              # Raised from 0.5 for stability


# ─── MAIN CLASSIFIER ─────────────────────────────────────────────────────────

def classify_strategy(
    *,
    pattern: Signal,
    candles: list[Candle],
    closed_i: int,
    engine_state: Any,
    htf_context: Any = None,
) -> StrategyClassification:
    """
    Classify a detected pattern into one of three strategy types.

    Classification rules:
    1. Determine pattern's PRIMARY strategy assignment
    2. Check if structural evidence justifies RECLASSIFICATION
    3. If reclassification score < threshold → keep primary assignment
    4. Apply boundary buffers to prevent threshold oscillation

    Args:
        pattern: Detected Signal from pattern gate
        candles: Candle history
        closed_i: Last closed bar index
        engine_state: Current EngineState (bias, regime, etc.)
        htf_context: Optional HTFContext

    Returns:
        StrategyClassification (immutable, read-only)
    """

    # Extract state (read-only)
    bias = getattr(engine_state, "current_bias", None)
    bias_phase = getattr(engine_state, "bias_phase", "EXPIRED")
    bias_strength = getattr(engine_state, "bias_strength", 0.0)

    # ─── STEP 1: DETERMINE PRIMARY ASSIGNMENT ─────────────────────────
    primary = _get_primary_strategy(pattern.pattern)

    # ─── STEP 2: CHECK RECLASSIFICATION OPPORTUNITIES ─────────────────

    # Can FALSE_BREAK primary reclassify to actual FALSE_BREAK? (needs structural sweep)
    if primary == StrategyType.FALSE_BREAK:
        fb_score = _check_false_break(candles, closed_i, pattern)
        if fb_score >= _FALSE_BREAK_SWEEP_THRESHOLD:
            return StrategyClassification(
                strategy=StrategyType.FALSE_BREAK,
                confidence=fb_score,
                reasoning=f"Primary FB pattern + sweep confirmed (score={fb_score:.2f})",
                detected_pattern=pattern.pattern,
                side=pattern.side,
            )
        # No sweep → check if reversal context exists (reclassification attempt)
        if pattern.pattern in _FB_CAN_RECLASSIFY_TO_REVERSAL:
            rev_score = _check_reversal(candles, closed_i, pattern, bias, bias_phase, bias_strength)
            if rev_score >= _RECLASSIFICATION_THRESHOLD:
                return StrategyClassification(
                    strategy=StrategyType.REVERSAL,
                    confidence=rev_score,
                    reasoning=f"FB pattern reclassified → REVERSAL (exhaustion={rev_score:.2f} >= {_RECLASSIFICATION_THRESHOLD})",
                    detected_pattern=pattern.pattern,
                    side=pattern.side,
                )
        # Default: treat as low-confidence continuation (rejection without sweep)
        cont_score = _check_continuation(pattern, bias, bias_phase, bias_strength)
        return StrategyClassification(
            strategy=StrategyType.CONTINUATION,
            confidence=max(0.25, cont_score * 0.7),  # Reduced confidence (not ideal setup)
            reasoning=f"FB pattern but no sweep detected — fallback to continuation (score={cont_score:.2f})",
            detected_pattern=pattern.pattern,
            side=pattern.side,
        )

    # Can REVERSAL primary patterns achieve reversal classification?
    if primary == StrategyType.REVERSAL:
        rev_score = _check_reversal(candles, closed_i, pattern, bias, bias_phase, bias_strength)
        if rev_score >= _REVERSAL_MIN_SCORE:
            return StrategyClassification(
                strategy=StrategyType.REVERSAL,
                confidence=rev_score,
                reasoning=f"Primary reversal pattern + exhaustion confirmed (score={rev_score:.2f})",
                detected_pattern=pattern.pattern,
                side=pattern.side,
            )
        # No exhaustion context → NOT a reversal, classify as continuation
        cont_score = _check_continuation(pattern, bias, bias_phase, bias_strength)
        return StrategyClassification(
            strategy=StrategyType.CONTINUATION,
            confidence=max(0.25, cont_score * 0.8),  # Slightly reduced (reversal pattern used as continuation)
            reasoning=f"Reversal pattern but no exhaustion — treated as continuation (score={cont_score:.2f})",
            detected_pattern=pattern.pattern,
            side=pattern.side,
        )

    # CONTINUATION primary patterns — check if reclassification to reversal is warranted
    if primary == StrategyType.CONTINUATION:
        if pattern.pattern in _CONT_CAN_RECLASSIFY_TO_REVERSAL:
            rev_score = _check_reversal(candles, closed_i, pattern, bias, bias_phase, bias_strength)
            if rev_score >= _RECLASSIFICATION_THRESHOLD:
                return StrategyClassification(
                    strategy=StrategyType.REVERSAL,
                    confidence=rev_score,
                    reasoning=f"Continuation pattern reclassified → REVERSAL (extreme exhaustion={rev_score:.2f})",
                    detected_pattern=pattern.pattern,
                    side=pattern.side,
                )
        # Default: continuation
        cont_score = _check_continuation(pattern, bias, bias_phase, bias_strength)
        return StrategyClassification(
            strategy=StrategyType.CONTINUATION,
            confidence=cont_score,
            reasoning=f"Primary continuation pattern + bias aligned (score={cont_score:.2f})",
            detected_pattern=pattern.pattern,
            side=pattern.side,
        )

    # Fallback (should never reach here)
    return StrategyClassification(
        strategy=StrategyType.CONTINUATION,
        confidence=0.3,
        reasoning="Unknown pattern — fallback to continuation",
        detected_pattern=pattern.pattern,
        side=pattern.side,
    )


# ─── PRIMARY ASSIGNMENT ───────────────────────────────────────────────────────

def _get_primary_strategy(pattern_name: str) -> StrategyType:
    """Deterministic primary strategy for each pattern. No overlap."""
    if pattern_name in _PRIMARY_FALSE_BREAK:
        return StrategyType.FALSE_BREAK
    if pattern_name in _PRIMARY_REVERSAL:
        return StrategyType.REVERSAL
    if pattern_name in _PRIMARY_CONTINUATION:
        return StrategyType.CONTINUATION
    # Unknown pattern → default continuation
    return StrategyType.CONTINUATION


# ─── CLASSIFICATION LOGIC (WITH HYSTERESIS) ───────────────────────────────────

def _check_false_break(
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
) -> float:
    """
    Detect false breakout / liquidity sweep pattern.

    Looks for: price exceeds recent high/low then rejects back.
    Returns 0.0–1.0 confidence.
    """
    if closed_i < 10:
        return 0.0

    lookback = 10
    window = candles[closed_i - lookback: closed_i]
    current = candles[closed_i]

    if pattern.side == Side.BUY:
        recent_low = min(c.low for c in window)
        swept = current.low < recent_low
        rejected = current.close > current.open
        wick_ratio = (current.open - current.low) / max(current.high - current.low, 0.0001)
    else:
        recent_high = max(c.high for c in window)
        swept = current.high > recent_high
        rejected = current.close < current.open
        wick_ratio = (current.high - current.open) / max(current.high - current.low, 0.0001)

    if not swept:
        return 0.0
    if not rejected:
        return 0.15  # Swept but no clear rejection

    # Confidence based on wick rejection strength
    score = 0.5 + (0.5 * min(1.0, wick_ratio / 0.6))
    return round(min(1.0, score), 3)


def _check_reversal(
    candles: list[Candle],
    closed_i: int,
    pattern: Signal,
    bias: Any,
    bias_phase: str,
    bias_strength: float,
) -> float:
    """
    Detect reversal conditions with hysteresis buffers.

    Uses buffer zones to prevent threshold oscillation:
    - bias_strength must be clearly WEAK (< 35) for exhaustion signal
    - Deviation must be clearly EXTENDED (>= 1.5 ATR) for reversal context

    Returns 0.0–1.0 confidence.
    """
    if closed_i < 20:
        return 0.0

    # Reversal REQUIRES counter-trend pattern (hard gate, no buffer needed)
    is_counter_trend = False
    if bias is not None:
        bias_dir = _extract_bias_direction(bias)
        if (bias_dir == "BUY" and pattern.side == Side.SELL) or \
           (bias_dir == "SELL" and pattern.side == Side.BUY):
            is_counter_trend = True

    if not is_counter_trend:
        return 0.0  # Absolute gate: not a reversal if trading with bias

    score = 0.2  # Base: counter-trend pattern exists (lowered from 0.3 for stability)

    # Extended move detection with buffer zone
    lookback = 20
    window = candles[closed_i - lookback: closed_i + 1]
    closes = [c.close for c in window]
    mean_price = sum(closes) / len(closes)
    current_price = candles[closed_i].close

    atr = sum(c.high - c.low for c in window) / len(window)
    if atr > 0:
        deviation = abs(current_price - mean_price) / atr
        # Buffer zone: only credit deviation if clearly past threshold
        if deviation >= _REVERSAL_DEVIATION_STRONG:
            score += 0.35  # Strong extension — high reversal confidence
        elif deviation >= _REVERSAL_DEVIATION_THRESHOLD:
            score += 0.20  # Moderate extension
        # Below 1.5 ATR = no extension credit (buffer protects against noise)

    # Bias exhaustion with hysteresis buffer
    # Only triggers if CLEARLY weak (< 35), not at boundary (35-50 is dead zone)
    if bias_phase == "CONFIRMED" and bias_strength < _BIAS_STRENGTH_WEAK_THRESHOLD:
        score += 0.25  # Clear exhaustion signal
    elif bias_phase == "CONFIRMED" and bias_strength < _BIAS_STRENGTH_STRONG_THRESHOLD:
        score += 0.10  # Ambiguous zone — minimal credit (prevents flip at boundary)

    return round(min(1.0, score), 3)


def _check_continuation(
    pattern: Signal,
    bias: Any,
    bias_phase: str,
    bias_strength: float,
) -> float:
    """
    Evaluate continuation fitness with hysteresis buffers.

    Uses buffer zones:
    - bias_strength must be clearly STRONG (>= 50) for full confidence
    - Dead zone (35-50) gets reduced confidence to prevent boundary flipping

    Returns 0.0–1.0 confidence.
    """
    # No bias = low continuation confidence
    if bias is None or bias_phase == "EXPIRED":
        return 0.25

    # Check alignment
    bias_dir = _extract_bias_direction(bias)
    aligned = (bias_dir == "BUY" and pattern.side == Side.BUY) or \
              (bias_dir == "SELL" and pattern.side == Side.SELL)

    if not aligned:
        return 0.15  # Counter-trend pattern in continuation bucket (very low confidence)

    score = 0.45  # Base: aligned with bias

    # Bias phase contribution
    if bias_phase == "CONFIRMED":
        score += 0.15
    elif bias_phase == "BUILDING":
        score += 0.05

    # Bias strength with buffer zones (no sharp cutoffs)
    if bias_strength >= _BIAS_STRENGTH_STRONG_THRESHOLD:
        score += 0.20  # Clearly strong — full continuation confidence
    elif bias_strength >= _BIAS_STRENGTH_WEAK_THRESHOLD:
        # Dead zone (35-50): proportional credit (smooth, no cliff)
        zone_progress = (bias_strength - _BIAS_STRENGTH_WEAK_THRESHOLD) / \
                        (_BIAS_STRENGTH_STRONG_THRESHOLD - _BIAS_STRENGTH_WEAK_THRESHOLD)
        score += 0.10 * zone_progress  # 0.0 at 35, 0.10 at 50
    # Below 35: no strength credit (too weak for continuation confidence)

    # Strong continuation patterns get small bonus
    if pattern.pattern in _PRIMARY_CONTINUATION:
        score += 0.05

    return round(min(1.0, score), 3)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _extract_bias_direction(bias: Any) -> str:
    """Safely extract bias direction string from enum or object."""
    if hasattr(bias, "value"):
        return bias.value
    if hasattr(bias, "name"):
        return bias.name
    return str(bias)
