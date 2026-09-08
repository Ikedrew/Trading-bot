"""Restart, dedup continuity, and lost-state recovery tests.

Invariant under test:
    one canonical M5 candle identity = one stable emitted completed candle fact

Recovery semantics:
    - genuine first-ever startup emits the closed window and creates the
      durable `dedup_initialized` marker,
    - restart with valid dedup emits only genuinely new candles,
    - restart after persistent dedup loss/corruption (marker present, no
      valid state) MUST NOT re-emit the historical MT5 fetch window.
"""
import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from data.mt5_data import (
    Candle,
    _persist_candles_to_cache,
    reset_candle_dedup_for_tests,
)


def _bar(ts: int, close: float = 1.05) -> Candle:
    return Candle(time=ts, open=1.0, high=1.1, low=0.9, close=close, tick_volume=100)


def _dedup_dir(td: str, symbol: str = "EURUSD", timeframe: int = 5) -> Path:
    return Path(td) / symbol / str(timeframe)


class TestCandleRestartContinuity:
    """Prove persistent dedup works across restart and date boundaries."""

    def test_same_day_restart_no_duplicate(self):
        """Process B restarts on same day: dedup file prevents re-emission."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            # Process A: emit T1, T2
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 2
            assert (_dedup_dir(td) / "dedup_initialized").exists()

            # Process B: no in-memory dedup, same MT5 window
            reset_candle_dedup_for_tests()
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 0

    def test_midnight_rollover_no_duplicate(self):
        """Non-date-partitioned dedup file prevents midnight re-emission."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            # Process A: establish state (creates marker + valid dedup)
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle"):
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])

            # Process B across midnight: T3=1900 is new; T4 forming (excluded)
            reset_candle_dedup_for_tests()
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache(
                    "EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600), _bar(1900)]
                )
                # Only T3 (1600) should be emitted; T1/T2 filtered
                assert emit.call_count == 1

    def test_in_memory_prevents_within_session_dup(self):
        """In-memory set prevents re-emission within one process session."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            for iteration in range(3):
                with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                     patch("core.config.REPLAY_CACHE_DIR", td), \
                     patch("core.event_stream.emit_candle") as emit:
                    _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                if iteration == 0:
                    assert emit.call_count == 2
                else:
                    assert emit.call_count == 0

    def test_new_completed_after_process_b(self):
        """T3 was forming in Process A, completes in Process B; emitted once."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle"):
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])

            # Process B: same window, no new emissions
            reset_candle_dedup_for_tests()
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 0

            # T3 completes now, T4 forms
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache(
                    "EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600), _bar(1900)]
                )
                # T3 (1600) should be emitted exactly once
                assert emit.call_count == 1

    def test_multiple_symbols_isolation(self):
        """Dedup is per-symbol: EURUSD and AUDUSD don't interfere."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                _persist_candles_to_cache("AUDUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 4  # 2 per symbol
            assert (_dedup_dir(td, "EURUSD") / "dedup_initialized").exists()
            assert (_dedup_dir(td, "AUDUSD") / "dedup_initialized").exists()


class TestGenuineFirstStartup:
    """A brand-new installation must still establish its initial candle state."""

    def test_first_ever_startup_emits_window_and_creates_marker(self):
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            assert not (_dedup_dir(td) / "dedup.jsonl").exists()
            assert not (_dedup_dir(td) / "dedup_initialized").exists()

            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])

            # First-ever startup: both closed candles emitted, forming excluded
            assert emit.call_count == 2
            assert (_dedup_dir(td) / "dedup.jsonl").exists()
            assert (_dedup_dir(td) / "dedup_initialized").exists()

    def test_new_installation_not_permanently_blocked(self):
        """A fresh install emits on cycle 1 AND emits genuinely new candles later."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 2

            # Next cycle with a genuinely new completed candle still emits
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache(
                    "EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600), _bar(1900)]
                )
                assert emit.call_count == 1

    def test_legacy_upgrade_valid_dedup_without_marker_no_reemission(self):
        """Existing bot with dedup.jsonl but no marker must not re-emit window."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            ddir = _dedup_dir(td)
            ddir.mkdir(parents=True, exist_ok=True)
            # Legacy state: valid dedup.jsonl, no marker yet
            with open(ddir / "dedup.jsonl", "w") as f:
                f.write(json.dumps(
                    {"ts": 1300000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100}
                ) + "\n")

            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])

            # last_ts=1300000 covers every CLOSED bar in the window (1600 is
            # still forming) → zero re-emission. Marker adopted.
            assert emit.call_count == 0
            assert (ddir / "dedup_initialized").exists()


class TestLostStateRecovery:
    """Restart after persistent dedup loss/corruption must NOT re-emit history."""

    def _establish_state(self, td: str):
        """Process A: emit T1/T2, creating marker + valid dedup state."""
        with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
             patch("core.config.REPLAY_CACHE_DIR", td), \
             patch("core.event_stream.emit_candle") as emit:
            _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
        assert emit.call_count == 2
        assert (_dedup_dir(td) / "dedup_initialized").exists()

    def _restart_fetch(self, td: str) -> int:
        """Fresh process (empty in-memory set) fetches same MT5 window."""
        reset_candle_dedup_for_tests()
        with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
             patch("core.config.REPLAY_CACHE_DIR", td), \
             patch("core.event_stream.emit_candle") as emit:
            _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
            return emit.call_count

    def test_restart_after_dedup_file_missing(self, caplog):
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").unlink()

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                emitted = self._restart_fetch(td)

            # FAIL SAFE: historical window NOT re-emitted
            assert emitted == 0
            assert any("dedup_state_lost" in r.message for r in caplog.records)

    def test_restart_after_dedup_unexpectedly_empty(self, caplog):
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").write_text("")

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                emitted = self._restart_fetch(td)

            assert emitted == 0
            assert any("dedup_state_lost" in r.message for r in caplog.records)

    def test_restart_after_dedup_malformed(self, caplog):
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").write_text("not valid json\n{\"ts\": broken\n")

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                emitted = self._restart_fetch(td)

            assert emitted == 0
            assert any("dedup_state_lost" in r.message for r in caplog.records)

    def test_restart_after_dedup_read_failure(self, caplog):
        """Reading a directory instead of a file raises → caught → None."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            dedup = _dedup_dir(td) / "dedup.jsonl"
            dedup.unlink()
            dedup.mkdir()  # now unreadable-as-jsonl (IsADirectoryError)

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                emitted = self._restart_fetch(td)

            assert emitted == 0
            assert any("dedup_state_lost" in r.message for r in caplog.records)

    def test_state_loss_critical_reported_once_per_session(self, caplog):
        """Within ONE session: CRITICAL logged once, not per-cycle spam."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").unlink()

            # Same session (no reset): three consecutive cycles
            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"), \
                 patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                for _ in range(3):
                    _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])

            assert emit.call_count == 0
            criticals = [r for r in caplog.records if "dedup_state_lost" in r.message]
            assert len(criticals) == 1  # log-once per symbol/timeframe/session

    def test_state_loss_across_restarts_never_reemits(self, caplog):
        """Across multiple restarts (fresh sessions): still zero re-emission."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").unlink()

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                assert self._restart_fetch(td) == 0
                assert self._restart_fetch(td) == 0
                assert self._restart_fetch(td) == 0

    def test_state_loss_isolated_per_symbol(self, caplog):
        """Losing EURUSD state must not block AUDUSD's genuine first startup."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td, "EURUSD") / "dedup.jsonl").unlink()

            reset_candle_dedup_for_tests()
            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                     patch("core.config.REPLAY_CACHE_DIR", td), \
                     patch("core.event_stream.emit_candle") as emit:
                    # AUDUSD: genuine first-ever startup — must still work
                    _persist_candles_to_cache("AUDUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                    assert emit.call_count == 2
                    # EURUSD: lost state — must skip entirely
                    _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                    assert emit.call_count == 2  # unchanged by EURUSD skip

    def test_repair_restores_normal_operation(self, caplog):
        """After operator restores valid dedup state, emission resumes normally."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            dedup = _dedup_dir(td) / "dedup.jsonl"
            dedup.unlink()

            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                assert self._restart_fetch(td) == 0

            # Operator repair: restore a valid dedup file
            reset_candle_dedup_for_tests()
            with open(dedup, "w") as f:
                f.write(json.dumps(
                    {"ts": 1300000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100}
                ) + "\n")

            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                # Window [1300, 1600, 1900]: closed = [1300, 1600]; only 1600
                # is genuinely newer than last_ts=1300000 → emitted once
                _persist_candles_to_cache("EURUSD", 5, [_bar(1300), _bar(1600), _bar(1900)])
                assert emit.call_count == 1

    def test_conflicting_ohlcv_cannot_enter_via_state_loss(self, caplog):
        """Broker-revised window after state loss produces zero new facts."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            self._establish_state(td)
            (_dedup_dir(td) / "dedup.jsonl").unlink()

            # Broker revised T1/T2 OHLCV; fresh process fetches revised window
            reset_candle_dedup_for_tests()
            revised = [
                Candle(time=1000, open=1.0, high=1.1, low=0.85, close=1.02, tick_volume=150),
                Candle(time=1300, open=1.0, high=1.15, low=0.9, close=1.08, tick_volume=180),
                _bar(1600),
            ]
            with caplog.at_level(logging.CRITICAL, logger="data.mt5_data"):
                with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                     patch("core.config.REPLAY_CACHE_DIR", td), \
                     patch("core.event_stream.emit_candle") as emit:
                    _persist_candles_to_cache("EURUSD", 5, revised)
                    # Conflicting OHLCV versions of T1/T2 never emitted
                    assert emit.call_count == 0
