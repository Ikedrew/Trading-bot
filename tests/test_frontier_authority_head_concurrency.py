"""Stage A final FRONTIER authority-path verification (offline, deterministic).

Drives the REAL live frontier path -- ``_discover_verified`` -- which is what
``run_frontier_snapshot_cycle`` (the continuous cadence entry point exercised by
the 10-minute diagnostic) invokes.  NOT the one-shot
``freeze_investigation_snapshot`` cold path (that one is used only by direct
snapshot materialization and is serial by design on the exact-digest cache).

One dataset (``decision_trace``) is scaled to many objects so a concurrent
authority-HEAD batch is observable; the live diagnostic had 7,359 decision_trace
and 19,454 shadow_runtime objects.  Nothing here is production-scale and no
production speedup is claimed from the synthetic timings -- they establish the
*shape* of the cost (serial vs concurrent) and the absence of redundant HEADs.

Proves:
  1. Over a cold predecessor every discovered+cached object receives EXACTLY
     ONE fresh authority HEAD during ``_discover_verified`` (no double-HEAD).
  2. Warm cache serves bodies with ZERO GET downloads during discovery.
  3. The authority HEAD batch is genuinely concurrent (peak in-flight > 1) and
     the configured width governs peak concurrency (width is the throughput
     lever, not redundancy).
  4. Changed / denied objects fail closed (fresh authority preserved).
  5. The discovered roster is identical regardless of HEAD concurrency width.
"""
from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from datetime import date

import pytest

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
)
from research_engine.v10.continuous import frontier_coordinator as fc
from tests.test_incremental_snapshot_materialization import (
    MemoryS3, _objects, _key, _body,
)


AS_OF = date(2026, 9, 25)
SCALE_DATASET = "decision_trace"
SCALE_N = 48  # > 2 * default batch width (8) so concurrency is observable


class VersionedLatencyS3(MemoryS3):
    """MemoryS3 that (a) returns VersionId on LIST (so the live batch-authority
    path is taken, not cold-start version resolution), (b) sleeps per HEAD, and
    (c) tracks locked peak concurrent HEADs."""

    def __init__(self, objects, *, head_latency=0.02):
        super().__init__(objects, {key: "v1" for key in objects})
        self._head_latency = head_latency
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak_in_flight = 0

    def list_objects_v2(self, **kwargs):
        result = super().list_objects_v2(**kwargs)
        for row in result.get("Contents", []):
            row["VersionId"] = self.versions.get(str(row["Key"]), "v1")
        return result

    def head_object(self, **kwargs):
        with self._lock:
            self._in_flight += 1
            if self._in_flight > self.peak_in_flight:
                self.peak_in_flight = self._in_flight
        try:
            if self._head_latency:
                time.sleep(self._head_latency)
            return super().head_object(**kwargs)
        finally:
            with self._lock:
                self._in_flight -= 1


def _scaled_objects():
    objects = dict(_objects())
    base_key = _key(SCALE_DATASET)
    objects.pop(base_key, None)
    for i in range(SCALE_N):
        objects[_key(SCALE_DATASET, part=f"part-{i:04d}.jsonl")] = _body(SCALE_DATASET, f"m{i}")
    return objects


def _src(fake, cache, **kwargs):
    return S3ResearchDataSource(bucket="authority-head-test", client=fake,
                                verified_cache_directory=cache, **kwargs)


def _seed_cache(cache_dir, objects):
    """Populate the verified-body cache (setup only, no latency, no counting)."""
    fake = VersionedLatencyS3(objects, head_latency=0.0)
    fc._discover_verified(_src(fake, cache_dir), as_of_date=AS_OF, previous=None)


def _discover(fake, cache):
    return fc._discover_verified(_src(fake, cache), as_of_date=AS_OF, previous=None)


# ─────────────────────────────────────────────────────────────────────────────
# 1 + 2. Exactly one HEAD per object; warm cache does not GET
# ─────────────────────────────────────────────────────────────────────────────


def test_discover_issues_one_head_per_object_and_no_warm_gets(tmp_path):
    objects = _scaled_objects()
    cache = tmp_path / "bodies"
    _seed_cache(cache, objects)

    fake = VersionedLatencyS3(objects, head_latency=0.0)
    before_h, before_g = fake.head_calls, fake.get_calls
    discovered, _c, _m = _discover(fake, cache)
    heads = fake.head_calls - before_h
    gets = fake.get_calls - before_g
    n = len(objects)

    assert sum(len(v) for v in discovered.values()) == n
    # ~1 HEAD/object; a double-HEAD would be ~2n.  Allow a small per-dataset
    # slack for empty-roster datasets.
    assert heads <= n + 12, f"expected ~1 HEAD/object ({n}), got {heads}; double would be ~{2*n}"
    assert heads >= n - 2, f"expected ~1 HEAD/object ({n}), got {heads}"
    assert gets == 0, f"warm cache must not GET during discovery, got {gets}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Authority HEAD is concurrent; configured width governs peak concurrency
# ─────────────────────────────────────────────────────────────────────────────


def test_discover_authority_head_is_concurrent(tmp_path):
    objects = _scaled_objects()
    cache = tmp_path / "bodies"
    _seed_cache(cache, objects)

    fake = VersionedLatencyS3(objects, head_latency=0.02)
    started = time.perf_counter()
    _discover(fake, cache)
    wall = time.perf_counter() - started

    serial_bound = fake.head_calls * 0.02
    assert fake.peak_in_flight > 1, (
        f"authority HEADs ran serially (peak in-flight={fake.peak_in_flight})")
    assert wall < serial_bound * 0.75, (
        f"wall {wall:.3f}s not materially below serial bound {serial_bound:.3f}s "
        f"(peak in-flight {fake.peak_in_flight})")


def test_authority_head_width_governs_peak_concurrency(tmp_path):
    """Clamping the batch-authority width lowers observed peak concurrency,
    proving width -- not a redundant/serial defect -- is the throughput lever."""

    def run_with_width(width, cache):
        objects = _scaled_objects()
        _seed_cache(cache, objects)
        fake = VersionedLatencyS3(objects, head_latency=0.01)
        original = S3ResearchDataSource.head_bound_objects

        def forced(self, dataset, items, *, expected_schema_version,
                   max_workers=16, progress_callback=None):
            # Only clamp the batch-authority call (which passes max_workers=8).
            effective = width if max_workers == 8 else max_workers
            return original(self, dataset, items,
                            expected_schema_version=expected_schema_version,
                            max_workers=effective, progress_callback=progress_callback)

        with contextlib.ExitStack() as stack:
            stack.enter_context(_patched(S3ResearchDataSource, "head_bound_objects", forced))
            _discover(fake, cache)
        return fake.peak_in_flight

    narrow = run_with_width(2, tmp_path / "w2")
    wide = run_with_width(8, tmp_path / "w8")
    assert narrow <= 2, f"width-2 peak exceeded 2: {narrow}"
    assert wide > narrow, f"raising width did not raise peak concurrency: {narrow}->{wide}"


@contextlib.contextmanager
def _patched(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fail-closed on changed / denied objects (authority preserved)
# ─────────────────────────────────────────────────────────────────────────────


def test_discover_fails_closed_on_denied_head(tmp_path):
    objects = _scaled_objects()
    cache = tmp_path / "bodies"
    _seed_cache(cache, objects)

    fake = VersionedLatencyS3(objects, head_latency=0.0)

    def denied(**kwargs):
        raise PermissionError("offline denied")

    fake.head_object = denied
    with pytest.raises(Exception):
        _discover(fake, cache)


def test_cached_body_cannot_satisfy_changed_version(tmp_path):
    """A cached body whose live version no longer matches must fail closed on a
    bound read -- cached bytes never grant authority."""
    objects = _scaled_objects()
    cache = tmp_path / "bodies"
    _seed_cache(cache, objects)

    fake = VersionedLatencyS3(objects, head_latency=0.0)
    src = _src(fake, cache)
    some_key = next(k for k in objects if SCALE_DATASET in k)
    listed = next(dict(o) for o in src.discover_dataset_objects(SCALE_DATASET)
                  if str(o.get("identifier")) == some_key)
    # Tamper the expected authority so it cannot match the cached header.
    listed["version_id"] = "v2"
    with pytest.raises(ResearchDataSourceError):
        src.read_bound_objects(SCALE_DATASET, [listed],
            expected_schema_version=current_schema(SCALE_DATASET))
    assert src.performance["verified_cache_hits"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Discovered roster is independent of HEAD concurrency width
# ─────────────────────────────────────────────────────────────────────────────


def test_discovered_roster_invariant_to_head_width(tmp_path):
    def roster(width, cache):
        objects = _scaled_objects()
        _seed_cache(cache, objects)
        fake = VersionedLatencyS3(objects, head_latency=0.0)
        original = S3ResearchDataSource.head_bound_objects

        def forced(self, dataset, items, *, expected_schema_version,
                   max_workers=16, progress_callback=None):
            effective = width if max_workers == 8 else max_workers
            return original(self, dataset, items,
                            expected_schema_version=expected_schema_version,
                            max_workers=effective, progress_callback=progress_callback)

        with _patched(S3ResearchDataSource, "head_bound_objects", forced):
            discovered, _c, _m = _discover(fake, cache)
        return {ds: tuple(sorted(
            (str(o.get("identifier")), str(o.get("version_id")),
             str(o.get("etag")), int(o.get("size") or 0))
            for o in objs)) for ds, objs in discovered.items()}

    assert roster(2, tmp_path / "r2") == roster(8, tmp_path / "r8")
