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
    "PIPELINE_DROP": Category.OPPORTUNITY,

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

        For LIVE_MARKET events: manages create/edit lifecycle.
        For other categories: builds card (delivery in future phases).

        Returns:
            Structured render instruction:
                {"category": str, "event_type": str, "card": dict, "action": str}
            Or None if event_type is not recognised.
        """
        category = classify_event(event_type)
        if category == Category.UNKNOWN:
            return None

        card = self._build_card(category, event_type, data or {})

        # Live market cards have create/edit lifecycle
        if category == Category.LIVE_MARKET:
            return self._handle_live_market(event_type, card)

        return {
            "category": category.value,
            "event_type": event_type,
            "card": card,
            "action": "send",
        }

    def _handle_live_market(self, event_type: str, card: dict[str, Any]) -> dict[str, Any]:
        """
        Handle live market card lifecycle: create new or edit existing.

        Checks state for existing message_id:
            - If exists → action=edit (update card in place)
            - If missing → action=create (new card needed)

        Also attempts delivery via bot_client (placeholder in Phase 2).
        """
        self._ensure_initialized()
        symbol = card.get("symbol") or ""

        if not symbol:
            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "skip",
                "reason": "no_symbol",
            }

        # Check for existing card
        existing = self._state.get_live_card(symbol)

        if existing:
            # Edit existing card
            channel_id = existing.get("channel_id", "")
            message_id = existing.get("message_id", "")

            try:
                self._client.edit_message(channel_id, message_id, card)
            except Exception:
                pass  # Delivery failure must not crash renderer

            # Update timestamp in state
            self._state.set_live_card(
                symbol,
                channel_id=channel_id,
                message_id=message_id,
            )

            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "edit",
                "message_id": message_id,
            }
        else:
            # Create new card
            try:
                from core import config as _cfg
                channels = getattr(_cfg, "DISCORD_LIVE_CHANNELS", {})
                channel_id = channels.get(symbol, "")
            except Exception:
                channel_id = ""

            new_message_id = None
            try:
                new_message_id = self._client.send_message(channel_id, card)
            except Exception:
                pass  # Delivery failure must not crash renderer

            if new_message_id:
                self._state.set_live_card(
                    symbol,
                    channel_id=channel_id,
                    message_id=new_message_id,
                )
                self._state.save()

            return {
                "category": Category.LIVE_MARKET.value,
                "event_type": event_type,
                "card": card,
                "action": "create",
                "channel_id": channel_id,
                "message_id": new_message_id or "",
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
