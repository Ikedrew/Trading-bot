"""
Multi-Timeframe Authority — Type Definitions.

All types are immutable (frozen dataclasses) except where explicitly noted.
These types form the contract between the cache layer, analyzers, and pipeline integration.

Ownership: core/timeframes/types.py
Dependencies: strategy.signals (Side enum only)
Must NOT import from: cache.py, integration.py, engine.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ─── ENUMS ────────────────────────────────────────────────────────────────────


class RegimeClassification(Enum):
    """H4 macro market environment classification."""

    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    TRANSITIONAL = "TRANSITIONAL"


class BiasDirection(Enum):
    """H1 directional preference."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


# ─── SNAPSHOT TYPES (analyzer outputs, cached per-timeframe) ──────────────────


@dataclass(frozen=True)
class RegimeSnapshot:
    """
    H4 regime analysis result.

    Produced by: h4_regime.py analyzer
    Cached by: TimeframeCache (replaced on each new H4 bar)
    Consumed by: integration.py (apply_htf_constraints)
    """

    classification: RegimeClassification
    confidence: float  # 0.0–1.0 indicating classification certainty
    bar_time: int  # unix timestamp of the H4 bar that produced this
    atr_ratio: float  # current ATR / rolling average ATR
    ema_slope: float  # normalized EMA slope direction
    trend_bias: str = "NEUTRAL"    # "BULLISH" | "BEARISH" | "NEUTRAL" (shadow — no decision impact)
    trend_strength: float = 0.0    # 0.0–1.0 structural trend confidence (shadow — no decision impact)


@dataclass(frozen=True)
class BiasSnapshot:
    """
    H1 bias analysis result.

    Produced by: h1_bias.py analyzer
    Cached by: TimeframeCache (replaced on each new H1 bar)
    Consumed by: integration.py (apply_htf_constraints)
    """

    direction: BiasDirection
    confidence: float  # 0.0–1.0
    bar_time: int  # unix timestamp of the H1 bar that produced this
    ema_position: float  # price distance from EMA (normalized by ATR)
    swing_structure: str  # "HH_HL" | "LH_LL" | "MIXED"
    bos_confirmed: bool = False  # True if price broke last swing level (Break of Structure)
    bos_direction: str = ""  # "BULLISH" | "BEARISH" | "" (direction of the break)
    bos_level: float | None = None  # The swing price that was broken (structural reference for stop/target)
    # H1 structure price levels (Phase 4C.1 — for horizon-aware SL/TP)
    last_swing_high: float | None = None  # Most recent confirmed H1 swing high price
    last_swing_low: float | None = None   # Most recent confirmed H1 swing low price


@dataclass(frozen=True)
class StructureSnapshot:
    """
    M15 structure analysis result.

    Produced by: m15_structure.py analyzer
    Cached by: TimeframeCache (replaced on each new M15 bar)
    Consumed by: integration.py (apply_htf_constraints)
    """

    quality_score: float  # 0.0–1.0 indicating structural favorability
    bar_time: int  # unix timestamp of the M15 bar that produced this
    nearest_support: float  # price level
    nearest_resistance: float  # price level
    at_key_level: bool  # price within ATR distance of S/R
    order_block_present: bool  # bullish/bearish OB detected


# ─── PIPELINE INTERFACE TYPES ─────────────────────────────────────────────────


@dataclass(frozen=True)
class HTFContext:
    """
    Immutable snapshot of all higher-timeframe authority states.

    Built once per M5 bar evaluation from cached snapshots.
    Consumed by M5 pipeline (read-only). Never mutated after creation.

    Ownership: nobody (value object)
    Lifetime: single M5 cycle (created, consumed, discarded)
    """

    macro: MacroSnapshot | None = None
    regime: RegimeSnapshot | None = None
    bias: BiasSnapshot | None = None
    structure: StructureSnapshot | None = None

    @property
    def is_populated(self) -> bool:
        """True if at least one HTF layer has data."""
        return any(x is not None for x in (self.regime, self.bias, self.structure))


# ─── MACRO CONTEXT TYPE ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroSnapshot:
    """
    MN/W1/D1 macro context — the market story before H4.

    Produced by: TimeframeCache (from D1/W1/MN analyzers)
    Consumed by: macro_alignment.py (compute_macro_alignment)

    Ownership: nobody (value object)
    Lifetime: refreshed on D1/W1/MN bar close, cached between.
    """

    # Monthly (from RegimeSnapshot on MN1 candles)
    monthly_trend: str = ""              # BULLISH / BEARISH / NEUTRAL
    monthly_trend_strength: float = 0.0  # 0.0–1.0
    monthly_classification: str = ""   # TRENDING_BULLISH / TRENDING_BEARISH / RANGING / VOLATILE / TRANSITIONAL

    # Weekly (from BiasSnapshot on W1 candles)
    weekly_trend: str = ""               # BULLISH / BEARISH / NEUTRAL
    weekly_trend_strength: float = 0.0   # 0.0–1.0
    weekly_swing_high: float = 0.0       # Price level
    weekly_swing_low: float = 0.0        # Price level
    weekly_bos_level: float = 0.0        # Institutional reference
    weekly_range_position: float = 0.0   # 0.0–1.0

    # Daily (from RegimeSnapshot + BiasSnapshot on D1 candles)
    daily_bias: str = ""                 # BULLISH / BEARISH / NEUTRAL
    daily_bias_strength: float = 0.0     # 0.0–1.0
    daily_swing_high: float = 0.0        # Today's structural high
    daily_swing_low: float = 0.0         # Today's structural low
    daily_range_position: float = 0.0    # 0.0–1.0
    daily_atr_ratio: float = 1.0         # Today's ATR vs average (volatility context)

    # Meta
    bar_time: int = 0                    # Timestamp of latest daily bar


@dataclass(frozen=True)
class HTFInfluence:
    """
    Result of applying HTF constraints to M5 scoring.

    Produced by: integration.py (apply_htf_constraints)
    Consumed by: scoring_engine (score/threshold adjustments) and engine.py (blocking)

    Ownership: nobody (value object)
    Lifetime: single M5 cycle

    Behaviour rules:
    - Does NOT decide trades directly
    - Only modifies scoring / gating
    - Must remain deterministic and pure
    """

    score_adjustment: float = 0.0  # net bonus/penalty to add to confluence score
    min_score_adjustment: float = 0.0  # increase to minimum score threshold
    directional_block: bool = False  # True = trade blocked by HTF bias contradiction
    structural_block: bool = False  # True = trade blocked by M15 quality gate
    block_reason: str = ""  # human-readable reason if blocked
    breakdown: dict[str, float] = field(default_factory=dict)  # per-layer contribution for audit

    @property
    def is_blocking(self) -> bool:
        """True if any hard block is active."""
        return self.directional_block or self.structural_block

    @property
    def has_influence(self) -> bool:
        """True if any scoring modification is applied."""
        return self.score_adjustment != 0.0 or self.min_score_adjustment != 0.0 or self.is_blocking
