"""
Edge Attribution — Pure Causal Attribution Layer.

Explains WHY an outcome happened by decomposing trade results into
contributing factors across time, context, and environment.

CONTAINS ONLY:
    1. Identity (references to trade_truth)
    2. Causal attribution breakdown (contribution scores)
    3. Aggregate explanation metrics
    4. Classification output

NEVER CONTAINS:
    - Execution data (prices, slippage, volume, pnl, r_multiple)
    - Decision data (strategy, pattern, score, indicators, HTF bias)
    - Simulation data (shadow trade internals, lifecycle)
    - Raw market data (OHLCV, candles, feature snapshots)

READS FROM (read-only):
    - trade_truth/ (outcome reference only — the label being explained)
    - trade_truth_graph/ (relationship context)

DESIGN INTENT:
    Answers ONLY: Why did this trade happen? What drove the outcome?
    Which conditions mattered most? What was noise vs signal?

S3: s3://trading-bot-data-mk1/edge_attribution/{symbol}/{YYYY-MM-DD}.jsonl
Local: logs/edge_attribution/{symbol}/{YYYY-MM-DD}.jsonl

Usage:
    from core.edge_attribution import build_attribution, persist_attribution

    attr = build_attribution(
        trade_id="T-1001",
        correlation_id="COR-XYZ",
        symbol="EURUSD",
        outcome_r=2.0,
        structure_strength=0.42,
        session_effect=0.18,
        ...
    )
    persist_attribution(attr)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "edge_attribution"
_LOCAL_DIR = "logs/edge_attribution"
_SCHEMA_VERSION = "edge_attribution_v2"


# ═══════════════════════════════════════════════════════════════════════════════
# FORBIDDEN FIELDS (reject at write time)
# ═══════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_FIELDS = frozenset({
    # Execution data
    "entry_price", "exit_price", "entry_fill_price", "exit_fill_price",
    "slippage", "slippage_entry", "slippage_exit",
    "spread", "spread_at_entry", "spread_at_exit",
    "volume", "volume_executed", "lot_size", "position_size",
    "pnl", "pnl_realised", "pnl_price", "net_profit",
    "r_multiple", "r_multiple_realised", "pnl_r_multiple",
    "commission", "swap",
    # Decision data
    "strategy_id", "strategy", "pattern", "confluence_score", "score",
    "decision_context", "decision_snapshot",
    "htf_context", "htf_snapshot", "H4_bias", "H1_bias", "M15_bias",
    "alignment_score", "bias", "indicators",
    # Simulation data
    "simulated_outcome", "trade_state_progression", "state_progression",
    "shadow_trade_ref", "simulation_environment",
    # Raw market data
    "candles", "ohlcv", "atr", "rsi", "feature_state",
    # Legacy
    "legacy", "final_r", "derived_metrics", "risk_model",
})


# ═══════════════════════════════════════════════════════════════════════════════
# ATTRIBUTION BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_attribution(
    *,
    # Identity (references only)
    trade_id: str,
    correlation_id: str,
    symbol: str,
    timestamp_reference: float = 0.0,  # Exit time — referenced, not recomputed
    # Causal attribution breakdown
    structure_strength: float = 0.0,
    breakout_quality: float = 0.0,
    trend_alignment_score: float = 0.0,
    session_effect: float = 0.0,
    liquidity_window_strength: float = 0.0,
    regime_fit_score: float = 0.0,
    regime_stability_impact: float = 0.0,
    volatility_regime_score: float = 0.0,
    volatility_misfit_penalty: float = 0.0,
    event_alignment_score: float = 0.0,
    pre_event_drift_effect: float = 0.0,
    # Outcome reference (label being explained — NOT execution data copy)
    outcome_r: float = 0.0,
    # Optional interaction structure
    feature_interactions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Build a causal attribution record.

    All inputs are DERIVED EXPLANATION SCORES, not raw execution data.
    outcome_r is the label being explained (scalar reference to trade_truth),
    NOT a duplication of execution data.
    """
    # Generate deterministic attribution ID
    raw_id = f"{correlation_id}:{trade_id}:{symbol}:{timestamp_reference}"
    attribution_id = f"ATT-{hashlib.sha256(raw_id.encode()).hexdigest()[:12].upper()}"

    # Compute aggregate metrics
    components = [
        structure_strength,
        breakout_quality,
        trend_alignment_score,
        session_effect,
        liquidity_window_strength,
        regime_fit_score,
        regime_stability_impact,
        volatility_regime_score,
        volatility_misfit_penalty,
        event_alignment_score,
        pre_event_drift_effect,
    ]
    net_score = round(sum(components), 4)
    residual = round(outcome_r - net_score, 4) if outcome_r != 0 else 0.0

    # Attribution confidence: how well do components explain the outcome
    if outcome_r != 0:
        explanation_ratio = abs(net_score / outcome_r) if outcome_r != 0 else 0
        confidence = round(min(1.0, explanation_ratio), 4)
    else:
        confidence = 0.0

    # Classification
    if confidence >= 0.7 and abs(residual) < abs(outcome_r) * 0.3:
        quality = "HIGH"
        stability = "STABLE"
        consistency = "TRUE"
    elif confidence >= 0.4:
        quality = "MEDIUM"
        stability = "STABLE" if abs(residual) < abs(outcome_r) * 0.5 else "UNSTABLE"
        consistency = "PARTIAL"
    else:
        quality = "LOW"
        stability = "UNSTABLE"
        consistency = "FALSE"

    record: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,

        # Domain 1: Identity (reference only)
        "attribution_id": attribution_id,
        "trade_id": trade_id,
        "correlation_id": correlation_id,
        "symbol": symbol,
        "timestamp_reference": timestamp_reference,

        # Domain 2: Causal attribution breakdown
        "attribution": {
            "structure_strength": round(structure_strength, 4),
            "breakout_quality": round(breakout_quality, 4),
            "trend_alignment_score": round(trend_alignment_score, 4),
            "session_effect": round(session_effect, 4),
            "liquidity_window_strength": round(liquidity_window_strength, 4),
            "regime_fit_score": round(regime_fit_score, 4),
            "regime_stability_impact": round(regime_stability_impact, 4),
            "volatility_regime_score": round(volatility_regime_score, 4),
            "volatility_misfit_penalty": round(volatility_misfit_penalty, 4),
            "event_alignment_score": round(event_alignment_score, 4),
            "pre_event_drift_effect": round(pre_event_drift_effect, 4),
        },

        # Domain 3: Aggregate explanation metrics
        "aggregate": {
            "net_attribution_score": net_score,
            "residual_unexplained_variance": residual,
            "attribution_confidence": confidence,
            "model_explanation_strength": round(1.0 - abs(residual / outcome_r), 4) if outcome_r != 0 else 0.0,
        },

        # Domain 4: Classification output
        "classification": {
            "attribution_quality": quality,
            "attribution_stability": stability,
            "causal_consistency": consistency,
        },
    }

    # Optional interaction structure (derived scores only)
    if feature_interactions:
        record["interactions"] = {
            k: round(v, 4) for k, v in feature_interactions.items()
        }

    return record


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_attribution(record: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate an attribution record before persistence.

    Rejects if:
        - Any forbidden field present (prices, pnl, strategy, etc.)
        - Identity fields missing
        - Attribution section missing
    """
    if record.get("schema_version") != _SCHEMA_VERSION:
        return False, f"invalid_schema_version: expected {_SCHEMA_VERSION}"

    # Required identity
    if not record.get("trade_id"):
        return False, "missing_trade_id"
    if not record.get("correlation_id"):
        return False, "missing_correlation_id"
    if not record.get("symbol"):
        return False, "missing_symbol"

    # Required sections
    if "attribution" not in record or not isinstance(record["attribution"], dict):
        return False, "missing_attribution_section"
    if "aggregate" not in record or not isinstance(record["aggregate"], dict):
        return False, "missing_aggregate_section"
    if "classification" not in record or not isinstance(record["classification"], dict):
        return False, "missing_classification_section"

    # Forbidden field scan
    forbidden = _scan_forbidden(record)
    if forbidden:
        return False, forbidden

    return True, "valid"


def _scan_forbidden(d: dict[str, Any], path: str = "") -> str | None:
    """Recursively scan for forbidden fields."""
    for k, v in d.items():
        if k in _FORBIDDEN_FIELDS:
            return f"forbidden_field:{path}{k}"
        if isinstance(v, dict):
            result = _scan_forbidden(v, f"{path}{k}.")
            if result:
                return result
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (local + S3)
# ═══════════════════════════════════════════════════════════════════════════════

def persist_attribution(record: dict[str, Any]) -> bool:
    """
    Persist a validated attribution record.

    Validates before write. Append-only. Immutable.
    """
    valid, reason = validate_attribution(record)
    if not valid:
        logger.warning("[EDGE_ATTRIBUTION] rejected: %s", reason)
        try:
            from core.contracts.quarantine import QuarantineStore
            from core.contracts.violation import ContractViolation
            from core.contracts.severity import Severity
            _qs = QuarantineStore()
            _qs.quarantine(
                record_id=record.get("attribution_id", "unknown"),
                layer="edge_attribution",
                violations=[ContractViolation(
                    contract_name="edge_attribution_schema",
                    contract_version="v2",
                    severity=Severity.MEDIUM,
                    reason=reason,
                )],
                original_payload=record,
            )
        except Exception:
            pass
        return False

    try:
        symbol = record.get("symbol", "UNKNOWN")
        ts = record.get("timestamp_reference", 0)
        if isinstance(ts, (int, float)) and ts > 0:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_append(symbol, date_str, line)
        except Exception:
            pass

        return True
    except Exception as exc:
        logger.debug("[EDGE_ATTRIBUTION] persist_failed: %s", exc)
        return False


def _s3_append(symbol: str, date_str: str, line: str) -> None:
    """Append to S3 edge attribution namespace."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        key = f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}/symbol={symbol}/date={date_str}/part-000.jsonl"
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + line
        except Exception:
            body = line
        s3.put_object(Bucket=_S3_BUCKET, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# READER
# ═══════════════════════════════════════════════════════════════════════════════

def load_attributions(
    *,
    symbol: str | None = None,
    local_dir: str = _LOCAL_DIR,
) -> list[dict[str, Any]]:
    """Load attribution records from local JSONL. Read-only."""
    records: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return records

    for f in sorted(path.rglob("*.jsonl")):
        if symbol and symbol not in str(f):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("schema_version") == _SCHEMA_VERSION:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue

    return records
