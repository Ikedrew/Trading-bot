"""Immutable checkpoints for bounded continuous research invocations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json


CYCLE_SCHEMA = "continuous_research_cycle_v1"
PROGRESS_SCHEMA = "continuous_research_cycle_progress_v1"
DEFAULT_CYCLE_STATE_DIRECTORY = Path("data/research/continuous/cycles")


class ContinuousCycleStateError(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False
    except Exception:
        # Fail closed if process liveness cannot be established.
        return True


class ContinuousCycleLease:
    """Single-host exclusive lease for the authoritative continuous loop."""

    def __init__(self, path: Path | str, *, lease_id: str):
        self.path = Path(path)
        self.lease_id = str(lease_id)
        self._owned = False

    def __enter__(self) -> "ContinuousCycleLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "lease_id": self.lease_id,
            "pid": os.getpid(),
            "started_at": _utc_now(),
        }
        for _ in range(2):
            try:
                descriptor = os.open(
                    str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._owned = True
                return self
            except FileExistsError:
                try:
                    existing_text = self.path.read_text(encoding="utf-8")
                    existing = json.loads(existing_text)
                    stale = not _pid_is_alive(int(existing.get("pid") or 0))
                    if stale and self.path.read_text(encoding="utf-8") == existing_text:
                        self.path.unlink()
                        continue
                except (OSError, ValueError, TypeError):
                    pass
                raise ContinuousCycleStateError(
                    "CONTINUOUS_RESEARCH_CYCLE_ALREADY_ACTIVE")
        raise ContinuousCycleStateError("CONTINUOUS_RESEARCH_CYCLE_ALREADY_ACTIVE")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if not self._owned:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if (current.get("lease_id") == self.lease_id
                    and int(current.get("pid") or 0) == os.getpid()):
                self.path.unlink(missing_ok=True)
        finally:
            self._owned = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_memory_bytes() -> tuple[int | None, int | None]:
    """Return current RSS and observed process peak without a third-party dependency."""
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (
                wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters), counters.cb)
            if ok:
                return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
        else:  # pragma: no cover - exercised on non-Windows deployment hosts
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            scale = 1 if sys.platform == "darwin" else 1024
            peak = int(usage.ru_maxrss * scale)
            return None, peak
    except Exception:
        pass
    return None, None


class ContinuousCycleProgressStore:
    """Durable control-plane progress for an in-flight continuous cycle."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.document: dict[str, Any] = {}

    def start(self, *, cycle_attempt_id: str | None = None) -> None:
        rss, peak = process_memory_bytes()
        stamp = _utc_now()
        self.document = {
            "progress_schema": PROGRESS_SCHEMA,
            "cycle_attempt_id": cycle_attempt_id,
            "status": "RUNNING",
            "cycle_started_at": stamp,
            "cycle_finished_at": None,
            "current_stage": None,
            "updated_at": stamp,
            "process_rss_bytes": rss,
            "process_peak_rss_bytes": peak,
            "stages": {},
        }
        self._save()

    def enter_stage(self, stage: str, *, details: Mapping[str, Any] | None = None) -> None:
        rss, peak = process_memory_bytes()
        stamp = _utc_now()
        self.document["current_stage"] = stage
        self.document["updated_at"] = stamp
        self.document["process_rss_bytes"] = rss
        self.document["process_peak_rss_bytes"] = peak
        self.document.setdefault("stages", {})[stage] = {
            "status": "RUNNING", "started_at": stamp, "finished_at": None,
            "elapsed_seconds": None, "rss_entered_bytes": rss,
            "rss_exited_bytes": None, "peak_rss_bytes": peak,
            "details": dict(details or {}),
        }
        self._save()

    def update_stage(self, stage: str, details: Mapping[str, Any]) -> None:
        rss, peak = process_memory_bytes()
        item = self.document.setdefault("stages", {}).setdefault(stage, {})
        item.setdefault("details", {}).update(dict(details))
        item["peak_rss_bytes"] = max(
            (value for value in (item.get("peak_rss_bytes"), peak) if value is not None),
            default=None)
        self.document.update({"updated_at": _utc_now(), "process_rss_bytes": rss,
                              "process_peak_rss_bytes": peak})
        self._save()

    def exit_stage(
        self, stage: str, *, status: str = "COMPLETED",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        rss, peak = process_memory_bytes()
        stamp = _utc_now()
        item = self.document.setdefault("stages", {}).setdefault(stage, {})
        started = datetime.fromisoformat(str(item.get("started_at") or stamp))
        finished = datetime.fromisoformat(stamp)
        item.update({
            "status": status, "finished_at": stamp,
            "elapsed_seconds": round((finished - started).total_seconds(), 6),
            "rss_exited_bytes": rss,
            "peak_rss_bytes": max(
                (value for value in (item.get("peak_rss_bytes"), peak) if value is not None),
                default=None),
        })
        if details:
            item.setdefault("details", {}).update(dict(details))
        self.document.update({"current_stage": None, "updated_at": stamp,
                              "process_rss_bytes": rss, "process_peak_rss_bytes": peak})
        self._save()

    def skip_stage(self, stage: str, reason: str) -> None:
        stamp = _utc_now()
        rss, peak = process_memory_bytes()
        self.document.setdefault("stages", {})[stage] = {
            "status": "SKIPPED", "started_at": stamp, "finished_at": stamp,
            "elapsed_seconds": 0.0, "rss_entered_bytes": rss,
            "rss_exited_bytes": rss, "peak_rss_bytes": peak,
            "details": {"reason": reason},
        }
        self.document["updated_at"] = stamp
        self._save()

    def finish(self, status: str, *, failure_stage: str | None = None) -> None:
        stamp = _utc_now()
        rss, peak = process_memory_bytes()
        self.document.update({
            "status": status, "cycle_finished_at": stamp, "current_stage": failure_stage,
            "updated_at": stamp, "process_rss_bytes": rss,
            "process_peak_rss_bytes": peak,
        })
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.document, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


@dataclass(frozen=True)
class ContinuousResearchCycleResult:
    continuous_cycle_id: str
    cycle_outcome: str
    frontier_snapshot_id: str | None
    question_cycle_id: str | None
    bridge_run_id: str | None
    q71_agenda_id: str | None
    q71_queue_id: str | None
    validation_queue_version: str | None
    projection_version: str | None
    predecessor_cycle_id: str | None
    started_at: str
    completed_at: str
    stage_statuses: dict[str, str]
    failure_stage: str | None = None
    failure_reason: str | None = None
    validation_transitions: tuple[dict[str, Any], ...] = ()
    shadow_eligibility: tuple[dict[str, Any], ...] = ()
    projection_path: str | None = None
    cycle_attempt_id: str | None = None
    evidence_identity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_schema": CYCLE_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContinuousResearchCycleResult":
        if value.get("cycle_schema") != CYCLE_SCHEMA:
            raise ContinuousCycleStateError("CONTINUOUS_CYCLE_SCHEMA_INVALID")
        fields = dict(value)
        fields.pop("cycle_schema", None)
        fields["validation_transitions"] = tuple(fields.get("validation_transitions") or ())
        fields["shadow_eligibility"] = tuple(fields.get("shadow_eligibility") or ())
        return cls(**fields)


class ContinuousCycleStore:
    def __init__(self, directory: Path | str = DEFAULT_CYCLE_STATE_DIRECTORY):
        self.directory = Path(directory)
        self.history_directory = self.directory / "history"
        self.latest_path = self.directory / "latest_success.json"
        self.latest_attempt_path = self.directory / "latest_attempt.json"

    def load_latest_success(self) -> ContinuousResearchCycleResult | None:
        if not self.latest_path.exists():
            return None
        try:
            pointer = json.loads(self.latest_path.read_text(encoding="utf-8"))
            cycle_id = str(pointer["continuous_cycle_id"])
            history = json.loads((self.history_directory / f"{cycle_id}.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ContinuousCycleStateError("LATEST_CONTINUOUS_CYCLE_INVALID") from exc
        result = ContinuousResearchCycleResult.from_dict(history)
        if result.cycle_outcome not in {"COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"}:
            raise ContinuousCycleStateError("LATEST_POINTER_IS_NOT_SUCCESS")
        return result

    def load_latest_attempt(self) -> ContinuousResearchCycleResult | None:
        if not self.latest_attempt_path.exists():
            return None
        try:
            pointer = json.loads(self.latest_attempt_path.read_text(encoding="utf-8"))
            cycle_id = str(pointer["continuous_cycle_id"])
            history = json.loads((self.history_directory / f"{cycle_id}.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ContinuousCycleStateError("LATEST_CONTINUOUS_ATTEMPT_INVALID") from exc
        result = ContinuousResearchCycleResult.from_dict(history)
        if result.continuous_cycle_id != cycle_id:
            raise ContinuousCycleStateError("LATEST_CONTINUOUS_ATTEMPT_ID_MISMATCH")
        return result

    def save(self, result: ContinuousResearchCycleResult, *, successful: bool) -> Path:
        path = self.history_directory / f"{result.continuous_cycle_id}.json"
        self._immutable(path, result.to_dict())
        if successful:
            self._atomic(self.latest_path, {
                "cycle_schema": CYCLE_SCHEMA,
                "continuous_cycle_id": result.continuous_cycle_id,
                "cycle_outcome": result.cycle_outcome,
                "completed_at": result.completed_at,
                "projection_version": result.projection_version,
                "predecessor_cycle_id": result.predecessor_cycle_id,
            })
        self._atomic(self.latest_attempt_path, {
            "cycle_schema": CYCLE_SCHEMA,
            "continuous_cycle_id": result.continuous_cycle_id,
            "cycle_attempt_id": result.cycle_attempt_id,
            "evidence_identity": result.evidence_identity,
            "cycle_outcome": result.cycle_outcome,
            "completed_at": result.completed_at,
            "failure_stage": result.failure_stage,
            "projection_version": result.projection_version,
            "predecessor_cycle_id": result.predecessor_cycle_id,
        })
        return path

    @classmethod
    def _immutable(cls, path: Path, value: Mapping[str, Any]) -> None:
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ContinuousCycleStateError("CONTINUOUS_CYCLE_HISTORY_UNREADABLE") from exc
            if canonical_json(existing) != canonical_json(value):
                raise ContinuousCycleStateError("CONTINUOUS_CYCLE_IDENTITY_COLLISION")
            return
        cls._atomic(path, value)

    @staticmethod
    def _atomic(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise ContinuousCycleStateError("CONTINUOUS_CYCLE_PERSISTENCE_FAILED") from exc


__all__ = [
    "CYCLE_SCHEMA", "PROGRESS_SCHEMA", "ContinuousCycleLease",
    "ContinuousCycleProgressStore",
    "ContinuousCycleStateError", "ContinuousCycleStore",
    "ContinuousResearchCycleResult", "DEFAULT_CYCLE_STATE_DIRECTORY",
    "process_memory_bytes",
]
