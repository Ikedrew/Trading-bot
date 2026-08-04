"""
Discord V2 Card Builders — Structured message formatters.

Each builder returns a plain dictionary describing what should be rendered.
No Discord API calls. No webhook calls. No side effects.

These dictionaries will be consumed by future delivery modules
(bot embeds, webhook formatters, etc.) in later phases.

Card structure:
    {
        "type": str,          # Card type identifier
        "symbol": str | None, # Trading symbol (if applicable)
        "event_type": str,    # Source event
        "fields": dict,       # Structured data for rendering
    }
"""

from __future__ import annotations

from typing import Any


def build_market_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a live market status card from a market-related event.

    Used for: MARKET_CONTEXT, H4_CONTEXT, HTF_CONTEXT_BUNDLE, MARKET_SNAPSHOT

    Contains all fields needed for an editable live market card:
        Symbol, Status, Regime, H4 Trend, H1 BOS, Location,
        M5 Momentum, Opportunity state, Strategy, Entry status, Timestamp.
    """
    from datetime import datetime, timezone

    symbol = data.get("symbol") or ""
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    return {
        "type": "market",
        "symbol": symbol,
        "event_type": event_type,
        "fields": {
            # Market structure
            "regime": data.get("regime") or data.get("h4_regime") or "",
            "h4_trend": data.get("h4_trend") or data.get("h4_bias") or "",
            "h4_trend_strength": data.get("h4_strength") or data.get("h4_trend_strength") or 0.0,
            "h1_bos_direction": data.get("h1_bos") or data.get("h1_direction") or data.get("h1_bias") or "",
            "h1_structural_clarity": data.get("h1_clarity") or data.get("h1_structural_clarity") or 0.0,
            # Location
            "location_type": data.get("location_type") or "",
            "range_position": data.get("range_position") or 0.0,
            # Momentum
            "m5_momentum": data.get("m5_momentum") or data.get("m5_bias") or "",
            "volatility_state": data.get("volatility_state") or "",
            # Opportunity context (if available in event payload)
            "opportunity_state": data.get("opportunity_state") or data.get("opp_state") or "",
            "opportunity_type": data.get("opportunity_type") or "",
            "opportunity_quality": data.get("opportunity_quality") or data.get("quality") or 0.0,
            # Strategy context
            "strategy": data.get("strategy") or data.get("strategy_family") or "",
            "strategy_confidence": data.get("strategy_confidence") or 0.0,
            # Entry context
            "entry_status": data.get("entry_status") or "",
            # Session
            "session": data.get("session") or "",
            # Timestamp
            "updated_at": now_utc,
        },
    }


def build_opportunity_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build an opportunity lifecycle card.

    Used for: TRADE_DECISION, DECISION_REJECTED, PIPELINE_DROP

    Future: This becomes a Discord embed showing opportunity progression.
    """
    return {
        "type": "opportunity",
        "symbol": data.get("symbol"),
        "event_type": event_type,
        "fields": {
            "decision": data.get("decision") or data.get("action"),
            "pattern": data.get("pattern"),
            "strategy": data.get("strategy"),
            "score": data.get("score"),
            "reason": data.get("reason"),
            "stage": data.get("stage"),
            "side": data.get("side"),
            "thesis": data.get("thesis"),
        },
    }


def build_execution_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a trade execution lifecycle card.

    Used for: ORDER_ATTEMPT, ORDER_FILLED, ORDER_MODIFIED, TRADE_CLOSED,
              TRADE_RESULT, RISK_BLOCK, RISK_CHECK

    Future: This becomes a Discord embed showing trade progression.
    """
    return {
        "type": "execution",
        "symbol": data.get("symbol"),
        "event_type": event_type,
        "fields": {
            "side": data.get("side"),
            "volume": data.get("volume"),
            "fill_price": data.get("fill_price"),
            "sl": data.get("sl"),
            "tp": data.get("tp"),
            "deal": data.get("deal"),
            "ticket": data.get("ticket"),
            "slippage": data.get("slippage"),
            "pnl": data.get("pnl") or data.get("pnl_r"),
            "r_multiple": data.get("r_multiple"),
            "reason": data.get("reason"),
            "guard": data.get("guard"),
            "close_type": (data.get("details") or {}).get("close_type"),
        },
    }


def build_system_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a system status card.

    Used for: SYSTEM_STARTUP, SYSTEM_SHUTDOWN, ERROR, KILL_SWITCH,
              HEARTBEAT, PNL_UPDATE, DRAWDOWN_UPDATE, DAILY_REPORT

    Future: This becomes a Discord embed for system health.
    """
    return {
        "type": "system",
        "symbol": data.get("symbol"),
        "event_type": event_type,
        "fields": {
            "mode": data.get("mode"),
            "reason": data.get("reason"),
            "error_type": data.get("error_type"),
            "message": data.get("message"),
            "location": data.get("location"),
            "cycle": data.get("cycle"),
            "latency_ms": data.get("latency_ms"),
            "symbols": data.get("symbols") or data.get("symbol_count"),
            "drawdown_pct": data.get("drawdown_pct"),
            "daily_loss_pct": data.get("daily_loss_pct"),
        },
    }
