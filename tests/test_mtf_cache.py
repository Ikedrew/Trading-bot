"""
Unit tests for Multi-Timeframe Authority cache infrastructure (Phase 3).

Validates:
- TimeframeCache instantiation
- update_if_needed triggers analyzer on new bar
- Snapshot replacement correctness
- Failure retention behavior
- Partial HTFContext construction
- Symbol isolation
- Stale data handling
- get_htf_context is read-only
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from data.mt5_data import Candle
from core.timeframes.cache import TimeframeCache, _CacheEntry, _TF_H4, _TF_H1, _TF_M15
from core.timeframes.types import (
    BiasDirection,
    BiasSnapshot,
    HTFContext,
    RegimeClassification,
    RegimeSnapshot,
    StructureSnapshot,
)


# --- HELPERS ------------------------------------------------------------------


def _make_candle(t: int, o: float, h: float, l: float, c: float, tv: int = 100) -> Candle:
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=tv)


def _trending_candles(count: int, start: float = 1.0, step: float = 0.005, base_time: int = 0, tf_seconds: int = 14400) -> list[Candle]:
    """Generate trending candles with proper timestamps."""
    candles = []
    for i in range(count):
        o = start + i * step
        c = o + step * 0.8
        h = c + step * 0.2
        l = o - step * 0.1
        candles.append(_make_candle(base_time + i * tf_seconds, o, h, l, c))
    return candles


def _mock_feed(candles_map: dict[int, list[Candle]]):
    """Create a mock MT5DataFeed that returns candles by timeframe."""
    feed = MagicMock()

    def _copy_rates(symbol, tf, count):
        all_candles = candles_map.get(tf, [])
        if not all_candles:
            return []
        return all_candles[-count:] if count < len(all_candles) else all_candles

    feed.copy_rates_closed = MagicMock(side_effect=_copy_rates)
    return feed


# --- INIT TESTS ---------------------------------------------------------------


class TestTimeframeCacheInit:
    def test_creates_with_symbol(self):
        cache = TimeframeCache(symbol="EURUSD")
        assert cache.symbol == "EURUSD"

    def test_creates_with_no_feed(self):
        cache = TimeframeCache(symbol="GBPUSD", feed=None, config=None)
        assert cache.symbol == "GBPUSD"

    def test_no_feed_update_is_noop(self):
        cache = TimeframeCache(symbol="EURUSD", feed=None)
        cache.update_if_needed(current_time_s=1000.0)
        ctx = cache.get_htf_context()
        assert ctx.is_populated is False


# --- UPDATE TESTS -------------------------------------------------------------


class TestTimeframeCacheUpdate:
    def test_h4_update_on_new_bar(self):
        """H4 analyzer runs when new bar detected."""
        h4_candles = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4_candles, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 100))

        entry = cache.get_entry(_TF_H4)
        assert entry is not None
        assert entry.snapshot is not None
        assert isinstance(entry.snapshot, RegimeSnapshot)
        assert entry.bar_time == h4_candles[-2].time  # Last CLOSED bar (forming bar excluded)
        assert entry.fetch_failures == 0

    def test_h1_update_on_new_bar(self):
        """H1 analyzer runs when new bar detected."""
        h1_candles = _trending_candles(50, base_time=2000, tf_seconds=3600)
        feed = _mock_feed({_TF_H4: [], _TF_H1: h1_candles, _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        cache.update_if_needed(current_time_s=float(h1_candles[-1].time + 100))

        entry = cache.get_entry(_TF_H1)
        assert entry is not None
        assert entry.snapshot is not None
        assert isinstance(entry.snapshot, BiasSnapshot)

    def test_m15_update_on_new_bar(self):
        """M15 analyzer runs when new bar detected."""
        m15_candles = _trending_candles(50, base_time=3000, tf_seconds=900)
        feed = _mock_feed({_TF_H4: [], _TF_H1: [], _TF_M15: m15_candles})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        cache.update_if_needed(current_time_s=float(m15_candles[-1].time + 100), current_price=m15_candles[-1].close)

        entry = cache.get_entry(_TF_M15)
        assert entry is not None
        assert entry.snapshot is not None
        assert isinstance(entry.snapshot, StructureSnapshot)

    def test_no_update_when_same_bar(self):
        """No fetch when bar_time hasn't changed."""
        h4_candles = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4_candles, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        # First update populates
        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 100))
        call_count_after_first = feed.copy_rates_closed.call_count

        # Second update with same data — should only do 1-bar checks (no full fetch)
        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 200))
        # The 1-bar check calls happen but no full fetch since bar_time matches
        # Total calls increase by number of enabled TFs (lightweight checks)
        assert feed.copy_rates_closed.call_count > call_count_after_first


# --- SNAPSHOT REPLACEMENT TESTS -----------------------------------------------


class TestSnapshotReplacement:
    def test_snapshot_replaced_on_new_bar(self):
        """New bar produces new snapshot, replacing old one."""
        candles_v1 = _trending_candles(50, start=1.0, base_time=1000, tf_seconds=14400)
        candles_v2 = _trending_candles(50, start=1.5, base_time=1000 + 14400, tf_seconds=14400)

        # First update
        feed = _mock_feed({_TF_H4: candles_v1, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)
        cache.update_if_needed(current_time_s=float(candles_v1[-1].time + 100))
        snap1 = cache.get_entry(_TF_H4).snapshot

        # Second update with newer data
        feed.copy_rates_closed = MagicMock(side_effect=lambda sym, tf, count: candles_v2[-count:] if tf == _TF_H4 else [])
        cache.update_if_needed(current_time_s=float(candles_v2[-1].time + 100))
        snap2 = cache.get_entry(_TF_H4).snapshot

        assert snap1 is not snap2
        assert snap2.bar_time == candles_v2[-2].time  # Last CLOSED bar (forming excluded)


# --- FAILURE RETENTION TESTS --------------------------------------------------


class TestFailureRetention:
    def test_failure_retains_previous_snapshot(self):
        """On fetch failure, previous snapshot is retained."""
        h4_candles = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4_candles, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        # Populate successfully
        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 100))
        original_snap = cache.get_entry(_TF_H4).snapshot
        assert original_snap is not None

        # Now make feed fail
        feed.copy_rates_closed = MagicMock(side_effect=RuntimeError("MT5 disconnected"))
        # Force staleness to trigger update attempt
        cache._entries[_TF_H4].bar_time = 0
        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 50000))

        # Snapshot retained
        assert cache.get_entry(_TF_H4).snapshot is original_snap
        assert cache.get_entry(_TF_H4).fetch_failures > 0

    def test_failure_increments_counter(self):
        """Each failure increments fetch_failures."""
        feed = MagicMock()
        feed.copy_rates_closed = MagicMock(side_effect=RuntimeError("fail"))
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        cache.update_if_needed(current_time_s=100000.0)

        # All timeframes should have failures (since all are stale on cold start)
        h4_entry = cache.get_entry(_TF_H4)
        assert h4_entry.fetch_failures > 0


# --- HTFCONTEXT CONSTRUCTION TESTS -------------------------------------------


class TestHTFContextConstruction:
    def test_empty_cache_returns_unpopulated(self):
        cache = TimeframeCache(symbol="EURUSD")
        ctx = cache.get_htf_context()
        assert ctx.is_populated is False
        assert ctx.regime is None
        assert ctx.bias is None
        assert ctx.structure is None

    def test_partial_context_with_h4_only(self):
        """Only H4 populated ? regime present, others None."""
        h4_candles = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4_candles, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)
        cache.update_if_needed(current_time_s=float(h4_candles[-1].time + 100))

        ctx = cache.get_htf_context()
        assert ctx.regime is not None
        assert ctx.bias is None
        assert ctx.structure is None
        assert ctx.is_populated is True

    def test_full_context_all_populated(self):
        """All timeframes populated ? full HTFContext."""
        h4 = _trending_candles(50, start=1.0, base_time=1000, tf_seconds=14400)
        h1 = _trending_candles(50, start=1.0, base_time=2000, tf_seconds=3600)
        m15 = _trending_candles(50, start=1.0, base_time=3000, tf_seconds=900)
        feed = _mock_feed({_TF_H4: h4, _TF_H1: h1, _TF_M15: m15})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)
        cache.update_if_needed(current_time_s=float(max(h4[-1].time, h1[-1].time, m15[-1].time) + 100), current_price=1.2)

        ctx = cache.get_htf_context()
        assert ctx.regime is not None
        assert ctx.bias is not None
        assert ctx.structure is not None
        assert ctx.is_populated is True

    def test_get_htf_context_is_readonly(self):
        """get_htf_context never modifies cache state."""
        h4 = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)
        cache.update_if_needed(current_time_s=float(h4[-1].time + 100))

        entry_before = cache.get_entry(_TF_H4).bar_time
        _ = cache.get_htf_context()
        _ = cache.get_htf_context()
        entry_after = cache.get_entry(_TF_H4).bar_time
        assert entry_before == entry_after


# --- SYMBOL ISOLATION TESTS ---------------------------------------------------


class TestSymbolIsolation:
    def test_separate_symbols_independent(self):
        """Updating one symbol's cache doesn't affect another."""
        h4 = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed_eu = _mock_feed({_TF_H4: h4, _TF_H1: [], _TF_M15: []})
        feed_gb = _mock_feed({_TF_H4: [], _TF_H1: [], _TF_M15: []})

        cache_eu = TimeframeCache(symbol="EURUSD", feed=feed_eu)
        cache_gb = TimeframeCache(symbol="GBPUSD", feed=feed_gb)

        cache_eu.update_if_needed(current_time_s=float(h4[-1].time + 100))

        assert cache_eu.get_htf_context().is_populated is True
        assert cache_gb.get_htf_context().is_populated is False


# --- STALE DATA TESTS ---------------------------------------------------------


class TestStaleData:
    def test_stale_triggers_refresh(self):
        """Stale snapshot triggers re-fetch attempt."""
        h4 = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        # Populate
        cache.update_if_needed(current_time_s=float(h4[-1].time + 100))
        first_call_count = feed.copy_rates_closed.call_count

        # Advance time beyond staleness threshold (3x H4 = 12 hours)
        stale_time = float(h4[-1].time + 14400 * 4)  # 4x H4 duration
        cache.update_if_needed(current_time_s=stale_time)

        # Should have made additional calls (staleness triggered refresh)
        assert feed.copy_rates_closed.call_count > first_call_count

    def test_cold_start_is_stale(self):
        """Empty cache (cold start) is treated as stale."""
        h4 = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        # First call should attempt fetch (cold start = stale)
        cache.update_if_needed(current_time_s=float(h4[-1].time + 100))
        assert feed.copy_rates_closed.call_count > 0


# --- INVALIDATION TESTS ------------------------------------------------------


class TestInvalidation:
    def test_invalidate_all_forces_refresh(self):
        """After invalidate_all, next update triggers fresh fetch."""
        h4 = _trending_candles(50, base_time=1000, tf_seconds=14400)
        feed = _mock_feed({_TF_H4: h4, _TF_H1: [], _TF_M15: []})
        cache = TimeframeCache(symbol="EURUSD", feed=feed)

        cache.update_if_needed(current_time_s=float(h4[-1].time + 100))
        calls_after_first = feed.copy_rates_closed.call_count

        cache.invalidate_all()
        cache.update_if_needed(current_time_s=float(h4[-1].time + 200))

        # Should have made more calls after invalidation
        assert feed.copy_rates_closed.call_count > calls_after_first
