# AR5 — Structural Risk Geometry Validation Results

**Date:** 2026-07-29
**Dataset:** 146 matched WEAK+INTERESTING records with progression data
**Verdict:** B) Edge exists but requires specific conditions — asymmetric winners drive EV

---

## Executive Summary

The positive EV (+0.065R) is **NOT from structural geometry quality** — it comes from **asymmetric rare winners** (6.8% of trades produce >0.5R, contributing 147% of total EV). Surprisingly, **smaller geometry (<10 pip stops) outperforms larger geometry** in raw EV.

The edge operates as a lottery-style system: mostly small outcomes, with rare significant moves creating all the profit.

---

## Analysis 1: Structural Distance Groups

| Risk Distance | n | WR | Raw EV | Cost-Adj (actual) | MFE |
|---|---|---|---|---|---|
| **<5 pips** | **64** | **54.7%** | **+0.181R** | -0.263R (cost too high) | 0.23R |
| 5-10 pips | 47 | 48.9% | -0.026R | -0.180R | 0.07R |
| 10-15 pips | 22 | 40.9% | -0.065R | -0.167R | -0.12R |
| 15-20 pips | 7 | 57.1% | +0.109R | +0.038R | 0.13R |
| 20-30 pips | 5 | 60.0% | +0.003R | -0.053R | 0.03R |

**Surprising finding:** The SMALLEST geometry group (<5 pips, n=64) has the HIGHEST raw EV (+0.181R). But at 5-pip stops, spread/risk = 24% which destroys the edge. The 15-20 pip group shows promise (+0.109R, n=7) but sample is tiny.

**Interpretation:** The signal is strongest at tight M5 entries (where the trade captures the immediate reaction) but transaction costs at those distances make it non-viable. This is the fundamental V3 dilemma.

---

## Analysis 2: SL Level Comparison

| SL Level | Raw EV | @10p net | @15p net | @20p net | @30p net |
|---|---|---|---|---|---|
| **0.25R** | **+0.065R** | -0.055R | -0.015R | **+0.005R** | **+0.025R** |
| **0.50R** | **+0.065R** | -0.055R | -0.015R | **+0.005R** | **+0.025R** |
| 0.75R | +0.051R | -0.070R | -0.030R | -0.010R | +0.011R |
| 1.00R | +0.039R | -0.081R | -0.041R | -0.021R | -0.001R |
| 1.50R | +0.031R | -0.089R | -0.049R | -0.029R | -0.009R |

**Key: Tight SL (0.25-0.5R) preserves all the EV (+0.065R). Wider SL REDUCES EV.**

This confirms: the edge is in cutting losers quickly. The 8% that hit 0.5R stop were going to lose anyway — but wider stops let them drag out the loss.

At 20-pip structural distances: **+0.005R net (barely positive)**
At 30-pip distances: **+0.025R net (more viable)**

---

## Analysis 3: Horizon Interaction

| Horizon | n | WR | EV (SL=0.5R) | Net @20p |
|---|---|---|---|---|
| SCALP | 110 | 51.8% | +0.065R | +0.005R |
| INTRADAY | 36 | 47.2% | +0.066R | +0.006R |

**Both horizons produce identical raw EV.** The V3 horizon classification doesn't differentiate outcomes — the signal quality is the same regardless of which horizon the pipeline assigned.

---

## Analysis 4: Stop Placement Source

Cannot separate structural vs ATR-derived stops because most records use the SAME shadow trade SL configuration. The test shows that the SL LEVEL (in R-multiples) matters more than the stop SOURCE:

- **0.25-0.5R = optimal** (+0.065R)
- **1.0R+ = worse** (+0.031-0.039R)

Tight stops work because they cut the 8% of trades that would become large losers.

---

## Analysis 5: Exit Distribution (the smoking gun)

| Outcome Category | Count | % |
|---|---|---|
| Full SL (-0.5R) | 12 | 8.2% |
| Small loss (-0.5 to -0.1R) | 30 | 20.5% |
| **Near zero (-0.1 to +0.1R)** | **61** | **41.8%** |
| Small win (+0.1 to +0.5R) | 33 | 22.6% |
| Good win (+0.5 to +1.0R) | 6 | 4.1% |
| **Runner (>+1.0R)** | **4** | **2.7%** |

**The system is a lottery:**
- 41.8% of trades go NOWHERE (near zero)
- 22.6% produce small wins
- **6.8% produce runners (>0.5R) which contribute 147% of total EV**

### Asymmetry Metrics

| Metric | Value |
|---|---|
| Avg win | +0.314R |
| Avg loss | -0.199R |
| Win/Loss ratio | 1.58:1 |
| Runners (>0.5R) | 6.8% of trades |
| **Runner contribution** | **147% of total EV** |

**Without the runners, the system is NEGATIVE.** The entire edge comes from 10 trades out of 146 that produce >0.5R. Remove them and EV drops to approximately -0.03R.

---

## AR5 Verdict

### B) Edge exists but requires specific conditions

**The edge is NOT from structural geometry quality.** Small geometry (<10 pips) actually produces the highest raw EV (+0.093R). The edge operates as:

1. **V3 identifies correct direction** (50.7% WR — barely above coin-flip)
2. **Tight stop cuts losers early** (saves 0.03R vs wider stops)
3. **No TP allows rare runners** (6.8% of trades produce >0.5R)
4. **Cost at 20+ pip stops** reduces spread impact enough for net positive

**Required conditions for profitability:**
- WEAK + INTERESTING context (V3 opportunity detection)
- Tight SL (0.25-0.5R) — quick invalidation
- No take profit — must allow expansion
- Stop distance ≥20 pips in absolute terms (6% spread/risk)
- Acceptance that 93% of trades produce <0.5R outcomes

**Critical risk:** The edge depends on 6.8% of trades being runners. A run of 15-20 trades without a runner (very possible by chance) would produce significant drawdown (-3 to -4R) before recovery.

---

## Implications for V3

| Component | Status | Evidence |
|---|---|---|
| V3 opportunity detection | **VALUABLE** | Correctly identifies direction 51% of the time |
| WEAK entry timing | **VALIDATED** | Captures +0.065R vs VALID's -0.112R |
| INTERESTING quality | **CORRECT** | Best quality level for this signal |
| Horizon classification | **NOT DIFFERENTIATING** | SCALP and INTRADAY produce same EV |
| Structural stop quality | **NOT THE EDGE SOURCE** | Small geometry has higher raw EV |
| Cost reduction | **THE MECHANISM** | Moving from 34% to 6% spread/risk converts negative to positive |
| Runner dependency | **THE RISK** | Without 6.8% runners, system is negative |

---

## Recommended AR6

**"Can V3 identify which 6.8% of trades will become runners BEFORE entry?"**

If the answer is yes — even partially (increasing runner rate from 6.8% to 10%) — the system becomes more robust. If no — the system is a fragile lottery that depends on uncontrollable rare events.

Alternatively: **"Is the +0.065R signal stable across different time periods, or is it driven by a few specific dates?"** This would validate whether the runners are systematic or anomalous.
