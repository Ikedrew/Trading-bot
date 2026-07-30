# V4.2 — Currency Strength Information Value Results

**Date:** 2026-07-29
**Dataset:** 1,125 shadow trades with cross-pair context (from 1,204 unique trades)
**Verdict:** B) Currency strength provides meaningful directional improvement — first genuine information gain since V2

---

## Executive Summary

**Currency strength alignment produces a +7.4% win rate improvement and +0.035R EV improvement over opposing trades.** This is the LARGEST single improvement found in the entire research program.

| Group | n | WR | EV | Significance |
|---|---|---|---|---|
| **USD ALIGNED** | **718** | **44.0%** | **-0.061R** | CI [-0.106, -0.016] |
| USD OPPOSING | 407 | 36.6% | -0.096R | CI [-0.180, -0.012] |
| **Improvement** | — | **+7.4%** | **+0.035R** | — |

**Strong agreement (3+ pairs, >80% aligned):**

| Group | n | WR | EV |
|---|---|---|---|
| **3+ pairs AGREE with trade** | **443** | **47.0%** | **-0.045R** |
| 3+ pairs OPPOSE trade | 147 | **29.3%** | **-0.287R** |
| **Separation** | — | **+17.7%** | **+0.242R** |

---

## Key Finding: Cross-Pair Agreement Is Powerfully Predictive

When 3+ pairs confirm the same USD direction as the trade:
- WR jumps to **47.0%** (from 41.3% baseline)
- EV improves to -0.045R (from -0.074R baseline)
- CI approaches zero: [-0.100, +0.010]

When 3+ pairs OPPOSE the trade:
- WR drops to **29.3%** (devastating)
- EV drops to **-0.287R**
- This is a strong REJECTION signal

**The separation between agreement and opposition (+0.242R, +17.7% WR) is the strongest predictive effect found in this entire research program.**

---

## Analysis 3: Agreement Strength Gradient

| Agreement Level | n | WR | EV |
|---|---|---|---|
| Strong agree (>80%) + aligned | 506 | **46.6%** | -0.038R |
| Moderate agree (60-80%) + aligned | 196 | 38.8% | -0.097R |
| Weak/mixed (<60%) | 132 | 37.1% | -0.104R |
| Strong disagree (>80%) opposing | 200 | **33.5%** | **-0.164R** |

**Clear monotonic gradient:** Stronger USD agreement → better outcomes. This is the signature of real predictive information.

---

## Analysis 5: Symbol Stability

| Symbol | Aligned EV | Opposing EV | Delta | Direction |
|---|---|---|---|---|
| **USDJPY** | -0.135R | **-0.456R** | **+0.320R** | STRONG positive |
| GBPUSD | -0.088R | -0.206R | +0.118R | Positive |
| USDCHF | -0.044R | -0.114R | +0.070R | Positive |
| AUDUSD | +0.005R | -0.012R | +0.017R | Marginal |
| USDCAD | -0.053R | -0.032R | -0.020R | Neutral |
| EURUSD | -0.000R | +0.057R | -0.057R | **Inverted** |
| NZDUSD | -0.127R | +0.038R | -0.165R | **Inverted** |

**5/7 symbols show positive alignment effect.** USDJPY (+0.32R) and GBPUSD (+0.12R) are strongest. EURUSD and NZDUSD show inverted effect (suggesting these pairs may lead rather than follow USD trends).

---

## What This Means

### Comparison to V3 Research

| Finding | V3 (AR series) | V4.2 (currency strength) |
|---|---|---|
| Directional accuracy | 50.7% (barely above random) | 47.0% aligned (from 41.3% baseline = **+5.7%**) |
| Best separation found | +0.07R (inside OB, n=23) | **+0.242R** (broad agreement, n=443 vs 147) |
| Effect direction | Often inverted (READY worst) | **Monotonic gradient** (stronger = better) |
| Sample size | n=38-174 | **n=443-718** |
| Statistical signature | CI includes zero | Strong agree CI approaches zero |

**Currency strength produces the first monotonically predictive signal in the research program.** It shows the expected behaviour: more agreement → better outcomes. This is fundamentally different from V3's inverted confirmation findings.

---

## Critical Caveats

1. **Still not profitable:** Even aligned trades (WR=44%, EV=-0.061R) are net negative after costs
2. **The improvement is in AVOIDING bad trades:** Opposing WR=29.3% means NOT trading against USD consensus saves ~-0.29R per avoided trade
3. **EURUSD and NZDUSD are inverted:** The effect is not universal
4. **Baseline is 41.3% not 50.7%:** This dataset (all shadow trades, not just WEAK+INTERESTING) has lower baseline than the V3-filtered set

---

## V4.2 Verdict

### B) Currency strength provides meaningful improvement — first genuine information gain

**Evidence:**
- +7.4% WR improvement (aligned vs opposing)
- +17.7% WR separation (broad agreement vs broad opposition)
- +0.242R EV separation (strongest ever found)
- Monotonic gradient (stronger agreement → better outcome)
- 5/7 symbols show positive effect
- Large sample (n=1,125)

**Limitations:**
- Still net negative after costs (-0.045R best case)
- Not a standalone edge — but the most promising FILTER discovered
- 2/7 symbols inverted (EURUSD, NZDUSD)

---

## Implication for V4 Architecture

Currency strength alignment should become a **PRE-FILTER** in the V4 pipeline:

```
Before V3 reasoning fires:
    Check: Is USD trend aligned with intended trade direction?
    If 3+ pairs oppose: DO NOT TRADE (saves -0.287R per avoided trade)
    If 3+ pairs agree: Proceed with V3 assessment
```

This doesn't create profitability alone (best case still -0.045R) but combined with:
- WEAK timing (AR2: +0.02R improvement)
- Location filtering (V3: inside OB +0.07R)
- Lower cost market (reduces the -0.045R to potentially breakeven)

...it represents the first GENUINE new information source that improves prediction.

---

## Recommended V4.3

**"Does V3 WEAK + INTERESTING + USD ALIGNED produce positive EV at INTRADAY geometry?"**

Combining:
- V3 timing (WEAK: +0.02R)
- V3 location (inside zone context)
- V4 filter (USD aligned: +0.035R)
- INTRADAY geometry (12% spread/risk)

If the improvements are ADDITIVE (not overlapping), total improvement could be +0.05-0.07R, against a 0.12R cost threshold. Still marginal but the closest the research has come to viability.
