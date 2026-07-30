"""V10 MarketUnderstanding — Single authoritative market state object.

Aggregates all V3 intelligence layers into one immutable snapshot:
  - H4 macro environment (trend, volatility, phase)
  - H1 structural authority (BOS, CHoCH, zones, swing levels)
  - M15 internal structure (pullback, displacement, refined zones)
  - M5 execution environment (momentum, rejection, spread)
  - Regime / volatility / behaviour classification
  - Location (premium/discount, institutional zones, liquidity)

This object is DESCRIPTIVE ONLY — it says what the market IS,
never what to DO about it.

No direction signals. No entry logic. No execution decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "v10_market_state_v1"


# ═══════════════════════════════════════════════════════════════
# H4 MACRO LAYER
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class H4State:
    """H4 macro environment — overall market condition."""

    trend: str = ""                       # BULLISH / BEARISH / NEUTRAL
    trend_strength: float = 0.0           # 0.0–1.0
    market_phase: str = ""                # IMPULSE / PULLBACK / CONSOLIDATION / DISTRIBUTION
    structure_type: str = ""              # HH_HL / LH_LL / MIXED

    swing_high: float = 0.0
    swing_low: float = 0.0
    last_bos_direction: str = ""          # BULLISH / BEARISH / ""

    atr: float = 0.0
    volatility_state: str = ""            # EXPANSION / CONTRACTION / NEUTRAL
    atr_percentile: float = 0.0           # 0.0–1.0

    major_liquidity_above: float = 0.0
    major_liquidity_below: float = 0.0


# ═══════════════════════════════════════════════════════════════
# H1 STRUCTURE LAYER
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class H1State:
    """H1 structural authority — which structure controls price."""

    dominant_trend: str = ""              # BULLISH / BEARISH / NEUTRAL
    structure_type: str = ""              # HH_HL / LH_LL / MIXED
    structural_clarity: float = 0.0       # 0.0–1.0

    bos_confirmed: bool = False
    bos_direction: str = ""               # BULLISH / BEARISH
    bos_level: float = 0.0               # The swing price that was broken (structural stop reference)
    choch_detected: bool = False
    choch_direction: str = ""             # BULLISH / BEARISH

    swing_high: float = 0.0
    swing_low: float = 0.0

    # Institutional zones
    demand_ob_high: float = 0.0
    demand_ob_low: float = 0.0
    supply_ob_high: float = 0.0
    supply_ob_low: float = 0.0
    nearest_fvg_above: float = 0.0
    nearest_fvg_below: float = 0.0

    # Liquidity
    equal_highs_level: float = 0.0
    equal_lows_level: float = 0.0
    session_high: float = 0.0
    session_low: float = 0.0


# ═══════════════════════════════════════════════════════════════
# M15 INTERNAL STRUCTURE LAYER
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class M15State:
    """M15 refinement — behaviour inside H1 structure."""

    internal_bos: bool = False
    internal_bos_direction: str = ""
    internal_choch: bool = False

    pullback_active: bool = False
    pullback_depth_atr: float = 0.0
    retracement_pct: float = 0.0          # % of last impulse retraced

    displacement_present: bool = False
    displacement_direction: str = ""
    displacement_magnitude_atr: float = 0.0

    # Refined zones
    refined_demand_ob_high: float = 0.0
    refined_demand_ob_low: float = 0.0
    refined_supply_ob_high: float = 0.0
    refined_supply_ob_low: float = 0.0
    nearest_fvg: float = 0.0

    swing_high: float = 0.0
    swing_low: float = 0.0
    range_position: float = 0.0           # 0=discount, 0.5=equilibrium, 1=premium


# ═══════════════════════════════════════════════════════════════
# M5 EXECUTION ENVIRONMENT
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class M5State:
    """M5 execution environment — describes readiness, NOT decisions."""

    local_bos: bool = False
    local_bos_direction: str = ""

    momentum_direction: str = ""          # BULLISH / BEARISH / NEUTRAL
    momentum_strength: float = 0.0        # 0.0–1.0

    rejection_present: bool = False
    rejection_direction: str = ""
    rejection_strength_atr: float = 0.0

    at_institutional_zone: bool = False
    zone_type: str = ""                   # DEMAND_OB / SUPPLY_OB / FVG / ""
    confirmation_candle: bool = False

    atr: float = 0.0
    spread: float = 0.0
    spread_atr_ratio: float = 0.0


# ═══════════════════════════════════════════════════════════════
# REGIME & BEHAVIOUR
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RegimeState:
    """Classified market regime and behavioural context."""

    regime: str = ""                      # TRENDING / RANGING / TRANSITIONAL / VOLATILE
    regime_confidence: float = 0.0

    volatility_state: str = ""            # EXPANSION / CONTRACTION / NEUTRAL
    volatility_level: float = 0.0         # 0.0–1.0

    expansion_state: str = ""             # EXPANDING / COMPRESSING / NEUTRAL
    compression_bars: int = 0

    momentum_direction: str = ""          # BULLISH / BEARISH / NEUTRAL
    momentum_strength: float = 0.0


# ═══════════════════════════════════════════════════════════════
# LOCATION
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LocationState:
    """Where price sits relative to institutional levels."""

    location_type: str = ""               # DEMAND_OB / SUPPLY_OB / BEARISH_FVG / BULLISH_FVG / OPEN_SPACE
    inside_institutional_zone: bool = False
    zone_quality: float = 0.0             # 0.0–1.0
    zone_mitigated: bool = False

    premium_discount: str = ""            # PREMIUM / DISCOUNT / EQUILIBRIUM
    range_position: float = 0.0           # 0.0–1.0

    liquidity_above: bool = False
    liquidity_below: bool = False
    nearest_liquidity_direction: str = ""
    nearest_liquidity_distance_pips: float = 0.0

    demand_zones_nearby: int = 0
    supply_zones_nearby: int = 0
    fvg_zones_nearby: int = 0


# ═══════════════════════════════════════════════════════════════
# HTF ALIGNMENT (derived: do timeframes agree?)
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HTFAlignment:
    """Derived higher-timeframe alignment assessment."""

    macro_bias: str = ""                  # BULLISH / BEARISH / NEUTRAL / CONFLICTED
    macro_bias_strength: float = 0.0
    structure_alignment: float = 0.0      # 0.0–1.0 (how well H4/H1/M15 agree)
    authority_timeframe: str = ""         # H4 / H1 / M15
    phase_alignment: str = ""             # ALIGNED / CONFLICTED / NEUTRAL


# ═══════════════════════════════════════════════════════════════
# COMPOSITE: V10 MARKET STATE
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class V10MarketState:
    """
    Single authoritative market state object.

    Immutable snapshot created once per observation cycle.
    Consumed read-only by strategy selection and opportunity detection.

    Contains:
      - Raw multi-timeframe observations (H4, H1, M15, M5)
      - Derived regime/behaviour classification
      - Location context
      - HTF alignment assessment

    Does NOT contain:
      - Trade signals or directions
      - Entry/exit logic
      - Risk parameters
      - Execution decisions
    """

    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _SCHEMA_VERSION

    # Multi-timeframe observation layers
    h4: H4State = field(default_factory=H4State)
    h1: H1State = field(default_factory=H1State)
    m15: M15State = field(default_factory=M15State)
    m5: M5State = field(default_factory=M5State)

    # Derived context layers
    regime: RegimeState = field(default_factory=RegimeState)
    location: LocationState = field(default_factory=LocationState)
    htf_alignment: HTFAlignment = field(default_factory=HTFAlignment)

    # Overall confidence in the reading (0.0–1.0)
    confidence: float = 0.0

    # Structured observations for research/debugging
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSONL persistence."""
        from dataclasses import fields as dc_fields
        result = {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "confidence": round(self.confidence, 4),
            "h4": _to_dict(self.h4),
            "h1": _to_dict(self.h1),
            "m15": _to_dict(self.m15),
            "m5": _to_dict(self.m5),
            "regime": _to_dict(self.regime),
            "location": _to_dict(self.location),
            "htf_alignment": _to_dict(self.htf_alignment),
            "observations": list(self.observations),
        }
        return result


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert a frozen dataclass to a plain dict."""
    from dataclasses import fields as dc_fields
    return {f.name: getattr(obj, f.name) for f in dc_fields(obj)}
