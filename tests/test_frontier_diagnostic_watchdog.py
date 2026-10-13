"""Offline process and progress fixtures for the one-shot FRONTIER watchdog."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import scripts.run_frontier_diagnostic as launcher
from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import ResearchDataSourceError
from scripts.run_frontier_diagnostic import run_watchdog
from research_engine.v10.continuous.research_cadence import run_cadence_tick
from research_engine.v10.continuous.research_loop import run_continuous_research_cycle
from research_engine.v10.continuous.frontier_coordinator import (
    SNAPSHOT_READY, run_frontier_snapshot_cycle,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS
from tests.test_frontier_coordinator import MemoryS3, _objects, _source


DUMMY = r'''
import json, os, pathlib, signal, subprocess, sys, time
mode = os.environ['DIAG_MODE']
path = pathlib.Path(os.environ['DIAG_PROGRESS'])
if mode in ('ignore_stop', 'spawn_descendant'):
    signal.signal(signal.SIGBREAK if os.name == 'nt' else signal.SIGTERM, signal.SIG_IGN)
if mode in ('complete', 'stall', 'slow', 'inflight', 'index_ten'):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = 'SNAPSHOT_READY' if mode == 'complete' else 'RUNNING'
    path.write_text(json.dumps({
        'cycle_attempt_id': 'DUMMY-NEW',
        'status': ('FRONTIER_DIAGNOSTIC_STOPPED' if mode == 'complete' else 'RUNNING'),
        'current_stage': 'FRONTIER',
        'stages': {'FRONTIER': {'status': stage, 'details': {
            'snapshot_id': os.environ.get('DIAG_SNAPSHOT'),
            'frontier_id': os.environ.get('DIAG_FRONTIER'),
            'stall_confirmed': mode == 'stall',
            'frontier_substage': {'phase': ('FRONTIER_COMPLETED' if mode == 'complete'
                                           else 'FRONTIER_DATASET_READING'),
                                  'dataset': 'decision_trace',
                                  'dataset_index': 10 if mode == 'index_ten' else 3,
                                  'dataset_total': 10,
                                  'objects_processed': 3,
                                  'queued': 8 if mode == 'inflight' else None,
                                  'completed': 7 if mode == 'inflight' else None,
                                  'source_io_cumulative': {
                                      'verified_cache_hits': 2,
                                      'head_calls': 4, 'get_calls': 1}}}}}}))
if mode == 'natural':
    sys.exit(0)
if mode == 'spawn_descendant':
    descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    (path.parent / 'descendant.pid').write_text(str(descendant.pid))
if mode in ('blocked_get', 'blocked_head'):
    class FakeS3:
        def get_object(self):
            time.sleep(30)
        def head_object(self):
            time.sleep(30)
    getattr(FakeS3(), 'get_object' if mode == 'blocked_get' else 'head_object')()
time.sleep(30)
'''


def _run(tmp_path: Path, mode: str, *, max_seconds=1.0, stall_seconds=0.3,
         grace_seconds=0.1, state=None, snapshot_id=None, frontier_id=None):
    state = state or tmp_path / "state"
    state.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(DIAG_MODE=mode, DIAG_PROGRESS=str(state / "cycle_progress.json"),
               DIAG_SNAPSHOT=snapshot_id or "", DIAG_FRONTIER=frontier_id or "")
    receipt = run_watchdog(
        [sys.executable, "-c", DUMMY], state_root=state,
        log_root=tmp_path / "diagnostic_logs", max_seconds=max_seconds,
        poll_seconds=0.05, stall_seconds=stall_seconds,
        grace_seconds=grace_seconds, env=env)
    log_dir = Path(receipt["log_directory"])
    assert receipt["child_exited"]
    assert (log_dir / "outcome.json").exists()
    assert (log_dir / "child.stdout.log").exists()
    assert (log_dir / "child.stderr.log").exists()
    samples = [json.loads(line) for line in
               (log_dir / "samples.jsonl").read_text().splitlines()]
    return receipt, samples


def _completed_frontier_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    objects = _objects()
    fake = MemoryS3(objects, {key: "v1" for key in objects})
    result = run_frontier_snapshot_cycle(
        source=_source(fake), state_directory=state / "frontier_state",
        manifest_directory=state / "manifests", as_of_date=date(2026, 10, 3))
    assert result.status == SNAPSHOT_READY
    return state, result


def _completion_document(result):
    return {"status": "FRONTIER_DIAGNOSTIC_STOPPED",
            "stages": {"FRONTIER": {"status": "SNAPSHOT_READY", "details": {
                "snapshot_id": result.snapshot_id,
                "frontier_id": result.frontier_id,
                "frontier_substage": {"phase": "FRONTIER_COMPLETED",
                                      "snapshot_id": result.snapshot_id}}}}}


def test_normal_frontier_completion_stops_child(tmp_path):
    state, result = _completed_frontier_state(tmp_path)
    receipt, samples = _run(tmp_path, "complete", state=state,
                            snapshot_id=result.snapshot_id,
                            frontier_id=result.frontier_id)
    assert receipt["reason"] == "frontier_completed"
    assert receipt["snapshot_validated"] is True
    assert receipt["snapshot_id"] == result.snapshot_id
    assert set(receipt["dataset_statuses"]) == set(BOUND_DATASETS)
    assert len(receipt["dataset_statuses"]) == 10
    assert any(sample["dataset"] == "decision_trace" for sample in samples)
    assert samples[-1]["counters"]["head_calls"] == 4


def test_dataset_ten_progress_alone_cannot_claim_completion(tmp_path):
    receipt, samples = _run(tmp_path, "index_ten", max_seconds=0.7,
                            stall_seconds=0.2)
    assert receipt["reason"] == "runtime_limit"
    assert receipt["snapshot_validated"] is False
    assert any(sample["dataset_index"] == 10 for sample in samples)


def test_missing_manifest_rejects_apparent_completion(tmp_path):
    state, result = _completed_frontier_state(tmp_path)
    (state / "manifests" / f"{result.snapshot_id}.json").unlink()
    checked = launcher._validate_completion(state, _completion_document(result))
    assert checked["validated"] is False
    assert checked["snapshot_id"] == result.snapshot_id
    assert checked["errors"]


def test_incomplete_dataset_status_rejects_snapshot(tmp_path):
    state, result = _completed_frontier_state(tmp_path)
    history = state / "frontier_state" / "history" / f"{result.snapshot_id}.json"
    payload = json.loads(history.read_text())
    del payload["frontier"]["dataset_status"][BOUND_DATASETS[-1]]
    history.write_text(json.dumps(payload))
    checked = launcher._validate_completion(state, _completion_document(result))
    assert checked["validated"] is False
    assert any("TEN_DATASET_STATUS_OR_MEMBERSHIP_INCOMPLETE" in error
               for error in checked["errors"])


def test_incoherent_frontier_rejects_snapshot(tmp_path):
    state, result = _completed_frontier_state(tmp_path)
    history = state / "frontier_state" / "history" / f"{result.snapshot_id}.json"
    payload = json.loads(history.read_text())
    payload["frontier"]["coherence_decision"] = "INCOHERENT"
    history.write_text(json.dumps(payload))
    checked = launcher._validate_completion(state, _completion_document(result))
    assert checked["validated"] is False
    assert any("FRONTIER_NOT_COHERENT" in error for error in checked["errors"])


def test_direct_script_path_can_import_validator_from_other_directory(tmp_path):
    state, result = _completed_frontier_state(tmp_path)
    document = tmp_path / "completed.json"
    document.write_text(json.dumps(_completion_document(result)))
    code = ("import json,runpy,sys; from pathlib import Path; "
            "module=runpy.run_path(sys.argv[1],run_name='probe'); "
            "value=module['_validate_completion'](Path(sys.argv[2]),"
            "json.loads(Path(sys.argv[3]).read_text())); "
            "print(json.dumps(value))")
    completed = subprocess.run(
        [sys.executable, "-c", code, str(launcher.__file__),
         str(state), str(document)], cwd=tmp_path, capture_output=True,
        text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["validated"] is True


def test_validation_import_failure_is_reported(tmp_path, monkeypatch):
    state, result = _completed_frontier_state(tmp_path)
    original_import = __import__

    def fail_research_import(name, *args, **kwargs):
        if name.startswith("research_engine"):
            raise ModuleNotFoundError("offline injected import failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_research_import)
    checked = launcher._validate_completion(state, _completion_document(result))
    assert checked["validated"] is False
    assert any("ModuleNotFoundError" in error for error in checked["errors"])


def test_bounded_validation_timeout_is_an_explicit_failure(tmp_path, monkeypatch):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="validator", timeout=0.01)

    monkeypatch.setattr(launcher.subprocess, "run", timed_out)
    checked = launcher._validate_completion_bounded(
        tmp_path, _completion_document(SimpleNamespace(
            snapshot_id="ISNAP-FIXTURE", frontier_id="FRONTIER-FIXTURE")),
        timeout_seconds=0.01)
    assert checked["validated"] is False
    assert any("TimeoutExpired" in error for error in checked["errors"])


def test_power_request_failure_writes_receipt_without_launch(tmp_path, monkeypatch):
    @contextmanager
    def unavailable():
        yield False

    monkeypatch.setattr(launcher, "_prevent_idle_sleep", unavailable)
    receipt = run_watchdog(
        [sys.executable, "-c", "raise RuntimeError('must not start')"],
        state_root=tmp_path / "state", log_root=tmp_path / "logs",
        max_seconds=1, poll_seconds=0.05, stall_seconds=0.2,
        grace_seconds=0.1)
    assert receipt["reason"] == "power_request_failed"
    assert receipt["pid"] is None
    assert (Path(receipt["log_directory"]) / "outcome.json").exists()


def test_watchdog_preserves_logs_when_validation_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_validate_completion_bounded",
                        lambda *args, **kwargs: {
                            "validated": False, "snapshot_id": None,
                            "dataset_statuses": {},
                            "errors": ["VALIDATION_FAILED:ModuleNotFoundError:fixture"]})
    receipt, _ = _run(tmp_path, "complete")
    assert receipt["reason"] == "frontier_invalid"
    assert receipt["validation_failure_reason"].startswith("VALIDATION_FAILED")
    log_dir = Path(receipt["log_directory"])
    assert (log_dir / "outcome.json").exists()
    assert (log_dir / "samples.jsonl").exists()
    assert (log_dir / "child.stderr.log").exists()


def test_normal_child_exit_is_recorded(tmp_path):
    receipt, _ = _run(tmp_path, "natural")
    assert receipt["reason"] == "process_exited"
    assert receipt["exit_code"] == 0
    assert receipt["termination"]["method"] == "natural"


def test_runtime_limit_stops_child_even_without_progress(tmp_path):
    receipt, samples = _run(tmp_path, "ignore_stop", max_seconds=0.7,
                            stall_seconds=2.0, grace_seconds=0.15)
    assert receipt["reason"] == "runtime_limit"
    assert receipt["termination"]["method"] == "forced"
    requested = datetime.fromisoformat(receipt["termination_requested_at"])
    deadline = datetime.fromisoformat(receipt["deadline_at_utc"])
    assert requested <= deadline + timedelta(seconds=0.5)
    assert receipt["elapsed_seconds"] < 3.0
    assert receipt["termination_window_started_at"] is not None
    assert receipt["termination_requested_at"] is not None
    assert receipt["child_exited_at"] is not None
    assert receipt["validation_completed_at"] is not None
    assert all(sample["stall_assessment"] == "telemetry_missing" for sample in samples)


def test_graceful_stop_that_never_finishes_forces_exact_child():
    class NeverGraceful:
        def __init__(self):
            self.killed = False
            self.wait_budgets = []
            self.signals = []

        def poll(self):
            return 1 if self.killed else None

        def send_signal(self, value):
            self.signals.append(value)

        def wait(self, timeout):
            self.wait_budgets.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="fixture", timeout=timeout)
            return 1

        def kill(self):
            self.killed = True

    proc = NeverGraceful()
    receipt = launcher._stop_owned_child(
        proc, grace_seconds=0.1, deadline=launcher.time.monotonic() + 0.1)
    assert receipt["method"] == "forced"
    assert proc.killed is True
    assert len(proc.signals) == 1
    assert proc.wait_budgets[-1] == launcher.FORCE_WAIT_SECONDS


@pytest.mark.parametrize("mode", ["blocked_get", "blocked_head"])
def test_blocked_fake_s3_operation_cannot_outlive_watchdog(tmp_path, mode):
    receipt, _ = _run(tmp_path, mode, max_seconds=0.8,
                      stall_seconds=0.2, grace_seconds=0.1)
    assert receipt["reason"] == "runtime_limit"
    assert receipt["child_exited"] is True
    assert receipt["elapsed_seconds"] < 2.0
    assert (Path(receipt["log_directory"]) / "samples.jsonl").exists()
    assert (Path(receipt["log_directory"]) / "outcome.json").exists()


def test_fake_s3_head_and_get_timeouts_fail_closed():
    class TimedOutS3(MemoryS3):
        def head_object(self, **kwargs):
            raise TimeoutError("offline HEAD timeout")

        def get_object(self, **kwargs):
            raise TimeoutError("offline GET timeout")

    fake = TimedOutS3(_objects())
    source = _source(fake)
    listed = source.discover_dataset_objects("execution_context")
    with pytest.raises(ResearchDataSourceError):
        source.head_bound_objects(
            "execution_context", listed,
            expected_schema_version=current_schema("execution_context"),
            max_workers=8)
    assert source._headed_objects == {}
    with pytest.raises(ResearchDataSourceError):
        source.read_objects_for_freeze(
            "execution_context", listed,
            expected_schema_version=current_schema("execution_context"))


def test_stable_counters_trigger_stall(tmp_path):
    receipt, samples = _run(tmp_path, "stall", max_seconds=2.0,
                            stall_seconds=0.3)
    assert receipt["reason"] == "demonstrated_stall"
    assert samples[-1]["stall_assessment"] == "demonstrated_stall"


def test_unchanged_counters_are_slow_not_a_proven_stall(tmp_path):
    receipt, samples = _run(tmp_path, "slow", max_seconds=0.8,
                            stall_seconds=0.2)
    assert receipt["reason"] == "runtime_limit"
    assert any(sample["stall_assessment"] == "slow_unconfirmed" for sample in samples)


def test_inflight_authority_check_survives_stall_threshold(tmp_path):
    receipt, samples = _run(tmp_path, "inflight", max_seconds=0.8,
                            stall_seconds=0.2)
    assert receipt["reason"] == "runtime_limit"
    assert any(sample["stall_assessment"] == "slow_in_flight" for sample in samples)


def test_missing_record_remains_unavailable_until_timeout(tmp_path):
    receipt, samples = _run(tmp_path, "ignore_stop", max_seconds=0.6,
                            stall_seconds=0.2)
    assert receipt["reason"] == "runtime_limit"
    assert receipt["progress_availability_at_stop"] == "missing"
    assert not any(sample["stall_assessment"] == "unchanged" for sample in samples)


def test_previous_attempt_progress_cannot_end_new_attempt(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "cycle_progress.json").write_text(json.dumps({
        "cycle_attempt_id": "OLD", "status": "COMPLETED",
        "stages": {"FRONTIER": {"status": "COMPLETED"}}}))
    env = os.environ.copy()
    env.update(DIAG_MODE="ignore_stop", DIAG_PROGRESS=str(state / "cycle_progress.json"))
    receipt = run_watchdog(
        [sys.executable, "-c", DUMMY], state_root=state,
        log_root=tmp_path / "logs", max_seconds=0.6,
        poll_seconds=0.05, stall_seconds=0.2,
        grace_seconds=0.1, env=env)
    assert receipt["reason"] == "runtime_limit"
    assert receipt["progress_availability_at_stop"] == "stale_previous_attempt"
    assert receipt["child_exited"]


def test_diagnostic_state_is_isolated_from_existing_progress(tmp_path):
    existing = tmp_path / "research_state"
    existing.mkdir()
    old_progress = existing / "cycle_progress.json"
    old_progress.write_text('{"cycle_attempt_id":"OLD","status":"COMPLETED"}')
    child_code = r'''
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
(root / 'cycle_progress.json').write_text(json.dumps({
  'cycle_attempt_id': 'NEW', 'status': 'FRONTIER_DIAGNOSTIC_STOPPED',
  'stages': {'FRONTIER': {'status': 'SNAPSHOT_READY'}}}))
time.sleep(30)
'''
    receipt = run_watchdog(
        [sys.executable, "-c", child_code, launcher.DIAGNOSTIC_STATE_PLACEHOLDER],
        state_root=existing, log_root=tmp_path / "logs",
        diagnostic_state=True, max_seconds=1.0, poll_seconds=0.05,
        stall_seconds=0.3, grace_seconds=0.1)
    assert receipt["reason"] == "frontier_invalid"
    assert receipt["child_exited"]
    assert old_progress.read_text() == '{"cycle_attempt_id":"OLD","status":"COMPLETED"}'
    assert (Path(receipt["log_directory"]) / "state" / "cycle_progress.json").exists()


def test_forced_stop_does_not_touch_unrelated_process(tmp_path):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                            if os.name == "nt" else 0))
    try:
        receipt, _ = _run(tmp_path, "ignore_stop", max_seconds=0.6,
                          stall_seconds=2.0)
        assert receipt["termination"]["method"] == "forced"
        assert other.poll() is None
        assert other.pid != receipt["pid"]
    finally:
        other.kill()
        other.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows diagnostic job behavior")
def test_forced_stop_closes_descendant_job(tmp_path):
    import ctypes

    receipt, _ = _run(tmp_path, "spawn_descendant", max_seconds=0.8,
                      stall_seconds=2.0, grace_seconds=0.1)
    assert receipt["termination"]["method"] == "forced"
    descendant_pid = int((tmp_path / "state" / "descendant.pid").read_text())
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    for _ in range(20):
        handle = kernel.OpenProcess(0x00100000, False, descendant_pid)
        if not handle:
            break
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                break
        finally:
            kernel.CloseHandle(handle)
        launcher.time.sleep(0.1)
    else:
        pytest.fail("diagnostic descendant survived job closure")


def test_diagnostic_stage_gate_skips_questions_and_cycle_publication(tmp_path, monkeypatch):
    import research_engine.v10.continuous.research_loop as loop
    received = {}

    def frontier(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(status="SNAPSHOT_READY", snapshot_id="SNAP-FIXTURE",
                               frontier_id="FRONTIER-FIXTURE",
                               frontier_end="2026-10-12T10:00:00Z")

    def forbidden(*args, **kwargs):
        raise AssertionError("question evaluation must not start")

    state = tmp_path / "state"
    existing_cache = tmp_path / "existing_cache"
    monkeypatch.setattr(loop, "run_frontier_snapshot_cycle", frontier)
    result = run_continuous_research_cycle(
        state_root=state, frontier_runner=frontier,
        question_runner=forbidden, frontier_diagnostic=True,
        frontier_diagnostic_cache_directory=existing_cache)
    assert result.cycle_outcome == "FRONTIER_DIAGNOSTIC_STOPPED"
    progress = json.loads((state / "cycle_progress.json").read_text())
    assert progress["status"] == "FRONTIER_DIAGNOSTIC_STOPPED"
    assert progress["stages"]["FRONTIER"]["status"] == "SNAPSHOT_READY"
    assert "QUESTIONS" not in progress["stages"]
    assert not (state / "cycles" / "latest_completed.json").exists()
    assert received["state_directory"] == state / "frontier_state"
    assert received["manifest_directory"] == state / "manifests"
    assert received["source"]._verified_cache_directory == existing_cache
    assert received["source"]._verified_cache_max_bytes == 1024 * 1024 * 1024


def test_launcher_uses_one_tick_profile_and_diagnostic_gate(monkeypatch, tmp_path):
    seen = {}

    def fake_watchdog(command, **kwargs):
        seen.update(command=command, **kwargs)
        return {"child_exited": True, "snapshot_validated": True,
                "exit_code": 0, "reason": "frontier_completed"}

    monkeypatch.setattr(launcher, "run_watchdog", fake_watchdog)
    monkeypatch.setattr(sys, "argv", ["run_frontier_diagnostic.py",
                                      "--state-root", str(tmp_path / "state"),
                                      "--log-root", str(tmp_path / "logs"),
                                      "--max-seconds", "2700"])
    assert launcher.main() == 0
    assert seen["command"][-3:] == ["--max-ticks", "1", "--frontier-diagnostic"]
    assert seen["env"]["AWS_PROFILE"] == "trading-bot-new"
    assert seen["env"]["RESEARCH_AWS_PROFILE"] == "trading-bot-new"
    assert seen["env"]["FRONTIER_DIAGNOSTIC_CACHE_MAX_BYTES"] == str(1024 * 1024 * 1024)
    assert seen["diagnostic_state"] is True
    assert seen["max_seconds"] == 2700
    assert launcher.DIAGNOSTIC_STATE_PLACEHOLDER in seen["command"]
    assert "--diagnostic-cache-directory" in seen["command"]


def test_launcher_serializes_writers_of_shared_verified_cache(tmp_path):
    with launcher._single_cache_writer(tmp_path / "state"):
        with pytest.raises(RuntimeError, match="another FRONTIER diagnostic"):
            with launcher._single_cache_writer(tmp_path / "state"):
                pass


def test_diagnostic_tick_never_starts_deep_work(monkeypatch, tmp_path):
    import research_engine.v10.continuous.research_loop as loop
    import research_engine.v10.continuous.production_validation as validation

    monkeypatch.setattr(loop, "_production_evaluator_registry", lambda: None)
    monkeypatch.setattr(validation, "production_executors", lambda **kwargs: (None, None))
    monkeypatch.setattr(loop, "run_continuous_research_cycle",
                        lambda **kwargs: SimpleNamespace(cycle_outcome="FRONTIER_DIAGNOSTIC_STOPPED"))

    def forbidden(**kwargs):
        raise AssertionError("deep work must not start")

    monkeypatch.setattr(loop, "run_deep_research_job", forbidden)
    report = run_cadence_tick(state_root=tmp_path / "state",
                              frontier_diagnostic=True, force_deep_job=True)
    assert report.fast_cycle_attempted
    assert not report.deep_job_attempted
