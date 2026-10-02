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
        from core.canonical_delivery import enqueue_canonical_delivery
        enqueue_canonical_delivery(
            dataset="assessments", payload=record, symbol=assessment.symbol,
            partition_date=date_str,
            lifecycle_obligation_id=(
                _obligation.obligation_id if _obligation is not None else None
            ),
        )
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
    """Compatibility name for the governed durable handoff."""
    from core.canonical_delivery import enqueue_canonical_jsonl
    enqueue_canonical_jsonl(
        dataset="assessments", content=line, symbol=symbol,
        partition_date=date_str,
    )
