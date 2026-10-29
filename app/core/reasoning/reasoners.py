"""
Evidence Generators — small reasoning components that inspect assessment fields.

Each reasoner contributes supporting or contradicting evidence based on
existing analytical information. They do NOT recalculate anything.

Architecture:
    ReasoningEngine
        ├── TrendReasoner
        ├── StructureReasoner
        ├── RegimeReasoner
        ├── MomentumReasoner
        ├── HTFReasoner
        └── PatternReasoner

Rules:
    - Read ONLY from OpportunityAssessment fields
    - Never return True/False (not a gate)
    - Output is human-readable explanation
    - Never modify assessment or any upstream state
"""

from __future__ import annotations

from typing import Any


# ─── THRESHOLDS (explanation triggers, NOT trading thresholds) ─────────────────
# These determine when evidence is NOTEWORTHY enough to mention.
# They have ZERO effect on trading decisions.

_STRONG_ALIGNMENT = 0.70
_WEAK_ALIGNMENT = 0.35
_HIGH_QUALITY = 0.70
_LOW_QUALITY = 0.30
_HIGH_CONFIDENCE = 0.65
_LOW_CONFIDENCE = 0.35


# ─── TREND REASONER ──────────────────────────────────────────────────────────

def reason_trend(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate trend alignment evidence.

    Reads: trend_alignment, bias_alignment
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    trend = getattr(assessment, "trend_alignment", 0.5)
    bias = getattr(assessment, "bias_alignment", 0.5)
    side = getattr(assessment, "side", "")

    if trend >= _STRONG_ALIGNMENT:
        supporting.append(f"Price aligned with EMA trend ({side} direction)")
    elif trend <= _WEAK_ALIGNMENT:
        contradicting.append(f"Counter-trend entry — price against EMA")

    if bias >= _STRONG_ALIGNMENT:
        supporting.append("Bias FSM confirms directional alignment")
    elif bias <= _WEAK_ALIGNMENT:
        contradicting.append("Bias FSM contradicts or expired — weak directional conviction")

    return supporting, contradicting


# ─── STRUCTURE REASONER ───────────────────────────────────────────────────────

def reason_structure(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate market structure evidence.

    Reads: market_state, market_state_confidence, chop_clarity, delta_stability
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    state = getattr(assessment, "market_state", "TRANSITIONAL")
    confidence = getattr(assessment, "market_state_confidence", 0.5)
    chop = getattr(assessment, "chop_clarity", 0.5)
    stability = getattr(assessment, "delta_stability", 0.5)

    if state == "STRUCTURED":
        supporting.append("Market state is STRUCTURED — clear directional behaviour")
        if confidence >= _HIGH_CONFIDENCE:
            supporting.append(f"High structure confidence ({confidence:.0%})")
    elif state == "CHOP":
        contradicting.append("Market state is CHOP — erratic price action")
    else:
        # TRANSITIONAL
        if confidence < _LOW_CONFIDENCE:
            contradicting.append(f"TRANSITIONAL state with low confidence ({confidence:.0%})")
        else:
            supporting.append("Market transitioning — potential structure forming")

    if chop >= _HIGH_QUALITY:
        supporting.append("Low candle overlap — clean price movement")
    elif chop <= _LOW_QUALITY:
        contradicting.append("High candle overlap — choppy conditions")

    if stability >= _STRONG_ALIGNMENT:
        supporting.append("Score delta stable — consistent signal")
    elif stability <= _WEAK_ALIGNMENT:
        contradicting.append("Score delta unstable — signal flickering")

    return supporting, contradicting


# ─── REGIME REASONER ──────────────────────────────────────────────────────────

def reason_regime(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate regime classification evidence.

    Reads: regime, regime_confidence, selected_strategy, strategy_confidence
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    regime = getattr(assessment, "regime", "TRANSITIONAL")
    regime_conf = getattr(assessment, "regime_confidence", 0.5)
    strategy = getattr(assessment, "selected_strategy", None)
    strat_conf = getattr(assessment, "strategy_confidence", 0.0)
    side = getattr(assessment, "side", "")

    if regime == "TRENDING":
        if side == "BUY":
            supporting.append("Regime is TRENDING — supports directional momentum (BUY)")
        else:
            supporting.append("Regime is TRENDING — supports directional momentum (SELL)")
    elif regime == "RANGE":
        if strategy == "REVERSAL":
            supporting.append("Regime is RANGE — supports mean-reversion / reversal strategy")
        else:
            contradicting.append("Regime is RANGE — continuation setups have lower probability")
    elif regime == "TRANSITIONAL":
        contradicting.append(f"Regime is TRANSITIONAL — uncertain market phase (conf={regime_conf:.0%})")

    if strategy and strat_conf >= _HIGH_CONFIDENCE:
        supporting.append(f"Strategy {strategy} selected with high confidence ({strat_conf:.0%})")
    elif strategy and strat_conf < _LOW_CONFIDENCE:
        contradicting.append(f"Strategy {strategy} selected but low confidence ({strat_conf:.0%})")
    elif strategy is None:
        contradicting.append("No strategy classified — using global fallback weights")

    return supporting, contradicting


# ─── MOMENTUM REASONER ────────────────────────────────────────────────────────

def reason_momentum(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate momentum / volatility evidence.

    Reads: volatility_quality, confirmation_pre, score_delta
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    vol_quality = getattr(assessment, "volatility_quality", 0.5)
    confirm = getattr(assessment, "confirmation_pre", 0.5)
    delta = getattr(assessment, "score_delta", 0.0)

    if vol_quality >= _HIGH_QUALITY:
        supporting.append("Directional volatility quality high — momentum behind the move")
    elif vol_quality <= _LOW_QUALITY:
        contradicting.append("Volatility quality low — no clear directional thrust")

    if confirm >= _HIGH_QUALITY:
        supporting.append("Strong candle body quality — conviction in close")
    elif confirm <= _LOW_QUALITY:
        contradicting.append("Weak candle body — indecision / doji-like close")

    if delta > 0.05:
        supporting.append(f"Strategy weighting improves score by +{delta:.2f} — setup matches classified edge")
    elif delta < -0.03:
        contradicting.append(f"Strategy weighting reduces score by {delta:.2f} — setup doesn't fit classified edge")

    return supporting, contradicting


# ─── HTF REASONER ─────────────────────────────────────────────────────────────

def reason_htf(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate higher-timeframe alignment evidence.

    Reads: htf_alignment, h4_alignment
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    htf = getattr(assessment, "htf_alignment", 0.5)
    h4 = getattr(assessment, "h4_alignment", 0.5)

    if htf >= _STRONG_ALIGNMENT:
        supporting.append("H1 bias + M15 structure aligned with trade direction")
    elif htf <= _WEAK_ALIGNMENT:
        contradicting.append("H1/M15 timeframes contradict trade direction")

    if h4 >= _STRONG_ALIGNMENT:
        supporting.append("H4 macro regime supports trade direction")
    elif h4 <= _WEAK_ALIGNMENT:
        contradicting.append("H4 regime adverse — counter-trend at macro level")
    elif 0.4 <= h4 <= 0.55:
        # Neutral H4 — mention for context
        pass  # Not noteworthy enough to include

    return supporting, contradicting


# ─── PATTERN REASONER ─────────────────────────────────────────────────────────

def reason_pattern(assessment: Any) -> tuple[list[str], list[str]]:
    """
    Evaluate pattern identity and quality evidence.

    Reads: pattern, pattern_quality, side
    Returns: (supporting, contradicting)
    """
    supporting: list[str] = []
    contradicting: list[str] = []

    pattern = getattr(assessment, "pattern", "")
    quality = getattr(assessment, "pattern_quality", 0.5)

    if quality >= 1.0:
        supporting.append(f"Strong pattern detected: {pattern}")
    elif quality >= 0.5:
        supporting.append(f"Pattern detected: {pattern} (moderate strength)")
    else:
        contradicting.append(f"Weak pattern: {pattern} — low structural quality")

    return supporting, contradicting
