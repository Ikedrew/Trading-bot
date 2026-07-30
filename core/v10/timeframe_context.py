"""V10 TimeframeContext — Formalised timeframe responsibility hierarchy.

Each timeframe owns specific information:
  H4:  Macro environment (trend, phase, volatility, major structure)
  H1:  Structural authority (BOS, CHoCH, zones, liquidity, premium/discount)
  M15: Opportunity formation (displacement, rejection, sweeps, refined zones)
  M5:  Execution environment ONLY (spread, micro-momentum, timing)

Lower timeframes CANNOT override higher timeframe determinations.
M5 never determines market bias, strategy, or opportunity validity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "v10_timeframe_context_v1"


# ═══════════════════════════════════════════════════════════════
# H4 — MACRO ENVIRONMENT (highest authority)
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class H4MacroEnvironment:
    """
    H4 macro environment — the broadest market condition.

    AUTHORITY: Trend state, market phase, major structure.
    No lower timeframe may contradict these determinations.
    """

    trend_state: str = ""                 # BULLISH / BEARISH / NEUTRAL
    trend_strength: float = 0.0           # 0.0–1.0
    market_phase: str = ""                # IMPULSE / PULLBACK / CONSOLIDATION / DISTRIBUTION
    range_or_trend: str = ""              # TRENDING / RANGING / TRANSITIONAL

    # Major structure
    major_structure: str = ""             # HH_HL / LH_LL / MIXED
    major_swing_high: float = 0.0
    major_swing_low: float = 0.0
    last_bos_direction: str = ""          # BULLISH / BEARISH / ""

    # Volatility environment
    volatility_state: str = ""            # EXPANSION / CONTRACTION / NEUTRAL
    atr: float = 0.0
    atr_percentile: float = 0.0           # 0.0–1.0

    # Major liquidity
    major_liquidity_above: float = 0.0
    major_liquidity_below: float = 0.0


# ═══════════════════════════════════════════════════════════════
# H1 — STRUCTURAL AUTHORITY
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class H1StructuralAuthority:
    """
    H1 structural authority — defines the current structural condition.

    AUTHORITY: Structure breaks, institutional zones, liquidity targets.
    Subordinate to H4 macro environment.
    """

    # Structure state
    structure_direction: str = ""         # BULLISH / BEARISH / NEUTRAL
    structure_type: str = ""              # HH_HL / LH_LL / MIXED
    structural_clarity: float = 0.0       # 0.0–1.0

    # Structure breaks
    bos_confirmed: bool = False
    bos_direction: str = ""               # BULLISH / BEARISH
    choch_detected: bool = False
    choch_direction: str = ""             # BULLISH / BEARISH

    # Swing levels
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

    # Premium/Discount (H1-level assessment)
    premium_discount: str = ""            # PREMIUM / DISCOUNT / EQUILIBRIUM
    range_position: float = 0.0           # 0.0–1.0


# ═══════════════════════════════════════════════════════════════
# M15 — OPPORTUNITY FORMATION
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class M15OpportunityFormation:
    """
    M15 opportunity formation — identifies developing market behaviour.

    AUTHORITY: Internal structure, displacement, sweeps, zone interactions.
    Subordinate to H1 structural authority.
    Does NOT determine overall market direction.
    """

    # Internal structure
    internal_bos: bool = False
    internal_bos_direction: str = ""
    internal_choch: bool = False

    # Pullback / retracement
    pullback_active: bool = False
    pullback_depth_atr: float = 0.0
    retracement_pct: float = 0.0

    # Displacement / momentum shift
    displacement_present: bool = False
    displacement_direction: str = ""
    displacement_magnitude_atr: float = 0.0

    # Liquidity interaction
    liquidity_sweep_detected: bool = False
    sweep_direction: str = ""             # Swept ABOVE (highs) or BELOW (lows)

    # Zone interaction
    at_order_block: bool = False
    order_block_type: str = ""            # DEMAND / SUPPLY
    at_fvg: bool = False
    fvg_type: str = ""                    # BULLISH / BEARISH

    # Refined zones
    refined_demand_ob_high: float = 0.0
    refined_demand_ob_low: float = 0.0
    refined_supply_ob_high: float = 0.0
    refined_supply_ob_low: float = 0.0
    nearest_fvg: float = 0.0

    # Swing
    swing_high: float = 0.0
    swing_low: float = 0.0
    range_position: float = 0.0


# ═══════════════════════════════════════════════════════════════
# M5 — EXECUTION ENVIRONMENT (lowest authority)
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class M5ExecutionEnvironment:
    """
    M5 execution environment — ONLY provides execution conditions.

    AUTHORITY: Spread, short-term momentum, entry timing.
    CANNOT determine: market bias, strategy, opportunity validity.
    This layer answers: "Can we execute cleanly right now?"
    """

    # Cost environment
    spread: float = 0.0
    spread_atr_ratio: float = 0.0
    atr: float = 0.0

    # Short-term momentum (descriptive, NOT directional authority)
    momentum_direction: str = ""          # BULLISH / BEARISH / NEUTRAL
    momentum_strength: float = 0.0        # 0.0–1.0

    # Entry timing conditions
    rejection_present: bool = False
    rejection_direction: str = ""
    rejection_strength_atr: float = 0.0
    confirmation_candle: bool = False
    local_bos: bool = False
    local_bos_direction: str = ""

    # Zone proximity (from M5 perspective)
    at_institutional_zone: bool = False
    zone_type: str = ""                   # DEMAND_OB / SUPPLY_OB / FVG / ""


# ═══════════════════════════════════════════════════════════════
# COMPOSITE: TIMEFRAME CONTEXT
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TimeframeContext:
    """
    Complete timeframe context with enforced responsibility hierarchy.

    Immutable. Created once per cycle. Feeds into V10MarketState.

    Hierarchy (highest authority first):
      H4 → H1 → M15 → M5
    """

    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _SCHEMA_VERSION

    h4: H4MacroEnvironment = field(default_factory=H4MacroEnvironment)
    h1: H1StructuralAuthority = field(default_factory=H1StructuralAuthority)
    m15: M15OpportunityFormation = field(default_factory=M15OpportunityFormation)
    m5: M5ExecutionEnvironment = field(default_factory=M5ExecutionEnvironment)

    # Validation flags
    hierarchy_valid: bool = True          # False if lower TF contradicts higher
    validation_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import fields as dc_fields
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "h4": _to_dict(self.h4),
            "h1": _to_dict(self.h1),
            "m15": _to_dict(self.m15),
            "m5": _to_dict(self.m5),
            "hierarchy_valid": self.hierarchy_valid,
            "validation_notes": list(self.validation_notes),
        }


def _to_dict(obj: Any) -> dict[str, Any]:
    from dataclasses import fields as dc_fields
    return {f.name: getattr(obj, f.name) for f in dc_fields(obj)}
