# V10-D2: EV Calibration

Generated: 2026-08-05T23:42:17.873380+00:00
Sample: 84 trades | 83 enriched with decision trace
Coverage: EV=76, p_success=76, confirmation=76

## Executive Summary

**Conclusion: UNDERCONFIDENT**

System is underconfident — actual outcomes better than predicted

## Confidence (Score) vs Outcome

| Score Range | N | Win% | Avg R | Expectancy | PF |
|---|---|---|---|---|---|
| 0.0-0.2 | 1 | 0% | -1.00 | -1.00 | 0.0 |
| 0.4-0.6 | 43 | 37% | -0.15 | -0.15 | 25.8 |
| 0.6-0.8 | 40 | 40% | -0.11 | -0.11 | 0.3 |

## Probability Calibration (p_success vs actual win rate)

| P Bucket | N | Predicted P | Actual Win% | Error | Direction |
|---|---|---|---|---|---|
| 0.0-0.2 | 11 | 5.4% | 45.5% | +40.1% | underconfident |
| 0.2-0.4 | 73 | 28.7% | 37.0% | +8.2% | underconfident |

## EV Analysis

| EV Range | N | Win% | Avg R | Expectancy |
|---|---|---|---|---|
| negative | 74 | 36% | -0.11 | -0.11 |
| zero_low | 10 | 50% | -0.33 | -0.33 |

**EV Gap:** Positive EV trades avg R = -0.5098 (n=2) | Negative EV trades avg R = -0.1124 (n=74)
**Gap: -0.3974R** — negative EV outperforms (inverted!)

## Predictor Comparison

| Metric | Correlation with R | Strength |
|---|---|---|
| confirmation_score | +0.0880 | Moderate |
| ev | +0.0793 | Weak |
| p_success | +0.0768 | Weak |
| score | +0.0682 | Weak |

**Best predictor: confirmation_score** (correlation = +0.0880)

## Confidence x Regime

| Confidence | Regime | N | Avg R | Win% |
|---|---|---|---|---|
| medium | TRENDING | 3 | +0.76 | 67% |
| high | RANGE | 20 | +0.27 | 50% |
| medium | TRANSITIONAL | 12 | -0.24 | 25% |
| medium | RANGE | 22 | -0.26 | 32% |
| high | TRANSITIONAL | 18 | -0.41 | 33% |

## What This Means For V10 Development

- V10 is UNDERCONFIDENT — actually performs better than it predicts
- May be filtering out good opportunities
- Consider: lower rejection thresholds could improve trade count without degrading edge

---
*83/84 trades enriched with decision trace EV/probability data*