"""
Unit tests for HealthMonitor — cycle-level health observation.

Tests:
    - tick executes without error
    - heartbeat file is written
    - no-trade alert fires at threshold
    - no-trade counter resets on trade
    - stall detection triggers at threshold
    - failures do not affect runtime flow
    - method returns None
    - MT5 state is passed correctly
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.health_monitor import HealthMonitor


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_config(**overrides):
    """Build a mock config with sensible defaults."""
    cfg = MagicMock()
    cfg.NO_TRADE_ALERT_THRESHOLD = overrides.get("no_trade_threshold", 100)
    cfg.NO_TRADE_ALERT_REPEAT_INTERVAL = overrides.get("no_trade_repeat", 25)
    cfg.LIVENESS_STALL_THRESHOLD_SECONDS = overrides.get("stall_threshold", 10.0)
    cfg._discord_logger = overrides.get("discord_logger", None)
    return cfg


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestTickBasics:
    """tick() executes without error and returns None."""

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_tick_returns_none(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=3, config=_make_config())
        result = monitor.tick(cycle_id=1, cycle_latency_s=0.5, mt5_state="CONNECTED", cycle_had_trade=False)
        assert result is None

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_tick_calls_log_heartbeat(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=2, config=_make_config())
        monitor.tick(cycle_id=5, cycle_latency_s=0.3, mt5_state="CONNECTED", cycle_had_trade=True)
        mock_hb.assert_called_once()
        args = mock_hb.call_args[0]
        assert args[0] == 5  # cycle_id
        assert args[2] == "ALL"
        assert args[3] == "CONNECTED"

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_mt5_state_passed_correctly(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=1, config=_make_config())
        monitor.tick(cycle_id=1, cycle_latency_s=0.1, mt5_state="DISCONNECTED", cycle_had_trade=False)
        args = mock_hb.call_args[0]
        assert args[3] == "DISCONNECTED"


class TestNoTradeAlert:
    """No-trade alert tracking and firing."""

    @patch("core.runtime.health_monitor.emit_quiet_period_alert")
    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_counter_increments_on_no_trade(self, mock_liveness, mock_hb, mock_alert):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(no_trade_threshold=5))
        for _ in range(3):
            monitor.tick(cycle_id=1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        assert monitor.consecutive_no_trade_cycles == 3
        mock_alert.assert_not_called()

    @patch("core.runtime.health_monitor.emit_quiet_period_alert")
    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_counter_resets_on_trade(self, mock_liveness, mock_hb, mock_alert):
        monitor = HealthMonitor(n_symbols=1, config=_make_config())
        for _ in range(10):
            monitor.tick(cycle_id=1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        assert monitor.consecutive_no_trade_cycles == 10
        monitor.tick(cycle_id=11, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=True)
        assert monitor.consecutive_no_trade_cycles == 0

    @patch("core.runtime.health_monitor.emit_quiet_period_alert")
    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_alert_fires_at_threshold(self, mock_liveness, mock_hb, mock_alert):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(no_trade_threshold=5, no_trade_repeat=3))
        for i in range(5):
            monitor.tick(cycle_id=i+1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        mock_alert.assert_called_once_with(5)

    @patch("core.runtime.health_monitor.emit_quiet_period_alert")
    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_alert_repeats_at_interval(self, mock_liveness, mock_hb, mock_alert):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(no_trade_threshold=3, no_trade_repeat=2))
        for i in range(7):
            monitor.tick(cycle_id=i+1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        # Should fire at: 3 (threshold), 4 (3+1, but 4%2==0 → yes), 5 (5%2!=0 → no), 6 (6%2==0 → yes)
        # Actually: fires when >= threshold AND (==threshold OR %repeat==0)
        # cycle 3: count=3, ==threshold → fires
        # cycle 4: count=4, 4%2==0 → fires
        # cycle 5: count=5, 5%2!=0 → no
        # cycle 6: count=6, 6%2==0 → fires
        # cycle 7: count=7, 7%2!=0 → no
        assert mock_alert.call_count == 3
        # count=3: >=3 and (==3) → YES
        # count=4: >=3 and (4==3? no, 4%2==0? yes) → YES
        # count=5: >=3 and (5==3? no, 5%2!=0) → NO
        # count=6: >=3 and (6==3? no, 6%2==0? yes) → YES
        # count=7: >=3 and (7==3? no, 7%2!=0) → NO
        assert mock_alert.call_count == 3


class TestStallDetection:
    """Stall detection via log_liveness_status."""

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_normal_latency_reports_ok(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(stall_threshold=10.0))
        monitor.tick(cycle_id=1, cycle_latency_s=2.0, mt5_state="CONNECTED", cycle_had_trade=False)
        mock_liveness.assert_called_once_with("OK", 2.0, 1)

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_high_latency_reports_stalled(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(stall_threshold=5.0))
        monitor.tick(cycle_id=10, cycle_latency_s=8.0, mt5_state="CONNECTED", cycle_had_trade=False)
        mock_liveness.assert_called_once_with("STALLED", 8.0, 10)


class TestWriteHeartbeat:
    """write_heartbeat writes an atomic JSON file."""

    def test_writes_heartbeat_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monitor = HealthMonitor(n_symbols=2, config=_make_config())
        monitor.write_heartbeat("alive", 42, 150, "CONNECTED")

        hb_path = tmp_path / "logs" / "heartbeat.json"
        assert hb_path.exists()
        data = json.loads(hb_path.read_text())
        assert data["cycle_id"] == 42
        assert data["status"] == "alive"
        assert data["latency_ms"] == 150
        assert data["symbols"] == 2
        assert data["mt5_state"] == "CONNECTED"

    def test_write_heartbeat_never_raises(self, monkeypatch):
        """Even with broken filesystem, write_heartbeat does not raise."""
        monitor = HealthMonitor(n_symbols=1, config=_make_config())
        # Patch os.makedirs to simulate filesystem failure
        with patch("os.makedirs", side_effect=OSError("disk full")):
            # Should not raise
            monitor.write_heartbeat("alive", 1, 0, "CONNECTED")


class TestFailureIsolation:
    """Failures in health monitoring do not propagate."""

    @patch("core.runtime.health_monitor.log_heartbeat", side_effect=RuntimeError("crash"))
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_tick_never_raises_on_heartbeat_failure(self, mock_liveness, mock_hb):
        monitor = HealthMonitor(n_symbols=1, config=_make_config())
        # Should not raise despite log_heartbeat crashing
        result = monitor.tick(cycle_id=1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        assert result is None

    @patch("core.runtime.health_monitor.emit_quiet_period_alert", side_effect=RuntimeError("alert crash"))
    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_tick_never_raises_on_alert_failure(self, mock_liveness, mock_hb, mock_alert):
        monitor = HealthMonitor(n_symbols=1, config=_make_config(no_trade_threshold=1))
        # Should not raise despite alert crashing
        result = monitor.tick(cycle_id=1, cycle_latency_s=0.1, mt5_state="CONNECTED", cycle_had_trade=False)
        assert result is None


class TestDiscordHeartbeat:
    """Discord heartbeat is throttled to every 10 cycles."""

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_discord_fires_at_cycle_10(self, mock_liveness, mock_hb):
        discord = MagicMock()
        cfg = _make_config(discord_logger=discord)
        monitor = HealthMonitor(n_symbols=2, config=cfg)
        monitor.tick(cycle_id=10, cycle_latency_s=0.5, mt5_state="CONNECTED", cycle_had_trade=False)
        discord.event.assert_called_once()
        call_args = discord.event.call_args[0]
        assert call_args[0] == "HEARTBEAT"

    @patch("core.runtime.health_monitor.log_heartbeat")
    @patch("core.runtime.health_monitor.log_liveness_status")
    def test_discord_does_not_fire_at_cycle_7(self, mock_liveness, mock_hb):
        discord = MagicMock()
        cfg = _make_config(discord_logger=discord)
        monitor = HealthMonitor(n_symbols=2, config=cfg)
        monitor.tick(cycle_id=7, cycle_latency_s=0.5, mt5_state="CONNECTED", cycle_had_trade=False)
        discord.event.assert_not_called()
