"""
Tests for B3: Trade Journal & P&L Persistence.

Covers:
- Journal write: closed trade persists successfully
- Journal read: persisted trade loads correctly
- P&L calculation: profit, loss, break-even
- Daily P&L: multiple trades same day, different days
- Restart recovery: rebuild daily P&L after restart
- Query functions: symbol, pattern, date, recent
- Duplicate protection: same trade cannot be recorded twice
- Running daily P&L with unrealised component
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_journal import (
    TradeRecord,
    CloseReason,
    build_trade_record,
    persist_trade,
    persist_trade_once,
    is_already_journaled,
    mark_journaled,
    get_trades_by_date,
    get_trades_today,
    get_trade,
    get_trades_by_symbol,
    get_trades_by_pattern,
    get_recent_trades,
    get_daily_realised_pnl,
    get_daily_trade_count,
    get_current_daily_pnl,
    get_daily_summary,
    reload_persisted_ids,
    _persisted_ids,
    _compute_pnl,
    _get_journal_dir,
)
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def use_temp_journal(tmp_path):
    """Redirect journal writes to a temp directory and reset dedup state."""
    _persisted_ids.clear()
    with patch("core.trade_journal._get_journal_dir", return_value=tmp_path):
        yield tmp_path
    _persisted_ids.clear()


def _make_position(
    position_id: str = "pos_100",
    symbol: str = "EURUSD",
    side: Side = Side.BUY,
    entry: float = 1.1000,
    sl: float = 1.0950,
    tp: float = 1.1100,
    volume: float = 0.10,
    open_time: float = 1717400000.0,
    magic: int = 713001,
    pattern: str = "ENGULFING_BULLISH",
    mt5_ticket: int = 12345,
) -> Position:
    return Position(
        position_id=position_id,
        symbol=symbol,
        side=side,
        magic=magic,
        entry_price=entry,
        initial_sl=sl,
        initial_tp=tp,
        stop_loss=sl,
        take_profit=tp,
        volume=volume,
        open_time=open_time,
        status=PositionStatus.CLOSED,
        mt5_ticket=mt5_ticket,
        pattern_tag=pattern,
        max_favourable_price=entry + 0.005 if side == Side.BUY else entry - 0.005,
    )


def _make_record(
    trade_id: str = "pos_100",
    symbol: str = "EURUSD",
    pnl: float = 50.0,
    exit_time: float | None = None,
    pattern: str = "ENGULFING_BULLISH",
    close_reason: str = "take_profit",
) -> TradeRecord:
    """Quick helper to build a TradeRecord for testing."""
    pos = _make_position(position_id=trade_id, symbol=symbol, pattern=pattern)
    et = exit_time if exit_time is not None else pos.open_time + 3600
    return build_trade_record(
        position=pos,
        exit_price=1.1050 if pnl > 0 else 1.0950,
        exit_time=et,
        close_reason=close_reason,
        realised_pnl_override=pnl,
    )


# --- TEST: P&L CALCULATION ---------------------------------------------------

class TestPnLCalculation:
    def test_buy_profit(self):
        """BUY trade: exit > entry = profit."""
        pnl = _compute_pnl(Side.BUY, 1.1000, 1.1050, 0.10)
        assert pnl > 0

    def test_buy_loss(self):
        """BUY trade: exit < entry = loss."""
        pnl = _compute_pnl(Side.BUY, 1.1000, 1.0950, 0.10)
        assert pnl < 0

    def test_sell_profit(self):
        """SELL trade: exit < entry = profit."""
        pnl = _compute_pnl(Side.SELL, 1.1000, 1.0950, 0.10)
        assert pnl > 0

    def test_sell_loss(self):
        """SELL trade: exit > entry = loss."""
        pnl = _compute_pnl(Side.SELL, 1.1000, 1.1050, 0.10)
        assert pnl < 0

    def test_break_even(self):
        """Same entry and exit = zero P&L."""
        pnl = _compute_pnl(Side.BUY, 1.1000, 1.1000, 0.10)
        assert pnl == 0.0


# --- TEST: BUILD TRADE RECORD -------------------------------------------------

class TestBuildTradeRecord:
    def test_builds_from_position(self):
        """TradeRecord built correctly from Position."""
        pos = _make_position()
        record = build_trade_record(
            position=pos,
            exit_price=1.1050,
            exit_time=pos.open_time + 1800,
            close_reason=CloseReason.TAKE_PROFIT.value,
        )
        assert record.trade_id == "pos_100"
        assert record.symbol == "EURUSD"
        assert record.direction == "BUY"
        assert record.entry_price == 1.1000
        assert record.exit_price == 1.1050
        assert record.duration_seconds == 1800.0
        assert record.close_reason == "take_profit"
        assert record.realised_pnl > 0

    def test_pnl_override(self):
        """When broker provides exact P&L, override is used."""
        pos = _make_position()
        record = build_trade_record(
            position=pos,
            exit_price=1.1050,
            exit_time=pos.open_time + 1000,
            close_reason=CloseReason.STOP_LOSS.value,
            realised_pnl_override=-25.50,
        )
        assert record.realised_pnl == -25.50

    def test_net_pnl_includes_commission_and_swap(self):
        """net_pnl = realised_pnl + swap + commission (raw MT5 signs; commission
        is NEGATIVE when it is a cost, so it reduces net)."""
        pos = _make_position()
        record = build_trade_record(
            position=pos,
            exit_price=1.1050,
            exit_time=pos.open_time + 1000,
            close_reason=CloseReason.TAKE_PROFIT.value,
            realised_pnl_override=100.0,
            commission=-5.0,   # raw MT5 sign: cost is negative
            swap=-2.0,
        )
        assert record.net_pnl == pytest.approx(100.0 + (-2.0) + (-5.0))  # = 93.0


# --- TEST: JOURNAL PERSISTENCE ------------------------------------------------

class TestJournalPersistence:
    def test_persist_creates_file(self, use_temp_journal):
        """Persisting a trade creates a JSONL file."""
        record = _make_record()
        result = persist_trade(record)
        assert result is True
        files = list(use_temp_journal.glob("*.jsonl"))
        assert len(files) == 1

    def test_persist_appends_line(self, use_temp_journal):
        """Each persist appends one line."""
        r1 = _make_record(trade_id="pos_1", pnl=10.0)
        r2 = _make_record(trade_id="pos_2", pnl=-5.0)
        persist_trade(r1)
        persist_trade(r2)

        files = list(use_temp_journal.glob("*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 2

    def test_persist_is_valid_json(self, use_temp_journal):
        """Each line is valid JSON."""
        record = _make_record()
        persist_trade(record)

        files = list(use_temp_journal.glob("*.jsonl"))
        line = files[0].read_text().strip()
        data = json.loads(line)
        assert data["trade_id"] == "pos_100"
        assert data["symbol"] == "EURUSD"

    def test_persist_trade_once_idempotent(self, use_temp_journal):
        """Same trade persisted twice results in only one entry."""
        record = _make_record(trade_id="pos_dup")
        persist_trade_once(record)
        persist_trade_once(record)

        files = list(use_temp_journal.glob("*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 1

    def test_dedup_flag_set(self):
        """mark_journaled sets the dedup flag."""
        mark_journaled("pos_xyz")
        assert is_already_journaled("pos_xyz") is True
        assert is_already_journaled("pos_other") is False


# --- TEST: JOURNAL READ / QUERY -----------------------------------------------

class TestJournalQuery:
    def test_get_trades_by_date(self, use_temp_journal):
        """Trades load correctly by date."""
        record = _make_record(exit_time=1717403600.0)  # 2024-06-03
        persist_trade(record)

        from core.trade_journal import _date_from_timestamp
        date_str = _date_from_timestamp(1717403600.0)
        trades = get_trades_by_date(date_str)
        assert len(trades) == 1
        assert trades[0].trade_id == "pos_100"

    def test_get_trades_by_symbol(self, use_temp_journal):
        """Filter by symbol works."""
        r1 = _make_record(trade_id="t1", symbol="EURUSD")
        r2 = _make_record(trade_id="t2", symbol="GBPUSD")
        r3 = _make_record(trade_id="t3", symbol="EURUSD")
        persist_trade(r1)
        persist_trade(r2)
        persist_trade(r3)

        results = get_trades_by_symbol("EURUSD")
        assert len(results) == 2
        assert all(r.symbol == "EURUSD" for r in results)

    def test_get_trades_by_pattern(self, use_temp_journal):
        """Filter by pattern works."""
        r1 = _make_record(trade_id="t1", pattern="ENGULFING_BULLISH")
        r2 = _make_record(trade_id="t2", pattern="HAMMER")
        persist_trade(r1)
        persist_trade(r2)

        results = get_trades_by_pattern("HAMMER")
        assert len(results) == 1
        assert results[0].pattern_name == "HAMMER"

    def test_get_recent_trades(self, use_temp_journal):
        """Recent trades returns most recent first."""
        for i in range(5):
            r = _make_record(trade_id=f"t{i}", exit_time=1717400000.0 + i * 100)
            persist_trade(r)

        results = get_recent_trades(limit=3)
        assert len(results) == 3
        # Most recent first
        assert results[0].exit_time > results[1].exit_time

    def test_get_trade_by_id(self, use_temp_journal):
        """Find specific trade by ID."""
        r1 = _make_record(trade_id="target_trade")
        persist_trade(r1)

        found = get_trade("target_trade")
        assert found is not None
        assert found.trade_id == "target_trade"

    def test_get_trade_not_found(self, use_temp_journal):
        """Non-existent trade returns None."""
        result = get_trade("nonexistent")
        assert result is None


# --- TEST: DAILY P&L ----------------------------------------------------------

class TestDailyPnL:
    def test_daily_pnl_single_trade(self, use_temp_journal):
        """Single trade P&L is daily total."""
        r = _make_record(pnl=75.0, exit_time=1717403600.0)
        persist_trade(r)

        from core.trade_journal import _date_from_timestamp
        d = _date_from_timestamp(1717403600.0)
        assert get_daily_realised_pnl(d) == pytest.approx(75.0)

    def test_daily_pnl_multiple_trades(self, use_temp_journal):
        """Multiple trades sum correctly."""
        r1 = _make_record(trade_id="t1", pnl=50.0, exit_time=1717403600.0)
        r2 = _make_record(trade_id="t2", pnl=-20.0, exit_time=1717403700.0)
        r3 = _make_record(trade_id="t3", pnl=30.0, exit_time=1717403800.0)
        persist_trade(r1)
        persist_trade(r2)
        persist_trade(r3)

        from core.trade_journal import _date_from_timestamp
        d = _date_from_timestamp(1717403600.0)
        assert get_daily_realised_pnl(d) == pytest.approx(60.0)

    def test_daily_pnl_different_days(self, use_temp_journal):
        """Trades on different days are separate."""
        # Day 1
        r1 = _make_record(trade_id="t1", pnl=100.0, exit_time=1717372800.0)  # 2024-06-03 00:00
        # Day 2 (next day)
        r2 = _make_record(trade_id="t2", pnl=-50.0, exit_time=1717459200.0)  # 2024-06-04 00:00
        persist_trade(r1)
        persist_trade(r2)

        from core.trade_journal import _date_from_timestamp
        d1 = _date_from_timestamp(1717372800.0)
        d2 = _date_from_timestamp(1717459200.0)
        assert get_daily_realised_pnl(d1) == pytest.approx(100.0)
        assert get_daily_realised_pnl(d2) == pytest.approx(-50.0)

    def test_daily_trade_count(self, use_temp_journal):
        """Trade count for a day."""
        for i in range(4):
            r = _make_record(trade_id=f"t{i}", pnl=10.0, exit_time=1717403600.0 + i)
            persist_trade(r)

        from core.trade_journal import _date_from_timestamp
        d = _date_from_timestamp(1717403600.0)
        assert get_daily_trade_count(d) == 4

    def test_current_daily_pnl_with_unrealised(self, use_temp_journal):
        """Running P&L includes unrealised component."""
        # Use a fixed time for "today"
        now = time.time()
        r = _make_record(trade_id="today_trade", pnl=40.0, exit_time=now)
        persist_trade(r)

        today_str = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        with patch("core.trade_journal.get_daily_realised_pnl", return_value=40.0):
            total = get_current_daily_pnl(unrealised_pnl=-15.0)
            assert total == pytest.approx(25.0)


# --- TEST: RESTART RECOVERY --------------------------------------------------

class TestRestartRecovery:
    def test_reload_persisted_ids(self, use_temp_journal):
        """After restart, dedup IDs are reloaded from journal."""
        # Simulate pre-restart: persist some trades
        now = time.time()
        today_str = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        r1 = _make_record(trade_id="survived_1", exit_time=now)
        r2 = _make_record(trade_id="survived_2", exit_time=now)
        persist_trade(r1)
        persist_trade(r2)

        # Clear in-memory state (simulate restart)
        _persisted_ids.clear()
        assert not is_already_journaled("survived_1")

        # Reload
        with patch("core.trade_journal.get_trades_today") as mock_today:
            mock_today.return_value = [r1, r2]
            count = reload_persisted_ids()

        assert count == 2
        assert is_already_journaled("survived_1")
        assert is_already_journaled("survived_2")

    def test_daily_pnl_survives_restart(self, use_temp_journal):
        """Daily P&L is reconstructed from disk after restart."""
        # Write trades to disk
        exit_t = 1717403600.0
        r1 = _make_record(trade_id="t1", pnl=80.0, exit_time=exit_t)
        r2 = _make_record(trade_id="t2", pnl=-30.0, exit_time=exit_t + 100)
        persist_trade(r1)
        persist_trade(r2)

        # Clear ALL in-memory state (simulate full restart)
        _persisted_ids.clear()

        # Reconstruct from disk
        from core.trade_journal import _date_from_timestamp
        d = _date_from_timestamp(exit_t)
        pnl = get_daily_realised_pnl(d)
        assert pnl == pytest.approx(50.0)


# --- TEST: DAILY SUMMARY ------------------------------------------------------

class TestDailySummary:
    def test_summary_fields(self, use_temp_journal):
        """Summary has all expected fields."""
        r1 = _make_record(trade_id="t1", pnl=50.0, exit_time=1717403600.0)
        r2 = _make_record(trade_id="t2", pnl=-20.0, exit_time=1717403700.0)
        r3 = _make_record(trade_id="t3", pnl=30.0, exit_time=1717403800.0)
        persist_trade(r1)
        persist_trade(r2)
        persist_trade(r3)

        from core.trade_journal import _date_from_timestamp
        d = _date_from_timestamp(1717403600.0)
        summary = get_daily_summary(d)

        assert summary["trades"] == 3
        assert summary["wins"] == 2
        assert summary["losses"] == 1
        assert summary["net_pnl"] == pytest.approx(60.0)
        assert summary["win_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_empty_day_summary(self, use_temp_journal):
        """Empty day returns zero summary."""
        summary = get_daily_summary("2020-01-01")
        assert summary["trades"] == 0
        assert summary["net_pnl"] == 0.0
