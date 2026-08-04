"""
Discord V2 — Clean observability rendering layer.

Organises Discord output around human questions:
    1. LIVE_MARKET:  "What's the market doing?"
    2. OPPORTUNITY:  "What opportunities happened?"
    3. EXECUTION:    "What trades happened?"
    4. SYSTEM:       "Is the machine alive?"

This package does NOT replace existing Discord webhooks.
It provides a parallel rendering path that future phases will
connect to Discord bot/embed infrastructure.

Architecture:
    Trading Engine → Events → S3 (source of truth) → Discord V2 (human interface)

Discord V2 never contains trading logic. It only renders.
"""

from core.discord.renderer import DiscordRenderer, Category
from core.discord.cards import (
    build_market_card,
    build_opportunity_card,
    build_execution_card,
    build_system_card,
)
from core.discord.state import DiscordState
from core.discord.bot_client import DiscordBotClient

__all__ = [
    "DiscordRenderer",
    "Category",
    "DiscordState",
    "DiscordBotClient",
    "build_market_card",
    "build_opportunity_card",
    "build_execution_card",
    "build_system_card",
]
