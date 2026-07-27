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

CURRENT_SCHEMA_VERSION: int = 2

# Legacy version (pre-canonical normalisation)
SCHEMA_VERSION_LEGACY: int = 1

# Current version (canonical fields enforced)
SCHEMA_VERSION_CANONICAL: int = 2


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

# Registry of all known schemas (for future v3, v4, etc.)
SCHEMA_REGISTRY: dict[int, dict[str, Any]] = {
    1: SCHEMA_V1,
    2: SCHEMA_V2,
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
