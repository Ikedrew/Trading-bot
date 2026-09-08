"""Closed-bar identity contract shared by CANDLE and decision paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.identity.canonical import mint_observation_id
from core.runtime.runtime_utils import _closed_bar_index
from data import mt5_data as _mt5_data
from data.mt5_data import Candle, _persist_candles_to_cache


SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
    "AUDUSD", "NZDUSD", "NAS100", "US500", "XAUUSD",
]


def _bar(ts: int) -> Candle:
    return Candle(time=ts, open=1.0, high=1.1, low=0.9, close=1.0, tick_volume=1)


def _persist(symbol, candles, tmp_path, emitted):
    with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
         patch("core.config.REPLAY_CACHE_DIR", str(tmp_path)), \
         patch("core.event_stream.emit_candle", side_effect=lambda s, p, source=None, timeframe=None: emitted.append((s, p)) or True):
        _persist_candles_to_cache(symbol, 5, candles)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_all_canonical_symbols_decide_on_persisted_closed_bar(symbol, tmp_path):
    _mt5_data.reset_candle_dedup_for_tests()
    candles = [_bar(1_000), _bar(1_300), _bar(1_600)]  # final row is forming
    emitted = []
    _persist(symbol, candles, tmp_path, emitted)

    decision_bar = candles[_closed_bar_index(candles)]
    persisted_ids = {
        mint_observation_id(symbol=s, timeframe="M5", bar_time=p["ts"] / 1000)
        for s, p in emitted
    }
    decision_id = mint_observation_id(symbol=symbol, timeframe="M5", bar_time=decision_bar.time)

    assert decision_bar.time == 1_300
    assert decision_id in persisted_ids
    assert all(s == symbol for s, _ in emitted)


def test_rollover_restart_and_duplicate_fetch_preserve_one_observation(tmp_path):
    _mt5_data.reset_candle_dedup_for_tests()
    emitted = []
    first = [_bar(1_000), _bar(1_300), _bar(1_600)]
    _persist("EURUSD", first, tmp_path, emitted)
    first_decision = mint_observation_id(
        symbol="EURUSD", timeframe="M5", bar_time=first[_closed_bar_index(first)].time,
    )

    # Repeated cycle and same-account restart both refetch the same window.
    _persist("EURUSD", first, tmp_path, emitted)
    _persist("EURUSD", first, tmp_path, emitted)

    rollover = [_bar(1_300), _bar(1_600), _bar(1_900)]
    _persist("EURUSD", rollover, tmp_path, emitted)
    second_decision = mint_observation_id(
        symbol="EURUSD", timeframe="M5", bar_time=rollover[_closed_bar_index(rollover)].time,
    )
    emitted_ids = [
        mint_observation_id(symbol=s, timeframe="M5", bar_time=p["ts"] / 1000)
        for s, p in emitted
    ]

    assert first_decision == "EURUSD.M5.1300"
    assert second_decision == "EURUSD.M5.1600"
    assert emitted_ids.count(first_decision) == 1
    assert emitted_ids.count(second_decision) == 1
    assert len(emitted_ids) == len(set(emitted_ids))


def test_nas100_identity_remains_canonical_at_persistence_boundary(tmp_path):
    _mt5_data.reset_candle_dedup_for_tests()
    emitted = []
    _persist("NAS100", [_bar(1_000), _bar(1_300), _bar(1_600)], tmp_path, emitted)
    assert {symbol for symbol, _ in emitted} == {"NAS100"}
    assert all("USTEC" not in str(item) for item in emitted)


def test_v1_versions_unchanged():
    from core.feature_registry import CURRENT_FEATURE_VERSION
    from core.production_data_contract import DATA_CONTRACT_VERSION
    from core.schema_registry import CURRENT_SCHEMA_VERSION

    assert DATA_CONTRACT_VERSION == "production_v1"
    assert CURRENT_SCHEMA_VERSION == 1
    assert CURRENT_FEATURE_VERSION == 1
