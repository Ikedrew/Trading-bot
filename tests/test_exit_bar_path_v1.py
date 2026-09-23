"""Focused contract tests for governed exit_bar_path_v1 evidence."""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane.evidence_readiness import (
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.control_plane.exit_bar_path import (
    SCHEMA_VERSION,
    LifecyclePathSource,
    build_exit_bar_path_v1,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
)


BASE_UTC = POST_NORMALIZATION_PRODUCER_EPOCH_UTC + 3_581
BASE_UTC -= BASE_UTC % 300


def _source(
    index: int = 0,
    *,
    offset: int = 10_800,
    closes: tuple[float, ...] = (1.101, 1.102),
) -> LifecyclePathSource:
    entry_time = BASE_UTC + index * 3_600
    symbol = "EURUSD"
    opportunity = f"{symbol}*{entry_time}*HAMMER_{index}"
    trade_id = f"nshadow_exit_path_{index}"
    common = {
        "schema_version": "shadow_runtime_v1",
        "shadow_trade_id": trade_id,
        "canonical_opportunity_id": opportunity,
        "symbol": symbol,
        "horizon": "SCALP",
        "broker_offset_seconds": offset,
    }
    opened = {
        **common,
        "event_type": "OPEN",
        "event_market_time": entry_time,
        "event_market_time_utc_epoch_s": entry_time - offset,
        "entry_market_time": entry_time,
        "entry_market_time_utc_epoch_s": entry_time - offset,
        "opportunity_market_time": entry_time,
        "opportunity_market_time_utc_epoch_s": entry_time - offset,
        "recorded_at_utc_ms": (entry_time + 60) * 1000,
        "construction": {
            "direction": "BUY",
            "entry_price": 1.1000,
            "stop_loss": 1.0980,
            "take_profit": 1.1040,
        },
    }
    exit_time = entry_time + len(closes) * 300
    closed = {
        **common,
        "event_type": "CLOSE",
        "event_market_time": exit_time,
        "event_market_time_utc_epoch_s": exit_time - offset,
        "exit_market_time": exit_time,
        "exit_market_time_utc_epoch_s": exit_time - offset,
        "recorded_at_utc_ms": (exit_time + 60) * 1000,
        "bars_held": len(closes),
        "trade_state_progression": [
            {"bar": bar, "r": bar / 10, "close": close}
            for bar, close in enumerate(closes, 1)
        ],
        "data_gaps": [],
    }
    candles = tuple({
        "ts_utc_ms": (entry_time + bar * 300 + 60) * 1000,
        "type": "CANDLE",
        "symbol": symbol,
        "timeframe": "M5",
        "payload": {
            "ts": (entry_time + bar * 300) * 1000,
            "o": close - 0.0002,
            "h": close + 0.0005,
            "l": close - 0.0005,
            "c": close,
            "v": 10,
        },
        "source": "mt5_data",
        "schema_version": "events_v1",
        "event_layout_version": 1,
        "feature_version": 1,
    } for bar, close in enumerate(closes, 1))
    return LifecyclePathSource(opened, closed, candles)


def _replace(source: LifecyclePathSource, **changes) -> LifecyclePathSource:
    values = {
        "open_event": deepcopy(dict(source.open_event)),
        "close_event": deepcopy(dict(source.close_event)),
        "candle_events": tuple(deepcopy(dict(item)) for item in source.candle_events),
    }
    values.update(changes)
    return LifecyclePathSource(**values)


def test_eligible_lifecycle_exposes_complete_ordered_contract_without_mutation():
    source = _source()
    original = deepcopy(source)
    evidence = build_exit_bar_path_v1((source,))

    assert evidence.schema_version == SCHEMA_VERSION == "exit_bar_path_v1"
    assert evidence.summary.total_completed_lifecycles == 1
    assert evidence.summary.eligible_lifecycles == 1
    assert evidence.summary.eligible_distinct_canonical_opportunities == 1
    assert evidence.summary.coverage_percentage == 100.0
    record = evidence.records[0]
    assert record.lifecycle_identity == (
        source.open_event["shadow_trade_id"],
        source.open_event["canonical_opportunity_id"],
        "SCALP",
    )
    assert record.canonical_symbol == "EURUSD"
    assert record.direction == "BUY"
    assert record.entry_price == 1.1000
    assert record.baseline_stop_loss == 1.0980
    assert record.baseline_take_profit == 1.1040
    assert record.timeframe == "M5"
    assert [bar.timestamp_utc_ms for bar in record.ordered_m5_bars] == sorted(
        bar.timestamp_utc_ms for bar in record.ordered_m5_bars
    )
    assert record.timestamp_semantics == "canonical_utc_bar_open_v1"
    assert record.timestamp_version == "shadow_post_candle_utc_normalization_v1"
    assert source == original


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda source: _replace(source, candle_events=()), "MISSING_M5_PATH"),
        (
            lambda source: _replace(
                source, candle_events=source.candle_events[:-1],
            ),
            "MISSING_EXIT_M5_BAR",
        ),
        (
            lambda source: _replace(
                source,
                close_event={**source.close_event, "data_gaps": [{"missing": 1}]},
            ),
            "RUNTIME_REPORTED_DATA_GAP",
        ),
        (
            lambda source: _replace(
                source,
                candle_events=(
                    source.candle_events[0],
                    {
                        **source.candle_events[0],
                        "payload": {
                            **source.candle_events[0]["payload"],
                            "h": 1.104,
                            "c": 1.103,
                        },
                    },
                    source.candle_events[1],
                ),
            ),
            "CONFLICTING_DUPLICATE_CANDLE",
        ),
        (
            lambda source: _replace(
                source,
                close_event={**source.close_event, "symbol": "GBPUSD"},
            ),
            "CANONICAL_SYMBOL_MISMATCH",
        ),
        (
            lambda source: _replace(
                source,
                candle_events=(
                    {
                        **source.candle_events[0],
                        "payload": {
                            **source.candle_events[0]["payload"], "h": "bad",
                        },
                    },
                    source.candle_events[1],
                ),
            ),
            "INVALID_CANDLE_PAYLOAD",
        ),
    ],
)
def test_normalization_exclusions_are_preserved(mutator, reason):
    evidence = build_exit_bar_path_v1((mutator(_source()),))
    assert not evidence.records
    assert evidence.summary.exclusions_by_reason == {reason: 1}
    assert evidence.exclusions[0].reason == reason


def test_path_layer_fails_closed_on_geometry_and_provenance_failures():
    source = _source()
    invalid_open = deepcopy(dict(source.open_event))
    invalid_open["construction"]["stop_loss"] = 1.105
    geometry = build_exit_bar_path_v1((_replace(source, open_event=invalid_open),))
    assert geometry.exclusions[0].reason == "INVALID_BASELINE_GEOMETRY"

    unsupported_open = deepcopy(dict(source.open_event))
    unsupported_open["unsupported"] = object()
    provenance = build_exit_bar_path_v1((_replace(source, open_event=unsupported_open),))
    assert provenance.exclusions[0].reason == "PROVENANCE_FAILURE"

    duplicates = build_exit_bar_path_v1((source, deepcopy(source)))
    assert not duplicates.records
    assert duplicates.summary.exclusions_by_reason == {
        "DUPLICATE_LIFECYCLE_IDENTITY": 2,
    }
    assert all(item.eligibility_state == "EXCLUDED" for item in duplicates.exclusions)


def test_reordering_is_invariant_and_analytical_changes_change_digest():
    first_source = _source(0)
    second_source = _source(1)
    first = build_exit_bar_path_v1((first_source, second_source))
    reordered_source = _replace(
        first_source, candle_events=tuple(reversed(first_source.candle_events)),
    )
    reordered = build_exit_bar_path_v1((second_source, reordered_source))
    assert reordered.provenance["digest"] == first.provenance["digest"]
    assert [item.lifecycle_identity for item in reordered.records] == [
        item.lifecycle_identity for item in first.records
    ]

    candles = [deepcopy(dict(item)) for item in first_source.candle_events]
    candles[0]["payload"] = deepcopy(candles[0]["payload"])
    candles[0]["payload"]["o"] -= 0.0001
    candles[0]["payload"]["l"] -= 0.0001
    changed = build_exit_bar_path_v1((
        _replace(first_source, candle_events=tuple(candles)), second_source,
    ))
    assert changed.provenance["digest"] != first.provenance["digest"]
    assert changed.records[0].analytical_digest != first.records[0].analytical_digest

    changed_open = deepcopy(dict(first_source.open_event))
    changed_open["construction"]["stop_loss"] = 1.0975
    geometry = build_exit_bar_path_v1((
        _replace(first_source, open_event=changed_open), second_source,
    ))
    assert geometry.records[0].analytical_digest != first.records[0].analytical_digest

    changed_open = deepcopy(dict(first_source.open_event))
    changed_open["source_object_identity"] = "immutable-object-version-2"
    source_changed = build_exit_bar_path_v1((
        _replace(first_source, open_event=changed_open), second_source,
    ))
    assert source_changed.provenance["digest"] != first.provenance["digest"]


def test_population_summary_and_generic_readiness_are_mechanical():
    valid = _source()
    missing = _replace(_source(1), candle_events=())
    evidence = build_exit_bar_path_v1((valid, missing))
    assert evidence.summary.eligible_lifecycles == 1
    assert evidence.summary.coverage_percentage == 50.0
    assert evidence.summary.counts_by_symbol == {"EURUSD": 1}
    assert evidence.summary.counts_by_horizon == {"SCALP": 1}

    readiness = evidence.readiness(EvidenceRequirement(
        minimum_valid_count=2,
        minimum_distinct_count=2,
        minimum_coverage=0.95,
    ))
    assert readiness.state == "WAITING_DATA"
    assert readiness.current_valid_count == 1
    assert readiness.required_valid_count == 2
    assert readiness.remaining_valid_count == 1
    assert readiness.current_coverage == 0.5
    assert readiness.required_coverage == 0.95
    assert readiness.blockers == (
        "VALID_COUNT_BELOW_REQUIRED",
        "DISTINCT_COUNT_BELOW_REQUIRED",
        "COVERAGE_BELOW_REQUIRED",
    )


def test_generic_readiness_supports_163_of_200_without_question_logic():
    result = evaluate_evidence_readiness(
        total_count=167,
        valid_count=163,
        distinct_valid_count=163,
        requirement=EvidenceRequirement(
            minimum_valid_count=200,
            minimum_distinct_count=200,
            minimum_coverage=0.95,
        ),
    )
    assert result.state == "WAITING_DATA"
    assert result.remaining_valid_count == 37
    assert result.remaining_distinct_count == 37
    assert result.coverage_gate_satisfied is True
    assert result.current_coverage == pytest.approx(163 / 167)
