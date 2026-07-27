"""
E2: Heartbeat System — Process liveness indicator.

Writes a JSON heartbeat file that represents actual bot activity
(not merely process existence). The watchdog monitors this file.

Updated:
- On startup complete
- Every scanner cycle
- Before graceful shutdown (status=SHUTDOWN)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_HEARTBEAT_DIR_DEFAULT = "runtime"
_HEARTBEAT_FILE_DEFAULT = "runtime/heartbeat.json"


def _get_heartbeat_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "HEARTBEAT_FILE", _HEARTBEAT_FILE_DEFAULT))
    except ImportError:
        return Path(_HEARTBEAT_FILE_DEFAULT)


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "HEARTBEAT_ENABLED", True))
    except ImportError:
        return True


# ─── STATUS CONSTANTS ─────────────────────────────────────────────────────────

STATUS_STARTING = "STARTING"
STATUS_RUNNING = "RUNNING"
STATUS_SHUTDOWN = "SHUTDOWN"
STATUS_DEGRADED = "DEGRADED"


# ─── HEARTBEAT WRITER ─────────────────────────────────────────────────────────

def write_heartbeat(
    *,
    status: str = STATUS_RUNNING,
    cycle_id: int = 0,
    latency_ms: int = 0,
    symbols: int = 0,
    mt5_state: str = "UNKNOWN",
    extra: dict | None = None,
) -> bool:
    """
    Write heartbeat file atomically. Never raises.

    The heartbeat represents actual bot activity — it is written
    only when the scanner loop is actively processing.

    Args:
        status: Current bot status (STARTING, RUNNING, SHUTDOWN, DEGRADED)
        cycle_id: Current scanner cycle number
        latency_ms: Last cycle latency in milliseconds
        symbols: Number of symbols being processed
        mt5_state: MT5 connection state
        extra: Optional additional metadata

    Returns:
        True if write succeeded, False otherwise.
    """
    if not _is_enabled():
        return True

    # Discord heartbeat (throttled: only on RUNNING status, every ~10 cycles via caller)
    if status == STATUS_RUNNING:
        try:
            from core.discord_notifier import send_discord
            send_discord("heartbeat", f"💓 Bot alive | cycle={cycle_id} | latency={latency_ms}ms | symbols={symbols}")
        except Exception:
            pass  # Discord failure must never affect heartbeat

    try:
        path = _get_heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "timestamp": _time.time(),
            "timestamp_iso": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "pid": os.getpid(),
            "status": status,
            "cycle_id": cycle_id,
            "latency_ms": latency_ms,
            "symbols": symbols,
            "mt5_state": mt5_state,
        }

        # Add strategy/profile info if available
        try:
            from core import config
            data["strategy"] = getattr(config, "STRATEGY_NAME", "unknown")
            data["profile"] = getattr(config, "ACTIVE_PROFILE", "none")
        except ImportError:
            pass

        if extra:
            data.update(extra)

        json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="hb_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(path))
        return True

    except Exception:
        return False


def read_heartbeat(path: Path | None = None) -> dict | None:
    """
    Read and parse the heartbeat file.

    Returns None if file doesn't exist, is corrupted, or unreadable.
    """
    try:
        p = path or _get_heartbeat_path()
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def get_heartbeat_age(path: Path | None = None) -> float | None:
    """
    Get age of heartbeat in seconds.

    Returns None if heartbeat file doesn't exist or is unreadable.
    Returns age in seconds (time since last heartbeat write).
    """
    data = read_heartbeat(path)
    if data is None:
        return None
    ts = data.get("timestamp")
    if ts is None or not isinstance(ts, (int, float)):
        return None
    return _time.time() - float(ts)
