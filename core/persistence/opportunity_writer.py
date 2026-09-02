"""
Opportunity Persistence Writer — V10 opportunity assessment records.

Persists every OpportunityAssessment from the V10 pipeline immediately
after evaluation, BEFORE any downstream strategy/horizon/entry/risk/execution
decisions. This ensures the opportunity record exists regardless of whether
the downstream pipeline eventually trades.

Also accepts legacy-engine-compatible fields for non-V10 paths.

Storage:
    Local: logs/opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-data-mk1/opportunities/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

SCHEMA: opportunities_v1 (the V10-enriched opportunity observation record)

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Modify strategy, risk, or execution logic
    - Gate or block trade execution
    - Retry or recover from failures

Design:
    - Fire-and-forget. Never raises to caller.
    - Local JSONL + fsync is canonical truth. S3 mirror is secondary.
    - One record per opportunity evaluation per cycle.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/opportunities"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("opportunities")
from core.production_data_contract import current_schema

_SCHEMA_VERSION = current_schema("opportunities")


def persist_opportunity(
    *,
    # ── Canonical lineage ──────────────────────────────────────────
    canonical_opportunity_id: str,
    observation_id: str,
    trigger_observation_id: str = "",

    # ── Identity ───────────────────────────────────────────────────
    symbol: str,
    bar_time: float,
    pattern: str,

    # ── Assessment fields (V10) ────────────────────────────────────
    opportunity_state: str = "",
    directional_bias: str = "",
    opportunity_type: str = "",

    # ── Quality scores ─────────────────────────────────────────────
    quality_location_score: float = 0.0,
    quality_structure_score: float = 0.0,
    quality_behaviour_score: float = 0.0,
    quality_formation_score: float = 0.0,
    quality_overall: float = 0.0,

    # ── Reasoning ──────────────────────────────────────────────────
    reasoning: list[str] | None = None,
    supporting_factors: list[str] | None = None,
    conflicting_factors: list[str] | None = None,

    # ── Detection-time market snapshot ─────────────────────────────
    bid_at_detection: float = 0.0,
    ask_at_detection: float = 0.0,

    # ── Legacy compatibility ───────────────────────────────────────
    engine: str = "V10",
    cycle_id: int = 0,
    entity_id: str = "",
) -> bool:
    """
    Persist one opportunity evaluation record to local JSONL + S3 mirror.

    Called immediately after opportunity assessment, before any downstream
    pipeline decisions. Records ALL opportunities regardless of whether
    they eventually result in EXECUTE, NO_TRADE, RISK_BLOCK, or any other
    downstream outcome.

    Args:
        canonical_opportunity_id: Canonical lineage root (REQUIRED).
        observation_id: Canonical observation identity (REQUIRED).
        trigger_observation_id: Reference to the observation that triggered
            this evaluation (defaults to observation_id when the bar itself
            is the trigger).
        symbol: Trading symbol.
        bar_time: Bar close epoch seconds.
        pattern: Detected pattern or opportunity type.
        opportunity_state: VALID / INVALID / WATCHING.
        directional_bias: BULLISH / BEARISH / NEUTRAL.
        opportunity_type: STRUCTURE_SHIFT / ZONE_REACTION / etc.
        quality_*_score: Dimension quality scores (0.0–1.0).
        quality_overall: Weighted composite quality.
        reasoning: Human-readable list of reasoning statements.
        supporting_factors: Factors supporting the opportunity thesis.
        conflicting_factors: Factors contradicting the thesis.
        bid_at_detection: Bid price at detection time (0.0 = unavailable).
        ask_at_detection: Ask price at detection time (0.0 = unavailable).
        engine: "V10" or "LEGACY".
        cycle_id: Scan cycle number (diagnostic only).
        entity_id: Legacy compatibility alias.

    Returns:
        True on success, False on failure. Never raises.
    """
    try:
        now = datetime.now(timezone.utc)
        if bar_time > 0:
            date_str = datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = now.strftime("%Y-%m-%d")

        # ── Build record ──────────────────────────────────────────
        record: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,

            # Canonical lineage
            "canonical_opportunity_id": canonical_opportunity_id,
            "observation_id": observation_id,
            "trigger_observation_id": trigger_observation_id or observation_id,

            # Identity
            "symbol": symbol,
            "bar_time": bar_time,
            "detection_timestamp_utc": bar_time,
            "pattern": pattern,

            # Assessment
            "opportunity_state": opportunity_state,
            "directional_bias": directional_bias or None,
            "opportunity_type": opportunity_type or None,

            # Quality
            "quality": {
                "location_score": round(quality_location_score, 4),
                "structure_score": round(quality_structure_score, 4),
                "behaviour_score": round(quality_behaviour_score, 4),
                "formation_score": round(quality_formation_score, 4),
                "overall_quality": round(quality_overall, 4),
            },

            # Reasoning
            "reasoning": list(reasoning) if reasoning else [],
            "supporting_factors": list(supporting_factors) if supporting_factors else [],
            "conflicting_factors": list(conflicting_factors) if conflicting_factors else [],

            # Detection-time market snapshot
            "bid_at_detection": bid_at_detection if bid_at_detection > 0 else None,
            "ask_at_detection": ask_at_detection if ask_at_detection > 0 else None,
            "spread_at_detection": (
                round(ask_at_detection - bid_at_detection, 8)
                if (ask_at_detection > 0 and bid_at_detection > 0)
                else None
            ),

            # Diagnostics
            "engine": engine,
            "cycle_id": cycle_id,
            "entity_id": entity_id or None,

            # Persistence metadata
            "persisted_at_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }

        # ── Local JSONL persistence (canonical truth) ─────────────
        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # ── S3 mirror (fire-and-forget) ──────────────────────────
        _write_s3(symbol, date_str, line)

        return True

    except Exception as exc:
        logger.debug("[OPPORTUNITY_PERSIST] write_failed: %s", exc)
        return False


def persist_opportunity_from_v10(
    *,
    opportunity: Any,  # OpportunityAssessment
    market_state: Any,  # V10MarketState
    bid: float = 0.0,
    ask: float = 0.0,
) -> bool:
    """
    Convenience wrapper: persist directly from V10 pipeline objects.

    Computes canonical IDs from the opportunity + market state and delegates
    to persist_opportunity().

    Args:
        opportunity: OpportunityAssessment dataclass instance.
        market_state: V10MarketState dataclass instance.
        bid: Bid price at detection time.
        ask: Ask price at detection time.

    Returns:
        True on success, False on failure.
    """
    if opportunity is None:
        return False

    try:
        from core.identity.canonical import mint_observation_id, make_canonical_opportunity_id

        # Canonical observation identity from bar close
        _obs_id = mint_observation_id(
            symbol=opportunity.symbol,
            bar_time=opportunity.timestamp_utc,
            timeframe="M5",
        )

        # Canonical opportunity identity using the opportunity type as pattern
        # (opportunity_type is the structural classification — STRUCTURE_SHIFT,
        # ZONE_REACTION, etc. — which serves as the V10 pattern equivalent)
        _pattern = opportunity.opportunity_type or "NONE"
        _canonical_opp_id = make_canonical_opportunity_id(
            symbol=opportunity.symbol,
            bar_time=opportunity.timestamp_utc,
            pattern=_pattern,
        )

        return persist_opportunity(
            canonical_opportunity_id=_canonical_opp_id,
            observation_id=_obs_id,
            trigger_observation_id=_obs_id,
            symbol=opportunity.symbol,
            bar_time=opportunity.timestamp_utc,
            pattern=_pattern,
            opportunity_state=opportunity.opportunity_state,
            directional_bias=opportunity.directional_bias,
            opportunity_type=opportunity.opportunity_type,
            quality_location_score=opportunity.quality.location_score,
            quality_structure_score=opportunity.quality.structure_score,
            quality_behaviour_score=opportunity.quality.behaviour_score,
            quality_formation_score=opportunity.quality.formation_score,
            quality_overall=opportunity.quality.overall_quality,
            reasoning=opportunity.reasoning,
            supporting_factors=opportunity.supporting_factors,
            conflicting_factors=opportunity.conflicting_factors,
            bid_at_detection=bid,
            ask_at_detection=ask,
            engine="V10",
        )
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror a single opportunity record to S3. Fire-and-forget. Never raises.

    Follows the same pattern as execution_result_writer.py and
    opportunity_assessment_writer.py.
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
        body = line + "\n"

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
        pass  # S3 failure must never affect runtime


# NOTE (Production V1 canonical cleanup): the LOCATION_OBSERVATION writer route
# was removed. It was fed by the retired V2/V3 opportunity observers — a parallel
# lineage outside the canonical V1 route. The opportunities dataset now carries
# only its canonical lifecycle/assessment records keyed by observation_id /
# canonical_opportunity_id.
