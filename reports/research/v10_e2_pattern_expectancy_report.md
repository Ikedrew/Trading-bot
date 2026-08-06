# V10-E2: Pattern Expectancy

Generated: 2026-08-05T23:08:03.893573+00:00
Sample: 84 research-ready trades | 13 patterns analysed
Baseline (V10-E1): -0.1380 R/trade

## Executive Summary

**Conclusion: SOME_PATTERNS_SHOW_PROMISE**

1 medium+ confidence positive, 2 promising but low-sample

## Pattern Performance Table

| Pattern | N | Conf | Win% | Avg R | Exp R | vs Base | PF | Type |
|---|---|---|---|---|---|---|---|---|
| TWEEZER_TOP | 29 | MEDIUM | 45% | 0.10 | 0.10 | +0.24 | 1.38 | BALANCED |
| TWEEZER_BOTTOM | 23 | MEDIUM | 35% | -0.10 | -0.10 | +0.04 | 0.84 | BALANCED |
| EVENING_STAR | 10 | MEDIUM | 20% | -0.42 | -0.42 | -0.28 | 0.47 | LOW_WIN_HIGH_REWARD |
| MEAN_REVERSION | 4 | LOW | 50% | -1.37 | -1.37 | -1.23 | 0.27 | HIGH_WIN_LOW_REWARD |
| MORNING_STAR | 4 | LOW | 75% | 0.57 | 0.57 | +0.71 | 4.34 | HIGH_WIN_LOW_REWARD |
| HAMMER | 4 | LOW | 25% | -0.31 | -0.31 | -0.17 | 0.64 | BALANCED |
| TREND_CONTINUATION | 3 | LOW | 67% | 1.41 | 1.41 | +1.55 | 42886.25 | BALANCED |
| THREE_INSIDE_DOWN | 2 | LOW | 50% | 0.23 | 0.23 | +0.37 | 2.16 | BALANCED |
| RECOVERED | 1 | LOW | 0% | -1.00 | -1.00 | -0.86 | 0.00 | BALANCED |
| HANGING_MAN | 1 | LOW | 0% | -1.09 | -1.09 | -0.95 | 0.00 | BALANCED |
| THREE_BLACK_CROWS | 1 | LOW | 0% | -1.04 | -1.04 | -0.90 | 0.00 | BALANCED |
| BULLISH_ENGULFING | 1 | LOW | 0% | -1.00 | -1.00 | -0.86 | 0.00 | BALANCED |
| THREE_INSIDE_UP | 1 | LOW | 0% | -4.29 | -4.29 | -4.15 | 0.00 | BALANCED |

## Exit Breakdown by Pattern

| Pattern | SL Exits | TP Exits | SL Avg R | TP Avg R |
|---|---|---|---|---|
| TWEEZER_TOP | 22 | 7 | -0.63 | 2.40 |
| TWEEZER_BOTTOM | 19 | 4 | -0.56 | 2.09 |
| EVENING_STAR | 9 | 1 | -0.70 | 2.10 |
| MEAN_REVERSION | 4 | 0 | -1.37 | 0.00 |
| MORNING_STAR | 4 | 0 | 0.57 | 0.00 |
| HAMMER | 3 | 1 | -1.04 | 1.88 |
| TREND_CONTINUATION | 1 | 2 | -0.12 | 2.18 |
| THREE_INSIDE_DOWN | 1 | 1 | -1.45 | 1.92 |
| RECOVERED | 1 | 0 | -1.00 | 0.00 |
| HANGING_MAN | 1 | 0 | -1.09 | 0.00 |
| THREE_BLACK_CROWS | 1 | 0 | -1.04 | 0.00 |
| BULLISH_ENGULFING | 1 | 0 | -1.00 | 0.00 |
| THREE_INSIDE_UP | 1 | 0 | -4.29 | 0.00 |

## Confidence Assessment

| Confidence | Patterns |
|---|---|
| HIGH (>=30 trades) | None |
| MEDIUM (10-29) | TWEEZER_TOP, TWEEZER_BOTTOM, EVENING_STAR |
| LOW (<10) | MEAN_REVERSION, MORNING_STAR, HAMMER, TREND_CONTINUATION, THREE_INSIDE_DOWN, RECOVERED, HANGING_MAN, THREE_BLACK_CROWS, BULLISH_ENGULFING, THREE_INSIDE_UP |

## Statistical Detail (High + Medium Confidence Only)

### TWEEZER_TOP (n=29, MEDIUM)

- Win rate: 44.8% | Avg R: 0.1036 | Median R: -1.0000
- Expectancy: 0.1036 R/trade | vs baseline: +0.2416
- 95% CI: [-0.5166, 0.7238]
- Avg winner: 1.63R | Avg loser: -1.14R
- Exit: 22 SL / 7 TP
- **Signal: inconclusive**

### TWEEZER_BOTTOM (n=23, MEDIUM)

- Win rate: 34.8% | Avg R: -0.0989 | Median R: -1.0000
- Expectancy: -0.0989 R/trade | vs baseline: +0.0391
- 95% CI: [-0.6175, 0.4197]
- Avg winner: 1.45R | Avg loser: -0.93R
- Exit: 19 SL / 4 TP
- **Signal: inconclusive**

### EVENING_STAR (n=10, MEDIUM)

- Win rate: 20.0% | Avg R: -0.4163 | Median R: -1.0119
- Expectancy: -0.4163 R/trade | vs baseline: -0.2783
- 95% CI: [-1.2509, 0.4183]
- Avg winner: 2.13R | Avg loser: -1.05R
- Exit: 9 SL / 1 TP
- **Signal: inconclusive**

## What This Means For V10 Development

### Patterns Worth Monitoring

- **TWEEZER_TOP**: 0.10R expectancy, 45% win rate 
- **MORNING_STAR**: 0.57R expectancy, 75% win rate (promising but insufficient sample)
- **TREND_CONTINUATION**: 1.41R expectancy, 67% win rate (promising but insufficient sample)
- **THREE_INSIDE_DOWN**: 0.23R expectancy, 50% win rate (promising but insufficient sample)

### Patterns Requiring More Data

- MEAN_REVERSION: only 4 trades — cannot assess
- MORNING_STAR: only 4 trades — cannot assess
- HAMMER: only 4 trades — cannot assess
- TREND_CONTINUATION: only 3 trades — cannot assess
- THREE_INSIDE_DOWN: only 2 trades — cannot assess
- RECOVERED: only 1 trades — cannot assess
- HANGING_MAN: only 1 trades — cannot assess
- THREE_BLACK_CROWS: only 1 trades — cannot assess
- BULLISH_ENGULFING: only 1 trades — cannot assess
- THREE_INSIDE_UP: only 1 trades — cannot assess

### Patterns That May Need Investigation

- **EVENING_STAR**: -0.42R negative expectancy (10 trades)

### Recommendations

- Do NOT disable any patterns based on this sample alone
- Continue forward collection to increase per-pattern sample sizes
- Patterns with fewer than 10 trades cannot be reliably assessed
- When any pattern reaches 30+ trades with consistent negative R, investigate further

---
*Data integrity: All 84 trades passed 7-check validation pipeline*