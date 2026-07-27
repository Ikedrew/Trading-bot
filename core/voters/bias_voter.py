"""
Shadow Bias Voter — First voter implementation (observational only).

Evaluates directional bias quality from StateSnapshot.
Produces a score based on bias_phase, bias_strength, and bias_age_seconds.

CRITICAL: This voter MUST NOT:
  - Mutate EngineState
  - Influence trade decisions
  - Interact with StateDelta
  - Call MT5 or any external system

It is purely observational during shadow mode.
"""

from __future__ import annotations

from core.state.snapshot import StateSnapshot
from core.voters.types import VoteResult


class ShadowBiasVoter:
    """
    Evaluates bias quality and produces a probabilistic score.

    Logic:
      - CONFIRMED bias with high strength and low age → strong positive score
      - BUILDING bias → weak positive (potential forming)
      - EXPIRED bias → negative score (no directional conviction)
      - Strength and age decay the score proportionally
    """

    def evaluate(self, snapshot: StateSnapshot) -> VoteResult:
        """
        Produce a bias vote from the current state snapshot.

        Returns:
            VoteResult with score (-2.0 to +2.0), confidence (0.0-1.0), reason.
        """
        phase = snapshot.bias_phase
        strength = snapshot.bias_strength
        age = snapshot.bias_age_seconds

        # Phase-based base score
        if phase == "CONFIRMED":
            base = 1.5
            reason_prefix = "confirmed"
        elif phase == "BUILDING":
            base = 0.3
            reason_prefix = "building"
        else:  # EXPIRED or LOCKED
            return VoteResult(
                score=-1.0,
                confidence=0.8,
                reason="bias_expired (no directional conviction)",
            )

        # Strength scaling (0-100 → 0.0-1.0 multiplier)
        strength_factor = min(1.0, strength / 80.0)

        # Age decay (fresh bias = full score, old bias = reduced)
        # Decay starts after 600s, reaches 50% at 3600s
        if age <= 600.0:
            age_factor = 1.0
        else:
            age_factor = max(0.3, 1.0 - ((age - 600.0) / 6000.0))

        # Final score
        score = base * strength_factor * age_factor

        # Confidence based on how clear the signal is
        confidence = min(1.0, strength_factor * 0.7 + (0.3 if phase == "CONFIRMED" else 0.0))

        # Clamp to valid range
        score = max(-2.0, min(2.0, score))
        confidence = max(0.0, min(1.0, confidence))

        reason = f"{reason_prefix} strength={strength:.0f} age={age:.0f}s factor={strength_factor:.2f}*{age_factor:.2f}"

        return VoteResult(score=round(score, 3), confidence=round(confidence, 3), reason=reason)
