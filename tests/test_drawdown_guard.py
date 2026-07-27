"""
Unit tests for risk/drawdown_guard.py — verify drawdown protection logic.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk.drawdown_guard import DrawdownGuard, REJECT_MAX_DRAWDOWN_EXCEEDED, REJECT_ACCOUNT_STATE_UNKNOWN


class TestBelowThreshold:
    def test_below_threshold_allows_trading(self):
        """Equity above threshold → trading allowed."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            acct = MagicMock()
            acct.equity = 9500.0  # 5% drawdown from peak of 10000
            mock_mt5.account_info.return_value = acct

            # Set peak first
            guard._peak_equity = 10000.0
            result = guard.check()
            assert result.allowed is True
            assert result.drawdown_pct < 10.0


class TestAboveThreshold:
    def test_above_threshold_blocks_trading(self):
        """Equity below threshold → trading blocked."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            acct = MagicMock()
            acct.equity = 8900.0  # 11% drawdown from peak of 10000
            mock_mt5.account_info.return_value = acct

            guard._peak_equity = 10000.0
            result = guard.check()
            assert result.allowed is False
            assert result.reason == REJECT_MAX_DRAWDOWN_EXCEEDED
            assert result.drawdown_pct > 10.0

    def test_exactly_at_threshold_blocks(self):
        """Exactly at threshold → blocked (>= comparison)."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            acct = MagicMock()
            acct.equity = 9000.0  # exactly 10%
            mock_mt5.account_info.return_value = acct

            guard._peak_equity = 10000.0
            result = guard.check()
            assert result.allowed is False


class TestHighWatermark:
    def test_peak_updates_on_new_high(self):
        """New equity high updates peak."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            acct = MagicMock()
            acct.equity = 11000.0
            mock_mt5.account_info.return_value = acct

            guard._peak_equity = 10000.0
            result = guard.check()
            assert guard.peak_equity == 11000.0
            assert result.allowed is True

    def test_peak_does_not_decrease(self):
        """Peak never decreases when equity drops."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            acct = MagicMock()
            acct.equity = 9500.0
            mock_mt5.account_info.return_value = acct

            guard._peak_equity = 10000.0
            guard.check()
            assert guard.peak_equity == 10000.0


class TestMT5Failure:
    def test_account_info_none_blocks_trading(self):
        """MT5 returns None → fail safe, block trading."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            mock_mt5.account_info.return_value = None
            mock_mt5.last_error.return_value = (-1, "no connection")

            result = guard.check()
            assert result.allowed is False
            assert result.reason == REJECT_ACCOUNT_STATE_UNKNOWN

    def test_account_info_exception_blocks_trading(self):
        """MT5 raises exception → fail safe, block trading."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg, \
             patch("risk.drawdown_guard.mt5") as mock_mt5:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = True
            mock_cfg.MAX_DRAWDOWN_PERCENT = 10.0
            mock_mt5.account_info.side_effect = RuntimeError("crash")

            result = guard.check()
            assert result.allowed is False
            assert result.reason == REJECT_ACCOUNT_STATE_UNKNOWN


class TestGuardDisabled:
    def test_disabled_always_allows(self):
        """When disabled, always returns allowed=True regardless of state."""
        guard = DrawdownGuard()
        with patch("risk.drawdown_guard.config") as mock_cfg:
            mock_cfg.ENABLE_DRAWDOWN_GUARD = False

            result = guard.check()
            assert result.allowed is True


class TestReset:
    def test_reset_clears_peak(self):
        """Reset sets peak to specified value."""
        guard = DrawdownGuard()
        guard._peak_equity = 10000.0
        guard.reset_peak(5000.0)
        assert guard.peak_equity == 5000.0

    def test_reset_to_zero(self):
        """Reset without argument sets peak to 0."""
        guard = DrawdownGuard()
        guard._peak_equity = 10000.0
        guard.reset_peak()
        assert guard.peak_equity == 0.0


# ─── RUNNER ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_classes = [
        TestBelowThreshold, TestAboveThreshold, TestHighWatermark,
        TestMT5Failure, TestGuardDisabled, TestReset,
    ]
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if not method_name.startswith("test_"):
                continue
            try:
                getattr(instance, method_name)()
                passed += 1
            except AssertionError as e:
                failed += 1
                errors.append(f"  FAIL {cls.__name__}.{method_name}: {e}")
            except Exception as e:
                failed += 1
                errors.append(f"  ERROR {cls.__name__}.{method_name}: {type(e).__name__}: {e}")

    print(f"\nDRAWDOWN GUARD TESTS: {passed} passed, {failed} failed")
    if errors:
        for e in errors:
            print(e)
    else:
        print("ALL PASS")
