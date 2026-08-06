# V10-OQ1: Opportunity Quality Predictive Analysis

Generated: 2026-08-06T00:14:15.793245+00:00
Sample: 84 trades | 83 with component data
Components: behaviour_score, bias_alignment, bias_stability, chop_clarity, confirmation_pre, formation_score, h4_alignment, htf_alignment, location_score, market_quality, pattern_quality, structure_score, trend_alignment, volatility_quality

## Executive Summary

**Conclusion: SOME_DIMENSIONS_PREDICTIVE**

5 strong correlation(s), 10 useful spread(s), but 4 have NEGATIVE direction

## Overall Score vs Outcome

| Score Range | N | Win% | Avg R | Expectancy | Conf |
|---|---|---|---|---|---|
| 0.0-0.4 | 1 | 0% | -1.00 | -1.00 | LOW |
| 0.4-0.5 | 12 | 33% | -0.56 | -0.56 | MEDIUM |
| 0.5-0.6 | 31 | 39% | +0.01 | +0.01 | HIGH |
| 0.6-0.7 | 32 | 41% | -0.02 | -0.02 | HIGH |
| 0.7-1.0 | 8 | 38% | -0.43 | -0.43 | LOW |

## Component Predictive Power

| Component | Correlation | Low R | Mid R | High R | Spread | Direction |
|---|---|---|---|---|---|---|
| formation_score | -0.346 | +0.09 | -4.50 | -0.40 | -0.49 | negative |
| behaviour_score | -0.284 | -0.12 | -4.50 | -0.33 | -0.20 | negative |
| market_quality | -0.218 | +0.56 | -0.76 | -0.17 | -0.73 | negative |
| confirmation_pre | +0.165 | -0.55 | +0.29 | -0.11 | +0.44 | positive |
| chop_clarity | -0.160 | +0.25 | -0.26 | -0.35 | -0.60 | negative |
| h4_alignment | -0.130 | -0.16 | +0.13 | -0.33 | -0.18 | neutral |
| structure_score | +0.109 | -4.50 | +0.10 | -0.40 | +4.10 | positive |
| bias_alignment | +0.106 | -0.24 | -0.22 | +0.08 | +0.33 | positive |
| bias_stability | +0.087 | -0.33 | -0.24 | +0.19 | +0.51 | positive |
| volatility_quality | +0.068 | -0.55 | +0.28 | -0.10 | +0.44 | positive |
| location_score | +0.058 | -4.50 | +0.10 | -0.40 | +4.10 | positive |
| htf_alignment | -0.046 | -0.12 | +0.11 | -0.35 | -0.22 | negative |
| trend_alignment | -0.011 | -0.32 | +0.05 | -0.09 | +0.23 | positive |
| pattern_quality | +0.007 | -0.15 | -0.38 | +0.15 | +0.31 | positive |

## Component Interactions

| Combination | HH Count | HH Avg R | LL Count | LL Avg R | Spread |
|---|---|---|---|---|---|
| market_quality + confirmation_pre | 63 | -0.02 | 8 | -0.28 | +0.26 |

## Quality x Regime

| Quality | Regime | N | Avg R | Win% |
|---|---|---|---|---|
| high | RANGE | 20 | +0.27 | 50% |
| low | TRANSITIONAL | 6 | +0.04 | 33% |
| high | TRANSITIONAL | 18 | -0.41 | 33% |
| low | RANGE | 14 | -0.68 | 21% |

## Quality x Pattern

| Quality | Pattern | N | Avg R | Win% |
|---|---|---|---|---|
| high | TWEEZER_TOP | 16 | +0.34 | 50% |
| low | EVENING_STAR | 3 | +0.02 | 33% |
| high | TWEEZER_BOTTOM | 12 | -0.00 | 42% |
| low | TWEEZER_TOP | 6 | -0.19 | 50% |
| high | EVENING_STAR | 5 | -0.45 | 20% |
| low | TWEEZER_BOTTOM | 7 | -0.90 | 0% |

## Predictor Comparison

| Predictor | Correlation with R |
|---|---|
| best_component (formation_score) | -0.3462 |
| confirmation_score | +0.0880 |
| overall_score | +0.0682 |
| pattern_quality | +0.0247 |

## What This Means For V10 Development

### Valuable Dimensions (positive signal)
- **bias_alignment**: corr=+0.106, spread=+0.33
- **bias_stability**: corr=+0.087, spread=+0.51
- **confirmation_pre**: corr=+0.165, spread=+0.44
- **location_score**: corr=+0.058, spread=+4.10
- **structure_score**: corr=+0.109, spread=+4.10
- **volatility_quality**: corr=+0.068, spread=+0.44

### Concerning Dimensions (negative correlation)
- **behaviour_score**: corr=-0.284 — higher values associated with WORSE outcomes
- **chop_clarity**: corr=-0.160 — higher values associated with WORSE outcomes
- **formation_score**: corr=-0.346 — higher values associated with WORSE outcomes
- **h4_alignment**: corr=-0.130 — higher values associated with WORSE outcomes
- **market_quality**: corr=-0.218 — higher values associated with WORSE outcomes

### Weak/Noisy Dimensions
- htf_alignment: corr=-0.046 — no signal
- pattern_quality: corr=+0.007 — no signal
- trend_alignment: corr=-0.011 — no signal

### Recommendations

- Do NOT change component weights based on 84 trades
- Components with NEGATIVE correlation need investigation (are they measuring noise?)
- The overall score IS predictive (from D1) despite individual component issues
- This suggests the scoring formula works through interaction effects, not individual component value
- Continue collecting component-enriched data for future weight recalibration

---
*83/84 trades enriched with 14 component scores*