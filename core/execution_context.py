"""
Execution Context — Frozen environment snapshot at decision time.

Captures the EXACT system conditions under which a trading decision was made.
This is a deterministic snapshot generator, not a strategy or analytics system.

PURPOSE:
    "What were the conditions when this decision happened?"

CONTAINS ONLY:
    - Market access state (session, spread, bid/ask)
    - Infrastructure state (latency, feed health, data gaps)
    - Risk environment (drawdown, exposure, loss state)
    - Event stream references (pointer to last observed candle/feature)

NEVER CONTAINS:
    - Trade outcomes (pnl, r_multiple)
    - Execution results (fills, slippage)
    - Strategy logic (score, confluence, rules)
    - Post-decision information of any kind

STORAGE:
    S3:    s3://trading-bot-data-mk1/execution_context/symbol={SYMBOL}/date={YYYY-MM-DD}/
    Local: logs/execution_context/{SYMBOL}/{YYYY-MM-DD}.jsonl

WRITING RULES:
    - Append-only (no updates, no overwrites)
    - One record per decision event
    - Written BEFORE any trade execution
    - Immutable once stored
    - Timestamp aligned with decision time (not execution time)

Usage:
    from core.execution_context import build_execution_context, persist_execution_context

    ctx = build_execution_context(
        correlation_id=cor_id,
        symbol="EURUSD",
        timestamp_utc=closed_time,
        bid=bid, ask=ask,
        session_state="LONDON",
        latency_ms=45,
        ...
    )
    persist_execution_context(ctx)
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

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "execution_context"
_LOCAL_DIR = "logs/execution_context"
_SCHEMA_VERSION = "execution_context_v1"

# ═══════════════════════════════════════════════════════════════════════════════
# FORBIDDEN FIELDS (hard rejection if present)
# ═══════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_FIELDS = frozenset({
    "entry_price", "exit_price", "fill_price",
    "pnl", "pnl_price", "pnl_cash", "r_multiple", "final_r",
    "trade_id", "position_id", "order_id", "order_ticket", "deal",
    "slippage", "slippage_entry", "slippage_exit",
    "confluence_score", "score", "should_trade",
    "strategy", "strategy_id", "pattern",
    "exit_reason", "bars_held", "mfe_r", "mae_r",
})


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketAccess:
    """Market access conditions at decision time."""
    session_state: str      # ASIA | LONDON | NY | OFF_SESSION | UNKNOWN
    spread: float           # ask - bid (raw)
    spread_atr_ratio: float # spread / atr (execution cost relative to volatility)
    bid: float              # Current bid price
    ask: float              # Current ask price


@dataclass(frozen=True)
class Infrastructure:
    """Infrastructure health at decision time."""
    latency_ms: int         # MT5 API response time for last bar fetch
    feed_state: str         # HEALTHY | DEGRADED | DISCONNECTED
    tick_age_ms: int        # Milliseconds since last tick received
    bars_since_last_gap: int  # Consecutive bars without data gap


@dataclass(frozen=True)
class RiskEnvironment:
    """Risk state at decision time."""
    drawdown_pct: float         # Current drawdown from equity high watermark
    daily_loss_pct: float       # Today's cumulative loss percentage
    open_positions: int         # Number of currently open positions
    correlation_exposure: float # Net correlation group exposure (lots)


@dataclass(frozen=True)
class EventsRef:
    """Pointers to the last observed events (for joining to events/ layer)."""
    last_candle_ts: int     # timestamp of last closed candle (ms)
    last_feature_ts: int    # timestamp of last FEATURE_UPDATE event (ms)


@dataclass(frozen=True)
class ExecutionContext:
    """
    Complete frozen snapshot of system state at decision time.

    IMMUTABLE after creation. NEVER updated post-decision.
    One record per decision. Joinable via correlation_id.
    """
    correlation_id: str
    symbol: str
    timestamp_utc: float        # Decision time (Unix seconds, bar close time)
    market_access: MarketAccess
    infrastructure: Infrastructure
    risk_environment: RiskEnvironment
    events_ref: EventsRef
    # Canonical lineage (remediation) — authoritative opportunity root
    canonical_opportunity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to flat dict for JSONL persistence."""
        return {
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "market_access": asdict(self.market_access),
            "infrastructure": asdict(self.infrastructure),
            "risk_environment": asdict(self.risk_environment),
            "events_ref": asdict(self.events_ref),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_execution_context(
    *,
    correlation_id: str,
    symbol: str,
    timestamp_utc: float,
    # Market access
    bid: float,
    ask: float,
    session_state: str = "UNKNOWN",
    spread_atr_ratio: float = 0.0,
    # Infrastructure
    latency_ms: int = 0,
    feed_state: str = "HEALTHY",
    tick_age_ms: int = 0,
    bars_since_last_gap: int = 0,
    # Risk environment
    drawdown_pct: float = 0.0,
    daily_loss_pct: float = 0.0,
    open_positions: int = 0,
    correlation_exposure: float = 0.0,
    # Events references
    last_candle_ts: int = 0,
    last_feature_ts: int = 0,
    # Canonical lineage (remediation)
    canonical_opportunity_id: str = "",
) -> ExecutionContext:
    """
    Build an immutable execution context snapshot.

    Called at decision time, BEFORE any trade execution.
    All values must be genuine runtime state — no inference, no defaults
    that mask absence (use 0 only for genuinely measured zeros).

    Args:
        correlation_id: Decision Spine ID linking all artefacts
        symbol: Trading pair (e.g., "EURUSD")
        timestamp_utc: Decision time as Unix seconds (closed bar time)
        bid: Current bid price from MT5
        ask: Current ask price from MT5
        session_state: Active trading session classification
        spread_atr_ratio: spread / ATR (execution cost relative to volatility)
        latency_ms: MT5 API response time (last fetch)
        feed_state: Feed health classification
        tick_age_ms: Time since last tick received
        bars_since_last_gap: Consecutive bars without missing data
        drawdown_pct: Current drawdown % from equity high
        daily_loss_pct: Today's cumulative loss %
        open_positions: Count of currently open positions
        correlation_exposure: Net lots in correlation group
        last_candle_ts: Timestamp of last closed candle (ms)
        last_feature_ts: Timestamp of last FEATURE_UPDATE (ms)

    Returns:
        Frozen ExecutionContext (immutable dataclass)
    """
    spread = round(ask - bid, 8) if (bid > 0 and ask > 0) else 0.0

    return ExecutionContext(
        correlation_id=correlation_id,
        symbol=symbol,
        timestamp_utc=timestamp_utc,
        canonical_opportunity_id=canonical_opportunity_id,
        market_access=MarketAccess(
            session_state=session_state,
            spread=spread,
            spread_atr_ratio=round(spread_atr_ratio, 6),
            bid=bid,
            ask=ask,
        ),
        infrastructure=Infrastructure(
            latency_ms=latency_ms,
            feed_state=feed_state,
            tick_age_ms=tick_age_ms,
            bars_since_last_gap=bars_since_last_gap,
        ),
        risk_environment=RiskEnvironment(
            drawdown_pct=round(drawdown_pct, 4),
            daily_loss_pct=round(daily_loss_pct, 4),
            open_positions=open_positions,
            correlation_exposure=round(correlation_exposure, 4),
        ),
        events_ref=EventsRef(
            last_candle_ts=last_candle_ts,
            last_feature_ts=last_feature_ts,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_execution_context(ctx: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate an execution context record before persistence.

    Checks:
        - All required fields present
        - No forbidden fields (outcome/execution data)
        - correlation_id is non-empty
        - timestamp is positive

    Returns:
        (valid, reason) — True if safe to persist, False with rejection reason.
    """
    # Required top-level
    if not ctx.get("correlation_id"):
        return False, "missing_correlation_id"
    if not ctx.get("symbol"):
        return False, "missing_symbol"
    if not ctx.get("timestamp_utc") or ctx["timestamp_utc"] <= 0:
        return False, "invalid_timestamp"

    # Required sections
    for section in ("market_access", "infrastructure", "risk_environment", "events_ref"):
        if section not in ctx or not isinstance(ctx[section], dict):
            return False, f"missing_section:{section}"

    # Market access required fields
    ma = ctx["market_access"]
    if ma.get("bid", 0) <= 0 or ma.get("ask", 0) <= 0:
        return False, "invalid_bid_ask"

    # Forbidden field scan (deep)
    def _scan_forbidden(d: dict, path: str = "") -> str | None:
        for k, v in d.items():
            if k in _FORBIDDEN_FIELDS:
                return f"forbidden_field:{path}{k}"
            if isinstance(v, dict):
                result = _scan_forbidden(v, f"{path}{k}.")
                if result:
                    return result
        return None

    forbidden = _scan_forbidden(ctx)
    if forbidden:
        return False, forbidden

    return True, "valid"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (local + S3)
# ═══════════════════════════════════════════════════════════════════════════════

def persist_execution_context(ctx: ExecutionContext | dict[str, Any]) -> bool:
    """
    Persist execution context to local JSONL and S3 mirror.

    MUST be called BEFORE trade execution (captures pre-execution state).
    Append-only. Never overwrites. Immutable once written.

    Args:
        ctx: ExecutionContext dataclass or pre-serialized dict

    Returns:
        True on successful local write, False on failure.
    """
    try:
        record = ctx.to_dict() if isinstance(ctx, ExecutionContext) else ctx

        # Validate before write
        valid, reason = validate_execution_context(record)
        if not valid:
            logger.warning(
                "[EXECUTION_CONTEXT] rejected: %s correlation_id=%s",
                reason, record.get("correlation_id", "?"),
            )
            return False

        symbol = record["symbol"]
        ts = record["timestamp_utc"]
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

        record["schema_version"] = _SCHEMA_VERSION

        # ─── LOCAL WRITE (primary truth) ──────────────────────────────
        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"

        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        logger.debug(
            "[EXECUTION_CONTEXT] persisted correlation_id=%s symbol=%s session=%s spread=%.6f",
            record["correlation_id"], symbol,
            record["market_access"]["session_state"],
            record["market_access"]["spread"],
        )

        # ─── S3 MIRROR (secondary, fire-and-forget) ──────────────────
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_append(symbol, date_str, line)
        except Exception:
            pass  # S3 failure must never block local persistence

        return True

    except Exception as exc:
        logger.warning("[EXECUTION_CONTEXT] persist_failed: %s", exc)
        return False


def _s3_append(symbol: str, date_str: str, line: str) -> None:
    """Append to S3 execution_context partition. Fire-and-forget."""
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
        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# READER (for analytics / offline query)
# ═══════════════════════════════════════════════════════════════════════════════

def load_execution_contexts(
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    local_dir: str = _LOCAL_DIR,
) -> list[dict[str, Any]]:
    """
    Load execution context records from local JSONL.

    Read-only. Never modifies records. Supports filtering by symbol and date.
    """
    records: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return records

    for f in sorted(path.rglob("*.jsonl")):
        if symbol and symbol not in str(f):
            continue
        fname = f.stem
        if date_from and fname < date_from:
            continue
        if date_to and fname > date_to:
            continue

        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return records
