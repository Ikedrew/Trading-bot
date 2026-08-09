# V10 Decision Enrichment Report

Generated: 2026-08-07T16:50:46.463835+00:00
Source: logs\research_ready_trade_dataset\research_ready_trades.jsonl
Output: logs\research_ready_trade_dataset\research_ready_trades_enriched.jsonl

## Summary

| Metric | Value |
|---|---|
| Total trades | 94 |
| Matched | 94 |
| Unmatched | 0 |
| Match rate | 100% |
| Decision traces loaded | 383 |
| Execution results loaded | 228 |

## Match Methods

| Method | Count |
|---|---|
| sym_cycle | 93 |
| entity_id | 1 |
| correlation_id | 0 |
| v10_correlation | 0 |
| sym_time | 0 |
| unmatched | 0 |

## Missing Fields (top 10)

| Field | Missing Count |
|---|---|
| dt_trade_horizon | 94 |
| dt_engine_version | 80 |
| dt_v10_regime | 80 |
| dt_v10_regime_confidence | 80 |
| dt_v10_volatility | 80 |
| dt_h1_direction | 80 |
| dt_h1_clarity | 80 |
| dt_h4_trend | 80 |
| dt_h4_phase | 80 |
| dt_opportunity_state | 80 |

---