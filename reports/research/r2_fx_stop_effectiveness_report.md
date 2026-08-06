# V10-R2-FX: FX Stop Effectiveness Analysis

Generated: 2026-08-06T01:12:09.566829+00:00
Dataset: FX_ONLY | 82 trades | 30 winners | 52 losers
Confidence: HIGH

## Executive Summary

**Conclusion (B): FX stops are too tight. Recommend wider structural stops.**

32% of SL exits are stop hunts/noise. Wider stops would save ~25 trades.

## 1. Loss Classification

Total SL exits: 68

| Category | Count | % | Description |
|---|---|---|---|
| Normal invalidation | 42 | 62% | Market structure broke against thesis |
| Stop hunt / noise | 22 | 32% | Stop barely grazed; trade would have worked with room |
| Wrong direction | 4 | 6% | Strong adverse move; wider stop would not help |

## 2. Stop Distance Summary

| Metric | Value |
|---|---|
| Mean stop | 3.5 pips |
| Median stop | 3.3 pips |
| Min stop | 1.1 pips |
| Max stop | 9.0 pips |
| Win rate | 37% |
| Avg R | -0.1944 |

## 3. Counterfactual Stop Simulation

*If stops were wider, how many losses would become winners?*

| Scenario | Trades Saved | New Win Rate | New Avg R | Improvement |
|---|---|---|---|---|
| 1.5x_wider | 25 | 67% | +0.42 | +0.61R |
| 2.0x_wider | 25 | 67% | +0.42 | +0.61R |

*Note: These are estimates based on loss classification heuristics, not bar-by-bar replay.*

## 4. Take Profit Scenario Analysis

*What if TP was set at different R-multiples?*

| TP Target | Hit Rate | Sim Win% | Sim Avg R | Expectancy |
|---|---|---|---|---|
| 1.0R | 27% | 27% | -0.43 | -0.43 |
| 1.5R | 22% | 22% | -0.42 | -0.42 |
| 2.0R | 12% | 12% | -0.60 | -0.60 |
| 2.5R | 2% | 2% | -0.88 | -0.88 |
| 3.0R | 1% | 1% | -0.92 | -0.92 |

**Best TP scenario: 1.5R** (expectancy = -0.4180R)

## 5. By Symbol

| Symbol | N | Mean Stop | Win% | Avg R | SL Rate |
|---|---|---|---|---|---|
| NZDUSD | 21 | 3.0 pips | 48% | +0.12 | 67% |
| USDCAD | 17 | 4.1 pips | 41% | +0.15 | 88% |
| USDCHF | 15 | 3.2 pips | 33% | -0.50 | 87% |
| AUDUSD | 13 | 3.1 pips | 23% | -0.33 | 85% |
| GBPUSD | 8 | 4.5 pips | 38% | -0.73 | 88% |
| EURUSD | 7 | 3.4 pips | 29% | -0.34 | 100% |

## 6. Final Recommendation

### Conclusion: FX stops are too tight. Recommend wider structural stops.

### Evidence:
- 22/68 losses (32%) are stop hunt/noise
- 42/68 losses (62%) are normal invalidation
- 4/68 losses (6%) are wrong direction
- Wider stops could save ~25 trades
- Best TP target: 1.5R (improves expectancy by -0.22R)

### Actionable Investigation:
- Review `entry_engine._determine_stop()` — prefer H1 structural levels over M15/BOS
- Consider minimum stop distance of 5 pips (1.0-1.5 ATR) for FX
- Test wider stops in shadow mode before production

---
*FX-only analysis: 82 trades across 6 symbols*