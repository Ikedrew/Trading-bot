"""
V2 Opportunity Observer — Captures complete market state for research.

Registered as observer #8 in ObserverRegistry. Called after every
engine evaluation cycle. Builds and persists a V2Opportunity record
containing the full market context for future predictive analysis.

This module:
    - NEVER influences trading decisions
    - NEVER modifies engine_result or any mutable state
    - NEVER blocks execution
    - Only READS context and WRITES research observations

Design: fire-and-forget observation. Failure never propagates.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def observe_v2_opportunity(ctx: Any) -> None:
    """
    V2 opportunity observation — called by ObserverRegistry as observer #8.

    Extracts all available market context from ObserverContext,
    builds a V2Opportunity, and persists it for research.

    Args:
        ctx: ObserverContext from the observer dispatch.

    Never raises. Failure is logged and silently ignored.
    """
    try:
        _do_observe(ctx)
    except Exception as exc:
        logger.debug("[V2_OPPORTUNITY_OBSERVER] failed: %s", exc)


def _do_observe(ctx: Any) -> None:
    """Internal observation logic. May raise."""
    from core.v2_opportunity_builder import build_v2_opportunity, persist_v2_opportunity

    engine_result = ctx.engine_result or {}
    market_ctx = getattr(ctx, "market_context", None)
    htf_context = ctx.htf_context

    # Use MarketContext preferentially, fall back to htf_context
    context_source = market_ctx if market_ctx is not None else htf_context

    # Extract pattern features from engine_result
    pattern_detected = engine_result.get("pattern", "") or ""
    side = engine_result.get("side", "") or ""
    # Pattern quality from assessment if available
    assessment = engine_result.get("assessment")
    pattern_quality = 0.0
    if assessment:
        pattern_quality = float(getattr(assessment, "pattern_quality", 0.0) or 0.0)

    # Candle geometry (from best_pattern signal if available)
    candle_range = 0.0
    body_ratio = 0.0
    wick_ratio = 0.0
    best_pattern = engine_result.get("_best_pattern")
    if best_pattern and ctx.candles and hasattr(best_pattern, "bar_index"):
        try:
            bar_idx = best_pattern.bar_index
            if 0 <= bar_idx < len(ctx.candles):
                c = ctx.candles[bar_idx]
                candle_range = c.high - c.low
                body = abs(c.close - c.open)
                if candle_range > 0:
                    body_ratio = body / candle_range
                    upper_wick = c.high - max(c.open, c.close)
                    lower_wick = min(c.open, c.close) - c.low
                    wick_ratio = max(upper_wick, lower_wick) / candle_range
        except Exception:
            pass

    # Risk geometry
    candle_stop = 0.0
    structure_stop = 0.0
    # Extract stop-loss geometry if available in engine_result
    intent = engine_result.get("intent")
    if intent and hasattr(intent, "sl"):
        entry_ref = getattr(intent, "entry_reference", 0.0) or ((ctx.bid + ctx.ask) / 2 if ctx.bid > 0 else 0.0)
        if entry_ref > 0:
            candle_stop = abs(entry_ref - intent.sl)

    # Structure stop from INTRADAY horizon if available
    # (M15 nearest_support/resistance → stop distance)
    if context_source and hasattr(context_source, "m15"):
        m15 = getattr(context_source, "m15", None)
        if m15:
            support = float(getattr(m15, "nearest_support", 0.0) or 0.0)
            resistance = float(getattr(m15, "nearest_resistance", 0.0) or 0.0)
            entry_price = (ctx.bid + ctx.ask) / 2 if ctx.bid > 0 else 0.0
            if entry_price > 0:
                if side == "BUY" and support > 0:
                    structure_stop = entry_price - support
                elif side == "SELL" and resistance > 0:
                    structure_stop = resistance - entry_price

    # ATR from engine_state
    atr_val = 0.0
    if ctx.engine_state:
        atr_val = float(getattr(ctx.engine_state, "volatility_filter", 0.0) or 0.0)

    # Session from time
    session = ""
    try:
        import time as _t
        hour = _t.gmtime(int(ctx.bar_time)).tm_hour
        if 7 <= hour < 12:
            session = "LONDON"
        elif 12 <= hour < 17:
            session = "NY"
        elif 0 <= hour < 7:
            session = "ASIA"
        else:
            session = "OFF"
    except Exception:
        pass

    # Correlation ID and entity_id
    correlation_id = engine_result.get("correlation_id", "") or ""
    entity_id = engine_result.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"

    # Build V2Opportunity
    opp = build_v2_opportunity(
        symbol=ctx.symbol,
        timestamp_utc=ctx.bar_time,
        correlation_id=correlation_id or entity_id,
        market_context=context_source,
        pattern_detected=pattern_detected,
        pattern_direction=side,
        pattern_quality=pattern_quality,
        candle_range=candle_range,
        body_ratio=body_ratio,
        wick_ratio=wick_ratio,
        bid=ctx.bid,
        ask=ctx.ask,
        atr=atr_val,
        session=session,
        proposed_direction=side,
        candle_stop_distance=candle_stop,
        structure_stop_distance=structure_stop,
        atr_stop_distance=atr_val * 1.5 if atr_val > 0 else 0.0,
    )

    # Persist
    persist_v2_opportunity(opp)
