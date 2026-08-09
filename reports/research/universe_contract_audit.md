# Universe & Population Contract Audit

Generated: 2026-08-09T00:49:33Z

## Summary

| Metric | Value |
|--------|-------|
| Universes | 4 |
| Populations | 31 |
| Joins | 7 |
| Semantic Fields | 46 |
| Questions Total | 45 |
| Questions READY | 40 |
| Questions BLOCKED | 5 |
| Questions INVALID | 0 |

## Universe Summary

| Universe | Grain | Records | Schema | Status |
|----------|-------|---------|--------|--------|
| EXECUTION | One validated trade (entry → exit → real | 94 | 1.0 | VALID |
| DECISION | One decision event at the moment the pip | 7841 | 2.0 | VALID |
| MARKET | One market-state observation tied to a d | 6028 | 2.0 | VALID |
| STRATEGY | One strategy evaluation/selection event  | 12990 | 2.0 | VALID |

## Population Summary

| Universe | Population | Records | Health | Errors | Warnings |
|----------|------------|---------|--------|--------|----------|
| EXECUTION | all_trades | 94 | VALID | 0 | 0 |
| EXECUTION | winning_trades | 34 | VALID | 0 | 0 |
| EXECUTION | losing_trades | 60 | VALID | 0 | 0 |
| EXECUTION | anomalous_trades | 0 | EMPTY | 0 | 1 |
| DECISION | all_decisions | 7841 | DEGRADED | 0 | 1 |
| DECISION | execute_decisions | 351 | VALID | 0 | 0 |
| DECISION | no_trade_decisions | 7490 | DEGRADED | 0 | 1 |
| DECISION | rejected_at_opportunity | 3026 | DEGRADED | 0 | 1 |
| DECISION | rejected_at_strategy | 727 | DEGRADED | 0 | 1 |
| DECISION | rejected_at_entry | 326 | VALID | 0 | 0 |
| DECISION | rejected_at_risk | 2559 | DEGRADED | 0 | 1 |
| DECISION | rejected_at_execution | 12 | VALID | 0 | 0 |
| DECISION | high_score_decisions | 0 | EMPTY | 0 | 1 |
| DECISION | low_score_decisions | 7841 | DEGRADED | 0 | 1 |
| MARKET | all_market_states | 6028 | DEGRADED | 0 | 2 |
| MARKET | trending_regime | 1308 | DEGRADED | 0 | 2 |
| MARKET | ranging_regime | 3542 | DEGRADED | 0 | 2 |
| MARKET | transitional_regime | 1116 | INVALID | 1 | 0 |
| MARKET | high_volatility | 82 | DEGRADED | 0 | 1 |
| MARKET | low_volatility | 0 | EMPTY | 0 | 1 |
| MARKET | session_london | 1716 | DEGRADED | 0 | 1 |
| MARKET | session_ny | 961 | DEGRADED | 0 | 1 |
| MARKET | session_asia | 1544 | DEGRADED | 0 | 1 |
| STRATEGY | all_strategies | 12990 | DEGRADED | 0 | 2 |
| STRATEGY | trend_continuation | 624 | DEGRADED | 0 | 1 |
| STRATEGY | mean_reversion | 5217 | DEGRADED | 0 | 2 |
| STRATEGY | breakout | 1865 | DEGRADED | 0 | 1 |
| STRATEGY | momentum | 280 | DEGRADED | 0 | 1 |
| STRATEGY | strategy_eligible | 10849 | DEGRADED | 0 | 2 |
| STRATEGY | strategy_selected | 390 | VALID | 0 | 0 |
| STRATEGY | strategy_rejected | 1946 | DEGRADED | 0 | 2 |

## Join Summary

| Join | Cardinality | Matched | Unmatched | Match Rate | Status |
|------|-------------|---------|-----------|------------|--------|
| EXEC_DECISION | 1:1 | 0 | 94 | 0.0% | DEGRADED |
| DECISION_EXECUTION | N:1 | 0 | 7812 | 0.0% | DEGRADED |
| DECISION_MARKET | 1:1 | 4202 | 3610 | 53.8% | VALID |
| DECISION_STRATEGY | 1:1 | 7812 | 0 | 100.0% | VALID |
| MARKET_STRATEGY | 1:1 | 4202 | 0 | 100.0% | VALID |
| EXEC_MARKET | 1:1 | 0 | 94 | 0.0% | DEGRADED |
| EXEC_STRATEGY | 1:1 | 0 | 94 | 0.0% | DEGRADED |

## Discrepancies

### EXECUTE_COUNT

- **Observation:** 390 raw EXECUTE vs 351 in Decision Universe
- **Explanation:** 39 EXECUTE records lack entity_id field
- **Classification:** EXPECTED_FILTER
- **Detail:** Records without entity_id cannot join to other universes

### HIGH_VOLATILITY_EMPTY

- **Observation:** HIGH_VOLATILITY population = 0 initially
- **Explanation:** Data contains NEUTRAL(96.4%) and EXPANSION(3.6%); no HIGH/LOW values
- **Classification:** DATA_FACT
- **Detail:** Fixed filter to include EXPANSION; now ~213 records qualify


## Question Readiness

| Question | Status | Reason |
|----------|--------|--------|
| E-001 | READY | All requirements met |
| E-002 | READY | All requirements met |
| E-003 | READY | All requirements met |
| E-004 | READY | All requirements met |
| E-005 | READY | All requirements met |
| E-006 | BLOCKED | Population all_trades: Row count 94 < minimum 100 |
| E-007 | READY | All requirements met |
| E-008 | READY | All requirements met |
| E-009 | READY | All requirements met |
| E-010 | READY | All requirements met |
| D-001 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| D-002 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| D-003 | BLOCKED | Population high_score_decisions: Row count 0 < minimum 20 |
| D-004 | BLOCKED | Population rejected_at_execution: Row count 12 < minimum 50 |
| D-005 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| D-006 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| D-007 | READY | All requirements met |
| M-001 | BLOCKED | Population transitional_regime: Population health is INVALID |
| M-002 | READY | All requirements met |
| M-003 | BLOCKED | Population low_volatility: Row count 0 < minimum 10 |
| M-004 | READY | All requirements met |
| M-005 | READY | All requirements met |
| M-006 | READY | All requirements met |
| S-001 | READY | Field 'r_multiple' not mapped in STRATEGY (available in ['EX |
| S-002 | READY | Field 'r_multiple' not mapped in STRATEGY (available in ['EX |
| S-003 | READY | All requirements met |
| S-004 | READY | All requirements met |
| ED-001 | READY | All requirements met |
| ED-002 | READY | All requirements met |
| ED-003 | READY | All requirements met |
| EM-001 | READY | All requirements met |
| EM-002 | READY | All requirements met |
| ES-001 | READY | All requirements met |
| DM-001 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| DM-002 | READY | All requirements met |
| DM-003 | READY | All requirements met |
| DS-001 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| DS-002 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| MS-001 | READY | Field 'r_multiple' not mapped in STRATEGY (available in ['EX |
| MS-002 | READY | Field 'r_multiple' not mapped in STRATEGY (available in ['EX |
| MS-003 | READY | All requirements met |
| EDM-001 | READY | All requirements met |
| DMS-001 | READY | Field 'r_multiple' not mapped in DECISION (available in ['EX |
| EDMS-001 | READY | All requirements met |
| EDMS-002 | READY | All requirements met |
