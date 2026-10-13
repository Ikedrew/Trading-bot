"""Offline contracts for the bounded all-LIST then first-HEAD reproduction."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from core.production_data_contract import canonical_s3_key
from research_engine.data_access import s3_source as s3s
from scripts import probe_frontier_list_then_head as probe


def test_first_batch_uses_listing_identity_and_checkpoint_version():
    listed = [{"identifier": f"key-{i}", "etag": "e", "size": 12,
               "last_modified": "t"} for i in range(40)]
    prior = [{"s3_key": f"key-{i}", "version_id": "v1", "etag": "e",
              "listed_byte_count": 12, "last_modified": "t"} for i in range(40)]
    listed[4]["etag"] = "changed"
    batch, total = probe.first_revalidation_batch(tuple(listed), prior)
    assert total == 39
    assert len(batch) == 32
    assert all(item["identifier"] != "key-4" for item in batch)


def test_real_source_lists_all_datasets_before_first_head_batch(monkeypatch):
    stamp = "2026-09-04T03:51:39+00:00"
    keys = [canonical_s3_key("trade_truth", symbol="AUDUSD", date="2026-09-04",
                             part=f"part-{i:03d}.jsonl") for i in range(32)]
    prior = [{"s3_key": key, "version_id": "v1", "etag": "etag-1",
              "listed_byte_count": 12, "last_modified": stamp} for key in keys]
    calls = []

    class Client:
        def __init__(self, config):
            self.meta = SimpleNamespace(config=config)

        def list_objects_v2(self, **kwargs):
            calls.append(("LIST", kwargs["Prefix"]))
            contents = ([{"Key": key, "ETag": '"etag-1"', "Size": 12,
                          "LastModified": stamp} for key in keys]
                        if "trade_truth" in kwargs["Prefix"] else [])
            return {"Contents": contents, "IsTruncated": False}

        def head_object(self, **kwargs):
            calls.append(("HEAD", kwargs))
            return {"VersionId": "v1", "ETag": '"etag-1"',
                    "ContentLength": 12, "LastModified": stamp,
                    "ResponseMetadata": {"RetryAttempts": 0}}

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            assert (service, region_name) == ("s3", probe.REGION)
            return Client(config)

    monkeypatch.setattr(s3s, "_build_session", lambda profile, region: Session())
    monkeypatch.setattr(
        probe.FrontierAcquisitionCheckpointStore, "load",
        lambda self, **kwargs: {"trade_truth": {"objects": prior}})
    monkeypatch.setattr(probe.faulthandler, "dump_traceback_later",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(probe.faulthandler, "cancel_dump_traceback_later",
                        lambda: None)
    events = []
    monkeypatch.setattr(probe, "emit", events.append)
    assert probe.worker() == 0
    kinds = [kind for kind, _ in calls]
    assert kinds == ["LIST"] * len(probe.BOUND_DATASETS) + ["HEAD"] * 32
    assert all(set(kwargs) == {"Bucket", "Key"} for kind, kwargs in calls
               if kind == "HEAD")
    ready = next(event for event in events if event["event"] == "client_ready")
    assert (ready["connect_timeout_seconds"], ready["read_timeout_seconds"],
            ready["total_max_attempts"], ready["max_pool_connections"]) == (3, 5, 1, 16)
    completed = next(event for event in events
                     if event["event"] == "head_batch_completed")
    assert completed["objects"] == 32
    assert completed["source_io"]["head_calls"] == 32
    assert completed["source_io"]["get_calls"] == 0
    assert ready["client_identity"] == completed["client_identity"]
    assert next(event for event in events
                if event["event"] == "all_lists_completed")["list_calls"] == len(probe.BOUND_DATASETS)


def test_parent_deadline_kills_child_and_returns_partial_progress(monkeypatch, capsys):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(
            command, 60, output=b'{"event":"head_batch_ready"}\n',
            stderr=b'Thread stack at HEAD\n')

    monkeypatch.setattr(probe.subprocess, "run", timeout)
    assert probe.parent() == 124
    output = capsys.readouterr()
    assert '"event":"head_batch_ready"' in output.out
    assert json.loads(output.out.splitlines()[-1]) == {
        "event": "wall_timeout", "wall_timeout_seconds": 60}
    assert "Thread stack at HEAD" in output.err


def test_setup_failure_is_reported_without_starting_s3(monkeypatch, capsys):
    monkeypatch.setattr(probe, "worker", lambda: (_ for _ in ()).throw(
        ValueError("checkpoint unavailable")))
    monkeypatch.setattr(sys, "argv", ["probe_frontier_list_then_head", "--worker"])
    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "event": "failed", "stage": "setup", "error_type": "ValueError",
        "error": "checkpoint unavailable"}
