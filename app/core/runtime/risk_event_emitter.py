"""
Risk Guard Event Emission — Emits RISK_CHECK events for runtime guard rejections.

Called by live_scanner at each guard gate. Never raises.
All emissions are fire-and-forget.
"""

from __future__ import annotations

from typing import Any


def emit_risk_guard_result(
    symbol: str,
    guard: str,
    result: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """
    Emit a RISK_CHECK event to the unified event stream.

    Args:
        symbol: Trading symbol (or "SYSTEM" for cycle-level guards)
        guard: Guard name (e.g. "drawdown_guard", "daily_trade_limit")
        result: "PASS" or "REJECTED"
        reason: Human-readable reason string
        details: Additional structured context (flattened into payload)

    Never raises. Silent on failure.
    """
    try:
        from core.event_stream import emit_risk_check
        payload: dict[str, Any] = {
            "result": result,
            "guard": guard,
            "reason": reason,
        }
        if details:
            payload.update(details)
        emit_risk_check(symbol, payload, source="live_scanner")
    except Exception:
        pass
