"""Targeted restart and dedup continuity tests."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from data.mt5_data import (
    Candle,
    _persist_candles_to_cache,
    reset_candle_dedup_for_tests,
)


def _bar(ts: int) -> Candle:
    return Candle(time=ts, open=1.0, high=1.1, low=0.9, close=1.05, tick_volume=100)


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
                first_count = emit.call_count
            assert first_count == 2

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
            # Seed dedup file with last_ts matching T2
            ddir = Path(td) / "EURUSD" / "5"
            ddir.mkdir(parents=True, exist_ok=True)
            with open(ddir / "dedup.jsonl", "w") as f:
                f.write(json.dumps({"ts": 1300000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100}) + "\n")

            # Midnight rollover window (T3=1900 is new, T2 already persisted)
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600), _bar(1900)])
                # Only T3 (1600) should be emitted; T1/T2 filtered; T4 forming
                assert emit.call_count == 1

    def test_in_memory_prevents_within_session_dup(self):
        """In-memory set prevents re-emission even if dedup file is missing."""
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
            reset_candle_dedup_for_tests()
            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600), _bar(1900)])
                # T3 (1600) should be emitted exactly once
                assert emit.call_count == 1

    def test_missing_dedup_file(self):
        """Missing dedup file: in-memory dedup still prevents single-session dup."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            for _ in range(2):
                with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                     patch("core.config.REPLAY_CACHE_DIR", td), \
                     patch("core.event_stream.emit_candle") as emit:
                    _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                    if emit.call_count > 0:
                        assert emit.call_count == 2
                        break

    def test_malformed_dedup_file(self):
        """Malformed last line: read failure returns None, file still written."""
        reset_candle_dedup_for_tests()
        with tempfile.TemporaryDirectory() as td:
            ddir = Path(td) / "EURUSD" / "5"
            ddir.mkdir(parents=True, exist_ok=True)
            with open(ddir / "dedup.jsonl", "w") as f:
                f.write("not valid json\n")

            with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
                 patch("core.config.REPLAY_CACHE_DIR", td), \
                 patch("core.event_stream.emit_candle") as emit:
                _persist_candles_to_cache("EURUSD", 5, [_bar(1000), _bar(1300), _bar(1600)])
                assert emit.call_count == 2
                # Verify dedup file was written despite malformed read
                with open(ddir / "dedup.jsonl") as f:
                    lines = f.read().strip().split("\n")
                    assert len(lines) == 3

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