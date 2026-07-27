"""Unit tests for StaleDataMonitor."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stale_monitor import StaleDataMonitor, StaleCheckResult


class _MockConfig:
    STALE_TICK_TIMEOUT_SECONDS = 30.0
    STALE_CANDLE_TIMEOUT_SECONDS = 600.0
    MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0
    STALE_ESCALATION_WARNING_SECONDS = 60.0
    STALE_ESCALATION_CRITICAL_SECONDS = 300.0


class _CustomConfig:
    STALE_TICK_TIMEOUT_SECONDS = 10.0
    STALE_CANDLE_TIMEOUT_SECONDS = 120.0
    MARKET_HEARTBEAT_TIMEOUT_SECONDS = 30.0
    STALE_ESCALATION_WARNING_SECONDS = 15.0
    STALE_ESCALATION_CRITICAL_SECONDS = 60.0


# 6.1: Initialization sets all fields to expected defaults
def test_initialization_defaults():
    m = StaleDataMonitor("EURUSD", _MockConfig())
    assert m.symbol == "EURUSD"
    assert m.last_tick_time is None
    assert m.last_candle_time is None
    assert m.last_data_update_time is None
    assert m.stale_state is False
    assert m._escalation_level == 0
    assert m.stale_tick_timeout == 30.0
    assert m.stale_candle_timeout == 600.0
    assert m.heartbeat_timeout == 120.0


# 6.2: Config parameter reading with custom values
def test_custom_config():
    m = StaleDataMonitor("GBPUSD", _CustomConfig())
    assert m.stale_tick_timeout == 10.0
    assert m.stale_candle_timeout == 120.0
    assert m.heartbeat_timeout == 30.0
    assert m.escalation_warning == 15.0
    assert m.escalation_critical == 60.0


# 6.3: StaleCheckResult structure and API contract
def test_stale_check_result_structure():
    r = StaleCheckResult(is_stale=True, escalation_level=2, stale_duration_seconds=75.0, action="log_escalation")
    assert r.is_stale is True
    assert r.escalation_level == 2
    assert r.stale_duration_seconds == 75.0
    assert r.action == "log_escalation"


# 6.4: Monitor methods handle first call gracefully
def test_on_tick_handles_none_gracefully():
    m = StaleDataMonitor("TEST", _MockConfig())
    # First call with valid data
    result = m.on_tick(1000, 100.0)
    assert result.is_stale is False


# 6.5: Escalation level 3 returns force_disconnect action
def test_escalation_level_3_forces_disconnect():
    m = StaleDataMonitor("TEST", _MockConfig())
    m.on_tick(1000, 100.0)
    # Second call: sets _tick_stale_since = 101.0
    m.on_tick(1000, 101.0)
    # Need duration >= 30s grace + 300s critical = 330s from stale_since
    # wall_clock = 101 + 331 = 432
    result = m.on_tick(1000, 432.0)
    assert result.action == "force_disconnect"
    assert result.escalation_level == 3


# 6.6: Recovery — fresh data after stale period resets tracking
def test_recovery_resets_stale_state():
    m = StaleDataMonitor("TEST", _MockConfig())
    m.on_tick(1000, 100.0)
    # Second call sets stale_since=101
    m.on_tick(1000, 101.0)
    # Third call: duration=31s, past grace → stale
    m.on_tick(1000, 132.0)
    assert m.stale_state is True
    # Recover with fresh tick
    m.on_tick(2000, 133.0)
    assert m.stale_state is False
    assert m._escalation_level == 0
    assert m.last_tick_time == 2000
