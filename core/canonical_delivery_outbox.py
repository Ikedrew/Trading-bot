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
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Iterator, Mapping

from core.lifecycle_evidence_obligations import (
    EXACT_IDENTITY_FIELDS,
    IDENTITY_FIELD_PATHS,
)
from core.production_data_contract import (
    DATA_CONTRACT_VERSION,
    PRODUCTION_SCHEMA_REGISTRY,
    canonical_s3_key,
    current_schema,
    is_symbol_scoped,
)


DEFAULT_OUTBOX_PATH = Path("logs/canonical_delivery_outbox.sqlite3")
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
    next_retry_at: str | None
    delivery_state: DeliveryState
    last_error: str | None
    canonical_ack: Mapping[str, Any] | None
    lifecycle_obligation_id: str | None
    conflict_payload_sha256: str | None
    claim_owner: str | None
    claim_expires_at: str | None
    revision: int

    @property
    def account_id(self) -> Any:
        return self.record_identity.get("account_id")


@dataclass(frozen=True)
class EnqueueResult:
    outcome: EnqueueOutcome
    record: OutboxRecord


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

    _SCHEMA_VERSION = 1

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
                next_retry_at TEXT,
                delivery_state TEXT NOT NULL,
                last_error TEXT,
                ack_json TEXT,
                lifecycle_obligation_id TEXT,
                conflict_payload_sha256 TEXT,
                claim_owner TEXT,
                claim_expires_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_delivery
                ON outbox_records(delivery_state, next_retry_at, created_at);
            CREATE INDEX IF NOT EXISTS ix_outbox_dataset
                ON outbox_records(dataset, delivery_state);
            """
        )
        self._db.execute(f"PRAGMA user_version={self._SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield self._db
                self._db.execute("COMMIT")
            except Exception:
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

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
                               claim_expires_at=NULL, updated_at=?, revision=revision+1
                           WHERE idempotency_key=?""",
                        (DeliveryState.CONFLICT.value, reason, payload_hash, now, idem),
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
                    payload_hash, now, now, 0, None, DeliveryState.PENDING.value,
                    None, None, lifecycle_obligation_id, None, None, None, 1,
                ))
                created = db.execute(
                    "SELECT * FROM outbox_records WHERE idempotency_key=?", (idem,),
                ).fetchone()
                return EnqueueResult(EnqueueOutcome.CREATED, self._row_to_record(created))
        except (sqlite3.Error, OSError) as exc:
            raise OutboxPersistenceError(f"ENQUEUE_NOT_DURABLE:{exc}") from exc

    def _insert_record(self, db: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        db.execute(
            """INSERT INTO outbox_records (
                   outbox_id, idempotency_key, dataset, production_namespace,
                   schema_version, canonical_bucket, canonical_key, identity_json,
                   payload_json, payload_sha256, created_at, updated_at,
                   attempt_count, next_retry_at, delivery_state, last_error,
                   ack_json, lifecycle_obligation_id, conflict_payload_sha256,
                   claim_owner, claim_expires_at, revision
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                row = db.execute(
                    """SELECT * FROM outbox_records
                       WHERE delivery_state=? OR
                             (delivery_state=? AND
                              (next_retry_at IS NULL OR next_retry_at<=?)) OR
                             (delivery_state=? AND claim_expires_at<=?)
                       ORDER BY created_at, outbox_id LIMIT 1""",
                    (
                        DeliveryState.PENDING.value,
                        DeliveryState.RETRYABLE_FAILURE.value,
                        claimed_at,
                        DeliveryState.IN_FLIGHT.value,
                        claimed_at,
                    ),
                ).fetchone()
                if row is None:
                    return None
                source = DeliveryState(row["delivery_state"])
                if source is not DeliveryState.IN_FLIGHT:
                    self._assert_transition(row, DeliveryState.IN_FLIGHT)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, attempt_count=attempt_count+1,
                           next_retry_at=NULL, last_error=NULL, updated_at=?,
                           claim_owner=?, claim_expires_at=?, revision=revision+1
                       WHERE outbox_id=? AND revision=?""",
                    (DeliveryState.IN_FLIGHT.value, claimed_at, owner, lease_until,
                     row["outbox_id"], row["revision"]),
                )
                changed = db.execute(
                    "SELECT * FROM outbox_records WHERE outbox_id=?",
                    (row["outbox_id"],),
                ).fetchone()
                return self._row_to_record(changed)
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"CLAIM_NOT_DURABLE:{exc}") from exc

    def mark_retryable_failure(
        self, outbox_id: str, *, error: str, next_retry_at: str | None = None,
    ) -> OutboxRecord:
        return self._set_failure(
            outbox_id, DeliveryState.RETRYABLE_FAILURE, error, next_retry_at,
        )

    def mark_terminal_failure(self, outbox_id: str, *, error: str) -> OutboxRecord:
        return self._set_failure(outbox_id, DeliveryState.TERMINAL_FAILURE, error, None)

    def _set_failure(
        self,
        outbox_id: str,
        state: DeliveryState,
        error: str,
        next_retry_at: str | None,
    ) -> OutboxRecord:
        if not error:
            raise ValueError("DELIVERY_ERROR_REQUIRED")
        now = _normalise_utc(self._clock())
        retry_at = _normalise_utc(next_retry_at) if next_retry_at else None
        try:
            with self._transaction() as db:
                row = self._required_row(db, outbox_id)
                self._assert_transition(row, state)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, last_error=?, next_retry_at=?,
                           claim_owner=NULL, claim_expires_at=NULL,
                           updated_at=?, revision=revision+1 WHERE outbox_id=?""",
                    (state.value, error, retry_at, now, outbox_id),
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
                if not evidence.get("etag"):
                    raise CanonicalAckError("ACK_ETAG_REQUIRED")
                if evidence.get("verification_method") not in _ACK_VERIFICATION_METHODS:
                    raise CanonicalAckError("ACK_EXACT_VERIFICATION_REQUIRED")
                if evidence.get("verified_payload_sha256") != row["payload_sha256"]:
                    raise CanonicalAckError("ACK_VERIFIED_PAYLOAD_HASH_MISMATCH")
                ack = dict(evidence)
                ack["acknowledged_at"] = _normalise_utc(
                    evidence.get("acknowledged_at") or now
                )
                ack_json = _canonical_json(ack)
                db.execute(
                    """UPDATE outbox_records
                       SET delivery_state=?, ack_json=?, last_error=NULL,
                           next_retry_at=NULL, claim_owner=NULL,
                           claim_expires_at=NULL, updated_at=?, revision=revision+1
                       WHERE outbox_id=?""",
                    (DeliveryState.ACKNOWLEDGED.value, ack_json, now, outbox_id),
                )
                return self._row_to_record(self._required_row(db, outbox_id))
        except sqlite3.Error as exc:
            raise OutboxPersistenceError(f"ACK_NOT_DURABLE:{exc}") from exc

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

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OutboxRecord:
        try:
            identity = json.loads(row["identity_json"])
            payload = json.loads(row["payload_json"])
            ack = json.loads(row["ack_json"]) if row["ack_json"] else None
            state = DeliveryState(row["delivery_state"])
            if not isinstance(identity, dict) or not isinstance(payload, dict):
                raise TypeError("identity/payload must be JSON objects")
            if _sha256_text(_canonical_json(payload)) != row["payload_sha256"]:
                raise ValueError("payload hash mismatch")
            expected_idem = deterministic_idempotency_key(
                row["dataset"], row["schema_version"], identity,
            )
            if expected_idem != row["idempotency_key"]:
                raise ValueError("idempotency key mismatch")
            if row["outbox_id"] != f"outbox_{expected_idem}":
                raise ValueError("outbox id mismatch")
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
                next_retry_at=row["next_retry_at"],
                delivery_state=state,
                last_error=row["last_error"],
                canonical_ack=ack,
                lifecycle_obligation_id=row["lifecycle_obligation_id"],
                conflict_payload_sha256=row["conflict_payload_sha256"],
                claim_owner=row["claim_owner"],
                claim_expires_at=row["claim_expires_at"],
                revision=row["revision"],
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise OutboxCorruptionError(
                f"CORRUPT_OUTBOX_ROW:{row['outbox_id']}:{exc}"
            ) from exc


__all__ = [
    "CanonicalAckError",
    "CanonicalDeliveryOutbox",
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
