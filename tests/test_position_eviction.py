"""
Tests for B5: Closed Position Eviction.

Covers:
- Open position is never evicted
- Closed + journaled ? evicted after delay
- Closed but not journaled ? NOT evicted
- Missing timestamp ? NOT evicted
- Eviction does not break TradeStateManager lookups
- Repeated sweeps are idempotent
- Config disabled ? no eviction
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

from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

def _cfg():
    return TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )


def _pos(pid: str, status=PositionStatus.OPEN, closed_time=None):
    """Create a test position."""
    return Position(
        position_id=pid,
        symbol="EURUSD",
        side=Side.BUY,
        magic=713001,
        entry_price=1.1,
        initial_sl=1.09,
        initial_tp=1.12,
        stop_loss=1.09,
        take_profit=1.12,
        volume=0.01,
        open_time=time.time() - 7200,
        status=status,
        closed_time=closed_time,
    )


# --- TEST: OPEN POSITION NEVER EVICTED ----------------------------------------

class TestOpenNeverEvicted:
    def test_open_position_stays(self):
        """OPEN position is never evicted regardless of age."""
        tm = TradeStateManager(_cfg())
        pos = _pos("pos_1", status=PositionStatus.OPEN)
        tm._by_id["pos_1"] = pos

        with patch("core.trade_management.manager.getattr", side_effect=lambda *a: True if 'ENABLED' in str(a) else 3600):
            evicted = tm.evict_closed_positions()

        assert evicted == 0
        assert "pos_1" in tm._by_id


# --- TEST: CLOSED + JOURNALED ? EVICTED ---------------------------------------

class TestClosedJournaledEvicted:
    def test_evicted_after_delay(self):
        """Closed, journaled, past delay ? evicted."""
        tm = TradeStateManager(_cfg())
        old_close = time.time() - 7200  # 2 hours ago (> 1hr delay)
        pos = _pos("pos_2", status=PositionStatus.CLOSED, closed_time=old_close)
        tm._by_id["pos_2"] = pos

        with patch("core.trade_management.manager.PositionStatus", PositionStatus), \
             patch("core.trade_journal.is_already_journaled", return_value=True):
            evicted = tm.evict_closed_positions()

        assert evicted == 1
        assert "pos_2" not in tm._by_id

    def test_not_evicted_before_delay(self):
        """Closed, journaled, but within delay ? NOT evicted."""
        tm = TradeStateManager(_cfg())
        recent_close = time.time() - 60  # 1 minute ago (< 1hr delay)
        pos = _pos("pos_3", status=PositionStatus.CLOSED, closed_time=recent_close)
        tm._by_id["pos_3"] = pos

        with patch("core.trade_journal.is_already_journaled", return_value=True):
            evicted = tm.evict_closed_positions()

        assert evicted == 0
        assert "pos_3" in tm._by_id


# --- TEST: NOT JOURNALED ? NOT EVICTED ----------------------------------------

class TestNotJournaledNotEvicted:
    def test_unjournaled_not_evicted(self):
        """Closed but NOT journaled ? NOT evicted (safety)."""
        tm = TradeStateManager(_cfg())
        old_close = time.time() - 7200
        pos = _pos("pos_4", status=PositionStatus.CLOSED, closed_time=old_close)
        tm._by_id["pos_4"] = pos

        with patch("core.trade_journal.is_already_journaled", return_value=False):
            evicted = tm.evict_closed_positions()

        assert evicted == 0
        assert "pos_4" in tm._by_id


# --- TEST: MISSING TIMESTAMP ? NOT EVICTED ------------------------------------

class TestMissingTimestamp:
    def test_no_closed_time_not_evicted(self):
        """Closed but no closed_time ? NOT evicted."""
        tm = TradeStateManager(_cfg())
        pos = _pos("pos_5", status=PositionStatus.CLOSED, closed_time=None)
        tm._by_id["pos_5"] = pos

        with patch("core.trade_journal.is_already_journaled", return_value=True):
            evicted = tm.evict_closed_positions()

        assert evicted == 0
        assert "pos_5" in tm._by_id


# --- TEST: DOES NOT BREAK LOOKUPS ---------------------------------------------

class TestNoBreakage:
    def test_positions_open_works_after_eviction(self):
        """positions_open() still works correctly after eviction."""
        tm = TradeStateManager(_cfg())

        # One open, one closed (evictable)
        open_pos = _pos("pos_open", status=PositionStatus.OPEN)
        closed_pos = _pos("pos_closed", status=PositionStatus.CLOSED, closed_time=time.time() - 7200)
        tm._by_id["pos_open"] = open_pos
        tm._by_id["pos_closed"] = closed_pos

        with patch("core.trade_journal.is_already_journaled", return_value=True):
            tm.evict_closed_positions()

        open_positions = tm.positions_open()
        assert len(open_positions) == 1
        assert open_positions[0].position_id == "pos_open"


# --- TEST: IDEMPOTENT SWEEPS --------------------------------------------------

class TestIdempotent:
    def test_repeated_sweeps_safe(self):
        """Multiple eviction sweeps don't cause errors."""
        tm = TradeStateManager(_cfg())
        pos = _pos("pos_6", status=PositionStatus.CLOSED, closed_time=time.time() - 7200)
        tm._by_id["pos_6"] = pos

        with patch("core.trade_journal.is_already_journaled", return_value=True):
            evicted1 = tm.evict_closed_positions()
            evicted2 = tm.evict_closed_positions()  # Already gone
            evicted3 = tm.evict_closed_positions()

        assert evicted1 == 1
        assert evicted2 == 0
        assert evicted3 == 0


# --- TEST: DISABLED CONFIG ----------------------------------------------------

class TestDisabled:
    def test_disabled_no_eviction(self):
        """When POSITION_EVICTION_ENABLED=False, no eviction occurs."""
        tm = TradeStateManager(_cfg())
        pos = _pos("pos_7", status=PositionStatus.CLOSED, closed_time=time.time() - 99999)
        tm._by_id["pos_7"] = pos

        with patch("core.trade_management.manager.getattr") as mock_getattr:
            # Make config return enabled=False
            def _side_effect(obj, key, default=None):
                if key == "POSITION_EVICTION_ENABLED":
                    return False
                if key == "POSITION_EVICTION_DELAY_SECONDS":
                    return 3600
                return default
            mock_getattr.side_effect = _side_effect

            evicted = tm.evict_closed_positions()

        assert evicted == 0
        assert "pos_7" in tm._by_id
