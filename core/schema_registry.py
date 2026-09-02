"""
Schema Registry — Canonical schema definitions for all event versions.

Defines the structure expectations for each schema version. Used by:
    - schema_migrator.py (to upgrade events between versions)
    - event_schema_contract.py (to validate the current version)
    - Athena DDL generators (to create/update table schemas)
    - Replay engine (to detect and migrate historical events)

Version History:
    v1 (legacy): Optional/inconsistent canonical fields. Events may lack
       pattern, regime, bias, side, guard_result at the top level.
       Historical data in S3 from before canonical normalisation.

    v2 (current): All canonical fields are guaranteed present as non-empty
       strings at the top level. Resolved once at emit-time. Immutable
       after write. No JSON extraction needed in Athena.

Rules:
    - Schema versions are monotonically increasing integers
    - New versions MUST be backward-compatible at read-time (via migrator)
    - Historical S3 data is NEVER overwritten — migration happens at read-time
    - emit() always writes the CURRENT_SCHEMA_VERSION
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# VERSION CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# CLEAN V1 BASELINE: the active canonical event layout generation starts at 1.
# (Historical development reached generation 3; that history is NOT preserved in
# the new canonical dataset — old data is being wiped. The current observation-only
# structure IS the generation-1 canonical layout.)
CURRENT_SCHEMA_VERSION: int = 1

# Legacy version (pre-canonical normalisation) — retained only for read-time
# migration of pre-wipe historical data; never emitted on the new baseline.
SCHEMA_VERSION_LEGACY: int = 1

# Current canonical generation (observation-only, decision fields removed).
SCHEMA_VERSION_CANONICAL: int = 1


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA_V1: dict[str, Any] = {
    "description": "Legacy schema — optional/inconsistent canonical fields",
    "version": 1,
    "fields": {
        "ts_utc_ms": {"type": "int", "required": True},
        "type": {"type": "str", "required": True},
        "symbol": {"type": "str", "required": False},
        "payload": {"type": "dict", "required": False},
        "source": {"type": "str", "required": False},
        # Canonical fields are OPTIONAL in v1
        "pattern": {"type": "str", "required": False, "note": "inconsistent — may be missing or nested"},
        "regime": {"type": "str", "required": False, "note": "optional — may be absent"},
        "bias": {"type": "str", "required": False, "note": "optional — may be absent"},
        "side": {"type": "str", "required": False, "note": "optional — may be absent"},
        "guard_result": {"type": "str", "required": False, "note": "did not exist in v1"},
    },
    "guarantees": [
        "ts_utc_ms always present",
        "type always present",
        "payload may or may not contain canonical fields",
    ],
}

SCHEMA_V2: dict[str, Any] = {
    "description": "Canonical schema — all trading features resolved at emit-time",
    "version": 2,
    "fields": {
        # Core identifiers
        "ts_utc_ms": {"type": "int", "required": True},
        "type": {"type": "str", "required": True},
        "symbol": {"type": "str", "required": False},
        "source": {"type": "str", "required": False},
        "schema_version": {"type": "int", "required": True, "value": 2},
        # Immutable canonical fields (NEVER None, NEVER empty)
        "pattern": {"type": "str", "required": True, "fallback": "UNKNOWN"},
        "regime": {"type": "str", "required": True, "fallback": "UNKNOWN"},
        "bias": {"type": "str", "required": True, "fallback": "UNKNOWN"},
        "side": {"type": "str", "required": True, "fallback": "FLAT", "allowed": ["BUY", "SELL", "FLAT"]},
        "guard_result": {"type": "str", "required": True, "fallback": "UNKNOWN", "allowed": ["APPROVED", "REJECTED", "UNKNOWN"]},
        # Raw payload (debugging only)
        "payload": {"type": "dict", "required": False, "note": "opaque to analytics"},
    },
    "guarantees": [
        "All canonical fields are top-level non-empty strings",
        "side ∈ {BUY, SELL, FLAT}",
        "guard_result ∈ {APPROVED, REJECTED, UNKNOWN}",
        "pattern, regime, bias always have string value (fallback UNKNOWN)",
        "schema_version = 2 always present",
        "No JSON extraction required for Athena queries",
    ],
}

SCHEMA_V3: dict[str, Any] = {
    "description": "Observation-only schema — legacy decision fields removed",
    "version": 3,
    "fields": {
        # Core identifiers
        "ts_utc_ms": {"type": "int", "required": True},
        "type": {"type": "str", "required": True},
        "symbol": {"type": "str", "required": False},
        "source": {"type": "str", "required": False},
        "schema_version": {"type": "int", "required": True, "value": 3},
        "feature_version": {"type": "str", "required": False},
        # Raw payload (type-specific observation data)
        "payload": {"type": "dict", "required": False},
    },
    "guarantees": [
        "Only observation event types reach disk (CANDLE, FEATURE_UPDATE, FEED_HEALTH, DATA_GAP, RECONNECT, SYSTEM_HEALTH, CLOCK_SYNC)",
        "No decision/trading fields (pattern, regime, bias, side, guard_result removed)",
        "schema_version = 3 always present",
        "payload contains type-specific observation data",
    ],
}

# ─── CANONICAL V1 (clean baseline) ────────────────────────────────────────────
# The active canonical event layout for the fresh baseline. Structurally identical
# to the observation-only shape (formerly generation 3) but numbered generation 1:
# canonical generation begins here.
SCHEMA_V1_CANONICAL: dict[str, Any] = {
    "description": "Canonical V1 — observation-only event layout (clean baseline)",
    "version": 1,
    "fields": {
        "ts_utc_ms": {"type": "int", "required": True},
        "type": {"type": "str", "required": True},
        "symbol": {"type": "str", "required": False},
        "source": {"type": "str", "required": False},
        "schema_version": {"type": "str", "required": True, "value": "events_v1"},
        "event_layout_version": {"type": "int", "required": True, "value": 1},
        "feature_version": {"type": "int", "required": False},
        "payload": {"type": "dict", "required": False},
    },
    "guarantees": [
        "Only observation event types reach disk (CANDLE, FEATURE_UPDATE, FEED_HEALTH, DATA_GAP, RECONNECT, SYSTEM_HEALTH, CLOCK_SYNC)",
        "No decision/trading fields",
        "event_layout_version = 1 (clean canonical generation)",
        "schema_version = events_v1 always present",
    ],
}

# Registry of schemas. Key 1 is the active canonical baseline. Keys _LEGACY2/_LEGACY3
# retain the pre-wipe historical definitions ONLY for read-time migration of old
# (disposable) data — they are never emitted on the new baseline.
SCHEMA_REGISTRY: dict[int, dict[str, Any]] = {
    1: SCHEMA_V1_CANONICAL,
}
# Historical (pre-baseline) definitions, retained for read-time migration only.
LEGACY_SCHEMA_DEFINITIONS: dict[int, dict[str, Any]] = {
    1: SCHEMA_V1,   # pre-canonical legacy
    2: SCHEMA_V2,
    3: SCHEMA_V3,
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER API
# ═══════════════════════════════════════════════════════════════════════════════

def get_schema(version: int) -> dict[str, Any] | None:
    """Get schema definition by version number."""
    return SCHEMA_REGISTRY.get(version)


def get_current_schema() -> dict[str, Any]:
    """Get the current active schema definition."""
    return SCHEMA_REGISTRY[CURRENT_SCHEMA_VERSION]


def detect_schema_version(event: dict[str, Any]) -> int:
    """
    Detect the schema version of an event.

    Uses explicit schema_version field if present, otherwise infers
    from the presence/absence of canonical fields.
    """
    # Explicit version tag (v2+ events always have this)
    explicit = event.get("schema_version")
    if isinstance(explicit, int) and explicit in SCHEMA_REGISTRY:
        return explicit

    # Inference: if all v2 canonical fields exist as non-empty strings → v2
    v2_fields = ("pattern", "regime", "bias", "side", "guard_result")
    all_present = all(
        isinstance(event.get(f), str) and event.get(f, "").strip()
        for f in v2_fields
    )
    if all_present:
        return 2

    # Default: legacy
    return 1
