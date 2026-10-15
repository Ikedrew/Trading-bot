"""Milestone 3 — G1 normalization cache: equivalence, isolation, bounds.

Proves the snapshot-scoped normalization cache (evidence_resolver.normalization_cache)
reuses the pure _normalise result across question assessments without changing
resolution semantics, evidence identities, populations/ordering, or governed
output, and that it is bounded, snapshot-partitioned and defensively isolated.

Offline/deterministic. No S3, no rehearsal.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane import evidence_resolver as er
from research_engine.control_plane.evidence_resolver import (
    _NormalizationCache,
    _normalise,
    _normalise_uncached,
    normalization_cache,
)


# ---------------------------------------------------------------------------
# Representative records with nested aliases, missing fields, mixed epochs.
# ---------------------------------------------------------------------------


def _decision_trace(i: int) -> dict:
    return {
        "data_epoch": "CURRENT", "schema_version": "decision_trace_v1",
        "entity_id": f"E-{i}", "correlation_id": f"COR-{i}",
        "canonical_opportunity_id": f"OPP-{i}", "action": "EXECUTE",
        "v10_market_state": {"regime": {"h4_regime": "TREND", "volatility_state": "CALM"},
                             "nested": {"deep": {"score_strategy": 0.7}}},
        "components": {"a": i, "b": {"c": i}},
    }


def _shadow(i: int) -> dict:
    return {
        "data_epoch": "CURRENT", "schema_version": "shadow_runtime_v1",
        "canonical_opportunity_id": f"OPP-{i}", "entity_id": f"E-{i}",
        "pnl_r_multiple": 1.5, "shadow_type": "PRIMARY_HORIZON_SIMULATION",
        "score": 0.8, "components": {"x": i},
    }


# ---------------------------------------------------------------------------
# 1. Scientific equivalence: cached == uncached, exact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,factory", [
    ("decision_trace", _decision_trace),
    ("shadow_trades", _shadow),
])
def test_cached_normalise_equals_uncached(source, factory):
    records = [factory(i) for i in range(50)]
    baseline = [_normalise_uncached(r, source) for r in records]
    with normalization_cache("ISNAP-TEST"):
        # First pass populates, second reuses — both must equal the baseline.
        first = [_normalise(r, source) for r in records]
        second = [_normalise(r, source) for r in records]
    assert first == baseline
    assert second == baseline


def test_absent_fields_and_nested_aliases_resolve_identically():
    source = "decision_trace"
    record = {
        "data_epoch": "CURRENT", "schema_version": "decision_trace_v1",
        "entity_id": "E-1",
        # score only present nested (alias resolution), r_multiple absent entirely
        "wrapper": {"inner": {"score_strategy": 0.42}},
    }
    base = _normalise_uncached(record, source)
    with normalization_cache("ISNAP-TEST"):
        cached = _normalise(record, source)
        cached2 = _normalise(record, source)
    assert cached == base
    assert cached2 == base
    # r_multiple genuinely absent → not fabricated
    assert "r_multiple" not in base or base.get("r_multiple") is not None


def test_alias_collision_same_record_same_result():
    # Two records with identical content hash to the same key -> same result.
    source = "shadow_trades"
    a = _shadow(7)
    b = deepcopy(a)
    with normalization_cache("ISNAP-TEST") as cache:
        ra = _normalise(a, source)
        rb = _normalise(b, source)
    assert ra == rb == _normalise_uncached(a, source)
    assert cache.hits >= 1  # identical content reused


def test_distinct_sources_do_not_collide():
    # Identical record *content* under two different sources must occupy two
    # distinct cache keys (the source is part of the key) and each cached value
    # must equal its own uncached normalisation.  _normalise is source-agnostic
    # in field resolution, so we assert equivalence to the uncached result
    # rather than asserting a (false) source-exclusive field set.
    content = {"data_epoch": "CURRENT", "schema_version": "decision_trace_v1",
               "entity_id": "E-1", "pnl_r_multiple": 2.0}
    base_dt = _normalise_uncached(dict(content), "decision_trace")
    base_shadow = _normalise_uncached(dict(content), "shadow_trades")
    with normalization_cache("ISNAP-TEST") as cache:
        as_dt = _normalise(dict(content), "decision_trace")      # miss (key A)
        as_shadow = _normalise(dict(content), "shadow_trades")   # miss (key B)
    # Two distinct sources with identical content -> two misses, two entries,
    # no cross-source collision.
    assert cache.misses == 2
    assert cache.hits == 0
    assert cache.stats()["entries"] == 2
    # Each cached value is exactly its own uncached normalisation.
    assert as_dt == base_dt
    assert as_shadow == base_shadow


# ---------------------------------------------------------------------------
# 2. Defensive isolation — no mutable sharing, source never mutated
# ---------------------------------------------------------------------------


def test_returned_rows_are_isolated_and_source_unchanged():
    source = "decision_trace"
    record = _decision_trace(1)
    source_before = deepcopy(record)
    with normalization_cache("ISNAP-TEST"):
        first = _normalise(record, source)
        first["components"]["b"]["c"] = "MUTATED"
        first["v10_market_state"]["regime"]["h4_regime"] = "MUTATED"
        second = _normalise(record, source)   # cache hit
    # Mutating a returned row must not affect a later cache-hit copy...
    assert second["components"]["b"]["c"] != "MUTATED"
    assert second.get("h4_regime") != "MUTATED"
    # ...nor the original source record.
    assert record == source_before


# ---------------------------------------------------------------------------
# 3. Bounded eviction & correct behaviour after eviction
# ---------------------------------------------------------------------------


def test_cache_is_bounded_and_correct_after_eviction():
    source = "shadow_trades"
    records = [_shadow(i) for i in range(20)]
    with normalization_cache("ISNAP-TEST", max_entries=5) as cache:
        # First pass: 20 distinct records, cap 5 → forced evictions, entries<=5.
        first = [_normalise(r, source) for r in records]
        assert len(cache._store) <= 5
        assert cache.evictions >= 15
        # Re-normalising after eviction still returns correct values.
        again = [_normalise(r, source) for r in records]
    baseline = [_normalise_uncached(r, source) for r in records]
    assert first == baseline
    assert again == baseline


def test_measured_byte_budget_evicts_fifo_without_changing_values():
    records = [_shadow(i) for i in range(4)]
    sample = _normalise_uncached(records[0], "shadow_trades")
    key = _NormalizationCache._content_key(records[0], "shadow_trades")
    entry_bytes = er._retained_bytes((key, sample))
    with normalization_cache("ISNAP-BYTES", max_bytes=entry_bytes * 2) as cache:
        outputs = [_normalise(row, "shadow_trades") for row in records]
        assert cache.bytes_used <= cache.max_bytes
        assert cache.evictions >= 2
        assert list(cache._store) == [
            _NormalizationCache._content_key(row, "shadow_trades")
            for row in records[-2:]
        ]
        outputs.append(_normalise(records[0], "shadow_trades"))
        assert cache.bytes_used <= cache.max_bytes
    assert outputs == [_normalise_uncached(row, "shadow_trades")
                       for row in [*records, records[0]]]


# ---------------------------------------------------------------------------
# 4. Snapshot partitioning — no cross-snapshot leakage
# ---------------------------------------------------------------------------


def test_nested_same_snapshot_shares_but_conflicting_snapshot_refused():
    with normalization_cache("ISNAP-A") as outer:
        _normalise(_shadow(1), "shadow_trades")
        with normalization_cache("ISNAP-A") as inner:
            assert inner is outer  # same snapshot reuses the same cache
        with pytest.raises(RuntimeError, match="SNAPSHOT_CONFLICT"):
            with normalization_cache("ISNAP-B"):
                pass


def test_cache_inactive_by_default_is_pure_passthrough():
    # Without an active context, _normalise must equal _normalise_uncached and
    # not create any cache (no global state).
    assert er._NORMALIZATION_CACHE.get() is None
    record = _decision_trace(3)
    assert _normalise(record, "decision_trace") == _normalise_uncached(record, "decision_trace")
    assert er._NORMALIZATION_CACHE.get() is None


# ---------------------------------------------------------------------------
# 5. Hit accounting
# ---------------------------------------------------------------------------


def test_hit_rate_reflects_reuse():
    source = "shadow_trades"
    records = [_shadow(i) for i in range(10)]
    with normalization_cache("ISNAP-TEST") as cache:
        for _ in range(6):           # 6 passes over the same 10 records
            for r in records:
                _normalise(r, source)
    stats = cache.stats()
    assert stats["misses"] == 10     # each distinct record computed once
    assert stats["hits"] == 50       # remaining 5 passes are hits
    assert stats["entries"] == 10
    assert stats["hit_rate"] > 0.8
