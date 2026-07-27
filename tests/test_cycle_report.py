"""
Unit tests for emit_cycle_report — end-of-cycle reporting.

Tests:
    - Report emits successfully
    - Inputs are passed correctly
    - Pipeline trace printed on drops
    - log_cycle_summary_simple called
    - Market snapshot throttled (every 25 cycles)
    - Reporting failure does not crash runtime
    - Function returns None
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline.cycle_report import emit_cycle_report


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_config(discord_logger=None):
    cfg = MagicMock()
    cfg._discord_logger = discord_logger
    return cfg


def _make_state(symbol="EURUSD", bias_value="BULLISH"):
    s = MagicMock()
    s.symbol = symbol
    s.engine_state.current_bias.value = bias_value
    return s


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestBasicEmission:
    """emit_cycle_report executes without error and returns None."""

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_returns_none(self, mock_log):
        result = emit_cycle_report(
            cycle_id=1,
            cycle_start=time.time(),
            n_symbols=2,
            cycle_drops=[],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={"market_context": 0, "trades_executed": 0},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
        )
        assert result is None

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_log_cycle_summary_called(self, mock_log):
        emit_cycle_report(
            cycle_id=5,
            cycle_start=time.time() - 0.1,
            n_symbols=3,
            cycle_drops=[],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
        )
        mock_log.assert_called_once()
        args = mock_log.call_args[0]
        assert args[0] == 5  # cycle_id
        assert args[1] == 3  # n_symbols


class TestPipelineTrace:
    """Pipeline trace is printed when drops or trade occurred."""

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_trace_printed_on_drops(self, mock_log, capsys):
        emit_cycle_report(
            cycle_id=10,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[("EURUSD", "pattern", "no_viable_pattern")],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
            cycle_decision_drops=[("EURUSD", "pattern", "no_viable_pattern")],
        )
        captured = capsys.readouterr()
        assert "CYCLE 10 PIPELINE TRACE" in captured.out
        assert "EURUSD" in captured.out

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_trace_shows_trade_filled(self, mock_log, capsys):
        emit_cycle_report(
            cycle_id=20,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[],
            cycle_had_trade=True,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
            cycle_had_fill=True,
            cycle_filled_symbols=["EURUSD"],
            cycle_execute_symbols=["EURUSD"],
            cycle_execution_symbols=["EURUSD"],
        )
        captured = capsys.readouterr()
        assert "TRADE FILLED" in captured.out
        assert "EURUSD" in captured.out

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_dominant_drop_stage_shown(self, mock_log, capsys):
        drops = [
            ("EURUSD", "market_context", "chop"),
            ("GBPUSD", "market_context", "chop"),
            ("USDJPY", "pattern", "no_pattern"),
        ]
        emit_cycle_report(
            cycle_id=30,
            cycle_start=time.time(),
            n_symbols=3,
            cycle_drops=drops,
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
            cycle_decision_drops=drops,
        )
        captured = capsys.readouterr()
        assert "dominant drop: market_context" in captured.out


class TestMarketSnapshot:
    """Market snapshot fires at cycle multiples of 25."""

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_snapshot_fires_at_cycle_25(self, mock_log):
        discord = MagicMock()
        emit_cycle_report(
            cycle_id=25,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[("EURUSD", "pattern", "no_pattern")],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={"market_context": 5, "trades_executed": 0},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(discord_logger=discord),
        )
        discord.event.assert_called_once()
        args = discord.event.call_args[0]
        assert args[0] == "MARKET_SNAPSHOT"

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_snapshot_does_not_fire_at_cycle_7(self, mock_log):
        discord = MagicMock()
        emit_cycle_report(
            cycle_id=7,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[("EURUSD", "pattern", "no_pattern")],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(discord_logger=discord),
        )
        discord.event.assert_not_called()

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    def test_snapshot_does_not_fire_without_drops(self, mock_log):
        discord = MagicMock()
        emit_cycle_report(
            cycle_id=25,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(discord_logger=discord),
        )
        discord.event.assert_not_called()


class TestFailureIsolation:
    """Reporting failures do not propagate."""

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple", side_effect=RuntimeError("crash"))
    def test_never_raises_on_log_failure(self, mock_log):
        result = emit_cycle_report(
            cycle_id=1,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[("EURUSD", "x", "y")],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
        )
        assert result is None

    def test_never_raises_on_discord_failure(self):
        discord = MagicMock()
        discord.event.side_effect = RuntimeError("discord_down")
        result = emit_cycle_report(
            cycle_id=25,
            cycle_start=time.time(),
            n_symbols=1,
            cycle_drops=[("EURUSD", "x", "y")],
            cycle_had_trade=False,
            this_cycle_new_bars=[],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(discord_logger=discord),
        )
        assert result is None


class TestNewBarLogging:
    """New-bar logging fires when symbols have new bars."""

    @patch("core.pipeline.cycle_report.log_cycle_summary_simple")
    @patch("core.pipeline.cycle_report.logger")
    def test_new_bars_logged(self, mock_logger, mock_log):
        emit_cycle_report(
            cycle_id=1,
            cycle_start=time.time(),
            n_symbols=2,
            cycle_drops=[],
            cycle_had_trade=False,
            this_cycle_new_bars=["EURUSD", "GBPUSD"],
            filter_hits={},
            states=[_make_state()],
            htf_context=None,
            config=_make_config(),
        )
        mock_logger.info.assert_called()
        call_args = mock_logger.info.call_args[0]
        assert "[CYCLE_BARS]" in call_args[0]
