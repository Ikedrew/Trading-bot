"""
Unit tests for RuntimeStateClassifier — gap detection and classification.

Tests:
    - No gap on first cycle (cycle_id=1)
    - No gap when time delta < 60s
    - EVENT_LOOP_STALL when gap 60-300s with MT5 connected
    - HOST_SUSPEND when gap > 300s with MT5 connected
    - MT5_DISCONNECT when MT5 is not connected
    - Returns RuntimeGapEvent with correct metadata
    - Never raises
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.runtime_state_classifier import RuntimeStateClassifier, RuntimeGapEvent


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestNoGapDetected:
    """Normal conditions — no gap."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_first_cycle_no_gap(self, mock_emit):
        classifier = RuntimeStateClassifier()
        result = classifier.check_gap(
            cycle_id=1, cycle_start=time.time(),
            mt5_state="CONNECTED", config=MagicMock(),
        )
        assert result is None
        mock_emit.assert_not_called()

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_normal_interval_no_gap(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        # First cycle
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        # Second cycle 5 seconds later (normal)
        result = classifier.check_gap(cycle_id=2, cycle_start=t0 + 5, mt5_state="CONNECTED", config=MagicMock())
        assert result is None
        mock_emit.assert_not_called()


class TestEventLoopStall:
    """Gap 60-300s with MT5 connected → EVENT_LOOP_STALL."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_event_loop_stall(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        # 120 second gap
        result = classifier.check_gap(cycle_id=2, cycle_start=t0 + 120, mt5_state="CONNECTED", config=MagicMock())
        assert result is not None
        assert result.gap_type == "EVENT_LOOP_STALL"
        assert result.gap_minutes == 2
        assert result.last_cycle == 1
        assert result.resumed_cycle == 2


class TestHostSuspend:
    """Gap > 300s with MT5 connected → HOST_SUSPEND."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_host_suspend(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        # 600 second gap
        result = classifier.check_gap(cycle_id=2, cycle_start=t0 + 600, mt5_state="CONNECTED", config=MagicMock())
        assert result is not None
        assert result.gap_type == "HOST_SUSPEND"
        assert result.gap_minutes == 10


class TestMT5Disconnect:
    """Gap > 60s with MT5 disconnected → MT5_DISCONNECT."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_mt5_disconnect(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        # 90 second gap with MT5 disconnected
        result = classifier.check_gap(cycle_id=2, cycle_start=t0 + 90, mt5_state="DISCONNECTED", config=MagicMock())
        assert result is not None
        assert result.gap_type == "MT5_DISCONNECT"


class TestEmitBehaviour:
    """Events are emitted on gap detection."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_emit_system_health_called(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        classifier.check_gap(cycle_id=2, cycle_start=t0 + 120, mt5_state="CONNECTED", config=MagicMock())
        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert call_args[0][0]["incident_type"] == "EVENT_LOOP_STALL"

    @patch("core.runtime.runtime_state_classifier.emit_system_health")
    def test_discord_notified(self, mock_emit):
        discord = MagicMock()
        cfg = MagicMock()
        cfg._discord_logger = discord

        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=cfg)
        classifier.check_gap(cycle_id=2, cycle_start=t0 + 120, mt5_state="CONNECTED", config=cfg)

        discord.event.assert_called_once()


class TestNeverRaises:
    """Classifier never raises regardless of input."""

    @patch("core.runtime.runtime_state_classifier.emit_system_health", side_effect=RuntimeError("crash"))
    def test_emit_failure_does_not_raise(self, mock_emit):
        classifier = RuntimeStateClassifier()
        t0 = time.time()
        classifier.check_gap(cycle_id=1, cycle_start=t0, mt5_state="CONNECTED", config=MagicMock())
        # Should not raise despite emit_system_health crashing
        result = classifier.check_gap(cycle_id=2, cycle_start=t0 + 120, mt5_state="CONNECTED", config=MagicMock())
        assert result is not None
