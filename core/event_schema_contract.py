"""
Event Schema Contract — Canonical structure for all trading bot events.

This module defines the formal, enforceable schema that every event written
via event_stream.emit() must satisfy. It is the single source of truth for
the event structure consumed by Athena, replay engines, and analytics.

Design Principles:
    - Canonical fields are resolved ONCE at emit-time (write-once, read-everywhere)
    - No downstream system may modify, re-derive, or recompute canonical fields
    - Athena queries use top-level columns directly (no json_extract_scalar)
    - Replay and production share identical schemas (deterministic parity)

Usage:
    from core.event_schema_contract import validate_canonical_event

    # Called inside emit() after field resolution, before write
    validate_canonical_event(event)

Non-goals (explicitly excluded from canonical schema):
    - Raw candle data (OHLCV)
    - Indicators (RSI, ATR, etc.)
    - Intermediate signals
    - Microstructure features (spread, tick velocity)
    Only decision-level features are canonicalised.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL FIELD DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Core identifiers (always present) ────────────────────────────────────────
CORE_FIELDS: tuple[str, ...] = (
    "ts_utc_ms",    # int — epoch milliseconds (monotonic clock)
    "type",         # str — event type enum value
)

# ─── Immutable trading features (write-once at emit, never recomputed) ────────
# These are resolved inside emit() via _resolve_* helpers.
# After serialisation, no system may modify these.
IMMUTABLE_FIELDS: tuple[str, ...] = (
    "pattern",       # str — trading pattern name (e.g. "BULLISH_ENGULFING")
    "regime",        # str — market regime (e.g. "TREND_UP", "RANGING")
    "bias",          # str — directional bias (e.g. "BUY", "SELL")
    "side",          # str — signal direction: "BUY" | "SELL" | "FLAT"
    "guard_result",  # str — risk gate outcome: "APPROVED" | "REJECTED" | "UNKNOWN"
)

# ─── Decision metrics (finalised at emit-time, nullable for non-decision events)
DECISION_METRICS: tuple[str, ...] = (
    "ev",             # float | None — expected value
    "score",          # float | None — confluence score
    "rr",             # float | None — risk/reward ratio
    "risk",           # float | None — risk amount
    "position_size",  # float | None — lot size / position volume
)

# ─── Traceability fields (nullable, for forensic linking) ─────────────────────
TRACEABILITY_FIELDS: tuple[str, ...] = (
    "entity_id",  # str | None — opportunity entity UUID
    "cycle_id",   # int | None — scanner cycle number
)

# ─── Raw payload (debugging only — NOT for Athena analytics) ──────────────────
RAW_FIELDS: tuple[str, ...] = (
    "payload",  # dict — event-specific data (opaque to analytics layer)
)

# ─── Allowed values for constrained fields ────────────────────────────────────
VALID_SIDE_VALUES: frozenset[str] = frozenset({"BUY", "SELL", "FLAT"})
VALID_GUARD_RESULT_VALUES: frozenset[str] = frozenset({"APPROVED", "REJECTED", "UNKNOWN"})


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class SchemaViolation(ValueError):
    """Raised when an event violates the canonical schema contract."""
    pass


def validate_canonical_event(event: dict[str, Any]) -> None:
    """
    Validate that an event satisfies the canonical schema contract.

    Called inside emit() after canonical field resolution, before write.
    Raises SchemaViolation on any contract breach.

    Checks:
        1. All immutable fields exist and are non-empty strings
        2. 'side' is one of: BUY, SELL, FLAT
        3. 'guard_result' is one of: APPROVED, REJECTED, UNKNOWN
        4. Core identifiers exist (ts_utc_ms, type)
        5. No immutable field is None, dict, list, or empty string
        6. schema_version is present and is an int

    Args:
        event: The fully-resolved event dict ready for serialisation.

    Raises:
        SchemaViolation: If any contract rule is violated.
    """
    # ─── Core identifiers ─────────────────────────────────────────────
    if "ts_utc_ms" not in event:
        raise SchemaViolation("[SCHEMA VIOLATION] missing required field: ts_utc_ms")
    if not isinstance(event["ts_utc_ms"], int):
        raise SchemaViolation(
            f"[SCHEMA VIOLATION] ts_utc_ms must be int, got {type(event['ts_utc_ms']).__name__}"
        )

    if "type" not in event:
        raise SchemaViolation("[SCHEMA VIOLATION] missing required field: type")
    if not isinstance(event["type"], str) or not event["type"].strip():
        raise SchemaViolation("[SCHEMA VIOLATION] type must be a non-empty string")

    # ─── Schema version ───────────────────────────────────────────────
    if "schema_version" not in event:
        raise SchemaViolation("[SCHEMA VIOLATION] missing required field: schema_version")
    if not isinstance(event["schema_version"], int):
        raise SchemaViolation(
            f"[SCHEMA VIOLATION] schema_version must be int, got {type(event['schema_version']).__name__}"
        )

    # ─── Immutable trading features ───────────────────────────────────
    for field in IMMUTABLE_FIELDS:
        if field not in event:
            raise SchemaViolation(f"[SCHEMA VIOLATION] missing immutable field: {field}")

        value = event[field]

        # Must be a string
        if not isinstance(value, str):
            raise SchemaViolation(
                f"[SCHEMA VIOLATION] {field} must be str, got {type(value).__name__}: {value!r}"
            )

        # Must not be empty or whitespace
        if not value.strip():
            raise SchemaViolation(
                f"[SCHEMA VIOLATION] {field} must not be empty/blank, got: {value!r}"
            )

    # ─── Constrained value checks ────────────────────────────────────
    side = event["side"]
    if side not in VALID_SIDE_VALUES:
        raise SchemaViolation(
            f"[SCHEMA VIOLATION] side must be one of {sorted(VALID_SIDE_VALUES)}, got: {side!r}"
        )

    guard_result = event["guard_result"]
    if guard_result not in VALID_GUARD_RESULT_VALUES:
        raise SchemaViolation(
            f"[SCHEMA VIOLATION] guard_result must be one of "
            f"{sorted(VALID_GUARD_RESULT_VALUES)}, got: {guard_result!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT SUMMARY (for documentation / external consumers)
# ═══════════════════════════════════════════════════════════════════════════════

def get_schema_summary() -> dict[str, Any]:
    """
    Return a machine-readable summary of the canonical event schema.

    Useful for:
        - Athena table DDL generation
        - Schema documentation
        - Contract testing
    """
    return {
        "core_fields": {
            "ts_utc_ms": {"type": "int", "nullable": False, "description": "Epoch milliseconds"},
            "type": {"type": "str", "nullable": False, "description": "Event type enum"},
            "symbol": {"type": "str", "nullable": True, "description": "Trading symbol"},
            "source": {"type": "str", "nullable": True, "description": "Emitting module"},
        },
        "immutable_fields": {
            "pattern": {"type": "str", "nullable": False, "fallback": "UNKNOWN"},
            "regime": {"type": "str", "nullable": False, "fallback": "UNKNOWN"},
            "bias": {"type": "str", "nullable": False, "fallback": "UNKNOWN"},
            "side": {"type": "str", "nullable": False, "fallback": "FLAT", "allowed": sorted(VALID_SIDE_VALUES)},
            "guard_result": {"type": "str", "nullable": False, "fallback": "UNKNOWN", "allowed": sorted(VALID_GUARD_RESULT_VALUES)},
        },
        "decision_metrics": {
            "ev": {"type": "float", "nullable": True, "description": "Expected value"},
            "score": {"type": "float", "nullable": True, "description": "Confluence score"},
            "rr": {"type": "float", "nullable": True, "description": "Risk/reward ratio"},
            "risk": {"type": "float", "nullable": True, "description": "Risk amount"},
            "position_size": {"type": "float", "nullable": True, "description": "Position volume"},
        },
        "traceability": {
            "entity_id": {"type": "str", "nullable": True, "description": "Opportunity entity UUID"},
            "cycle_id": {"type": "int", "nullable": True, "description": "Scanner cycle number"},
        },
        "raw_fields": {
            "payload": {"type": "dict", "nullable": True, "description": "Debug-only event data"},
        },
        "immutability_rule": (
            "After emit() writes the event, no downstream system may modify "
            "pattern, regime, bias, side, or guard_result. These are stored facts."
        ),
    }
