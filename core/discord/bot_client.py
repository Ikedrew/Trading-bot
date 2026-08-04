"""
Discord V2 Bot Client — Real Discord API delivery via Bot token REST calls.

Sends and edits messages using Discord's REST API.
Synchronous HTTP (requests library). Never blocks trading engine beyond timeout.
All failures are caught and logged — never crash the bot.

Requires:
    DISCORD_BOT_TOKEN environment variable or config setting.

API endpoints used:
    POST   /channels/{channel_id}/messages           → send new message
    PATCH  /channels/{channel_id}/messages/{msg_id}  → edit existing message
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DISCORD_API_BASE = "https://discord.com/api/v10"
_TIMEOUT_SECONDS = 5


class DiscordBotClient:
    """
    Discord message delivery via Bot token REST API.

    Usage:
        client = DiscordBotClient()
        msg_id = client.send_message(channel_id, card)
        client.edit_message(channel_id, msg_id, card)
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token if token is not None else self._load_token()
        self._send_count = 0
        self._edit_count = 0
        self._error_count = 0

    def _load_token(self) -> str:
        """Load bot token from config/environment."""
        try:
            from core import config
            return getattr(config, "DISCORD_BOT_TOKEN", "") or ""
        except Exception:
            return ""

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
        }

    @property
    def connected(self) -> bool:
        """True if a token is configured."""
        return bool(self._token)

    def send_message(self, channel_id: str, card: dict[str, Any]) -> str | None:
        """
        Send a new message (embed) to a Discord channel.

        Args:
            channel_id: Discord channel ID string
            card: Structured card dict from cards.py

        Returns:
            message_id string on success, None on failure.
        """
        if not channel_id or not self._token:
            self._send_count += 1
            logger.debug("[DISCORD_V2] send skipped: no channel_id or token")
            return None

        self._send_count += 1

        try:
            from core.discord.cards import card_to_embed
            embed = card_to_embed(card)
        except Exception as exc:
            logger.warning("[DISCORD_V2] embed conversion failed: %s", exc)
            self._error_count += 1
            return None

        url = f"{_DISCORD_API_BASE}/channels/{channel_id}/messages"
        payload = {"embeds": [embed]}

        try:
            resp = requests.post(
                url,
                headers=self._headers,
                data=json.dumps(payload),
                timeout=_TIMEOUT_SECONDS,
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                message_id = data.get("id", "")
                logger.info(
                    "[DISCORD_V2] SENT channel=%s message_id=%s symbol=%s",
                    channel_id, message_id, card.get("symbol", "?"),
                )
                return message_id
            else:
                logger.warning(
                    "[DISCORD_V2] send failed: status=%d body=%s",
                    resp.status_code, resp.text[:200],
                )
                self._error_count += 1
                return None

        except requests.Timeout:
            logger.warning("[DISCORD_V2] send timeout: channel=%s", channel_id)
            self._error_count += 1
            return None
        except Exception as exc:
            logger.warning("[DISCORD_V2] send error: %s", exc)
            self._error_count += 1
            return None

    def edit_message(self, channel_id: str, message_id: str, card: dict[str, Any]) -> bool:
        """
        Edit an existing Discord message (embed).

        Args:
            channel_id: Discord channel ID
            message_id: Existing message ID to edit
            card: Updated card dict

        Returns:
            True on success, False on failure.
        """
        if not channel_id or not message_id or not self._token:
            self._edit_count += 1
            logger.debug("[DISCORD_V2] edit skipped: missing channel/message/token")
            return False

        self._edit_count += 1

        try:
            from core.discord.cards import card_to_embed
            embed = card_to_embed(card)
        except Exception as exc:
            logger.warning("[DISCORD_V2] embed conversion failed: %s", exc)
            self._error_count += 1
            return False

        url = f"{_DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"
        payload = {"embeds": [embed]}

        try:
            resp = requests.patch(
                url,
                headers=self._headers,
                data=json.dumps(payload),
                timeout=_TIMEOUT_SECONDS,
            )

            if resp.status_code == 200:
                logger.info(
                    "[DISCORD_V2] EDITED channel=%s message=%s symbol=%s",
                    channel_id, message_id, card.get("symbol", "?"),
                )
                return True
            else:
                logger.warning(
                    "[DISCORD_V2] edit failed: status=%d message=%s body=%s",
                    resp.status_code, message_id, resp.text[:200],
                )
                self._error_count += 1
                return False

        except requests.Timeout:
            logger.warning("[DISCORD_V2] edit timeout: message=%s", message_id)
            self._error_count += 1
            return False
        except Exception as exc:
            logger.warning("[DISCORD_V2] edit error: %s", exc)
            self._error_count += 1
            return False

    @property
    def stats(self) -> dict[str, int]:
        """Return delivery statistics."""
        return {
            "sends_attempted": self._send_count,
            "edits_attempted": self._edit_count,
            "errors": self._error_count,
            "connected": 1 if self.connected else 0,
        }
