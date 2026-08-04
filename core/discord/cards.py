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

    Answers: "What opportunities happened?"
    Shows the opportunity progression with reasoning.
    """
    from datetime import datetime, timezone

    symbol = data.get("symbol") or ""
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    return {
        "type": "opportunity",
        "symbol": symbol,
        "event_type": event_type,
        "fields": {
            "decision": data.get("decision") or data.get("action") or "",
            "pattern": data.get("pattern") or "",
            "strategy": data.get("strategy") or "",
            "strategy_confidence": data.get("strategy_confidence") or 0.0,
            "direction": data.get("side") or data.get("direction") or "",
            "score": data.get("score") or 0.0,
            "reason": data.get("reason") or "",
            "stage": data.get("stage") or data.get("terminal_stage") or "",
            "thesis": data.get("thesis") or "",
            "opportunity_state": data.get("opportunity_state") or "",
            "opportunity_type": data.get("opportunity_type") or "",
            "supporting": data.get("supporting") or [],
            "contradicting": data.get("contradicting") or [],
            "timestamp": now_utc,
        },
    }


def build_execution_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a trade execution lifecycle card.

    Used for: ORDER_ATTEMPT, ORDER_FILLED, ORDER_MODIFIED, TRADE_CLOSED,
              TRADE_RESULT, RISK_BLOCK, RISK_CHECK

    Answers: "What trades happened?"
    Shows the full execution lifecycle: open → fill → modify → close.
    """
    from datetime import datetime, timezone

    symbol = data.get("symbol") or ""
    details = data.get("details") or {}
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    return {
        "type": "execution",
        "symbol": symbol,
        "event_type": event_type,
        "fields": {
            "side": data.get("side") or "",
            "volume": data.get("volume") or 0.0,
            "entry_price": data.get("entry_reference") or data.get("entry_price") or data.get("fill_price") or 0.0,
            "exit_price": data.get("exit_price") or 0.0,
            "fill_price": data.get("fill_price") or 0.0,
            "sl": data.get("sl") or 0.0,
            "tp": data.get("tp") or 0.0,
            "deal": data.get("deal") or data.get("ticket") or "",
            "slippage": data.get("slippage") or 0.0,
            "pnl": data.get("pnl") or details.get("pnl") or 0.0,
            "r_multiple": data.get("r_multiple") or details.get("pnl_r") or 0.0,
            "exit_reason": data.get("exit_reason") or details.get("close_type") or data.get("reason") or "",
            "guard": data.get("guard") or "",
            "pattern": data.get("pattern") or "",
            "duration_min": details.get("duration_min") or 0,
            "timestamp": now_utc,
        },
    }


def build_system_card(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a system status card.

    Used for: SYSTEM_STARTUP, SYSTEM_SHUTDOWN, ERROR, KILL_SWITCH,
              HEARTBEAT, PNL_UPDATE, DRAWDOWN_UPDATE, DAILY_REPORT

    Answers: "Is the machine alive?"
    Shows infrastructure health and critical events.
    """
    from datetime import datetime, timezone

    symbol = data.get("symbol") or ""
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    return {
        "type": "system",
        "symbol": symbol,
        "event_type": event_type,
        "fields": {
            "status": data.get("status") or event_type,
            "mode": data.get("mode") or "",
            "reason": data.get("reason") or "",
            "error_type": data.get("error_type") or "",
            "message": (data.get("message") or "")[:200],
            "location": data.get("location") or "",
            "component": data.get("component") or data.get("guard") or "",
            "cycle": data.get("cycle") or data.get("cycles") or "",
            "latency_ms": data.get("latency_ms") or 0,
            "symbols": data.get("symbols") or data.get("symbol_count") or "",
            "drawdown_pct": data.get("drawdown_pct") or 0.0,
            "daily_loss_pct": data.get("daily_loss_pct") or 0.0,
            "uptime": data.get("uptime_human") or "",
            "timestamp": now_utc,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EMBED CONVERSION (Phase 4 — Discord API delivery)
# ═══════════════════════════════════════════════════════════════════════════════

_EMBED_COLORS = {
    "market": 0x3498DB,      # Blue
    "opportunity": 0xF39C12,  # Orange
    "execution": 0x2ECC71,   # Green
    "system": 0x95A5A6,      # Grey
}


def card_to_embed(card: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a structured card dict into a Discord embed payload.

    Input: card from build_market_card/build_opportunity_card/etc.
    Output: Discord embed dict ready for API submission.

    Discord embed format:
        {
            "title": "...",
            "description": "...",
            "color": int,
            "fields": [{"name": "...", "value": "...", "inline": bool}],
            "footer": {"text": "..."},
            "timestamp": "ISO8601"
        }
    """
    card_type = card.get("type", "unknown")
    symbol = card.get("symbol") or ""
    event_type = card.get("event_type", "")
    fields_data = card.get("fields", {})

    color = _EMBED_COLORS.get(card_type, 0x7F8C8D)

    if card_type == "market":
        return _market_embed(symbol, fields_data, color)
    elif card_type == "opportunity":
        return _opportunity_embed(symbol, event_type, fields_data, color)
    elif card_type == "execution":
        return _execution_embed(symbol, event_type, fields_data, color)
    elif card_type == "system":
        return _system_embed(event_type, fields_data, color)

    # Fallback
    return {
        "title": f"{card_type.upper()} | {symbol or event_type}",
        "description": str(fields_data)[:200],
        "color": color,
    }


def _market_embed(symbol: str, f: dict[str, Any], color: int) -> dict[str, Any]:
    """Build Discord embed for live market card."""
    status = "LIVE" if f.get("regime") else "WAITING"
    title = f"{symbol} — {status}"

    embed_fields = []
    if f.get("regime"):
        embed_fields.append({"name": "Regime", "value": str(f["regime"]), "inline": True})
    if f.get("h4_trend"):
        embed_fields.append({"name": "H4 Trend", "value": str(f["h4_trend"]), "inline": True})
    if f.get("h1_bos_direction"):
        embed_fields.append({"name": "H1 BOS", "value": str(f["h1_bos_direction"]), "inline": True})
    if f.get("location_type"):
        embed_fields.append({"name": "Location", "value": str(f["location_type"]), "inline": True})
    if f.get("m5_momentum"):
        embed_fields.append({"name": "M5", "value": str(f["m5_momentum"]), "inline": True})
    if f.get("volatility_state"):
        embed_fields.append({"name": "Volatility", "value": str(f["volatility_state"]), "inline": True})
    if f.get("strategy"):
        embed_fields.append({"name": "Strategy", "value": str(f["strategy"]), "inline": True})
    if f.get("entry_status"):
        embed_fields.append({"name": "Entry", "value": str(f["entry_status"]), "inline": True})
    if f.get("opportunity_state"):
        embed_fields.append({"name": "Opportunity", "value": str(f["opportunity_state"]), "inline": True})

    return {
        "title": title,
        "color": color,
        "fields": embed_fields,
        "footer": {"text": f"Updated {f.get('updated_at', '')}"},
    }


def _opportunity_embed(symbol: str, event_type: str, f: dict[str, Any], color: int) -> dict[str, Any]:
    """Build Discord embed for opportunity event."""
    decision = f.get("decision") or event_type
    title = f"{symbol} — {decision}"

    embed_fields = []
    if f.get("strategy"):
        embed_fields.append({"name": "Strategy", "value": str(f["strategy"]), "inline": True})
    if f.get("direction"):
        embed_fields.append({"name": "Direction", "value": str(f["direction"]), "inline": True})
    if f.get("score"):
        embed_fields.append({"name": "Score", "value": f"{f['score']:.2f}", "inline": True})
    if f.get("stage"):
        embed_fields.append({"name": "Stage", "value": str(f["stage"]), "inline": True})
    if f.get("reason"):
        embed_fields.append({"name": "Reason", "value": str(f["reason"])[:100], "inline": False})

    return {
        "title": title,
        "color": color,
        "fields": embed_fields,
        "footer": {"text": f.get("timestamp", "")},
    }


def _execution_embed(symbol: str, event_type: str, f: dict[str, Any], color: int) -> dict[str, Any]:
    """Build Discord embed for execution event."""
    action = event_type.replace("ORDER_", "").replace("TRADE_", "")
    title = f"{symbol} — {action}"

    embed_fields = []
    if f.get("side"):
        embed_fields.append({"name": "Side", "value": str(f["side"]), "inline": True})
    if f.get("volume"):
        embed_fields.append({"name": "Volume", "value": str(f["volume"]), "inline": True})
    if f.get("fill_price"):
        embed_fields.append({"name": "Fill", "value": str(f["fill_price"]), "inline": True})
    if f.get("sl"):
        embed_fields.append({"name": "SL", "value": str(f["sl"]), "inline": True})
    if f.get("tp"):
        embed_fields.append({"name": "TP", "value": str(f["tp"]), "inline": True})
    if f.get("r_multiple"):
        embed_fields.append({"name": "R", "value": f"{f['r_multiple']:.2f}R", "inline": True})
    if f.get("pnl"):
        embed_fields.append({"name": "P&L", "value": str(f["pnl"]), "inline": True})
    if f.get("exit_reason"):
        embed_fields.append({"name": "Exit", "value": str(f["exit_reason"]), "inline": True})
    if f.get("guard"):
        embed_fields.append({"name": "Guard", "value": str(f["guard"]), "inline": True})

    return {
        "title": title,
        "color": 0xE74C3C if f.get("guard") else color,  # Red for blocks
        "fields": embed_fields,
        "footer": {"text": f.get("timestamp", "")},
    }


def _system_embed(event_type: str, f: dict[str, Any], color: int) -> dict[str, Any]:
    """Build Discord embed for system event."""
    title = event_type.replace("_", " ").title()

    embed_fields = []
    if f.get("status"):
        embed_fields.append({"name": "Status", "value": str(f["status"]), "inline": True})
    if f.get("mode"):
        embed_fields.append({"name": "Mode", "value": str(f["mode"]), "inline": True})
    if f.get("uptime"):
        embed_fields.append({"name": "Uptime", "value": str(f["uptime"]), "inline": True})
    if f.get("cycle"):
        embed_fields.append({"name": "Cycle", "value": str(f["cycle"]), "inline": True})
    if f.get("latency_ms"):
        embed_fields.append({"name": "Latency", "value": f"{f['latency_ms']}ms", "inline": True})
    if f.get("error_type"):
        embed_fields.append({"name": "Error", "value": str(f["error_type"]), "inline": True})
    if f.get("message"):
        embed_fields.append({"name": "Detail", "value": str(f["message"])[:200], "inline": False})
    if f.get("reason"):
        embed_fields.append({"name": "Reason", "value": str(f["reason"])[:100], "inline": False})

    # Color override for critical events
    if event_type in ("ERROR", "KILL_SWITCH"):
        color = 0xE74C3C  # Red

    return {
        "title": title,
        "color": color,
        "fields": embed_fields,
        "footer": {"text": f.get("timestamp", "")},
    }
