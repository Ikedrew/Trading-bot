"""
Discord V2 Bot Client — Delivery interface for editable messages.

Abstracts Discord communication behind a sync-safe interface.
Current implementation: placeholder logging (no Discord API connection).
Future: connects via discord.py or direct REST for send/edit operations.

Design:
    - Never blocks the trading engine
    - Failures are logged, never raised
    - Stateless: relies on DiscordState for message tracking
    - All methods are fire-and-forget
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DiscordBotClient:
    """
    Discord message delivery interface.

    Phase 2: Placeholder that logs intended actions.
    Future phases will connect to Discord REST API or discord.py gateway.

    Usage:
        client = DiscordBotClient()
        msg_id = client.send_message(channel_id, card)
        client.edit_message(channel_id, message_id, card)
    """

    def __init__(self) -> None:
        self._connected = False
        self._send_count = 0
        self._edit_count = 0

    def send_message(self, channel_id: str, card: dict[str, Any]) -> str | None:
        """
        Send a new message to a Discord channel.

        Args:
            channel_id: Discord channel ID
            card: Structured card dict from cards.py

        Returns:
            message_id on success, None on failure.
            Current: always returns None (placeholder — no API connection).
        """
        if not channel_id:
            return None

        self._send_count += 1
        symbol = card.get("symbol", "?")
        card_type = card.get("type", "?")

        logger.info(
            "[DISCORD_V2] SEND channel=%s symbol=%s type=%s (delivery pending API connection)",
            channel_id, symbol, card_type,
        )

        # Future: Discord API call here
        # response = discord_rest.create_message(channel_id, embed=card_to_embed(card))
        # return response.id

        return None  # No API connection yet

    def edit_message(self, channel_id: str, message_id: str, card: dict[str, Any]) -> bool:
        """
        Edit an existing Discord message.

        Args:
            channel_id: Discord channel ID
            message_id: Existing message to edit
            card: Updated card dict

        Returns:
            True on success, False on failure.
            Current: always returns False (placeholder — no API connection).
        """
        if not channel_id or not message_id:
            return False

        self._edit_count += 1
        symbol = card.get("symbol", "?")
        card_type = card.get("type", "?")

        logger.info(
            "[DISCORD_V2] EDIT channel=%s message=%s symbol=%s type=%s (delivery pending API connection)",
            channel_id, message_id, symbol, card_type,
        )

        # Future: Discord API call here
        # discord_rest.edit_message(channel_id, message_id, embed=card_to_embed(card))
        # return True

        return False  # No API connection yet

    @property
    def stats(self) -> dict[str, int]:
        """Return delivery attempt statistics."""
        return {
            "sends_attempted": self._send_count,
            "edits_attempted": self._edit_count,
        }
