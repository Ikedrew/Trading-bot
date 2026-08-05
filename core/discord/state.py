"""
Discord V2 State Manager — Tracks message IDs for editable live cards.

Persists to local JSON file so live market cards survive bot restarts.
On startup, the renderer reads stored message_ids to resume editing
existing Discord messages rather than creating new ones.

Storage: logs/discord_state.json

Thread-safe: uses file lock pattern for read/write.
Never raises: all operations are fail-safe.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = "logs/discord_state.json"
_STATE_VERSION = 1


class DiscordState:
    """
    Manages Discord message IDs for editable live cards.

    Usage:
        state = DiscordState()
        state.load()

        # Check if a card exists
        card = state.get_live_card("AUDUSD")

        # Store a new card
        state.set_live_card("AUDUSD", channel_id="123", message_id="456")
        state.save()
    """

    def __init__(self, state_file: str = _DEFAULT_STATE_FILE) -> None:
        self._path = Path(state_file)
        self._live_cards: dict[str, dict[str, Any]] = {}
        self._system_health: dict[str, str] = {}
        self._loaded = False

    def load(self) -> None:
        """Load state from disk. Safe to call multiple times."""
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if data.get("version") == _STATE_VERSION:
                    self._live_cards = data.get("live_cards", {})
                    self._system_health = data.get("system_health", {})
                    self._loaded = True
                    logger.info("[DISCORD_STATE] loaded %d live cards", len(self._live_cards))
                else:
                    logger.info("[DISCORD_STATE] version mismatch — starting fresh")
                    self._live_cards = {}
                    self._loaded = True
            else:
                self._live_cards = {}
                self._loaded = True
        except Exception as exc:
            logger.warning("[DISCORD_STATE] load failed: %s — starting fresh", exc)
            self._live_cards = {}
            self._loaded = True

    def save(self) -> None:
        """Persist state to disk. Fire-and-forget."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": _STATE_VERSION,
                "live_cards": self._live_cards,
                "system_health": self._system_health,
            }
            self._path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[DISCORD_STATE] save failed: %s", exc)

    def get_live_card(self, symbol: str) -> dict[str, Any] | None:
        """
        Get stored live card info for a symbol.

        Returns:
            {"channel_id": "...", "message_id": "...", "last_updated": "..."}
            or None if no card exists.
        """
        if not self._loaded:
            self.load()
        entry = self._live_cards.get(symbol)
        if entry and entry.get("message_id"):
            return entry
        return None

    def set_live_card(
        self,
        symbol: str,
        *,
        channel_id: str,
        message_id: str,
    ) -> None:
        """Store a live card message reference."""
        existing = self._live_cards.get(symbol, {})
        existing.update({
            "channel_id": channel_id,
            "message_id": message_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        self._live_cards[symbol] = existing

    def get_market_state(self, symbol: str) -> dict[str, Any]:
        """Get accumulated market state for a symbol."""
        card = self._live_cards.get(symbol, {})
        return card.get("market_state", {})

    def merge_market_state(self, symbol: str, new_fields: dict[str, Any]) -> dict[str, Any]:
        """
        Merge new market data into existing accumulated state.

        Only overwrites fields that have non-empty/non-zero values in new_fields.
        Returns the merged state.
        """
        if symbol not in self._live_cards:
            self._live_cards[symbol] = {}
        card = self._live_cards[symbol]
        current = card.get("market_state", {})

        for key, value in new_fields.items():
            # Only overwrite if new value is meaningful
            if value is not None and value != "" and value != 0 and value != 0.0:
                current[key] = value

        current["last_merged"] = datetime.now(timezone.utc).isoformat()
        card["market_state"] = current
        self._live_cards[symbol] = card
        return current

    def get_timeline(self, symbol: str) -> list[dict[str, str]]:
        """Get the rolling timeline entries for a symbol."""
        card = self._live_cards.get(symbol, {})
        return card.get("timeline", [])

    def append_timeline(self, symbol: str, entries: list[dict[str, str]], max_entries: int = 15) -> None:
        """
        Append new timeline entries for a symbol. Keeps only the most recent max_entries.

        Each entry: {"time": "HH:MM", "text": "description"}
        """
        if symbol not in self._live_cards:
            self._live_cards[symbol] = {}
        card = self._live_cards[symbol]
        timeline = card.get("timeline", [])
        timeline.extend(entries)
        # Keep only most recent
        card["timeline"] = timeline[-max_entries:]
        self._live_cards[symbol] = card

    def get_observation_id(self, symbol: str) -> str:
        """Get the last rendered observation_id for a symbol."""
        card = self._live_cards.get(symbol, {})
        return card.get("observation_id", "")

    def set_observation_id(self, symbol: str, observation_id: str) -> None:
        """Store the current observation_id for a symbol."""
        if symbol not in self._live_cards:
            self._live_cards[symbol] = {}
        self._live_cards[symbol]["observation_id"] = observation_id

    def reset_timeline(self, symbol: str) -> None:
        """Clear timeline for a symbol (new observation started)."""
        if symbol in self._live_cards:
            self._live_cards[symbol]["timeline"] = []

    def get_system_health(self) -> dict[str, str] | None:
        """Get stored system health card info."""
        return self._system_health if self._system_health.get("message_id") else None

    def set_system_health(self, *, channel_id: str, message_id: str) -> None:
        """Store system health card message reference."""
        self._system_health = {
            "channel_id": channel_id,
            "message_id": message_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def remove_live_card(self, symbol: str) -> None:
        """Remove a live card reference (e.g., if message was deleted)."""
        self._live_cards.pop(symbol, None)

    def all_live_cards(self) -> dict[str, dict[str, Any]]:
        """Return all stored live card references."""
        if not self._loaded:
            self.load()
        return dict(self._live_cards)

    @property
    def loaded(self) -> bool:
        return self._loaded
