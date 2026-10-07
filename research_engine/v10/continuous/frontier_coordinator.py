"""Canonical governed-data frontier coordinator for continuous research.

This module deliberately stops at a verified immutable investigation snapshot.
It does not execute questions or create findings, hypotheses, or candidates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, current_schema
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint
from research_engine.data_access.s3_source import S3ResearchDataSource, get_default_source
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    EXCLUDED_BY_DESIGN,
    MANIFEST_DIRECTORY,
    OPTIONAL_DATASETS,
    REQUIRED_DATASETS,
    InvestigationSnapshot,
    SnapshotBoundDatasetReader,
    freeze_investigation_snapshot,
    load_investigation_snapshot_id,
    save_investigation_snapshot,
)


COORDINATOR_SCHEMA = "canonical_research_frontier_v1"
STATE_SCHEMA = "canonical_research_frontier_state_v1"
CANDIDATE_SCHEMA = "canonical_research_frontier_candidate_v1"
DEFAULT_STATE_DIRECTORY = MANIFEST_DIRECTORY.parent / "research_frontier"

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


def _full_object_record(dataset: str, item: Mapping[str, Any]) -> dict[str, Any]:
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
        "byte_count": int(item.get("byte_size") or 0),
        "listed_byte_count": int(item.get("size") or 0),
        "producer": contract.semantic_owner,
        "authority": "core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY",
        "partition_date": _key_date(str(item.get("identifier") or "")),
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


def _discover_verified(
    source: S3ResearchDataSource, *, as_of_date: date,
) -> tuple[
    dict[str, tuple[dict[str, Any], ...]],
    dict[str, dict[str, str | None]],
    str,
]:
    # Close membership before any potentially long object materialisation.
    # Objects arriving after this point remain live and are discovered by the
    # next cycle; they cannot expand the roster being verified below.
    listed_by_dataset = {
        dataset: tuple(source.discover_dataset_objects(dataset))
        for dataset in BOUND_DATASETS
    }
    membership_closed_at = _utc_now().isoformat()
    verified: dict[str, tuple[dict[str, Any], ...]] = {}
    event_coverage: dict[str, dict[str, str | None]] = {}
    for dataset in BOUND_DATASETS:
        listed = listed_by_dataset[dataset]
        if not listed:
            verified[dataset] = ()
            event_coverage[dataset] = {"start": None, "end": None}
            continue
        rows = source.read_objects_for_freeze(
            dataset, listed, expected_schema_version=current_schema(dataset)
        )
        event_coverage[dataset] = _event_time_coverage(dataset, rows, as_of_date)
        malformed = source.malformed_report(dataset)
        if malformed and malformed.malformed_lines:
            raise FrontierCoordinatorError(
                FRONTIER_INVALID,
                f"MALFORMED_GOVERNED_OBJECT_ROWS:{dataset}:{malformed.malformed_lines}",
            )
        metadata = tuple(
            _full_object_record(dataset, item) for item in source.object_metadata(dataset)
        )
        if len(metadata) != len(listed):
            raise FrontierCoordinatorError(
                FRONTIER_INVALID, "OBJECT_DISCOVERY_VERIFICATION_COUNT_MISMATCH:" + dataset
            )
        verified[dataset] = tuple(sorted(metadata, key=lambda item: item["s3_key"]))
        # Assignment evaluates the next read before releasing this local.  Drop
        # it explicitly so two large parsed datasets never overlap in memory.
        del rows
    return verified, event_coverage, membership_closed_at


def _frontier_from_state(previous: Mapping[str, Any]) -> FrontierSelection:
    value = previous["frontier"]
    return FrontierSelection(
        frontier_id=str(value["frontier_id"]),
        selected_start_time=str(value["selected_start_time"]),
        selected_end_time=str(value["selected_end_time"]),
        dataset_membership=dict(value["dataset_membership"]),
        dataset_status=dict(value["dataset_status"]),
        stale_datasets=tuple(value.get("stale_datasets") or ()),
        missing_optional_datasets=tuple(value.get("missing_optional_datasets") or ()),
        gap_diagnostics={
            name: tuple(items)
            for name, items in (value.get("gap_diagnostics") or {}).items()
        },
        pending_required_objects={
            name: tuple(items)
            for name, items in (value.get("pending_required_objects") or {}).items()
        },
        coherence_decision=str(value["coherence_decision"]),
        predecessor_snapshot_id=value.get("predecessor_snapshot_id"),
        membership_closed_at=value.get("membership_closed_at"),
    )


def _reuse_unchanged_frontier(
    source: S3ResearchDataSource,
    previous: Mapping[str, Any] | None,
    *,
    as_of_date: date,
) -> FrontierSelection | None:
    """Reuse consumed state only after exact current S3 identities match."""
    if previous is None:
        return None
    prior = _frontier_from_state(previous)
    listed: dict[str, tuple[dict[str, Any], ...]] = {}
    for dataset in BOUND_DATASETS:
        items = source.discover_dataset_objects(dataset)
        if dataset in REQUIRED_DATASETS and not items:
            return None
        enriched = tuple(
            {**dict(item), "partition_date": _key_date(str(item["identifier"]))}
            for item in items
        )
        if any(str(item["partition_date"]) > as_of_date.isoformat() for item in enriched):
            return None
        listed[dataset] = enriched

    required_dates = {
        name: sorted({str(item["partition_date"]) for item in listed[name]})
        for name in REQUIRED_DATASETS
    }
    if any(not values for values in required_dates.values()):
        return None
    start = max(values[0] for values in required_dates.values())
    end = min(values[-1] for values in required_dates.values())
    if start > end or start != prior.selected_start_time or end != prior.selected_end_time:
        return None
    # Preserve the existing fail-closed handling for a required dataset that
    # has advanced beyond its peers.
    if any(any(str(item["partition_date"]) > end for item in listed[name])
           for name in REQUIRED_DATASETS):
        return None

    selected_by_dataset: dict[str, tuple[dict[str, Any], ...]] = {}
    for dataset in BOUND_DATASETS:
        selected = tuple(
            item for item in listed[dataset]
            if start <= str(item["partition_date"]) <= end
        )
        old = tuple(prior.dataset_membership[dataset]["objects"])
        current_listing = [
            (str(item["identifier"]), str(item.get("etag") or ""),
             int(item.get("size") or 0))
            for item in selected
        ]
        prior_listing = [
            (str(item["s3_key"]), str(item.get("etag") or ""),
             int(item.get("listed_byte_count") or item.get("byte_count") or 0))
            for item in old
        ]
        if current_listing != prior_listing:
            return None
        selected_by_dataset[dataset] = selected

    # HEAD resolves VersionId, which list_objects_v2 omits.  This retains the
    # existing exact-version replacement semantics without downloading bodies.
    for dataset in BOUND_DATASETS:
        headed = source.head_bound_objects(
            dataset,
            selected_by_dataset[dataset],
            expected_schema_version=current_schema(dataset),
        )
        old = tuple(prior.dataset_membership[dataset]["objects"])
        current_identity = [
            (str(item["identifier"]), item.get("version_id"),
             str(item.get("etag") or ""), int(item.get("size") or 0),
             str(item.get("last_modified") or ""))
            for item in headed
        ]
        prior_identity = [
            (str(item["s3_key"]), item.get("version_id"),
             str(item.get("etag") or ""),
             int(item.get("listed_byte_count") or item.get("byte_count") or 0),
             str(item.get("last_modified") or ""))
            for item in old
        ]
        if current_identity != prior_identity:
            return None
    return prior


def _select_frontier(
    discovered: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    event_coverage: Mapping[str, Mapping[str, str | None]],
    predecessor_snapshot_id: str | None,
    as_of_date: date,
    membership_closed_at: str | None = None,
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


def run_frontier_snapshot_cycle(
    *,
    source: S3ResearchDataSource | None = None,
    state_directory: Path | str = DEFAULT_STATE_DIRECTORY,
    manifest_directory: Path | str = MANIFEST_DIRECTORY,
    as_of_date: date | None = None,
    freezer: Callable[..., InvestigationSnapshot] = freeze_investigation_snapshot,
    verifier: Callable[..., Any] | None = None,
) -> FrontierCycleResult:
    """Discover, cohere, freeze, verify, and advance one governed frontier."""
    store = FrontierStateStore(state_directory)
    previous: dict[str, Any] | None = None
    frontier: FrontierSelection | None = None
    try:
        previous = store.load_latest_success()
        predecessor = None if previous is None else str(
            previous["last_successful_snapshot_id"])
        resolved_source = source or get_default_source()
        resolved_as_of = as_of_date or _utc_now().date()
        frontier = _reuse_unchanged_frontier(
            resolved_source, previous, as_of_date=resolved_as_of)
        if frontier is None:
            discovered, event_coverage, membership_closed_at = _discover_verified(
                resolved_source, as_of_date=resolved_as_of)
            frontier = _select_frontier(
                discovered,
                event_coverage=event_coverage,
                predecessor_snapshot_id=predecessor,
                as_of_date=resolved_as_of,
                membership_closed_at=membership_closed_at,
            )
        delta = _compute_delta(frontier, previous)
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
                return result
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

        snapshot = freezer(
            start_date=frontier.selected_start_time,
            end_date=frontier.selected_end_time,
            source=resolved_source,
            object_membership={
                name: tuple(frontier.dataset_membership[name]["objects"])
                for name in BOUND_DATASETS
            },
        )
        _assert_snapshot_membership(snapshot, frontier)
        manifest_dir = Path(manifest_directory)
        manifest_path = manifest_dir / f"{snapshot.snapshot_id}.json"
        save_investigation_snapshot(snapshot, manifest_path)
        if verifier is None:
            loaded = load_investigation_snapshot_id(
                snapshot.snapshot_id, manifest_directory=manifest_dir
            )
            SnapshotBoundDatasetReader(loaded, source=resolved_source)
        else:
            verifier(
                snapshot.snapshot_id,
                source=resolved_source,
                manifest_directory=manifest_dir,
            )

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
        return result
    except Exception as exc:
        error = FrontierCoordinatorError(
            FRONTIER_INVALID, f"{type(exc).__name__}:{exc}"
        )
        result = _failure_result(error, previous, frontier)
        store.save_failure(result, None if frontier is None else frontier.to_dict())
        return result


__all__ = [
    "CANDIDATE_SCHEMA",
    "COORDINATOR_SCHEMA",
    "DATASET_SCOPE",
    "DEFAULT_STATE_DIRECTORY",
    "FRONTIER_INCOMPLETE",
    "FRONTIER_INVALID",
    "FrontierCoordinatorError",
    "FrontierCycleResult",
    "FrontierSelection",
    "FrontierStateStore",
    "NEW_GOVERNED_EVIDENCE",
    "NO_NEW_GOVERNED_EVIDENCE",
    "SNAPSHOT_READY",
    "run_frontier_snapshot_cycle",
]
