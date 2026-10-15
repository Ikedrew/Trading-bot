"""Canonical governed-data frontier coordinator for continuous research.

This module deliberately stops at a verified immutable investigation snapshot.
It does not execute questions or create findings, hypotheses, or candidates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import faulthandler
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, current_schema
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint
from research_engine.data_access.s3_source import (
    GUARD_COHORT_ELIGIBLE,
    ResearchDataSourceError,
    S3ResearchDataSource,
    classify_guard_cohort_record,
    get_default_source,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    EXCLUDED_BY_DESIGN,
    MANIFEST_DIRECTORY,
    OPTIONAL_DATASETS,
    REQUIRED_DATASETS,
    InvestigationSnapshot,
    InvestigationSnapshotError,
    freeze_investigation_snapshot,
    freeze_investigation_snapshot_incremental,
    load_investigation_snapshot_id,
    save_investigation_snapshot,
    verify_investigation_snapshot_identity,
)


COORDINATOR_SCHEMA = "canonical_research_frontier_v1"
STATE_SCHEMA = "canonical_research_frontier_state_v1"
CANDIDATE_SCHEMA = "canonical_research_frontier_candidate_v1"
CHECKPOINT_SCHEMA = "canonical_research_frontier_acquisition_checkpoint_v1"
PARTIAL_CHECKPOINT_SCHEMA = "canonical_research_frontier_partial_dataset_v1"
DEFAULT_STATE_DIRECTORY = MANIFEST_DIRECTORY.parent / "research_frontier"
# Bounded disk budget for one resumable-acquisition checkpoint. A roster that
# would exceed it is simply not persisted (never truncated); the run then
# behaves exactly like a cold acquisition.
DEFAULT_CHECKPOINT_MAX_BYTES = 64 * 1024 * 1024

_EVENT_TIMESTAMP_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_truth": (
        "timestamps.exit_timestamp_broker", "timestamps.entry_timestamp_broker"),
    "execution_results": ("timestamp_utc",),
    "decision_trace": ("timestamp_utc",),
    "execution_attempts": ("timestamp_unix", "timestamp_utc"),
    "execution_context": ("timestamp_utc",),
    "shadow_runtime": (
        "exit_market_time_utc_iso8601", "entry_market_time_utc_iso8601",
        "exit_market_time_utc_epoch_s", "entry_market_time_utc_epoch_s",
        "timestamp_utc",
    ),
    "market_context": ("timestamp_utc",),
    "strategy_observations": ("timestamp_utc",),
    "protection_audit": ("timestamp_utc",),
    "risk_deviation": ("timestamp_utc",),
}

NO_NEW_GOVERNED_EVIDENCE = "NO_NEW_GOVERNED_EVIDENCE"
NEW_GOVERNED_EVIDENCE = "NEW_GOVERNED_EVIDENCE"
FRONTIER_INCOMPLETE = "FRONTIER_INCOMPLETE"
FRONTIER_INVALID = "FRONTIER_INVALID"
SNAPSHOT_READY = "NEW_VERIFIED_IMMUTABLE_SNAPSHOT_READY"


# This is a coordinator-level explanation of the pre-existing investigation
# snapshot policy. The tuples themselves remain owned by investigation_snapshot.
DATASET_SCOPE: dict[str, dict[str, str]] = {
    **{
        name: {
            "classification": "REQUIRED",
            "reason": "Required by the existing common investigation snapshot policy.",
        }
        for name in REQUIRED_DATASETS
    },
    **{
        name: {
            "classification": "OPTIONAL",
            "reason": "Optional supporting evidence in the existing common investigation snapshot policy.",
        }
        for name in OPTIONAL_DATASETS
    },
    **{
        name: {
            "classification": "EXCLUDED",
            "reason": (
                "Excluded by the existing common investigation snapshot policy; "
                "it is derived, separately governed, or outside this upstream frontier."
            ),
        }
        for name in EXCLUDED_BY_DESIGN
    },
    "opportunities": {
        "classification": "EXCLUDED",
        "reason": "Not part of the existing common investigation snapshot scope.",
    },
    "assessments": {
        "classification": "EXCLUDED",
        "reason": "Not part of the existing common investigation snapshot scope.",
    },
    "shadow_candidate": {
        "classification": "EXCLUDED",
        "reason": "Governed local Block 3 evidence with its own snapshot path, not canonical S3.",
    },
    "shadow_candidate_evaluation": {
        "classification": "EXCLUDED",
        "reason": "Governed local Block 3 evidence with its own snapshot path, not canonical S3.",
    },
}


class FrontierCoordinatorError(RuntimeError):
    """The governed frontier could not safely advance."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _substage(progress_callback: Callable[[Mapping[str, Any]], None] | None):
    """Return a safe FRONTIER substage emitter.

    Progress is diagnostic only: a failing or absent callback must never alter
    the governed frontier outcome, so every emit is best-effort and swallows
    callback errors. The emitted payload never contains partial evidence and is
    never written to the completed-cycle/scientific-state authority — the
    caller decides how to persist it.
    """
    def report(phase: str, **values: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({"phase": phase, **values})
        except Exception:
            pass

    return report


class _DiagnosticHeadStackCapture:
    """Opt-in thread dump for a stalled diagnostic HEAD batch only."""

    def __init__(self, dataset: str):
        self._directory = (os.getenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_DIR")
                           if dataset == "trade_truth" else None)
        self._seconds = os.getenv("FRONTIER_DIAGNOSTIC_HEAD_STACK_SECONDS")
        self._handle = None
        self._path: Path | None = None
        self._attempted = False
        self._batch_completed = False

    @staticmethod
    def _note(message: str) -> None:
        try:
            print("FRONTIER_HEAD_STACK_CAPTURE:" + message,
                  file=sys.stderr, flush=True)
        except OSError:
            pass

    def observe(self, counts: Mapping[str, Any]) -> None:
        if (self._attempted or not self._directory or not self._seconds
                or int(counts.get("queued") or 0) == 0
                or int(counts.get("completed") or 0) != 0):
            return
        self._attempted = True
        try:
            delay = float(self._seconds)
            directory = Path(self._directory)
            if not 0 < delay <= 60 or not directory.is_absolute() or not directory.is_dir():
                self._note("invalid_diagnostic_configuration")
                return
            path = directory / "trade_truth_head_threads.log"
            handle = path.open("x", encoding="utf-8")
            try:
                faulthandler.dump_traceback_later(delay, repeat=False, file=handle)
            except Exception:
                handle.close()
                path.unlink(missing_ok=True)
                self._note("arm_failed")
                return
            self._handle, self._path = handle, path
            self._note("armed:" + str(path))
        except (OSError, ValueError) as exc:
            self._note("arm_failed:" + type(exc).__name__)

    def completed(self) -> None:
        """Cancel only after the entire HEAD batch returned normally."""
        self._batch_completed = True
        self.close()

    def close(self) -> None:
        if self._handle is None:
            return
        handle, path = self._handle, self._path
        self._handle, self._path = None, None
        try:
            faulthandler.cancel_dump_traceback_later()
            empty = os.fstat(handle.fileno()).st_size == 0
            if empty and not self._batch_completed:
                # A watchdog interruption also enters the coordinator's finally
                # block. Capture the still-stalled workers before closing the file.
                faulthandler.dump_traceback(file=handle, all_threads=True)
                empty = os.fstat(handle.fileno()).st_size == 0
            handle.close()
            if empty and path is not None:
                path.unlink(missing_ok=True)
            if self._batch_completed:
                self._note("cancelled_after_completion" if empty else "dump_retained")
            else:
                self._note("dump_retained" if not empty else "dump_failed")
        except Exception as exc:
            try:
                handle.close()
            except OSError:
                pass
            self._note("dump_failed:" + type(exc).__name__)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_checkpoint_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Replace a checkpoint after flushing a unique temp file to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=path.stem + "-", suffix=".tmp",
                dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "FRONTIER_HISTORY_UNREADABLE:" + str(path)
            ) from exc
        if canonical_json(existing) != canonical_json(payload):
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "FRONTIER_HISTORY_ID_CONFLICT:" + str(path)
            )
        return
    _atomic_json(path, payload)


def _key_date(identifier: str) -> str:
    for part in identifier.split("/"):
        if part.startswith("date="):
            value = part[5:]
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID, "INVALID_OBJECT_DATE_PARTITION:" + identifier
                ) from exc
    raise FrontierCoordinatorError(
        FRONTIER_INVALID, "OBJECT_WITHOUT_DATE_PARTITION:" + identifier
    )


def _object_identity(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identifier": str(item.get("identifier") or item.get("s3_key") or ""),
        "version_id": None if item.get("version_id") is None else str(item["version_id"]),
        "etag": str(item.get("etag") or ""),
        "content_sha256": str(item.get("content_sha256") or ""),
        "row_count": int(item.get("row_count") or 0),
        "byte_size": int(item.get("byte_size") or item.get("byte_count") or 0),
    }


def _full_object_record(
    dataset: str,
    item: Mapping[str, Any],
    event_time_coverage: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    contract = PRODUCTION_SCHEMA_REGISTRY[dataset]
    return {
        "dataset": dataset,
        "schema_version": current_schema(dataset),
        "s3_key": str(item.get("identifier") or ""),
        "identifier": str(item.get("identifier") or ""),
        "version_id": None if item.get("version_id") is None else str(item["version_id"]),
        "etag": str(item.get("etag") or ""),
        "content_sha256": str(item.get("content_sha256") or ""),
        "last_modified": str(item.get("last_modified") or ""),
        "row_count": int(item.get("row_count") or 0),
        "historical_guard_rows": int(item.get("historical_guard_rows") or 0),
        "byte_count": int(item.get("byte_size") or 0),
        "byte_size": int(item.get("byte_size") or 0),
        "size": int(item.get("size") or 0),
        "listed_byte_count": int(item.get("size") or 0),
        "producer": contract.semantic_owner,
        "authority": "core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY",
        "partition_date": _key_date(str(item.get("identifier") or "")),
        "event_time_coverage": dict(
            event_time_coverage or {"start": None, "end": None}),
    }


def _dig(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _event_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if abs(numeric) > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_time_coverage(
    dataset: str, rows: Sequence[Mapping[str, Any]], as_of_date: date,
) -> dict[str, str | None]:
    observed: list[datetime] = []
    aliases = _EVENT_TIMESTAMP_ALIASES[dataset]
    for row in rows:
        for alias in aliases:
            raw = _dig(row, alias)
            if raw in (None, ""):
                continue
            try:
                observed.append(_event_datetime(raw))
            except (OverflowError, OSError, TypeError, ValueError) as exc:
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID,
                    f"INVALID_GOVERNED_EVENT_TIMESTAMP:{dataset}:{alias}:{raw}",
                ) from exc
            break
    future = [stamp for stamp in observed if stamp.date() > as_of_date]
    if future:
        raise FrontierCoordinatorError(
            FRONTIER_INVALID,
            "FUTURE_DATED_GOVERNED_RECORD:"
            + dataset + ":" + min(future).isoformat(),
        )
    return {
        "start": min(observed).isoformat() if observed else None,
        "end": max(observed).isoformat() if observed else None,
    }


@dataclass(frozen=True)
class FrontierSelection:
    frontier_id: str
    selected_start_time: str
    selected_end_time: str
    dataset_membership: dict[str, dict[str, Any]]
    dataset_status: dict[str, str]
    stale_datasets: tuple[str, ...]
    missing_optional_datasets: tuple[str, ...]
    gap_diagnostics: dict[str, tuple[str, ...]]
    pending_required_objects: dict[str, tuple[str, ...]]
    coherence_decision: str
    predecessor_snapshot_id: str | None
    membership_closed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrontierCycleResult:
    status: str
    snapshot_id: str | None = None
    fingerprint: str | None = None
    investigation_epoch: str | None = None
    frontier_id: str | None = None
    frontier_start: str | None = None
    frontier_end: str | None = None
    predecessor_snapshot_id: str | None = None
    membership_closed_at: str | None = None
    changed_datasets: tuple[str, ...] = ()
    unchanged_datasets: tuple[str, ...] = ()
    missing_optional_datasets: tuple[str, ...] = ()
    stale_datasets: tuple[str, ...] = ()
    new_object_count: int = 0
    replaced_object_count: int = 0
    verification_status: str = "NOT_RUN"
    failure_reason: str | None = None
    dataset_status: dict[str, str] = field(default_factory=dict)
    delta: dict[str, Any] = field(default_factory=dict)
    candidate_status: str | None = None
    candidate_frontier_end: str | None = None
    pending_required_objects: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pending_missing_datasets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FrontierStateStore:
    """Append-only success/failure lineage plus a mutable latest-success read model."""

    def __init__(self, directory: Path | str = DEFAULT_STATE_DIRECTORY):
        self.directory = Path(directory)
        self.history_directory = self.directory / "history"
        self.failure_directory = self.directory / "failures"
        self.candidate_history_directory = self.directory / "candidates"
        self.latest_path = self.directory / "latest_success.json"
        self.latest_candidate_path = self.directory / "latest_candidate.json"

    def load_latest_success(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        try:
            value = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "LATEST_SUCCESS_STATE_UNREADABLE"
            ) from exc
        if value.get("coordinator_schema") != STATE_SCHEMA or value.get("status") != "SUCCESS":
            raise FrontierCoordinatorError(FRONTIER_INVALID, "LATEST_SUCCESS_STATE_INVALID")
        history_path = self.history_directory / f"{value.get('last_successful_snapshot_id')}.json"
        if not history_path.exists():
            raise FrontierCoordinatorError(FRONTIER_INVALID, "LATEST_SUCCESS_HISTORY_MISSING")
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "LATEST_SUCCESS_HISTORY_UNREADABLE"
            ) from exc
        for field_name in (
            "last_successful_snapshot_id",
            "last_successful_snapshot_fingerprint",
            "last_successful_investigation_epoch",
            "completion_timestamp",
        ):
            if value.get(field_name) != history.get(field_name):
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID, "LATEST_SUCCESS_POINTER_MISMATCH:" + field_name
                )
        return history

    def save_success(self, state: Mapping[str, Any]) -> None:
        snapshot_id = str(state.get("last_successful_snapshot_id") or "")
        if not snapshot_id:
            raise FrontierCoordinatorError(FRONTIER_INVALID, "SUCCESS_STATE_WITHOUT_SNAPSHOT_ID")
        history_path = self.history_directory / f"{snapshot_id}.json"
        persisted: Mapping[str, Any] = state
        if history_path.exists():
            try:
                existing = json.loads(history_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID, "FRONTIER_HISTORY_UNREADABLE:" + str(history_path)
                ) from exc
            if (
                existing.get("last_successful_snapshot_fingerprint")
                != state.get("last_successful_snapshot_fingerprint")
                or (existing.get("frontier") or {}).get("frontier_id")
                != (state.get("frontier") or {}).get("frontier_id")
            ):
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID, "FRONTIER_HISTORY_ID_CONFLICT:" + str(history_path)
                )
            # Repair a missing/stale pointer after a crash without rewriting the
            # immutable history record or changing its completion timestamp.
            persisted = existing
        else:
            _write_immutable(history_path, state)
        pointer = {
            "coordinator_schema": STATE_SCHEMA,
            "status": "SUCCESS",
            "last_successful_snapshot_id": persisted["last_successful_snapshot_id"],
            "last_successful_snapshot_fingerprint": persisted[
                "last_successful_snapshot_fingerprint"],
            "last_successful_investigation_epoch": persisted[
                "last_successful_investigation_epoch"],
            "frontier_id": (persisted.get("frontier") or {}).get("frontier_id"),
            "predecessor_snapshot_id": persisted.get("predecessor_snapshot_id"),
            "completion_timestamp": persisted["completion_timestamp"],
            "history_file": str(history_path.name),
        }
        _atomic_json(self.latest_path, pointer)

    def save_failure(self, result: FrontierCycleResult, frontier: Mapping[str, Any] | None) -> None:
        material = {
            "coordinator_schema": STATE_SCHEMA,
            "status": "FAILED",
            "failure_status": result.status,
            "failure_reason": result.failure_reason,
            "frontier": frontier,
        }
        failure_id = "FRONTIER-FAIL-" + fingerprint(material)[:24].upper()
        payload = {**material, "failure_id": failure_id, "completion_timestamp": _utc_now().isoformat()}
        path = self.failure_directory / f"{failure_id}.json"
        # Re-observing the same deterministic failure is not a new lineage event.
        if path.exists():
            return
        _write_immutable(path, payload)

    def load_latest_candidate(self) -> dict[str, Any] | None:
        if not self.latest_candidate_path.exists():
            return None
        try:
            value = json.loads(self.latest_candidate_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "LATEST_CANDIDATE_STATE_UNREADABLE"
            ) from exc
        if value.get("candidate_schema") != CANDIDATE_SCHEMA:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "LATEST_CANDIDATE_STATE_INVALID")
        return value

    def save_incomplete_candidate(
        self, result: FrontierCycleResult, frontier: FrontierSelection,
    ) -> None:
        material = {
            "candidate_schema": CANDIDATE_SCHEMA,
            "candidate_status": "CANDIDATE_INCOMPLETE",
            "last_coherent_snapshot_id": result.snapshot_id,
            "last_coherent_snapshot_fingerprint": result.fingerprint,
            "candidate_frontier_start": frontier.selected_start_time,
            "candidate_frontier_end": result.candidate_frontier_end,
            "pending_required_objects": {
                name: list(items)
                for name, items in sorted(result.pending_required_objects.items())
            },
            "missing_required_datasets": list(result.pending_missing_datasets),
            "coherence_decision": "INCOMPLETE_NOT_PROMOTABLE_RETAIN_LAST_COHERENT",
        }
        candidate_id = "RFRONTIER-CANDIDATE-" + fingerprint(material)[:24].upper()
        payload = {
            **material,
            "candidate_id": candidate_id,
            "observed_at": _utc_now().isoformat(),
        }
        history_path = self.candidate_history_directory / f"{candidate_id}.json"
        if not history_path.exists():
            _write_immutable(history_path, payload)
        _atomic_json(self.latest_candidate_path, payload)

    def save_candidate_promotion(
        self, *, snapshot: InvestigationSnapshot, frontier: FrontierSelection,
    ) -> None:
        previous = self.load_latest_candidate()
        payload = {
            "candidate_schema": CANDIDATE_SCHEMA,
            "candidate_status": "PROMOTED",
            "candidate_id": "RFRONTIER-CANDIDATE-" + fingerprint({
                "snapshot_id": snapshot.snapshot_id,
                "frontier_id": frontier.frontier_id,
            })[:24].upper(),
            "promoted_snapshot_id": snapshot.snapshot_id,
            "promoted_snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "frontier_id": frontier.frontier_id,
            "candidate_frontier_start": frontier.selected_start_time,
            "candidate_frontier_end": frontier.selected_end_time,
            "pending_required_objects": {},
            "missing_required_datasets": [],
            "coherence_decision": "COHERENT_CANDIDATE_ATOMICALLY_PROMOTED",
            "predecessor_candidate_id": (
                None if previous is None else previous.get("candidate_id")),
            "observed_at": _utc_now().isoformat(),
        }
        history_path = self.candidate_history_directory / f"{payload['candidate_id']}.json"
        if not history_path.exists():
            _write_immutable(history_path, payload)
        _atomic_json(self.latest_candidate_path, payload)


def _checkpoint_boundary(source: S3ResearchDataSource) -> dict[str, Any]:
    """Source authority that must be unchanged for a checkpoint to be reusable.

    A checkpoint only ever reuses *verified immutable object identities*; a
    changed listing, changed object identity, or changed content is detected per
    object at resume time (see :func:`_same_frontier_listing_identity` and the
    fresh authority HEAD). This boundary therefore binds only the authority that
    would invalidate the stored verified content outright: the canonical schema
    versions and the bucket/region/profile the identities were resolved against.
    """
    return {
        "schemas": {name: current_schema(name) for name in BOUND_DATASETS},
        "bucket": source.bucket,
        "region": str(getattr(source, "_region", "")),
        "research_profile": source.research_profile,
    }


class FrontierAcquisitionCheckpointStore:
    """Atomic, integrity-validated progress store for one frontier acquisition.

    This is deliberately **not** a snapshot authority and is never consulted by
    question evaluation or any downstream scientific stage. It records, per
    completed dataset, the exact verified immutable object roster (identity,
    version, content digest, byte size, row count and event-time coverage) that a
    completed FRONTIER run would have produced. An interrupted attempt leaves a
    partial checkpoint; the next attempt reuses those datasets after revalidating
    their S3 authority instead of re-downloading or re-decoding their bodies.

    Only ``latest_success`` / the immutable manifests can advance the frontier.
    A checkpoint that is unreadable, malformed, integrity-failing,
    boundary-mismatched or superseded by a newer success is rejected safely by
    :meth:`load` (it returns ``None``) so the caller performs a full acquisition.
    """

    def __init__(
        self, directory: Path | str, *,
        max_bytes: int = DEFAULT_CHECKPOINT_MAX_BYTES,
    ):
        self.directory = Path(directory)
        self.path = self.directory / "acquisition_checkpoint.json"
        self.partial_path = self.directory / "in_progress.json"
        self.max_bytes = max(0, int(max_bytes))

    def _fingerprint(
        self, boundary: Mapping[str, Any], datasets: Mapping[str, Any],
    ) -> str:
        return fingerprint({
            "checkpoint_schema": CHECKPOINT_SCHEMA,
            "boundary": dict(boundary),
            "datasets": dict(datasets),
        })

    @staticmethod
    def _is_newer(updated_at: str, reference: str) -> bool:
        try:
            return (datetime.fromisoformat(updated_at)
                    > datetime.fromisoformat(reference))
        except (TypeError, ValueError):
            return False

    def load(
        self, *, boundary: Mapping[str, Any], not_before: str | None = None,
    ) -> dict[str, Any] | None:
        """Return reusable dataset rosters, or ``None`` when unusable."""
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (not isinstance(value, Mapping)
                or value.get("checkpoint_schema") != CHECKPOINT_SCHEMA):
            return None
        datasets = value.get("datasets")
        if not isinstance(datasets, Mapping):
            return None
        if value.get("checkpoint_fingerprint") != self._fingerprint(
                value.get("boundary") or {}, datasets):
            return None
        if canonical_json(value.get("boundary") or {}) != canonical_json(
                dict(boundary)):
            return None
        if not_before:
            if not self._is_newer(str(value.get("updated_at") or ""),
                                  str(not_before)):
                return None
        return {
            name: dict(item) for name, item in datasets.items()
            if isinstance(item, Mapping)
        }

    def save(
        self, *, boundary: Mapping[str, Any], datasets: Mapping[str, Any],
    ) -> bool:
        """Persist the accumulated rosters atomically; ``False`` if over budget."""
        payload = {
            "checkpoint_schema": CHECKPOINT_SCHEMA,
            "boundary": dict(boundary),
            "updated_at": _utc_now().isoformat(),
            "dataset_count": len(datasets),
            "object_count": sum(
                len((item or {}).get("objects") or ())
                for item in datasets.values() if isinstance(item, Mapping)),
            "datasets": {name: dict(item) for name, item in datasets.items()},
            "checkpoint_fingerprint": self._fingerprint(boundary, datasets),
        }
        try:
            encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        except (TypeError, ValueError):
            return False
        if len(encoded.encode("utf-8")) > self.max_bytes:
            # Bounded disk: skip rather than persist a truncated roster.
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_checkpoint_json(self.path, payload)
        return True

    def clear(self) -> None:
        """Drop the checkpoint once a full success supersedes it."""
        try:
            self.path.unlink(missing_ok=True)
            self.partial_path.unlink(missing_ok=True)
        except OSError:
            pass

    def load_partial(self, *, boundary: Mapping[str, Any],
                     not_before: str | None = None) -> dict[str, Any] | None:
        """Load an unfinished dataset only as a fresh-authority reuse prior."""
        try:
            value = json.loads(self.partial_path.read_text(encoding="utf-8"))
            records = value["objects"]
            if (value.get("checkpoint_schema") != PARTIAL_CHECKPOINT_SCHEMA
                    or value.get("boundary") != dict(boundary)
                    or value.get("dataset") not in BOUND_DATASETS
                    or not isinstance(records, list)
                    or len(records) != len({str(item["s3_key"]) for item in records})
                    or any(not isinstance(item, Mapping)
                           or item.get("version_id") in (None, "", "null")
                           or not item.get("content_sha256") for item in records)
                    or value.get("checkpoint_fingerprint") != fingerprint({
                        "checkpoint_schema": PARTIAL_CHECKPOINT_SCHEMA,
                        "boundary": dict(boundary),
                        "dataset": value["dataset"], "objects": records,
                    })
                    or (not_before and not self._is_newer(
                        str(value.get("updated_at") or ""), not_before))):
                return None
            return {"dataset": value["dataset"], "objects": records}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save_partial(self, *, boundary: Mapping[str, Any], dataset: str,
                     objects: Sequence[Mapping[str, Any]]) -> bool:
        """Atomically retain only fully verified, versioned object records."""
        records = [dict(item) for item in objects
                   if item.get("version_id") not in (None, "", "null")]
        material = {"checkpoint_schema": PARTIAL_CHECKPOINT_SCHEMA,
                    "boundary": dict(boundary), "dataset": dataset,
                    "objects": records}
        payload = {**material, "updated_at": _utc_now().isoformat(),
                   "checkpoint_fingerprint": fingerprint(material)}
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_bytes:
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_checkpoint_json(self.partial_path, payload)
        return True

    def clear_partial(self) -> None:
        self.partial_path.unlink(missing_ok=True)


def _same_frontier_listing_identity(
    prior: Mapping[str, Any], current: Mapping[str, Any],
) -> bool:
    """Compare the immutable identity fields supplied by ListObjectsV2."""
    return (
        str(prior.get("s3_key") or prior.get("identifier") or "")
        == str(current.get("identifier") or "")
        and str(prior.get("etag") or "") == str(current.get("etag") or "")
        and int(prior.get("listed_byte_count") or prior.get("byte_count") or 0)
        == int(current.get("size") or 0)
        and str(prior.get("last_modified") or "")
        == str(current.get("last_modified") or "")
    )


def _same_frontier_object_identity(
    prior: Mapping[str, Any], current: Mapping[str, Any],
) -> bool:
    if not _same_frontier_listing_identity(prior, current):
        return False
    prior_version = prior.get("version_id")
    current_version = current.get("version_id")
    if (prior_version is None) != (current_version is None):
        return False
    return prior_version is None or str(prior_version) == str(current_version)


def _merge_event_coverage(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, str | None]:
    starts = [str(item["start"]) for item in (prior, current)
              if item and item.get("start")]
    ends = [str(item["end"]) for item in (prior, current)
            if item and item.get("end")]
    return {
        "start": min(starts) if starts else None,
        "end": max(ends) if ends else None,
    }


def _coverage_from_object_records(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, str | None] | None:
    coverage: dict[str, str | None] = {"start": None, "end": None}
    for record in records:
        contribution = record.get("event_time_coverage")
        if not isinstance(contribution, Mapping):
            return None
        coverage = _merge_event_coverage(coverage, contribution)
    return coverage


def _read_frontier_object(
    source: S3ResearchDataSource,
    dataset: str,
    item: Mapping[str, Any],
    as_of_date: date,
    *,
    fresh_authority: bool = False,
) -> dict[str, Any]:
    rows = source.read_objects_for_freeze(
        dataset, (item,), expected_schema_version=current_schema(dataset),
        _fresh_authority=fresh_authority)
    key = str(item.get("identifier") or "")
    canonical_rows = [
        row for row in rows
        if classify_guard_cohort_record(dataset, row, key) != GUARD_COHORT_ELIGIBLE
    ]
    contribution = _event_time_coverage(dataset, canonical_rows, as_of_date)
    malformed = source.malformed_report(dataset)
    if malformed and malformed.malformed_lines:
        raise FrontierCoordinatorError(
            FRONTIER_INVALID,
            f"MALFORMED_GOVERNED_OBJECT_ROWS:{dataset}:{malformed.malformed_lines}",
        )
    metadata = source.object_metadata(dataset)
    if len(metadata) != 1:
        raise FrontierCoordinatorError(
            FRONTIER_INVALID,
            "OBJECT_DISCOVERY_VERIFICATION_COUNT_MISMATCH:" + dataset,
        )
    record = _full_object_record(dataset, metadata[0], contribution)
    record["historical_guard_rows"] = source.guard_cohort_count(
        dataset, str(item.get("identifier") or ""))
    if not _same_frontier_listing_identity(record, item):
        raise FrontierCoordinatorError(
            FRONTIER_INVALID, "OBJECT_CHANGED_DURING_DISCOVERY:" + dataset)
    del rows
    return record


def _discover_verified(
    source: S3ResearchDataSource,
    *,
    as_of_date: date,
    previous: Mapping[str, Any] | None = None,
    resume: Mapping[str, Any] | None = None,
    partial: Mapping[str, Any] | None = None,
    on_partial_verified: Callable[
        [str, Sequence[Mapping[str, Any]]], None
    ] | None = None,
    on_dataset_verified: Callable[
        [str, Sequence[Mapping[str, Any]], Mapping[str, Any]], None
    ] | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    metrics: dict[str, Any] | None = None,
) -> tuple[
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, dict[str, str | None]],
    str,
]:
    """Close one roster and download only added or replaced object bodies.

    ``resume`` is an optional ``{dataset: {"objects": [...],
    "event_time_coverage": {...}}}`` roster carried over from an interrupted
    attempt of the same acquisition boundary. It is used *only* as a reuse prior:
    every resumed object is still matched against fresh S3 authority (listing
    identity plus a fresh HEAD for versioned objects) and re-read when changed, so
    the resulting roster is byte-identical to a cold acquisition.
    ``on_dataset_verified`` is a best-effort hook fired once per completed dataset
    so a caller can checkpoint progress; it can never change the governed result.
    ``metrics`` is an optional diagnostic accumulator (never consulted by the
    governed result). When supplied it is filled with ``datasets_recovered``,
    ``objects_reused``, ``revalidation_seconds`` (fresh authority HEAD time spent
    revalidating recovered datasets), ``recovered_datasets_seconds`` and
    ``remaining_datasets_seconds`` so the caller can report recovery timing.
    """
    emit = _substage(progress_callback)
    listed_by_dataset = {
        dataset: tuple(source.discover_dataset_objects(dataset))
        for dataset in BOUND_DATASETS
    }
    membership_closed_at = _utc_now().isoformat()
    prior_objects = _prior_objects(previous)
    prior_membership = ((previous or {}).get("frontier") or {}).get(
        "dataset_membership") or {}
    resume_membership = dict(resume or {})
    resume_objects = (_prior_objects(
        {"frontier": {"dataset_membership": resume_membership}})
        if resume_membership else {})
    partial_dataset = str((partial or {}).get("dataset") or "")
    partial_objects = list((partial or {}).get("objects") or ())
    if metrics is not None:
        metrics["datasets_recovered"] = len(resume_membership)
        metrics["objects_reused"] = 0
        metrics["revalidation_seconds"] = 0.0
        metrics["recovered_datasets_seconds"] = 0.0
        metrics["remaining_datasets_seconds"] = 0.0
    verified: dict[str, tuple[dict[str, Any], ...]] = {}
    event_coverage: dict[str, dict[str, str | None]] = {}

    for dataset in BOUND_DATASETS:
        listed = listed_by_dataset[dataset]
        # A resume roster reflects verified work from an interrupted attempt of
        # this same boundary and is at least as fresh as the last success (a
        # success clears it), so it takes precedence as the reuse prior.
        reuse_prior = resume_objects.get(dataset) or prior_objects.get(dataset, ())
        prior_by_key = {
            str(item.get("s3_key") or item.get("identifier") or ""): dict(item)
            for item in reuse_prior
        }
        if dataset == partial_dataset and not resume_objects.get(dataset):
            prior_by_key.update({
                str(item["s3_key"]): dict(item) for item in partial_objects
            })
        listed_by_key = {
            str(item.get("identifier") or ""): dict(item) for item in listed
        }
        records: dict[str, dict[str, Any]] = {}
        to_read: dict[str, dict[str, Any]] = {}
        version_checks: list[dict[str, Any]] = []
        reused_keys: set[str] = set()

        for key, item in listed_by_key.items():
            prior = prior_by_key.get(key)
            if (prior is None or not _same_frontier_listing_identity(prior, item)
                    or (dataset == "decision_trace"
                        and "historical_guard_rows" not in prior)):
                to_read[key] = item
            elif prior.get("version_id") is None and item.get("version_id") is None:
                records[key] = prior
                reused_keys.add(key)
            else:
                version_checks.append(item)

        dataset_started = time.perf_counter()
        emit("FRONTIER_DATASET_STARTED", dataset=dataset,
             dataset_index=len(verified) + 1, dataset_total=len(BOUND_DATASETS),
             listed_objects=len(listed), initially_selected_for_read=len(to_read),
             objects_requiring_head=len(version_checks),
             source_io_cumulative=dict(source.performance))
        dataset_head_seconds = 0.0
        headed_keys: set[str] = set()
        if version_checks:
            head_started = time.perf_counter()
            stack_capture = _DiagnosticHeadStackCapture(dataset)

            emit("FRONTIER_VERSION_REVALIDATION_STARTED", dataset=dataset,
                 dataset_index=len(verified) + 1,
                 dataset_total=len(BOUND_DATASETS), total=len(version_checks),
                 source_io_cumulative=source.performance_snapshot())

            def version_progress(counts: Mapping[str, Any]) -> None:
                stack_capture.observe(counts)
                phase = ("FRONTIER_VERSION_REVALIDATION_CLIENT_READY"
                         if counts.get("stage") == "client_ready"
                         else "FRONTIER_VERSION_REVALIDATION_PROGRESS")
                emit(phase, dataset=dataset,
                     dataset_index=len(verified) + 1,
                     dataset_total=len(BOUND_DATASETS), **dict(counts),
                     source_io_cumulative=source.performance_snapshot())

            try:
                headed = source.head_bound_objects(
                    dataset, version_checks,
                    expected_schema_version=current_schema(dataset),
                    progress_callback=version_progress)
                stack_capture.completed()
            finally:
                stack_capture.close()
            for item in headed:
                key = str(item.get("identifier") or "")
                headed_keys.add(key)
                prior = prior_by_key[key]
                if _same_frontier_object_identity(prior, item):
                    records[key] = prior
                    reused_keys.add(key)
                else:
                    to_read[key] = dict(item)
            dataset_head_seconds += time.perf_counter() - head_started

        candidates = {
            str(item["identifier"]) for item in source.cached_authority_candidates(
                dataset, tuple(item for key, item in to_read.items()
                               if key not in headed_keys))
        }
        authority_done = {name: 0 for name in
                          ("queued", "completed", "successful", "failed", "retried")}
        read_total = len(to_read)
        last_object_progress = dataset_started
        read_items = list(to_read.items())
        for offset in range(0, read_total, 64):
            batch = read_items[offset:offset + 64]
            head_inputs = [item for key, item in batch if key in candidates]
            if head_inputs:
                before = dict(authority_done)
                retries_before = source.performance_snapshot()["head_retries"]

                def authority_progress(counts: Mapping[str, int]) -> None:
                    cumulative = {name: before[name] + counts[name]
                                  for name in authority_done}
                    emit("FRONTIER_AUTHORITY_CHECKS", dataset=dataset,
                         dataset_index=len(verified) + 1,
                         dataset_total=len(BOUND_DATASETS), **cumulative,
                         total=len(candidates),
                         source_io_cumulative=source.performance_snapshot())

                for item in source.head_bound_objects(
                        dataset, head_inputs,
                        expected_schema_version=current_schema(dataset),
                        max_workers=8, progress_callback=authority_progress):
                    key = str(item["identifier"])
                    to_read[key] = dict(item)
                    headed_keys.add(key)
                authority_done["queued"] += len(head_inputs)
                authority_done["completed"] += len(head_inputs)
                authority_done["successful"] += len(head_inputs)
                authority_done["retried"] += (
                    source.performance_snapshot()["head_retries"] - retries_before)

            for read_index, (key, _) in enumerate(batch, offset + 1):
                records[key] = _read_frontier_object(
                    source, dataset, to_read[key], as_of_date,
                    fresh_authority=key in headed_keys)
                now = time.perf_counter()
                if (read_index == 1 or read_index % 25 == 0 or read_index == read_total
                        or now - last_object_progress >= 5.0):
                    emit("FRONTIER_DATASET_READING", dataset=dataset,
                         dataset_index=len(verified) + 1,
                         dataset_total=len(BOUND_DATASETS),
                         objects_processed=read_index, objects_selected_for_read=read_total,
                         elapsed_seconds=round(now - dataset_started, 3),
                         source_io_cumulative=dict(source.performance))
                    last_object_progress = now

            # Save only complete verified records, after a full read batch.
            # The next attempt still lists and HEADs each versioned object.
            if on_partial_verified is not None and read_total:
                on_partial_verified(dataset, tuple(records.values()))

        current_keys = set(listed_by_key)
        prior_keys = set(prior_by_key)
        added_keys = current_keys - prior_keys
        removed_keys = prior_keys - current_keys
        replaced_keys = {
            key for key in current_keys & prior_keys if key in to_read
        }
        ordered = [records[key] for key in sorted(records)]
        prior_coverage = dict(
            (resume_membership.get(dataset) or {}).get("event_time_coverage")
            or (prior_membership.get(dataset) or {}).get("event_time_coverage")
            or {"start": None, "end": None})

        if not added_keys and not replaced_keys and not removed_keys:
            coverage = prior_coverage
        elif added_keys and not replaced_keys and not removed_keys:
            added_coverage = _coverage_from_object_records(
                [records[key] for key in sorted(added_keys)])
            coverage = _merge_event_coverage(prior_coverage, added_coverage)
        else:
            coverage = _coverage_from_object_records(ordered)
            if coverage is None:
                # Compatibility fallback for predecessor states created before
                # per-object event coverage was persisted. Re-read only the
                # affected dataset once; subsequent epochs remain delta-only.
                for key, record in list(records.items()):
                    if not isinstance(record.get("event_time_coverage"), Mapping):
                        refreshed = _read_frontier_object(
                            source, dataset, listed_by_key[key], as_of_date)
                        if not _same_frontier_object_identity(record, refreshed):
                            raise FrontierCoordinatorError(
                                FRONTIER_INVALID,
                                "OBJECT_CHANGED_DURING_COVERAGE_RECOMPUTE:" + dataset)
                        records[key] = refreshed
                ordered = [records[key] for key in sorted(records)]
                coverage = _coverage_from_object_records(ordered)
            if coverage is None:
                raise FrontierCoordinatorError(
                    FRONTIER_INVALID, "EVENT_COVERAGE_RECOMPUTE_FAILED:" + dataset)

        verified[dataset] = tuple(ordered)
        event_coverage[dataset] = coverage
        dataset_elapsed = time.perf_counter() - dataset_started
        dataset_reused = len(reused_keys)
        if metrics is not None:
            metrics["objects_reused"] += dataset_reused
            if dataset in resume_membership:
                metrics["revalidation_seconds"] += dataset_head_seconds
                metrics["recovered_datasets_seconds"] += dataset_elapsed
            else:
                metrics["remaining_datasets_seconds"] += dataset_elapsed
        if on_dataset_verified is not None:
            try:
                on_dataset_verified(dataset, verified[dataset], coverage)
            except Exception:
                pass
        emit("FRONTIER_DATASET_VERIFIED", dataset=dataset,
             dataset_index=len(verified), dataset_total=len(BOUND_DATASETS),
             objects=len(ordered), downloaded=len(to_read),
             objects_reused=dataset_reused,
             objects_selected_for_read=len(to_read),
             added=len(added_keys), replaced=len(replaced_keys),
             removed=len(removed_keys),
             elapsed_seconds=round(dataset_elapsed, 3),
             revalidation_seconds=round(dataset_head_seconds, 3),
             source_io_cumulative=dict(source.performance))

    return verified, event_coverage, membership_closed_at


def _select_frontier(
    discovered: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    event_coverage: Mapping[str, Mapping[str, str | None]],
    predecessor_snapshot_id: str | None,
    as_of_date: date,
    membership_closed_at: str | None = None,
    frozen_start_date: str | None = None,
    frozen_end_date: str | None = None,
) -> FrontierSelection:
    for dataset in REQUIRED_DATASETS:
        if not discovered.get(dataset):
            raise FrontierCoordinatorError(
                FRONTIER_INCOMPLETE, "REQUIRED_DATASET_ABSENT:" + dataset
            )
    today = as_of_date.isoformat()
    for dataset in BOUND_DATASETS:
        future = sorted({str(item["partition_date"]) for item in discovered[dataset]
                         if str(item["partition_date"]) > today})
        if future:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID,
                "FUTURE_DATED_GOVERNED_EVIDENCE:" + dataset + ":" + ",".join(future),
            )

    required_dates = {
        name: sorted({str(item["partition_date"]) for item in discovered[name]})
        for name in REQUIRED_DATASETS
    }

    # When a frozen baseline snapshot exists, respect its date boundaries
    # to preserve immutable membership across incremental cycles.
    if frozen_start_date is not None and frozen_end_date is not None:
        start = frozen_start_date
        end = frozen_end_date
    else:
        start = max(values[0] for values in required_dates.values())
        end = min(values[-1] for values in required_dates.values())

    if start > end:
        raise FrontierCoordinatorError(
            FRONTIER_INCOMPLETE,
            "REQUIRED_DATASET_COVERAGE_DOES_NOT_OVERLAP:"
            + canonical_json(required_dates),
        )

    membership: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    missing_optional: list[str] = []
    stale: list[str] = []
    selected_dates: dict[str, set[str]] = {}
    for dataset in BOUND_DATASETS:
        all_objects = tuple(discovered.get(dataset, ()))
        selected = tuple(
            item for item in all_objects if start <= str(item["partition_date"]) <= end
        )
        requirement = "REQUIRED" if dataset in REQUIRED_DATASETS else "OPTIONAL"
        if requirement == "REQUIRED" and not selected:
            raise FrontierCoordinatorError(
                FRONTIER_INCOMPLETE, "REQUIRED_DATASET_CANNOT_SUPPORT_FRONTIER:" + dataset
            )
        if selected:
            status = "READY"
        elif all_objects:
            status = "STALE"
            stale.append(dataset)
        else:
            status = "MISSING"
            if requirement == "OPTIONAL":
                missing_optional.append(dataset)
        statuses[dataset] = status
        selected_dates[dataset] = {str(item["partition_date"]) for item in selected}
        membership[dataset] = {
            "classification": requirement,
            "status": status,
            "schema_version": current_schema(dataset),
            "producer": PRODUCTION_SCHEMA_REGISTRY[dataset].semantic_owner,
            "event_time_coverage": dict(event_coverage[dataset]),
            "objects": [dict(item) for item in selected],
        }

    all_selected_dates = set().union(*(selected_dates[name] for name in REQUIRED_DATASETS))
    gaps = {
        name: tuple(sorted(all_selected_dates - selected_dates[name]))
        for name in BOUND_DATASETS
        if all_selected_dates - selected_dates[name]
    }
    identity = {
        "coordinator_schema": COORDINATOR_SCHEMA,
        "selected_start_time": start,
        "selected_end_time": end,
        "dataset_membership": membership,
        "coherence_decision": "COHERENT_COMMON_DATE_PARTITION_INTERSECTION",
    }
    frontier_id = "RFRONTIER-" + fingerprint(identity)[:24].upper()
    pending_required = {
        name: tuple(
            str(item["s3_key"]) for item in discovered[name]
            if str(item["partition_date"]) > end
        )
        for name in REQUIRED_DATASETS
        if any(str(item["partition_date"]) > end for item in discovered[name])
    }
    return FrontierSelection(
        frontier_id=frontier_id,
        selected_start_time=start,
        selected_end_time=end,
        dataset_membership=membership,
        dataset_status=statuses,
        stale_datasets=tuple(sorted(stale)),
        missing_optional_datasets=tuple(sorted(missing_optional)),
        gap_diagnostics=gaps,
        pending_required_objects=pending_required,
        coherence_decision="COHERENT_COMMON_DATE_PARTITION_INTERSECTION",
        predecessor_snapshot_id=predecessor_snapshot_id,
        membership_closed_at=membership_closed_at,
    )


def _prior_objects(state: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not state:
        return {}
    frontier = state.get("frontier") or {}
    membership = frontier.get("dataset_membership") or {}
    return {
        name: list((membership.get(name) or {}).get("objects") or ())
        for name in BOUND_DATASETS
    }


def _compute_delta(
    frontier: FrontierSelection, previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    previous_objects = _prior_objects(previous)
    changed: list[str] = []
    unchanged: list[str] = []
    new_objects: list[dict[str, Any]] = []
    replaced_objects: list[dict[str, Any]] = []
    dataset_delta: dict[str, Any] = {}
    for dataset in BOUND_DATASETS:
        current = list(frontier.dataset_membership[dataset]["objects"])
        old = previous_objects.get(dataset, [])
        current_by_key = {str(item["s3_key"]): item for item in current}
        old_by_key = {str(item["s3_key"]): item for item in old}
        added_keys = sorted(set(current_by_key) - set(old_by_key))
        removed_keys = sorted(set(old_by_key) - set(current_by_key))
        replaced_keys = sorted(
            key for key in set(current_by_key) & set(old_by_key)
            if _object_identity(current_by_key[key]) != _object_identity(old_by_key[key])
        )
        for key in added_keys:
            new_objects.append({"dataset": dataset, **current_by_key[key]})
        for key in replaced_keys:
            replaced_objects.append({
                "dataset": dataset,
                "s3_key": key,
                "before": old_by_key[key],
                "after": current_by_key[key],
            })
        is_changed = bool(added_keys or removed_keys or replaced_keys)
        (changed if is_changed else unchanged).append(dataset)
        old_rows = sum(int(item.get("row_count") or 0) for item in old)
        new_rows = sum(int(item.get("row_count") or 0) for item in current)
        old_dates = sorted({str(item.get("partition_date")) for item in old})
        new_dates = sorted({str(item.get("partition_date")) for item in current})
        dataset_delta[dataset] = {
            "new_object_keys": added_keys,
            "removed_object_keys": removed_keys,
            "replaced_object_keys": replaced_keys,
            "row_count_before": old_rows,
            "row_count_after": new_rows,
            "row_count_delta": new_rows - old_rows,
            "time_coverage_before": {
                "start": old_dates[0] if old_dates else None,
                "end": old_dates[-1] if old_dates else None,
            },
            "time_coverage_after": {
                "start": new_dates[0] if new_dates else None,
                "end": new_dates[-1] if new_dates else None,
            },
        }
    return {
        "changed_datasets": tuple(changed),
        "unchanged_datasets": tuple(unchanged),
        "new_objects": new_objects,
        "replaced_objects": replaced_objects,
        "new_object_count": len(new_objects),
        "replaced_object_count": len(replaced_objects),
        "datasets": dataset_delta,
        "stale_datasets": frontier.stale_datasets,
        "missing_optional_datasets": frontier.missing_optional_datasets,
    }


def _pending_candidate(
    frontier: FrontierSelection,
) -> tuple[str | None, tuple[str, ...]]:
    """Describe the newest unpromotable required-date candidate.

    ``pending_required_objects`` contains only required datasets already seen
    beyond the common coherent end.  Required datasets absent at the newest
    observed date are therefore the exact members still needed for promotion.
    """
    dates_by_dataset = {
        name: {_key_date(key) for key in items}
        for name, items in frontier.pending_required_objects.items()
    }
    candidate_dates = set().union(*dates_by_dataset.values()) if dates_by_dataset else set()
    if not candidate_dates:
        return None, ()
    newest = max(candidate_dates)
    present = {
        name for name, dates in dates_by_dataset.items() if newest in dates
    }
    return newest, tuple(sorted(set(REQUIRED_DATASETS) - present))


def _failure_result(
    error: FrontierCoordinatorError,
    previous: Mapping[str, Any] | None,
    frontier: FrontierSelection | None = None,
) -> FrontierCycleResult:
    return FrontierCycleResult(
        status=error.status,
        snapshot_id=(None if previous is None else previous.get("last_successful_snapshot_id")),
        fingerprint=(None if previous is None else previous.get("last_successful_snapshot_fingerprint")),
        investigation_epoch=(None if previous is None else previous.get("last_successful_investigation_epoch")),
        frontier_id=None if frontier is None else frontier.frontier_id,
        frontier_start=None if frontier is None else frontier.selected_start_time,
        frontier_end=None if frontier is None else frontier.selected_end_time,
        predecessor_snapshot_id=(None if previous is None else previous.get("last_successful_snapshot_id")),
        membership_closed_at=(
            None if frontier is None else frontier.membership_closed_at),
        missing_optional_datasets=(() if frontier is None else frontier.missing_optional_datasets),
        stale_datasets=(() if frontier is None else frontier.stale_datasets),
        verification_status="FAILED",
        failure_reason=error.reason,
        dataset_status=({} if frontier is None else frontier.dataset_status),
    )


def _assert_snapshot_membership(
    snapshot: InvestigationSnapshot, frontier: FrontierSelection,
) -> None:
    bindings = {binding.dataset: binding for binding in snapshot.datasets}
    for dataset in BOUND_DATASETS:
        expected = [
            _object_identity(item)
            for item in frontier.dataset_membership[dataset]["objects"]
        ]
        actual = [_object_identity(item.to_dict()) for item in bindings[dataset].objects]
        if expected != actual:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "SNAPSHOT_FRONTIER_MEMBERSHIP_MISMATCH:" + dataset
            )


def _materialize_snapshot(
    *,
    freezer: Callable[..., InvestigationSnapshot],
    incremental_freezer: Callable[..., InvestigationSnapshot],
    predecessor_snapshot_id: str | None,
    manifest_directory: Path,
    start_date: str,
    end_date: str,
    source: S3ResearchDataSource,
    object_membership: Mapping[str, Sequence[Mapping[str, Any]]],
) -> InvestigationSnapshot:
    """Reuse predecessor object authority when safe; otherwise freeze fully.

    A missing or unreadable predecessor artifact falls back to a full clean
    freeze rather than risking a partially-reused snapshot. A present predecessor
    is passed to the incremental freezer, which re-reads only changed datasets.
    """
    if predecessor_snapshot_id is None:
        return freezer(
            start_date=start_date,
            end_date=end_date,
            source=source,
            object_membership=object_membership,
        )
    try:
        predecessor = load_investigation_snapshot_id(
            predecessor_snapshot_id, manifest_directory=manifest_directory)
    except InvestigationSnapshotError:
        return freezer(
            start_date=start_date,
            end_date=end_date,
            source=source,
            object_membership=object_membership,
        )
    try:
        return incremental_freezer(
            predecessor=predecessor,
            start_date=start_date,
            end_date=end_date,
            source=source,
            object_membership=object_membership,
        )
    except (InvestigationSnapshotError, ResearchDataSourceError):
        return freezer(
            start_date=start_date,
            end_date=end_date,
            source=source,
            object_membership=object_membership,
        )


def run_frontier_snapshot_cycle(
    *,
    source: S3ResearchDataSource | None = None,
    state_directory: Path | str = DEFAULT_STATE_DIRECTORY,
    manifest_directory: Path | str = MANIFEST_DIRECTORY,
    as_of_date: date | None = None,
    freezer: Callable[..., InvestigationSnapshot] = freeze_investigation_snapshot,
    incremental_freezer: Callable[..., InvestigationSnapshot] = freeze_investigation_snapshot_incremental,
    verifier: Callable[..., Any] | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    checkpoint_directory: Path | str | None = None,
) -> FrontierCycleResult:
    """Discover, cohere, freeze, verify, and advance one governed frontier.

    ``progress_callback`` is an optional diagnostic hook. It receives ordered
    substage events (``phase`` plus counts) so a long FRONTIER run can report
    meaningful progress. It is best-effort only: it never receives partial
    evidence and can never change the governed result or the persisted
    snapshot/state authority.

    ``checkpoint_directory`` optionally points the resumable-acquisition
    checkpoint at a location that survives across attempts (for example beside
    the persistent verified-body cache). When omitted it defaults to
    ``<state_directory>/acquisition``. The checkpoint is never a snapshot
    authority: it only lets a subsequent attempt reuse already-verified datasets
    after revalidating their S3 authority.
    """
    emit = _substage(progress_callback)
    store = FrontierStateStore(state_directory)
    checkpoint_store = FrontierAcquisitionCheckpointStore(
        Path(checkpoint_directory) if checkpoint_directory is not None
        else Path(state_directory) / "acquisition")
    previous: dict[str, Any] | None = None
    frontier: FrontierSelection | None = None
    try:
        previous = store.load_latest_success()
        predecessor = None if previous is None else str(
            previous["last_successful_snapshot_id"])
        resolved_source = source or get_default_source()
        resolved_as_of = as_of_date or _utc_now().date()
        boundary = _checkpoint_boundary(resolved_source)
        checkpoint_load_started = time.perf_counter()
        resume = checkpoint_store.load(
            boundary=boundary,
            not_before=(None if previous is None else
                        (str(previous.get("completion_timestamp") or "") or None)))
        partial = checkpoint_store.load_partial(
            boundary=boundary,
            not_before=(None if previous is None else
                        (str(previous.get("completion_timestamp") or "") or None)))
        checkpoint_load_seconds = round(
            time.perf_counter() - checkpoint_load_started, 6)
        resumed_objects = sum(
            len((item or {}).get("objects") or ())
            for item in (resume or {}).values() if isinstance(item, Mapping))
        accumulated: dict[str, Any] = {
            name: dict(item) for name, item in (resume or {}).items()}

        def record_dataset(
            dataset: str, objects: Sequence[Mapping[str, Any]],
            coverage: Mapping[str, Any],
        ) -> None:
            accumulated[dataset] = {
                "objects": [dict(item) for item in objects],
                "event_time_coverage": dict(coverage),
            }
            if checkpoint_store.save(boundary=boundary, datasets=accumulated):
                if partial and partial["dataset"] == dataset:
                    checkpoint_store.clear_partial()

        def record_partial(
            dataset: str, objects: Sequence[Mapping[str, Any]],
        ) -> None:
            if dataset in (resume or {}):
                # A complete roster already protects this dataset. Preserve
                # the unfinished later dataset while revalidating it.
                return
            if not checkpoint_store.save_partial(
                    boundary=boundary, dataset=dataset, objects=objects):
                raise FrontierCoordinatorError(
                    FRONTIER_INCOMPLETE, "PARTIAL_CHECKPOINT_CAPACITY_EXCEEDED")

        emit("FRONTIER_DISCOVERY_STARTED", as_of_date=resolved_as_of.isoformat(),
             predecessor_snapshot_id=predecessor,
             resumed_datasets=len(resume or {}),
             resumed_objects=resumed_objects,
             checkpoint_loaded=resume is not None,
             partial_dataset=(None if partial is None else partial["dataset"]),
             partial_objects=(0 if partial is None else len(partial["objects"])),
             checkpoint_path=str(checkpoint_store.path),
             checkpoint_load_seconds=checkpoint_load_seconds,
             datasets_total=len(BOUND_DATASETS))
        discovery_metrics: dict[str, Any] = {}
        discovered, event_coverage, membership_closed_at = _discover_verified(
            resolved_source, as_of_date=resolved_as_of, previous=previous,
            resume=resume, partial=partial,
            on_partial_verified=record_partial,
            on_dataset_verified=record_dataset,
            progress_callback=progress_callback, metrics=discovery_metrics)
        emit("FRONTIER_DISCOVERY_COMPLETED",
             datasets=len(discovered),
             objects=sum(len(items) for items in discovered.values()),
             membership_closed_at=membership_closed_at,
             **discovery_metrics)

        # Extract frozen date boundaries from the predecessor snapshot to preserve
        # the immutable baseline membership across incremental cycles.
        frozen_start_date = None
        frozen_end_date = None
        if predecessor and "coherent_frontier_start" in previous:
            frozen_start_date = previous.get("coherent_frontier_start")
            frozen_end_date = previous.get("coherent_frontier_end")

        frontier = _select_frontier(
            discovered,
            event_coverage=event_coverage,
            predecessor_snapshot_id=predecessor,
            as_of_date=resolved_as_of,
            membership_closed_at=membership_closed_at,
            frozen_start_date=frozen_start_date,
            frozen_end_date=frozen_end_date,
        )
        delta = _compute_delta(frontier, previous)
        emit("FRONTIER_SELECTION_READY", frontier_id=frontier.frontier_id,
             changed_datasets=len(delta["changed_datasets"]),
             unchanged_datasets=len(delta["unchanged_datasets"]))
        if not delta["changed_datasets"]:
            if frontier.pending_required_objects:
                candidate_end, missing = _pending_candidate(frontier)
                reason = (
                    "REQUIRED_DATASETS_NOT_YET_COHERENT_AT_NEWEST_COVERAGE:"
                    + canonical_json(frontier.pending_required_objects)
                )
                result = FrontierCycleResult(
                    status=FRONTIER_INCOMPLETE,
                    snapshot_id=predecessor,
                    fingerprint=(None if previous is None else str(
                        previous["last_successful_snapshot_fingerprint"])),
                    investigation_epoch=(None if previous is None else str(
                        previous["last_successful_investigation_epoch"])),
                    frontier_id=frontier.frontier_id,
                    frontier_start=frontier.selected_start_time,
                    frontier_end=frontier.selected_end_time,
                    predecessor_snapshot_id=predecessor,
                    membership_closed_at=frontier.membership_closed_at,
                    changed_datasets=(),
                    unchanged_datasets=tuple(delta["unchanged_datasets"]),
                    missing_optional_datasets=frontier.missing_optional_datasets,
                    stale_datasets=frontier.stale_datasets,
                    verification_status="RETAINED_VERIFIED",
                    failure_reason=reason,
                    dataset_status=frontier.dataset_status,
                    delta=delta,
                    candidate_status="CANDIDATE_INCOMPLETE",
                    candidate_frontier_end=candidate_end,
                    pending_required_objects=frontier.pending_required_objects,
                    pending_missing_datasets=missing,
                )
                store.save_incomplete_candidate(result, frontier)
                emit("FRONTIER_INCOMPLETE", status=FRONTIER_INCOMPLETE,
                     frontier_id=frontier.frontier_id,
                     pending_missing_datasets=len(missing))
                return result
            emit("FRONTIER_NO_NEW_EVIDENCE", status=NO_NEW_GOVERNED_EVIDENCE,
                 frontier_id=frontier.frontier_id)
            return FrontierCycleResult(
                status=NO_NEW_GOVERNED_EVIDENCE,
                snapshot_id=predecessor,
                fingerprint=(None if previous is None else str(previous["last_successful_snapshot_fingerprint"])),
                investigation_epoch=(None if previous is None else str(previous["last_successful_investigation_epoch"])),
                frontier_id=frontier.frontier_id,
                frontier_start=frontier.selected_start_time,
                frontier_end=frontier.selected_end_time,
                predecessor_snapshot_id=predecessor,
                membership_closed_at=frontier.membership_closed_at,
                changed_datasets=(),
                unchanged_datasets=tuple(delta["unchanged_datasets"]),
                missing_optional_datasets=frontier.missing_optional_datasets,
                stale_datasets=frontier.stale_datasets,
                verification_status="NOT_REQUIRED",
                dataset_status=frontier.dataset_status,
                delta=delta,
            )

        manifest_dir = Path(manifest_directory)
        snapshot = _materialize_snapshot(
            freezer=freezer,
            incremental_freezer=incremental_freezer,
            predecessor_snapshot_id=predecessor,
            manifest_directory=manifest_dir,
            start_date=frontier.selected_start_time,
            end_date=frontier.selected_end_time,
            source=resolved_source,
            object_membership={
                name: tuple(frontier.dataset_membership[name]["objects"])
                for name in BOUND_DATASETS
            },
        )
        emit("FRONTIER_SNAPSHOT_MATERIALIZED", snapshot_id=snapshot.snapshot_id)
        _assert_snapshot_membership(snapshot, frontier)
        manifest_path = manifest_dir / f"{snapshot.snapshot_id}.json"
        save_investigation_snapshot(snapshot, manifest_path)
        if verifier is None:
            verify_investigation_snapshot_identity(
                snapshot.snapshot_id,
                source=resolved_source,
                manifest_directory=manifest_dir,
            )
        else:
            verifier(
                snapshot.snapshot_id,
                source=resolved_source,
                manifest_directory=manifest_dir,
            )
        emit("FRONTIER_VERIFIED", snapshot_id=snapshot.snapshot_id,
             verification_status="VERIFIED")

        completed = _utc_now().isoformat()
        state = {
            "coordinator_schema": STATE_SCHEMA,
            "status": "SUCCESS",
            "last_successful_snapshot_id": snapshot.snapshot_id,
            "last_successful_snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "last_successful_investigation_epoch": snapshot.evidence_epoch,
            "exact_consumed_objects_by_dataset": {
                name: frontier.dataset_membership[name]["objects"] for name in BOUND_DATASETS
            },
            "coherent_frontier_start": frontier.selected_start_time,
            "coherent_frontier_end": frontier.selected_end_time,
            "membership_closed_at": frontier.membership_closed_at,
            "completion_timestamp": completed,
            "failure_information": None,
            "predecessor_snapshot_id": predecessor,
            "frontier": frontier.to_dict(),
            "delta": delta,
            "latest_success_pointer": {
                "snapshot_id": snapshot.snapshot_id,
                "fingerprint": snapshot.snapshot_fingerprint,
                "frontier_id": frontier.frontier_id,
                "predecessor_snapshot_id": predecessor,
                "completion_timestamp": completed,
            },
        }
        store.save_success(state)
        store.save_candidate_promotion(snapshot=snapshot, frontier=frontier)
        # The success supersedes any partial acquisition progress.
        checkpoint_store.clear()
        emit("FRONTIER_COMPLETED", snapshot_id=snapshot.snapshot_id,
             frontier_id=frontier.frontier_id)
        return FrontierCycleResult(
            status=SNAPSHOT_READY,
            snapshot_id=snapshot.snapshot_id,
            fingerprint=snapshot.snapshot_fingerprint,
            investigation_epoch=snapshot.evidence_epoch,
            frontier_id=frontier.frontier_id,
            frontier_start=frontier.selected_start_time,
            frontier_end=frontier.selected_end_time,
            predecessor_snapshot_id=predecessor,
            membership_closed_at=frontier.membership_closed_at,
            changed_datasets=tuple(delta["changed_datasets"]),
            unchanged_datasets=tuple(delta["unchanged_datasets"]),
            missing_optional_datasets=frontier.missing_optional_datasets,
            stale_datasets=frontier.stale_datasets,
            new_object_count=int(delta["new_object_count"]),
            replaced_object_count=int(delta["replaced_object_count"]),
            verification_status="VERIFIED",
            dataset_status=frontier.dataset_status,
            delta=delta,
        )
    except FrontierCoordinatorError as exc:
        result = _failure_result(exc, previous, frontier)
        store.save_failure(result, None if frontier is None else frontier.to_dict())
        emit("FRONTIER_FAILED", status=result.status, reason=exc.reason)
        return result
    except Exception as exc:
        error = FrontierCoordinatorError(
            FRONTIER_INVALID, f"{type(exc).__name__}:{exc}"
        )
        result = _failure_result(error, previous, frontier)
        store.save_failure(result, None if frontier is None else frontier.to_dict())
        emit("FRONTIER_FAILED", status=result.status, reason=error.reason)
        return result


__all__ = [
    "CANDIDATE_SCHEMA",
    "CHECKPOINT_SCHEMA",
    "COORDINATOR_SCHEMA",
    "DATASET_SCOPE",
    "DEFAULT_CHECKPOINT_MAX_BYTES",
    "DEFAULT_STATE_DIRECTORY",
    "FRONTIER_INCOMPLETE",
    "FRONTIER_INVALID",
    "FrontierAcquisitionCheckpointStore",
    "FrontierCoordinatorError",
    "FrontierCycleResult",
    "FrontierSelection",
    "FrontierStateStore",
    "NEW_GOVERNED_EVIDENCE",
    "NO_NEW_GOVERNED_EVIDENCE",
    "SNAPSHOT_READY",
    "run_frontier_snapshot_cycle",
]
