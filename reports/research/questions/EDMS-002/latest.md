# EDMS-002: Promotion Impact Analysis

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** NEGATIVE
**Confidence:** MEDIUM

## Research Intent

If a specific research finding is promoted to production (e.g. disable a pattern, adjust threshold, gate by regime), what is the expected impact on EV, win rate, drawdown, and trade frequency?

## Data Used

- **Universes:** EXECUTION, DECISION, MARKET, STRATEGY
- **Populations:** all_trades, execute_decisions, all_market_states, all_strategies
- **total:** 94 records

## Primary Metrics

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
- **distribution_count:** 94
- **mean:** -0.1758
- **median:** -1.0
- **std:** 1.5628
- **min:** -4.5
- **max:** 5.449

## Evidence

### primitives_executed
- expectancy
- distribution
- **primary_analysis:** expectancy

## Four-Angle Evidence

### EXECUTION
- included: True

### DECISION
- included: True

### MARKET
- included: True

### STRATEGY
- included: True

## Conclusion

Negative expectancy: -0.1758R per trade

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

