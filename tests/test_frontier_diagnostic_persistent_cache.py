"""Offline acceptance for bounded diagnostic verified-body persistence."""
from __future__ import annotations

import time

import pytest

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.investigation_snapshot import freeze_investigation_snapshot
from tests.test_incremental_snapshot_materialization import (
    MemoryS3, _body, _key, _objects,
)


DAY = "2026-09-25"


def _source(fake, directory, budget):
    return S3ResearchDataSource(bucket="incremental-test", client=fake,
                                verified_cache_directory=directory,
                                verified_cache_max_bytes=budget)


def _freeze(source):
    return freeze_investigation_snapshot(start_date=DAY, end_date=DAY, source=source)


def _fixtures():
    objects = _objects()
    for index in range(12):
        objects[_key("market_context", part=f"part-{index:03d}.jsonl")] = (
            _body("market_context", f"extra-{index}"))
    return objects


def test_bounded_cache_survives_interruption_and_preserves_snapshot(tmp_path):
    objects = _fixtures()
    fake = MemoryS3(objects, {key: "v1" for key in objects})
    cache = tmp_path / "verified"
    budget = 2 * 1024 * 1024
    original_get = fake.get_object

    def interrupted_get(**kwargs):
        if fake.get_calls == 2:
            raise KeyboardInterrupt
        return original_get(**kwargs)

    fake.get_object = interrupted_get
    with pytest.raises(KeyboardInterrupt):
        _freeze(_source(fake, cache, budget))
    saved = list(cache.glob("*.cache"))
    assert len(saved) == 2
    assert not list(tmp_path.glob("ISNAP-*.json"))

    fake.get_object = original_get
    source = _source(fake, cache, budget)
    before = fake.get_calls
    resumed = _freeze(source)
    assert fake.get_calls - before == len(objects) - len(saved)
    assert source.performance["verified_cache_hits"] >= len(saved)
    assert source.performance["verified_cache_writes"] == len(objects) - len(saved)
    assert sum(p.stat().st_size for p in cache.iterdir()) <= budget
    assert source.performance["verified_cache_used_bytes"] <= budget

    warm = _source(fake, cache, budget)
    before = fake.get_calls
    repeat = _freeze(warm)
    assert fake.get_calls == before
    assert warm.performance["verified_cache_hits"] >= len(objects)
    assert repeat.snapshot_fingerprint == resumed.snapshot_fingerprint

    clean = _freeze(_source(MemoryS3(objects, {key: "v1" for key in objects}),
                            tmp_path / "clean", budget))
    assert resumed.snapshot_fingerprint == clean.snapshot_fingerprint


def test_equivalent_workload_gets_and_wall_time(tmp_path):
    objects = _fixtures()

    class SlowS3(MemoryS3):
        def get_object(self, **kwargs):
            time.sleep(0.02)
            return super().get_object(**kwargs)

    def pair(directory, budget):
        fake = SlowS3(objects, {key: "v1" for key in objects})
        results = []
        for _ in range(2):
            source = _source(fake, directory, budget)
            before = fake.get_calls
            started = time.perf_counter()
            snapshot = _freeze(source)
            results.append((fake.get_calls - before,
                            time.perf_counter() - started,
                            source.performance["verified_cache_hits"],
                            snapshot.snapshot_fingerprint))
        return results

    disabled = pair(tmp_path / "disabled", 0)
    enabled = pair(tmp_path / "enabled", 2 * 1024 * 1024)
    assert disabled[1][0] >= len(objects)
    assert enabled[1][0] == 0
    assert enabled[1][2] >= len(objects)
    assert disabled[1][3] == enabled[1][3]
    print(f"disabled second: GET={disabled[1][0]} hits={disabled[1][2]} "
          f"seconds={disabled[1][1]:.3f}; enabled second: GET={enabled[1][0]} "
          f"hits={enabled[1][2]} seconds={enabled[1][1]:.3f}")


def test_capacity_stops_writes_without_deleting_existing_entries(tmp_path):
    objects = _fixtures()
    fake = MemoryS3(objects, {key: "v1" for key in objects})
    directory = tmp_path / "verified"
    budget = 67_000
    first = _source(fake, directory, budget)
    snapshot = _freeze(first)
    files = {path.name: path.read_bytes() for path in directory.glob("*.cache")}
    assert 0 < len(files) < len(objects)
    assert first.performance["verified_cache_capacity_skips"] > 0
    assert sum(path.stat().st_size for path in directory.iterdir()) <= budget

    repeat = _source(fake, directory, budget)
    same = _freeze(repeat)
    assert same.snapshot_fingerprint == snapshot.snapshot_fingerprint
    assert {path.name: path.read_bytes() for path in directory.glob("*.cache")} == files
    assert sum(path.stat().st_size for path in directory.iterdir()) <= budget
