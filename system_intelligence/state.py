"""
Runtime State Reader — Answers: "Is the bot running? What status?"

Reads from: runtime/heartbeat.json + core/config.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


_HEARTBEAT_PATH = Path("runtime/heartbeat.json")


def get_runtime_state() -> dict[str, Any]:
    """
    Read current runtime state from heartbeat + config.

    Returns structured dict with:
        - status (RUNNING/SHUTDOWN/UNKNOWN)
        - last_heartbeat (ISO timestamp)
        - age_seconds (how long since last heartbeat)
        - mt5_state
        - strategy
        - execution_mode (live/replay/dry_run)
        - symbols (enabled list)
    """
    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "last_heartbeat": None,
        "age_seconds": None,
        "pid": None,
        "mt5_state": "UNKNOWN",
        "strategy": "UNKNOWN",
        "execution_mode": "UNKNOWN",
        "symbols": [],
    }

    # Read heartbeat
    try:
        if _HEARTBEAT_PATH.exists():
            data = json.loads(_HEARTBEAT_PATH.read_text(encoding="utf-8"))
            result["status"] = data.get("status", "UNKNOWN")
            result["last_heartbeat"] = data.get("timestamp_iso")
            result["pid"] = data.get("pid")
            result["mt5_state"] = data.get("mt5_state", "UNKNOWN")
            result["strategy"] = data.get("strategy", "UNKNOWN")

            ts = data.get("timestamp", 0)
            if ts > 0:
                result["age_seconds"] = round(time.time() - ts, 1)
    except Exception:
        pass

    # Read config for execution mode + symbols
    try:
        from core import config
        if getattr(config, "REPLAY_MODE", False):
            result["execution_mode"] = "REPLAY"
        elif getattr(config, "DRY_RUN", False):
            result["execution_mode"] = "DRY_RUN"
        elif getattr(config, "EXECUTION_ENABLED", False):
            result["execution_mode"] = "LIVE"
        else:
            result["execution_mode"] = "DISABLED"

        result["symbols"] = list(getattr(config, "SYMBOLS", []))
    except Exception:
        pass

    return result
