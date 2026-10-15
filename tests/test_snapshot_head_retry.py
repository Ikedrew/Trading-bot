"""Offline, snapshot-only retry contract for fresh authority HEADs."""
import time

import pytest
from botocore.exceptions import (
    ClientError, ConnectTimeoutError, ConnectionClosedError,
    EndpointConnectionError, ReadTimeoutError)

from research_engine.data_access.s3_source import (
    ResearchDataSourceError, S3ResearchDataSource)
from research_engine.v10.investigation_snapshot import (
    InvestigationSnapshotError, SnapshotBoundDatasetReader,
    _transient_head_error_category)
from tests.test_cold_start_cache_recovery import _freeze, _objects, _versioned


class SequencedHead:
    def __init__(self, delegate, key, *, failures=0, denied=False, pause=0.0):
        self.delegate = delegate
        self.key = key
        self.failures = failures
        self.denied = denied
        self.pause = pause
        self.target_calls = 0

    def head_object(self, **kwargs):
        if kwargs["Key"] == self.key:
            self.target_calls += 1
            if self.pause:
                time.sleep(self.pause)
            if self.denied:
                raise ClientError({"Error": {"Code": "AccessDenied",
                                             "Message": "offline denial"}}, "HeadObject")
            if self.target_calls <= self.failures:
                raise ReadTimeoutError(endpoint_url="https://offline.invalid")
        return self.delegate.head_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


@pytest.mark.parametrize("cause,category", [
    (ConnectTimeoutError(endpoint_url="https://offline.invalid"), "CONNECT_TIMEOUT"),
    (ReadTimeoutError(endpoint_url="https://offline.invalid"), "READ_TIMEOUT"),
    (ConnectionClosedError(endpoint_url="https://offline.invalid"), "CONNECTION_CLOSED"),
    (EndpointConnectionError(endpoint_url="https://offline.invalid"), "ENDPOINT_CONNECTION"),
    (TimeoutError("offline timeout"), "TIMEOUT"),
    (ConnectionError("offline connection"), "CONNECTION"),
])
def test_only_named_head_transport_categories_are_retryable(cause, category):
    wrapped = ResearchDataSourceError("head_object key='offline-key'")
    wrapped.__cause__ = cause
    assert _transient_head_error_category(wrapped) == category
    wrapped_get = ResearchDataSourceError("get_object key='offline-key'")
    wrapped_get.__cause__ = cause
    assert _transient_head_error_category(wrapped_get) is None


def fixture(tmp_path, *, failures=0, denied=False, pause=0.0):
    fake = _versioned(_objects())
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)
    binding = next(binding for binding in snapshot.datasets if binding.objects)
    item = binding.objects[0]
    client = SequencedHead(fake, item.identifier, failures=failures,
                           denied=denied, pause=pause)
    source = S3ResearchDataSource(bucket="incremental-test", client=client,
                                  verified_cache_directory=cache)
    return snapshot, source, fake, client, binding, item


def test_transient_head_recovers_after_two_failures(tmp_path, monkeypatch):
    snapshot, source, _, client, _, _ = fixture(tmp_path, failures=2)
    bounds = []

    def jitter(low, high):
        bounds.append((low, high))
        return 0.0
    monkeypatch.setattr("research_engine.v10.investigation_snapshot.random.uniform",
                        jitter)
    events = []
    SnapshotBoundDatasetReader(snapshot, source, progress_callback=events.append,
                               deadline_seconds=60)
    assert client.target_calls == 3
    assert source.performance_snapshot()["head_failures"] == 2
    assert source.performance_snapshot()["get_calls"] == 0
    assert events[-1]["objects_completed"] == events[-1]["total_objects"]
    assert bounds == [(0.0, 0.5), (0.0, 1.0)]


def test_transient_head_exhaustion_records_identity_and_original_cause(tmp_path, monkeypatch):
    snapshot, source, _, client, binding, item = fixture(tmp_path, failures=4)
    bounds = []

    def jitter(low, high):
        bounds.append((low, high))
        return 0.0
    monkeypatch.setattr("research_engine.v10.investigation_snapshot.random.uniform",
                        jitter)
    events = []
    with pytest.raises(InvestigationSnapshotError,
                       match="SNAPSHOT_HEAD_RETRIES_EXHAUSTED") as caught:
        SnapshotBoundDatasetReader(snapshot, source, progress_callback=events.append,
                                   deadline_seconds=60)
    assert client.target_calls == 4
    assert f"dataset={binding.dataset}" in str(caught.value)
    assert f"key={item.identifier}" in str(caught.value)
    assert "attempts=4:category=READ_TIMEOUT" in str(caught.value)
    assert isinstance(caught.value.__cause__, ResearchDataSourceError)
    assert isinstance(caught.value.__cause__.__cause__, ReadTimeoutError)
    assert events[-1]["head_retry_attempts"] == 4
    assert events[-1]["head_error_category"] == "READ_TIMEOUT"
    assert events[-1]["failed_object"] == item.identifier
    assert events[-1]["head_failures"] == 4
    assert bounds == [(0.0, 0.5), (0.0, 1.0), (0.0, 2.0)]


def test_transient_head_stops_at_snapshot_deadline(tmp_path):
    snapshot, source, _, client, _, item = fixture(tmp_path, failures=4, pause=0.2)
    events = []
    with pytest.raises(InvestigationSnapshotError,
                       match="SNAPSHOT_LOAD_DEADLINE_EXCEEDED"):
        SnapshotBoundDatasetReader(snapshot, source, progress_callback=events.append,
                                   deadline_seconds=0.1)
    assert client.target_calls == 1
    assert events[-2]["deadline_exceeded"] is True
    assert events[-1]["failed_object"] == item.identifier


def test_backoff_is_cut_short_by_snapshot_deadline(tmp_path, monkeypatch):
    snapshot, source, _, client, _, _ = fixture(tmp_path, failures=4)
    monkeypatch.setattr("research_engine.v10.investigation_snapshot.random.uniform",
                        lambda low, high: high)
    with pytest.raises(InvestigationSnapshotError,
                       match="SNAPSHOT_LOAD_DEADLINE_EXCEEDED"):
        SnapshotBoundDatasetReader(snapshot, source, deadline_seconds=0.05)
    assert client.target_calls == 1


@pytest.mark.parametrize("failure", ["authority", "permission"])
def test_nontransient_head_fails_immediately(tmp_path, failure):
    snapshot, source, fake, client, _, item = fixture(
        tmp_path, denied=failure == "permission")
    if failure == "authority":
        fake.versions[item.identifier] = "different-version"
    expected = ("VERIFIED_BODY_AUTHORITY_MISMATCH" if failure == "authority"
                else "DENIED")
    with pytest.raises(ResearchDataSourceError, match=expected):
        SnapshotBoundDatasetReader(snapshot, source, deadline_seconds=60)
    assert client.target_calls == 1
    assert source.performance_snapshot()["get_calls"] == 0


def test_cached_body_digest_failure_is_never_retried(tmp_path):
    snapshot, source, _, client, binding, item = fixture(tmp_path)
    identity = source._verified_body_identity(binding.dataset, item.to_dict())
    path = source._verified_body_path(identity)
    contents = path.read_bytes()
    assert contents.endswith(b"\n")
    path.write_bytes(contents[:-1] + b" ")
    with pytest.raises(ResearchDataSourceError,
                       match="SNAPSHOT_OBJECT_DIGEST_MISMATCH"):
        SnapshotBoundDatasetReader(snapshot, source, deadline_seconds=60)
    assert client.target_calls == 1
    assert source.performance_snapshot()["get_calls"] == 0
