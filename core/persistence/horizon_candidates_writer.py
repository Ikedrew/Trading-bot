"""
Horizon Candidates Persistence Writer — Records every evaluated horizon.

Persists ALL horizon assessments from the horizon classifier's
classify_horizons() — not merely the selected horizon. This preserves
the complete candidate search space for research questions such as:
    - Which horizons were considered for each opportunity?
    - Which eligible horizon was selected and which were rejected?
    - Why was each horizon eligible or ineligible?
    - Does the selected horizon actually outperform rejected horizons?

Storage:
    Local: logs/horizon_candidates/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-v10-data/horizon_candidates/schema_version=horizon_candidates_v1/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

SCHEMA: horizon_candidates_v1

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect horizon classification or eligibility
    - Modify confidence calculations
    - Change horizon selection
    - Affect trading decisions, risk, or execution
    - Gate or block any pipeline behaviour

Design:
    - Fire-and-forget. Never raises to caller.
    - Local JSONL + fsync is canonical truth. S3 mirror is secondary.
    - One record per evaluated horizon per classification.
    - Persistence failure never affects trading.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/horizon_candidates"
from core.config import NEW_RUNTIME_S3_BUCKET

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = "horizon_candidates"
_SCHEMA_VERSION = "horizon_candidates_v1"


def persist_horizon_candidates(
    *,
    candidates: list[dict[str, Any]],
) -> bool:
    """
    Persist a batch of horizon candidate records to local JSONL + S3 mirror.

    Args:
        candidates: List of candidate record dicts. Each must contain:
            - candidate_id (str)
            - symbol (str)
            - bar_time (float)
            - horizon (str)
            - eligible (bool)
            - confidence (float)
            - Plus lineage fields and selection_status.

    Returns:
        True on success, False on failure. Never raises.
    """
    if not candidates:
        return False

    try:
        now = datetime.now(timezone.utc)
        evaluated_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Group by symbol for efficient file writes
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for cand in candidates:
            sym = str(cand.get("symbol", "UNKNOWN"))
            by_symbol.setdefault(sym, []).append(cand)

        for symbol, cands in by_symbol.items():
            # Partition by date derived from bar_time
            bar_time = float(cands[0].get("bar_time", 0.0) or 0.0)
            if bar_time > 0:
                date_str = datetime.fromtimestamp(
                    bar_time, tz=timezone.utc
                ).strftime("%Y-%m-%d")
            else:
                date_str = now.strftime("%Y-%m-%d")

            path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)

            lines: list[str] = []
            for cand in cands:
                record = dict(cand)  # Copy — never mutate caller's data
                record.setdefault("schema_version", _SCHEMA_VERSION)
                record.setdefault("engine", "V10")
                record["evaluated_at_utc"] = evaluated_at

                # Round floats for stability (classifier already rounds
                # confidence to 4dp — this is a no-op safety net)
                if "confidence" in record:
                    record["confidence"] = round(float(record["confidence"]), 4)

                lines.append(
                    json.dumps(record, separators=(",", ":"), default=str)
                )

            content = "\n".join(lines) + "\n"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, content.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            # S3 mirror (fire-and-forget)
            _write_s3(symbol, date_str, content)

        return True

    except Exception as exc:
        logger.debug("[HORIZON_CANDIDATES_PERSIST] write_failed: %s", exc)
        return False


def build_horizon_candidate_records(
    *,
    assessments: list,
    selected_horizon: str = "",
    symbol: str,
    bar_time: float,
    lineage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Build serialisable candidate record dicts from HorizonAssessment objects.

    Called with the classification result's assessments. Derives
    selection_status DIRECTLY from existing runtime state — never
    re-ranks, re-scores, or re-classifies anything.

    Args:
        assessments: List of HorizonAssessment objects (from
            HorizonClassificationResult.assessments). Must each have
            .horizon, .eligible, .confidence, .reasoning, .evidence.
        selected_horizon: The horizon selected by the existing V10
            pipeline (result.horizon.horizon_type). Empty string when
            no V10 horizon selection occurred (legacy engine path).
        symbol: Trading symbol.
        bar_time: Bar close epoch seconds.
        lineage: Optional dict with canonical IDs. Recognised keys:
            canonical_opportunity_id, observation_id, decision_id,
            correlation_id, cycle_id, entity_id.
            When absent, canonical IDs are computed from symbol+bar_time.

    Returns:
        List of candidate record dicts — one per evaluated horizon.
    """
    lin = lineage or {}

    # Compute fallback canonical IDs when not provided
    canonical_opp_id = str(lin.get("canonical_opportunity_id", "") or "")
    observation_id = str(lin.get("observation_id", "") or "")
    if not observation_id:
        try:
            from core.identity.canonical import mint_observation_id
            observation_id = mint_observation_id(
                symbol=symbol, bar_time=bar_time, timeframe="M5"
            )
        except Exception:
            observation_id = ""
    if not canonical_opp_id:
        canonical_opp_id = observation_id

    decision_id = str(lin.get("decision_id", "") or "")
    correlation_id = str(lin.get("correlation_id", "") or "")
    cycle_id = int(lin.get("cycle_id", 0) or 0)
    entity_id = str(lin.get("entity_id", "") or "")

    selected_upper = str(selected_horizon or "").upper()

    records: list[dict[str, Any]] = []
    for assessment in assessments:
        horizon = str(getattr(assessment, "horizon", "") or "")
        eligible = bool(getattr(assessment, "eligible", False))
        confidence = float(getattr(assessment, "confidence", 0.0) or 0.0)
        reasoning = str(getattr(assessment, "reasoning", "") or "")
        evidence = dict(getattr(assessment, "evidence", {}) or {})

        # Derive selection_status from existing runtime state only
        if not eligible:
            selection_status = "INELIGIBLE"
        elif selected_upper and horizon.upper() == selected_upper:
            selection_status = "SELECTED"
        elif selected_upper:
            selection_status = "REJECTED"
        else:
            # No V10 horizon selection occurred (legacy engine path) —
            # the horizon was evaluated and eligible, but no selection
            # took place to reject it from.
            selection_status = "NOT_APPLICABLE"

        # Deterministic candidate ID: unique per opportunity × horizon
        candidate_id = f"{canonical_opp_id or observation_id}:{horizon}"

        record: dict[str, Any] = {
            "candidate_id": candidate_id,
            "canonical_opportunity_id": canonical_opp_id,
            "observation_id": observation_id,
            "decision_id": decision_id,
            "correlation_id": correlation_id,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "bar_time": bar_time,
            "horizon": horizon,
            "eligible": eligible,
            "confidence": confidence,
            "reasoning": reasoning,
            "evidence": evidence,
            "selection_status": selection_status,
            "entity_id": entity_id or None,
        }
        records.append(record)

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3(symbol: str, date_str: str, content: str) -> None:
    """
    Mirror horizon candidates to S3. Fire-and-forget. Never raises.

    Follows the same pattern as strategy_candidates_writer.py and
    opportunity_writer.py.
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

        # Read-append-write (acceptable for candidate volume)
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
        pass  # S3 failure must never affect runtime
