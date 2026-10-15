"""Host watcher stays outside the canonical runner's process-wide guard."""
from __future__ import annotations

import json
import multiprocessing
import sys
import time
from pathlib import Path

import pytest

from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.canonical_question_cycle import (
    SnapshotEscapeError, _snapshot_only_runner_environment,
)

HOST_ROOT = Path(__file__).resolve().parents[2] / "Trading-bot-stage-a"
sys.path.insert(0, str(HOST_ROOT))
from milestone3_progress_watcher import watch_progress  # noqa: E402
from milestone3_host_cycle import stop_progress_watcher  # noqa: E402


def test_watcher_observes_questions_during_guarded_runner(tmp_path):
    state = tmp_path / "continuous"
    state.mkdir()
    (state / "cycle_progress.json").write_text(json.dumps({
        "current_stage": "QUESTIONS", "status": "RUNNING",
        "stages": {"QUESTIONS": {"details": {
            "phase": "QUESTION_COMPLETED", "questions_completed": 2,
            "objects_completed": 44938, "head_calls": 44938,
        }}},
    }), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    watcher = context.Process(target=watch_progress, args=(state, tmp_path, stop))
    watcher.start()
    try:
        with _snapshot_only_runner_environment(object()):
            with pytest.raises(SnapshotEscapeError, match="FILESYSTEM_EVIDENCE_ACCESS_FORBIDDEN"):
                (state / "cycle_progress.json").read_text(encoding="utf-8")
            with pytest.raises(SnapshotEscapeError, match="LIVE_S3_ACCESS_FORBIDDEN"):
                S3ResearchDataSource(bucket="offline")._get_client()
            deadline = time.monotonic() + 10
            while not (tmp_path / "run_status.json").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert (tmp_path / "run_status.json").exists()
        status = json.loads((tmp_path / "run_status.json").read_text(encoding="utf-8"))
        assert status["snapshot_loading"]["questions_completed"] == 2
        assert status["snapshot_loading"]["head_calls"] == 44938
        assert not (tmp_path / "watcher_failure.json").exists()
    finally:
        stop.set()
        watcher.join(timeout=5)
        if watcher.is_alive():
            watcher.terminate()
            watcher.join(timeout=5)
    assert watcher.exitcode == 0


def test_repeated_progress_read_failure_is_reported(tmp_path):
    state = tmp_path / "continuous"
    state.mkdir()

    class NeverStopped:
        def wait(self, seconds):
            return False

    with pytest.raises(RuntimeError, match="PROGRESS_WATCHER_READ_FAILED_30_TIMES"):
        watch_progress(state, tmp_path, NeverStopped())
    failure = json.loads((tmp_path / "watcher_failure.json").read_text(encoding="utf-8"))
    assert "PROGRESS_WATCHER_READ_FAILED_30_TIMES" in failure["error"]


def test_host_rejects_failed_or_stuck_watcher():
    class Stop:
        def set(self):
            self.called = True

    class Watcher:
        def __init__(self, exitcode, alive=False):
            self.exitcode = exitcode
            self.alive = alive
            self.terminated = False

        def join(self, timeout):
            pass

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    stop = Stop()
    assert stop_progress_watcher(Watcher(0), stop) is None
    assert stop.called
    assert stop_progress_watcher(Watcher(3), stop) == "PROGRESS_WATCHER_FAILED:exit_code=3"
    stuck = Watcher(None, alive=True)
    assert stop_progress_watcher(stuck, stop) == "PROGRESS_WATCHER_DID_NOT_STOP"
    assert stuck.terminated
