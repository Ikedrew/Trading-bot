"""
Central Shutdown Flag — Single source of truth for graceful shutdown.

All modules check is_shutdown_requested() instead of importing main.
Thread-safe via GIL (bool assignment is atomic in CPython).
"""

from __future__ import annotations

_shutdown_requested: bool = False
_shutdown_reason: str = ""


def request_shutdown(reason: str = "signal") -> None:
    """Request graceful shutdown. Safe to call from any thread or signal handler."""
    global _shutdown_requested, _shutdown_reason
    if _shutdown_requested:
        return  # Already requested
    _shutdown_requested = True
    _shutdown_reason = reason
    print(f"[SHUTDOWN REQUESTED] reason={reason}")


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested. Fast, no side effects."""
    return _shutdown_requested


def get_shutdown_reason() -> str:
    """Get the reason shutdown was requested."""
    return _shutdown_reason


def interruptible_sleep(duration: float, step: float = 0.1) -> None:
    """
    Sleep that checks shutdown flag every `step` seconds.
    Returns early if shutdown is requested. Never blocks longer than `step`.
    """
    import time
    elapsed = 0.0
    while elapsed < duration:
        if _shutdown_requested:
            return
        time.sleep(min(step, duration - elapsed))
        elapsed += step
