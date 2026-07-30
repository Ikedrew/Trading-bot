# AR8 — Edge Stability and Regime Robustness Results

**Date:** 2026-07-29
**Dataset:** 146 WEAK+INTERESTING records, SL=0.5R, 20p absolute stop
**Verdict:** C) Edge unstable — not reliable for deployment

---

## Executive Summary

The V3 edge (+0.065R raw, +0.005R net) is **statistically indistinguishable from zero** and **fails multiple robustness tests**.

| Key Metric | Value | Implication |
|---|---|---|
| Net EV | +0.005R | Barely positive |
| 95% CI | [-0.015, +0.145] | **Includes zero** |
| P(profit over 146 trades) | **52%** | Coin-flip |
| Symbols positive | **2/7** | Not universal |
| Median drawdown | **5.5R** | Severe |
| Cost sensitivity | Fails at +25% cost increase | Extremely fragile |

---

## Analysis 1: Time Stability

| Period | n | WR | Raw EV | Net EV | Max DD |
|---|---|---|---|---|---|
| Period 1 (earliest) | 48 | 41.7% | +0.091R | +0.031R | -1.50R |
| Period 2 (middle) | 48 | 43.8% | +0.035R | **-0.025R** | -1.81R |
| Period 3 (latest) | 50 | 66.0% | +0.069R | +0.009R | -0.15R |

**Period 2 is NET NEGATIVE.** The edge disappears in the middle third of the data. It reappears in Period 3 but with a different WR profile (66% vs 42%). This inconsistency suggests the edge is epoch-dependent.

---

## Analysis 2: Rolling Windows

| Metric | Value |
|---|---|
| Positive EV windows (20-trade) | 85% (11/13) |
| Negative EV windows | 15% (2/13) |
| Best window | +0.238R |
| Worst window | -0.053R |
| Longest negative streak | 1 window |

The rolling view looks more encouraging — 85% of 20-trade windows are positive. But the net EV per window is so small that any single bad window can erase multiple good ones.

---

## Analysis 3: Symbol Stability — CRITICAL FAILURE

| Symbol | n | WR | Raw EV | Net EV | Status |
|---|---|---|---|---|---|
| **USDJPY** | **13** | **61.5%** | **+0.614R** | **+0.554R** | **Dominant** |
| USDCHF | 24 | 66.7% | +0.074R | +0.014R | Marginal |
| AUDUSD | 28 | 53.6% | +0.052R | -0.008R | Breakeven |
| NZDUSD | 26 | 50.0% | +0.008R | -0.052R | Negative |
| GBPUSD | 19 | 47.4% | +0.008R | -0.052R | Negative |
| EURUSD | 24 | 29.2% | -0.055R | -0.114R | Negative |
| USDCAD | 12 | 50.0% | -0.064R | -0.124R | Negative |

**Only 2/7 symbols are net positive.** USDJPY alone contributes +0.554R × 13 trades = +7.2R to the total. Without USDJPY, the system is net NEGATIVE.

**This is the most damning finding:** The "edge" is concentrated in a single symbol with only 13 observations. It's not a universal market signal — it's a USDJPY anomaly.

---

## Analysis 4: Direction & Horizon

| Split | n | Net EV |
|---|---|---|
| BULLISH | 76 | +0.001R |
| BEARISH | 68 | +0.009R |
| SCALP | 110 | +0.005R |
| INTRADAY | 36 | +0.006R |

Direction and horizon splits both show near-zero net EV. No significant differentiation.

---

## Analysis 5: Cost Sensitivity — EXTREMELY FRAGILE

| Scenario | Cost/Trade | Net EV | Viable? |
|---|---|---|---|
| Current (1.2p/20p) | 0.060R | **+0.005R** | Barely |
| **+25% cost** | 0.075R | **-0.010R** | **NO** |
| +50% cost | 0.090R | -0.025R | NO |
| ECN (0.8p/20p) | 0.040R | +0.025R | YES |
| Institutional (0.5p/20p) | 0.025R | +0.040R | YES |
| 30p stop (1.2p/30p) | 0.040R | +0.025R | YES |

**A 25% increase in execution costs (1.2→1.5 pips) makes the system unprofitable.** The margin of safety is essentially zero.

---

## Analysis 6: Monte Carlo — SEVERE RISK

Over 10,000 simulations of 146 trades:

| Metric | Value | Implication |
|---|---|---|
| **Probability of profit** | **52.3%** | **Coin-flip** |
| Median final P&L | +0.37R | Barely positive |
| 5th percentile P&L | **-8.51R** | Significant loss |
| Median max drawdown | **5.53R** | Substantial |
| 95th percentile drawdown | **10.82R** | Devastating |
| Worst-case drawdown (99th) | 13.20R | Account-threatening |
| Median losing streak | 8 trades | Expected |
| 95th percentile streak | 13 trades | Painful |

**52% probability of profit = no better than random.** The system has a coin-flip chance of making money over 146 trades, with a median drawdown of 5.5R. This is not a tradeable edge.

---

## Analysis 7: Statistical Power

| Metric | Value |
|---|---|
| Raw EV | +0.065R |
| Standard deviation | 0.495R |
| 95% CI | **[-0.015, +0.145]** |
| t-statistic | 1.59 (p ≈ 0.11) |
| Required n for significance | **~222** |
| Classification | **Not statistically significant** |

The effect size (+0.065R) with standard deviation (0.495R) means we need at least 222 trades to detect this effect with 95% confidence. At n=146, we cannot reject the null hypothesis that the true EV is zero.

---

## AR8 Verdict

### C) Edge unstable — not reliable

**Evidence:**
1. **Not statistically significant** — CI [-0.015, +0.145] includes zero
2. **52% Monte Carlo profit probability** — no better than coin-flip
3. **Only 2/7 symbols positive** — edge concentrated in USDJPY (n=13)
4. **Fails at +25% cost increase** — zero margin of safety
5. **5.5R median drawdown** for a +0.005R net edge — terrible risk/reward
6. **One period (middle third) is net negative** — edge disappears temporarily

**What this means for V3:**

The system has discovered that:
- It can identify direction slightly above random (51%)
- WEAK confirmation timing is superior to VALID
- Tight stops with no TP create an asymmetric payoff profile
- The net result is barely distinguishable from zero

**The edge does NOT survive statistical scrutiny.** With 52% profit probability and 5.5R drawdown risk for a +0.005R expected gain, this is not a tradeable system.

---

## Recommended Path Forward

Given the AR1-AR8 research series conclusion, the options are:

| Option | Description | Confidence |
|---|---|---|
| **A) Continue collecting** | Need n=222+ for statistical power | MEDIUM — may confirm zero |
| **B) Focus on USDJPY only** | Only symbol with clear positive EV | LOW — n=13, likely anomaly |
| **C) Change market** | Try indices/crypto with lower spread% | MEDIUM — structural cost issue |
| **D) Accept null result** | The M5 FX architecture cannot overcome costs | HIGH — consistent with V2 |
| **E) Fundamentally different entry** | H1/H4 entries (not M5 timing) | UNKNOWN — unexplored |

---

## Complete AR Series Conclusion (AR1-AR8)

The V3 research program has reached a definitive conclusion:

> **The M5 entry mechanism on FX pairs, even with V3 market intelligence (context, location, timing, horizon), does not produce a reliable, statistically significant positive expected value after transaction costs.**

The architecture is correct. The research methodology is sound. The data pipeline works. The analysis is rigorous. **The answer is simply: no exploitable edge exists in this configuration.**
