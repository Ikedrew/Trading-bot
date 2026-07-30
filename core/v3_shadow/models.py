"""
V3 MarketUnderstanding — Immutable objective market description.

This model describes WHAT the market IS, not what to DO about it.

It does NOT contain:
    - BUY / SELL signals
    - EXECUTE recommendations
    - Scores or confidence gates
    - Risk parameters
    - Trade recommendations

It answers: "What is the complete objective state of this market right now?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "market_understanding_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# PER-TIMEFRAME SUMMARIES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class H4Understanding:
    """
    H4 macro environment description.

    Answers: "What is the overall market environment?"
    """
    # Trend state
    trend: str = ""                      # BULLISH / BEARISH / NEUTRAL
    trend_strength: float = 0.0          # 0-1
    market_phase: str = ""               # IMPULSE / PULLBACK / CONSOLIDATION / DISTRIBUTION

    # Structure
    swing_high: float = 0.0
    swing_low: float = 0.0
    structure_type: str = ""             # HH_HL / LH_LL / MIXED
    last_bos_direction: str = ""         # BULLISH / BEARISH / ""

    # Liquidity
    major_liquidity_above: float = 0.0   # Nearest major pool above
    major_liquidity_below: float = 0.0   # Nearest major pool below

    # Volatility
    atr: float = 0.0
    volatility_state: str = ""           # EXPANSION / CONTRACTION / NEUTRAL
    atr_percentile: float = 0.0          # 0-1 (current ATR vs historical)


@dataclass(frozen=True)
class H1Understanding:
    """
    H1 structural authority description.

    Answers: "What structure currently controls price?"
    """
    # Structure breaks
    bos_confirmed: bool = False
    bos_direction: str = ""              # BULLISH / BEARISH
    choch_detected: bool = False
    choch_direction: str = ""            # BULLISH / BEARISH

    # Trend
    dominant_trend: str = ""             # BULLISH / BEARISH / NEUTRAL
    swing_high: float = 0.0
    swing_low: float = 0.0
    structure_type: str = ""             # HH_HL / LH_LL / MIXED

    # Institutional zones
    active_demand_ob_high: float = 0.0
    active_demand_ob_low: float = 0.0
    active_supply_ob_high: float = 0.0
    active_supply_ob_low: float = 0.0
    nearest_fvg_above: float = 0.0
    nearest_fvg_below: float = 0.0

    # Liquidity
    equal_highs_level: float = 0.0
    equal_lows_level: float = 0.0
    session_high: float = 0.0
    session_low: float = 0.0

    # Quality
    structural_clarity: float = 0.0      # 0-1 (how clean is the structure)


@dataclass(frozen=True)
class M15Understanding:
    """
    M15 refinement inside H1 structure.

    Answers: "How is price behaving inside the higher timeframe structure?"
    """
    # Internal structure
    internal_bos: bool = False
    internal_bos_direction: str = ""     # BULLISH / BEARISH
    internal_choch: bool = False

    # Pullback state
    pullback_active: bool = False
    pullback_depth_atr: float = 0.0      # Depth of pullback in ATR multiples
    retracement_pct: float = 0.0         # % of last impulse retraced (0-1)

    # Refined zones
    refined_demand_ob_high: float = 0.0
    refined_demand_ob_low: float = 0.0
    refined_supply_ob_high: float = 0.0
    refined_supply_ob_low: float = 0.0
    nearest_fvg: float = 0.0

    # Position
    range_position: float = 0.0          # 0=discount, 0.5=equilibrium, 1=premium
    swing_high: float = 0.0
    swing_low: float = 0.0

    # Expected movement
    expected_direction: str = ""         # BULLISH / BEARISH / NEUTRAL (continuation bias)
    displacement_present: bool = False
    displacement_magnitude_atr: float = 0.0


@dataclass(frozen=True)
class M5Understanding:
    """
    M5 execution environment description.

    Answers: "Is the market approaching a potential execution environment?"
    Does NOT generate entries. Only describes.
    """
    # Local structure
    local_bos: bool = False
    local_bos_direction: str = ""

    # Momentum
    momentum_direction: str = ""         # BULLISH / BEARISH / NEUTRAL
    momentum_strength: float = 0.0       # 0-1

    # Rejection / confirmation
    rejection_present: bool = False
    rejection_direction: str = ""        # Direction the rejection suggests
    rejection_strength_atr: float = 0.0  # Wick size in ATR

    # Execution readiness (descriptive, NOT prescriptive)
    at_institutional_zone: bool = False  # Price currently at OB/FVG/liquidity
    zone_type: str = ""                  # DEMAND_OB / SUPPLY_OB / FVG / LIQUIDITY / ""
    confirmation_candle: bool = False    # Structure candle pattern present

    # Volatility micro-context
    atr: float = 0.0
    spread: float = 0.0
    spread_atr_ratio: float = 0.0


@dataclass(frozen=True)
class M1Understanding:
    """
    M1 research-only precision layer.

    Answers: "Can lower timeframe information improve execution quality?"
    Experimental. Not integrated into decisions.
    """
    # Micro structure
    micro_bos: bool = False
    micro_bos_direction: str = ""

    # Precision
    micro_rejection: bool = False
    micro_displacement: bool = False
    candle_velocity: float = 0.0         # Rate of price change (normalized)

    # Spread timing
    spread_at_observation: float = 0.0
    spread_vs_session_avg: float = 0.0   # Ratio: current / session average

    # Micro liquidity
    recent_high: float = 0.0             # Last 5 min high
    recent_low: float = 0.0              # Last 5 min low
    micro_range_pips: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MarketUnderstanding:
    """
    Complete objective market description at one point in time.

    Immutable snapshot. Created once per cycle. Never modified.
    Consumed read-only by downstream V3 shadow pipeline.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _SCHEMA_VERSION

    # Confidence in the overall reading (0-1)
    # Higher = more data available, clearer structure
    confidence: float = 0.0

    # Timeframe layers
    h4: H4Understanding = field(default_factory=H4Understanding)
    h1: H1Understanding = field(default_factory=H1Understanding)
    m15: M15Understanding = field(default_factory=M15Understanding)
    m5: M5Understanding = field(default_factory=M5Understanding)
    m1: M1Understanding = field(default_factory=M1Understanding)

    # Observations (structured notes for debugging/research)
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "confidence": round(self.confidence, 4),
            "h4": _frozen_to_dict(self.h4),
            "h1": _frozen_to_dict(self.h1),
            "m15": _frozen_to_dict(self.m15),
            "m5": _frozen_to_dict(self.m5),
            "m1": _frozen_to_dict(self.m1),
            "observations": list(self.observations),
        }


def _frozen_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a frozen dataclass to dict."""
    from dataclasses import fields, asdict
    return {f.name: getattr(obj, f.name) for f in fields(obj)}
