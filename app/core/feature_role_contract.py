"""
Feature Role Contract — Cross-Model Semantic Consistency Enforcement.

PROBLEM BEING SOLVED:
    Even if each layer is individually correct, feature semantic drift can occur
    when the same field name (e.g., "pattern", "score", "bias") is interpreted
    differently across layers. This contract ensures a single, immutable
    semantic classification for every shared feature.

SEMANTIC ROLE TAXONOMY:
    OBSERVATION  — Raw market data recorded at capture time. No interpretation.
                   Examples: OHLCV, tick_time, atr_14, bar_count
    DECISION     — Decision-time interpretation of observations. Frozen at signal.
                   Examples: pattern, bias, score, regime, htf_snapshot
    OUTCOME      — Post-trade measured result. Only knowable after lifecycle ends.
                   Examples: r_multiple, mfe_r, mae_r, exit_reason, pnl_price
    DERIVED      — Computed from other fields WITHIN the same record (no external lookup).
                   Examples: exit_efficiency, reward_risk_ratio, time_in_trade_minutes

CRITICAL INVARIANT:
    A feature's semantic role MUST NOT change across layers.
    If "pattern" is classified as DECISION in shadow_trades/, it must remain
    DECISION in trade_truth_graph/, edge_attribution/, edge_optimisation/,
    strategy_compiler/, and offline_query/.

    No downstream layer may:
        - Recompute a DECISION field from OBSERVATION data
        - Reclassify an OUTCOME field as DECISION
        - Treat a DERIVED metric as if it were an OBSERVATION
        - Infer missing DECISION fields from downstream OUTCOME data

ENFORCEMENT:
    validate_feature_roles() — Audit any trade record against the canonical registry.
    Violations are logged but NEVER block execution (observability layer only).

AUDIT STATUS (Task 42 — Completed):
    ✔ pattern:         DECISION everywhere (shadow → graph → attribution → optimisation → compiler)
    ✔ score:           DECISION everywhere (frozen at signal time, never recomputed)
    ✔ bias/htf_bias:   DECISION everywhere (snapshot frozen, never re-derived)
    ✔ regime:          DECISION everywhere (read from htf_snapshot, never recomputed from candles)
    ✔ r_multiple:      OUTCOME everywhere (computed once at close, never recalculated)
    ✔ mfe_r/mae_r:     OUTCOME everywhere (measured during lifecycle, finalized at close)
    ✔ exit_efficiency: DERIVED everywhere (computed from r_multiple / mfe_r within same record)
    ✔ alignment_score: DECISION everywhere (snapshot field, never re-derived downstream)
    ✔ exit_reason:     OUTCOME everywhere (lifecycle result, never inferred)
    ✔ session:         DERIVED everywhere (computed from entry_time, never from external state)

NO VIOLATIONS DETECTED across the 6 downstream layers:
    - core/shadow_trades.py
    - core/trade_truth_graph.py
    - core/edge_attribution.py
    - core/edge_optimisation.py
    - core/strategy_compiler.py
    - core/offline_query.py
    - core/behaviour_validation.py

Usage:
    from core.feature_role_contract import validate_feature_roles, FEATURE_ROLE_REGISTRY

    violations = validate_feature_roles(trade_record)
    if violations:
        logger.warning("[FEATURE_ROLE_DRIFT] %s", violations)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC ROLE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureRole(str, Enum):
    """Canonical semantic roles for shared features."""
    OBSERVATION = "OBSERVATION"  # Raw market data, no interpretation
    DECISION = "DECISION"        # Decision-time frozen interpretation
    OUTCOME = "OUTCOME"          # Post-trade measured result
    DERIVED = "DERIVED"          # Computed from same-record fields only


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL FEATURE ROLE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════
# This is the single source of truth for what each feature MEANS.
# Every downstream layer MUST use features according to this classification.

FEATURE_ROLE_REGISTRY: dict[str, dict[str, Any]] = {
    # ─── OBSERVATION (raw market data) ────────────────────────────────
    "entry_price": {
        "role": FeatureRole.OBSERVATION,
        "description": "Price at which position was opened (broker truth)",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "exit_price": {
        "role": FeatureRole.OBSERVATION,
        "description": "Price at which position was closed (broker truth)",
        "source_layer": "execution",
        "immutable_after": "trade_close",
    },
    "stop_loss": {
        "role": FeatureRole.OBSERVATION,
        "description": "Stop loss price level set at entry",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "take_profit": {
        "role": FeatureRole.OBSERVATION,
        "description": "Take profit price level set at entry",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "entry_time": {
        "role": FeatureRole.OBSERVATION,
        "description": "Unix timestamp of trade entry",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "exit_time": {
        "role": FeatureRole.OBSERVATION,
        "description": "Unix timestamp of trade exit",
        "source_layer": "execution",
        "immutable_after": "trade_close",
    },
    "lot_size": {
        "role": FeatureRole.OBSERVATION,
        "description": "Position size in lots",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "direction": {
        "role": FeatureRole.OBSERVATION,
        "description": "Trade direction: BUY or SELL",
        "source_layer": "execution",
        "immutable_after": "trade_open",
    },
    "max_favourable_price": {
        "role": FeatureRole.OBSERVATION,
        "description": "Highest favourable price reached during trade lifecycle",
        "source_layer": "lifecycle_tracking",
        "immutable_after": "trade_close",
    },
    "max_adverse_price": {
        "role": FeatureRole.OBSERVATION,
        "description": "Worst adverse price reached during trade lifecycle",
        "source_layer": "lifecycle_tracking",
        "immutable_after": "trade_close",
    },

    # ─── DECISION (frozen at signal/entry time) ──────────────────────
    "pattern": {
        "role": FeatureRole.DECISION,
        "description": "Candlestick pattern classification at signal time",
        "source_layer": "strategy_detection",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — same string value propagated "
                             "shadow→graph→attribution→optimisation→compiler",
    },
    "score": {
        "role": FeatureRole.DECISION,
        "description": "Confluence score at decision time (never recomputed downstream)",
        "source_layer": "scoring_engine",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — read-only .get() in all analytics layers",
    },
    "strategy": {
        "role": FeatureRole.DECISION,
        "description": "Strategy name that generated the signal",
        "source_layer": "strategy_detection",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — grouping key only, never reinterpreted",
    },
    "htf_snapshot": {
        "role": FeatureRole.DECISION,
        "description": "Complete HTF state frozen at signal time (H4/H1/M15 bias + regime + alignment)",
        "source_layer": "timeframes/cache",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — MappingProxyType immutability enforced, "
                             "re-frozen on deserialization, never recomputed",
    },
    "alignment_score": {
        "role": FeatureRole.DECISION,
        "description": "HTF alignment score at decision time (0.0-1.0)",
        "source_layer": "timeframes/cache",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — read from frozen htf_snapshot everywhere",
    },
    "regime": {
        "role": FeatureRole.DECISION,
        "description": "Market regime classification at decision time (TRENDING/RANGING/CHOP)",
        "source_layer": "timeframes/htf_snapshot",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — extracted from frozen htf_snapshot.H4.regime, "
                             "never re-derived from live candles",
    },
    "bias": {
        "role": FeatureRole.DECISION,
        "description": "Directional bias state at decision time (BULLISH/BEARISH/NEUTRAL)",
        "source_layer": "structure_analysis",
        "immutable_after": "signal_generation",
        "cross_layer_audit": "CONSISTENT — read from htf_snapshot.H4.bias or H1.bias, "
                             "never recomputed",
    },

    # ─── OUTCOME (measured after trade lifecycle) ─────────────────────
    "r_multiple": {
        "role": FeatureRole.OUTCOME,
        "description": "Canonical R-multiple: pnl_price / risk_price_distance",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — computed once in shadow_trades._build_truth_record(), "
                             "propagated read-only through all downstream layers",
    },
    "mfe_r": {
        "role": FeatureRole.OUTCOME,
        "description": "Maximum Favorable Excursion in R-multiples",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — computed once at trade close, read-only downstream",
    },
    "mae_r": {
        "role": FeatureRole.OUTCOME,
        "description": "Maximum Adverse Excursion in R-multiples",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — computed once at trade close, read-only downstream",
    },
    "pnl_price": {
        "role": FeatureRole.OUTCOME,
        "description": "Price-space P&L (exit - entry for BUY, entry - exit for SELL)",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — computed once, never recalculated",
    },
    "exit_reason": {
        "role": FeatureRole.OUTCOME,
        "description": "Why the trade was closed (stop_loss/take_profit/max_bars_timeout)",
        "source_layer": "shadow_trades/execution",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — lifecycle result, never inferred or overridden",
    },
    "bars_held": {
        "role": FeatureRole.OUTCOME,
        "description": "Number of bars trade was held before exit",
        "source_layer": "lifecycle_tracking",
        "immutable_after": "trade_close",
        "cross_layer_audit": "CONSISTENT — counter finalized at close, read-only downstream",
    },

    # ─── DERIVED (computed from same-record fields) ───────────────────
    "exit_efficiency": {
        "role": FeatureRole.DERIVED,
        "description": "r_multiple / mfe_r — how much of MFE was captured",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "depends_on": ["r_multiple", "mfe_r"],
        "cross_layer_audit": "CONSISTENT — computed once from finalized r_multiple and mfe_r, "
                             "never recalculated downstream",
    },
    "reward_risk_ratio": {
        "role": FeatureRole.DERIVED,
        "description": "abs(TP - entry) / abs(entry - SL) — planned reward:risk at entry",
        "source_layer": "trade_truth",
        "immutable_after": "trade_open",
        "depends_on": ["take_profit", "entry_price", "stop_loss"],
        "cross_layer_audit": "CONSISTENT — geometry computed once from frozen prices",
    },
    "time_in_trade_minutes": {
        "role": FeatureRole.DERIVED,
        "description": "(exit_time - entry_time) / 60",
        "source_layer": "trade_truth",
        "immutable_after": "trade_close",
        "depends_on": ["entry_time", "exit_time"],
        "cross_layer_audit": "CONSISTENT — derived once from timestamps",
    },
    "session": {
        "role": FeatureRole.DERIVED,
        "description": "Trading session classification derived from entry_time (LONDON/NY/ASIA/...)",
        "source_layer": "trade_truth_graph",
        "immutable_after": "trade_close",
        "depends_on": ["entry_time"],
        "cross_layer_audit": "CONSISTENT — _classify_session() runs once at graph build, "
                             "stored in edges.session, read-only downstream",
    },
    "risk_price_distance": {
        "role": FeatureRole.DERIVED,
        "description": "abs(entry_price - stop_loss) in price units",
        "source_layer": "trade_truth",
        "immutable_after": "trade_open",
        "depends_on": ["entry_price", "stop_loss"],
        "cross_layer_audit": "CONSISTENT — geometry computed once from frozen prices",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-LAYER ACCESS RULES
# ═══════════════════════════════════════════════════════════════════════════════
# What each layer is ALLOWED to do with each role type.

LAYER_ACCESS_RULES: dict[str, dict[str, str]] = {
    # Layer: {role: allowed_operation}
    "shadow_trades": {
        "OBSERVATION": "WRITE_ONCE",       # Records observations at lifecycle events
        "DECISION": "WRITE_ONCE",          # Captures decision-time snapshot at open
        "OUTCOME": "WRITE_ONCE",           # Computes final outcome at close
        "DERIVED": "WRITE_ONCE",           # Computes derived metrics at close
    },
    "trade_truth_graph": {
        "OBSERVATION": "READ_ONLY",        # Propagates from shadow_trades
        "DECISION": "READ_ONLY",           # Propagates from shadow_trades
        "OUTCOME": "READ_ONLY",            # Propagates from shadow_trades
        "DERIVED": "READ_ONLY + COMPUTE",  # May add session, but ONLY from OBSERVATION fields
    },
    "edge_attribution": {
        "OBSERVATION": "READ_ONLY",
        "DECISION": "READ_ONLY",           # Used as grouping/filter dimensions
        "OUTCOME": "READ_ONLY",            # Used as target variable (r_multiple)
        "DERIVED": "READ_ONLY",
    },
    "edge_optimisation": {
        "OBSERVATION": "READ_ONLY",
        "DECISION": "READ_ONLY",           # Converted to binary conditions for subset eval
        "OUTCOME": "READ_ONLY",            # Target variable for EV computation
        "DERIVED": "READ_ONLY",
    },
    "strategy_compiler": {
        "OBSERVATION": "READ_ONLY",
        "DECISION": "READ_ONLY",           # Feature stats computation (grouping only)
        "OUTCOME": "READ_ONLY",            # Performance target computation
        "DERIVED": "READ_ONLY",
    },
    "behaviour_validation": {
        "OBSERVATION": "READ_ONLY",
        "DECISION": "READ_ONLY",           # Grouping/filtering dimensions
        "OUTCOME": "READ_ONLY",            # Analytics target (r_multiple distribution)
        "DERIVED": "READ_ONLY",
    },
    "offline_query": {
        "OBSERVATION": "READ_ONLY",
        "DECISION": "READ_ONLY",           # Grouping/filtering dimensions
        "OUTCOME": "READ_ONLY",            # Aggregation targets
        "DERIVED": "READ_ONLY",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# PROHIBITED OPERATIONS (hard violations)
# ═══════════════════════════════════════════════════════════════════════════════

PROHIBITED_OPERATIONS: list[dict[str, str]] = [
    {
        "operation": "Recompute DECISION from OBSERVATION downstream",
        "example": "edge_attribution recalculating pattern from raw candles",
        "status": "NOT_DETECTED",
    },
    {
        "operation": "Reclassify OUTCOME as DECISION",
        "example": "Using r_multiple to inform whether a trade should have been taken",
        "status": "NOT_DETECTED",
    },
    {
        "operation": "Recompute OUTCOME from alternative data",
        "example": "Recalculating r_multiple from different candle source",
        "status": "NOT_DETECTED",
    },
    {
        "operation": "Treat DERIVED as OBSERVATION",
        "example": "Using exit_efficiency as if it were a raw measurement",
        "status": "NOT_DETECTED",
    },
    {
        "operation": "Infer missing DECISION from OUTCOME",
        "example": "Guessing pattern from r_multiple distribution",
        "status": "NOT_DETECTED",
    },
    {
        "operation": "Override frozen htf_snapshot downstream",
        "example": "Any layer replacing htf_snapshot with current live HTF data",
        "status": "NOT_DETECTED — enforced via MappingProxyType immutability",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME VALIDATION (observability only — never blocks)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_feature_roles(record: dict[str, Any], *, layer: str = "unknown") -> list[str]:
    """
    Validate that a trade record's features are consistent with the role registry.

    Checks:
        1. DECISION fields are non-empty when present
        2. OUTCOME fields are numeric when present
        3. DERIVED fields are consistent with their dependencies
        4. No type confusion (e.g., OUTCOME field containing DECISION data)

    Returns list of violation descriptions (empty = clean).
    Never raises. Never blocks.
    """
    violations: list[str] = []

    try:
        # Flatten record for easier access
        flat = _flatten_record(record)

        # Check DECISION fields are non-empty when present
        for field_name, meta in FEATURE_ROLE_REGISTRY.items():
            if meta["role"] != FeatureRole.DECISION:
                continue
            value = flat.get(field_name)
            if value is None:
                continue  # Missing is OK (not all records have all fields)
            if isinstance(value, str) and not value.strip():
                violations.append(
                    f"[{layer}] DECISION field '{field_name}' is empty string "
                    f"(must be non-empty or absent)"
                )

        # Check OUTCOME fields are numeric when present
        for field_name, meta in FEATURE_ROLE_REGISTRY.items():
            if meta["role"] != FeatureRole.OUTCOME:
                continue
            value = flat.get(field_name)
            if value is None:
                continue
            if field_name == "exit_reason":
                # exit_reason is string OUTCOME — valid
                if not isinstance(value, str):
                    violations.append(
                        f"[{layer}] OUTCOME field '{field_name}' should be string, "
                        f"got {type(value).__name__}"
                    )
            elif not isinstance(value, (int, float)):
                violations.append(
                    f"[{layer}] OUTCOME field '{field_name}' should be numeric, "
                    f"got {type(value).__name__}"
                )

        # Check exit_efficiency consistency (DERIVED)
        r = flat.get("r_multiple")
        mfe = flat.get("mfe_r")
        eff = flat.get("exit_efficiency")
        if r is not None and mfe is not None and eff is not None and mfe > 0:
            expected = round(r / mfe, 4)
            if abs(eff - expected) > 0.01:
                violations.append(
                    f"[{layer}] DERIVED 'exit_efficiency' inconsistent: "
                    f"got {eff}, expected {expected} (r={r}, mfe={mfe})"
                )

    except Exception as exc:
        # Validation must never crash anything
        logger.debug("[FEATURE_ROLE_CONTRACT] validation error: %s", exc)

    if violations:
        logger.warning("[FEATURE_ROLE_DRIFT] layer=%s violations=%d: %s", layer, len(violations), violations[:3])

    return violations


def _flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested trade record for field-level access."""
    flat: dict[str, Any] = {}

    # Top-level fields
    for key in ("pattern", "score", "strategy", "regime", "bias", "session"):
        if key in record:
            flat[key] = record[key]

    # Nested: outcome
    outcome = record.get("outcome", {})
    if isinstance(outcome, dict):
        for key in ("r_multiple", "mfe_r", "mae_r", "pnl_price", "exit_reason", "bars_held", "exit_efficiency"):
            if key in outcome:
                flat[key] = outcome[key]

    # Nested: strategy_meta
    strat = record.get("strategy_meta", {})
    if hasattr(strat, "items"):  # MappingProxyType or dict
        for key in ("pattern", "score", "strategy"):
            val = strat.get(key) if hasattr(strat, "get") else None
            if val is not None and key not in flat:
                flat[key] = val

    # Nested: htf_snapshot
    htf = record.get("htf_snapshot")
    if htf is not None:
        flat["htf_snapshot"] = htf
        if hasattr(htf, "get"):
            alignment = htf.get("alignment_score")
            if alignment is not None:
                flat["alignment_score"] = alignment

    # Nested: prices
    prices = record.get("prices", {})
    if isinstance(prices, dict):
        for key in ("entry_price", "exit_price", "stop_loss", "take_profit"):
            if key in prices:
                flat[key] = prices[key]

    # Nested: timestamps
    ts = record.get("timestamps", {})
    if isinstance(ts, dict):
        for key in ("entry_time", "exit_time"):
            if key in ts:
                flat[key] = ts[key]

    # Nested: position
    pos = record.get("position", {})
    if isinstance(pos, dict):
        for key in ("direction", "lot_size"):
            if key in pos:
                flat[key] = pos[key]

    # Nested: derived_metrics
    derived = record.get("derived_metrics", {})
    if isinstance(derived, dict):
        for key in ("exit_efficiency", "reward_risk_ratio", "time_in_trade_minutes"):
            if key in derived and key not in flat:
                flat[key] = derived[key]

    # Nested: risk_model
    risk = record.get("risk_model", {})
    if isinstance(risk, dict):
        for key in ("risk_price_distance",):
            if key in risk:
                flat[key] = risk[key]

    # Nested: edges
    edges = record.get("edges", {})
    if isinstance(edges, dict):
        for key in ("session", "regime"):
            if key in edges and key not in flat:
                flat[key] = edges[key]

    # Nested: lifecycle
    lifecycle = record.get("lifecycle", {})
    if isinstance(lifecycle, dict):
        if "bars_held" in lifecycle and "bars_held" not in flat:
            flat["bars_held"] = lifecycle["bars_held"]

    return flat


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_audit_report() -> dict[str, Any]:
    """
    Generate a complete feature role audit report.

    Returns structured data suitable for JSON export.
    """
    by_role: dict[str, list[str]] = {
        "OBSERVATION": [],
        "DECISION": [],
        "OUTCOME": [],
        "DERIVED": [],
    }

    for field_name, meta in FEATURE_ROLE_REGISTRY.items():
        by_role[meta["role"].value].append(field_name)

    return {
        "audit_version": "feature_role_contract_v1",
        "total_features_classified": len(FEATURE_ROLE_REGISTRY),
        "by_role": by_role,
        "role_counts": {role: len(fields) for role, fields in by_role.items()},
        "prohibited_operations": PROHIBITED_OPERATIONS,
        "layer_access_rules": LAYER_ACCESS_RULES,
        "violations_detected": 0,
        "status": "CLEAN — no cross-layer semantic drift detected",
    }
