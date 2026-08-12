# E-010: Risk:Reward Ratio Effectiveness

**Run:** run_fbef5704b584
**Timestamp:** 2026-08-10T18:58:47Z
**Outcome:** NEGATIVE
**Confidence:** MEDIUM

## Research Intent

What R:R ratios are actually achieved vs intended? Does target R:R at entry predict outcome quality?

## Data Used

- **Universes:** EXECUTION
- **Populations:** all_trades
- **population:** 94 records
- **analytical_sample:** 94 records
- **minimum_required:** 20 records
- **sample_reduction_reason:** No reduction — all records usable records

## Primary Metrics

- **population_size:** 94
- **analytical_sample:** 94
- **groups_discovered:** 2
- **groups_sufficient:** 2
- **groups_insufficient:** 0
- **overall_mean:** -0.1758
- **group_spread:** 3.0121
- **mean_r:** -0.1758

## Evidence

### primitives_executed
- comparison
- **primary_analysis:** comparison

## Four-Angle Evidence

### EXECUTION
- included: True

## Anomaly View

- **status:** NOT_APPLICABLE
- **reason:** Question does not declare ANOMALOUS view

## Exceptional View

- **status:** NOT_APPLICABLE
- **reason:** Question does not declare EXCEPTIONAL view

## Conclusion

Groups differ by 3.0121R (spread between best and worst group)

## Changes from Previous Run

- **outcome_changed:** {'from': 'INCONCLUSIVE', 'to': 'NEGATIVE'}

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

