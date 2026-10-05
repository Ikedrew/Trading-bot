"""Durable transport obligations for canonical Production V1 evidence.

This module sits between an existing producer/local write and canonical S3.
It deliberately does not call S3 or alter producer/trading behaviour.  Block
1B can enqueue producer records; a later delivery worker can claim records and
use :meth:`CanonicalDeliveryOutbox.acknowledge` only after exact verification.

Destination model
-----------------
Every logical record owns one deterministic immutable JSONL object:

    .../date=YYYY-MM-DD/part-outbox-<idempotency sha256>.jsonl

This is compatible with the existing partition loaders and does not use the
unsafe shared ``part-000.jsonl`` GET/append/PUT pattern.  Delivery should use a
create-only PUT (``IfNoneMatch='*'``).  A pre-existing object is successful
only after its exact payload hash has been verified.

SQLite is the local authority.  FULL synchronous transactions and SQLite's
file locking provide thread/process serialization.  There is no in-memory
queue authority and no capacity/drop policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, Mapping

from core.lifecycle_evidence_obligations import (
    EXACT_IDENTITY_FIELDS,
    IDENTITY_FIELD_PATHS,
)
from core.production_data_contract import (
    DATA_CONTRACT_VERSION,
    PRODUCTION_SCHEMA_REGISTRY,
    canonical_s3_schema_prefix,
    canonical_s3_key,
    current_schema,
    is_symbol_scoped,
)


DEFAULT_OUTBOX_PATH = Path("logs/canonical_delivery_outbox.sqlite3")
logger = logging.getLogger(__name__)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACK_VERIFICATION_METHODS = frozenset({"GET_BODY_SHA256", "HEAD_METADATA_SHA256"})


class DeliveryState(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    CONFLICT = "CONFLICT"


STATE_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    DeliveryState.PENDING: frozenset({DeliveryState.IN_FLIGHT, DeliveryState.CONFLICT}),
    DeliveryState.IN_FLIGHT: frozenset({
        DeliveryState.ACKNOWLEDGED,
        DeliveryState.RETRYABLE_FAILURE,
        DeliveryState.TERMINAL_FAILURE,
        DeliveryState.CONFLICT,
    }),
    DeliveryState.RETRYABLE_FAILURE: frozenset({
        DeliveryState.IN_FLIGHT, DeliveryState.CONFLICT,
    }),
    DeliveryState.ACKNOWLEDGED: frozenset({
        DeliveryState.ACKNOWLEDGED, DeliveryState.CONFLICT,
    }),
    DeliveryState.TERMINAL_FAILURE: frozenset({
        DeliveryState.TERMINAL_FAILURE, DeliveryState.CONFLICT,
    }),
    DeliveryState.CONFLICT: frozenset({DeliveryState.CONFLICT}),
}


class EnqueueOutcome(str, Enum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


class OutboxError(RuntimeError):
    """Base outbox failure."""


class OutboxPersistenceError(OutboxError):
    """The durable local transaction did not commit."""


class OutboxCorruptionError(OutboxError):
    """Persisted state cannot be reconstructed exactly; fail closed."""


class InvalidDeliveryTransition(OutboxError):
    """A state transition violates the finite-state contract."""


class CanonicalAckError(OutboxError):
    """Supplied evidence is insufficient or does not match the record."""


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    idempotency_key: str
    dataset: str
    production_namespace: str
    schema_version: str
    canonical_bucket: str
    canonical_key: str
    record_identity: Mapping[str, Any]
    payload: Mapping[str, Any]
    payload_sha256: str
    created_at: str
    updated_at: str
    attempt_count: int
    last_attempt_at: str | None
    next_retry_at: str | None
    delivery_state: DeliveryState
    last_error: str | None
    last_error_class: str | None
    canonical_ack: Mapping[str, Any] | None
    lifecycle_obligation_id: str | None
    conflict_payload_sha256: str | None
    claim_owner: str | None
    claim_expires_at: str | None
    lease_reclaim_count: int
    revision: int
    reconciliation_state: str
    reconciliation_error: str | None

    @property
    def account_id(self) -> Any:
        return self.record_identity.get("account_id")


@dataclass(frozen=True)
class EnqueueResult:
    outcome: EnqueueOutcome
    record: OutboxRecord


@dataclass(frozen=True)
class ClaimResult:
    record: OutboxRecord
    state_before: DeliveryState
    reclaimed_expired_lease: bool


@dataclass(frozen=True)
class OutboxStatus:
    pending_count: int
    in_flight_count: int
    acknowledged_count: int
    retryable_failure_count: int
    terminal_failure_count: int
    conflict_count: int
    terminal_or_conflict_count: int
    oldest_pending_age_seconds: float | None
    dataset_breakdown: Mapping[str, Mapping[str, int]]


@dataclass(frozen=True)
class LocalHandoff:
    handoff_id: str
    dataset: str
    symbol: str
    partition_date: str
    identity: Mapping[str, Any]
    payload: Mapping[str, Any]
    local_path: str
    local_line: str
    lifecycle_obligation_id: str | None
    state: str
    outbox_id: str | None
    created_at: str
    updated_at: str
    last_error: str | None


def _canonical_json(value: Any) -> str:
    """Serialize only JSON-compatible data; arbitrary repr() is forbidden."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"PAYLOAD_NOT_CANONICAL_JSON:{exc}") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"INVALID_UTC_TIMESTAMP:{value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"TIMEZONE_REQUIRED:{value}")
    return parsed.astimezone(timezone.utc).isoformat()


def _nested_value(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def governed_identity(
    dataset: str,
    payload: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract every exact field from the existing lifecycle identity authority."""
    fields = EXACT_IDENTITY_FIELDS.get(dataset)
    if fields is None:
        raise ValueError(f"NO_GOVERNED_IDENTITY_CONTRACT:{dataset}")
    supplied = identity or {}
    paths = IDENTITY_FIELD_PATHS.get(dataset, {})
    result: dict[str, Any] = {}
    for field in fields:
        value = supplied.get(field)
        if value is None:
            value = _nested_value(payload, paths.get(field, field))
        if value is None or value == "":
            raise ValueError(f"EXACT_IDENTITY_FIELD_REQUIRED:{dataset}:{field}")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"EXACT_IDENTITY_FIELD_NOT_SCALAR:{dataset}:{field}")
        # Prove that identity itself has deterministic JSON semantics.
        _canonical_json(value)
        result[field] = value
    return result


def deterministic_idempotency_key(
    dataset: str,
    schema_version: str,
    identity: Mapping[str, Any],
) -> str:
    """Hash namespace + dataset + schema + exact identity (never wall clock)."""
    material = {
        "production_namespace": DATA_CONTRACT_VERSION,
        "dataset": dataset,
        "schema_version": schema_version,
        "identity": dict(identity),
    }
    return _sha256_text(_canonical_json(material))


def canonical_outbox_destination(
    dataset: str,
    *,
    symbol: str,
    partition_date: str,
    idempotency_key: str,
) -> str:
    """Return the immutable canonical key owned by one logical delivery."""
    if dataset not in PRODUCTION_SCHEMA_REGISTRY:
        raise ValueError(f"UNKNOWN_PRODUCTION_DATASET:{dataset}")
    if not _DATE_RE.fullmatch(partition_date):
        raise ValueError("PARTITION_DATE_REQUIRED_YYYY_MM_DD")
    if is_symbol_scoped(dataset) and not symbol:
        raise ValueError(f"SYMBOL_REQUIRED:{dataset}")
    return canonical_s3_key(
        dataset,
        symbol=symbol,
        date=partition_date,
        part=f"part-outbox-{idempotency_key}.jsonl",
    )


class CanonicalDeliveryOutbox:
    """Transaction-safe durable authority for canonical delivery obligations."""

    _SCHEMA_VERSION = 5

    def __init__(
        self,
        path: str | Path = DEFAULT_OUTBOX_PATH,
        *,
        canonical_bucket: str | None = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        if canonical_bucket is None:
            from core.config import NEW_RUNTIME_S3_BUCKET
            canonical_bucket = NEW_RUNTIME_S3_BUCKET
        if not canonical_bucket:
            raise ValueError("CANONICAL_BUCKET_REQUIRED")
        self.canonical_bucket = canonical_bucket
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            self._db = sqlite3.connect(
                str(self.path), timeout=30.0, isolation_level=None,
                check_same_thread=False,
            )
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA busy_timeout=30000")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._create_schema()
            if not existed:
                self._fsync_parent_best_effort()
            self._validate_all_rows()
        except (OSError, sqlite3.Error) as exc:
            db = getattr(self, "_db", None)
            if db is not None:
                db.close()
            raise OutboxPersistenceError(f"OUTBOX_OPEN_FAILED:{exc}") from exc
        except OutboxCorruptionError:
            db = getattr(self, "_db", None)
            if db is not None:
                db.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "CanonicalDeliveryOutbox":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _fsync_parent_best_effort(self) -> None:
        if os.name == "nt":
            return  # SQLite FlushFileBuffers covers database durability on Windows.
        descriptor = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox_records (
                outbox_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                dataset TEXT NOT NULL,
                production_namespace TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                canonical_bucket TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_attempt_at TEXT,
                next_retry_at TEXT,
                delivery_state TEXT NOT NULL,
                last_error TEXT,
                last_error_class TEXT,
                ack_json TEXT,
                lifecycle_obligation_id TEXT,
                conflict_payload_sha256 TEXT,
                claim_owner TEXT,
                claim_expires_at TEXT,
                lease_reclaim_count INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                reconciliation_state TEXT NOT NULL DEFAULT 'NOT_LINKED',
                reconciliation_error TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_delivery
                ON outbox_records(delivery_state, next_retry_at, created_at);
            CREATE INDEX IF NOT EXISTS ix_outbox_dataset
                ON outbox_records(dataset, delivery_state);
            CREATE TABLE IF NOT EXISTS local_handoffs (
                handoff_id TEXT PRIMARY KEY,
                dataset TEXT NOT NULL,
                symbol TEXT NOT NULL,
                partition_date TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                local_path TEXT NOT NULL,
                local_line TEXT NOT NULL,
                lifecycle_obligation_id TEXT,
                state TEXT NOT NULL CHECK (state IN ('PREPARED','COMPLETED','CONFLICT')),
                outbox_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error TEXT,
                recovery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (recovery_attempts >= 0)
            );
            CREATE INDEX IF NOT EXISTS ix_local_handoffs_recovery
                ON local_handoffs(state, recovery_attempts, created_at, handoff_id);
            """
        )
        columns = {
            row[1] for row in self._db.execute("PRAGMA table_info(outbox_records)")
        }
        additions = {
            "last_attempt_at": "TEXT",
            "last_error_class": "TEXT",
            "reconciliation_state": "TEXT NOT NULL DEFAULT 'NOT_LINKED'",
            "reconciliation_error": "TEXT",
            "lease_reclaim_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in columns:
                self._db.execute(
                    f"ALTER TABLE outbox_records ADD COLUMN {name} {declaration}"
                )
        handoff_columns = {
            row[1] for row in self._db.execute("PRAGMA table_info(local_handoffs)")
        }
        if "recovery_attempts" not in handoff_columns:
            self._db.execute(
                "ALTER TABLE local_handoffs ADD COLUMN recovery_attempts "
                "INTEGER NOT NULL DEFAULT 0"
            )
        self._db.execute(f"PRAGMA user_version={self._SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            _t0 = time.perf_counter()
            _begin_ms = 0
            _commit_ms = 0
            try:
                _tb = time.perf_counter()
                self._db.execute("BEGIN IMMEDIATE")
                _begin_ms = int((time.perf_counter() - _tb) * 1000)
                yield self._db
                _tc = time.perf_counter()
                self._db.execute("COMMIT")
                _commit_ms = int((time.perf_counter() - _tc) * 1000)
            except Exception:
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            _total_ms = int((time.perf_counter() - _t0) * 1000)
            if _total_ms >= 250:
                # BEGIN IMMEDIATE waits on the write lock (busy_timeout=30s) and
                # COMMIT fsyncs the WAL (synchronous=FULL). Either can stall the
                # scanner thread for seconds on a contended/slow disk.
                logger.warning(
                    "[OUTBOX_TXN_SLOW] total_ms=%d begin_ms=%d commit_ms=%d",
                    _total_ms, _begin_ms, _commit_ms,
                )

    def _validate_all_rows(self) -> None:
        try:
            quick_check = [row[0] for row in self._db.execute("PRAGMA quick_check").fetchall()]
            if quick_check != ["ok"]:
                raise OutboxCorruptionError(
                    f"SQLITE_QUICK_CHECK_FAILED:{'|'.join(quick_check)}"
                )
            rows = self._db.execute("SELECT * FROM outbox_records").fetchall()
            for row in rows:
                self._row_to_record(row)
        except OutboxCorruptionError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise OutboxCorruptionError(f"OUTBOX_RELOAD_CORRUPT:{exc}") from exc

    def enqueue(
        self,
        *,
        dataset: str,
        payload: Mapping[str, Any],
        symbol: str,
        partition_date: str,
        identity: Mapping[str, Any] | None = None,
        lifecycle_obligation_id: str | None = None,
    ) -> EnqueueResult:
        if dataset not in PRODUCTION_SCHEMA_REGISTRY:
            raise ValueError(f"UNKNOWN_PRODUCTION_DATASET:{dataset}")
        schema = current_schema(dataset)
        canonical_payload = dict(payload)
        present_schema = canonical_payload.get("schema_version")
        if present_schema not in (None, schema):
            raise ValueError(f"PAYLOAD_SCHEMA_MISMATCH:{dataset}:{present_schema}")
        canonical_payload["schema_version"] = schema
        exact_identity = governed_identity(dataset, canonical_payload, identity)
        payload_identity = governed_identity(dataset, canonical_payload)
        if payload_identity != exact_identity:
            raise ValueError("EXPLICIT_IDENTITY_DOES_NOT_MATCH_PAYLOAD")
        payload_json = _canonical_json(canonical_payload)
        identity_json = _canonical_json(exact_identity)
        payload_hash = _sha256_text(payload_json)
        idem = deterministic_idempotency_key(dataset, schema, exact_identity)
        destination = canonical_outbox_destination(
            dataset, symbol=symbol, partition_date=partition_date,
            idempotency_key=idem,
        )
        outbox_id = f"outbox_{idem}"
        now = _normalise_utc(self._clock())

        try:
            with self._transaction() as db:
                existing = db.execute(
                    "SELECT * FROM outbox_records WHERE idempotency_key=?", (idem,),
                ).fetchone()
                if existing is not None:
                    same = (
                        existing["payload_sha256"] == payload_hash
                        and existing["payload_json"] == payload_json
                        and existing["canonical_key"] == destination
                        and existing["canonical_bucket"] == self.canonical_bucket
                    )
                    if same:
                        self._complete_local_handoff_tx(
                            db, idem, existing["outbox_id"], state="COMPLETED")
                        return EnqueueResult(
                            EnqueueOutcome.DUPLICATE, self._row_to_record(existing),
                        )
                    reason = (
                        "IDEMPOTENCY_CONFLICT:existing_payload_sha256="
                        f"{existing['payload_sha256']}:incoming_payload_sha256={payload_hash}"
                    )
                    db.execute(
                        """UPDATE outbox_records
                           SET delivery_state=?, last_error=?,
                               conflict_payload_sha256=?, claim_owner=NULL,
                               claim_expires_at=NULL, next_retry_at=NULL,
                               updated_at=?, revision=revision+1
                           WHERE idempotency_key=?""",
                        (DeliveryState.CONFLICT.value, reason, payload_hash, now, idem),
                    )
                    self._complete_local_handoff_tx(
                        db, idem, f"outbox_{idem}", state="CONFLICT",
                        error=reason,
                    )
                    changed = db.execute(
                        "SELECT * FROM outbox_records WHERE idempotency_key=?", (idem,),
                    ).fetchone()
                    return EnqueueResult(
                        EnqueueOutcome.CONFLICT, self._row_to_record(changed),
                    )

                self._insert_record(db, (
                    outbox_id, idem, dataset, DATA_CONTRACT_VERSION, schema,
                    self.canonical_bucket, destination, identity_json, payload_json,
                    payload_hash, now, now, 0, None, None,
                    DeliveryState.PENDING.value, None, None, None,
                    lifecycle_obligation_id, None, None, None, 0, 1,
                    "PENDING" if lifecycle_obligation_id else "NOT_LINKED", None,
                ))
                self._complete_local_handoff_tx(
                    db, idem, outbox_id, state="COMPLETED")
                created = db.execute(
                    "SELECT * FROM outbox_records WHERE idempotency_key=?", (idem,),
                ).fetchone()
                return EnqueueResult(EnqueueOutcome.CREATED, self._row_to_record(created))
        except (sqlite3.Error, OSError) as exc:
            raise OutboxPersistenceError(f"ENQUEUE_NOT_DURABLE:{exc}") from exc

    def prepare_local_handoff(
        self, *, dataset: str, payload: Mapping[str, Any], symbol: str,
        partition_date: str, local_path: str | Path, local_line: str,
        identity: Mapping[str, Any] | None = None,
        lifecycle_obligation_id: str | None = None,
    ) -> LocalHandoff:
        """Durably journal a specific local record before its JSONL append."""
        if dataset not in PRODUCTION_SCHEMA_REGISTRY:
            raise ValueError(f"UNKNOWN_PRODUCTION_DATASET:{dataset}")
        schema = current_schema(dataset)
        canonical_payload = dict(payload)
        present_schema = canonical_payload.get("schema_version")
        if present_schema not in (None, schema):
            raise ValueError(f"PAYLOAD_SCHEMA_MISMATCH:{dataset}:{present_schema}")
        canonical_payload["schema_version"] = schema
        exact_identity = governed_identity(dataset, canonical_payload, identity)
        if governed_identity(dataset, canonical_payload) != exact_identity:
            raise ValueError("EXPLICIT_IDENTITY_DOES_NOT_MATCH_PAYLOAD")
        if not local_line or "\n" in local_line.rstrip("\r\n"):
            raise ValueError("LOCAL_HANDOFF_LINE_REQUIRED")
        try:
            local_payload = json.loads(local_line.rstrip("\r\n"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"LOCAL_HANDOFF_LINE_INVALID:{exc.msg}") from exc
        if not isinstance(local_payload, dict):
            raise ValueError("LOCAL_HANDOFF_LINE_OBJECT_REQUIRED")
        local_payload.setdefault("schema_version", schema)
        if _canonical_json(local_payload) != _canonical_json(canonical_payload):
            raise ValueError("LOCAL_HANDOFF_LINE_PAYLOAD_MISMATCH")
        normalized_path = str(Path(local_path).resolve())
        idem = deterministic_idempotency_key(dataset, schema, exact_identity)
        payload_json = _canonical_json(canonical_payload)
        identity_json = _canonical_json(exact_identity)
        prepared_at = _normalise_utc(self._clock())
        local_handoff = LocalHandoff(
            handoff_id=idem, dataset=dataset, symbol=symbol,
            partition_date=partition_date, identity=exact_identity,
            payload=canonical_payload, local_path=normalized_path,
            local_line=local_line.rstrip("\r\n"),
            lifecycle_obligation_id=lifecycle_obligation_id,
            state="PREPARED", outbox_id=None,
            created_at=prepared_at, updated_at=prepared_at, last_error=None,
        )
        try:
            conflict = False
            with self._transaction() as db:
                existing = db.execute(
                    "SELECT * FROM local_handoffs WHERE handoff_id=?", (idem,),
                ).fetchone()
                if existing is not None:
                    same = (
                        existing["dataset"] == dataset
                        and existing["identity_json"] == identity_json
                        and existing["payload_json"] == payload_json
                        and existing["local_path"] == normalized_path
                        and existing["local_line"] == local_handoff.local_line
                    )
                    if not same:
                        db.execute(
                            "UPDATE local_handoffs SET state='CONFLICT', last_error=?, "
                            "updated_at=? WHERE handoff_id=?",
                            ("LOCAL_HANDOFF_IDEMPOTENCY_CONFLICT", local_handoff.updated_at, idem),
                        )
                        conflict = True
                    else:
                        return self._row_to_local_handoff(existing)
                else:
                    db.execute(
                        """INSERT INTO local_handoffs (
                            handoff_id,dataset,symbol,partition_date,identity_json,
                            payload_json,local_path,local_line,lifecycle_obligation_id,
                            state,outbox_id,created_at,updated_at,last_error
                        ) VALUES (?,?,?,?,?,?,?,?,?,'PREPARED',NULL,?,?,NULL)""",
                        (idem, dataset, symbol, partition_date, identity_json,
                         payload_json, normalized_path, local_handoff.local_line,
                         lifecycle_obligation_id, local_handoff.created_at,
                         local_handoff.updated_at),
                    )
                    row = db.execute(
                        "SELECT * FROM local_handoffs WHERE handoff_id=?", (idem,),
                    ).fetchone()
                    return self._row_to_local_handoff(row)
            if conflict:
                raise ValueError("LOCAL_HANDOFF_IDEMPOTENCY_CONFLICT")
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"LOCAL_HANDOFF_NOT_DURABLE:{exc}") from exc

    def pending_local_handoffs(self, *, limit: int = 100) -> tuple[LocalHandoff, ...]:
        if limit < 0:
            raise ValueError("LOCAL_HANDOFF_LIMIT_MUST_BE_NON_NEGATIVE")
        if limit == 0:
            return ()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM local_handoffs WHERE state='PREPARED' "
                "ORDER BY recovery_attempts,created_at,handoff_id LIMIT ?", (limit,),
            ).fetchall()
        return tuple(self._row_to_local_handoff(row) for row in rows)

    def note_local_handoff_recovery_attempt(self, handoff_id: str, error: str) -> None:
        now = _normalise_utc(self._clock())
        try:
            with self._transaction() as db:
                db.execute(
                    "UPDATE local_handoffs SET recovery_attempts=recovery_attempts+1, "
                    "last_error=?,updated_at=? WHERE handoff_id=? AND state='PREPARED'",
                    (error, now, handoff_id),
                )
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(
                f"LOCAL_HANDOFF_RECOVERY_ATTEMPT_NOT_DURABLE:{exc}"
            ) from exc

    def local_handoff_status(self) -> Mapping[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT state,COUNT(*) AS n FROM local_handoffs GROUP BY state"
            ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    def _complete_local_handoff_tx(
        self, db: sqlite3.Connection, handoff_id: str, outbox_id: str, *,
        state: str, error: str | None = None,
    ) -> None:
        existing = db.execute(
            "SELECT 1 FROM local_handoffs WHERE handoff_id=? AND state='PREPARED'",
            (handoff_id,),
        ).fetchone()
        if existing is None:
            return
        db.execute(
            "UPDATE local_handoffs SET state=?,outbox_id=?,last_error=?,updated_at=? "
            "WHERE handoff_id=? AND state='PREPARED'",
            (state, outbox_id, error, _normalise_utc(self._clock()), handoff_id),
        )

    @staticmethod
    def _row_to_local_handoff(row: sqlite3.Row) -> LocalHandoff:
        try:
            identity = json.loads(row["identity_json"])
            payload = json.loads(row["payload_json"])
            if not isinstance(identity, dict) or not isinstance(payload, dict):
                raise TypeError("identity/payload must be JSON objects")
            if row["state"] not in {"PREPARED", "COMPLETED", "CONFLICT"}:
                raise ValueError("unknown local-handoff state")
            if row["dataset"] not in PRODUCTION_SCHEMA_REGISTRY \
                    or payload.get("schema_version") != current_schema(row["dataset"]):
                raise ValueError("local-handoff dataset/schema mismatch")
            if deterministic_idempotency_key(
                    row["dataset"], payload["schema_version"], identity) != row["handoff_id"]:
                raise ValueError("local-handoff deterministic identity mismatch")
            if governed_identity(row["dataset"], payload) != identity:
                raise ValueError("local-handoff payload identity mismatch")
            local_payload = json.loads(row["local_line"])
            if not isinstance(local_payload, dict):
                raise TypeError("local line must be a JSON object")
            local_payload.setdefault("schema_version", payload["schema_version"])
            if _canonical_json(local_payload) != _canonical_json(payload):
                raise ValueError("local line/payload mismatch")
            if not row["local_path"] or not row["partition_date"]:
                raise ValueError("local-handoff source location missing")
            return LocalHandoff(
                handoff_id=row["handoff_id"], dataset=row["dataset"],
                symbol=row["symbol"], partition_date=row["partition_date"],
                identity=identity, payload=payload,
                local_path=row["local_path"], local_line=row["local_line"],
                lifecycle_obligation_id=row["lifecycle_obligation_id"],
                state=row["state"], outbox_id=row["outbox_id"],
                created_at=row["created_at"], updated_at=row["updated_at"],
                last_error=row["last_error"],
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OutboxCorruptionError(f"CORRUPT_LOCAL_HANDOFF:{exc}") from exc

    def _insert_record(self, db: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        db.execute(
            """INSERT INTO outbox_records (
                   outbox_id, idempotency_key, dataset, production_namespace,
                   schema_version, canonical_bucket, canonical_key, identity_json,
                   payload_json, payload_sha256, created_at, updated_at,
                   attempt_count, last_attempt_at, next_retry_at, delivery_state,
                   last_error, last_error_class, ack_json,
                   lifecycle_obligation_id, conflict_payload_sha256,
                   claim_owner, claim_expires_at, lease_reclaim_count, revision,
                   reconciliation_state, reconciliation_error
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )

    def get(self, outbox_id: str) -> OutboxRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM outbox_records WHERE outbox_id=?", (outbox_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def records(self) -> tuple[OutboxRecord, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM outbox_records ORDER BY created_at, outbox_id"
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def claim_next(
        self,
        *,
        now: str | None = None,
        owner_id: str | None = None,
        lease_seconds: int = 300,
    ) -> OutboxRecord | None:
        result = self.claim_next_result(
            now=now, owner_id=owner_id, lease_seconds=lease_seconds,
        )
        return result.record if result is not None else None

    def claim_next_result(
        self,
        *,
        now: str | None = None,
        owner_id: str | None = None,
        lease_seconds: int = 300,
        ignore_retry_schedule: bool = False,
        exclude_outbox_ids: tuple[str, ...] = (),
    ) -> ClaimResult | None:
        if lease_seconds <= 0:
            raise ValueError("POSITIVE_CLAIM_LEASE_REQUIRED")
        claimed_at = _normalise_utc(now or self._clock())
        claimed_dt = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
        if claimed_dt.tzinfo is None:
            claimed_dt = claimed_dt.replace(tzinfo=timezone.utc)
        lease_until = (claimed_dt + timedelta(seconds=lease_seconds)).isoformat()
        owner = owner_id or f"pid-{os.getpid()}-thread-{threading.get_ident()}"
        try:
            with self._transaction() as db:
                exclusion = ""
                parameters: list[Any] = [
                    DeliveryState.PENDING.value,
                    DeliveryState.RETRYABLE_FAILURE.value,
                    1 if ignore_retry_schedule else 0,
                    claimed_at,
                    DeliveryState.IN_FLIGHT.value,
                    claimed_at,
                ]
                if exclude_outbox_ids:
                    placeholders = ",".join("?" for _ in exclude_outbox_ids)
                    exclusion = f" AND outbox_id NOT IN ({placeholders})"
                    parameters.extend(exclude_outbox_ids)
                row = db.execute(
                    """SELECT * FROM outbox_records
                       WHERE (delivery_state=? OR
                             (delivery_state=? AND
                              (?=1 OR next_retry_at IS NULL OR next_retry_at<=?)) OR
                             (delivery_state=? AND claim_expires_at<=?))"""
                    + exclusion + " ORDER BY created_at, outbox_id LIMIT 1",
                    tuple(parameters),
                ).fetchone()
                if row is None:
                    return None
                source = DeliveryState(row["delivery_state"])
                if source is not DeliveryState.IN_FLIGHT:
                    self._assert_transition(row, DeliveryState.IN_FLIGHT)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, attempt_count=attempt_count+1,
                           last_attempt_at=?, next_retry_at=NULL, last_error=NULL,
                           last_error_class=NULL, updated_at=?,
                           claim_owner=?, claim_expires_at=?,
                           lease_reclaim_count=lease_reclaim_count+?,
                           revision=revision+1
                       WHERE outbox_id=? AND revision=?""",
                    (DeliveryState.IN_FLIGHT.value, claimed_at, claimed_at,
                     owner, lease_until, 1 if source is DeliveryState.IN_FLIGHT else 0,
                     row["outbox_id"], row["revision"]),
                )
                changed = db.execute(
                    "SELECT * FROM outbox_records WHERE outbox_id=?",
                    (row["outbox_id"],),
                ).fetchone()
                return ClaimResult(
                    record=self._row_to_record(changed),
                    state_before=source,
                    reclaimed_expired_lease=source is DeliveryState.IN_FLIGHT,
                )
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"CLAIM_NOT_DURABLE:{exc}") from exc

    def mark_retryable_failure(
        self, outbox_id: str, *, error: str, next_retry_at: str | None = None,
        error_class: str | None = None,
    ) -> OutboxRecord:
        return self._set_failure(
            outbox_id, DeliveryState.RETRYABLE_FAILURE, error, next_retry_at,
            error_class,
        )

    def mark_terminal_failure(
        self, outbox_id: str, *, error: str, error_class: str | None = None,
    ) -> OutboxRecord:
        return self._set_failure(
            outbox_id, DeliveryState.TERMINAL_FAILURE, error, None, error_class,
        )

    def mark_conflict(
        self, outbox_id: str, *, error: str, error_class: str = "ObjectConflict",
    ) -> OutboxRecord:
        return self._set_failure(
            outbox_id, DeliveryState.CONFLICT, error, None, error_class,
        )

    def _set_failure(
        self,
        outbox_id: str,
        state: DeliveryState,
        error: str,
        next_retry_at: str | None,
        error_class: str | None,
    ) -> OutboxRecord:
        if not error:
            raise ValueError("DELIVERY_ERROR_REQUIRED")
        now = _normalise_utc(self._clock())
        retry_at = _normalise_utc(next_retry_at) if next_retry_at else None
        if state is DeliveryState.RETRYABLE_FAILURE and retry_at is None:
            raise ValueError("RETRY_SCHEDULE_REQUIRED")
        try:
            with self._transaction() as db:
                row = self._required_row(db, outbox_id)
                self._assert_transition(row, state)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, last_error=?, last_error_class=?,
                           next_retry_at=?,
                           claim_owner=NULL, claim_expires_at=NULL,
                           updated_at=?, revision=revision+1 WHERE outbox_id=?""",
                    (state.value, error, error_class, retry_at, now, outbox_id),
                )
                changed = self._required_row(db, outbox_id)
                return self._row_to_record(changed)
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"FAILURE_STATE_NOT_DURABLE:{exc}") from exc

    def acknowledge(self, outbox_id: str, evidence: Mapping[str, Any]) -> OutboxRecord:
        """Persist ACK only for a successful write plus exact object verification.

        Required evidence binds the response and verification to bucket, key,
        payload hash, outbox ID, and idempotency key.  Merely returning from
        ``put_object`` or writing locally is never an acknowledgement.
        """
        now = _normalise_utc(self._clock())
        try:
            with self._transaction() as db:
                row = self._required_row(db, outbox_id)
                self._assert_transition(row, DeliveryState.ACKNOWLEDGED)
                expected = {
                    "outbox_id": row["outbox_id"],
                    "idempotency_key": row["idempotency_key"],
                    "canonical_bucket": row["canonical_bucket"],
                    "canonical_key": row["canonical_key"],
                    "payload_sha256": row["payload_sha256"],
                }
                for field, value in expected.items():
                    if evidence.get(field) != value:
                        raise CanonicalAckError(f"ACK_MISMATCH:{field}")
                status = evidence.get("put_status_code")
                put_created = isinstance(status, int) and 200 <= status < 300
                preexisting_verified = status == 412 and evidence.get("object_preexisted") is True
                if not (put_created or preexisting_verified):
                    raise CanonicalAckError("ACK_PUT_SUCCESS_REQUIRED")
                etag_available = evidence.get("etag_available")
                if etag_available is None:
                    if evidence.get("etag"):
                        etag_available = True
                    else:
                        raise CanonicalAckError("ACK_ETAG_ABSENCE_NOT_EXPLICIT")
                if not isinstance(etag_available, bool):
                    raise CanonicalAckError("ACK_ETAG_AVAILABILITY_REQUIRED")
                if etag_available and not evidence.get("etag"):
                    raise CanonicalAckError("ACK_ETAG_VALUE_REQUIRED")
                if etag_available and evidence.get("etag_observation") not in (None, "RETURNED"):
                    raise CanonicalAckError("ACK_ETAG_OBSERVATION_MISMATCH")
                if not etag_available and evidence.get("etag") not in (None, ""):
                    raise CanonicalAckError("ACK_ETAG_ABSENCE_CONTRADICTS_VALUE")
                if not etag_available and evidence.get("etag_observation") != "NOT_RETURNED_BY_S3":
                    raise CanonicalAckError("ACK_ETAG_ABSENCE_NOT_EXPLICIT")
                if evidence.get("verification_method") not in _ACK_VERIFICATION_METHODS:
                    raise CanonicalAckError("ACK_EXACT_VERIFICATION_REQUIRED")
                if evidence.get("verified_payload_sha256") != row["payload_sha256"]:
                    raise CanonicalAckError("ACK_VERIFIED_PAYLOAD_HASH_MISMATCH")
                ack = dict(evidence)
                ack["etag_available"] = etag_available
                ack["etag_observation"] = (
                    "RETURNED" if etag_available else "NOT_RETURNED_BY_S3")
                ack["acknowledged_at"] = _normalise_utc(
                    evidence.get("acknowledged_at") or now
                )
                ack_json = _canonical_json(ack)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, ack_json=?, last_error=NULL,
                           last_error_class=NULL, next_retry_at=NULL, claim_owner=NULL,
                           claim_expires_at=NULL, updated_at=?, revision=revision+1
                       WHERE outbox_id=?""",
                    (DeliveryState.ACKNOWLEDGED.value, ack_json, now, outbox_id),
                )
                return self._row_to_record(self._required_row(db, outbox_id))
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"ACK_NOT_DURABLE:{exc}") from exc

    def record_reconciliation(
        self, outbox_id: str, *, state: str, error: str | None = None,
    ) -> OutboxRecord:
        if state not in {
            "PENDING", "RECONCILED", "NOT_APPLICABLE", "NOT_LINKED", "ANOMALY",
            "TERMINAL_HISTORICAL",
        }:
            raise ValueError(f"INVALID_RECONCILIATION_STATE:{state}")
        now = _normalise_utc(self._clock())
        try:
            with self._transaction() as db:
                self._required_row(db, outbox_id)
                db.execute(
                    """UPDATE outbox_records SET reconciliation_state=?,
                       reconciliation_error=?, updated_at=?, revision=revision+1
                       WHERE outbox_id=?""",
                    (state, error, now, outbox_id),
                )
                return self._row_to_record(self._required_row(db, outbox_id))
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"RECONCILIATION_NOT_DURABLE:{exc}") from exc

    def acknowledged_needing_reconciliation(
        self, *, limit: int = 100, exclude_outbox_ids: tuple[str, ...] = (),
    ) -> tuple[OutboxRecord, ...]:
        """Read bounded ACK rows whose lifecycle reconciliation is incomplete."""
        if limit < 0:
            raise ValueError("RECONCILIATION_LIMIT_MUST_BE_NON_NEGATIVE")
        if limit == 0:
            return ()
        exclusion = ""
        parameters: list[Any] = [
            DeliveryState.ACKNOWLEDGED.value, "PENDING", "ANOMALY", "NOT_LINKED",
        ]
        if exclude_outbox_ids:
            placeholders = ",".join("?" for _ in exclude_outbox_ids)
            exclusion = f" AND outbox_id NOT IN ({placeholders})"
            parameters.extend(exclude_outbox_ids)
        parameters.append(limit)
        try:
            with self._lock:
                rows = self._db.execute(
                    """SELECT * FROM outbox_records
                       WHERE delivery_state=? AND reconciliation_state IN (?,?,?)"""
                    + exclusion + " ORDER BY updated_at, created_at, outbox_id LIMIT ?",
                    tuple(parameters),
                ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise OutboxPersistenceError(
                f"RECONCILIATION_SCAN_FAILED:{exc}") from exc

    def mark_terminal_historical(
        self, outbox_id: str, *, error: str | None = None,
    ) -> OutboxRecord:
        """Terminally park a historical ACK row whose reconciliation cannot complete.

        ``TERMINAL_HISTORICAL`` is deliberately absent from the reconciliation
        scan whitelist, so a parked row is never re-selected as active work.
        """
        return self.record_reconciliation(
            outbox_id, state="TERMINAL_HISTORICAL", error=error)

    def historical_acknowledged_needing_reconciliation(
        self, *, horizon: str, limit: int = 100,
    ) -> tuple[OutboxRecord, ...]:
        """Bounded ACK rows created before ``horizon`` whose reconciliation is incomplete.

        ``horizon`` is an ISO-8601 UTC instant captured when the delivery
        service starts; rows created strictly before it form the pre-existing
        backlog. The same reconciliation-state whitelist as
        :meth:`acknowledged_needing_reconciliation` applies.
        """
        if limit < 0:
            raise ValueError("RECONCILIATION_LIMIT_MUST_BE_NON_NEGATIVE")
        if limit == 0:
            return ()
        try:
            with self._lock:
                rows = self._db.execute(
                    """SELECT * FROM outbox_records
                       WHERE delivery_state=? AND reconciliation_state IN (?,?,?)
                         AND created_at < ?
                       ORDER BY created_at, outbox_id LIMIT ?""",
                    (
                        DeliveryState.ACKNOWLEDGED.value, "PENDING", "ANOMALY",
                        "NOT_LINKED", horizon, limit,
                    ),
                ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            raise OutboxPersistenceError(
                f"HISTORICAL_RECONCILIATION_SCAN_FAILED:{exc}") from exc

    def reconciliation_state_counts(self) -> Mapping[str, int]:
        """Reconciliation-state histogram for bounded aggregate reporting."""
        with self._lock:
            rows = self._db.execute(
                "SELECT reconciliation_state, COUNT(*) FROM outbox_records "
                "GROUP BY reconciliation_state"
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def reconciliation_contract(self, outbox_id: str) -> Mapping[str, Any]:
        """Exact future Block 1C comparison material; this does not claim ACK."""
        record = self.get(outbox_id)
        if record is None:
            raise KeyError(outbox_id)
        return {
            "outbox_id": record.outbox_id,
            "idempotency_key": record.idempotency_key,
            "dataset": record.dataset,
            "schema_version": record.schema_version,
            "canonical_bucket": record.canonical_bucket,
            "canonical_key": record.canonical_key,
            "record_identity": dict(record.record_identity),
            "payload_sha256": record.payload_sha256,
            "lifecycle_obligation_id": record.lifecycle_obligation_id,
            "canonical_ack_required_for_present": True,
        }

    def acknowledged_lifecycle_obligation_ids(self) -> frozenset[str]:
        """Boundary for lifecycle reconciliation; enqueue alone yields nothing."""
        with self._lock:
            rows = self._db.execute(
                """SELECT lifecycle_obligation_id FROM outbox_records
                   WHERE delivery_state=? AND lifecycle_obligation_id IS NOT NULL""",
                (DeliveryState.ACKNOWLEDGED.value,),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def status(self, *, now: datetime | None = None) -> OutboxStatus:
        reference = now or datetime.now(timezone.utc)
        with self._lock:
            counts = {
                row["delivery_state"]: row["n"]
                for row in self._db.execute(
                    "SELECT delivery_state, COUNT(*) AS n FROM outbox_records GROUP BY delivery_state"
                ).fetchall()
            }
            dataset_rows = self._db.execute(
                """SELECT dataset, delivery_state, COUNT(*) AS n
                   FROM outbox_records GROUP BY dataset, delivery_state"""
            ).fetchall()
            oldest = self._db.execute(
                """SELECT MIN(created_at) FROM outbox_records
                   WHERE delivery_state IN (?,?,?)""",
                (
                    DeliveryState.PENDING.value,
                    DeliveryState.IN_FLIGHT.value,
                    DeliveryState.RETRYABLE_FAILURE.value,
                ),
            ).fetchone()[0]
        breakdown: dict[str, dict[str, int]] = {}
        for row in dataset_rows:
            breakdown.setdefault(row["dataset"], {})[row["delivery_state"]] = row["n"]
        oldest_age = None
        if oldest:
            created = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            oldest_age = max(0.0, (reference - created.astimezone(timezone.utc)).total_seconds())
        terminal = counts.get(DeliveryState.TERMINAL_FAILURE.value, 0)
        conflict = counts.get(DeliveryState.CONFLICT.value, 0)
        return OutboxStatus(
            pending_count=counts.get(DeliveryState.PENDING.value, 0),
            in_flight_count=counts.get(DeliveryState.IN_FLIGHT.value, 0),
            acknowledged_count=counts.get(DeliveryState.ACKNOWLEDGED.value, 0),
            retryable_failure_count=counts.get(DeliveryState.RETRYABLE_FAILURE.value, 0),
            terminal_failure_count=terminal,
            conflict_count=conflict,
            terminal_or_conflict_count=terminal + conflict,
            oldest_pending_age_seconds=oldest_age,
            dataset_breakdown=breakdown,
        )

    def delivery_metrics(self) -> Mapping[str, Any]:
        """Durable aggregates used by worker observability after restart."""
        with self._lock:
            attempts = {
                row["dataset"]: int(row["n"] or 0)
                for row in self._db.execute(
                    "SELECT dataset, SUM(attempt_count) AS n FROM outbox_records GROUP BY dataset"
                ).fetchall()
            }
            failures = {
                row["last_error_class"]: int(row["n"])
                for row in self._db.execute(
                    """SELECT last_error_class, COUNT(*) AS n FROM outbox_records
                       WHERE last_error_class IS NOT NULL GROUP BY last_error_class"""
                ).fetchall()
            }
            recovered = int(self._db.execute(
                """SELECT COUNT(*) FROM outbox_records
                   WHERE ack_json LIKE '%\"object_preexisted\":true%'"""
            ).fetchone()[0])
            reclaimed = int(self._db.execute(
                "SELECT COALESCE(SUM(lease_reclaim_count),0) FROM outbox_records"
            ).fetchone()[0])
            anomalies = int(self._db.execute(
                "SELECT COUNT(*) FROM outbox_records WHERE reconciliation_state='ANOMALY'"
            ).fetchone()[0])
        return {
            "attempts_by_dataset": attempts,
            "failures_by_error_class": failures,
            "recovered_orphan_ack_count": recovered,
            "reclaimed_expired_lease_count": reclaimed,
            "reconciliation_anomaly_count": anomalies,
        }

    @staticmethod
    def _required_row(db: sqlite3.Connection, outbox_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM outbox_records WHERE outbox_id=?", (outbox_id,),
        ).fetchone()
        if row is None:
            raise KeyError(outbox_id)
        return row

    @staticmethod
    def _assert_transition(row: sqlite3.Row, target: DeliveryState) -> None:
        try:
            source = DeliveryState(row["delivery_state"])
        except ValueError as exc:
            raise OutboxCorruptionError(
                f"UNKNOWN_DELIVERY_STATE:{row['delivery_state']}"
            ) from exc
        if target not in STATE_TRANSITIONS[source]:
            raise InvalidDeliveryTransition(f"{source.value}->{target.value}")

    def _row_to_record(self, row: sqlite3.Row) -> OutboxRecord:
        try:
            identity = json.loads(row["identity_json"])
            payload = json.loads(row["payload_json"])
            ack = json.loads(row["ack_json"]) if row["ack_json"] else None
            state = DeliveryState(row["delivery_state"])
            if not isinstance(identity, dict) or not isinstance(payload, dict):
                raise TypeError("identity/payload must be JSON objects")
            if row["production_namespace"] != DATA_CONTRACT_VERSION:
                raise ValueError("production namespace mismatch")
            if row["schema_version"] != current_schema(row["dataset"]):
                raise ValueError("production schema mismatch")
            if not row["canonical_bucket"] or not row["canonical_key"]:
                raise ValueError("canonical destination missing")
            if row["canonical_bucket"] != self.canonical_bucket:
                raise ValueError("canonical bucket mismatch")
            if row["reconciliation_state"] not in {
                "PENDING", "RECONCILED", "NOT_APPLICABLE", "NOT_LINKED", "ANOMALY",
                "TERMINAL_HISTORICAL",
            }:
                raise ValueError("unknown reconciliation state")
            if _sha256_text(_canonical_json(payload)) != row["payload_sha256"]:
                raise ValueError("payload hash mismatch")
            if governed_identity(row["dataset"], payload) != identity:
                raise ValueError("governed payload identity mismatch")
            expected_idem = deterministic_idempotency_key(
                row["dataset"], row["schema_version"], identity,
            )
            if expected_idem != row["idempotency_key"]:
                raise ValueError("idempotency key mismatch")
            if row["outbox_id"] != f"outbox_{expected_idem}":
                raise ValueError("outbox id mismatch")
            schema_prefix = canonical_s3_schema_prefix(
                row["dataset"], schema=row["schema_version"],
            )
            if not row["canonical_key"].startswith(schema_prefix):
                raise ValueError("canonical destination prefix mismatch")
            relative = row["canonical_key"][len(schema_prefix):].split("/")
            if is_symbol_scoped(row["dataset"]):
                if len(relative) != 3 or not relative[0].startswith("symbol=") \
                        or not relative[0][len("symbol="):]:
                    raise ValueError("canonical symbol partition invalid")
                symbol = relative[0][len("symbol="):]
                date_part, object_name = relative[1:]
            else:
                if len(relative) != 2:
                    raise ValueError("canonical date partition invalid")
                symbol = ""
                date_part, object_name = relative
            if not date_part.startswith("date=") or not _DATE_RE.fullmatch(date_part[5:]):
                raise ValueError("canonical destination date partition invalid")
            if object_name != f"part-outbox-{expected_idem}.jsonl":
                raise ValueError("canonical destination part identity mismatch")
            expected_destination = canonical_outbox_destination(
                row["dataset"], symbol=symbol, partition_date=date_part[5:],
                idempotency_key=expected_idem,
            )
            if expected_destination != row["canonical_key"]:
                raise ValueError("canonical destination mismatch")
            if state is DeliveryState.RETRYABLE_FAILURE:
                if not row["next_retry_at"]:
                    raise ValueError("retryable row lacks next_retry_at")
                _normalise_utc(row["next_retry_at"])
            elif row["next_retry_at"] is not None:
                raise ValueError("non-retryable row has next_retry_at")
            if state is DeliveryState.IN_FLIGHT:
                if not row["claim_owner"] or not row["claim_expires_at"]:
                    raise ValueError("in-flight row lacks durable lease")
                _normalise_utc(row["claim_expires_at"])
            elif row["claim_owner"] is not None or row["claim_expires_at"] is not None:
                raise ValueError("non-in-flight row retains lease")
            if state is DeliveryState.ACKNOWLEDGED and ack is None:
                raise ValueError("acknowledged row has no ACK evidence")
            if state is not DeliveryState.ACKNOWLEDGED and ack is not None:
                raise ValueError("non-acknowledged row contains ACK evidence")
            if ack is not None:
                CanonicalDeliveryOutbox._validate_ack_row(row, ack)
            return OutboxRecord(
                outbox_id=row["outbox_id"],
                idempotency_key=row["idempotency_key"],
                dataset=row["dataset"],
                production_namespace=row["production_namespace"],
                schema_version=row["schema_version"],
                canonical_bucket=row["canonical_bucket"],
                canonical_key=row["canonical_key"],
                record_identity=identity,
                payload=payload,
                payload_sha256=row["payload_sha256"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                attempt_count=row["attempt_count"],
                last_attempt_at=row["last_attempt_at"],
                next_retry_at=row["next_retry_at"],
                delivery_state=state,
                last_error=row["last_error"],
                last_error_class=row["last_error_class"],
                canonical_ack=ack,
                lifecycle_obligation_id=row["lifecycle_obligation_id"],
                conflict_payload_sha256=row["conflict_payload_sha256"],
                claim_owner=row["claim_owner"],
                claim_expires_at=row["claim_expires_at"],
                lease_reclaim_count=row["lease_reclaim_count"],
                revision=row["revision"],
                reconciliation_state=row["reconciliation_state"],
                reconciliation_error=row["reconciliation_error"],
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise OutboxCorruptionError(
                f"CORRUPT_OUTBOX_ROW:{row['outbox_id']}:{exc}"
            ) from exc

    @staticmethod
    def _validate_ack_row(row: sqlite3.Row, ack: Any) -> None:
        if not isinstance(ack, dict):
            raise ValueError("ACK evidence must be an object")
        expected = {
            "outbox_id": row["outbox_id"],
            "idempotency_key": row["idempotency_key"],
            "canonical_bucket": row["canonical_bucket"],
            "canonical_key": row["canonical_key"],
            "payload_sha256": row["payload_sha256"],
        }
        if any(ack.get(field) != value for field, value in expected.items()):
            raise ValueError("ACK identity/hash binding mismatch")
        status = ack.get("put_status_code")
        put_created = isinstance(status, int) and 200 <= status < 300
        preexisting_verified = status == 412 and ack.get("object_preexisted") is True
        if not (put_created or preexisting_verified):
            raise ValueError("ACK lacks successful PUT/preexisting evidence")
        if ack.get("verification_method") not in _ACK_VERIFICATION_METHODS:
            raise ValueError("ACK exact verification method missing")
        if ack.get("verified_payload_sha256") != row["payload_sha256"]:
            raise ValueError("ACK verified payload hash mismatch")
        etag_available = ack.get("etag_available")
        if etag_available is None:
            etag_available = bool(ack.get("etag"))
        if not isinstance(etag_available, bool):
            raise ValueError("ACK ETag availability invalid")
        if etag_available and not ack.get("etag"):
            raise ValueError("ACK ETag value missing")
        if not etag_available:
            if ack.get("etag") not in (None, ""):
                raise ValueError("ACK ETag absence contradicts stored value")
            if ack.get("etag_observation") != "NOT_RETURNED_BY_S3":
                raise ValueError("ACK missing ETag is not truthfully recorded")
        try:
            _normalise_utc(str(ack["acknowledged_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("ACK timestamp invalid") from exc


__all__ = [
    "CanonicalAckError",
    "CanonicalDeliveryOutbox",
    "ClaimResult",
    "DeliveryState",
    "EnqueueOutcome",
    "EnqueueResult",
    "InvalidDeliveryTransition",
    "OutboxCorruptionError",
    "OutboxPersistenceError",
    "OutboxRecord",
    "OutboxStatus",
    "STATE_TRANSITIONS",
    "canonical_outbox_destination",
    "deterministic_idempotency_key",
    "governed_identity",
]
