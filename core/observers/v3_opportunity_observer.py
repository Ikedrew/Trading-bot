"""
V3 Opportunity Observer — Captures market location and liquidity state for research.

Registered as observer #9 in ObserverRegistry. Called after every engine
evaluation cycle. Builds and persists a V3Opportunity record focused on
WHERE price is relative to structure, liquidity, and institutional levels.

This module:
    - NEVER influences trading decisions
    - NEVER modifies engine_result or any mutable state
    - NEVER blocks any operation
    - Only READS context and WRITES research observations

Design: fire-and-forget observation. Failure never propagates.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def observe_v3_opportunity(ctx: Any) -> None:
    """
    V3 opportunity observation — called by ObserverRegistry as observer #9.

    Extracts location and liquidity information from ObserverContext,
    builds a V3Opportunity, and persists it for research.

    Args:
        ctx: ObserverContext from the observer dispatch.

    Never raises. Failure is logged and silently ignored.
    """
    try:
        _do_observe(ctx)
    except Exception as exc:
        logger.debug("[V3_OPPORTUNITY_OBSERVER] failed: %s", exc)


def _do_observe(ctx: Any) -> None:
    """Internal observation logic. May raise."""
    from core.v3_opportunity_builder import build_v3_opportunity, persist_v3_opportunity

    engine_result = ctx.engine_result or {}
    market_ctx = getattr(ctx, "market_context", None)
    htf_context = ctx.htf_context

    # Use MarketContext preferentially, fall back to htf_context
    context_source = market_ctx if market_ctx is not None else htf_context

    # Mid price
    price = (ctx.bid + ctx.ask) / 2 if ctx.bid > 0 and ctx.ask > 0 else 0.0

    # ATR computed directly from candles (14-period average true range in price units)
    atr_val = 0.0
    if ctx.candles and len(ctx.candles) > 14:
        recent = ctx.candles[-14:]
        atr_val = sum(c.high - c.low for c in recent) / len(recent)

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

    # Correlation ID / entity_id
    correlation_id = engine_result.get("correlation_id", "") or ""
    entity_id = engine_result.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"

    # ─── Run market intelligence detectors ────────────────────────────
    liquidity_snap = None
    fvg_snap = None
    ob_snap = None

    if ctx.candles and atr_val > 0:
        closed_i = getattr(ctx, "closed_i", -1)
        if closed_i > 10:
            try:
                from core.market_intelligence.liquidity_detector import detect_liquidity
                liquidity_snap = detect_liquidity(
                    ctx.candles, price, closed_i, ctx.symbol)
            except Exception:
                pass

            try:
                from core.market_intelligence.fvg_detector import detect_fvgs
                fvg_snap = detect_fvgs(
                    ctx.candles, price, closed_i, atr_val, ctx.symbol)
            except Exception:
                pass

            try:
                from core.market_intelligence.order_block_detector import detect_order_blocks
                ob_snap = detect_order_blocks(
                    ctx.candles, price, closed_i, atr_val, ctx.symbol)
            except Exception:
                pass

    # Build V3Opportunity
    opp = build_v3_opportunity(
        symbol=ctx.symbol,
        timestamp_utc=ctx.bar_time,
        correlation_id=correlation_id or entity_id,
        price=price,
        bid=ctx.bid,
        ask=ctx.ask,
        atr=atr_val,
        session=session,
        market_context=context_source,
        candles=ctx.candles,
        closed_index=getattr(ctx, "closed_i", -1),
        liquidity_snapshot=liquidity_snap,
        fvg_snapshot=fvg_snap,
        ob_snapshot=ob_snap,
    )

    # Persist
    persist_v3_opportunity(opp)
