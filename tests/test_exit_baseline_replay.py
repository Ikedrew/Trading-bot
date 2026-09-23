"""Focused tests for governed SHADOW_BASELINE_V1 replay and reproduction."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import EvidenceRequirement
from research_engine.control_plane.exit_bar_path import (
    LifecyclePathSource,
    build_exit_bar_path_v1,
)
from research_engine.control_plane.exit_baseline_replay import (
    AUTHORITY_MISSING,
    REPRODUCTION_FAILED,
    BaselineReproductionExclusion,
    BaselineReproductionRecord,
    build_baseline_reproduction_population,
    replay_shadow_baseline_v1,
    reproduce_shadow_baseline_v1,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
)


BASE_UTC = POST_NORMALIZATION_PRODUCER_EPOCH_UTC + 3_581
BASE_UTC -= BASE_UTC % 300


def _bars(*values):
    return tuple(values)


def _source(
    *,
    index: int = 0,
    direction: str = "BUY",
    entry: float = 100.0,
    stop: float = 98.0,
    target: float = 104.0,
    bars=_bars((100.0, 101.0, 99.0, 100.0),),
    exit_reason: str = "timeout",
    exit_price: float = 100.0,
    pnl_r: float = 0.0,
    mfe_r: float = 0.5,
) -> LifecyclePathSource:
    offset = 10_800
    entry_time = BASE_UTC + index * 7_200
    symbol = "EURUSD"
    opportunity = f"{symbol}*{entry_time}*BASELINE_{index}"
    common = {
        "schema_version": "shadow_runtime_v1",
        "shadow_trade_id": f"nshadow_baseline_{index}",
        "canonical_opportunity_id": opportunity,
        "symbol": symbol,
        "horizon": "SCALP",
        "broker_offset_seconds": offset,
        "simulation_model_version": "simulation_v1",
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
        "simulation_assumptions": {"timeout_bars": 9},
        "construction": {
            "direction": direction,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": target,
        },
    }
    exit_time = entry_time + len(bars) * 300
    closed = {
        **common,
        "event_type": "CLOSE",
        "event_market_time": exit_time,
        "event_market_time_utc_epoch_s": exit_time - offset,
        "exit_market_time": exit_time,
        "exit_market_time_utc_epoch_s": exit_time - offset,
        "recorded_at_utc_ms": (exit_time + 60) * 1000,
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "bars_held": len(bars),
        "outcome": {"pnl_r_multiple": pnl_r, "mfe_r": mfe_r},
        "trade_state_progression": [
            {"bar": number, "r": 0.0, "close": values[3]}
            for number, values in enumerate(bars, 1)
        ],
        "data_gaps": [],
    }
    candles = tuple({
        "ts_utc_ms": (entry_time + number * 300 + 60) * 1000,
        "type": "CANDLE",
        "symbol": symbol,
        "timeframe": "M5",
        "payload": {
            "ts": (entry_time + number * 300) * 1000,
            "o": values[0], "h": values[1], "l": values[2], "c": values[3],
            "v": 100,
        },
        "source": "mt5_data",
        "schema_version": "events_v1",
        "event_layout_version": 1,
        "feature_version": 1,
    } for number, values in enumerate(bars, 1))
    return LifecyclePathSource(opened, closed, candles)


def _evidence(*sources):
    result = build_exit_bar_path_v1(sources)
    assert len(result.records) == len(sources), result.summary.exclusions_by_reason
    return result


def _record(source):
    return _evidence(source).records[0]


def _replace_record(record, **changes):
    changed = replace(record, **changes)
    material = changed.analytical_record()
    material.pop("analytical_digest")
    return replace(changed, analytical_digest=evidence_digest((material,)))


@pytest.mark.parametrize(
    ("source", "reason", "price", "r_value"),
    [
        (
            _source(
                bars=_bars((100.0, 101.0, 97.0, 98.5),),
                exit_reason="stop_loss", exit_price=98.0, pnl_r=-1.0, mfe_r=0.5,
            ),
            "stop_loss", 98.0, -1.0,
        ),
        (
            _source(
                index=1, bars=_bars((100.0, 105.0, 99.0, 103.0),),
                exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
            ),
            "take_profit", 104.0, 2.0,
        ),
        (
            _source(
                index=2, direction="SELL", stop=102.0, target=96.0,
                bars=_bars((100.0, 103.0, 99.0, 102.0),),
                exit_reason="stop_loss", exit_price=102.0, pnl_r=-1.0, mfe_r=0.5,
            ),
            "stop_loss", 102.0, -1.0,
        ),
        (
            _source(
                index=3, direction="SELL", stop=102.0, target=96.0,
                bars=_bars((100.0, 101.0, 95.0, 97.0),),
                exit_reason="take_profit", exit_price=96.0, pnl_r=2.0, mfe_r=2.5,
            ),
            "take_profit", 96.0, 2.0,
        ),
    ],
)
def test_long_and_short_sl_tp_replay(source, reason, price, r_value):
    original = deepcopy(source)
    attempt = replay_shadow_baseline_v1(_record(source))
    assert attempt.eligibility_state == "ELIGIBLE"
    assert attempt.replay.exit_reason == reason
    assert attempt.replay.exit_price == price
    assert attempt.replay.pnl_r_multiple == pytest.approx(r_value)
    assert source == original


def test_same_bar_sl_and_tp_resolves_to_sl_and_includes_full_bar_mfe():
    source = _source(
        bars=_bars((100.0, 110.0, 97.0, 101.0),),
        exit_reason="stop_loss", exit_price=98.0, pnl_r=-1.0, mfe_r=5.0,
    )
    replay = replay_shadow_baseline_v1(_record(source)).replay
    assert replay.exit_reason == "stop_loss"
    assert replay.exit_price == 98.0
    assert replay.mfe_r == 5.0


def test_timeout_uses_ninth_bar_close_and_exact_bars_held():
    bars = tuple((100.0, 101.0, 99.0, 100.0 + number / 100) for number in range(1, 10))
    source = _source(
        bars=bars, exit_reason="timeout", exit_price=100.09,
        pnl_r=0.045, mfe_r=0.5,
    )
    replay = replay_shadow_baseline_v1(_record(source)).replay
    assert replay.exit_reason == "timeout"
    assert replay.exit_price == 100.09
    assert replay.bars_held == 9
    assert replay.exit_utc_epoch_s == source.close_event["exit_market_time"]


@pytest.mark.parametrize(
    ("last_bar", "expected_reason", "expected_price"),
    [
        ((100.0, 105.0, 99.0, 100.5), "take_profit", 104.0),
        ((100.0, 105.0, 97.0, 100.5), "stop_loss", 98.0),
    ],
)
def test_sl_tp_precede_timeout_on_timeout_bar(last_bar, expected_reason, expected_price):
    bars = ((100.0, 101.0, 99.0, 100.0),) * 8 + (last_bar,)
    source = _source(
        bars=bars,
        exit_reason=expected_reason,
        exit_price=expected_price,
        pnl_r=-1.0 if expected_reason == "stop_loss" else 2.0,
        mfe_r=2.5,
    )
    replay = replay_shadow_baseline_v1(_record(source)).replay
    assert (replay.exit_reason, replay.exit_price, replay.bars_held) == (
        expected_reason, expected_price, 9,
    )


def test_first_ordered_post_entry_bar_is_bar_one_and_can_exit():
    source = _source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    )
    record = _record(source)
    replay = replay_shadow_baseline_v1(record).replay
    assert record.ordered_m5_bars[0].timestamp_utc_ms == (
        record.entry_utc_epoch_s + 300
    ) * 1000
    assert replay.bars_held == 1


def test_direction_aware_mfe_uses_low_for_sell():
    source = _source(
        direction="SELL", stop=102.0, target=96.0,
        bars=_bars((100.0, 103.0, 95.0, 99.0),),
        exit_reason="stop_loss", exit_price=102.0, pnl_r=-1.0, mfe_r=2.5,
    )
    replay = replay_shadow_baseline_v1(_record(source)).replay
    assert replay.pnl_r_multiple == -1.0
    assert replay.mfe_r == 2.5


def test_reproduction_success_and_deterministic_digests():
    record = _record(_source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    ))
    first = reproduce_shadow_baseline_v1(record)
    second = reproduce_shadow_baseline_v1(record)
    assert isinstance(first, BaselineReproductionRecord)
    assert first.reproduction_digest == second.reproduction_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_exit_reason", "stop_loss"),
        ("exit_utc_epoch_s", BASE_UTC + 600),
        ("observed_bars_held", 2),
        ("observed_exit_price", 103.0),
        ("observed_pnl_r_multiple", 1.0),
        ("observed_mfe_r", 1.0),
    ],
)
def test_each_comparison_mismatch_fails_closed_with_field_diagnostic(field, value):
    record = _record(_source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    ))
    result = reproduce_shadow_baseline_v1(_replace_record(record, **{field: value}))
    assert isinstance(result, BaselineReproductionExclusion)
    assert result.reason == REPRODUCTION_FAILED
    assert [item.field for item in result.diagnostics] == [field]


def test_exit_price_absolute_tolerance_has_no_relative_component():
    record = _record(_source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    ))
    within = reproduce_shadow_baseline_v1(_replace_record(
        record, observed_exit_price=104.0 + 5e-13,
    ))
    outside = reproduce_shadow_baseline_v1(_replace_record(
        record, observed_exit_price=104.0 + 2e-12,
    ))
    assert isinstance(within, BaselineReproductionRecord)
    assert isinstance(outside, BaselineReproductionExclusion)


def test_r_and_mfe_are_rounded_to_four_decimals_only_for_comparison():
    bars = ((100.0, 101.23456, 99.0, 100.0),) * 8 + (
        (100.0, 101.23456, 99.0, 101.23456),
    )
    record = _record(_source(
        bars=bars, exit_reason="timeout", exit_price=101.23456,
        pnl_r=0.6173, mfe_r=0.6173,
    ))
    result = reproduce_shadow_baseline_v1(record)
    assert isinstance(result, BaselineReproductionRecord)
    assert result.replay.pnl_r_multiple == pytest.approx(0.61728)
    assert result.replay.mfe_r == pytest.approx(0.61728)


def test_missing_or_invalid_comparison_authority_fails_closed():
    record = _record(_source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    ))
    result = reproduce_shadow_baseline_v1(_replace_record(
        record, observed_mfe_r=None,
    ))
    assert isinstance(result, BaselineReproductionExclusion)
    assert result.reason == AUTHORITY_MISSING
    assert result.diagnostics[0].field == "observed_mfe_r"


def test_population_is_reorder_invariant_ready_and_digest_sensitive():
    first = _source(
        index=10, bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    )
    second = _source(
        index=11, bars=_bars((100.0, 101.0, 97.0, 98.5),),
        exit_reason="stop_loss", exit_price=98.0, pnl_r=-1.0, mfe_r=0.5,
    )
    population = build_baseline_reproduction_population(_evidence(first, second))
    reordered = build_baseline_reproduction_population(_evidence(second, first))
    assert population.provenance["digest"] == reordered.provenance["digest"]
    assert population.summary.successful_reproductions == 2
    readiness = population.readiness(EvidenceRequirement(2, 2, 1.0))
    assert readiness.state == "READY"

    changed = deepcopy(first)
    candles = list(changed.candle_events)
    candles[0] = deepcopy(candles[0])
    candles[0]["payload"] = deepcopy(candles[0]["payload"])
    candles[0]["payload"]["h"] = 106.0
    changed = LifecyclePathSource(
        changed.open_event, changed.close_event, tuple(candles),
    )
    changed_population = build_baseline_reproduction_population(
        _evidence(changed, second)
    )
    assert changed_population.provenance["digest"] != population.provenance["digest"]


def test_policy_and_observed_authority_are_bound_into_provenance():
    source = _source(
        bars=_bars((100.0, 105.0, 99.0, 103.0),),
        exit_reason="take_profit", exit_price=104.0, pnl_r=2.0, mfe_r=2.5,
    )
    population = build_baseline_reproduction_population(_evidence(source))
    provenance = population.provenance
    assert provenance["baseline_policy"]["policy_id"] == "SHADOW_BASELINE_V1"
    altered = deepcopy(provenance)
    original_digest = altered.pop("digest")
    altered["baseline_policy"]["policy_id"] = "DIFFERENT_POLICY"
    assert evidence_digest((altered,)) != original_digest
