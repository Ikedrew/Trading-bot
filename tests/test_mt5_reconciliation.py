"""Tests for MT5 broker PnL reconciliation."""
import pytest
from unittest.mock import MagicMock
from core.mt5_reconciliation import (
    MT5DealRecord, ReconciliationEntry, match_journal_to_mt5, _aggregate_deals,
)


def _make_deal(ticket=1, position_id=100, symbol="EURUSD", entry_type=1,
               profit=50.0, commission=-0.5, swap=-0.1, volume=0.1, price=1.1,
               time_unix=1000.0, magic=713001):
    return MT5DealRecord(
        deal_ticket=ticket, order_ticket=ticket, position_id=position_id,
        symbol=symbol, deal_type=0, entry_type=entry_type, volume=volume,
        price=price, time_unix=time_unix, profit=profit,
        commission=commission, swap=swap, fee=0.0, magic=magic,
    )


def _make_trade(trade_id="pos_100", symbol="EURUSD", position_ticket=100,
                net_pnl=50.0, entry_time=1000.0, direction="BUY", final_volume=0.1):
    return {
        "trade_id": trade_id, "symbol": symbol, "position_ticket": position_ticket,
        "net_pnl": net_pnl, "entry_time": entry_time, "direction": direction,
        "final_volume": final_volume,
    }


class TestSingleDealMatch:
    """One journal trade matches one MT5 deal."""

    def test_match_by_position_id(self):
        trades = [_make_trade(position_ticket=100)]
        deals = [_make_deal(position_id=100, entry_type=1, profit=50.0)]

        results = match_journal_to_mt5(trades, deals)

        assert len(results) == 1
        assert results[0].mt5_matched is True
        assert results[0].match_method == "POSITION_ID"
        assert results[0].match_confidence == "HIGH"
        assert results[0].broker_profit == 50.0

    def test_match_by_deal_ticket(self):
        """Position_ticket matches deal_ticket (not position_id)."""
        trades = [_make_trade(position_ticket=999)]
        deals = [_make_deal(ticket=999, position_id=888, entry_type=1, profit=30.0)]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].mt5_matched is True
        assert results[0].match_method == "DEAL_TICKET"
        assert results[0].broker_profit == 30.0


class TestMultiDealPosition:
    """One journal trade maps to multiple MT5 deals (entry + exit)."""

    def test_aggregates_exit_deals(self):
        trades = [_make_trade(position_ticket=200)]
        deals = [
            _make_deal(ticket=1, position_id=200, entry_type=0, profit=0.0, commission=-0.5, swap=0.0),  # Entry (no swap)
            _make_deal(ticket=2, position_id=200, entry_type=1, profit=75.0, commission=-0.5, swap=-0.2),  # Exit
        ]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].mt5_matched is True
        assert results[0].broker_profit == 75.0  # Only exit deal profit
        assert results[0].broker_commission == -1.0  # Both deals' commission
        assert results[0].broker_swap == -0.2
        assert results[0].deal_count == 2

    def test_partial_close_multiple_exits(self):
        """Position with partial close = multiple exit deals."""
        trades = [_make_trade(position_ticket=300)]
        deals = [
            _make_deal(ticket=1, position_id=300, entry_type=0, profit=0.0),
            _make_deal(ticket=2, position_id=300, entry_type=1, profit=20.0),  # Partial
            _make_deal(ticket=3, position_id=300, entry_type=1, profit=30.0),  # Final
        ]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].broker_profit == 50.0  # 20 + 30
        assert results[0].deal_count == 3


class TestUnmatchedTrade:
    """Trade with no matching MT5 deal."""

    def test_unmatched(self):
        trades = [_make_trade(position_ticket=999)]
        deals = [_make_deal(position_id=111)]  # Different position

        results = match_journal_to_mt5(trades, deals)

        assert results[0].mt5_matched is False
        assert results[0].match_method == "UNMATCHED"
        assert results[0].match_confidence == "NONE"

    def test_no_ticket(self):
        trades = [_make_trade(position_ticket=0)]
        deals = [_make_deal()]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].match_method == "NO_TICKET"


class TestAmbiguousMatch:
    """Multiple possible matches via timestamp fallback."""

    def test_ambiguous_timestamp_match(self):
        trades = [_make_trade(position_ticket=999, symbol="EURUSD",
                              entry_time=1000.0, final_volume=0.1)]
        # Two positions with same symbol/time/volume
        deals = [
            _make_deal(ticket=1, position_id=501, entry_type=0, time_unix=1000.0, volume=0.1),
            _make_deal(ticket=2, position_id=501, entry_type=1, profit=10.0),
            _make_deal(ticket=3, position_id=502, entry_type=0, time_unix=1000.0, volume=0.1),
            _make_deal(ticket=4, position_id=502, entry_type=1, profit=20.0),
        ]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].match_method == "AMBIGUOUS"
        assert results[0].mt5_matched is False


class TestCommissionSwapFee:
    """Financial aggregation handles all cost fields."""

    def test_all_costs(self):
        trades = [_make_trade(position_ticket=400)]
        deals = [
            _make_deal(position_id=400, entry_type=1, profit=100.0,
                       commission=-2.5, swap=-1.0),
        ]
        # Manually set fee
        deals[0].fee = -0.5

        results = match_journal_to_mt5(trades, deals)

        assert results[0].broker_profit == 100.0
        assert results[0].broker_commission == -2.5
        assert results[0].broker_swap == -1.0
        assert results[0].broker_fee == -0.5
        assert results[0].broker_net_profit == 100.0 + (-2.5) + (-1.0) + (-0.5)


class TestNonFXTrade:
    """Non-FX instruments match correctly."""

    def test_index_trade(self):
        trades = [_make_trade(trade_id="pos_82095735", symbol="US500",
                              position_ticket=82095735, net_pnl=113708000.0)]
        deals = [_make_deal(position_id=82095735, symbol="US500",
                           entry_type=1, profit=843.48)]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].mt5_matched is True
        assert results[0].broker_profit == 843.48
        # Mismatch confirms the 100K multiplier bug in journal
        assert abs(results[0].pnl_mismatch) > 100000000


class TestIdempotent:
    """Running reconciliation twice produces same results."""

    def test_repeated_run(self):
        trades = [_make_trade(position_ticket=100)]
        deals = [_make_deal(position_id=100, entry_type=1, profit=50.0)]

        r1 = match_journal_to_mt5(trades, deals)
        r2 = match_journal_to_mt5(trades, deals)

        assert r1[0].broker_profit == r2[0].broker_profit
        assert r1[0].match_method == r2[0].match_method


class TestNoDuplicateCounting:
    """Same deal not counted twice."""

    def test_single_deal_not_duplicated(self):
        trades = [_make_trade(position_ticket=500)]
        # One deal with both entry and exit on same position
        deals = [_make_deal(ticket=1, position_id=500, entry_type=1, profit=40.0)]

        results = match_journal_to_mt5(trades, deals)

        assert results[0].broker_profit == 40.0
        assert results[0].deal_count == 1
