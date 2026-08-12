# Inconclusive Question Diagnostic

**Run:** run_20260810_004555_d181fe
**Total INCONCLUSIVE:** 11

## Executive Summary

- **OTHER:** 5
- **WRONG_PRIMITIVE_PARAMETERS:** 3
- **LEGITIMATE_INSUFFICIENT_DATA:** 2
- **MISSING_INPUT_FIELD:** 1

## Per-Question Diagnostic

| Question | Primary Prim | Raw Pop | R Available | Analytical | Classification |
|----------|-------------|---------|-------------|-----------|----------------|
| E-010 | comparison | 94 | 94 | 94 | OTHER |
| D-004 | distribution | 7690 | 1 | 1 | LEGITIMATE_INSUFFICIENT_DATA |
| M-002 | predictive_power | 6230 | 4 | 0 | WRONG_PRIMITIVE_PARAMETERS |
| M-004 | predictive_power | 6230 | 4 | 0 | WRONG_PRIMITIVE_PARAMETERS |
| S-003 | calibration | 391 | 80 | 0 | WRONG_PRIMITIVE_PARAMETERS |
| S-004 | distribution | 1946 | 0 | 0 | MISSING_INPUT_FIELD |
| ED-001 | comparison | 94 | 94 | 94 | OTHER |
| ED-002 | comparison | 0 | 0 | 0 | OTHER |
| DM-002 | comparison | 8042 | 81 | 8042 | OTHER |
| MS-003 | distribution | 6230 | 4 | 4 | LEGITIMATE_INSUFFICIENT_DATA |
| EDM-001 | comparison | 94 | 94 | 94 | OTHER |

## Detailed Root-Cause Analysis

### E-010 - Risk:Reward Ratio Effectiveness
- **Angles:** EXECUTION
- **Population:** all_trades
- **Raw size:** 94
- **r_multiple available:** 94
- **Analytical sample:** 94
- **Missing fields:** []
- **Classification:** OTHER
- **Explanation:** Analytical sample: 94, r_multiple available: 94
- **Fix:** Investigate individually.

### D-004 - Rejection Stage Analysis
- **Angles:** DECISION
- **Population:** no_trade_decisions
- **Raw size:** 7690
- **r_multiple available:** 1
- **Analytical sample:** 1
- **Missing fields:** []
- **Classification:** LEGITIMATE_INSUFFICIENT_DATA
- **Explanation:** Analytical sample 1 is below minimum threshold. r_multiple available in 1 records.
- **Fix:** Wait for more data to accumulate. No code fix required.

### M-002 - HTF Alignment Value
- **Angles:** MARKET
- **Population:** all_market_states
- **Raw size:** 6230
- **r_multiple available:** 4
- **Analytical sample:** 0
- **Missing fields:** []
- **Classification:** WRONG_PRIMITIVE_PARAMETERS
- **Explanation:** r_multiple available in 4 records but primitive 'predictive_power' used default parameters that look for different fields. Analytical sample was 0.
- **Fix:** The primitive 'predictive_power' default parameters may not match the actual field names in this universe. Pass explicit parameters mapping question fields to primitive expectations.

### M-004 - Market Structure Clarity
- **Angles:** MARKET
- **Population:** all_market_states
- **Raw size:** 6230
- **r_multiple available:** 4
- **Analytical sample:** 0
- **Missing fields:** []
- **Classification:** WRONG_PRIMITIVE_PARAMETERS
- **Explanation:** r_multiple available in 4 records but primitive 'predictive_power' used default parameters that look for different fields. Analytical sample was 0.
- **Fix:** The primitive 'predictive_power' default parameters may not match the actual field names in this universe. Pass explicit parameters mapping question fields to primitive expectations.

### S-003 - Strategy Selection Accuracy
- **Angles:** STRATEGY
- **Population:** strategy_selected
- **Raw size:** 391
- **r_multiple available:** 80
- **Analytical sample:** 0
- **Missing fields:** []
- **Classification:** WRONG_PRIMITIVE_PARAMETERS
- **Explanation:** r_multiple available in 80 records but primitive 'calibration' used default parameters that look for different fields. Analytical sample was 0.
- **Fix:** The primitive 'calibration' default parameters may not match the actual field names in this universe. Pass explicit parameters mapping question fields to primitive expectations.

### S-004 - Strategy Rejection Patterns
- **Angles:** STRATEGY
- **Population:** strategy_rejected
- **Raw size:** 1946
- **r_multiple available:** 0
- **Analytical sample:** 0
- **Missing fields:** []
- **Classification:** MISSING_INPUT_FIELD
- **Explanation:** Population has 1946 records but r_multiple is available in only 0. The primitive 'distribution' requires outcome data (r_multiple) which is only populated for records with execution matches. Missing fields: ['r_multiple (implicit)']
- **Fix:** Outcome enrichment produced 0 matches for this population. Either the population contains no EXECUTE decisions, or the enrichment join didn't match. Check if population filter is too broad.

### ED-001 - Decision-to-Execution Edge Leakage
- **Angles:** EXECUTION, DECISION
- **Population:** all_trades
- **Raw size:** 94
- **r_multiple available:** 94
- **Analytical sample:** 94
- **Missing fields:** []
- **Classification:** OTHER
- **Explanation:** Analytical sample: 94, r_multiple available: 94
- **Fix:** Investigate individually.

### ED-002 - Missed Opportunity Cost
- **Angles:** EXECUTION, DECISION
- **Population:** no_trade_decisions
- **Raw size:** 0
- **r_multiple available:** 0
- **Analytical sample:** 0
- **Missing fields:** ['terminal_stage', 'terminal_reason', 'score', 'ev', 'r_multiple']
- **Classification:** OTHER
- **Explanation:** Analytical sample: 0, r_multiple available: 0
- **Fix:** Investigate individually.

### DM-002 - Opportunity Detection vs Market State
- **Angles:** DECISION, MARKET
- **Population:** all_decisions
- **Raw size:** 8042
- **r_multiple available:** 81
- **Analytical sample:** 8042
- **Missing fields:** []
- **Classification:** OTHER
- **Explanation:** Analytical sample: 8042, r_multiple available: 81
- **Fix:** Investigate individually.

### MS-003 - Strategy Availability by Market State
- **Angles:** MARKET, STRATEGY
- **Population:** all_market_states
- **Raw size:** 6230
- **r_multiple available:** 4
- **Analytical sample:** 4
- **Missing fields:** ['h4_phase', 'family', 'conditions_met']
- **Classification:** LEGITIMATE_INSUFFICIENT_DATA
- **Explanation:** Analytical sample 4 is below minimum threshold. r_multiple available in 4 records.
- **Fix:** Wait for more data to accumulate. No code fix required.

### EDM-001 - Complete Trade Lifecycle Analysis
- **Angles:** EXECUTION, DECISION, MARKET
- **Population:** all_trades
- **Raw size:** 94
- **r_multiple available:** 94
- **Analytical sample:** 94
- **Missing fields:** ['volatility_state']
- **Classification:** OTHER
- **Explanation:** Analytical sample: 94, r_multiple available: 94
- **Fix:** Investigate individually.

## Shared Root Causes

### r_multiple unavailable in resolved population
- **Affected:** 2 questions
- **Questions:** S-004, ED-002
- **Explanation:** These questions resolve populations where outcome enrichment produced 0 matches, or the primary population doesn't contain EXECUTE decisions with entity_id matches.

### Primitive 'comparison' producing empty results
- **Affected:** 5 questions
- **Questions:** E-010, ED-001, ED-002, DM-002, EDM-001

### Primitive 'distribution' producing empty results
- **Affected:** 3 questions
- **Questions:** D-004, S-004, MS-003

### Primitive 'predictive_power' producing empty results
- **Affected:** 2 questions
- **Questions:** M-002, M-004

### Population 'all_trades' lacking outcome data
- **Affected:** 3 questions
- **Questions:** E-010, ED-001, EDM-001

### Population 'all_market_states' lacking outcome data
- **Affected:** 3 questions
- **Questions:** M-002, M-004, MS-003

### Population 'no_trade_decisions' lacking outcome data
- **Affected:** 2 questions
- **Questions:** D-004, ED-002

## Recommended Repair Order

1. **Pass explicit primitive parameters** for questions where r_multiple IS available
   but the primitive uses wrong default field names (WRONG_PRIMITIVE_PARAMETERS)
2. **Filter population to outcome-available records** for questions that need
   r_multiple but resolve broad populations (e.g., all_decisions vs execute_decisions)
3. **Wait for data** for questions with genuinely insufficient sample
   (LEGITIMATE_INSUFFICIENT_DATA)

## What Was NOT Changed

- No question contracts modified
- No universe builders modified
- No primitives modified
- No finding classification modified
- No questions rerun
- No existing findings overwritten
- No S3 data modified

