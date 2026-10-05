"""Bounded execution, retry, ACK, and reconciliation for canonical delivery.

The durable outbox is the only work authority.  This worker performs no
producer or trading actions and creates no lifecycle obligations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import random
import socket
import time
import uuid
from typing import Any, Callable, Mapping

from core.canonical_delivery_outbox import (
    CanonicalDeliveryOutbox,
    DeliveryState,
    OutboxRecord,
    governed_identity,
)
from core.lifecycle_evidence_obligations import (
    DATASET_DISPOSITIONS,
    LifecycleEvidenceLedger,
    ObligationStatus,
)

logger = logging.getLogger(__name__)


class ObjectNotFound(Exception):
    pass


class ObjectConflict(Exception):
    pass


class InvalidDeliveryRecord(Exception):
    pass


@dataclass(frozen=True)
class FailureClassification:
    category: str
    error_class: str
    error: str


@dataclass(frozen=True)
class DeliveryResult:
    outbox_id: str
    dataset: str
    state_before: DeliveryState
    state_after: DeliveryState
    attempt_number: int
    bucket: str
    key: str
    ack_verified: bool
    retry_scheduled: bool
    error_class: str | None
    lifecycle_reconciled: bool
    reconciliation_anomaly: str | None = None
    orphan_ack_recovered: bool = False
    expired_lease_reclaimed: bool = False


@dataclass(frozen=True)
class WorkerStatus:
    owner_id: str
    pending: int
    in_flight: int
    retryable: int
    acknowledged: int
    terminal: int
    conflict: int
    oldest_pending_age_seconds: float | None
    attempts_by_dataset: Mapping[str, int]
    failures_by_error_class: Mapping[str, int]
    recovered_orphan_ack_count: int
    reclaimed_expired_lease_count: int
    reconciliation_anomaly_count: int
    local_handoff_prepared_count: int
    local_handoff_completed_count: int
    local_handoff_conflict_count: int
    recovered_local_handoff_count: int
    local_handoff_recovery_failure_count: int
    last_run_at: str | None
    last_successful_ack_at: str | None
    last_worker_error: str | None
    last_reconciliation_pass_seconds: float | None


@dataclass(frozen=True)
class HistoricalTriageResult:
    """Outcome of one bounded historical backlog triage pass."""

    scanned: int
    skipped_terminal_historical: int
    deferred_to_active_pass: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc).isoformat()


def _payload_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidDeliveryRecord(f"PAYLOAD_NOT_CANONICAL_JSON:{exc}") from exc
    return encoded


def _error_details(exc: Exception) -> tuple[str, int | None]:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    metadata = response.get("ResponseMetadata", {}) or {}
    code = str(error.get("Code") or type(exc).__name__)
    status = metadata.get("HTTPStatusCode")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return code, status


def classify_delivery_failure(exc: Exception) -> FailureClassification:
    code, status = _error_details(exc)
    name = type(exc).__name__
    message = str(exc) or code
    if isinstance(exc, ObjectConflict):
        category = "CONFLICT"
    elif isinstance(exc, InvalidDeliveryRecord):
        category = "TERMINAL"
    elif code in {"ExpiredToken", "RequestExpired", "NoCredentialsError",
                  "CredentialRetrievalError", "RequestTimeout", "SlowDown",
                  "Throttling", "ThrottlingException", "ServiceUnavailable",
                  "InternalError"} or status in {408, 429, 500, 502, 503, 504} \
            or isinstance(exc, (TimeoutError, ConnectionError)):
        category = "RETRYABLE"
    elif code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch",
                  "InvalidBucketName", "NoSuchBucket"} or status in {400, 403, 404}:
        category = "TERMINAL"
    else:
        category = "RETRYABLE"
    return FailureClassification(category, code or name, message)


def _is_not_found(exc: Exception) -> bool:
    code, status = _error_details(exc)
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404


def _is_precondition_failed(exc: Exception) -> bool:
    code, status = _error_details(exc)
    return code in {"PreconditionFailed", "412"} or status == 412


class CanonicalDeliveryWorker:
    """One bounded, dependency-injected canonical delivery executor."""

    def __init__(
        self,
        outbox: CanonicalDeliveryOutbox,
        *,
        s3_client: Any | None = None,
        s3_client_factory: Callable[[], Any] | None = None,
        lifecycle_ledger: LifecycleEvidenceLedger | None = None,
        owner_id: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        jitter: Callable[[], float] = random.random,
        lease_seconds: int = 300,
        max_attempts: int = 8,
        base_retry_seconds: float = 5.0,
        max_retry_seconds: float = 900.0,
        jitter_ratio: float = 0.2,
        after_put_hook: Callable[[OutboxRecord], None] | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts <= 0 or base_retry_seconds <= 0:
            raise ValueError("INVALID_WORKER_POLICY")
        self.outbox = outbox
        self._s3_client = s3_client
        self._s3_client_factory = s3_client_factory or self._default_s3_client
        self.lifecycle_ledger = lifecycle_ledger
        self.owner_id = owner_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )
        self.clock = clock
        self.jitter = jitter
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds
        self.jitter_ratio = jitter_ratio
        self.after_put_hook = after_put_hook
        self._recovered_orphans = 0
        self._reclaimed_leases = 0
        self._reconciliation_anomalies = 0
        self._recovered_local_handoffs = 0
        self._local_handoff_recovery_failures = 0
        self._attempts_by_dataset: dict[str, int] = {}
        self._failures_by_error_class: dict[str, int] = {}
        self._last_run_at: str | None = None
        self._last_successful_ack_at: str | None = None
        self._last_worker_error: str | None = None
        self._last_reconciliation_pass_seconds: float | None = None

    @staticmethod
    def _default_s3_client() -> Any:
        import boto3
        from botocore.config import Config as BotoConfig
        return boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=5, read_timeout=10,
                retries={"max_attempts": 0},
            ),
        )

    def _client(self) -> Any:
        if self._s3_client is None:
            self._s3_client = self._s3_client_factory()
        return self._s3_client

    @staticmethod
    def _metadata(record: OutboxRecord) -> dict[str, str]:
        return {
            "outbox-id": record.outbox_id,
            "idempotency-key": record.idempotency_key,
            "payload-sha256": record.payload_sha256,
            "dataset": record.dataset,
            "schema-version": record.schema_version,
        }

    @staticmethod
    def _validate_record(record: OutboxRecord, body: bytes) -> None:
        if hashlib.sha256(body).hexdigest() != record.payload_sha256:
            raise InvalidDeliveryRecord("LOCAL_PAYLOAD_HASH_MISMATCH")
        if record.payload.get("schema_version") != record.schema_version:
            raise InvalidDeliveryRecord("LOCAL_SCHEMA_MISMATCH")
        if governed_identity(record.dataset, record.payload) != dict(record.record_identity):
            raise InvalidDeliveryRecord("LOCAL_EXACT_IDENTITY_MISMATCH")

    def _get_and_verify(self, record: OutboxRecord, body: bytes) -> Mapping[str, Any]:
        try:
            response = self._client().get_object(
                Bucket=record.canonical_bucket, Key=record.canonical_key,
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(record.canonical_key) from exc
            raise
        remote_body = response["Body"].read()
        if not isinstance(remote_body, bytes):
            remote_body = bytes(remote_body)
        remote_hash = hashlib.sha256(remote_body).hexdigest()
        metadata = {str(k).lower(): str(v) for k, v in
                    (response.get("Metadata", {}) or {}).items()}
        expected_metadata = self._metadata(record)
        if remote_hash != record.payload_sha256 or remote_body != body:
            raise ObjectConflict("CANONICAL_OBJECT_PAYLOAD_MISMATCH")
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ObjectConflict("CANONICAL_OBJECT_METADATA_MISMATCH")
        try:
            remote_payload = json.loads(remote_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ObjectConflict("CANONICAL_OBJECT_NOT_VALID_JSON") from exc
        if governed_identity(record.dataset, remote_payload) != dict(record.record_identity):
            raise ObjectConflict("CANONICAL_OBJECT_IDENTITY_MISMATCH")
        raw_etag = response.get("ETag")
        etag = str(raw_etag).strip() if raw_etag else ""
        return {
            "etag": etag or None,
            "etag_available": bool(etag),
            "etag_observation": "RETURNED" if etag else "NOT_RETURNED_BY_S3",
            "version_id": response.get("VersionId"),
            "verified_payload_sha256": remote_hash,
        }

    def _put(self, record: OutboxRecord, body: bytes) -> Mapping[str, Any]:
        return self._client().put_object(
            Bucket=record.canonical_bucket,
            Key=record.canonical_key,
            Body=body,
            ContentType="application/x-ndjson",
            Metadata=self._metadata(record),
            IfNoneMatch="*",
        )

    def _ack(
        self, record: OutboxRecord, verification: Mapping[str, Any],
        *, put_status: int, preexisting: bool,
    ) -> OutboxRecord:
        evidence = {
            "outbox_id": record.outbox_id,
            "idempotency_key": record.idempotency_key,
            "canonical_bucket": record.canonical_bucket,
            "canonical_key": record.canonical_key,
            "payload_sha256": record.payload_sha256,
            "put_status_code": put_status,
            "object_preexisted": preexisting,
            "etag": verification.get("etag"),
            "etag_available": verification.get("etag_available") is True,
            "etag_observation": verification.get("etag_observation"),
            "version_id": verification.get("version_id"),
            "verification_method": "GET_BODY_SHA256",
            "verified_payload_sha256": verification["verified_payload_sha256"],
            "attempt_number": record.attempt_count,
            "acknowledged_at": _iso(self.clock()),
        }
        return self.outbox.acknowledge(record.outbox_id, evidence)

    def _evaluate_lifecycle(
        self, record: OutboxRecord, *, mutate: bool = True,
    ) -> tuple[bool, str | None]:
        """Lifecycle classification, with an optional PRESENT promotion.

        Returns ``(reconcilable, anomaly)``. ``reconcilable`` is True only when
        the exact linked obligation exists and matches. ``anomaly`` is None both
        for a reconcilable record and for operational datasets that are
        deliberately NOT_APPLICABLE; callers distinguish those by
        ``reconcilable``. With ``mutate=True`` a reconcilable obligation is
        promoted to PRESENT (the active pass); with ``mutate=False`` the ledger
        is only read, so a triage caller can classify without side effects.
        """
        disposition = DATASET_DISPOSITIONS[record.dataset]["class"]
        if disposition not in {"A_LIVE_REQUIRED", "B_LIVE_CONDITIONAL"}:
            return False, None
        if not record.lifecycle_obligation_id:
            return False, "LIFECYCLE_OBLIGATION_NOT_LINKED"
        ledger = self.lifecycle_ledger
        if ledger is None:
            from core.lifecycle_evidence_obligations import obligation_ledger
            ledger = obligation_ledger()
        obligation = ledger.get(record.lifecycle_obligation_id)
        if obligation is None:
            return False, "LINKED_LIFECYCLE_OBLIGATION_NOT_FOUND"
        if obligation.expected_dataset != record.dataset:
            return False, "LIFECYCLE_DATASET_MISMATCH"
        if dict(obligation.expected_identity) != dict(record.record_identity):
            return False, "LIFECYCLE_EXACT_IDENTITY_MISMATCH"
        if obligation.expected_schema_version != record.schema_version:
            return False, "LIFECYCLE_SCHEMA_MISMATCH"
        if mutate and obligation.current_status != ObligationStatus.PRESENT.value:
            ledger.update(
                obligation.obligation_id,
                ObligationStatus.PRESENT,
                observed_record_id=record.outbox_id,
                provenance={
                    "canonical_delivery": "ACKNOWLEDGED",
                    "canonical_bucket": record.canonical_bucket,
                    "canonical_key": record.canonical_key,
                    "payload_sha256": record.payload_sha256,
                    "outbox_id": record.outbox_id,
                    "idempotency_key": record.idempotency_key,
                },
            )
        return True, None

    def _reconcile_lifecycle(self, record: OutboxRecord) -> tuple[bool, str | None]:
        reconciled, anomaly = self._evaluate_lifecycle(record)
        if anomaly:
            self._reconciliation_anomalies += 1
            self.outbox.record_reconciliation(
                record.outbox_id, state="ANOMALY", error=anomaly,
            )
            logger.error(
                "[CANONICAL_RECONCILIATION_ANOMALY] outbox=%s reason=%s",
                record.outbox_id, anomaly,
            )
            return False, anomaly
        self.outbox.record_reconciliation(
            record.outbox_id,
            state=("RECONCILED" if reconciled else "NOT_APPLICABLE"),
        )
        return reconciled, None

    def _safe_reconcile_lifecycle(
        self, record: OutboxRecord,
    ) -> tuple[bool, str | None]:
        try:
            return self._reconcile_lifecycle(record)
        except Exception as exc:
            anomaly = f"LIFECYCLE_RECONCILIATION_FAILED:{type(exc).__name__}"
            self._reconciliation_anomalies += 1
            self.outbox.record_reconciliation(
                record.outbox_id, state="ANOMALY", error=anomaly,
            )
            logger.exception(
                "[CANONICAL_RECONCILIATION_FAILED] outbox=%s",
                record.outbox_id,
            )
            return False, anomaly

    def _retry_at(self, attempt_number: int) -> str:
        raw = min(
            self.max_retry_seconds,
            self.base_retry_seconds * (2 ** max(0, attempt_number - 1)),
        )
        fraction = min(1.0, max(0.0, float(self.jitter())))
        delay = min(self.max_retry_seconds, raw * (1.0 + self.jitter_ratio * fraction))
        return _iso(self.clock() + timedelta(seconds=delay))

    def _failure_result(
        self, record: OutboxRecord, state_before: DeliveryState,
        classification: FailureClassification, *, reclaimed: bool,
    ) -> DeliveryResult:
        self._failures_by_error_class[classification.error_class] = (
            self._failures_by_error_class.get(classification.error_class, 0) + 1
        )
        error = f"{classification.error_class}:{classification.error}"
        retry = classification.category == "RETRYABLE" and record.attempt_count < self.max_attempts
        if classification.category == "CONFLICT":
            final = self.outbox.mark_conflict(
                record.outbox_id, error=error,
                error_class=classification.error_class,
            )
        elif retry:
            final = self.outbox.mark_retryable_failure(
                record.outbox_id, error=error,
                error_class=classification.error_class,
                next_retry_at=self._retry_at(record.attempt_count),
            )
        else:
            if classification.category == "RETRYABLE":
                error = f"MAX_ATTEMPTS_EXHAUSTED:{error}"
            final = self.outbox.mark_terminal_failure(
                record.outbox_id, error=error,
                error_class=classification.error_class,
            )
        return DeliveryResult(
            record.outbox_id, record.dataset, state_before, final.delivery_state,
            record.attempt_count, record.canonical_bucket, record.canonical_key,
            False, retry, classification.error_class, False,
            expired_lease_reclaimed=reclaimed,
        )

    def run_once(
        self, *, reconciliation_only: bool = False,
        exclude_outbox_ids: tuple[str, ...] = (),
        recover_handoffs: bool = True,
    ) -> DeliveryResult | None:
        self._last_run_at = _iso(self.clock())
        try:
            from core.canonical_delivery import recover_local_handoffs
            recovery = recover_local_handoffs(
                outbox=self.outbox, max_items=100 if recover_handoffs else 0,
            )
            self._recovered_local_handoffs += recovery.recovered
            self._local_handoff_recovery_failures += recovery.failed
            if recovery.failed:
                self._last_worker_error = "LOCAL_HANDOFF_RECOVERY_FAILED"
        except Exception as exc:
            self._local_handoff_recovery_failures += 1
            self._last_worker_error = f"LOCAL_HANDOFF_RECOVERY:{type(exc).__name__}"
            logger.exception("[CANONICAL_LOCAL_HANDOFF_RECOVERY_FAILED]")
        now = _iso(self.clock())
        claim = self.outbox.claim_next_result(
            now=now, owner_id=self.owner_id, lease_seconds=self.lease_seconds,
            ignore_retry_schedule=reconciliation_only,
            exclude_outbox_ids=exclude_outbox_ids,
        )
        if claim is None:
            return None
        record = claim.record
        if claim.reclaimed_expired_lease:
            self._reclaimed_leases += 1
        self._attempts_by_dataset[record.dataset] = (
            self._attempts_by_dataset.get(record.dataset, 0) + 1
        )
        body = _payload_bytes(record.payload)
        try:
            self._validate_record(record, body)
            orphan = False
            try:
                verification = self._get_and_verify(record, body)
                orphan = True
                acked = self._ack(
                    record, verification, put_status=412, preexisting=True,
                )
            except ObjectNotFound:
                try:
                    put_response = self._put(record, body)
                    if self.after_put_hook is not None:
                        self.after_put_hook(record)
                    verification = self._get_and_verify(record, body)
                    status = int((put_response.get("ResponseMetadata", {}) or {}).get(
                        "HTTPStatusCode", 200,
                    ))
                    acked = self._ack(
                        record, verification, put_status=status, preexisting=False,
                    )
                except Exception as exc:
                    if not _is_precondition_failed(exc):
                        raise
                    verification = self._get_and_verify(record, body)
                    orphan = True
                    acked = self._ack(
                        record, verification, put_status=412, preexisting=True,
                    )
            if orphan:
                self._recovered_orphans += 1
            reconciled, anomaly = self._safe_reconcile_lifecycle(acked)
            final = self.outbox.get(record.outbox_id) or acked
            self._last_successful_ack_at = final.canonical_ack.get("acknowledged_at") \
                if final.canonical_ack else self._last_successful_ack_at
            self._last_worker_error = None if anomaly is None else anomaly
            return DeliveryResult(
                record.outbox_id, record.dataset, claim.state_before,
                final.delivery_state, record.attempt_count,
                record.canonical_bucket, record.canonical_key, True, False,
                None, reconciled, anomaly, orphan,
                claim.reclaimed_expired_lease,
            )
        except Exception as exc:
            self._last_worker_error = f"{type(exc).__name__}:{exc}"
            current = self.outbox.get(record.outbox_id)
            if current is not None and current.delivery_state is DeliveryState.ACKNOWLEDGED:
                reconciled, anomaly = self._safe_reconcile_lifecycle(current)
                self._last_worker_error = anomaly
                return DeliveryResult(
                    record.outbox_id, record.dataset, claim.state_before,
                    DeliveryState.ACKNOWLEDGED, record.attempt_count,
                    record.canonical_bucket, record.canonical_key, True, False,
                    None, reconciled, anomaly, False,
                    claim.reclaimed_expired_lease,
                )
            return self._failure_result(
                record, claim.state_before, classify_delivery_failure(exc),
                reclaimed=claim.reclaimed_expired_lease,
            )

    def drain(self, *, max_items: int = 100) -> tuple[DeliveryResult, ...]:
        if max_items < 0:
            raise ValueError("MAX_ITEMS_MUST_BE_NON_NEGATIVE")
        results = []
        for _ in range(max_items):
            result = self.run_once()
            if result is None:
                break
            results.append(result)
        return tuple(results)

    def reconciliation_sweep(self, *, max_items: int = 100) -> tuple[DeliveryResult, ...]:
        """Repair acknowledged lifecycle anomalies, then inspect bounded delivery rows."""
        if max_items < 0:
            raise ValueError("MAX_ITEMS_MUST_BE_NON_NEGATIVE")
        results = []
        processed: list[str] = []
        for record in self.outbox.acknowledged_needing_reconciliation(limit=max_items):
            reconciled, anomaly = self._safe_reconcile_lifecycle(record)
            current = self.outbox.get(record.outbox_id) or record
            results.append(DeliveryResult(
                record.outbox_id, record.dataset, DeliveryState.ACKNOWLEDGED,
                DeliveryState.ACKNOWLEDGED, record.attempt_count,
                record.canonical_bucket, record.canonical_key, True, False,
                None, reconciled, anomaly,
            ))
            processed.append(record.outbox_id)
        remaining = max_items - len(results)
        for _ in range(remaining):
            result = self.run_once(
                reconciliation_only=True,
                exclude_outbox_ids=tuple(processed),
            )
            if result is None:
                break
            results.append(result)
            processed.append(result.outbox_id)
        return tuple(results)

    def reconcile_acknowledged(self, *, max_items: int = 100) -> tuple[DeliveryResult, ...]:
        """Retry bounded lifecycle reconciliation for durable ACK rows only."""
        if max_items < 0:
            raise ValueError("MAX_ITEMS_MUST_BE_NON_NEGATIVE")
        started = time.perf_counter()
        records = self.outbox.acknowledged_needing_reconciliation(limit=max_items)
        results = []
        for record in records:
            reconciled, anomaly = self._safe_reconcile_lifecycle(record)
            final = self.outbox.get(record.outbox_id) or record
            results.append(DeliveryResult(
                record.outbox_id, record.dataset, DeliveryState.ACKNOWLEDGED,
                DeliveryState.ACKNOWLEDGED, record.attempt_count,
                record.canonical_bucket, record.canonical_key, True, False,
                None, reconciled, anomaly,
            ))
        self._last_reconciliation_pass_seconds = time.perf_counter() - started
        return tuple(results)

    def triage_historical_backlog(
        self, *, horizon: str, max_items: int = 100000,
    ) -> HistoricalTriageResult:
        """One bounded pass that parks unreconcilable historical ACK rows.

        "Historical" means ACKNOWLEDGED rows created before ``horizon`` that
        still need reconciliation -- the pre-existing backlog. Each is
        classified once without side effects: a row whose reconciliation can
        never complete is parked TERMINAL_HISTORICAL, so it is never re-selected
        as active work and never emits a per-row anomaly again. Reconcilable and
        operational rows are deferred to the normal active pass, so existing
        repair and observability semantics are unchanged. No per-row anomaly is
        logged; the caller reports the aggregate.
        """
        if max_items < 0:
            raise ValueError("MAX_ITEMS_MUST_BE_NON_NEGATIVE")
        records = self.outbox.historical_acknowledged_needing_reconciliation(
            horizon=horizon, limit=max_items,
        )
        skipped = deferred = 0
        for record in records:
            try:
                reconcilable, anomaly = self._evaluate_lifecycle(record, mutate=False)
            except Exception as exc:
                reconcilable = False
                anomaly = f"LIFECYCLE_RECONCILIATION_FAILED:{type(exc).__name__}"
            if anomaly:
                self.outbox.mark_terminal_historical(record.outbox_id, error=anomaly)
                skipped += 1
            else:
                deferred += 1
        return HistoricalTriageResult(
            scanned=len(records), skipped_terminal_historical=skipped,
            deferred_to_active_pass=deferred,
        )

    def status(self) -> WorkerStatus:
        outbox = self.outbox.status(now=self.clock())
        durable = self.outbox.delivery_metrics()
        handoffs = self.outbox.local_handoff_status()
        return WorkerStatus(
            owner_id=self.owner_id,
            pending=outbox.pending_count,
            in_flight=outbox.in_flight_count,
            retryable=outbox.retryable_failure_count,
            acknowledged=outbox.acknowledged_count,
            terminal=outbox.terminal_failure_count,
            conflict=outbox.conflict_count,
            oldest_pending_age_seconds=outbox.oldest_pending_age_seconds,
            attempts_by_dataset=durable["attempts_by_dataset"],
            failures_by_error_class=durable["failures_by_error_class"],
            recovered_orphan_ack_count=durable["recovered_orphan_ack_count"],
            reclaimed_expired_lease_count=durable["reclaimed_expired_lease_count"],
            reconciliation_anomaly_count=durable["reconciliation_anomaly_count"],
            local_handoff_prepared_count=handoffs.get("PREPARED", 0),
            local_handoff_completed_count=handoffs.get("COMPLETED", 0),
            local_handoff_conflict_count=handoffs.get("CONFLICT", 0),
            recovered_local_handoff_count=self._recovered_local_handoffs,
            local_handoff_recovery_failure_count=self._local_handoff_recovery_failures,
            last_run_at=self._last_run_at,
            last_successful_ack_at=self._last_successful_ack_at,
            last_worker_error=self._last_worker_error,
            last_reconciliation_pass_seconds=self._last_reconciliation_pass_seconds,
        )


__all__ = [
    "CanonicalDeliveryWorker",
    "DeliveryResult",
    "FailureClassification",
    "HistoricalTriageResult",
    "WorkerStatus",
    "classify_delivery_failure",
]
