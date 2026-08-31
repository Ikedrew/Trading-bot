"""
Strategy Candidates Persistence Writer — Records every evaluated strategy candidate.

Persists ALL strategy candidates evaluated by the V10 strategy engine's
select_strategy() — not merely the winning strategy. This preserves the
complete candidate set for research questions such as:
    - Which strategies were considered for each opportunity?
    - How close was the winner to the alternatives?
    - Does the current winner actually outperform rejected strategies?

Storage:
    Local: logs/strategy_candidates/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-data-mk1/strategy_candidates/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

SCHEMA: strategy_candidates_v1

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect strategy evaluation or scoring
    - Modify candidate ranking or winner selection
    - Affect trading decisions, risk, or execution
    - Gate or block any pipeline behaviour

Design:
    - Fire-and-forget. Never raises to caller.
    - Local JSONL + fsync is canonical truth. S3 mirror is secondary.
    - One record per strategy candidate per evaluation.
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

_LOCAL_DIR = "logs/strategy_candidates"
from core.config import NEW_RUNTIME_S3_BUCKET

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = "strategy_candidates"
_SCHEMA_VERSION = "strategy_candidates_v1"


def persist_strategy_candidates(
    *,
    candidates: list[dict[str, Any]],
) -> bool:
    """
    Persist a batch of strategy candidate records to local JSONL + S3 mirror.

    Args:
        candidates: List of candidate record dicts. Each must contain:
            - candidate_id (str)
            - symbol (str)
            - bar_time (float)
            - strategy_family (str)
            - confidence (float)
            - reasoning (list[str])
            - supporting_conditions (dict[str, bool])
            - selected (bool)
            - rank (int)
            - Plus lineage fields (canonical_opportunity_id, observation_id, etc.)

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

                # Round floats for stability
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
        logger.debug("[STRATEGY_CANDIDATES_PERSIST] write_failed: %s", exc)
        return False


def build_candidate_records(
    *,
    candidates: list[tuple],
    winner_family: str,
    symbol: str,
    bar_time: float,
    lineage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Build serialisable candidate record dicts from the raw strategy engine tuples.

    Called AFTER the existing sort has occurred. The list order IS the rank.
    Does NOT re-rank, re-sort, or re-evaluate anything.

    Args:
        candidates: Sorted list of tuples (family, confidence, reasoning, conditions).
            This must be the POST-SORT list from select_strategy().
        winner_family: The strategy family that the existing engine selected.
            Used to mark selected=true on exactly one candidate.
        symbol: Trading symbol.
        bar_time: Bar close epoch seconds.
        lineage: Optional dict with canonical IDs. Recognised keys:
            canonical_opportunity_id, observation_id, decision_id,
            correlation_id, cycle_id, entity_id.
            When absent, observation_id is computed from symbol+bar_time.
            canonical_opportunity_id remains null unless supplied by lineage.

    Returns:
        List of candidate record dicts, in rank order (1 = first post-sort).
    """
    lin = lineage or {}

    # The opportunity lineage root must come from the parent opportunity.
    # Do not substitute observation_id; one observation can contain multiple
    # opportunity semantics.
    raw_canonical_opp_id = str(lin.get("canonical_opportunity_id", "") or "")
    canonical_opp_id = raw_canonical_opp_id or None
    observation_id = str(lin.get("observation_id", "") or "")
    if not observation_id:
        try:
            from core.identity.canonical import mint_observation_id
            observation_id = mint_observation_id(
                symbol=symbol, bar_time=bar_time, timeframe="M5"
            )
        except Exception:
            observation_id = ""

    decision_id = str(lin.get("decision_id", "") or "")
    correlation_id = str(lin.get("correlation_id", "") or "")
    cycle_id = int(lin.get("cycle_id", 0) or 0)
    entity_id = str(lin.get("entity_id", "") or "")

    records: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        family, confidence, reasoning, conditions = item
        family_str = family.value if hasattr(family, "value") else str(family)
        is_selected = (family_str == winner_family)

        # Deterministic candidate ID: unique per opportunity × family
        candidate_id = f"{canonical_opp_id or observation_id}:{family_str}"

        record: dict[str, Any] = {
            "candidate_id": candidate_id,
            "decision_id": decision_id,
            "canonical_opportunity_id": canonical_opp_id,
            "observation_id": observation_id,
            "correlation_id": correlation_id,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "strategy_family": family_str,
            "confidence": float(confidence),
            "reasoning": list(reasoning) if reasoning else [],
            "supporting_conditions": dict(conditions) if conditions else {},
            "selected": is_selected,
            "rank": idx + 1,  # Post-sort position: 1 = first (winner by priority)
            "bar_time": bar_time,
            "entity_id": entity_id or None,
        }
        records.append(record)

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3(symbol: str, date_str: str, content: str) -> None:
    """
    Mirror strategy candidates to S3. Fire-and-forget. Never raises.

    Follows the same pattern as opportunity_writer.py and
    execution_result_writer.py.
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
