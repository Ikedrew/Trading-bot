"""
Market Context Models — Pure data definitions.

All types are frozen (immutable) dataclasses.
No imports from core/pipeline/, risk/, execution/, or strategy/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ─── ENUMS ────────────────────────────────────────────────────────────────────


class Direction(str, Enum):
    """Unified cross-timeframe directional conclusion."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Regime(str, Enum):
    """Macro market environment."""
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    TRANSITIONAL = "TRANSITIONAL"


class Phase(str, Enum):
    """Market phase within the current regime."""
    IMPULSE = "IMPULSE"
    PULLBACK = "PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    EXHAUSTION = "EXHAUSTION"
    REVERSAL = "REVERSAL"


# ─── TIMEFRAME SUMMARIES ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class H4Summary:
    """Condensed H4 state for MarketContext."""
    regime: str = "TRANSITIONAL"      # TRENDING_BULLISH | TRENDING_BEARISH | RANGING | VOLATILE | TRANSITIONAL
    confidence: float = 0.0
    trend_bias: str = "NEUTRAL"       # BULLISH | BEARISH | NEUTRAL
    trend_strength: float = 0.0
    atr_ratio: float = 1.0


@dataclass(frozen=True)
class H1Summary:
    """Condensed H1 state for MarketContext."""
    direction: str = "NEUTRAL"        # BULLISH | BEARISH | NEUTRAL
    confidence: float = 0.0
    swing_structure: str = "MIXED"    # HH_HL | LH_LL | MIXED
    ema_position: float = 0.0
    bos_confirmed: bool = False       # Break of Structure detected
    bos_direction: str = ""           # BULLISH | BEARISH | ""
    bos_level: float = 0.0            # The swing price that was broken (structural stop reference)
    # Swing price levels (propagated from BiasSnapshot for location research)
    swing_high: float = 0.0           # Most recent confirmed H1 swing high
    swing_low: float = 0.0            # Most recent confirmed H1 swing low


@dataclass(frozen=True)
class M15Summary:
    """
    Condensed M15 state for MarketContext.

    Authority: M15 owns setup context (quality, levels, order blocks).
    This is the authoritative source for "is there a valid opportunity forming?"
    M5 does NOT contribute to setup context — only to execution timing.
    """
    quality_score: float = 0.0          # Structure quality (0.0–1.0)
    at_key_level: bool = False          # Price near support/resistance
    order_block_present: bool = False   # Institutional interest detected
    nearest_support: float = 0.0        # Nearest support price level
    nearest_resistance: float = 0.0     # Nearest resistance price level
    # Swing price levels (from M15 pivot detection — same as nearest S/R)
    swing_high: float = 0.0             # Most recent confirmed M15 swing high
    swing_low: float = 0.0              # Most recent confirmed M15 swing low


@dataclass(frozen=True)
class M5Summary:
    """
    Condensed M5 execution context for MarketContext.

    Authority: M5 owns ONLY execution timing and trigger conditions.
    M5 does NOT own: regime, structure, BOS, phase, setup quality, or key levels.
    Those are owned by H4, H1, and M15 respectively.

    M5 answers: "Given the market context, is this the correct moment to execute?"
    """
    # ─── TRIGGER STATE ────────────────────────────────────────────────
    bias_phase: str = "EXPIRED"        # EXPIRED | FORMING | CONFIRMING | CONFIRMED | WEAKENING
    bias_strength: float = 0.0         # 0–100 FSM conviction strength
    bias_direction: str = "NEUTRAL"    # BUY | SELL | NEUTRAL

    # ─── EXECUTION ENVIRONMENT ────────────────────────────────────────
    regime_state: str = "RANGING"      # M5 micro-regime (TREND_UP | TREND_DOWN | RANGING) — diagnostic only

    # ─── TIMING READINESS ─────────────────────────────────────────────
    trigger_ready: bool = False         # True when bias is CONFIRMED (ready to trigger)
    confirmation_strength: str = ""     # STRONG | WEAK | "" (last candle quality)


# ─── MAIN MODEL ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketContext:
    """
    Single authoritative market interpretation.

    Produced once per symbol per cycle by MarketContextBuilder.
    Consumed read-only by downstream systems.
    Phase 1: observational/persistence only — does NOT influence decisions.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    symbol: str
    cycle_id: int
    timestamp_utc: float
    context_version: int = 1

    # ─── UNIFIED INTERPRETATION ───────────────────────────────────────
    direction: Direction = Direction.NEUTRAL
    direction_confidence: float = 0.0
    regime: Regime = Regime.TRANSITIONAL
    regime_confidence: float = 0.0
    phase: Phase = Phase.CONSOLIDATION
    phase_confidence: float = 0.0

    # ─── TRADABILITY ──────────────────────────────────────────────────
    tradability_score: float = 0.0
    alignment_score: float = 0.0

    # ─── TIMEFRAME COMPONENTS ─────────────────────────────────────────
    h4: H4Summary = field(default_factory=H4Summary)
    h1: H1Summary = field(default_factory=H1Summary)
    m15: M15Summary = field(default_factory=M15Summary)
    m5: M5Summary = field(default_factory=M5Summary)

    # ─── CONFLICT RESOLUTION ──────────────────────────────────────────
    conflict_detected: bool = False
    conflict_description: str = ""
    resolution_method: str = "HIERARCHY"

    # ─── CHANGE METADATA ──────────────────────────────────────────────
    is_material_change: bool = False
    change_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to flat dict for JSONL persistence."""
        return {
            "context_version": self.context_version,
            "symbol": self.symbol,
            "cycle_id": self.cycle_id,
            "timestamp_utc": self.timestamp_utc,
            "direction": self.direction.value,
            "direction_confidence": round(self.direction_confidence, 4),
            "regime": self.regime.value,
            "regime_confidence": round(self.regime_confidence, 4),
            "phase": self.phase.value,
            "phase_confidence": round(self.phase_confidence, 4),
            "tradability_score": round(self.tradability_score, 4),
            "alignment_score": round(self.alignment_score, 4),
            "h4": {
                "regime": self.h4.regime,
                "confidence": round(self.h4.confidence, 4),
                "trend_bias": self.h4.trend_bias,
                "trend_strength": round(self.h4.trend_strength, 4),
                "atr_ratio": round(self.h4.atr_ratio, 4),
            },
            "h1": {
                "direction": self.h1.direction,
                "confidence": round(self.h1.confidence, 4),
                "swing_structure": self.h1.swing_structure,
                "ema_position": round(self.h1.ema_position, 4),
                "bos_confirmed": self.h1.bos_confirmed,
                "bos_direction": self.h1.bos_direction,
                "swing_high": round(self.h1.swing_high, 8),
                "swing_low": round(self.h1.swing_low, 8),
            },
            "m15": {
                "quality_score": round(self.m15.quality_score, 4),
                "at_key_level": self.m15.at_key_level,
                "order_block_present": self.m15.order_block_present,
                "nearest_support": round(self.m15.nearest_support, 6),
                "nearest_resistance": round(self.m15.nearest_resistance, 6),
                "swing_high": round(self.m15.swing_high, 8),
                "swing_low": round(self.m15.swing_low, 8),
            },
            "m5": {
                "bias_phase": self.m5.bias_phase,
                "bias_strength": round(self.m5.bias_strength, 4),
                "bias_direction": self.m5.bias_direction,
                "regime_state": self.m5.regime_state,
                "trigger_ready": self.m5.trigger_ready,
                "confirmation_strength": self.m5.confirmation_strength,
            },
            "conflict_detected": self.conflict_detected,
            "conflict_description": self.conflict_description,
            "resolution_method": self.resolution_method,
            "is_material_change": self.is_material_change,
            "change_reason": self.change_reason,
        }

    def to_summary(self) -> dict[str, Any]:
        """Compact version for embedding in other records."""
        return {
            "direction": self.direction.value,
            "regime": self.regime.value,
            "phase": self.phase.value,
            "tradability_score": round(self.tradability_score, 4),
            "conflict_detected": self.conflict_detected,
        }
