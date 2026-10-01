"""
Assessment Persistence — Local JSONL + S3 mirror for assessment records.

Every assessment is persisted regardless of trade outcome.
This enables research into:
    - Which assessment factors predict profitability?
    - Which high-confidence assessments were rejected and why?
    - Which assessment dimensions have the greatest predictive power?

Storage:
    Local:  logs/assessments/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-v10-data/assessments/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

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

from core.assessment.assessment import Assessment

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/assessments"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("assessments")
from core.production_data_contract import current_schema as _current_schema
_SCHEMA_VERSION = _current_schema("assessments")


def persist_assessment(assessment: Assessment) -> bool:
    """
    Persist an Assessment record to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks the trading pipeline.
    """
    _ledger = None
    _obligation = None
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        # ─── LOCAL PERSISTENCE ────────────────────────────────────────
        path = Path(_LOCAL_DIR) / assessment.symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = assessment.to_dict()
        if "schema_version" not in record:
            record["schema_version"] = _SCHEMA_VERSION
        try:
            from core.lifecycle_evidence_obligations import (
                create_dataset_obligation, obligation_ledger,
            )
            _ledger = obligation_ledger()
            _obligation = create_dataset_obligation(
                _ledger,
                event_id=f"assessment:{assessment.assessment_id}",
                lifecycle_stage="ASSESSMENT_SCORED",
                dataset="assessments",
                identity={"assessment_id": assessment.assessment_id,
                          "entity_id": assessment.entity_id,
                          "symbol": assessment.symbol},
                timestamp=assessment.assessed_at_utc,
                producer="core.assessment.persistence.persist_assessment",
                trigger="ASSESSMENT_BUILT_AFTER_SCORING",
            )
        except Exception as _obligation_exc:
            _ledger = None
            _obligation = None
            logger.warning("[LIFECYCLE_OBLIGATION] assessment creation failed: %s",
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
                observed_record_id=assessment.assessment_id,
                provenance={"local_path_authority": "LOCAL_ONLY"},
            )

        # ─── S3 MIRROR ───────────────────────────────────────────────
        _write_s3(assessment.symbol, date_str, line)
        return True

    except Exception as exc:
        if _ledger is not None and _obligation is not None:
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=False,
                    failure_reason=f"ASSESSMENT_LOCAL_WRITE:{type(exc).__name__}",
                )
            except Exception:
                pass
        logger.error("[ASSESSMENT_PERSIST_ERROR] symbol=%s id=%s error=%s",
                     assessment.symbol, assessment.assessment_id, exc)
        return False


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror a single assessment line to S3. Fire-and-forget.

    Pattern matches decision_ledger.py and execution_context.py.
    Never raises. Never blocks runtime.
    """
    try:
        from core import config
        if not getattr(config, "EVENT_STREAM_S3_MIRROR", False):
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
        from core.production_data_contract import canonical_s3_key
        key = canonical_s3_key("assessments", symbol=symbol, date=date_str)
        body = line + "\n"

        # Read-append-write (acceptable for assessment volume)
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
        from core.s3_write_observability import record_s3_success
        record_s3_success("assessments")
    except Exception as _exc:
        # Non-blocking: local write is authoritative; surface (not silent).
        try:
            from core.s3_write_observability import record_s3_failure
            record_s3_failure("assessments", _exc)
        except Exception:
            pass  # S3 failure must never affect runtime
