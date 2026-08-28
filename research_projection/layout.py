"""Static layout of the research_data/ layer.

Maps every research area to its source dataset(s) under logs/ and declares the
research schema name and research-id prefix used for each area.

This module is data-only (like ownership.py): the projector and the manifest
both consume it. No I/O happens here.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Source datasets (READ-ONLY under logs/)
# layout: "symbol_date"  -> logs/<dataset>/<SYMBOL>/<YYYY-MM-DD>.jsonl
#         "flat_date"    -> logs/<dataset>/<YYYY-MM-DD>.jsonl
#         "symbol_date_flatname" -> logs/<dataset>/<SYMBOL>_<YYYY-MM-DD>.jsonl
# ---------------------------------------------------------------------------
SOURCE_DATASETS = {
    "strategy_observations": {"path": "strategy_observations", "layout": "symbol_date"},
    "opportunities": {"path": "opportunities", "layout": "symbol_date"},
    "decision_ledger": {"path": "decision_ledger", "layout": "symbol_date"},
    "decision_trace": {"path": "decision_trace", "layout": "symbol_date"},
    "decision_audit": {"path": "decision_audit", "layout": "symbol_date_flatname"},
    "assessments": {"path": "assessments", "layout": "symbol_date"},
    "execution_context": {"path": "execution_context", "layout": "symbol_date"},
    "execution_results": {"path": "execution_results", "layout": "symbol_date"},
    "trade_truth": {"path": "trade_truth", "layout": "symbol_date"},
    "trade_journal": {"path": "trade_journal", "layout": "flat_date"},
    "risk_deviation": {"path": "risk_deviation", "layout": "symbol_date"},
    "shadow_runtime_v1": {"path": "shadow_runtime_v1", "layout": "symbol_date"},
    "v3_market_context": {"path": "v3_shadow/market_context", "layout": "symbol_date"},
    "market_context": {"path": "market_context", "layout": "symbol_date"},
}

# ---------------------------------------------------------------------------
# Research areas (WRITTEN under research_data/)
# ---------------------------------------------------------------------------
# lineage_fields lists the identifier fields preserved VERBATIM in
# research_lineage. They are never renamed, never merged into each other and
# never fabricated when absent in the source record.
RESEARCH_AREAS = {
    "live/observation": {
        "schema": "research_observation_v1",
        "prefix": "robs",
        "sources": ["strategy_observations"],
        "grain": "symbol x cycle",
        "lineage_fields": ["observation_id", "entity_id", "symbol", "cycle_id"],
        "join": "none (self-contained record)",
    },
    "live/opportunity": {
        "schema": "research_opportunity_v1",
        "prefix": "ropp",
        "sources": ["opportunities"],
        "grain": "canonical_opportunity_id (state upserts retained)",
        "lineage_fields": [
            "canonical_opportunity_id", "opportunity_id", "entity_id",
            "correlation_id", "decision_id", "symbol", "cycle_id",
        ],
        "join": "none (identities preserved verbatim; opportunity_id retained as "
                "source identity slot per ownership registry)",
    },
    "live/decision": {
        "schema": "research_decision_v1",
        "prefix": "rdec",
        "sources": ["decision_ledger", "decision_trace", "decision_audit", "assessments"],
        "grain": "symbol x cycle (ledger spine)",
        "lineage_fields": [
            "canonical_opportunity_id", "entity_id", "correlation_id",
            "context_snapshot_id", "decision_id", "trace_id", "symbol", "cycle_id",
        ],
        "join": "exact-match enrichment: trace/audit by entity_id (fallback "
                "correlation/decision ids); assessments contribute EV block only. "
                "No timestamp-proximity joining. Unmatched -> link_status=unresolved.",
    },
    "live/execution": {
        "schema": "research_execution_v1",
        "prefix": "rexe",
        "sources": ["execution_context", "execution_results"],
        "grain": "correlation_id (order)",
        "lineage_fields": [
            "canonical_opportunity_id", "correlation_id", "entity_id",
            "decision_id", "observation_id", "symbol", "cycle_id",
        ],
        "join": "exact correlation_id match; context is the pre-trade owner, "
                "results contribute fill fields. Unmatched results are emitted "
                "as owner='execution_results_only' with link_status=unresolved. "
                "Outcome fields forbidden (outcome boundary).",
    },
    "live/outcome": {
        "schema": "research_outcome_v1",
        "prefix": "rout",
        "sources": ["trade_truth", "trade_journal", "risk_deviation"],
        "grain": "trade_id",
        "lineage_fields": [
            "canonical_opportunity_id", "trade_id", "correlation_id", "symbol",
        ],
        "join": "exact trade_id match; trade_truth is the canonical outcome "
                "owner, journal/risk_deviation contribute keep-list fields only.",
    },
    "market_context": {
        "schema": "research_market_context_v1",
        "prefix": "rmcx",
        "sources": ["v3_market_context", "market_context"],
        "grain": "symbol x bar_time",
        "lineage_fields": ["entity_id", "symbol", "cycle_id", "timestamp_utc"],
        "join": "v3_shadow/market_context is primary; logs/market_context is the "
                "fallback for symbol/dates absent from v3. cycle_id/entity_id "
                "reconciled ONLY from exact same-bar (symbol, bar_time) "
                "observation rows, with the reconciliation recorded.",
    },
}

SHADOW_LINEAGE_FIELDS = [
    "canonical_opportunity_id", "plan_id", "shadow_trade_id",
    "entity_id", "symbol", "cycle_id",
]
SHADOW_JOIN_NOTE = "none (event stream projected verbatim per event_type)"

RESEARCH_AREAS.update({
    "shadow/plan": {
        "schema": "research_shadow_plan_v1",
        "prefix": "rshp",
        "sources": ["shadow_runtime_v1"],
        "grain": "shadow_trade_id x event (plan rows carry plan_id only)",
        "lineage_fields": list(SHADOW_LINEAGE_FIELDS),
        "join": SHADOW_JOIN_NOTE,
    },
    "shadow/open": {
        "schema": "research_shadow_open_v1",
        "prefix": "rsho",
        "sources": ["shadow_runtime_v1"],
        "grain": "shadow_trade_id x event",
        "lineage_fields": list(SHADOW_LINEAGE_FIELDS),
        "join": SHADOW_JOIN_NOTE,
    },
    "shadow/progress": {
        "schema": "research_shadow_progress_v1",
        "prefix": "rshg",
        "sources": ["shadow_runtime_v1"],
        "grain": "shadow_trade_id x event",
        "lineage_fields": list(SHADOW_LINEAGE_FIELDS),
        "join": SHADOW_JOIN_NOTE,
    },
    "shadow/close": {
        "schema": "research_shadow_close_v1",
        "prefix": "rshc",
        "sources": ["shadow_runtime_v1"],
        "grain": "shadow_trade_id x event",
        "lineage_fields": list(SHADOW_LINEAGE_FIELDS),
        "join": SHADOW_JOIN_NOTE,
    },
})

# shadow_runtime_v1 event_type -> research area (kept separate from LIVE).
SHADOW_EVENT_TO_AREA = {
    "PLAN": "shadow/plan",
    "OPEN": "shadow/open",
    "PROGRESS": "shadow/progress",
    "CLOSE": "shadow/close",
}

# Reserved envelope keys (prefixed to guarantee no collision with source
# fields carried through verbatim).
ENVELOPE_KEYS = (
    "research_id",
    "research_schema",
    "research_area",
    "projector_version",
    "projected_at_utc",
    "source_schema",
    "research_source",
    "research_lineage",
    "research_reconciliation",
)
