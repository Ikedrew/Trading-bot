"""
Voter: ConfluenceEngine
Domain: decision_aggregation
Layer: post-evaluation (between voters and trade executor)
Input: VoteResult from each voter + structure_score/regime for weighting
Mutability: NONE
Dependencies: NONE (only VoteResult objects + structure signals)
Signal Type: aggregation-only

The ONLY place where voter outputs interact.
Converts independent voter scores into a single trade decision.

Structure influence is applied HERE as a voter weighting multiplier.
This is the SINGLE authoritative point where structure affects decision balance.

HARD RULES:
  ❌ No voter re-evaluation
  ❌ No raw market recalculation
  ❌ No FSM access (EngineState forbidden)
  ❌ No feature recomputation
  ✅ Consumes VoteResult objects + structure_score/regime for weighting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from core.voters.types import VoteResult

_logger = logging.getLogger(__name__)


# ─── DEFAULT WEIGHTS ──────────────────────────────────────────────────────────

DEFAULT_WEIGHTS = {
    "bias": 0.30,
    "structure": 0.25,
    "volatility": 0.20,
    "spread": 0.15,
    "session": 0.10,
}

# ─── DECISION THRESHOLDS ─────────────────────────────────────────────────────

DEFAULT_THRESHOLD = 0.75
DEFAULT_MIN_CONFIDENCE = 0.6
RISK_FLAG_THRESHOLD = -1.0  # If volatility or spread score below this → risk flag


# ─── OUTPUT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfluenceDecision:
    """
    Final aggregated decision from the voter system.

    action: BUY / SELL / NO_TRADE
    score: weighted aggregate (-2.0 to +2.0 range)
    confidence: aggregated confidence (0.0–1.0)
    risk_flag: True if execution conditions are unfavorable
    conflict_flag: True if directional voters disagree
    breakdown: per-voter weighted contribution
    """

    action: Literal["BUY", "SELL", "NO_TRADE"]
    score: float
    confidence: float
    risk_flag: bool
    conflict_flag: bool
    breakdown: dict[str, float] = field(default_factory=dict)


# ─── NEUTRAL VOTE (used when a voter is not available) ────────────────────────

_NEUTRAL_VOTE = VoteResult(score=0.0, confidence=0.0, reason="not_available")


# ─── STRUCTURE WEIGHT MULTIPLIER (SWM) ───────────────────────────────────────

def compute_structure_weight(structure_score: float, structure_regime: str) -> float:
    """
    Compute structure-based voter weighting multiplier.

    Pure function: deterministic, no state access, no FSM dependency.

    This multiplier adjusts how much each voter's contribution matters
    based on the current structural context. Applied BEFORE aggregation.

    Args:
        structure_score: Rolling weighted structure score (from structure_scoring.py)
        structure_regime: Derived regime (WEAK/BUILDING/CONFIRMED/INVALID)

    Returns:
        Weight multiplier (0.60–1.25 range).
          < 1.0 = structure dampens voter influence (conservative)
          = 1.0 = neutral
          > 1.0 = structure amplifies voter influence (directional)
    """
    # Score-based weighting
    if structure_score < 1.5:
        score_factor = 0.80
    elif structure_score < 3.0:
        score_factor = 0.95
    elif structure_score < 4.5:
        score_factor = 1.10
    else:
        score_factor = 1.20

    # Regime override layer
    regime_factors = {
        "WEAK": 0.85,
        "BUILDING": 1.00,
        "CONFIRMED": 1.15,
        "INVALID": 0.70,
    }
    regime_factor = regime_factors.get(structure_regime, 1.00)

    # Combined (multiplicative)
    swm = score_factor * regime_factor

    # Clamp to safe bounds
    return round(max(0.60, min(1.25, swm)), 3)


# ─── CONFLUENCE ENGINE ────────────────────────────────────────────────────────

def compute_confluence(
    *,
    bias_vote: VoteResult | None = None,
    structure_vote: VoteResult | None = None,
    volatility_vote: VoteResult | None = None,
    spread_vote: VoteResult | None = None,
    session_vote: VoteResult | None = None,
    weights: dict[str, float] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    structure_score: float | None = None,
    structure_regime: str | None = None,
) -> ConfluenceDecision:
    """
    Aggregate voter outputs into a single trade decision.

    Pure function: no state access, no side effects, deterministic.

    Structure weighting is applied to each voter contribution before aggregation.
    This is the SINGLE authoritative point where structure influences decisions.
    When structure_score/regime are None, SWM is 1.0 (neutral — no influence).

    Args:
        bias_vote: BiasVoter output (or None if unavailable)
        structure_vote: StructureVoter output (or None)
        volatility_vote: VolatilityVoter output (or None)
        spread_vote: SpreadVoter output (or None)
        session_vote: SessionVoter output (or None)
        weights: Custom weight overrides (or None for defaults)
        threshold: Score threshold for BUY/SELL decision
        min_confidence: Minimum confidence required for action
        structure_score: Rolling structure score for SWM (None = neutral/disabled)
        structure_regime: Structure regime for SWM (None = neutral/disabled)

    Returns:
        ConfluenceDecision with action, score, confidence, flags, breakdown.
    """
    w = weights or DEFAULT_WEIGHTS

    # Resolve votes (None → neutral)
    votes = {
        "bias": bias_vote or _NEUTRAL_VOTE,
        "structure": structure_vote or _NEUTRAL_VOTE,
        "volatility": volatility_vote or _NEUTRAL_VOTE,
        "spread": spread_vote or _NEUTRAL_VOTE,
        "session": session_vote or _NEUTRAL_VOTE,
    }

    # Structure Weight Multiplier — applied to each voter contribution
    # When structure data is not provided, SWM = 1.0 (neutral, no influence)
    if structure_score is not None and structure_regime is not None:
        swm = compute_structure_weight(structure_score, structure_regime)
    else:
        swm = 1.0

    # 1. Weighted score (with SWM applied to each contribution)
    breakdown: dict[str, float] = {}
    raw_score = 0.0
    final_score = 0.0
    for name, vote in votes.items():
        weight = w.get(name, 0.0)
        raw_contribution = vote.score * weight
        weighted_contribution = raw_contribution * swm
        breakdown[name] = round(weighted_contribution, 4)
        raw_score += raw_contribution
        final_score += weighted_contribution

    raw_score = round(raw_score, 4)
    final_score = round(final_score, 4)
    breakdown["structure_weight_multiplier"] = swm
    breakdown["raw_score"] = raw_score
    breakdown["final_score"] = final_score

    # Structured log for stability debugging (only when SWM is active)
    if swm != 1.0:
        _logger.info(
            "[STRUCTURE_WEIGHT] structure_score=%.3f structure_regime=%s "
            "swm=%.3f raw_confluence=%.4f weighted_confluence=%.4f",
            structure_score if structure_score is not None else 0.0,
            structure_regime if structure_regime is not None else "NONE",
            swm,
            raw_score,
            final_score,
        )

    # 2. Weighted confidence (SWM does NOT affect confidence — only score magnitude)
    total_weight = sum(w.values())
    if total_weight > 0:
        conf_numerator = sum(
            abs(votes[name].score) * votes[name].confidence * w.get(name, 0.0)
            for name in votes
        )
        raw_confidence = conf_numerator / total_weight
        final_confidence = max(0.0, min(1.0, raw_confidence))
    else:
        final_confidence = 0.0

    # 3. Conflict detection
    bias_sign = 1 if votes["bias"].score > 0 else (-1 if votes["bias"].score < 0 else 0)
    structure_sign = 1 if votes["structure"].score > 0 else (-1 if votes["structure"].score < 0 else 0)
    conflict_flag = (bias_sign != 0 and structure_sign != 0 and bias_sign != structure_sign)

    # 4. Risk flag (execution conditions unfavorable)
    risk_flag = (
        votes["volatility"].score < RISK_FLAG_THRESHOLD or
        votes["spread"].score < RISK_FLAG_THRESHOLD
    )

    # 5. Decision rules
    if final_score > threshold and final_confidence >= min_confidence and not risk_flag:
        action: Literal["BUY", "SELL", "NO_TRADE"] = "BUY"
    elif final_score < -threshold and final_confidence >= min_confidence and not risk_flag:
        action = "SELL"
    else:
        action = "NO_TRADE"

    return ConfluenceDecision(
        action=action,
        score=final_score,
        confidence=round(final_confidence, 4),
        risk_flag=risk_flag,
        conflict_flag=conflict_flag,
        breakdown=breakdown,
    )
