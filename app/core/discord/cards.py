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

    Used for: TRADE_DECISION, DECISION_REJECTED, OPPORTUNITY_LIFECYCLE

    Answers: "What opportunities happened?"
    Shows the opportunity progression with reasoning.

    Supports lifecycle states:
        DETECTED → ASSESSED → REJECTED / EXECUTED
    """
    from datetime import datetime, timezone

    symbol = data.get("symbol") or ""
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lifecycle_state = data.get("lifecycle_state") or data.get("state") or ""

    return {
        "type": "opportunity",
        "symbol": symbol,
        "event_type": event_type,
        "fields": {
            # Identity
            "opportunity_id": data.get("opportunity_id") or "",
            "entity_id": data.get("entity_id") or "",
            "observation_id": data.get("observation_id") or "",
            "cycle_id": data.get("cycle_id") or "",
            # Lifecycle
            "lifecycle_state": lifecycle_state,
            "decision": data.get("decision") or data.get("action") or "",
            # Detection context
            "pattern": data.get("pattern") or "",
            "pattern_confidence": data.get("pattern_confidence") or 0.0,
            "direction": data.get("side") or data.get("direction") or "",
            "session": data.get("session_at_detection") or data.get("session") or "",
            # Market context at detection
            "h4_regime": data.get("h4_regime") or "",
            "h1_direction": data.get("h1_direction") or "",
            # V10 pipeline results (enrichment from decision_trace)
            "strategy": data.get("strategy") or data.get("strategy_classification") or "",
            "strategy_confidence": data.get("strategy_confidence") or 0.0,
            "score": data.get("score") or data.get("overall_score") or 0.0,
            "entry_status": data.get("entry_status") or "",
            "entry_price": data.get("entry_price") or 0.0,
            "stop_price": data.get("stop_price") or 0.0,
            "target_price": data.get("target_price") or 0.0,
            "expected_rr": data.get("expected_rr") or 0.0,
            "risk_approved": data.get("risk_approved"),
            "position_size": data.get("position_size") or 0.0,
            # Rejection/execution
            "reason": data.get("reason") or data.get("rejection_reason") or "",
            "stage": data.get("stage") or data.get("rejection_stage") or data.get("terminal_stage") or "",
            "correlation_id": data.get("correlation_id") or "",
            "outcome_trade_id": data.get("outcome_trade_id") or "",
            # Timestamp
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
    """Build Discord embed for live market dashboard — current state + timeline."""
    status = "LIVE" if f.get("regime") else "WAITING"
    title = f"{symbol} — {status}"

    embed_fields = []

    # ─── SECTION 1: CURRENT STATE ─────────────────────────────────
    # Market
    market_lines = []
    if f.get("regime"):
        market_lines.append(f"Regime: **{f['regime']}**")
    if f.get("h4_trend"):
        strength = f.get("h4_trend_strength")
        val = f["h4_trend"] + (f" ({strength:.2f})" if strength else "")
        market_lines.append(f"H4: {val}")
    if f.get("h1_bos_direction"):
        clarity = f.get("h1_structural_clarity")
        val = f["h1_bos_direction"] + (f" (clarity {clarity:.2f})" if clarity else "")
        market_lines.append(f"H1: {val}")
    if f.get("location_type"):
        pos = f.get("range_position")
        val = f["location_type"] + (f" ({pos:.2f})" if pos else "")
        market_lines.append(f"Location: {val}")
    if f.get("m5_momentum"):
        market_lines.append(f"M5: {f['m5_momentum']}")
    if f.get("volatility_state"):
        market_lines.append(f"Volatility: {f['volatility_state']}")

    if market_lines:
        embed_fields.append({"name": "Market", "value": "\n".join(market_lines), "inline": False})

    # ─── SECTION 2: ACTIVE OPPORTUNITIES ─────────────────────────
    _active_opps = f.get("_active_opportunities", [])
    _terminal_opps = f.get("_terminal_opportunities", [])

    if _active_opps:
        active_text_parts = []
        for idx, opp in enumerate(_active_opps, 1):
            lines = []
            pattern = opp.get("pattern", "?")
            state = opp.get("lifecycle_state", "?")
            lines.append(f"**{idx}. {pattern}**")
            lines.append(f"State: {state}")
            if opp.get("direction"):
                lines.append(f"Direction: {opp['direction']}")
            if opp.get("strategy"):
                conf = opp.get("strategy_confidence")
                val = opp["strategy"] + (f" ({float(conf):.2f})" if conf else "")
                lines.append(f"Strategy: {val}")
            if opp.get("overall_score"):
                lines.append(f"Score: {float(opp['overall_score']):.2f}")
            # Per-opportunity timeline
            opp_timeline = opp.get("_timeline", [])
            if opp_timeline:
                lines.append("")
                for e in opp_timeline[-6:]:
                    lines.append(f"`{e['time']}` {e['text']}")
            active_text_parts.append("\n".join(lines))
        embed_fields.append({
            "name": "Active Opportunities",
            "value": "\n\n".join(active_text_parts)[:1024],
            "inline": False,
        })
    else:
        # Fallback to live_market_state snapshot fields if no tracked opps
        opp_lines = []
        if f.get("opportunity_state"):
            opp_lines.append(f"State: **{f['opportunity_state']}**")
        if f.get("opportunity_type"):
            opp_lines.append(f"Type: {f['opportunity_type']}")
        if f.get("opportunity_quality"):
            opp_lines.append(f"Quality: {f['opportunity_quality']:.2f}")
        if opp_lines:
            embed_fields.append({"name": "Opportunity", "value": "\n".join(opp_lines), "inline": True})

    # ─── SECTION 3: RECENT (terminal) OPPORTUNITIES ───────────────
    if _terminal_opps:
        recent_lines = []
        for opp in _terminal_opps[-3:]:
            pattern = opp.get("pattern", "?")
            state = opp.get("lifecycle_state", "?")
            reason = opp.get("rejection_reason", "")
            trade_id = opp.get("outcome_trade_id", "")
            line = f"**{pattern}** — {state}"
            if reason:
                line += f"\n  {reason[:50]}"
            elif trade_id:
                line += f"\n  Trade: `{trade_id}`"
            recent_lines.append(line)
        embed_fields.append({
            "name": "Recent",
            "value": "\n\n".join(recent_lines)[:1024],
            "inline": False,
        })

    # Strategy
    strat_lines = []
    if f.get("strategy") and f["strategy"] != "NONE":
        strat_lines.append(f"Family: **{f['strategy']}**")
        if f.get("strategy_confidence"):
            strat_lines.append(f"Confidence: {f['strategy_confidence']:.2f}")

    if strat_lines:
        embed_fields.append({"name": "Strategy", "value": "\n".join(strat_lines), "inline": True})

    # Entry + Risk (compact)
    entry_risk_lines = []
    if f.get("entry_status") and f["entry_status"] != "INVALID":
        entry_risk_lines.append(f"Entry: **{f['entry_status']}**")
    if f.get("expected_rr"):
        entry_risk_lines.append(f"R:R: {f['expected_rr']:.2f}")
    if f.get("risk_approved") is not None:
        entry_risk_lines.append(f"Risk: **{'Approved' if f['risk_approved'] else 'Rejected'}**")

    if entry_risk_lines:
        embed_fields.append({"name": "Entry / Risk", "value": "\n".join(entry_risk_lines), "inline": True})

    # ─── SECTION 4: MARKET TIMELINE (symbol-level state changes) ─
    timeline = f.get("_timeline", [])
    if timeline:
        timeline_text = "\n".join(f"`{e['time']}` {e['text']}" for e in timeline[-8:])
        embed_fields.append({"name": "Market Timeline", "value": timeline_text, "inline": False})

    # ─── IDENTITY (most recent active opportunity) ────────────────
    identity = f.get("_identity", {})
    if identity.get("opportunity_id") or identity.get("observation_id"):
        id_lines = []
        _opp_id_raw = identity.get("opportunity_id", "")
        _ent_id_raw = identity.get("entity_id", "")
        _obs_id_raw = identity.get("observation_id", "")
        # Strip symbol prefix for readability
        _opp_display = _opp_id_raw.split("_", 1)[1] if "_" in _opp_id_raw else _opp_id_raw
        _ent_display = _ent_id_raw.split("_", 1)[1] if "_" in _ent_id_raw else _ent_id_raw
        if _opp_display:
            id_lines.append(f"Opp: `{_opp_display}`")
        if _ent_display:
            id_lines.append(f"Ent: `{_ent_display}`")
        if _obs_id_raw:
            id_lines.append(f"Obs: `{_obs_id_raw}`")
        if id_lines:
            embed_fields.append({"name": "\U0001f194 Identity", "value": "\n".join(id_lines), "inline": False})

    # Footer
    footer_parts = []
    if f.get("session"):
        footer_parts.append(f["session"])
    footer_parts.append(f"Updated {f.get('updated_at', '?')}")

    return {
        "title": title,
        "color": color,
        "fields": embed_fields,
        "footer": {"text": " | ".join(footer_parts)},
    }


def _opportunity_embed(symbol: str, event_type: str, f: dict[str, Any], color: int) -> dict[str, Any]:
    """Build Discord embed for opportunity lifecycle event."""
    lifecycle = f.get("lifecycle_state") or f.get("decision") or event_type
    strategy = f.get("strategy") or ""
    direction = f.get("direction") or ""

    # Title: SYMBOL — STRATEGY — DIRECTION (or lifecycle state)
    title_parts = [symbol]
    if strategy:
        title_parts.append(strategy)
    if direction:
        title_parts.append(direction)
    if not strategy and not direction:
        title_parts.append(lifecycle)
    title = " — ".join(title_parts)

    # State icon
    _STATE_ICONS = {
        "DETECTED": "\U0001f7e2",    # Green circle
        "ASSESSED": "\U0001f4ca",    # Chart
        "REJECTED": "\u26a0\ufe0f",  # Warning
        "EXECUTED": "\U0001f680",    # Rocket
    }
    icon = _STATE_ICONS.get(lifecycle, "\U0001f4cb")

    embed_fields = []

    # ─── Identity block (always present, normalized across all symbols) ───
    _opp_id = f.get("opportunity_id") or ""
    _ent_id = f.get("entity_id") or ""
    _obs_id = f.get("observation_id") or ""

    # Strip leading symbol prefix (e.g. "XAUUSD_1753574400_BEARISH_ENGULFING" → "1753574400_BEARISH_ENGULFING")
    _opp_display = _opp_id.split("_", 1)[1] if "_" in _opp_id else _opp_id
    _ent_display = _ent_id.split("_", 1)[1] if "_" in _ent_id else _ent_id
    _obs_display = _obs_id  # Full value — already a compact hash

    identity_lines = []
    if _opp_display:
        identity_lines.append(f"Opp: `{_opp_display}`")
    if _ent_display:
        identity_lines.append(f"Ent: `{_ent_display}`")
    if _obs_display:
        identity_lines.append(f"Obs: `{_obs_display}`")
    if identity_lines:
        embed_fields.append({"name": "\U0001f194 Identity", "value": "\n".join(identity_lines), "inline": False})
    if f.get("pattern"):
        conf = f.get("pattern_confidence")
        val = f["pattern"] + (f" ({conf:.0%})" if conf else "")
        embed_fields.append({"name": "Pattern", "value": val, "inline": True})

    # Strategy
    if strategy:
        conf = f.get("strategy_confidence")
        val = strategy + (f" ({conf:.2f})" if conf else "")
        embed_fields.append({"name": "Strategy", "value": val, "inline": True})

    # Direction
    if direction:
        embed_fields.append({"name": "Direction", "value": direction, "inline": True})

    # Entry geometry (if available)
    if f.get("entry_price"):
        embed_fields.append({"name": "Entry", "value": str(f["entry_price"]), "inline": True})
    if f.get("stop_price"):
        embed_fields.append({"name": "Stop", "value": str(f["stop_price"]), "inline": True})
    if f.get("target_price"):
        embed_fields.append({"name": "Target", "value": str(f["target_price"]), "inline": True})
    if f.get("expected_rr"):
        embed_fields.append({"name": "R:R", "value": f"{f['expected_rr']:.2f}", "inline": True})

    # Risk
    if f.get("risk_approved") is not None:
        val = f"Approved ({f.get('position_size', '?')} lots)" if f["risk_approved"] else "Rejected"
        embed_fields.append({"name": "Risk", "value": val, "inline": True})

    # Market context
    if f.get("h4_regime") or f.get("h1_direction"):
        ctx = []
        if f.get("h4_regime"):
            ctx.append(f"H4: {f['h4_regime']}")
        if f.get("h1_direction"):
            ctx.append(f"H1: {f['h1_direction']}")
        embed_fields.append({"name": "Market", "value": " | ".join(ctx), "inline": False})

    # Rejection info
    if f.get("reason") and lifecycle in ("REJECTED", ""):
        embed_fields.append({"name": "Reason", "value": str(f["reason"])[:100], "inline": False})
    if f.get("stage") and lifecycle == "REJECTED":
        embed_fields.append({"name": "Stage", "value": str(f["stage"]), "inline": True})

    # Execution link
    if f.get("correlation_id"):
        embed_fields.append({"name": "Correlation", "value": f"`{f['correlation_id']}`", "inline": False})
    if f.get("outcome_trade_id"):
        embed_fields.append({"name": "Trade", "value": f"`{f['outcome_trade_id']}`", "inline": True})

    # Color by state
    _STATE_COLORS = {
        "DETECTED": 0x3498DB,   # Blue
        "ASSESSED": 0xF39C12,   # Orange
        "REJECTED": 0xE74C3C,   # Red
        "EXECUTED": 0x2ECC71,   # Green
    }
    embed_color = _STATE_COLORS.get(lifecycle, color)

    return {
        "title": f"{icon} {title}",
        "color": embed_color,
        "fields": embed_fields,
        "footer": {"text": f"{lifecycle} | {f.get('session', '')} | {f.get('timestamp', '')} | {_opp_display or '?'}"},
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
