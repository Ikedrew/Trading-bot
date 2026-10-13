"""Focused acceptance for bounded, resumable FRONTIER acquisition.

Isolated, immutable fixtures and an in-memory S3 double only: no network, no
production data, no real research state. Proves that an interrupted FRONTIER
acquisition is resumed by the next attempt without repeating completed datasets,
that changed objects are revalidated and reacquired, that an invalid changed
object fails closed, that a resumed run is fingerprint-identical to an
uninterrupted run, that corrupt / stale / boundary-mismatched checkpoints are
rejected safely, that unfinished snapshots never become authoritative, and that
the checkpoint stays within its disk budget.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from core.production_data_contract import s3_base_prefix
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.frontier_coordinator import (
    CHECKPOINT_SCHEMA,
    FRONTIER_INVALID,
    SNAPSHOT_READY,
    FrontierAcquisitionCheckpointStore,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    InvestigationSnapshotError,
    load_investigation_snapshot,
)
from tests.test_frontier_coordinator import (
    MemoryS3, _body, _key, _objects, _source,
)

DAY = date(2026, 10, 3)
CHECKPOINT_NAME = "acquisition_checkpoint.json"


class RecordingS3(MemoryS3):
    """MemoryS3 that records exactly which bodies were fetched by GET."""

    def __init__(self, objects, versions=None):
        super().__init__(objects, versions)
        self.fetched: list[str] = []

    def get_object(self, **kwargs):
        self.fetched.append(str(kwargs["Key"]))
        return super().get_object(**kwargs)

    def dataset_of(self, key: str) -> str:
        for dataset in BOUND_DATASETS:
            if key.startswith(s3_base_prefix(dataset)):
                return dataset
        return ""

    def gets_for(self, dataset: str) -> int:
        return sum(1 for key in self.fetched if self.dataset_of(key) == dataset)


def _versioned(objects) -> RecordingS3:
    return RecordingS3(dict(objects), {key: "v1" for key in objects})


def _add(objects, dataset: str, count: int) -> dict:
    for index in range(1, count + 1):
        key = _key(dataset, part=f"part-{index:03d}.jsonl")
        objects[key] = _body(dataset, marker=f"{dataset}-{index}")
    return objects


def _cycle(fake, tmp_path, **kwargs):
    return run_frontier_snapshot_cycle(
        source=_source(fake),
        state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests",
        checkpoint_directory=tmp_path / "checkpoint",
        as_of_date=DAY,
        **kwargs,
    )


def _interrupt_after(index: int):
    """Abort the run the moment dataset ``index`` has been verified.

    The checkpoint for that dataset is persisted before the verified event is
    emitted, so the partial checkpoint always covers datasets ``1..index``.
    """
    def callback(event):
        if (event["phase"] == "FRONTIER_DATASET_VERIFIED"
                and event["dataset_index"] >= index):
            raise KeyboardInterrupt
    return callback


def _checkpoint_path(tmp_path):
    return tmp_path / "checkpoint" / CHECKPOINT_NAME


# ─── Store unit contracts ────────────────────────────────────────────────────


def _roster(dataset: str = "trade_truth") -> dict:
    return {dataset: {
        "objects": [{"s3_key": "k", "content_sha256": "a" * 64, "row_count": 1}],
        "event_time_coverage": {"start": None, "end": None},
    }}


def test_store_round_trips_and_rejects_boundary_mismatch(tmp_path):
    store = FrontierAcquisitionCheckpointStore(tmp_path)
    boundary = {"bucket": "b", "schemas": {"trade_truth": "v1"}}
    assert store.save(boundary=boundary, datasets=_roster()) is True
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["checkpoint_schema"] == CHECKPOINT_SCHEMA
    assert payload["checkpoint_fingerprint"]
    assert store.load(boundary=boundary) == _roster()
    # A changed boundary (e.g. bucket/schema authority) invalidates the roster.
    assert store.load(boundary={"bucket": "other", "schemas": {}}) is None


def test_store_rejects_tampered_and_corrupt_checkpoints(tmp_path):
    store = FrontierAcquisitionCheckpointStore(tmp_path)
    boundary = {"bucket": "b"}
    assert store.save(boundary=boundary, datasets=_roster()) is True

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["datasets"]["trade_truth"]["objects"][0]["content_sha256"] = "b" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load(boundary=boundary) is None  # integrity fingerprint mismatch

    store.path.write_text("{ not json", encoding="utf-8")
    assert store.load(boundary=boundary) is None  # unreadable -> rejected safely


def test_store_rejects_checkpoint_superseded_by_a_newer_success(tmp_path):
    store = FrontierAcquisitionCheckpointStore(tmp_path)
    boundary = {"bucket": "b"}
    assert store.save(boundary=boundary, datasets=_roster()) is True
    assert store.load(boundary=boundary) is not None
    # A reference timestamp after the checkpoint means a success already exists.
    assert store.load(
        boundary=boundary, not_before="2999-01-01T00:00:00+00:00") is None


def test_store_bounds_disk_usage(tmp_path):
    store = FrontierAcquisitionCheckpointStore(tmp_path, max_bytes=200)
    datasets = {"trade_truth": {
        "objects": [{"s3_key": "k" * 200, "content_sha256": "a" * 64}],
        "event_time_coverage": {"start": None, "end": None},
    }}
    assert store.save(boundary={"bucket": "b"}, datasets=datasets) is False
    assert not store.path.exists()


# ─── End-to-end resumption ───────────────────────────────────────────────────


def test_interrupted_acquisition_resumes_without_repeating_completed_datasets(tmp_path):
    objects = _add(_objects(), "decision_trace", 4)
    fake = _versioned(objects)

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_interrupt_after(2))

    # Two datasets are checkpointed; nothing authoritative was written.
    assert _checkpoint_path(tmp_path).exists()
    assert not (tmp_path / "state" / "latest_success.json").exists()
    assert not list((tmp_path / "manifests").glob("ISNAP-*.json"))
    checkpoint = json.loads(_checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    assert set(checkpoint["datasets"]) == set(BOUND_DATASETS[:2])
    first_run_downloaded = {fake.dataset_of(key) for key in fake.fetched}
    assert set(BOUND_DATASETS[:2]) <= first_run_downloaded

    # Resume: completed datasets must not be downloaded again.
    fake.fetched.clear()
    events: list[dict] = []
    resumed = _cycle(fake, tmp_path, progress_callback=events.append)

    assert resumed.status == SNAPSHOT_READY
    for dataset in BOUND_DATASETS[:2]:
        assert fake.gets_for(dataset) == 0
    for dataset in BOUND_DATASETS[2:]:
        assert fake.gets_for(dataset) >= 1
    started = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_STARTED")
    assert started["resumed_datasets"] == 2

    # Deterministic: identical to an uninterrupted acquisition of the same objects.
    clean = run_frontier_snapshot_cycle(
        source=_source(_versioned(objects)),
        state_directory=tmp_path / "clean-state",
        manifest_directory=tmp_path / "clean-manifests",
        checkpoint_directory=tmp_path / "clean-checkpoint",
        as_of_date=DAY,
    )
    assert resumed.fingerprint == clean.fingerprint
    assert resumed.snapshot_id == clean.snapshot_id


def test_changed_object_is_revalidated_and_reacquired_on_resume(tmp_path):
    objects = _add(_objects(), "trade_truth", 1)
    fake = _versioned(objects)

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_interrupt_after(1))

    # Change one object inside the checkpointed dataset (fresh etag + version).
    changed = _key("trade_truth")
    fake.objects[changed] = _body("trade_truth", marker="changed")
    fake.versions[changed] = "v2"
    fake.fetched.clear()

    result = _cycle(fake, tmp_path)

    assert result.status == SNAPSHOT_READY
    assert fake.fetched.count(changed) == 1  # changed body reacquired exactly once
    # The rest of the checkpointed dataset was NOT re-downloaded.
    untouched = _key("trade_truth", part="part-001.jsonl")
    assert fake.fetched.count(untouched) == 0
    # And the resulting snapshot matches a clean full acquisition.
    clean = run_frontier_snapshot_cycle(
        source=_source(RecordingS3(dict(fake.objects), dict(fake.versions))),
        state_directory=tmp_path / "clean-state",
        manifest_directory=tmp_path / "clean-manifests",
        checkpoint_directory=tmp_path / "clean-checkpoint",
        as_of_date=DAY,
    )
    assert result.fingerprint == clean.fingerprint


def test_invalid_changed_object_fails_closed_on_resume(tmp_path):
    objects = _add(_objects(), "decision_trace", 1)
    fake = _versioned(objects)

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_interrupt_after(1))

    # The checkpointed object changes to an invalid canonical record.
    broken = _key("decision_trace")
    fake.objects[broken] = "{ not a canonical record }\n"
    fake.versions[broken] = "v2"

    result = _cycle(fake, tmp_path)
    assert result.status == FRONTIER_INVALID
    assert not (tmp_path / "state" / "latest_success.json").exists()


def test_corrupted_checkpoint_is_rejected_safely(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / CHECKPOINT_NAME).write_text("}{ not json", encoding="utf-8")

    result = _cycle(fake, tmp_path)

    # A corrupt checkpoint never breaks the governed run; it degrades to a cold
    # acquisition that re-downloads every object exactly once.
    assert result.status == SNAPSHOT_READY
    assert fake.gets_for("trade_truth") == 1
    assert len(fake.fetched) == len(objects)


def test_boundary_change_invalidates_checkpoint(tmp_path):
    objects = _objects()
    fake = _versioned(objects)

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_interrupt_after(2))

    other = _versioned(objects)
    result = run_frontier_snapshot_cycle(
        source=S3ResearchDataSource(bucket="different-bucket", client=other),
        state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests",
        checkpoint_directory=tmp_path / "checkpoint",
        as_of_date=DAY,
    )

    assert result.status == SNAPSHOT_READY
    # Different bucket authority -> the checkpoint is ignored, full acquisition.
    assert set(other.dataset_of(key) for key in other.fetched) == set(BOUND_DATASETS)
    for dataset in BOUND_DATASETS[:2]:
        assert other.gets_for(dataset) == 1


def test_unfinished_snapshot_is_never_authoritative(tmp_path):
    objects = _objects()
    fake = _versioned(objects)

    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_interrupt_after(3))

    assert not (tmp_path / "state" / "latest_success.json").exists()
    assert not (tmp_path / "state" / "history").exists()
    assert not list((tmp_path / "manifests").glob("ISNAP-*.json"))
    # The checkpoint can never be mistaken for an immutable investigation snapshot.
    with pytest.raises(InvestigationSnapshotError):
        load_investigation_snapshot(_checkpoint_path(tmp_path))


def test_checkpoint_is_cleared_after_successful_acquisition(tmp_path):
    fake = _versioned(_objects())
    result = _cycle(fake, tmp_path)
    assert result.status == SNAPSHOT_READY
    assert (tmp_path / "state" / "latest_success.json").exists()
    assert not _checkpoint_path(tmp_path).exists()
