"""
Discord Structured Logger — Lightweight observability layer.

Routes structured events to Discord webhooks via channel mapping.
Synchronous HTTP. Fails silently. Never crashes the bot.

EVENT CONTRACT (frozen):
    All events are emitted via StructuredLogger.event(event_type, data)
    - event_type: str (must be in CHANNEL_MAP)
    - data: dict | None (event-specific payload, flat structure preferred)

    The formatter (_format_event_message) renders data→str for Discord.
    Priority system (_should_send) gates delivery based on mode.

    Canonical output shape (for downstream consumers / future AWS):
    {
        "event_type": str,
        "timestamp": ISO 8601,
        "data": { ...event-specific fields... }
    }

    This contract is STABLE. Do not add nested "details" patterns.
    New events should use flat data dicts.
"""

from __future__ import annotations

import json
from typing import Any


# ─── CHANNEL MAP ──────────────────────────────────────────────────────────────
# Maps event types → Discord channel names (which map to webhook URLs)

CHANNEL_MAP: dict[str, str] = {
    # CORE
    "SYSTEM_STARTUP": "system-status",
    "SYSTEM_SHUTDOWN": "system-status",
    "KILL_SWITCH": "system-status",
    "BOT_STATE": "system-status",

    "HEARTBEAT": "heartbeat",

    "ERROR": "errors",

    # AUDIT
    "TRADE_DECISION": "decision-log",
    "DECISION_REJECTED": "decision-log",
    "PIPELINE_DROP": "decision-log",

    "ORDER_ATTEMPT": "trade-execution",
    "ORDER_FILLED": "trade-execution",
    "ORDER_MODIFIED": "trade-execution",
    "TRADE_CLOSED": "trade-execution",

    "RISK_CHECK": "risk-log",
    "RISK_BLOCK": "risk-log",
    "EXPOSURE_UPDATE": "risk-log",

    "MARKET_CONTEXT": "market-context",
    "H4_CONTEXT": "market-context",
    "HTF_CONTEXT_BUNDLE": "market-context",
    "MARKET_SNAPSHOT": "market-context",

    # PERFORMANCE
    "DAILY_REPORT": "performance-summary",
    "TRADE_RESULT": "performance-summary",
    "PNL_UPDATE": "pnl-drawdown",
    "DRAWDOWN_UPDATE": "pnl-drawdown",

    # RESEARCH
    "RESEARCH_MONITOR": "research_monitor-shadow-research",
}


# ─── EVENT PRIORITY SYSTEM ─────────────────────────────────────────────────────
# Priority 0 = CRITICAL (always sent), 1 = HIGH, 2 = MEDIUM, 3 = LOW (suppressible)

EVENT_PRIORITY: dict[str, int] = {
    # 🚨 CRITICAL (always visible)
    "SYSTEM_STARTUP": 0,
    "SYSTEM_SHUTDOWN": 0,
    "ORDER_ATTEMPT": 0,
    "ORDER_FILLED": 0,
    "TRADE_CLOSED": 0,
    "TRADE_DECISION": 0,
    "ERROR": 0,
    "KILL_SWITCH": 0,

    # 📊 HIGH (decision context)
    "PIPELINE_DROP": 1,
    "RISK_BLOCK": 1,

    # 🧭 MEDIUM (HTF / structure context)
    "H4_CONTEXT": 2,
    "HTF_CONTEXT_BUNDLE": 2,
    "MARKET_SNAPSHOT": 2,
    "MARKET_CONTEXT": 2,

    # 🟢 LOW (background / diagnostics)
    "HEARTBEAT": 3,
    "PNL_UPDATE": 3,
    "DRAWDOWN_UPDATE": 3,
    "DAILY_REPORT": 3,
    "TRADE_RESULT": 3,
}

# Runtime mode: "ALL" sends everything, "LIVE" suppresses priority >= 3
_discord_output_mode: str = "ALL"


def set_discord_output_mode(mode: str) -> None:
    """Set Discord output filtering: 'ALL' (everything) or 'LIVE' (suppress low priority)."""
    global _discord_output_mode
    _discord_output_mode = mode


def _should_send(event_type: str) -> bool:
    """Check if event should be sent based on priority and current mode."""
    priority = EVENT_PRIORITY.get(event_type, 2)  # Default: MEDIUM
    if priority == 0:
        return True  # Critical always sends
    if _discord_output_mode == "LIVE" and priority >= 3:
        return False  # Suppress low-priority in live mode
    return True


# ─── STRUCTURED LOGGER ────────────────────────────────────────────────────────

class StructuredLogger:
    """
    Routes structured events to Discord channels via CHANNEL_MAP.

    Usage:
        logger = StructuredLogger(discord_client)
        logger.event("SYSTEM_STARTUP", {"mode": "live", "symbols": 3})
    """

    def __init__(self, discord_client: Any = None) -> None:
        self._discord = discord_client  # Legacy — unused, kept for backward compat

    def event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """
        Emit a structured event to persistence (S3) and optionally Discord.

        Flow:
            1. ALWAYS persist to S3 (system of record)
            2. THEN optionally send to Discord (if mapped + priority allows)
            3. THEN duplicate to pair-{symbol} channel (if symbol present + event qualifies)

        Args:
            event_type: Event name (persisted regardless of CHANNEL_MAP membership).
            data: Optional payload dict with event-specific data.
        """
        # 1. ALWAYS persist first (source of truth — never gated by channel mapping)
        try:
            from core.aws_uploader import upload_event
            upload_event({"event_type": event_type, **(data or {})})
        except Exception:
            pass  # S3 failure must never affect pipeline

        # 2. THEN optionally send Discord (notification layer — best effort)
        channel = CHANNEL_MAP.get(event_type)
        if channel and _should_send(event_type):
            try:
                from core.discord_notifier import send_discord
                _msg = _format_event_message(event_type, data)
                send_discord(channel, _msg)

                # 3. Duplicate to pair-{symbol} channel for per-symbol visibility
                _duplicate_to_pair_channel(event_type, data, _msg)
            except Exception:
                pass  # Discord failure must never affect pipeline

        # 4. Discord V2 renderer (parallel path — gated by feature flag)
        try:
            from core import config as _v2_cfg
            if getattr(_v2_cfg, "ENABLE_DISCORD_V2", False):
                from core.discord.renderer import DiscordRenderer
                _v2_renderer = DiscordRenderer()
                _v2_renderer.render(event_type, data)
        except Exception:
            pass  # V2 renderer failure must never affect pipeline


# ─── PAIR-CHANNEL DUPLICATION ─────────────────────────────────────────────────
# Events that should also appear in pair-{symbol} channels for per-symbol visibility.
# Central channel delivery is preserved — this is additive only.

_PAIR_CHANNEL_EVENTS: set[str] = {
    "ORDER_ATTEMPT",
    "ORDER_FILLED",
    "ORDER_MODIFIED",
    "TRADE_CLOSED",
    "RISK_BLOCK",
}


def _duplicate_to_pair_channel(event_type: str, data: dict[str, Any] | None, formatted_msg: str) -> None:
    """
    Send a copy of the formatted message to the pair-{symbol} channel.

    Only fires for events in _PAIR_CHANNEL_EVENTS that contain a 'symbol' key.
    Silent on failure. Never affects execution.
    """
    if event_type not in _PAIR_CHANNEL_EVENTS:
        return
    if not data:
        return
    symbol = data.get("symbol")
    if not symbol:
        return
    try:
        from core.discord_notifier import send_discord
        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"
        send_discord(channel, formatted_msg)
    except Exception:
        pass  # Pair-channel failure must never affect execution


def _format_event_message(event_type: str, data: dict[str, Any] | None) -> str:
    """Format a structured event into a human-readable Discord message."""
    d = data or {}

    if event_type == "TRADE_DECISION":
        decision = d.get("decision", "UNKNOWN")
        symbol = d.get("symbol", "?")
        side = d.get("side", "?")
        pattern = d.get("pattern", "")
        score = d.get("score", "")
        thesis = d.get("thesis", "")

        if decision in ("ALLOW", "EXECUTE"):
            msg = f"**TRADE SIGNAL** | {symbol} {side}\n"
            msg += f"Pattern: {pattern} | Score: {score}\n"
            if thesis:
                msg += f"Thesis: {thesis}\n"
            supporting = d.get("supporting", [])
            if supporting:
                msg += f"Supporting: {', '.join(str(s) for s in supporting[:3])}\n"
            contradicting = d.get("contradicting", [])
            if contradicting:
                msg += f"Contradicting: {', '.join(str(s) for s in contradicting[:2])}"
            return msg
        else:
            reason = d.get("reason", "")
            return f"**REJECTED** | {symbol} | {reason} | score={score}"

    if event_type == "DECISION_REJECTED":
        symbol = d.get("symbol", "?")
        reason = d.get("reason", "?")
        stage = d.get("stage", "")
        score = d.get("score", "")
        msg = f"**NO TRADE** | {symbol}"
        if stage:
            msg += f" | stage: {stage}"
        if reason:
            msg += f" | {reason}"
        if score:
            msg += f" | score={score}"
        return msg

    if event_type == "RISK_BLOCK":
        guard = d.get("guard", "?")
        symbol = d.get("symbol", "?")
        reason = d.get("reason", "")
        details = d.get("details", {})
        msg = f"**BLOCKED** | {symbol} | Guard: {guard}\n"
        msg += f"Reason: {reason}"
        if details:
            detail_str = " | ".join(f"{k}={v}" for k, v in list(details.items())[:3])
            msg += f"\n{detail_str}"
        return msg

    if event_type == "RISK_CHECK":
        symbol = d.get("symbol", "?")
        result_val = d.get("result", "?")
        guard = d.get("guard", "?")
        if result_val == "APPROVED":
            return f"Risk check passed | {symbol} | {guard}"
        else:
            reason = d.get("reason", "")
            return f"**RISK REJECTED** | {symbol} | {guard} | {reason}"

    if event_type == "ORDER_ATTEMPT":
        symbol = d.get("symbol", "?")
        side = d.get("side", "?")
        volume = d.get("volume", "?")
        sl = d.get("sl", "?")
        tp = d.get("tp", "?")
        entry_ref = d.get("entry_reference", "")
        msg = f"**SENDING ORDER** | {symbol} {side} {volume} lots"
        if entry_ref:
            msg += f" @ ~{entry_ref}"
        if sl and tp:
            msg += f"\nSL: {sl} | TP: {tp}"
        return msg

    if event_type == "ORDER_FILLED":
        symbol = d.get("symbol", "?")
        side = d.get("side", "?")
        fill_price = d.get("fill_price", "?")
        volume = d.get("volume", "")
        deal = d.get("deal", "")
        slippage = d.get("slippage", "")
        msg = f"**FILLED** | {symbol} {side} @ {fill_price}"
        if volume:
            msg += f" | {volume} lots"
        if deal:
            msg += f" | deal #{deal}"
        if slippage and float(slippage) != 0:
            msg += f" | slip: {slippage}"
        return msg

    if event_type == "ORDER_MODIFIED":
        symbol = d.get("symbol", "?")
        field = d.get("field", "SL/TP")
        old_val = d.get("old", "?")
        new_val = d.get("new", "?")
        reason = d.get("reason", "")
        msg = f"**MODIFIED** | {symbol} | {field}: {old_val} -> {new_val}"
        if reason:
            msg += f" | {reason}"
        return msg

    if event_type == "TRADE_CLOSED":
        symbol = d.get("symbol", "?")
        reason = d.get("reason", "?")
        details = d.get("details", {})
        pnl_r = details.get("pnl_r", details.get("pnl", "?"))
        close_type = details.get("close_type", reason)
        duration = details.get("duration_min", "")
        icon = "+" if str(pnl_r).replace('.','').replace('-','').isdigit() and float(str(pnl_r)) > 0 else "-" if str(pnl_r).replace('.','').replace('-','').isdigit() else ""
        msg = f"**CLOSED {'WIN' if icon == '+' else 'LOSS' if icon == '-' else ''}** | {symbol} | {close_type}\n"
        msg += f"Result: {pnl_r}R"
        if duration:
            msg += f" | Duration: {duration} min"
        return msg

    if event_type == "TRADE_RESULT":
        symbol = d.get("symbol", "?")
        pnl = d.get("pnl", "?")
        r_multiple = d.get("r_multiple", "?")
        pattern = d.get("pattern", "")
        duration = d.get("duration_min", "")
        exit_reason = d.get("exit_reason", "")
        msg = f"**RESULT** | {symbol} | {r_multiple}R | PnL: {pnl}"
        if pattern:
            msg += f" | {pattern}"
        if exit_reason:
            msg += f" | Exit: {exit_reason}"
        if duration:
            msg += f" | {duration} min"
        return msg

    if event_type == "ERROR":
        location = d.get("location", "?")
        error_type = d.get("error_type", "?")
        message = d.get("message", "")[:200]
        symbol = d.get("symbol", "")
        msg = f"**ERROR** | {error_type}"
        if location:
            msg += f" | {location}"
        if symbol:
            msg += f" | {symbol}"
        msg += f"\n{message}"
        return msg

    if event_type == "KILL_SWITCH":
        reason = d.get("reason", "Manual kill switch activated")
        return f"**KILL SWITCH ACTIVATED**\nReason: {reason}\nAll execution halted."

    if event_type == "SYSTEM_STARTUP":
        mode = d.get("mode", "?")
        symbols = d.get("symbols", d.get("symbol_count", "?"))
        strategy = d.get("strategy", "")
        msg = f"**BOT STARTED** | Mode: {mode}"
        if strategy:
            msg += f" | Strategy: {strategy}"
        msg += f" | Symbols: {symbols}"
        return msg

    if event_type == "SYSTEM_SHUTDOWN":
        reason = d.get("reason", "Normal shutdown")
        cycles = d.get("cycles", "")
        msg = f"**BOT STOPPED** | {reason}"
        if cycles:
            msg += f" | Cycles: {cycles}"
        return msg

    if event_type == "HEARTBEAT":
        cycle = d.get("cycle", "?")
        latency = d.get("latency_ms", "?")
        symbols = d.get("symbols", "")
        positions = d.get("positions", "")
        msg = f"Alive | cycle {cycle} | {latency}ms"
        if symbols:
            msg += f" | {symbols} symbols"
        if positions:
            msg += f" | {positions} pos"
        return msg

    if event_type == "DRAWDOWN_UPDATE":
        drawdown = d.get("drawdown_pct", "?")
        daily_loss = d.get("daily_loss_pct", "")
        msg = f"**DRAWDOWN** | {drawdown}%"
        if daily_loss:
            msg += f" | Daily loss: {daily_loss}%"
        return msg

    if event_type == "PNL_UPDATE":
        daily_pnl = d.get("daily_pnl", "?")
        total_pnl = d.get("total_pnl", "")
        trades_today = d.get("trades_today", "")
        msg = f"**P&L** | Today: {daily_pnl}"
        if total_pnl:
            msg += f" | Total: {total_pnl}"
        if trades_today:
            msg += f" | Trades: {trades_today}"
        return msg

    if event_type == "EXPOSURE_UPDATE":
        positions = d.get("positions", "?")
        exposure_pct = d.get("exposure_pct", "?")
        symbols_open = d.get("symbols", [])
        msg = f"**EXPOSURE** | {positions} positions | {exposure_pct}% risk"
        if symbols_open:
            msg += f" | {', '.join(symbols_open)}"
        return msg

    if event_type == "DAILY_REPORT":
        trades = d.get("trades", 0)
        wins = d.get("wins", 0)
        losses = d.get("losses", 0)
        pnl = d.get("pnl", "?")
        win_rate = d.get("win_rate", "")
        msg = f"**DAILY SUMMARY**\n"
        msg += f"Trades: {trades} ({wins}W / {losses}L)"
        if win_rate:
            msg += f" | WR: {win_rate}"
        msg += f"\nP&L: {pnl}"
        return msg

    if event_type == "H4_CONTEXT":
        symbol = d.get("symbol", "?")
        regime = d.get("regime", "?")
        bias = d.get("bias", "?")
        strength = d.get("strength", "?")
        return f"H4 | {symbol} | {regime} | bias={bias} | str={strength}"

    if event_type == "HTF_CONTEXT_BUNDLE":
        symbol = d.get("symbol", "?")
        return (
            f"**HTF** | {symbol}\n"
            f"H1: {d.get('h1_regime', '?')} bias={d.get('h1_bias', '?')}\n"
            f"M15: struct={d.get('m15_structure', '?')} bias={d.get('m15_bias', '?')}\n"
            f"M5: bias={d.get('m5_bias', '?')} reg={d.get('m5_micro_regime', '?')}"
        )

    if event_type == "MARKET_CONTEXT":
        symbol = d.get("symbol", "?")
        regime = d.get("regime", d.get("h4_regime", "?"))
        direction = d.get("direction", d.get("unified_direction", "?"))
        phase = d.get("phase", d.get("market_phase", "?"))
        return f"**CONTEXT** | {symbol} | regime={regime} | dir={direction} | phase={phase}"

    if event_type == "MARKET_SNAPSHOT":
        symbol = d.get("symbol", "?")
        return (
            f"**SNAPSHOT** | {symbol}\n"
            f"H4: {d.get('h4_bias', '?')} | M15: {d.get('m15_bias', '?')} | M5: {d.get('m5_bias', '?')}\n"
            f"Last block: {d.get('last_drop_stage', '?')}"
        )

    if event_type == "PIPELINE_DROP":
        symbol = d.get("symbol", "?")
        stage = d.get("stage", "?")
        reason = d.get("reason", "?")
        return f"Drop | {symbol} | {stage} | {reason}"

    if event_type == "RESEARCH_MONITOR":
        event_subtype = d.get("event", "?")
        symbol = d.get("symbol", "")
        msg = f"Research | {event_subtype}"
        if symbol:
            msg += f" | {symbol}"
        # Add compact detail
        detail_keys = [k for k in d.keys() if k not in ("event", "symbol")]
        if detail_keys:
            msg += " | " + " ".join(f"{k}={d[k]}" for k in detail_keys[:4])
        return msg

    # Default: compact JSON with event type
    return f"**{event_type}** | {json.dumps(d, default=str)[:300]}"


# ─── CANONICAL EVENT ROUTER ───────────────────────────────────────────────────
# Single entry point for all Discord event routing.
# Future: all direct _dl.event() calls should migrate to route_event().

_router_logger: StructuredLogger | None = None


def set_router_logger(logger_instance: StructuredLogger) -> None:
    """Set the global router logger instance. Call once at startup."""
    global _router_logger
    _router_logger = logger_instance


def route_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """
    Canonical entry point for all Discord event routing.

    Wraps _discord_logger.event() with validation.
    All modules should prefer this over direct _dl.event() calls.

    Args:
        event_type: Must be a key in CHANNEL_MAP.
        payload: Event data dict.
    """
    if _router_logger is None:
        # Fallback: try config-attached logger
        try:
            from core import config
            _dl = getattr(config, "_discord_logger", None)
            if _dl is not None:
                _dl.event(event_type, payload)
                return
        except Exception:
            pass
        return

    _router_logger.event(event_type, payload)
