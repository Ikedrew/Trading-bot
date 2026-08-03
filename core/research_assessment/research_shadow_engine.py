"""
Research Shadow Engine — Tracks hypothetical trades for RESEARCH_WOULD_EXECUTE decisions.

Reuses ShadowTradeEngine infrastructure for lifecycle management.
Persists to a SEPARATE path: logs/research_shadow_trades/

This module ONLY creates and tracks research-only trades.
It does NOT affect production execution in any way.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.shadow_trades import ShadowTradeEngine

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/research_shadow_trades"
_S3_BUCKET = "v10-engine"
_S3_PREFIX = "research_shadow_trades"
_SCHEMA_VERSION = "research_shadow_trades_v1"
_engine: ShadowTradeEngine | None = None


def get_research_shadow_engine() -> ShadowTradeEngine:
    """Get or create the singleton research shadow trade engine."""
    global _engine
    if _engine is None:
        _engine = ShadowTradeEngine(max_bars=60)
    return _engine


def open_research_trade(
    *,
    trade_id: str,
    cycle_id: int,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: float,
    pattern: str = "",
    candidate_id: str = "",
    score: float = 0.0,
) -> None:
    """
    Open a research shadow trade for a RESEARCH_WOULD_EXECUTE decision.

    Called when the research model would have executed but production rejected.
    Never raises. Never affects production.
    """
    try:
        engine = get_research_shadow_engine()
        engine.open_trade(
            trade_id=trade_id,
            cycle_id=cycle_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=entry_time,
            strategy=f"research:{candidate_id}",
            pattern=pattern,
            score=score,
            correlation_id=candidate_id,
        )
    except Exception:
        pass  # Research shadow must never affect production


def evaluate_research_bar(
    *,
    symbol: str,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_time: float,
    bar_index: int = 0,
) -> list[dict[str, Any]]:
    """
    Evaluate all active research shadow trades for the given symbol.

    Called on every closed bar. Returns list of closed trade records.
    Never raises. Never affects production.
    """
    try:
        engine = get_research_shadow_engine()
        closed = engine.evaluate_bar(
            symbol=symbol,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            bar_time=bar_time,
            bar_index=bar_index,
        )
        # Persist closed research trades
        for record in closed:
            _persist_research_trade(record)
            _update_promotion_monitor(record)
        return closed
    except Exception:
        return []


def _persist_research_trade(record: dict[str, Any]) -> None:
    """Persist research shadow trade to local JSONL + S3 mirror. Never raises."""
    try:
        identity = record.get("identity", {})
        symbol = identity.get("symbol", "UNKNOWN")
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Tag as research trade
        record["source"] = "research_shadow_engine"
        record["schema_version"] = _SCHEMA_VERSION

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget)
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_append_research(symbol, date_str, line)
        except Exception:
            pass

    except Exception:
        pass


def _s3_append_research(symbol: str, date_str: str, line: str) -> None:
    """Append a single line to S3 research shadow trades JSONL. Never raises."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        key = f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}/symbol={symbol}/date={date_str}/part-000.jsonl"

        # Read-append-write (safe for low-volume research trades)
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
        pass  # S3 failure must never affect runtime


def _update_promotion_monitor(record: dict[str, Any]) -> None:
    """Feed closed research trade R-multiple to promotion monitor. Never raises."""
    try:
        outcome = record.get("simulated_outcome", {})
        r_multiple = outcome.get("pnl_r_multiple", 0.0)
        exit_reason = outcome.get("exit_reason", "")

        from core.research_assessment.promotion_monitor import record_research_outcome
        record_research_outcome(r_multiple=r_multiple, exit_reason=exit_reason)
    except Exception:
        pass


def get_stats() -> dict[str, Any]:
    """Return research shadow engine statistics."""
    try:
        engine = get_research_shadow_engine()
        return engine.stats()
    except Exception:
        return {"active_trades": 0, "closed_trades": 0}
