"""
Audit Data Loader — Reads trade data into analysis-ready records.

PRIMARY SOURCE: events/{YYYY-MM-DD}.jsonl (unified event ledger)

Filters OUTCOME and DECISION events from the unified stream to produce
structured dicts suitable for cohort slicing.

STRICTLY OFFLINE — never imported by runtime code.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── UNIFIED LEDGER READER ───────────────────────────────────────────────────

def _read_events_by_type(
    event_type: str,
    *,
    event_dir: str = "events",
    symbol: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Read all events of a specific type from the unified ledger.

    Args:
        event_type: CANDLE, ENTITY, STRATEGY, DECISION, EXECUTION, or OUTCOME
        event_dir: Path to events directory
        symbol: Filter by symbol (None = all)
        days: How many recent days to scan

    Returns:
        List of event dicts sorted by ts_utc_ms.
    """
    events: list[dict[str, Any]] = []
    base_dir = Path(event_dir)
    if not base_dir.exists():
        logger.info("[COHORT_LOADER] event_dir not found: %s", base_dir)
        return events

    files = sorted(base_dir.glob("*.jsonl"), reverse=True)[:days]

    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") != event_type:
                        continue
                    if symbol and event.get("symbol") != symbol:
                        continue

                    events.append(event)
        except Exception as exc:
            logger.warning("[COHORT_LOADER] error reading %s: %s", filepath, exc)

    return sorted(events, key=lambda e: e.get("ts_utc_ms", 0))


# ─── PRIMARY API: OUTCOME EVENTS ─────────────────────────────────────────────

def load_trade_outcomes(
    *,
    event_dir: str = "events",
    symbol: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Load all trade outcomes from the unified event ledger.

    Returns analysis-ready dicts derived from OUTCOME events.
    """
    events = _read_events_by_type("OUTCOME", event_dir=event_dir, symbol=symbol, days=days)

    records: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        records.append({
            "ts_utc_ms": event.get("ts_utc_ms", 0),
            "trade_id": payload.get("trade_id", ""),
            "symbol": event.get("symbol", ""),
            "should_trade": True,
            "entry_price": payload.get("entry_price"),
            "exit_price": payload.get("exit_price"),
            "pnl": payload.get("pnl"),
            "rr_realised": payload.get("rr_realised"),
            "duration_ms": payload.get("duration_ms"),
            "exit_reason": payload.get("exit_reason"),
            "mfe_r": payload.get("mfe_r"),
            "mae_r": payload.get("mae_r"),
            "pattern": payload.get("pattern"),
            "direction": payload.get("direction"),
            "volume": payload.get("volume"),
            "decision_ts_utc_ms": payload.get("decision_ts_utc_ms"),
            "execution_ts_utc_ms": payload.get("execution_ts_utc_ms"),
            "outcome_win": (payload.get("pnl") or 0) > 0 if payload.get("pnl") is not None else None,
            "outcome_rr": payload.get("rr_realised"),
            "outcome_pnl": payload.get("pnl"),
        })

    return records


# ─── PRIMARY API: DECISION EVENTS ────────────────────────────────────────────

def load_decisions(
    *,
    event_dir: str = "events",
    symbol: str | None = None,
    days: int = 30,
    only_trades: bool = False,
) -> list[dict[str, Any]]:
    """
    Load decision events from the unified event ledger.

    Args:
        event_dir: Path to events directory
        symbol: Filter by symbol
        days: How many recent days to scan
        only_trades: If True, only return decisions where should_trade=True

    Returns:
        List of decision payload dicts.
    """
    events = _read_events_by_type("DECISION", event_dir=event_dir, symbol=symbol, days=days)

    records: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})

        if only_trades and not payload.get("should_trade", False):
            continue

        record = dict(payload)
        record["ts_utc_ms"] = event.get("ts_utc_ms", 0)
        record["symbol"] = event.get("symbol", "")
        records.append(record)

    return records


# ─── PRIMARY API: EXECUTION EVENTS ───────────────────────────────────────────

def load_executions(
    *,
    event_dir: str = "events",
    symbol: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Load execution events from unified ledger."""
    events = _read_events_by_type("EXECUTION", event_dir=event_dir, symbol=symbol, days=days)
    records: list[dict[str, Any]] = []
    for event in events:
        record = dict(event.get("payload", {}))
        record["ts_utc_ms"] = event.get("ts_utc_ms", 0)
        record["symbol"] = event.get("symbol", "")
        records.append(record)
    return records


# ─── ENRICHMENT: JOIN OUTCOMES TO DECISIONS ───────────────────────────────────

def enrich_decisions_with_outcomes(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Join OUTCOME data to DECISION records using causal linkage.

    Matches on: decision_ts_utc_ms (exact match from OUTCOME payload).
    No timestamp proximity heuristics.
    """
    # Index outcomes by decision_ts_utc_ms
    outcome_by_decision_ts: dict[int, dict[str, Any]] = {}
    for o in outcomes:
        dts = o.get("decision_ts_utc_ms")
        if dts:
            outcome_by_decision_ts[dts] = o

    enriched: list[dict[str, Any]] = []
    for dec in decisions:
        enriched_dec = dict(dec)
        dec_ts = dec.get("ts_utc_ms", 0)
        outcome = outcome_by_decision_ts.get(dec_ts)

        if outcome:
            enriched_dec["outcome_pnl"] = outcome.get("pnl")
            enriched_dec["outcome_rr"] = outcome.get("rr_realised")
            enriched_dec["outcome_win"] = outcome.get("outcome_win")
            enriched_dec["outcome_exit_reason"] = outcome.get("exit_reason")
            enriched_dec["outcome_duration_ms"] = outcome.get("duration_ms")
        else:
            enriched_dec.setdefault("outcome_pnl", None)
            enriched_dec.setdefault("outcome_rr", None)
            enriched_dec.setdefault("outcome_win", None)

        enriched.append(enriched_dec)

    return enriched


# ─── DEPRECATED LEGACY LOADERS (remove after migration) ──────────────────────

def load_canonical_trades(
    path: str | None = None,
    *,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """
    # deprecated — remove after migration
    Legacy loader for data/canonical_trades.jsonl.
    Prefer load_trade_outcomes() from unified ledger.
    """
    filepath = Path(path) if path else Path("data/canonical_trades.jsonl")
    records: list[dict[str, Any]] = []

    if not filepath.exists():
        return records

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if symbol and record.get("symbol") != symbol:
                continue
            records.append(record)

    return records


# NOTE (Production V1 cleanup): load_audit_records() was removed — it read the
# retired logs/decision_audit dataset and had no callers. Use load_decisions()
# from the unified decision ledger instead.


def load_journal_outcomes(
    journal_dir: str = "logs/trade_journal",
) -> dict[str, dict[str, Any]]:
    """
    # deprecated — remove after migration
    Legacy loader for logs/trade_journal/*.jsonl.
    Prefer load_trade_outcomes() from unified ledger.
    """
    outcomes: dict[str, dict[str, Any]] = {}
    journal_path = Path(journal_dir)
    if not journal_path.exists():
        return outcomes

    for filepath in sorted(journal_path.glob("*.jsonl")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    trade_id = record.get("trade_id", "")
                    if trade_id:
                        outcomes[trade_id] = record
        except Exception:
            continue

    return outcomes
