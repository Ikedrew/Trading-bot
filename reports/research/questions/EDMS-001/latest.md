# EDMS-001: Full System Attribution

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** NOT_PREDICTIVE
**Confidence:** MEDIUM

## Research Intent

Across all four angles — what is the relative contribution of market conditions, strategy selection, decision quality, and execution quality to final trade outcomes?

## Data Used

- **Universes:** EXECUTION, DECISION, MARKET, STRATEGY
- **Populations:** all_trades, execute_decisions, all_market_states, all_strategies
- **total:** 94 records

## Primary Metrics

- **monotonic:** False
- **top_bottom_spread:** -0.1949
- **bucket_count:** 5
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
- predictive_power
- anomaly_analysis
- exceptional_analysis
- **primary_analysis:** predictive_power

## Four-Angle Evidence

### EXECUTION
- included: True

### DECISION
- included: True

### MARKET
- included: True

### STRATEGY
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

Feature 'score' is NOT monotonically predictive; Top-bottom spread: -0.1949R; 12 records above 2.0R; 2 records below -2.0R

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

