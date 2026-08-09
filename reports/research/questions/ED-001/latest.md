# ED-001: Decision-to-Execution Edge Leakage

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** COMPLETED
**Confidence:** MEDIUM

## Research Intent

How much expected edge (from decision EV and score) is lost between the decision point and realised execution? Where does leakage occur?

## Data Used

- **Universes:** EXECUTION, DECISION
- **Populations:** all_trades, execute_decisions
- **total:** 94 records

## Primary Metrics

- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758

## Evidence

### primitives_executed
- comparison
- anomaly_analysis
- **primary_analysis:** comparison

## Four-Angle Evidence

### EXECUTION
- included: True

### DECISION
- included: True

## Anomaly View

- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758

## Conclusion

No conclusion

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

