"""
Structured decision audit trail — persists actionable UnifiedDecision snapshots as JSONL.

Append-only, crash-resilient, production-safe.
Never blocks execution. Never raises to caller.

Includes entry timing classification for cohort analysis (EARLY / MID / LATE).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.clock import utc_ms, utc_ms_to_iso, utc_ms_to_date, utc_ms_to_unix

from core import config
from core.entry_timing import classify_entry_timing

logger = logging.getLogger(__name__)

# ─── S3 MIRROR CONFIGURATION ─────────────────────────────────────────────────

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "decision_audit"
_SCHEMA_VERSION = "decision_audit_v1"


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror a single decision audit line to S3. Fire-and-forget.

    Pattern matches decision_ledger.py and execution_context.py.
    Never raises. Never blocks runtime.
    """
    try:
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
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line + "\n"

        # Read-append-write (acceptable for decision audit volume)
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


def _safe_serialize(obj: Any) -> Any:
    """Recursively convert an object to JSON-safe primitives."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return {k: _safe_serialize(v) for k, v in asdict(obj).items()}
        except Exception:
            return str(obj)
    if isinstance(obj, frozenset):
        return list(obj)
    # Fallback: skip non-serializable objects
    return None


def _extract_confirmation(decision: Any) -> dict[str, Any] | None:
    """
    Safely extract structured confirmation metrics from UnifiedDecision.

    Returns None if confirmation object is missing or not populated.
    Never raises — gracefully handles legacy decisions without these fields.
    """
    try:
        confirmation = getattr(decision, "confirmation", None)
        if confirmation is None:
            return None

        # Check if confirmation was actually evaluated (not just default-constructed)
        if not getattr(confirmation, "evaluated", False):
            return None

        return {
            "strength": getattr(confirmation, "strength", None) or None,
            "body_pct": getattr(confirmation, "body_pct", None),
            "wick_ratio": getattr(confirmation, "wick_ratio", None),
            "close_location": getattr(confirmation, "close_location", None),
            "reason": getattr(confirmation, "reason", None) or None,
            "passed": getattr(confirmation, "passed", None),
        }
    except Exception:
        return None


def _classify_entry_timing_from_decision(decision: Any) -> str | None:
    """
    Classify entry timing from UnifiedDecision confirmation data.

    Returns "EARLY", "MID", or "LATE" if confirmation data is available.
    Returns None if confirmation was not evaluated or data is missing.

    STRICTLY OBSERVATIONAL — never affects execution decisions.
    """
    try:
        confirmation = getattr(decision, "confirmation", None)
        if confirmation is None:
            return None

        if not getattr(confirmation, "evaluated", False):
            return None

        if not getattr(confirmation, "passed", False):
            return None  # Only classify entries that were actually confirmed

        return classify_entry_timing(
            confirmation_strength=getattr(confirmation, "strength", None),
            body_pct=getattr(confirmation, "body_pct", None),
            wick_ratio=getattr(confirmation, "wick_ratio", None),
            close_location=getattr(confirmation, "close_location", None),
        )
    except Exception:
        return None


def _build_audit_record(
    *,
    symbol: str,
    cycle_id: int,
    decision: Any,  # UnifiedDecision
    engine_state: Any,  # EngineState
    candles: list,
    closed_i: int,
    runtime_mode: str,
    risk_rejection: Any | None = None,
) -> dict[str, Any]:
    """Build a structured audit record from decision context."""
    _ts_ms = utc_ms()

    # Extract decision fields safely
    dec = decision.decision if hasattr(decision, "decision") else decision
    intent = getattr(dec, "intent", None)

    # Trigger candle OHLC
    trigger_candle: dict[str, Any] = {}
    if candles and 0 <= closed_i < len(candles):
        c = candles[closed_i]
        trigger_candle = {
            "time": getattr(c, "time", None),
            "open": getattr(c, "open", None),
            "high": getattr(c, "high", None),
            "low": getattr(c, "low", None),
            "close": getattr(c, "close", None),
        }

    record: dict[str, Any] = {
        # Metadata (canonical clock)
        "ts_utc_ms": _ts_ms,
        "timestamp_utc": utc_ms_to_iso(_ts_ms),
        "timestamp_unix": utc_ms_to_unix(_ts_ms),
        "symbol": symbol,
        "runtime_mode": runtime_mode,
        "cycle_id": cycle_id,
        "timeframe": getattr(config, "TIMEFRAME", None),

        # Decision snapshot
        "should_trade": getattr(dec, "should_trade", False),
        "reason": getattr(dec, "reason", ""),
        "side": getattr(dec, "bias", None).name if getattr(dec, "bias", None) else None,
        "score": getattr(dec, "score", 0),
        "bias_phase": getattr(dec, "bias_phase", ""),
        "bias_validation_score": getattr(dec, "bias_validation_score", 0),
        "structure_ok": getattr(dec, "structure_ok", False),
        "patterns": getattr(dec, "patterns", None),

        # Confirmation quality metrics
        "confirmation": _extract_confirmation(decision),

        # Entry timing classification (observational analytics only)
        "entry_timing": _classify_entry_timing_from_decision(decision),

        # Intent (if trade triggered)
        "intent": None,

        # EngineState snapshot
        "engine_state": {
            "current_bias": engine_state.current_bias.name if engine_state.current_bias else None,
            "bias_phase": engine_state.bias_phase,
            "bias_strength": engine_state.bias_strength,
            "bias_age_seconds": engine_state.bias_age_seconds,
            "regime_state": engine_state.regime_state,
            "volatility_filter": engine_state.volatility_filter,
            "bias_confirmation_score": engine_state.bias_confirmation_score,
            "bias_confirmation_count": engine_state.bias_confirmation_count,
            "bias_contradiction_count": engine_state.bias_contradiction_count,
        },

        # Market context
        "trigger_candle": trigger_candle,
        "spread": None,

        # Pipeline stage reached
        "last_completed_stage": getattr(decision, "last_completed_stage", ""),

        # Stability policy (observational — cohort attribution)
        "stability_policy": getattr(decision, "stability_policy", "NORMAL_MODE"),
    }

    # Populate intent if present
    if intent is not None:
        record["intent"] = {
            "symbol": getattr(intent, "symbol", symbol),
            "side": getattr(intent, "side", None).name if getattr(intent, "side", None) else None,
            "volume": getattr(intent, "volume", None),
            "sl": getattr(intent, "sl", None),
            "tp": getattr(intent, "tp", None),
            "pattern": getattr(intent, "pattern", None),
        }

    # Compute spread from bar context if available
    bar_ctx = getattr(decision, "bar_context", None)
    if bar_ctx is not None:
        bid = getattr(bar_ctx, "bid", 0.0)
        ask = getattr(bar_ctx, "ask", 0.0)
        if bid > 0 and ask > 0:
            record["spread"] = round(ask - bid, 6)

    # Risk rejection context (if provided)
    if risk_rejection is not None:
        record["risk_rejection"] = {
            "reason": getattr(risk_rejection, "reason", None),
            "pattern": getattr(risk_rejection, "pattern", None),
            "symbol": getattr(risk_rejection, "symbol", None),
            "metadata": getattr(risk_rejection, "metadata", {}),
        }

    return record


def persist_decision_audit(
    *,
    symbol: str,
    cycle_id: int,
    decision: Any,
    engine_state: Any,
    candles: list,
    closed_i: int,
    runtime_mode: str,
    risk_rejection: Any | None = None,
    entity_id: str = "",
    observation_id: str = "",
    strategy_ts_utc_ms: int = 0,
) -> str:
    """
    Persist a structured decision audit record to JSONL file.

    Safe to call from any runtime module. Never raises. Never blocks.
    Controlled by config.DECISION_AUDIT_ENABLED.

    Returns:
        decision_id (UUID hex string) — propagate to EXECUTION and OUTCOME.
        Returns "" on failure or if disabled.
    """
    import uuid as _uuid
    _decision_id = _uuid.uuid4().hex

    if not getattr(config, "DECISION_AUDIT_ENABLED", False):
        return _decision_id

    try:
        record = _build_audit_record(
            symbol=symbol,
            cycle_id=cycle_id,
            decision=decision,
            engine_state=engine_state,
            candles=candles,
            closed_i=closed_i,
            runtime_mode=runtime_mode,
            risk_rejection=risk_rejection,
        )

        # Inject causal identity + linkage fields
        record["decision_id"] = _decision_id
        if entity_id:
            record["entity_id"] = entity_id
        if observation_id:
            record["observation_id"] = observation_id
        if strategy_ts_utc_ms:
            record["strategy_ts_utc_ms"] = strategy_ts_utc_ms

        record["schema_version"] = _SCHEMA_VERSION

        # Determine output path
        audit_dir = getattr(config, "DECISION_AUDIT_DIR", "logs/decision_audit")
        date_str = utc_ms_to_date(utc_ms())
        filename = f"{symbol}_{date_str}.jsonl"
        filepath = Path(audit_dir) / filename

        # Ensure directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Local JSONL persistence (source of truth)
        line = json.dumps(record, default=str, separators=(",", ":"))
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if getattr(config, "DECISION_AUDIT_FLUSH_EVERY_WRITE", True):
                f.flush()
                os.fsync(f.fileno())

        # S3 mirror (fire-and-forget durability)
        _write_s3(symbol, date_str, line)

    except Exception as exc:
        logger.error("[DECISION_AUDIT_ERROR] symbol=%s cycle=%d error=%s", symbol, cycle_id, exc)

    return _decision_id


def persist_new_engine_decision_audit(
    *,
    symbol: str,
    cycle_id: int,
    engine_result: dict[str, Any],
    engine_state: Any,
    candles: list,
    closed_i: int,
    correlation_id: str = "",
    entity_id: str = "",
    observation_id: str = "",
    strategy_ts_utc_ms: int = 0,
    runtime_session_id: str = "",
) -> str:
    """
    Persist a structured decision audit record from the NEW ENGINE path.

    This is the authoritative audit function for the new pipeline.
    Produces the same JSONL format as persist_decision_audit() but constructs
    the record directly from run_new_engine() output (no UnifiedDecision dependency).

    Safe to call from any runtime module. Never raises. Never blocks.
    Controlled by config.DECISION_AUDIT_ENABLED.

    Args:
        symbol: Trading symbol
        cycle_id: Current cycle ID
        engine_result: Dict output from run_new_engine()
        engine_state: Current EngineState
        candles: Full candle history
        closed_i: Index of last closed bar
        correlation_id: Decision spine correlation ID
        entity_id: Entity ID for causal linkage
        observation_id: Canonical V10 opportunity identity
        strategy_ts_utc_ms: Strategy trace timestamp for linkage

    Returns:
        decision_id (UUID hex string) — propagate to EXECUTION and OUTCOME.
        Returns "" on failure or if disabled.
    """
    import uuid as _uuid
    _decision_id = _uuid.uuid4().hex

    if not getattr(config, "DECISION_AUDIT_ENABLED", False):
        return _decision_id

    try:
        _ts_ms = utc_ms()
        action = engine_result.get("action", "NO_TRADE")
        intent = engine_result.get("intent")
        assessment = engine_result.get("assessment")

        # Trigger candle OHLC
        trigger_candle: dict[str, Any] = {}
        if candles and 0 <= closed_i < len(candles):
            c = candles[closed_i]
            trigger_candle = {
                "time": getattr(c, "time", None),
                "open": getattr(c, "open", None),
                "high": getattr(c, "high", None),
                "low": getattr(c, "low", None),
                "close": getattr(c, "close", None),
            }

        record: dict[str, Any] = {
            # Metadata (canonical clock)
            "ts_utc_ms": _ts_ms,
            "timestamp_utc": utc_ms_to_iso(_ts_ms),
            "timestamp_unix": utc_ms_to_unix(_ts_ms),
            "symbol": symbol,
            "runtime_mode": "LIVE",
            "cycle_id": cycle_id,
            "timeframe": getattr(config, "TIMEFRAME", None),
            "engine_version": "V10",

            # Decision snapshot
            "should_trade": action == "EXECUTE",
            "reason": engine_result.get("reason", "") if action != "EXECUTE" else "all_gates_passed",
            "side": engine_result.get("side") or (assessment.side if assessment else None),
            "score": engine_result.get("score", 0.0),
            "score_neutral": engine_result.get("score_neutral", 0.0),
            "score_strategy": engine_result.get("score_strategy", 0.0),
            "pattern": engine_result.get("pattern"),
            "strategy": engine_result.get("strategy"),
            "strategy_confidence": engine_result.get("strategy_confidence", 0.0),
            "regime": engine_result.get("activation_regime") or (assessment.regime if assessment else "unknown"),
            "market_state": engine_result.get("market_state"),
            "market_state_confidence": engine_result.get("market_state_confidence"),

            # Policy/EV information
            "policy_trade_allowed": engine_result.get("policy_trade_allowed"),
            "policy_reasoning": engine_result.get("policy_reasoning"),
            "ev": engine_result.get("ev"),
            "ev_positive": engine_result.get("ev_positive"),
            "p_success": engine_result.get("p_success"),
            "rr_effective": engine_result.get("rr_effective"),
            "confirmation_score": engine_result.get("confirmation_score"),

            # EV experiment observability (tracks gate bypass for later analysis)
            "ev_gate_enabled": engine_result.get("ev_gate_enabled", True),
            "ev_rejection_bypassed": engine_result.get("ev_rejection_bypassed", False),
            "ev_would_have_blocked": engine_result.get("ev_would_have_blocked") is not None,
            "ev_experiment_mode": engine_result.get("ev_rejection_bypassed", False),

            # Intent (if trade triggered)
            "intent": None,

            # EngineState snapshot
            "engine_state": {
                "current_bias": engine_state.current_bias.name if getattr(engine_state, "current_bias", None) else None,
                "bias_phase": getattr(engine_state, "bias_phase", ""),
                "bias_strength": getattr(engine_state, "bias_strength", 0.0),
                "regime_state": getattr(engine_state, "regime_state", ""),
            },

            # Market context
            "trigger_candle": trigger_candle,

            # Causal linkage
            "decision_id": _decision_id,
            "correlation_id": correlation_id,
            "entity_id": entity_id,
            "observation_id": observation_id,
            "strategy_ts_utc_ms": strategy_ts_utc_ms,
            "runtime_session_id": runtime_session_id,
        }

        # Populate intent if present
        if intent is not None:
            record["intent"] = {
                "symbol": getattr(intent, "symbol", symbol),
                "side": getattr(intent, "side", None).name if getattr(intent, "side", None) else None,
                "volume": getattr(intent, "volume", None),
                "sl": getattr(intent, "sl", None),
                "tp": getattr(intent, "tp", None),
                "pattern": getattr(intent, "pattern", None),
            }

        record["schema_version"] = _SCHEMA_VERSION

        # Determine output path
        audit_dir = getattr(config, "DECISION_AUDIT_DIR", "logs/decision_audit")
        date_str = utc_ms_to_date(_ts_ms)
        filename = f"{symbol}_{date_str}.jsonl"
        filepath = Path(audit_dir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Local JSONL persistence (source of truth)
        line = json.dumps(record, default=str, separators=(",", ":"))
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if getattr(config, "DECISION_AUDIT_FLUSH_EVERY_WRITE", True):
                f.flush()
                os.fsync(f.fileno())

        # S3 mirror (fire-and-forget durability)
        _write_s3(symbol, date_str, line)

    except Exception as exc:
        logger.error("[DECISION_AUDIT_ERROR] new_engine symbol=%s cycle=%d error=%s", symbol, cycle_id, exc)

    return _decision_id


def persist_risk_rejection(
    *,
    symbol: str,
    cycle_id: int,
    guard: str,
    reason: str,
    correlation_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Persist a lightweight risk guard rejection record to decision_audit/.

    Called when a runtime guard blocks execution AFTER a valid signal was produced.
    This ensures no-trade decisions at the risk layer are forensically traceable
    without requiring a full UnifiedDecision object.

    Never raises. Never blocks. Controlled by DECISION_AUDIT_INCLUDE_REJECTIONS.
    """
    if not getattr(config, "DECISION_AUDIT_INCLUDE_REJECTIONS", False):
        return

    try:
        _ts_ms = utc_ms()
        record: dict[str, Any] = {
            "ts_utc_ms": _ts_ms,
            "timestamp_utc": utc_ms_to_iso(_ts_ms),
            "timestamp_unix": utc_ms_to_unix(_ts_ms),
            "symbol": symbol,
            "runtime_mode": "LIVE",
            "cycle_id": cycle_id,
            "should_trade": False,
            "reason": f"risk_guard:{guard}:{reason}",
            "guard": guard,
            "guard_reason": reason,
            "correlation_id": correlation_id,
            "rejection_type": "RISK_GUARD",
            "metadata": metadata or {},
        }

        record["schema_version"] = _SCHEMA_VERSION

        audit_dir = getattr(config, "DECISION_AUDIT_DIR", "logs/decision_audit")
        date_str = utc_ms_to_date(_ts_ms)
        filename = f"{symbol}_{date_str}.jsonl"
        filepath = Path(audit_dir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, default=str, separators=(",", ":"))
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if getattr(config, "DECISION_AUDIT_FLUSH_EVERY_WRITE", True):
                f.flush()
                os.fsync(f.fileno())

    except Exception as exc:
        logger.debug("[DECISION_AUDIT_RISK_REJECT] error=%s", exc)
