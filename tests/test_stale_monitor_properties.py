"""Property-based tests for StaleDataMonitor using Hypothesis."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, strategies as st, assume
from core.stale_monitor import StaleDataMonitor, StaleCheckResult


class _MockConfig:
    STALE_TICK_TIMEOUT_SECONDS = 30.0
    STALE_CANDLE_TIMEOUT_SECONDS = 600.0
    MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0
    STALE_ESCALATION_WARNING_SECONDS = 60.0
    STALE_ESCALATION_CRITICAL_SECONDS = 300.0


def _make_monitor() -> StaleDataMonitor:
    return StaleDataMonitor("TEST", _MockConfig())


# 5.1: Fresh tick always updates last_tick_time and clears stale state
# **Validates: Requirements 1.2, 2.3, 5.6**
@given(t1=st.integers(1000, 2000000000), t2=st.integers(1000, 2000000000))
def test_fresh_tick_updates_state(t1: int, t2: int):
    assume(t2 > t1)
    m = _make_monitor()
    m.on_tick(t1, 100.0)
    m.on_tick(t2, 200.0)
    assert m.last_tick_time == t2
    assert m.stale_state is False


# 5.2: Stale tick never updates last_tick_time
# **Validates: Requirements 2.1**
@given(t1=st.integers(1000, 2000000000), t2=st.integers(1000, 2000000000))
def test_stale_tick_never_updates(t1: int, t2: int):
    assume(t2 <= t1)
    m = _make_monitor()
    m.on_tick(t1, 100.0)
    m.on_tick(t2, 200.0)
    assert m.last_tick_time == t1


# 5.3: Escalation level is pure function of duration
# **Validates: Requirements 5.1, 5.2, 5.3**
@given(duration=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
def test_escalation_pure_function(duration: float):
    m = _make_monitor()
    level = m._compute_escalation(duration)
    if duration >= 300.0:
        assert level == 3
    elif duration >= 60.0:
        assert level == 2
    elif duration > 0:
        assert level == 1
    else:
        assert level == 0


# 5.4: Fresh data always resets escalation to level 0
# **Validates: Requirements 5.6**
@given(t1=st.integers(1000, 2000000000), t2=st.integers(1000, 2000000000))
def test_fresh_data_resets_escalation(t1: int, t2: int):
    assume(t2 > t1)
    m = _make_monitor()
    m.on_tick(t1, 100.0)
    # Force stale: second call sets stale_since=101, third call at 132 exceeds grace
    m.on_tick(t1, 101.0)
    m.on_tick(t1, 132.0)  # 31s from stale_since → stale
    assert m.stale_state is True
    # Fresh tick resets
    m.on_tick(t2, 133.0)
    assert m._escalation_level == 0
    assert m.stale_state is False


# 5.5: Heartbeat loss triggers force_disconnect when elapsed > timeout
# **Validates: Requirements 4.1, 4.2**
@given(elapsed=st.floats(min_value=121.0, max_value=10000.0, allow_nan=False, allow_infinity=False))
def test_heartbeat_loss_triggers_disconnect(elapsed: float):
    m = _make_monitor()
    m.on_tick(1000, 100.0)  # Set last_data_update_time
    result = m.check_heartbeat(100.0 + elapsed)
    assert result.action == "force_disconnect"
    assert result.is_stale is True
    assert result.escalation_level == 3


# 5.6: Candle staleness only escalates after timeout threshold exceeded
# **Validates: Requirements 3.2, 3.3**
@given(elapsed=st.floats(min_value=0.0, max_value=599.0, allow_nan=False, allow_infinity=False))
def test_candle_no_escalation_within_timeout(elapsed: float):
    m = _make_monitor()
    m.on_candle(1000, 100.0)  # Initial candle
    result = m.on_candle(1000, 100.0 + elapsed)  # Same candle, within timeout
    assert result.action == "none"
    assert result.is_stale is False
