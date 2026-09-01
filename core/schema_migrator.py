"""
Schema Migrator — Upgrades legacy (v1) events to canonical (v2) format.

Applied in two contexts:
    1. emit() pipeline: stamps schema_version on newly emitted events (always v2)
    2. read_stream() / replay: migrates historical v1 events to v2 at read-time

Design Rules:
    - NEVER overwrites historical S3 data (migration is read-time only)
    - NEVER re-derives canonical fields from raw payload (uses existing values)
    - Preserves all original fields (additive migration only)
    - Idempotent: calling migrate on a v2 event returns it unchanged
    - Never raises: gracefully handles malformed events

Usage:
    from core.schema_migrator import migrate_event, stamp_schema_version

    # At emit-time (event already has canonical fields resolved):
    event = stamp_schema_version(event)

    # At read-time (historical data may be v1):
    event = migrate_event(event)
"""

from __future__ import annotations

from typing import Any

from core.schema_registry import CURRENT_SCHEMA_VERSION
from core.production_data_contract import current_schema


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL FALLBACKS (same as _resolve_* helpers in event_stream)
# ═══════════════════════════════════════════════════════════════════════════════

_FALLBACK_UNKNOWN = "UNKNOWN"
_FALLBACK_FLAT = "FLAT"
_VALID_SIDES = frozenset({"BUY", "SELL", "FLAT"})
_VALID_GUARD_RESULTS = frozenset({"APPROVED", "REJECTED", "UNKNOWN"})


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def stamp_schema_version(event: dict[str, Any]) -> dict[str, Any]:
    """
    Stamp the current schema version on a newly emitted event.

    Called inside emit() AFTER canonical field resolution. The event
    already has all v2 fields resolved — this just adds the version tag.

    Args:
        event: Fully-resolved event dict (pattern, regime, bias, side, guard_result present)

    Returns:
        Same event dict with schema_version stamped.
    """
    event["schema_version"] = current_schema("events")
    event["event_layout_version"] = CURRENT_SCHEMA_VERSION
    return event


def migrate_event(event: dict[str, Any]) -> dict[str, Any]:
    """
    Migrate an event to the current schema version (v2).

    Behaviour:
        - If event is already v2 (schema_version == 2): return unchanged
        - If event is v1 (no schema_version, or schema_version == 1):
          normalize all canonical fields to v2 shape using fallbacks

    This is the READ-TIME migration path for historical data.
    It does NOT re-derive fields from payload — it uses whatever values
    exist at the top level, filling gaps with safe fallbacks.

    Args:
        event: Raw event dict from storage (local JSONL or S3)

    Returns:
        Event dict conforming to v2 schema. Original dict is mutated in-place.
    """
    version = event.get("event_layout_version", event.get("schema_version", 1))

    if event.get("schema_version") == current_schema("events"):
        return event

    if isinstance(version, int) and version >= CURRENT_SCHEMA_VERSION:
        event["event_layout_version"] = version
        event["schema_version"] = current_schema("events")
        return event

    # ─── v1 → v2 migration ───────────────────────────────────────────
    if version == 1:
        event = _migrate_v1_to_v2(event)

    event["event_layout_version"] = CURRENT_SCHEMA_VERSION
    event["schema_version"] = current_schema("events")

    return event


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATION FUNCTIONS (per version step)
# ═══════════════════════════════════════════════════════════════════════════════

def _migrate_v1_to_v2(event: dict[str, Any]) -> dict[str, Any]:
    """
    Migrate a v1 event to v2 canonical schema.

    Strategy:
        1. Check top-level field first (may already be set for late-v1 events)
        2. Check payload dict for the field (common in v1)
        3. Check payload.data dict (ENTITY/SCORING events)
        4. Apply fallback

    Never re-derives from candle data or recomputes patterns.
    """
    payload = event.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    data_dict = payload_dict.get("data") if isinstance(payload_dict.get("data"), dict) else {}

    # ─── pattern ──────────────────────────────────────────────────────
    event["pattern"] = _resolve_str(
        event.get("pattern"),
        payload_dict.get("pattern"),
        data_dict.get("pattern"),
        fallback=_FALLBACK_UNKNOWN,
    )

    # ─── regime ───────────────────────────────────────────────────────
    event["regime"] = _resolve_str(
        event.get("regime"),
        payload_dict.get("regime"),
        payload_dict.get("regime_state"),
        payload_dict.get("market_state"),
        data_dict.get("regime"),
        fallback=_FALLBACK_UNKNOWN,
    )

    # ─── bias ─────────────────────────────────────────────────────────
    event["bias"] = _resolve_str(
        event.get("bias"),
        payload_dict.get("bias"),
        payload_dict.get("new_bias"),
        data_dict.get("bias"),
        fallback=_FALLBACK_UNKNOWN,
    )

    # ─── side ─────────────────────────────────────────────────────────
    raw_side = _resolve_str(
        event.get("side"),
        payload_dict.get("side"),
        _nested_str(payload_dict, "signal", "side"),
        fallback=_FALLBACK_FLAT,
    )
    # Enforce constrained values
    event["side"] = raw_side if raw_side in _VALID_SIDES else _FALLBACK_FLAT

    # ─── guard_result ─────────────────────────────────────────────────
    raw_result = _resolve_str(
        event.get("guard_result"),
        payload_dict.get("result"),
        _nested_str(payload_dict, "decision", "result"),
        fallback=_FALLBACK_UNKNOWN,
    )
    # Enforce constrained values
    event["guard_result"] = raw_result if raw_result in _VALID_GUARD_RESULTS else _FALLBACK_UNKNOWN

    # ─── Stamp version ────────────────────────────────────────────────
    event["schema_version"] = 2

    return event


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_str(*candidates: Any, fallback: str = _FALLBACK_UNKNOWN) -> str:
    """
    Return the first valid non-empty string from candidates, or fallback.

    Filters out None, empty strings, whitespace, and non-string types.
    """
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return fallback


def _nested_str(d: dict[str, Any], outer_key: str, inner_key: str) -> Any:
    """Safely extract a nested dict value. Returns None on any failure."""
    nested = d.get(outer_key)
    if isinstance(nested, dict):
        return nested.get(inner_key)
    return None
