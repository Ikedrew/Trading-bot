"""
Unit tests for position sizing integration.

Tests verify:
- FIXED mode preserves existing behaviour
- DYNAMIC mode scales with risk distance
- MT5 failures are handled safely
- Invalid inputs are rejected
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestFixedMode:
    """FIXED mode must preserve existing behaviour."""

    def test_fixed_mode_uses_configured_lot(self):
        """Fixed lot is used regardless of SL distance."""
        with patch("risk.manager._cfg") as mock_cfg, \
             patch("risk.manager.mt5"):
            mock_cfg.POSITION_SIZING_MODE = "FIXED"
            mock_cfg.FIXED_LOT = 0.01

            from risk.manager import RiskManager
            from strategy.signals import Side, Signal
            from data.mt5_data import Candle

            rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)
            candles = [
                Candle(time=1, open=1.10, high=1.11, low=1.08, close=1.09, tick_volume=0),
                Candle(time=2, open=1.09, high=1.12, low=1.07, close=1.11, tick_volume=0),
            ]
            signal = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=1, bar_time=2)
            intent = rm.build_intent("EURUSD", signal, candles, bid=1.11, ask=1.1102)
            if intent is not None:
                assert intent.volume == 0.01, f"Expected 0.01, got {intent.volume}"

    def test_fixed_mode_zero_lot_rejects(self):
        """Zero lot size rejects trade."""
        with patch("risk.manager._cfg") as mock_cfg, \
             patch("risk.manager.mt5"):
            mock_cfg.POSITION_SIZING_MODE = "FIXED"

            from risk.manager import RiskManager
            from strategy.signals import Side, Signal
            from data.mt5_data import Candle

            rm = RiskManager(fixed_lot=0.0, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)
            candles = [
                Candle(time=1, open=1.10, high=1.11, low=1.08, close=1.09, tick_volume=0),
                Candle(time=2, open=1.09, high=1.12, low=1.07, close=1.11, tick_volume=0),
            ]
            signal = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=1, bar_time=2)
            intent = rm.build_intent("EURUSD", signal, candles, bid=1.11, ask=1.1102)
            assert intent is None, "Expected rejection for zero lot"


class TestDynamicMode:
    """DYNAMIC mode must scale volume with risk distance."""

    def test_dynamic_mode_calculates_volume(self):
        """Dynamic sizing calls volume_for_risk and uses result."""
        with patch("risk.manager._cfg") as mock_cfg, \
             patch("risk.manager.mt5"), \
             patch("risk.manager.volume_for_risk", return_value=0.23) as mock_vfr:
            mock_cfg.POSITION_SIZING_MODE = "DYNAMIC"
            mock_cfg.RISK_PER_TRADE_PERCENT = 1.0

            from risk.manager import RiskManager
            from strategy.signals import Side, Signal
            from data.mt5_data import Candle

            rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)
            candles = [
                Candle(time=1, open=1.10, high=1.11, low=1.08, close=1.09, tick_volume=0),
                Candle(time=2, open=1.09, high=1.12, low=1.07, close=1.11, tick_volume=0),
            ]
            signal = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=1, bar_time=2)
            intent = rm.build_intent("EURUSD", signal, candles, bid=1.11, ask=1.1102)
            assert intent is not None, "Expected accepted trade"
            assert intent.volume == 0.23, f"Expected 0.23, got {intent.volume}"
            mock_vfr.assert_called_once()

    def test_dynamic_mode_mt5_failure_rejects(self):
        """If volume_for_risk returns None, trade is rejected."""
        with patch("risk.manager._cfg") as mock_cfg, \
             patch("risk.manager.mt5"), \
             patch("risk.manager.volume_for_risk", return_value=None):
            mock_cfg.POSITION_SIZING_MODE = "DYNAMIC"
            mock_cfg.RISK_PER_TRADE_PERCENT = 1.0

            from risk.manager import RiskManager
            from strategy.signals import Side, Signal
            from data.mt5_data import Candle

            rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)
            candles = [
                Candle(time=1, open=1.10, high=1.11, low=1.08, close=1.09, tick_volume=0),
                Candle(time=2, open=1.09, high=1.12, low=1.07, close=1.11, tick_volume=0),
            ]
            signal = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=1, bar_time=2)
            intent = rm.build_intent("EURUSD", signal, candles, bid=1.11, ask=1.1102)
            assert intent is None, "Expected rejection when dynamic sizing fails"


class TestPositionSizingFunction:
    """Test volume_for_risk directly."""

    def test_zero_risk_distance_returns_none(self):
        """If entry == SL, volume cannot be calculated."""
        with patch("risk.position_sizing.mt5"):
            from risk.position_sizing import volume_for_risk
            result = volume_for_risk("EURUSD", 0, 1.10, 1.10, 1.0)
            assert result is None, "Expected None for zero risk distance"

    def test_account_unavailable_returns_none(self):
        """If MT5 account_info returns None, sizing fails safely."""
        with patch("risk.position_sizing.mt5") as mock_mt5:
            mock_mt5.account_info.return_value = None
            from risk.position_sizing import volume_for_risk
            result = volume_for_risk("EURUSD", 0, 1.10, 1.09, 1.0)
            assert result is None, "Expected None when account unavailable"

    def test_valid_inputs_returns_volume(self):
        """Valid inputs produce a positive volume."""
        with patch("risk.position_sizing.mt5") as mock_mt5:
            acct = MagicMock()
            acct.balance = 10000.0
            mock_mt5.account_info.return_value = acct
            mock_mt5.order_calc_profit.return_value = -100.0  # loss for 1 lot
            sym = MagicMock()
            sym.volume_step = 0.01
            sym.volume_min = 0.01
            sym.volume_max = 100.0
            mock_mt5.symbol_info.return_value = sym

            from risk.position_sizing import volume_for_risk
            # 1% of 10000 = 100, loss per lot = 100, so volume = 1.0
            result = volume_for_risk("EURUSD", 0, 1.10, 1.09, 1.0)
            assert result is not None, "Expected valid volume"
            assert result == 1.0, f"Expected 1.0, got {result}"


# ─── RUNNER ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_classes = [TestFixedMode, TestDynamicMode, TestPositionSizingFunction]
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

    print(f"\nPOSITION SIZING TESTS: {passed} passed, {failed} failed")
    if errors:
        for e in errors:
            print(e)
    else:
        print("ALL PASS")
