"""CANDLE timeframe contract tests.

Proves every NEW CANDLE event persists its explicit timeframe so the full
canonical identity (symbol, timeframe, bar_timestamp) is reconstructable, while
historical timeframe-less CANDLE events remain readable without an implicit M5
default.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.constants.timeframes import timeframe_name
from core.event_stream import emit_candle, close
from data import mt5_data as _mt5_data
from data.mt5_data import Candle, _persist_candles_to_cache


def _bar(ts: int) -> Candle:
    return Candle(time=ts, open=1.0, high=1.1, low=0.9, close=1.0, tick_volume=1)


def _persist(symbol, timeframe, candles, tmp_path, calls):
    with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
         patch("core.config.REPLAY_CACHE_DIR", str(tmp_path)), \
         patch("core.event_stream.emit_candle",
               side_effect=lambda s, p, source=None, timeframe=None: calls.append((s, p, timeframe)) or True):
        _persist_candles_to_cache(symbol, timeframe, candles)


def test_timeframe_name_mapping():
    assert timeframe_name(5) == "M5"
    assert timeframe_name(15) == "M15"
    assert timeframe_name(16385) == "H1"
    assert timeframe_name(16388) == "H4"
    assert timeframe_name(16408) == "D1"
    assert timeframe_name(32769) == "W1"
    assert timeframe_name(49153) == "MN"


@pytest.mark.parametrize("tf,name", [(5, "M5"), (15, "M15"), (16385, "H1")])
def test_new_candle_persists_timeframe(tf, name, tmp_path):
    _mt5_data.reset_candle_dedup_for_tests()
    calls = []
    _persist("EURUSD", tf, [_bar(1000), _bar(1300), _bar(1600)], tmp_path, calls)
    assert calls
    assert all(c[2] == name for c in calls)


def test_same_timestamp_different_timeframe_distinguishable(tmp_path):
    _mt5_data.reset_candle_dedup_for_tests()
    m5 = []
    _persist("EURUSD", 5, [_bar(1000), _bar(1300)], tmp_path, m5)
    _mt5_data.reset_candle_dedup_for_tests()
    m15 = []
    _persist("EURUSD", 15, [_bar(1000), _bar(1300)], tmp_path, m15)

    m5_ids = {(s, p["ts"], tf) for s, p, tf in m5}
    m15_ids = {(s, p["ts"], tf) for s, p, tf in m15}

    # Same (symbol, timestamp) but different timeframe → distinguishable
    assert ("EURUSD", 1000000, "M5") in m5_ids
    assert ("EURUSD", 1000000, "M15") in m15_ids
    assert ("EURUSD", 1000000, "M5") != ("EURUSD", 1000000, "M15")


# ─── Serialized event shape (top-level timeframe) ──────────────────────────────

@pytest.fixture
def isolated_events(tmp_path, monkeypatch):
    monkeypatch.setattr("core.event_stream._EVENT_DIR", str(tmp_path))
    monkeypatch.setattr("core.event_stream._current_file", None)
    monkeypatch.setattr("core.event_stream._current_date", None)
    monkeypatch.setattr("core.event_stream._file_handle", None)
    monkeypatch.setattr("core.event_stream._total_emitted", 0)
    monkeypatch.setattr("core.event_stream._total_errors", 0)
    monkeypatch.setattr("core.event_stream._enabled", True)
    monkeypatch.setattr("core.event_stream._S3_ENABLED", False)
    monkeypatch.setattr("core.event_stream._get_event_dir", lambda: tmp_path)
    yield tmp_path
    close()


def _last_event(tmp_path) -> dict:
    files = sorted(Path(tmp_path).glob("*.jsonl"))
    assert files, "no event file written"
    line = files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    return json.loads(line)


def test_new_candle_serializes_timeframe_top_level(isolated_events):
    emit_candle("EURUSD", {"ts": 1719388800000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 10}, timeframe="M5")
    evt = _last_event(isolated_events)
    assert evt["type"] == "CANDLE"
    assert evt["symbol"] == "EURUSD"
    assert evt["timeframe"] == "M5"


def test_backward_compat_historical_candle_no_timeframe(isolated_events):
    emit_candle("EURUSD", {"ts": 1719388800000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 10})
    evt = _last_event(isolated_events)
    # No crash, no implicit M5 default: timeframe field is simply absent.
    assert "timeframe" not in evt
    assert evt["type"] == "CANDLE"
