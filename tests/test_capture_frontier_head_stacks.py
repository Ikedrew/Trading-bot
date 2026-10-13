"""Offline checks for the read-only FRONTIER worker stack-capture harness."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from scripts import capture_frontier_head_stacks as capture


def test_worker_uses_checkpoint_objects_and_actual_head_entry(monkeypatch, capsys):
    items = [{"identifier": f"core/trade_truth/{i}", "s3_key": f"core/trade_truth/{i}"}
             for i in range(16)]
    seen = {}

    class Source:
        def __init__(self, **kwargs):
            seen["source_kwargs"] = kwargs

        def _get_client(self):
            config = SimpleNamespace(connect_timeout=3, read_timeout=5,
                                     retries={"total_max_attempts": 1},
                                     max_pool_connections=16)
            return SimpleNamespace(meta=SimpleNamespace(config=config))

        def head_bound_objects(self, dataset, objects, *, expected_schema_version,
                               progress_callback):
            seen["dataset"] = dataset
            seen["objects"] = objects
            progress_callback({"queued": 16, "completed": 16,
                               "successful": 16, "failed": 0})
            return tuple(objects)

        def performance_snapshot(self):
            return {"head_calls": 16}

    scheduled = []
    monkeypatch.setattr(capture, "checkpoint_sample", lambda: items)
    monkeypatch.setattr(capture, "S3ResearchDataSource", Source)
    monkeypatch.setattr(capture.faulthandler, "dump_traceback_later",
                        lambda seconds, **kwargs: scheduled.append(seconds))
    monkeypatch.setattr(capture.faulthandler, "cancel_dump_traceback_later",
                        lambda: scheduled.append("cancelled"))
    assert capture.worker() == 0
    assert seen["objects"] == items and seen["dataset"] == "trade_truth"
    assert "verified_cache_directory" not in seen["source_kwargs"]
    assert scheduled == [10, "cancelled"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == {"event": "completed", "objects": 16, "head_calls": 16}


def test_parent_kills_child_at_deadline_and_preserves_thread_dump(monkeypatch, capsys):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 25
        raise subprocess.TimeoutExpired(
            command, 25, output=b'{"event":"client_ready"}\n',
            stderr=b'Thread worker stack\n')

    monkeypatch.setattr(capture.subprocess, "run", timeout)
    assert capture.parent() == 124
    output = capsys.readouterr()
    assert '"event":"client_ready"' in output.out
    assert '"event": "wall_timeout"' in output.out
    assert "Thread worker stack" in output.err
