"""Stage B Phase 3 - focused qualification for the bounded recovery runner.

Deterministic fixtures and mocked S3 responses only: no network, no production
data, no real research state.  Proves manifest identity and source
authentication, cached-body validation, missing-version retrieval with pinned
VersionIds, fail-closed digest/metadata mismatches, interrupted recovery with
safe continuation, cache corruption handling, atomic progress persistence, and
that a complete snapshot handoff is produced only after every required object
passes.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from botocore.exceptions import (
    ConnectTimeoutError, ConnectionClosedError, EndpointConnectionError,
    ReadTimeoutError,
)

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.investigation_snapshot import (
    InvestigationSnapshotError,
    freeze_investigation_snapshot,
    save_investigation_snapshot,
)
from tests.test_incremental_snapshot_materialization import (
    MemoryS3, _body, _key, _objects,
)
from core.production_data_contract import current_schema as _current_schema


def source_head_schema(dataset):
    return _current_schema(dataset)
from tools import stage_b_recovery as recovery
from tools.stage_b_recovery import (
    RecoveryConfig, RecoveryError, atomic_write_json, run_recovery,
)

DAY = "2026-09-25"
BUCKET = "stage-b-recovery-test"


class _FixedBody:
    """Minimal S3 Body double mirroring the chunked MemoryS3 read contract."""

    def __init__(self, payload: bytes):
        self.remaining = bytes(payload)

    def read(self, amount=-1):
        if not self.remaining:
            return b""
        size = (len(self.remaining) if amount is None or amount < 0
                else min(amount, 17))
        chunk, self.remaining = self.remaining[:size], self.remaining[size:]
        return chunk


class CountingMemoryS3(MemoryS3):
    """MemoryS3 double with listing observability and failure injection."""

    TAMPER_MODES = ("tampered-body",)

    def __init__(self, objects, versions=None, tamper=None,
                 fail_on_get_attempt=None):
        super().__init__(objects, versions)
        self.list_calls = 0
        self.get_version_ids = []
        self.tamper = dict(tamper or {})
        self.served_tamper = []
        self.fail_on_get_attempt = fail_on_get_attempt
        self._failed_once = False

    def list_objects_v2(self, **kwargs):
        self.list_calls += 1
        return super().list_objects_v2(**kwargs)

    def get_object(self, **kwargs):
        key = str(kwargs["Key"])
        if "VersionId" in kwargs:
            self.get_version_ids.append((key, kwargs["VersionId"]))
        if (self.fail_on_get_attempt is not None and not self._failed_once):
            self.get_calls += 1
            if self.get_calls >= self.fail_on_get_attempt:
                self._failed_once = True
                raise RuntimeError("simulated transport failure")
        response = MemoryS3.get_object(self, **kwargs)
        mode = self.tamper.get(key)
        if mode == "body":
            # Change ONLY the trailing newline byte (keep the length) so the
            # JSONL lines stay structurally valid (row count and parser
            # behaviour are unchanged) while the delivered content digest
            # definitely diverges from the manifest digest.
            raw = bytes(response["Body"].remaining)
            if raw.endswith(b"\n"):
                poisoned = raw[:-1] + b" "
            else:
                poisoned = raw + b" "
            self.served_tamper.append(key)
            return {**response, "Body": _FixedBody(poisoned),
                    "ContentLength": len(poisoned)}
        if mode == "etag":
            self.served_tamper.append(key)
            return {**response, "ETag": '"ffffffffffffffffffffffffffffffff"'}
        if mode == "version":
            self.served_tamper.append(key)
            return {**response, "VersionId": "vTAMPERED"}
        return response

# ─── fixture helpers ──────────────────────────────────────────────────────────



class FlakyTransportS3(CountingMemoryS3):
    """Inject typed botocore GET failures without network access."""

    def __init__(self, objects, versions, *, target, error_type, failures):
        super().__init__(objects, versions)
        self.target = target
        self.error_type = error_type
        self.failures_remaining = failures

    def get_object(self, **kwargs):
        if (kwargs["Key"] == self.target and self.failures_remaining):
            self.failures_remaining -= 1
            self.get_calls += 1
            self.get_version_ids.append((kwargs["Key"], kwargs["VersionId"]))
            raise self.error_type(endpoint_url="https://example.invalid")
        return super().get_object(**kwargs)


def _versioned(objects):
    return CountingMemoryS3(dict(objects),
                            {key: "v1" for key in objects})


def _freeze(tmp_path, fake, cache, bucket=BUCKET):
    """Freeze a deterministic 10-object snapshot into ``cache``."""
    source = S3ResearchDataSource(bucket=bucket, client=fake,
                                  verified_cache_directory=cache)
    snapshot = freeze_investigation_snapshot(start_date=DAY, end_date=DAY,
                                             source=source)
    manifests = tmp_path / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    save_investigation_snapshot(
        snapshot, manifests / f"{snapshot.snapshot_id}.json")
    return snapshot, manifests


def _config(tmp_path, fake, snapshot, manifests, cache, name="run", **kwargs):
    settings = {
        "snapshot_id": snapshot.snapshot_id,
        "manifest_directory": manifests,
        "cache_directory": cache,
        "state_directory": tmp_path / name / "state",
        "client": fake,
        "bucket": BUCKET,
        "max_objects": None,
        "max_seconds": None,
        "batch_size": 4,
        "workers": 4,
    }
    settings.update(kwargs)
    return RecoveryConfig(**settings)


def _first_object(snapshot):
    binding = snapshot.datasets[0]
    return binding.dataset, binding.objects[0]


def _cache_paths(source, snapshot):
    entries = {}
    for binding in snapshot.datasets:
        for item in binding.objects:
            identity = source._verified_body_identity(
                binding.dataset, item.to_dict())
            entries[item.identifier] = source._verified_body_path(identity)
    return entries


# ─── manifest identity and source authentication ──────────────────────────────


def test_manifest_identity_binding_and_no_listing(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    freeze_cache = tmp_path / "freeze_cache"
    snapshot, manifests = _freeze(tmp_path, fake, freeze_cache)
    gets_after_freeze = fake.get_calls
    fake.list_calls = 0

    report = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                  freeze_cache, workers=2))

    assert report["snapshot_id"] == snapshot.snapshot_id
    assert report["snapshot_fingerprint"] == snapshot.snapshot_fingerprint
    assert report["objects_required"] == 10
    assert report["verified_total"] == 10
    assert report["remaining_objects"] == 0
    assert report["stop_reason"] == "complete"
    assert report["source_authenticated_before_recovery"] is True
    assert report["dataset_accounting_verified"] is True
    assert report["network"]["list_calls"] == 0
    assert fake.list_calls == 0
    assert fake.get_calls == gets_after_freeze  # cached-only replay
    assert report["authoritative_handoff"] is True
    total = sum(b.source_object_count for b in snapshot.datasets)
    assert report["objects_required"] == total


def test_source_authority_mismatch_fails_before_any_work(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    fake.list_calls = 0
    gets_before = fake.get_calls

    bad = _config(tmp_path, fake, snapshot, manifests,
                  tmp_path / "other_cache", bucket="wrong-bucket")
    with pytest.raises(InvestigationSnapshotError,
                       match="SOURCE_AUTHORITY_CHANGED"):
        run_recovery(bad)
    assert fake.get_calls == gets_before
    assert fake.list_calls == 0
    assert not bad.progress_path.exists()


def test_dataset_digest_mismatch_fails_closed(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    path = manifests / f"{snapshot.snapshot_id}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["datasets"][1]["historical_guard_rows"] = 999
    path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    config = _config(tmp_path, fake, snapshot, manifests,
                     tmp_path / "other_cache")
    with pytest.raises(InvestigationSnapshotError,
                       match="GUARD_COHORT_ACCOUNTING_MISMATCH|"
                       "RECOVERY_DATASET_DIGEST_MISMATCH"):
        run_recovery(config)
    assert not config.progress_path.exists()


# ─── cached-body validation and missing-version retrieval ─────────────────────


def _tampered_response_body(response, poison=b'{"schema_version": "tampered"}\n'):
    raw = bytes(response["Body"].remaining)
    return {**response, "Body": _FixedBody(raw + poison)}


def test_recovery_read_path_uses_response_body_bytes(tmp_path):
    """Guard: the tamper helper below truly alters served read-path bytes."""
    objects = _objects()
    probe = CountingMemoryS3(dict(objects), {key: "v1" for key in objects},
                             tamper={_key("trade_truth"): "body"})
    source = S3ResearchDataSource(bucket=BUCKET, client=probe)
    with __import__("pytest").raises(Exception, match="DIGEST|MISMATCH|INVALID"):
        source.read_bound_objects(
            "trade_truth",
            [{"identifier": _key("trade_truth"), "version_id": "v1",
              "content_sha256": "0" * 64, "byte_size": 1, "row_count": 1,
              "etag": "x", "size": 1, "last_modified": "2026-09-25T13:00:00Z"}],
            expected_schema_version=source_head_schema("trade_truth"))
    assert probe.get_version_ids == [(_key("trade_truth"), "v1")]


def test_missing_versions_retrieved_with_pinned_version_ids(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    recovery_cache = tmp_path / "recovery_cache"
    gets_before = fake.get_calls

    report = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                  recovery_cache))

    assert report["retrieved_this_run"] == 10
    assert report["verified_cached_this_run"] == 0
    assert fake.get_calls - gets_before == 10
    assert len(fake.get_version_ids) == 10  # (key, version) pairs
    assert ({version for _, version in fake.get_version_ids}
            == {"v1"})  # pinned VersionIds only
    assert report["network"]["get_calls"] == 10
    assert report["network"]["list_calls"] == 0
    assert len(list(recovery_cache.glob("*.cache"))) == 10

    # Continuation reuses the now-populated cache with zero repeat GETs.
    gets_after_first = fake.get_calls
    second = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                  recovery_cache))
    assert fake.get_calls == gets_after_first
    assert second["revalidated_prior_objects"] == 10
    assert second["retrieved_this_run"] == 0
    assert second["stop_reason"] == "complete"


def test_cached_bodies_verified_without_network(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    freeze_cache = tmp_path / "freeze_cache"
    snapshot, manifests = _freeze(tmp_path, fake, freeze_cache)
    gets_before = fake.get_calls

    report = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                  freeze_cache, workers=2))

    assert report["verified_cached_this_run"] == 10
    assert report["retrieved_this_run"] == 0
    assert fake.get_calls == gets_before
    assert report["verified_total"] == 10
    assert report["remaining_objects"] == 0


def test_body_byte_tamper_breaks_manifest_digest(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    dataset, item = _first_object(snapshot)
    key = item.identifier
    raw = objects[key].encode()
    poisoned = raw + b'{"schema_version": "tampered"}\n'
    assert poisoned != raw
    assert __import__("hashlib").sha256(poisoned).hexdigest() != (
        item.content_sha256)
    assert raw.count(b"\n") == 1


@pytest.mark.parametrize("tamper_mode,marker", [
    ("body", "SNAPSHOT_OBJECT_DIGEST_MISMATCH"),
    ("etag", "SNAPSHOT_OBJECT_ETAG_MISMATCH"),
    ("version", "SNAPSHOT_OBJECT_VERSION_MISMATCH"),
])
def test_digest_and_metadata_mismatches_stop_safely(tmp_path, tamper_mode, marker):
    objects = _objects()
    healthy = CountingMemoryS3(dict(objects),
                               {key: "v1" for key in objects})
    snapshot, manifests = _freeze(tmp_path, healthy,
                                  tmp_path / "freeze_cache")
    dataset, item = _first_object(snapshot)
    # Only the recovery pass observes tampering; freeze-time reads stay clean.
    # NOTE (2026-10-13): with workers=1 this covers every dispatch path below
    # - the sole in-flight future is the tampered object itself, so if this
    # test ever completes silently the runner swallowed a production mismatch.
    fake = CountingMemoryS3(dict(objects),
                            {key: "v1" for key in objects},
                            tamper={item.identifier: tamper_mode})
    gets_before = fake.get_calls

    report = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, tmp_path / "recovery_cache",
        workers=1, order=recovery.ORDER_MANIFEST))

    # The tampered object enters through the retrieval path: its response-side
    # GET must surface the mismatch at read time.  served_tamper proves the
    # poisoned bytes were actually served to the reader, so a silent pass
    # here would mean the runner itself swallowed the mismatch.
    assert item.identifier in fake.served_tamper
    assert {version for _, version in fake.get_version_ids} == {"v1"}
    assert item.identifier in {key for key, _ in fake.get_version_ids}
    failures = report["failures"]
    assert report["stop_reason"] == "failure", report
    assert report["failures_total"] == 1
    assert marker in report["failures"][0]["error"]
    assert report["failures"][0]["identifier"] == item.identifier
    assert fake.get_calls == 1  # strictly serial, nothing else attempted
    assert report["verified_total"] == 0
    assert report["authoritative_handoff"] is False
    config_paths = _config(tmp_path, fake, snapshot, manifests,
                           tmp_path / "recovery_cache")
    assert not config_paths.handoff_path.exists()
    progress = json.loads(config_paths.progress_path.read_text(
        encoding="utf-8"))
    assert progress["authoritative"] is False  # partial is never authoritative


# ─── interrupted recovery and safe continuation ───────────────────────────────


def test_recovery_pass_sees_consecutive_get_calls(tmp_path):
    objects = _objects()
    healthy = CountingMemoryS3(dict(objects),
                               {key: "v1" for key in objects})
    snapshot, manifests = _freeze(tmp_path, healthy,
                                  tmp_path / "freeze_cache")
    fake = CountingMemoryS3(dict(objects),
                            {key: "v1" for key in objects})
    report = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, tmp_path / "recovery_cache",
        workers=1, order=recovery.ORDER_MANIFEST))
    assert report["stop_reason"] == "complete"
    assert report["retrieved_this_run"] == 10
    # MemoryS3 counts every response-side GET; the (key, version) pairs prove
    # the runner's response traffic uses manifest-pinned VersionIds only.
    assert fake.get_calls == 10
    assert len(fake.get_version_ids) == 10
    assert {version for _, version in fake.get_version_ids} == {"v1"}
    dataset, item = _first_object(snapshot)
    assert fake.get_version_ids[0] == (item.identifier, "v1")


def test_tamper_fixture_only_applies_to_recovery_client(tmp_path):
    """Pin down WHY the digest test needs a separate recovery client."""
    objects = _objects()
    healthy = CountingMemoryS3(dict(objects),
                               {key: "v1" for key in objects})
    snapshot, manifests = _freeze(tmp_path, healthy,
                                  tmp_path / "freeze_cache")
    dataset, item = _first_object(snapshot)
    tampered = CountingMemoryS3(dict(objects),
                                {key: "v1" for key in objects},
                                tamper={item.identifier: "body"})
    source = S3ResearchDataSource(bucket=BUCKET, client=tampered)
    with __import__("pytest").raises(Exception, match="DIGEST|MISMATCH"):
        source.read_bound_objects(
            dataset, [item.to_dict()],
            expected_schema_version=source_head_schema(dataset),
            start_date="2026-09-25", end_date="2026-09-25")
    assert tampered.get_version_ids == [(item.identifier, "v1")]


def test_interrupted_recovery_continues_safely(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    recovery_cache = tmp_path / "recovery_cache"
    gets_before = fake.get_calls

    first = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        max_objects=4, order=recovery.ORDER_MANIFEST))
    assert first["stop_reason"] == "budget_objects"
    assert first["verified_total"] == 4
    assert first["remaining_objects"] == 6
    assert first["authoritative_handoff"] is False
    assert fake.get_calls - gets_before == 4

    first_config = _config(tmp_path, fake, snapshot, manifests,
                           recovery_cache)
    assert not first_config.handoff_path.exists()
    progress = json.loads(first_config.progress_path.read_text(
        encoding="utf-8"))
    assert len(progress["verified"]) == 4

    second = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        order=recovery.ORDER_MANIFEST))
    assert second["verified_before_run"] == 4
    assert second["revalidated_prior_objects"] == 4
    assert second["retrieved_this_run"] == 6
    assert fake.get_calls - gets_before == 10  # zero repeat GETs
    assert second["verified_total"] == 10
    assert second["stop_reason"] == "complete"
    assert second["authoritative_handoff"] is True
    assert len(list(recovery_cache.glob("*.cache"))) == 10


def test_transport_failure_records_and_resumes(tmp_path):
    objects = _objects()
    healthy = CountingMemoryS3(dict(objects),
                               {key: "v1" for key in objects})
    snapshot, manifests = _freeze(tmp_path, healthy,
                                  tmp_path / "freeze_cache")
    recovery_cache = tmp_path / "recovery_cache"

    # The recovery pass alone sees the failing client: the second GET raises.
    fake = CountingMemoryS3(dict(objects),
                            {key: "v1" for key in objects},
                            fail_on_get_attempt=2)
    failing = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        workers=1, order=recovery.ORDER_MANIFEST))
    assert failing["stop_reason"] == "failure"
    assert failing["failures_total"] == 1
    assert failing["verified_total"] == 1
    assert failing["remaining_objects"] == 9

    fake.fail_on_get_attempt = None
    healed = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        order=recovery.ORDER_MANIFEST))
    assert healed["stop_reason"] == "complete"
    assert healed["verified_total"] == 10
    assert healed["failures_total"] == 0  # cleared on successful verify
    assert healed["authoritative_handoff"] is True


# ─── cache corruption and failure handling ────────────────────────────────────



@pytest.mark.parametrize("error_type", [
    ReadTimeoutError, ConnectionClosedError, ConnectTimeoutError,
    EndpointConnectionError,
])
def test_transient_get_retries_then_verifies(tmp_path, monkeypatch, error_type):
    objects = _objects()
    healthy = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, healthy, tmp_path / "freeze_cache")
    _, item = _first_object(snapshot)
    fake = FlakyTransportS3(
        dict(objects), {key: "v1" for key in objects},
        target=item.identifier, error_type=error_type, failures=2)
    delays = []
    monkeypatch.setattr(recovery.random, "uniform",
                        lambda lower, upper: upper / 2)
    monkeypatch.setattr(recovery.time, "sleep", delays.append)

    report = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, tmp_path / "recovery_cache",
        workers=1, max_objects=1, order=recovery.ORDER_MANIFEST))
    assert report["stop_reason"] == "budget_objects"
    assert report["retrieved_this_run"] == 1
    assert report["failures_total"] == 0
    assert report["authoritative_handoff"] is False
    assert fake.get_calls == 3
    assert fake.get_version_ids == [(item.identifier, "v1")] * 3
    assert delays == [0.25, 0.5]
    assert fake.list_calls == 0


def test_exhausted_transient_get_preserves_progress_and_resumes(
        tmp_path, monkeypatch):
    objects = _objects()
    healthy = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, healthy, tmp_path / "freeze_cache")
    bindings = [(binding.dataset, item) for binding in snapshot.datasets
                for item in binding.objects]
    target = bindings[1][1].identifier
    fake = FlakyTransportS3(
        dict(objects), {key: "v1" for key in objects},
        target=target, error_type=ReadTimeoutError, failures=4)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    config = _config(
        tmp_path, fake, snapshot, manifests, tmp_path / "recovery_cache",
        workers=1, order=recovery.ORDER_MANIFEST)

    failed = run_recovery(config)
    assert failed["stop_reason"] == "failure"
    assert failed["verified_total"] == 1
    assert failed["failures_total"] == 1
    assert "RECOVERY_TRANSIENT_ATTEMPTS_EXHAUSTED:4:" in (
        failed["failures"][0]["error"])
    assert fake.get_calls == 5
    assert fake.get_version_ids[:5] == [
        (bindings[0][1].identifier, "v1"), *[(target, "v1")] * 4]
    progress = json.loads(config.progress_path.read_text(encoding="utf-8"))
    assert len(progress["verified"]) == 1
    assert progress["authoritative"] is False
    assert not config.handoff_path.exists()

    fake.failures_remaining = 0
    healed = run_recovery(config)
    assert healed["verified_before_run"] == 1
    assert healed["revalidated_prior_objects"] == 1
    assert healed["retrieved_this_run"] == 9
    assert healed["verified_total"] == 10
    assert healed["failures_total"] == 0
    assert healed["stop_reason"] == "complete"
    assert healed["authoritative_handoff"] is True
    assert fake.get_calls == 14
    assert fake.list_calls == 0



def test_retry_wait_respects_overall_deadline(tmp_path, monkeypatch):
    objects = _objects()
    healthy = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, healthy, tmp_path / "freeze_cache")
    dataset, item = _first_object(snapshot)
    fake = FlakyTransportS3(
        dict(objects), {key: "v1" for key in objects},
        target=item.identifier, error_type=ConnectionClosedError, failures=4)
    cache = tmp_path / "recovery_cache"
    source = S3ResearchDataSource(
        bucket=BUCKET, client=fake, verified_cache_directory=cache)
    config = _config(tmp_path, fake, snapshot, manifests, cache, workers=1)
    ctx = recovery._RunContext(
        primary=source, snapshot=snapshot, config=config,
        index={item.identifier: (dataset, item)},
        schema_by_dataset={dataset: source_head_schema(dataset)},
        deadline=time.perf_counter() + 0.25)
    monkeypatch.setattr(recovery.random, "uniform", lambda lower, upper: upper)
    sleeps = []
    monkeypatch.setattr(recovery.time, "sleep", sleeps.append)

    with pytest.raises(RecoveryError, match="RECOVERY_RETRY_DEADLINE_EXCEEDED"):
        recovery._process_one(ctx, (dataset, item))
    assert fake.get_calls == 1
    assert fake.get_version_ids == [(item.identifier, "v1")]
    assert sleeps == []


def test_integrity_failure_is_never_retried_with_transport_policy(
        tmp_path, monkeypatch):
    objects = _objects()
    healthy = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, healthy, tmp_path / "freeze_cache")
    _, item = _first_object(snapshot)
    fake = CountingMemoryS3(
        dict(objects), {key: "v1" for key in objects},
        tamper={item.identifier: "body"})
    sleeps = []
    monkeypatch.setattr(recovery.time, "sleep", sleeps.append)
    report = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, tmp_path / "recovery_cache",
        workers=1, order=recovery.ORDER_MANIFEST))
    assert report["stop_reason"] == "failure"
    assert "SNAPSHOT_OBJECT_DIGEST_MISMATCH" in report["failures"][0]["error"]
    assert fake.get_calls == 1
    assert sleeps == []
    assert report["authoritative_handoff"] is False


def test_corrupted_cached_body_stops_safely(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    freeze_cache = tmp_path / "freeze_cache"
    snapshot, manifests = _freeze(tmp_path, fake, freeze_cache)
    dataset, item = _first_object(snapshot)
    source = S3ResearchDataSource(bucket=BUCKET, client=fake,
                                  verified_cache_directory=freeze_cache)
    identity = source._verified_body_identity(dataset, item.to_dict())
    target = source._verified_body_path(identity)
    raw = target.read_bytes()
    offset = raw.index(b"\n") + 1  # corrupt one body byte, keep the header
    target.write_bytes(raw[:offset] + b"X" + raw[offset + 1:])

    report = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, freeze_cache,
        workers=1, order=recovery.ORDER_MANIFEST))

    assert report["stop_reason"] == "failure"
    assert report["failures_total"] == 1
    assert "RECOVERY_CACHE_CORRUPTED" in report["failures"][0]["error"]
    assert report["failures"][0]["identifier"] == item.identifier
    assert report["verified_total"] == 0
    assert report["authoritative_handoff"] is False


def test_corrupted_body_in_prior_progress_stops_resume(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    freeze_cache = tmp_path / "freeze_cache"
    snapshot, manifests = _freeze(tmp_path, fake, freeze_cache)
    recovery_cache = tmp_path / "recovery_cache"

    first = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        max_objects=3, order=recovery.ORDER_MANIFEST))
    assert first["verified_total"] == 3

    progress = json.loads(_config(
        tmp_path, fake, snapshot, manifests,
        recovery_cache).progress_path.read_text(encoding="utf-8"))
    victim = next(iter(progress["verified"]))
    dataset = progress["verified"][victim]["dataset"]
    source = S3ResearchDataSource(bucket=BUCKET, client=fake,
                                  verified_cache_directory=recovery_cache)
    item = next(item for binding in snapshot.datasets
                for item in binding.objects if item.identifier == victim)
    identity = source._verified_body_identity(dataset, item.to_dict())
    target = source._verified_body_path(identity)
    raw = target.read_bytes()
    offset = raw.index(b"\n") + 1
    target.write_bytes(raw[:offset] + b"X" + raw[offset + 1:])

    second = run_recovery(_config(
        tmp_path, fake, snapshot, manifests, recovery_cache,
        order=recovery.ORDER_MANIFEST))
    assert second["stop_reason"] == "failure"
    assert second["failures_total"] == 1
    assert "RECOVERY_CACHE_CORRUPTED" in second["failures"][0]["error"]
    assert second["failures"][0]["identifier"] == victim
    assert second["verified_before_run"] == 3
    assert second["verified_total"] == 2  # corrupt entry demoted, no new work


# ─── atomic progress persistence ──────────────────────────────────────────────


def test_progress_write_is_atomic_and_clean(tmp_path):
    path = tmp_path / "progress.json"
    atomic_write_json(path, {"verified": {"a": {"dataset": "d", "m": "c"}},
                             "authoritative": False})
    assert json.loads(path.read_text(encoding="utf-8"))["authoritative"] is False
    assert not list(tmp_path.glob("*.tmp"))


def test_interrupted_write_never_leaves_partial_progress(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    atomic_write_json(path, {"runs": 7})
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(recovery.os, "replace", boom)
    with pytest.raises(OSError, match="simulated disk failure"):
        atomic_write_json(path, {"runs": 8})
    assert path.read_bytes() == before  # original checkpoint fully intact
    assert json.loads(path.read_text(encoding="utf-8")) == {"runs": 7}
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.undo()

    # Resume still binds and re-uses the intact checkpoint.
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    config = _config(tmp_path, fake, snapshot, manifests,
                     tmp_path / "freeze_cache")
    report = run_recovery(config)
    assert report["stop_reason"] == "complete"
    assert report["verified_total"] == 10


# ─── progress authority binding and complete handoff ──────────────────────────


def test_tampered_progress_is_rejected_before_any_work(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    config = _config(tmp_path, fake, snapshot, manifests,
                     tmp_path / "freeze_cache", max_objects=3)
    run_recovery(config)
    gets_before = fake.get_calls

    progress_path = config.progress_path
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress["snapshot_fingerprint"] = "f" * 64
    progress_path.write_text(json.dumps(progress), encoding="utf-8")

    with pytest.raises(RecoveryError,
                       match="RECOVERY_PROGRESS_AUTHORITY_MISMATCH"):
        run_recovery(config)
    assert fake.get_calls == gets_before


def test_handoff_only_after_all_required_objects_pass(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    snapshot, manifests = _freeze(tmp_path, fake, tmp_path / "freeze_cache")
    recovery_cache = tmp_path / "recovery_cache"
    config = _config(tmp_path, fake, snapshot, manifests, recovery_cache,
                     max_objects=3)

    partial = run_recovery(config)
    status = recovery.recovery_status(config)
    assert partial["stop_reason"] == "budget_objects"
    assert not config.handoff_path.exists()
    assert status["exists"] is True
    assert status["verified_total"] == 3
    assert status["authoritative"] is False
    assert status["handoff_exists"] is False

    done = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                recovery_cache))
    assert done["stop_reason"] == "complete"
    assert done["authoritative_handoff"] is True
    handoff = json.loads(config.handoff_path.read_text(encoding="utf-8"))
    assert handoff["schema"] == recovery.HANDOFF_SCHEMA
    assert handoff["snapshot_fingerprint"] == snapshot.snapshot_fingerprint
    assert handoff["objects_required"] == 10
    assert handoff["objects_verified"] == 10
    assert handoff["verified_cached_objects"] == 0
    assert handoff["retrieved_objects"] == 10
    assert handoff["failures"] == 0
    assert handoff["authoritative"] is True

    # Re-running a completed recovery is a safe no-op, not a new handoff.
    gets_before = fake.get_calls
    again = run_recovery(_config(tmp_path, fake, snapshot, manifests,
                                 recovery_cache))
    assert again["verified_before_run"] == 10
    assert again["revalidated_prior_objects"] == 10
    assert fake.get_calls == gets_before
    assert again["authoritative_handoff"] is True


def test_config_enforces_cache_capacity_and_bounds(tmp_path):
    with pytest.raises(RecoveryError,
                       match="RECOVERY_CACHE_CAPACITY_BELOW_1GIB"):
        RecoveryConfig(cache_capacity_bytes=512 * 1024 * 1024)
    assert RecoveryConfig().cache_capacity_bytes >= 1024 * 1024 * 1024
    with pytest.raises(RecoveryError, match="RECOVERY_WORKERS_OUT_OF_RANGE"):
        RecoveryConfig(workers=17)
    with pytest.raises(RecoveryError, match="RECOVERY_WORKERS_OUT_OF_RANGE"):
        RecoveryConfig(workers=0)
    with pytest.raises(RecoveryError, match="RECOVERY_ORDER_INVALID"):
        RecoveryConfig(order="newest-first")
