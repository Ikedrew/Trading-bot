"""
Tests for spread-at-entry capture in shadow trades.

Verifies:
    1. Shadow trade with spread data stores it correctly
    2. Shadow trade without spread handles missing values safely
    3. New fields survive persistence and reload
    4. Existing shadow trade consumers do not fail
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.shadow_trades import ShadowTrade, ShadowTradeEngine


class TestSpreadCapture:
    """Shadow trades store spread/bid/ask at entry."""

    def test_spread_stored_on_open(self):
        """open_trade with spread data stores it on the ShadowTrade."""
        engine = ShadowTradeEngine(max_bars=5)
        trade = engine.open_trade(
            trade_id="spread_test_1",
            cycle_id=100,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.08505,
            stop_loss=1.08400,
            take_profit=1.08700,
            entry_time=1753574400.0,
            spread_at_entry=0.00010,
            bid_at_entry=1.08500,
            ask_at_entry=1.08510,
        )
        assert trade.spread_at_entry == 0.00010
        assert trade.bid_at_entry == 1.08500
        assert trade.ask_at_entry == 1.08510

    def test_spread_defaults_to_zero(self):
        """Shadow trade without spread data defaults to 0.0."""
        engine = ShadowTradeEngine(max_bars=5)
        trade = engine.open_trade(
            trade_id="spread_test_2",
            cycle_id=101,
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.27000,
            stop_loss=1.27100,
            take_profit=1.26700,
            entry_time=1753574400.0,
            # No spread fields provided
        )
        assert trade.spread_at_entry == 0.0
        assert trade.bid_at_entry == 0.0
        assert trade.ask_at_entry == 0.0

    def test_spread_in_persisted_record(self):
        """Spread fields appear in the persisted truth record."""
        engine = ShadowTradeEngine(max_bars=3)
        engine.open_trade(
            trade_id="spread_test_3",
            cycle_id=102,
            symbol="USDJPY",
            direction="BUY",
            entry_price=150.005,
            stop_loss=149.900,
            take_profit=150.200,
            entry_time=1753574400.0,
            spread_at_entry=0.020,
            bid_at_entry=149.995,
            ask_at_entry=150.015,
        )

        # Simulate bars to close the trade
        closed = engine.evaluate_bar(
            symbol="USDJPY",
            bar_high=150.100,
            bar_low=149.850,  # Below SL
            bar_close=149.870,
            bar_time=1753574700.0,
        )

        assert len(closed) == 1
        record = closed[0]
        ds = record["decision_snapshot"]
        assert ds["spread_at_entry"] == 0.02
        assert ds["bid_at_entry"] == 149.995
        assert ds["ask_at_entry"] == 150.015

    def test_spread_none_when_zero(self):
        """When spread is 0 (not provided), persisted value is None."""
        engine = ShadowTradeEngine(max_bars=2)
        engine.open_trade(
            trade_id="spread_test_4",
            cycle_id=103,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.08500,
            stop_loss=1.08400,
            take_profit=1.08700,
            entry_time=1753574400.0,
            # spread_at_entry not provided (defaults to 0)
        )

        closed = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.08600,
            bar_low=1.08350,  # Below SL
            bar_close=1.08380,
            bar_time=1753574700.0,
        )

        assert len(closed) == 1
        ds = closed[0]["decision_snapshot"]
        # Should be None (0.0 → None in persistence)
        assert ds["spread_at_entry"] is None

    def test_spread_survives_json_roundtrip(self):
        """Spread data can be serialised and deserialised."""
        engine = ShadowTradeEngine(max_bars=2)
        engine.open_trade(
            trade_id="spread_test_5",
            cycle_id=104,
            symbol="NZDUSD",
            direction="SELL",
            entry_price=0.60500,
            stop_loss=0.60600,
            take_profit=0.60300,
            entry_time=1753574400.0,
            spread_at_entry=0.00015,
            bid_at_entry=0.60493,
            ask_at_entry=0.60508,
        )

        closed = engine.evaluate_bar(
            symbol="NZDUSD",
            bar_high=0.60650,  # Above SL for SELL
            bar_low=0.60450,
            bar_close=0.60620,
            bar_time=1753574700.0,
        )

        record = closed[0]
        # Roundtrip through JSON
        json_str = json.dumps(record, default=str)
        reloaded = json.loads(json_str)
        assert reloaded["decision_snapshot"]["spread_at_entry"] == 0.00015
        assert reloaded["decision_snapshot"]["bid_at_entry"] == 0.60493
        assert reloaded["decision_snapshot"]["ask_at_entry"] == 0.60508


class TestExistingConsumersUnaffected:
    """Existing code consuming shadow trades is not broken."""

    def test_existing_fields_unchanged(self):
        """All original fields remain present and correct."""
        engine = ShadowTradeEngine(max_bars=5)
        trade = engine.open_trade(
            trade_id="compat_1",
            cycle_id=200,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.085,
            stop_loss=1.084,
            take_profit=1.087,
            entry_time=1753574400.0,
            strategy="REVERSAL",
            pattern="HAMMER",
            score=0.65,
            entity_id="EURUSD_1753574400",
            correlation_id="COR-200-EURUSD-AB12",
            spread_at_entry=0.0001,
            bid_at_entry=1.0849,
            ask_at_entry=1.0851,
        )

        # Verify all original fields
        assert trade.trade_id == "compat_1"
        assert trade.symbol == "EURUSD"
        assert trade.direction == "BUY"
        assert trade.entry_price == 1.085
        assert trade.stop_loss == 1.084
        assert trade.take_profit == 1.087
        assert trade.strategy == "REVERSAL"
        assert trade.pattern == "HAMMER"
        assert trade.entity_id == "EURUSD_1753574400"
        assert trade.correlation_id == "COR-200-EURUSD-AB12"

    def test_trade_without_new_fields_still_works(self):
        """Trades created without spread data function normally."""
        engine = ShadowTradeEngine(max_bars=5)
        trade = engine.open_trade(
            trade_id="compat_2",
            cycle_id=201,
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.270,
            stop_loss=1.271,
            take_profit=1.267,
            entry_time=1753574400.0,
        )

        # Should work without spread
        assert trade.closed is False
        engine.evaluate_bar(
            symbol="GBPUSD",
            bar_high=1.272,  # Above SL for SELL
            bar_low=1.269,
            bar_close=1.271,
            bar_time=1753574700.0,
        )
        # Trade closed via SL
        assert trade.closed is True

    def test_engine_stats_unaffected(self):
        """Engine stats method still works."""
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="stats_1",
            cycle_id=300,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.085,
            stop_loss=1.084,
            take_profit=1.087,
            entry_time=1753574400.0,
            spread_at_entry=0.0001,
        )
        stats = engine.stats()
        assert stats["active_trades"] == 1
        assert "EURUSD" in stats["symbols_tracked"]
