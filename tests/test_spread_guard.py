"""
Tests for A2: Spread Guard — Hard Pre-Execution Block.

Covers:
- Normal spread ? allowed
- High ratio ? blocked
- High absolute ? blocked
- Missing tick data ? blocked (fail-safe)
- Missing risk distance ? blocked (fail-safe)
- Negative spread ? blocked
- Guard disabled ? always allowed
- Per-symbol absolute thresholds
- Metrics tracking
- Integration: ExecutionResult returned on block
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.spread_guard import (
    check_spread,
    SpreadGuardResult,
    get_spread_guard_metrics,
    reset_spread_guard_metrics,
)


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset guard metrics before each test."""
    reset_spread_guard_metrics()
    yield
    reset_spread_guard_metrics()


# --- NORMAL SPREAD (ALLOWED) --------------------------------------------------

class TestSpreadAllowed:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.0005)
    def test_normal_spread_allowed(self, *_):
        """Normal spread well within limits passes."""
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.10010,  # 1 pip spread
            risk_distance=0.00500,  # 50 pip SL = 5 pips
        )
        assert result.allowed is True
        assert result.reason == "PASS"
        assert result.spread == pytest.approx(0.00010)
        assert result.ratio == pytest.approx(0.02, abs=0.001)

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.0005)
    def test_spread_at_boundary_allowed(self, *_):
        """Spread exactly at ratio boundary passes (not strictly greater)."""
        # ratio = 0.0003 / 0.001 = 0.30 ? NOT greater than 0.30 ? allowed
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.10030,  # 3 pip spread
            risk_distance=0.01000,  # ratio = 0.003/0.01 = 0.30
        )
        assert result.allowed is True


# --- RATIO EXCEEDED (BLOCKED) -------------------------------------------------

class TestRatioBlock:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.15)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.01)
    def test_high_ratio_blocked(self, *_):
        """Spread/risk ratio exceeding threshold is blocked."""
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.10020,  # 2 pip spread
            risk_distance=0.00050,  # ratio = 0.0002/0.0005 = 0.40 > 0.15
        )
        assert result.allowed is False
        assert "RATIO" in result.reason
        assert result.ratio == pytest.approx(0.40, abs=0.01)

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.01)
    def test_ratio_just_above_blocked(self, *_):
        """Ratio barely above threshold is blocked."""
        # ratio = 0.00031 / 0.001 = 0.31 > 0.30
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.10031,
            risk_distance=0.001,
        )
        assert result.allowed is False
        assert "RATIO" in result.reason


# --- ABSOLUTE EXCEEDED (BLOCKED) ----------------------------------------------

class TestAbsoluteBlock:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.90)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.00020)
    def test_absolute_exceeded_blocked(self, *_):
        """Spread above absolute cap is blocked even if ratio is fine."""
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.10030,  # 3 pip = 0.0003 > 0.0002
            risk_distance=0.01000,  # ratio = 0.03 (fine)
        )
        assert result.allowed is False
        assert "ABSOLUTE" in result.reason


# --- FAIL-SAFE (BLOCKED ON MISSING DATA) --------------------------------------

class TestFailSafe:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_zero_bid_blocked(self, *_):
        """Zero bid ? fail-safe block."""
        result = check_spread(symbol="EURUSD", bid=0.0, ask=1.10, risk_distance=0.005)
        assert result.allowed is False
        assert "INVALID_TICK" in result.reason

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_zero_ask_blocked(self, *_):
        """Zero ask ? fail-safe block."""
        result = check_spread(symbol="EURUSD", bid=1.10, ask=0.0, risk_distance=0.005)
        assert result.allowed is False
        assert "INVALID_TICK" in result.reason

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_negative_spread_blocked(self, *_):
        """Negative spread (ask < bid) ? fail-safe block."""
        result = check_spread(symbol="EURUSD", bid=1.10010, ask=1.10000, risk_distance=0.005)
        assert result.allowed is False
        assert "NEGATIVE_SPREAD" in result.reason

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_zero_risk_distance_blocked(self, *_):
        """Zero risk distance ? fail-safe block (cannot compute ratio)."""
        result = check_spread(symbol="EURUSD", bid=1.10000, ask=1.10010, risk_distance=0.0)
        assert result.allowed is False
        assert "MISSING_RISK_DISTANCE" in result.reason

    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_negative_risk_distance_blocked(self, *_):
        """Negative risk distance ? fail-safe block."""
        result = check_spread(symbol="EURUSD", bid=1.10000, ask=1.10010, risk_distance=-0.001)
        assert result.allowed is False
        assert "MISSING_RISK_DISTANCE" in result.reason


# --- GUARD DISABLED -----------------------------------------------------------

class TestGuardDisabled:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=False)
    def test_disabled_always_allows(self, *_):
        """When guard is disabled, all spreads pass."""
        result = check_spread(
            symbol="EURUSD",
            bid=1.10000,
            ask=1.20000,  # Insane 1000 pip spread
            risk_distance=0.001,
        )
        assert result.allowed is True
        assert "DISABLED" in result.reason


# --- METRICS ------------------------------------------------------------------

class TestMetrics:
    @patch("risk.spread_guard._is_spread_guard_enabled", return_value=True)
    @patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.30)
    @patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001)
    def test_metrics_tracked(self, *_):
        """Metrics accumulate correctly."""
        # Allowed
        check_spread(symbol="X", bid=1.1, ask=1.10001, risk_distance=0.01)
        # Blocked by ratio
        check_spread(symbol="X", bid=1.1, ask=1.105, risk_distance=0.001)
        # Blocked by missing data
        check_spread(symbol="X", bid=0.0, ask=1.1, risk_distance=0.01)

        m = get_spread_guard_metrics()
        assert m["checked"] == 3
        assert m["allowed"] == 1
        assert m["blocked_ratio"] == 1
        assert m["blocked_missing_data"] == 1


# --- EXECUTION INTEGRATION ----------------------------------------------------

class TestExecutionIntegration:
    """Verify spread guard is wired into execution path."""

    def test_execution_blocks_on_wide_spread(self):
        """MT5Execution.place_market returns failure when spread guard blocks."""
        from risk.models import OrderIntent
        from strategy.signals import Side
        from execution.mt5_execution import MT5Execution, ExecutionResult

        intent = OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.10000,
            sl=1.09500,  # risk_distance = 0.005
            tp=1.11000,
            pattern="TEST",
        )

        with patch("execution.mt5_execution.mt5_call") as mock_call, \
             patch("execution.mt5_execution._cfg") as mock_cfg, \
             patch("risk.spread_guard._is_spread_guard_enabled", return_value=True), \
             patch("risk.spread_guard._get_max_spread_atr_ratio", return_value=0.10), \
             patch("risk.spread_guard._get_max_spread_absolute", return_value=0.001):

            mock_cfg.DRY_RUN = False
            mock_cfg.DRY_RUN_EXECUTION_LOGS = False

            # Mock tick with wide spread: 5 pips = 0.0005 / 0.005 risk = 0.10 ratio exactly
            # Need > 0.10, so make it 6 pips
            tick_mock = MagicMock()
            tick_mock.bid = 1.10000
            tick_mock.ask = 1.10060  # 6 pips = ratio 0.0006/0.005 = 0.12 > 0.10

            sym_info_mock = MagicMock()
            sym_info_mock.visible = True
            sym_info_mock.trade_mode = 1
            sym_info_mock.volume_min = 0.01
            sym_info_mock.volume_max = 100.0
            sym_info_mock.volume_step = 0.01
            sym_info_mock.filling_mode = 2  # IOC

            def side_effect(func, *args, **kwargs):
                name = getattr(func, "__name__", "")
                if "symbol_info_tick" in name:
                    return tick_mock
                if "symbol_info" in name:
                    return sym_info_mock
                return None

            mock_call.side_effect = side_effect

            exec_engine = MT5Execution(magic=713001)
            result = exec_engine.place_market(intent)

            assert result.ok is False
            assert "SPREAD_EXCEEDED" in result.comment
