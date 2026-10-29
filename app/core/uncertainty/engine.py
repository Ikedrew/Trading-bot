"""
Uncertainty Engine — consolidates ambiguity signals into a single measurement.

Reads from OpportunityAssessment and DecisionReasoning.
Never recalculates upstream analysis. Never makes trading decisions. Pure measurement.

Sources of uncertainty (already exist indirectly):
    - Regime classification confidence (low = uncertain)
    - Market state confidence (TRANSITIONAL/CHOP = uncertain)
    - HTF disagreement (htf_alignment + h4_alignment low = timeframes conflict)
    - Volatility quality (low = no clear directional thrust)
    - Confirmation weakness (low candle body quality = indecision)
    - Score instability (delta_stability low = signal flickering)
    - Strategy confidence (low or no strategy = classification uncertain)
    - Conflicting evidence (reasoning.contradicting_evidence count = multiple disagreements)

Architecture:
    compute_uncertainty(assessment, reasoning)
        ├── _measure_regime_uncertainty(assessment)
        ├── _measure_structure_uncertainty(assessment)
        ├── _measure_htf_uncertainty(assessment)
        ├── _measure_momentum_uncertainty(assessment)
        ├── _measure_confirmation_uncertainty(assessment)
        └── _measure_conflicting_evidence(reasoning)
"""

from __future__ import annotations

from typing import Any

from core.uncertainty.model import UncertaintyAssessment


# ─── DIMENSION WEIGHTS (contribution to composite score) ──────────────────────
# These control how much each dimension contributes to overall uncertainty.
# They do NOT affect trading decisions — only the measurement.

_W_REGIME = 0.22
_W_STRUCTURE = 0.18
_W_HTF = 0.18
_W_MOMENTUM = 0.17
_W_CONFIRMATION = 0.12
_W_CONFLICTING = 0.13   # Reasoning-derived: conflicting evidence weight

# ─── CONFIDENCE MODIFIER SCALING ─────────────────────────────────────────────
# Maps uncertainty_score to a suggested confidence reduction.
# Maximum reduction at uncertainty=1.0 is -0.30 (30% confidence penalty).
_MAX_CONFIDENCE_PENALTY = 0.30


def compute_uncertainty(*, assessment: Any, reasoning: Any = None) -> UncertaintyAssessment:
    """
    Compute uncertainty measurement from assessment + optional reasoning.

    Consumes existing knowledge only — never recalculates bias, regime,
    structure, momentum, or confidence.

    Never raises — returns maximum uncertainty on error.
    Never makes trading decisions.

    Args:
        assessment: OpportunityAssessment (frozen analytical snapshot)
        reasoning: Optional DecisionReasoning (for conflicting evidence signal)

    Returns:
        UncertaintyAssessment (frozen measurement)
    """
    if assessment is None:
        return UncertaintyAssessment(
            uncertainty_score=1.0,
            confidence_modifier=-_MAX_CONFIDENCE_PENALTY,
            uncertainty_factors=("No assessment available",),
        )

    try:
        return _build_uncertainty(assessment, reasoning)
    except Exception:
        return UncertaintyAssessment(
            uncertainty_score=1.0,
            confidence_modifier=-_MAX_CONFIDENCE_PENALTY,
            uncertainty_factors=("Uncertainty computation failed",),
            metadata={"error": True},
        )


def _build_uncertainty(assessment: Any, reasoning: Any) -> UncertaintyAssessment:
    """Internal computation — may raise."""
    factors: list[str] = []

    # ─── REGIME UNCERTAINTY ───────────────────────────────────────────
    regime_u = _measure_regime_uncertainty(assessment, factors)

    # ─── STRUCTURE UNCERTAINTY ────────────────────────────────────────
    structure_u = _measure_structure_uncertainty(assessment, factors)

    # ─── HTF UNCERTAINTY ──────────────────────────────────────────────
    htf_u = _measure_htf_uncertainty(assessment, factors)

    # ─── MOMENTUM UNCERTAINTY ─────────────────────────────────────────
    momentum_u = _measure_momentum_uncertainty(assessment, factors)

    # ─── CONFIRMATION UNCERTAINTY ─────────────────────────────────────
    confirm_u = _measure_confirmation_uncertainty(assessment, factors)

    # ─── CONFLICTING EVIDENCE (from reasoning) ────────────────────────
    conflict_u = _measure_conflicting_evidence(reasoning, factors)

    # ─── COMPOSITE SCORE ──────────────────────────────────────────────
    composite = (
        regime_u * _W_REGIME
        + structure_u * _W_STRUCTURE
        + htf_u * _W_HTF
        + momentum_u * _W_MOMENTUM
        + confirm_u * _W_CONFIRMATION
        + conflict_u * _W_CONFLICTING
    )
    composite = round(min(1.0, max(0.0, composite)), 4)

    # ─── CONFIDENCE MODIFIER ──────────────────────────────────────────
    # Linear mapping: uncertainty 0→0, uncertainty 1→-MAX_PENALTY
    modifier = round(-composite * _MAX_CONFIDENCE_PENALTY, 4)

    return UncertaintyAssessment(
        uncertainty_score=composite,
        confidence_modifier=modifier,
        uncertainty_factors=tuple(factors),
        regime_uncertainty=round(regime_u, 4),
        structure_uncertainty=round(structure_u, 4),
        htf_uncertainty=round(htf_u, 4),
        momentum_uncertainty=round(momentum_u, 4),
        confirmation_uncertainty=round(confirm_u, 4),
        metadata={
            "symbol": getattr(assessment, "symbol", ""),
            "cycle_id": getattr(assessment, "cycle_id", 0),
            "conflicting_evidence_count": len(getattr(reasoning, "contradicting_evidence", ())) if reasoning else 0,
        },
    )


# ─── DIMENSION MEASURERS ──────────────────────────────────────────────────────

def _measure_regime_uncertainty(assessment: Any, factors: list[str]) -> float:
    """
    Regime uncertainty: how unclear is the regime classification?

    High uncertainty when:
        - regime_confidence is low
        - regime is TRANSITIONAL
        - strategy_confidence is low or no strategy selected
    """
    regime = getattr(assessment, "regime", "TRANSITIONAL")
    regime_conf = getattr(assessment, "regime_confidence", 0.5)
    strat_conf = getattr(assessment, "strategy_confidence", 0.0)
    selected = getattr(assessment, "selected_strategy", None)

    u = 0.0

    # Regime confidence (inverted: low confidence = high uncertainty)
    u += (1.0 - regime_conf) * 0.4

    # TRANSITIONAL regime adds uncertainty
    if regime == "TRANSITIONAL":
        u += 0.35
        factors.append(f"Regime TRANSITIONAL (confidence={regime_conf:.0%})")
    elif regime == "RANGE":
        u += 0.15

    # Strategy classification uncertainty
    if selected is None:
        u += 0.25
        factors.append("No strategy classified — classification uncertain")
    elif strat_conf < 0.4:
        u += 0.15
        factors.append(f"Strategy confidence low ({strat_conf:.0%})")

    return min(1.0, u)


def _measure_structure_uncertainty(assessment: Any, factors: list[str]) -> float:
    """
    Structure uncertainty: how unclear is market structure?

    High uncertainty when:
        - market_state is not STRUCTURED
        - market_state_confidence is low
        - delta_stability is low (signal flickering)
        - chop_clarity is low (overlapping candles)
    """
    state = getattr(assessment, "market_state", "TRANSITIONAL")
    state_conf = getattr(assessment, "market_state_confidence", 0.5)
    stability = getattr(assessment, "delta_stability", 0.5)
    chop = getattr(assessment, "chop_clarity", 0.5)

    u = 0.0

    # Market state classification
    if state == "CHOP":
        u += 0.40
        factors.append("Market state CHOP — no clear structure")
    elif state == "TRANSITIONAL":
        u += 0.25
        if state_conf < 0.4:
            factors.append(f"TRANSITIONAL state with low confidence ({state_conf:.0%})")

    # State confidence (inverted)
    u += (1.0 - state_conf) * 0.25

    # Delta stability (inverted: low stability = high uncertainty)
    if stability < 0.4:
        u += 0.20
        factors.append("Score delta unstable — signal flickering")

    # Chop clarity (inverted: low clarity = high overlap = uncertain)
    if chop < 0.35:
        u += 0.15
        factors.append("High candle overlap — choppy structure")

    return min(1.0, u)


def _measure_htf_uncertainty(assessment: Any, factors: list[str]) -> float:
    """
    HTF uncertainty: how much do timeframes disagree?

    High uncertainty when:
        - htf_alignment is low (H1/M15 contradict)
        - h4_alignment is low (H4 contradicts)
        - Both are in neutral zone (no clear signal either way)
    """
    htf = getattr(assessment, "htf_alignment", 0.5)
    h4 = getattr(assessment, "h4_alignment", 0.5)

    u = 0.0

    # HTF disagreement (below 0.5 = contradiction, exactly 0.5 = neutral/unknown)
    if htf < 0.35:
        u += 0.45
        factors.append("H1/M15 timeframes contradict trade direction")
    elif htf < 0.50:
        u += 0.20

    # H4 disagreement
    if h4 < 0.35:
        u += 0.40
        factors.append("H4 macro regime contradicts trade direction")
    elif h4 < 0.50:
        u += 0.15

    # Neutral zone (no clear signal from HTF — uncertainty from absence)
    if 0.45 <= htf <= 0.55 and 0.45 <= h4 <= 0.55:
        u += 0.20
        factors.append("Higher timeframes neutral — no directional guidance")

    return min(1.0, u)


def _measure_momentum_uncertainty(assessment: Any, factors: list[str]) -> float:
    """
    Momentum uncertainty: how weak/contradictory is directional momentum?

    High uncertainty when:
        - volatility_quality is low (no thrust)
        - trend_alignment is low (counter-trend)
        - bias_alignment is low (bias contradicts)
    """
    vol = getattr(assessment, "volatility_quality", 0.5)
    trend = getattr(assessment, "trend_alignment", 0.5)
    bias = getattr(assessment, "bias_alignment", 0.5)

    u = 0.0

    # Volatility quality (inverted)
    if vol < 0.3:
        u += 0.35
        factors.append("Volatility quality low — no directional thrust")
    elif vol < 0.5:
        u += 0.15

    # Trend alignment (inverted)
    if trend < 0.3:
        u += 0.30
        factors.append("Counter-trend entry — price against EMA")
    elif trend < 0.5:
        u += 0.10

    # Bias alignment (inverted)
    if bias < 0.3:
        u += 0.25
        factors.append("Bias FSM contradicts trade direction")
    elif bias < 0.5:
        u += 0.10

    return min(1.0, u)


def _measure_confirmation_uncertainty(assessment: Any, factors: list[str]) -> float:
    """
    Confirmation uncertainty: how weak is candle quality evidence?

    High uncertainty when:
        - confirmation_pre is low (doji-like, indecisive)
        - pattern_quality is low (weak pattern)
    """
    confirm = getattr(assessment, "confirmation_pre", 0.5)
    quality = getattr(assessment, "pattern_quality", 0.5)

    u = 0.0

    # Confirmation weakness
    if confirm < 0.3:
        u += 0.50
        factors.append("Weak candle body — indecision at close")
    elif confirm < 0.5:
        u += 0.20

    # Pattern quality
    if quality < 0.5:
        u += 0.35
        factors.append("Weak pattern quality — structural evidence thin")
    elif quality < 0.7:
        u += 0.10

    return min(1.0, u)


# ─── CONFLICTING EVIDENCE (reasoning-derived) ─────────────────────────────────

def _measure_conflicting_evidence(reasoning: Any, factors: list[str]) -> float:
    """
    Conflicting evidence uncertainty: how many contradictions exist?

    High uncertainty when:
        - reasoning.contradicting_evidence has many entries
        - Alternative thesis exists (market could be doing something else)

    This does NOT recalculate anything — it counts contradictions
    already identified by the Reasoning Engine.
    """
    if reasoning is None:
        return 0.0  # No reasoning available — cannot measure conflicts

    contradictions = getattr(reasoning, "contradicting_evidence", ())
    alternative = getattr(reasoning, "alternative_thesis", None)

    n_contradictions = len(contradictions) if contradictions else 0
    u = 0.0

    # Contradiction count → uncertainty
    if n_contradictions >= 5:
        u += 0.60
        factors.append(f"Multiple conflicting signals ({n_contradictions} contradictions)")
    elif n_contradictions >= 3:
        u += 0.40
        factors.append(f"Several conflicting signals ({n_contradictions} contradictions)")
    elif n_contradictions >= 1:
        u += 0.15
        # Only report if meaningful (1-2 contradictions are normal)

    # Alternative thesis existence adds uncertainty
    if alternative:
        u += 0.25
        factors.append("Alternative market interpretation exists")

    return min(1.0, u)
