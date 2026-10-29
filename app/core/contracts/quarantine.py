"""
Quarantine System — Isolates invalid records without destroying evidence.

QUARANTINE CONTRACT:
    - Quarantine is NOT deletion.
    - A quarantined record remains FULLY recoverable.
    - The original record is NEVER modified.
    - Quarantine metadata wraps the original payload.

Storage:
    Local:  logs/quarantine/{layer}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-v10-data/quarantine/{layer}/{YYYY-MM-DD}.jsonl
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts.severity import Severity
from core.contracts.violation import ContractViolation

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/quarantine"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("quarantine")
_SCHEMA_VERSION = "quarantine_v1"


@dataclass(frozen=True)
class QuarantineRecord:
    """
    Complete quarantine envelope wrapping an invalid record.

    Contains all forensic data needed for audit and recovery.
    The original payload is preserved EXACTLY as received.

    GOVERNANCE: Every quarantine record includes the full validator
    identity (validator_id, validator_version, contract_version) of
    the primary validator that triggered quarantine. This enables
    forensic tracing of any quarantine decision.
    """

    # Identity
    record_id: str                      # trade_id or event identifier
    layer: str                          # Origin persistence layer
    timestamp: str                      # ISO-8601 quarantine time

    # Violation details
    violated_contract: str              # Primary contract violated
    validator_name: str                 # Validator that detected it
    severity: str                       # Severity level name
    reason: str                         # Human-readable explanation

    # Validator governance identity
    validator_id: str = ""              # Globally unique validator ID
    validator_version: int = 0          # Validator implementation version

    # Versions
    contract_version: str = "1.0"
    schema_version: str = ""            # Schema version of the record (if available)

    # Full violation list (record may violate multiple contracts)
    violations: tuple[dict[str, Any], ...] = ()

    # Original payload (NEVER modified)
    original_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "quarantine_version": "quarantine_v2",
            "record_id": self.record_id,
            "layer": self.layer,
            "timestamp": self.timestamp,
            "violated_contract": self.violated_contract,
            "validator_name": self.validator_name,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "severity": self.severity,
            "reason": self.reason,
            "contract_version": self.contract_version,
            "schema_version": self.schema_version,
            "violations": list(self.violations),
            "original_payload": self.original_payload,
        }


class QuarantineStore:
    """
    Thread-safe quarantine persistence.

    Append-only JSONL storage. Never deletes, never overwrites.
    Provides metrics for observability.
    """

    def __init__(self, *, local_dir: str = _LOCAL_DIR) -> None:
        self._local_dir = Path(local_dir)
        self._lock = threading.Lock()
        self._count = 0
        self._by_layer: dict[str, int] = {}
        self._by_contract: dict[str, int] = {}

    @property
    def total_quarantined(self) -> int:
        return self._count

    def quarantine(
        self,
        *,
        record: dict[str, Any],
        violations: list[ContractViolation],
        layer: str,
    ) -> QuarantineRecord:
        """
        Quarantine a record with its violations.

        The original record is preserved EXACTLY — never modified.
        Returns the QuarantineRecord for caller reference.
        """
        now = datetime.now(timezone.utc)
        primary = max(violations, key=lambda v: v.severity) if violations else None

        # Extract record identity
        record_id = (
            record.get("trade_id")
            or record.get("record_id")
            or record.get("cycle_id")
            or "payload_" + hashlib.sha256(json.dumps(
                record, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")).hexdigest()[:24]
        )

        qr = QuarantineRecord(
            record_id=str(record_id),
            layer=layer,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            violated_contract=primary.contract_name if primary else "unknown",
            validator_name=primary.validator_name if primary else "unknown",
            severity=primary.severity.name if primary else "ERROR",
            reason=primary.reason if primary else "unknown violation",
            validator_id=primary.validator_id if primary else "",
            validator_version=primary.validator_version if primary else 0,
            contract_version=primary.contract_version if primary else "1.0",
            schema_version=str(record.get("schema_version", "")),
            violations=tuple(v.to_dict() for v in violations),
            original_payload=record,  # NEVER modified
        )

        # A quarantine obligation exists only after an actual contract rejection.
        _ledger = None
        _obligation = None
        try:
            from core.lifecycle_evidence_obligations import (
                ObligationStatus, obligation_ledger,
            )
            _ledger = obligation_ledger()
            _obligation = _ledger.create(
                lifecycle_event_id=f"quarantine:{layer}:{qr.record_id}",
                lifecycle_stage="CONTRACT_REJECTION",
                expected_dataset="quarantine",
                identity={"record_id": qr.record_id, "symbol": str(
                    qr.original_payload.get("symbol") or "QUARANTINE")},
                originating_timestamp=qr.timestamp,
                due_state="EXPECTED_NOW", due_after="QUARANTINE_LOCAL_WRITE",
                requirement_type="CONDITIONAL",
                current_status=ObligationStatus.NOT_YET_DUE,
                producer="core.contracts.quarantine.QuarantineStore",
                producer_trigger=f"REJECTED:{layer}:{qr.violated_contract}",
            )
        except Exception as _obligation_exc:
            _ledger = None
            _obligation = None
            logger.error("[LIFECYCLE_OBLIGATION] quarantine creation failed: %s",
                         _obligation_exc)

        _persisted = self._persist(qr, now)
        if _ledger is not None and _obligation is not None:
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=_persisted,
                    observed_record_id=qr.record_id,
                    failure_reason="QUARANTINE_LOCAL_WRITE_FAILED",
                    provenance={"local_path_authority": "LOCAL_ONLY"},
                )
            except Exception as _obligation_exc:
                logger.error("[LIFECYCLE_OBLIGATION] quarantine outcome failed: %s",
                             _obligation_exc)

        # Update metrics
        with self._lock:
            self._count += 1
            self._by_layer[layer] = self._by_layer.get(layer, 0) + 1
            if primary:
                self._by_contract[primary.contract_name] = (
                    self._by_contract.get(primary.contract_name, 0) + 1
                )

        logger.warning(
            "[QUARANTINE] record_id=%s layer=%s contract=%s severity=%s reason=%s",
            record_id, layer,
            primary.contract_name if primary else "?",
            primary.severity.name if primary else "?",
            primary.reason if primary else "?",
        )

        return qr

    def _persist(self, qr: QuarantineRecord, now: datetime) -> bool:
        """Write quarantine record locally; S3 mirroring is secondary."""
        try:
            date_str = now.strftime("%Y-%m-%d")
            local_path = self._local_dir / qr.layer / f"{date_str}.jsonl"
            local_path.parent.mkdir(parents=True, exist_ok=True)

            record = qr.to_dict()
            record["schema_version"] = _SCHEMA_VERSION
            line = json.dumps(record, separators=(",", ":"), default=str) + "\n"

            from core.canonical_delivery import try_prepare_local_jsonl_handoffs
            try_prepare_local_jsonl_handoffs(
                dataset="quarantine", content=line,
                symbol=str(qr.original_payload.get("symbol") or "QUARANTINE"),
                partition_date=date_str, local_path=local_path,
            )

            fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            local_written = True

            from core.canonical_delivery import enqueue_canonical_delivery
            enqueue_canonical_delivery(
                dataset="quarantine", payload=record,
                symbol=str(qr.original_payload.get("symbol") or "QUARANTINE"),
                partition_date=date_str,
            )
            return local_written

        except Exception as exc:
            logger.debug("[QUARANTINE_PERSIST_FAIL] %s", exc)
            return False

    def stats(self) -> dict[str, Any]:
        """Return quarantine metrics for observability."""
        with self._lock:
            return {
                "total_quarantined": self._count,
                "by_layer": dict(self._by_layer),
                "by_contract": dict(self._by_contract),
            }

    def load_quarantined(
        self,
        *,
        layer: str | None = None,
        date_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load quarantined records for review/recovery. Read-only."""
        records: list[dict[str, Any]] = []
        search_dir = self._local_dir / layer if layer else self._local_dir

        if not search_dir.exists():
            return records

        for f in sorted(search_dir.rglob("*.jsonl")):
            if date_str and date_str not in f.name:
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            records.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue

        return records


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3_quarantine(layer: str, date_str: str, line: str) -> None:
    """Compatibility name for the governed durable handoff."""
    record = json.loads(line)
    symbol = str(record.get("original_payload", {}).get("symbol") or "QUARANTINE")
    from core.canonical_delivery import enqueue_canonical_delivery
    enqueue_canonical_delivery(
        dataset="quarantine", payload=record, symbol=symbol,
        partition_date=date_str,
    )
