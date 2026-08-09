# EM-001: Regime-Conditioned Expectancy

**Run:** run_20260809_033621_3c7f7b
**Timestamp:** 2026-08-09T03:36:49Z
**Outcome:** NEGATIVE
**Confidence:** MEDIUM

## Research Intent

Does trade expectancy differ significantly across market regimes when measured on realised execution outcomes?

## Data Used

- **Universes:** EXECUTION, MARKET
- **Populations:** all_trades, trending_regime, ranging_regime
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
- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758

## Evidence

### primitives_executed
- segmentation
- expectancy
- anomaly_analysis
- **primary_analysis:** segmentation

## Four-Angle Evidence

### EXECUTION
- included: True

### MARKET
- included: True

## Anomaly View

- **total:** 94
- **normal_count:** 94
- **anomaly_count:** 0
- **anomaly_rate:** 0.0
- **normal_mean:** -0.1758

## Conclusion

Negative expectancy: -0.1758R per trade

## Reproducibility

- Engine: 1.0.0
- Question version: 1.0.0
- Analysis version: 1.0.0

