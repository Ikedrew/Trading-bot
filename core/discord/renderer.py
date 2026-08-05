"""
Discord V2 Renderer — Routes events to human-question categories.

Categories:
    LIVE_MARKET:  Market state updates (H4, H1, M15, regime, context)
    OPPORTUNITY:  Decision lifecycle (detected → assessed → executed/rejected)
    EXECUTION:    Trade lifecycle (attempt → fill → modify → close)
    SYSTEM:       Infrastructure events (startup, shutdown, errors, kill switch)

This module does NOT send messages to Discord.
It classifies events and builds structured render instructions
that future phases will deliver to Discord channels.

Design:
    - Pure function: event_type + data → (category, card)
    - No side effects, no I/O, no state
    - Testable in isolation
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from core.discord.cards import (
    build_market_card,
    build_opportunity_card,
    build_execution_card,
    build_system_card,
)
from core.live_market_state import read_live_market_state


class Category(str, Enum):
    """Discord V2 channel categories — one per human question."""
    LIVE_MARKET = "LIVE_MARKET"
    OPPORTUNITY = "OPPORTUNITY"
    EXECUTION = "EXECUTION"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


# ─── EVENT → CATEGORY ROUTING ─────────────────────────────────────────────────

_CATEGORY_MAP: dict[str, Category] = {
    # LIVE_MARKET: "What's the market doing?"
    "MARKET_CONTEXT": Category.LIVE_MARKET,
    "H4_CONTEXT": Category.LIVE_MARKET,
    "HTF_CONTEXT_BUNDLE": Category.LIVE_MARKET,
    "MARKET_SNAPSHOT": Category.LIVE_MARKET,
    "BOT_STATE": Category.LIVE_MARKET,

    # OPPORTUNITY: "What opportunities happened?"
    "TRADE_DECISION": Category.OPPORTUNITY,
    "DECISION_REJECTED": Category.OPPORTUNITY,
    "OPPORTUNITY_LIFECYCLE": Category.OPPORTUNITY,

    # EXECUTION: "What trades happened?"
    "ORDER_ATTEMPT": Category.EXECUTION,
    "ORDER_FILLED": Category.EXECUTION,
    "ORDER_MODIFIED": Category.EXECUTION,
    "TRADE_CLOSED": Category.EXECUTION,
    "TRADE_RESULT": Category.EXECUTION,
    "RISK_BLOCK": Category.EXECUTION,
    "RISK_CHECK": Category.EXECUTION,
    "EXPOSURE_UPDATE": Category.EXECUTION,

    # SYSTEM: "Is the machine alive?"
    "SYSTEM_STARTUP": Category.SYSTEM,
    "SYSTEM_SHUTDOWN": Category.SYSTEM,
    "KILL_SWITCH": Category.SYSTEM,
    "ERROR": Category.SYSTEM,
    "HEARTBEAT": Category.SYSTEM,
    "PNL_UPDATE": Category.SYSTEM,
    "DRAWDOWN_UPDATE": Category.SYSTEM,
    "DAILY_REPORT": Category.SYSTEM,
}


def classify_event(event_type: str) -> Category:
    """Classify an event_type into a Discord V2 category."""
    return _CATEGORY_MAP.get(event_type, Category.UNKNOWN)


# ─── RENDERER ──────────────────────────────────────────────────────────────────

class DiscordRenderer:
    """
    Discord V2 rendering entry point.

    Accepts an event, classifies it, and builds a structured card.
    For LIVE_MARKET events, manages the card lifecycle (create vs edit).

    Usage:
        renderer = DiscordRenderer()
        result = renderer.render("ORDER_FILLED", {"symbol": "EURUSD", ...})
    """

    def __init__(self) -> None:
        self._state: "DiscordState | None" = None
        self._client: "DiscordBotClient | None" = None

    def _ensure_initialized(self) -> None:
        """Lazy-initialize state and client on first use."""
        if self._state is None:
            from core.discord.state import DiscordState
            self._state = DiscordState()
            self._state.load()
        if self._client is None:
            from core.discord.bot_client import DiscordBotClient
            self._client = DiscordBotClient()

    def render(self, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """
        Render an event into a Discord V2 card structure.

        Intelligence layer:
            - LIVE_MARKET: accumulates state, edits card
            - OPPORTUNITY: filtered (only strategy-matched or deeper)
            - EXECUTION: always sent
            - SYSTEM: health-card events edit; critical events send new
        """
        category = classify_event(event_type)
        if category == Category.UNKNOWN:
            return None

        # ─── NOISE FILTER: suppress events that don't belong in Discord ───
        if self._should_suppress(category, event_type, data or {}):
            return None

        card = self._build_card(category, event_type, data or {})

        # Live market cards: accumulate state + edit
        if category == Category.LIVE_MARKET:
            return self._handle_live_market(event_type, data or {}, card)

        # System health events: some edit card, some send new message
        if category == Category.SYSTEM:
            return self._handle_system(event_type, data or {}, card)

        # Opportunity + Execution: send new messages
        return self._handle_standard_event(category, event_type, card)

    def _should_suppress(self, category: Category, event_type: str, data: dict[str, Any]) -> bool:
        """
        Intelligence filter: suppress events that are noise for humans.

        Suppressed (S3 only, not Discord):
            - PIPELINE_DROP (no pattern = nothing happened)
            - EXPOSURE_UPDATE (internal state)
            - RISK_CHECK where result is not a block
            - PNL_UPDATE / DRAWDOWN_UPDATE as discrete events (folded into health card)
            - DECISION_REJECTED at early stages (opportunity/strategy)
        """
        # Events completely removed from Discord
        if event_type == "PIPELINE_DROP":
            return True
        if event_type == "EXPOSURE_UPDATE":
            return True
        if event_type == "RISK_CHECK":
            result = data.get("result", "")
            if result == "APPROVED" or result != "REJECTED":
                return True

        # PNL/DRAWDOWN handled by health card edit, not new message
        # (They'll be routed to _handle_system which edits the card)
        # Don't suppress here — let _handle_system manage them

        # Opportunity filtering: only show meaningful near-misses
        if event_type == "DECISION_REJECTED":
            stage = data.get("stage") or data.get("terminal_stage") or ""
            # Only send if reached risk stage or deeper
            _deep_stages = ("risk", "execution", "ev_policy", "data_validation", "swing")
            if not any(s in stage.lower() for s in _deep_stages):
                return True

        # OPPORTUNITY_LIFECYCLE: only show ASSESSED and EXECUTED (not every REJECTED)
        if event_type == "OPPORTUNITY_LIFECYCLE":
            state = data.get("lifecycle_state", "")
            # Only show opportunities that progressed meaningfully
            if state == "REJECTED":
                # Only show rejections that had strategy selected (not pattern_not_selected)
                reason = data.get("rejection_reason", "")
                if reason == "pattern_not_selected":
                    return True
                # Show decision_engine rejections that had a strategy
                if not data.get("strategy_classification"):
                    return True

        return False

    def _handle_live_market(self, event_type: str, data: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        """
        Handle live market card by reading from live_market_state snapshot.

        Pure presentation layer: reads the complete latest snapshot for the
        symbol and renders the card from that snapshot. No incremental
        event-state accumulation. Compares against previous render to
        suppress cosmetic edits.

        Currently limited to EURUSD (V2 live market pilot).
        """
        self._ensure_initialized()
        symbol = card.get("symbol") or data.get("symbol") or ""

        if not symbol:
            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "skip",
                "reason": "no_symbol",
            }

        # V2 Live Market pilot: only EURUSD for now
        _LIVE_MARKET_V2_SYMBOLS = {"EURUSD"}
        if symbol not in _LIVE_MARKET_V2_SYMBOLS:
            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "skip",
                "reason": "not_in_v2_pilot",
            }

        # ─── READ SNAPSHOT (single source of truth) ───────────────────
        try:
            snapshot = read_live_market_state(symbol)
        except Exception:
            snapshot = None

        if not snapshot:
            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "skip",
                "reason": "no_snapshot_available",
            }

        # ─── BUILD CARD FROM SNAPSHOT ─────────────────────────────────
        market = snapshot.get("market", {})
        opp = snapshot.get("opportunity", {})
        strat = snapshot.get("strategy", {})
        entry = snapshot.get("entry", {})
        risk = snapshot.get("risk", {})

        card_data = {
            "symbol": symbol,
            "regime": market.get("regime"),
            "h4_trend": market.get("h4_trend"),
            "h4_trend_strength": market.get("h4_trend_strength"),
            "h1_bos": market.get("h1_bos_direction"),
            "h1_structural_clarity": market.get("h1_structural_clarity"),
            "location_type": market.get("location_type"),
            "range_position": market.get("range_position"),
            "m5_momentum": market.get("m5_momentum"),
            "volatility_state": market.get("volatility_state"),
            "opportunity_state": opp.get("state"),
            "opportunity_type": opp.get("opportunity_type"),
            "opportunity_quality": opp.get("overall_quality"),
            "strategy": strat.get("family"),
            "strategy_confidence": strat.get("confidence"),
            "entry_status": entry.get("status"),
            "entry_price": entry.get("price"),
            "stop_price": entry.get("stop"),
            "target_price": entry.get("target"),
            "expected_rr": entry.get("expected_rr"),
            "risk_approved": risk.get("approved"),
            "position_size": risk.get("position_size"),
        }
        rendered_card = build_market_card("MARKET_CONTEXT", card_data)

        # ─── CHANGE DETECTION (compare vs last rendered state) ────────
        _MEANINGFUL_FIELDS = (
            "regime", "h4_trend", "h1_bos_direction", "location_type",
            "range_position", "m5_momentum", "volatility_state",
            "strategy", "entry_status", "opportunity_state",
            "h4_trend_strength", "h1_structural_clarity",
        )
        previous_state = self._state.get_market_state(symbol)
        new_fields = rendered_card.get("fields", {})

        has_meaningful_change = any(
            new_fields.get(f) != previous_state.get(f)
            for f in _MEANINGFUL_FIELDS
            if new_fields.get(f)
        )

        if not has_meaningful_change and previous_state:
            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": rendered_card,
                "action": "skip",
                "reason": "no_meaningful_change",
            }

        # Update tracked state for next comparison
        self._state.merge_market_state(symbol, new_fields)
        self._state.save()  # Persist so next renderer instance sees latest

        # ─── DELIVER (edit existing or create new) ────────────────────
        existing = self._state.get_live_card(symbol)

        if existing and existing.get("message_id"):
            channel_id = existing.get("channel_id", "")
            message_id = existing.get("message_id", "")

            try:
                self._client.edit_message(channel_id, message_id, rendered_card)
            except Exception:
                pass

            self._state.set_live_card(symbol, channel_id=channel_id, message_id=message_id)

            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": rendered_card,
                "action": "edit",
                "message_id": message_id,
            }
        else:
            try:
                from core import config as _cfg
                channels = getattr(_cfg, "DISCORD_LIVE_CHANNELS", {})
                channel_id = channels.get(symbol, "")
            except Exception:
                channel_id = ""

            new_message_id = None
            try:
                new_message_id = self._client.send_message(channel_id, rendered_card)
            except Exception:
                pass

            if new_message_id:
                self._state.set_live_card(symbol, channel_id=channel_id, message_id=new_message_id)
                self._state.save()

            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": rendered_card,
                "action": "create",
                "channel_id": channel_id,
                "message_id": new_message_id or "",
            }

    def _handle_system(self, event_type: str, data: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        """
        Handle system events: health-card updates vs critical new messages.

        Editable health card events (update existing card):
            HEARTBEAT, PNL_UPDATE, DRAWDOWN_UPDATE, SYSTEM_STARTUP, SYSTEM_SHUTDOWN

        New message events (critical, always post):
            ERROR, KILL_SWITCH, DAILY_REPORT
        """
        self._ensure_initialized()

        # These events UPDATE the system health card (no new message)
        _HEALTH_CARD_EVENTS = {"HEARTBEAT", "PNL_UPDATE", "DRAWDOWN_UPDATE", "SYSTEM_STARTUP", "SYSTEM_SHUTDOWN"}

        if event_type in _HEALTH_CARD_EVENTS:
            return self._update_system_health_card(event_type, data, card)

        # Critical events: send new message to system channel
        return self._handle_standard_event(Category.SYSTEM, event_type, card)

    def _update_system_health_card(self, event_type: str, data: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        """Edit the persistent system health card."""
        existing = self._state.get_system_health()

        if existing:
            channel_id = existing.get("channel_id", "")
            message_id = existing.get("message_id", "")
            try:
                self._client.edit_message(channel_id, message_id, card)
            except Exception:
                pass
            self._state.set_system_health(channel_id=channel_id, message_id=message_id)
            return {
                "category": Category.SYSTEM.value,
                "event_type": event_type,
                "card": card,
                "action": "health_update",
                "message_id": message_id,
            }
        else:
            # Create new health card
            channel_id = ""
            try:
                from core import config as _cfg
                channels = getattr(_cfg, "DISCORD_V2_CHANNELS", {})
                channel_id = channels.get("system", "")
            except Exception:
                pass

            new_id = None
            try:
                new_id = self._client.send_message(channel_id, card)
            except Exception:
                pass

            if new_id:
                self._state.set_system_health(channel_id=channel_id, message_id=new_id)
                self._state.save()

            return {
                "category": Category.SYSTEM.value,
                "event_type": event_type,
                "card": card,
                "action": "health_create",
                "message_id": new_id or "",
            }

    def _handle_standard_event(self, category: Category, event_type: str, card: dict[str, Any]) -> dict[str, Any]:
        """
        Handle non-LIVE_MARKET events: send to consolidated channel.

        Routes:
            OPPORTUNITY → "opportunities" channel
            EXECUTION   → "executions" channel
            SYSTEM      → "system" channel

        Attempts delivery via bot_client. Fire-and-forget.
        """
        self._ensure_initialized()

        # Map category to channel config key
        _CATEGORY_TO_CHANNEL = {
            Category.OPPORTUNITY: "opportunities",
            Category.EXECUTION: "executions",
            Category.SYSTEM: "system",
        }
        channel_key = _CATEGORY_TO_CHANNEL.get(category, "")

        # Look up channel ID from config
        channel_id = ""
        if channel_key:
            try:
                from core import config as _cfg
                channels = getattr(_cfg, "DISCORD_V2_CHANNELS", {})
                channel_id = channels.get(channel_key, "")
            except Exception:
                pass

        # Attempt delivery (placeholder — logs intention)
        try:
            self._client.send_message(channel_id, card)
        except Exception:
            pass  # Delivery failure must not crash renderer

        return {
            "category": category.value,
            "event_type": event_type,
            "card": card,
            "action": "send",
            "channel": channel_key,
        }

    def _build_card(self, category: Category, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Route to the appropriate card builder."""
        if category == Category.LIVE_MARKET:
            return build_market_card(event_type, data)
        elif category == Category.OPPORTUNITY:
            return build_opportunity_card(event_type, data)
        elif category == Category.EXECUTION:
            return build_execution_card(event_type, data)
        elif category == Category.SYSTEM:
            return build_system_card(event_type, data)
        return {"type": "unknown", "event_type": event_type, "data": data}
