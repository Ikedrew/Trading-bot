"""Focused offline checks for opt-in FRONTIER HEAD worker stack capture."""
from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

from core.production_data_contract import canonical_s3_key, current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous import frontier_coordinator as fc
from research_engine.v10.continuous.research_loop import _frontier_substage_emitter
from scripts import run_frontier_diagnostic as diagnostic


def _items():
    return [{"identifier": canonical_s3_key(
        "trade_truth", symbol="AUDUSD", date="2026-09-04",
        part=f"part-{index:03d}.jsonl")} for index in range(32)]


class HeadClient:
    def __init__(self, release=None):
        self.release = release
        self.started = threading.Event()

    def head_object(self, **kwargs):
        self.started.set()
        if self.release is not None:
            assert self.release.wait(timeout=4)
        return {"VersionId": "v1", "ETag": '"etag-1"',
                "ContentLength": 12, "LastModified": "t",
                "ResponseMetadata": {"RetryAttempts": 0}}


def _source(client):
    return S3ResearchDataSource(bucket="offline", region="eu-west-2",
                                profile="offline-profile", client=client)


def _enable(monkeypatch, directory):
    monkeypatch.setenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_DIR", str(directory))
    monkeypatch.setenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_SECONDS", "0.2")


def test_blocked_head_dumps_all_thread_stacks_inside_diagnostic_dir(monkeypatch, tmp_path):
    log_dir = tmp_path / "diagnostic-run"
    log_dir.mkdir()
    _enable(monkeypatch, log_dir)
    monkeypatch.setenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_SECONDS", "10")
    release = threading.Event()
    client = HeadClient(release)
    source = _source(client)
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    errors = []

    def run():
        try:
            source.head_bound_objects(
                "trade_truth", _items(),
                expected_schema_version=current_schema("trade_truth"),
                progress_callback=capture.observe)
        except Exception as exc:
            errors.append(exc)
        finally:
            capture.close()

    coordinator = threading.Thread(target=run, daemon=True)
    coordinator.start()
    path = log_dir / "trade_truth_head_threads.log"
    try:
        assert client.started.wait(timeout=2)
        deadline = time.monotonic() + 2
        while capture._handle is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert capture._handle is not None
        capture.close()
        assert path.exists() and path.stat().st_size > 0
    finally:
        release.set()
        coordinator.join(timeout=4)
    assert not coordinator.is_alive() and not errors
    dump = path.read_text(encoding="utf-8")
    assert "head_object" in dump
    assert "head_bound_objects" in dump
    assert dump.count("Thread ") >= 16
    assert path.parent == log_dir


def test_completed_head_cancels_dump_and_removes_empty_file(monkeypatch, tmp_path, capsys):
    log_dir = tmp_path / "diagnostic-run"
    log_dir.mkdir()
    _enable(monkeypatch, log_dir)
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    source = _source(HeadClient())
    headed = source.head_bound_objects(
        "trade_truth", _items(),
        expected_schema_version=current_schema("trade_truth"),
        progress_callback=capture.observe)
    capture.completed()
    capture.close()
    time.sleep(0.3)
    assert len(headed) == 32
    assert not (log_dir / "trade_truth_head_threads.log").exists()
    assert "cancelled_after_completion" in capsys.readouterr().err


def test_zero_completed_heads_dump_after_delay(monkeypatch, tmp_path):
    log_dir = tmp_path / "diagnostic-run"
    log_dir.mkdir()
    _enable(monkeypatch, log_dir)
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    capture.observe({"queued": 32, "completed": 0})
    path = log_dir / "trade_truth_head_threads.log"
    try:
        deadline = time.monotonic() + 3
        while path.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "test_zero_completed_heads_dump_after_delay" in path.read_text(
            encoding="utf-8")
    finally:
        capture.close()


def test_watchdog_interruption_captures_before_cancel(monkeypatch, tmp_path, capsys):
    log_dir = tmp_path / "diagnostic-run"
    log_dir.mkdir()
    _enable(monkeypatch, log_dir)
    # Simulate a pending timer at watchdog shutdown: the old close() silently
    # removed its empty file and reported successful cancellation.
    monkeypatch.setattr(fc.faulthandler, "dump_traceback_later",
                        lambda *args, **kwargs: None)
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    capture.observe({"queued": 32, "completed": 0})
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    finally:
        capture.close()
    path = log_dir / "trade_truth_head_threads.log"
    assert "test_watchdog_interruption_captures_before_cancel" in path.read_text(
        encoding="utf-8")
    notes = capsys.readouterr().err
    assert "dump_retained" in notes
    assert "cancelled" not in notes


def test_failed_future_can_leave_persisted_zero_and_retains_stacks(
        monkeypatch, tmp_path, capsys):
    log_dir = tmp_path / "diagnostic-run"
    log_dir.mkdir()
    _enable(monkeypatch, log_dir)
    release = threading.Event()
    failed = threading.Event()

    class FirstHeadFails(HeadClient):
        def __init__(self):
            super().__init__(release)
            self.lock = threading.Lock()
            self.calls = 0

        def head_object(self, **kwargs):
            with self.lock:
                self.calls += 1
                first = self.calls == 1
            if first:
                failed.set()
                raise RuntimeError("offline HEAD failure")
            return super().head_object(**kwargs)

    persisted = []
    emit, _ = _frontier_substage_emitter(
        lambda *args: persisted.append(args), min_interval_seconds=1000)
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    reported = []
    errors = []

    def progress(counts):
        capture.observe(counts)
        phase = ("CLIENT_READY" if counts.get("stage") == "client_ready"
                 else "PROGRESS")
        emit({"phase": phase, **counts})
        reported.append(dict(counts))

    def run():
        try:
            _source(FirstHeadFails()).head_bound_objects(
                "trade_truth", _items(),
                expected_schema_version=current_schema("trade_truth"),
                progress_callback=progress)
        except Exception as exc:
            errors.append(exc)
        finally:
            capture.close()

    coordinator = threading.Thread(target=run, daemon=True)
    coordinator.start()
    try:
        assert failed.wait(timeout=2)
        deadline = time.monotonic() + 2
        while not any(item.get("failed") for item in reported) \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        assert any(item.get("completed") == 1 and item.get("failed") == 1
                   for item in reported)
        assert persisted[-1][2]["frontier_substage"]["completed"] == 0
        # Simulate watchdog interruption while the other HEAD calls remain stuck.
        capture.close()
        path = log_dir / "trade_truth_head_threads.log"
        assert "head_object" in path.read_text(encoding="utf-8")
        assert "cancelled_after_completion" not in capsys.readouterr().err
    finally:
        release.set()
        coordinator.join(timeout=4)
    assert not coordinator.is_alive() and errors


def test_capture_is_inert_without_diagnostic_opt_in(monkeypatch):
    monkeypatch.delenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_DIR", raising=False)
    monkeypatch.delenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_SECONDS", raising=False)
    monkeypatch.setattr(fc.faulthandler, "dump_traceback_later",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("capture must remain disabled")))
    capture = fc._DiagnosticHeadStackCapture("trade_truth")
    capture.observe({"queued": 32, "completed": 0})
    capture.close()


def test_watchdog_child_environment_is_scoped_to_current_log_dir(tmp_path):
    log_dir = tmp_path / "current-run"
    log_dir.mkdir()
    supplied = {diagnostic.HEAD_STACK_DIR_ENV: str(tmp_path / "elsewhere"),
                diagnostic.HEAD_STACK_SECONDS_ENV: "999", "KEEP": "yes"}
    enabled = diagnostic._child_environment(supplied, log_dir, 15)
    assert enabled[diagnostic.HEAD_STACK_DIR_ENV] == str(log_dir)
    assert enabled[diagnostic.HEAD_STACK_SECONDS_ENV] == "15"
    assert enabled["KEEP"] == "yes"
    disabled = diagnostic._child_environment(supplied, log_dir, None)
    assert diagnostic.HEAD_STACK_DIR_ENV not in disabled
    assert diagnostic.HEAD_STACK_SECONDS_ENV not in disabled


def test_launcher_opt_in_passes_delay_to_isolated_watchdog(monkeypatch, tmp_path):
    seen = {}

    def watchdog(command, **kwargs):
        seen.update(kwargs)
        return {"child_exited": True, "snapshot_validated": True,
                "reason": "frontier_completed"}

    monkeypatch.setattr(diagnostic, "run_watchdog", watchdog)
    monkeypatch.setattr(sys, "argv", [
        "run_frontier_diagnostic", "--state-root", str(tmp_path / "state"),
        "--log-root", str(tmp_path / "logs"), "--max-seconds", "75",
        "--head-stack-after-seconds", "15"])
    assert diagnostic.main() == 0
    assert seen["diagnostic_state"] is True
    assert seen["max_seconds"] == 75
    assert seen["head_stack_after_seconds"] == 15


def test_watchdog_child_receives_only_its_isolated_log_directory(tmp_path):
    command = [sys.executable, "-c", "import os; print(os.getenv("
               "'FRONTIER_DIAGNOSTIC_HEAD_STACK_DIR'), flush=True)"]
    receipt = diagnostic.run_watchdog(
        command, state_root=tmp_path / "research", log_root=tmp_path / "logs",
        diagnostic_state=True, head_stack_after_seconds=0.5,
        max_seconds=2, poll_seconds=0.1, stall_seconds=1,
        grace_seconds=0.2, validation_timeout_seconds=2)
    log_dir = Path(receipt["log_directory"])
    reported = (log_dir / "child.stdout.log").read_text(encoding="utf-8").strip()
    assert reported == str(log_dir)
    assert log_dir.parent == (tmp_path / "logs").resolve()
