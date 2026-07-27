"""
Tests for G1: Strategy Identity / Magic Number Registry.

Covers:
- Registry validation (duplicates, missing strategy, empty registry)
- Strategy lookup (correct magic assigned)
- Unknown strategy (startup failure)
- Position isolation (only own magic visible)
- Startup recovery isolation (only own magic recovered)
- Order submission (uses assigned magic)
- Exposure isolation (calculations use only own positions)
- Backward compatibility (config.BOT_MAGIC updated)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.strategy_identity import (
    StrategyIdentity,
    StrategyRegistryError,
    validate_magic_registry,
    resolve_strategy_identity,
    get_identity,
    _active_identity,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_identity():
    """Reset the module-level singleton before each test."""
    import core.strategy_identity as mod
    mod._active_identity = None
    yield
    mod._active_identity = None


def _mock_config(**kwargs):
    """Create a mock config module with specified attributes."""
    mock = MagicMock()
    for key, value in kwargs.items():
        setattr(mock, key, value)
    # Make getattr work correctly
    mock.__class__ = type("MockConfig", (), kwargs)
    return mock


# --- TEST 1: REGISTRY VALIDATION — DUPLICATES ---------------------------------

class TestRegistryValidation:
    def test_duplicate_magic_fails(self, reset_identity):
        """Duplicate magic numbers must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "strategy_a"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "strategy_a": 713001,
            "strategy_b": 713001,  # DUPLICATE
        }

        with patch("core.strategy_identity.config", mock_cfg, create=True), \
             patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "Duplicate magic detected: 713001" in str(exc_info.value)

    def test_empty_registry_fails(self, reset_identity):
        """Empty registry must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "test"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {}

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "missing or empty" in str(exc_info.value)

    def test_no_registry_fails(self, reset_identity):
        """Missing registry attribute must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "test"
        mock_cfg.MAGIC_NUMBER_REGISTRY = None

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "missing or empty" in str(exc_info.value)

    def test_non_integer_magic_fails(self, reset_identity):
        """Non-integer magic numbers must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "test"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "test": "not_an_int",
        }

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "must be integers" in str(exc_info.value)

    def test_valid_registry_passes(self, reset_identity):
        """Valid registry passes validation without error."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "momentum_v1"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "momentum_v1": 713001,
            "mean_reversion_v1": 713002,
            "breakout_v1": 713003,
        }

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            # Should not raise
            validate_magic_registry()


# --- TEST 2: STRATEGY LOOKUP --------------------------------------------------

class TestStrategyLookup:
    def test_correct_magic_assigned(self, reset_identity):
        """Strategy name resolves to correct magic number."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "breakout_v1"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "momentum_v1": 713001,
            "breakout_v1": 713003,
        }
        mock_cfg.BOT_MAGIC = 713001  # Will be overwritten

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            identity = resolve_strategy_identity()

        assert identity.strategy_name == "breakout_v1"
        assert identity.magic_number == 713003

    def test_identity_singleton_set(self, reset_identity):
        """After resolve, get_identity() returns the same object."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "momentum_v1"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {"momentum_v1": 713001}
        mock_cfg.BOT_MAGIC = 713001

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            resolved = resolve_strategy_identity()
            fetched = get_identity()

        assert fetched is resolved
        assert fetched.magic_number == 713001

    def test_config_bot_magic_updated(self, reset_identity):
        """resolve_strategy_identity updates config.BOT_MAGIC for backward compatibility."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "funded_eval_v1"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "funded_eval_v1": 713004,
        }
        mock_cfg.BOT_MAGIC = 713001  # Old value

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            resolve_strategy_identity()

        # BOT_MAGIC should now reflect the resolved magic
        assert mock_cfg.BOT_MAGIC == 713004


# --- TEST 3: UNKNOWN STRATEGY -------------------------------------------------

class TestUnknownStrategy:
    def test_missing_strategy_fails(self, reset_identity):
        """Strategy not in registry must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = "unknown_strategy"
        mock_cfg.MAGIC_NUMBER_REGISTRY = {
            "momentum_v1": 713001,
            "breakout_v1": 713003,
        }

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "unknown_strategy" in str(exc_info.value)
            assert "not found" in str(exc_info.value)

    def test_no_strategy_name_fails(self, reset_identity):
        """Empty/None STRATEGY_NAME must cause startup failure."""
        mock_cfg = MagicMock()
        mock_cfg.STRATEGY_NAME = ""
        mock_cfg.MAGIC_NUMBER_REGISTRY = {"test": 713001}

        with patch.dict("sys.modules", {"core.config": mock_cfg, "core": MagicMock(config=mock_cfg)}):
            with pytest.raises(SystemExit) as exc_info:
                validate_magic_registry()
            assert "STRATEGY_NAME" in str(exc_info.value)


# --- TEST 4: POSITION ISOLATION -----------------------------------------------

class TestPositionIsolation:
    def test_only_own_magic_counted(self, reset_identity):
        """Position count only includes positions matching our magic."""
        from risk.portfolio_exposure_guard import _count_all_bot_positions

        # MT5 returns positions from multiple strategies
        pos_ours = MagicMock(magic=713001)
        pos_other1 = MagicMock(magic=713002)
        pos_other2 = MagicMock(magic=713003)
        all_positions = [pos_ours, pos_ours, pos_other1, pos_other2]

        with patch("risk.portfolio_exposure_guard.mt5_call", return_value=all_positions):
            count = _count_all_bot_positions(713001)

        # Only our 2 positions counted
        assert count == 2

    def test_other_magic_invisible(self, reset_identity):
        """Positions from other strategies are completely invisible."""
        from risk.portfolio_exposure_guard import _count_all_bot_positions

        # Only other strategies' positions exist
        positions = [MagicMock(magic=713002), MagicMock(magic=713003)]

        with patch("risk.portfolio_exposure_guard.mt5_call", return_value=positions):
            count = _count_all_bot_positions(713001)

        assert count == 0


# --- TEST 5: STARTUP RECOVERY ISOLATION ----------------------------------------

class TestStartupRecoveryIsolation:
    def test_only_own_magic_recovered(self, reset_identity):
        """D3 recovery only registers positions matching our magic."""
        from core.runtime.startup_recovery import recover_positions_on_startup
        from core.trade_management import TradeStateManager
        from core.trade_management.config import TradeManagementConfig

        # Create a minimal trade manager
        cfg = TradeManagementConfig(
            break_even_trigger_rr=0, break_even_buffer_rr=0,
            trailing_step=0, trailing_start_rr=0,
            partial_tp_fraction=0, partial_tp_path_fraction=0,
            max_time_in_trade_seconds=0,
        )
        tm = TradeStateManager(cfg)

        # Broker has positions from two strategies
        pos_ours = MagicMock()
        pos_ours.ticket = 1001
        pos_ours.magic = 713001
        pos_ours.symbol = "EURUSD"
        pos_ours.type = 0  # BUY
        pos_ours.price_open = 1.1000
        pos_ours.sl = 1.0900
        pos_ours.tp = 1.1200
        pos_ours.volume = 0.01
        pos_ours.time = 1700000000
        pos_ours.price_current = 1.1050

        pos_other = MagicMock()
        pos_other.ticket = 2001
        pos_other.magic = 713002  # Different strategy
        pos_other.symbol = "EURUSD"
        pos_other.type = 1  # SELL
        pos_other.price_open = 1.1100
        pos_other.sl = 1.1200
        pos_other.tp = 1.0900
        pos_other.volume = 0.02
        pos_other.time = 1700000001
        pos_other.price_current = 1.1050

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[pos_ours, pos_other]):
            recovered = recover_positions_on_startup(
                trade_manager=tm,
                symbol="EURUSD",
                magic=713001,  # Our magic
            )

        # Only our position recovered
        assert recovered == 1
        open_pos = tm.positions_open()
        assert len(open_pos) == 1
        assert open_pos[0].magic == 713001


# --- TEST 6: ORDER SUBMISSION -------------------------------------------------

class TestOrderSubmission:
    def test_execution_uses_assigned_magic(self, reset_identity):
        """MT5Execution stores and uses the assigned magic number."""
        from execution.mt5_execution import MT5Execution

        # Create execution with specific magic
        exec_instance = MT5Execution(magic=713003)
        assert exec_instance._magic == 713003

    def test_different_strategies_different_magic(self, reset_identity):
        """Two execution instances with different magics are independent."""
        from execution.mt5_execution import MT5Execution

        exec_a = MT5Execution(magic=713001)
        exec_b = MT5Execution(magic=713003)

        assert exec_a._magic != exec_b._magic
        assert exec_a._magic == 713001
        assert exec_b._magic == 713003


# --- TEST 7: EXPOSURE ISOLATION ------------------------------------------------

class TestExposureIsolation:
    def test_exposure_only_counts_own_positions(self, reset_identity):
        """Portfolio exposure guard only counts positions for our magic."""
        from risk.portfolio_exposure_guard import check_portfolio_exposure

        # Broker: 2 positions under 713001, 3 under 713002
        all_broker = [
            MagicMock(magic=713001),
            MagicMock(magic=713001),
            MagicMock(magic=713002),
            MagicMock(magic=713002),
            MagicMock(magic=713002),
        ]

        with patch("risk.portfolio_exposure_guard._is_enabled", return_value=True), \
             patch("risk.portfolio_exposure_guard._get_max_positions", return_value=3), \
             patch("risk.portfolio_exposure_guard._get_max_risk_pct", return_value=5.0), \
             patch("risk.portfolio_exposure_guard._get_bot_magic", return_value=713001), \
             patch("risk.portfolio_exposure_guard._get_strict_mode", return_value=True), \
             patch("risk.portfolio_exposure_guard.mt5_call", return_value=all_broker), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):

            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=[MagicMock(), MagicMock()],  # 2 positions from TSM
            )

        # Only 2 positions (our magic) counted — not 5
        assert result.current_positions == 2
        assert result.allowed is True  # 2 < 3 limit


# --- TEST: IDENTITY NOT INITIALIZED -------------------------------------------

class TestIdentityNotInitialized:
    def test_get_identity_before_resolve_raises(self, reset_identity):
        """Calling get_identity() before resolve raises RuntimeError."""
        with pytest.raises(RuntimeError) as exc_info:
            get_identity()
        assert "not initialized" in str(exc_info.value)


# --- TEST: IMMUTABILITY -------------------------------------------------------

class TestImmutability:
    def test_identity_is_frozen(self, reset_identity):
        """StrategyIdentity is frozen — cannot be modified after creation."""
        identity = StrategyIdentity(strategy_name="test", magic_number=99)

        with pytest.raises(Exception):  # FrozenInstanceError
            identity.magic_number = 100
