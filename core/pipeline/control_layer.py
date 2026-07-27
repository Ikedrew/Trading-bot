"""
Control Layer — Final execution authority gate.

NOT strategy logic. NOT risk logic. NOT market evaluation.
ONLY controls whether broker execution is permitted.

Sits between all runtime guards and MT5 order placement.
Strategy pipeline runs regardless of control state.
"""

from __future__ import annotations

control_state: dict = {
    "mode": "LIVE",          # LIVE / DRY_RUN
    "force_block": False,
}


def control_gate() -> tuple[bool, str | None]:
    """
    Final permission check before broker execution.

    Returns:
        (True, None) if execution is allowed.
        (False, reason) if execution is blocked.
    """
    if control_state["force_block"]:
        return False, "Manual force block active"

    if control_state["mode"] == "DRY_RUN":
        return False, "Dry run mode active"

    return True, None
