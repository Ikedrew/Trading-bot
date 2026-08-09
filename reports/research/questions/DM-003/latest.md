# DM-003: Rejection Rate by Market State

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** COMPLETED
**Confidence:** HIGH

## Research Intent

Does the NO_TRADE rate vary by market state? Are there market conditions where the system rejects everything (possibly missing edge)?

## Data Used

- **Universes:** DECISION, MARKET
- **Populations:** all_decisions, no_trade_decisions, all_market_states
- **total:** 7841 records

## Primary Metrics

- **dimensions:** ['symbol']
- **segment_count:** 0
- **count:** 0
- **total:** 0
- **normal_count:** 0
- **exceptional_high_count:** 0
- **exceptional_low_count:** 0
- **exceptional_rate:** 0

## Evidence

### primitives_executed
- segmentation
- expectancy
- exceptional_analysis
- **primary_analysis:** segmentation

## Four-Angle Evidence

### DECISION
- included: True

### MARKET
- included: True

## Exceptional View

- **total:** 0
- **normal_count:** 0
- **exceptional_high_count:** 0
- **exceptional_low_count:** 0
- **exceptional_rate:** 0

## Conclusion

0 records above 2.0R; 0 records below -2.0R

## Limitations

- No records with R-multiple data

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

