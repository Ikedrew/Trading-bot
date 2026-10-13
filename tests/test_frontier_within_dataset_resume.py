"""Focused process-interruption recovery for an unfinished FRONTIER dataset."""
from __future__ import annotations

import json

import pytest

from research_engine.v10.continuous.frontier_coordinator import (
    SNAPSHOT_READY, FrontierAcquisitionCheckpointStore,
)
from tests.test_frontier_resumable_acquisition import (
    RecordingS3, _add, _body, _checkpoint_path, _cycle, _objects, _versioned,
)


def _stop_inside_execution_context(event):
    if (event["phase"] == "FRONTIER_DATASET_READING"
            and event["dataset"] == "execution_context"
            and event["objects_processed"] >= 75):
        raise KeyboardInterrupt


def _interrupted(tmp_path):
    objects = _add(_objects(), "execution_context", 130)
    fake = _versioned(objects)
    with pytest.raises(KeyboardInterrupt):
        _cycle(fake, tmp_path, progress_callback=_stop_inside_execution_context)
    partial_path = tmp_path / "checkpoint" / "in_progress.json"
    partial = json.loads(partial_path.read_text(encoding="utf-8"))
    assert partial["dataset"] == "execution_context"
    assert len(partial["objects"]) >= 64
    assert set(json.loads(_checkpoint_path(tmp_path).read_text())["datasets"]) == {
        "trade_truth", "execution_results", "decision_trace",
        "execution_attempts", "shadow_runtime",
    }
    assert not (tmp_path / "state" / "latest_success.json").exists()
    assert not list((tmp_path / "manifests").glob("ISNAP-*.json"))
    return objects, fake, partial_path, partial


def test_partial_boundary_and_versionless_records_fail_closed(tmp_path):
    store = FrontierAcquisitionCheckpointStore(tmp_path)
    boundary = {"bucket": "source-a", "region": "eu-west-2"}
    records = [
        {"s3_key": "versioned", "version_id": "v1", "content_sha256": "a" * 64},
        {"s3_key": "versionless", "version_id": None,
         "content_sha256": "b" * 64},
        {"s3_key": "suspended-version", "version_id": "null",
         "content_sha256": "c" * 64},
    ]
    assert store.save_partial(boundary=boundary, dataset="execution_context",
                              objects=records)
    assert [item["s3_key"] for item in store.load_partial(
        boundary=boundary)["objects"]] == ["versioned"]
    assert store.load_partial(boundary={"bucket": "source-b",
                                        "region": "eu-west-2"}) is None


def test_revalidation_publishes_client_and_head_progress(tmp_path):
    objects = _objects()
    events = []

    def stop_after_first_dataset(event):
        events.append(event)
        if (event["phase"] == "FRONTIER_DATASET_VERIFIED"
                and event["dataset"] == "trade_truth"):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _cycle(_versioned(objects), tmp_path,
               progress_callback=stop_after_first_dataset)
    phases = [event["phase"] for event in events
              if event.get("dataset") == "trade_truth"]
    assert "FRONTIER_VERSION_REVALIDATION_STARTED" not in phases
    # A cold run reads the object. Resume it to exercise the version HEAD path.
    events.clear()
    _cycle(_versioned(objects), tmp_path, progress_callback=events.append)
    head_events = [event for event in events
                   if event.get("dataset") == "trade_truth"]
    phases = [event["phase"] for event in head_events]
    assert "FRONTIER_VERSION_REVALIDATION_STARTED" in phases
    assert "FRONTIER_VERSION_REVALIDATION_CLIENT_READY" in phases
    assert any(event["phase"] == "FRONTIER_VERSION_REVALIDATION_PROGRESS"
               and event["queued"] >= 1 and event["completed"] >= 1
               and event["source_io_cumulative"]["head_calls"] >= 1
               for event in head_events)


def test_restart_reuses_verified_objects_and_matches_cold_snapshot(tmp_path):
    objects, fake, partial_path, partial = _interrupted(tmp_path)
    reused = {item["s3_key"] for item in partial["objects"]}
    # A process killed during an atomic write can leave an abandoned temp file.
    (partial_path.parent / "in_progress-abandoned.tmp").write_text("truncated")
    fake.fetched.clear()
    events = []
    resumed = _cycle(fake, tmp_path, progress_callback=events.append)
    assert resumed.status == SNAPSHOT_READY
    started = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_STARTED")
    verified = next(e for e in events if e["phase"] == "FRONTIER_DATASET_VERIFIED"
                    and e["dataset"] == "execution_context")
    assert started["partial_objects"] == len(reused)
    dataset_started = next(e for e in events
                           if e["phase"] == "FRONTIER_DATASET_STARTED"
                           and e["dataset"] == "execution_context")
    assert dataset_started["objects_requiring_head"] >= len(reused)
    assert verified["objects_reused"] >= len(reused)
    assert verified["downloaded"] < verified["objects"]
    assert not partial_path.exists()
    clean = _cycle(_versioned(objects), tmp_path / "clean")
    assert resumed.snapshot_id == clean.snapshot_id
    assert resumed.fingerprint == clean.fingerprint


def test_changed_verified_object_is_reacquired_after_fresh_authority(tmp_path):
    objects, fake, _, partial = _interrupted(tmp_path)
    changed = partial["objects"][0]["s3_key"]
    fake.objects[changed] = _body("execution_context", marker="changed")
    fake.versions[changed] = "v2"
    fake.fetched.clear()
    events = []
    result = _cycle(fake, tmp_path, progress_callback=events.append)
    assert result.status == SNAPSHOT_READY
    verified = next(e for e in events if e["phase"] == "FRONTIER_DATASET_VERIFIED"
                    and e["dataset"] == "execution_context")
    assert verified["objects_reused"] >= len(partial["objects"]) - 1
    assert verified["downloaded"] < verified["objects"]
    clean = _cycle(RecordingS3(dict(fake.objects), dict(fake.versions)),
                   tmp_path / "clean")
    assert result.snapshot_id == clean.snapshot_id


def test_two_interruptions_keep_advancing_same_dataset(tmp_path):
    objects = _add(_objects(), "execution_context", 200)
    fake = _versioned(objects)
    partial_path = tmp_path / "checkpoint" / "in_progress.json"
    for minimum in (64, 128):
        with pytest.raises(KeyboardInterrupt):
            _cycle(fake, tmp_path, progress_callback=_stop_inside_execution_context)
        partial = json.loads(partial_path.read_text(encoding="utf-8"))
        assert len(partial["objects"]) >= minimum
        assert not (tmp_path / "state" / "latest_success.json").exists()
    assert _cycle(fake, tmp_path).status == SNAPSHOT_READY


def test_corrupt_partial_cannot_promote_or_suppress_body_reads(tmp_path):
    _, fake, partial_path, partial = _interrupted(tmp_path)
    partial["objects"][0]["content_sha256"] = "0" * 64
    partial_path.write_text(json.dumps(partial), encoding="utf-8")
    events = []
    result = _cycle(fake, tmp_path, progress_callback=events.append)
    assert result.status == SNAPSHOT_READY
    started = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_STARTED")
    verified = next(e for e in events if e["phase"] == "FRONTIER_DATASET_VERIFIED"
                    and e["dataset"] == "execution_context")
    assert started["partial_objects"] == 0
    assert verified["downloaded"] == verified["objects"]
