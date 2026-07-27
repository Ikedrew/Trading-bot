"""
Protection Verification — Post-fill broker-side SL/TP confirmation.

After a trade is filled, this module verifies that the broker ACTUALLY
has SL/TP set on the position. If protection is missing or mismatched,
it attempts correction and logs a CRITICAL event.

PURPOSE:
    "Does the broker actually have our stop loss?"

CALLED:
    Immediately after successful order_send + register_from_execution.
    Before returning control to the main loop.

GUARANTEES:
    - Every filled trade is verified within seconds of fill
    - Missing protection triggers immediate correction attempt
    - All verification results are persisted for forensic analysis
    - Never blocks trading on failure (fire-and-forget correction)
    - Never modifies strategy, scoring, or decision logic

STORAGE:
    logs/protection_audit/{SYMBOL}/{YYYY-MM-DD}.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/protection_audit"
_S3_BUCKET = "trading-bot-data-mk1"
_S3_PREFIX = "protection_audit"
_SCHEMA_VERSION = "protection_audit_v1"
_MAX_VERIFY_ATTEMPTS = 3
_VERIFY_RETRY_DELAY_S = 0.5
_SL_TP_TOLERANCE = 1e-6  # Price tolerance for SL/TP matching


# ═══════════════════════════════════════════════════════════════════════════════
# PROTECTION STATUS
# ═══════════════════════════════════════════════════════════════════════════════

class ProtectionStatus(str, Enum):
    """Outcome of post-fill protection verification."""
    VERIFIED = "VERIFIED"                   # SL/TP confirmed on broker
    CORRECTED = "CORRECTED"                 # SL/TP was missing, successfully applied
    MISMATCH_CORRECTED = "MISMATCH_CORRECTED"  # SL/TP differed, successfully corrected
    FAILED_UNPROTECTED = "FAILED_UNPROTECTED"  # SL/TP missing AND correction failed
    FAILED_MISMATCH = "FAILED_MISMATCH"    # SL/TP mismatch AND correction failed
    POSITION_NOT_FOUND = "POSITION_NOT_FOUND"  # Could not locate position on broker
    VERIFICATION_ERROR = "VERIFICATION_ERROR"  # Exception during verification


# ═══════════════════════════════════════════════════════════════════════════════
# VERIFICATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProtectionVerificationResult:
    """Complete record of one protection verification attempt."""

    # Identity
    symbol: str
    position_ticket: int
    correlation_id: str

    # What we requested
    requested_sl: float
    requested_tp: float

    # What broker has
    broker_confirmed_sl: float
    broker_confirmed_tp: float

    # Outcome
    protection_status: str
    protection_failure_reason: str

    # Timing
    verification_timestamp_utc: str
    verification_latency_ms: int
    attempts: int

    # Correction (if attempted)
    correction_attempted: bool
    correction_success: bool
    correction_detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE VERIFICATION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def verify_protection(
    *,
    symbol: str,
    position_ticket: int,
    requested_sl: float,
    requested_tp: float,
    correlation_id: str = "",
    execution_module: Any = None,
) -> ProtectionVerificationResult:
    """
    Verify that broker-side SL/TP protection exists on a filled position.

    Called immediately after order fill. Queries MT5 for the actual position
    state and compares against requested values.

    If SL/TP are missing or mismatched:
        1. Logs CRITICAL event
        2. Attempts correction via position_modify_sl_tp
        3. Re-verifies after correction

    Args:
        symbol: Trading pair
        position_ticket: MT5 position ticket (deal ID from fill)
        requested_sl: The SL we sent in order_send
        requested_tp: The TP we sent in order_send
        correlation_id: For forensic linking
        execution_module: MT5Execution instance for correction attempts

    Returns:
        ProtectionVerificationResult with full audit trail
    """
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)

    # Default result (will be overwritten on success)
    result = ProtectionVerificationResult(
        symbol=symbol,
        position_ticket=position_ticket,
        correlation_id=correlation_id,
        requested_sl=requested_sl,
        requested_tp=requested_tp,
        broker_confirmed_sl=0.0,
        broker_confirmed_tp=0.0,
        protection_status=ProtectionStatus.VERIFICATION_ERROR.value,
        protection_failure_reason="",
        verification_timestamp_utc=now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        verification_latency_ms=0,
        attempts=0,
        correction_attempted=False,
        correction_success=False,
        correction_detail="",
    )

    try:
        # ─── QUERY BROKER FOR POSITION STATE ──────────────────────────
        broker_sl, broker_tp, found, attempts = _query_broker_position(
            position_ticket=position_ticket,
            symbol=symbol,
        )
        result.attempts = attempts

        if not found:
            result.protection_status = ProtectionStatus.POSITION_NOT_FOUND.value
            result.protection_failure_reason = (
                f"Position ticket={position_ticket} not found on broker after {attempts} attempts"
            )
            logger.critical(
                "[PROTECTION_CRITICAL] %s ticket=%d — POSITION NOT FOUND on broker",
                symbol, position_ticket,
            )
            _persist_result(result, symbol)
            return result

        result.broker_confirmed_sl = broker_sl
        result.broker_confirmed_tp = broker_tp

        # ─── CHECK SL PROTECTION ─────────────────────────────────────
        sl_ok = _values_match(broker_sl, requested_sl, _SL_TP_TOLERANCE)
        tp_ok = _values_match(broker_tp, requested_tp, _SL_TP_TOLERANCE)
        sl_missing = broker_sl == 0.0 and requested_sl != 0.0
        tp_missing = broker_tp == 0.0 and requested_tp != 0.0
        sl_mismatch = not sl_ok and not sl_missing and requested_sl != 0.0
        tp_mismatch = not tp_ok and not tp_missing and requested_tp != 0.0

        if sl_ok and tp_ok:
            # ─── PROTECTION VERIFIED ──────────────────────────────────
            result.protection_status = ProtectionStatus.VERIFIED.value
            result.verification_latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "[PROTECTION_VERIFIED] %s ticket=%d sl=%.5f tp=%.5f",
                symbol, position_ticket, broker_sl, broker_tp,
            )
            _persist_result(result, symbol)
            return result

        # ─── PROTECTION PROBLEM DETECTED ─────────────────────────────
        if sl_missing or tp_missing:
            problem = "SL_MISSING" if sl_missing else ""
            if tp_missing:
                problem = f"{problem}+TP_MISSING" if problem else "TP_MISSING"
            logger.critical(
                "[PROTECTION_CRITICAL] %s ticket=%d — %s | "
                "requested_sl=%.5f broker_sl=%.5f requested_tp=%.5f broker_tp=%.5f",
                symbol, position_ticket, problem,
                requested_sl, broker_sl, requested_tp, broker_tp,
            )
        elif sl_mismatch or tp_mismatch:
            problem = "SL_MISMATCH" if sl_mismatch else ""
            if tp_mismatch:
                problem = f"{problem}+TP_MISMATCH" if problem else "TP_MISMATCH"
            logger.warning(
                "[PROTECTION_MISMATCH] %s ticket=%d — %s | "
                "requested_sl=%.5f broker_sl=%.5f requested_tp=%.5f broker_tp=%.5f",
                symbol, position_ticket, problem,
                requested_sl, broker_sl, requested_tp, broker_tp,
            )

        # ─── ATTEMPT CORRECTION ──────────────────────────────────────
        result.correction_attempted = True
        correction_ok = _attempt_correction(
            symbol=symbol,
            position_ticket=position_ticket,
            target_sl=requested_sl,
            target_tp=requested_tp,
            execution_module=execution_module,
        )
        result.correction_success = correction_ok

        if correction_ok:
            # Re-verify after correction
            broker_sl2, broker_tp2, found2, _ = _query_broker_position(
                position_ticket=position_ticket,
                symbol=symbol,
            )
            if found2:
                result.broker_confirmed_sl = broker_sl2
                result.broker_confirmed_tp = broker_tp2

            if sl_missing or tp_missing:
                result.protection_status = ProtectionStatus.CORRECTED.value
                result.correction_detail = f"Protection restored: sl={broker_sl2:.5f} tp={broker_tp2:.5f}"
            else:
                result.protection_status = ProtectionStatus.MISMATCH_CORRECTED.value
                result.correction_detail = f"Mismatch corrected: sl={broker_sl2:.5f} tp={broker_tp2:.5f}"

            logger.info(
                "[PROTECTION_CORRECTED] %s ticket=%d — sl=%.5f tp=%.5f",
                symbol, position_ticket, broker_sl2, broker_tp2,
            )
        else:
            # Correction failed — position remains at risk
            if sl_missing or tp_missing:
                result.protection_status = ProtectionStatus.FAILED_UNPROTECTED.value
                result.protection_failure_reason = (
                    f"SL/TP missing on broker AND correction failed. "
                    f"broker_sl={broker_sl} broker_tp={broker_tp}"
                )
            else:
                result.protection_status = ProtectionStatus.FAILED_MISMATCH.value
                result.protection_failure_reason = (
                    f"SL/TP mismatch AND correction failed. "
                    f"requested_sl={requested_sl} broker_sl={broker_sl} "
                    f"requested_tp={requested_tp} broker_tp={broker_tp}"
                )

            logger.critical(
                "[PROTECTION_FAILED] %s ticket=%d — POSITION MAY BE UNPROTECTED | %s",
                symbol, position_ticket, result.protection_failure_reason,
            )

            # ─── EMERGENCY: attempt to close unprotected position ─────
            if sl_missing and execution_module is not None:
                logger.critical(
                    "[PROTECTION_EMERGENCY] %s ticket=%d — SL missing, correction failed. "
                    "Position at unlimited risk.",
                    symbol, position_ticket,
                )
                # Note: We do NOT auto-close here. That would be a strategy decision.
                # Instead we escalate via Discord and leave the position for manual review.
                _emit_discord_alert(symbol, position_ticket, result)

    except Exception as exc:
        result.protection_status = ProtectionStatus.VERIFICATION_ERROR.value
        result.protection_failure_reason = f"Exception during verification: {type(exc).__name__}: {str(exc)[:200]}"
        logger.error(
            "[PROTECTION_ERROR] %s ticket=%d — verification exception: %s",
            symbol, position_ticket, exc,
        )

    result.verification_latency_ms = int((time.perf_counter() - t0) * 1000)
    _persist_result(result, symbol)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _query_broker_position(
    *,
    position_ticket: int,
    symbol: str,
) -> tuple[float, float, bool, int]:
    """
    Query MT5 for position SL/TP state.

    Retries up to _MAX_VERIFY_ATTEMPTS with delay (broker may take
    a moment to propagate the position after fill).

    Returns: (broker_sl, broker_tp, found, attempts)
    """
    for attempt in range(1, _MAX_VERIFY_ATTEMPTS + 1):
        positions = mt5_call(mt5.positions_get, ticket=position_ticket)
        if positions is not None and len(positions) > 0:
            pos = positions[0]
            return float(pos.sl), float(pos.tp), True, attempt

        # Position not yet visible — wait and retry
        if attempt < _MAX_VERIFY_ATTEMPTS:
            time.sleep(_VERIFY_RETRY_DELAY_S)

    # Also try by symbol as fallback (some brokers use different ticket refs)
    positions = mt5_call(mt5.positions_get, symbol=symbol)
    if positions is not None:
        for pos in positions:
            if int(pos.ticket) == position_ticket:
                return float(pos.sl), float(pos.tp), True, _MAX_VERIFY_ATTEMPTS

    return 0.0, 0.0, False, _MAX_VERIFY_ATTEMPTS


def _values_match(actual: float, expected: float, tolerance: float) -> bool:
    """Check if two price values match within tolerance."""
    if expected == 0.0:
        return True  # No protection was requested
    return abs(actual - expected) <= tolerance


def _attempt_correction(
    *,
    symbol: str,
    position_ticket: int,
    target_sl: float,
    target_tp: float,
    execution_module: Any,
) -> bool:
    """
    Attempt to apply SL/TP to position via position_modify_sl_tp.

    Returns True if correction succeeded.
    """
    if execution_module is None:
        logger.warning(
            "[PROTECTION_CORRECTION] No execution module — cannot correct ticket=%d",
            position_ticket,
        )
        return False

    try:
        result = execution_module.position_modify_sl_tp(
            symbol=symbol,
            position_ticket=position_ticket,
            sl=target_sl,
            tp=target_tp,
        )
        if result.ok:
            logger.info(
                "[PROTECTION_CORRECTION_OK] ticket=%d sl=%.5f tp=%.5f",
                position_ticket, target_sl, target_tp,
            )
            return True
        else:
            logger.warning(
                "[PROTECTION_CORRECTION_FAILED] ticket=%d retcode=%d comment=%s",
                position_ticket, result.retcode, result.comment,
            )
            return False
    except Exception as exc:
        logger.error(
            "[PROTECTION_CORRECTION_ERROR] ticket=%d error=%s",
            position_ticket, exc,
        )
        return False


def _emit_discord_alert(symbol: str, ticket: int, result: ProtectionVerificationResult) -> None:
    """Send critical Discord alert for unprotected position."""
    try:
        from core import config
        _dl = getattr(config, "_discord_logger", None)
        if _dl is not None:
            _dl.event("CRITICAL_PROTECTION_FAILURE", {
                "symbol": symbol,
                "ticket": ticket,
                "status": result.protection_status,
                "requested_sl": result.requested_sl,
                "broker_sl": result.broker_confirmed_sl,
                "requested_tp": result.requested_tp,
                "broker_tp": result.broker_confirmed_tp,
                "reason": result.protection_failure_reason,
                "action_required": "MANUAL_REVIEW — position may be unprotected",
            })
    except Exception:
        pass  # Discord failure must never affect the critical path


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _persist_result(result: ProtectionVerificationResult, symbol: str) -> None:
    """Persist verification result to local JSONL + S3 mirror. Fire-and-forget."""
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = result.to_dict()
        record["schema_version"] = _SCHEMA_VERSION
        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget)
        try:
            _write_s3_protection_audit(symbol, date_str, line + "\n")
        except Exception:
            pass
    except Exception as exc:
        logger.error("[PROTECTION_PERSIST_ERROR] %s", exc)


def _write_s3_protection_audit(symbol: str, date_str: str, line: str) -> None:
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
