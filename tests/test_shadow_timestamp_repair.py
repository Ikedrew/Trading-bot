"""Focused coverage for single-conversion shadow timestamps and history repair."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.shadow.runtime import ShadowRuntime
from data import mt5_data
from data.mt5_data import _persist_candles_to_cache, _rows_to_candles
from research_engine.control_plane.shadow_timestamp_normalization import (
    NORMALIZATION_CONTRACT_VERSION,
    POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
    normalize_post_candle_utc_lifecycle,
)


UTC = POST_NORMALIZATION_PRODUCER_EPOCH_UTC + 3_581  # M5-aligned 2026-09-08 21:20Z
UTC -= UTC % 300
SYMBOL = "EURUSD"
OPPORTUNITY = f"{SYMBOL}*{UTC}*HAMMER"
TRADE_ID = "nshadow_timestamp_repair"


class CaptureWriter:
    def __init__(self, base_dir: Path) -> None:
        self.events: list[dict] = []
        self.base_dir = str(base_dir)

    def append(self, **kwargs) -> None:
        self.events.append(deepcopy(kwargs["event"]))


def _live_context() -> dict:
    return {
        "canonical_opportunity_id": OPPORTUNITY,
        "observation_id": f"{SYMBOL}.M5.{UTC}",
        "entity_id": f"{SYMBOL}_{UTC}",
        "symbol": SYMBOL,
        "cycle_id": 1,
        "bar_time_utc": UTC,
        "direction": "BUY",
        "pattern": "HAMMER",
        "strategy": "BREAKOUT",
        "score": 0.8,
        "regime": "TRENDING",
        "h4_regime": "TRENDING",
        "h1_bias": "BULLISH",
        "market_phase": "IMPULSE",
        "market_phase_confidence": 0.8,
        "bid": 1.0999,
        "ask": 1.1000,
        "structure": {"m5_candle_high": 1.1005, "m5_candle_low": 1.0990},
        "eligible_horizons": ["SCALP"],
        "horizon_assessments": [],
        "v10_action": "TRADE",
        "v10_rejection_stage": "",
        "v10_selected_horizon": "SCALP",
    }


def test_mt5_to_candle_event_to_shadow_applies_offset_exactly_once(tmp_path: Path):
    broker_offset = 10_800
    rows = [
        {"time": UTC + broker_offset, "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105, "tick_volume": 7},
        {"time": UTC + 300 + broker_offset, "open": 1.105, "high": 1.11, "low": 1.10, "close": 1.106, "tick_volume": 4},
    ]
    candles = _rows_to_candles(rows, utc_offset_seconds=broker_offset)
    assert candles[0].time == UTC

    emitted: list[dict] = []
    mt5_data.reset_candle_dedup_for_tests()
    with patch("core.config.ENABLE_CANDLE_REPLAY_CACHE", True), \
         patch("core.config.REPLAY_CACHE_DIR", str(tmp_path)), \
         patch("data.mt5_data._time.time", return_value=UTC + 600), \
         patch("core.event_stream.emit_candle", side_effect=lambda symbol, payload, **kwargs: emitted.append(deepcopy(payload)) or True):
        _persist_candles_to_cache(
            SYMBOL, 5, candles, source_utc_offset_seconds=broker_offset,
        )
    assert emitted[0]["ts"] == UTC * 1000
    assert emitted[0]["timestamp_semantics"] == "canonical_utc_bar_open_v1"
    assert emitted[0]["timestamp_normalization_version"] == "mt5_broker_to_utc_once_v1"
    assert emitted[0]["source_broker_offset_seconds"] == broker_offset

    writer = CaptureWriter(tmp_path / "shadow")
    runtime = ShadowRuntime(writer=writer)
    with patch("core.shadow.runtime.get_broker_offset_seconds", return_value=broker_offset):
        runtime.handle_opportunity(_live_context())
        runtime.evaluate_bar(
            symbol=SYMBOL,
            bar_time=UTC + 300,
            bar_high=1.101,
            bar_low=1.098,
            bar_close=1.099,
        )

    opened = next(event for event in writer.events if event["event_type"] == "OPEN")
    closed = next(event for event in writer.events if event["event_type"] == "CLOSE")
    assert opened["entry_market_time"] == UTC
    assert opened["entry_market_time_utc_epoch_s"] == UTC
    assert opened["broker_offset_seconds"] == broker_offset
    assert opened["market_timestamp_semantics"] == "canonical_utc_bar_open_v1"
    assert opened["market_timestamp_normalization_version"] == "mt5_broker_to_utc_once_v1"
    assert closed["exit_market_time"] == UTC + 300
    assert closed["exit_market_time_utc_epoch_s"] == UTC + 300


def _shadow_pair(*, offset: int = 10_800, entry: int = UTC, closes=(1.101, 1.102)):
    exit_time = entry + 300 * len(closes)
    common = {
        "schema_version": "shadow_runtime_v1",
        "shadow_trade_id": TRADE_ID,
        "canonical_opportunity_id": f"{SYMBOL}*{entry}*HAMMER",
        "symbol": SYMBOL,
        "horizon": "SCALP",
        "broker_offset_seconds": offset,
    }
    opened = {
        **common,
        "event_type": "OPEN",
        "event_market_time": entry,
        "event_market_time_utc_epoch_s": entry - offset,
        "entry_market_time": entry,
        "entry_market_time_utc_epoch_s": entry - offset,
        "opportunity_market_time": entry,
        "opportunity_market_time_utc_epoch_s": entry - offset,
        "recorded_at_utc_ms": (entry + 300) * 1000,
    }
    closed = {
        **common,
        "event_type": "CLOSE",
        "event_market_time": exit_time,
        "event_market_time_utc_epoch_s": exit_time - offset,
        "exit_market_time": exit_time,
        "exit_market_time_utc_epoch_s": exit_time - offset,
        "recorded_at_utc_ms": (exit_time + 300) * 1000,
        "bars_held": len(closes),
        "trade_state_progression": [
            {"bar": index, "r": 0.1 * index, "close": close}
            for index, close in enumerate(closes, 1)
        ],
        "data_gaps": [],
    }
    return opened, closed


def _candles(*, entry: int = UTC, closes=(1.101, 1.102)) -> list[dict]:
    result = []
    for index, close in enumerate(closes, 1):
        ts = (entry + index * 300) * 1000
        result.append({
            "ts_utc_ms": ts + 300_000,
            "type": "CANDLE",
            "symbol": SYMBOL,
            "timeframe": "M5",
            "payload": {"ts": ts, "o": close, "h": close + 0.001, "l": close - 0.001, "c": close, "v": 10},
            "source": "mt5_data",
            "schema_version": "events_v1",
            "event_layout_version": 1,
            "feature_version": 1,
        })
    return result


def test_historical_post_normalization_lifecycle_is_eligible_without_mutation():
    opened, closed = _shadow_pair()
    candles = _candles()
    originals = deepcopy((opened, closed, candles))
    result = normalize_post_candle_utc_lifecycle(opened, closed, candles)
    assert result.eligible is True
    assert result.normalized_open["entry_market_time_utc_epoch_s"] == UTC
    assert result.normalized_close["exit_market_time_utc_epoch_s"] == UTC + 600
    assert result.normalized_open["historical_timestamp_correction_seconds"] == 10_800
    assert result.provenance["normalization_contract_version"] == NORMALIZATION_CONTRACT_VERSION
    assert result.provenance["source_identity"] == "events_v1:CANDLE:mt5_data:M5"
    assert result.provenance["producer_authority"] == "git:7cdf2a6ba"
    assert (opened, closed, candles) == originals


def test_historical_normalization_uses_persisted_non_10800_offset():
    opened, closed = _shadow_pair(offset=7_200)
    result = normalize_post_candle_utc_lifecycle(opened, closed, _candles())
    assert result.eligible is True
    assert result.provenance["applied_offset_seconds"] == 7_200
    assert result.normalized_open["entry_market_time_utc_epoch_s"] == UTC


def test_pre_normalization_and_missing_or_inconsistent_offsets_fail_closed():
    pre_entry = POST_NORMALIZATION_PRODUCER_EPOCH_UTC - 1_219
    pre_entry -= pre_entry % 300
    opened, closed = _shadow_pair(entry=pre_entry)
    assert normalize_post_candle_utc_lifecycle(
        opened, closed, _candles(entry=pre_entry),
    ).reason == "PRE_NORMALIZATION_OR_AMBIGUOUS_EPOCH"

    opened, closed = _shadow_pair()
    opened.pop("broker_offset_seconds")
    assert normalize_post_candle_utc_lifecycle(opened, closed, _candles()).reason == "MISSING_BROKER_OFFSET"

    opened, closed = _shadow_pair()
    closed["broker_offset_seconds"] = 7_200
    assert normalize_post_candle_utc_lifecycle(opened, closed, _candles()).reason == "INCONSISTENT_BROKER_OFFSET"


def test_inconsistent_offset_application_and_path_mismatch_fail_closed():
    opened, closed = _shadow_pair()
    closed["exit_market_time_utc_epoch_s"] += 1
    assert normalize_post_candle_utc_lifecycle(opened, closed, _candles()).reason == "INCONSISTENT_OFFSET_APPLICATION"

    opened, closed = _shadow_pair()
    candles = _candles()
    candles[-1]["payload"]["c"] += 0.0001
    assert normalize_post_candle_utc_lifecycle(opened, closed, candles).reason == "PATH_CLOSE_MISMATCH"


def test_historical_provenance_is_reorder_invariant_and_change_sensitive():
    opened, closed = _shadow_pair()
    candles = _candles()
    first = normalize_post_candle_utc_lifecycle(opened, closed, candles)
    reordered = normalize_post_candle_utc_lifecycle(opened, closed, list(reversed(candles)))
    assert first.eligible and reordered.eligible
    assert first.provenance["digest"] == reordered.provenance["digest"]
    assert [row["payload"]["ts"] for row in reordered.ordered_m5_path] == sorted(
        row["payload"]["ts"] for row in candles
    )

    changed_candles = deepcopy(candles)
    changed_closed = deepcopy(closed)
    changed_candles[0]["payload"]["o"] -= 0.0001
    changed_candles[0]["payload"]["l"] -= 0.0001
    changed = normalize_post_candle_utc_lifecycle(opened, changed_closed, changed_candles)
    assert changed.eligible
    assert changed.provenance["digest"] != first.provenance["digest"]
