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
        try:
            _write_s3_opportunity(opportunity.symbol, date_str, line + "\n")
        except Exception:
            pass  # S3 failure must NEVER affect opportunity persistence
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
            try:
                _write_s3_opportunity_batch(symbol, date_str, content)
            except Exception:
                pass  # S3 failure must NEVER affect opportunity persistence
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
    """
    Mirror opportunity record to S3. Fire-and-forget. Never raises.

    S3 Layout (Hive-compatible, Athena-queryable):
        opportunities/schema_version=opportunities_v1/symbol={SYMBOL}/date={DATE}/part-000.jsonl

    Partition keys:
        - schema_version: enables future schema evolution
        - symbol: enables per-pair opportunity analysis
        - date: enables time-range partition pruning
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = (
            f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}"
            f"/symbol={symbol}/date={date_str}/part-000.jsonl"
        )
        body = line

        # Read-append-write (acceptable for opportunity volume)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must NEVER affect opportunity persistence


def _write_s3_opportunity_batch(symbol: str, date_str: str, content: str) -> None:
    """
    Mirror a batch of opportunity records to S3. Fire-and-forget. Never raises.

    Same S3 key format as _write_s3_opportunity — appends to existing object.
    More efficient: one S3 round-trip per symbol/date batch instead of per record.
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = (
            f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}"
            f"/symbol={symbol}/date={date_str}/part-000.jsonl"
        )
        body = content

        # Read-append-write (acceptable for opportunity volume)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must NEVER affect opportunity persistence
