# AR9 — Timeframe Transfer Validation Results

**Date:** 2026-07-29
**Dataset:** 146 matched WEAK+INTERESTING records, simulated at M5/M15/H1/H4 geometry
**Verdict:** The signal collapses at higher timeframes. The M5 "edge" was an artefact of R-scaling, not transferable intelligence.

---

## Critical Finding

**When the same V3 signals are expressed through higher timeframe risk geometry, the raw EV approaches ZERO — not positive.**

| Timeframe | Stop (pips) | Cost% | Raw EV | Net EV | WR |
|---|---|---|---|---|---|
| M5 | 3.5 | 28.6% | **+0.040R** | -0.246R | 50.7% |
| M15 | 10 | 10.0% | +0.000R | -0.100R | 50.7% |
| H1 | 25 | 4.0% | **-0.006R** | -0.046R | 50.7% |
| H4 | 50 | 2.0% | -0.003R | -0.023R | 50.7% |

**The raw EV DECREASES as stop size increases.** At H1 geometry, raw EV is -0.006R (negative BEFORE costs). This demolishes the hypothesis that wider stops "preserve" the signal.

---

## What Actually Happened

The AR4 finding (+0.075R at tight stops) was an R-scaling artefact, exactly as AR7 identified:

```
At M5 (3.5p stop): Same price moves produce large R-multiples → +0.040R raw
At H1 (25p stop): Same price moves produce tiny R-multiples → -0.006R raw

The MARKET doesn't move more at tight stops.
The R-MEASUREMENT amplifies the same small moves.
```

When measured against a 25-pip stop, the actual market movement is so tiny that it's negative EV. The "runners" at M5 were just normal 10-15 pip moves measured against a 3.5-pip denominator.

---

## Analysis 2: No Breakeven Point Exists

| Stop Size | Raw EV | Net EV | Viable? |
|---|---|---|---|
| 2 pips | +0.122R | -0.378R | NO (cost dominant) |
| 3.5 pips | +0.040R | -0.246R | NO |
| 5 pips | +0.016R | -0.184R | NO |
| 10 pips | +0.000R | -0.100R | NO |
| 15 pips | -0.004R | -0.070R | NO |
| 20 pips | -0.005R | -0.055R | NO |
| 25 pips | -0.006R | -0.046R | NO |
| 30 pips | -0.005R | -0.038R | NO |
| 50 pips | -0.003R | -0.023R | NO |

**At NO stop size does the system produce positive net EV.** The curve shows:
- Tight stops: high raw EV (from R-scaling) but crushed by costs
- Wide stops: near-zero raw EV (true signal exposed) PLUS costs → always negative

There is NO sweet spot.

---

## Analysis 4: Win Rate Is Constant

| Stop Size | Win Rate |
|---|---|
| 3.5 pips | 50.7% |
| 10 pips | 50.7% |
| 25 pips | 50.7% |
| 50 pips | 50.7% |

**Win rate is IDENTICAL regardless of stop size.** The V3 directional signal is exactly 50.7% at every timeframe. It doesn't improve with wider stops or longer holds. The direction accuracy IS what it IS — barely above a coin-flip.

---

## Analysis 5: Distribution Reveals the Truth

### M5 (3.5p) — R-scaling creates artificial variety

| Outcome | % | (appears varied because small denominator) |
|---|---|---|
| SL hit | 12% | Some reach -0.5R (only 1.75 pips adverse) |
| Near zero | 26% | |
| Moderate win | 34% | (any 1-2 pip move = 0.3-0.6R) |
| Runner (>0.5R) | 7% | (any 2+ pip move) |

### H1 (25p) — True market behaviour exposed

| Outcome | % | (same moves, larger denominator) |
|---|---|---|
| SL hit | **0%** | (nothing moves 12+ pips against in 5 hours) |
| **Near zero** | **90%** | **THE TRUTH: price barely moves** |
| Moderate win | 3% | |
| Runner | 0% | |

**At H1 geometry, 90% of outcomes are near-zero.** The market simply doesn't move enough within 60 M5 bars (5 hours) to produce meaningful R-multiples at 25-pip risk.

---

## Analysis 6: Symbol Stability at H1

| Symbol | n | Net EV | Positive? |
|---|---|---|---|
| USDJPY | 13 | -0.003R | NO |
| USDCHF | 24 | -0.025R | NO |
| AUDUSD | 28 | -0.034R | NO |
| NZDUSD | 26 | -0.039R | NO |
| GBPUSD | 19 | -0.049R | NO |
| USDCAD | 12 | -0.049R | NO |
| EURUSD | 24 | -0.106R | NO |

**0/7 symbols are positive at H1 geometry.** Even USDJPY (which dominated at M5) is negative at H1.

---

## Analysis 7: Monte Carlo at H1

| Metric | Value |
|---|---|
| P(profit, 146 trades) | **0.0%** |
| Raw EV | -0.006R |
| Net EV | -0.046R |
| Required n for significance | 99,999+ (effect size too small) |

**Zero probability of profit at H1 geometry.** The system is definitively negative.

---

## AR9 Verdict

### The Signal Does NOT Transfer to Higher Timeframes

The V3 "edge" was entirely an R-scaling measurement effect:

1. **At M5 (3.5p stop):** Small market moves (5-15 pips) get scaled into large R-multiples (1.5-4R). Combined with 51% WR and tight stops, this creates a barely-positive raw EV.

2. **At H1 (25p stop):** The same small moves (5-15 pips) become tiny R-multiples (0.2-0.6R). The 51% WR can't overcome costs at these smaller R-values.

3. **The market moves the same amount regardless of how we measure it.** V3 doesn't identify when the market WILL move — it just measures the SAME small movements against different denominators.

---

## Complete AR Series Conclusion (AR1-AR9)

| Experiment | Key Finding |
|---|---|
| AR1 | READY pipeline anti-predictive |
| AR2 | WEAK timing optimal |
| AR3 | +0.04R doesn't survive costs |
| AR4 | +0.015R at 20p (appeared positive) |
| AR5 | "Runners" are R-scaling artefact |
| AR6 | Cannot predict which trades expand |
| AR7 | Expansion = measurement, not market |
| AR8 | Edge statistically indistinguishable from zero |
| **AR9** | **Signal does NOT transfer to higher TF — it's R-scaling, not intelligence** |

---

## Final Architecture Assessment

| V3 Component | Conclusion |
|---|---|
| Market Understanding | Produces 50.7% directional accuracy (barely above random) |
| Market Context | Does not improve direction at any timeframe |
| Opportunity Assessment | INTERESTING is marginally better than HIGH (but neither is profitable) |
| Horizon Assessment | Doesn't matter — same WR at all horizons |
| Entry Assessment | WEAK > VALID (timing matters, but insufficient for profitability) |
| Risk Assessment | Cost calculation is correct — but no geometry makes it viable |
| Execution Assessment | Mechanically sound but has no edge to execute |

---

## Definitive Project Conclusion

**The V3 architecture correctly identifies direction 50.7% of the time. This is statistically real but economically insufficient. No combination of timing, geometry, horizon, or cost reduction converts this 0.7% directional edge into a tradeable system.**

**Remaining viable paths:**
1. **Different signal source** — order flow, news, cross-pair correlation (not candlestick structure)
2. **Different market** — lower-spread instruments where 0.7% direction edge might survive
3. **Different timescale** — H4/Daily entries where 50.7% might compound differently
4. **Accept null result** — the current information set is exhausted
