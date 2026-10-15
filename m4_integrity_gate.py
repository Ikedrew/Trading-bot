"""Fail-closed local evidence gate for Milestone 4; executes no questions.

The local client serves bytes from Milestone 3's verified cache and one explicit
simulated JSONL file. It never contacts or writes production S3. Production
snapshot authentication, object digest, schema, guard and row-count checks stay on.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOST = Path(__file__).resolve().parent
REPO = HOST.parent / "Trading-bot-stage-a-restored"
ROOT = HOST / "milestone4_integrity_gate"
CACHE = REPO / "data/research/continuous/verified_object_bodies"
ORIGINAL_MANIFESTS = (REPO / "reports/research_lab_archive" /
    "180297f6a58099f348d83d69e9b0e748bb6e7718ea5f68ddda996e17a8c9d26b" /
    "source/snapshot/manifests")
BASELINE_ID = "ISNAP-5FB0DD9FC4D808D6C23789CD"
os.environ["RESEARCH_AWS_PROFILE"] = "trading-bot-new"
sys.path.insert(0, str(REPO))

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.investigation_snapshot import (
    SnapshotBoundDatasetReader, authenticate_snapshot_inputs,
    freeze_investigation_snapshot_incremental, load_investigation_snapshot_id,
    save_investigation_snapshot, verify_investigation_snapshot_identity)
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierSelection, _compute_delta, _full_object_record)


def event(stage, **facts):
    ROOT.mkdir(exist_ok=True)
    row = {"at_utc": datetime.now(timezone.utc).isoformat(),
           "stage": stage, **facts}
    with (ROOT / "events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, default=str) + "\n")
    print(json.dumps(row, default=str), flush=True)


class LocalEvidenceClient:
    """Exact-byte local transport for a closed logical S3 object roster."""

    def __init__(self, cache_paths, metadata, simulated_path=None, simulated_key=None):
        self.cache_paths = cache_paths
        self.metadata = metadata
        self.simulated_path = simulated_path
        self.simulated_key = simulated_key
        self.heads = 0
        self.gets = 0

    def _meta(self, bucket, key):
        if bucket != "trading-bot-v10-data" or key not in self.metadata:
            raise RuntimeError("LOCAL_EVIDENCE_OBJECT_UNAVAILABLE:" + key)
        return self.metadata[key]

    def head_object(self, *, Bucket, Key):
        item = self._meta(Bucket, Key)
        self.heads += 1
        return {"VersionId": item["version_id"], "ETag": item["etag"],
                "ContentLength": item["size"], "LastModified": item["last_modified"]}

    def get_object(self, *, Bucket, Key, VersionId=None):
        item = self._meta(Bucket, Key)
        if VersionId is not None and VersionId != item["version_id"]:
            raise RuntimeError("LOCAL_EVIDENCE_VERSION_MISMATCH:" + Key)
        self.gets += 1
        if Key == self.simulated_key:
            body = self.simulated_path.open("rb")
        else:
            body = self.cache_paths[Key].open("rb")
            header = body.readline(65537)
            if len(header) > 65536:
                body.close()
                raise RuntimeError("LOCAL_CACHE_HEADER_TOO_LARGE:" + Key)
        return {**self.head_object(Bucket=Bucket, Key=Key), "Body": body}

    def __getattr__(self, name):
        raise RuntimeError("LOCAL_TRANSPORT_OPERATION_FORBIDDEN:" + name)


class LocalEvidenceSource(S3ResearchDataSource):
    def __init__(self, cache_paths, membership, simulated_path=None, simulated_key=None):
        metadata = {item["identifier"]: item for rows in membership.values() for item in rows}
        client = LocalEvidenceClient(cache_paths, metadata, simulated_path, simulated_key)
        super().__init__(bucket="trading-bot-v10-data", client=client,
                         verified_cache_directory=CACHE, verified_cache_max_bytes=0)
        self.membership = membership
        self.local_client = client
        self.local_lists = 0

    def discover_dataset_objects(self, dataset, *, symbol=None, start_date=None,
                                 end_date=None, **kwargs):
        self.local_lists += 1
        result = []
        for item in self.membership[dataset]:
            key = item["identifier"]
            if symbol is not None and f"symbol={symbol}/" not in key:
                continue
            if not self._in_range(key, start_date, end_date):
                continue
            result.append(dict(item))
        return tuple(result)


def indexed_cache(snapshot):
    source = S3ResearchDataSource(bucket="trading-bot-v10-data",
                                  verified_cache_directory=CACHE,
                                  verified_cache_max_bytes=0)
    paths = {}
    metadata = {}
    started = time.perf_counter()
    for binding in snapshot.datasets:
        for obj in binding.objects:
            item = obj.to_dict()
            identity = source._verified_body_identity(binding.dataset, item)
            path = source._verified_body_path(identity)
            paths[obj.identifier] = path
            metadata[obj.identifier] = item
            if len(paths) % 5000 == 0:
                event("CACHE_PATHS_DERIVED", objects=len(paths),
                      elapsed_seconds=round(time.perf_counter()-started, 3))
    return paths, metadata


def simulated_increment(source):
    # Preserve a real execution_context record's schema and field types.
    first = source.membership["execution_context"][0]
    path = source.cache_paths[first["identifier"]] if hasattr(source, "cache_paths") else None
    if path is None:
        raise RuntimeError("BASE_RECORD_CACHE_PATH_UNAVAILABLE")
    with path.open("rb") as handle:
        handle.readline()
        exemplar = json.loads(handle.readline())
    row = dict(exemplar)
    row.update({"symbol": "AUDUSD", "timestamp_utc": 1791547200.0,
                "entity_id": "AUDUSD_M4_SIM_20261009_120000",
                "correlation_id": "M4-SIM-20261009-EXECUTION-CONTEXT",
                "cycle_id": "M4-SIM-1"})
    if row.get("schema_version") != "execution_context_v1":
        raise RuntimeError("SIMULATED_ROW_SCHEMA_INVALID")
    key = ("supporting/execution_context/schema_version=execution_context_v1/"
           "symbol=AUDUSD/date=2026-10-09/part-m4-simulated-001.jsonl")
    data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    file = ROOT / "simulated_execution_context.jsonl"
    file.write_bytes(data)
    item = {"identifier": key, "version_id": "M4-SIMULATED-V1",
            "etag": hashlib.md5(data).hexdigest(), "size": len(data),
            "last_modified": "2026-10-09T12:00:00+00:00"}
    return key, file, item, hashlib.sha256(data).hexdigest()


def main():
    begin = time.perf_counter()
    manifests = ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    baseline_file = ORIGINAL_MANIFESTS / f"{BASELINE_ID}.json"
    local_baseline = manifests / baseline_file.name
    if not local_baseline.exists():
        shutil.copyfile(baseline_file, local_baseline)
    baseline = load_investigation_snapshot_id(BASELINE_ID, manifest_directory=manifests)
    paths, historical = indexed_cache(baseline)
    event("HISTORICAL_CACHE_PATHS_DERIVED", objects=len(paths),
          manifest_sha256=hashlib.sha256(local_baseline.read_bytes()).hexdigest())
    membership = {binding.dataset: [obj.to_dict() for obj in binding.objects]
                  for binding in baseline.datasets}
    base_source = LocalEvidenceSource(paths, membership)
    base_source.cache_paths = paths
    authenticate_snapshot_inputs(baseline, base_source)
    verify_investigation_snapshot_identity(BASELINE_ID, source=base_source,
        manifest_directory=manifests, fresh_observation=True)
    event("BASELINE_AUTHORITY_VERIFIED", local_heads=base_source.local_client.heads,
          local_lists=base_source.local_lists)
    base_reader = SnapshotBoundDatasetReader(
        baseline, source=base_source, immutable=True,
        deadline_seconds=1200, progress_interval_seconds=15,
        progress_interval_objects=500,
        progress_callback=lambda row: event("BASELINE_READER_PROGRESS", **row))
    event("BASELINE_READER_VERIFIED", objects=len(paths),
          rows_by_dataset=base_reader.reads_by_dataset,
          local_gets=base_source.local_client.gets,
          elapsed_seconds=round(time.perf_counter()-begin, 3))

    key, file, listed, digest = simulated_increment(base_source)
    simulated_membership = {name: list(rows) for name, rows in membership.items()}
    simulated_membership["execution_context"].append(listed)
    successor_source = LocalEvidenceSource(paths, simulated_membership, file, key)
    successor_source.cache_paths = paths
    rows = successor_source.read_objects_for_freeze(
        "execution_context", (listed,), expected_schema_version="execution_context_v1",
        start_date="2026-09-07", end_date="2026-10-09")
    actual = successor_source.object_metadata("execution_context")
    if len(rows) != 1 or len(actual) != 1 or actual[0]["content_sha256"] != digest:
        raise RuntimeError("SIMULATED_INCREMENT_VERIFICATION_FAILED")
    simulated_membership["execution_context"][-1] = dict(actual[0])
    event("SIMULATED_OBJECT_VERIFIED", key=key, content_sha256=digest,
          metadata=actual[0], rows=len(rows))

    successor = freeze_investigation_snapshot_incremental(
        predecessor=baseline, start_date=baseline.start_date,
        end_date=baseline.end_date, source=successor_source,
        object_membership=simulated_membership)
    if successor.snapshot_id == baseline.snapshot_id:
        raise RuntimeError("SUCCESSOR_IDENTITY_NOT_DISTINCT")
    save_investigation_snapshot(successor, manifests / f"{successor.snapshot_id}.json")
    authenticate_snapshot_inputs(successor, successor_source)
    verify_investigation_snapshot_identity(successor.snapshot_id,
        source=successor_source, manifest_directory=manifests,
        fresh_observation=True)
    event("SUCCESSOR_AUTHORITY_VERIFIED", snapshot_id=successor.snapshot_id,
          predecessor_id=baseline.snapshot_id,
          source_authority=successor.source_authority,
          objects=sum(len(b.objects) for b in successor.datasets))
    successor_reader = SnapshotBoundDatasetReader(
        successor, source=successor_source, immutable=True,
        deadline_seconds=1200, progress_interval_seconds=15,
        progress_interval_objects=500,
        progress_callback=lambda row: event("SUCCESSOR_READER_PROGRESS", **row))
    event("SUCCESSOR_READER_VERIFIED", snapshot_id=successor.snapshot_id,
          rows_by_dataset=successor_reader.reads_by_dataset,
          local_gets=successor_source.local_client.gets)

    # Production delta planner; preserve the frozen baseline's exact membership.
    def selection(snapshot):
        members = {binding.dataset: {
            "classification": binding.requirement,
            "status": "READY" if binding.objects else "MISSING",
            "schema_version": binding.schema_version,
            "producer": None, "event_time_coverage": {"start": None, "end": None},
            "objects": [_full_object_record(binding.dataset, obj.to_dict())
                        for obj in binding.objects],
        } for binding in snapshot.datasets}
        return FrontierSelection(
            frontier_id="M4-LOCAL-" + snapshot.snapshot_id,
            selected_start_time=snapshot.start_date,
            selected_end_time=snapshot.end_date,
            dataset_membership=members,
            dataset_status={name: value["status"] for name, value in members.items()},
            stale_datasets=(), missing_optional_datasets=(),
            gap_diagnostics={}, pending_required_objects={},
            coherence_decision="FROZEN_MEMBERSHIP_WITH_LOCAL_SIMULATED_INCREMENT",
            predecessor_snapshot_id=baseline.snapshot_id)
    prior = {"frontier": selection(baseline).to_dict()}
    delta = _compute_delta(selection(successor), prior)
    if delta["changed_datasets"] != ("execution_context",):
        raise RuntimeError("UNEXPECTED_PRODUCTION_DELTA:" + str(delta["changed_datasets"]))
    event("HARD_GATE_PASS", baseline_id=baseline.snapshot_id,
          successor_id=successor.snapshot_id,
          changed_datasets=delta["changed_datasets"],
          added_objects=delta["new_object_count"],
          baseline_local_gets=base_source.local_client.gets,
          successor_local_gets=successor_source.local_client.gets,
          elapsed_seconds=round(time.perf_counter()-begin, 3))
    (ROOT / "gate_result.json").write_text(json.dumps({
        "status": "PASS", "baseline_id": baseline.snapshot_id,
        "successor_id": successor.snapshot_id,
        "changed_datasets": delta["changed_datasets"],
        "simulated_key": key, "simulated_sha256": digest}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        event("HARD_GATE_FAILED", error=f"{type(exc).__name__}:{exc}")
        raise
