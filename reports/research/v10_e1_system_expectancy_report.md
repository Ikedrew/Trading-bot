# V10-E1: True System Expectancy

Generated: 2026-08-05T22:57:18.787542+00:00
Sample: 84 research-ready trades

## Executive Summary

**Conclusion: INCONCLUSIVE**

95% CI spans zero [-0.4755, 0.1995] — cannot confirm edge with current sample

## Core Metrics

| Metric | Value |
|---|---|
| Trade count | 84 |
| Win rate | 38.1% |
| Loss rate | 61.9% |
| Total net profit | $1637.94 |
| Average winner | $54.3537 |
| Average loser | $-1.9496 |
| Largest winner | $871.9700 |
| Largest loser | $-58.0000 |
| Profit factor | 17.16 |

## R-Multiple Analysis

| Metric | Value |
|---|---|
| Average R | -0.1380 |
| Median R | -1.0000 |
| Std Dev R | 1.5782 |
| Avg Winner R | 1.5224 |
| Avg Loser R | -1.1598 |
| **Expectancy (R/trade)** | **-0.1380** |

## Confidence Interval (95%)

| Metric | Value |
|---|---|
| Standard Error | 0.1722 |
| CI Lower | -0.4755 |
| CI Upper | 0.1995 |
| Sample adequate (n>=30) | Yes |

## R-Multiple Distribution

| Bucket | Count | % |
|---|---|---|
| < -2R | 2 | 2% |
| -2R to -1R | 30 | 36% |
| -1R to 0R | 20 | 24% |
| 0R to 1R | 8 | 10% |
| 1R to 2R | 12 | 14% |
| 2R+ | 12 | 14% |

Longest winning streak: 6
Longest losing streak: 16

## By Exit Reason

| Exit | Count | Avg R | Win Rate |
|---|---|---|---|
| STOP_LOSS | 68 | -0.6914 | 23.5% |
| TAKE_PROFIT | 16 | 2.2142 | 100.0% |

## By Pattern

| Pattern | Count | Avg R | Win Rate |
|---|---|---|---|
| TWEEZER_TOP | 29 | 0.1036 | 44.8% |
| TWEEZER_BOTTOM | 23 | -0.0989 | 34.8% |
| EVENING_STAR | 10 | -0.4163 | 20.0% |
| MEAN_REVERSION | 4 | -1.3705 | 50.0% |
| MORNING_STAR | 4 | 0.5704 | 75.0% |
| HAMMER | 4 | -0.3075 | 25.0% |
| TREND_CONTINUATION | 3 | 1.4096 | 66.7% |
| THREE_INSIDE_DOWN | 2 | 0.2311 | 50.0% |
| RECOVERED | 1 | -1.0000 | 0.0% |
| HANGING_MAN | 1 | -1.0909 | 0.0% |
| THREE_BLACK_CROWS | 1 | -1.0357 | 0.0% |
| BULLISH_ENGULFING | 1 | -1.0000 | 0.0% |
| THREE_INSIDE_UP | 1 | -4.2903 | 0.0% |

## What This Means For Development

Cannot confirm or deny edge with current sample size.
- Continue collecting forward data (target: 200+ validated trades)
- Do NOT increase position size or risk
- Monitor per-pattern breakdown for early signals
- The R-distribution and exit analysis provide directional insight

---
*Data integrity: All 84 trades passed 7-check validation pipeline (data_quality_score >= 70)*