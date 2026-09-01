"""
V3 Horizon Assessment Model — Expected movement profile classification.

Determines WHAT TYPE of price expansion to expect if an opportunity works.
Does NOT predict direction, create entries, or execute trades.

It answers: "If this opportunity works, what type of price expansion
should we reasonably expect?"

HORIZON HYPOTHESIS EVALUATION:
    The engine does NOT know the answer. It creates measurable hypotheses
    that research can validate by comparing predicted vs actual outcomes.

    Each horizon is a COMPETING HYPOTHESIS about the movement distribution:
        SCALP:     5-20 pip reaction
        INTRADAY:  20-50 pip structural move
        EXTENDED:  50+ pip continuation

    Initial plausibility scores are RESEARCH PRIORS — starting assumptions
    before outcome data exists. They are NOT rules or permanent weights.
    The research engine determines which hypothesis best explains reality.

RESEARCH FEEDBACK LOOP:
    Predicted horizon → observed movement → classification accuracy
    Example: predicted INTRADAY (20-50 pips), actual 42 pips → correct
    Example: predicted INTRADAY (20-50 pips), actual 12 pips → was SCALP
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from core.production_data_contract import current_schema

_HORIZON_SCHEMA_VERSION = current_schema("v3_horizon_assessment")


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON TYPES
# ═══════════════════════════════════════════════════════════════════════════════

SCALP = "SCALP"
INTRADAY = "INTRADAY"
EXTENDED = "EXTENDED"
NO_HORIZON = "NO_HORIZON"


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON PROFILES (research hypotheses — initial assumptions, not facts)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HorizonProfile:
    """
    Describes the expected behaviour of one horizon type.

    These are HYPOTHESES about what movement follows a given context.
    They are NOT proven rules. Research validates or updates them.
    """
    name: str
    # Expected movement
    expected_move_min_pips: float = 0.0
    expected_move_max_pips: float = 0.0
    # Duration expectations
    expected_duration_min_minutes: int = 0
    expected_duration_max_minutes: int = 0
    # Stop framework
    stop_source: str = ""                # M5_STRUCTURE / M15_STRUCTURE / H1_STRUCTURE
    typical_stop_pips_min: float = 0.0
    typical_stop_pips_max: float = 0.0
    # Target expectations
    typical_rr_min: float = 0.0
    typical_rr_max: float = 0.0
    # Management
    management_style: str = ""           # FAST_EXIT / TRAIL_BREAKEVEN / STRUCTURAL_TRAIL
    # Spread constraint
    max_spread_risk_ratio: float = 1.0   # Maximum acceptable spread/risk
    # Description
    description: str = ""


SCALP_PROFILE = HorizonProfile(
    name=SCALP,
    expected_move_min_pips=5.0,
    expected_move_max_pips=20.0,
    expected_duration_min_minutes=2,
    expected_duration_max_minutes=45,
    stop_source="M5_STRUCTURE",
    typical_stop_pips_min=2.0,
    typical_stop_pips_max=5.0,
    typical_rr_min=1.5,
    typical_rr_max=3.0,
    management_style="FAST_EXIT",
    max_spread_risk_ratio=0.50,
    description="Fast reaction from high-quality location. Nearby liquidity targets.",
)

INTRADAY_PROFILE = HorizonProfile(
    name=INTRADAY,
    expected_move_min_pips=20.0,
    expected_move_max_pips=50.0,
    expected_duration_min_minutes=30,
    expected_duration_max_minutes=480,
    stop_source="M15_STRUCTURE",
    typical_stop_pips_min=5.0,
    typical_stop_pips_max=15.0,
    typical_rr_min=2.0,
    typical_rr_max=4.0,
    management_style="TRAIL_BREAKEVEN",
    max_spread_risk_ratio=0.25,
    description="Main movement from institutional location. Structure-driven targets.",
)

EXTENDED_PROFILE = HorizonProfile(
    name=EXTENDED,
    expected_move_min_pips=50.0,
    expected_move_max_pips=150.0,
    expected_duration_min_minutes=240,
    expected_duration_max_minutes=4320,
    stop_source="H1_STRUCTURE",
    typical_stop_pips_min=15.0,
    typical_stop_pips_max=50.0,
    typical_rr_min=3.0,
    typical_rr_max=6.0,
    management_style="STRUCTURAL_TRAIL",
    max_spread_risk_ratio=0.10,
    description="Larger structural continuation. H1/H4 alignment required.",
)

PROFILES: dict[str, HorizonProfile] = {
    SCALP: SCALP_PROFILE,
    INTRADAY: INTRADAY_PROFILE,
    EXTENDED: EXTENDED_PROFILE,
}


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON CANDIDATE (per-horizon evaluation)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HorizonCandidate:
    """Evaluation of one horizon for this opportunity."""
    horizon: str                         # SCALP / INTRADAY / EXTENDED
    plausibility: float = 0.0            # 0-1 how plausible is this horizon
    expected_move_min_pips: float = 0.0
    expected_move_max_pips: float = 0.0
    stop_framework: str = ""
    target_framework: str = ""
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HorizonAssessment:
    """
    Immutable assessment of expected movement profile.

    Evaluates ALL horizons as candidates. Selects the most plausible.
    Preserves all evaluations for research comparison.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _HORIZON_SCHEMA_VERSION

    # Opportunity state (from upstream)
    opportunity_state: str = ""

    # Primary selection (most plausible horizon)
    selected_horizon: str = NO_HORIZON
    expected_move_min_pips: float = 0.0
    expected_move_max_pips: float = 0.0

    # Structure source
    structure_timeframe: str = ""        # M5 / M15 / H1 (which TF provides stop)
    stop_framework: str = ""
    target_framework: str = ""

    # Duration class
    duration_class: str = ""             # SHORT / MEDIUM / LONG

    # Management
    management_profile: str = ""

    # Volatility
    volatility_fit: str = ""             # SUITABLE / MARGINAL / UNSUITABLE
    spread_risk_estimate: float = 0.0

    # All candidates (for research comparison)
    candidates: list[HorizonCandidate] = field(default_factory=list)

    # Confidence in primary selection
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
            "opportunity_state": self.opportunity_state,
            "selected_horizon": self.selected_horizon,
            "expected_move_min_pips": round(self.expected_move_min_pips, 1),
            "expected_move_max_pips": round(self.expected_move_max_pips, 1),
            "structure_timeframe": self.structure_timeframe,
            "stop_framework": self.stop_framework,
            "target_framework": self.target_framework,
            "duration_class": self.duration_class,
            "management_profile": self.management_profile,
            "volatility_fit": self.volatility_fit,
            "spread_risk_estimate": round(self.spread_risk_estimate, 4),
            "candidates": [
                {
                    "horizon": c.horizon,
                    "plausibility": round(c.plausibility, 4),
                    "expected_move_min_pips": round(c.expected_move_min_pips, 1),
                    "expected_move_max_pips": round(c.expected_move_max_pips, 1),
                    "stop_framework": c.stop_framework,
                    "target_framework": c.target_framework,
                    "supporting": list(c.supporting_factors),
                    "conflicting": list(c.conflicting_factors),
                }
                for c in self.candidates
            ],
            "confidence": round(self.confidence, 4),
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
            "observations": list(self.observations),
        }
