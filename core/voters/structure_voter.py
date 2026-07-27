"""
Shadow Structure Voter v2 — Pure candle-derived market structure assessment.

Evaluates structural clarity and tradability using ONLY FeatureBundle-derived
fields from StateSnapshot. Contains ZERO FSM-derived inputs.

Inputs used (all from FeatureBundle via snapshot):
  - m5_swing_high_count (candle-derived)
  - m5_swing_low_count (candle-derived)
  - m5_structure_clarity (candle-derived, ATR-normalized)
  - candle_overlap_ratio (candle-derived)
  - feature_sweep_high / feature_sweep_low (liquidity events)
  - regime_state (market-context derived, acceptable as environment context)

EXPLICITLY NOT USED:
  - bias_flip_bars_count (FSM)
  - bias_confirmation_count (FSM)
  - bias_contradiction_count (FSM)
  - bias_strength (FSM)
  - volatility_filter (scoring engine output)
"""

from __future__ import annotations

from core.state.snapshot import StateSnapshot
from core.voters.types import VoteResult


class ShadowStructureVoter:
    """
    Evaluates market structure quality from pure candle-derived features.

    Answers ONLY: "Is price structure clean, tradable, and well-formed?"
    Does NOT answer: "Is bias strong?" or "Is FSM stable?"
    """

    def evaluate(self, snapshot: StateSnapshot) -> VoteResult:
        """
        Produce a structure quality vote from feature-derived snapshot fields.

        Returns:
            VoteResult with score (-2.0 to +2.0), confidence (0.0-1.0), reason.
        """
        clarity = snapshot.m5_structure_clarity
        swing_highs = snapshot.m5_swing_high_count
        swing_lows = snapshot.m5_swing_low_count
        overlap = snapshot.candle_overlap_ratio
        has_sweep_high = snapshot.feature_sweep_high is not None
        has_sweep_low = snapshot.feature_sweep_low is not None
        regime = snapshot.regime_state

        score = 0.0
        reasons: list[str] = []

        # Structure clarity (primary signal)
        if clarity >= 0.7:
            score += 1.2
            reasons.append(f"clarity={clarity:.2f}(high)")
        elif clarity >= 0.4:
            score += 0.4
            reasons.append(f"clarity={clarity:.2f}(moderate)")
        elif clarity >= 0.2:
            score -= 0.2
        else:
            score -= 0.8
            reasons.append(f"clarity={clarity:.2f}(poor)")

        # Swing count (well-defined pivots = tradable structure)
        total_swings = swing_highs + swing_lows
        if total_swings >= 4:
            score += 0.5
            reasons.append(f"swings={total_swings}")
        elif total_swings >= 2:
            score += 0.2
        elif total_swings == 0:
            score -= 0.4
            reasons.append("no_swings")

        # Overlap ratio (chop detection — high overlap = poor structure)
        if overlap >= 0.7:
            score -= 0.8
            reasons.append(f"overlap={overlap:.2f}(choppy)")
        elif overlap >= 0.4:
            score -= 0.3
        elif overlap <= 0.2:
            score += 0.3
            reasons.append(f"overlap={overlap:.2f}(clean)")

        # Sweep events (liquidity taken = structure forming)
        if has_sweep_high or has_sweep_low:
            score += 0.2
            reasons.append("sweep_present")

        # Regime context (environment, not primary signal)
        if regime in ("TREND_UP", "TREND_DOWN", "TRENDING"):
            score += 0.3
        elif regime == "VOLATILE":
            score -= 0.3

        # Clamp
        score = max(-2.0, min(2.0, score))

        # Confidence based on data quality
        if total_swings >= 3 and clarity >= 0.4:
            confidence = 0.85
        elif total_swings >= 1:
            confidence = 0.6
        else:
            confidence = 0.4

        confidence = max(0.0, min(1.0, confidence))
        reason = " | ".join(reasons) if reasons else "neutral"

        return VoteResult(score=round(score, 3), confidence=round(confidence, 3), reason=reason)
