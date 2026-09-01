"""
Risk Deviation — Measures whether realised trade risk matches intended risk.

PURPOSE:
    Distinguish between:
    1. Normal strategy losses (risk_deviation ≈ 1.0)
    2. Execution/protection failures (risk_deviation >> 1.0)

DEFINITIONS:
    planned_risk_R:  Always -1.0 (one unit of risk, by definition)
    actual_risk_R:   Realised R-multiple from the completed trade
    risk_deviation:  abs(actual_risk_R) when trade is a loss, or
                     actual_risk_R when trade is a win (positive)
                     Compared against planned_risk_R to detect anomalies.

    For losses:
        risk_deviation = abs(actual_risk_R / planned_risk_R)
        Normal loss: ≈ 1.0
        Over-risk:   > 1.5 (position lost more than intended)
        Critical:    > 3.0 (likely protection failure)

    For wins:
        risk_deviation = actual_risk_R (positive R-multiple)
        Always indicates planned risk was respected (won within framework)

STORAGE:
    logs/risk_deviation/{SYMBOL}/{YYYY-MM-DD}.jsonl

RULES:
    - Pure computation — no broker calls, no state mutation
    - Backwards compatible — existing schemas unchanged
    - Fire-and-forget persistence — never blocks trade journal
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/risk_deviation"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("risk_deviation")
_SCHEMA_VERSION = "risk_deviation_v1"

# ═══════════════════════════════════════════════════════════════════════════════
# RISK CLASSIFICATION THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

NORMAL_DEVIATION_MAX = 1.5       # Up to 1.5R loss is within tolerance (slippage, spread)
ELEVATED_DEVIATION_MAX = 3.0     # 1.5-3.0R indicates possible execution issue
# > 3.0R indicates likely protection failure


class RiskClassification:
    """Risk deviation severity classification."""
    NORMAL = "NORMAL"              # Loss within planned risk (deviation ≤ 1.5)
    ELEVATED = "ELEVATED"          # Loss somewhat exceeds plan (1.5 < deviation ≤ 3.0)
    CRITICAL = "CRITICAL"          # Loss far exceeds plan (deviation > 3.0)
    WIN = "WIN"                    # Trade was profitable
    NO_RISK_DATA = "NO_RISK_DATA"  # Cannot compute (missing SL or prices)


# ═══════════════════════════════════════════════════════════════════════════════
# RISK DEVIATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RiskDeviationResult:
    """Complete risk deviation measurement for one trade."""

    # Identity
    trade_id: str
    symbol: str
    correlation_id: str

    # Risk measurement
    planned_risk_R: float | None
    actual_risk_R: float | None
    risk_deviation: float | None

    # Classification
    risk_classification: str    # NORMAL / ELEVATED / CRITICAL / WIN / NO_RISK_DATA

    # Context (for forensic analysis)
    entry_price: float
    exit_price: float
    initial_sl: float
    direction: str
    risk_distance: float        # abs(entry - initial_sl) in price units
    pnl_distance: float         # price movement from entry to exit (signed)

    # Metadata
    timestamp_utc: str

    # Phase 3 Step 5: canonical lineage root of the originating opportunity.
    # Propagated from the journal's TradeRecord (Position identity); empty for
    # recovered/legacy trades — never fabricated.
    canonical_opportunity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_risk_deviation(
    *,
    trade_id: str,
    symbol: str,
    correlation_id: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    initial_sl: float,
    canonical_opportunity_id: str = "",
) -> RiskDeviationResult:
    """
    Compute risk deviation for a completed trade.

    Args:
        trade_id: Unique trade identifier
        symbol: Trading pair
        correlation_id: Forensic linking ID
        direction: "BUY" or "SELL"
        entry_price: Actual fill price at entry
        exit_price: Actual fill price at exit
        initial_sl: The stop loss level at time of entry

    Returns:
        RiskDeviationResult with all fields populated.
        If initial_sl is missing (0.0) or equals entry_price,
        returns NO_RISK_DATA classification.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Calculate risk distance (price units)
    risk_distance = abs(entry_price - initial_sl)

    # Guard: cannot compute if no meaningful SL
    if risk_distance == 0.0 or initial_sl == 0.0:
        return RiskDeviationResult(
            trade_id=trade_id,
            symbol=symbol,
            correlation_id=correlation_id,
            planned_risk_R=None,
            actual_risk_R=None,
            risk_deviation=None,
            risk_classification=RiskClassification.NO_RISK_DATA,
            entry_price=entry_price,
            exit_price=exit_price,
            initial_sl=initial_sl,
            direction=direction,
            risk_distance=0.0,
            pnl_distance=0.0,
            timestamp_utc=timestamp,
        )

    # Compute actual R-multiple (same formula as trade_truth.compute_r_multiple)
    if direction.upper() == "BUY":
        pnl_distance = exit_price - entry_price
    else:
        pnl_distance = entry_price - exit_price

    actual_risk_R = round(pnl_distance / risk_distance, 4)

    # Compute risk deviation
    planned_risk_R = -1.0  # By definition, intended risk is always -1R

    if actual_risk_R >= 0:
        # Winning trade — risk was respected, trade profitable
        risk_deviation = round(actual_risk_R, 4)
        classification = RiskClassification.WIN
    else:
        # Losing trade — compare actual loss to planned loss
        risk_deviation = round(abs(actual_risk_R) / abs(planned_risk_R), 4)
        if risk_deviation <= NORMAL_DEVIATION_MAX:
            classification = RiskClassification.NORMAL
        elif risk_deviation <= ELEVATED_DEVIATION_MAX:
            classification = RiskClassification.ELEVATED
        else:
            classification = RiskClassification.CRITICAL

    return RiskDeviationResult(
        trade_id=trade_id,
        symbol=symbol,
        correlation_id=correlation_id,
        canonical_opportunity_id=str(canonical_opportunity_id or ""),
        planned_risk_R=planned_risk_R,
        actual_risk_R=actual_risk_R,
        risk_deviation=risk_deviation,
        risk_classification=classification,
        entry_price=entry_price,
        exit_price=exit_price,
        initial_sl=initial_sl,
        direction=direction,
        risk_distance=round(risk_distance, 8),
        pnl_distance=round(pnl_distance, 8),
        timestamp_utc=timestamp,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def persist_risk_deviation(result: RiskDeviationResult) -> None:
    """
    Persist risk deviation result to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks trade journal.
    """
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / result.symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = result.to_dict()
        record["schema_version"] = _SCHEMA_VERSION
        record["semantic_stage"] = "post_outcome_analysis"
        record["authority"] = "diagnostic_projection"
        record["pre_trade_authority"] = False
        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget)
        try:
            _write_s3_risk_deviation(result.symbol, date_str, line + "\n")
        except Exception:
            pass

        # Log critical deviations
        if result.risk_classification == RiskClassification.CRITICAL:
            logger.critical(
                "[RISK_DEVIATION_CRITICAL] %s trade=%s actual_R=%.2f deviation=%.2f "
                "entry=%.5f exit=%.5f sl=%.5f direction=%s",
                result.symbol, result.trade_id, result.actual_risk_R,
                result.risk_deviation, result.entry_price, result.exit_price,
                result.initial_sl, result.direction,
            )
        elif result.risk_classification == RiskClassification.ELEVATED:
            logger.warning(
                "[RISK_DEVIATION_ELEVATED] %s trade=%s actual_R=%.2f deviation=%.2f",
                result.symbol, result.trade_id, result.actual_risk_R,
                result.risk_deviation,
            )

    except Exception as exc:
        logger.error("[RISK_DEVIATION_PERSIST_ERROR] %s", exc)


def _write_s3_risk_deviation(symbol: str, date_str: str, line: str) -> None:
    """Mirror to S3. Fire-and-forget. Never raises."""
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
            config=BotoConfig(connect_timeout=3, read_timeout=5, retries={"max_attempts": 0}),
        )
        key = f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass
        s3.put_object(Bucket=_S3_BUCKET, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    except Exception:
        pass
