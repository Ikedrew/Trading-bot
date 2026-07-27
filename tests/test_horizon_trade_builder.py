"""
Tests for Phase 4C.2 — Horizon Trade Builder.

Covers:
    1. SCALP trade construction (M5 geometry)
    2. INTRADAY trade construction (M15 structure)
    3. EXTENDED trade construction (H1 swing levels)
    4. Missing structure data → returns None
    5. Invalid direction handling
    6. RR calculation verification
    7. build_all_horizon_trades
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.horizon.horizon_trade_builder import (
    build_horizon_trade,
    build_all_horizon_trades,
    HorizonTrade,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: SCALP trade construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalpTrade:
    def test_sell_scalp(self):
        trade = build_horizon_trade(
            horizon="SCALP",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            m5_candle_high=1.33730,
        )
        assert trade is not None
        assert trade.horizon == "SCALP"
        assert trade.direction == "SELL"
        assert trade.stop_loss > trade.entry  # SL above entry for SELL
        assert trade.take_profit < trade.entry  # TP below entry for SELL
        assert trade.sl_source == "M5_CANDLE_GEOMETRY"
        assert trade.rr == 2.0

    def test_buy_scalp(self):
        trade = build_horizon_trade(
            horizon="SCALP",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.10000,
            m5_candle_low=1.09970,
        )
        assert trade is not None
        assert trade.stop_loss < trade.entry  # SL below entry for BUY
        assert trade.take_profit > trade.entry  # TP above entry for BUY

    def test_scalp_rr_correct(self):
        trade = build_horizon_trade(
            horizon="SCALP",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            m5_candle_high=1.33720,
        )
        assert trade is not None
        risk = trade.stop_loss - trade.entry
        reward = trade.entry - trade.take_profit
        assert abs(reward / risk - 2.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: INTRADAY trade construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntradayTrade:
    def test_sell_intraday(self):
        trade = build_horizon_trade(
            horizon="INTRADAY",
            symbol="NZDUSD",
            direction="SELL",
            entry_price=0.58000,
            m15_nearest_resistance=0.58050,
        )
        assert trade is not None
        assert trade.horizon == "INTRADAY"
        assert trade.stop_loss > trade.entry
        assert trade.take_profit < trade.entry
        assert trade.sl_source == "M15_STRUCTURE"
        assert trade.rr == 3.0

    def test_buy_intraday(self):
        trade = build_horizon_trade(
            horizon="INTRADAY",
            symbol="AUDUSD",
            direction="BUY",
            entry_price=0.70000,
            m15_nearest_support=0.69940,
        )
        assert trade is not None
        assert trade.stop_loss < trade.entry
        assert trade.take_profit > trade.entry
        assert trade.rr == 3.0

    def test_intraday_rr_correct(self):
        trade = build_horizon_trade(
            horizon="INTRADAY",
            symbol="NZDUSD",
            direction="SELL",
            entry_price=0.58000,
            m15_nearest_resistance=0.58100,
        )
        assert trade is not None
        risk = trade.stop_loss - trade.entry
        reward = trade.entry - trade.take_profit
        assert abs(reward / risk - 3.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: EXTENDED trade construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedTrade:
    def test_sell_extended(self):
        trade = build_horizon_trade(
            horizon="EXTENDED",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            h1_last_swing_high=1.33900,
        )
        assert trade is not None
        assert trade.horizon == "EXTENDED"
        assert trade.stop_loss > trade.entry
        assert trade.take_profit < trade.entry
        assert trade.sl_source == "H1_SWING_STRUCTURE"
        assert trade.rr == 4.0

    def test_buy_extended(self):
        trade = build_horizon_trade(
            horizon="EXTENDED",
            symbol="USDJPY",
            direction="BUY",
            entry_price=163.200,
            h1_last_swing_low=163.000,
        )
        assert trade is not None
        assert trade.stop_loss < trade.entry
        assert trade.take_profit > trade.entry
        assert trade.rr == 4.0

    def test_extended_rr_correct(self):
        trade = build_horizon_trade(
            horizon="EXTENDED",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            h1_last_swing_high=1.33800,
        )
        assert trade is not None
        risk = trade.stop_loss - trade.entry
        reward = trade.entry - trade.take_profit
        assert abs(reward / risk - 4.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Missing structure data → None
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingData:
    def test_scalp_missing_candle_high(self):
        trade = build_horizon_trade(
            horizon="SCALP", symbol="GBPUSD", direction="SELL",
            entry_price=1.33700, m5_candle_high=None,
        )
        assert trade is None

    def test_intraday_missing_resistance(self):
        trade = build_horizon_trade(
            horizon="INTRADAY", symbol="GBPUSD", direction="SELL",
            entry_price=1.33700, m15_nearest_resistance=None,
        )
        assert trade is None

    def test_extended_missing_swing_high(self):
        trade = build_horizon_trade(
            horizon="EXTENDED", symbol="GBPUSD", direction="SELL",
            entry_price=1.33700, h1_last_swing_high=None,
        )
        assert trade is None

    def test_buy_scalp_missing_candle_low(self):
        trade = build_horizon_trade(
            horizon="SCALP", symbol="EURUSD", direction="BUY",
            entry_price=1.10000, m5_candle_low=None,
        )
        assert trade is None

    def test_zero_risk_returns_none(self):
        """SL at entry = zero risk → returns None."""
        trade = build_horizon_trade(
            horizon="SCALP", symbol="GBPUSD", direction="SELL",
            entry_price=1.33700, m5_candle_high=1.33680,  # Below entry
        )
        assert trade is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Invalid direction
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvalidDirection:
    def test_invalid_direction_returns_none(self):
        trade = build_horizon_trade(
            horizon="SCALP", symbol="GBPUSD", direction="INVALID",
            entry_price=1.33700, m5_candle_high=1.33750,
        )
        assert trade is None

    def test_unknown_horizon_returns_none(self):
        trade = build_horizon_trade(
            horizon="WEEKLY", symbol="GBPUSD", direction="SELL",
            entry_price=1.33700,
        )
        assert trade is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: RR calculation verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestRRVerification:
    def test_risk_distance_matches_sl_entry_gap(self):
        trade = build_horizon_trade(
            horizon="INTRADAY", symbol="NZDUSD", direction="SELL",
            entry_price=0.58000, m15_nearest_resistance=0.58100,
        )
        assert trade is not None
        expected_risk = abs(trade.stop_loss - trade.entry)
        assert abs(trade.risk_distance - expected_risk) < 1e-8

    def test_tp_distance_equals_risk_times_rr(self):
        trade = build_horizon_trade(
            horizon="EXTENDED", symbol="GBPUSD", direction="BUY",
            entry_price=1.33000, h1_last_swing_low=1.32800,
        )
        assert trade is not None
        tp_distance = trade.take_profit - trade.entry
        expected = trade.risk_distance * trade.rr
        assert abs(tp_distance - expected) < 1e-8


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: build_all_horizon_trades
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildAll:
    def test_builds_multiple_horizons(self):
        trades = build_all_horizon_trades(
            eligible_horizons=["SCALP", "INTRADAY", "EXTENDED"],
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            m5_candle_high=1.33730,
            m15_nearest_resistance=1.33800,
            h1_last_swing_high=1.33950,
        )
        assert len(trades) == 3
        horizons = [t.horizon for t in trades]
        assert "SCALP" in horizons
        assert "INTRADAY" in horizons
        assert "EXTENDED" in horizons

    def test_skips_horizons_missing_data(self):
        trades = build_all_horizon_trades(
            eligible_horizons=["SCALP", "INTRADAY", "EXTENDED"],
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
            m5_candle_high=1.33730,
            # M15 and H1 data missing
        )
        assert len(trades) == 1
        assert trades[0].horizon == "SCALP"

    def test_empty_eligible_returns_empty(self):
        trades = build_all_horizon_trades(
            eligible_horizons=[],
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.33700,
        )
        assert trades == []

    def test_serialization(self):
        trade = build_horizon_trade(
            horizon="INTRADAY", symbol="NZDUSD", direction="SELL",
            entry_price=0.58000, m15_nearest_resistance=0.58050,
        )
        assert trade is not None
        d = trade.to_dict()
        assert d["horizon"] == "INTRADAY"
        assert d["sl_source"] == "M15_STRUCTURE"
        assert d["rr"] == 3.0
        assert isinstance(d["reasoning"], list)
