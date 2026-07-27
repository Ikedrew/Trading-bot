"""
Voter: VolatilityVoter
Domain: market_volatility
Layer: evaluation
Input: StateSnapshot (FeatureEngine-backed only)
Mutability: NONE
Dependencies: NONE
Signal Type: measurement-only

Assesses market noise vs structure clarity.
Prevents trades during unstable or chaotic conditions.

VOTER_SIGNAL_DOMAIN = {
    "name": "VolatilityVoter",
    "domain": "market_volatility",
    "primary_signals": ["m5_atr_14", "m5_atr_ratio", "candle_overlap_ratio"],
    "explicitly_excluded_signals": [
        "bias_phase", "bias_strength", "spread",
        "m5_structure_clarity", "volatility_filter",
        "regime_state"
    ]
}

NOTE: candle_overlap_ratio is shared with StructureVoter.
StructureVoter interprets it as "is structure clean?"
VolatilityVoter interprets it as "is market choppy/noisy?"
Same measurement, different domain interpretation. Acceptable per contract.
"""

from __future__ import annotations

from core.state.snapshot import StateSnapshot
from core.voters.types import VoteResult


class VolatilityVoter:
    """
    Market noise and stability classifier.

    Answers ONLY: "How stable and tradable are current volatility conditions?"
    Uses ATR ratio (expansion/contraction) and overlap ratio (chop detection).

    Does NOT measure: direction, structure quality, execution cost, or session timing.
    """

    def evaluate(self, snapshot: StateSnapshot) -> VoteResult:
        """
        Produce a volatility quality vote from ATR and overlap metrics.

        Returns:
            VoteResult with score (-2.0 to +2.0), confidence (0.0-1.0), reason.
        """
        atr_ratio = snapshot.m5_atr_ratio
        overlap = snapshot.candle_overlap_ratio

        score = 0.0
        reasons: list[str] = []

        # ATR ratio assessment (current vs average)
        # Ideal: 0.8–1.3 (normal volatility)
        # Low (<0.7): compressed, breakout potential but low opportunity now
        # High (>1.5): chaotic, risky
        if 0.8 <= atr_ratio <= 1.3:
            score += 0.8
            reasons.append(f"atr_ratio={atr_ratio:.2f}(stable)")
        elif 0.7 <= atr_ratio < 0.8:
            score += 0.3
            reasons.append(f"atr_ratio={atr_ratio:.2f}(compressed)")
        elif 1.3 < atr_ratio <= 1.5:
            score += 0.2
            reasons.append(f"atr_ratio={atr_ratio:.2f}(elevated)")
        elif atr_ratio < 0.7:
            score -= 0.3
            reasons.append(f"atr_ratio={atr_ratio:.2f}(very_low)")
        elif 1.5 < atr_ratio <= 2.0:
            score -= 0.8
            reasons.append(f"atr_ratio={atr_ratio:.2f}(high)")
        else:  # > 2.0
            score -= 1.5
            reasons.append(f"atr_ratio={atr_ratio:.2f}(extreme)")

        # Overlap ratio assessment (chop detection)
        # Low overlap = clean directional moves
        # High overlap = choppy, indecisive
        if overlap <= 0.2:
            score += 0.5
            reasons.append(f"overlap={overlap:.2f}(clean)")
        elif overlap <= 0.4:
            score += 0.1
        elif overlap <= 0.6:
            score -= 0.3
            reasons.append(f"overlap={overlap:.2f}(moderate_chop)")
        else:
            score -= 0.8
            reasons.append(f"overlap={overlap:.2f}(heavy_chop)")

        # Clamp
        score = max(-2.0, min(2.0, score))

        # Confidence: higher when signal is clear (extreme values = high confidence)
        if atr_ratio > 1.8 or atr_ratio < 0.6 or overlap > 0.7:
            confidence = 0.85  # High confidence that conditions are BAD
        elif 0.9 <= atr_ratio <= 1.2 and overlap <= 0.3:
            confidence = 0.8  # High confidence that conditions are GOOD
        else:
            confidence = 0.6  # Moderate certainty

        # Classification
        if score > 0.5:
            classification = "stable"
        elif score > -0.3:
            classification = "moderate"
        else:
            classification = "chaotic"

        reasons.append(classification)
        reason = " | ".join(reasons)

        return VoteResult(score=round(score, 3), confidence=round(confidence, 3), reason=reason)
