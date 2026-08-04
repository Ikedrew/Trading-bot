"""Discord V2 Phase 4 — API Delivery Tests.

Tests Discord REST API delivery with mocked HTTP.
No real Discord messages are sent.

Proves:
1. card_to_embed() converts all card types correctly
2. send_message returns message_id on success
3. edit_message handles success and failure
4. Missing token fails safely (no crash)
5. HTTP errors are caught and logged
6. Renderer can call delivery client without crashing
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.discord.bot_client import DiscordBotClient
from core.discord.cards import (
    card_to_embed,
    build_market_card,
    build_opportunity_card,
    build_execution_card,
    build_system_card,
)


# ═══════════════════════════════════════════════════════════════════════════════
# EMBED CONVERSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCardToEmbed:
    """card_to_embed converts structured cards to Discord embed format."""

    def test_market_card_embed(self):
        card = build_market_card("MARKET_CONTEXT", {
            "symbol": "AUDUSD", "regime": "RANGING",
            "h4_trend": "NEUTRAL", "h1_bos": "BULLISH",
        })
        embed = card_to_embed(card)
        assert "title" in embed
        assert "AUDUSD" in embed["title"]
        assert "color" in embed
        assert embed["color"] == 0x3498DB  # Blue
        assert "fields" in embed
        assert any(f["name"] == "Regime" for f in embed["fields"])

    def test_opportunity_card_embed(self):
        card = build_opportunity_card("TRADE_DECISION", {
            "symbol": "EURUSD", "decision": "EXECUTE",
            "strategy": "TREND_CONTINUATION", "score": 0.85,
            "side": "BUY",
        })
        embed = card_to_embed(card)
        assert "EURUSD" in embed["title"]
        assert embed["color"] == 0xF39C12  # Orange
        assert any(f["name"] == "Strategy" for f in embed["fields"])

    def test_execution_card_embed(self):
        card = build_execution_card("ORDER_FILLED", {
            "symbol": "GBPUSD", "side": "SELL",
            "fill_price": 1.27, "volume": 0.5,
        })
        embed = card_to_embed(card)
        assert "GBPUSD" in embed["title"]
        assert "FILLED" in embed["title"]
        assert embed["color"] == 0x2ECC71  # Green

    def test_execution_block_embed_is_red(self):
        card = build_execution_card("RISK_BLOCK", {
            "symbol": "USDJPY", "guard": "spread_guard",
            "reason": "SPREAD_EXCEEDED",
        })
        embed = card_to_embed(card)
        assert embed["color"] == 0xE74C3C  # Red for blocks

    def test_system_card_embed(self):
        card = build_system_card("SYSTEM_STARTUP", {"mode": "LIVE_V10", "symbols": 10})
        embed = card_to_embed(card)
        assert "Startup" in embed["title"] or "System" in embed["title"]
        assert embed["color"] == 0x95A5A6  # Grey

    def test_error_embed_is_red(self):
        card = build_system_card("ERROR", {"error_type": "TimeoutError", "message": "fail"})
        embed = card_to_embed(card)
        assert embed["color"] == 0xE74C3C  # Red

    def test_embed_is_json_serializable(self):
        card = build_market_card("MARKET_CONTEXT", {"symbol": "NAS100", "regime": "TRENDING"})
        embed = card_to_embed(card)
        serialized = json.dumps(embed)
        assert len(serialized) > 10


# ═══════════════════════════════════════════════════════════════════════════════
# BOT CLIENT — SEND MESSAGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendMessage:
    """send_message communicates with Discord API correctly."""

    @patch("core.discord.bot_client.requests.post")
    def test_send_success_returns_message_id(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "msg_12345"}
        mock_post.return_value = mock_resp

        client = DiscordBotClient(token="test_token")
        card = build_market_card("MARKET_CONTEXT", {"symbol": "EURUSD", "regime": "RANGING"})
        result = client.send_message("channel_abc", card)

        assert result == "msg_12345"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "channel_abc" in call_kwargs[0][0]
        assert "Bot test_token" in call_kwargs[1]["headers"]["Authorization"]

    @patch("core.discord.bot_client.requests.post")
    def test_send_failure_returns_none(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        client = DiscordBotClient(token="test_token")
        card = build_market_card("MARKET_CONTEXT", {"symbol": "AUDUSD"})
        result = client.send_message("ch_1", card)
        assert result is None

    @patch("core.discord.bot_client.requests.post")
    def test_send_timeout_returns_none(self, mock_post):
        import requests as req
        mock_post.side_effect = req.Timeout("timeout")

        client = DiscordBotClient(token="tok")
        result = client.send_message("ch", build_market_card("H4_CONTEXT", {"symbol": "X"}))
        assert result is None

    def test_send_no_token_returns_none(self):
        client = DiscordBotClient(token="")
        result = client.send_message("ch_123", {"type": "market", "symbol": "E"})
        assert result is None

    def test_send_no_channel_returns_none(self):
        client = DiscordBotClient(token="tok")
        result = client.send_message("", {"type": "market", "symbol": "E"})
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# BOT CLIENT — EDIT MESSAGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEditMessage:
    """edit_message uses PATCH to update existing messages."""

    @patch("core.discord.bot_client.requests.patch")
    def test_edit_success_returns_true(self, mock_patch):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_patch.return_value = mock_resp

        client = DiscordBotClient(token="test_token")
        card = build_market_card("MARKET_CONTEXT", {"symbol": "EURUSD", "regime": "TRENDING"})
        result = client.edit_message("ch_1", "msg_99", card)

        assert result is True
        mock_patch.assert_called_once()
        url = mock_patch.call_args[0][0]
        assert "ch_1" in url
        assert "msg_99" in url

    @patch("core.discord.bot_client.requests.patch")
    def test_edit_failure_returns_false(self, mock_patch):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Unknown Message"
        mock_patch.return_value = mock_resp

        client = DiscordBotClient(token="tok")
        result = client.edit_message("ch", "msg", build_system_card("HEARTBEAT", {}))
        assert result is False

    @patch("core.discord.bot_client.requests.patch")
    def test_edit_timeout_returns_false(self, mock_patch):
        import requests as req
        mock_patch.side_effect = req.Timeout("timeout")

        client = DiscordBotClient(token="tok")
        result = client.edit_message("ch", "msg", {"type": "system"})
        assert result is False

    def test_edit_no_token_returns_false(self):
        client = DiscordBotClient(token="")
        result = client.edit_message("ch", "msg", {"type": "market"})
        assert result is False

    def test_edit_no_message_id_returns_false(self):
        client = DiscordBotClient(token="tok")
        result = client.edit_message("ch", "", {"type": "market"})
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: RENDERER → CLIENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererDelivery:
    """Renderer calls bot_client without crashing on failure."""

    @patch("core.discord.bot_client.requests.post")
    def test_renderer_handles_api_failure(self, mock_post, tmp_path):
        mock_post.side_effect = ConnectionError("Network down")

        from core.discord.renderer import DiscordRenderer
        from core.discord.state import DiscordState

        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient(token="test_token")

        # Should not raise
        result = renderer.render("MARKET_CONTEXT", {"symbol": "AUDUSD", "regime": "RANGING"})
        assert result is not None  # Renderer produces result even if delivery fails

    def test_client_stats_accessible(self):
        client = DiscordBotClient(token="")
        client.send_message("ch", {"type": "market"})
        client.edit_message("ch", "msg", {"type": "market"})
        stats = client.stats
        assert stats["sends_attempted"] == 1
        assert stats["edits_attempted"] == 1
        assert stats["connected"] == 0
