# D-004: Rejection Stage Analysis

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** COMPLETED
**Confidence:** HIGH

## Research Intent

Where in the decision pipeline are trades most commonly rejected? Which rejection stage removes the most potential edge vs protecting from losses?

## Data Used

- **Universes:** DECISION
- **Populations:** no_trade_decisions, rejected_at_opportunity, rejected_at_strategy, rejected_at_entry, rejected_at_risk, rejected_at_execution
- **total:** 7490 records

## Primary Metrics

- **count:** 0
- **total:** 0
- **normal_count:** 0
- **exceptional_high_count:** 0
- **exceptional_low_count:** 0
- **exceptional_rate:** 0

## Evidence

### primitives_executed
- distribution
- expectancy
- exceptional_analysis
- **primary_analysis:** distribution

## Four-Angle Evidence

### DECISION
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

- No records with field 'r_multiple'
- No records with R-multiple data

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

