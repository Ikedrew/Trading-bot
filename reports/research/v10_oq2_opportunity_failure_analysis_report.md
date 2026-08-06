# V10-OQ2: Opportunity vs Outcome Failure Analysis

Generated: 2026-08-06T00:35:34.013447+00:00
Sample: 84 trades (32 winners, 52 losers)

## Executive Summary

**Conclusion: OPPORTUNITY_MODEL_HAS_EDGE_BUT_FAILURE_MODES_EXIST**

Higher quality trades DO win more (43% vs 36%), but delayed failures (42/52) indicate entry timing issues. Regime is a clear differentiator.

## Winners vs Losers — What's Different?

| Metric | Winners (n=32) | Losers (n=52) | Difference |
|---|---|---|---|
| score | 0.6008 | 0.5829 | +0.0179 |
| confirmation | 0.6372 | 0.6314 | +0.0057 |
| rr_planned | 2.2453 | 2.1312 | +0.1142 |
| duration_seconds | 6905.3438 | 1821.4692 | +5083.8745 |

## Component Scores: Winners vs Losers

| Component | Winners | Losers | Diff | Who's Higher? |
|---|---|---|---|---|
| structure_score | 0.350 | 0.517 | -0.167 | losers_higher |
| bias_alignment | 0.582 | 0.496 | +0.086 | winners_higher |
| location_score | 0.500 | 0.583 | -0.083 | losers_higher |
| confirmation_pre | 0.949 | 0.877 | +0.072 | winners_higher |
| bias_stability | 0.554 | 0.494 | +0.060 | winners_higher |
| formation_score | 0.350 | 0.400 | -0.050 | losers_higher |
| h4_alignment | 0.289 | 0.327 | -0.038 | losers_higher |
| market_quality | 0.838 | 0.872 | -0.034 | losers_higher |
| behaviour_score | 0.800 | 0.767 | +0.033 | winners_higher |
| chop_clarity | 0.924 | 0.949 | -0.024 | losers_higher |
| volatility_quality | 0.643 | 0.621 | +0.022 | winners_higher |
| trend_alignment | 0.560 | 0.543 | +0.017 | winners_higher |
| pattern_quality | 0.582 | 0.596 | -0.014 | losers_higher |
| htf_alignment | 0.577 | 0.571 | +0.005 | winners_higher |

## High Quality Trades (top 25% by score)

Score threshold: >= 0.6612
Trades: 21 | Win rate: 43% | Avg R: -0.0169

**When high-quality trades FAIL:**
- Regime distribution: {'TRANSITIONAL': 9, 'RANGE': 3}
- Avg confirmation score: 0.7018
- Avg duration: 44 min
- Avg planned R:R: 1.9

## Low Quality Trades (bottom 25% by score)

Score threshold: <= 0.5348
Trades: 22 | Win rate: 36% | Avg R: -0.2704

**Quality scoring works: YES** — high quality win rate > low quality win rate

## Failure Mode Classification

| Mode | Count | % of Losses | Description |
|---|---|---|---|
| delayed | 42 | 81% | Standard SL hit after development |
| wrong_direction | 4 | 8% | Lost >1.5R (strong adverse move) |
| immediate | 3 | 6% | SL hit within 2 min |
| near_miss | 3 | 6% | Lost <0.5R (close to breakeven) |
| risk_failure | 0 | 0% | Other risk-related |

## Failures by Regime

| Regime | Trades | Win Rate | Avg R | Failure Rate |
|---|---|---|---|---|
| TRANSITIONAL | 30 | 30% | -0.34 | 70% |
| RANGE | 42 | 40% | -0.01 | 60% |
| TRENDING | 4 | 50% | +0.32 | 50% |

## Confirmation Level Impact

| Level | N | Win Rate | Avg R |
|---|---|---|---|
| high_confirmation | 56 | 39% | -0.1325 |
| low_confirmation | 13 | 38% | -0.6356 |

## What Should V10 Investigate Next?

Based on this failure analysis:

1. **Stops are being hit after normal development time**
   - This is NOT an entry problem — it's a stop distance or target problem
   - From R1: 2-3R targets fail (-0.31R) while 1-2R targets work (+0.10R)
2. **Regime is a stronger predictor of failure than opportunity quality**
   - TRANSITIONAL regime has the highest failure rate
   - From D3: filtering TRANSITIONAL flips expectancy positive
   - This suggests the opportunity model should weight regime MORE heavily
3. **Quality scoring IS working** — higher quality = higher win rate
   - But the gap is small — scoring is directionally correct but weak
   - Strengthening confirmation_pre weight would likely improve separation

---
*Analysis: 84 trades, 52 failures classified into 5 modes*