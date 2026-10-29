"""
Risk Observability Layer — read-only snapshot of all active risk systems.

STRICTLY OBSERVATIONAL. This module:
  - Aggregates state from existing independent risk guards
  - Never modifies any state
  - Never influences trading decisions
  - Never gates execution

Intended use: logging, dashboards, debugging, audit trails.

FORBIDDEN imports in:
  - execution layer
  - scanner loop decision logic
  - retry queue logic
  - kill switch enforcement paths
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def get_risk_summary(
    *,
    daily_loss_guard=None,   # DailyLossGuard instance (optional)
    drawdown_guard=None,     # DrawdownGuard instance (optional)
    symbol: str = "",
    bid: float = 0.0,
    ask: float = 0.0,
    regime: str = "unknown",
) -> dict[str, Any]:
    """
    Aggregate current state of all risk systems into a single read-only dict.

    Reads from existing guards without re-evaluating enforcement logic.
    Never modifies any state. Never raises — returns 'unknown' for unavailable fields.

    Args:
        daily_loss_guard: DailyLossGuard instance (from scanner context)
        drawdown_guard:   DrawdownGuard instance (from scanner context)
        symbol:           Current symbol (for spread context)
        bid/ask:          Latest tick prices (for live spread calculation)
        regime:           Current regime string from engine state

    Returns:
        dict with all risk system states. Suitable for structured logging.
    """
    snapshot: dict[str, Any] = {
        "timestamp": round(time.time(), 3),
        "symbol": symbol,
    }

    # ─── KILL SWITCH ──────────────────────────────────────────────
    try:
        from core.kill_switch import is_kill_switch_active, _last_known_state
        snapshot["kill_switch_active"] = is_kill_switch_active()
        snapshot["kill_switch_last_known"] = _last_known_state
    except Exception:
        snapshot["kill_switch_active"] = "unknown"
        snapshot["kill_switch_last_known"] = "unknown"

    # ─── DAILY LOSS GUARD ─────────────────────────────────────────
    try:
        if daily_loss_guard is not None:
            snapshot["daily_loss_blocked"] = daily_loss_guard.is_triggered
            snapshot["daily_loss_start_equity"] = round(daily_loss_guard.daily_start_equity, 2)
        else:
            snapshot["daily_loss_blocked"] = "unavailable"
            snapshot["daily_loss_start_equity"] = "unavailable"
    except Exception:
        snapshot["daily_loss_blocked"] = "unknown"
        snapshot["daily_loss_start_equity"] = "unknown"

    # ─── DRAWDOWN GUARD ───────────────────────────────────────────
    try:
        if drawdown_guard is not None:
            snapshot["drawdown_peak_equity"] = round(drawdown_guard.peak_equity, 2)
        else:
            snapshot["drawdown_peak_equity"] = "unavailable"
    except Exception:
        snapshot["drawdown_peak_equity"] = "unknown"

    # ─── SPREAD (live calculation from current tick) ───────────────
    try:
        if bid > 0 and ask > 0:
            spread = ask - bid
            snapshot["current_spread"] = round(spread, 6)
            snapshot["spread_ok"] = spread >= 0
        else:
            snapshot["current_spread"] = "unavailable"
            snapshot["spread_ok"] = "unavailable"
    except Exception:
        snapshot["current_spread"] = "unknown"
        snapshot["spread_ok"] = "unknown"

    # ─── SPREAD GUARD METRICS (lifetime counters) ─────────────────
    try:
        from risk.spread_guard import get_spread_guard_metrics
        sg_metrics = get_spread_guard_metrics()
        snapshot["spread_guard_checked"] = sg_metrics.get("checked", 0)
        snapshot["spread_guard_blocked"] = (
            sg_metrics.get("blocked_ratio", 0)
            + sg_metrics.get("blocked_absolute", 0)
            + sg_metrics.get("blocked_missing_data", 0)
        )
    except Exception:
        snapshot["spread_guard_checked"] = "unknown"
        snapshot["spread_guard_blocked"] = "unknown"

    # ─── MARKET REGIME ────────────────────────────────────────────
    try:
        snapshot["regime"] = regime if regime else "unknown"
    except Exception:
        snapshot["regime"] = "unknown"

    # ─── CIRCUIT BREAKER ──────────────────────────────────────────
    try:
        from core.mt5_timeout import get_circuit_state, get_timeout_metrics
        snapshot["circuit_breaker_state"] = get_circuit_state()
        tm = get_timeout_metrics()
        snapshot["consecutive_timeouts"] = tm.get("consecutive_timeouts", 0)
        snapshot["total_timeouts"] = tm.get("total_timeouts", 0)
    except Exception:
        snapshot["circuit_breaker_state"] = "unknown"
        snapshot["consecutive_timeouts"] = "unknown"
        snapshot["total_timeouts"] = "unknown"

    return snapshot
