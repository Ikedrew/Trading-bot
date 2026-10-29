"""
Voter: SessionVoter
Domain: time_structure
Layer: evaluation
Input: StateSnapshot (current_time only)
Mutability: NONE
Dependencies: NONE
Signal Type: temporal-only classification

Classifies market session from UTC timestamp.
Measures liquidity availability — NOT direction, volatility, or structure.

VOTER_SIGNAL_DOMAIN = {
    "name": "SessionVoter",
    "domain": "time_structure",
    "primary_signals": ["current_time"],
    "explicitly_excluded_signals": [
        "bias_phase", "bias_strength", "regime_state",
        "m5_atr_14", "spread", "m5_structure_clarity",
        "candle_overlap_ratio", "volatility_filter"
    ]
}
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.state.snapshot import StateSnapshot
from core.voters.types import VoteResult


class SessionVoter:
    """
    Temporal liquidity availability classifier.

    Answers ONLY: "How favorable is the current time for trade execution?"
    Based on known FX session windows (UTC):
      - Asia (00:00–06:00): low liquidity for majors
      - London open (06:00–08:00): expansion phase
      - London body (08:00–12:00): active session
      - London/NY overlap (12:00–16:00): highest liquidity
      - NY body (16:00–21:00): moderate activity
      - Post-session (21:00–00:00): winding down

    Does NOT measure: direction, volatility, structure, or bias.
    """

    def evaluate(self, snapshot: StateSnapshot) -> VoteResult:
        """
        Produce a session quality vote from current_time.

        Returns:
            VoteResult with score (-2.0 to +2.0), confidence (0.0-1.0), reason.
        """
        # Convert unix timestamp to UTC hour
        dt = datetime.fromtimestamp(snapshot.current_time, tz=timezone.utc)
        hour = dt.hour
        weekday = dt.weekday()  # 0=Monday, 4=Friday

        # Friday wind-down (after 20:00 UTC)
        if weekday == 4 and hour >= 20:
            return VoteResult(
                score=-1.5,
                confidence=0.9,
                reason=f"current_time=friday_{hour:02d}utc_winddown",
            )

        # Session classification
        if 0 <= hour < 6:
            # Asia session — low liquidity for FX majors
            score = -0.3
            session = "asia"
        elif 6 <= hour < 8:
            # London open — expansion, kill zone
            score = 0.8
            session = "london_open"
        elif 8 <= hour < 12:
            # London body — active
            score = 0.6
            session = "london_body"
        elif 12 <= hour < 16:
            # London/NY overlap — highest liquidity
            score = 1.0
            session = "london_ny_overlap"
        elif 16 <= hour < 21:
            # NY body — moderate
            score = 0.4
            session = "ny_body"
        else:
            # Post-session (21:00–00:00) — low activity
            score = -0.2
            session = "post_session"

        return VoteResult(
            score=score,
            confidence=0.9,
            reason=f"current_time={hour:02d}utc_{session}",
        )
