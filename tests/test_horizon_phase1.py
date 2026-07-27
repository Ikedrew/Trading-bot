"""
Phase 1 Horizon Identity & Profile Foundation — Tests.

Validates:
    1. HorizonExecutionProfile creation and serialization
    2. HorizonManager resolution, validation, permitted horizons
    3. Position.trade_horizon propagation from OrderIntent.metadata
    4. TradeRecord.trade_horizon propagation from Position
    5. Execution intent dict carries horizon
    6. Default behaviour preserved (SCALP everywhere)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HorizonExecutionProfile tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonExecutionProfile:
    def test_profile_creation(self):
        from core.horizon.horizon_execution_profile import HorizonExecutionProfile
        profile = HorizonExecutionProfile(name="SCALP")
        assert profile.name == "SCALP"
        assert profile.break_even_trigger_rr == 0.0
        assert profile.max_time_in_trade_seconds == 0.0

    def test_profile_frozen(self):
        from core.horizon.horizon_execution_profile import HorizonExecutionProfile
        profile = HorizonExecutionProfile(name="SCALP")
        with pytest.raises(Exception):
            profile.name = "INTRADAY"  # type: ignore

    def test_profile_to_dict(self):
        from core.horizon.horizon_execution_profile import HorizonExecutionProfile
        profile = HorizonExecutionProfile(
            name="INTRADAY",
            break_even_trigger_rr=1.5,
            max_time_in_trade_seconds=28800.0,
            typical_rr=3.0,
        )
        d = profile.to_dict()
        assert d["name"] == "INTRADAY"
        assert d["break_even_trigger_rr"] == 1.5
        assert d["max_time_in_trade_seconds"] == 28800.0
        assert d["typical_rr"] == 3.0

    def test_default_profiles_exist(self):
        from core.horizon.horizon_execution_profile import DEFAULT_PROFILES
        assert "SCALP" in DEFAULT_PROFILES
        assert "INTRADAY" in DEFAULT_PROFILES
        assert "EXTENDED" in DEFAULT_PROFILES

    def test_policy_retrieval_none(self):
        from core.horizon.horizon_execution_profile import HorizonExecutionProfile
        profile = HorizonExecutionProfile(name="SCALP")
        assert profile.get_policy("break_even") is None

    def test_policy_retrieval_registered(self):
        from core.horizon.horizon_execution_profile import HorizonExecutionProfile
        mock_policy = MagicMock()
        profile = HorizonExecutionProfile(name="SCALP", _policies={"break_even": mock_policy})
        assert profile.get_policy("break_even") is mock_policy


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HorizonManager tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonManager:
    def setup_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def teardown_method(self):
        from core.horizon.horizon_manager import reset_horizon_manager
        reset_horizon_manager()

    def test_get_profile_scalp(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("SCALP")
        assert profile.name == "SCALP"

    def test_get_profile_intraday(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("INTRADAY")
        assert profile.name == "INTRADAY"

    def test_get_profile_extended(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("EXTENDED")
        assert profile.name == "EXTENDED"

    def test_get_profile_case_insensitive(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("scalp")
        assert profile.name == "SCALP"

    def test_get_profile_invalid_falls_back_to_scalp(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("INVALID_HORIZON")
        assert profile.name == "SCALP"

    def test_get_profile_empty_falls_back_to_scalp(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profile = mgr.get_profile("")
        assert profile.name == "SCALP"

    def test_is_permitted_scalp_default(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.is_permitted("SCALP") is True

    def test_is_not_permitted_intraday_default(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.is_permitted("INTRADAY") is False

    def test_validate_horizon_valid(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.validate_horizon("INTRADAY") == "INTRADAY"
        assert mgr.validate_horizon("scalp") == "SCALP"

    def test_validate_horizon_invalid(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        assert mgr.validate_horizon("GARBAGE") == "SCALP"

    def test_all_profiles_populated(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr = get_horizon_manager()
        profiles = mgr.all_profiles
        assert len(profiles) == 3
        assert set(profiles.keys()) == {"SCALP", "INTRADAY", "EXTENDED"}

    def test_profiles_use_config_values(self):
        """Phase 3B: profiles have horizon-specific TM values from HORIZON_TRADE_MANAGEMENT."""
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()
        mgr = get_horizon_manager()
        scalp = mgr.get_profile("SCALP")
        intraday = mgr.get_profile("INTRADAY")
        extended = mgr.get_profile("EXTENDED")
        # Phase 3B: profiles have DIFFERENT management parameters
        # SCALP BE=1.0, INTRADAY BE=1.5, EXTENDED BE=2.0
        assert scalp.break_even_trigger_rr == 1.0
        assert intraday.break_even_trigger_rr == 1.5
        assert extended.break_even_trigger_rr == 2.0
        reset_horizon_manager()

    def test_singleton_returns_same_instance(self):
        from core.horizon.horizon_manager import get_horizon_manager
        mgr1 = get_horizon_manager()
        mgr2 = get_horizon_manager()
        assert mgr1 is mgr2


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Position.trade_horizon propagation
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionHorizonPropagation:
    def test_position_default_horizon(self):
        from core.trade_management.position import Position, PositionStatus
        from strategy.signals import Side
        pos = Position(
            position_id="pos_test",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.1,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000000.0,
        )
        assert pos.trade_horizon == "SCALP"

    def test_position_explicit_horizon(self):
        from core.trade_management.position import Position
        from strategy.signals import Side
        pos = Position(
            position_id="pos_test2",
            symbol="GBPUSD",
            side=Side.SELL,
            magic=713001,
            entry_price=1.33,
            initial_sl=1.34,
            initial_tp=1.31,
            stop_loss=1.34,
            take_profit=1.31,
            volume=0.01,
            open_time=1000000.0,
            trade_horizon="INTRADAY",
        )
        assert pos.trade_horizon == "INTRADAY"

    def test_register_from_execution_propagates_horizon(self):
        """
        register_from_execution reads intent.metadata['horizon']
        and sets Position.trade_horizon.
        """
        from core.trade_management.manager import TradeStateManager, TradeManagementConfig
        from risk.models import OrderIntent
        from strategy.signals import Side
        from unittest.mock import MagicMock

        cfg = TradeManagementConfig(
            break_even_trigger_rr=0.0,
            break_even_buffer_rr=0.0,
            trailing_step=0.0,
            trailing_start_rr=0.0,
            partial_tp_fraction=0.0,
            partial_tp_path_fraction=0.0,
            max_time_in_trade_seconds=0.0,
        )
        mgr = TradeStateManager(cfg)

        intent = OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.10,
            sl=1.09,
            tp=1.12,
            pattern="HAMMER",
            metadata={"horizon": "INTRADAY"},
        )

        execution = MagicMock()
        execution.ok = True
        execution.deal = 12345
        execution.order = 67890

        pos = mgr.register_from_execution(
            intent,
            magic=713001,
            execution=execution,
            entry_fill_price=1.1001,
            bid=1.1000,
            ask=1.1002,
        )

        assert pos is not None
        assert pos.trade_horizon == "INTRADAY"

    def test_register_from_execution_missing_metadata_defaults_scalp(self):
        """If metadata is empty dict or missing horizon key, defaults to SCALP."""
        from core.trade_management.manager import TradeStateManager, TradeManagementConfig
        from risk.models import OrderIntent
        from strategy.signals import Side
        from unittest.mock import MagicMock

        cfg = TradeManagementConfig(
            break_even_trigger_rr=0.0,
            break_even_buffer_rr=0.0,
            trailing_step=0.0,
            trailing_start_rr=0.0,
            partial_tp_fraction=0.0,
            partial_tp_path_fraction=0.0,
            max_time_in_trade_seconds=0.0,
        )
        mgr = TradeStateManager(cfg)

        # OrderIntent with empty metadata (no horizon key)
        intent = OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.10,
            sl=1.09,
            tp=1.12,
            pattern="HAMMER",
        )

        execution = MagicMock()
        execution.ok = True
        execution.deal = 12346
        execution.order = 67891

        pos = mgr.register_from_execution(
            intent,
            magic=713001,
            execution=execution,
            entry_fill_price=1.1001,
            bid=1.1000,
            ask=1.1002,
        )

        assert pos is not None
        assert pos.trade_horizon == "SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TradeRecord.trade_horizon propagation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeRecordHorizonPropagation:
    def test_trade_record_default_horizon(self):
        from core.trade_journal import TradeRecord
        rec = TradeRecord(
            trade_id="pos_1",
            position_ticket=100,
            symbol="EURUSD",
            magic=713001,
            pattern_name="HAMMER",
            direction="BUY",
            entry_time=1000.0,
            exit_time=2000.0,
            duration_seconds=1000.0,
            entry_price=1.10,
            exit_price=1.11,
            initial_volume=0.01,
            final_volume=0.01,
            realised_pnl=10.0,
            commission=0.5,
            swap=0.0,
            net_pnl=9.5,
            close_reason="tp_hit",
            initial_sl=1.09,
            initial_tp=1.11,
            max_favourable_price=1.11,
            recorded_at_utc="2026-07-23T00:00:00Z",
        )
        assert rec.trade_horizon == "SCALP"

    def test_trade_record_explicit_horizon(self):
        from core.trade_journal import TradeRecord
        rec = TradeRecord(
            trade_id="pos_2",
            position_ticket=200,
            symbol="GBPUSD",
            magic=713001,
            pattern_name="ENGULFING",
            direction="SELL",
            entry_time=1000.0,
            exit_time=5000.0,
            duration_seconds=4000.0,
            entry_price=1.33,
            exit_price=1.32,
            initial_volume=0.01,
            final_volume=0.01,
            realised_pnl=10.0,
            commission=0.5,
            swap=0.0,
            net_pnl=9.5,
            close_reason="tp_hit",
            initial_sl=1.34,
            initial_tp=1.32,
            max_favourable_price=1.32,
            recorded_at_utc="2026-07-23T00:00:00Z",
            trade_horizon="EXTENDED",
        )
        assert rec.trade_horizon == "EXTENDED"

    def test_build_trade_record_propagates_horizon(self):
        from core.trade_journal import build_trade_record
        from core.trade_management.position import Position, PositionStatus
        from strategy.signals import Side

        pos = Position(
            position_id="pos_journal_test",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.10,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            trade_horizon="INTRADAY",
            max_favourable_price=1.115,
        )
        record = build_trade_record(
            position=pos,
            exit_price=1.115,
            exit_time=5000.0,
            close_reason="tp_hit",
        )
        assert record.trade_horizon == "INTRADAY"

    def test_build_trade_record_defaults_scalp_when_missing(self):
        """Backward compat: old Position without trade_horizon field defaults to SCALP."""
        from core.trade_journal import build_trade_record
        from core.trade_management.position import Position
        from strategy.signals import Side

        pos = Position(
            position_id="pos_legacy",
            symbol="EURUSD",
            side=Side.BUY,
            magic=713001,
            entry_price=1.10,
            initial_sl=1.09,
            initial_tp=1.12,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            open_time=1000.0,
            max_favourable_price=1.11,
        )
        record = build_trade_record(
            position=pos,
            exit_price=1.11,
            exit_time=2000.0,
            close_reason="sl_hit",
        )
        assert record.trade_horizon == "SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. OrderIntent carries horizon in metadata
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrderIntentHorizonMetadata:
    def test_order_intent_metadata_contains_horizon(self):
        from risk.models import OrderIntent
        from strategy.signals import Side
        intent = OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.10,
            sl=1.09,
            tp=1.12,
            pattern="HAMMER",
            metadata={"horizon": "SCALP"},
        )
        assert intent.metadata["horizon"] == "SCALP"

    def test_order_intent_empty_metadata_safe(self):
        """OrderIntent without metadata should not break horizon resolution."""
        from risk.models import OrderIntent
        from strategy.signals import Side
        intent = OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.10,
            sl=1.09,
            tp=1.12,
            pattern="HAMMER",
        )
        # Resolve horizon same way register_from_execution does
        horizon = intent.metadata.get("horizon", "SCALP") if intent.metadata else "SCALP"
        assert horizon == "SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Config PERMITTED_HORIZONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigPermittedHorizons:
    def test_permitted_horizons_exists(self):
        from core import config
        assert hasattr(config, "PERMITTED_HORIZONS")
        assert "SCALP" in config.PERMITTED_HORIZONS

    def test_permitted_horizons_is_list(self):
        from core import config
        assert isinstance(config.PERMITTED_HORIZONS, list)

    def test_only_scalp_permitted_phase1(self):
        """Phase 1: Only SCALP should be in PERMITTED_HORIZONS."""
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Integration: Full pipeline propagation
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullPipelinePropagation:
    def test_end_to_end_horizon_identity(self):
        """
        Verify: OrderIntent(metadata.horizon) → Position.trade_horizon → TradeRecord.trade_horizon
        """
        from core.trade_management.manager import TradeStateManager, TradeManagementConfig
        from core.trade_journal import build_trade_record
        from risk.models import OrderIntent
        from strategy.signals import Side
        from unittest.mock import MagicMock

        cfg = TradeManagementConfig(
            break_even_trigger_rr=0.0,
            break_even_buffer_rr=0.0,
            trailing_step=0.0,
            trailing_start_rr=0.0,
            partial_tp_fraction=0.0,
            partial_tp_path_fraction=0.0,
            max_time_in_trade_seconds=0.0,
        )
        mgr = TradeStateManager(cfg)

        # Step 1: Create OrderIntent with horizon=EXTENDED
        intent = OrderIntent(
            symbol="USDJPY",
            side=Side.SELL,
            volume=0.02,
            entry_reference=150.0,
            sl=151.0,
            tp=147.0,
            pattern="THREE_BLACK_CROWS",
            metadata={"horizon": "EXTENDED"},
        )

        # Step 2: Register position (simulates broker fill)
        execution = MagicMock()
        execution.ok = True
        execution.deal = 99999
        execution.order = 88888

        pos = mgr.register_from_execution(
            intent,
            magic=713001,
            execution=execution,
            entry_fill_price=150.01,
            bid=150.00,
            ask=150.02,
        )

        assert pos is not None
        assert pos.trade_horizon == "EXTENDED"

        # Step 3: Build trade record (simulates position close)
        record = build_trade_record(
            position=pos,
            exit_price=148.0,
            exit_time=pos.open_time + 86400.0,
            close_reason="tp_hit",
        )

        assert record.trade_horizon == "EXTENDED"

    def test_horizon_manager_resolves_position_profile(self):
        """
        Verify: Position.trade_horizon → HorizonManager → correct profile.
        This is the architectural invariant: every Position has exactly one profile.
        """
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        from core.trade_management.position import Position
        from strategy.signals import Side

        reset_horizon_manager()

        pos = Position(
            position_id="pos_profile_test",
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
            trade_horizon="INTRADAY",
        )

        manager = get_horizon_manager()
        profile = manager.get_profile(pos.trade_horizon)

        assert profile.name == "INTRADAY"
        assert profile.typical_rr == 3.0

        reset_horizon_manager()
