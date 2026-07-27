"""
Unit tests for scanner_init — scanner initialization and symbol state creation.

Tests:
    - Returns list of states on success
    - Returns empty list when all symbols fail
    - Symbols are resolved correctly
    - State objects have expected attributes
    - Position recovery is attempted
    - Failures in one symbol don't block others
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestInitializeSymbolStates:
    """initialize_symbol_states creates per-symbol state objects."""

    @patch("core.runtime.scanner_init.MT5DataFeed")
    @patch("core.runtime.scanner_init.load_engine_state", return_value=None)
    @patch("core.runtime.scanner_init.StaleDataMonitor")
    @patch("core.runtime.scanner_init._build_risk_manager")
    @patch("core.runtime.scanner_init._build_trade_management_config")
    def test_returns_states_list(self, mock_tm_config, mock_risk, mock_stale,
                                  mock_load_state, mock_feed_cls):
        from core.runtime.scanner_init import initialize_symbol_states

        # Mock feed
        mock_feed = MagicMock()
        mock_feed.resolve_symbol.return_value = "EURUSD"
        mock_feed_cls.return_value = mock_feed

        # Mock config
        with patch("core.runtime.scanner_init.config") as mock_config:
            mock_config.CANONICAL_SYMBOLS = None
            mock_config.SYMBOLS = ["EURUSD"]
            mock_config.TRADE_MANAGEMENT_ENABLED = False
            mock_config.MTF_ENABLED = False
            mock_config.BOT_MAGIC = 12345

            states = initialize_symbol_states(
                symbols=["EURUSD"],
                execution=MagicMock(),
            )

        assert len(states) == 1
        assert states[0].symbol == "EURUSD"

    @patch("core.runtime.scanner_init.MT5DataFeed")
    def test_returns_empty_on_all_failures(self, mock_feed_cls):
        from core.runtime.scanner_init import initialize_symbol_states

        mock_feed_cls.side_effect = RuntimeError("connection failed")

        with patch("core.runtime.scanner_init.config") as mock_config:
            mock_config.CANONICAL_SYMBOLS = None
            mock_config.SYMBOLS = ["EURUSD"]
            mock_config.TRADE_MANAGEMENT_ENABLED = False
            mock_config.MTF_ENABLED = False

            states = initialize_symbol_states(
                symbols=["EURUSD", "GBPUSD"],
                execution=MagicMock(),
            )

        assert states == []

    @patch("core.runtime.scanner_init.MT5DataFeed")
    @patch("core.runtime.scanner_init.load_engine_state", return_value=None)
    @patch("core.runtime.scanner_init.StaleDataMonitor")
    @patch("core.runtime.scanner_init._build_risk_manager")
    @patch("core.runtime.scanner_init._build_trade_management_config")
    def test_partial_failure_still_returns_valid_states(
        self, mock_tm_config, mock_risk, mock_stale, mock_load_state, mock_feed_cls
    ):
        from core.runtime.scanner_init import initialize_symbol_states

        call_count = [0]

        def feed_factory(sym):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first symbol fails")
            mock_feed = MagicMock()
            mock_feed.resolve_symbol.return_value = sym
            return mock_feed

        mock_feed_cls.side_effect = feed_factory

        with patch("core.runtime.scanner_init.config") as mock_config:
            mock_config.CANONICAL_SYMBOLS = None
            mock_config.SYMBOLS = ["FAIL", "GBPUSD"]
            mock_config.TRADE_MANAGEMENT_ENABLED = False
            mock_config.MTF_ENABLED = False
            mock_config.BOT_MAGIC = 12345

            states = initialize_symbol_states(
                symbols=["FAIL", "GBPUSD"],
                execution=MagicMock(),
            )

        # First failed, second succeeded
        assert len(states) == 1
        assert states[0].symbol == "GBPUSD"

    @patch("core.runtime.scanner_init.MT5DataFeed")
    @patch("core.runtime.scanner_init.load_engine_state", return_value=None)
    @patch("core.runtime.scanner_init.StaleDataMonitor")
    @patch("core.runtime.scanner_init._build_risk_manager")
    @patch("core.runtime.scanner_init._build_trade_management_config")
    def test_state_has_expected_attributes(self, mock_tm_config, mock_risk, mock_stale,
                                            mock_load_state, mock_feed_cls):
        from core.runtime.scanner_init import initialize_symbol_states

        mock_feed = MagicMock()
        mock_feed.resolve_symbol.return_value = "USDJPY"
        mock_feed_cls.return_value = mock_feed

        with patch("core.runtime.scanner_init.config") as mock_config:
            mock_config.CANONICAL_SYMBOLS = None
            mock_config.SYMBOLS = ["USDJPY"]
            mock_config.TRADE_MANAGEMENT_ENABLED = False
            mock_config.MTF_ENABLED = False
            mock_config.BOT_MAGIC = 99

            states = initialize_symbol_states(
                symbols=["USDJPY"],
                execution=MagicMock(),
            )

        s = states[0]
        assert s.symbol == "USDJPY"
        assert s.feed is mock_feed
        assert s.last_closed_time is None
        assert s.iterations == 0
        assert s.tf_cache is None
