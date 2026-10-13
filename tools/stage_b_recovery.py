"""Stage B - bounded, resumable historical evidence recovery runner.

This is the smallest resumable recovery mechanism around the existing archived
snapshot manifest (``ISNAP-5FB0DD9FC4D808D6C23789CD``) and the production S3
evidence reader.  It deliberately changes no production code:

* the manifest is loaded through the production entry point
  ``load_investigation_snapshot_id`` (identity/fingerprint rederived, never
  rewritten);
* the source contract is authenticated before any object work with
  ``authenticate_snapshot_inputs`` (bucket/region/research profile/contract/
  Stage 4 policy + evidence epochs) and every run additionally rebinds the
  persisted progress file to the manifest SHA-256, snapshot fingerprint and
  authority fingerprint;
* cached bodies are re-verified independently per object: exact cache-identity
  match, byte size and a full SHA-256 re-hash against the manifest digest;
* missing historical versions are retrieved only through the production
  reader's ``read_bound_objects`` (pinned ``VersionId`` GET, strict schema
  decode, digest/row-count/byte-size/ETag/size/last-modified/version checks);
* per-dataset ``content_digest`` is recomputed with the same fingerprint the
  production ``SnapshotBoundDatasetReader`` uses before work starts;
* progress is persisted atomically (temp file + ``os.replace``) at bounded
  ``batch_size`` intervals and on every stop, and is only reused after the
  authority rebind plus a full local re-verification of every previously
  verified body;
* a partial checkpoint is never authoritative: the progress file carries
  ``authoritative: false`` and the handoff document is written only when every
  required object of the snapshot has passed verification with zero failures;
* no ListObjectsV2 is ever issued (``list_calls`` must stay 0 - this is not a
  FRONTIER acquisition) and no S3 write occurs; new verified bodies are stored
  through the existing production cache writer only.

Operational state (progress, reports, handoff) lives in an isolated Stage B
recovery directory; verified bodies are read from and written into the existing
production verified-body cache (capacity >= 1 GiB) so the production scientific
engine can consume the handoff directly.

Run a single bounded batch (defaults are deliberately bounded):

    $env:RESEARCH_AWS_PROFILE="trading-bot-new"
    python tools/stage_b_recovery.py run --max-objects 200 --max-seconds 900
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, local
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.control_plane import stage4_dataset_snapshot as D  # noqa: E402
from research_engine.data_access.s3_source import (  # noqa: E402
    S3ResearchDataSource,
)
from research_engine.v10.investigation_snapshot import (  # noqa: E402
    InvestigationSnapshotError,
    _authority_material,
    authenticate_snapshot_inputs,
    load_investigation_snapshot_id,
)

DEFAULT_SNAPSHOT_ID = "ISNAP-5FB0DD9FC4D808D6C23789CD"
ARCHIVE_MANIFESTS = (
    ROOT / "reports" / "research_lab_archive" /
    "180297f6a58099f348d83d69e9b0e748bb6e7718ea5f68ddda996e17a8c9d26b" /
    "source" / "snapshot" / "manifests")
DEFAULT_CACHE_DIRECTORY = (
    ROOT / "data" / "research" / "continuous" / "verified_object_bodies")
DEFAULT_STATE_DIRECTORY = ROOT / "_stageb_evidence" / "recovery"

# Existing verified-cache configuration requires >= 1 GiB for recovery runs.
CACHE_CAPACITY_MIN_BYTES = 1024 * 1024 * 1024
# Production client config uses max_pool_connections=16; never exceed it.
MAX_WORKERS = 16

PROGRESS_SCHEMA = "stage_b_recovery_progress_v1"
REPORT_SCHEMA = "stage_b_recovery_report_v1"
HANDOFF_SCHEMA = "stage_b_recovery_handoff_v1"

ORDER_MISSING_FIRST = "missing-first"
ORDER_MANIFEST = "manifest"


class RecoveryError(RuntimeError):
    """Fail-closed Stage B recovery contract violation."""


# ─── atomic state helpers ─────────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Persist JSON atomically: temp file in the same directory + replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp",
                prefix=path.name + ".", dir=str(path.parent), delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ─── configuration ────────────────────────────────────────────────────────────


@dataclass
class RecoveryConfig:
    """One bounded, isolated Stage B recovery run."""

    snapshot_id: str = DEFAULT_SNAPSHOT_ID
    manifest_directory: Path = ARCHIVE_MANIFESTS
    cache_directory: Path = DEFAULT_CACHE_DIRECTORY
    state_directory: Path = DEFAULT_STATE_DIRECTORY
    cache_capacity_bytes: int = CACHE_CAPACITY_MIN_BYTES
    max_objects: int | None = None
    max_seconds: float | None = None
    batch_size: int = 50
    workers: int = 8
    order: str = ORDER_MISSING_FIRST
    bucket: str | None = None
    client: Any = None
    report_path: Path | None = None

    def __post_init__(self) -> None:
        self.manifest_directory = Path(self.manifest_directory)
        self.cache_directory = Path(self.cache_directory)
        self.state_directory = Path(self.state_directory)
        if self.report_path is not None:
            self.report_path = Path(self.report_path)
        if int(self.cache_capacity_bytes) < CACHE_CAPACITY_MIN_BYTES:
            raise RecoveryError(
                "RECOVERY_CACHE_CAPACITY_BELOW_1GIB:"
                f"{int(self.cache_capacity_bytes)}")
        if not (1 <= int(self.workers) <= MAX_WORKERS):
            raise RecoveryError(f"RECOVERY_WORKERS_OUT_OF_RANGE:{self.workers}")
        if int(self.batch_size) < 1:
            raise RecoveryError(f"RECOVERY_BATCH_SIZE_INVALID:{self.batch_size}")
        if self.max_objects is not None and int(self.max_objects) < 1:
            raise RecoveryError(f"RECOVERY_MAX_OBJECTS_INVALID:{self.max_objects}")
        if self.max_seconds is not None and float(self.max_seconds) <= 0:
            raise RecoveryError(f"RECOVERY_MAX_SECONDS_INVALID:{self.max_seconds}")
        if self.order not in (ORDER_MISSING_FIRST, ORDER_MANIFEST):
            raise RecoveryError(f"RECOVERY_ORDER_INVALID:{self.order}")

    @property
    def progress_path(self) -> Path:
        return self.state_directory / f"{self.snapshot_id}.progress.json"

    @property
    def handoff_path(self) -> Path:
        return self.state_directory / f"{self.snapshot_id}.handoff.json"

    @property
    def last_report_path(self) -> Path:
        return self.state_directory / f"{self.snapshot_id}.last_report.json"


# ─── per-object verification ──────────────────────────────────────────────────


def verify_cached_entry(
    source: S3ResearchDataSource, dataset: str, item: Mapping[str, Any],
) -> None:
    """Independently validate one cached body against its bound identity.

    Raises ``RecoveryError`` with a ``RECOVERY_CACHE_ENTRY_MISSING`` marker when
    no entry exists (the object simply still needs retrieval) and with a
    ``RECOVERY_CACHE_CORRUPTED`` marker for any identity/size/digest mismatch,
    which must stop the recovery pass safely.
    """
    identifier = str(item.get("identifier") or "")
    identity = source._verified_body_identity(dataset, dict(item))
    if identity is None:
        raise RecoveryError(
            "RECOVERY_UNVERSIONED_OR_INCOMPLETE_IDENTITY:" + identifier)
    path = source._verified_body_path(identity)
    if not path.exists():
        raise RecoveryError("RECOVERY_CACHE_ENTRY_MISSING:" + identifier)
    try:
        with path.open("rb") as handle:
            header_line = handle.readline(65537)
            if len(header_line) > 65536:
                raise ValueError("cache header too large")
            header = json.loads(header_line)
            if header != identity:
                raise ValueError("cache identity mismatch")
            if path.stat().st_size - len(header_line) != int(item["byte_size"]):
                raise ValueError("cache body size mismatch")
            hasher = hashlib.sha256()
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
            if hasher.hexdigest() != str(item["content_sha256"]):
                raise ValueError("cache body digest mismatch")
    except RecoveryError:
        raise
    except Exception as exc:  # noqa: BLE001 - any defect is fail-closed
        raise RecoveryError(
            f"RECOVERY_CACHE_CORRUPTED:{exc}:{identifier}") from exc


def _is_missing_marker(exc: BaseException) -> bool:
    return (isinstance(exc, RecoveryError)
            and str(exc).startswith("RECOVERY_CACHE_ENTRY_MISSING:"))


def validate_dataset_accounting(snapshot) -> None:
    """Recompute every present binding's content digest like the reader does."""
    for binding in snapshot.datasets:
        if binding.presence == "ABSENT":
            continue
        recomputed = D.fingerprint({
            "dataset": binding.dataset,
            "schema_version": binding.schema_version,
            "objects": [item.to_dict() for item in binding.objects],
        })
        if recomputed != binding.content_digest:
            raise RecoveryError(
                "RECOVERY_DATASET_DIGEST_MISMATCH:" + binding.dataset)


# ─── run context ──────────────────────────────────────────────────────────────


@dataclass
class _RunContext:
    primary: S3ResearchDataSource
    snapshot: Any
    config: RecoveryConfig
    index: dict[str, tuple[str, Any]]
    deadline: float | None
    schema_by_dataset: dict[str, str]
    shared_client: Any = None
    _client_lock: Lock = field(default_factory=Lock, repr=False)
    _source_lock: Lock = field(default_factory=Lock, repr=False)
    sources: list[S3ResearchDataSource] = field(default_factory=list, repr=False)
    _local: Any = field(default_factory=local, repr=False)

    def client(self) -> Any:
        with self._client_lock:
            if self.shared_client is None:
                # Built at most once, on the main thread, only when a missing
                # body actually has to be retrieved.
                self.shared_client = self.primary._get_client()
            return self.shared_client

    def thread_source(self) -> S3ResearchDataSource:
        existing = getattr(self._local, "source", None)
        if existing is not None:
            return existing
        source = S3ResearchDataSource(
            bucket=self.primary.bucket,
            region=self.primary._region,
            profile=self.primary.research_profile,
            verified_cache_directory=self.primary._verified_cache_directory,
            verified_cache_max_bytes=self.primary._verified_cache_max_bytes,
            verified_cache_object_max_bytes=(
                self.primary._verified_cache_object_max_bytes),
            client=self.client(),
        )
        with self._source_lock:
            self.sources.append(source)
        self._local.source = source
        return source

    @property
    def all_sources(self) -> list[S3ResearchDataSource]:
        with self._source_lock:
            return [self.primary, *self.sources]


def _revalidate_one(ctx: _RunContext, identifier: str) -> str:
    try:
        dataset, item = ctx.index[identifier]
    except KeyError as exc:
        raise RecoveryError(
            "RECOVERY_PROGRESS_UNKNOWN_OBJECT:" + identifier) from exc
    verify_cached_entry(ctx.primary, dataset, item.to_dict())
    return "ok"


def _process_one(ctx: _RunContext, candidate: tuple[str, Any]) -> str:
    dataset, item = candidate
    try:
        verify_cached_entry(ctx.primary, dataset, item.to_dict())
        return "cache"
    except RecoveryError as exc:
        if not _is_missing_marker(exc):
            raise
    # Only missing bodies are retrieved, only through the production reader,
    # only with the manifest-pinned VersionId.
    source = ctx.thread_source()
    source.read_bound_objects(
        dataset, [item.to_dict()],
        expected_schema_version=ctx.schema_by_dataset[dataset],
        start_date=ctx.snapshot.start_date,
        end_date=ctx.snapshot.end_date,
    )
    try:
        verify_cached_entry(ctx.primary, dataset, item.to_dict())
    except RecoveryError as exc:
        if _is_missing_marker(exc):
            raise RecoveryError(
                "RECOVERY_CACHE_WRITE_SKIPPED:" + str(item.identifier)) from exc
        raise
    return "retrieved"


# ─── bounded worker scheduling ────────────────────────────────────────────────


def _run_windowed(
    items: Sequence[Any],
    *,
    workers: int,
    deadline: float | None,
    run_one: Callable[[Any], str],
    on_success: Callable[[Any, str], None],
    on_error: Callable[[Any, BaseException], None],
) -> str | None:
    """Drive a bounded worker pool over ``items``.

    At most ``workers`` tasks are in flight (no speculative over-fetch after a
    failure), the deadline is checked before every new submission, and every
    already-submitted task is harvested so completed verification is never
    discarded.  Returns ``"budget_seconds"`` when the runtime limit stops new
    submissions, otherwise ``None``; failures are reported through
    ``on_error``, which always stops further submission.
    """
    stop: str | None = None
    inflight: deque[tuple[Any, Any]] = deque()
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        while True:
            while stop is None and len(inflight) < max(1, int(workers)):
                if deadline is not None and time.perf_counter() > deadline:
                    stop = "budget_seconds"
                    break
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                inflight.append((item, executor.submit(run_one, item)))
            if not inflight:
                break
            item, future = inflight.popleft()
            try:
                outcome = future.result()
            except BaseException as exc:  # noqa: BLE001 - recorded fail-closed
                on_error(item, exc)
                stop = stop or "failure"
                continue
            on_success(item, outcome)
    return stop


# ─── progress state ───────────────────────────────────────────────────────────


def _fresh_progress(
    config: RecoveryConfig, snapshot, manifest_sha256: str,
    authority_fingerprint: str, source: S3ResearchDataSource,
) -> dict[str, Any]:
    return {
        "schema": PROGRESS_SCHEMA,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "manifest_sha256": manifest_sha256,
        "authority_fingerprint": authority_fingerprint,
        "manifest_directory": str(config.manifest_directory),
        "bucket": source.bucket,
        "region": str(getattr(source, "_region", "")),
        "research_profile": source.research_profile,
        "cache_directory": str(config.cache_directory),
        # A recovery checkpoint is never an authoritative snapshot.
        "authoritative": False,
        "verified": {},
        "failures": [],
        "runs": 0,
        "created_at_utc": _utc_now(),
        "updated_at_utc": _utc_now(),
    }


def _load_progress(
    config: RecoveryConfig, snapshot, manifest_sha256: str,
    authority_fingerprint: str, source: S3ResearchDataSource,
) -> dict[str, Any]:
    path = config.progress_path
    if not path.exists():
        return _fresh_progress(config, snapshot, manifest_sha256,
                               authority_fingerprint, source)
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError("RECOVERY_PROGRESS_UNREADABLE:" + str(path)) from exc
    expected = {
        "schema": PROGRESS_SCHEMA,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "manifest_sha256": manifest_sha256,
        "authority_fingerprint": authority_fingerprint,
    }
    for key, value in expected.items():
        if progress.get(key) != value:
            raise RecoveryError(
                f"RECOVERY_PROGRESS_AUTHORITY_MISMATCH:{key}:"
                f"{progress.get(key)!r}!={value!r}")
    if progress.get("authoritative") is not False:
        raise RecoveryError("RECOVERY_PROGRESS_CLAIMS_AUTHORITY")
    verified = progress.get("verified")
    if not isinstance(verified, dict):
        raise RecoveryError("RECOVERY_PROGRESS_MALFORMED:verified")
    for identifier, record in verified.items():
        if (not isinstance(record, dict)
                or record.get("method") not in ("cache", "retrieved")
                or not record.get("dataset")):
            raise RecoveryError(
                "RECOVERY_PROGRESS_MALFORMED:" + str(identifier))
    if not isinstance(progress.get("failures"), list):
        raise RecoveryError("RECOVERY_PROGRESS_MALFORMED:failures")
    progress.setdefault("runs", 0)
    return progress


def _persist(config: RecoveryConfig, progress: dict[str, Any]) -> None:
    progress["updated_at_utc"] = _utc_now()
    atomic_write_json(config.progress_path, progress)


def _record_failure(
    progress: dict[str, Any], dataset: str | None, identifier: str | None,
    error: str,
) -> None:
    entry = {"dataset": dataset, "identifier": identifier,
             "error": error, "at_utc": _utc_now()}
    progress["failures"].append(entry)
    if identifier:
        progress["verified"].pop(identifier, None)


def _clear_failure(progress: dict[str, Any], identifier: str) -> None:
    progress["failures"] = [
        entry for entry in progress["failures"]
        if entry.get("identifier") != identifier]


# ─── candidate selection ──────────────────────────────────────────────────────


def _cache_probe(source: S3ResearchDataSource, dataset: str, item) -> bool:
    identity = source._verified_body_identity(dataset, item.to_dict())
    if identity is None:
        return False
    return source._verified_body_path(identity).exists()


def _select_candidates(
    pending: list[tuple[str, Any]], order: str,
    probe: Callable[[str, Any], bool],
) -> list[tuple[str, Any]]:
    if order == ORDER_MANIFEST:
        return list(pending)
    # missing-first: round-robin the missing bodies across datasets (manifest
    # dataset order) so a bounded batch samples every affected prefix, then the
    # locally verifiable leftovers in manifest order.
    missing_by_dataset: dict[str, list[tuple[str, Any]]] = {}
    cached: list[tuple[str, Any]] = []
    for dataset, item in pending:
        if probe(dataset, item):
            cached.append((dataset, item))
        else:
            missing_by_dataset.setdefault(dataset, []).append((dataset, item))
    ordered: list[tuple[str, Any]] = []
    queues = list(missing_by_dataset.values())
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    ordered.extend(cached)
    return ordered


# ─── main entry point ─────────────────────────────────────────────────────────


def run_recovery(config: RecoveryConfig) -> dict[str, Any]:
    """Run one bounded recovery batch, returning its report.

    A partial checkpoint is never authoritative; the handoff document is only
    written when every required object has passed verification with no
    failures recorded.
    """
    run_started = time.perf_counter()
    run_started_utc = _utc_now()
    verified_before_run = 0
    revalidated = 0
    demoted = 0
    verified_cached_this_run = 0
    retrieved_this_run = 0
    since_save = 0
    stop_reason: str | None = None
    run_failures: list[dict[str, Any]] = []
    progress: dict[str, Any] | None = None
    ctx: _RunContext | None = None
    total_required = 0
    manifest_sha256 = ""
    authority_fingerprint = ""

    # ── Phase 0: manifest identity, dataset accounting, source authentication
    #              and progress authority binding.  Any failure here raises
    #              before a single object is touched.
    manifest_path = config.manifest_directory / f"{config.snapshot_id}.json"
    snapshot = load_investigation_snapshot_id(
        config.snapshot_id, manifest_directory=config.manifest_directory)
    manifest_sha256 = _sha256_file(manifest_path)
    validate_dataset_accounting(snapshot)
    source = S3ResearchDataSource(
        bucket=config.bucket,
        client=config.client,
        verified_cache_directory=config.cache_directory,
        verified_cache_max_bytes=int(config.cache_capacity_bytes),
    )
    authenticate_snapshot_inputs(snapshot, source)  # source contract check
    authority_fingerprint = D.fingerprint(_authority_material(source))
    progress = _load_progress(config, snapshot, manifest_sha256,
                              authority_fingerprint, source)
    verified_before_run = len(progress["verified"])

    objects_index: dict[str, tuple[str, Any]] = {}
    required_objects: list[tuple[str, Any]] = []
    schema_by_dataset: dict[str, str] = {}
    for binding in snapshot.datasets:
        schema_by_dataset[binding.dataset] = binding.schema_version
        if binding.presence == "ABSENT":
            continue
        for item in binding.objects:
            objects_index[item.identifier] = (binding.dataset, item)
            required_objects.append((binding.dataset, item))
    total_required = len(required_objects)

    ctx = _RunContext(
        primary=source, snapshot=snapshot, config=config,
        index=objects_index,
        schema_by_dataset=schema_by_dataset,
        deadline=((run_started + float(config.max_seconds))
                  if config.max_seconds is not None else None),
    )
    deadline = ctx.deadline
    try:
        # ── Phase 1: revalidate previously verified progress (local only).
        #              A corrupt body stops the pass; a missing body simply
        #              becomes pending work again.
        previously_verified = list(progress["verified"].keys())

        def revalidate_success(identifier: str, outcome: str) -> None:
            nonlocal revalidated
            if outcome == "ok":
                revalidated += 1

        def revalidate_error(identifier: str, exc: BaseException) -> None:
            nonlocal demoted
            if _is_missing_marker(exc):
                # Cache state changed since the checkpoint: no longer verified.
                progress["verified"].pop(identifier, None)
                demoted += 1
                return
            _record_failure(progress, None, identifier, str(exc))
            run_failures.append(progress["failures"][-1])

        budget_stop = _run_windowed(
            previously_verified, workers=config.workers, deadline=deadline,
            run_one=lambda ident: _revalidate_one(ctx, ident),
            on_success=revalidate_success, on_error=revalidate_error)
        if run_failures:
            # Previously verified progress failed its authority re-check:
            # stop safely before any new work.
            stop_reason = "failure"
        elif budget_stop == "budget_seconds":
            stop_reason = "budget_seconds"
        _persist(config, progress)


        if stop_reason is None:
            # ── Phase 2: bounded processing of pending objects.
            verified_ids = set(progress["verified"])
            pending = [entry for entry in required_objects
                       if entry[1].identifier not in verified_ids]
            candidates = _select_candidates(
                pending, config.order,
                lambda ds, item: _cache_probe(source, ds, item))
            if config.max_objects is not None:
                candidates = candidates[:int(config.max_objects)]

            def process_success(
                candidate: tuple[str, Any], outcome: str,
            ) -> None:
                nonlocal verified_cached_this_run, retrieved_this_run, since_save
                dataset, item = candidate
                progress["verified"][item.identifier] = {
                    "dataset": dataset, "method": outcome}
                _clear_failure(progress, item.identifier)
                if outcome == "cache":
                    verified_cached_this_run += 1
                else:
                    retrieved_this_run += 1
                since_save += 1
                if since_save >= int(config.batch_size):
                    _persist(config, progress)
                    since_save = 0

            def process_error(
                candidate: tuple[str, Any], exc: BaseException,
            ) -> None:
                dataset, item = candidate
                _record_failure(progress, dataset, item.identifier, str(exc))
                run_failures.append(progress["failures"][-1])

            processed = _run_windowed(
                candidates, workers=config.workers, deadline=deadline,
                run_one=lambda entry: _process_one(ctx, entry),
                on_success=process_success, on_error=process_error)
            if processed == "budget_seconds":
                stop_reason = "budget_seconds"
            elif processed == "failure" or run_failures:
                stop_reason = "failure"

        if stop_reason is None:
            stop_reason = "complete" if not (
                [entry for entry in required_objects
                 if entry[1].identifier not in progress["verified"]]
            ) and not progress["failures"] else "budget_objects"
    except Exception as exc:  # noqa: BLE001 - fail closed with a recorded error
        stop_reason = "failure"
        _record_failure(progress, None, None, f"{type(exc).__name__}:{exc}")
        run_failures.append(progress["failures"][-1])
    finally:
        progress["runs"] = int(progress.get("runs", 0)) + 1
        _persist(config, progress)


    # ── Phase 3: completion gate.  A handoff is only ever produced when every
    #              required object is verified and no failure is recorded.
    handoff_written = False
    verified_total = len(progress["verified"])
    remaining_after = max(0, total_required - verified_total)
    if verified_total >= total_required and not progress["failures"] \
            and remaining_after == 0:
        method_counts = {"cache": 0, "retrieved": 0}
        for record in progress["verified"].values():
            method_counts[record["method"]] += 1
        atomic_write_json(config.handoff_path, {
            "schema": HANDOFF_SCHEMA,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "manifest_sha256": manifest_sha256,
            "authority_fingerprint": authority_fingerprint,
            "objects_required": total_required,
            "objects_verified": verified_total,
            "verified_cached_objects": method_counts["cache"],
            "retrieved_objects": method_counts["retrieved"],
            "failures": 0,
            "authoritative": True,
            "completed_at_utc": _utc_now(),
        })
        handoff_written = True

    # ── Report ────────────────────────────────────────────────────────────
    elapsed = round(time.perf_counter() - run_started, 3)
    newly_verified = verified_cached_this_run + retrieved_this_run
    seconds_per_new_object = (
        round(elapsed / newly_verified, 6) if newly_verified else None)
    network = {"list_calls": 0, "head_calls": 0, "get_calls": 0,
               "verified_cache_hits": 0, "verified_cache_writes": 0,
               "remote_body_bytes": 0}
    for item_source in ctx.all_sources:
        perf = item_source.performance
        for key in network:
            network[key] += int(perf.get(key, 0))
    method_totals = {"cache": 0, "retrieved": 0}
    for record in progress["verified"].values():
        method_totals[record["method"]] += 1


    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "phase": "PHASE4_BOUNDED_RECOVERY_BATCH",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "authority_fingerprint": authority_fingerprint,
        "source_authenticated_before_recovery": True,
        "dataset_accounting_verified": True,
        "progress_authority_bound": True,
        "run_started_utc": run_started_utc,
        "elapsed_seconds": elapsed,
        "objects_required": total_required,
        "verified_before_run": verified_before_run,
        "revalidated_prior_objects": revalidated,
        "demoted_missing_cache_entries": demoted,
        "verified_cached_this_run": verified_cached_this_run,
        "retrieved_this_run": retrieved_this_run,
        "verified_total": verified_total,
        "verified_cached_total": method_totals["cache"],
        "verified_retrieved_total": method_totals["retrieved"],
        "failures": run_failures,
        "failures_total": len(progress["failures"]),
        "remaining_objects": remaining_after,
        "stop_reason": stop_reason,
        "authoritative_handoff": handoff_written,
        "partial_checkpoint_authoritative": False,
        "fresh_authority_head_deferred_to_production_reader": True,
        "throughput": {
            "newly_verified_objects": newly_verified,
            "objects_per_second": (
                round((revalidated + newly_verified) / elapsed, 4)
                if elapsed > 0 else None),
            "seconds_per_new_object": seconds_per_new_object,
            "retrieval_objects_per_second": (
                round(retrieved_this_run / elapsed, 4)
                if elapsed > 0 and retrieved_this_run else None),
        },
        "projection": {
            "remaining_objects": remaining_after,
            "estimated_remaining_seconds": (
                round(remaining_after * seconds_per_new_object, 1)
                if seconds_per_new_object is not None else None),
            "basis": ("measured throughput of this run"
                      if newly_verified else None),
        },
        "network": network,
        "no_listing_proof": network["list_calls"] == 0,
        "cache_directory": str(config.cache_directory),
        "cache_capacity_bytes": int(config.cache_capacity_bytes),
        "state_directory": str(config.state_directory),
        "progress_path": str(config.progress_path),
        "handoff_path": (str(config.handoff_path)
                         if handoff_written else None),
        "workers": int(config.workers),
        "batch_size": int(config.batch_size),
        "max_objects": config.max_objects,
        "max_seconds": config.max_seconds,
        "order": config.order,
        "runs_recorded": int(progress["runs"]),
    }
    atomic_write_json(config.last_report_path, report)
    if config.report_path is not None:
        atomic_write_json(config.report_path, report)
    return report



def recovery_status(config: RecoveryConfig) -> dict[str, Any]:
    """Read-only summary of persisted recovery state."""
    path = config.progress_path
    if not path.exists():
        return {"schema": PROGRESS_SCHEMA, "snapshot_id": config.snapshot_id,
                "exists": False, "verified_total": 0, "authoritative": False}
    progress = json.loads(path.read_text(encoding="utf-8"))
    methods = {"cache": 0, "retrieved": 0}
    for record in progress.get("verified", {}).values():
        methods[record.get("method", "cache")] += 1
    return {
        "schema": progress.get("schema"),
        "exists": True,
        "snapshot_id": progress.get("snapshot_id"),
        "snapshot_fingerprint": progress.get("snapshot_fingerprint"),
        "verified_total": len(progress.get("verified", {})),
        "verified_cached_total": methods["cache"],
        "verified_retrieved_total": methods["retrieved"],
        "failures_total": len(progress.get("failures", [])),
        "authoritative": progress.get("authoritative", False),
        "handoff_exists": config.handoff_path.exists(),
        "runs": progress.get("runs", 0),
        "updated_at_utc": progress.get("updated_at_utc"),
        "progress_path": str(path),
    }



# ─── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stage_b_recovery",
        description="Bounded, resumable Stage B historical evidence recovery.")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run one bounded recovery batch")
    run.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    run.add_argument("--manifest-directory", default=str(ARCHIVE_MANIFESTS))
    run.add_argument("--cache-directory", default=str(DEFAULT_CACHE_DIRECTORY))
    run.add_argument("--state-directory", default=str(DEFAULT_STATE_DIRECTORY))
    run.add_argument("--cache-capacity-bytes", type=int,
                     default=CACHE_CAPACITY_MIN_BYTES)
    run.add_argument("--max-objects", type=int, default=200,
                     help="bounded batch size (default 200)")
    run.add_argument("--max-seconds", type=float, default=900.0,
                     help="runtime limit in seconds (default 900)")
    run.add_argument("--batch-size", type=int, default=50,
                     help="atomic progress persist interval (default 50)")
    run.add_argument("--workers", type=int, default=8,
                     help="bounded worker pool size, 1..16 (default 8)")
    run.add_argument("--order", choices=(ORDER_MISSING_FIRST, ORDER_MANIFEST),
                     default=ORDER_MISSING_FIRST)
    run.add_argument("--bucket", default=None)
    run.add_argument("--report", default=None,
                     help="optional extra path for the run report JSON")
    status = sub.add_parser("status", help="show persisted recovery progress")
    status.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    status.add_argument("--state-directory",
                        default=str(DEFAULT_STATE_DIRECTORY))
    return parser



def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command or "run"
    if command == "status":
        config = RecoveryConfig(
            snapshot_id=args.snapshot_id,
            state_directory=Path(args.state_directory),
            # status never touches the cache or network; validation is happy.
            cache_directory=Path(args.state_directory),
        )
        print(json.dumps(recovery_status(config), indent=2, sort_keys=True))
        return 0

    config = RecoveryConfig(
        snapshot_id=args.snapshot_id,
        manifest_directory=Path(args.manifest_directory),
        cache_directory=Path(args.cache_directory),
        state_directory=Path(args.state_directory),
        cache_capacity_bytes=args.cache_capacity_bytes,
        max_objects=args.max_objects,
        max_seconds=args.max_seconds,
        batch_size=args.batch_size,
        workers=args.workers,
        order=args.order,
        bucket=args.bucket,
        report_path=(Path(args.report) if args.report else None),
    )
    try:
        report = run_recovery(config)
    except (RecoveryError, InvestigationSnapshotError) as exc:
        print(f"RECOVERY STOPPED (fail-closed): {exc}", file=sys.stderr)
        return 1
    summary = {key: report[key] for key in (
        "snapshot_id", "objects_required", "verified_before_run",
        "revalidated_prior_objects", "verified_cached_this_run",
        "retrieved_this_run", "verified_total", "remaining_objects",
        "failures", "stop_reason", "elapsed_seconds", "throughput",
        "projection", "network", "no_listing_proof", "authoritative_handoff",
        "progress_path", "handoff_path")}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if report["stop_reason"] == "failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())
