"""
V2Opportunity — Complete market state observation at the moment of opportunity.

This is an OBSERVATION schema for research. It does NOT:
    - Make trading decisions
    - Modify scores or confidence
    - Block or gate execution
    - Replace the existing pipeline

It captures: "Everything the bot knows before a potential trade"
so research can later determine which information has predictive value.

The V2Opportunity is the dataset that answers:
    "Which market information actually predicts future price movement?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "v2_opportunity_v1"


@dataclass(frozen=True)
class V2Opportunity:
    """
    Complete market state observation at opportunity detection time.

    Frozen (immutable) — once created, never modified.
    Outcome fields are populated later via a separate linkage process.
    """

    # ═══════════════════════════════════════════════════════════════════
    # IDENTITY
    # ═══════════════════════════════════════════════════════════════════
    opportunity_id: str
    correlation_id: str = ""
    timestamp_utc: float = 0.0
    symbol: str = ""
    timeframe: str = "M5"
    architecture_version: str = _SCHEMA_VERSION

    # ═══════════════════════════════════════════════════════════════════
    # H4 CONTEXT
    # ═══════════════════════════════════════════════════════════════════
    h4_regime: str = ""                  # TRENDING / RANGING / TRANSITIONAL
    h4_structure_state: str = ""         # HH_HL / LH_LL / MIXED
    h4_trend_direction: str = ""         # BULLISH / BEARISH / NEUTRAL
    h4_volatility_state: str = ""        # EXPANSION / CONTRACTION / NEUTRAL
    h4_distance_from_key_level: float = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # H1 CONTEXT
    # ═══════════════════════════════════════════════════════════════════
    h1_bias: str = ""                    # BULLISH / BEARISH / NEUTRAL
    h1_structure_type: str = ""          # HH_HL / LH_LL / MIXED
    h1_bos_confirmed: bool = False
    h1_bos_direction: str = ""           # BULLISH / BEARISH
    h1_choch_detected: bool = False

    # ═══════════════════════════════════════════════════════════════════
    # MARKET LOCATION
    # ═══════════════════════════════════════════════════════════════════
    near_support: bool = False
    near_resistance: bool = False
    distance_to_support: float = 0.0
    distance_to_resistance: float = 0.0
    liquidity_sweep_detected: bool = False
    liquidity_sweep_direction: str = ""  # BULLISH / BEARISH
    order_block_present: bool = False
    fair_value_gap_present: bool = False
    zone_type: str = ""                  # DEMAND / SUPPLY / NONE

    # ═══════════════════════════════════════════════════════════════════
    # M15 CONFIRMATION
    # ═══════════════════════════════════════════════════════════════════
    m15_structure_state: str = ""        # HH_HL / LH_LL / MIXED
    m15_confirmation_type: str = ""      # REJECTION / ENGULFING / DISPLACEMENT
    m15_displacement: float = 0.0
    m15_rejection_strength: float = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # M5 ENTRY FEATURES (pattern as feature, NOT signal)
    # ═══════════════════════════════════════════════════════════════════
    pattern_detected: str = ""           # HAMMER / ENGULFING / etc.
    pattern_direction: str = ""          # BUY / SELL (what pattern implies)
    pattern_quality: float = 0.0         # 0-1 (candle geometry quality)
    candle_range: float = 0.0            # High - Low of trigger candle
    body_ratio: float = 0.0             # Body / Range
    wick_ratio: float = 0.0             # Dominant wick / Range
    m5_displacement: float = 0.0         # Price displacement from mean

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTION CONDITIONS
    # ═══════════════════════════════════════════════════════════════════
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    spread_atr_ratio: float = 0.0
    atr: float = 0.0
    volatility: float = 0.0
    session: str = ""                    # LONDON / NY / ASIA / OFF
    market_open_state: str = ""          # OPEN / CLOSED / PRE_MARKET

    # ═══════════════════════════════════════════════════════════════════
    # RISK GEOMETRY
    # ═══════════════════════════════════════════════════════════════════
    proposed_direction: str = ""         # BUY / SELL
    proposed_entry: float = 0.0
    structure_stop_distance: float = 0.0
    candle_stop_distance: float = 0.0
    atr_stop_distance: float = 0.0
    risk_distance_pips: float = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # PROBABILITY PLACEHOLDER (future prediction layer)
    # ═══════════════════════════════════════════════════════════════════
    predicted_probability: float | None = None
    probability_model_version: str | None = None
    confidence_score: float | None = None

    # ═══════════════════════════════════════════════════════════════════
    # OUTCOME PLACEHOLDER (linked after trade resolves)
    # ═══════════════════════════════════════════════════════════════════
    outcome_recorded: bool = False
    outcome_raw_r: float | None = None
    mfe: float | None = None
    mae: float | None = None
    reached_positive_target: bool | None = None
    reached_negative_target: bool | None = None
    bars_to_outcome: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.architecture_version,
            "opportunity_id": self.opportunity_id,
            "correlation_id": self.correlation_id,
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            # H4
            "h4_regime": self.h4_regime,
            "h4_structure_state": self.h4_structure_state,
            "h4_trend_direction": self.h4_trend_direction,
            "h4_volatility_state": self.h4_volatility_state,
            "h4_distance_from_key_level": self.h4_distance_from_key_level,
            # H1
            "h1_bias": self.h1_bias,
            "h1_structure_type": self.h1_structure_type,
            "h1_bos_confirmed": self.h1_bos_confirmed,
            "h1_bos_direction": self.h1_bos_direction,
            "h1_choch_detected": self.h1_choch_detected,
            # Location
            "near_support": self.near_support,
            "near_resistance": self.near_resistance,
            "distance_to_support": self.distance_to_support,
            "distance_to_resistance": self.distance_to_resistance,
            "liquidity_sweep_detected": self.liquidity_sweep_detected,
            "liquidity_sweep_direction": self.liquidity_sweep_direction,
            "order_block_present": self.order_block_present,
            "fair_value_gap_present": self.fair_value_gap_present,
            "zone_type": self.zone_type,
            # M15
            "m15_structure_state": self.m15_structure_state,
            "m15_confirmation_type": self.m15_confirmation_type,
            "m15_displacement": self.m15_displacement,
            "m15_rejection_strength": self.m15_rejection_strength,
            # M5
            "pattern_detected": self.pattern_detected,
            "pattern_direction": self.pattern_direction,
            "pattern_quality": self.pattern_quality,
            "candle_range": self.candle_range,
            "body_ratio": self.body_ratio,
            "wick_ratio": self.wick_ratio,
            "m5_displacement": self.m5_displacement,
            # Execution
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "spread_atr_ratio": self.spread_atr_ratio,
            "atr": self.atr,
            "volatility": self.volatility,
            "session": self.session,
            "market_open_state": self.market_open_state,
            # Risk
            "proposed_direction": self.proposed_direction,
            "proposed_entry": self.proposed_entry,
            "structure_stop_distance": self.structure_stop_distance,
            "candle_stop_distance": self.candle_stop_distance,
            "atr_stop_distance": self.atr_stop_distance,
            "risk_distance_pips": self.risk_distance_pips,
            # Probability
            "predicted_probability": self.predicted_probability,
            "probability_model_version": self.probability_model_version,
            "confidence_score": self.confidence_score,
            # Outcome
            "outcome_recorded": self.outcome_recorded,
            "outcome_raw_r": self.outcome_raw_r,
            "mfe": self.mfe,
            "mae": self.mae,
            "reached_positive_target": self.reached_positive_target,
            "reached_negative_target": self.reached_negative_target,
            "bars_to_outcome": self.bars_to_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "V2Opportunity":
        """Reconstruct from serialized dict."""
        return cls(
            opportunity_id=data.get("opportunity_id", ""),
            correlation_id=data.get("correlation_id", ""),
            timestamp_utc=float(data.get("timestamp_utc", 0)),
            symbol=data.get("symbol", ""),
            timeframe=data.get("timeframe", "M5"),
            architecture_version=data.get("schema_version", _SCHEMA_VERSION),
            h4_regime=data.get("h4_regime", ""),
            h4_structure_state=data.get("h4_structure_state", ""),
            h4_trend_direction=data.get("h4_trend_direction", ""),
            h4_volatility_state=data.get("h4_volatility_state", ""),
            h4_distance_from_key_level=float(data.get("h4_distance_from_key_level", 0)),
            h1_bias=data.get("h1_bias", ""),
            h1_structure_type=data.get("h1_structure_type", ""),
            h1_bos_confirmed=bool(data.get("h1_bos_confirmed", False)),
            h1_bos_direction=data.get("h1_bos_direction", ""),
            h1_choch_detected=bool(data.get("h1_choch_detected", False)),
            near_support=bool(data.get("near_support", False)),
            near_resistance=bool(data.get("near_resistance", False)),
            distance_to_support=float(data.get("distance_to_support", 0)),
            distance_to_resistance=float(data.get("distance_to_resistance", 0)),
            liquidity_sweep_detected=bool(data.get("liquidity_sweep_detected", False)),
            liquidity_sweep_direction=data.get("liquidity_sweep_direction", ""),
            order_block_present=bool(data.get("order_block_present", False)),
            fair_value_gap_present=bool(data.get("fair_value_gap_present", False)),
            zone_type=data.get("zone_type", ""),
            m15_structure_state=data.get("m15_structure_state", ""),
            m15_confirmation_type=data.get("m15_confirmation_type", ""),
            m15_displacement=float(data.get("m15_displacement", 0)),
            m15_rejection_strength=float(data.get("m15_rejection_strength", 0)),
            pattern_detected=data.get("pattern_detected", ""),
            pattern_direction=data.get("pattern_direction", ""),
            pattern_quality=float(data.get("pattern_quality", 0)),
            candle_range=float(data.get("candle_range", 0)),
            body_ratio=float(data.get("body_ratio", 0)),
            wick_ratio=float(data.get("wick_ratio", 0)),
            m5_displacement=float(data.get("m5_displacement", 0)),
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            spread=float(data.get("spread", 0)),
            spread_atr_ratio=float(data.get("spread_atr_ratio", 0)),
            atr=float(data.get("atr", 0)),
            volatility=float(data.get("volatility", 0)),
            session=data.get("session", ""),
            market_open_state=data.get("market_open_state", ""),
            proposed_direction=data.get("proposed_direction", ""),
            proposed_entry=float(data.get("proposed_entry", 0)),
            structure_stop_distance=float(data.get("structure_stop_distance", 0)),
            candle_stop_distance=float(data.get("candle_stop_distance", 0)),
            atr_stop_distance=float(data.get("atr_stop_distance", 0)),
            risk_distance_pips=float(data.get("risk_distance_pips", 0)),
            predicted_probability=data.get("predicted_probability"),
            probability_model_version=data.get("probability_model_version"),
            confidence_score=data.get("confidence_score"),
            outcome_recorded=bool(data.get("outcome_recorded", False)),
            outcome_raw_r=data.get("outcome_raw_r"),
            mfe=data.get("mfe"),
            mae=data.get("mae"),
            reached_positive_target=data.get("reached_positive_target"),
            reached_negative_target=data.get("reached_negative_target"),
            bars_to_outcome=data.get("bars_to_outcome"),
        )
