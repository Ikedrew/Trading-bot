"""
Phase 3: Profile-Driven Trade Management — Tests.

Validates:
    1. SCALP behaviour is unchanged (profile returns same values as global config)
    2. Different profiles return different management values
    3. TradeStateManager uses profile resolution, not horizon-specific conditionals
    4. Fallback to self._cfg when HorizonManager unavailable
    5. Recovered positions resolve correct profile
    6. Future INTRADAY/EXTENDED activation requires no TradeManager changes
"""

from __future__ import annotations

import sys
import time
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

def _make_cfg(**overrides) -> TradeManagementConfig:
    defaults = dict(
        break_even_trigger_rr=1.0,
        break_even_buffer_rr=0.0001,
        trailing_step=0.0,
        trailing_start_rr=0.0,
        partial_tp_fraction=0.0,
        partial_tp_path_fraction=0.0,
        max_time_in_trade_seconds=2700.0,
    )
    defaults.update(overrides)
    return TradeManagementConfig(**defaults)


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
        position_id=f"pos_test_{horizon}",
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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SCALP Behaviour Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalpBehaviourUnchanged:
    def test_scalp_resolves_same_as_global_config(self):
        """Profile for SCALP returns values matching HORIZON_TRADE_MANAGEMENT['SCALP']."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        mgr = get_horizon_manager()
        profile = mgr.get_profile("SCALP")

        from core import config
        _scalp_tm = config.HORIZON_TRADE_MANAGEMENT["SCALP"]
        assert profile.break_even_trigger_rr == _scalp_tm["break_even_trigger_rr"]
        assert profile.break_even_buffer_rr == _scalp_tm["break_even_buffer_rr"]
        assert profile.trailing_step == _scalp_tm["trailing_step"]
        assert profile.trailing_start_rr == _scalp_tm["trailing_start_rr"]
        assert profile.partial_tp_fraction == _scalp_tm["partial_tp_fraction"]
        assert profile.partial_tp_path_fraction == _scalp_tm["partial_tp_path_fraction"]
        assert profile.max_time_in_trade_seconds == _scalp_tm["max_time_in_trade_seconds"]

        reset_horizon_manager()

    def test_scalp_break_even_triggers_at_config_level(self):
        """SCALP position triggers break-even using profile-resolved config values."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        profile = get_horizon_manager().get_profile("SCALP")
        # Use profile values to construct the expected cfg
        cfg = TradeManagementConfig(
            break_even_trigger_rr=profile.break_even_trigger_rr,
            break_even_buffer_rr=profile.break_even_buffer_rr,
            trailing_step=profile.trailing_step,
            trailing_start_rr=profile.trailing_start_rr,
            partial_tp_fraction=profile.partial_tp_fraction,
            partial_tp_path_fraction=profile.partial_tp_path_fraction,
            max_time_in_trade_seconds=profile.max_time_in_trade_seconds,
        )
        tm = TradeStateManager(cfg)

        # Risk = entry - sl = 0.0010
        pos = _make_position(entry=1.1000, sl=1.0990, tp=1.1020)
        tm._by_id[pos.position_id] = pos

        # Move price to > 1R profit (1.0 * risk = 0.0010 above entry = 1.1010)
        tm.on_price_update("EURUSD", bid=1.1011, ask=1.1012, time_s=1500.0)

        # If BE trigger is 1.0R and buffer is 0.1 (RR units), new SL = entry + buffer*risk
        # SL should have moved from initial 1.0990 toward entry (break-even behaviour)
        if profile.break_even_trigger_rr > 0:
            assert pos.stop_loss > pos.initial_sl, "BE should have moved SL up from initial"

        reset_horizon_manager()

    def test_scalp_time_exit_uses_profile_value(self):
        """SCALP position uses max_time from profile (0 = disabled in current config)."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        profile = get_horizon_manager().get_profile("SCALP")
        cfg = TradeManagementConfig(
            break_even_trigger_rr=profile.break_even_trigger_rr,
            break_even_buffer_rr=profile.break_even_buffer_rr,
            trailing_step=profile.trailing_step,
            trailing_start_rr=profile.trailing_start_rr,
            partial_tp_fraction=profile.partial_tp_fraction,
            partial_tp_path_fraction=profile.partial_tp_path_fraction,
            max_time_in_trade_seconds=profile.max_time_in_trade_seconds,
        )
        tm = TradeStateManager(cfg)
        pos = _make_position(open_time=1000.0)
        tm._by_id[pos.position_id] = pos

        if profile.max_time_in_trade_seconds > 0:
            # If time exit enabled, position should close at threshold
            tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001,
                             time_s=1000.0 + profile.max_time_in_trade_seconds + 1)
            assert pos.status == PositionStatus.CLOSED
        else:
            # If disabled (current config: 0), position stays open indefinitely
            tm.on_price_update("EURUSD", bid=1.1000, ask=1.1001, time_s=100000.0)
            assert pos.status == PositionStatus.OPEN

        reset_horizon_manager()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Different Profiles Return Different Values
# ═══════════════════════════════════════════════════════════════════════════════

class TestDifferentProfileValues:
    def test_profiles_have_different_metadata(self):
        """SCALP, INTRADAY, EXTENDED have different typical_rr and hold times."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        # Metadata differs
        assert scalp.typical_rr == 2.0
        assert intraday.typical_rr == 3.0
        assert extended.typical_rr == 4.0

        assert scalp.expected_hold_minutes_max < intraday.expected_hold_minutes_max
        assert intraday.expected_hold_minutes_max < extended.expected_hold_minutes_max

        reset_horizon_manager()

    def test_phase3b_profiles_have_different_tm_values(self):
        """Phase 3B: each profile has distinct TM parameters from HORIZON_TRADE_MANAGEMENT."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")

        # Phase 3B: BE triggers differ
        assert scalp.break_even_trigger_rr == 1.0
        assert intraday.break_even_trigger_rr == 1.5
        assert extended.break_even_trigger_rr == 2.0

        # Trailing differs
        assert scalp.trailing_step == 0.0
        assert intraday.trailing_step > 0
        assert extended.trailing_step > intraday.trailing_step

        reset_horizon_manager()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Manager Uses Profile Resolution (No Horizon Conditionals)
# ═══════════════════════════════════════════════════════════════════════════════

class TestManagerUsesProfile:
    def test_resolve_config_calls_horizon_manager(self):
        """_resolve_config_for_position calls HorizonManager.get_profile."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="INTRADAY")

        with patch("core.horizon.horizon_manager.get_horizon_manager") as mock_mgr:
            mock_profile = MagicMock()
            mock_profile.break_even_trigger_rr = 1.5
            mock_profile.break_even_buffer_rr = 0.0002
            mock_profile.trailing_step = 0.0003
            mock_profile.trailing_start_rr = 2.0
            mock_profile.partial_tp_fraction = 0.5
            mock_profile.partial_tp_path_fraction = 0.6
            mock_profile.max_time_in_trade_seconds = 28800.0
            mock_mgr.return_value.get_profile.return_value = mock_profile

            resolved = tm._resolve_config_for_position(pos)

            mock_mgr.return_value.get_profile.assert_called_once_with("INTRADAY")
            assert resolved.break_even_trigger_rr == 1.5
            assert resolved.max_time_in_trade_seconds == 28800.0

    def test_no_horizon_conditionals_in_manager(self):
        """TradeStateManager.manager.py must not contain if horizon == patterns."""
        import inspect
        source = inspect.getsource(TradeStateManager)
        # Must never branch on specific horizon names
        assert 'horizon == "SCALP"' not in source
        assert 'horizon == "INTRADAY"' not in source
        assert 'horizon == "EXTENDED"' not in source
        assert "trade_horizon ==" not in source

    def test_different_horizon_positions_get_different_configs(self):
        """Two positions with different horizons resolve different profiles."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)

        pos_scalp = _make_position(horizon="SCALP")
        pos_intra = _make_position(horizon="INTRADAY")

        cfg_scalp = tm._resolve_config_for_position(pos_scalp)
        cfg_intra = tm._resolve_config_for_position(pos_intra)

        # Phase 3B: values differ between horizons
        assert cfg_scalp.break_even_trigger_rr != cfg_intra.break_even_trigger_rr
        assert cfg_scalp.break_even_trigger_rr == 1.0
        assert cfg_intra.break_even_trigger_rr == 1.5


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Fallback to self._cfg
# ═══════════════════════════════════════════════════════════════════════════════

class TestFallback:
    def test_fallback_on_import_error(self):
        """If HorizonManager import fails, falls back to self._cfg."""
        cfg = _make_cfg(break_even_trigger_rr=99.0)
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="SCALP")

        with patch.dict("sys.modules", {"core.horizon.horizon_manager": None}):
            resolved = tm._resolve_config_for_position(pos)
            assert resolved.break_even_trigger_rr == 99.0

    def test_fallback_on_runtime_error(self):
        """If HorizonManager raises at runtime, falls back to self._cfg."""
        cfg = _make_cfg(max_time_in_trade_seconds=999.0)
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="SCALP")

        with patch("core.horizon.horizon_manager.get_horizon_manager", side_effect=RuntimeError("boom")):
            resolved = tm._resolve_config_for_position(pos)
            assert resolved.max_time_in_trade_seconds == 999.0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Recovered Positions Resolve Correct Profile
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecoveredPositions:
    def test_recovered_position_defaults_to_scalp_profile(self):
        """Position without explicit trade_horizon gets SCALP profile."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)

        # Simulate a recovered position (default horizon)
        pos = Position(
            position_id="pos_recovered",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.1,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            # trade_horizon defaults to "SCALP"
        )

        resolved = tm._resolve_config_for_position(pos)
        assert resolved is not None
        # Should match global config (SCALP profile reads from same TM_* values)
        from core import config
        assert resolved.break_even_trigger_rr == float(getattr(config, "TM_BREAK_EVEN_TRIGGER_RR", 0.0))

    def test_recovered_position_managed_correctly(self):
        """Recovered position (default SCALP) gets managed with break-even as before."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()

        profile = get_horizon_manager().get_profile("SCALP")
        cfg = TradeManagementConfig(
            break_even_trigger_rr=profile.break_even_trigger_rr,
            break_even_buffer_rr=profile.break_even_buffer_rr,
            trailing_step=profile.trailing_step,
            trailing_start_rr=profile.trailing_start_rr,
            partial_tp_fraction=profile.partial_tp_fraction,
            partial_tp_path_fraction=profile.partial_tp_path_fraction,
            max_time_in_trade_seconds=profile.max_time_in_trade_seconds,
        )
        tm = TradeStateManager(cfg)

        pos = Position(
            position_id="pos_recovery_be",
            symbol="GBPUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.3300,
            initial_sl=1.3290,
            initial_tp=1.3320,
            stop_loss=1.3290,
            take_profit=1.3320,
            volume=0.01,
            open_time=1000.0,
            # trade_horizon defaults to "SCALP"
        )
        tm._by_id[pos.position_id] = pos

        # Move price to > 1R profit (risk = 0.0010, so 1R = 1.3310)
        tm.on_price_update("GBPUSD", bid=1.3311, ask=1.3312, time_s=1500.0)

        if profile.break_even_trigger_rr > 0:
            # SL should have moved from initial toward entry (break-even)
            assert pos.stop_loss > pos.initial_sl

        reset_horizon_manager()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Future Activation Without Manager Changes
# ═══════════════════════════════════════════════════════════════════════════════

class TestFutureActivation:
    def test_intraday_position_resolves_profile(self):
        """An INTRADAY position resolves its profile without any manager changes."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="INTRADAY")

        resolved = tm._resolve_config_for_position(pos)
        # Should resolve successfully (INTRADAY profile exists in HorizonManager)
        assert resolved is not None
        assert isinstance(resolved, TradeManagementConfig)

    def test_extended_position_resolves_profile(self):
        """An EXTENDED position resolves its profile without any manager changes."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="EXTENDED")

        resolved = tm._resolve_config_for_position(pos)
        assert resolved is not None
        assert isinstance(resolved, TradeManagementConfig)

    def test_unknown_horizon_falls_back_gracefully(self):
        """Unknown horizon falls back to SCALP profile via HorizonManager."""
        cfg = _make_cfg()
        tm = TradeStateManager(cfg)
        pos = _make_position(horizon="UNKNOWN_FUTURE")

        resolved = tm._resolve_config_for_position(pos)
        # HorizonManager.get_profile falls back to SCALP for invalid horizons
        assert resolved is not None
        assert isinstance(resolved, TradeManagementConfig)
