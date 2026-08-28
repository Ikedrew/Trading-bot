"""Field-ownership / reconciliation registry for the research_data projection.

Phase 7B contract (approved Phase 7A audit):
  * "merge" = FIELD-LEVEL RECONCILIATION into NEW research records, never
    filesystem merging; sources under logs/ are read-only.
  * every research field has ONE canonical owner source; other sources may
    contribute only their exclusive fields.
  * canonical lineage is preserved verbatim; an empty canonical root stays
    empty (no fabrication).
  * outcome data stays strictly on the outcome side.

This module is data-only: the projector and the manifest both consume it.
"""

from __future__ import annotations

PROJECTOR_VERSION = "1.0.0"
NEW_LAYER_SCHEMA_PREFIX = "research_"

# ---------------------------------------------------------------------------
# Field-level DROP lists (duplicate / conflicting / non-research fields).
# Everything not listed as dropped is retained with its source field name.
# ---------------------------------------------------------------------------

OPPORTUNITY_DROPS = {
    # writer bookkeeping / retired identity slot (== canonical_opportunity_id)
    "_persisted_at",
    "_state_at_persist",
    "dataset_version",
    "opportunity_id",
}

DECISION_TRACE_DROPS = {
    # conflicting copies — ledger/audit own these facts
    "schema_version",
    "correlation_id",          # renamed to trace_id (v10_* format)
    "action",                  # ledger `decision` is authoritative
    "reason",                  # ledger `reason` is authoritative
    "observation_id",          # duplicate of decision_id inside the trace
    # non-research / verbose
    "v10_account_snapshot",
    "stages_reached",          # replaced by derived stages_reached_count
    "stages_passed",           # replaced by derived stages_passed_count
    "metadata",
}
DECISION_TRACE_RENAMES = {"correlation_id": "trace_id"}

DECISION_AUDIT_DROPS = {
    "schema_version",
    "correlation_id",          # identical COR-* fact already on ledger spine
    "symbol",                  # join keys already carried
    "cycle_id",
    "timestamp_utc",
    "timestamp_unix",
    "ts_utc_ms",
    # all-Null legacy placeholders (verified in Phase 7 discovery)
    "intent",
    "market_state",
    "market_state_confidence",
    "pattern",
    "observation_id",          # dual-meaning slot; audit's == canonical root
}

ASSESSMENT_EV_FIELDS = [
    # canonical EV / confidence block — owner: assessments; echoes dropped
    "ev",
    "ev_positive",
    "ev_reward",
    "ev_risk",
    "p_success",
    "rr_effective",
    "confirmation_strength",
    "uncertainty_score",
    "weights_used",
    "evidence_contributions",
    "reasoning_narrative",
    "probability_source",
    "probability_model_version",
    "confidence_modifier",
    "selected_strategy",
    "strategy_confidence",
]
ASSESSMENT_DROPS = {
    "schema_version",
    "dataset_version",
    # identity/echo fields already carried from the ledger spine
    "symbol",
    "cycle_id",
    "entity_id",
    "canonical_opportunity_id",
    "bar_time",
    "correlation_id",
    "decision_id",
    "runtime_session_id",
    "assessed_at_utc",
    "assessment_id",
    "opportunity_id",
    "bid_at_assessment",
    "ask_at_assessment",
    "components",
    "score_delta",
    "score_neutral",
    "score_strategy",
    "market_state",
    "market_state_confidence",
    "regime",
    "regime_confidence",
    "direction",
    "pattern",
    "policy_reasoning",
}

EXECUTION_CONTEXT_DROPS = {"schema_version"}
EXECUTION_RESULTS_DROPS = {
    "schema_version",
    # identity/echo already carried from execution_context (pre-trade owner)
    "symbol",
    "cycle_id",
    "entity_id",
    "canonical_opportunity_id",
    "correlation_id",
    "pattern",
    "observation_id",
    "decision_id",
    "decision_ts_utc_ms",
    "timestamp_unix",
    "timestamp_utc",
    "side",                     # execution_context pre-trade side is not present;
                                # exec_results.side is the actual filled side — keep it
}
EXECUTION_RESULTS_DROPS.discard("side")

# Outcome: trade_truth is the canonical outcome owner (Phase 7A audit).
TRADE_JOURNAL_DROPS = {
    "schema_version",
    # conflicting pnl copies — trade_truth owns outcome facts
    "realised_pnl",
    "net_pnl",
    # duplicates of trade_truth facts
    "symbol",
    "trade_id",
    "canonical_opportunity_id",
    "correlation_id",
    "entry_price",
    "exit_price",
    "duration_seconds",
    "recorded_at_utc",
}
TRADE_JOURNAL_KEEP = [
    # journal-exclusive enrichment fields
    "initial_sl", "initial_tp", "initial_volume", "final_volume",
    "max_favourable_price", "close_reason", "trade_horizon", "magic",
    "position_ticket", "entry_time", "exit_time", "direction", "pattern_name",
]

RISK_DEVIATION_DROPS = {
    "schema_version",
    "symbol",
    "trade_id",
    "correlation_id",
    "timestamp_utc",
    "entry_price",
    "exit_price",
    "initial_sl",
    "direction",
    "pnl_distance",   # keep? it is exclusive -> keep
}
RISK_DEVIATION_DROPS.discard("pnl_distance")
RISK_DEVIATION_KEEP = [
    "planned_risk_R", "actual_risk_R", "risk_deviation",
    "risk_distance", "risk_classification", "pnl_distance",
]

SHADOW_DROPS = {
    # verbose duplicate of trade_state_progression (Phase 7A §6)
    "lifecycle.state_log_tail",
}
SHADOW_EVENT_TYPES = {"PLAN": "plan", "OPEN": "open", "PROGRESS": "progress", "CLOSE": "close"}

# Fields that must NEVER appear in a live/execution record (outcome boundary).
OUTCOME_FORBIDDEN_IN_EXECUTION = {
    "pnl", "pnl_realised", "net_profit", "r_multiple_realised",
    "realised_pnl", "net_pnl", "exit_price", "exit_reason",
    "close_reason", "duration_seconds", "commission", "swap",
    "outcome_trade_id",
}

# Canonical-root no-fabrication invariant
CANONICAL_FIELD = "canonical_opportunity_id"


def drop_nested(record: dict, dotted_keys: set) -> dict:
    """Remove nested keys given as dotted paths (e.g. 'lifecycle.state_log_tail')."""
    for dk in dotted_keys:
        parts = dk.split(".")
        node = record
        for p in parts[:-1]:
            if not isinstance(node, dict) or p not in node:
                node = None
                break
            node = node[p]
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return record


def validate_canonical_root(value: str, symbol: str | None = None) -> bool:
    """Root must be empty (lineage not established) or SYMBOL*BAR*PATTERN."""
    if value == "" or value is None:
        return True
    parts = value.split("*")
    if len(parts) != 3 or not parts[1].isdigit():
        return False
    if symbol and parts[0] != symbol:
        return False
    return True


OWNERSHIP_REGISTRY = {
    "market_context": {
        "owner": "logs/v3_shadow/market_context (primary); logs/market_context (fallback)",
        "grain": "symbol x bar_time",
        "note": "single canonical market-understanding dataset; entity_id/cycle_id "
                "reconciled from same-bar observation rows when absent",
    },
    "live/observation": {"owner": "logs/strategy_observations", "grain": "symbol x cycle"},
    "live/opportunity": {"owner": "logs/opportunities", "grain": "canonical_opportunity_id (state upserts)"},
    "live/decision": {
        "owner": "logs/decision_ledger (spine) + logs/decision_trace (stage detail) "
                 "+ logs/decision_audit (gate flags) + logs/assessments (EV block)",
        "grain": "symbol x cycle",
    },
    "live/execution": {
        "owner": "logs/execution_context (pre-trade) + logs/execution_results (fills)",
        "grain": "correlation_id (order)",
        "boundary": "outcome fields forbidden",
    },
    "live/outcome": {
        "owner": "logs/trade_truth (canonical outcome) + logs/trade_journal (exclusive "
                 "enrichment) + logs/risk_deviation (plan-vs-actual risk)",
        "grain": "trade_id",
    },
    "shadow/plan|open|progress|close": {
        "owner": "logs/shadow_runtime_v1 split by event_type",
        "grain": "shadow_trade_id x event",
    },
}
