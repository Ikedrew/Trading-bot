# V10-R2: Stop Placement Effectiveness

Generated: 2026-08-06T00:47:09.558827+00:00
Sample: 84 trades | SL exits: 68 (81%) | TP exits: 16 (19%)

## Executive Summary

**Conclusion: STOPS_TOO_WIDE**

Mean ATR ratio 2.91 — stops are very far from entry

## Stop Distance Summary

| Metric | Value |
|---|---|
| Mean stop distance | 13.6 pips |
| Median stop distance | 3.4 pips |
| Min stop distance | 1.1 pips |
| Max stop distance | 435.0 pips |
| Mean stop % of price | 0.052% |
| Mean ATR ratio (est) | 2.91 |
| Median ATR ratio | 0.90 |
| % too tight (<0.5 ATR) | 5% |
| % normal (0.5-1.5 ATR) | 92% |
| % wide (>1.5 ATR) | 4% |

## Performance by ATR Ratio Bucket

| Bucket | N | Win% | Avg R | Conf |
|---|---|---|---|---|
| < 0.5 ATR (tight) | 4 | 25% | -0.83 | LOW |
| 0.5-1.0 ATR | 52 | 33% | -0.37 | HIGH |
| 1.0-1.5 ATR | 25 | 48% | +0.31 | MEDIUM |
| > 1.5 ATR (wide) | 3 | 67% | +1.11 | LOW |

## Time to Stop Loss

| Duration | Count | % of SL Exits |
|---|---|---|
| <5 min | 6 | 9% |
| 5-30 min | 39 | 57% |
| 30-120 min | 20 | 29% |
| 2+ hours | 3 | 4% |

## Winners vs Losers — Stop Characteristics

| Metric | Winners | Losers | Interpretation |
|---|---|---|---|
| Mean stop (pips) | 30.3 | 3.4 | Winners wider |
| Mean ATR ratio | 6.24 | 0.86 | |
| Mean duration (min) | 115 | 34 | Winners hold 3.4x longer |

## Loss Classification

| Type | Count | % | Description |
|---|---|---|---|
| Correct stop | 45 | 66% | Standard -1R loss (stop at right level) |
| Stop too tight | 19 | 28% | Lost <0.5R (barely grazed, wider stop would help) |
| Wrong direction | 4 | 6% | Lost >1.5R (strong adverse move / gap) |

## Regime Analysis

| Regime | N | Mean Stop | SL Rate | Win% | Avg R |
|---|---|---|---|---|---|
| RANGE | 42 | 3.5 pips | 79% | 40% | -0.01 |
| TRANSITIONAL | 30 | 3.6 pips | 87% | 30% | -0.34 |
| TRENDING | 4 | 3.5 pips | 75% | 50% | +0.32 |

## By Symbol

| Symbol | N | Mean Stop | Median Stop | Min | Max |
|---|---|---|---|---|---|
| NZDUSD | 21 | 3.0 | 3.1 | 1.1 | 4.2 |
| USDCAD | 17 | 4.1 | 3.8 | 2.5 | 9.0 |
| USDCHF | 15 | 3.2 | 3.1 | 1.7 | 5.5 |
| AUDUSD | 13 | 3.1 | 3.2 | 1.4 | 4.5 |
| GBPUSD | 8 | 4.5 | 4.1 | 2.5 | 6.5 |
| EURUSD | 7 | 3.4 | 3.3 | 2.9 | 4.1 |
| US500 | 2 | 429.0 | 429.0 | 423.0 | 435.0 |
| USDJPY | 1 | 5.6 | 5.6 | 5.6 | 5.6 |

## Final Assessment

### 1. Are V10 stops structurally valid?
Mostly YES — 45/68 losses are standard -1R (stop placed correctly).

### 2. Are stops too close for market volatility?
NO — mean ATR ratio 2.91 is reasonable.

### 3. Are wide values a calculation issue or structural?
The range (1.1 to 435.0 pips) suggests structural placement (varying by market conditions), not a fixed formula.

### 4. Should stop logic be investigated further?
**YES** — 81% SL hit rate is too high. The combination of 19 'too tight' stops and 59 trades stopped after normal development suggests stops need ~20-50% more room.

---
*ATR ratios estimated from typical M5 ranges per instrument. Actual ATR at entry not available in dataset.*