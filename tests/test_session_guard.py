"""
Tests for C5: Session Guard — Hard Trading Hours Gate.

Covers:
- Within trading hours → allowed
- Outside trading hours → blocked
- Friday after cutoff → blocked
- Friday before cutoff → allowed
- Saturday → blocked
- Sunday before open → blocked
- Sunday after open → allowed
- Guard disabled → always allowed
- Structured reason strings
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.session_guard import (
    check_session,
    SessionGuardResult,
    reset_session_log_state,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset log throttle between tests."""
    reset_session_log_state()
    yield
    reset_session_log_state()


def _utc(year=2026, month=6, day=4, hour=10, weekday_override=None):
    """Create a UTC datetime. day=4 is Wednesday in June 2026."""
    # June 4 2026 is Thursday (weekday=3)
    # Adjust to get desired weekday
    dt = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    return dt


# ─── TRADING HOURS TESTS ──────────────────────────────────────────────────────

class TestTradingHours:
    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_within_hours_allowed(self, *_):
        """10:00 UTC Wednesday → allowed."""
        # June 4 2026 = Thursday (weekday=3)
        dt = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)  # Wednesday=2
        result = check_session(now_utc=dt)
        assert result.allowed is True
        assert result.reason == "SESSION_OPEN"

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_before_start_blocked(self, *_):
        """03:00 UTC → blocked (before 07:00 start)."""
        dt = datetime(2026, 6, 3, 3, 0, tzinfo=timezone.utc)  # Wednesday 03:00
        result = check_session(now_utc=dt)
        assert result.allowed is False
        assert "OUTSIDE_TRADING_HOURS" in result.reason

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_after_end_blocked(self, *_):
        """22:00 UTC → blocked (after 21:00 end)."""
        dt = datetime(2026, 6, 3, 22, 0, tzinfo=timezone.utc)  # Wednesday 22:00
        result = check_session(now_utc=dt)
        assert result.allowed is False
        assert "OUTSIDE_TRADING_HOURS" in result.reason

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_boundary_start_allowed(self, *_):
        """07:00 UTC → allowed (exactly at start)."""
        dt = datetime(2026, 6, 3, 7, 0, tzinfo=timezone.utc)
        result = check_session(now_utc=dt)
        assert result.allowed is True

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_boundary_end_blocked(self, *_):
        """21:00 UTC → blocked (end is exclusive)."""
        dt = datetime(2026, 6, 3, 21, 0, tzinfo=timezone.utc)
        result = check_session(now_utc=dt)
        assert result.allowed is False


# ─── FRIDAY TESTS ─────────────────────────────────────────────────────────────

class TestFridayCutoff:
    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_friday_before_cutoff_allowed(self, *_):
        """Friday 15:00 → allowed."""
        dt = datetime(2026, 6, 5, 15, 0, tzinfo=timezone.utc)  # Friday=4
        assert dt.weekday() == 4
        result = check_session(now_utc=dt)
        assert result.allowed is True

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_friday_after_cutoff_blocked(self, *_):
        """Friday 20:00 → blocked."""
        dt = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)
        result = check_session(now_utc=dt)
        assert result.allowed is False
        assert "FRIDAY_CUTOFF" in result.reason


# ─── WEEKEND TESTS ────────────────────────────────────────────────────────────

class TestWeekend:
    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_saturday_blocked(self, *_):
        """Saturday any hour → blocked."""
        dt = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)  # Saturday=5
        assert dt.weekday() == 5
        result = check_session(now_utc=dt)
        assert result.allowed is False
        assert "SATURDAY" in result.reason

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_sunday_before_open_blocked(self, *_):
        """Sunday 18:00 → blocked (before 22:00 open)."""
        dt = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)  # Sunday=6
        assert dt.weekday() == 6
        result = check_session(now_utc=dt)
        assert result.allowed is False
        assert "SUNDAY_CLOSED" in result.reason

    @patch("risk.session_guard._is_session_guard_enabled", return_value=True)
    @patch("risk.session_guard._get_trading_hours_start", return_value=7)
    @patch("risk.session_guard._get_trading_hours_end", return_value=21)
    @patch("risk.session_guard._get_friday_cutoff", return_value=20)
    @patch("risk.session_guard._get_sunday_open", return_value=22)
    def test_sunday_after_open_allowed(self, *_):
        """Sunday 22:00 → allowed (at open hour)."""
        dt = datetime(2026, 6, 7, 22, 0, tzinfo=timezone.utc)
        result = check_session(now_utc=dt)
        assert result.allowed is True


# ─── DISABLED GUARD ───────────────────────────────────────────────────────────

class TestDisabled:
    @patch("risk.session_guard._is_session_guard_enabled", return_value=False)
    def test_disabled_always_allows(self, *_):
        """Guard disabled → always allowed regardless of time."""
        dt = datetime(2026, 6, 6, 3, 0, tzinfo=timezone.utc)  # Saturday 03:00
        result = check_session(now_utc=dt)
        assert result.allowed is True
        assert "DISABLED" in result.reason
