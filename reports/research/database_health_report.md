# V10 Data Governance Report

Generated: 2026-08-09T00:15:41.439190+00:00
Dataset version: 2026-08-07_1422
**DATA TRUST: WARNING**

## Summary

| Source | Count |
|---|---|
| MT5 matched | 106 |
| Journal trades | 106 |
| Research trades | 94 |
| Excluded trades | 12 |

## Trade Counts [PASS]

- mt5_trades: 106
- journal_trades: 106
- research_trades: 94
- excluded_trades: 12
- research_plus_excluded: 106

## Pnl Reconciliation [PASS]

- mt5_pnl: 714.27
- research_pnl: 714.27
- matched_trades: 94
- difference_abs: 0.0
- difference_pct: 0.0
- canonical_field: net_realised_pnl (gross + commission + swap + fees)

## Identity Validation [PASS]

- missing_ticket_count: 0
- duplicate_ticket_count: 0
- unmatched_position_count: 0
- total_research_identities: 94

## Field Completeness [WARN]

**missing_required:**
**missing_desired:**
  - correlation_id: 1
- total_trades_checked: 94
- source_used: enriched

**Issues:**
- Desired fields with gaps: {'correlation_id': 1}

## Decision Coverage [PASS]

- trades_total: 94
- with_decision_trace: 94
- without_decision_trace: 0
- coverage: 100%
- coverage_pct: 100.0
- decision_traces_loaded: 390
- unmatched_trade_ids: []

---