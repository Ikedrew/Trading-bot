"""
Tests for F1: External Alerting System.

Covers:
- AlertEvent structured creation
- JSON serialization (API-ready)
- Discord formatting (rich)
- Email formatting (concise)
- Throttling (per-level cooldowns)
- EMERGENCY never throttled
- Channel failure isolation
- Non-blocking dispatch
- LogChannel always works
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alerting import (
    AlertEvent,
    AlertLevel,
    LogChannel,
    DiscordWebhookChannel,
    send_alert,
    initialize_alerting,
    _is_throttled,
    _throttle_state,
    _channels,
)


@pytest.fixture(autouse=True)
def reset_alerting():
    """Reset alerting state between tests."""
    import core.alerting as mod
    mod._initialized = False
    mod._channels = []
    _throttle_state.clear()
    yield
    mod._initialized = False
    mod._channels = []
    _throttle_state.clear()


# --- TEST: STRUCTURED EVENT ---------------------------------------------------

class TestAlertEvent:
    def test_to_dict_is_json_serializable(self):
        """Event converts to JSON-safe dict."""
        import json
        event = AlertEvent(
            level=AlertLevel.CRITICAL,
            event_type="DRAWDOWN_BREACH",
            message="Drawdown exceeded",
            timestamp="2026-06-06T14:30:00+00:00",
            unix_time=1717682000.0,
            symbol="EURUSD",
            metrics={"drawdown": 3.8, "limit": 3.5},
            state_snapshot={"open_positions": 4},
        )
        d = event.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert "DRAWDOWN_BREACH" in serialized
        assert d["level"] == "CRITICAL"
        assert d["metrics"]["drawdown"] == 3.8

    def test_to_discord_rich_format(self):
        """Discord format includes structured sections."""
        event = AlertEvent(
            level=AlertLevel.WARNING,
            event_type="STALE_FEED",
            message="Feed frozen",
            timestamp="2026-06-06T14:30:00+00:00",
            unix_time=1717682000.0,
            symbol="GBPUSD",
            metrics={"stale_seconds": 45.0},
        )
        text = event.to_discord()
        assert "**[WARNING] STALE_FEED**" in text
        assert "GBPUSD" in text
        assert "stale_seconds" in text

    def test_to_email_concise(self):
        """Email format is short (under 10 lines)."""
        event = AlertEvent(
            level=AlertLevel.CRITICAL,
            event_type="DAILY_LOSS",
            message="Daily loss limit hit",
            timestamp="2026-06-06T14:30:00+00:00",
            unix_time=1717682000.0,
            symbol="EURUSD",
            metrics={"loss_pct": 4.2, "limit": 4.0},
        )
        body = event.to_email_body()
        lines = body.strip().split("\n")
        assert len(lines) <= 10
        subject = event.to_email_subject()
        assert "CRITICAL" in subject
        assert "DAILY_LOSS" in subject


# --- TEST: THROTTLING ----------------------------------------------------------

class TestThrottling:
    def test_first_event_not_throttled(self):
        """First occurrence of an event is never throttled."""
        event = AlertEvent(
            level=AlertLevel.WARNING, event_type="TEST",
            message="x", timestamp="", unix_time=time.time(),
        )
        assert _is_throttled(event) is False

    def test_rapid_repeat_throttled(self):
        """Same event type repeated immediately is throttled."""
        event = AlertEvent(
            level=AlertLevel.WARNING, event_type="TEST_REPEAT",
            message="x", timestamp="", unix_time=time.time(),
        )
        _is_throttled(event)  # First: not throttled (sets timestamp)
        assert _is_throttled(event) is True  # Second: throttled

    def test_emergency_never_throttled(self):
        """EMERGENCY level bypasses all throttling."""
        event = AlertEvent(
            level=AlertLevel.EMERGENCY, event_type="EMERGENCY_TEST",
            message="x", timestamp="", unix_time=time.time(),
        )
        assert _is_throttled(event) is False
        assert _is_throttled(event) is False  # Still not throttled
        assert _is_throttled(event) is False  # Never throttled


# --- TEST: CHANNEL ISOLATION --------------------------------------------------

class TestChannelIsolation:
    def test_log_channel_always_succeeds(self):
        """LogChannel always returns True."""
        ch = LogChannel()
        event = AlertEvent(
            level=AlertLevel.INFO, event_type="TEST",
            message="hi", timestamp="", unix_time=time.time(),
        )
        assert ch.send(event) is True

    def test_discord_failure_returns_false(self):
        """Discord channel failure returns False (no crash)."""
        ch = DiscordWebhookChannel("https://invalid.example.com/webhook")
        event = AlertEvent(
            level=AlertLevel.CRITICAL, event_type="TEST",
            message="hi", timestamp="", unix_time=time.time(),
        )
        # Will fail (invalid URL / no network in test) — should not raise
        result = ch.send(event)
        assert result is False

    def test_empty_webhook_returns_false(self):
        """Empty webhook URL ? channel disabled."""
        ch = DiscordWebhookChannel("")
        event = AlertEvent(
            level=AlertLevel.CRITICAL, event_type="TEST",
            message="hi", timestamp="", unix_time=time.time(),
        )
        assert ch.send(event) is False


# --- TEST: SEND_ALERT INTEGRATION ---------------------------------------------

class TestSendAlert:
    def test_send_alert_does_not_raise(self):
        """send_alert never raises regardless of channel state."""
        # No channels configured, no crash
        send_alert(
            level=AlertLevel.CRITICAL,
            event_type="TEST",
            message="test alert",
            symbol="EURUSD",
            metrics={"value": 42},
        )
        # Give thread time to execute
        time.sleep(0.1)

    def test_send_alert_dispatches_to_log(self, caplog):
        """send_alert dispatches to LogChannel (always present)."""
        import logging
        with caplog.at_level(logging.INFO):
            initialize_alerting()
            # Clear throttle for this test
            _throttle_state.clear()
            send_alert(
                level=AlertLevel.CRITICAL,
                event_type="DISPATCH_TEST",
                message="should appear in log",
            )
            time.sleep(0.2)  # Wait for thread

        assert any("DISPATCH_TEST" in r.message for r in caplog.records)
