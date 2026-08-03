"""V10 Research Schema Contract — Immutable data model definition.

This file defines the FROZEN research schema for V10 decision records.
Once data is persisted under a schema version, that version's fields
are permanent and their meanings cannot change.

SCHEMA VERSION: v10_decision_v1

EVOLUTION RULES:
  - Fields may be ADDED in v10_decision_v2 (additive only)
  - Fields may NEVER be renamed
  - Fields may NEVER be removed
  - Field meanings may NEVER change
  - IDs may NEVER be reused for different purposes
  - schema_version MUST always be present
"""

from __future__ import annotations

SCHEMA_VERSION = "v10_decision_v1"

# ═══════════════════════════════════════════════════════════════
# CRITICAL FIELDS (must ALWAYS exist — never null)
# ═══════════════════════════════════════════════════════════════

CRITICAL_FIELDS = frozenset({
    "observation_id",
    "symbol",
    "timestamp_utc",
    "engine_version",
    "schema_version",
    "final_action",
})

# For EXECUTE decisions, these must also exist:
EXECUTE_CRITICAL_FIELDS = frozenset({
    "entry_direction",
    "entry_price",
    "stop_price",
    "risk_approved",
    "execution_approved",
    "position_size",
})

# ═══════════════════════════════════════════════════════════════
# COMPLETE FIELD REGISTRY (v10_decision_v1)
# ═══════════════════════════════════════════════════════════════

SCHEMA_FIELDS = {
    # Identity
    "observation_id": {"type": "str", "required": True, "nullable": False},
    "decision_id": {"type": "str", "required": True, "nullable": False},
    "correlation_id": {"type": "str", "required": True, "nullable": False},
    "symbol": {"type": "str", "required": True, "nullable": False},
    "timestamp_utc": {"type": "float", "required": True, "nullable": False},
    "cycle_id": {"type": "int", "required": False, "nullable": True},
    "engine_version": {"type": "str", "required": True, "nullable": False},
    "schema_version": {"type": "str", "required": True, "nullable": False},

    # Final outcome
    "final_action": {"type": "str", "required": True, "nullable": False},
    "rejection_stage": {"type": "str", "required": False, "nullable": True},
    "rejection_reason": {"type": "str", "required": False, "nullable": True},

    # Market state
    "market_state": {"type": "dict", "required": True, "nullable": False},

    # Opportunity
    "opportunity": {"type": "dict", "required": True, "nullable": False},

    # Strategy
    "strategy_family": {"type": "str", "required": False, "nullable": True},
    "strategy_confidence": {"type": "float", "required": False, "nullable": True},
    "strategy_direction": {"type": "str", "required": False, "nullable": True},

    # Horizon
    "horizon": {"type": "str", "required": False, "nullable": True},
    "horizon_min_move": {"type": "float", "required": False, "nullable": True},
    "horizon_max_move": {"type": "float", "required": False, "nullable": True},
    "horizon_unit": {"type": "str", "required": False, "nullable": True},

    # Entry
    "entry_method": {"type": "str", "required": False, "nullable": True},
    "entry_direction": {"type": "str", "required": False, "nullable": True},
    "entry_status": {"type": "str", "required": False, "nullable": True},
    "entry_price": {"type": "float", "required": False, "nullable": True},
    "stop_price": {"type": "float", "required": False, "nullable": True},
    "target_price": {"type": "float", "required": False, "nullable": True},
    "risk_distance": {"type": "float", "required": False, "nullable": True},
    "reward_distance": {"type": "float", "required": False, "nullable": True},
    "expected_rr": {"type": "float", "required": False, "nullable": True},

    # Risk
    "risk_approved": {"type": "bool", "required": True, "nullable": False},
    "risk_rejection": {"type": "str", "required": False, "nullable": True},
    "risk_percentage": {"type": "float", "required": False, "nullable": True},
    "position_size": {"type": "float", "required": False, "nullable": True},

    # Execution
    "execution_approved": {"type": "bool", "required": True, "nullable": False},
    "execution_rejection": {"type": "str", "required": False, "nullable": True},
    "order_type": {"type": "str", "required": False, "nullable": True},
    "order_volume": {"type": "float", "required": False, "nullable": True},

    # Snapshots
    "account_snapshot": {"type": "dict", "required": False, "nullable": True},
    "broker_snapshot": {"type": "dict", "required": False, "nullable": True},

    # Lineage
    "lineage": {"type": "dict", "required": True, "nullable": False},
}

# ═══════════════════════════════════════════════════════════════
# S3 DATASET STRUCTURE
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# S3 DATASET STRUCTURE (LEGACY — v10/ prefix removed, now in decision_trace)
# ═══════════════════════════════════════════════════════════════

S3_DATASETS = {
    "decisions": {
        "path": "decision_trace/schema_version=decision_trace_v2/symbol={symbol}/date={date}/",
        "contains": "Every V10 evaluation (EXECUTE and NO_TRADE) — merged into decision_trace_v2",
        "join_key": "observation_id",
    },
}

# ═══════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════


def validate_decision_record(record: dict) -> tuple[bool, list[str]]:
    """
    Validate a V10 decision record against the frozen schema.

    Returns (is_valid, list_of_violations).
    """
    violations: list[str] = []

    # Schema version must match
    if record.get("schema_version") != SCHEMA_VERSION:
        violations.append(f"schema_version mismatch: got {record.get('schema_version')}, expected {SCHEMA_VERSION}")

    # Critical fields must exist and not be null
    for field in CRITICAL_FIELDS:
        if field not in record:
            violations.append(f"critical field missing: {field}")
        elif record[field] is None:
            violations.append(f"critical field is null: {field}")
        elif isinstance(record[field], str) and record[field] == "":
            violations.append(f"critical field is empty string: {field}")

    # EXECUTE-specific checks
    if record.get("final_action") == "EXECUTE":
        for field in EXECUTE_CRITICAL_FIELDS:
            if field not in record:
                violations.append(f"EXECUTE critical field missing: {field}")

    # Market state must be populated
    ms = record.get("market_state")
    if ms is None or not isinstance(ms, dict):
        violations.append("market_state missing or not a dict")

    # Opportunity must be populated
    opp = record.get("opportunity")
    if opp is None or not isinstance(opp, dict):
        violations.append("opportunity missing or not a dict")

    return len(violations) == 0, violations
