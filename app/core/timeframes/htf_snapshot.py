"""
HTF Snapshot — Frozen multi-timeframe state descriptor per M5 decision cycle.

Produces a single immutable snapshot of H4/H1/M15 state at each M5 bar close.
This snapshot is:
    - Computed ONCE per cycle
    - Immutable for that cycle
    - Reused across all scoring, filtering, and logging
    - Based ONLY on closed HTF bars (no forming bar)
    - OBSERVATIONAL ONLY — does NOT trigger/block trades

Mental model:
    HTF = "market weather system"
    M5  = "execution engine"

Output contract:
    htf_contract:
        closed_bar_only: true
        snapshot_mode: true
        decision_influence: false (state descriptor only)

Usage:
    from core.timeframes.htf_snapshot import build_htf_snapshot

    snapshot = build_htf_snapshot(htf_context, cycle_id, symbol)
    # Attach to decision audit, event stream, S3 logs
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from core.timeframes.types import (
    BiasDirection,
    HTFContext,
    RegimeClassification,
    RegimeSnapshot,
    BiasSnapshot,
    StructureSnapshot,
)


# ═══════════════════════════════════════════════════════════════════════════════
# BIAS CLASSIFICATION (per timeframe)
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_bias(direction: BiasDirection | None) -> str:
    """Convert BiasDirection enum to canonical string."""
    if direction is None:
        return "NEUTRAL"
    mapping = {
        BiasDirection.BULLISH: "BULLISH",
        BiasDirection.BEARISH: "BEARISH",
        BiasDirection.NEUTRAL: "NEUTRAL",
    }
    return mapping.get(direction, "NEUTRAL")


def _classify_regime(classification: RegimeClassification | None) -> str:
    """Convert RegimeClassification to canonical regime string."""
    if classification is None:
        return "RANGING"
    mapping = {
        RegimeClassification.TRENDING_BULLISH: "TRENDING",
        RegimeClassification.TRENDING_BEARISH: "TRENDING",
        RegimeClassification.RANGING: "RANGING",
        RegimeClassification.VOLATILE: "CHOP",
        RegimeClassification.TRANSITIONAL: "RANGING",
    }
    return mapping.get(classification, "RANGING")


def _classify_volatility(atr_ratio: float) -> str:
    """Classify volatility from ATR ratio."""
    if atr_ratio > 1.5:
        return "EXPANDING"
    elif atr_ratio > 0.8:
        return "MEDIUM"
    return "LOW"


def _compute_agreement(bias_a: str, bias_b: str) -> float:
    """Compute agreement score between two bias strings (0.0-1.0)."""
    if bias_a == bias_b:
        return 1.0
    if bias_a == "NEUTRAL" or bias_b == "NEUTRAL":
        return 0.5
    # Opposing biases
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TimeframeBias:
    """Single timeframe state descriptor."""
    bias: str               # BULLISH | BEARISH | NEUTRAL
    strength: float         # 0.0 – 1.0
    regime: str             # TRENDING | RANGING | CHOP
    volatility: str         # LOW | MEDIUM | EXPANDING


@dataclass(frozen=True)
class HTFAlignment:
    """Cross-timeframe agreement scores."""
    h4_h1_agreement: float          # 0.0 – 1.0
    h1_m15_agreement: float         # 0.0 – 1.0
    overall_alignment_score: float  # 0.0 – 1.0


@dataclass(frozen=True)
class HTFSnapshot:
    """
    Frozen multi-timeframe state snapshot — computed once per M5 cycle.

    Contract:
        closed_bar_only: true
        snapshot_mode: true
        decision_influence: false
    """
    cycle_id: int
    symbol: str
    timestamp_utc: str

    # Per-timeframe state
    h4: TimeframeBias
    h1: TimeframeBias
    m15: TimeframeBias

    # Cross-timeframe alignment
    alignment: HTFAlignment

    # Contract metadata
    htf_contract: dict

    def to_dict(self) -> dict[str, Any]:
        """Convert to flat dict for S3/Athena serialisation."""
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "timeframe_bias": {
                "H4": asdict(self.h4),
                "H1": asdict(self.h1),
                "M15": asdict(self.m15),
            },
            "alignment": asdict(self.alignment),
            "htf_contract": self.htf_contract,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_htf_snapshot(
    htf_context: HTFContext | None,
    cycle_id: int,
    symbol: str,
    timestamp_utc: str = "",
) -> HTFSnapshot:
    """
    Build a frozen HTF snapshot from the cached HTFContext.

    Called once per M5 cycle. Result is immutable and reused across
    all scoring, filtering, and logging for that cycle.

    Args:
        htf_context: Cached HTF state (from TimeframeCache.get_htf_context())
        cycle_id: Current scanner cycle number
        symbol: Trading symbol
        timestamp_utc: ISO timestamp (auto-generated if empty)

    Returns:
        Frozen HTFSnapshot — observational only, no decision power.
    """
    if not timestamp_utc:
        from core.clock import utc_ms, utc_ms_to_iso
        timestamp_utc = utc_ms_to_iso(utc_ms())

    # ─── H4 STATE ─────────────────────────────────────────────────────
    h4_regime: RegimeSnapshot | None = htf_context.regime if htf_context else None
    if h4_regime is not None:
        h4_bias_str = getattr(h4_regime, "trend_bias", "NEUTRAL") or "NEUTRAL"
        h4_strength = getattr(h4_regime, "trend_strength", 0.0) or h4_regime.confidence
        h4_regime_str = _classify_regime(h4_regime.classification)
        h4_vol = _classify_volatility(h4_regime.atr_ratio)
    else:
        h4_bias_str = "NEUTRAL"
        h4_strength = 0.0
        h4_regime_str = "RANGING"
        h4_vol = "MEDIUM"

    h4 = TimeframeBias(
        bias=h4_bias_str,
        strength=round(min(1.0, h4_strength), 3),
        regime=h4_regime_str,
        volatility=h4_vol,
    )

    # ─── H1 STATE ─────────────────────────────────────────────────────
    h1_snap: BiasSnapshot | None = htf_context.bias if htf_context else None
    if h1_snap is not None:
        h1_bias_str = _classify_bias(h1_snap.direction)
        h1_strength = h1_snap.confidence
        # H1 regime inferred from swing structure
        h1_regime_str = "TRENDING" if h1_snap.swing_structure in ("HH_HL", "LH_LL") else "RANGING"
        h1_vol = "MEDIUM"  # H1 doesn't have independent ATR ratio
    else:
        h1_bias_str = "NEUTRAL"
        h1_strength = 0.0
        h1_regime_str = "RANGING"
        h1_vol = "MEDIUM"

    h1 = TimeframeBias(
        bias=h1_bias_str,
        strength=round(min(1.0, h1_strength), 3),
        regime=h1_regime_str,
        volatility=h1_vol,
    )

    # ─── M15 STATE ────────────────────────────────────────────────────
    m15_snap: StructureSnapshot | None = htf_context.structure if htf_context else None
    if m15_snap is not None:
        # M15 bias inferred from structure quality + key level proximity
        m15_quality = m15_snap.quality_score
        m15_bias_str = "NEUTRAL"  # M15 is structural, not directional
        m15_strength = m15_quality
        m15_regime_str = "TRENDING" if m15_quality > 0.6 else "RANGING" if m15_quality > 0.3 else "CHOP"
        m15_vol = "MEDIUM"
    else:
        m15_bias_str = "NEUTRAL"
        m15_strength = 0.0
        m15_regime_str = "RANGING"
        m15_vol = "MEDIUM"

    m15 = TimeframeBias(
        bias=m15_bias_str,
        strength=round(min(1.0, m15_strength), 3),
        regime=m15_regime_str,
        volatility=m15_vol,
    )

    # ─── ALIGNMENT ────────────────────────────────────────────────────
    h4_h1 = _compute_agreement(h4_bias_str, h1_bias_str)
    h1_m15 = _compute_agreement(h1_bias_str, m15_bias_str)
    overall = round((h4_h1 * 0.6 + h1_m15 * 0.4), 3)

    alignment = HTFAlignment(
        h4_h1_agreement=round(h4_h1, 3),
        h1_m15_agreement=round(h1_m15, 3),
        overall_alignment_score=overall,
    )

    # ─── CONTRACT METADATA ────────────────────────────────────────────
    contract = {
        "closed_bar_only": True,
        "snapshot_mode": True,
        "decision_influence": False,
    }

    return HTFSnapshot(
        cycle_id=cycle_id,
        symbol=symbol,
        timestamp_utc=timestamp_utc,
        h4=h4,
        h1=h1,
        m15=m15,
        alignment=alignment,
        htf_contract=contract,
    )
