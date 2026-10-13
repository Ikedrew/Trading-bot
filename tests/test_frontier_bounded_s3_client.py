"""Offline checks for the S3 client used by FRONTIER current-version HEADs."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from botocore.exceptions import ReadTimeoutError

from core.production_data_contract import canonical_s3_list_prefix, current_schema
from research_engine.data_access import s3_source as s3s
from research_engine.v10.continuous.frontier_coordinator import (
    _same_frontier_object_identity,
)


DATASET = "trade_truth"
PREFIX = canonical_s3_list_prefix(DATASET, schema=current_schema(DATASET))


def _items(count):
    return [{"identifier": f"{PREFIX}part-{index:03d}.jsonl",
             "version_id": "v1", "etag": "etag-1", "size": 12,
             "last_modified": "2026-10-13T00:00:00+00:00"}
            for index in range(count)]


class HeadClient:
    def __init__(self, config, *, failed_key=None, changed_key=None):
        self.meta = SimpleNamespace(config=config)
        self.failed_key = failed_key
        self.changed_key = changed_key
        self.calls = []

    def head_object(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["Key"] == self.failed_key:
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        return {"VersionId": "v2" if kwargs["Key"] == self.changed_key else "v1",
                "ETag": '"etag-1"', "ContentLength": 12,
                "LastModified": "2026-10-13T00:00:00+00:00",
                "ResponseMetadata": {"RetryAttempts": 0}}


@pytest.mark.parametrize("profile", ["trading-bot-new", None])
def test_frontier_head_uses_bounded_shared_client_and_completes(monkeypatch, profile):
    built = []

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            assert (service, region_name) == ("s3", "eu-west-2")
            client = HeadClient(config)
            built.append(client)
            return client

    def session_factory(actual_profile, region):
        assert (actual_profile, region) == (profile, "eu-west-2")
        return Session()

    monkeypatch.setattr(s3s, "_build_session", session_factory)
    source = s3s.S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                      profile=profile)
    progress = []
    items = _items(16)
    headed = source.head_bound_objects(
        DATASET, items, expected_schema_version=current_schema(DATASET),
        progress_callback=progress.append)
    assert len(built) == 1
    config = built[0].meta.config
    assert (config.connect_timeout, config.read_timeout,
            config.retries["total_max_attempts"],
            config.max_pool_connections) == (3, 5, 1, 16)
    assert len(built[0].calls) == len(headed) == 16
    assert all(set(call) == {"Bucket", "Key"} for call in built[0].calls)
    assert progress[-1]["completed"] == progress[-1]["successful"] == 16
    assert progress[-1]["failed"] == progress[-1]["retried"] == 0
    assert source.performance["get_calls"] == 0


def test_head_timeout_fails_closed_with_no_partial_authority(monkeypatch):
    items = _items(4)
    built = []

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            client = HeadClient(config, failed_key=items[1]["identifier"])
            built.append(client)
            return client

    monkeypatch.setattr(s3s, "_build_session", lambda profile, region: Session())
    source = s3s.S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                      profile="trading-bot-new")
    progress = []
    with pytest.raises(s3s.ResearchDataSourceError):
        source.head_bound_objects(
            DATASET, items, expected_schema_version=current_schema(DATASET),
            progress_callback=progress.append)
    called_keys = [call["Key"] for call in built[0].calls]
    assert 1 <= len(called_keys) <= 4
    assert called_keys.count(items[1]["identifier"]) == 1
    assert len(called_keys) == len(set(called_keys))  # no application-level repeat
    assert progress[-1]["failed"] == 1
    assert source._headed_objects == {}
    assert source.performance["get_calls"] == 0


def test_changed_current_version_cannot_match_prior_identity(monkeypatch):
    items = _items(1)

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            return HeadClient(config, changed_key=items[0]["identifier"])

    monkeypatch.setattr(s3s, "_build_session", lambda profile, region: Session())
    source = s3s.S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                      profile="trading-bot-new")
    current, = source.head_bound_objects(
        DATASET, items, expected_schema_version=current_schema(DATASET))
    assert current["version_id"] == "v2"
    assert not _same_frontier_object_identity(items[0], current)


def test_botocore_timeout_has_one_total_attempt(monkeypatch):
    """Intercept transport before network I/O; exercise botocore retry logic."""
    import boto3

    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    sends = []

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            client = boto3.Session(
                aws_access_key_id="offline-access", aws_secret_access_key="offline-secret",
                region_name=region_name).client(
                    service, region_name=region_name, config=config,
                    endpoint_url="https://example.invalid")

            def fail_before_network(request):
                sends.append(request)
                raise ReadTimeoutError(endpoint_url="https://example.invalid")

            client._endpoint.http_session.send = fail_before_network
            return client

    monkeypatch.setattr(s3s, "_build_session", lambda profile, region: Session())
    source = s3s.S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                      profile="trading-bot-new")
    with pytest.raises(s3s.ResearchDataSourceError) as caught:
        source.head_bound_objects(
            DATASET, _items(1), expected_schema_version=current_schema(DATASET))
    assert len(sends) == 1, str(caught.value)
    assert source._headed_objects == {}


def test_sixteen_workers_enter_head_without_holding_source_counter_lock(monkeypatch):
    barrier = threading.Barrier(16, timeout=3)

    class Client(HeadClient):
        def head_object(self, **kwargs):
            barrier.wait()
            return super().head_object(**kwargs)

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            return Client(config)

    monkeypatch.setattr(s3s, "_build_session", lambda profile, region: Session())
    source = s3s.S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                      profile="trading-bot-new")
    events = []

    def progress(counts):
        source.performance_snapshot()  # Mirrors FRONTIER's progress callback.
        events.append(counts)

    headed = source.head_bound_objects(
        DATASET, _items(16), expected_schema_version=current_schema(DATASET),
        progress_callback=progress)
    assert len(headed) == 16
    assert events[-1]["completed"] == events[-1]["successful"] == 16
