"""
Tests for A5: Portfolio Exposure Guard.

Covers:
- Position count limit blocks when max reached
- Risk limit blocks when aggregate risk exceeds threshold
- Below limit allows new trade
- Restart recovery (D3 positions reconstruct exposure correctly)
- Fail-closed when MT5 unavailable
- Disabled guard always allows
- Mixed scenarios (positions + risk interaction)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.portfolio_exposure_guard import (
    check_portfolio_exposure,
    PortfolioExposureResult,
    REJECT_POSITION_LIMIT,
    REJECT_RISK_LIMIT,
    REJECT_EXPOSURE_STATE_UNKNOWN,
    _compute_position_risk_pct,
    _count_all_bot_positions,
)


# --- TEST HELPERS -------------------------------------------------------------

class _FakeSide:
    def __init__(self, value: str):
        self.value = value

    def __eq__(self, other):
        if hasattr(other, 'value'):
            return self.value == other.value
        return NotImplemented


@dataclass
class _FakePosition:
    """Minimal position mock for testing."""
    symbol: str
    side: _FakeSide
    entry_price: float
    stop_loss: float
    volume: float
    status: str = "open"

    @property
    def take_profit(self):
        return 0.0


def _pos(symbol: str, side: str, entry: float, sl: float, volume: float = 0.01) -> _FakePosition:
    """Create a fake position for testing."""
    return _FakePosition(
        symbol=symbol,
        side=_FakeSide(side),
        entry_price=entry,
        stop_loss=sl,
        volume=volume,
    )


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config():
    """Set known config defaults for all tests."""
    with patch("risk.portfolio_exposure_guard._is_enabled", return_value=True), \
         patch("risk.portfolio_exposure_guard._get_max_positions", return_value=3), \
         patch("risk.portfolio_exposure_guard._get_max_risk_pct", return_value=3.0), \
         patch("risk.portfolio_exposure_guard._get_bot_magic", return_value=713001), \
         patch("risk.portfolio_exposure_guard._get_strict_mode", return_value=True):
        yield


# --- TEST 1: POSITION COUNT LIMIT ---------------------------------------------

class TestPositionCountLimit:
    def test_blocks_at_position_limit(self, default_config):
        """When 3 positions open, attempting 4th is blocked."""
        # Mock MT5: 3 positions open
        mock_positions = [MagicMock(magic=713001) for _ in range(3)]
        with patch("risk.portfolio_exposure_guard.mt5_call", return_value=mock_positions):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=[_pos("EURUSD", "BUY", 1.1, 1.09),
                                _pos("GBPUSD", "BUY", 1.3, 1.29),
                                _pos("AUDUSD", "SELL", 0.7, 0.71)],
            )

        assert result.allowed is False
        assert result.reason == REJECT_POSITION_LIMIT
        assert result.current_positions == 3
        assert result.max_positions == 3

    def test_allows_below_position_limit(self, default_config):
        """When 2 positions open (< 3 max), new trade allowed."""
        mock_positions = [MagicMock(magic=713001) for _ in range(2)]
        # Mock risk calculation to return fixed 1.0% per position
        with patch("risk.portfolio_exposure_guard.mt5_call") as mock_mt5:
            # First call: positions_get returns 2 positions
            # Subsequent calls: order_calc_profit and account_info for risk calc
            mock_mt5.side_effect = self._mock_mt5_sequence(2, balance=100000, loss_per_pos=-1000)
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=[_pos("EURUSD", "BUY", 1.1, 1.09),
                                _pos("GBPUSD", "BUY", 1.3, 1.29)],
            )

        assert result.allowed is True
        assert result.current_positions == 2

    def test_blocks_other_symbols_when_max_reached(self, default_config):
        """Position limit is portfolio-wide — blocks ANY symbol."""
        mock_positions = [MagicMock(magic=713001) for _ in range(3)]
        with patch("risk.portfolio_exposure_guard.mt5_call", return_value=mock_positions):
            # Even a brand new symbol is blocked
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=[_pos("EURUSD", "BUY", 1.1, 1.09),
                                _pos("GBPUSD", "BUY", 1.3, 1.29),
                                _pos("AUDUSD", "SELL", 0.7, 0.71)],
            )

        assert result.allowed is False
        assert result.reason == REJECT_POSITION_LIMIT

    @staticmethod
    def _mock_mt5_sequence(position_count: int, balance: float, loss_per_pos: float):
        """Create a side_effect function for mt5_call that returns positions first, then risk data."""
        call_count = [0]
        positions = [MagicMock(magic=713001) for _ in range(position_count)]
        account = MagicMock(balance=balance)

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return positions
            # order_calc_profit calls
            if len(args) >= 5:
                return loss_per_pos
            # account_info calls
            return account

        return _side_effect


# --- TEST 2: RISK LIMIT -------------------------------------------------------

class TestRiskLimit:
    def test_blocks_when_projected_risk_exceeds_limit(self, default_config):
        """3 × 1% open + 1% proposed = 4% > 3% limit ? blocked."""
        mock_positions = [MagicMock(magic=713001) for _ in range(3)]
        positions = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "BUY", 1.3000, 1.2900),
            _pos("AUDUSD", "SELL", 0.7000, 0.7100),
        ]

        # Patch: position count < max (allow through count check)
        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=5), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=3), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=positions,
            )

        assert result.allowed is False
        assert result.reason == REJECT_RISK_LIMIT
        assert result.current_risk_pct == 3.0
        assert result.projected_risk_pct == 4.0
        assert result.max_risk_pct == 3.0

    def test_allows_when_projected_within_limit(self, default_config):
        """2 × 1% open + 0.5% proposed = 2.5% < 3% ? allowed."""
        positions = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "BUY", 1.3000, 1.2900),
        ]

        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=5), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=2), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=positions,
            )

        assert result.allowed is True
        assert result.current_risk_pct == 2.0
        assert result.projected_risk_pct == 2.5

    def test_blocks_at_exact_limit(self, default_config):
        """2 × 1% + 1.01% proposed = 3.01% > 3.0% ? blocked."""
        positions = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "BUY", 1.3000, 1.2900),
        ]

        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=5), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=2), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.01,
                open_positions=positions,
            )

        assert result.allowed is False
        assert result.reason == REJECT_RISK_LIMIT

    def test_allows_at_exact_limit(self, default_config):
        """2 × 1% + 1.0% proposed = 3.0% == 3.0% ? allowed (not exceeded)."""
        positions = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "BUY", 1.3000, 1.2900),
        ]

        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=5), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=2), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=positions,
            )

        assert result.allowed is True
        assert result.projected_risk_pct == 3.0


# --- TEST 3: BELOW LIMIT ------------------------------------------------------

class TestBelowLimit:
    def test_empty_portfolio_allows(self, default_config):
        """No open positions ? any trade allowed."""
        with patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=0):
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=[],
            )

        assert result.allowed is True
        assert result.current_positions == 0
        assert result.current_risk_pct == 0.0
        assert result.projected_risk_pct == 0.5

    def test_partial_portfolio_allows(self, default_config):
        """2 positions, 2% risk, proposing 0.5% ? all within limits."""
        positions = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "BUY", 1.3000, 1.2900),
        ]

        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=3), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=2), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=positions,
            )

        assert result.allowed is True
        assert result.current_positions >= 0
        assert result.max_positions > 0
        assert result.current_risk_pct >= 0
        assert result.projected_risk_pct >= 0


# --- TEST 4: RESTART / D3 RECOVERY --------------------------------------------

class TestD3Recovery:
    def test_recovered_positions_counted(self, default_config):
        """D3-recovered positions contribute to exposure calculations."""
        # Simulate recovered positions (pattern_tag="RECOVERED")
        recovered = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "SELL", 1.3000, 1.3100),
            _pos("AUDUSD", "BUY", 0.7000, 0.6900),
        ]

        # Broker confirms 3 positions
        with patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=3), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=recovered,
            )

        # Position count blocks (3 == 3 max)
        assert result.allowed is False
        assert result.reason == REJECT_POSITION_LIMIT
        assert result.current_positions == 3

    def test_recovered_risk_calculated(self, default_config):
        """Recovered positions have their risk computed from SL distance."""
        recovered = [
            _pos("EURUSD", "BUY", 1.1000, 1.0900),
            _pos("GBPUSD", "SELL", 1.3000, 1.3100),
        ]

        with patch("risk.portfolio_exposure_guard._get_max_positions", return_value=5), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=2), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.5):
            result = check_portfolio_exposure(
                proposed_risk_pct=1.0,
                open_positions=recovered,
            )

        # 2 × 1.5% = 3.0% current + 1.0% proposed = 4.0% > 3.0%
        assert result.allowed is False
        assert result.reason == REJECT_RISK_LIMIT
        assert result.current_risk_pct == 3.0
        assert result.projected_risk_pct == 4.0


# --- TEST 5: FAIL-CLOSED ------------------------------------------------------

class TestFailClosed:
    def test_mt5_unavailable_blocks_strict(self, default_config):
        """When MT5 positions_get returns None ? fail-closed in strict mode."""
        with patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=None):
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=[],
            )

        assert result.allowed is False
        assert result.reason == REJECT_EXPOSURE_STATE_UNKNOWN

    def test_mt5_unavailable_lenient_uses_tsm(self, default_config):
        """When MT5 unavailable in lenient mode ? uses TradeStateManager count."""
        positions = [_pos("EURUSD", "BUY", 1.1, 1.09)]

        with patch("risk.portfolio_exposure_guard._get_strict_mode", return_value=False), \
             patch("risk.portfolio_exposure_guard._count_all_bot_positions", return_value=None), \
             patch("risk.portfolio_exposure_guard._compute_position_risk_pct", return_value=1.0):
            result = check_portfolio_exposure(
                proposed_risk_pct=0.5,
                open_positions=positions,
            )

        # Lenient: uses len(open_positions) = 1 as count
        assert result.allowed is True
        assert result.current_positions == 1


# --- TEST: DISABLED GUARD -----------------------------------------------------

class TestDisabledGuard:
    def test_disabled_always_allows(self, default_config):
        """When disabled, all checks pass regardless of exposure."""
        with patch("risk.portfolio_exposure_guard._is_enabled", return_value=False):
            result = check_portfolio_exposure(
                proposed_risk_pct=99.0,
                open_positions=[_pos("X", "BUY", 1.0, 0.5)] * 100,
            )

        assert result.allowed is True
        assert result.reason == "PORTFOLIO_EXPOSURE_GUARD_DISABLED"


# --- TEST: RISK COMPUTATION ---------------------------------------------------

class TestRiskComputation:
    def test_position_with_no_sl_uses_fallback(self, default_config):
        """Position without SL uses config RISK_PER_TRADE_PERCENT."""
        pos = _pos("EURUSD", "BUY", 1.1000, 0.0)  # SL = 0 (no SL)

        from core import config as _cfg
        _original = getattr(_cfg, "RISK_PER_TRADE_PERCENT", 1.0)
        _cfg.RISK_PER_TRADE_PERCENT = 1.0
        try:
            with patch("risk.portfolio_exposure_guard.mt5_call"):
                risk = _compute_position_risk_pct(pos)
            assert risk == 1.0
        finally:
            _cfg.RISK_PER_TRADE_PERCENT = _original

    def test_mt5_calc_failure_uses_fallback(self, default_config):
        """When MT5 order_calc_profit fails, uses fallback."""
        pos = _pos("EURUSD", "BUY", 1.1000, 1.0900)

        from core import config as _cfg
        _original = getattr(_cfg, "RISK_PER_TRADE_PERCENT", 1.0)
        _cfg.RISK_PER_TRADE_PERCENT = 1.0
        try:
            with patch("risk.portfolio_exposure_guard.mt5_call", return_value=None):
                risk = _compute_position_risk_pct(pos)
            assert risk == 1.0
        finally:
            _cfg.RISK_PER_TRADE_PERCENT = _original

    def test_successful_risk_calculation(self, default_config):
        """Proper MT5 risk calculation returns correct percentage."""
        pos = _pos("EURUSD", "BUY", 1.1000, 1.0900)

        account = MagicMock(balance=100000.0)

        def _mock_mt5(*args, **kwargs):
            # order_calc_profit: loss of $500 for this position
            if len(args) >= 5:
                return -500.0
            # account_info
            return account

        with patch("risk.portfolio_exposure_guard.mt5_call", side_effect=_mock_mt5):
            risk = _compute_position_risk_pct(pos)

        # $500 / $100000 × 100 = 0.5%
        assert risk == pytest.approx(0.5, abs=0.01)


# --- TEST: PRODUCTION INTEGRATION VERIFICATION --------------------------------

class TestProductionIntegration:
    def test_guard_ordering_in_pipeline(self):
        """Verify the guard exists in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        # A5 check appears in the guard chain
        a5_pos = source.find("check_portfolio_exposure")

        assert a5_pos > 0, "A5 guard not found in runtime guard chain"

    def test_guard_after_a4(self):
        """Verify A5 runs AFTER A4 daily trade limit."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        a4_pos = source.find("daily_trade_limit.can_open_trade")
        a5_pos = source.find("check_portfolio_exposure")

        assert a4_pos > 0, "A4 guard not found"
        assert a5_pos > 0, "A5 guard not found"
        assert a4_pos < a5_pos, "A5 must appear AFTER A4"
