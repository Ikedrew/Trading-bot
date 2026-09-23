"""Focused tests for the deterministic nine-policy HD09 candidate replay layer."""
from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from dataclasses import replace

import pytest

from research_engine.control_plane import exit_candidate_replay as candidate_replay
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.exit_bar_path import (
    LifecyclePathSource,
    build_exit_bar_path_v1,
)
from research_engine.control_plane.exit_baseline_replay import (
    BaselineReproductionRecord,
    build_baseline_reproduction_population,
    replay_shadow_baseline_v1,
)
from research_engine.control_plane.exit_candidate_replay import (
    BASELINE_REPRODUCTION_REQUIRED,
    CANDIDATE_POLICIES,
    CANDIDATE_POLICY_BY_ID,
    CANDIDATE_POLICY_COUNT,
    CANDIDATE_POLICY_IDS,
    CANDIDATE_REPLAY_SCHEMA_VERSION,
    GEOMETRY_INVALID,
    IDENTITY_CONFLICT,
    NON_FINITE_RESULT,
    PATH_INSUFFICIENT,
    POLICY_IDENTITY_INVALID,
    POLICY_PARAMETERS_MISMATCH,
    PROVENANCE_FAILURE,
    CandidateReplayExclusion,
    CandidateReplayResult,
    build_candidate_replay_population,
    candidate_replay_digest,
    replay_candidate_policy,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
)
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1

BASE_UTC = POST_NORMALIZATION_PRODUCER_EPOCH_UTC + 3_581
BASE_UTC -= BASE_UTC % 300

# Benign bars used by the governed synthetic lifecycles.
FLAT = (100.0, 100.6, 99.4, 100.3)   # never touches SL 98 / TP 104
SOFT = (100.0, 100.3, 99.5, 100.1)   # highs stay <= 100.4 (never activates P1)


def _bars(*values):
    return tuple(values)


def _repeat(bar, count):
    return tuple(bar for _ in range(count))


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
    horizon: str = "SCALP",
    timeout_bars: int = 9,
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
        "horizon": horizon,
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
        "simulation_assumptions": {"timeout_bars": timeout_bars},
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


def _record(source):
    evidence = build_exit_bar_path_v1((source,))
    assert len(evidence.records) == 1, evidence.summary.exclusions_by_reason
    return evidence.records[0]


def _replace_record(record, **changes):
    changed = replace(record, **changes)
    material = changed.analytical_record()
    material.pop("analytical_digest")
    return replace(changed, analytical_digest=evidence_digest((material,)))


def _reproduced_source(
    *,
    index: int = 0,
    direction: str = "BUY",
    entry: float = 100.0,
    stop: float = 98.0,
    target: float = 104.0,
    bars=_repeat(FLAT, 9),
    horizon: str = "SCALP",
    timeout_bars: int = 9,
    observed_override: dict | None = None,
) -> LifecyclePathSource:
    """Build a source whose observed outcome equals its governed baseline replay."""
    common = dict(
        index=index, direction=direction, entry=entry, stop=stop, target=target,
        bars=bars, horizon=horizon, timeout_bars=timeout_bars,
    )
    # Probe observed values must satisfy build-time authority for ANY path:
    # only "timeout" is cross-checked against baseline_timeout_bars, so the
    # placeholder uses a finite stop_loss outcome.  Authoritative observed
    # values are stamped from the probe replay below.
    probe = _source(
        **common, exit_reason="stop_loss", exit_price=stop, pnl_r=-1.0, mfe_r=0.5,
    )
    attempt = replay_shadow_baseline_v1(_record(probe))
    assert attempt.replay is not None, (
        f"probe replay failed: {attempt.reason} "
        f"{[item.record() for item in attempt.diagnostics]}"
    )
    replay = attempt.replay
    assert replay.bars_held == len(bars), "governed paths must exit on their final bar"
    observed = dict(
        exit_reason=replay.exit_reason,
        exit_price=replay.exit_price,
        pnl_r=round(replay.pnl_r_multiple, 4),
        mfe_r=round(replay.mfe_r, 4),
    )
    if observed_override:
        observed.update(observed_override)
    return _source(**common, **observed)


def _paired(*sources):
    evidence = build_exit_bar_path_v1(sources)
    reproduction = build_baseline_reproduction_population(evidence)
    return evidence, reproduction


def _one():
    """One fully reproduced governed lifecycle: FLAT x 9, SCALP timeout."""
    evidence, reproduction = _paired(_reproduced_source(bars=_repeat(FLAT, 9)))
    assert len(evidence.records) == 1, evidence.summary.exclusions_by_reason
    assert len(reproduction.records) == 1, reproduction.summary.mismatch_counts_by_field
    return evidence.records[0], reproduction.records[0]


def _clone_reproduction(reproduction, records):
    new_records = tuple(records)
    provenance = dict(reproduction.provenance)
    provenance.pop("digest", None)
    provenance["successful_reproduction_digest"] = evidence_digest(
        [item.analytical_record() for item in new_records]
    )
    provenance["digest"] = evidence_digest((provenance,))
    return replace(reproduction, records=new_records, provenance=provenance)


def _population_rows(evidence, reproduction):
    return build_candidate_replay_population(evidence, reproduction)


def _pair_record(bars, **kwargs):
    evidence, reproduction = _paired(_reproduced_source(bars=bars, **kwargs))
    assert len(evidence.records) == 1, evidence.summary.exclusions_by_reason
    assert len(reproduction.records) == 1, reproduction.summary.mismatch_counts_by_field
    return evidence.records[0], reproduction.records[0]


def test_exactly_nine_frozen_candidate_policies_are_registered():
    assert CANDIDATE_POLICY_COUNT == 9
    assert CANDIDATE_POLICIES == tuple(CANDIDATE_POLICIES_V1)
    assert CANDIDATE_POLICY_IDS == (
        "TRAIL_ACT_0_25R_DIST_0_10R_V1",
        "TRAIL_ACT_0_50R_DIST_0_25R_V1",
        "TRAIL_ACT_1_00R_DIST_0_50R_V1",
        "REDUCED_TP_0_50R_V1",
        "REDUCED_TP_1_00R_V1",
        "REDUCED_TP_1_50R_V1",
        "TIME_CAP_20_BARS_V1",
        "TIME_CAP_60_BARS_V1",
        "TIME_CAP_180_BARS_V1",
    )
    type_counts = Counter(item["policy_type"] for item in CANDIDATE_POLICIES)
    assert type_counts == {"TRAILING": 3, "REDUCED_TP": 3, "TIME_CAP": 3}
    assert dict(CANDIDATE_POLICY_BY_ID["REDUCED_TP_0_50R_V1"]) == {
        "policy_id": "REDUCED_TP_0_50R_V1", "policy_type": "REDUCED_TP", "target_cap_r": 0.50,
    }
    assert dict(CANDIDATE_POLICY_BY_ID["TIME_CAP_180_BARS_V1"]) == {
        "policy_id": "TIME_CAP_180_BARS_V1", "policy_type": "TIME_CAP", "bar_cap": 180,
    }
    assert CANDIDATE_REPLAY_SCHEMA_VERSION == "exit_candidate_replay_v1"


def test_policy_id_and_exact_frozen_mapping_both_resolve_to_identical_rows():
    record, reproduction = _one()
    by_id = replay_candidate_policy(record, reproduction, CANDIDATE_POLICY_IDS[0])
    by_mapping = replay_candidate_policy(
        record, reproduction, dict(CANDIDATE_POLICIES[0]),
    )
    assert isinstance(by_id, CandidateReplayResult)
    assert isinstance(by_mapping, CandidateReplayResult)
    assert by_id == by_mapping
    assert by_id.candidate_replay_digest == candidate_replay_digest(by_id)


def test_trailing_stop_does_not_move_before_activation_threshold():
    bars = _repeat(SOFT, 8) + ((100.1, 100.4, 97.8, 98.0),)
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Excursion never reaches 0.50 R, so the stop never moves off the original SL.
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("stop_loss", 98.0, 9)
    assert row.candidate_r == pytest.approx(-1.0)
    assert row.exit_utc_epoch_s == BASE_UTC + 9 * 300
    assert row.exit_price == record.baseline_stop_loss


def test_trailing_effective_stop_is_next_bar_and_uses_completed_bars_only():
    bars = _bars((100.0, 101.6, 99.8, 101.0), (101.5, 105.0, 100.3, 104.5))
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 1 activates but its own stop (101.4) may NOT trigger inside bar 1:
    # the exit lands on bar 2, at 101.4 - not at a stop derived from bar 2's
    # future high of 105 (which would be 104.8).
    assert (row.exit_reason, row.bars_held) == ("stop_loss", 2)
    assert row.exit_price == pytest.approx(101.4, abs=1e-9)
    assert row.candidate_r == pytest.approx(0.7)
    assert row.candidate_mfe_r == pytest.approx(2.5)
    assert row.exit_utc_epoch_s == BASE_UTC + 2 * 300


def test_trailing_protective_stop_precedes_original_tp_on_same_bar():
    bars = _bars((100.0, 101.6, 99.8, 101.0), (101.5, 105.0, 100.3, 104.5))
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 2 touches both the effective stop and the original TP (high 105):
    # the effective protective stop must resolve first.
    assert row.exit_reason == "stop_loss"
    assert row.exit_price != record.baseline_take_profit
    baseline = replay_shadow_baseline_v1(record).replay
    assert baseline.exit_reason == "take_profit" and baseline.exit_price == 104.0


def test_trailing_uses_running_extreme_monotonically():
    bars = (
        (100.0, 101.6, 99.8, 101.0),
        (101.55, 101.55, 101.45, 101.5),
        (101.5, 101.55, 101.3, 101.4),
    ) + _repeat(FLAT, 6)
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 1 extreme 101.6 locks the trailing level at 101.4.  Bar 2 prints a
    # lower high (101.55): a naive recompute would loosen the stop to 101.35
    # and fill bar 3 at 101.35.  The monotonic running extreme keeps 101.4.
    assert (row.exit_reason, row.bars_held) == ("stop_loss", 3)
    assert row.exit_price == pytest.approx(101.4, abs=1e-9)
    assert row.candidate_r == pytest.approx(0.7)
    assert row.candidate_mfe_r == pytest.approx(0.8)
    assert row.exit_utc_epoch_s == BASE_UTC + 3 * 300


def test_trailing_stop_chooses_more_protective_level_and_keeps_original_sl():
    bars = _bars((100.0, 101.6, 99.8, 101.0), (101.0, 101.6, 97.5, 97.6))
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 2 trades through BOTH the trailing level (101.4) and the original
    # SL (98): the effective stop is max(original SL, trailing) for BUY, so
    # the fill is the more protective 101.4 - never 98, never weaker.
    assert row.exit_reason == "stop_loss"
    assert row.exit_price == pytest.approx(101.4, abs=1e-9)
    assert row.exit_price > record.baseline_stop_loss
    assert row.bars_held == 2
    assert row.candidate_r == pytest.approx(0.7)
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.exit_price, baseline.bars_held) == (
        "stop_loss", 98.0, 2,
    )


def test_trailing_sell_uses_low_extreme_and_min_protective_stop():
    bars = (
        (100.0, 100.5, 98.2, 99.0),
        (99.0, 99.2, 98.0, 98.2),
    ) + _repeat(FLAT, 7)
    record, reproduction = _pair_record(
        bars, direction="SELL", stop=102.0, target=96.0,
    )
    row = replay_candidate_policy(record, reproduction, "TRAIL_ACT_0_25R_DIST_0_10R_V1")
    assert isinstance(row, CandidateReplayResult)
    # SELL trail = min(original SL 102, lowest completed low 98.2 + 0.10R*2).
    expected_stop = min(102.0, 98.2 + 0.10 * 2.0)
    assert (row.exit_reason, row.bars_held) == ("stop_loss", 2)
    assert row.exit_price == pytest.approx(expected_stop, abs=1e-9)
    assert row.exit_price < record.baseline_stop_loss  # more protective for SELL
    assert row.candidate_r == pytest.approx(0.8)
    assert row.candidate_mfe_r == pytest.approx(1.0)
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.bars_held) == ("timeout", 9)


def test_reduced_tp_moves_target_closer_and_exits_at_exact_candidate_price():
    bars = _bars((100.0, 101.2, 99.9, 100.5)) + _repeat(FLAT, 8)
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "REDUCED_TP_0_50R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Candidate TP = entry + 0.50R*2 = 101 fills at bar 1 while the baseline
    # runs on to its timeout at bar 9 - an exact-price fill inside the path.
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("take_profit", 101.0, 1)
    assert row.candidate_r == pytest.approx(0.5)
    assert row.candidate_mfe_r == pytest.approx(0.6)
    assert row.exit_utc_epoch_s == BASE_UTC + 300
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.bars_held) == ("timeout", 9)


def test_reduced_tp_never_extends_beyond_original_target():
    bars = _bars((100.0, 101.1, 99.9, 100.6))
    record, reproduction = _pair_record(bars, target=101.0)
    row = replay_candidate_policy(record, reproduction, "REDUCED_TP_1_00R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Cap value (entry + 1.00R*2 = 102) lies beyond the original TP 101:
    # the candidate target must stay at the original 101, never 102.
    assert row.exit_reason == "take_profit"
    assert row.exit_price == record.baseline_take_profit == 101.0
    assert row.exit_price != pytest.approx(102.0)
    assert row.bars_held == 1
    assert row.candidate_r == pytest.approx(0.5)


def test_reduced_tp_original_sl_is_evaluated_before_capped_target():
    bars = _bars((100.0, 102.0, 97.5, 101.0))
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "REDUCED_TP_1_00R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 1 trades through the original SL (98) AND reaches the capped TP
    # (100 + 1.00R*2 = 102): barrier order is SL first, capped TP second.
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("stop_loss", 98.0, 1)
    assert row.exit_price != pytest.approx(102.0)
    assert row.candidate_r == pytest.approx(-1.0)
    assert row.candidate_mfe_r == pytest.approx(1.0)


def test_reduced_tp_preserves_baseline_timeout_as_third_barrier():
    record, reproduction = _pair_record(_repeat(FLAT, 9))
    row = replay_candidate_policy(record, reproduction, "REDUCED_TP_0_50R_V1")
    assert isinstance(row, CandidateReplayResult)
    # No SL or capped-TP touch anywhere on the path: the canonical reason
    # stays the baseline "timeout" at bar 9 close, at the baseline exit.
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("timeout", 100.3, 9)
    assert row.candidate_r == pytest.approx(0.15)
    baseline = replay_shadow_baseline_v1(record).replay
    assert (
        row.exit_reason, row.exit_price, row.bars_held, row.exit_utc_epoch_s
    ) == (
        baseline.exit_reason, baseline.exit_price, baseline.bars_held,
        baseline.exit_utc_epoch_s,
    )


def test_reduced_tp_fills_before_timeout_on_the_same_final_bar():
    bars = _repeat(FLAT, 8) + ((100.0, 101.05, 99.4, 100.3),)
    record, reproduction = _pair_record(bars)
    row = replay_candidate_policy(record, reproduction, "REDUCED_TP_0_50R_V1")
    assert isinstance(row, CandidateReplayResult)
    # Bar 9 is both the capped-TP touch (high 101.05 >= 101) and the
    # baseline timeout bar: capped TP fills before the timeout reason.
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("take_profit", 101.0, 9)
    assert row.candidate_r == pytest.approx(0.5)
    baseline = replay_shadow_baseline_v1(record).replay
    assert baseline.exit_reason == "timeout" and baseline.bars_held == 9


def test_time_cap_effective_cap_is_min_of_candidate_cap_and_baseline_timeout():
    record, reproduction = _pair_record(
        _repeat(FLAT, 96), horizon="INTRADAY", timeout_bars=96,
    )
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.bars_held) == ("timeout", 96)

    cap_60 = replay_candidate_policy(record, reproduction, "TIME_CAP_60_BARS_V1")
    assert isinstance(cap_60, CandidateReplayResult)
    assert (cap_60.exit_reason, cap_60.exit_price, cap_60.bars_held) == (
        "time_cap", 100.3, 60,
    )
    assert cap_60.exit_utc_epoch_s == BASE_UTC + 60 * 300
    assert cap_60.candidate_r == pytest.approx(0.15)
    assert cap_60.candidate_mfe_r == pytest.approx(0.3)

    cap_20 = replay_candidate_policy(record, reproduction, "TIME_CAP_20_BARS_V1")
    assert isinstance(cap_20, CandidateReplayResult)
    assert (cap_20.exit_reason, cap_20.exit_price, cap_20.bars_held) == (
        "time_cap", 100.3, 20,
    )
    assert cap_20.exit_utc_epoch_s == BASE_UTC + 20 * 300

    # Cap 180 exceeds the baseline timeout: effective cap = 96 and the
    # canonical reason remains "timeout" (never time_cap).
    cap_180 = replay_candidate_policy(record, reproduction, "TIME_CAP_180_BARS_V1")
    assert isinstance(cap_180, CandidateReplayResult)
    assert (cap_180.exit_reason, cap_180.exit_price, cap_180.bars_held) == (
        "timeout", 100.3, 96,
    )
    assert cap_180.exit_utc_epoch_s == baseline.exit_utc_epoch_s


def test_time_cap_twenty_identity_cap_preserves_scalp_baseline_timeout():
    record, reproduction = _pair_record(_repeat(FLAT, 9))
    row = replay_candidate_policy(record, reproduction, "TIME_CAP_20_BARS_V1")
    assert isinstance(row, CandidateReplayResult)
    # min(20, SCALP timeout 9) = 9: the reason stays the baseline "timeout".
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("timeout", 100.3, 9)
    assert row.exit_reason != "time_cap"
    baseline = replay_shadow_baseline_v1(record).replay
    assert row.exit_price == baseline.exit_price


def test_time_cap_original_stop_precedes_cap_exit_on_cap_bar():
    bars = _repeat(FLAT, 59) + ((100.0, 100.6, 97.8, 99.5),)
    record, reproduction = _pair_record(
        bars, horizon="INTRADAY", timeout_bars=96,
    )
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.exit_price, baseline.bars_held) == (
        "stop_loss", 98.0, 60,
    )
    # Cap bar 60 coincides with the SL touch: cap-bar order is SL first,
    # so the candidate reason is stop_loss - never time_cap.
    row = replay_candidate_policy(record, reproduction, "TIME_CAP_60_BARS_V1")
    assert isinstance(row, CandidateReplayResult)
    assert (row.exit_reason, row.exit_price, row.bars_held) == ("stop_loss", 98.0, 60)
    assert row.exit_reason != "time_cap"
    assert row.exit_utc_epoch_s == baseline.exit_utc_epoch_s
    assert row.candidate_r == pytest.approx(-1.0)


def test_time_cap_original_take_profit_precedes_cap_exit_on_cap_bar():
    bars = _repeat(FLAT, 59) + ((100.0, 104.5, 99.4, 104.2),)
    record, reproduction = _pair_record(
        bars, horizon="INTRADAY", timeout_bars=96,
    )
    baseline = replay_shadow_baseline_v1(record).replay
    assert (baseline.exit_reason, baseline.exit_price, baseline.bars_held) == (
        "take_profit", 104.0, 60,
    )
    row = replay_candidate_policy(record, reproduction, "TIME_CAP_60_BARS_V1")
    assert isinstance(row, CandidateReplayResult)
    # Cap-bar order: SL, then original TP, then the cap close.
    assert (row.exit_reason, row.exit_price, row.bars_held) == (
        "take_profit", 104.0, 60,
    )
    assert row.exit_reason != "time_cap"
    assert row.candidate_r == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("policy", "expected_reason", "expected_label"),
    [
        (
            {"policy_id": "TRAIL_ACT_0_25R_DIST_0_10R_V1",
             "policy_type": "TRAILING", "activation_r": 0.30, "distance_r": 0.10},
            POLICY_PARAMETERS_MISMATCH, "TRAIL_ACT_0_25R_DIST_0_10R_V1",
        ),
        (
            {"policy_id": "TRAIL_ACT_0_25R_DIST_0_10R_V1",
             "policy_type": "TRAILING", "activation_r": 0.25, "distance_r": 0.10,
             "lookback": 3},
            POLICY_PARAMETERS_MISMATCH, "TRAIL_ACT_0_25R_DIST_0_10R_V1",
        ),
        (
            {"policy_id": "REDUCED_TP_1_00R_V1",
             "policy_type": "REDUCED_TP", "target_cap_r": 0.75},
            POLICY_PARAMETERS_MISMATCH, "REDUCED_TP_1_00R_V1",
        ),
        (
            {"policy_id": "TIME_CAP_60_BARS_V1",
             "policy_type": "TIME_CAP", "bar_cap": 61},
            POLICY_PARAMETERS_MISMATCH, "TIME_CAP_60_BARS_V1",
        ),
        (
            {"policy_id": "TRAIL_ACT_1_00R_DIST_0_50R_V1",
             "policy_type": "TRAILING", "activation_r": True, "distance_r": 0.50},
            POLICY_PARAMETERS_MISMATCH, "TRAIL_ACT_1_00R_DIST_0_50R_V1",
        ),
        (
            {"policy_id": "TIME_CAP_12_BARS_V1",
             "policy_type": "TIME_CAP", "bar_cap": 12},
            POLICY_IDENTITY_INVALID, "TIME_CAP_12_BARS_V1",
        ),
        ("TRAIL_ACT_0_20R_DIST_0_10R_V1", POLICY_IDENTITY_INVALID,
         "TRAIL_ACT_0_20R_DIST_0_10R_V1"),
        (42, POLICY_IDENTITY_INVALID, None),
        (None, POLICY_IDENTITY_INVALID, None),
    ],
)
def test_policy_vocabulary_and_parameter_mutations_fail_closed(
    policy, expected_reason, expected_label,
):
    record, reproduction = _one()
    outcome = replay_candidate_policy(record, reproduction, policy)
    assert isinstance(outcome, CandidateReplayExclusion)
    assert outcome.reason == expected_reason
    assert outcome.candidate_policy_id == expected_label
    again = replay_candidate_policy(record, reproduction, policy)
    assert outcome == again  # stable reason AND stable exclusion digest


def test_baseline_reproduction_is_mandatory_for_every_candidate_policy():
    record, reproduction = _one()
    for policy in CANDIDATE_POLICY_IDS:
        outcome = replay_candidate_policy(record, None, policy)
        assert isinstance(outcome, CandidateReplayExclusion)
        assert outcome.reason == BASELINE_REPRODUCTION_REQUIRED
    wrong_type = replay_candidate_policy(record, object(), CANDIDATE_POLICY_IDS[0])
    assert isinstance(wrong_type, CandidateReplayExclusion)
    assert wrong_type.reason == BASELINE_REPRODUCTION_REQUIRED


def test_record_geometry_and_ordered_path_failures_fail_closed():
    record, reproduction = _one()
    zero_entry = replay_candidate_policy(
        _replace_record(record, entry_price=0.0), None, CANDIDATE_POLICY_IDS[0],
    )
    assert isinstance(zero_entry, CandidateReplayExclusion)
    assert zero_entry.reason == GEOMETRY_INVALID

    inverted = replay_candidate_policy(
        _replace_record(record, baseline_stop_loss=106.0), None, CANDIDATE_POLICY_IDS[0],
    )
    assert isinstance(inverted, CandidateReplayExclusion)
    assert inverted.reason == GEOMETRY_INVALID

    bars = record.ordered_m5_bars
    impossible = (replace(bars[0], low=bars[0].high + 1.0), *bars[1:])
    bad_ohlc = replay_candidate_policy(
        _replace_record(record, ordered_m5_bars=impossible), None, CANDIDATE_POLICY_IDS[0],
    )
    assert isinstance(bad_ohlc, CandidateReplayExclusion)
    assert bad_ohlc.reason == GEOMETRY_INVALID

    empty = replay_candidate_policy(
        _replace_record(record, ordered_m5_bars=()), None, CANDIDATE_POLICY_IDS[0],
    )
    assert isinstance(empty, CandidateReplayExclusion)
    assert empty.reason == PATH_INSUFFICIENT

    disorder = (
        bars[0],
        replace(bars[1], timestamp_utc_ms=bars[0].timestamp_utc_ms),
        *bars[2:],
    )
    bad_order = replay_candidate_policy(
        _replace_record(record, ordered_m5_bars=disorder), None, CANDIDATE_POLICY_IDS[0],
    )
    assert isinstance(bad_order, CandidateReplayExclusion)
    assert bad_order.reason == PATH_INSUFFICIENT


def test_lifecycle_identity_conflicts_fail_closed():
    record, reproduction = _one()
    policy = CANDIDATE_POLICY_IDS[0]

    self_conflict = replay_candidate_policy(
        _replace_record(record, canonical_opportunity_id="TAMPERED_OPPORTUNITY"),
        reproduction, policy,
    )
    assert isinstance(self_conflict, CandidateReplayExclusion)
    assert self_conflict.reason == IDENTITY_CONFLICT

    symbol_conflict = replay_candidate_policy(
        record, replace(reproduction, canonical_symbol="GBPUSD"), policy,
    )
    assert isinstance(symbol_conflict, CandidateReplayExclusion)
    assert symbol_conflict.reason == IDENTITY_CONFLICT

    digest_conflict = replay_candidate_policy(
        record, replace(reproduction, path_analytical_digest="0" * 64), policy,
    )
    assert isinstance(digest_conflict, CandidateReplayExclusion)
    assert digest_conflict.reason == IDENTITY_CONFLICT

    replay_binding_conflict = replay_candidate_policy(
        record,
        replace(
            reproduction,
            replay=replace(reproduction.replay, source_path_analytical_digest="0" * 64),
        ),
        policy,
    )
    assert isinstance(replay_binding_conflict, CandidateReplayExclusion)
    assert replay_binding_conflict.reason == IDENTITY_CONFLICT


def test_reproduction_digest_tampering_fails_closed_as_provenance_failure():
    record, reproduction = _one()
    policy = CANDIDATE_POLICY_IDS[0]

    outer = replay_candidate_policy(
        record, replace(reproduction, reproduction_digest="0" * 64), policy,
    )
    assert isinstance(outer, CandidateReplayExclusion)
    assert outer.reason == PROVENANCE_FAILURE

    inner = replay_candidate_policy(
        record,
        replace(
            reproduction,
            replay=replace(reproduction.replay, replay_digest="0" * 64),
        ),
        policy,
    )
    assert isinstance(inner, CandidateReplayExclusion)
    assert inner.reason == PROVENANCE_FAILURE


def test_non_finite_candidate_outputs_fail_closed(monkeypatch):
    record, reproduction = _one()
    fake = candidate_replay._Outcome(
        exit_reason="stop_loss", exit_price=math.inf, bars_held=1,
        exit_utc_epoch_s=BASE_UTC, candidate_r=math.nan, candidate_mfe_r=0.0,
    )
    monkeypatch.setattr(
        candidate_replay, "_evaluate_outcome",
        lambda record_arg, policy_arg: (fake, ()),
    )
    outcome = replay_candidate_policy(record, reproduction, CANDIDATE_POLICY_IDS[0])
    assert isinstance(outcome, CandidateReplayExclusion)
    assert outcome.reason == NON_FINITE_RESULT


def test_no_exit_within_reproduced_path_fails_closed(monkeypatch):
    record, reproduction = _one()
    diagnostics = (candidate_replay._diagnostic("replay_exit", None, None, "forced"),)
    monkeypatch.setattr(
        candidate_replay, "_evaluate_outcome",
        lambda record_arg, policy_arg: (None, diagnostics),
    )
    outcome = replay_candidate_policy(record, reproduction, CANDIDATE_POLICY_IDS[0])
    assert isinstance(outcome, CandidateReplayExclusion)
    assert outcome.reason == PATH_INSUFFICIENT
    assert [item.field for item in outcome.diagnostics] == ["replay_exit"]


def test_population_reports_partial_eligibility_without_forcing_row_maximum():
    good = _reproduced_source(index=0, bars=_repeat(FLAT, 9))
    bad = _reproduced_source(
        index=1, bars=_repeat(FLAT, 9), observed_override={"exit_price": 100.8},
    )
    evidence, reproduction = _paired(good, bad)
    assert len(evidence.records) == 2
    assert len(reproduction.records) == 1  # observed-vs-replay mismatch fails closed
    population = build_candidate_replay_population(evidence, reproduction)
    summary = population.summary
    assert summary.input_path_lifecycles == 2
    assert summary.upstream_reproduction_exclusions == 1
    assert summary.candidate_eligible_lifecycles == 1
    assert summary.candidate_rows == 9
    assert summary.expected_maximum_candidate_rows == 9  # actual, never forced to 18
    assert summary.maximum_policy_count == 9
    assert summary.candidate_exclusions == 9
    assert summary.exclusions_by_reason == {BASELINE_REPRODUCTION_REQUIRED: 9}
    assert set(summary.rows_by_policy.values()) == {1}
    assert summary.distinct_eligible_opportunities == 1
    excluded_identity = (
        "nshadow_baseline_1",
        f"EURUSD*{BASE_UTC + 7_200}*BASELINE_1",
        "SCALP",
    )
    assert {item.lifecycle_identity for item in population.exclusions} == {excluded_identity}
    assert excluded_identity not in {row.lifecycle_identity for row in population.records}
    assert population.provenance["candidate_rows"] == 9
    assert population.provenance["candidate_eligible_lifecycles"] == 1


def test_row_grain_pairing_identity_and_nine_policy_fanout():
    sources = tuple(
        _reproduced_source(index=index, bars=_repeat(FLAT, 9)) for index in range(3)
    )
    evidence, reproduction = _paired(*sources)
    assert len(evidence.records) == 3
    assert len(reproduction.records) == 3
    population = build_candidate_replay_population(evidence, reproduction)
    summary = population.summary
    assert summary.candidate_eligible_lifecycles == 3
    assert summary.candidate_rows == 27
    assert summary.expected_maximum_candidate_rows == 27
    assert summary.candidate_exclusions == 0
    assert summary.rows_by_policy == {pid: 3 for pid in CANDIDATE_POLICY_IDS}
    assert summary.distinct_eligible_opportunities == 3

    pairs = [
        (row.lifecycle_identity, row.candidate_policy_id) for row in population.records
    ]
    assert len(pairs) == len(set(pairs)) == 27
    expected_pairs = {
        (record.lifecycle_identity, policy_id)
        for record in evidence.records for policy_id in CANDIDATE_POLICY_IDS
    }
    assert set(pairs) == expected_pairs

    for row in population.records:
        identity = row.lifecycle_identity
        assert identity == (
            identity[0], row.canonical_opportunity_id, row.trade_horizon,
        )
        assert row.canonical_opportunity_id == identity[1]  # cluster identity
        assert row.candidate_policy_id == row.candidate_policy["policy_id"]
        assert row.candidate_replay_digest == candidate_replay_digest(row)
        assert row.eligibility_state == "ELIGIBLE"
        assert row.hd09_adjudication_version
    for offset in range(0, 27, 9):
        assert [row.candidate_policy_id for row in population.records[offset:offset + 9]] == (
            list(CANDIDATE_POLICY_IDS)
        )
    assert population.provenance["candidate_rows_digest"]
    assert population.provenance["digest"]


def test_account_fanout_duplicates_cannot_create_observations():
    first = _reproduced_source(index=0, bars=_repeat(FLAT, 9))
    second = _reproduced_source(index=1, bars=_repeat(FLAT, 9))
    evidence = build_exit_bar_path_v1((first, deepcopy(first), second))
    # Both copies of the duplicated identity fail closed at the path layer.
    assert len(evidence.records) == 1
    assert len(evidence.exclusions) == 2
    assert evidence.summary.exclusions_by_reason == {"DUPLICATE_LIFECYCLE_IDENTITY": 2}
    reproduction = build_baseline_reproduction_population(evidence)
    population = build_candidate_replay_population(evidence, reproduction)
    summary = population.summary
    assert summary.input_path_lifecycles == 1
    assert summary.upstream_path_exclusions == 2
    assert summary.candidate_eligible_lifecycles == 1
    assert summary.candidate_rows == 9  # fanout can never inflate the grain
    pairs = {
        (row.lifecycle_identity, row.candidate_policy_id) for row in population.records
    }
    assert len(pairs) == 9
    assert {row.lifecycle_identity[0] for row in population.records} == {
        "nshadow_baseline_1",
    }


def test_duplicate_pairing_identity_fails_closed_at_candidate_grain():
    sources = tuple(
        _reproduced_source(index=index, bars=_repeat(FLAT, 9)) for index in range(2)
    )
    evidence, reproduction = _paired(*sources)
    assert len(evidence.records) == len(reproduction.records) == 2
    duplicated_identity = reproduction.records[0].lifecycle_identity
    duplicated = _clone_reproduction(reproduction, (
        reproduction.records[0], reproduction.records[0], reproduction.records[1],
    ))
    population = build_candidate_replay_population(evidence, duplicated)
    assert population.summary.candidate_rows == 9
    assert population.summary.candidate_eligible_lifecycles == 1
    assert all(item.reason == IDENTITY_CONFLICT for item in population.exclusions)
    assert {item.lifecycle_identity for item in population.exclusions} == {
        duplicated_identity
    }  # nine grain exclusions, no ambiguous rows for the duplicated identity
    assert len(population.exclusions) == 9
    assert duplicated_identity not in {
        row.lifecycle_identity for row in population.records
    }


def test_population_build_is_deterministic_across_runs():
    sources = tuple(
        _reproduced_source(index=index, bars=_repeat(FLAT, 9)) for index in range(2)
    )
    evidence, reproduction = _paired(*sources)
    first = build_candidate_replay_population(evidence, reproduction)
    second = build_candidate_replay_population(evidence, reproduction)
    assert first == second
    assert first.provenance["digest"] == second.provenance["digest"]


def test_build_does_not_mutate_sources_or_populations():
    sources = tuple(
        _reproduced_source(index=index, bars=_repeat(FLAT, 9)) for index in range(2)
    )
    sources_before = deepcopy(sources)
    evidence, reproduction = _paired(*sources)
    assert sources == sources_before
    evidence_before = deepcopy(evidence)
    reproduction_before = deepcopy(reproduction)
    build_candidate_replay_population(evidence, reproduction)
    build_candidate_replay_population(evidence, reproduction)
    assert evidence == evidence_before
    assert reproduction == reproduction_before


def test_reordered_inputs_produce_identical_population_and_digests():
    sources = tuple(
        _reproduced_source(index=index, bars=_repeat(FLAT, 9)) for index in range(3)
    )
    evidence, reproduction = _paired(*sources)
    forward = build_candidate_replay_population(evidence, reproduction)
    reversed_evidence = replace(evidence, records=tuple(reversed(evidence.records)))
    reversed_reproduction = replace(
        reproduction, records=tuple(reversed(reproduction.records)),
    )
    backward = build_candidate_replay_population(reversed_evidence, reversed_reproduction)
    assert forward == backward
    assert forward.provenance["digest"] == backward.provenance["digest"]
    assert [row.analytical_record() for row in forward.records] == [
        row.analytical_record() for row in backward.records
    ]
    assert [item.record() for item in forward.exclusions] == [
        item.record() for item in backward.exclusions
    ]


def test_row_digest_is_sensitive_to_policy_baseline_and_path_ohlc():
    record, reproduction = _one()
    by_policy = [
        replay_candidate_policy(record, reproduction, policy_id)
        for policy_id in (
            "TRAIL_ACT_0_25R_DIST_0_10R_V1", "TRAIL_ACT_0_50R_DIST_0_25R_V1",
        )
    ]
    assert all(isinstance(item, CandidateReplayResult) for item in by_policy)
    # Frozen policy identity/parameters bind the digest.
    assert by_policy[0].candidate_replay_digest != by_policy[1].candidate_replay_digest
    # Baseline reproduction identity binds the digest (recompute hook).
    assert candidate_replay_digest(
        replace(by_policy[0], baseline_replay_digest="0" * 64),
    ) != by_policy[0].candidate_replay_digest
    assert candidate_replay_digest(
        replace(by_policy[0], baseline_reproduction_digest="0" * 64),
    ) != by_policy[0].candidate_replay_digest
    assert candidate_replay_digest(
        replace(by_policy[0], source_path_analytical_digest="0" * 64),
    ) != by_policy[0].candidate_replay_digest
    assert candidate_replay_digest(
        replace(by_policy[0], candidate_policy=dict(CANDIDATE_POLICIES[2])),
    ) != by_policy[0].candidate_replay_digest

    # OHLC sensitivity with NO outcome change: only a mid-path close shifts.
    bars_a = _repeat(FLAT, 9)
    bars_b = list(bars_a)
    open_b, high_b, low_b, _close_b = bars_b[4]
    bars_b[4] = (open_b, high_b, low_b, 100.35)
    population_a = build_candidate_replay_population(
        *_paired(_reproduced_source(index=0, bars=tuple(bars_a))),
    )
    population_b = build_candidate_replay_population(
        *_paired(_reproduced_source(index=0, bars=tuple(bars_b))),
    )
    assert len(population_a.records) == len(population_b.records) == 9
    for row_a, row_b in zip(population_a.records, population_b.records):
        assert row_a.candidate_policy_id == row_b.candidate_policy_id
        assert (
            row_a.exit_reason, row_a.exit_price, row_a.bars_held,
            row_a.candidate_r, row_a.candidate_mfe_r,
        ) == (
            row_b.exit_reason, row_b.exit_price, row_b.bars_held,
            row_b.candidate_r, row_b.candidate_mfe_r,
        )
        # The path digest chain reacts to the OHLC change even without an
        # outcome change, so the row digest must differ.
        assert row_a.source_path_analytical_digest != row_b.source_path_analytical_digest
        assert row_a.candidate_replay_digest != row_b.candidate_replay_digest


def test_path_evidence_provenance_tampering_fails_closed():
    evidence, reproduction = _paired(_reproduced_source(bars=_repeat(FLAT, 9)))
    with pytest.raises(ValueError, match="Missing governed"):
        build_candidate_replay_population(
            replace(evidence, schema_version="bogus"), reproduction,
        )
    with pytest.raises(ValueError, match="analytical provenance failed"):
        build_candidate_replay_population(
            replace(
                evidence,
                provenance={**evidence.provenance, "analytical_digest": "0" * 64},
            ),
            reproduction,
        )
    with pytest.raises(ValueError, match="population provenance failed"):
        build_candidate_replay_population(
            replace(evidence, provenance={**evidence.provenance, "digest": "0" * 64}),
            reproduction,
        )


def test_reproduction_population_provenance_tampering_fails_closed():
    evidence, reproduction = _paired(_reproduced_source(bars=_repeat(FLAT, 9)))
    with pytest.raises(ValueError, match="Missing governed"):
        build_candidate_replay_population(
            evidence, replace(reproduction, schema_version="bogus"),
        )
    with pytest.raises(ValueError, match="analytical provenance failed"):
        build_candidate_replay_population(
            evidence,
            replace(
                reproduction,
                provenance={
                    **reproduction.provenance,
                    "successful_reproduction_digest": "0" * 64,
                },
            ),
        )
    with pytest.raises(ValueError, match="population provenance failed"):
        build_candidate_replay_population(
            evidence,
            replace(
                reproduction,
                provenance={**reproduction.provenance, "digest": "0" * 64},
            ),
        )
    _other_evidence, other_reproduction = _paired(
        _reproduced_source(index=5, bars=_repeat(FLAT, 9)),
    )
    with pytest.raises(ValueError, match="not bound"):
        build_candidate_replay_population(evidence, other_reproduction)
