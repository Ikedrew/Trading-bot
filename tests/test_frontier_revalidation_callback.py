"""Offline reproduction of checkpoint recovery plus diagnostic HEAD progress."""
from __future__ import annotations

import json
import threading
import time

from core.production_data_contract import canonical_s3_key, current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.cycle_state import ContinuousCycleProgressStore
from research_engine.v10.continuous.frontier_coordinator import (
    BOUND_DATASETS, FrontierAcquisitionCheckpointStore, _checkpoint_boundary,
    _same_frontier_listing_identity, _same_frontier_object_identity, _substage,
)
from research_engine.v10.continuous.research_loop import _frontier_substage_emitter
from research_engine.v10.investigation_snapshot import CycleSnapshotInputs


DATASET = "trade_truth"
STAMP = "2026-09-04T03:51:39+00:00"
COMPLETED = ("trade_truth", "execution_results", "decision_trace",
             "shadow_runtime", "execution_attempts")


class MemoryHeadClient:
    def __init__(self, keys):
        self.keys = set(keys)
        self.head_calls = []
        self.get_calls = 0
        self.lock = threading.Lock()

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {"Contents": [
            {"Key": key, "ETag": '"etag-1"', "Size": 12,
             "LastModified": STAMP}
            for key in sorted(self.keys) if key.startswith(prefix)
        ], "IsTruncated": False}

    def head_object(self, **kwargs):
        assert set(kwargs) == {"Bucket", "Key"}  # current-version authority
        with self.lock:
            self.head_calls.append(kwargs["Key"])
        time.sleep(0.001)
        return {"VersionId": "v1", "ETag": '"etag-1"',
                "ContentLength": 12, "LastModified": STAMP,
                "ResponseMetadata": {"RetryAttempts": 0}}

    def get_object(self, **kwargs):
        self.get_calls += 1
        raise AssertionError("HEAD revalidation must not read bodies")


def test_87_checkpoint_heads_finish_with_real_diagnostic_persistence(tmp_path):
    keys = [canonical_s3_key(DATASET, symbol="AUDUSD", date="2026-09-04",
                             part=f"part-{index:03d}.jsonl") for index in range(87)]
    client = MemoryHeadClient(keys)
    source = S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                  profile="offline-profile", client=client)
    checkpoint_store = FrontierAcquisitionCheckpointStore(tmp_path / "checkpoint")
    boundary = _checkpoint_boundary(source)
    prior = [{"s3_key": key, "identifier": key, "dataset": DATASET,
              "version_id": "v1", "etag": "etag-1", "listed_byte_count": 12,
              "last_modified": STAMP, "content_sha256": "a" * 64,
              "row_count": 1, "byte_size": 12} for key in keys]
    datasets = {name: {"objects": prior if name == DATASET else [],
                       "event_time_coverage": {}}
                for name in COMPLETED}
    assert checkpoint_store.save(boundary=boundary, datasets=datasets)
    recovered = checkpoint_store.load(boundary=boundary)
    assert recovered is not None and len(recovered) == 5
    assert len(recovered[DATASET]["objects"]) == 87

    listed = tuple(source.discover_dataset_objects(DATASET))
    by_key = {item["s3_key"]: item for item in recovered[DATASET]["objects"]}
    version_checks = [item for item in listed if
                      _same_frontier_listing_identity(by_key[item["identifier"]], item)]
    assert len(version_checks) == 87

    progress_path = tmp_path / "diagnostic" / "cycle_progress.json"
    progress_store = ContinuousCycleProgressStore(progress_path)
    shared_inputs = CycleSnapshotInputs("offline-cycle")
    persistence_errors = []

    def progress(method, *args, **kwargs):
        try:
            if method == "update_stage" and len(args) > 1:
                args = (args[0], {**args[1], "snapshot_inputs": shared_inputs.diagnostics()},
                        *args[2:])
            getattr(progress_store, method)(*args, **kwargs)
        except Exception as exc:
            persistence_errors.append(exc)
            raise

    progress("start", cycle_attempt_id="offline-attempt")
    progress("enter_stage", "FRONTIER")
    frontier_substage, state = _frontier_substage_emitter(progress)
    emit = _substage(frontier_substage)
    emit("FRONTIER_VERSION_REVALIDATION_STARTED", dataset=DATASET, total=87,
         source_io_cumulative=source.performance_snapshot())

    def version_progress(counts):
        phase = ("FRONTIER_VERSION_REVALIDATION_CLIENT_READY"
                 if counts.get("stage") == "client_ready"
                 else "FRONTIER_VERSION_REVALIDATION_PROGRESS")
        emit(phase, dataset=DATASET, dataset_index=1,
             dataset_total=len(BOUND_DATASETS), **dict(counts),
             source_io_cumulative=source.performance_snapshot())

    headed = source.head_bound_objects(
        DATASET, version_checks, expected_schema_version=current_schema(DATASET),
        progress_callback=version_progress)
    assert len(headed) == 87
    assert all(_same_frontier_object_identity(by_key[item["identifier"]], item)
               for item in headed)
    assert len(client.head_calls) == len(set(client.head_calls)) == 87
    assert source.performance_snapshot()["head_calls"] == 87
    assert source.performance_snapshot()["head_seconds_inclusive"] > 0
    assert client.get_calls == source.performance_snapshot()["get_calls"] == 0
    assert not persistence_errors
    assert state["events"] >= state["persisted"] >= 3

    document = json.loads(progress_path.read_text(encoding="utf-8"))
    substage = document["stages"]["FRONTIER"]["details"]["frontier_substage"]
    assert substage["phase"] == "FRONTIER_VERSION_REVALIDATION_PROGRESS"
    assert substage["queued"] >= 32
    assert document["stages"]["FRONTIER"]["details"]["snapshot_inputs"] == shared_inputs.diagnostics()
    assert not list((tmp_path / "diagnostic").glob("**/latest_success.json"))
    assert not list((tmp_path / "diagnostic").glob("**/ISNAP-*.json"))


def test_blocked_coordinator_callback_cannot_hold_completed_head_workers():
    keys = [canonical_s3_key(DATASET, symbol="AUDUSD", date="2026-09-04",
                             part=f"part-{index:03d}.jsonl") for index in range(87)]
    client = MemoryHeadClient(keys)
    source = S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                  profile="offline-profile", client=client)
    listed = tuple(source.discover_dataset_objects(DATASET))
    callback_entered = threading.Event()
    release_callback = threading.Event()
    result = []

    def delayed_persistence(counts):
        if counts["queued"] == 32 and counts["completed"] == 0:
            callback_entered.set()
            assert release_callback.wait(timeout=3)

    def run():
        result.append(source.head_bound_objects(
            DATASET, listed, expected_schema_version=current_schema(DATASET),
            progress_callback=delayed_persistence))

    coordinator = threading.Thread(target=run, daemon=True)
    coordinator.start()
    try:
        assert callback_entered.wait(timeout=2)
        deadline = time.monotonic() + 2
        while source.performance_snapshot()["head_seconds_inclusive"] == 0 \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        assert source.performance_snapshot()["head_seconds_inclusive"] > 0
        assert source.performance_snapshot()["head_calls"] >= 16
    finally:
        release_callback.set()
        coordinator.join(timeout=3)
    assert not coordinator.is_alive()
    assert len(result[0]) == 87
