"""
Unit tests for BarProvider — bar fetching, validation, and deduplication.

Tests:
    - Valid bar returned (new bar)
    - No new bar returns None (duplicate)
    - Feed stale returns None
    - Candle fetch failure returns None
    - Timestamp conversion preserved
    - Stale data monitor integration
    - Feed state classification
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.bar_provider import BarProvider, BarResult


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_candle(t: int, o=1.1, h=1.2, l=1.0, c=1.15):
    """Create a mock candle with time and OHLC."""
    candle = MagicMock()
    candle.time = t
    candle.open = o
    candle.high = h
    candle.low = l
    candle.close = c
    return candle


def _make_config():
    cfg = MagicMock()
    cfg.TIMEFRAME = "M5"
    cfg.CANDLE_COUNT = 100
    return cfg


def _make_sym_state(symbol="EURUSD", last_closed_time=None, iterations=0):
    """Build a mock symbol state."""
    state = MagicMock()
    state.symbol = symbol
    state.last_closed_time = last_closed_time
    state.iterations = iterations
    state.feed = MagicMock()
    state.stale_monitor = MagicMock()
    # Stale monitor returns non-stale by default
    _candle_result = MagicMock()
    _candle_result.is_stale = False
    _candle_result.escalation_level = 0
    state.stale_monitor.on_candle.return_value = _candle_result
    # No prior attributes
    state._feed_stale_alerted = False
    state._bar_stale_counter = 0
    state._stale_warned = False
    return state


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestValidBarReturned:
    """New bar produces valid BarResult."""

    @patch("core.shadow_trades.get_shadow_engine")
    def test_new_bar_returns_bar_result(self, mock_shadow):
        now = int(time.time())
        # Bar is 60 seconds old (well within HEALTHY threshold)
        bar_time = now - 60
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("core.runtime.bar_provider._TICK_UTC_OFFSET_SECONDS", 0, create=True):
            with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
                result = provider.fetch_bar(sym_state)

        assert result is not None
        assert isinstance(result, BarResult)
        assert result.candles is candles
        assert result.closed_i == 1
        assert result.closed_time == bar_time
        assert result.feed_state == "HEALTHY"
        assert result.is_new_bar is True

    @patch("core.shadow_trades.get_shadow_engine")
    def test_state_updated_on_new_bar(self, mock_shadow):
        now = int(time.time())
        bar_time = now - 60
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300, iterations=5)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is not None
        assert sym_state.last_closed_time == bar_time
        assert sym_state.iterations == 6


class TestDuplicateBarRejected:
    """Same bar as last time returns None."""

    @patch("core.shadow_trades.get_shadow_engine")
    def test_duplicate_bar_returns_none(self, mock_shadow):
        now = int(time.time())
        bar_time = now - 60
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        # Last closed time same as current bar
        sym_state = _make_sym_state(last_closed_time=bar_time, iterations=3)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is None
        # Iterations should NOT have incremented
        assert sym_state.iterations == 3


class TestFetchFailure:
    """Candle fetch failure returns None."""

    def test_runtime_error_returns_none(self):
        config = _make_config()
        sym_state = _make_sym_state()
        sym_state.feed.copy_rates_closed.side_effect = RuntimeError("connection lost")

        provider = BarProvider(config)
        result = provider.fetch_bar(sym_state)

        assert result is None


class TestFeedStaleBlock:
    """Feed classified as FEED_STALE returns None."""

    @patch("core.shadow_trades.get_shadow_engine")
    def test_stale_feed_returns_none(self, mock_shadow):
        now = int(time.time())
        # Bar is 2000 seconds old (>1800 = FEED_STALE)
        bar_time = now - 2000
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is None


class TestFeedStateClassification:
    """Feed state classification thresholds."""

    @patch("core.shadow_trades.get_shadow_engine")
    def test_healthy_under_600s(self, mock_shadow):
        now = int(time.time())
        bar_time = now - 300  # 5 min old
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is not None
        assert result.feed_state == "HEALTHY"

    @patch("core.shadow_trades.get_shadow_engine")
    def test_slow_between_600_and_1200s(self, mock_shadow):
        now = int(time.time())
        bar_time = now - 800  # 13 min old
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300)
        sym_state.feed.copy_rates_closed.return_value = candles

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is not None
        assert result.feed_state == "SLOW"


class TestStaleDataMonitor:
    """Stale candle monitor blocks critically stale data."""

    @patch("core.shadow_trades.get_shadow_engine")
    def test_critically_stale_candle_returns_none(self, mock_shadow):
        now = int(time.time())
        bar_time = now - 60
        candles = [_make_candle(bar_time - 300), _make_candle(bar_time)]

        config = _make_config()
        sym_state = _make_sym_state(last_closed_time=bar_time - 300)
        sym_state.feed.copy_rates_closed.return_value = candles
        # Simulate critically stale candle
        stale_result = MagicMock()
        stale_result.is_stale = True
        stale_result.escalation_level = 2
        stale_result.stale_duration_seconds = 120.0
        stale_result.action = "SKIP"
        sym_state.stale_monitor.on_candle.return_value = stale_result

        provider = BarProvider(config)

        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            result = provider.fetch_bar(sym_state)

        assert result is None
