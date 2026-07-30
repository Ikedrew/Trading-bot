"""
V3 Shadow Observer — Produces MarketUnderstanding each cycle for research.

Registered as observer #10 in ObserverRegistry. Runs AFTER all other observers.
Builds MarketUnderstanding and persists to JSONL for V3 shadow pipeline research.

This module:
    - NEVER influences trading decisions
    - NEVER modifies engine_result
    - NEVER blocks any operation
    - Only READS context and WRITES research observations

Persistence: logs/v3_shadow/market_understanding/{SYMBOL}/{DATE}.jsonl
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/v3_shadow/market_understanding"


def observe_market_understanding(ctx: Any) -> None:
    """
    V3 shadow observer — called by ObserverRegistry as observer #10.

    Builds MarketUnderstanding from all available context and persists.
    Never raises. Failure is logged and silently ignored.
    """
    try:
        _do_observe(ctx)
    except Exception as exc:
        logger.debug("[V3_SHADOW_OBSERVER] failed: %s", exc)


def _do_observe(ctx: Any) -> None:
    """Internal observation logic. May raise."""
    from core.v3_shadow.builders import build_market_understanding

    # Gather detector snapshots if available (from V3 observer #9 data)
    # These are computed fresh since observer #10 runs after #9
    liquidity_snap = None
    fvg_snap = None
    ob_snap = None

    if ctx.candles and len(ctx.candles) > 14:
        closed_i = getattr(ctx, "closed_i", -1)
        bid = ctx.bid
        ask = ctx.ask
        price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        atr = sum(c.high - c.low for c in ctx.candles[-14:]) / 14

        if closed_i > 10 and atr > 0:
            try:
                from core.market_intelligence.liquidity_detector import detect_liquidity
                liquidity_snap = detect_liquidity(ctx.candles, price, closed_i, ctx.symbol)
            except Exception:
                pass
            try:
                from core.market_intelligence.fvg_detector import detect_fvgs
                fvg_snap = detect_fvgs(ctx.candles, price, closed_i, atr, ctx.symbol)
            except Exception:
                pass
            try:
                from core.market_intelligence.order_block_detector import detect_order_blocks
                ob_snap = detect_order_blocks(ctx.candles, price, closed_i, atr, ctx.symbol)
            except Exception:
                pass

    # Build MarketUnderstanding
    understanding = build_market_understanding(
        symbol=ctx.symbol,
        timestamp_utc=ctx.bar_time,
        candles=ctx.candles,
        htf_context=ctx.htf_context,
        market_context=getattr(ctx, "market_context", None),
        bid=ctx.bid,
        ask=ctx.ask,
        liquidity_snapshot=liquidity_snap,
        fvg_snapshot=fvg_snap,
        ob_snapshot=ob_snap,
    )

    # Persist MarketUnderstanding
    _persist(understanding)

    # Build V3MarketContext from MarketUnderstanding
    from core.v3_shadow.context_builders import build_v3_market_context
    market_ctx = build_v3_market_context(understanding)

    # Persist V3MarketContext
    _persist_context(market_ctx)

    # Build OpportunityAssessment from V3MarketContext
    from core.v3_shadow.opportunity_builder import build_opportunity_assessment
    assessment = build_opportunity_assessment(market_ctx)

    # Persist OpportunityAssessment
    _persist_assessment(assessment)

    # Build HorizonAssessment from context + opportunity
    from core.v3_shadow.horizon_builder import build_horizon_assessment
    horizon = build_horizon_assessment(market_ctx, assessment)

    # Persist HorizonAssessment
    _persist_horizon(horizon)

    # Build EntryAssessment from context + opportunity + horizon
    from core.v3_shadow.entry_builder import build_entry_assessment
    price = (ctx.bid + ctx.ask) / 2 if ctx.bid > 0 and ctx.ask > 0 else 0.0
    entry = build_entry_assessment(
        market_ctx, assessment, horizon, current_price=price)

    # Persist EntryAssessment
    _persist_entry(entry)

    # Build RiskAssessment from horizon + context
    from core.v3_shadow.risk_builder import build_risk_assessment
    spread_pips = 0.0
    if ctx.bid > 0 and ctx.ask > 0:
        pip_size = 0.01 if "JPY" in ctx.symbol.upper() else 0.0001
        spread_pips = abs(ctx.ask - ctx.bid) / pip_size
    risk = build_risk_assessment(market_ctx, horizon, spread_pips=spread_pips)

    # Persist RiskAssessment
    _persist_risk(risk)

    # Build ExecutionAssessment from all upstream
    from core.v3_shadow.execution_builder import build_execution_assessment
    execution = build_execution_assessment(
        market_ctx, assessment, horizon, entry, risk,
        bid=ctx.bid, ask=ctx.ask)

    # Persist ExecutionAssessment
    _persist_execution(execution)

    # Structured logging (debug level)
    if execution.execution_state != "NOT_EXECUTABLE":
        logger.debug(
            "[V3_EXEC] %s %s | %s %s | entry=%.5f stop=%.5f tgt=%.5f",
            execution.symbol, execution.execution_state,
            execution.direction, execution.horizon,
            execution.entry_price, execution.stop_price, execution.target_price,
        )


def _persist(understanding: Any) -> None:
    """Persist MarketUnderstanding to local JSONL. Fire-and-forget."""
    try:
        symbol = understanding.symbol or "UNKNOWN"
        ts = understanding.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(understanding.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_SHADOW_PERSIST] failed: %s", exc)


_CONTEXT_DIR = "logs/v3_shadow/market_context"


def _persist_context(context: Any) -> None:
    """Persist V3MarketContext to local JSONL. Fire-and-forget."""
    try:
        symbol = context.symbol or "UNKNOWN"
        ts = context.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_CONTEXT_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(context.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_CONTEXT_PERSIST] failed: %s", exc)


_ASSESSMENT_DIR = "logs/v3_shadow/opportunity_assessment"


def _persist_assessment(assessment: Any) -> None:
    """Persist OpportunityAssessment to local JSONL. Fire-and-forget."""
    try:
        symbol = assessment.symbol or "UNKNOWN"
        ts = assessment.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_ASSESSMENT_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(assessment.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_ASSESSMENT_PERSIST] failed: %s", exc)


_HORIZON_DIR = "logs/v3_shadow/horizon_assessment"


def _persist_horizon(horizon_assessment: Any) -> None:
    """Persist HorizonAssessment to local JSONL. Fire-and-forget."""
    try:
        symbol = horizon_assessment.symbol or "UNKNOWN"
        ts = horizon_assessment.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_HORIZON_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(horizon_assessment.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_HORIZON_PERSIST] failed: %s", exc)


_RISK_DIR = "logs/v3_shadow/risk_assessment"


def _persist_risk(risk_assessment: Any) -> None:
    """Persist RiskAssessment to local JSONL. Fire-and-forget."""
    try:
        symbol = risk_assessment.symbol or "UNKNOWN"
        ts = risk_assessment.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_RISK_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(risk_assessment.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_RISK_PERSIST] failed: %s", exc)


_ENTRY_DIR = "logs/v3_shadow/entry_assessment"


def _persist_entry(entry_assessment: Any) -> None:
    """Persist EntryAssessment to local JSONL. Fire-and-forget."""
    try:
        symbol = entry_assessment.symbol or "UNKNOWN"
        ts = entry_assessment.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_ENTRY_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(entry_assessment.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_ENTRY_PERSIST] failed: %s", exc)


_EXECUTION_DIR = "logs/v3_shadow/execution_assessment"


def _persist_execution(execution_assessment: Any) -> None:
    """Persist ExecutionAssessment to local JSONL. Fire-and-forget."""
    try:
        symbol = execution_assessment.symbol or "UNKNOWN"
        ts = execution_assessment.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_EXECUTION_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(execution_assessment.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:
        logger.debug("[V3_EXECUTION_PERSIST] failed: %s", exc)
