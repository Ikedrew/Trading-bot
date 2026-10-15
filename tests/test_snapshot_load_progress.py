"""Offline coverage for bounded, observable immutable snapshot admission."""
from types import SimpleNamespace

import pytest

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import ResearchDataSourceError, S3ResearchDataSource
from research_engine.v10.investigation_snapshot import (
    InvestigationSnapshotError, SnapshotBoundDatasetReader)
import research_engine.v10.investigation_snapshot as snapshot_module

from test_investigation_snapshot import _freeze, _key, _objects, _source


def test_reader_reports_bounded_progress_and_delayed_eta(monkeypatch):
    objects = _objects()
    original = objects[_key("trade_truth")]
    for number in range(101):
        objects[_key("trade_truth", part=f"extra-{number:03}.jsonl")] = original
    _, source = _source(objects)
    snapshot = _freeze(source)
    ticks = iter(range(100000))
    monkeypatch.setattr(snapshot_module, "time",
                        SimpleNamespace(perf_counter=lambda: next(ticks)))
    events = []
    reader = SnapshotBoundDatasetReader(
        snapshot, source, progress_callback=events.append,
        progress_interval_seconds=1000, progress_interval_objects=25)
    total = sum(len(binding.objects) for binding in snapshot.datasets
                if binding.presence != "ABSENT")
    assert reader.reads_by_dataset["trade_truth"] == 102
    assert events[0]["objects_completed"] == 0
    assert events[-1]["objects_completed"] == total
    assert events[-1]["total_objects"] == total
    assert all(event["objects_failed"] == 0 for event in events)
    assert all(event["phase"] == "SNAPSHOT_LOADING" for event in events)
    assert all(event["estimated_remaining_seconds"] is None
               for event in events if event["objects_completed"] < 100)
    assert any(event["estimated_remaining_seconds"] is not None
               for event in events if 100 <= event["objects_completed"] < total)
    assert events[-1]["throughput_objects_per_second"] > 0
    assert len(events) < total / 4


def test_reader_deadline_fails_closed_with_last_count(monkeypatch):
    _, source = _source()
    snapshot = _freeze(source)
    ticks = iter(range(100000))
    monkeypatch.setattr(snapshot_module, "time",
                        SimpleNamespace(perf_counter=lambda: next(ticks)))
    events = []
    with pytest.raises(InvestigationSnapshotError,
                       match="SNAPSHOT_LOAD_DEADLINE_EXCEEDED"):
        SnapshotBoundDatasetReader(snapshot, source, progress_callback=events.append,
                                   deadline_seconds=12,
                                   progress_interval_objects=100)
    assert events[-1]["deadline_exceeded"] is True
    assert events[-1]["objects_completed"] < events[-1]["total_objects"]
    assert events[-1]["objects_failed"] == 0


def test_failed_object_and_head_failure_are_reported():
    _, source = _source()
    snapshot = _freeze(source)
    target = snapshot.datasets[0].objects[0].identifier
    original = source.read_bound_objects

    def fail_one(dataset, objects, **kwargs):
        if objects[0]["identifier"] == target:
            source.performance["head_calls"] += 1
            source.performance["head_failures"] += 1
            raise ResearchDataSourceError("HEAD_DENIED")
        return original(dataset, objects, **kwargs)

    source.read_bound_objects = fail_one
    events = []
    with pytest.raises(ResearchDataSourceError, match="HEAD_DENIED"):
        SnapshotBoundDatasetReader(snapshot, source,
                                   progress_callback=events.append)
    assert events[-1]["objects_failed"] == 1
    assert events[-1]["objects_completed"] == 0
    assert events[-1]["failed_object"] == target
    assert events[-1]["head_failures"] == 1


def test_source_counts_real_head_failure():
    class FailingHead:
        def head_object(self, **kwargs):
            raise RuntimeError("HEAD_DENIED")

    source = S3ResearchDataSource(bucket="test-bucket", client=FailingHead())
    with pytest.raises(ResearchDataSourceError):
        source.head_bound_objects(
            "trade_truth", [{"identifier": _key("trade_truth")}],
            expected_schema_version=current_schema("trade_truth"))
    assert source.performance_snapshot()["head_calls"] == 1
    assert source.performance_snapshot()["head_failures"] == 1
