"""
Trade Truth — Pure Execution Reality Layer.

Stores an immutable record of what ACTUALLY happened when a trade was executed
in a live or simulated broker environment.

CONTAINS ONLY:
    1. IDENTITY — trade_id, correlation_id, symbol
    2. EXECUTION — fills, volume, slippage, spread (REAL broker data)
    3. TIMESTAMPS — broker entry/exit times (REAL wall-clock)
    4. OUTCOME — realised PnL, R-multiple, commission, swap
    5. EXIT — classification of how the trade ended

NEVER CONTAINS:
    - strategy_id, pattern, confluence_score
    - decision_context, HTF bias, indicator snapshots
    - execution_context references, shadow_trades references
    - simulated values, predicted outcomes
    - pre-trade intent fields (stop_loss_intent, take_profit_intent, entry_intent)

RULES:
    - Append-only, immutable after write
    - No recalculation, enrichment, or backfilling
    - Reject any record with forbidden fields

S3: s3://trading-bot-data-mk1/trades/{symbol}/{YYYY-MM-DD}.jsonl
Local: logs/trade_truth/{symbol}/{YYYY-MM-DD}.jsonl

Usage:
    from core.trade_truth import build_trade_truth, persist_trade_truth, validate_trade_truth

    record = build_trade_truth(
        trade_id="12345",
        correlation_id="COR-20260704-100-EURUSD-A93F",
        symbol="EURUSD",
        entry_fill_price=1.10005,
        exit_fill_price=1.10205,
        ...
    )
    persist_trade_truth(record)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "trading-bot-data-mk1"
_S3_TRADES_PREFIX = "trades"
_SCHEMA_VERSION = "trade_truth_v3"

# ═══════════════════════════════════════════════════════════════════════════════
# FORBIDDEN FIELDS (reject at write time if present)
# ═══════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_FIELDS = frozenset({
    # Decision / strategy
    "strategy_id", "strategy", "pattern", "confluence_score", "score",
    "decision_context", "should_trade",
    # Market analysis
    "htf_context", "htf_snapshot", "H4_bias", "H1_bias", "M15_bias",
    "alignment_score", "regime", "bias",
    # Intent (pre-trade)
    "stop_loss_intent", "take_profit_intent", "entry_intent_price",
    "stop_loss", "take_profit",  # These are intent — fills are in execution section
    # Simulation
    "simulated_outcome", "simulation_environment", "shadow_trade_ref",
    # Legacy
    "legacy", "final_r", "derived_metrics", "risk_model",
    # Cross-layer references (not allowed)
    "execution_context_ref", "events_ref", "feature_snapshot_ref",
})

# Allowed exit reasons (strict enum)
_VALID_EXIT_REASONS = frozenset({
    "stop_loss_hit",
    "take_profit_hit",
    "manual_close",
    "margin_call",
    "system_close",
})


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_trade_truth(
    *,
    # Identity
    trade_id: str,
    correlation_id: str,
    symbol: str,
    # Execution (real broker data)
    entry_fill_price: float,
    exit_fill_price: float,
    volume_executed: float,
    order_type: str = "market",
    slippage_entry: float = 0.0,
    slippage_exit: float = 0.0,
    spread_at_entry: float = 0.0,
    spread_at_exit: float = 0.0,
    # Timestamps (real broker times)
    entry_timestamp_broker: float = 0.0,
    exit_timestamp_broker: float = 0.0,
    # Outcome (realised)
    pnl_realised: float = 0.0,
    r_multiple_realised: float = 0.0,
    commission: float = 0.0,
    swap: float = 0.0,
    net_profit: float = 0.0,
    # Exit classification
    exit_reason: str = "system_close",
) -> dict[str, Any]:
    """
    Build a Pure Execution Reality record.

    Every field MUST come from real broker execution data.
    No simulation, no prediction, no intent.
    """
    duration = exit_timestamp_broker - entry_timestamp_broker if (
        entry_timestamp_broker > 0 and exit_timestamp_broker > 0
    ) else 0.0

    return {
        "schema_version": _SCHEMA_VERSION,

        # Domain 1: Identity
        "identity": {
            "trade_id": trade_id,
            "correlation_id": correlation_id,
            "symbol": symbol,
        },

        # Domain 2: Execution (real only)
        "execution": {
            "entry_fill_price": round(entry_fill_price, 8),
            "exit_fill_price": round(exit_fill_price, 8),
            "volume_executed": round(volume_executed, 4),
            "order_type": order_type,
            "slippage_entry": round(slippage_entry, 8),
            "slippage_exit": round(slippage_exit, 8),
            "spread_at_entry": round(spread_at_entry, 8),
            "spread_at_exit": round(spread_at_exit, 8),
        },

        # Domain 3: Timestamps (real world only)
        "timestamps": {
            "entry_timestamp_broker": entry_timestamp_broker,
            "exit_timestamp_broker": exit_timestamp_broker,
            "duration_seconds": round(duration, 1),
        },

        # Domain 4: Outcome (realised only)
        "outcome": {
            "pnl_realised": round(pnl_realised, 4),
            "r_multiple_realised": round(r_multiple_realised, 4),
            "commission": round(commission, 4),
            "swap": round(swap, 4),
            "net_profit": round(net_profit, 4),
        },

        # Domain 5: Exit classification (observed only)
        "exit": {
            "exit_reason": exit_reason,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION (enforced at write time)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_trade_truth(record: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a trade_truth record BEFORE persistence.

    Rejects if:
        - Any forbidden field is present (strategy, intent, simulation)
        - Required identity fields missing
        - Fill prices missing
        - Timestamps missing
        - Exit reason invalid

    Returns (valid, reason).
    """
    # Schema version check
    if record.get("schema_version") != _SCHEMA_VERSION:
        return False, f"invalid_schema_version: expected {_SCHEMA_VERSION}"

    # Required sections
    for section in ("identity", "execution", "timestamps", "outcome", "exit"):
        if section not in record or not isinstance(record[section], dict):
            return False, f"missing_section:{section}"

    # Identity fields
    identity = record["identity"]
    if not identity.get("trade_id"):
        return False, "missing_trade_id"
    if not identity.get("correlation_id"):
        return False, "missing_correlation_id"
    if not identity.get("symbol"):
        return False, "missing_symbol"

    # Execution fill prices
    execution = record["execution"]
    if not execution.get("entry_fill_price") or execution["entry_fill_price"] <= 0:
        return False, "missing_entry_fill_price"
    if not execution.get("exit_fill_price") or execution["exit_fill_price"] <= 0:
        return False, "missing_exit_fill_price"

    # Timestamps
    timestamps = record["timestamps"]
    if not timestamps.get("entry_timestamp_broker") or timestamps["entry_timestamp_broker"] <= 0:
        return False, "missing_entry_timestamp"
    if not timestamps.get("exit_timestamp_broker") or timestamps["exit_timestamp_broker"] <= 0:
        return False, "missing_exit_timestamp"

    # Exit reason validation
    exit_section = record["exit"]
    exit_reason = exit_section.get("exit_reason", "")
    if exit_reason not in _VALID_EXIT_REASONS:
        return False, f"invalid_exit_reason:{exit_reason}"

    # Forbidden field scan (deep recursive)
    forbidden = _scan_forbidden(record)
    if forbidden:
        return False, forbidden

    return True, "valid"


def _scan_forbidden(d: dict[str, Any], path: str = "") -> str | None:
    """Recursively scan for forbidden fields. Returns first violation or None."""
    for k, v in d.items():
        if k in _FORBIDDEN_FIELDS:
            return f"forbidden_field:{path}{k}"
        if isinstance(v, dict):
            result = _scan_forbidden(v, f"{path}{k}.")
            if result:
                return result
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# R-MULTIPLE COMPUTATION (pure price-space)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_r_multiple(
    *,
    direction: str,
    entry_price: float,
    exit_price: float,
    stop_loss: float,
) -> float:
    """
    Compute canonical R-multiple in price space.

    This is a UTILITY for callers constructing trade_truth records.
    The result goes into outcome.r_multiple_realised.

    Formula:
        risk = abs(entry_price - stop_loss)
        pnl = exit - entry (BUY) or entry - exit (SELL)
        R = pnl / risk
    """
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return 0.0
    if direction.upper() == "BUY":
        pnl = exit_price - entry_price
    else:
        pnl = entry_price - exit_price
    return round(pnl / risk, 4)


def compute_mfe_r(
    *,
    direction: str,
    entry_price: float,
    max_favourable_price: float,
    stop_loss: float,
) -> float:
    """MFE in R-multiples."""
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return 0.0
    if direction.upper() == "BUY":
        mfe = max_favourable_price - entry_price
    else:
        mfe = entry_price - max_favourable_price
    return round(max(0.0, mfe) / risk, 4)


def compute_mae_r(
    *,
    direction: str,
    entry_price: float,
    max_adverse_price: float,
    stop_loss: float,
) -> float:
    """MAE in R-multiples."""
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return 0.0
    if direction.upper() == "BUY":
        mae = entry_price - max_adverse_price
    else:
        mae = max_adverse_price - entry_price
    return round(max(0.0, mae) / risk, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (local + S3)
# ═══════════════════════════════════════════════════════════════════════════════

def persist_trade_truth(record: dict[str, Any], *, local_dir: str = "logs/trade_truth") -> bool:
    """
    Persist a validated trade_truth record.

    Validates BEFORE write. Rejects invalid records.
    Append-only. Immutable. Never overwrites.

    Returns True on success, False on rejection or failure.
    """
    # Validate before write
    valid, reason = validate_trade_truth(record)
    if not valid:
        logger.warning("[TRADE_TRUTH] rejected: %s", reason)
        return False

    try:
        identity = record["identity"]
        symbol = identity["symbol"]
        exit_ts = record["timestamps"]["exit_timestamp_broker"]
        date_str = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        # Local write (primary truth)
        local_path = Path(local_dir) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"

        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        logger.info(
            "[TRADE_TRUTH] persisted trade_id=%s symbol=%s r=%.4f net=%.4f",
            identity["trade_id"], symbol,
            record["outcome"]["r_multiple_realised"],
            record["outcome"]["net_profit"],
        )

        # S3 mirror (fire-and-forget) — only when writing to production path
        try:
            from core import config as _cfg
            _production_dir = Path("logs/trade_truth").resolve()
            _actual_dir = Path(local_dir).resolve()
            _is_production_path = (_actual_dir == _production_dir)
            if _is_production_path and getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_persist(symbol, date_str, line)
        except Exception:
            pass

        return True

    except Exception as exc:
        logger.error("[TRADE_TRUTH] persist_failed: %s", exc)
        return False


def _s3_persist(symbol: str, date_str: str, line: str) -> None:
    """Fire-and-forget S3 write."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        key = f"{_S3_TRADES_PREFIX}/schema_version=trade_truth_v3/symbol={symbol}/date={date_str}/part-000.jsonl"
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + line
        except Exception:
            body = line
        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# READER (for downstream layers)
# ═══════════════════════════════════════════════════════════════════════════════

def load_trade_truth(
    *,
    symbol: str | None = None,
    local_dir: str = "logs/trade_truth",
) -> list[dict[str, Any]]:
    """Load trade_truth records from local JSONL. Read-only."""
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
                    # Only load v3 records (skip legacy test data)
                    if rec.get("schema_version") == _SCHEMA_VERSION:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue

    return records
