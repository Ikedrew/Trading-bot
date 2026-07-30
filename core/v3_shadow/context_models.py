"""
V3 Market Context Models — Structured interpretation of market state.

Transforms objective MarketUnderstanding observations into meaningful context.

Three independent context domains:
    1. HTFStructureContext — Which higher timeframe structure has authority?
    2. LocationContext — Where is price relative to institutional areas?
    3. BehaviourContext — How is the market currently behaving?

Combined into V3MarketContext — the complete interpretive layer.

This module does NOT contain:
    - Trade signals or recommendations
    - Opportunity scores
    - Risk calculations
    - Execution decisions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_CONTEXT_SCHEMA_VERSION = "v3_market_context_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HTF STRUCTURE CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HTFStructureContext:
    """
    Interprets higher timeframe structural authority.

    Answers: "Which higher timeframe structure currently has authority?"
    """
    # Macro bias (from H4/H1 agreement)
    macro_bias: str = ""                 # BULLISH / BEARISH / NEUTRAL / CONFLICTED
    macro_bias_strength: float = 0.0     # 0-1

    # Dominant structure
    dominant_structure: str = ""         # HH_HL / LH_LL / MIXED
    authority_timeframe: str = ""        # H4 / H1 / M15 (which TF controls)

    # Structural breaks
    bos_active: bool = False
    bos_direction: str = ""             # BULLISH / BEARISH
    choch_active: bool = False
    choch_direction: str = ""

    # Phase alignment (do H4 phase and H1 structure agree?)
    phase_alignment: str = ""           # ALIGNED / CONFLICTED / NEUTRAL
    structure_alignment: float = 0.0    # 0-1 (how well do timeframes agree)

    # Confidence in this reading
    confidence: float = 0.0

    # Observations
    observations: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOCATION CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LocationContext:
    """
    Interprets where price is positioned relative to institutional areas.

    Answers: "Where is price located relative to meaningful institutional zones?"
    """
    # Zone positioning
    location_type: str = ""              # DEMAND_OB / SUPPLY_OB / FVG / LIQUIDITY / OPEN_SPACE
    inside_institutional_zone: bool = False

    # Premium / Discount
    premium_discount: str = ""           # PREMIUM / DISCOUNT / EQUILIBRIUM
    range_position: float = 0.0          # 0=extreme discount, 0.5=equilibrium, 1=extreme premium

    # Institutional alignment
    institutional_alignment: str = ""    # BULLISH / BEARISH / NEUTRAL
    # (BULLISH = at demand zone in discount; BEARISH = at supply zone in premium)

    # Zone quality
    zone_quality: float = 0.0            # 0-1 (strength, recency, mitigation status)
    zone_mitigated: bool = False         # Zone already visited before

    # Liquidity context
    liquidity_above: bool = False        # Equal highs / session high above
    liquidity_below: bool = False        # Equal lows / session low below
    nearest_liquidity_direction: str = ""  # ABOVE / BELOW (nearest target)
    nearest_liquidity_distance_pips: float = 0.0

    # Active zones (count of nearby institutional areas)
    demand_zones_nearby: int = 0
    supply_zones_nearby: int = 0
    fvg_zones_nearby: int = 0

    # Confidence
    confidence: float = 0.0

    # Observations
    observations: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BEHAVIOUR CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BehaviourContext:
    """
    Describes how the market is currently behaving.

    Answers: "How is the market currently behaving?"
    Note: V3 research has NOT validated this layer as predictive.
    """
    # Regime
    regime: str = ""                     # TRENDING / RANGING / TRANSITIONAL / VOLATILE
    regime_confidence: float = 0.0

    # Volatility
    volatility_state: str = ""           # EXPANSION / CONTRACTION / NEUTRAL
    volatility_level: float = 0.0        # 0-1 (normalized)

    # Momentum
    momentum_direction: str = ""         # BULLISH / BEARISH / NEUTRAL
    momentum_strength: float = 0.0       # 0-1

    # Expansion / Compression
    expansion_state: str = ""            # EXPANDING / COMPRESSING / NEUTRAL
    compression_bars: int = 0            # How long price has been compressing

    # Displacement
    displacement_active: bool = False
    displacement_direction: str = ""     # BULLISH / BEARISH
    displacement_magnitude_atr: float = 0.0

    # Confidence
    confidence: float = 0.0

    # Observations
    observations: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE: V3 MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class V3MarketContext:
    """
    Complete V3 market context — structured interpretation of market state.

    Immutable. Created once per cycle from MarketUnderstanding.
    Consumed by future Opportunity Engine, Horizon Engine, Research datasets.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _CONTEXT_SCHEMA_VERSION

    # Three context layers
    htf_structure: HTFStructureContext = field(default_factory=HTFStructureContext)
    location: LocationContext = field(default_factory=LocationContext)
    behaviour: BehaviourContext = field(default_factory=BehaviourContext)

    # Overall confidence (min of three layers)
    overall_confidence: float = 0.0

    # Combined observations
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "overall_confidence": round(self.overall_confidence, 4),
            "htf_structure": _to_dict(self.htf_structure),
            "location": _to_dict(self.location),
            "behaviour": _to_dict(self.behaviour),
            "observations": list(self.observations),
        }


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert frozen dataclass to dict."""
    from dataclasses import fields
    return {f.name: getattr(obj, f.name) for f in fields(obj)}
