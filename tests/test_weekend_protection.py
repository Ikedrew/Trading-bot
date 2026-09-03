"""
Tests for H4: Weekend Position Protection.

Covers:
- Friday before flatten hour ? allowed
- Friday after flatten hour ? blocked
- Saturday/Sunday ? blocked
- Monday ? allowed (reset)
- Flatten disabled ? no closures
- Block disabled ? allows during weekend window
- Persistence survives restart
- Pipeline integration ordering
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.weekend_protection import (
    WeekendGateResult,
    REJECT_WEEKEND_BLOCK,
    ACTION_ALLOW,
    ACTION_BLOCK,
    is_friday_close_window,
    is_weekend_over,
    check_weekend_gate,
    flatten_all_positions,
    load_weekend_state,
    clear_weekend_state,
    _persist_weekend_state,
    validate_weekend_config,
)

from core.trade_management.config import TradeManagementConfig
from core.trade_management.events import TradeLifecycleEvent
from core.trade_management.manager import TradeStateManager
from core.trade_management.position import Position, PositionStatus
from execution.mt5_execution import ExecutionResult
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config(tmp_path):
    """Set known config defaults."""
    state_file = tmp_path / "weekend_state.json"
    with patch("core.weekend_protection._flatten_enabled", return_value=True), \
         patch("core.weekend_protection._block_enabled", return_value=True), \
         patch("core.weekend_protection._get_flatten_hour", return_value=20), \
         patch("core.weekend_protection._get_state_path", return_value=state_file):
        yield state_file


# --- HELPER -------------------------------------------------------------------

def _dt(year=2026, month=6, day=5, hour=10):
    """Create a datetime. June 5 2026 = Friday."""
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


# --- TEST: FRIDAY BEFORE FLATTEN HOUR ------------------------------------------

class TestFridayBeforeFlatten:
    def test_friday_morning_allowed(self, default_config):
        """Friday 10:00 UTC ? trading allowed."""
        fri_morning = _dt(day=5, hour=10)  # Friday
        assert is_friday_close_window(fri_morning) is False

        result = check_weekend_gate(fri_morning)
        assert result.allowed is True
        assert result.action == ACTION_ALLOW

    def test_friday_just_before_flatten(self, default_config):
        """Friday 19:59 UTC ? still allowed."""
        fri_before = _dt(day=5, hour=19)  # Friday 19:00, flatten at 20
        assert is_friday_close_window(fri_before) is False

        result = check_weekend_gate(fri_before)
        assert result.allowed is True


# --- TEST: FRIDAY AFTER FLATTEN HOUR ------------------------------------------

class TestFridayAfterFlatten:
    def test_friday_at_flatten_hour_blocked(self, default_config):
        """Friday 20:00 UTC ? blocked."""
        fri_flatten = _dt(day=5, hour=20)  # Friday 20:00
        assert is_friday_close_window(fri_flatten) is True

        result = check_weekend_gate(fri_flatten)
        assert result.allowed is False
        assert result.reason == REJECT_WEEKEND_BLOCK
        assert result.action == ACTION_BLOCK

    def test_friday_late_blocked(self, default_config):
        """Friday 23:00 UTC ? blocked."""
        fri_late = _dt(day=5, hour=23)
        assert is_friday_close_window(fri_late) is True

        result = check_weekend_gate(fri_late)
        assert result.allowed is False


# --- TEST: SATURDAY/SUNDAY ----------------------------------------------------

class TestWeekend:
    def test_saturday_blocked(self, default_config):
        """Saturday ? blocked."""
        saturday = _dt(day=6, hour=12)  # June 6 2026 = Saturday
        assert is_friday_close_window(saturday) is True

        result = check_weekend_gate(saturday)
        assert result.allowed is False

    def test_sunday_blocked(self, default_config):
        """Sunday ? blocked."""
        sunday = _dt(day=7, hour=15)  # June 7 2026 = Sunday
        assert is_friday_close_window(sunday) is True

        result = check_weekend_gate(sunday)
        assert result.allowed is False


# --- TEST: MONDAY RESET ------------------------------------------------------

class TestMondayReset:
    def test_monday_allowed(self, default_config):
        """Monday ? trading allowed."""
        monday = _dt(day=8, hour=8)  # June 8 2026 = Monday
        assert is_friday_close_window(monday) is False
        assert is_weekend_over(monday) is True

        result = check_weekend_gate(monday)
        assert result.allowed is True

    def test_thursday_allowed(self, default_config):
        """Thursday ? fully allowed."""
        thursday = _dt(day=4, hour=14)  # June 4 2026 = Thursday
        assert is_weekend_over(thursday) is True

        result = check_weekend_gate(thursday)
        assert result.allowed is True


# --- TEST: FLATTEN DISABLED ---------------------------------------------------

class TestFlattenDisabled:
    def test_no_closures_when_disabled(self, default_config):
        """Flatten disabled ? no positions closed."""
        with patch("core.weekend_protection._flatten_enabled", return_value=False):
            closed = flatten_all_positions(
                trade_managers=[MagicMock()],
                execution=MagicMock(),
            )
        assert closed == 0


# --- TEST: FLATTEN VIA CANONICAL CLOSE PATH -----------------------------------

def _flatten_cfg() -> TradeManagementConfig:
    return TradeManagementConfig(
        break_even_trigger_rr=0.0,
        break_even_buffer_rr=0.0,
        trailing_step=0.0,
        trailing_start_rr=0.0,
        partial_tp_fraction=0.0,
        partial_tp_path_fraction=0.0,
        max_time_in_trade_seconds=0.0,
    )


def _flatten_position(position_id: str = "pos_flat", ticket: int = 12345) -> Position:
    return Position(
        position_id=position_id,
        symbol="EURUSD",
        side=Side.BUY,
        magic=713001,
        entry_price=1.1000,
        initial_sl=1.0950,
        initial_tp=1.1100,
        stop_loss=1.0950,
        take_profit=1.1100,
        volume=0.10,
        open_time=1000.0,
        status=PositionStatus.OPEN,
        mt5_ticket=ticket,
        deal_id=ticket,
        order_id=99999,
        max_favourable_price=1.1000,
    )


class TestFlattenCanonicalClose:
    def test_flatten_closes_positions(self, default_config):
        """Flatten enabled ? positions closed via the manager's canonical close."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = ExecutionResult(True, 10009, 12345, 99, "done")

        mgr = TradeStateManager(_flatten_cfg(), execution=mock_exec)
        pos = _flatten_position()
        mgr._by_id[pos.position_id] = pos

        closed = flatten_all_positions(
            trade_managers=[mgr],
            execution=mock_exec,
        )

        assert closed == 1
        assert pos.status == PositionStatus.CLOSED
        mock_exec.close_position.assert_called_once()

    def test_flatten_emits_single_canonical_close_with_broker_accounting(self, default_config):
        """Weekend flatten produces exactly one ON_TRADE_CLOSE carrying genuine
        broker close facts (regression, Issue 2)."""
        listener = MagicMock()
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = ExecutionResult(True, 10009, 12345, 99, "done")

        brok = {
            "reason": "broker_close",
            "broker_exit_price": 1.0990,
            "broker_exit_time": 3000.0,
            "broker_profit": -10.50,
            "broker_commission": -0.05,
            "broker_swap": 0.0,
            "broker_deal_id": 12345,
            "broker_deal_reason": 0,
        }

        mgr = TradeStateManager(_flatten_cfg(), listener=listener, execution=mock_exec)
        mgr._query_broker_close_history = MagicMock(return_value=brok)
        pos = _flatten_position()
        mgr._by_id[pos.position_id] = pos

        closed = flatten_all_positions(
            trade_managers=[mgr],
            execution=mock_exec,
        )
        assert closed == 1

        events = [c.args[0] for c in listener.on_trade_event.call_args_list]
        close_events = [e for e in events if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE]
        assert len(close_events) == 1, (
            "exactly one canonical ON_TRADE_CLOSE per flattened position"
        )
        d = close_events[0].detail
        assert d["broker_profit"] == -10.50
        assert d["broker_commission"] == -0.05
        assert d["broker_swap"] == 0.0
        assert d["broker_exit_price"] == 1.0990
        assert d["broker_deal_id"] == 12345

    def test_flatten_broker_failure_no_fake_close(self, default_config):
        """If the broker close fails, weekend flatten must NOT report a close
        and must NOT emit a canonical close record."""
        listener = MagicMock()
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = ExecutionResult(False, 10004, 0, 0, "requote")

        mgr = TradeStateManager(_flatten_cfg(), listener=listener, execution=mock_exec)
        pos = _flatten_position()
        mgr._by_id[pos.position_id] = pos

        closed = flatten_all_positions(
            trade_managers=[mgr],
            execution=mock_exec,
        )
        assert closed == 0
        assert pos.status == PositionStatus.OPEN
        close_events = [
            e for e in (c.args[0] for c in listener.on_trade_event.call_args_list)
            if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE
        ]
        assert close_events == []

    def test_flatten_position_not_found_closed_via_manager(self, default_config):
        """Server-side closure (POSITION_NOT_FOUND) is interpreted by the manager
        consistently: broker history reconciled and the position counted as a
        confirmed close."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = ExecutionResult(False, 10008, 0, 0, "POSITION_NOT_FOUND")

        mgr = TradeStateManager(_flatten_cfg(), execution=mock_exec)
        mgr._query_broker_close_history = MagicMock(return_value={
            "reason": "broker_close",
            "broker_profit": -3.30,
            "broker_commission": None,
            "broker_swap": None,
        })
        pos = _flatten_position()
        mgr._by_id[pos.position_id] = pos

        closed = flatten_all_positions(
            trade_managers=[mgr],
            execution=mock_exec,
        )
        assert closed == 1
        assert pos.status == PositionStatus.CLOSED
        mgr._query_broker_close_history.assert_called_with(pos)


# --- TEST: BLOCK DISABLED -----------------------------------------------------

class TestBlockDisabled:
    def test_block_disabled_allows_weekend(self, default_config):
        """Block disabled ? allows trading even during weekend window."""
        with patch("core.weekend_protection._block_enabled", return_value=False):
            fri_late = _dt(day=5, hour=22)
            result = check_weekend_gate(fri_late)

        assert result.allowed is True


# --- TEST: PERSISTENCE --------------------------------------------------------

class TestPersistence:
    def test_state_persisted_on_flatten(self, default_config):
        """Flatten writes state file."""
        _persist_weekend_state(closed=2, reason="WEEKEND_FLATTEN")

        assert default_config.exists()
        data = json.loads(default_config.read_text())
        assert data["weekend_mode_active"] is True
        assert data["positions_closed"] == 2

    def test_state_loads_on_restart(self, default_config):
        """State file loads correctly."""
        default_config.write_text(json.dumps({
            "weekend_mode_active": True,
            "last_flatten_time": "2026-06-05T20:00:00",
            "positions_closed": 3,
            "reason": "WEEKEND_FLATTEN",
        }))

        state = load_weekend_state()
        assert state is not None
        assert state["weekend_mode_active"] is True
        assert state["positions_closed"] == 3

    def test_clear_state(self, default_config):
        """clear_weekend_state removes file."""
        default_config.write_text("{}")
        clear_weekend_state()
        assert not default_config.exists()


# --- TEST: CONFIG VALIDATION --------------------------------------------------

class TestConfigValidation:
    def test_valid_config(self, default_config):
        """Valid config passes."""
        errors = validate_weekend_config()
        assert errors == []

    def test_invalid_hour(self, default_config):
        """Invalid flatten hour generates error."""
        with patch("core.weekend_protection._get_flatten_hour", return_value=25):
            errors = validate_weekend_config()
        assert any("FRIDAY_FLATTEN_HOUR" in e for e in errors)


# --- TEST: PRODUCTION INTEGRATION ---------------------------------------------

class TestProductionIntegration:
    def test_gate_before_execution(self):
        """Weekend gate appears in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        wk_pos = source.find("check_weekend_gate")

        assert wk_pos > 0, "Weekend gate not found in runtime guard chain"

    def test_gate_after_prop_firm(self):
        """Weekend gate after prop firm rules."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        pfr_pos = source.find("check_prop_firm_gate")
        wk_pos = source.find("check_weekend_gate")

        assert pfr_pos > 0
        assert wk_pos > 0
        assert pfr_pos < wk_pos
