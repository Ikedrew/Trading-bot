"""
V2OpportunityBuilder — Constructs V2Opportunity observations from available market state.

This is an OBSERVATION layer. It does NOT:
    - Make trading decisions
    - Modify scores or confidence
    - Block or gate any trade
    - Import execution, risk, or pipeline modules

It extracts available context from MarketContext, pattern detection output,
and the execution environment to produce a V2Opportunity record for research.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.v2_opportunity import V2Opportunity, _SCHEMA_VERSION

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/v2_opportunities"


def build_v2_opportunity(
    *,
    symbol: str,
    timestamp_utc: float,
    correlation_id: str = "",
    # Market context (from MarketContext object or raw fields)
    market_context: Any = None,
    # Pattern info (from engine result or detected patterns)
    pattern_detected: str = "",
    pattern_direction: str = "",
    pattern_quality: float = 0.0,
    # Candle features
    candle_range: float = 0.0,
    body_ratio: float = 0.0,
    wick_ratio: float = 0.0,
    # Execution environment
    bid: float = 0.0,
    ask: float = 0.0,
    atr: float = 0.0,
    session: str = "",
    # Risk geometry
    proposed_direction: str = "",
    candle_stop_distance: float = 0.0,
    structure_stop_distance: float = 0.0,
    atr_stop_distance: float = 0.0,
) -> V2Opportunity:
    """
    Build a V2Opportunity from available market state.

    Extracts HTF context from MarketContext if provided.
    Falls back to empty strings/zeros for unavailable fields.
    Never raises — returns partial opportunity on any failure.
    """
    # Generate unique ID
    opp_id = f"v2_{symbol}_{int(timestamp_utc)}_{uuid.uuid4().hex[:8]}"

    # Spread
    spread = abs(ask - bid) if (bid > 0 and ask > 0) else 0.0
    spread_atr = spread / atr if atr > 0 else 0.0

    # Risk distance in pips
    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    risk_dist = structure_stop_distance or candle_stop_distance or atr_stop_distance
    risk_pips = risk_dist / pip_size if risk_dist > 0 else 0.0

    # Extract from MarketContext if available
    h4_regime = ""
    h4_structure_state = ""
    h4_trend_direction = ""
    h4_volatility_state = ""
    h1_bias = ""
    h1_structure_type = ""
    h1_bos_confirmed = False
    h1_bos_direction = ""
    h1_choch_detected = False
    near_support = False
    near_resistance = False
    order_block_present = False
    m15_structure_state = ""
    m15_displacement = 0.0
    m15_rejection_strength = 0.0
    volatility = 0.0

    if market_context is not None:
        try:
            _extract_market_context(market_context, locals())
        except Exception:
            pass

        # H4
        h4 = getattr(market_context, "h4", None)
        if h4:
            h4_regime = getattr(h4, "regime", "") or ""
            h4_trend_direction = getattr(h4, "trend_bias", "") or ""
            h4_volatility_state = "EXPANSION" if getattr(h4, "atr_ratio", 1.0) > 1.3 else "CONTRACTION" if getattr(h4, "atr_ratio", 1.0) < 0.7 else "NEUTRAL"

        # H1
        h1 = getattr(market_context, "h1", None)
        if h1:
            h1_bias = getattr(h1, "direction", "") or ""
            h1_structure_type = getattr(h1, "swing_structure", "") or ""
            h1_bos_confirmed = bool(getattr(h1, "bos_confirmed", False))
            h1_bos_direction = getattr(h1, "bos_direction", "") or ""

        # M15
        m15 = getattr(market_context, "m15", None)
        if m15:
            near_support = bool(getattr(m15, "at_key_level", False))
            near_resistance = bool(getattr(m15, "at_key_level", False))
            order_block_present = bool(getattr(m15, "order_block_present", False))
            m15_rejection_strength = float(getattr(m15, "quality_score", 0.0) or 0.0)

        # Unified
        _regime = getattr(market_context, "regime", None)
        if _regime and not h4_regime:
            h4_regime = _regime.value if hasattr(_regime, "value") else str(_regime)

        volatility = float(getattr(market_context, "tradability_score", 0.0) or 0.0)

    # Determine proposed entry
    proposed_entry = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0

    return V2Opportunity(
        opportunity_id=opp_id,
        correlation_id=correlation_id,
        timestamp_utc=timestamp_utc,
        symbol=symbol,
        timeframe="M5",
        architecture_version=_SCHEMA_VERSION,
        # H4
        h4_regime=h4_regime,
        h4_structure_state=h4_structure_state,
        h4_trend_direction=h4_trend_direction,
        h4_volatility_state=h4_volatility_state,
        # H1
        h1_bias=h1_bias,
        h1_structure_type=h1_structure_type,
        h1_bos_confirmed=h1_bos_confirmed,
        h1_bos_direction=h1_bos_direction,
        h1_choch_detected=h1_choch_detected,
        # Location
        near_support=near_support,
        near_resistance=near_resistance,
        order_block_present=order_block_present,
        # M15
        m15_structure_state=m15_structure_state,
        m15_rejection_strength=m15_rejection_strength,
        m15_displacement=m15_displacement,
        # M5 pattern (as feature, not signal)
        pattern_detected=pattern_detected,
        pattern_direction=pattern_direction,
        pattern_quality=pattern_quality,
        candle_range=candle_range,
        body_ratio=body_ratio,
        wick_ratio=wick_ratio,
        # Execution
        bid=bid,
        ask=ask,
        spread=spread,
        spread_atr_ratio=round(spread_atr, 6),
        atr=atr,
        volatility=volatility,
        session=session,
        # Risk
        proposed_direction=proposed_direction,
        proposed_entry=proposed_entry,
        structure_stop_distance=structure_stop_distance,
        candle_stop_distance=candle_stop_distance,
        atr_stop_distance=atr_stop_distance,
        risk_distance_pips=round(risk_pips, 2),
    )


def _extract_market_context(ctx: Any, local_vars: dict) -> None:
    """Helper to extract nested MarketContext fields. May raise."""
    pass  # Extraction handled inline above for clarity


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


def persist_v2_opportunity(opp: V2Opportunity) -> bool:
    """
    Persist a V2Opportunity to local JSONL. Fire-and-forget.

    Storage: logs/v2_opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl
    Never raises. Never blocks trading.
    """
    try:
        symbol = opp.symbol or "UNKNOWN"
        ts = opp.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(opp.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        return True
    except Exception as exc:
        logger.debug("[V2_OPP_PERSIST] failed: %s", exc)
        return False


def read_v2_opportunities(*, symbol: str | None = None) -> list[dict[str, Any]]:
    """Read persisted V2Opportunities. For research queries."""
    base = Path(_LOCAL_DIR)
    if not base.exists():
        return []

    results: list[dict[str, Any]] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]

    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for filepath in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    return results
