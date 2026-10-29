"""
Voter: SpreadVoter
Domain: execution_cost
Layer: evaluation
Input: StateSnapshot (FeatureEngine-backed only)
Mutability: NONE
Dependencies: NONE
Signal Type: measurement-only

Evaluates execution cost impact from current spread conditions.
Detects poor trade timing due to widened spreads.

VOTER_SIGNAL_DOMAIN = {
    "name": "SpreadVoter",
    "domain": "execution_cost",
    "primary_signals": ["spread", "m5_atr_14"],
    "explicitly_excluded_signals": [
        "bias_phase", "bias_strength", "regime_state",
        "m5_structure_clarity", "candle_overlap_ratio",
        "volatility_filter"
    ]
}
"""

from __future__ import annotations

from core.state.snapshot import StateSnapshot
from core.voters.types import VoteResult


class SpreadVoter:
    """
    Execution cost classifier.

    Answers ONLY: "How favorable are current spread conditions for trade execution?"
    Measures spread as a percentage of ATR (execution cost relative to expected move).

    Does NOT measure: direction, structure, volatility regime, or session timing.
    """

    def evaluate(self, snapshot: StateSnapshot) -> VoteResult:
        """
        Produce an execution cost vote from spread and ATR.

        Returns:
            VoteResult with score (-2.0 to +2.0), confidence (0.0-1.0), reason.
        """
        spread = snapshot.spread
        atr = snapshot.m5_atr_14

        # Guard: if ATR is zero or negative, cannot normalize
        if atr <= 0:
            return VoteResult(
                score=0.0,
                confidence=0.3,
                reason="spread=unknown (atr=0)",
            )

        spread_pct = spread / atr

        # Classification and scoring
        if spread_pct < 0.10:
            score = 1.0
            classification = "optimal"
        elif spread_pct < 0.15:
            score = 0.6
            classification = "good"
        elif spread_pct < 0.20:
            score = 0.2
            classification = "acceptable"
        elif spread_pct < 0.30:
            score = -0.5
            classification = "poor"
        elif spread_pct < 0.50:
            score = -1.2
            classification = "avoid"
        else:
            score = -2.0
            classification = "extreme"

        # Confidence is high — spread is a hard fact
        confidence = 0.9

        return VoteResult(
            score=score,
            confidence=confidence,
            reason=f"spread={spread_pct:.3f}atr ({classification})",
        )
