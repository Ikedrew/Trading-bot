# ES-001: Execution Quality by Strategy

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** NEGATIVE
**Confidence:** MEDIUM

## Research Intent

Do different strategy families produce systematically different execution quality? Are some strategies more execution-sensitive?

## Data Used

- **Universes:** EXECUTION, STRATEGY
- **Populations:** all_trades, all_strategies
- **total:** 94 records

## Primary Metrics

- **dimensions:** ['symbol']
- **segment_count:** 9
- **count:** 94
- **wins:** 34
- **losses:** 60
- **win_rate:** 0.3617
- **mean_r:** -0.1758
- **median_r:** -1.0
- **total_r:** -16.5231
- **avg_win_r:** 1.5395
- **avg_loss_r:** -1.1478
- **profit_factor:** 0.7601
- **std_r:** 1.5628
- **expectancy:** -0.1758

## Evidence

### primitives_executed
- segmentation
- expectancy
- **primary_analysis:** segmentation

## Four-Angle Evidence

### EXECUTION
- included: True

### STRATEGY
- included: True

## Conclusion

Negative expectancy: -0.1758R per trade

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

