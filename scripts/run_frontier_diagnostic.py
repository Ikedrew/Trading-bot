"""One bounded FRONTIER diagnostic; never invokes the cadence supervisor."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
COUNTERS = ("verified_cache_hits", "verified_cache_misses",
            "verified_cache_writes", "verified_cache_capacity_skips",
            "verified_cache_used_bytes", "verified_cache_capacity_bytes",
            "head_calls", "get_calls")
TIMINGS = ("head_seconds_inclusive", "get_open_seconds_inclusive",
           "cache_index_seconds_inclusive", "cache_body_verify_seconds_inclusive",
           "body_load_seconds_inclusive", "hash_seconds", "decode_seconds")
DIAGNOSTIC_STATE_PLACEHOLDER = "{DIAGNOSTIC_STATE_ROOT}"
FORCE_WAIT_SECONDS = 5
HEAD_STACK_DIR_ENV = "FRONTIER_DIAGNOSTIC_HEAD_STACK_DIR"
HEAD_STACK_SECONDS_ENV = "FRONTIER_DIAGNOSTIC_HEAD_STACK_SECONDS"


@contextmanager
def _single_cache_writer(state_root: Path):
    """Keep bounded diagnostic launches from writing the shared cache together."""
    lock_path = state_root.resolve() / ".verified_cache_writer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another FRONTIER diagnostic owns the verified cache") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _child_environment(
    env: dict[str, str] | None, log_dir: Path, head_stack_after_seconds: float | None,
) -> dict[str, str]:
    child = dict(os.environ if env is None else env)
    child.pop(HEAD_STACK_DIR_ENV, None)
    child.pop(HEAD_STACK_SECONDS_ENV, None)
    if head_stack_after_seconds is not None:
        child[HEAD_STACK_DIR_ENV] = str(log_dir)
        child[HEAD_STACK_SECONDS_ENV] = str(head_stack_after_seconds)
    return child


@contextmanager
def _prevent_idle_sleep():
    """Keep Windows awake only while this watchdog owns the diagnostic child."""
    if os.name != "nt":
        yield True
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.SetThreadExecutionState.argtypes = (ctypes.c_ulong,)
    kernel.SetThreadExecutionState.restype = ctypes.c_ulong
    continuous = 0x80000000
    active = bool(kernel.SetThreadExecutionState(continuous | 0x00000001))
    try:
        yield active
    finally:
        if active:
            kernel.SetThreadExecutionState(continuous)


def _read_progress(path: Path, baseline: tuple[int | None, str | None]) -> tuple[dict[str, Any] | None, str]:
    try:
        stat = path.stat()
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return None, "malformed"
        if (baseline[0] is not None
                and (stat.st_mtime_ns <= baseline[0]
                     or document.get("cycle_attempt_id") == baseline[1])):
            return None, "stale_previous_attempt"
        return document, "available"
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, UnicodeError):
        return None, "malformed"


def _sample(document: dict[str, Any] | None, availability: str, *, pid: int,
            elapsed: float) -> dict[str, Any]:
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    frontier = mapping(mapping((document or {}).get("stages")).get("FRONTIER"))
    details = mapping(frontier.get("details"))
    substage = mapping(details.get("frontier_substage"))
    io = mapping(substage.get("source_io_cumulative")
                 or details.get("frontier_source_io_delta"))
    return {
        "timestamp_utc": _stamp(), "elapsed_seconds": round(elapsed, 3),
        "pid": pid, "progress_availability": availability,
        "cycle_attempt_id": (document or {}).get("cycle_attempt_id"),
        "progress_status": (document or {}).get("status"),
        "current_stage": (document or {}).get("current_stage"),
        "frontier_status": frontier.get("status"),
        "frontier_failure": details.get("failure"),
        "frontier_phase": substage.get("phase"),
        "frontier_snapshot_id": details.get("snapshot_id") or substage.get("snapshot_id"),
        "dataset": substage.get("dataset"),
        "dataset_index": substage.get("dataset_index"),
        "dataset_total": substage.get("dataset_total"),
        "objects_processed": substage.get("objects_processed"),
        "objects_selected_for_read": substage.get("objects_selected_for_read"),
        "authority_queued": substage.get("queued"),
        "authority_completed": substage.get("completed"),
        "frontier_substage_events": details.get("frontier_substage_events"),
        "progress_updated_at": (document or {}).get("updated_at"),
        "stall_confirmed": details.get("stall_confirmed") is True,
        "counters": {key: io.get(key) for key in COUNTERS},
        "timings_seconds": {key: io.get(key) for key in TIMINGS},
        "recovery": {
            "checkpoint_loaded": substage.get("checkpoint_loaded"),
            "checkpoint_load_seconds": substage.get("checkpoint_load_seconds"),
            "resumed_datasets": substage.get("resumed_datasets"),
            "resumed_objects": substage.get("resumed_objects"),
            "datasets_recovered": substage.get("datasets_recovered"),
            "objects_reused": substage.get("objects_reused"),
            "revalidation_seconds": substage.get("revalidation_seconds"),
            "recovered_datasets_seconds": substage.get("recovered_datasets_seconds"),
            "remaining_datasets_seconds": substage.get("remaining_datasets_seconds"),
        },
        "process_rss_bytes": (document or {}).get("process_rss_bytes"),
        "process_peak_rss_bytes": (document or {}).get("process_peak_rss_bytes"),
    }


def _frontier_done(sample: dict[str, Any]) -> bool:
    return (sample["progress_status"] == "FRONTIER_DIAGNOSTIC_STOPPED"
            and sample["frontier_status"] not in (None, "", "RUNNING"))


def _activity(sample: dict[str, Any]) -> tuple[Any, ...] | None:
    values = (sample["frontier_phase"], sample["dataset"],
              sample["objects_processed"], sample["authority_queued"],
              sample["authority_completed"], sample["frontier_substage_events"],
              sample["progress_updated_at"],
              *(sample["counters"][key] for key in COUNTERS))
    return values if any(value is not None for value in values) else None


def _validate_completion(state_root: Path, document: dict[str, Any] | None) -> dict[str, Any]:
    """Check the isolated success state and immutable manifest without S3 I/O."""
    result: dict[str, Any] = {"validated": False, "snapshot_id": None,
                              "dataset_statuses": {}, "errors": []}
    if not isinstance(document, dict):
        result["errors"].append("CURRENT_PROGRESS_MISSING")
        return result
    stages = document.get("stages") or {}
    frontier_stage = stages.get("FRONTIER") or {}
    details = frontier_stage.get("details") or {}
    substage = details.get("frontier_substage") or {}
    result["snapshot_id"] = details.get("snapshot_id") or substage.get("snapshot_id")
    if document.get("status") != "FRONTIER_DIAGNOSTIC_STOPPED":
        result["errors"].append("DIAGNOSTIC_BOUNDARY_NOT_REACHED")
    if frontier_stage.get("status") != "SNAPSHOT_READY":
        result["errors"].append("FRONTIER_NOT_SNAPSHOT_READY")
    if substage.get("phase") != "FRONTIER_COMPLETED":
        result["errors"].append("FRONTIER_VERIFICATION_NOT_COMPLETED")
    if "QUESTIONS" in stages:
        result["errors"].append("QUESTION_STAGE_ENTERED")
    if result["errors"]:
        return result
    try:
        from research_engine.v10.continuous.frontier_coordinator import (
            FrontierStateStore, _object_identity,
        )
        from research_engine.v10.investigation_snapshot import (
            BOUND_DATASETS, REQUIRED_DATASETS, load_investigation_snapshot_id,
        )
        state = FrontierStateStore(state_root / "frontier_state").load_latest_success()
        if state is None:
            raise ValueError("FRONTIER_SUCCESS_STATE_MISSING")
        snapshot_id = str(state.get("last_successful_snapshot_id") or "")
        result["snapshot_id"] = snapshot_id
        if snapshot_id != details.get("snapshot_id"):
            raise ValueError("PROGRESS_SNAPSHOT_ID_MISMATCH")
        snapshot = load_investigation_snapshot_id(
            snapshot_id, manifest_directory=state_root / "manifests")
        if snapshot.snapshot_fingerprint != state.get("last_successful_snapshot_fingerprint"):
            raise ValueError("SNAPSHOT_FINGERPRINT_MISMATCH")
        frontier = state.get("frontier") or {}
        if frontier.get("coherence_decision") != "COHERENT_COMMON_DATE_PARTITION_INTERSECTION":
            raise ValueError("FRONTIER_NOT_COHERENT")
        if frontier.get("frontier_id") != details.get("frontier_id"):
            raise ValueError("FRONTIER_ID_MISMATCH")
        statuses = frontier.get("dataset_status") or {}
        membership = frontier.get("dataset_membership") or {}
        result["dataset_statuses"] = dict(statuses)
        if set(statuses) != set(BOUND_DATASETS) or set(membership) != set(BOUND_DATASETS):
            raise ValueError("TEN_DATASET_STATUS_OR_MEMBERSHIP_INCOMPLETE")
        bindings = {binding.dataset: binding for binding in snapshot.datasets}
        for dataset in BOUND_DATASETS:
            status = statuses[dataset]
            if status not in {"READY", "MISSING", "STALE"}:
                raise ValueError("DATASET_STATUS_INVALID:" + dataset)
            if dataset in REQUIRED_DATASETS and status != "READY":
                raise ValueError("REQUIRED_DATASET_NOT_READY:" + dataset)
            if membership[dataset].get("status") != status:
                raise ValueError("DATASET_MEMBERSHIP_STATUS_MISMATCH:" + dataset)
            expected = [_object_identity(item) for item in membership[dataset].get("objects", ())]
            actual = [_object_identity(item.to_dict()) for item in bindings[dataset].objects]
            if expected != actual:
                raise ValueError("DATASET_SNAPSHOT_MEMBERSHIP_MISMATCH:" + dataset)
            if (status == "READY") != (bindings[dataset].presence == "PRESENT"):
                raise ValueError("DATASET_SNAPSHOT_PRESENCE_MISMATCH:" + dataset)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
        result["errors"].append(f"{type(exc).__name__}:{exc}")
        return result
    result["validated"] = True
    return result


def _validate_completion_bounded(
    state_root: Path, document: dict[str, Any] | None, *, timeout_seconds: float,
) -> dict[str, Any]:
    """Run local manifest validation in an exact child with a hard timeout."""
    code = (
        "import json,sys; from pathlib import Path; "
        "sys.path.insert(0,sys.argv[1]); "
        "from scripts.run_frontier_diagnostic import _validate_completion; "
        "print(json.dumps(_validate_completion(Path(sys.argv[2]),json.load(sys.stdin))))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(REPO_ROOT), str(state_root)],
            input=json.dumps(document), text=True, capture_output=True,
            timeout=timeout_seconds, cwd=REPO_ROOT,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        if result.returncode:
            raise RuntimeError(f"validator exited {result.returncode}:"
                               + result.stderr[-500:])
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not isinstance(value.get("validated"), bool):
            raise ValueError("validator returned an invalid result")
        return value
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"validated": False, "snapshot_id": None,
                "dataset_statuses": {},
                "errors": [f"VALIDATION_FAILED:{type(exc).__name__}:{exc}"]}


def _stop_owned_child(proc: subprocess.Popen[Any], *, grace_seconds: float,
                      deadline: float) -> dict[str, Any]:
    if proc.poll() is not None:
        return {"method": "already_exited", "graceful_signal_sent": False}
    sent = False
    signal_error = None
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
        sent = True
    except (OSError, ValueError) as exc:
        signal_error = f"{type(exc).__name__}:{exc}"
    wait_seconds = max(0.0, min(grace_seconds, deadline - time.monotonic()))
    if sent:
        try:
            proc.wait(timeout=wait_seconds)
            return {"method": "graceful", "graceful_signal_sent": True}
        except subprocess.TimeoutExpired:
            pass
    try:
        if proc.poll() is None:
            proc.kill()  # Popen's owned process handle, never a process-name search.
        proc.wait(timeout=FORCE_WAIT_SECONDS)
        method = "forced"
        force_error = None
    except (OSError, subprocess.TimeoutExpired) as exc:
        method = "failed"
        force_error = f"{type(exc).__name__}:{exc}"
    return {"method": method, "graceful_signal_sent": sent,
            "graceful_signal_error": signal_error, "force_error": force_error}


def _windows_kill_on_close_job(proc: subprocess.Popen[Any]) -> Any:
    """Bind the owned child to a Windows job that closes on watchdog death."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("per_process_time", ctypes.c_longlong),
                    ("per_job_time", ctypes.c_longlong),
                    ("limit_flags", wintypes.DWORD),
                    ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t),
                    ("active_process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority_class", wintypes.DWORD),
                    ("scheduling_class", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [("read_operations", ctypes.c_ulonglong),
                    ("write_operations", ctypes.c_ulonglong),
                    ("other_operations", ctypes.c_ulonglong),
                    ("read_bytes", ctypes.c_ulonglong),
                    ("write_bytes", ctypes.c_ulonglong),
                    ("other_bytes", ctypes.c_ulonglong)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                    ("process_memory_limit", ctypes.c_size_t),
                    ("job_memory_limit", ctypes.c_size_t),
                    ("peak_process_memory_used", ctypes.c_size_t),
                    ("peak_job_memory_used", ctypes.c_size_t)]

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()
    try:
        limits = ExtendedLimits()
        limits.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits),
                                               ctypes.sizeof(limits)):
            raise ctypes.WinError()
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            raise ctypes.WinError()
        return job
    except BaseException:
        kernel.CloseHandle(job)
        raise


def _close_windows_job(job: Any) -> None:
    if job is not None:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(job)


def run_watchdog(command: Sequence[str], *, state_root: Path, log_root: Path,
                 max_seconds: float = 600, poll_seconds: float = 15,
                 stall_seconds: float = 120, grace_seconds: float = 5,
                 validation_timeout_seconds: float = 15,
                 env: dict[str, str] | None = None,
                 diagnostic_state: bool = False,
                 head_stack_after_seconds: float | None = None) -> dict[str, Any]:
    """Monitor one directly owned child and persist a termination receipt."""
    if (not command or min(max_seconds, poll_seconds, stall_seconds,
                           validation_timeout_seconds) <= 0
            or not 0 <= grace_seconds < max_seconds):
        raise ValueError("invalid diagnostic limits")
    if head_stack_after_seconds is not None and (
            not diagnostic_state or not 0 < head_stack_after_seconds < max_seconds):
        raise ValueError("head stack capture requires bounded diagnostic state")
    state_root = state_root.resolve()
    log_root = log_root.resolve()
    if log_root == state_root or state_root in log_root.parents:
        raise ValueError("diagnostic logs must be outside research state")
    log_dir = log_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                          + "-" + uuid.uuid4().hex[:8])
    log_dir.mkdir(parents=True, exist_ok=False)
    child_env = _child_environment(env, log_dir, head_stack_after_seconds)
    monitored_state = log_dir / "state" if diagnostic_state else state_root
    if diagnostic_state:
        command = [arg.replace(DIAGNOSTIC_STATE_PLACEHOLDER, str(monitored_state))
                   for arg in command]
    progress_path = monitored_state / "cycle_progress.json"
    try:
        baseline_mtime = progress_path.stat().st_mtime_ns
        try:
            baseline_doc = json.loads(progress_path.read_text(encoding="utf-8"))
            baseline_id = baseline_doc.get("cycle_attempt_id")
        except (OSError, ValueError, AttributeError):
            baseline_id = None
        baseline = (baseline_mtime, baseline_id)
    except OSError:
        baseline = (None, None)

    stdout_path, stderr_path = log_dir / "child.stdout.log", log_dir / "child.stderr.log"
    events_path = log_dir / "samples.jsonl"
    started = time.monotonic()
    deadline = started + max_seconds
    deadline_at_utc = (datetime.now(timezone.utc)
                       + timedelta(seconds=max_seconds)).isoformat()
    reason = None
    deadline_reached_at = None
    termination_window_started_at = None
    termination_requested_at = None
    child_exited_at = None
    validation_completed_at = None
    termination: dict[str, Any] = {"method": "natural", "graceful_signal_sent": False}
    with (_prevent_idle_sleep() as awake, stdout_path.open("wb") as stdout,
          stderr_path.open("wb") as stderr,
          events_path.open("w", encoding="utf-8") as events):
        if not awake:
            receipt = {"timestamp_utc": _stamp(), "reason": "power_request_failed",
                       "pid": None, "exit_code": None, "child_exited": True,
                       "termination": {"method": "not_started"},
                       "log_directory": str(log_dir), "command": list(command),
                       "runtime_limit_seconds": max_seconds, "elapsed_seconds": 0.0,
                       "deadline_at_utc": deadline_at_utc,
                       "deadline_reached_at": None,
                       "termination_window_started_at": None,
                       "termination_requested_at": None,
                       "child_exited_at": None,
                       "validation_completed_at": None,
                       "snapshot_id": None, "snapshot_validated": False,
                       "dataset_statuses": {}, "counters": {},
                       "process_rss_bytes": None, "process_peak_rss_bytes": None,
                       "errors": ["WINDOWS_IDLE_SLEEP_PREVENTION_UNAVAILABLE"]}
            (log_dir / "outcome.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return receipt
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(list(command), cwd=REPO_ROOT, env=child_env,
                                    stdout=stdout, stderr=stderr,
                                    creationflags=flags)
        except OSError as exc:
            receipt = {"timestamp_utc": _stamp(), "reason": "launch_failed",
                       "pid": None, "exit_code": None, "child_exited": True,
                       "termination": {"method": "not_started",
                                       "error": f"{type(exc).__name__}:{exc}"},
                       "log_directory": str(log_dir), "command": list(command),
                       "runtime_limit_seconds": max_seconds, "elapsed_seconds": 0.0,
                       "deadline_at_utc": deadline_at_utc,
                       "deadline_reached_at": None,
                       "termination_window_started_at": None,
                       "termination_requested_at": None,
                       "child_exited_at": None,
                       "validation_completed_at": None,
                       "snapshot_id": None, "snapshot_validated": False,
                       "dataset_statuses": {}, "counters": {},
                       "process_rss_bytes": None, "process_peak_rss_bytes": None,
                       "errors": [f"{type(exc).__name__}:{exc}"]}
            (log_dir / "outcome.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return receipt
        try:
            job = _windows_kill_on_close_job(proc)
        except BaseException as exc:
            stop_error = None
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=FORCE_WAIT_SECONDS)
            except (OSError, subprocess.TimeoutExpired) as stop_exc:
                stop_error = f"{type(stop_exc).__name__}:{stop_exc}"
            receipt = {"timestamp_utc": _stamp(), "reason": "job_setup_failed",
                       "pid": proc.pid, "exit_code": proc.poll(),
                       "child_exited": proc.poll() is not None,
                       "termination": {"method": "forced",
                                       "error": f"{type(exc).__name__}:{exc}",
                                       "force_error": stop_error},
                       "log_directory": str(log_dir), "command": list(command),
                       "runtime_limit_seconds": max_seconds,
                       "elapsed_seconds": round(time.monotonic() - started, 3),
                       "deadline_at_utc": deadline_at_utc,
                       "deadline_reached_at": None,
                       "termination_window_started_at": None,
                       "termination_requested_at": _stamp(),
                       "child_exited_at": _stamp() if proc.poll() is not None else None,
                       "validation_completed_at": None,
                       "snapshot_id": None, "snapshot_validated": False,
                       "dataset_statuses": {}, "counters": {},
                       "process_rss_bytes": None, "process_peak_rss_bytes": None,
                       "errors": [f"{type(exc).__name__}:{exc}"]
                       + ([stop_error] if stop_error else [])}
            (log_dir / "outcome.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return receipt
        last_activity = None
        activity_since = None
        last_availability = None
        last_sample: dict[str, Any] | None = None
        last_recovery: dict[str, Any] = {}
        observed_datasets: dict[str, str] = {}
        try:
            while True:
                now = time.monotonic()
                if now >= deadline - grace_seconds:
                    reason = "runtime_limit"
                    termination_window_started_at = _stamp()
                    if now >= deadline:
                        deadline_reached_at = termination_window_started_at
                    break
                document, availability = _read_progress(progress_path, baseline)
                sample = _sample(document, availability, pid=proc.pid, elapsed=now - started)
                last_sample = sample
                if any(value is not None for value in sample["recovery"].values()):
                    last_recovery = sample["recovery"]
                if (sample["frontier_phase"] == "FRONTIER_DATASET_VERIFIED"
                        and sample["dataset"]):
                    observed_datasets[str(sample["dataset"])] = "VERIFIED_DISCOVERY"
                activity = _activity(sample) if availability == "available" else None
                if activity is None:
                    sample["stall_assessment"] = "telemetry_missing"
                    activity_since = None
                    last_activity = None
                elif activity != last_activity:
                    last_activity, activity_since = activity, now
                    sample["stall_assessment"] = "progress_changed"
                else:
                    sample["unchanged_seconds"] = round(now - activity_since, 3)
                    in_flight = (isinstance(sample["authority_queued"], int)
                                 and isinstance(sample["authority_completed"], int)
                                 and sample["authority_queued"] > sample["authority_completed"])
                    if now - activity_since < stall_seconds:
                        sample["stall_assessment"] = "unchanged"
                    elif in_flight:
                        sample["stall_assessment"] = "slow_in_flight"
                    elif sample["stall_confirmed"]:
                        sample["stall_assessment"] = "demonstrated_stall"
                    else:
                        sample["stall_assessment"] = "slow_unconfirmed"
                events.write(json.dumps(sample, sort_keys=True) + "\n")
                events.flush()
                last_availability = availability

                if _frontier_done(sample):
                    reason = "frontier_boundary_reached"
                elif proc.poll() is not None:
                    reason = "process_exited"
                elif sample["stall_assessment"] == "demonstrated_stall":
                    reason = "demonstrated_stall"
                elif now >= deadline - grace_seconds:
                    reason = "runtime_limit"
                    termination_window_started_at = _stamp()
                    if now >= deadline:
                        deadline_reached_at = termination_window_started_at
                if reason:
                    break
                time.sleep(min(poll_seconds, max(0.0, deadline - grace_seconds - time.monotonic())))
        finally:
            if proc.poll() is None:
                termination_requested_at = _stamp()
                try:
                    termination = _stop_owned_child(proc, grace_seconds=grace_seconds,
                                                    deadline=deadline)
                except Exception as exc:
                    termination = {"method": "failed",
                                   "error": f"{type(exc).__name__}:{exc}"}
            exit_code = proc.poll()
            if deadline_reached_at is None and time.monotonic() >= deadline:
                deadline_reached_at = _stamp()
            if exit_code is not None:
                child_exited_at = _stamp()
            final_document, final_availability = _read_progress(progress_path, baseline)
            if final_availability == "available":
                last_sample = _sample(final_document, final_availability,
                                      pid=proc.pid, elapsed=time.monotonic() - started)
                if any(value is not None for value in last_sample["recovery"].values()):
                    last_recovery = last_sample["recovery"]
            try:
                validation = _validate_completion_bounded(
                    monitored_state,
                    final_document if final_availability == "available" else None,
                    timeout_seconds=validation_timeout_seconds)
            except Exception as exc:
                validation = {"validated": False, "snapshot_id": None,
                              "dataset_statuses": {},
                              "errors": [f"VALIDATION_EXCEPTION:{type(exc).__name__}:{exc}"]}
            validation_completed_at = _stamp()
            if validation["validated"]:
                reason = "frontier_completed"
            elif reason == "frontier_boundary_reached":
                reason = "frontier_invalid"
            errors = list(validation["errors"])
            if last_sample and last_sample.get("frontier_failure"):
                errors.append(str(last_sample["frontier_failure"]))
            if exit_code not in (None, 0) and not validation["validated"]:
                errors.append("CHILD_EXIT_CODE:" + str(exit_code))
            receipt = {
                "timestamp_utc": _stamp(), "reason": reason or "watchdog_error",
                "pid": proc.pid, "exit_code": exit_code,
                "child_exited": exit_code is not None,
                "termination": termination,
                "progress_availability_at_stop": last_availability,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "runtime_limit_seconds": max_seconds,
                "termination_grace_seconds": grace_seconds,
                "force_wait_seconds": FORCE_WAIT_SECONDS,
                "validation_timeout_seconds": validation_timeout_seconds,
                "deadline_at_utc": deadline_at_utc,
                "deadline_reached_at": deadline_reached_at,
                "termination_window_started_at": termination_window_started_at,
                "termination_requested_at": termination_requested_at,
                "child_exited_at": child_exited_at,
                "validation_completed_at": validation_completed_at,
                "command": list(command),
                "log_directory": str(log_dir),
                "snapshot_id": validation["snapshot_id"],
                "snapshot_validated": validation["validated"],
                "validation_failure_reason": (None if validation["validated"]
                                              else (errors[0] if errors else "UNKNOWN")),
                "dataset_statuses": (validation["dataset_statuses"]
                                     or observed_datasets),
                "counters": (last_sample or {}).get("counters", {}),
                "recovery": last_recovery,
                "process_rss_bytes": (last_sample or {}).get("process_rss_bytes"),
                "process_peak_rss_bytes": (last_sample or {}).get("process_peak_rss_bytes"),
                "errors": errors,
            }
            try:
                (log_dir / "outcome.json").write_text(
                    json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            finally:
                _close_windows_job(job)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--state-root", type=Path,
                        default=REPO_ROOT / "data/research/continuous")
    parser.add_argument("--log-root", type=Path,
                        default=REPO_ROOT / "logs/frontier_diagnostics")
    parser.add_argument("--max-seconds", type=float, default=600,
                        help="supervised wall-clock limit (2700 for the 45-minute completion test)")
    parser.add_argument("--cache-max-bytes", type=int, default=1024 * 1024 * 1024,
                        help="total verified-body cache cap including existing entries (default: 1 GiB)")
    parser.add_argument("--head-stack-after-seconds", type=float, default=None,
                        help="opt-in trade_truth HEAD thread dump delay; writes inside this run's log directory")
    args = parser.parse_args()
    if args.cache_max_bytes < 0:
        parser.error("--cache-max-bytes must be nonnegative")
    env = os.environ.copy()
    env["AWS_PROFILE"] = "trading-bot-new"
    env["RESEARCH_AWS_PROFILE"] = "trading-bot-new"
    env["FRONTIER_DIAGNOSTIC_CACHE_MAX_BYTES"] = str(args.cache_max_bytes)
    command = [args.python, "-m", "research_engine.v10.continuous.research_cadence",
               "--state-root", DIAGNOSTIC_STATE_PLACEHOLDER,
               "--diagnostic-cache-directory",
               str((args.state_root.resolve() / "verified_object_bodies")),
               "--max-ticks", "1",
               "--frontier-diagnostic"]
    with _single_cache_writer(args.state_root):
        receipt = run_watchdog(command, state_root=args.state_root, log_root=args.log_root,
                               env=env, diagnostic_state=True,
                               max_seconds=args.max_seconds,
                               head_stack_after_seconds=args.head_stack_after_seconds)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if (receipt["child_exited"] and receipt["snapshot_validated"]
                 and receipt["reason"] == "frontier_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
