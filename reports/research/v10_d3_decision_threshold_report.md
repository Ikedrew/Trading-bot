# V10-D3: Decision Threshold Effectiveness

Generated: 2026-08-06T00:00:47.940863+00:00
Sample: 84 research-ready trades

## Executive Summary

**Conclusion: SCORE_OUTPERFORMS_EV**

Score-based filtering produces better results than EV-based filtering

## Score Threshold Analysis

| Threshold | Trades | Retained | Win% | Avg R | Expectancy | PF | Conf |
|---|---|---|---|---|---|---|---|
| >=0.30 | 83 | 99% | 39% | -0.13 | -0.13 | 17.2 | HIGH |
| >=0.40 | 83 | 99% | 39% | -0.13 | -0.13 | 17.2 | HIGH |
| >=0.50 | 71 | 85% | 39% | -0.05 | -0.05 | 0.2 | HIGH |
| >=0.55 | 56 | 67% | 39% | -0.06 | -0.06 | 0.1 | HIGH |
| >=0.60 | 40 | 48% | 40% | -0.11 | -0.11 | 0.3 | HIGH |
| >=0.65 | 23 | 27% | 43% | -0.02 | -0.02 | 0.2 | MEDIUM |
| >=0.70 | 8 | 10% | 38% | -0.43 | -0.43 | 0.6 | LOW |
| all (no filter) | 84 | 100% | 38% | -0.14 | -0.14 | 17.2 | HIGH |

## Confidence (Confirmation Score) Threshold

| Threshold | Trades | Win% | Avg R | Expectancy | PF | Conf |
|---|---|---|---|---|---|---|
| >=0.3 | 75 | 37% | -0.11 | -0.11 | 0.2 | HIGH |
| >=0.5 | 71 | 38% | -0.05 | -0.05 | 0.2 | HIGH |
| >=0.6 | 65 | 40% | -0.05 | -0.05 | 0.9 | HIGH |
| >=0.7 | 56 | 39% | -0.13 | -0.13 | 0.8 | HIGH |
| >=0.8 | 5 | 60% | +0.17 | +0.17 | 1.5 | LOW |

## EV Threshold Analysis

| Filter | Trades | Win% | Avg R | Expectancy | PF |
|---|---|---|---|---|---|
| all | 84 | 38% | -0.14 | -0.14 | 17.2 |
| ev > 0 | 2 | 50% | -0.51 | -0.51 | 0.1 |

## Combined Filter Analysis

| Filter | Trades | Retained | Win% | Avg R | Expectancy | PF | Conf |
|---|---|---|---|---|---|---|---|
| score>=0.55 + regime=RANGE | 28 | 33% | 50% | +0.33 | +0.33 | 1.8 | MEDIUM |
| score>=0.6 + regime!=TRANSITIONAL | 22 | 26% | 45% | +0.14 | +0.14 | 0.2 | MEDIUM |
| score>=0.5 + regime!=TRANSITIONAL | 44 | 52% | 43% | +0.09 | +0.09 | 0.3 | HIGH |
| score>=0.6 + conf>=0.5 | 35 | 42% | 43% | +0.06 | +0.06 | 1.2 | HIGH |
| score>=0.5 + conf>=0.5 | 64 | 76% | 42% | +0.05 | +0.05 | 0.2 | HIGH |
| score>=0.6 + conf>=0.7 | 32 | 38% | 41% | -0.14 | -0.14 | 0.8 | HIGH |

## Optimal Threshold Identification

**Best score threshold:** >=0.65
- Expectancy: -0.0203R
- Trades retained: 23 (27%)
- Win rate: 43%
- Improvement over baseline: +0.1177R

**Best combined filter:** score>=0.55 + regime=RANGE
- Expectancy: +0.3262R
- Trades retained: 28 (33%)
- Win rate: 50%
- Improvement over baseline: +0.4642R

## What Should V10 Use As Primary Trade Selection Signal?

| Signal | Practical Value | Evidence |
|---|---|---|
| **Score** | Best single predictor | D1 showed 0.89R calibration gap, monotonic |
| EV | Broken — rejects 97% of trades | D2 showed system is underconfident |
| Confirmation | Moderate signal | Positive correlation +0.09 with R |
| Regime filter | Promising | M1 showed TRANSITIONAL drags performance |

### Recommendations

- **Score** remains the best primary selection signal
- **Regime filter** (exclude TRANSITIONAL) shows promise as a secondary gate
- **EV gate** should NOT be enabled in its current form
- Combined filters need larger samples before production deployment
- Target: 200+ trades before implementing threshold changes

---
*Analysis performed on 84 validated research-ready trades*