"""Offline contracts for the single-object, read-only host HEAD probe."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import probe_frontier_head as probe


ITEM = {"s3_key": "canonical/trade_truth/test.jsonl", "version_id": "version-1",
        "etag": "etag-1", "listed_byte_count": 12, "content_sha256": "a" * 64}


def test_one_version_specific_head_with_bounded_config_and_no_other_io():
    calls = []

    class Client:
        def __init__(self, config):
            self.meta = SimpleNamespace(config=config)

        def head_object(self, **kwargs):
            calls.append(kwargs)
            return {"VersionId": "version-1", "ETag": '"etag-1"',
                    "ContentLength": 12}

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            assert service == "s3" and region_name == probe.REGION
            return Client(config)

    def session_factory(profile, region):
        assert (profile, region) == (probe.PROFILE, probe.REGION)
        return Session()

    result = probe.probe_once(ITEM, session_factory=session_factory)
    assert result["status"] == "HEAD_MATCH"
    assert result["checks"] == {"version_id": True, "etag": True, "size": True}
    assert result["body_sha256_checked"] is False
    assert (result["connect_timeout_seconds"], result["read_timeout_seconds"],
            result["total_max_attempts"]) == (3, 5, 1)
    assert calls == [{"Bucket": probe.BUCKET, "Key": ITEM["s3_key"],
                      "VersionId": ITEM["version_id"]}]


def test_worker_reports_request_failure_without_retrying(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_checkpoint_object", lambda: ITEM)
    def fail(_item):
        raise TimeoutError("offline simulated timeout")
    monkeypatch.setattr(probe, "probe_once", fail)
    monkeypatch.setattr(sys, "argv", ["probe_frontier_head", "--worker"])
    assert probe.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "HEAD_ERROR", "error_type": "TimeoutError",
                      "aws_error_code": None}


def test_parent_kills_bounded_worker_on_wall_timeout(monkeypatch, capsys):
    def expire(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired(args[0], 20)
    monkeypatch.setattr(probe.subprocess, "run", expire)
    monkeypatch.setattr(sys, "argv", ["probe_frontier_head"])
    assert probe.main() == 124
    assert json.loads(capsys.readouterr().out)["status"] == "WALL_TIMEOUT"


@pytest.mark.parametrize(
    ("response", "expected_status", "expected_checks"),
    [
        ({"VersionId": "version-1", "ETag": '"etag-1"', "ContentLength": 12},
         "HEAD_MATCH", {"version_id": True, "etag": True, "size": True}),
        ({"VersionId": "version-2", "ETag": '"etag-1"', "ContentLength": 12},
         "HEAD_CHANGED", {"version_id": False, "etag": True, "size": True}),
        ({"VersionId": "version-1", "ETag": '"etag-2"', "ContentLength": 12},
         "HEAD_INCONSISTENT", {"version_id": True, "etag": False, "size": True}),
        ({"ETag": '"etag-1"', "ContentLength": 12},
         "HEAD_IDENTITY_UNCONFIRMED",
         {"version_id": False, "etag": True, "size": True}),
    ],
)
def test_current_head_omits_version_and_classifies_identity(
    response, expected_status, expected_checks,
):
    calls = []

    class Client:
        def __init__(self, config):
            self.meta = SimpleNamespace(config=config)

        def head_object(self, **kwargs):
            calls.append(kwargs)
            return response

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            assert service == "s3" and region_name == probe.REGION
            return Client(config)

    result = probe.probe_once(
        ITEM, current=True, session_factory=lambda profile, region: Session())
    assert result["status"] == expected_status
    assert result["mode"] == "current"
    assert result["checks"] == expected_checks
    assert (result["connect_timeout_seconds"], result["read_timeout_seconds"],
            result["total_max_attempts"]) == (3, 5, 1)
    assert calls == [{"Bucket": probe.BUCKET, "Key": ITEM["s3_key"]}]


def test_current_mode_uses_bounded_worker(monkeypatch, capsys):
    def run(args, **kwargs):
        assert args == [sys.executable, "-m", "scripts.probe_frontier_head",
                        "--worker", "--current"]
        assert kwargs["timeout"] == 20
        return SimpleNamespace(stdout='{"status":"HEAD_CHANGED"}\n', returncode=0)

    monkeypatch.setattr(probe.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["probe_frontier_head", "--current"])
    assert probe.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "HEAD_CHANGED"
