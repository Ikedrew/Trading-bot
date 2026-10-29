"""
Structure & Bias Scoring Module — Probabilistic market state descriptor.

Replaces the old hard-gating Structure/Bias filter with a continuous
scoring model that DESCRIBES market state without REJECTING signals.

This module NEVER:
    - Rejects signals
    - Returns None
    - Blocks downstream processing
    - Decides whether to trade

It ONLY answers: "What is the current market state and how strong is it?"

Outputs:
    structure_score: 0–100
    bias_score: 0–100
    regime: TREND / RANGE / CHOP / TRANSITION
    confidence: 0.0–1.0
    notes: reasoning string

All outputs pass through to strategy activation and EV scoring.

Design: deterministic, pure function, always produces output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data.mt5_data import Candle
from strategy.signals import Side


# ─── OUTPUT CONTRACT ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StructureBiasResult:
    """Complete structure + bias scoring output. Always populated."""
    structure_score: float      # 0–100
    bias_score: float           # 0–100
    regime: str                 # TREND / RANGE / CHOP / TRANSITION
    confidence: float           # 0.0–1.0
    notes: str


# ─── MAIN SCORING FUNCTION ────────────────────────────────────────────────────

def score_structure_and_bias(
    candles: list[Candle],
    closed_i: int,
    engine_state: Any = None,
) -> StructureBiasResult:
    """
    Score current market structure and directional bias.

    NEVER rejects. Always returns full scoring object.
    Downstream layers (EV, strategy activation) use these scores probabilistically.

    Args:
        candles: Full candle history
        closed_i: Last closed bar index
        engine_state: Optional EngineState for bias FSM data

    Returns:
        StructureBiasResult (always populated, never None)
    """
    if closed_i < 20:
        return StructureBiasResult(
            structure_score=25.0,
            bias_score=25.0,
            regime="TRANSITION",
            confidence=0.2,
            notes="Insufficient data (< 20 bars)",
        )

    lookback = 20
    window = candles[closed_i - lookback: closed_i + 1]

    # ─── STRUCTURE SCORING (0–100) ────────────────────────────────────
    swing_quality = _score_swing_quality(window)             # 0–25
    hh_ll_consistency = _score_hh_ll_consistency(window)     # 0–25
    bos_presence = _score_bos_presence(window)               # 0–20
    compression_expansion = _score_compression(window)       # 0–15
    candle_continuity = _score_continuity(window)            # 0–15

    structure_score = swing_quality + hh_ll_consistency + bos_presence + compression_expansion + candle_continuity
    structure_score = round(min(100.0, max(0.0, structure_score)), 1)

    # ─── BIAS SCORING (0–100) ─────────────────────────────────────────
    htf_alignment = _score_htf_alignment(candles, closed_i, engine_state)  # 0–30
    momentum = _score_momentum(window)                                      # 0–25
    price_location = _score_price_location(window)                          # 0–20
    session_tendency = _score_session_tendency(candles, closed_i)            # 0–15
    vol_clarity = _score_volatility_clarity(window)                          # 0–10

    bias_score = htf_alignment + momentum + price_location + session_tendency + vol_clarity
    bias_score = round(min(100.0, max(0.0, bias_score)), 1)

    # ─── REGIME CLASSIFICATION (descriptive only) ─────────────────────
    regime = _classify_regime(structure_score, bias_score, window)

    # ─── CONFIDENCE ───────────────────────────────────────────────────
    confidence = round(min(1.0, (structure_score + bias_score) / 150.0), 3)

    notes = (
        f"struct={structure_score:.0f} (swing={swing_quality:.0f} hh_ll={hh_ll_consistency:.0f} "
        f"bos={bos_presence:.0f} comp={compression_expansion:.0f} cont={candle_continuity:.0f}) | "
        f"bias={bias_score:.0f} (htf={htf_alignment:.0f} mom={momentum:.0f} "
        f"loc={price_location:.0f} sess={session_tendency:.0f} vol={vol_clarity:.0f})"
    )

    return StructureBiasResult(
        structure_score=structure_score,
        bias_score=bias_score,
        regime=regime,
        confidence=confidence,
        notes=notes,
    )


# ─── STRUCTURE COMPONENTS ─────────────────────────────────────────────────────

def _score_swing_quality(window: list[Candle]) -> float:
    """Swing structure quality (0–25). Well-defined pivots = high score."""
    if len(window) < 10:
        return 5.0

    # Count clear swing pivots (local extrema with 2-bar confirmation)
    pivot_count = 0
    for i in range(2, len(window) - 2):
        is_high = window[i].high > window[i-1].high and window[i].high > window[i+1].high
        is_low = window[i].low < window[i-1].low and window[i].low < window[i+1].low
        if is_high or is_low:
            pivot_count += 1

    # 4+ pivots = full score, 0 = minimal
    return min(25.0, pivot_count * 5.0)


def _score_hh_ll_consistency(window: list[Candle]) -> float:
    """Higher-high/lower-low consistency (0–25). Directional sequence quality."""
    if len(window) < 10:
        return 5.0

    mid = len(window) // 2
    first_highs = [c.high for c in window[:mid]]
    second_highs = [c.high for c in window[mid:]]
    first_lows = [c.low for c in window[:mid]]
    second_lows = [c.low for c in window[mid:]]

    hh = max(second_highs) > max(first_highs)
    hl = min(second_lows) > min(first_lows)
    lh = max(second_highs) < max(first_highs)
    ll = min(second_lows) < min(first_lows)

    score = 5.0  # Base
    if (hh and hl) or (lh and ll):
        score = 25.0  # Perfect directional structure
    elif hh or ll:
        score = 15.0  # Partial directional
    elif hl or lh:
        score = 10.0  # Mixed

    return score


def _score_bos_presence(window: list[Candle]) -> float:
    """Break of structure presence (0–20). Recent level breaks."""
    if len(window) < 10:
        return 5.0

    lookback_for_level = window[:len(window)//2]
    recent = window[len(window)//2:]

    level_high = max(c.high for c in lookback_for_level)
    level_low = min(c.low for c in lookback_for_level)

    # Check if recent price broke these levels
    broke_high = any(c.close > level_high for c in recent)
    broke_low = any(c.close < level_low for c in recent)

    if broke_high or broke_low:
        # Count confirmation candles above/below
        confirms = sum(1 for c in recent[-3:] if c.close > level_high or c.close < level_low)
        return min(20.0, 10.0 + confirms * 3.0)

    return 3.0  # No break


def _score_compression(window: list[Candle]) -> float:
    """Market compression/expansion state (0–15)."""
    if len(window) < 6:
        return 7.0

    # Compare recent ATR to older ATR
    first_half = window[:len(window)//2]
    second_half = window[len(window)//2:]

    atr_first = sum(c.high - c.low for c in first_half) / len(first_half)
    atr_second = sum(c.high - c.low for c in second_half) / len(second_half)

    if atr_first <= 0:
        return 7.0

    ratio = atr_second / atr_first

    if ratio >= 1.5:
        return 15.0  # Expansion (strong structure forming)
    elif ratio >= 1.0:
        return 10.0  # Mild expansion
    elif ratio >= 0.5:
        return 5.0   # Compression (potential breakout building)
    else:
        return 2.0   # Dead market


def _score_continuity(window: list[Candle]) -> float:
    """Candle continuity / trend cohesion (0–15)."""
    if len(window) < 5:
        return 5.0

    # Count consecutive same-direction closes
    directions = []
    for c in window[-10:]:
        directions.append(1 if c.close > c.open else -1)

    if not directions:
        return 5.0

    # Longest streak
    max_streak = 1
    current_streak = 1
    for i in range(1, len(directions)):
        if directions[i] == directions[i-1]:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1

    # Also check net direction
    net = sum(directions)
    directional_clarity = abs(net) / len(directions)

    return min(15.0, max_streak * 2.5 + directional_clarity * 8.0)


# ─── BIAS COMPONENTS ──────────────────────────────────────────────────────────

def _score_htf_alignment(candles: list[Candle], closed_i: int, engine_state: Any) -> float:
    """HTF alignment strength (0–30). Uses longer lookback as HTF proxy."""
    if closed_i < 50:
        return 10.0

    # Use 50-bar EMA-like proxy
    closes_50 = [candles[i].close for i in range(closed_i - 49, closed_i + 1)]
    ema_proxy = sum(closes_50) / len(closes_50)
    current = candles[closed_i].close

    distance = abs(current - ema_proxy) / ema_proxy if ema_proxy > 0 else 0.0

    # Bias from FSM (if available)
    fsm_bonus = 0.0
    if engine_state is not None:
        phase = getattr(engine_state, "bias_phase", "EXPIRED")
        strength = getattr(engine_state, "bias_strength", 0.0)
        if phase == "CONFIRMED":
            fsm_bonus = min(10.0, strength / 10.0)
        elif phase in ("FORMING", "CONFIRMING"):
            fsm_bonus = min(5.0, strength / 20.0)

    # Distance from mean = directional conviction
    distance_score = min(15.0, distance * 300.0)  # Normalized for forex pips

    return min(30.0, distance_score + fsm_bonus + 5.0)


def _score_momentum(window: list[Candle]) -> float:
    """Momentum alignment (0–25). Net directional pressure."""
    if len(window) < 5:
        return 10.0

    recent = window[-5:]
    net_move = recent[-1].close - recent[0].open
    total_range = sum(c.high - c.low for c in recent)

    if total_range <= 0:
        return 5.0

    momentum_ratio = abs(net_move) / total_range

    # Body dominance (strong bodies = momentum)
    body_sum = sum(abs(c.close - c.open) for c in recent)
    body_ratio = body_sum / total_range

    return min(25.0, momentum_ratio * 20.0 + body_ratio * 8.0)


def _score_price_location(window: list[Candle]) -> float:
    """Price location vs recent range (0–20). Extremes = bias."""
    if len(window) < 10:
        return 10.0

    high = max(c.high for c in window)
    low = min(c.low for c in window)
    rng = high - low

    if rng <= 0:
        return 10.0

    current = window[-1].close
    position = (current - low) / rng  # 0 = at low, 1 = at high

    # Extreme positions suggest directional bias
    if position >= 0.8 or position <= 0.2:
        return 18.0  # Strong directional bias
    elif position >= 0.65 or position <= 0.35:
        return 13.0  # Moderate
    else:
        return 7.0   # Mid-range (neutral)


def _score_session_tendency(candles: list[Candle], closed_i: int) -> float:
    """Session directional tendency (0–15)."""
    import time as _time

    if closed_i < 0 or closed_i >= len(candles):
        return 7.0

    try:
        hour = _time.gmtime(candles[closed_i].time).tm_hour
    except (ValueError, OSError):
        return 7.0

    # London/NY = higher directional tendency
    if 7 <= hour < 12:
        return 13.0  # London open = high bias
    elif 12 <= hour < 16:
        return 12.0  # NY overlap = high bias
    elif 16 <= hour < 21:
        return 9.0   # NY afternoon = moderate
    elif 0 <= hour < 7:
        return 5.0   # Asian = low bias
    else:
        return 4.0   # Off session


def _score_volatility_clarity(window: list[Candle]) -> float:
    """Volatility directional clarity (0–10)."""
    if len(window) < 5:
        return 5.0

    # High vol + directional = clear bias
    recent = window[-5:]
    avg_range = sum(c.high - c.low for c in recent) / len(recent)
    net_move = abs(recent[-1].close - recent[0].open)

    if avg_range <= 0:
        return 3.0

    efficiency = net_move / (avg_range * len(recent))

    return min(10.0, efficiency * 20.0 + 2.0)


# ─── REGIME CLASSIFICATION (descriptive only — NOT a filter) ──────────────────

def _classify_regime(structure_score: float, bias_score: float, window: list[Candle]) -> str:
    """
    Descriptive regime label. Does NOT reject anything.

    TREND: strong structure + strong bias
    RANGE: moderate structure + weak bias
    CHOP: weak structure + weak bias
    TRANSITION: mixed signals
    """
    if structure_score >= 55 and bias_score >= 50:
        return "TREND"
    elif structure_score >= 40 and bias_score < 35:
        return "RANGE"
    elif structure_score < 35 and bias_score < 35:
        return "CHOP"
    else:
        return "TRANSITION"
