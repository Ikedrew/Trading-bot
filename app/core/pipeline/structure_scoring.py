"""
Structure Cohesion Scoring System — Rolling probabilistic state estimator.

Replaces the FSM-based confirmation logic with a bounded memory score system.
Runs IN PARALLEL with existing FSM (safe layer — does not affect decisions yet).

Architecture:
  - Buffer: deque(maxlen=5) of per-bar structure scores
  - Score: recency-weighted sum of buffer
  - Regime: derived from score + guards (WEAK/BUILDING/CONFIRMED/INVALID)

Rules:
  - No resets, no counters, no consecutive-bar requirements
  - Contradictions penalize score (soft), never reset
  - CONFIRMED requires score ≥ 3.0 AND ≥2 strong bars (≥0.8)
  - INVALID triggers when negative pressure ≤ -2.0

Ownership: core/pipeline/structure_scoring.py
Mutability: structure_buffer on EngineState (rolling deque)
Dependencies: Candle data only (no voter, no confluence, no execution)
"""

from __future__ import annotations

import math
from collections import deque
from typing import Literal

from data.mt5_data import Candle
from strategy.signals import Side

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

STRUCTURE_BUFFER_SIZE = 5
DECAY_FACTOR = 0.3          # Exponential decay per bar age
CONFIRMED_THRESHOLD = 3.0
BUILDING_THRESHOLD = 1.5
STRONG_BAR_THRESHOLD = 0.8
MIN_STRONG_BARS = 2
INVALID_NEGATIVE_THRESHOLD = -2.0


# ─── BAR SCORING ──────────────────────────────────────────────────────────────

def score_bar(candles: list[Candle], closed_i: int, bias_direction: Side | None) -> float:
    """
    Score a single bar's structural contribution.

    Returns:
      +1.0: aligned structure continuation (HH/HL or LH/LL matching bias)
      +0.5: weak alignment / ambiguous
      +0.2: neutral (no clear signal)
      -0.5: noisy / inconclusive
      -1.0: contradiction (opposite structure shift)
    """
    if closed_i < 1 or closed_i >= len(candles):
        return 0.0

    curr = candles[closed_i]
    prev = candles[closed_i - 1]

    # Detect structure signals
    higher_high = curr.high > prev.high
    higher_low = curr.low > prev.low
    lower_high = curr.high < prev.high
    lower_low = curr.low < prev.low

    bullish_structure = higher_high and higher_low
    bearish_structure = lower_high and lower_low
    mixed = (higher_high and lower_low) or (lower_high and higher_low)

    # Score based on bias alignment
    if bias_direction == Side.BUY:
        if bullish_structure:
            return 1.0
        elif higher_high or higher_low:
            return 0.5
        elif bearish_structure:
            return -1.0
        elif lower_high or lower_low:
            return -0.5
        else:
            return 0.2

    elif bias_direction == Side.SELL:
        if bearish_structure:
            return 1.0
        elif lower_high or lower_low:
            return 0.5
        elif bullish_structure:
            return -1.0
        elif higher_high or higher_low:
            return -0.5
        else:
            return 0.2

    else:
        # No bias direction — score based on any clear structure
        if bullish_structure or bearish_structure:
            return 0.5
        elif mixed:
            return -0.5
        else:
            return 0.2


# ─── SCORE COMPUTATION ────────────────────────────────────────────────────────

def compute_structure_score(buffer: deque[float]) -> float:
    """
    Compute weighted structure score from buffer.
    Most recent bar has highest weight (index 0 = oldest, -1 = newest).
    """
    if not buffer:
        return 0.0

    total = 0.0
    for i, bar_score in enumerate(buffer):
        # Age: 0 = oldest in buffer, len-1 = newest
        age = len(buffer) - 1 - i  # newest = age 0
        weight = math.exp(-age * DECAY_FACTOR)
        total += bar_score * weight

    return round(total, 3)


# ─── REGIME CLASSIFICATION ────────────────────────────────────────────────────

def classify_regime(
    score: float,
    buffer: deque[float],
) -> Literal["WEAK", "BUILDING", "CONFIRMED", "INVALID"]:
    """
    Derive structure regime from score + guards.
    No FSM. No counters. Pure function of current buffer state.
    """
    # Guard 1: Negative pressure → INVALID
    negative_sum = sum(s for s in buffer if s < 0)
    if negative_sum <= INVALID_NEGATIVE_THRESHOLD:
        return "INVALID"

    # Guard 2: CONFIRMED requires score + strong bars
    if score >= CONFIRMED_THRESHOLD:
        strong_count = sum(1 for s in buffer if s >= STRONG_BAR_THRESHOLD)
        if strong_count >= MIN_STRONG_BARS:
            return "CONFIRMED"
        else:
            return "BUILDING"  # Score high but no conviction anchors

    # Standard classification
    if score >= BUILDING_THRESHOLD:
        return "BUILDING"

    return "WEAK"


# ─── MAIN UPDATE FUNCTION ─────────────────────────────────────────────────────

def update_structure_state(
    buffer: deque[float],
    candles: list[Candle],
    closed_i: int,
    bias_direction: Side | None,
) -> tuple[float, str]:
    """
    Update structure buffer with new bar and return (score, regime).

    This is the ONLY function that modifies the buffer.
    Called once per bar, after state preparation.

    Args:
        buffer: Mutable deque (on EngineState) — modified in place
        candles: Full candle array
        closed_i: Index of closed bar
        bias_direction: Current bias direction (from existing FSM or setup)

    Returns:
        (structure_score, structure_regime)
    """
    # Score this bar
    bar_score = score_bar(candles, closed_i, bias_direction)

    # Append to buffer (oldest auto-evicted by maxlen)
    buffer.append(bar_score)

    # Compute aggregate score
    score = compute_structure_score(buffer)

    # Classify regime
    regime = classify_regime(score, buffer)

    return score, regime
