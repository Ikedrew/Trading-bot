"""
Unified Query Layer — Read-time normalisation over raw S3 persistence.

Transforms ALL persistence layers into a single canonical schema WITHOUT
modifying stored data. Raw S3 files stay unchanged forever.

Pipeline:
    RAW S3 → NORMALISER → UNIFIED TRADE MODEL → QUERY ENGINE → ANALYSIS

Position in system:
    OFFLINE ONLY. Never imported by live runtime.
    Consumes persistence layers read-only.

Usage:
    from data_pipeline.query_layer import QueryEngine

    engine = QueryEngine()
    dataset = engine.load_all()

    # Filter
    trades = engine.filter(dataset, type="trade")
    eurusd = engine.filter(dataset, symbol="EURUSD")

    # Analyse
    from data_pipeline.query_layer import win_rate, profit_by_symbol
    print(win_rate(trades))
    print(profit_by_symbol(trades))
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL UNIFIED TRADE SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════
# Every record from every layer is normalised into this shape.
# This is the ONLY format the query layer outputs.

UNIFIED_SCHEMA_FIELDS = {
    "correlation_id": str,   # Decision spine ID (may be None for pre-correlation data)
    "symbol": str,           # Trading pair
    "event_time_utc": float, # Unix seconds (canonical time reference)
    "source": str,           # Origin layer: events|decision_audit|trade_truth|shadow_trades|execution_context
    "type": str,             # Record type: event|decision|trade|simulation|context
    "decision": dict,        # {action, confidence, strategy_version, reason, score, patterns}
    "outcome": dict,         # {pnl, r_multiple, win, exit_reason, bars_held}
    "market": dict,          # {spread, session, bid, ask, regime}
    "execution": dict,       # {fill_price, slippage, order_type, volume}
    "context": dict,         # {state, latency_ms, feed_state, drawdown_pct}
}


# ═══════════════════════════════════════════════════════════════════════════════
# JSONL READER (works on local files — S3 variant below)
# ═══════════════════════════════════════════════════════════════════════════════

def _read_jsonl(filepath: Path) -> Iterator[dict[str, Any]]:
    """Read JSONL file, yielding parsed dicts. Skips malformed lines."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _read_all_jsonl(base_dir: Path) -> Iterator[dict[str, Any]]:
    """Read all JSONL files recursively from a directory."""
    if not base_dir.exists():
        return
    for f in sorted(base_dir.rglob("*.jsonl")):
        yield from _read_jsonl(f)


# ═══════════════════════════════════════════════════════════════════════════════
# S3 READER (for remote queries against trading-bot-data-mk1)
# ═══════════════════════════════════════════════════════════════════════════════

def _read_s3_jsonl(bucket: str, prefix: str) -> Iterator[dict[str, Any]]:
    """Read all JSONL objects under an S3 prefix. Requires boto3."""
    try:
        import boto3
        import os
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".jsonl"):
                    continue
                try:
                    response = s3.get_object(Bucket=bucket, Key=key)
                    body = response["Body"].read().decode("utf-8")
                    for line in body.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                continue
                except Exception:
                    continue
    except ImportError:
        logger.warning("[QUERY_LAYER] boto3 not available — S3 read skipped")
    except Exception as exc:
        logger.warning("[QUERY_LAYER] S3 read error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALISATION LAYER — Maps raw schemas to unified canonical format
# ═══════════════════════════════════════════════════════════════════════════════

def _pick_time(rec: dict[str, Any]) -> float:
    """
    Extract the best available timestamp from any record as Unix seconds.
    Handles: ts_utc_ms, timestamp_utc, timestamp_unix, timestamps.entry_time,
    decision_snapshot.timestamp_decision_utc, temporal.event_window_start_ts.
    """
    # Direct milliseconds (events/)
    ts_ms = rec.get("ts_utc_ms")
    if isinstance(ts_ms, (int, float)) and ts_ms > 1_000_000_000_000:
        return ts_ms / 1000.0

    # Direct unix seconds
    ts_unix = rec.get("timestamp_unix")
    if isinstance(ts_unix, (int, float)) and ts_unix > 1_000_000_000:
        return float(ts_unix)

    # ISO string
    ts_iso = rec.get("timestamp_utc")
    if isinstance(ts_iso, str) and ts_iso:
        try:
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            pass

    # Nested: timestamps.entry_time (trade_truth v2)
    timestamps = rec.get("timestamps", {})
    if isinstance(timestamps, dict):
        for key in ("entry_time", "entry_timestamp_broker", "exit_time"):
            val = timestamps.get(key)
            if isinstance(val, (int, float)) and val > 1_000_000_000:
                return float(val)

    # Nested: decision_snapshot.timestamp_decision_utc (shadow_trades v2)
    snap = rec.get("decision_snapshot", {})
    if isinstance(snap, dict):
        val = snap.get("timestamp_decision_utc")
        if isinstance(val, (int, float)) and val > 1_000_000_000:
            return float(val)

    # Nested: temporal.event_window_start_ts (trade_truth_graph)
    temporal = rec.get("temporal", {})
    if isinstance(temporal, dict):
        val = temporal.get("event_window_start_ts")
        if isinstance(val, (int, float)) and val > 1_000_000_000:
            return float(val)

    return 0.0


def _pick_correlation_id(rec: dict[str, Any]) -> str | None:
    """Extract correlation_id from any schema variant."""
    # Flat
    cor = rec.get("correlation_id")
    if isinstance(cor, str) and cor:
        return cor
    # Nested identity block (v3 schemas)
    identity = rec.get("identity", {})
    if isinstance(identity, dict):
        cor = identity.get("correlation_id")
        if isinstance(cor, str) and cor:
            return cor
    return None


def _pick_symbol(rec: dict[str, Any]) -> str:
    """Extract symbol from any schema variant."""
    sym = rec.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    identity = rec.get("identity", {})
    if isinstance(identity, dict):
        sym = identity.get("symbol")
        if isinstance(sym, str) and sym:
            return sym
    return "UNKNOWN"


def _normalise_session(hour: int) -> str:
    """Map UTC hour to trading session name."""
    if 0 <= hour < 7:
        return "ASIA"
    elif 7 <= hour < 12:
        return "LONDON"
    elif 12 <= hour < 17:
        return "NY"
    else:
        return "OFF_SESSION"


# ─── NORMALISER: events/ ──────────────────────────────────────────────────────

def normalise_event(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw event (CANDLE, FEATURE_UPDATE, etc.) to unified schema."""
    payload = rec.get("payload", {}) or {}
    event_time = _pick_time(rec)
    hour = int(datetime.fromtimestamp(event_time, tz=timezone.utc).hour) if event_time > 0 else 0

    return {
        "correlation_id": _pick_correlation_id(rec),
        "symbol": _pick_symbol(rec),
        "event_time_utc": event_time,
        "source": "events",
        "type": "event",
        "event_subtype": rec.get("type", "UNKNOWN"),
        "decision": {},
        "outcome": {},
        "market": {
            "spread": payload.get("spread"),
            "session": _normalise_session(hour),
            "bid": payload.get("bid"),
            "ask": payload.get("ask"),
            "regime": rec.get("regime", payload.get("regime", "UNKNOWN")),
        },
        "execution": {},
        "context": {},
    }


# ─── NORMALISER: execution_context/ ───────────────────────────────────────────

def normalise_execution_context(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise an execution_context record to unified schema."""
    ma = rec.get("market_access", {}) or {}
    infra = rec.get("infrastructure", {}) or {}
    risk = rec.get("risk_environment", {}) or {}

    return {
        "correlation_id": rec.get("correlation_id"),
        "symbol": rec.get("symbol", "UNKNOWN"),
        "event_time_utc": float(rec.get("timestamp_utc", 0)),
        "source": "execution_context",
        "type": "context",
        "decision": {},
        "outcome": {},
        "market": {
            "spread": ma.get("spread"),
            "session": ma.get("session_state", "UNKNOWN"),
            "bid": ma.get("bid"),
            "ask": ma.get("ask"),
            "regime": None,
        },
        "execution": {},
        "context": {
            "state": infra.get("feed_state", "UNKNOWN"),
            "latency_ms": infra.get("latency_ms", 0),
            "feed_state": infra.get("feed_state"),
            "drawdown_pct": risk.get("drawdown_pct", 0),
            "daily_loss_pct": risk.get("daily_loss_pct", 0),
            "open_positions": risk.get("open_positions", 0),
        },
    }


# ─── NORMALISER: decision_audit/ ──────────────────────────────────────────────

def normalise_decision_trace(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a decision_trace record to unified schema.

    Migrated from the retired decision_audit dataset (Production V1
    consolidation). The decision_trace record is the authoritative decision
    record and now carries the audit fields that were previously in
    decision_audit (trigger_candle, entry_timing, stability_policy, spread,
    confirmation_detail, EV experiment flags). This normaliser reads them
    directly from the trace record shape.

    Event-typed enrichment records (e.g. RISK_REJECTION) are skipped by the
    caller before this normaliser is invoked.
    """
    event_time = _pick_time(rec)

    # decision_trace carries `action` directly (EXECUTE / NO_TRADE)
    raw_action = rec.get("action", "")
    if raw_action == "EXECUTE":
        action = rec.get("side") or "BUY"
    elif raw_action == "NO_TRADE":
        action = "HOLD"
    else:
        action = raw_action or "HOLD"

    score = float(rec.get("score_strategy", rec.get("score", 0)) or 0)

    return {
        "correlation_id": rec.get("correlation_id") or rec.get("decision_id"),
        "symbol": rec.get("symbol", "UNKNOWN"),
        "event_time_utc": event_time,
        "source": "decision_trace",
        "type": "decision",
        "decision": {
            "action": action,
            "confidence": score,
            "strategy_version": rec.get("stability_policy", ""),
            "reason": rec.get("terminal_reason", rec.get("reason", "")),
            "score": score,
            "patterns": [rec.get("pattern_name")] if rec.get("pattern_name") else [],
            "bias_phase": rec.get("h1_structural_phase", ""),
            "entry_timing": rec.get("entry_timing"),
            "terminal_stage": rec.get("terminal_stage", ""),
        },
        "outcome": {},
        "market": {
            "spread": rec.get("spread_at_decision"),
            "session": None,
            "bid": None,
            "ask": None,
            "regime": rec.get("regime"),
        },
        "execution": {},
        "context": {
            "cycle_id": rec.get("cycle_id"),
            "last_stage": rec.get("terminal_stage", ""),
            "stability_policy": rec.get("stability_policy", ""),
        },
    }


# Backward-compatible alias — decision_audit dataset was consolidated into
# decision_trace. Any offline caller referencing the old name still works.
normalise_decision_audit = normalise_decision_trace


# ─── NORMALISER: trade_truth/ ─────────────────────────────────────────────────

def normalise_trade_truth(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Production V1 trade_truth record to the unified schema.

    Fresh V1 baseline: the trade_truth dataset uses a single nested-domain
    shape (identity / execution / timestamps / outcome / exit). No V2/V3
    schema-compatibility branch is retained.
    """
    identity = rec.get("identity", {})
    execution = rec.get("execution", {})
    timestamps = rec.get("timestamps", {})
    outcome = rec.get("outcome", {})
    pnl = outcome.get("pnl_realised", 0)
    return {
        "correlation_id": identity.get("correlation_id"),
        "symbol": identity.get("symbol", "UNKNOWN"),
        "event_time_utc": timestamps.get("entry_timestamp_broker", 0),
        "source": "trade_truth",
        "type": "trade",
        "decision": {},
        "outcome": {
            "pnl": pnl,
            "r_multiple": outcome.get("r_multiple_realised", 0),
            "win": pnl > 0,
            "exit_reason": rec.get("exit", {}).get("exit_reason", ""),
            "net_profit": outcome.get("net_profit", 0),
        },
        "market": {},
        "execution": {
            "fill_price": execution.get("entry_fill_price"),
            "slippage": execution.get("slippage_entry", 0),
            "order_type": execution.get("order_type", "market"),
            "volume": execution.get("volume_executed", 0),
        },
        "context": {},
    }
