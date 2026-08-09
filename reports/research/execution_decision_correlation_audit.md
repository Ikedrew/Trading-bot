# Execution - Decision Correlation Audit

Generated: 2026-08-09T01:24:21Z

## Classification: PARTIAL_BUT_USABLE

## Historical Coverage

| Metric | Value |
|--------|-------|
| Total Execution Records | 94 |
| Correlated | 7 |
| Uncorrelated | 86 |
| Ambiguous | 1 |
| Coverage Rate | 7.4% |

## By Symbol

| Symbol | Total | Correlated | Uncorrelated |
|--------|-------|------------|--------------|
| AUDUSD | 14 | 0 | 14 |
| EURUSD | 8 | 1 | 7 |
| GBPUSD | 8 | 0 | 8 |
| NZDUSD | 21 | 3 | 18 |
| US500 | 4 | 0 | 4 |
| USDCAD | 19 | 0 | 19 |
| USDCHF | 15 | 3 | 12 |
| USDJPY | 2 | 0 | 2 |
| XAUUSD | 3 | 0 | 3 |

## Root Cause

Execution Universe uses pos_TICKET as identity. Decision Universe uses SYMBOL_UNIX_CYCLE_TS as identity. No shared deterministic key exists in the historical data.

## Correlation Method

Temporal reconstruction: match execution entry_time to closest EXECUTE decision entity_id timestamp for same symbol, within 600-second window.

## Why Partial

Decision entity_id timestamp represents cycle evaluation time, execution entry_time represents broker fill time. The gap can exceed the correlation window (600s) because the execution may occur in a different cycle than evaluation.

## Future Improvement

Store decision_entity_id in the execution record at order placement time. This would make future correlation DETERMINISTIC_1_TO_1.

## Monitoring

- Baseline coverage: 7.4%
- Alert if drops below: 3.7%
