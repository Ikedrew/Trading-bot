"""
Live Trade Snapshot — Captures immutable snapshots of completed trades for offline cohort analysis.

PURE LOGGING ONLY.
Does NOT modify execution logic, TradeStateManager, or any runtime engine.
No runtime imports. No engine coupling. Only post-trade capture.

Primary source: data/canonical_trades.jsonl (emitted by event_bus on trade close)
Legacy source: data/trade_snapshots.jsonl (append-only, one line per completed trade)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.trade_schema import CanonicalTradeEvent, TradeOutcome

logger = logging.getLogger(__name__)

_CANONICAL_FILE = "data/canonical_trades.jsonl"
_LEGACY_SNAPSHOT_FILE = "data/trade_snapshots.jsonl"


# ─── CANONICAL LOADING ────────────────────────────────────────────────────────

def load_canonical_trades(path: str | None = None) -> list[CanonicalTradeEvent]:
    """
    Load all canonical trade events from JSONL.

    Args:
        path: Path to JSONL file. Defaults to data/canonical_trades.jsonl.

    Returns:
        List of CanonicalTradeEvent objects.
    """
    filepath = Path(path) if path else Path(_CANONICAL_FILE)
    results: list[CanonicalTradeEvent] = []

    if not filepath.exists():
        return results

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event = _dict_to_canonical(data)
                results.append(event)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    return results


def _dict_to_canonical(data: dict[str, Any]) -> CanonicalTradeEvent:
    """Convert a dict record to CanonicalTradeEvent."""
    outcome = None
    raw_outcome = data.get("outcome")
    if raw_outcome:
        try:
            outcome = TradeOutcome(raw_outcome)
        except ValueError:
            pass

    return CanonicalTradeEvent(
        trade_id=data.get("trade_id", ""),
        symbol=data.get("symbol", ""),
        entry_time=data.get("entry_time", ""),
        exit_time=data.get("exit_time"),
        entry_price=float(data.get("entry_price", 0.0)),
        exit_price=data.get("exit_price"),
        position_size=float(data.get("position_size", 0.0)),
        entry_r=float(data.get("entry_r", 0.0)),
        final_r=data.get("final_r"),
        mfe=float(data.get("mfe", 0.0)),
        mae=float(data.get("mae", 0.0)),
        outcome=outcome,
        confirmation_strength=data.get("confirmation_strength", "UNKNOWN"),
        entry_timing=data.get("entry_timing", "UNKNOWN"),
        market_regime=data.get("market_regime", "UNKNOWN"),
        breakeven_triggered=bool(data.get("breakeven_triggered", False)),
        trailing_triggered=bool(data.get("trailing_triggered", False)),
        partials_taken=data.get("partials_taken") or [],
    )


# ─── LEGACY COMPATIBILITY (TradeSnapshot) ─────────────────────────────────────

# Keep TradeSnapshot as alias for backward compat with existing test/analysis code
TradeSnapshot = CanonicalTradeEvent


def log_trade_snapshot(trade_state: dict[str, Any]) -> CanonicalTradeEvent:
    """
    Extract a CanonicalTradeEvent from a completed trade state dict.
    Also persists to legacy snapshot file for backward compatibility.

    Args:
        trade_state: Dict containing trade lifecycle data.

    Returns:
        CanonicalTradeEvent (also persisted to disk).
    """
    from datetime import datetime, timezone

    # Identity
    trade_id = trade_state.get("trade_id") or trade_state.get("position_id", "unknown")
    symbol = trade_state.get("symbol", "unknown")

    # Timing
    entry_time = trade_state.get("entry_time") or trade_state.get("open_time", "")
    if isinstance(entry_time, (int, float)):
        entry_time = datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
    exit_time = trade_state.get("exit_time") or trade_state.get("closed_time")
    if isinstance(exit_time, (int, float)):
        exit_time = datetime.fromtimestamp(exit_time, tz=timezone.utc).isoformat()

    # R values
    final_r = trade_state.get("final_r") or trade_state.get("outcome_rr")
    mfe = float(trade_state.get("mfe_r") or trade_state.get("mfe", 0.0))
    mae = float(trade_state.get("mae_r") or trade_state.get("mae", 0.0))

    # Outcome
    outcome = None
    if final_r is not None:
        if final_r > 0.05:
            outcome = TradeOutcome.WIN
        elif final_r < -0.05:
            outcome = TradeOutcome.LOSS
        else:
            outcome = TradeOutcome.BREAKEVEN

    # Confirmation context (direct from canonical fields)
    confirmation = trade_state.get("confirmation") or {}
    confirmation_strength = confirmation.get("strength", "UNKNOWN") if isinstance(confirmation, dict) else "UNKNOWN"
    entry_timing = trade_state.get("entry_timing", "UNKNOWN") or "UNKNOWN"
    engine_state = trade_state.get("engine_state") or {}
    regime = engine_state.get("regime_state", "UNKNOWN") if isinstance(engine_state, dict) else "UNKNOWN"

    # Management flags
    breakeven_triggered = bool(trade_state.get("be_triggered") or trade_state.get("breakeven_triggered", False))
    trailing_triggered = bool(trade_state.get("trailing_active") or trade_state.get("trailing_triggered", False))
    partials_taken = trade_state.get("partials_taken") or trade_state.get("partials", [])
    if not isinstance(partials_taken, list):
        partials_taken = []

    event = CanonicalTradeEvent(
        trade_id=str(trade_id),
        symbol=str(symbol),
        entry_time=str(entry_time),
        exit_time=str(exit_time) if exit_time else None,
        entry_price=float(trade_state.get("entry_price", 0.0)),
        exit_price=trade_state.get("exit_price"),
        position_size=float(trade_state.get("position_size") or trade_state.get("volume", 0.0)),
        entry_r=0.0,
        final_r=float(final_r) if final_r is not None else None,
        mfe=mfe,
        mae=mae,
        outcome=outcome,
        confirmation_strength=str(confirmation_strength),
        entry_timing=str(entry_timing),
        market_regime=str(regime),
        breakeven_triggered=breakeven_triggered,
        trailing_triggered=trailing_triggered,
        partials_taken=list(partials_taken),
    )

    _persist_legacy_snapshot(event)
    return event


def _persist_legacy_snapshot(event: CanonicalTradeEvent) -> bool:
    """Append to legacy snapshot JSONL. Never raises."""
    try:
        from core.trade_schema import to_dict
        filepath = Path(_LEGACY_SNAPSHOT_FILE)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(to_dict(event), default=str, separators=(",", ":")) + "\n"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception as exc:
        logger.warning("[TRADE_SNAPSHOT] persist failed: %s", exc)
        return False

