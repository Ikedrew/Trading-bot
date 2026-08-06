# V10-R1: Risk Model Effectiveness

Generated: 2026-08-06T00:21:03.422997+00:00
Sample: 84 research-ready trades

## Executive Summary

**Conclusion: STOPS_NEED_REVIEW**

SL hit rate 81% is very high — stops may be too tight or entries poorly timed

## Key Risk Metrics

| Metric | Value | Assessment |
|---|---|---|
| SL hit rate | 81% | High — entries may be poorly timed |
| TP hit rate | 19% | Normal |
| Avg planned R:R | 2.17 | Realistic |
| Avg realised R | -0.1380 | Negative |
| Geometry violations | 0 | None — good |

## R:R Effectiveness (Does Higher Planned R:R = Better Outcomes?)

| R:R Range | N | Win% | Avg R | Expectancy | PF | Conf |
|---|---|---|---|---|---|---|
| 1-2R | 31 | 48% | +0.10 | +0.10 | 1.7 | HIGH |
| 2-3R | 48 | 29% | -0.31 | -0.31 | 24.8 | HIGH |
| 3-5R | 4 | 50% | +0.00 | +0.00 | 0.0 | LOW |
| 5R+ | 1 | 100% | +0.09 | +0.09 | 999.0 | LOW |

## Stop Loss Analysis

Mean stop distance: 0.052% of price
Median stop distance: 0.038% of price

| Stop Category | N | Win% | Avg R | Expectancy | Conf |
|---|---|---|---|---|---|
| very_tight (<0.02%) | 4 | 25% | -0.88 | -0.88 | LOW |
| tight (0.02-0.05%) | 53 | 36% | -0.21 | -0.21 | HIGH |
| normal (0.05-0.15%) | 25 | 40% | -0.06 | -0.06 | MEDIUM |
| wide (0.15%+) | 2 | 100% | +2.18 | +2.18 | LOW |

## Take Profit Analysis

| Metric | Value |
|---|---|
| TP hit rate | 19% |
| Avg R when TP hit | 2.21 |
| Avg planned R:R (TP trades) | 1.94 |
| Avg planned R:R (SL trades) | 2.23 |

## Exit Type Breakdown

| Exit | N | Win% | Avg R | Avg Duration | Avg Planned R:R |
|---|---|---|---|---|---|
| STOP_LOSS | 68 | 24% | -0.69 | 33 min | 2.2 |
| TAKE_PROFIT | 16 | 100% | +2.21 | 200 min | 1.9 |

## Risk by Regime

| Regime | N | Win% | Avg R | Avg Stop% | Avg R:R | Conf |
|---|---|---|---|---|---|---|
| RANGE | 42 | 40% | -0.01 | 0.040% | 2.0 | HIGH |
| TRANSITIONAL | 30 | 30% | -0.34 | 0.042% | 2.0 | HIGH |
| UNKNOWN | 8 | 50% | -0.28 | 0.165% | 3.2 | LOW |
| TRENDING | 4 | 50% | +0.32 | 0.037% | 2.6 | LOW |

## Instrument Analysis

| Instrument | N | Win% | Avg R | Avg Stop% | Avg R:R | Conf |
|---|---|---|---|---|---|---|
| FX_MAJOR | 81 | 37% | -0.18 | 0.040% | 2.2 | HIGH |
| INDEX | 2 | 100% | +2.18 | 0.566% | 2.2 | LOW |
| FX_JPY | 1 | 0% | -1.04 | 0.034% | 2.1 | LOW |

## What This Means For V10 Development

- **Stop placement needs investigation** — high SL hit rate suggests entries are poorly timed or stops too tight
- Consider: are stops placed below structure or just at fixed distances?
- The entry engine's `_determine_stop()` may need to use wider structural levels
- Do NOT widen stops without understanding WHY they're being hit

---
*Analysis: 84 validated trades, 0 geometry violations*