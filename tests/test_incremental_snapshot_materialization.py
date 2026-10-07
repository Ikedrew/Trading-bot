"""Stage B Wave 2: object-level incremental snapshot materialization.

Proves that a new governed snapshot after a small evidence delta reuses verified
immutable object authority for unchanged datasets and re-reads only changed
objects, while remaining byte-for-byte equivalent to a clean full freeze.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json

import pytest

from core.production_data_contract import current_schema, s3_base_prefix
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    InvestigationSnapshotError,
    SnapshotBoundDatasetReader,
    classify_dataset_objects,
    freeze_investigation_snapshot,
    freeze_investigation_snapshot_incremental,
    load_investigation_snapshot_id,
)
from research_engine.v10.continuous.frontier_coordinator import (
    SNAPSHOT_READY,
    FrontierStateStore,
    run_frontier_snapshot_cycle,
)


START = "2026-09-25"
END = "2026-09-25"


def _key(dataset: str, day: str = "2026-09-25", part: str = "part-000.jsonl") -> str:
    return (
        f"{s3_base_prefix(dataset)}/schema_version={current_schema(dataset)}"
        f"/symbol=EURUSD/date={day}/{part}"
    )


def _body(dataset: str, marker: str = "base") -> str:
    return json.dumps({
        "schema_version": current_schema(dataset),
        "dataset": dataset,
        "marker": marker,
        "timestamp_utc": "2026-09-25T12:00:00Z",
    }, sort_keys=True) + "\n"


def _objects() -> dict[str, str]:
    return {_key(dataset): _body(dataset) for dataset in BOUND_DATASETS}


class MemoryS3:
    def __init__(self, objects: dict[str, str], versions: dict[str, str] | None = None):
        self.objects = dict(objects)
        self.versions = dict(versions or {})
        self.get_calls = 0
        self.head_calls = 0

    def list_objects_v2(self, **kwargs):
        prefix = str(kwargs.get("Prefix") or "")
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": key,
                    "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
                    "Size": len(body.encode()),
                    "LastModified": "2026-09-25T13:00:00Z",
                }
                for key, body in sorted(self.objects.items()) if key.startswith(prefix)
            ],
        }

    def get_object(self, **kwargs):
        self.get_calls += 1
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]

        class Body:
            def __init__(self):
                self.remaining = body.encode()

            def read(self, amount=-1):
                if not self.remaining:
                    return b""
                size = len(self.remaining) if amount < 0 else min(amount, 17)
                chunk, self.remaining = self.remaining[:size], self.remaining[size:]
                return chunk

        response = {
            "Body": Body(),
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-25T13:00:00Z",
        }
        if key in self.versions:
            response["VersionId"] = self.versions[key]
        return response

    def head_object(self, **kwargs):
        self.head_calls += 1
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]
        response = {
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-25T13:00:00Z",
        }
        if key in self.versions:
            response["VersionId"] = self.versions[key]
        return response


def _source(fake: MemoryS3) -> S3ResearchDataSource:
    return S3ResearchDataSource(bucket="incremental-test", client=fake)


def _full_freeze(fake: MemoryS3):
    return freeze_investigation_snapshot(
        start_date=START, end_date=END, source=_source(fake))


def _incremental_freeze(fake: MemoryS3, predecessor):
    return freeze_investigation_snapshot_incremental(
        predecessor=predecessor, start_date=START, end_date=END, source=_source(fake))


# Classification unit tests

def test_same_object_identity_requires_exact_match():
    from research_engine.v10.investigation_snapshot import (
        BoundObject, _same_object_identity,
    )

    prior = BoundObject(
        identifier="k", etag="e1", size=10, last_modified="2026-09-25T13:00:00Z",
        version_id="v1", content_sha256="a" * 64, byte_size=10, row_count=1,
    )
    same = {"identifier": "k", "etag": "e1", "size": 10,
            "last_modified": "2026-09-25T13:00:00Z", "version_id": "v1"}
    assert _same_object_identity(prior, same) is True
    assert _same_object_identity(prior, {**same, "etag": "e2"}) is False
    assert _same_object_identity(prior, {**same, "size": 11}) is False
    assert _same_object_identity(prior, {**same, "version_id": "v2"}) is False
    # Versioning-mode flip must fail closed (not same).
    assert _same_object_identity(prior, {**same, "version_id": None}) is False


def test_classify_dataset_objects_deterministic_rules():
    from research_engine.v10.investigation_snapshot import DatasetBinding

    def obj(key, etag, version):
        return {"identifier": key, "etag": etag, "size": 10,
                "last_modified": "2026-09-25T13:00:00Z", "version_id": version}

    def bound(key, etag, version):
        return {
            "identifier": key, "etag": etag, "size": 10,
            "last_modified": "2026-09-25T13:00:00Z", "version_id": version,
            "content_sha256": "a" * 64, "byte_size": 10, "row_count": 1,
        }

    from research_engine.v10.investigation_snapshot import BoundObject
    prior = DatasetBinding(
        dataset="trade_truth", requirement="REQUIRED", presence="PRESENT",
        schema_version=current_schema("trade_truth"), schema_generation=None,
        dataset_snapshot_id=None, dataset_snapshot_json=None,
        source_object_count=3, source_row_count=3,
        content_digest="a" * 64,
        objects=(BoundObject(**bound("A", "eA", "vA")),
                 BoundObject(**bound("B", "eB", "vB")),
                 BoundObject(**bound("C", "eC", "vC"))),
    )
    # A unchanged, C replaced, D added, B removed.
    current = [obj("A", "eA", "vA"), obj("C", "eC2", "vC"), obj("D", "eD", "vD")]
    result = classify_dataset_objects(prior, current)
    assert set(result["unchanged"]) == {"A"}
    assert set(result["added"]) == {"D"}
    assert set(result["replaced"]) == {"C"}
    assert set(result["removed"]) == {"B"}
    assert result["fully_unchanged"] is False



# Closed-roster byte-size identity (legacy frontier record field names)

def test_bound_object_from_metadata_preserves_legacy_frontier_byte_fields():
    from research_engine.v10.investigation_snapshot import BoundObject

    legacy = {
        "identifier": "k",
        "etag": "e1",
        "byte_count": 1624,
        "listed_byte_count": 1624,
        "last_modified": "2026-09-25T13:00:00Z",
        "version_id": "v1",
        "content_sha256": "a" * 64,
        "row_count": 1,
    }
    bound = BoundObject.from_metadata(legacy)
    assert bound.byte_size == 1624
    assert bound.size == 1624


def test_same_object_identity_accepts_legacy_listed_byte_count():
    from research_engine.v10.investigation_snapshot import (
        BoundObject, _same_object_identity,
    )

    prior = BoundObject(
        identifier="k", etag="e1", size=10, last_modified="2026-09-25T13:00:00Z",
        version_id="v1", content_sha256="a" * 64, byte_size=10, row_count=1,
    )
    # The legacy frontier roster carries the listing size under listed_byte_count.
    legacy = {"identifier": "k", "etag": "e1", "listed_byte_count": 10,
              "last_modified": "2026-09-25T13:00:00Z", "version_id": "v1"}
    assert _same_object_identity(prior, legacy) is True
    # A genuinely different size must still fail closed.
    assert _same_object_identity(prior, {**legacy, "listed_byte_count": 11}) is False


def test_classify_dataset_objects_legacy_records_are_unchanged():
    from research_engine.v10.investigation_snapshot import (
        BoundObject, DatasetBinding, classify_dataset_objects,
    )

    def bound(key, etag, version):
        return BoundObject(
            identifier=key, etag=etag, size=10,
            last_modified="2026-09-25T13:00:00Z", version_id=version,
            content_sha256="a" * 64, byte_size=10, row_count=1,
        )

    prior = DatasetBinding(
        dataset="trade_truth", requirement="REQUIRED", presence="PRESENT",
        schema_version=current_schema("trade_truth"), schema_generation=None,
        dataset_snapshot_id=None, dataset_snapshot_json=None,
        source_object_count=2, source_row_count=2,
        content_digest="a" * 64,
        objects=(bound("A", "eA", "vA"), bound("B", "eB", "vB")),
    )
    # Legacy closed roster: byte size under listed_byte_count/byte_count only.
    current = [
        {"identifier": "A", "etag": "eA", "listed_byte_count": 10,
         "byte_count": 10, "last_modified": "2026-09-25T13:00:00Z",
         "version_id": "vA"},
        {"identifier": "B", "etag": "eB", "listed_byte_count": 10,
         "byte_count": 10, "last_modified": "2026-09-25T13:00:00Z",
         "version_id": "vB"},
    ]
    result = classify_dataset_objects(prior, current)
    assert set(result["unchanged"]) == {"A", "B"}
    assert result["replaced"] == ()
    assert result["added"] == ()
    assert result["removed"] == ()
    assert result["fully_unchanged"] is True

# Equivalence and reuse tests

def test_incremental_is_equivalent_to_full_freeze_for_replaced_and_added():
    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)

    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    fake.objects[_key("execution_results", part="part-001.jsonl")] = _body(
        "execution_results", marker="added")

    incremental = _incremental_freeze(fake, predecessor)
    clean = _full_freeze(fake)

    assert incremental.snapshot_id == clean.snapshot_id
    assert incremental.snapshot_fingerprint == clean.snapshot_fingerprint
    # Clock-only creation metadata is intentionally excluded from identity, so
    # compare the exact deterministic scientific identity of every binding.
    assert [b.dataset for b in incremental.datasets] == list(BOUND_DATASETS)
    for inc, cln in zip(incremental.datasets, clean.datasets):
        assert inc.content_digest == cln.content_digest
        assert inc.dataset_snapshot_id == cln.dataset_snapshot_id
        assert inc.source_row_count == cln.source_row_count
        assert inc.source_object_count == cln.source_object_count
        assert inc.objects == cln.objects


def test_incremental_reuses_unchanged_and_reads_only_changed():
    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)
    gets_after_predecessor = fake.get_calls

    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    _incremental_freeze(fake, predecessor)

    # Only the replacement object is read once; unchanged object authority is reused.
    assert fake.get_calls == gets_after_predecessor + 1
    assert fake.head_calls == 0


def test_removed_object_disappears_and_counts_update():
    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)

    fake.objects.pop(_key("market_context"))
    incremental = _incremental_freeze(fake, predecessor)
    clean = _full_freeze(fake)

    assert incremental.snapshot_id == clean.snapshot_id
    binding = {b.dataset: b for b in incremental.datasets}["market_context"]
    assert binding.presence == "ABSENT"
    assert binding.source_object_count == 0
    assert binding.source_row_count == 0


def test_no_stale_reused_object_survives_a_changed_identity():
    fake = MemoryS3(_objects(), versions={_key("trade_truth"): "v1"})
    predecessor = _full_freeze(fake)

    fake.versions[_key("trade_truth")] = "v2"
    incremental = _incremental_freeze(fake, predecessor)
    clean = _full_freeze(fake)

    assert incremental.snapshot_id == clean.snapshot_id
    trade = {b.dataset: b for b in incremental.datasets}["trade_truth"]
    assert trade.objects[0].version_id == "v2"


def test_incremental_equivalent_to_full_freeze_when_window_advances():
    # A shifted temporal window must not leak stale dates into reused bindings.
    fake = MemoryS3(_objects())
    predecessor = freeze_investigation_snapshot(
        start_date=START, end_date=END, source=_source(fake))

    new_end = "2026-09-26"  # no objects exist at this date; window merely widens
    incremental = freeze_investigation_snapshot_incremental(
        predecessor=predecessor, start_date=START, end_date=new_end,
        source=_source(fake))
    clean = freeze_investigation_snapshot(
        start_date=START, end_date=new_end, source=_source(fake))

    assert incremental.snapshot_id == clean.snapshot_id
    assert incremental.snapshot_fingerprint == clean.snapshot_fingerprint
    for inc, cln in zip(incremental.datasets, clean.datasets):
        assert inc.dataset_snapshot_id == cln.dataset_snapshot_id
        inc_child = json.loads(inc.dataset_snapshot_json)
        cln_child = json.loads(cln.dataset_snapshot_json)
        assert inc_child["temporal_bounds"] == cln_child["temporal_bounds"] == [START, new_end]
        assert inc_child["population_filters"] == cln_child["population_filters"]
        assert inc_child["content_digest"] == cln_child["content_digest"]


def test_downstream_evidence_identical_between_incremental_and_full():
    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)
    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    fake.objects[_key("execution_results", part="part-001.jsonl")] = _body(
        "execution_results", marker="added")

    incremental = _incremental_freeze(fake, predecessor)
    clean = _full_freeze(fake)

    source = _source(fake)
    inc_reader = SnapshotBoundDatasetReader(incremental, source)
    clean_reader = SnapshotBoundDatasetReader(clean, source)
    for dataset in BOUND_DATASETS:
        assert inc_reader.read_dataset(dataset) == clean_reader.read_dataset(dataset)


# Failure and fallback safety tests

def test_corrupt_predecessor_fails_closed(tmp_path):
    from research_engine.v10.investigation_snapshot import save_investigation_snapshot

    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)
    path = tmp_path / "manifests"
    save_investigation_snapshot(predecessor, path / f"{predecessor.snapshot_id}.json")
    (path / f"{predecessor.snapshot_id}.json").write_text(
        json.dumps({"snapshot_id": predecessor.snapshot_id, "bogus": True}))
    with pytest.raises(InvestigationSnapshotError):
        load_investigation_snapshot_id(
            predecessor.snapshot_id, manifest_directory=path)


def test_frontier_cycle_falls_back_when_predecessor_manifest_missing(tmp_path):
    fake = MemoryS3(_objects())
    first = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
    )
    assert first.status == SNAPSHOT_READY

    (tmp_path / "manifests" / f"{first.snapshot_id}.json").unlink()
    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    second = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
    )
    assert second.status == SNAPSHOT_READY
    assert second.snapshot_id != first.snapshot_id


def test_interrupted_incremental_build_does_not_move_pointers(tmp_path):
    fake = MemoryS3(_objects())
    first = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
    )
    first_state = FrontierStateStore(tmp_path / "state").load_latest_success()

    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")

    def broken_verifier(*args, **kwargs):
        raise RuntimeError("forced verification failure")

    failed = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
        verifier=broken_verifier,
    )
    assert FrontierStateStore(tmp_path / "state").load_latest_success() == first_state
    assert failed.snapshot_id == first.snapshot_id


def test_historical_snapshots_remain_immutable_after_incremental(tmp_path):
    from research_engine.v10.investigation_snapshot import save_investigation_snapshot

    fake = MemoryS3(_objects())
    predecessor = _full_freeze(fake)
    path = tmp_path / "manifests"
    save_investigation_snapshot(predecessor, path / f"{predecessor.snapshot_id}.json")
    original_bytes = (path / f"{predecessor.snapshot_id}.json").read_bytes()

    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    _incremental_freeze(fake, predecessor)

    assert (path / f"{predecessor.snapshot_id}.json").read_bytes() == original_bytes


def test_frontier_cycle_incremental_preserves_snapshot_identity(tmp_path):
    fake = MemoryS3(_objects())
    first = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
    )
    assert first.status == SNAPSHOT_READY

    fake.objects[_key("trade_truth")] = _body("trade_truth", marker="replaced")
    second = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests", as_of_date=date(2026, 10, 3),
    )
    assert second.status == SNAPSHOT_READY
    assert second.predecessor_snapshot_id == first.snapshot_id

    clean = freeze_investigation_snapshot(
        start_date=START, end_date=END, source=_source(fake))
    assert second.snapshot_id == clean.snapshot_id
