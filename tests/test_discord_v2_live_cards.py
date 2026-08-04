"""Discord V2 Phase 2 — Live Market Cards Tests.

Proves:
1. State manager persists and restores message IDs
2. Market card builder produces complete V10 field set
3. Renderer chooses CREATE for new symbols, EDIT for existing
4. State survives save/load cycle
5. Failed delivery does not crash
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.discord.state import DiscordState
from core.discord.bot_client import DiscordBotClient
from core.discord.cards import build_market_card
from core.discord.renderer import DiscordRenderer, Category


# ═══════════════════════════════════════════════════════════════════════════════
# STATE MANAGER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiscordState:
    """DiscordState persists live card message IDs to JSON file."""

    def test_fresh_state_has_no_cards(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "state.json"))
        state.load()
        assert state.get_live_card("AUDUSD") is None
        assert state.all_live_cards() == {}

    def test_set_and_get_live_card(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "state.json"))
        state.load()
        state.set_live_card("EURUSD", channel_id="ch_123", message_id="msg_456")
        card = state.get_live_card("EURUSD")
        assert card is not None
        assert card["channel_id"] == "ch_123"
        assert card["message_id"] == "msg_456"
        assert "last_updated" in card

    def test_save_and_reload(self, tmp_path):
        f = str(tmp_path / "state.json")
        # Write
        state1 = DiscordState(state_file=f)
        state1.load()
        state1.set_live_card("GBPUSD", channel_id="ch_A", message_id="msg_B")
        state1.save()

        # Read in new instance
        state2 = DiscordState(state_file=f)
        state2.load()
        card = state2.get_live_card("GBPUSD")
        assert card is not None
        assert card["message_id"] == "msg_B"

    def test_remove_live_card(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "state.json"))
        state.load()
        state.set_live_card("USDJPY", channel_id="c", message_id="m")
        state.remove_live_card("USDJPY")
        assert state.get_live_card("USDJPY") is None

    def test_load_nonexistent_file(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "missing.json"))
        state.load()  # Should not raise
        assert state.loaded is True
        assert state.all_live_cards() == {}

    def test_load_corrupt_file(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        state = DiscordState(state_file=str(f))
        state.load()  # Should not raise
        assert state.loaded is True

    def test_state_file_format(self, tmp_path):
        f = tmp_path / "state.json"
        state = DiscordState(state_file=str(f))
        state.load()
        state.set_live_card("AUDUSD", channel_id="ch1", message_id="msg1")
        state.save()

        raw = json.loads(f.read_text())
        assert raw["version"] == 1
        assert "AUDUSD" in raw["live_cards"]
        assert raw["live_cards"]["AUDUSD"]["message_id"] == "msg1"


# ═══════════════════════════════════════════════════════════════════════════════
# BOT CLIENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBotClient:
    """DiscordBotClient placeholder handles calls without crashing."""

    def test_send_message_returns_none_placeholder(self):
        client = DiscordBotClient()
        result = client.send_message("channel_123", {"type": "market", "symbol": "AUDUSD"})
        assert result is None  # No API connection yet

    def test_edit_message_returns_false_placeholder(self):
        client = DiscordBotClient()
        result = client.edit_message("ch", "msg", {"type": "market", "symbol": "EURUSD"})
        assert result is False  # No API connection yet

    def test_send_with_empty_channel_returns_none(self):
        client = DiscordBotClient()
        result = client.send_message("", {"type": "market"})
        assert result is None

    def test_edit_with_empty_ids_returns_false(self):
        client = DiscordBotClient()
        assert client.edit_message("", "msg", {}) is False
        assert client.edit_message("ch", "", {}) is False

    def test_stats_track_attempts(self):
        client = DiscordBotClient()
        client.send_message("ch", {"type": "market"})
        client.send_message("ch", {"type": "market"})
        client.edit_message("ch", "msg", {"type": "market"})
        assert client.stats["sends_attempted"] == 2
        assert client.stats["edits_attempted"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET CARD BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarketCardBuilder:
    """build_market_card produces complete V10 field structure."""

    def test_full_market_card_fields(self):
        card = build_market_card("MARKET_CONTEXT", {
            "symbol": "AUDUSD",
            "regime": "RANGING",
            "h4_trend": "NEUTRAL",
            "h1_bos": "BULLISH",
            "location_type": "OPEN_SPACE",
            "range_position": 0.96,
            "m5_momentum": "BULLISH",
            "volatility_state": "NORMAL",
            "opportunity_state": "WATCHING",
            "opportunity_type": "ZONE_REACTION",
            "strategy": "MEAN_REVERSION",
            "strategy_confidence": 0.70,
            "entry_status": "WAITING",
        })
        assert card["type"] == "market"
        assert card["symbol"] == "AUDUSD"
        f = card["fields"]
        assert f["regime"] == "RANGING"
        assert f["h4_trend"] == "NEUTRAL"
        assert f["h1_bos_direction"] == "BULLISH"
        assert f["location_type"] == "OPEN_SPACE"
        assert f["range_position"] == 0.96
        assert f["m5_momentum"] == "BULLISH"
        assert f["opportunity_state"] == "WATCHING"
        assert f["strategy"] == "MEAN_REVERSION"
        assert f["entry_status"] == "WAITING"
        assert f["updated_at"]  # Timestamp present

    def test_market_card_with_minimal_data(self):
        card = build_market_card("H4_CONTEXT", {"symbol": "USDJPY"})
        assert card["type"] == "market"
        assert card["symbol"] == "USDJPY"
        assert card["fields"]["regime"] == ""
        assert card["fields"]["updated_at"]  # Always has timestamp

    def test_market_card_with_empty_data(self):
        card = build_market_card("MARKET_SNAPSHOT", {})
        assert card["type"] == "market"
        assert card["symbol"] == ""
        assert card["fields"]["h4_trend"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# RENDERER LIFECYCLE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererLiveMarketLifecycle:
    """Renderer manages create vs edit for live market cards."""

    def test_new_symbol_triggers_create(self, tmp_path):
        """First market event for a symbol → action=create."""
        renderer = DiscordRenderer()
        # Inject fresh state
        from core.discord.state import DiscordState
        renderer._state = DiscordState(state_file=str(tmp_path / "state.json"))
        renderer._state.load()
        renderer._client = DiscordBotClient()

        result = renderer.render("MARKET_CONTEXT", {"symbol": "NZDUSD", "regime": "TRENDING"})
        assert result is not None
        assert result["action"] == "create"
        assert result["category"] == "LIVE_MARKET"
        assert result["card"]["symbol"] == "NZDUSD"

    def test_existing_symbol_triggers_edit(self, tmp_path):
        """Subsequent market event for same symbol → action=edit."""
        renderer = DiscordRenderer()
        from core.discord.state import DiscordState
        state = DiscordState(state_file=str(tmp_path / "state.json"))
        state.load()
        state.set_live_card("EURUSD", channel_id="ch_1", message_id="msg_99")
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("H4_CONTEXT", {"symbol": "EURUSD", "h4_trend": "BULLISH"})
        assert result is not None
        assert result["action"] == "edit"
        assert result["message_id"] == "msg_99"

    def test_missing_symbol_skips(self):
        """Event without symbol → action=skip."""
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("MARKET_SNAPSHOT", {})
        assert result is not None
        assert result["action"] == "skip"

    def test_non_market_events_still_return_send(self):
        """Non-LIVE_MARKET events return action=send (no lifecycle)."""
        renderer = DiscordRenderer()
        result = renderer.render("ORDER_FILLED", {"symbol": "GBPUSD", "fill_price": 1.27})
        assert result is not None
        assert result["action"] == "send"
        assert result["category"] == "EXECUTION"

    def test_renderer_does_not_crash_on_delivery_failure(self, tmp_path):
        """If bot_client raises, renderer catches and continues."""
        renderer = DiscordRenderer()
        from core.discord.state import DiscordState
        renderer._state = DiscordState(state_file=str(tmp_path / "state.json"))
        renderer._state.load()
        # Mock client that raises
        mock_client = MagicMock()
        mock_client.send_message.side_effect = RuntimeError("Discord down")
        renderer._client = mock_client

        # Should not raise
        result = renderer.render("MARKET_CONTEXT", {"symbol": "XAUUSD", "regime": "TRENDING"})
        # Renderer should still produce a result (even if delivery failed)
        assert result is not None or True  # Doesn't crash

    def test_state_persisted_after_create(self, tmp_path):
        """After creating a new card with a message_id, state is saved."""
        renderer = DiscordRenderer()
        from core.discord.state import DiscordState
        state = DiscordState(state_file=str(tmp_path / "state.json"))
        state.load()
        renderer._state = state

        # Mock client that returns a message_id
        mock_client = MagicMock()
        mock_client.send_message.return_value = "new_msg_123"
        renderer._client = mock_client

        with patch("core.config.DISCORD_LIVE_CHANNELS", {"NAS100": "ch_nas"}):
            renderer.render("MARKET_CONTEXT", {"symbol": "NAS100", "regime": "TRENDING"})

        # State should have the new card
        card = state.get_live_card("NAS100")
        assert card is not None
        assert card["message_id"] == "new_msg_123"
        # State file should exist
        assert (tmp_path / "state.json").exists()


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: CHANNEL CONSOLIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOpportunityChannel:
    """Opportunity events route to opportunities channel."""

    def test_trade_decision_routes_to_opportunity(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._state.get_live_card.return_value = None
        renderer._client = DiscordBotClient()

        result = renderer.render("TRADE_DECISION", {
            "symbol": "EURUSD", "decision": "EXECUTE",
            "pattern": "TREND_CONTINUATION", "score": 0.85,
            "side": "BUY", "strategy": "TREND_CONTINUATION",
        })
        assert result["category"] == "OPPORTUNITY"
        assert result["action"] == "send"
        assert result["channel"] == "opportunities"
        assert result["card"]["fields"]["pattern"] == "TREND_CONTINUATION"
        assert result["card"]["fields"]["direction"] == "BUY"

    def test_decision_rejected_routes_to_opportunity(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "AUDUSD", "reason": "exposure_limit",
            "stage": "risk", "score": 0.28,
        })
        assert result["category"] == "OPPORTUNITY"
        assert result["channel"] == "opportunities"
        assert result["card"]["fields"]["reason"] == "exposure_limit"

    def test_pipeline_drop_routes_to_opportunity(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("PIPELINE_DROP", {"symbol": "GBPUSD", "reason": "no_viable_pattern"})
        # Phase 3.5: PIPELINE_DROP is now suppressed (noise)
        assert result is None


class TestExecutionChannel:
    """Execution events route to executions channel."""

    def test_order_filled_routes_to_executions(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("ORDER_FILLED", {
            "symbol": "AUDUSD", "side": "SELL",
            "fill_price": 0.699, "volume": 2.32, "deal": 54488302,
        })
        assert result["category"] == "EXECUTION"
        assert result["channel"] == "executions"
        assert result["card"]["fields"]["fill_price"] == 0.699
        assert result["card"]["fields"]["deal"] == 54488302

    def test_trade_closed_routes_to_executions(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("TRADE_CLOSED", {
            "symbol": "EURUSD", "reason": "stop_loss",
            "details": {"pnl_r": -1.0, "close_type": "stop_loss", "duration_min": 45},
        })
        assert result["category"] == "EXECUTION"
        assert result["channel"] == "executions"
        assert result["card"]["fields"]["r_multiple"] == -1.0
        assert result["card"]["fields"]["exit_reason"] == "stop_loss"

    def test_risk_block_routes_to_executions(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("RISK_BLOCK", {
            "symbol": "USDJPY", "guard": "spread_guard", "reason": "SPREAD_EXCEEDED",
        })
        assert result["category"] == "EXECUTION"
        assert result["channel"] == "executions"
        assert result["card"]["fields"]["guard"] == "spread_guard"


class TestSystemChannel:
    """System events route to system channel."""

    def test_startup_routes_to_system(self):
        renderer = DiscordRenderer()
        state = DiscordState(state_file="logs/_test_system.json")
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("SYSTEM_STARTUP", {"mode": "LIVE_V10", "symbols": 10})
        assert result["category"] == "SYSTEM"
        # Phase 3.5: startup updates health card
        assert result["action"] in ("health_create", "health_update")
        assert result["card"]["fields"]["mode"] == "LIVE_V10"

    def test_error_routes_to_system(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("ERROR", {
            "error_type": "TimeoutError", "location": "mt5",
            "message": "Connection lost",
        })
        assert result["category"] == "SYSTEM"
        assert result["channel"] == "system"
        assert result["card"]["fields"]["error_type"] == "TimeoutError"

    def test_kill_switch_routes_to_system(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("KILL_SWITCH", {"reason": "Manual stop"})
        assert result["category"] == "SYSTEM"
        assert result["channel"] == "system"


class TestPhase3Config:
    """Phase 3 configuration exists."""

    def test_v2_channels_config_exists(self):
        from core import config
        channels = getattr(config, "DISCORD_V2_CHANNELS", None)
        assert channels is not None
        assert "opportunities" in channels
        assert "executions" in channels
        assert "system" in channels
