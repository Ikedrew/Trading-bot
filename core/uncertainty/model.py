"""
UncertaintyAssessment — Frozen measurement of opportunity ambiguity.

Answers: "How uncertain is this opportunity?"
Does NOT answer: "Should we trade?"

uncertainty_score:
    0.0 = very clear opportunity (strong alignment, high confidence, stable structure)
    1.0 = highly ambiguous opportunity (conflicting signals, weak structure, regime unclear)

confidence_modifier:
    Negative value representing how much uncertainty WOULD reduce confidence
    if consumed by downstream policy. Range: -1.0 to 0.0.
    Example: -0.15 means "uncertainty suggests 15% confidence reduction"

This object is OBSERVATIONAL. It does not gate, block, or modify trading.
Downstream consumers (policy, EV) MAY choose to consume these values,
but the Uncertainty Engine itself never decides.

INVARIANT: Frozen after construction. Pure measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UncertaintyAssessment:
    """
    Quantified measurement of how ambiguous the current opportunity is.

    Produced after OpportunityAssessment, consumed by observability
    and optionally by policy/EV layers.
    """

    # ─── CORE SCORES ──────────────────────────────────────────────────
    uncertainty_score: float         # 0.0 (clear) to 1.0 (highly ambiguous)
    confidence_modifier: float       # -1.0 to 0.0 (suggested confidence reduction)

    # ─── CONTRIBUTING FACTORS ─────────────────────────────────────────
    # Human-readable list of what contributes to uncertainty.
    # Each entry explains one source of ambiguity.
    uncertainty_factors: tuple[str, ...]

    # ─── COMPONENT BREAKDOWN ──────────────────────────────────────────
    # Per-dimension uncertainty measurements (0.0–1.0 each)
    regime_uncertainty: float = 0.0       # How uncertain is regime classification?
    structure_uncertainty: float = 0.0    # How unclear is market structure?
    htf_uncertainty: float = 0.0          # How much do timeframes disagree?
    momentum_uncertainty: float = 0.0     # How weak/contradictory is momentum?
    confirmation_uncertainty: float = 0.0 # How weak is candle quality evidence?

    # ─── METADATA ─────────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence / logging."""
        return {
            "uncertainty_score": round(self.uncertainty_score, 4),
            "confidence_modifier": round(self.confidence_modifier, 4),
            "uncertainty_factors": list(self.uncertainty_factors),
            "regime_uncertainty": round(self.regime_uncertainty, 4),
            "structure_uncertainty": round(self.structure_uncertainty, 4),
            "htf_uncertainty": round(self.htf_uncertainty, 4),
            "momentum_uncertainty": round(self.momentum_uncertainty, 4),
            "confirmation_uncertainty": round(self.confirmation_uncertainty, 4),
            "metadata": self.metadata,
        }
