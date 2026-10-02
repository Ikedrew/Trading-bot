"""
Opportunity Persistence — JSONL storage for all detected opportunities.

Every opportunity is persisted regardless of outcome (executed, rejected, expired).
This creates the dataset needed for future analysis:
    - How many opportunities appear per session?
    - Which opportunities become trades?
    - Which rejected opportunities would have worked?
    - Are filters removing valuable setups?

Storage: logs/opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Block or gate execution
    - Modify any pipeline behaviour
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.opportunity.opportunity import Opportunity

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/opportunities"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("opportunities")
_SCHEMA_VERSION = "opportunities_v1"


def persist_opportunity(opportunity: Opportunity) -> bool:
    """
    Persist an Opportunity record to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks the trading pipeline.
    Called on every state transition (DETECTED, ASSESSED, REJECTED, EXECUTED, EXPIRED).
    """
    _ledger = None
    _obligation = None
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / opportunity.symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = opportunity.to_dict()
        _canonical_id = str(opportunity.canonical_opportunity_id or "")
        _opportunity_record_id = (
            f"lifecycle:{_canonical_id or opportunity.opportunity_id}:{opportunity.state}"
            if (_canonical_id or opportunity.opportunity_id) else ""
        )
        record["opportunity_record_id"] = _opportunity_record_id
        # Add persistence metadata
        record["_persisted_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        record["_state_at_persist"] = opportunity.state
        record["schema_version"] = _SCHEMA_VERSION

        try:
            from core.lifecycle_evidence_obligations import (
                create_dataset_obligation, obligation_ledger,
            )
            _ledger = obligation_ledger()
            _obligation = create_dataset_obligation(
                _ledger,
                event_id=(f"opportunity:{_opportunity_record_id}"
                          if _opportunity_record_id else
                          f"opportunity:lifecycle-missing:{opportunity.symbol}:{opportunity.cycle_id}"),
                lifecycle_stage="OPPORTUNITY_LIFECYCLE",
                dataset="opportunities",
                identity={"opportunity_record_id": _opportunity_record_id,
                          "canonical_opportunity_id": _canonical_id,
                          "symbol": opportunity.symbol},
                timestamp=str(opportunity.detected_at_utc),
                producer="core.opportunity.persistence.persist_opportunity",
                trigger=f"OPPORTUNITY_STATE_{opportunity.state}",
            )
        except Exception as _obligation_exc:
            _ledger = None
            _obligation = None
            logger.warning("[LIFECYCLE_OBLIGATION] opportunity lifecycle creation failed: %s",
                           _obligation_exc)

        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        if _ledger is not None and _obligation is not None:
            from core.lifecycle_evidence_obligations import record_producer_outcome
            record_producer_outcome(
                _ledger, _obligation, succeeded=True,
                observed_record_id=_opportunity_record_id or None,
                provenance={"local_path_authority": "LOCAL_ONLY"},
            )

        # ─── S3 MIRROR (Hive-partitioned, fire-and-forget) ───────────
        from core.canonical_delivery import enqueue_canonical_delivery
        enqueue_canonical_delivery(
            dataset="opportunities", payload=record,
            symbol=opportunity.symbol, partition_date=date_str,
            lifecycle_obligation_id=(
                _obligation.obligation_id if _obligation is not None else None
            ),
        )
        # ─── END S3 MIRROR ────────────────────────────────────────────
        return True

    except Exception as exc:
        if _ledger is not None and _obligation is not None:
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=False,
                    failure_reason=f"OPPORTUNITY_LIFECYCLE_LOCAL_WRITE:{type(exc).__name__}",
                )
            except Exception:
                pass
        logger.error("[OPPORTUNITY_PERSIST_ERROR] symbol=%s id=%s error=%s",
                     opportunity.symbol, opportunity.opportunity_id, exc)
        return False


def persist_opportunity_batch(opportunities: list[Opportunity]) -> bool:
    """
    Persist multiple opportunities efficiently (single file open per symbol/date).

    Fire-and-forget. Never raises.
    """
    if not opportunities:
        return False

    _ledger = None
    _obligations = []
    _persisted = False
    try:
        from core.lifecycle_evidence_obligations import (
            create_dataset_obligation, obligation_ledger,
        )
        _ledger = obligation_ledger()
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        persisted_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Group by symbol for efficient file writes
        by_symbol: dict[str, list[Opportunity]] = {}
        for opp in opportunities:
            by_symbol.setdefault(opp.symbol, []).append(opp)

        for symbol, opps in by_symbol.items():
            path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)

            lines: list[str] = []
            for opp in opps:
                record = opp.to_dict()
                _canonical_id = str(opp.canonical_opportunity_id or "")
                _record_id = (
                    f"lifecycle:{_canonical_id or opp.opportunity_id}:{opp.state}"
                    if (_canonical_id or opp.opportunity_id) else ""
                )
                record["opportunity_record_id"] = _record_id
                record["_persisted_at"] = persisted_at
                record["_state_at_persist"] = opp.state
                lines.append(json.dumps(record, separators=(",", ":"), default=str))
                _obligations.append(create_dataset_obligation(
                    _ledger,
                    event_id=f"opportunity:{_record_id or f'lifecycle-missing:{symbol}:{opp.cycle_id}'}",
                    lifecycle_stage="OPPORTUNITY_LIFECYCLE",
                    dataset="opportunities",
                    identity={"opportunity_record_id": _record_id,
                              "canonical_opportunity_id": _canonical_id,
                              "symbol": symbol},
                    timestamp=str(opp.detected_at_utc),
                    producer="core.opportunity.persistence.persist_opportunity",
                    trigger=f"OPPORTUNITY_STATE_{opp.state}",
                ))

            content = "\n".join(lines) + "\n"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, content.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            _persisted = True

            # ─── S3 MIRROR (batch — one put per symbol/date) ─────────
            from core.canonical_delivery import enqueue_canonical_batch
            enqueue_canonical_batch(
                dataset="opportunities",
                payloads=[json.loads(item) for item in lines],
                symbol=symbol, partition_date=date_str,
            )
            # ─── END S3 MIRROR ────────────────────────────────────────

        from core.lifecycle_evidence_obligations import record_producer_outcome
        for obligation in _obligations:
            record_producer_outcome(
                _ledger, obligation, succeeded=True,
                observed_record_id=str(obligation.expected_identity.get(
                    "opportunity_record_id") or "") or None,
                provenance={"local_path_authority": "LOCAL_ONLY"},
            )
        return True

    except Exception as exc:
        if _ledger is not None:
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                for obligation in _obligations:
                    if obligation.current_status != "PRODUCER_FAILED":
                        record_producer_outcome(
                            _ledger, obligation, succeeded=False,
                            failure_reason=f"OPPORTUNITY_BATCH_LOCAL_WRITE:{type(exc).__name__}",
                        )
            except Exception:
                pass
        logger.error("[OPPORTUNITY_BATCH_PERSIST_ERROR] count=%d error=%s",
                     len(opportunities), exc)
        return _persisted


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR (Hive-partitioned, fire-and-forget)
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3_opportunity(symbol: str, date_str: str, line: str) -> None:
    """Compatibility entry point; canonical delivery is now outbox-owned."""
    from core.canonical_delivery import enqueue_canonical_jsonl
    enqueue_canonical_jsonl(
        dataset="opportunities", content=line, symbol=symbol,
        partition_date=date_str,
    )


def _write_s3_opportunity_batch(symbol: str, date_str: str, content: str) -> None:
    """Compatibility entry point; each record is independently enqueued."""
    from core.canonical_delivery import enqueue_canonical_jsonl
    enqueue_canonical_jsonl(
        dataset="opportunities", content=content, symbol=symbol,
        partition_date=date_str,
    )
