"""
Regression + performance tests for the end-to-end runtime performance recovery.

Covers the guarantees the performance repairs must NOT break, and the new
behaviours they introduce:

1.  Cycle-scoped MT5 fetch reuse (one MT5 hit per identical request per cycle,
    nothing reused outside a cycle, nothing surviving a cycle boundary)
2.  New-bar gating (HTF analyzers not re-run when bar identity is unchanged)
3.  Broker alias resolution (NAS100 -> USTEC) is unaffected
4.  Startup warm-up (constructs hot-path singletons once; isolates failures)
5.  Cycle profiler (records stages/notes; never raises)
6.  Fail-closed data access (an MT5 rate failure still raises)
7.  Durable replay-cache watermark (no duplicate emission; disk still appended)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import data.mt5_data as mt5_data
from data.mt5_data import MT5DataFeed, Candle
from core.timeframes.cache import (
    TimeframeCache,
    _TF_H4,
    _TF_H1,
    _TF_M15,
    _TF_D1,
    _TF_D1_BIAS,
    _TF_W1,
    _TF_MN,
)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _rows(times: list[int]) -> list[dict]:
    return [
        {"time": t, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "tick_volume": 10}
        for t in times
    ]


@pytest.fixture(autouse=True)
def _reset_cycle_cache():
    mt5_data.reset_data_cycle_cache_for_tests()
    yield
    mt5_data.reset_data_cycle_cache_for_tests()


def _feed():
    """MT5DataFeed whose broker symbol is pinned (skips MT5 symbol resolution)."""
    feed = MT5DataFeed("EURUSD")
    feed._broker_symbol = "EURUSD"
    return feed


def _counter_provider():
    calls = {"n": 0}

    def _copy_rates(symbol, timeframe, start, count):
        calls["n"] += 1
        return _rows(list(range(1000, 1000 + count)))

    return calls, _copy_rates


# ─── 1. CYCLE-SCOPED FETCH REUSE ──────────────────────────────────────────────

class TestCycleScopedFetchReuse:
    def test_identical_fetch_within_cycle_is_reused(self):
        feed = _feed()
        calls, copy_rates = _counter_provider()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache") as persist:
            mt5_data.begin_data_cycle()
            first = feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            second = feed.copy_rates_closed("EURUSD", _TF_D1, 2)
        assert calls["n"] == 1, "identical fetch must hit MT5 once per cycle"
        assert [c.time for c in first] == [c.time for c in second]
        assert persist.call_count == 1, "persistence must run for the real fetch only"

    def test_no_reuse_outside_an_open_cycle(self):
        feed = _feed()
        calls, copy_rates = _counter_provider()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            # No begin_data_cycle() -> cache inert (replay/tests keep fresh reads)
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
        assert calls["n"] == 2

    def test_nothing_survives_a_cycle_boundary(self):
        feed = _feed()
        seq = {"i": 0}

        def _copy_rates(symbol, timeframe, start, count):
            seq["i"] += 1
            base = 1000 if seq["i"] == 1 else 5000
            return _rows([base, base + 300])

        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=_copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            mt5_data.begin_data_cycle()
            first = feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            mt5_data.begin_data_cycle()  # new cycle -> cache cleared
            second = feed.copy_rates_closed("EURUSD", _TF_D1, 2)
        assert first[0].time == 1000
        assert second[0].time == 5000, "stale bar data must not cross a cycle boundary"

    def test_distinct_timeframes_are_not_conflated(self):
        feed = _feed()
        calls = {"n": 0}

        def _copy_rates(symbol, timeframe, start, count):
            calls["n"] += 1
            return _rows(list(range(timeframe, timeframe + count)))

        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=_copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            mt5_data.begin_data_cycle()
            h4 = feed.copy_rates_closed("EURUSD", _TF_H4, 2)
            h1 = feed.copy_rates_closed("EURUSD", _TF_H1, 2)
        assert calls["n"] == 2
        assert h4[0].time != h1[0].time

    def test_distinct_counts_are_not_conflated(self):
        feed = _feed()
        calls, copy_rates = _counter_provider()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            mt5_data.begin_data_cycle()
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            feed.copy_rates_closed("EURUSD", _TF_D1, 100)
        assert calls["n"] == 2

    def test_cycle_fetch_stats_report_hits(self):
        feed = _feed()
        calls, copy_rates = _counter_provider()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            mt5_data.begin_data_cycle()
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            stats = mt5_data.cycle_fetch_stats()
        assert stats == {"hits": 1, "misses": 1}

    def test_end_data_cycle_drops_cache(self):
        feed = _feed()
        calls, copy_rates = _counter_provider()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", side_effect=copy_rates), \
             patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
             patch.object(mt5_data, "_persist_candles_to_cache"):
            mt5_data.begin_data_cycle()
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            mt5_data.end_data_cycle()
            feed.copy_rates_closed("EURUSD", _TF_D1, 2)
        assert calls["n"] == 2


# ─── 2. NEW-BAR GATING (HTF analyzers) ────────────────────────────────────────

def _tf_candles(tf_seconds: int, count: int, end_time: int) -> list[Candle]:
    times = [end_time - tf_seconds * (count - 1 - i) for i in range(count)]
    return [
        Candle(time=t, open=1.0, high=1.1, low=0.9, close=1.05, tick_volume=5)
        for t in times
    ]


class TestNewBarGating:
    def test_analyzers_not_rerun_when_bar_identity_unchanged(self):
        from core import config as real_config

        now = 1_700_000_000
        tfs = {_TF_H4: 14400, _TF_H1: 3600, _TF_M15: 900, _TF_D1: 86400,
               _TF_W1: 604800, _TF_MN: 2592000}
        candles_map = {tf: _tf_candles(sec, 50, now - sec) for tf, sec in tfs.items()}
        candles_map[_TF_D1_BIAS] = candles_map[_TF_D1]

        feed = MagicMock()
        feed.copy_rates_closed = MagicMock(
            side_effect=lambda symbol, tf, count: list(candles_map.get(tf, []))[-count:]
        )

        cache = TimeframeCache(symbol="EURUSD", feed=feed, config=real_config)
        with patch.object(TimeframeCache, "_run_analyzer", autospec=True,
                          side_effect=lambda self, tf, c: MagicMock()) as spy:
            cache.update_if_needed(current_time_s=float(now), current_price=1.05)
            first = spy.call_count
            assert first > 0, "cold cache must run analyzers once"
            cache.update_if_needed(current_time_s=float(now), current_price=1.05)
        assert spy.call_count == first, "unchanged bar identity must not re-run analyzers"

    def test_new_closed_bar_reruns_and_replaces_snapshot(self):
        from core import config as real_config

        now = 1_700_000_000
        h4 = _tf_candles(14400, 50, now - 14400)
        feed = MagicMock()
        feed.copy_rates_closed = MagicMock(return_value=list(h4))

        cache = TimeframeCache(symbol="EURUSD", feed=feed, config=real_config)
        with patch.object(TimeframeCache, "_run_analyzer", autospec=True,
                          side_effect=lambda self, tf, c: MagicMock()):
            cache.update_if_needed(current_time_s=float(now), current_price=1.05)
            before = cache.get_entry(_TF_H4).bar_time
            h4_new = h4 + [Candle(time=now, open=1.0, high=1.1, low=0.9,
                                  close=1.05, tick_volume=5)]
            feed.copy_rates_closed = MagicMock(return_value=list(h4_new))
            cache.update_if_needed(current_time_s=float(now + 14400), current_price=1.06)
            after = cache.get_entry(_TF_H4).bar_time
        assert after > before, "a new closed bar must advance the snapshot watermark"



# ─── 3. BROKER ALIAS RESOLUTION ───────────────────────────────────────────────

def _fake_symbols(names):
    out = []
    for n in names:
        m = MagicMock()
        m.name = n
        out.append(m)
    return out


class TestAliasResolution:
    def test_nas100_resolves_to_ustec_via_alias(self):
        import core.symbol_resolver as sr

        sr.clear_resolved_symbols()
        with patch.object(sr.mt5, "symbols_get",
                          return_value=_fake_symbols(["EURUSD", "USTEC", "US500"])), \
             patch.object(sr.mt5, "symbol_select", return_value=True):
            resolved = sr.resolve_broker_symbol("NAS100")
        assert resolved == "USTEC"
        assert sr.broker_symbol_for("NAS100") == "USTEC"

    def test_exact_symbol_still_resolves_without_alias(self):
        import core.symbol_resolver as sr

        sr.clear_resolved_symbols()
        with patch.object(sr.mt5, "symbols_get",
                          return_value=_fake_symbols(["EURUSD", "GBPUSD"])), \
             patch.object(sr.mt5, "symbol_select", return_value=True):
            assert sr.resolve_broker_symbol("EURUSD") == "EURUSD"


# ─── 4. STARTUP WARM-UP ───────────────────────────────────────────────────────

class TestStartupWarmup:
    def test_constructs_every_dependency_once(self):
        from core.runtime.startup_warmup import warm_runtime_dependencies

        calls = {"a": 0, "b": 0}
        deps = [
            ("a", lambda: calls.__setitem__("a", calls["a"] + 1)),
            ("b", lambda: calls.__setitem__("b", calls["b"] + 1)),
        ]
        timings = warm_runtime_dependencies(deps)
        assert calls == {"a": 1, "b": 1}
        assert set(timings) == {"a", "b"}
        assert all(v >= 0 for v in timings.values())

    def test_isolates_failures_instead_of_aborting_startup(self):
        from core.runtime.startup_warmup import warm_runtime_dependencies

        def _boom():
            raise RuntimeError("outbox corrupt")

        ok = {"n": 0}
        deps = [("bad", _boom), ("good", lambda: ok.__setitem__("n", ok["n"] + 1))]
        timings = warm_runtime_dependencies(deps)
        assert ok["n"] == 1, "a failing dependency must not stop later ones"
        assert timings["bad"] <= 0, "failures are reported as non-positive durations"

    def test_registry_targets_hot_path_singletons(self):
        from core.runtime.startup_warmup import WARMUP_DEPENDENCIES

        labels = {label for label, _ in WARMUP_DEPENDENCIES}
        assert {"canonical_delivery_outbox", "lifecycle_evidence_ledger",
                "shadow_runtime"}.issubset(labels)



# ─── 5. CYCLE PROFILER ────────────────────────────────────────────────────────

class TestCycleProfiler:
    def test_records_stages_and_notes(self):
        from core.runtime.cycle_profiler import CycleProfiler

        prof = CycleProfiler(1, enabled=False)
        with prof.stage("alpha"):
            pass
        prof.add("beta", 0.25)
        prof.note("k", "v")
        ranked = {n: s for n, s, _ in prof.ranked()}
        assert "alpha" in ranked and ranked["beta"] == pytest.approx(0.25)
        report = prof.report(total_override=1.0)
        # `alpha` is sub-millisecond/single-call so it is intentionally omitted
        # from the ranked report; the recorded bucket and note must still appear.
        assert "beta" in report and "k=v" in report

    def test_symbol_attribution_and_emit_never_raise(self):
        from core.runtime.cycle_profiler import CycleProfiler

        prof = CycleProfiler(2, enabled=False)
        prof.symbol("EURUSD", 0.5)
        prof.symbol("GBPUSD", 0.1)
        assert "EURUSD" in prof.report()
        prof.emit(force=False)  # disabled -> no output, no exception
        prof.emit(force=True)   # forced -> still no exception

    def test_nested_stages_are_supported(self):
        from core.runtime.cycle_profiler import CycleProfiler

        prof = CycleProfiler(3, enabled=False)
        with prof.stage("outer"):
            with prof.stage("inner"):
                pass
        names = {n for n, _, _ in prof.ranked()}
        assert {"outer", "inner"}.issubset(names)


# ─── 6. FAIL-CLOSED DATA ACCESS ───────────────────────────────────────────────

class TestFailClosedDataAccess:
    def test_rate_failure_raises_and_caches_nothing(self):
        feed = _feed()
        with patch.object(mt5_data.mt5, "copy_rates_from_pos", return_value=None), \
             patch.object(mt5_data.mt5, "last_error", return_value=(-1, "boom")):
            mt5_data.begin_data_cycle()
            with pytest.raises(Exception):
                feed.copy_rates_closed("EURUSD", _TF_D1, 2)
            # Nothing cached -> a later retry still attempts the broker.
            with patch.object(mt5_data.mt5, "copy_rates_from_pos",
                              return_value=_rows([1000, 1300])), \
                 patch.object(mt5_data, "_ensure_broker_utc_offset", return_value=0), \
                 patch.object(mt5_data, "_persist_candles_to_cache"):
                out = feed.copy_rates_closed("EURUSD", _TF_D1, 2)
        assert len(out) == 2


# ─── 7. DURABLE REPLAY-CACHE WATERMARK ────────────────────────────────────────

class TestReplayWatermark:
    def test_in_memory_watermark_prevents_duplicate_emission(self, tmp_path):
        from core import config as real_config

        mt5_data.reset_candle_dedup_for_tests()
        candles = [
            Candle(time=1000, open=1.0, high=1.1, low=0.9, close=1.05, tick_volume=1),
            Candle(time=1300, open=1.0, high=1.1, low=0.9, close=1.05, tick_volume=1),
            Candle(time=1600, open=1.0, high=1.1, low=0.9, close=1.05, tick_volume=1),
        ]
        with patch.object(real_config, "ENABLE_CANDLE_REPLAY_CACHE", True), \
             patch.object(real_config, "REPLAY_CACHE_DIR", str(tmp_path)):
            mt5_data._persist_candles_to_cache("EURUSD", 5, candles)
            path = tmp_path / "EURUSD" / "5" / "dedup.jsonl"
            first_lines = path.read_text(encoding="utf-8").strip().splitlines()
            # Same window again -> nothing new appended (in-memory watermark)
            mt5_data._persist_candles_to_cache("EURUSD", 5, candles)
            second_lines = path.read_text(encoding="utf-8").strip().splitlines()
        mt5_data.reset_candle_dedup_for_tests()
        assert len(first_lines) == len(second_lines) >= 1

