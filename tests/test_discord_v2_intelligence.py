"""Discord V2 Phase 3.5 — Intelligence Layer Tests.

Proves:
1. Market state accumulates across partial events
2. Opportunity filtering suppresses noise
3. System health card edits on heartbeat/startup
4. Noisy events are suppressed
5. Channel rename is consistent
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.discord.state import DiscordState
from core.discord.bot_client import DiscordBotClient
from core.discord.renderer import DiscordRenderer, Category


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE 1: MARKET STATE ACCUMULATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarketStateAccumulation:
    """Partial events merge into full accumulated state."""

    def test_first_event_creates_state(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        merged = state.merge_market_state("EURUSD", {"regime": "RANGING", "h4_trend": "NEUTRAL"})
        assert merged["regime"] == "RANGING"
        assert merged["h4_trend"] == "NEUTRAL"

    def test_second_event_preserves_existing_fields(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.merge_market_state("EURUSD", {"regime": "RANGING", "h4_trend": "NEUTRAL"})
        # Second event only has strategy — previous fields preserved
        merged = state.merge_market_state("EURUSD", {"strategy": "MEAN_REVERSION"})
        assert merged["regime"] == "RANGING"
        assert merged["h4_trend"] == "NEUTRAL"
        assert merged["strategy"] == "MEAN_REVERSION"

    def test_empty_fields_do_not_overwrite(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.merge_market_state("AUDUSD", {"regime": "TRENDING", "h4_trend": "BULLISH"})
        # Event with empty string should NOT overwrite
        merged = state.merge_market_state("AUDUSD", {"regime": "", "entry_status": "WAITING"})
        assert merged["regime"] == "TRENDING"  # Preserved
        assert merged["entry_status"] == "WAITING"  # Added

    def test_zero_values_do_not_overwrite(self, tmp_path):
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.merge_market_state("GBPUSD", {"range_position": 0.85})
        merged = state.merge_market_state("GBPUSD", {"range_position": 0.0})
        assert merged["range_position"] == 0.85  # Preserved

    def test_renderer_uses_accumulated_state(self, tmp_path):
        from unittest.mock import patch
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient()

        # Renderer now reads from live_market_state snapshot
        snapshot = {
            "symbol": "EURUSD",
            "market": {"regime": "RANGING", "h4_trend": "NEUTRAL"},
            "opportunity": {},
            "strategy": {"family": "MEAN_REVERSION"},
            "entry": {"status": "READY"},
            "risk": {},
        }
        with patch("core.discord.renderer.read_live_market_state", return_value=snapshot):
            result = renderer.render("MARKET_CONTEXT", {"symbol": "EURUSD"})

        fields = result["card"]["fields"]
        assert fields["regime"] == "RANGING"
        assert fields["h4_trend"] == "NEUTRAL"
        assert fields["strategy"] == "MEAN_REVERSION"
        assert fields["entry_status"] == "READY"


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE 2: OPPORTUNITY FILTERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestOpportunityFiltering:
    """Only strategy-matched-or-deeper decisions reach Discord."""

    def test_execute_decision_passes(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._state.get_live_card.return_value = None
        renderer._client = DiscordBotClient()

        result = renderer.render("TRADE_DECISION", {
            "symbol": "EURUSD", "decision": "EXECUTE", "action": "EXECUTE",
            "pattern": "TREND_CONTINUATION", "score": 0.8,
        })
        assert result is not None
        assert result["category"] == "OPPORTUNITY"

    def test_pipeline_drop_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("PIPELINE_DROP", {"symbol": "AUDUSD", "reason": "no_patterns"})
        assert result is None  # Suppressed

    def test_early_rejection_at_opportunity_stage_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "GBPUSD", "reason": "opportunity_invalid",
            "stage": "opportunity",
        })
        assert result is None  # Suppressed

    def test_early_rejection_at_strategy_stage_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "USDJPY", "reason": "no_strategy",
            "stage": "strategy_classification",
        })
        assert result is None  # Suppressed

    def test_rejection_at_risk_stage_passes(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "EURUSD", "reason": "exposure_limit",
            "stage": "risk",
        })
        assert result is not None
        assert result["category"] == "OPPORTUNITY"
        assert result["channel"] == "opportunities"

    def test_rejection_at_execution_stage_passes(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "AUDUSD", "reason": "entry_not_ready",
            "stage": "execution",
        })
        assert result is not None
        assert result["category"] == "OPPORTUNITY"

    def test_rejection_with_terminal_stage_field(self):
        """Supports both 'stage' and 'terminal_stage' field names."""
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()

        result = renderer.render("DECISION_REJECTED", {
            "symbol": "NZDUSD", "reason": "RR too low",
            "terminal_stage": "risk",
        })
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE 3: SYSTEM HEALTH CARD
# ═══════════════════════════════════════════════════════════════════════════════


class TestSystemHealthCard:
    """System health events edit a persistent card instead of spamming."""

    def test_heartbeat_creates_health_card(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("HEARTBEAT", {"cycle": 100, "latency_ms": 50})
        assert result is not None
        assert result["action"] == "health_create"
        assert result["category"] == "SYSTEM"

    def test_heartbeat_edits_existing_health_card(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.set_system_health(channel_id="ch_sys", message_id="msg_health")
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("HEARTBEAT", {"cycle": 200, "latency_ms": 30})
        assert result is not None
        assert result["action"] == "health_update"
        assert result["message_id"] == "msg_health"

    def test_startup_updates_health_card(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.set_system_health(channel_id="ch_sys", message_id="msg_h")
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("SYSTEM_STARTUP", {"mode": "LIVE_V10", "symbols": 10})
        assert result["action"] == "health_update"

    def test_shutdown_updates_health_card(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.set_system_health(channel_id="c", message_id="m")
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("SYSTEM_SHUTDOWN", {"reason": "Manual stop"})
        assert result["action"] == "health_update"

    def test_error_sends_new_message(self, tmp_path):
        """Errors are critical — always new message, not health card edit."""
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        state.set_system_health(channel_id="c", message_id="m")
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("ERROR", {"error_type": "TimeoutError", "message": "fail"})
        assert result["action"] == "send"
        assert result["channel"] == "system"

    def test_kill_switch_sends_new_message(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("KILL_SWITCH", {"reason": "Manual"})
        assert result["action"] == "send"

    def test_daily_report_sends_new_message(self, tmp_path):
        renderer = DiscordRenderer()
        state = DiscordState(state_file=str(tmp_path / "s.json"))
        state.load()
        renderer._state = state
        renderer._client = DiscordBotClient()

        result = renderer.render("DAILY_REPORT", {"wins": 3, "losses": 1})
        assert result["action"] == "send"


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE 4: NOISE REMOVAL
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoiseRemoval:
    """Noisy events are suppressed from Discord but remain in S3."""

    def test_exposure_update_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()
        result = renderer.render("EXPOSURE_UPDATE", {"total": 2.5})
        assert result is None

    def test_risk_check_approved_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()
        result = renderer.render("RISK_CHECK", {"result": "APPROVED", "guard": "spread"})
        assert result is None

    def test_risk_block_not_suppressed(self):
        """RISK_BLOCK is meaningful — should pass to executions."""
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()
        result = renderer.render("RISK_BLOCK", {"symbol": "EURUSD", "guard": "daily_limit"})
        assert result is not None
        assert result["category"] == "EXECUTION"

    def test_order_filled_not_suppressed(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()
        result = renderer.render("ORDER_FILLED", {"symbol": "AUDUSD", "fill_price": 0.7})
        assert result is not None
        assert result["category"] == "EXECUTION"


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE 5: CHANNEL RENAME
# ═══════════════════════════════════════════════════════════════════════════════


class TestChannelRename:
    """Channel is renamed from opportunity_feed to opportunities."""

    def test_config_has_opportunities_key(self):
        from core import config
        channels = getattr(config, "DISCORD_V2_CHANNELS", {})
        assert "opportunities" in channels
        assert "opportunity_feed" not in channels

    def test_renderer_routes_to_opportunities_channel(self):
        renderer = DiscordRenderer()
        renderer._state = MagicMock()
        renderer._client = DiscordBotClient()
        result = renderer.render("TRADE_DECISION", {
            "symbol": "EURUSD", "decision": "EXECUTE", "action": "EXECUTE",
        })
        assert result["channel"] == "opportunities"
