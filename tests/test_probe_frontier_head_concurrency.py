"""Offline contracts for bounded, read-only concurrent HEAD diagnosis."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from scripts import probe_frontier_head_concurrency as probe


def sample_items():
    return [{"s3_key": f"canonical/trade_truth/{i}.jsonl", "version_id": "v1",
             "etag": "etag", "listed_byte_count": 12, "content_sha256": "a" * 64}
            for i in range(16)]


def test_serial_run_uses_four_spread_objects_and_parallel_runs_share_sixteen():
    sample = sample_items()
    assert probe.items_for_workers(1, sample) == [sample[i] for i in (0, 5, 10, 15)]
    assert probe.items_for_workers(4, sample) == sample
    assert probe.items_for_workers(16, sample) == sample


def test_shared_client_current_head_at_all_worker_counts():
    for workers in probe.WORKER_COUNTS:
        events = []
        calls = []
        clients = []

        class Client:
            def __init__(self, config):
                self.meta = SimpleNamespace(config=config)

            def head_object(self, **kwargs):
                calls.append(kwargs)
                return {"VersionId": "v1", "ETag": '"etag"', "ContentLength": 12}

        class Session:
            def get_credentials(self):
                return object()

            def client(self, service, *, region_name, config):
                assert (service, region_name) == ("s3", probe.REGION)
                client = Client(config)
                clients.append(client)
                return client

        def session_factory(profile, region):
            assert (profile, region) == (probe.PROFILE, probe.REGION)
            return Session()

        probe.run_worker(workers, sample_items(), session_factory=session_factory,
                         emit=events.append)
        assert len(clients) == 1
        config = clients[0].meta.config
        assert (config.connect_timeout, config.read_timeout,
                config.retries["total_max_attempts"]) == (3, 5, 1)
        assert len(calls) == 16
        assert all(set(call) == {"Bucket", "Key"} for call in calls)
        summary = probe.summarize(workers, "\n".join(map(json.dumps, events)), 1.0,
                                  deadline_hit=False, worker_exit_code=0)
        assert summary["requests_started"] == summary["requests_completed"] == 16
        assert summary["successful"] == 16
        assert summary["failed"] == summary["timed_out"] == 0
        assert all(result["status"] == "HEAD_MATCH"
                   for result in summary["request_results"])


def test_distinct_object_change_and_bounded_failure_are_reported():
    events = []

    class Client:
        def __init__(self, config):
            self.meta = SimpleNamespace(config=config)

        def head_object(self, **kwargs):
            key = kwargs["Key"]
            if key.endswith("/1.jsonl"):
                raise TimeoutError("offline simulated read timeout")
            version = "v2" if key.endswith("/2.jsonl") else "v1"
            return {"VersionId": version, "ETag": '"etag"', "ContentLength": 12}

    class Session:
        def get_credentials(self):
            return object()

        def client(self, service, *, region_name, config):
            return Client(config)

    probe.run_worker(4, sample_items(), session_factory=lambda p, r: Session(),
                     emit=events.append)
    summary = probe.summarize(4, "\n".join(map(json.dumps, events)), 1.0,
                              deadline_hit=False, worker_exit_code=0)
    assert (summary["successful"], summary["failed"], summary["timed_out"]) == (15, 1, 1)
    assert summary["request_results"][2]["status"] == "HEAD_CHANGED"
    assert summary["request_results"][1]["status"] == "HEAD_ERROR"


def test_parent_deadline_terminates_child_and_counts_outstanding(monkeypatch, capsys):
    seen = []

    def run(args, **kwargs):
        assert kwargs["timeout"] == 30
        seen.append(args[-1])
        output = '\n'.join((
            json.dumps({"event": "selection", "objects": [
                {"index": 0, "key_sha256_prefix": "abc"}]}),
            json.dumps({"event": "started", "index": 0,
                        "key_sha256_prefix": "abc"}),
        )).encode()
        raise subprocess.TimeoutExpired(args, 30, output=output)

    monkeypatch.setattr(probe.subprocess, "run", run)
    assert probe.run_parent() == 1
    assert seen == ["1", "4", "16"]
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(result["status"] == "WALL_TIMEOUT" for result in results)
    assert all(result["requests_started"] == 1 and result["requests_completed"] == 0
               and result["timed_out"] == 1 and result["outstanding_at_deadline"] == [0]
               for result in results)
