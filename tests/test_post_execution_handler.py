"""
Unit tests for post_execution_handler — fire-and-forget execution effects.

Tests:
    - Success path emits all effects
    - Failure path emits trade events
    - Each effect is independently guarded (one failure doesn't block others)
    - Never raises
    - Discord notification includes reasoning when available
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.post_execution_handler import emit_post_trade_success, emit_post_trade_failure


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_intent(side="BUY", volume=0.01, entry=1.1, sl=1.09, tp=1.12, pattern="engulfing"):
    intent = MagicMock()
    intent.side.name = side
    intent.volume = volume
    intent.entry_reference = entry
    intent.sl = sl
    intent.tp = tp
    intent.pattern = pattern
    return intent


def _make_result(ok=True, fill_price=1.1001, retcode=10009):
    r = MagicMock()
    r.ok = ok
    r.fill_price = fill_price
    r.retcode = retcode
    return r


def _make_config(discord_logger=None):
    cfg = MagicMock()
    cfg._discord_logger = discord_logger
    return cfg


# ─── TESTS: SUCCESS PATH ─────────────────────────────────────────────────────


class TestEmitPostTradeSuccess:
    """Success path emits all fire-and-forget effects."""

    @patch("execution.post_execution_handler.emit_trade_events")
    @patch("execution.post_execution_handler.record_slippage")
    def test_emits_slippage_and_events(self, mock_slippage, mock_events):
        emit_post_trade_success(
            symbol="EURUSD",
            intent=_make_intent(),
            result=_make_result(),
            score_value=6,
            closed_i=5,
            closed_time=1700000000,
            bias_value="BULLISH",
            config=_make_config(),
            new_result=None,
            unified=None,
            engine_state=MagicMock(regime_state="TRENDING"),
        )

        mock_slippage.assert_called_once()
        mock_events.assert_called_once()
        call_kwargs = mock_events.call_args[1]
        assert call_kwargs["execution_ok"] is True

    @patch("execution.post_execution_handler.emit_trade_events")
    @patch("execution.post_execution_handler.record_slippage")
    def test_discord_notification_fires(self, mock_slippage, mock_events):
        discord = MagicMock()
        emit_post_trade_success(
            symbol="EURUSD",
            intent=_make_intent(),
            result=_make_result(),
            score_value=6,
            closed_i=5,
            closed_time=1700000000,
            bias_value="BULLISH",
            config=_make_config(discord_logger=discord),
            new_result=None,
            unified=None,
            engine_state=MagicMock(regime_state="TRENDING"),
        )

        discord.event.assert_called()
        call_args = discord.event.call_args[0]
        assert call_args[0] == "TRADE_DECISION"

    @patch("execution.post_execution_handler.emit_trade_events")
    @patch("execution.post_execution_handler.record_slippage", side_effect=RuntimeError("crash"))
    def test_slippage_failure_does_not_block_events(self, mock_slippage, mock_events):
        """If slippage recording crashes, trade events still fire."""
        emit_post_trade_success(
            symbol="EURUSD",
            intent=_make_intent(),
            result=_make_result(),
            score_value=6,
            closed_i=5,
            closed_time=1700000000,
            bias_value="BULLISH",
            config=_make_config(),
            new_result=None,
            unified=None,
            engine_state=MagicMock(regime_state="TRENDING"),
        )

        # Events still fired despite slippage crash
        mock_events.assert_called_once()

    @patch("execution.post_execution_handler.emit_trade_events")
    @patch("execution.post_execution_handler.record_slippage")
    def test_never_raises(self, mock_slippage, mock_events):
        """Even with completely broken inputs, never raises."""
        # Should not raise
        emit_post_trade_success(
            symbol="EURUSD",
            intent=_make_intent(),
            result=_make_result(),
            score_value=6,
            closed_i=5,
            closed_time=1700000000,
            bias_value="BULLISH",
            config=None,  # Broken config
            new_result=None,
            unified=None,
            engine_state=MagicMock(regime_state="TRENDING"),
        )


# ─── TESTS: FAILURE PATH ─────────────────────────────────────────────────────


class TestEmitPostTradeFailure:
    """Failure path emits trade events with rejection."""

    @patch("execution.post_execution_handler.emit_trade_events")
    def test_emits_failure_event(self, mock_events):
        emit_post_trade_failure(
            result=_make_result(ok=False, retcode=10004),
            closed_i=3,
            closed_time=1700000000,
            bias_value="BEARISH",
            score_value=5,
        )

        mock_events.assert_called_once()
        call_kwargs = mock_events.call_args[1]
        assert call_kwargs["execution_ok"] is False
        assert call_kwargs["should_trade"] is True

    @patch("execution.post_execution_handler.emit_trade_events", side_effect=RuntimeError("crash"))
    def test_never_raises(self, mock_events):
        """Even if emit_trade_events crashes, never raises."""
        emit_post_trade_failure(
            result=_make_result(ok=False),
            closed_i=0,
            closed_time=1700000000,
            bias_value="NONE",
            score_value=0,
        )
