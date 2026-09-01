"""
Phase 3B: Horizon-Specific Trade Management Rules — Tests.

Validates:
    1. SCALP behaviour preserved (same BE trigger, same time exit)
    2. INTRADAY profile loads with distinct values
    3. EXTENDED profile loads with distinct values
    4. Hard time exit triggers at correct per-horizon duration
    5. Trailing stop priority: if trailing active, time exit still fires but notes it
    6. INTRADAY/EXTENDED cannot bypass execution restrictions
    7. Profiles have genuinely different management parameters
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.trade_management.config import TradeManagementConfig
from core.trade_management.manager import TradeStateManager
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_position(
    symbol: str = "EURUSD",
    side: Side = Side.BUY,
    horizon: str = "SCALP",
    entry: float = 1.1000,
    sl: float = 1.0990,
    tp: float = 1.1020,
    open_time: float = 1000.0,
) -> Position:
    return Position(
        position_id=f"pos_{horizon}_{symbol}",
        symbol=symbol,
        side=side,
        magic=713001,
        entry_price=entry,
        initial_sl=sl,
        initial_tp=tp,
        stop_loss=sl,
        take_profit=tp,
        volume=0.01,
        open_time=open_time,
        trade_horizon=horizon,
    )


def _scalp_cfg() -> TradeManagementConfig:
    """Baseline config (will be overridden by profile resolution)."""
    return TradeManagementConfig()


def _fresh_manager() -> tuple:
    """Reset HorizonManager and return fresh state."""
    from core.horizon.horizon_manager import reset_horizon_manager
    reset_horizon_manager()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SCALP Behaviour Preserved
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalpPreserved:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_scalp_profile_has_correct_be_trigger(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("SCALP")
        assert profile.break_even_trigger_rr == 1.0

    def test_scalp_profile_has_correct_time_exit(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("SCALP")
        assert profile.max_time_in_trade_seconds == 0.0  # Disabled (preserves current behaviour)

    def test_scalp_break_even_still_works(self):
        """SCALP position triggers BE at 1R profit as before."""
        tm = TradeStateManager(_scalp_cfg())
        # risk = entry - sl = 0.0010
        pos = _make_position(entry=1.1000, sl=1.0990, tp=1.1020, horizon="SCALP")
        tm._by_id[pos.position_id] = pos

        # Price at 1.1R profit → should trigger BE (trigger=1.0R)
        tm.on_price_update("EURUSD", bid=1.1011, ask=1.1012, time_s=1500.0)
        assert pos.stop_loss > pos.initial_sl, "BE should move SL above initial"

    def test_scalp_no_time_exit_when_disabled(self):
        """SCALP with max_time=0 never time-exits (current production behaviour)."""
        tm = TradeStateManager(_scalp_cfg())
        pos = _make_position(open_time=1000.0, horizon="SCALP")
        tm._by_id[pos.position_id] = pos

        # Even after very long time: still open (time exit disabled)
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=100000.0)
        assert pos.status == PositionStatus.OPEN


# ═══════════════════════════════════════════════════════════════════════════════
# 2. INTRADAY Profile Loads Correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntradayProfile:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_intraday_has_distinct_be_trigger(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("INTRADAY")
        assert profile.break_even_trigger_rr == 1.5

    def test_intraday_has_trailing(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("INTRADAY")
        assert profile.trailing_step == 0.0003
        assert profile.trailing_start_rr == 2.0

    def test_intraday_has_partial_tp(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("INTRADAY")
        assert profile.partial_tp_fraction == 0.5
        assert profile.partial_tp_path_fraction == 0.7

    def test_intraday_time_exit_240_minutes(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("INTRADAY")
        assert profile.max_time_in_trade_seconds == 14400.0  # 240 min

    def test_intraday_in_permitted_horizons(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.is_permitted("INTRADAY") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXTENDED Profile Loads Correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedProfile:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_extended_has_distinct_be_trigger(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("EXTENDED")
        assert profile.break_even_trigger_rr == 2.0

    def test_extended_has_trailing(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("EXTENDED")
        assert profile.trailing_step == 0.0005
        assert profile.trailing_start_rr == 3.0

    def test_extended_has_partial_tp(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("EXTENDED")
        assert profile.partial_tp_fraction == 0.5
        assert profile.partial_tp_path_fraction == 0.6

    def test_extended_time_exit_720_minutes(self):
        from core.horizon.horizon_manager import get_horizon_manager
        profile = get_horizon_manager().get_profile("EXTENDED")
        assert profile.max_time_in_trade_seconds == 43200.0  # 720 min

    def test_extended_in_permitted_horizons(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.is_permitted("EXTENDED") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Hard Time Exit at Correct Duration
# ═══════════════════════════════════════════════════════════════════════════════

class TestHardTimeExit:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_scalp_no_time_exit_disabled(self):
        """SCALP time exit is disabled (0.0) — preserves current behaviour."""
        tm = TradeStateManager(_scalp_cfg())
        pos = _make_position(open_time=0.0, horizon="SCALP")
        tm._by_id[pos.position_id] = pos

        # Even after hours: still open
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=99999.0)
        assert pos.status == PositionStatus.OPEN

    def test_intraday_closes_at_14400s(self):
        """If an INTRADAY position existed, it would close at 240 min."""
        tm = TradeStateManager(_scalp_cfg())
        pos = _make_position(open_time=0.0, horizon="INTRADAY")
        tm._by_id[pos.position_id] = pos

        # Before threshold: open
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=14399.0)
        assert pos.status == PositionStatus.OPEN

        # After threshold: closed
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=14401.0)
        assert pos.status == PositionStatus.CLOSED

    def test_extended_closes_at_43200s(self):
        """If an EXTENDED position existed, it would close at 720 min."""
        tm = TradeStateManager(_scalp_cfg())
        pos = _make_position(open_time=0.0, horizon="EXTENDED")
        tm._by_id[pos.position_id] = pos

        # Before threshold: open
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=43199.0)
        assert pos.status == PositionStatus.OPEN

        # After threshold: closed
        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=43201.0)
        assert pos.status == PositionStatus.CLOSED

    def test_scalp_survives_indefinitely_without_time_exit(self):
        """SCALP stays open indefinitely (time exit disabled in current config)."""
        tm = TradeStateManager(_scalp_cfg())
        pos = _make_position(open_time=0.0, horizon="SCALP")
        tm._by_id[pos.position_id] = pos

        tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=86400.0)  # 24 hours
        assert pos.status == PositionStatus.OPEN


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Trailing Stop and Time Exit Interaction
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrailingAndTimeExit:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_scalp_no_time_exit_even_with_be_active(self):
        """
        SCALP time exit is disabled (0.0). Position exits are controlled
        by SL/TP only. This test verifies the time exit path is not invoked.
        """
        tm = TradeStateManager(_scalp_cfg())
        # Use a wider price structure so BE buffer doesn't immediately trigger SL
        # entry=1.1, sl=1.0, tp=1.2 → risk=0.1, BE buffer=0.1 → be=entry+0.1=1.2
        pos = _make_position(open_time=0.0, entry=1.1000, sl=1.0000, tp=1.3000, horizon="SCALP")
        tm._by_id[pos.position_id] = pos

        # Price moves to 1.1R profit → BE trigger at 1R = 0.1 above entry = 1.2
        # bid=1.2001 > 1.2 so BE fires, SL moves to entry+buffer = 1.1+0.1 = 1.2
        tm.on_price_update("EURUSD", bid=1.2001, ask=1.2002, time_s=100.0)

        # Price stays above SL (1.2) so position remains open
        # After very long time — still open (time exit disabled)
        tm.on_price_update("EURUSD", bid=1.2500, ask=1.2501, time_s=99999.0)
        assert pos.status == PositionStatus.OPEN

    def test_intraday_trailing_active_before_time_exit(self):
        """INTRADAY with trailing active still closes at 240 min."""
        tm = TradeStateManager(_scalp_cfg())
        # INTRADAY has trailing at 2R start, 3 pip step
        pos = _make_position(
            open_time=0.0, entry=1.1000, sl=1.0990, tp=1.1060,
            horizon="INTRADAY",
        )
        tm._by_id[pos.position_id] = pos

        # Push price to 2.5R profit to activate trailing
        # risk = 0.0010, 2.5R = 0.0025 → bid=1.1025
        tm.on_price_update("EURUSD", bid=1.1025, ask=1.1026, time_s=1000.0)
        # SL should have moved (BE at 1.5R + trailing at 2R)
        assert pos.stop_loss > pos.initial_sl

        # Time exit at 240 min — still closes
        tm.on_price_update("EURUSD", bid=1.1020, ask=1.1021, time_s=14401.0)
        assert pos.status == PositionStatus.CLOSED


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Disabled Horizons Cannot Bypass Execution Restrictions
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisabledHorizonsBlocked:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_execution_authority_allows_intraday(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is True

    def test_execution_authority_allows_extended(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=[])
        assert result.allowed is True

    def test_execution_authority_allows_scalp(self):
        """SCALP remains permitted."""
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="SCALP", current_positions=[])
        assert result.allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Profiles Have Genuinely Different Parameters
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfileDifferences:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_all_horizons_have_different_time_exits(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        assert scalp.max_time_in_trade_seconds == 0.0      # Disabled (current behaviour preserved)
        assert intraday.max_time_in_trade_seconds == 14400.0
        assert extended.max_time_in_trade_seconds == 43200.0
        assert intraday.max_time_in_trade_seconds < extended.max_time_in_trade_seconds

    def test_be_trigger_increases_with_horizon(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        assert scalp.break_even_trigger_rr < intraday.break_even_trigger_rr < extended.break_even_trigger_rr

    def test_trailing_only_on_higher_horizons(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        assert scalp.trailing_step == 0.0  # Disabled for SCALP
        assert intraday.trailing_step > 0  # Enabled for INTRADAY
        assert extended.trailing_step > intraday.trailing_step  # Wider for EXTENDED

    def test_partial_tp_only_on_higher_horizons(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        assert scalp.partial_tp_fraction == 0.0  # Disabled for SCALP
        assert intraday.partial_tp_fraction == 0.5
        assert extended.partial_tp_fraction == 0.5
