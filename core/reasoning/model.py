"""
DecisionReasoning — Frozen explanation of why an opportunity exists.

This object contains ONLY explanation. No trading logic.
It is produced AFTER analysis, consumed ONLY by observability layers
(logging, Discord, audit trail, dashboards).

NEVER used to:
    - Make trading decisions
    - Approve or reject trades
    - Modify risk parameters
    - Change execution behaviour

The system already decides. This explains why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionReasoning:
    """
    Human-readable explanation of a trade opportunity assessment.

    Frozen after construction. Observational only.
    """

    # ─── PRIMARY THESIS ───────────────────────────────────────────────
    # One-sentence explanation of WHY this opportunity exists.
    # Example: "Trend continuation likely — bullish momentum aligned with H4 regime"
    primary_thesis: str

    # ─── EVIDENCE ─────────────────────────────────────────────────────
    # Factors that SUPPORT the primary thesis direction.
    # Each entry is a plain-English explanation.
    # Example: ["HTF alignment supports continuation", "Strong bullish displacement"]
    supporting_evidence: tuple[str, ...]

    # Factors that CONTRADICT or weaken the primary thesis.
    # Example: ["Approaching daily resistance", "Regime transitional — low confidence"]
    contradicting_evidence: tuple[str, ...]

    # ─── ALTERNATIVE INTERPRETATION ───────────────────────────────────
    # What the market COULD be doing instead of the primary thesis.
    # Example: "Liquidity sweep reversal — recent high may be a trap"
    alternative_thesis: str | None = None

    # ─── CONFIDENCE EXPLANATION ───────────────────────────────────────
    # Why the system's confidence is what it is (not a number — an explanation).
    # Example: "Moderate confidence — strategy-specific weighting boosted score but regime uncertain"
    confidence_explanation: str | None = None

    # ─── METADATA ─────────────────────────────────────────────────────
    # Arbitrary context for debugging / audit (component scores, thresholds hit, etc.)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence / logging."""
        return {
            "primary_thesis": self.primary_thesis,
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "alternative_thesis": self.alternative_thesis,
            "confidence_explanation": self.confidence_explanation,
            "metadata": self.metadata,
        }
