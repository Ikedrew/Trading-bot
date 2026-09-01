"""
Instance Lock — Prevents multiple live trading runtimes from operating simultaneously.

Uses a lock file with PID validation. Atomic creation prevents race conditions.
Stale lock recovery handles crash scenarios automatically.

Lock file: logs/trading.lock
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time

logger = logging.getLogger(__name__)

_LOCK_PATH = "logs/trading.lock"
_lock_acquired: bool = False


def _get_lock_path() -> str:
    try:
        from core import config
        return str(getattr(config, "INSTANCE_LOCK_PATH", _LOCK_PATH))
    except ImportError:
        return _LOCK_PATH


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: use kernel32 OpenProcess
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            # Unix: signal 0 checks existence without killing
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False
    except Exception:
        # If we can't determine, assume alive (safe — blocks startup)
        return True


def is_lock_stale() -> bool:
    """
    Check if an existing lock file is stale (PID no longer alive).
    Returns True if lock exists but the owning process is dead.
    Returns False if lock doesn't exist or PID is still alive.
    """
    lock_path = _get_lock_path()
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pid = int(data.get("pid", 0))
        if pid <= 0:
            return True  # Invalid PID = stale
        return not _is_pid_alive(pid)
    except Exception:
        # Can't read/parse = treat as stale (allow recovery)
        return True


def acquire_instance_lock() -> bool:
    """
    Attempt to acquire the instance lock.

    Returns True if lock acquired successfully.
    Returns False if another instance is already running.

    Handles stale locks automatically (dead PID → remove and acquire).
    Uses atomic file creation (os.O_CREAT | os.O_EXCL) to prevent race conditions.
    """
    global _lock_acquired
    lock_path = _get_lock_path()

    # Ensure directory exists
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)

    # Check for existing lock
    if os.path.exists(lock_path):
        if is_lock_stale():
            # Stale lock — remove and proceed
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    stale_data = json.load(f)
                stale_pid = stale_data.get("pid", "unknown")
            except Exception:
                stale_pid = "unknown"

            logger.warning(
                "[STALE_INSTANCE_LOCK] Removing stale lock pid=%s path=%s",
                stale_pid, lock_path,
            )
            try:
                os.remove(lock_path)
            except OSError:
                pass  # Will fail on atomic create below if removal failed
        else:
            # Lock is valid — another instance is running
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    active_data = json.load(f)
                active_pid = active_data.get("pid", "unknown")
            except Exception:
                active_pid = "unknown"

            logger.critical(
                "[INSTANCE_LOCK_ACTIVE] Another runtime already running "
                "pid=%s path=%s — startup blocked",
                active_pid, lock_path,
            )
            return False

    # Atomic lock creation (O_CREAT | O_EXCL = fail if file already exists)
    lock_data = json.dumps({
        "pid": os.getpid(),
        "started_at": round(time.time(), 3),
        "hostname": platform.node(),
        "process": "trading_bot",
    }, indent=2).encode("utf-8")

    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            os.write(fd, lock_data)
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        # Race condition: another process created the file between our check and create
        logger.critical(
            "[INSTANCE_LOCK_ACTIVE] Lock acquired by another process during startup race — blocked"
        )
        return False
    except OSError as exc:
        logger.critical(
            "[INSTANCE_LOCK_FAILED] Cannot create lock file path=%s error=%s",
            lock_path, exc,
        )
        return False

    _lock_acquired = True
    logger.info(
        "[INSTANCE_LOCK_ACQUIRED] pid=%d path=%s",
        os.getpid(), lock_path,
    )
    return True


def release_instance_lock() -> None:
    """
    Release the instance lock. Call on graceful shutdown.
    Only removes lock if this process owns it. Never raises.
    """
    global _lock_acquired
    if not _lock_acquired:
        return

    lock_path = _get_lock_path()
    try:
        # Verify we own the lock before removing
        if os.path.exists(lock_path):
            with open(lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if int(data.get("pid", 0)) == os.getpid():
                os.remove(lock_path)
                logger.info(
                    "[INSTANCE_LOCK_RELEASED] pid=%d path=%s",
                    os.getpid(), lock_path,
                )
            else:
                logger.warning(
                    "[INSTANCE_LOCK_RELEASE_SKIPPED] Lock owned by pid=%s, we are pid=%d",
                    data.get("pid"), os.getpid(),
                )
    except Exception as exc:
        logger.warning("[INSTANCE_LOCK_RELEASE_ERROR] error=%s", exc)
    finally:
        _lock_acquired = False
