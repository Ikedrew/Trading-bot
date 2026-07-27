"""
Unit tests for TickMonitor — tick freshness evaluation.

Tests:
    - Fresh tick returns valid=True
    - Stale tick returns valid=False, stale=True
    - Monitor exception returns valid=False, error=True
    - FRESH→STALE transition emits feed_health + risk_guard
    - STALE→FRESH recovery emits feed_health
    - Never raises
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.tick_monitor import TickMonitor, TickMonitorResult


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_stale_monitor(stale_state=False, on_tick_result=None, on_tick_error=None):
    monitor = MagicMock()
    monitor.stale_state = stale_state
    monitor.last_tick_time = time.time() - 5
    monitor._tick_stale_since = None
    monitor.stale_tick_timeout = 60

    if on_tick_error:
        monitor.on_tick.side_effect = on_tick_error
    else:
        result = on_tick_result or MagicMock(is_stale=False)
        monitor.on_tick.return_value = result
    return monitor


def _make_stale_tick_result(stale=True, duration=120.0, escalation=1, action="SKIP"):
    r = MagicMock()
    r.is_stale = stale
    r.stale_duration_seconds = duration
    r.escalation_level = escalation
    r.action = action
    return r


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestFreshTick:
    """Fresh tick returns valid=True."""

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_fresh_tick_valid(self, mock_feed, mock_risk):
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(
            stale_state=False,
            on_tick_result=_make_stale_tick_result(stale=False),
        )

        result = monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time(),
        )

        assert result.valid is True
        assert result.stale is False
        assert result.error is False
        mock_feed.assert_not_called()
        mock_risk.assert_not_called()


class TestStaleTick:
    """Stale tick returns valid=False, stale=True."""

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_stale_tick_invalid(self, mock_feed, mock_risk):
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(
            stale_state=False,  # Was fresh before
            on_tick_result=_make_stale_tick_result(stale=True, duration=90.0),
        )

        result = monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time() - 90,
        )

        assert result.valid is False
        assert result.stale is True

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_fresh_to_stale_transition_emits_events(self, mock_feed, mock_risk):
        """FRESH→STALE transition emits feed_health + risk_guard_result."""
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(
            stale_state=False,  # Was fresh
            on_tick_result=_make_stale_tick_result(stale=True, duration=65.0),
        )

        monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time() - 65,
        )

        mock_feed.assert_called_once()
        call_args = mock_feed.call_args
        assert call_args[0][1]["transition"] == "FRESH_TO_STALE"
        mock_risk.assert_called_once()

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_already_stale_no_transition_events(self, mock_feed, mock_risk):
        """If already stale (not a transition), no feed_health/risk events."""
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(
            stale_state=True,  # Was already stale
            on_tick_result=_make_stale_tick_result(stale=True, duration=200.0, escalation=1),
        )

        monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time() - 200,
        )

        mock_feed.assert_not_called()
        mock_risk.assert_not_called()


class TestRecovery:
    """STALE→FRESH recovery emits feed_health."""

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_stale_to_fresh_emits_recovery(self, mock_feed, mock_risk):
        monitor = TickMonitor()
        stale_mon = MagicMock()
        # stale_state: True on first read (_was_stale), False on second read (recovery check)
        type(stale_mon).stale_state = PropertyMock(side_effect=[True, False])
        stale_mon.last_tick_time = 1700000000
        stale_mon._tick_stale_since = None
        stale_mon.stale_tick_timeout = 60
        stale_mon.on_tick.return_value = _make_stale_tick_result(stale=False)

        monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time(),
        )

        mock_feed.assert_called_once()
        call_args = mock_feed.call_args
        assert call_args[0][1]["transition"] == "STALE_TO_FRESH"


class TestMonitorError:
    """Monitor exception returns valid=False, error=True."""

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health")
    def test_on_tick_error_returns_invalid(self, mock_feed, mock_risk):
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(on_tick_error=RuntimeError("crash"))

        result = monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time(),
        )

        assert result.valid is False
        assert result.error is True


class TestNeverRaises:
    """TickMonitor handles emit_feed_health failures gracefully."""

    @patch("core.runtime.tick_monitor.emit_risk_guard_result")
    @patch("core.runtime.tick_monitor.emit_feed_health", side_effect=RuntimeError("feed crash"))
    def test_feed_health_failure_still_returns(self, mock_feed, mock_risk):
        """emit_feed_health failure doesn't prevent result."""
        monitor = TickMonitor()
        stale_mon = _make_stale_monitor(
            stale_state=False,
            on_tick_result=_make_stale_tick_result(stale=True, duration=70.0),
        )

        result = monitor.evaluate(
            symbol="EURUSD",
            stale_monitor=stale_mon,
            tick_time=time.time(),
        )

        assert result.valid is False
        assert result.stale is True
