"""
Unit tests for CycleGuards — cycle-level permission evaluation.

Tests:
    - Allowed cycle (all guards pass)
    - Blocked cycle (drawdown blocks)
    - Daily loss soft-block (flag set, cycle still allowed)
    - Kill switch snapshot (flag set, cycle still allowed)
    - Guard ordering preserved (drawdown short-circuits remaining)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.cycle_guards import CycleGuards, CyclePermission


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_drawdown_result(allowed: bool, **kwargs):
    """Create a mock DrawdownResult."""
    r = MagicMock()
    r.allowed = allowed
    r.current_drawdown_pct = kwargs.get("current_drawdown_pct", 0.0)
    r.max_drawdown_pct = kwargs.get("max_drawdown_pct", 10.0)
    return r


def _make_daily_loss_result(allowed: bool, **kwargs):
    """Create a mock DailyLossResult."""
    r = MagicMock()
    r.allowed = allowed
    r.current_loss_pct = kwargs.get("current_loss_pct", 0.0)
    r.limit_pct = kwargs.get("limit_pct", 4.0)
    return r


def _build_guards(dd_allowed=True, dl_allowed=True, kill_active=False, reset_triggered=False):
    """Build CycleGuards with mocked dependencies."""
    config = MagicMock()
    config._discord_logger = None

    drawdown_guard = MagicMock()
    drawdown_guard.check.return_value = _make_drawdown_result(dd_allowed)

    daily_loss_guard = MagicMock()
    daily_loss_guard.check.return_value = _make_daily_loss_result(dl_allowed)

    daily_reset = MagicMock()
    daily_reset.evaluate.return_value = reset_triggered

    daily_trade_limit = MagicMock()

    guards = CycleGuards(config, drawdown_guard, daily_loss_guard, daily_reset, daily_trade_limit)
    return guards, drawdown_guard, daily_loss_guard, daily_reset, daily_trade_limit, kill_active


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestCycleGuardsAllowed:
    """All guards pass — cycle is allowed."""

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_all_guards_pass(self, mock_emit, mock_kill):
        guards, dd, dl, reset, limit, _ = _build_guards(
            dd_allowed=True, dl_allowed=True, kill_active=False
        )

        result = guards.evaluate()

        assert result.cycle_allowed is True
        assert result.daily_loss_blocked is False
        assert result.kill_switch_active is False
        assert result.block_reason == ""
        assert result.drawdown_result is not None
        assert result.daily_loss_result is not None

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_daily_reset_triggers_limit_reset(self, mock_emit, mock_kill):
        guards, dd, dl, reset, limit, _ = _build_guards(
            dd_allowed=True, dl_allowed=True, reset_triggered=True
        )

        result = guards.evaluate()

        assert result.cycle_allowed is True
        limit.reset.assert_called_once()


class TestCycleGuardsBlocked:
    """Drawdown guard blocks — cycle is not allowed."""

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_drawdown_blocks_cycle(self, mock_emit, mock_kill):
        guards, dd, dl, reset, limit, _ = _build_guards(dd_allowed=False)

        result = guards.evaluate()

        assert result.cycle_allowed is False
        assert result.block_reason == "drawdown_limit_exceeded"
        # Drawdown result is available for heartbeat
        assert result.drawdown_result is not None
        # Risk event was emitted
        mock_emit.assert_called_once()

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_drawdown_short_circuits_remaining_guards(self, mock_emit, mock_kill):
        """When drawdown blocks, daily_loss and kill_switch are NOT evaluated."""
        guards, dd, dl, reset, limit, _ = _build_guards(dd_allowed=False)

        guards.evaluate()

        # Daily loss guard should NOT have been called
        dl.check.assert_not_called()
        # Daily reset should NOT have been called
        reset.evaluate.assert_not_called()
        # Kill switch should NOT have been called
        mock_kill.assert_not_called()


class TestCycleGuardsSoftBlocks:
    """Daily loss and kill switch set flags but cycle continues."""

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_daily_loss_blocked_flag(self, mock_emit, mock_kill):
        guards, dd, dl, reset, limit, _ = _build_guards(
            dd_allowed=True, dl_allowed=False
        )

        result = guards.evaluate()

        assert result.cycle_allowed is True
        assert result.daily_loss_blocked is True
        assert result.kill_switch_active is False

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=True)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_kill_switch_active_flag(self, mock_emit, mock_kill):
        guards, dd, dl, reset, limit, _ = _build_guards(
            dd_allowed=True, dl_allowed=True
        )

        result = guards.evaluate()

        assert result.cycle_allowed is True
        assert result.kill_switch_active is True
        assert result.daily_loss_blocked is False

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=True)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_both_soft_blocks_active(self, mock_emit, mock_kill):
        """Both daily loss and kill switch can be active simultaneously."""
        guards, dd, dl, reset, limit, _ = _build_guards(
            dd_allowed=True, dl_allowed=False
        )

        result = guards.evaluate()

        assert result.cycle_allowed is True
        assert result.daily_loss_blocked is True
        assert result.kill_switch_active is True


class TestCycleGuardsOrdering:
    """Guard evaluation order is preserved."""

    @patch("core.runtime.cycle_guards.is_kill_switch_active", return_value=False)
    @patch("core.runtime.cycle_guards.emit_risk_guard_result")
    def test_evaluation_order_when_all_pass(self, mock_emit, mock_kill):
        """Guards are called in correct order: drawdown → reset → daily_loss → kill."""
        call_order = []

        config = MagicMock()
        config._discord_logger = None

        drawdown_guard = MagicMock()
        dd_result = _make_drawdown_result(True)
        drawdown_guard.check.side_effect = lambda: (call_order.append("drawdown"), dd_result)[1]

        daily_loss_guard = MagicMock()
        dl_result = _make_daily_loss_result(True)
        daily_loss_guard.check.side_effect = lambda: (call_order.append("daily_loss"), dl_result)[1]

        daily_reset = MagicMock()
        daily_reset.evaluate.side_effect = lambda: (call_order.append("daily_reset"), False)[1]

        daily_trade_limit = MagicMock()

        guards = CycleGuards(config, drawdown_guard, daily_loss_guard, daily_reset, daily_trade_limit)
        guards.evaluate()

        assert call_order == ["drawdown", "daily_reset", "daily_loss"]
