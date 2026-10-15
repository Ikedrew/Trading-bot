"""Offline canonical-reader checks for same-cycle bounded HEAD authority."""
from __future__ import annotations

import threading
import time
from collections import Counter

import pytest

from research_engine.data_access.s3_source import ResearchDataSourceError, S3ResearchDataSource
from research_engine.v10.investigation_snapshot import SnapshotBoundDatasetReader
from tests.test_cold_start_cache_recovery import _freeze, _objects, _versioned
from tests.test_incremental_snapshot_materialization import _body, _key


class HeadOnly:
    def __init__(self, delegate, *, delay=0.0, override=None, missing=None):
        self.delegate = delegate
        self.delay = delay
        self.override = override or {}
        self.missing = missing
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.calls = []

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        with self.lock:
            self.calls.append(key)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if key == self.missing:
                raise KeyError(key)
            return {**self.delegate.head_object(**kwargs), **self.override.get(key, {})}
        finally:
            with self.lock:
                self.active -= 1

    def get_object(self, **kwargs):
        raise AssertionError("unexpected GET")

    def list_objects_v2(self, **kwargs):
        raise AssertionError("unexpected LIST")


def prepared(tmp_path, count=20):
    objects = _objects()
    objects.update({_key("market_context", part=f"part-{i:04}.jsonl"):
                    _body("market_context", str(i)) for i in range(count)})
    fake = _versioned(objects)
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)
    return snapshot, fake, cache


def reader(snapshot, client, cache, **kwargs):
    source = S3ResearchDataSource(bucket="incremental-test", client=client,
                                  verified_cache_directory=cache)
    value = SnapshotBoundDatasetReader(snapshot, source, **kwargs)
    return value, source


def test_bounded_head_is_once_per_object_ordered_and_offline(tmp_path):
    snapshot, fake, cache = prepared(tmp_path, count=140)
    client = HeadOnly(fake, delay=0.002)
    events = []
    value, source = reader(snapshot, client, cache, head_workers=8,
                           progress_callback=events.append)
    keys = [item.identifier for binding in snapshot.datasets for item in binding.objects]
    assert Counter(client.calls) == Counter(keys)
    assert 1 < client.peak <= 8
    assert source.performance_snapshot()["head_calls"] == len(keys)
    assert source.performance_snapshot()["get_calls"] == 0
    assert source.performance_snapshot()["list_calls"] == 0
    assert events[-1]["objects_completed"] == len(keys)
    assert events[-1]["objects_failed"] == 0
    sequential, _ = reader(snapshot, HeadOnly(fake), cache, head_workers=1)
    for binding in snapshot.datasets:
        assert value.read_dataset(binding.dataset) == sequential.read_dataset(binding.dataset)
    assert value.snapshot_provenance == sequential.snapshot_provenance


@pytest.mark.parametrize("field,replacement", [
    ("VersionId", "changed"), ("ETag", '"changed"'),
    ("ContentLength", 1), ("LastModified", "changed"),
])
def test_batch_rejects_exact_identity_mismatch(tmp_path, field, replacement):
    snapshot, fake, cache = prepared(tmp_path, count=2)
    key = next(item.identifier for binding in snapshot.datasets for item in binding.objects)
    client = HeadOnly(fake, override={key: {field: replacement}})
    with pytest.raises(ResearchDataSourceError, match="VERIFIED_BODY_AUTHORITY_MISMATCH"):
        reader(snapshot, client, cache)
    assert fake.get_calls == len(fake.objects)


def test_missing_object_or_body_fails_closed_without_get(tmp_path):
    snapshot, fake, cache = prepared(tmp_path, count=2)
    binding = next(binding for binding in snapshot.datasets if binding.objects)
    item = binding.objects[0]
    with pytest.raises(ResearchDataSourceError):
        reader(snapshot, HeadOnly(fake, missing=item.identifier), cache)
    source = S3ResearchDataSource(bucket="incremental-test", client=HeadOnly(fake),
                                  verified_cache_directory=cache)
    source._verified_body_path(source._verified_body_identity(
        binding.dataset, item.to_dict())).unlink()
    with pytest.raises(ResearchDataSourceError):
        SnapshotBoundDatasetReader(snapshot, source)


def test_worker_limit_is_capped(tmp_path):
    snapshot, fake, cache = prepared(tmp_path, count=0)
    with pytest.raises(ValueError, match="SNAPSHOT_HEAD_WORKERS_OUT_OF_BOUNDS"):
        reader(snapshot, HeadOnly(fake), cache, head_workers=17)
