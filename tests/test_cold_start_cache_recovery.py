"""Focused acceptance for safe cold-start verified-cache reuse (Priority A).

Isolated, immutable fixtures and an in-memory S3 double only: no network, no
production data, no real research state. Proves that an interrupted cold start
can reuse unchanged cached evidence after fresh authority checks, that
unchanged bodies require zero repeat GETs, that changed/corrupted evidence is
reacquired or fails closed, and that completed snapshot identities remain
identical to the original verified implementation.
"""
from __future__ import annotations

import pytest
import threading
import time
from datetime import date

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import (
    ResearchDataSourceError, S3ResearchDataSource,
)
from research_engine.v10.investigation_snapshot import freeze_investigation_snapshot
from research_engine.v10.continuous.frontier_coordinator import (
    _discover_verified, run_frontier_snapshot_cycle,
)
from tests.test_incremental_snapshot_materialization import (
    MemoryS3, _body, _key, _objects,
)

DAY = "2026-09-25"


def _versioned(objects):
    return MemoryS3(dict(objects), {key: "v1" for key in objects})


def _source(fake, cache):
    return S3ResearchDataSource(bucket="incremental-test", client=fake,
                                verified_cache_directory=cache)


def _freeze(fake, cache):
    return freeze_investigation_snapshot(start_date=DAY, end_date=DAY,
                                         source=_source(fake, cache))


def test_cold_start_reuses_unchanged_bodies_with_zero_repeat_gets(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    cache = tmp_path / "bodies"

    first = _freeze(fake, cache)
    assert fake.get_calls == len(objects)  # cold: every body downloaded once
    assert len(list(cache.glob("*.cache"))) == len(objects)

    # A brand-new source with NO prior manifest/digest must still find the
    # existing entries from fresh S3 authority alone.
    before = fake.get_calls
    src = _source(fake, cache)
    second = freeze_investigation_snapshot(start_date=DAY, end_date=DAY, source=src)
    assert fake.get_calls == before  # zero repeat GETs
    # One fresh discovery HEAD and one snapshot materialization HEAD per object.
    # The discovery lookup must not repeat its own fresh authority check.
    assert src.performance["head_calls"] == 2 * len(objects)
    assert src.performance["verified_cache_hits"] >= len(objects)
    assert src.performance["remote_body_bytes"] == 0
    assert second.snapshot_fingerprint == first.snapshot_fingerprint
    for actual, expected in zip(second.datasets, first.datasets):
        assert actual.objects == expected.objects
        assert actual.content_digest == expected.content_digest
        assert actual.dataset_snapshot_id == expected.dataset_snapshot_id
        assert actual.canonical_row_count == expected.canonical_row_count


def test_changed_object_is_reacquired_not_served_from_cache(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    cache = tmp_path / "bodies"
    first = _freeze(fake, cache)

    key = _key("market_context")
    fake.objects[key] = _body("market_context", "changed")
    fake.versions[key] = "v2"

    before = fake.get_calls
    src = _source(fake, cache)
    second = freeze_investigation_snapshot(start_date=DAY, end_date=DAY, source=src)
    assert fake.get_calls - before == 1  # only the changed body is re-fetched
    assert src.performance["verified_cache_hits"] >= len(objects) - 1
    assert second.snapshot_fingerprint != first.snapshot_fingerprint


def test_corrupted_cold_start_entry_fails_closed(tmp_path):
    fake = _versioned(_objects())
    cache = tmp_path / "bodies"
    _freeze(fake, cache)
    path = next(cache.glob("*.cache"))
    data = path.read_bytes()
    offset = data.index(b"\n") + 1
    path.write_bytes(data[:offset] + data[offset:].replace(
        b'"symbol"', b'"symboL"', 1))
    with pytest.raises(ResearchDataSourceError, match="VERIFIED_BODY_CACHE_INVALID"):
        _freeze(fake, cache)


def test_full_cache_reuses_existing_entries_and_fetches_changed_body(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    cache = tmp_path / "bodies"
    first = _freeze(fake, cache)
    files_before = {path.name for path in cache.glob("*.cache")}
    key = _key("market_context")
    fake.objects[key] = _body("market_context", "changed")
    fake.versions[key] = "v2"
    gets_before = fake.get_calls
    source = S3ResearchDataSource(
        bucket="incremental-test", client=fake,
        verified_cache_directory=cache, verified_cache_max_bytes=1)
    second = freeze_investigation_snapshot(
        start_date=DAY, end_date=DAY, source=source)
    # The full freeze has discovery and materialization reads. With no quota
    # for a new verified body, the changed object is fetched in both phases.
    assert fake.get_calls - gets_before == 2
    assert source.performance["verified_cache_hits"] >= len(objects) - 1
    assert source.performance["verified_cache_writes"] == 0
    assert {path.name for path in cache.glob("*.cache")} == files_before
    assert second.snapshot_fingerprint != first.snapshot_fingerprint


def test_parallel_head_counter_records_every_request(tmp_path):
    objects = {_key("market_context", part=f"part-{index:03d}.jsonl"):
               _body("market_context") for index in range(64)}
    fake = _versioned(objects)
    source = _source(fake, tmp_path / "bodies")
    listed = source.discover_dataset_objects("market_context")
    headed = source.head_bound_objects(
        "market_context", listed,
        expected_schema_version=current_schema("market_context"))
    assert len(headed) == len(objects)
    assert source.performance["head_calls"] == len(objects)
    assert source.performance["head_seconds_inclusive"] >= 0


class RecordingHeadS3(MemoryS3):
    def __init__(self, objects, *, latency=0.02, fail_key=None, retries=0,
                 throttle=False):
        super().__init__(objects, {key: "v1" for key in objects})
        self.latency = latency
        self.fail_key = fail_key
        self.retries = retries
        self.throttle = throttle
        self.lock = threading.Lock()
        self.active = 0
        self.peak_active = 0
        self.head_keys = []

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.head_keys.append(key)
        try:
            # Identical mean latency, deliberately different completion order.
            time.sleep(self.latency * (0.6 + int(key[-10:-6]) % 5 / 5))
            if key == self.fail_key:
                if self.throttle:
                    class Throttled(Exception):
                        response = {"Error": {"Code": "SlowDown"},
                                    "ResponseMetadata": {"RetryAttempts": 2}}
                    raise Throttled("simulated S3 throttle after retries")
                raise KeyError(key)
            response = super().head_object(**kwargs)
            response["ResponseMetadata"] = {"RetryAttempts": self.retries}
            return response
        finally:
            with self.lock:
                self.active -= 1


def _many_objects(count=64):
    return {_key("market_context", part=f"part-{index:04d}.jsonl"):
            _body("market_context", str(index)) for index in range(count)}


def test_bounded_head_results_order_and_retry_counters(tmp_path):
    fake = RecordingHeadS3(_many_objects(), retries=1)
    source = _source(fake, tmp_path / "cache")
    listed = source.discover_dataset_objects("market_context")
    progress = []
    headed = source.head_bound_objects(
        "market_context", listed,
        expected_schema_version=current_schema("market_context"),
        max_workers=8, progress_callback=progress.append)
    assert [item["identifier"] for item in headed] == [
        item["identifier"] for item in listed]
    assert fake.peak_active <= 8
    assert fake.peak_active > 1
    assert source.performance["head_calls"] == len(listed)
    assert source.performance["head_retries"] == len(listed)
    assert fake.get_calls == 0
    assert progress[-1] == {"queued": len(listed), "completed": len(listed),
                            "successful": len(listed), "failed": 0,
                            "retried": len(listed), "total": len(listed)}
    assert all(p["queued"] - p["completed"] <= 16 for p in progress)


def test_failed_parallel_head_never_publishes_partial_authority(tmp_path):
    objects = _many_objects(24)
    missing = sorted(objects)[4]
    fake = RecordingHeadS3(objects, fail_key=missing)
    source = _source(fake, tmp_path / "cache")
    listed = source.discover_dataset_objects("market_context")
    progress = []
    with pytest.raises(ResearchDataSourceError):
        source.head_bound_objects(
            "market_context", listed,
            expected_schema_version=current_schema("market_context"),
            max_workers=8, progress_callback=progress.append)
    assert progress[-1]["failed"] >= 1
    assert source._headed_objects == {}
    assert fake.peak_active <= 8
    assert fake.get_calls == 0


def test_interrupted_parallel_head_has_no_completed_authority(tmp_path):
    fake = RecordingHeadS3(_many_objects(24))
    source = _source(fake, tmp_path / "cache")
    listed = source.discover_dataset_objects("market_context")

    def interrupt(progress):
        if progress["completed"]:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        source.head_bound_objects(
            "market_context", listed,
            expected_schema_version=current_schema("market_context"),
            max_workers=8, progress_callback=interrupt)
    assert source._headed_objects == {}
    assert fake.get_calls == 0


def test_throttled_head_counts_retries_and_fails_closed(tmp_path):
    objects = _many_objects(24)
    fake = RecordingHeadS3(objects, fail_key=sorted(objects)[4], throttle=True)
    source = _source(fake, tmp_path / "cache")
    listed = source.discover_dataset_objects("market_context")
    progress = []
    with pytest.raises(ResearchDataSourceError):
        source.head_bound_objects(
            "market_context", listed,
            expected_schema_version=current_schema("market_context"),
            max_workers=8, progress_callback=progress.append)
    assert source.performance["head_retries"] == 2
    assert progress[-1]["retried"] == 2
    assert progress[-1]["failed"] >= 1
    assert source._headed_objects == {}


def test_object_missing_after_listing_fails_closed(tmp_path):
    objects = _many_objects(16)
    fake = RecordingHeadS3(objects)
    source = _source(fake, tmp_path / "cache")
    listed = source.discover_dataset_objects("market_context")
    del fake.objects[listed[3]["identifier"]]
    with pytest.raises(ResearchDataSourceError):
        source.head_bound_objects(
            "market_context", listed,
            expected_schema_version=current_schema("market_context"),
            max_workers=8)
    assert source._headed_objects == {}


def test_frontier_cold_start_parallel_authority_reuses_each_head(tmp_path):
    objects = {**_objects(), **_many_objects(32)}
    fake = RecordingHeadS3(objects, latency=0.01)
    cache = tmp_path / "bodies"
    baseline = _freeze(fake, cache)
    cache_names = {path.name for path in cache.glob("*.cache")}
    head_before, get_before = fake.head_calls, fake.get_calls
    source = _source(fake, cache)
    progress = []
    discovered, _, _ = _discover_verified(
        source, as_of_date=date(2026, 10, 3),
        progress_callback=progress.append)
    assert sum(map(len, discovered.values())) == len(objects)
    assert source.performance["head_calls"] == len(objects)
    assert fake.head_calls - head_before == len(objects)
    assert fake.get_calls == get_before
    assert source.performance["verified_cache_hits"] == len(objects)
    assert fake.peak_active <= 8
    assert any(event["phase"] == "FRONTIER_AUTHORITY_CHECKS"
               and event["completed"] == 33
               and event["successful"] == 33
               and event["source_io_cumulative"]["head_calls"] >= 33
               for event in progress)
    assert {path.name for path in cache.glob("*.cache")} == cache_names
    repeat = freeze_investigation_snapshot(
        start_date=DAY, end_date=DAY, source=_source(fake, cache))
    assert repeat.snapshot_fingerprint == baseline.snapshot_fingerprint
    assert [(d.dataset, d.dataset_snapshot_id, d.objects) for d in repeat.datasets] == [
        (d.dataset, d.dataset_snapshot_id, d.objects) for d in baseline.datasets]


def test_interrupted_frontier_does_not_mark_cycle_complete(tmp_path):
    objects = {**_objects(), **_many_objects(24)}
    fake = RecordingHeadS3(objects, latency=0.01)
    cache = tmp_path / "bodies"
    _freeze(fake, cache)
    cache_names = {path.name for path in cache.glob("*.cache")}
    state = tmp_path / "frontier-state"

    def interrupt(event):
        if (event["phase"] == "FRONTIER_AUTHORITY_CHECKS"
                and event["completed"]):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_frontier_snapshot_cycle(
            source=_source(fake, cache), state_directory=state,
            manifest_directory=tmp_path / "manifests",
            as_of_date=date(2026, 10, 3), progress_callback=interrupt)
    assert not (state / "latest.json").exists()
    assert {path.name for path in cache.glob("*.cache")} == cache_names
