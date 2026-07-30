"""
V3Opportunity — Market location and liquidity observation at the moment of opportunity.

This is an OBSERVATION schema for research. It does NOT:
    - Make trading decisions
    - Modify scores or confidence
    - Block or gate any trade
    - Import execution, risk, or pipeline modules

V3 extends beyond V2 by capturing granular LOCATION and LIQUIDITY information:
    - Where in the market structure is price positioned?
    - What liquidity features exist nearby?
    - How does the current location relate to institutional order flow concepts?
    - What is the quality and recency of nearby structure levels?

The V3 research question:
    "Does precise market location and liquidity context predict trade outcome
     when general context (V2) does not?"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_SCHEMA_VERSION = "v3_opportunity_v1"


@dataclass(frozen=True)
class V3Opportunity:
    """
    Market location and liquidity observation at opportunity detection time.

    Frozen (immutable) — once created, never modified.
    Outcome fields populated later via linkage.
    """

    # ═══════════════════════════════════════════════════════════════════
    # IDENTITY
    # ═══════════════════════════════════════════════════════════════════
    opportunity_id: str
    correlation_id: str = ""
    timestamp_utc: float = 0.0
    symbol: str = ""
    timeframe: str = "M5"
    schema_version: str = _SCHEMA_VERSION

    # ═══════════════════════════════════════════════════════════════════
    # PRICE POSITION — Where is price relative to structure?
    # ═══════════════════════════════════════════════════════════════════
    price_at_observation: float = 0.0

    # H4 structure levels
    h4_swing_high: float = 0.0
    h4_swing_low: float = 0.0
    h4_range_position: float = 0.0       # 0.0=at low, 1.0=at high (within H4 range)
    h4_distance_from_high_pips: float = 0.0
    h4_distance_from_low_pips: float = 0.0

    # H1 structure levels
    h1_swing_high: float = 0.0
    h1_swing_low: float = 0.0
    h1_range_position: float = 0.0       # 0.0=at low, 1.0=at high
    h1_distance_from_high_pips: float = 0.0
    h1_distance_from_low_pips: float = 0.0
    h1_last_bos_price: float = 0.0       # Price where last BOS occurred
    h1_distance_from_bos_pips: float = 0.0

    # M15 structure levels
    m15_swing_high: float = 0.0
    m15_swing_low: float = 0.0
    m15_range_position: float = 0.0
    m15_last_displacement_price: float = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # SUPPORT / RESISTANCE — Nearest levels and their quality
    # ═══════════════════════════════════════════════════════════════════
    nearest_support_price: float = 0.0
    nearest_support_distance_pips: float = 0.0
    nearest_support_touches: int = 0     # Times price bounced from this level
    nearest_support_age_bars: int = 0    # How old is this level (M5 bars)
    nearest_support_timeframe: str = ""  # Which TF defined this level (H4/H1/M15)

    nearest_resistance_price: float = 0.0
    nearest_resistance_distance_pips: float = 0.0
    nearest_resistance_touches: int = 0
    nearest_resistance_age_bars: int = 0
    nearest_resistance_timeframe: str = ""

    # Structure quality
    support_quality_score: float = 0.0   # 0-1 composite (touches, recency, TF)
    resistance_quality_score: float = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # LIQUIDITY — Where liquidity pools likely exist
    # ═══════════════════════════════════════════════════════════════════
    # Equal highs/lows (liquidity targets)
    equal_highs_above: bool = False
    equal_highs_distance_pips: float = 0.0
    equal_highs_count: int = 0           # Number of touches forming the equal level

    equal_lows_below: bool = False
    equal_lows_distance_pips: float = 0.0
    equal_lows_count: int = 0

    # Previous session liquidity
    prev_session_high: float = 0.0
    prev_session_low: float = 0.0
    distance_to_prev_session_high_pips: float = 0.0
    distance_to_prev_session_low_pips: float = 0.0
    prev_session_high_swept: bool = False
    prev_session_low_swept: bool = False

    # Previous day liquidity
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    distance_to_prev_day_high_pips: float = 0.0
    distance_to_prev_day_low_pips: float = 0.0
    prev_day_high_swept: bool = False
    prev_day_low_swept: bool = False

    # Sweep status
    liquidity_sweep_just_occurred: bool = False
    sweep_direction: str = ""            # BULLISH (swept lows) / BEARISH (swept highs)
    sweep_distance_pips: float = 0.0     # How far past the level
    bars_since_sweep: int = 0

    # ═══════════════════════════════════════════════════════════════════
    # ORDER BLOCKS — Institutional supply/demand zones
    # ═══════════════════════════════════════════════════════════════════
    nearest_demand_ob_price: float = 0.0
    nearest_demand_ob_distance_pips: float = 0.0
    demand_ob_timeframe: str = ""        # H4 / H1 / M15
    demand_ob_mitigated: bool = False    # Has price already returned to it
    demand_ob_strength: float = 0.0      # 0-1 based on displacement that created it

    nearest_supply_ob_price: float = 0.0
    nearest_supply_ob_distance_pips: float = 0.0
    supply_ob_timeframe: str = ""
    supply_ob_mitigated: bool = False
    supply_ob_strength: float = 0.0

    price_inside_ob: bool = False        # Currently within an order block
    ob_type_if_inside: str = ""          # DEMAND / SUPPLY

    # ═══════════════════════════════════════════════════════════════════
    # FAIR VALUE GAPS — Imbalance zones
    # ═══════════════════════════════════════════════════════════════════
    nearest_fvg_above_price: float = 0.0
    nearest_fvg_above_distance_pips: float = 0.0
    fvg_above_filled_pct: float = 0.0    # 0-1 how much has been filled

    nearest_fvg_below_price: float = 0.0
    nearest_fvg_below_distance_pips: float = 0.0
    fvg_below_filled_pct: float = 0.0

    price_inside_fvg: bool = False
    fvg_direction_if_inside: str = ""    # BULLISH / BEARISH
    total_unfilled_fvgs_above: int = 0
    total_unfilled_fvgs_below: int = 0

    # ═══════════════════════════════════════════════════════════════════
    # DISPLACEMENT & MOMENTUM — Recent price behaviour at location
    # ═══════════════════════════════════════════════════════════════════
    displacement_into_level: bool = False  # Did price move aggressively into this zone?
    displacement_magnitude_atr: float = 0.0  # Size of move in ATR multiples
    rejection_candle_present: bool = False
    rejection_body_ratio: float = 0.0     # Body/range of rejection candle
    rejection_wick_atr_ratio: float = 0.0 # Wick size in ATR multiples

    bars_at_current_level: int = 0        # How long price has been at this zone
    consolidation_range_pips: float = 0.0 # Range of consolidation at level

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTION CONTEXT (minimal — for cost adjustment)
    # ═══════════════════════════════════════════════════════════════════
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    spread_risk_ratio: float = 0.0       # spread / nearest stop distance
    atr: float = 0.0
    session: str = ""                    # LONDON / NY / ASIA / OFF

    # ═══════════════════════════════════════════════════════════════════
    # OUTCOME PLACEHOLDER (linked after trade resolves)
    # ═══════════════════════════════════════════════════════════════════
    outcome_linked: bool = False
    outcome_raw_r: float | None = None
    outcome_win: bool | None = None
    outcome_mfe_r: float | None = None
    outcome_mae_r: float | None = None
    outcome_exit_reason: str | None = None
    outcome_bars_held: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "opportunity_id": self.opportunity_id,
            "correlation_id": self.correlation_id,
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            # Price position
            "price_at_observation": self.price_at_observation,
            "h4_swing_high": self.h4_swing_high,
            "h4_swing_low": self.h4_swing_low,
            "h4_range_position": self.h4_range_position,
            "h4_distance_from_high_pips": self.h4_distance_from_high_pips,
            "h4_distance_from_low_pips": self.h4_distance_from_low_pips,
            "h1_swing_high": self.h1_swing_high,
            "h1_swing_low": self.h1_swing_low,
            "h1_range_position": self.h1_range_position,
            "h1_distance_from_high_pips": self.h1_distance_from_high_pips,
            "h1_distance_from_low_pips": self.h1_distance_from_low_pips,
            "h1_last_bos_price": self.h1_last_bos_price,
            "h1_distance_from_bos_pips": self.h1_distance_from_bos_pips,
            "m15_swing_high": self.m15_swing_high,
            "m15_swing_low": self.m15_swing_low,
            "m15_range_position": self.m15_range_position,
            "m15_last_displacement_price": self.m15_last_displacement_price,
            # Support/Resistance
            "nearest_support_price": self.nearest_support_price,
            "nearest_support_distance_pips": self.nearest_support_distance_pips,
            "nearest_support_touches": self.nearest_support_touches,
            "nearest_support_age_bars": self.nearest_support_age_bars,
            "nearest_support_timeframe": self.nearest_support_timeframe,
            "nearest_resistance_price": self.nearest_resistance_price,
            "nearest_resistance_distance_pips": self.nearest_resistance_distance_pips,
            "nearest_resistance_touches": self.nearest_resistance_touches,
            "nearest_resistance_age_bars": self.nearest_resistance_age_bars,
            "nearest_resistance_timeframe": self.nearest_resistance_timeframe,
            "support_quality_score": self.support_quality_score,
            "resistance_quality_score": self.resistance_quality_score,
            # Liquidity
            "equal_highs_above": self.equal_highs_above,
            "equal_highs_distance_pips": self.equal_highs_distance_pips,
            "equal_highs_count": self.equal_highs_count,
            "equal_lows_below": self.equal_lows_below,
            "equal_lows_distance_pips": self.equal_lows_distance_pips,
            "equal_lows_count": self.equal_lows_count,
            "prev_session_high": self.prev_session_high,
            "prev_session_low": self.prev_session_low,
            "distance_to_prev_session_high_pips": self.distance_to_prev_session_high_pips,
            "distance_to_prev_session_low_pips": self.distance_to_prev_session_low_pips,
            "prev_session_high_swept": self.prev_session_high_swept,
            "prev_session_low_swept": self.prev_session_low_swept,
            "prev_day_high": self.prev_day_high,
            "prev_day_low": self.prev_day_low,
            "distance_to_prev_day_high_pips": self.distance_to_prev_day_high_pips,
            "distance_to_prev_day_low_pips": self.distance_to_prev_day_low_pips,
            "prev_day_high_swept": self.prev_day_high_swept,
            "prev_day_low_swept": self.prev_day_low_swept,
            "liquidity_sweep_just_occurred": self.liquidity_sweep_just_occurred,
            "sweep_direction": self.sweep_direction,
            "sweep_distance_pips": self.sweep_distance_pips,
            "bars_since_sweep": self.bars_since_sweep,
            # Order blocks
            "nearest_demand_ob_price": self.nearest_demand_ob_price,
            "nearest_demand_ob_distance_pips": self.nearest_demand_ob_distance_pips,
            "demand_ob_timeframe": self.demand_ob_timeframe,
            "demand_ob_mitigated": self.demand_ob_mitigated,
            "demand_ob_strength": self.demand_ob_strength,
            "nearest_supply_ob_price": self.nearest_supply_ob_price,
            "nearest_supply_ob_distance_pips": self.nearest_supply_ob_distance_pips,
            "supply_ob_timeframe": self.supply_ob_timeframe,
            "supply_ob_mitigated": self.supply_ob_mitigated,
            "supply_ob_strength": self.supply_ob_strength,
            "price_inside_ob": self.price_inside_ob,
            "ob_type_if_inside": self.ob_type_if_inside,
            # Fair value gaps
            "nearest_fvg_above_price": self.nearest_fvg_above_price,
            "nearest_fvg_above_distance_pips": self.nearest_fvg_above_distance_pips,
            "fvg_above_filled_pct": self.fvg_above_filled_pct,
            "nearest_fvg_below_price": self.nearest_fvg_below_price,
            "nearest_fvg_below_distance_pips": self.nearest_fvg_below_distance_pips,
            "fvg_below_filled_pct": self.fvg_below_filled_pct,
            "price_inside_fvg": self.price_inside_fvg,
            "fvg_direction_if_inside": self.fvg_direction_if_inside,
            "total_unfilled_fvgs_above": self.total_unfilled_fvgs_above,
            "total_unfilled_fvgs_below": self.total_unfilled_fvgs_below,
            # Displacement
            "displacement_into_level": self.displacement_into_level,
            "displacement_magnitude_atr": self.displacement_magnitude_atr,
            "rejection_candle_present": self.rejection_candle_present,
            "rejection_body_ratio": self.rejection_body_ratio,
            "rejection_wick_atr_ratio": self.rejection_wick_atr_ratio,
            "bars_at_current_level": self.bars_at_current_level,
            "consolidation_range_pips": self.consolidation_range_pips,
            # Execution
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "spread_risk_ratio": self.spread_risk_ratio,
            "atr": self.atr,
            "session": self.session,
            # Outcome
            "outcome_linked": self.outcome_linked,
            "outcome_raw_r": self.outcome_raw_r,
            "outcome_win": self.outcome_win,
            "outcome_mfe_r": self.outcome_mfe_r,
            "outcome_mae_r": self.outcome_mae_r,
            "outcome_exit_reason": self.outcome_exit_reason,
            "outcome_bars_held": self.outcome_bars_held,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "V3Opportunity":
        """Reconstruct from serialized dict."""
        return cls(
            opportunity_id=data.get("opportunity_id", ""),
            correlation_id=data.get("correlation_id", ""),
            timestamp_utc=float(data.get("timestamp_utc", 0)),
            symbol=data.get("symbol", ""),
            timeframe=data.get("timeframe", "M5"),
            schema_version=data.get("schema_version", _SCHEMA_VERSION),
            # Price position
            price_at_observation=float(data.get("price_at_observation", 0)),
            h4_swing_high=float(data.get("h4_swing_high", 0)),
            h4_swing_low=float(data.get("h4_swing_low", 0)),
            h4_range_position=float(data.get("h4_range_position", 0)),
            h4_distance_from_high_pips=float(data.get("h4_distance_from_high_pips", 0)),
            h4_distance_from_low_pips=float(data.get("h4_distance_from_low_pips", 0)),
            h1_swing_high=float(data.get("h1_swing_high", 0)),
            h1_swing_low=float(data.get("h1_swing_low", 0)),
            h1_range_position=float(data.get("h1_range_position", 0)),
            h1_distance_from_high_pips=float(data.get("h1_distance_from_high_pips", 0)),
            h1_distance_from_low_pips=float(data.get("h1_distance_from_low_pips", 0)),
            h1_last_bos_price=float(data.get("h1_last_bos_price", 0)),
            h1_distance_from_bos_pips=float(data.get("h1_distance_from_bos_pips", 0)),
            m15_swing_high=float(data.get("m15_swing_high", 0)),
            m15_swing_low=float(data.get("m15_swing_low", 0)),
            m15_range_position=float(data.get("m15_range_position", 0)),
            m15_last_displacement_price=float(data.get("m15_last_displacement_price", 0)),
            # Support/Resistance
            nearest_support_price=float(data.get("nearest_support_price", 0)),
            nearest_support_distance_pips=float(data.get("nearest_support_distance_pips", 0)),
            nearest_support_touches=int(data.get("nearest_support_touches", 0)),
            nearest_support_age_bars=int(data.get("nearest_support_age_bars", 0)),
            nearest_support_timeframe=data.get("nearest_support_timeframe", ""),
            nearest_resistance_price=float(data.get("nearest_resistance_price", 0)),
            nearest_resistance_distance_pips=float(data.get("nearest_resistance_distance_pips", 0)),
            nearest_resistance_touches=int(data.get("nearest_resistance_touches", 0)),
            nearest_resistance_age_bars=int(data.get("nearest_resistance_age_bars", 0)),
            nearest_resistance_timeframe=data.get("nearest_resistance_timeframe", ""),
            support_quality_score=float(data.get("support_quality_score", 0)),
            resistance_quality_score=float(data.get("resistance_quality_score", 0)),
            # Liquidity
            equal_highs_above=bool(data.get("equal_highs_above", False)),
            equal_highs_distance_pips=float(data.get("equal_highs_distance_pips", 0)),
            equal_highs_count=int(data.get("equal_highs_count", 0)),
            equal_lows_below=bool(data.get("equal_lows_below", False)),
            equal_lows_distance_pips=float(data.get("equal_lows_distance_pips", 0)),
            equal_lows_count=int(data.get("equal_lows_count", 0)),
            prev_session_high=float(data.get("prev_session_high", 0)),
            prev_session_low=float(data.get("prev_session_low", 0)),
            distance_to_prev_session_high_pips=float(data.get("distance_to_prev_session_high_pips", 0)),
            distance_to_prev_session_low_pips=float(data.get("distance_to_prev_session_low_pips", 0)),
            prev_session_high_swept=bool(data.get("prev_session_high_swept", False)),
            prev_session_low_swept=bool(data.get("prev_session_low_swept", False)),
            prev_day_high=float(data.get("prev_day_high", 0)),
            prev_day_low=float(data.get("prev_day_low", 0)),
            distance_to_prev_day_high_pips=float(data.get("distance_to_prev_day_high_pips", 0)),
            distance_to_prev_day_low_pips=float(data.get("distance_to_prev_day_low_pips", 0)),
            prev_day_high_swept=bool(data.get("prev_day_high_swept", False)),
            prev_day_low_swept=bool(data.get("prev_day_low_swept", False)),
            liquidity_sweep_just_occurred=bool(data.get("liquidity_sweep_just_occurred", False)),
            sweep_direction=data.get("sweep_direction", ""),
            sweep_distance_pips=float(data.get("sweep_distance_pips", 0)),
            bars_since_sweep=int(data.get("bars_since_sweep", 0)),
            # Order blocks
            nearest_demand_ob_price=float(data.get("nearest_demand_ob_price", 0)),
            nearest_demand_ob_distance_pips=float(data.get("nearest_demand_ob_distance_pips", 0)),
            demand_ob_timeframe=data.get("demand_ob_timeframe", ""),
            demand_ob_mitigated=bool(data.get("demand_ob_mitigated", False)),
            demand_ob_strength=float(data.get("demand_ob_strength", 0)),
            nearest_supply_ob_price=float(data.get("nearest_supply_ob_price", 0)),
            nearest_supply_ob_distance_pips=float(data.get("nearest_supply_ob_distance_pips", 0)),
            supply_ob_timeframe=data.get("supply_ob_timeframe", ""),
            supply_ob_mitigated=bool(data.get("supply_ob_mitigated", False)),
            supply_ob_strength=float(data.get("supply_ob_strength", 0)),
            price_inside_ob=bool(data.get("price_inside_ob", False)),
            ob_type_if_inside=data.get("ob_type_if_inside", ""),
            # Fair value gaps
            nearest_fvg_above_price=float(data.get("nearest_fvg_above_price", 0)),
            nearest_fvg_above_distance_pips=float(data.get("nearest_fvg_above_distance_pips", 0)),
            fvg_above_filled_pct=float(data.get("fvg_above_filled_pct", 0)),
            nearest_fvg_below_price=float(data.get("nearest_fvg_below_price", 0)),
            nearest_fvg_below_distance_pips=float(data.get("nearest_fvg_below_distance_pips", 0)),
            fvg_below_filled_pct=float(data.get("fvg_below_filled_pct", 0)),
            price_inside_fvg=bool(data.get("price_inside_fvg", False)),
            fvg_direction_if_inside=data.get("fvg_direction_if_inside", ""),
            total_unfilled_fvgs_above=int(data.get("total_unfilled_fvgs_above", 0)),
            total_unfilled_fvgs_below=int(data.get("total_unfilled_fvgs_below", 0)),
            # Displacement
            displacement_into_level=bool(data.get("displacement_into_level", False)),
            displacement_magnitude_atr=float(data.get("displacement_magnitude_atr", 0)),
            rejection_candle_present=bool(data.get("rejection_candle_present", False)),
            rejection_body_ratio=float(data.get("rejection_body_ratio", 0)),
            rejection_wick_atr_ratio=float(data.get("rejection_wick_atr_ratio", 0)),
            bars_at_current_level=int(data.get("bars_at_current_level", 0)),
            consolidation_range_pips=float(data.get("consolidation_range_pips", 0)),
            # Execution
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            spread=float(data.get("spread", 0)),
            spread_risk_ratio=float(data.get("spread_risk_ratio", 0)),
            atr=float(data.get("atr", 0)),
            session=data.get("session", ""),
            # Outcome
            outcome_linked=bool(data.get("outcome_linked", False)),
            outcome_raw_r=data.get("outcome_raw_r"),
            outcome_win=data.get("outcome_win"),
            outcome_mfe_r=data.get("outcome_mfe_r"),
            outcome_mae_r=data.get("outcome_mae_r"),
            outcome_exit_reason=data.get("outcome_exit_reason"),
            outcome_bars_held=data.get("outcome_bars_held"),
        )
