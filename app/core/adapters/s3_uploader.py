"""
S3 Uploader Adapter — No-op compatibility bridge.

TYPE: ADAPTER
CAPABILITIES: ["noop_s3"]
STATUS: Disabled sink. All S3 writes handled by core/event_stream.py.

This module exists because core/log_router.py imports upload_event()
on every StructuredLogger.event() call. Removing the import path
would break log_router without refactoring it.

CONTRACTS:
    - upload_event() → returns True (no I/O)
    - _get_client() → returns None (no client)
    - MUST NOT use AWS SDK
    - MUST NOT perform any network I/O

REMOVE AFTER: log_router.py migrates to event_stream exclusively.
"""

from __future__ import annotations

from typing import Any


# ─── ADAPTER GUARD ────────────────────────────────────────────────────────────

def _assert_adapter_mode() -> None:
    """Verify adapter mode is active. Raises if not configured."""
    try:
        from core import config
        assert getattr(config, "ADAPTER_MODE", True), (
            "ADAPTER_MODE must be True — adapters are disabled sinks only"
        )
    except ImportError:
        pass  # Config not available = adapter mode assumed


_assert_adapter_mode()


# ─── ADAPTER METADATA ─────────────────────────────────────────────────────────

ADAPTER_TYPE = "ADAPTER"
ADAPTER_CAPABILITIES = ["noop_s3"]
ADAPTER_STATUS = "DISABLED_SINK"


# ─── PUBLIC API (no-op interface) ─────────────────────────────────────────────

def upload_event(event: dict[str, Any]) -> bool:
    """
    ADAPTER: No-op S3 sink. Returns True for caller compatibility.

    All real S3 persistence is handled by core/event_stream._s3_enqueue().
    """
    return True


def _get_client():
    """ADAPTER: Returns None. No client created."""
    return None
