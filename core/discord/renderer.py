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
    Does NOT send to Discord — that is a future phase responsibility.

    Usage:
        renderer = DiscordRenderer()
        result = renderer.render("ORDER_FILLED", {"symbol": "EURUSD", ...})
        # result = {"category": "EXECUTION", "card": {...}}
    """

    def render(self, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """
        Render an event into a Discord V2 card structure.

        Args:
            event_type: Event name (from CHANNEL_MAP keys)
            data: Event payload dict

        Returns:
            Structured render instruction:
                {"category": Category, "event_type": str, "card": dict}
            Or None if event_type is not recognised.
        """
        category = classify_event(event_type)
        if category == Category.UNKNOWN:
            return None

        card = self._build_card(category, event_type, data or {})

        return {
            "category": category.value,
            "event_type": event_type,
            "card": card,
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
