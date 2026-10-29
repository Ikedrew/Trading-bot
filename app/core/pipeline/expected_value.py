"""
Expected Value Engine — Comparative trade quality ranking metric.

EV = (P_success × reward) - (P_failure × risk)

CRITICAL INTERPRETATION RULE:
    EV is COMPARATIVE, not absolute.
    EV > 0 means "this setup ranks higher than alternatives under current conditions"
    EV does NOT mean "this trade is guaranteed profitable"
    EV magnitude = relative opportunity strength, NOT certainty of profit

The system selects the best available trade in the current distribution
of opportunities, not a guaranteed winning trade.

Where:
    P_success = derived from neutral score + strategy confidence + market state
    reward = projected move (TP distance from entry)
    risk = structural stop distance (SL distance from entry)
    P_failure = uncertainty-adjusted complement of P_success

This module does NOT:
    - Modify scores or strategy classification
    - Place trades or interact with execution
    - Replace existing layers
    - Predict per-trade outcomes
    - Guarantee profitability

It ONLY:
    - Computes expected value from upstream inputs
    - Applies market-state uncertainty dampening
    - Ranks trade quality relative to current conditions

Design: deterministic, no learning, no adaptation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.pipeline.market_state_engine import MarketState, MarketStateResult


# ─── UNCERTAINTY COEFFICIENTS (per market state) ──────────────────────────────
# These dampen P_success based on market stability.
# STRUCTURED = minimal dampening, CHOP = severe dampening.

_UNCERTAINTY_DAMPENING = {
    MarketState.STRUCTURED: 0.05,       # 5% probability reduction
    MarketState.TRANSITIONAL: 0.20,     # 20% probability reduction
    MarketState.CHOP: 0.25,             # 25% probability reduction (allows EV to remain positive for strong setups)
}

# Minimum EV required to allow execution (must be positive + buffer)
_MIN_EV_THRESHOLD = 0.0  # EV must be > 0 (strictly positive)

# P_success floor/ceiling
_P_SUCCESS_MIN = 0.10    # Never assume less than 10% chance
_P_SUCCESS_MAX = 0.85    # Never assume more than 85% chance (overconfidence cap)


# ─── EV RESULT ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExpectedValueResult:
    """Immutable output of expected value calculation."""
    ev: float                       # Expected value (positive = edge exists)
    p_success: float                # Probability of success (0–1)
    p_failure: float                # Probability of failure (0–1)
    reward: float                   # Projected reward (TP distance)
    risk: float                     # Structural risk (SL distance)
    rr_effective: float             # Derived RR = reward / risk
    uncertainty_dampening: float    # Probability reduction applied
    ev_positive: bool               # True if EV > 0
    reasoning: str


# ─── MAIN COMPUTATION ─────────────────────────────────────────────────────────

def compute_expected_value(
    *,
    score_neutral: float | None = None,
    strategy_confidence: float | None = None,
    market_state_result: MarketStateResult,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    confirmation_score: float = 1.0,
    assessment: Any | None = None,
    probability_estimate: Any | None = None,
) -> ExpectedValueResult:
    """
    Compute expected value for a potential trade.

    When probability_estimate is provided (from ProbabilityEstimator),
    it is used directly. Otherwise, probability is computed inline for
    backward compatibility.

    Args:
        score_neutral: Global-weighted composite (legacy — used if no estimate provided)
        strategy_confidence: Unused (preserved for API compatibility)
        market_state_result: Output from MarketStateEngine
        entry_price: Proposed entry price
        stop_loss: Proposed SL price
        take_profit: Proposed TP price
        confirmation_score: Candle quality score (0–1, used if no estimate provided)
        assessment: Optional OpportunityAssessment
        probability_estimate: Optional ProbabilityEstimate (from ProbabilityEstimator)

    Returns:
        ExpectedValueResult with EV, probabilities, and pass/fail
    """
    # Resolve analytical fields from assessment if not provided explicitly
    if assessment is not None:
        if score_neutral is None:
            score_neutral = assessment.score_neutral

    if score_neutral is None:
        score_neutral = 0.0

    # ─── COMPUTE DISTANCES ────────────────────────────────────────────
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)

    if risk <= 0:
        return ExpectedValueResult(
            ev=0.0, p_success=0.0, p_failure=1.0,
            reward=reward, risk=0.0, rr_effective=0.0,
            uncertainty_dampening=0.0, ev_positive=False,
            reasoning="Invalid risk distance (SL = entry)",
        )

    rr_effective = round(reward / risk, 3)

    # ─── PROBABILITY ──────────────────────────────────────────────────
    # PRIMARY: use ProbabilityEstimate if provided (new architecture)
    # FALLBACK: compute inline (backward compatibility)
    state = market_state_result.state

    if probability_estimate is not None:
        p_success = getattr(probability_estimate, "p_success", 0.0)
        p_failure = getattr(probability_estimate, "p_failure", 1.0)
        dampening = getattr(probability_estimate, "uncertainty_dampening", 0.0)
    else:
        # Inline fallback (preserves behaviour for callers not yet migrated)
        p_base = score_neutral
        dampening = _UNCERTAINTY_DAMPENING.get(state, 0.20)
        confirmation_modifier = 0.5 + (0.5 * confirmation_score)
        p_success = p_base * confirmation_modifier * (1.0 - dampening)
        p_success = max(_P_SUCCESS_MIN, min(_P_SUCCESS_MAX, p_success))
        p_success = round(p_success, 4)
        p_failure = round(1.0 - p_success, 4)

    # ─── COMPUTE EXPECTED VALUE ───────────────────────────────────────
    ev = (p_success * reward) - (p_failure * risk)
    ev = round(ev, 6)

    ev_positive = ev > _MIN_EV_THRESHOLD

    # ─── BUILD REASONING ──────────────────────────────────────────────
    if ev_positive:
        reasoning = (
            f"EV={ev:.6f} (positive — ranks above threshold under current uncertainty) | "
            f"P_win={p_success:.3f} × reward={reward:.5f} "
            f"- P_loss={p_failure:.3f} × risk={risk:.5f} | "
            f"RR={rr_effective:.2f} | dampening={dampening:.0%} ({state.value}) | "
            f"context=comparative_ranking_metric"
        )
    else:
        reasoning = (
            f"EV={ev:.6f} (negative — does not rank above alternatives) | "
            f"P_win={p_success:.3f} × reward={reward:.5f} "
            f"- P_loss={p_failure:.3f} × risk={risk:.5f} | "
            f"RR={rr_effective:.2f} | dampening={dampening:.0%} ({state.value}) | "
            f"context=comparative_ranking_metric"
        )

    return ExpectedValueResult(
        ev=ev,
        p_success=p_success,
        p_failure=p_failure,
        reward=reward,
        risk=risk,
        rr_effective=rr_effective,
        uncertainty_dampening=dampening,
        ev_positive=ev_positive,
        reasoning=reasoning,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DUAL EV: SYNTHETIC + EMPIRICAL (observability layer)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DualEVComparison:
    """
    Side-by-side synthetic and empirical EV for one decision.

    Observational only. Never affects execution when USE_EMPIRICAL_PROBABILITY=False.
    """
    # Synthetic (production)
    synthetic_p: float = 0.0
    synthetic_ev: float = 0.0
    synthetic_positive: bool = False

    # Empirical (research)
    empirical_p: float = 0.0
    empirical_ev: float = 0.0
    empirical_positive: bool = False

    # Research context
    candidate_match: bool = False
    candidate_id: str = ""
    walk_forward_survivor: bool = False
    research_confidence: str = "NONE"

    # Difference metrics
    probability_difference: float = 0.0  # empirical - synthetic
    ev_difference: float = 0.0           # empirical_ev - synthetic_ev
    execution_difference: str = ""       # "AGREE" / "RESEARCH_WOULD_EXECUTE" / "RESEARCH_WOULD_REJECT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthetic_p": round(self.synthetic_p, 4),
            "synthetic_ev": round(self.synthetic_ev, 6),
            "synthetic_positive": self.synthetic_positive,
            "empirical_p": round(self.empirical_p, 4),
            "empirical_ev": round(self.empirical_ev, 6),
            "empirical_positive": self.empirical_positive,
            "candidate_match": self.candidate_match,
            "candidate_id": self.candidate_id,
            "walk_forward_survivor": self.walk_forward_survivor,
            "research_confidence": self.research_confidence,
            "probability_difference": round(self.probability_difference, 4),
            "ev_difference": round(self.ev_difference, 6),
            "execution_difference": self.execution_difference,
        }


def compute_dual_ev(
    *,
    synthetic_result: ExpectedValueResult,
    pattern_name: str = "",
    regime: str = "",
    market_state: str = "",
    symbol: str = "",
    timestamp_utc: str = "",
    components: dict[str, float] | None = None,
    reward: float = 0.0,
    risk: float = 0.0,
) -> DualEVComparison:
    """
    Compute empirical EV alongside synthetic EV for observability comparison.

    Uses the Research Assessment provider to look up validated candidate data.
    Computes what the empirical EV WOULD be, without affecting execution.

    Args:
        synthetic_result: Already-computed production EV result.
        pattern_name: Pattern name for candidate lookup.
        regime: Current regime for lookup.
        market_state: Market state string.
        symbol: Trading symbol.
        timestamp_utc: Decision timestamp.
        components: Component scores dict.
        reward: TP distance (from synthetic result).
        risk: SL distance (from synthetic result).

    Returns:
        DualEVComparison with both models' outputs and difference metrics.
    """
    try:
        from core.research_assessment.provider import get_research_assessment
        assessment = get_research_assessment(
            pattern_name=pattern_name,
            regime=regime,
            market_state=market_state,
            symbol=symbol,
            timestamp_utc=timestamp_utc,
            components=components,
        )
    except Exception:
        # Research assessment unavailable — return synthetic-only comparison
        return DualEVComparison(
            synthetic_p=synthetic_result.p_success,
            synthetic_ev=synthetic_result.ev,
            synthetic_positive=synthetic_result.ev_positive,
            execution_difference="AGREE",
        )

    # Compute empirical EV using research win rate
    if assessment.candidate_match and assessment.historical_win_rate > 0:
        empirical_p = assessment.historical_win_rate
        # Apply same bounds as synthetic
        empirical_p = max(_P_SUCCESS_MIN, min(_P_SUCCESS_MAX, empirical_p))
        empirical_p_failure = 1.0 - empirical_p

        if risk > 0:
            empirical_ev = (empirical_p * reward) - (empirical_p_failure * risk)
        else:
            empirical_ev = 0.0
        empirical_positive = empirical_ev > _MIN_EV_THRESHOLD
    else:
        # No match — empirical equals synthetic (no information gain)
        empirical_p = synthetic_result.p_success
        empirical_ev = synthetic_result.ev
        empirical_positive = synthetic_result.ev_positive

    # Execution difference
    if synthetic_result.ev_positive == empirical_positive:
        exec_diff = "AGREE"
    elif empirical_positive and not synthetic_result.ev_positive:
        exec_diff = "RESEARCH_WOULD_EXECUTE"
    else:
        exec_diff = "RESEARCH_WOULD_REJECT"

    return DualEVComparison(
        synthetic_p=synthetic_result.p_success,
        synthetic_ev=synthetic_result.ev,
        synthetic_positive=synthetic_result.ev_positive,
        empirical_p=round(empirical_p, 4),
        empirical_ev=round(empirical_ev, 6),
        empirical_positive=empirical_positive,
        candidate_match=assessment.candidate_match,
        candidate_id=assessment.candidate_id,
        walk_forward_survivor=assessment.walk_forward_survivor,
        research_confidence=assessment.research_confidence,
        probability_difference=round(empirical_p - synthetic_result.p_success, 4),
        ev_difference=round(empirical_ev - synthetic_result.ev, 6),
        execution_difference=exec_diff,
    )
