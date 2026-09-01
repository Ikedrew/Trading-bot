"""
Trade Truth Graph — Pure Relationship Graph Layer.

A minimal, reference-only graph that expresses relationships between
already-materialised truth layers. It does NOT store data.

CONTAINS ONLY:
    1. Node identity (references to other layers)
    2. Edge relationships (temporal, causal, correlation, derivation)
    3. Linking references (S3/local paths to source layers)
    4. Causal relationship tags (labels, NOT numeric values)

NEVER CONTAINS:
    - Execution data (prices, slippage, volume, pnl, r_multiple)
    - Decision data (strategy, pattern, score, indicators, HTF bias)
    - Simulation data (shadow trade internals, lifecycle progression)
    - Raw market data (OHLCV, candles, features)

DESIGN INTENT:
    Answers ONLY: What influenced what? What came before what?
    What is connected to what? What causal chain exists?

    NOT for: performance analysis, profitability, strategy evaluation,
    or trade reconstruction.

S3: s3://trading-bot-data-mk1/trade_truth_graph/symbol={SYMBOL}/date={YYYY-MM-DD}/
Local: logs/trade_truth_graph/{SYMBOL}/{YYYY-MM-DD}.jsonl

Usage:
    from core.trade_truth_graph import build_graph_node, persist_graph_node

    node = build_graph_node(
        trade_id="T-1001",
        correlation_id="COR-20260704-100-EURUSD-A93F",
        symbol="EURUSD",
        cycle_id=1001,
        ...
    )
    persist_graph_node(node)
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

from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("trade_truth_graph")
_LOCAL_DIR = "logs/trade_truth_graph"
from core.production_data_contract import current_schema

_SCHEMA_VERSION = current_schema("trade_truth_graph")


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE TYPES (strict enum)
# ═══════════════════════════════════════════════════════════════════════════════

VALID_EDGE_TYPES = frozenset({
    "TEMPORAL",       # Time-ordered relationship (A happened before B)
    "CAUSAL",         # A caused B to exist
    "CORRELATION",    # A and B are related but not causally
    "DERIVATION",     # B was derived from A
})

VALID_RELATIONSHIP_TAGS = frozenset({
    # Session
    "SAME_SESSION", "CROSS_SESSION",
    # Regime
    "TREND_SHIFT", "CONTINUATION",
    # Pattern
    "CONFIRMED", "FAILED", "NONE",
    # Symbol
    "SAME_SYMBOL",
})


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
    "mfe_r", "mae_r", "exit_efficiency",
    # Decision data
    "strategy_id", "strategy", "pattern", "confluence_score", "score",
    "decision_context", "htf_context", "htf_snapshot",
    "H4_bias", "H1_bias", "M15_bias", "alignment_score",
    "bias", "regime", "indicators",
    # Simulation data
    "simulated_outcome", "trade_state_progression", "state_progression",
    "bars_held", "exit_reason",
    # Raw market data
    "candles", "ohlcv", "atr", "rsi",
    # Legacy
    "legacy", "final_r", "derived_metrics", "risk_model",
})


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH NODE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_graph_node(
    *,
    # Node identity
    trade_id: str,
    correlation_id: str,
    symbol: str,
    cycle_id: int | str,
    # Temporal relationships
    event_window_start_ts: float = 0.0,
    event_window_end_ts: float = 0.0,
    decision_to_execution_lag_ms: float = 0.0,
    execution_to_exit_lag_ms: float = 0.0,
    # Linking references (paths/pointers to source layers)
    shadow_trade_ref: str = "",
    trade_truth_ref: str = "",
    execution_context_ref: str = "",
    events_window_ref: str = "",
    # Causal relationship tags
    session_relationship: str = "SAME_SESSION",
    regime_relationship: str = "NONE",
    pattern_relationship: str = "NONE",
) -> dict[str, Any]:
    """
    Build a pure relationship graph node.

    Contains ONLY references, edges, and temporal metadata.
    NO execution data, NO decision data, NO market data.
    """
    # Generate deterministic node ID
    raw_id = f"{correlation_id}:{trade_id}:{symbol}:{cycle_id}"
    graph_node_id = f"GN-{hashlib.sha256(raw_id.encode()).hexdigest()[:12].upper()}"

    # Build edges (structural relationships between layers)
    edges: list[dict[str, Any]] = []

    if events_window_ref:
        edges.append({
            "from": "event_window",
            "to": "execution_context",
            "edge_type": "TEMPORAL",
            "weight": None,
            "confidence": None,
        })

    if execution_context_ref:
        edges.append({
            "from": "execution_context",
            "to": "shadow_trade",
            "edge_type": "CAUSAL",
            "weight": None,
            "confidence": None,
        })

    if shadow_trade_ref:
        edges.append({
            "from": "shadow_trade",
            "to": "trade_truth",
            "edge_type": "DERIVATION",
            "weight": None,
            "confidence": None,
        })

    if events_window_ref and trade_truth_ref:
        edges.append({
            "from": "event_window",
            "to": "trade_truth",
            "edge_type": "CORRELATION",
            "weight": None,
            "confidence": None,
        })

    # Compute reference hash for integrity
    ref_str = f"{shadow_trade_ref}|{trade_truth_ref}|{execution_context_ref}|{events_window_ref}"
    hash_of_refs = hashlib.md5(ref_str.encode()).hexdigest()[:8]

    return {
        "schema_version": _SCHEMA_VERSION,

        # Node identity (references only)
        "graph_node_id": graph_node_id,
        "trade_id": trade_id,
        "correlation_id": correlation_id,
        "symbol": symbol,
        "cycle_id": str(cycle_id),

        # Temporal relationships
        "temporal": {
            "event_window_start_ts": event_window_start_ts,
            "event_window_end_ts": event_window_end_ts,
            "decision_to_execution_lag_ms": round(decision_to_execution_lag_ms, 1),
            "execution_to_exit_lag_ms": round(execution_to_exit_lag_ms, 1),
        },

        # Linking references (paths to source layers)
        "refs": {
            "events": events_window_ref,
            "execution_context": execution_context_ref,
            "shadow_trade": shadow_trade_ref,
            "trade_truth": trade_truth_ref,
        },

        # Causal relationship tags (labels only — NOT numeric values)
        "relationships": {
            "session": session_relationship,
            "regime": regime_relationship,
            "pattern": pattern_relationship,
            "symbol": "SAME_SYMBOL",
        },

        # Graph edges (structural only)
        "edges": edges,

        # Metadata
        "metadata": {
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_system": "graph_builder",
            "hash_of_refs": hash_of_refs,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_graph_node(node: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a graph node before persistence.

    Rejects if:
        - Any forbidden field is present (prices, pnl, strategy, etc.)
        - Identity fields missing
        - Schema version wrong
        - Edge types invalid
    """
    if node.get("schema_version") != _SCHEMA_VERSION:
        return False, f"invalid_schema_version: expected {_SCHEMA_VERSION}"

    # Identity required
    if not node.get("trade_id"):
        return False, "missing_trade_id"
    if not node.get("correlation_id"):
        return False, "missing_correlation_id"
    if not node.get("symbol"):
        return False, "missing_symbol"

    # Validate edge types
    for edge in node.get("edges", []):
        etype = edge.get("edge_type", "")
        if etype not in VALID_EDGE_TYPES:
            return False, f"invalid_edge_type:{etype}"
        # weight and confidence MUST be None (no numeric outcomes)
        if edge.get("weight") is not None:
            return False, "edge_weight_must_be_null"
        if edge.get("confidence") is not None:
            return False, "edge_confidence_must_be_null"

    # Forbidden field scan (deep)
    forbidden = _scan_forbidden(node)
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

def persist_graph_node(node: dict[str, Any]) -> bool:
    """
    Persist a validated graph node.

    Validates before write. Append-only. Immutable.
    """
    valid, reason = validate_graph_node(node)
    if not valid:
        logger.warning("[TRADE_TRUTH_GRAPH] rejected: %s", reason)
        try:
            from core.contracts.quarantine import QuarantineStore
            from core.contracts.violation import ContractViolation
            from core.contracts.severity import Severity
            _qs = QuarantineStore()
            _qs.quarantine(
                record_id=node.get("trade_id", "unknown"),
                layer="trade_truth_graph",
                violations=[ContractViolation(
                    contract_name="trade_truth_graph_schema",
                    contract_version="v2",
                    severity=Severity.MEDIUM,
                    reason=reason,
                )],
                original_payload=node,
            )
        except Exception:
            pass
        return False

    try:
        symbol = node.get("symbol", "UNKNOWN")
        # Use event window end or current time for date partitioning
        ts = node.get("temporal", {}).get("event_window_end_ts", 0)
        if ts > 0:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        line = json.dumps(node, separators=(",", ":"), default=str) + "\n"

        # Local write (primary)
        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (Hive-partitioned)
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_write(symbol, date_str, line)
        except Exception:
            pass

        return True
    except Exception as exc:
        logger.debug("[TRADE_TRUTH_GRAPH] persist_failed: %s", exc)
        return False


def _s3_write(symbol: str, date_str: str, line: str) -> None:
    """Append to Hive-partitioned S3 path."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + line
        except Exception:
            body = line
        s3.put_object(Bucket=_S3_BUCKET, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# READER (for analytics / downstream)
# ═══════════════════════════════════════════════════════════════════════════════

def load_graph_local(local_dir: str = _LOCAL_DIR) -> list[dict[str, Any]]:
    """
    Load all graph nodes from local JSONL files.

    Returns only v2 schema nodes (pure relationship graph).
    Legacy nodes (with embedded data) are skipped.
    """
    nodes: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return nodes
    for f in sorted(path.rglob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        node = json.loads(line)
                        # Only load v2 graph nodes (skip legacy denormalized records)
                        if node.get("schema_version") == _SCHEMA_VERSION:
                            nodes.append(node)
                    except json.JSONDecodeError:
                        continue
    return nodes


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH BUILDER (from completed trade lifecycle)
# ═══════════════════════════════════════════════════════════════════════════════

def build_graph_from_shadow_trade(
    shadow_record: dict[str, Any],
    *,
    trade_truth_ref: str = "",
    execution_context_ref: str = "",
    shadow_trade_ref: str = "",
) -> dict[str, Any]:
    """
    Build a graph node from a completed shadow trade (STR format).

    Extracts ONLY identity + temporal data. Never copies execution/outcome.
    """
    identity = shadow_record.get("identity", {})
    decision = shadow_record.get("decision_snapshot", {})
    sim_env = shadow_record.get("simulation_environment", {})
    sim_outcome = shadow_record.get("simulated_outcome", {})

    correlation_id = identity.get("correlation_id", "")
    symbol = identity.get("symbol", "")

    # Compute temporal metadata from available timestamps
    decision_ts = decision.get("timestamp_decision_utc", 0)
    exit_ts = sim_outcome.get("exit_timestamp", 0)
    decision_to_exit_ms = (exit_ts - decision_ts) * 1000 if (decision_ts > 0 and exit_ts > 0) else 0

    # Build S3 references
    if not shadow_trade_ref and symbol and decision_ts > 0:
        date = datetime.fromtimestamp(decision_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        shadow_trade_ref = (
            f"s3://{_S3_BUCKET}/{s3_base_prefix('shadow_trades')}/"
            f"schema_version={current_schema('shadow_trades')}/symbol={symbol}/"
            f"date={date}/part-000.jsonl"
        )

    if not execution_context_ref and correlation_id:
        execution_context_ref = correlation_id  # Joinable via correlation_id

    events_ref = ""
    if decision_ts > 0:
        date = datetime.fromtimestamp(decision_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        events_ref = f"s3://{_S3_BUCKET}/{s3_base_prefix('events')}/symbol={symbol}/date={date}/"

    return build_graph_node(
        trade_id=identity.get("trade_id", ""),
        correlation_id=correlation_id,
        symbol=symbol,
        cycle_id=identity.get("cycle_id", ""),
        event_window_start_ts=decision_ts - 300 if decision_ts > 0 else 0,  # 5min before decision
        event_window_end_ts=exit_ts,
        decision_to_execution_lag_ms=0,  # Shadow trades have no execution lag
        execution_to_exit_lag_ms=decision_to_exit_ms,
        shadow_trade_ref=shadow_trade_ref,
        trade_truth_ref=trade_truth_ref,
        execution_context_ref=execution_context_ref,
        events_window_ref=events_ref,
    )
