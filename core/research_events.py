"""
Research Events — Structured persistence for research-observable bot state.

This module provides fire-and-forget event persistence for bot surfaces
that were previously unobservable by the Research Engine:
    - Guard decisions (cooldown, correlation, position limits)
    - Recovery/restart events
    - Configuration snapshots

CONTRACT:
    - NEVER affects trading decisions
    - NEVER blocks execution
    - NEVER raises exceptions to callers
    - NEVER modifies production state
    - Append-only JSONL persistence
    - Used by Research Engine detectors downstream

Persistence location: logs/research_events/{date}.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EVENT_DIR = Path("logs/research_events")


def persist_guard_event(
    *,
    symbol: str,
    cycle_id: int,
    correlation_id: str,
    guard_name: str,
    allowed: bool,
    reason: str,
    metadata: dict[str, Any] | None = None,
    direction: str = "",
    pattern: str = "",
) -> None:
    """
    Persist a runtime guard evaluation result for research.

    Called after evaluate_runtime_guards() regardless of outcome.
    Captures both ALLOWED and BLOCKED decisions.
    """
    try:
        event = {
            "event_type": "GUARD_DECISION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "cycle_id": cycle_id,
            "correlation_id": correlation_id,
            "guard_name": guard_name,
            "allowed": allowed,
            "reason": reason,
            "direction": direction,
            "pattern": pattern,
            **(metadata or {}),
        }
        _append_event(event)
    except Exception:
        pass  # Must NEVER affect trading


def persist_recovery_event(
    *,
    symbol: str,
    recovered_count: int,
    broker_total: int,
    positions: list[dict[str, Any]] | None = None,
    identity_restored: int = 0,
    identity_failed: int = 0,
    protection_missing: int = 0,
    error: str = "",
) -> None:
    """Persist a startup recovery event for research."""
    try:
        event = {
            "event_type": "RECOVERY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "recovered_count": recovered_count,
            "broker_total": broker_total,
            "identity_restored": identity_restored,
            "identity_failed": identity_failed,
            "protection_missing": protection_missing,
            "error": error,
            "positions": positions or [],
        }
        _append_event(event)
    except Exception:
        pass


def persist_config_snapshot(*, correlation_id: str = "", cycle_id: int = 0) -> str:
    """
    Persist a configuration fingerprint for research version attribution.

    Returns the config hash (or "" on failure).
    """
    try:
        config_hash = compute_config_hash()
        event = {
            "event_type": "CONFIG_SNAPSHOT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_hash": config_hash,
            "correlation_id": correlation_id,
            "cycle_id": cycle_id,
            "material_params": _get_material_params(),
        }
        _append_event(event)
        return config_hash
    except Exception:
        return ""


def compute_config_hash() -> str:
    """
    Compute a deterministic hash of material trading configuration.

    Only includes parameters that affect trading decisions.
    Excludes secrets, paths, and non-functional settings.
    """
    try:
        params = _get_material_params()
        content = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    except Exception:
        return "UNKNOWN"


def _get_material_params() -> dict[str, Any]:
    """Extract material trading parameters from config."""
    try:
        from core import config
        return {
            "ENGINE_MODE": getattr(config, "ENGINE_MODE", ""),
            "COOLDOWN_SECONDS": getattr(config, "COOLDOWN_SECONDS", 0),
            "COOLDOWN_AFTER_LOSS_SECONDS": getattr(config, "COOLDOWN_AFTER_LOSS_SECONDS", 0),
            "MAX_TOTAL_OPEN_POSITIONS": getattr(config, "MAX_TOTAL_OPEN_POSITIONS", 0),
            "MAX_TOTAL_RISK_EXPOSURE_PCT": getattr(config, "MAX_TOTAL_RISK_EXPOSURE_PCT", 0),
            "MAX_CURRENCY_EXPOSURE_LOTS": getattr(config, "MAX_CURRENCY_EXPOSURE_LOTS", 0),
            "MAX_CORRELATION_GROUP_POSITIONS": getattr(config, "MAX_CORRELATION_GROUP_POSITIONS", 0),
            "MIN_SCORE_TO_TRADE": getattr(config, "MIN_SCORE_TO_TRADE", 0),
            "FIXED_LOT": getattr(config, "FIXED_LOT", 0),
            "MIN_RR": getattr(config, "MIN_RR", 0),
            "BASE_RR": getattr(config, "BASE_RR", 0),
            "SPREAD_GUARD_ENABLED": getattr(config, "SPREAD_GUARD_ENABLED", False),
            "MAX_SPREAD_ATR_RATIO": getattr(config, "MAX_SPREAD_ATR_RATIO", 0),
            "DAILY_TRADE_LIMIT_ENABLED": getattr(config, "DAILY_TRADE_LIMIT_ENABLED", False),
            "MAX_TRADES_PER_DAY_TOTAL": getattr(config, "MAX_TRADES_PER_DAY_TOTAL", 0),
            "RISK_PER_TRADE_PERCENT": getattr(config, "RISK_PER_TRADE_PERCENT", 0),
            "DRY_RUN": getattr(config, "DRY_RUN", False),
            "EXECUTION_ENABLED": getattr(config, "EXECUTION_ENABLED", False),
        }
    except Exception:
        return {}


def _append_event(event: dict[str, Any]) -> None:
    """Append a single event to the daily JSONL file. Never raises."""
    try:
        _EVENT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = _EVENT_DIR / f"{date_str}.jsonl"
        line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass
