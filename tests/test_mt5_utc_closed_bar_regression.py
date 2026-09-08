"""Regression coverage for broker-time normalization and closed-bar selection."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.runtime.runtime_utils import _closed_bar_index
from data import mt5_data
from data.mt5_data import Candle, _persist_candles_to_cache, _rows_to_candles


def bar(ts: int) -> Candle:
    return Candle(ts, 1.0, 1.1, 0.9, 1.0, 10)


@pytest.mark.parametrize("seconds", [300, 900, 3600, 14400])
def test_current_forming_bar_is_never_selected(seconds):
    now = 1_800_000_000
    candles = [bar(now - 2 * seconds), bar(now - seconds), bar(now)]
    assert _closed_bar_index(candles, seconds, now) == 1
    assert candles[1].time + seconds <= now


def test_future_timestamp_cannot_become_canonical_observation():
    now = 1_800_000_000
    candles = [bar(now - 300), bar(now + 300), bar(now + 600)]
    assert _closed_bar_index(candles, 300, now) == 0


def test_broker_epoch_is_normalized_once_without_naive_datetime():
    broker_offset = 10_800
    rows = [{"time": 1_800_010_800, "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "tick_volume": 7}]
    candles = _rows_to_candles(rows, utc_offset_seconds=broker_offset)
    assert candles[0].time == 1_800_000_000


def test_local_timezone_cannot_shift_canonical_rate_epoch(monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    rows = [{"time": 1_800_010_800, "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "tick_volume": 7}]
    assert _rows_to_candles(rows, utc_offset_seconds=10_800)[0].time == 1_800_000_000


def persist(tmp_path: Path, candles: list[Candle], emitted: list, *, offset=0):
    with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
         patch("core.config.REPLAY_CACHE_DIR", str(tmp_path)), \
         patch("data.mt5_data._time.time", return_value=1_800_001_000), \
         patch("core.event_stream.emit_candle", side_effect=lambda s, p, **k: emitted.append(p) or True):
        _persist_candles_to_cache(
            "EURUSD", 5, candles, source_utc_offset_seconds=offset,
        )


def test_restart_legacy_broker_watermark_emits_no_historical_window(tmp_path):
    d = tmp_path / "EURUSD" / "5"; d.mkdir(parents=True)
    (d / "dedup_initialized").touch()
    (d / "dedup.jsonl").write_text(json.dumps({"ts": 1_800_011_400_000}) + "\n")
    emitted = []
    persist(tmp_path, [bar(1_800_000_000), bar(1_800_000_300),
                       bar(1_800_000_600)], emitted, offset=10_800)
    assert emitted == []
    assert (d / "dedup_timestamp_utc_v1").exists()


def test_restart_one_new_completed_bar_emits_exactly_one(tmp_path):
    d = tmp_path / "EURUSD" / "5"; d.mkdir(parents=True)
    (d / "dedup_initialized").touch(); (d / "dedup_timestamp_utc_v1").touch()
    (d / "dedup.jsonl").write_text(json.dumps({"ts": 1_800_000_300_000}) + "\n")
    emitted = []
    persist(tmp_path, [bar(1_800_000_000), bar(1_800_000_300),
                       bar(1_800_000_600), bar(1_800_000_900)], emitted)
    assert [x["ts"] for x in emitted] == [1_800_000_600_000]


def test_restart_no_new_completed_bar_emits_zero(tmp_path):
    d = tmp_path / "EURUSD" / "5"; d.mkdir(parents=True)
    (d / "dedup_initialized").touch(); (d / "dedup_timestamp_utc_v1").touch()
    (d / "dedup.jsonl").write_text(json.dumps({"ts": 1_800_000_600_000}) + "\n")
    emitted = []
    persist(tmp_path, [bar(1_800_000_300), bar(1_800_000_600),
                       bar(1_800_000_900)], emitted)
    assert emitted == []
