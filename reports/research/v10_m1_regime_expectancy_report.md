# V10-M1: Regime Predicts Outcomes

Generated: 2026-08-05T23:15:25.579171+00:00
Sample: 84 research-ready trades | Regime coverage: 90% (76/84)
Baseline (V10-E1): -0.1380 R/trade

## Executive Summary

**Conclusion: SOME_REGIMES_SHOW_PROMISE**

Expectancy spread of 0.66R between best (TRENDING) and worst (TRANSITIONAL) regime

## Regime Performance Table

| Regime | N | Conf | Win% | Avg R | Exp R | vs Base | TP Rate | PF |
|---|---|---|---|---|---|---|---|---|
| RANGE | 42 | HIGH | 40% | -0.01 | -0.01 | +0.13 | 21% | 1.11 |
| TRANSITIONAL | 30 | HIGH | 30% | -0.34 | -0.34 | -0.20 | 13% | 0.08 |
| TRENDING | 4 | LOW | 50% | 0.32 | 0.32 | +0.46 | 25% | 1.89 |

## Statistical Detail

### RANGE (n=42, HIGH)

- Win rate: 40.5% | Avg R: -0.0078 | Median R: -1.0000
- Expectancy: -0.0078 R/trade | vs baseline: +0.1302
- 95% CI: [-0.4812, 0.4657]
- Avg winner: 1.57R | Avg loser: -1.08R
- Exits: 33 SL / 9 TP
- **Signal: inconclusive**

### TRANSITIONAL (n=30, HIGH)

- Win rate: 30.0% | Avg R: -0.3427 | Median R: -1.0000
- Expectancy: -0.3427 R/trade | vs baseline: -0.2047
- 95% CI: [-0.8760, 0.1905]
- Avg winner: 1.58R | Avg loser: -1.17R
- Exits: 26 SL / 4 TP
- **Signal: inconclusive**

### TRENDING (n=4, LOW)

- Win rate: 50.0% | Avg R: 0.3177 | Median R: 0.0869
- Expectancy: 0.3177 R/trade | vs baseline: +0.4557
- 95% CI: [-1.2185, 1.8538]
- Avg winner: 1.64R | Avg loser: -1.00R
- Exits: 3 SL / 1 TP
- **Signal: inconclusive**

## Pattern x Regime Cross-Table

| Pattern | Regime | Count | Avg R | Win% |
|---|---|---|---|---|
| MORNING_STAR | RANGE | 4 | 0.57 | 75% |
| TWEEZER_TOP | TRANSITIONAL | 13 | 0.18 | 46% |
| TWEEZER_TOP | RANGE | 16 | 0.04 | 44% |
| HAMMER | RANGE | 3 | -0.08 | 33% |
| TWEEZER_BOTTOM | RANGE | 14 | -0.08 | 36% |
| EVENING_STAR | RANGE | 4 | -0.22 | 25% |
| TWEEZER_BOTTOM | TRANSITIONAL | 8 | -0.29 | 25% |
| EVENING_STAR | TRANSITIONAL | 4 | -1.09 | 0% |

## Profit/Loss Concentration

| Regime | Winners | Losers | Net Direction |
|---|---|---|---|
| RANGE | 17 | 25 | Losing |
| TRANSITIONAL | 9 | 21 | Losing |
| TRENDING | 2 | 2 | Neutral |

## What This Means For V10 Development

### Regimes Worth Monitoring

- **RANGE**: -0.01R expectancy, 40% win rate 
- **TRENDING**: 0.32R expectancy, 50% win rate (promising but insufficient sample)

### Regimes Requiring Investigation

- **TRANSITIONAL**: -0.34R (30 trades) — underperforms baseline by 0.20R

### Recommendations

- Do NOT implement regime filters based on this sample alone
- Continue collecting data with regime classification
- When any regime reaches 30+ trades, reassess with statistical confidence
- Cross-reference regime findings with V10-E2 (pattern) results for interaction effects

---
*Data integrity: 76 trades with valid regime (90% coverage)*