"""
Test: Incremental candle replay cache — deduplication and append-only behavior.

Validates:
    1. First persist writes all closed candles (excludes last/forming bar)
    2. Repeated fetches do NOT create duplicates
    3. New candles are correctly appended incrementally
    4. Bot restart idempotency (overlapping fetch ? no dupes)
    5. Timestamps are unique and monotonically increasing
"""

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.config
from data.mt5_data import Candle, _get_last_cached_timestamp, _persist_candles_to_cache


@pytest.fixture
def replay_dir(tmp_path):
    """Redirect replay cache to temp dir and enable it."""
    original_enable = core.config.ENABLE_CANDLE_REPLAY_CACHE
    original_dir = core.config.REPLAY_CACHE_DIR
    core.config.ENABLE_CANDLE_REPLAY_CACHE = True
    core.config.REPLAY_CACHE_DIR = str(tmp_path)
    yield tmp_path
    core.config.ENABLE_CANDLE_REPLAY_CACHE = original_enable
    core.config.REPLAY_CACHE_DIR = original_dir


def _make_candles(start_ts: int = 1719388500, count: int = 5, interval: int = 300) -> list[Candle]:
    """Generate sequential M5 candles."""
    candles = []
    for i in range(count):
        ts = start_ts + (i * interval)
        candles.append(Candle(
            time=ts,
            open=1.074 + (i * 0.001),
            high=1.075 + (i * 0.001),
            low=1.073 + (i * 0.001),
            close=1.0745 + (i * 0.001),
            tick_volume=100 + (i * 10),
        ))
    return candles


def _read_file_lines(replay_dir: Path, symbol: str = "EURUSD", tf: int = 5) -> list[dict]:
    """Read all JSONL records from today's replay file."""
    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filepath = replay_dir / symbol / str(tf) / f"{date_str}.jsonl"
    if not filepath.exists():
        return []
    lines = filepath.read_text(encoding="utf-8").strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


class TestIncrementalPersist:

    def test_first_persist_writes_all_closed(self, replay_dir):
        """First persist writes N-1 candles (last bar is forming)."""
        candles = _make_candles(count=5)
        _persist_candles_to_cache("EURUSD", 5, candles)

        records = _read_file_lines(replay_dir)
        assert len(records) == 4  # 5 candles - 1 forming = 4 written
        assert records[0]["ts"] == candles[0].time * 1000  # Stored in millis
        assert records[-1]["ts"] == candles[3].time * 1000

    def test_same_fetch_no_duplicates(self, replay_dir):
        """Repeated fetch of same candles writes nothing new."""
        candles = _make_candles(count=5)

        _persist_candles_to_cache("EURUSD", 5, candles)
        count_after_first = len(_read_file_lines(replay_dir))

        _persist_candles_to_cache("EURUSD", 5, candles)
        count_after_second = len(_read_file_lines(replay_dir))

        assert count_after_first == count_after_second

    def test_new_candle_appended(self, replay_dir):
        """When a new bar closes, exactly 1 candle is appended."""
        candles = _make_candles(count=5)
        _persist_candles_to_cache("EURUSD", 5, candles)
        count_before = len(_read_file_lines(replay_dir))

        # Simulate next bar close: add one candle to the end
        candles_next = candles + [Candle(
            time=candles[-1].time + 300,
            open=1.080, high=1.082, low=1.079, close=1.081, tick_volume=200,
        )]
        _persist_candles_to_cache("EURUSD", 5, candles_next)
        records = _read_file_lines(replay_dir)

        assert len(records) == count_before + 1
        assert records[-1]["ts"] == candles[-1].time * 1000  # Previously-forming bar now written (in millis)

    def test_bot_restart_idempotent(self, replay_dir):
        """After restart, overlapping fetch does not produce duplicates."""
        candles = _make_candles(count=5)
        _persist_candles_to_cache("EURUSD", 5, candles)
        count_before = len(_read_file_lines(replay_dir))

        # "Restart": same data fetched again
        _persist_candles_to_cache("EURUSD", 5, candles)
        count_after = len(_read_file_lines(replay_dir))

        assert count_before == count_after

    def test_timestamps_unique_and_monotonic(self, replay_dir):
        """All timestamps in file are unique and strictly increasing."""
        candles = _make_candles(count=5)
        _persist_candles_to_cache("EURUSD", 5, candles)

        # Add 3 more bars one by one
        for i in range(3):
            candles = candles + [Candle(
                time=candles[-1].time + 300,
                open=1.080 + (i * 0.001),
                high=1.082 + (i * 0.001),
                low=1.079 + (i * 0.001),
                close=1.081 + (i * 0.001),
                tick_volume=200 + (i * 10),
            )]
            _persist_candles_to_cache("EURUSD", 5, candles)

        records = _read_file_lines(replay_dir)
        timestamps = [r["ts"] for r in records]

        # Unique
        assert len(timestamps) == len(set(timestamps))
        # Monotonic
        assert all(timestamps[i] < timestamps[i + 1] for i in range(len(timestamps) - 1))

    def test_schema_per_candle(self, replay_dir):
        """Each JSONL line has the compact per-candle schema."""
        candles = _make_candles(count=3)
        _persist_candles_to_cache("EURUSD", 5, candles)

        records = _read_file_lines(replay_dir)
        for r in records:
            assert set(r.keys()) == {"ts", "o", "h", "l", "c", "v"}
            assert isinstance(r["ts"], int)
            assert r["ts"] > 1_000_000_000_000  # Milliseconds (13 digits)
            assert isinstance(r["o"], float)
            assert isinstance(r["v"], int)


class TestGetLastCachedTimestamp:

    def test_nonexistent_file(self, replay_dir):
        """Returns None for nonexistent file."""
        filepath = replay_dir / "FAKE" / "5" / "2026-01-01.jsonl"
        assert _get_last_cached_timestamp(filepath) is None

    def test_empty_file(self, replay_dir):
        """Returns None for empty file."""
        filepath = replay_dir / "test.jsonl"
        filepath.write_text("")
        assert _get_last_cached_timestamp(filepath) is None

    def test_single_line(self, replay_dir):
        """Returns timestamp from single-line file."""
        filepath = replay_dir / "test.jsonl"
        filepath.write_text('{"ts":1719388500,"o":1.074,"h":1.075,"l":1.073,"c":1.0745,"v":100}\n')
        assert _get_last_cached_timestamp(filepath) == 1719388500

    def test_multiple_lines(self, replay_dir):
        """Returns timestamp from LAST line only."""
        filepath = replay_dir / "test.jsonl"
        filepath.write_text(
            '{"ts":1719388500,"o":1.074,"h":1.075,"l":1.073,"c":1.0745,"v":100}\n'
            '{"ts":1719388800,"o":1.075,"h":1.076,"l":1.074,"c":1.0755,"v":120}\n'
            '{"ts":1719389100,"o":1.076,"h":1.077,"l":1.075,"c":1.076,"v":110}\n'
        )
        assert _get_last_cached_timestamp(filepath) == 1719389100
