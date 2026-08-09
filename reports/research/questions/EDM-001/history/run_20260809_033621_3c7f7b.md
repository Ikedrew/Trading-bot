# EDM-001: Complete Trade Lifecycle Analysis

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** COMPLETED
**Confidence:** MEDIUM

## Research Intent

For executed trades, what is the full pathway from market state → decision → execution outcome? Where does the pipeline add or lose value?

## Data Used

- **Universes:** EXECUTION, DECISION, MARKET
- **Populations:** all_trades, execute_decisions, all_market_states
- **total:** 94 records

## Primary Metrics

- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758
- **exceptional_analysis_total:** 94
- **exceptional_analysis_normal_count:** 80
- **exceptional_high_count:** 12
- **exceptional_low_count:** 2
- **exceptional_rate:** 0.1489
- **exceptional_high_mean:** 2.5539
- **exceptional_low_mean:** -4.3952

## Evidence

### primitives_executed
- comparison
- anomaly_analysis
- exceptional_analysis
- **primary_analysis:** comparison

## Four-Angle Evidence

### EXECUTION
- included: True

### DECISION
- included: True

### MARKET
- included: True

## Anomaly View

- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758

## Exceptional View

- **total:** 94
- **normal_count:** 80
- **exceptional_high_count:** 12
- **exceptional_low_count:** 2
- **exceptional_rate:** 0.1489
- **exceptional_high_mean:** 2.5539
- **exceptional_low_mean:** -4.3952

## Conclusion

12 records above 2.0R; 2 records below -2.0R

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

