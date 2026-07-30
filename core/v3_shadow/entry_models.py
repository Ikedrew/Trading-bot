"""
V3 Entry Assessment Model — Evaluates confirmation behaviour before movement.

Determines WHAT confirmation behaviour is present after a valid context
and horizon have been identified. Does NOT create opportunities or
determine risk.

It answers: "Given the right context, what behaviour consistently appears
before successful movement?"

ENTRY PHILOSOPHY:
    The old model: Pattern detected → assume trade
    The V3 model: Context exists → Observe which confirmation produces best outcomes

RESEARCH QUESTIONS ENABLED:
    EM1: Does confirmation improve expectancy vs context-only?
    EM2: Which trigger type works best per context?
    EM3: Does timeframe of confirmation matter?
    EM4: Does entry refinement improve risk geometry?

Multiple confirmation hypotheses are evaluated as CANDIDATES.
Research determines which produces the best outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_ENTRY_SCHEMA_VERSION = "v3_entry_assessment_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY BEHAVIOUR TYPES (broad classification)
# ═══════════════════════════════════════════════════════════════════════════════

BEHAVIOUR_STRUCTURE_ALIGNMENT = "STRUCTURE_ALIGNMENT"
BEHAVIOUR_DISPLACEMENT = "DISPLACEMENT_BEHAVIOUR"
BEHAVIOUR_REJECTION = "REJECTION_BEHAVIOUR"
BEHAVIOUR_RETEST = "RETEST_BEHAVIOUR"
BEHAVIOUR_MOMENTUM_TRANSITION = "MOMENTUM_TRANSITION"
BEHAVIOUR_UNKNOWN = "UNKNOWN"

# Mapping: trigger observation → behaviour type
_TRIGGER_TO_BEHAVIOUR: dict[str, str] = {
    "BOS_CONFIRMATION": BEHAVIOUR_STRUCTURE_ALIGNMENT,
    "CHOCH_CONFIRMATION": BEHAVIOUR_STRUCTURE_ALIGNMENT,
    "DISPLACEMENT_CANDLE": BEHAVIOUR_DISPLACEMENT,
    "REJECTION_CANDLE": BEHAVIOUR_REJECTION,
    "RETEST_ENTRY": BEHAVIOUR_RETEST,
    "MOMENTUM_SHIFT": BEHAVIOUR_MOMENTUM_TRANSITION,
    "NONE": BEHAVIOUR_UNKNOWN,
}


def get_behaviour_type(trigger_type: str) -> str:
    """Map a trigger observation to its broader behaviour classification."""
    return _TRIGGER_TO_BEHAVIOUR.get(trigger_type, BEHAVIOUR_UNKNOWN)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY STATES
# ═══════════════════════════════════════════════════════════════════════════════

VALID_ENTRY_CONFIRMATION = "VALID_ENTRY_CONFIRMATION"
WEAK_ENTRY_CONFIRMATION = "WEAK_ENTRY_CONFIRMATION"
NO_ENTRY_CONFIRMATION = "NO_ENTRY_CONFIRMATION"
INSUFFICIENT_ENTRY_DATA = "INSUFFICIENT_ENTRY_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER TYPES (specific observations — competing hypotheses)
# ═══════════════════════════════════════════════════════════════════════════════

TRIGGER_BOS = "BOS_CONFIRMATION"
TRIGGER_CHOCH = "CHOCH_CONFIRMATION"
TRIGGER_DISPLACEMENT = "DISPLACEMENT_CANDLE"
TRIGGER_REJECTION = "REJECTION_CANDLE"
TRIGGER_RETEST = "RETEST_ENTRY"
TRIGGER_MOMENTUM = "MOMENTUM_SHIFT"
TRIGGER_NONE = "NONE"


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY CANDIDATE
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EntryCandidate:
    """One evaluated confirmation trigger hypothesis."""
    trigger_type: str = TRIGGER_NONE
    detected: bool = False
    strength: float = 0.0         # 0-1 quality of the trigger
    timeframe: str = ""           # M1 / M5 / M15
    direction: str = ""           # BULLISH / BEARISH / ""
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EntryAssessment:
    """
    Immutable assessment of entry confirmation behaviour.

    Evaluates multiple trigger hypotheses. Records which confirmations
    are present. Research determines which produces best outcomes.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _ENTRY_SCHEMA_VERSION

    # Direction (from context alignment, NOT from entry model)
    direction: str = ""                  # BULLISH / BEARISH / NEUTRAL

    # Entry state
    entry_state: str = INSUFFICIENT_ENTRY_DATA

    # Behaviour classification (broad type)
    entry_behaviour_type: str = BEHAVIOUR_UNKNOWN

    # Primary trigger observation (specific event)
    primary_trigger: str = TRIGGER_NONE
    trigger_timeframe: str = ""
    trigger_strength: float = 0.0

    # Entry location
    entry_price: float = 0.0
    entry_at_zone: bool = False          # Is entry precisely at institutional zone?

    # Alignment with upstream
    location_alignment: float = 0.0      # How well entry aligns with location context
    horizon_alignment: float = 0.0       # How well entry aligns with horizon expectation

    # Quality
    entry_quality_score: float = 0.0     # 0-1 composite

    # All candidates (for research comparison)
    candidates: list[EntryCandidate] = field(default_factory=list)

    # Confidence
    confidence: float = 0.0

    # Evidence
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)

    # Observations
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "direction": self.direction,
            "entry_state": self.entry_state,
            "entry_behaviour_type": self.entry_behaviour_type,
            "primary_trigger": self.primary_trigger,
            "trigger_timeframe": self.trigger_timeframe,
            "trigger_strength": round(self.trigger_strength, 4),
            "entry_price": round(self.entry_price, 8),
            "entry_at_zone": self.entry_at_zone,
            "location_alignment": round(self.location_alignment, 4),
            "horizon_alignment": round(self.horizon_alignment, 4),
            "entry_quality_score": round(self.entry_quality_score, 4),
            "candidates": [
                {
                    "trigger_type": c.trigger_type,
                    "detected": c.detected,
                    "strength": round(c.strength, 4),
                    "timeframe": c.timeframe,
                    "direction": c.direction,
                    "supporting": list(c.supporting),
                    "conflicting": list(c.conflicting),
                }
                for c in self.candidates
            ],
            "confidence": round(self.confidence, 4),
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
            "observations": list(self.observations),
        }
