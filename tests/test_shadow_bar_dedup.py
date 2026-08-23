"""
Regression tests — ShadowTradeEngine.evaluate_bar (symbol, bar_time) dedup guard.

Invariant (Phase 1D Option 1):
    For a given ShadowTradeEngine instance, evaluate_bar may be called
    repeatedly for the same symbol/bar_time, but the Shadow lifecycle
    mutation for that symbol/bar_time occurs at most once.

Covers:
    - First call mutates normally.
    - Duplicate call for same (symbol, bar_time) performs NO lifecycle mutation.
    - Newer bar_time for the same symbol evaluates normally.
    - Same bar_time for a different symbol evaluates normally.
    - Separate engine instances (production vs research shadow) are independent.
    - Distinct bar timestamps preserve existing behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow_trades import ShadowTradeEngine


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _open_trade(engine: ShadowTradeEngine, trade_id: str = "t1", symbol: str = "EURUSD"):
    """Open a non-closing BUY shadow trade on the engine."""
    return engine.open_trade(
        trade_id=trade_id,
        cycle_id=1,
        symbol=symbol,
        direction="BUY",
        entry_price=1.10000,
        stop_loss=1.09900,
        take_profit=1.10500,
        entry_time=1000.0,
    )


def _eval(engine: ShadowTradeEngine, symbol: str = "EURUSD", bar_time: float = 1300.0):
    """Evaluate a bar that hits neither SL nor TP."""
    return engine.evaluate_bar(
        symbol=symbol,
        bar_high=1.10100,
        bar_low=1.09950,
        bar_close=1.10050,
        bar_time=bar_time,
    )


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestBarTimeDedupGuard:
    """(symbol, bar_time) dedup invariant on ShadowTradeEngine.evaluate_bar."""

    def test_first_call_mutates_normally(self):
        """Requirement 1: first evaluate_bar(symbol=A, bar_time=B) mutates."""
        engine = ShadowTradeEngine(max_bars=5)
        _open_trade(engine)

        closed = _eval(engine, symbol="EURUSD", bar_time=1300.0)

        assert closed == []  # trade stays open
        trade = engine._active["t1"]
        assert trade.bars_elapsed == 1
        assert len(trade._state_log) == 1
        assert trade.closed is False

    def test_duplicate_same_symbol_same_bar_does_not_mutate(self):
        """Requirement 2: second identical call performs no lifecycle mutation."""
        engine = ShadowTradeEngine(max_bars=5)
        _open_trade(engine)
        _eval(engine, symbol="EURUSD", bar_time=1300.0)

        closed_dup = _eval(engine, symbol="EURUSD", bar_time=1300.0)
        _eval(engine, symbol="EURUSD", bar_time=1300.0)

        assert closed_dup == []
        trade = engine._active["t1"]
        # bars_elapsed incremented exactly once despite three calls
        assert trade.bars_elapsed == 1
        # _state_log appended exactly once
        assert len(trade._state_log) == 1
        # MFE/MAE tracking inputs unchanged by duplicate calls
        mfe_after_first = trade.max_favourable_price
        mae_after_first = trade.max_adverse_price
        _eval(engine, symbol="EURUSD", bar_time=1300.0)
        assert trade.max_favourable_price == mfe_after_first
        assert trade.max_adverse_price == mae_after_first
        assert trade.closed is False

    def test_newer_bar_time_for_same_symbol_evaluates(self):
        """Requirement 3: newer bar_time for same symbol proceeds normally."""
        engine = ShadowTradeEngine(max_bars=5)
        _open_trade(engine)
        _eval(engine, symbol="EURUSD", bar_time=1300.0)
        _eval(engine, symbol="EURUSD", bar_time=1300.0)  # duplicate — suppressed

        closed = _eval(engine, symbol="EURUSD", bar_time=1600.0)

        assert closed == []
        trade = engine._active["t1"]
        assert trade.bars_elapsed == 2  # two distinct bars only
        assert len(trade._state_log) == 2

    def test_same_bar_time_different_symbol_evaluates(self):
        """Requirement 4: same bar_time for another symbol is not suppressed."""
        engine = ShadowTradeEngine(max_bars=5)
        _open_trade(engine, trade_id="t_eur", symbol="EURUSD")
        _open_trade(engine, trade_id="t_gbp", symbol="GBPUSD")

        _eval(engine, symbol="EURUSD", bar_time=1300.0)
        _eval(engine, symbol="GBPUSD", bar_time=1300.0)

        assert engine._active["t_eur"].bars_elapsed == 1
        assert engine._active["t_gbp"].bars_elapsed == 1

        # Duplicates are per-symbol: suppressing EURUSD must not affect GBPUSD
        _eval(engine, symbol="EURUSD", bar_time=1300.0)
        assert engine._active["t_eur"].bars_elapsed == 1
        _eval(engine, symbol="GBPUSD", bar_time=1300.0)
        assert engine._active["t_gbp"].bars_elapsed == 1

    def test_instances_are_independent(self):
        """Requirement 5: separate engines have independent guards."""
        prod_engine = ShadowTradeEngine(max_bars=5)
        research_engine = ShadowTradeEngine(max_bars=60)
        _open_trade(prod_engine, trade_id="p1")
        _open_trade(research_engine, trade_id="r1")

        _eval(prod_engine, symbol="EURUSD", bar_time=1300.0)
        # Research instance has its own guard — same (symbol, bar_time) still evaluates
        _eval(research_engine, symbol="EURUSD", bar_time=1300.0)

        assert prod_engine._active["p1"].bars_elapsed == 1
        assert research_engine._active["r1"].bars_elapsed == 1

        # And each engine's own duplicate is suppressed independently
        _eval(prod_engine, symbol="EURUSD", bar_time=1300.0)
        _eval(research_engine, symbol="EURUSD", bar_time=1300.0)
        assert prod_engine._active["p1"].bars_elapsed == 1
        assert research_engine._active["r1"].bars_elapsed == 1

    def test_distinct_bar_times_preserve_existing_behaviour(self):
        """Requirement 6: distinct timestamps behave exactly as before the guard."""
        engine = ShadowTradeEngine(max_bars=3)
        _open_trade(engine)

        # Three distinct bars — max_bars=3 timeout should close on the third
        records_1 = _eval(engine, symbol="EURUSD", bar_time=1300.0)
        records_2 = _eval(engine, symbol="EURUSD", bar_time=1600.0)
        records_3 = _eval(engine, symbol="EURUSD", bar_time=1900.0)

        assert records_1 == []
        assert records_2 == []
        assert len(records_3) == 1  # max_bars_timeout close record produced
        record = records_3[0]
        sim_outcome = record["simulated_outcome"]
        assert sim_outcome["exit_reason"] == "max_bars_timeout"
        # bars_held reflects true elapsed closed bars (3), not poll count
        assert sim_outcome["bars_held"] == 3

    def test_guard_is_in_memory_only(self):
        """No persistence surface exists for the dedup state."""
        engine = ShadowTradeEngine(max_bars=5)
        assert isinstance(engine._last_evaluated_bar, dict)
        assert not hasattr(engine, "_persist")  # no persistence hook added
        _open_trade(engine)
        _eval(engine, symbol="EURUSD", bar_time=1300.0)
        # Guard state is a plain in-memory dict keyed by symbol
        assert engine._last_evaluated_bar == {"EURUSD": 1300.0}
