"""
Unit tests for execution_context_builder — per-cycle environment snapshot.

Tests:
    - Returns correlation ID on success
    - Returns empty string on failure
    - Session classification correct
    - Persist called on success
    - Never raises regardless of input
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.execution_context_builder import build_cycle_context


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_sym_state(symbol="EURUSD", volatility=0.001, iterations=5):
    state = MagicMock()
    state.symbol = symbol
    state.engine_state.volatility_filter = volatility
    state.iterations = iterations
    state.trade_manager.positions_open.return_value = []
    return state


def _make_dd_result(pct=2.0):
    r = MagicMock()
    r.current_drawdown_pct = pct
    return r


def _make_dl_result(pct=1.0):
    r = MagicMock()
    r.current_loss_pct = pct
    return r


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestBuildCycleContext:
    """build_cycle_context returns correlation ID and persists context."""

    @patch("core.runtime.execution_context_builder.persist_execution_context")
    @patch("core.runtime.execution_context_builder.build_execution_context")
    @patch("core.runtime.execution_context_builder.generate_correlation_id", return_value="cor_abc123")
    def test_returns_correlation_id(self, mock_gen, mock_build, mock_persist):
        mock_build.return_value = {"context": "data"}

        result = build_cycle_context(
            cycle_id=5,
            cycle_start=time.time() - 0.1,
            sym_state=_make_sym_state(),
            closed_time=1700000000,
            bid=1.1000,
            ask=1.1002,
            tick_time=time.time() - 1,
            feed_state="HEALTHY",
            dd_result=_make_dd_result(),
            dl_result=_make_dl_result(),
        )

        assert result == "cor_abc123"
        mock_persist.assert_called_once_with({
            "context": "data",
            "bar_time": 1700000000,
            "entity_id": "EURUSD_1700000000",
            "cycle_id": 5,
        })

    @patch("core.runtime.execution_context_builder.persist_execution_context")
    @patch("core.runtime.execution_context_builder.build_execution_context")
    @patch("core.runtime.execution_context_builder.generate_correlation_id", return_value="id123")
    def test_session_london(self, mock_gen, mock_build, mock_persist):
        """Hour 9 UTC → LONDON session."""
        mock_build.return_value = {}
        # 9 AM UTC: gmtime(ts).tm_hour == 9
        import calendar
        from datetime import datetime, timezone
        ts = int(datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc).timestamp())

        build_cycle_context(
            cycle_id=1, cycle_start=time.time(),
            sym_state=_make_sym_state(), closed_time=ts,
            bid=1.1, ask=1.1002, tick_time=time.time(),
            feed_state="HEALTHY", dd_result=_make_dd_result(), dl_result=_make_dl_result(),
        )

        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["session_state"] == "LONDON"

    @patch("core.runtime.execution_context_builder.persist_execution_context")
    @patch("core.runtime.execution_context_builder.build_execution_context")
    @patch("core.runtime.execution_context_builder.generate_correlation_id", return_value="id123")
    def test_session_ny(self, mock_gen, mock_build, mock_persist):
        """Hour 14 UTC → NY session."""
        mock_build.return_value = {}
        from datetime import datetime, timezone
        ts = int(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp())

        build_cycle_context(
            cycle_id=1, cycle_start=time.time(),
            sym_state=_make_sym_state(), closed_time=ts,
            bid=1.1, ask=1.1002, tick_time=time.time(),
            feed_state="HEALTHY", dd_result=_make_dd_result(), dl_result=_make_dl_result(),
        )

        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["session_state"] == "NY"

    @patch("core.runtime.execution_context_builder.persist_execution_context")
    @patch("core.runtime.execution_context_builder.build_execution_context")
    @patch("core.runtime.execution_context_builder.generate_correlation_id", return_value="id123")
    def test_session_asia(self, mock_gen, mock_build, mock_persist):
        """Hour 3 UTC → ASIA session."""
        mock_build.return_value = {}
        from datetime import datetime, timezone
        ts = int(datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc).timestamp())

        build_cycle_context(
            cycle_id=1, cycle_start=time.time(),
            sym_state=_make_sym_state(), closed_time=ts,
            bid=1.1, ask=1.1002, tick_time=time.time(),
            feed_state="HEALTHY", dd_result=_make_dd_result(), dl_result=_make_dl_result(),
        )

        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["session_state"] == "ASIA"


class TestFailureHandling:
    """Never raises — returns empty string on failure."""

    def test_returns_empty_on_generate_failure(self):
        with patch("core.runtime.execution_context_builder.generate_correlation_id", side_effect=RuntimeError("crash")):
            result = build_cycle_context(
                cycle_id=1, cycle_start=time.time(),
                sym_state=_make_sym_state(), closed_time=1700000000,
                bid=1.1, ask=1.1002, tick_time=time.time(),
                feed_state="HEALTHY", dd_result=_make_dd_result(), dl_result=_make_dl_result(),
            )
        assert result == ""

    def test_returns_empty_on_broken_sym_state(self):
        """Completely broken sym_state → returns empty, no raise."""
        result = build_cycle_context(
            cycle_id=1, cycle_start=time.time(),
            sym_state=None,  # Will cause AttributeError
            closed_time=1700000000,
            bid=1.1, ask=1.1002, tick_time=time.time(),
            feed_state="HEALTHY", dd_result=MagicMock(), dl_result=MagicMock(),
        )
        assert result == ""

    @patch("core.runtime.execution_context_builder.persist_execution_context", side_effect=RuntimeError("disk full"))
    @patch("core.runtime.execution_context_builder.build_execution_context", return_value={})
    @patch("core.runtime.execution_context_builder.generate_correlation_id", return_value="id_ok")
    def test_persist_failure_still_returns_id(self, mock_gen, mock_build, mock_persist):
        """If persist fails, correlation ID is still returned."""
        result = build_cycle_context(
            cycle_id=1, cycle_start=time.time(),
            sym_state=_make_sym_state(), closed_time=1700000000,
            bid=1.1, ask=1.1002, tick_time=time.time(),
            feed_state="HEALTHY", dd_result=_make_dd_result(), dl_result=_make_dl_result(),
        )
        # persist raised AFTER _cor_id_cycle was set, but the whole block is in try/except
        # so it depends on where in the try block persist is called
        # In our implementation, persist is inside the try, so if it raises,
        # _cor_id_cycle was already set before persist was called
        # BUT the except catches it and _cor_id_cycle was set BEFORE persist
        # Actually: _cor_id_cycle is set BEFORE persist_execution_context()
        # So even if persist fails, _cor_id_cycle has already been assigned
        # Wait - the entire function has the pattern:
        # _cor_id_cycle = ""
        # try: ... _cor_id_cycle = generate...; ...; persist...; except: pass
        # return _cor_id_cycle
        # If persist raises, _cor_id_cycle is already "id_ok" from generate
        assert result == "id_ok"
