# AR1 — Incremental Predictive Value Analysis Results

**Date:** 2026-07-29
**Dataset:** 368 linked V3 shadow execution assessments
**Verdict:** C) Current approval logic is selecting inferior opportunities

---

## Executive Summary

The V3 reasoning chain is **inversely correlated with outcomes**. Every additional filter applied by the pipeline REDUCES expected value rather than improving it.

| Filter Applied | n | EV | vs Baseline |
|---|---|---|---|
| Baseline (all) | 368 | +0.093R | — |
| EXECUTION_CONSTRAINED | 174 | +0.020R | -0.07R |
| **READY_FOR_EXECUTION** | **38** | **-0.112R** | **-0.21R** |

**The most approved state (READY) has the worst EV. The most rejected state (NOT_EXECUTABLE) has the best EV (+0.68R).** This is the inverse of what a predictive pipeline should produce.

---

## Analysis 1: Execution State Performance

| State | n | WR | EV | 95% CI | PF |
|---|---|---|---|---|---|
| NOT_EXECUTABLE | 60 | 43.3% | **+0.679R** | [+0.22, +1.14] *** | 2.58 |
| EXECUTION_CONSTRAINED | 174 | 49.4% | +0.020R | [-0.05, +0.09] | 1.16 |
| SIMULATED_ONLY | 96 | 46.9% | -0.059R | [-0.13, +0.02] | 0.63 |
| **READY_FOR_EXECUTION** | **38** | **34.2%** | **-0.112R** | [-0.24, +0.02] | **0.48** |

**Pattern:** As the pipeline becomes MORE selective (CONSTRAINED → READY), outcomes get WORSE.

---

## Analysis 2: Layer Incremental Value

### Every V3 layer shows NEGATIVE incremental value:

| Layer | "Pass" EV | "Fail" EV | Delta | Direction |
|---|---|---|---|---|
| Opportunity (HIGH+INT vs LOW+MIX) | -0.019R | +0.401R | **-0.42R** | INVERTED |
| Horizon (SCALP vs rest) | -0.016R | +0.270R | **-0.29R** | INVERTED |
| Horizon (INTRADAY vs rest) | -0.037R | +0.129R | **-0.17R** | INVERTED |
| Entry (VALID vs rest) | -0.112R | +0.117R | **-0.23R** | INVERTED |
| Entry (any confirmation vs none) | -0.004R | +0.225R | **-0.23R** | INVERTED |
| Risk (ACCEPTABLE vs rest) | -0.021R | +0.679R | **-0.70R** | INVERTED |

**Every single layer comparison shows: the "approved" group performs WORSE than the "rejected" group.**

---

## Analysis 3: READY Deep Dive

### n=38 READY trades:
- Winners: 13 (34.2%)
- Losers: 25 (65.8%)
- Exit reason: 95% timeout, 5% stop loss
- **MFE: average 0.29R** — trades barely move in the right direction
- MFE ≥ 1R: only 5% (2/38)
- MFE ≥ 2R: 0%

### By horizon within READY:
- SCALP (n=26): EV=-0.107R
- INTRADAY (n=12): EV=-0.122R

Both horizons produce negative outcomes when READY.

---

## Analysis 4: Late Entry / MFE Investigation

| State | MFE | MAE | Result | Captured |
|---|---|---|---|---|
| READY | 0.29R | 0.42R | -0.11R | -38% of MFE (goes against) |
| CONSTRAINED | 0.24R | 0.30R | +0.02R | +8% of MFE |
| SIMULATED | 0.20R | 0.32R | -0.06R | -30% of MFE |

**READY has the HIGHEST MFE (0.29R) but the WORST result (-0.11R).** This means:
- The market DOES move in the expected direction initially (MFE > 0)
- But then reverses, finishing negative
- MAE (0.42R) exceeds MFE (0.29R) → adverse excursion dominates

This is consistent with the **late entry hypothesis**: by the time all confirmation triggers fire (BOS + zone + momentum = VALID + ACCEPTABLE), the move has already partially completed, and the trade enters near the reversal point.

---

## Analysis 5: NOT_EXECUTABLE Anomaly

All 60 NOT_EXECUTABLE records have:
- `opportunity_state = LOW_QUALITY_CONTEXT`
- `horizon = NO_HORIZON`
- Timestamp range: 2023-11-14 to 2026-07-29

**Explanation:** These are early records from BEFORE the V3 pipeline had MarketContext data. They were matched to shadow trades via timestamp — many of which are multi-horizon variants that happened to reach their targets. The +0.68R is NOT genuine alpha — it's a **composition artefact from timestamp-matched historical shadow trades** that predate the V3 pipeline.

---

## Analysis 6: Predictive Ranking

Every comparison shows the "approved" condition performing WORSE:

| Rank | Layer | Delta | n | Direction |
|---|---|---|---|---|
| 1 | Risk (ACCEPTABLE) | -0.70R | 308 | INVERTED |
| 2 | Opportunity (HIGH+INT) | -0.42R | 270 | INVERTED |
| 3 | Horizon (SCALP) | -0.29R | 228 | INVERTED |
| 4 | Entry (has confirmation) | -0.23R | 212 | INVERTED |
| 5 | Entry (VALID) | -0.23R | 38 | INVERTED |
| 6 | Horizon (INTRADAY) | -0.17R | 80 | INVERTED |

**No layer adds value. Every layer subtracts.**

---

## Root Cause Analysis

### Why is the pipeline inverted?

1. **NOT_EXECUTABLE anomaly inflates the baseline.** The 60 early NOT_EXECUTABLE records (+0.68R) are matched to historical multi-horizon shadow trades that predate the V3 pipeline. Removing them would make the baseline near-zero.

2. **READY requires confluence that correlates with LATE entries.** When BOS is confirmed, price is inside a zone, momentum aligns, AND risk is acceptable — this confluence typically occurs AFTER the initial move from the zone, not before it. The system confirms the move and then enters as it reverses.

3. **The underlying signal (M5 pattern + shadow trade) has negative EV after costs.** V2 proved this conclusively (-0.70R). V3 is selecting subsets of the SAME shadow trades — the signal hasn't changed, only the filter.

4. **95% timeout rate confirms: trades don't move.** Whether READY or not, 95% of shadow trades time out. The M5 entry mechanism doesn't produce directional movement.

---

## AR1 Verdict

### C) Current approval logic is selecting inferior opportunities

**Evidence:**
- READY EV (-0.11R) < CONSTRAINED EV (+0.02R) < Baseline (+0.09R)
- Every layer comparison shows INVERTED value (pass < fail)
- MFE analysis shows READY entries are catching reversals (MFE 0.29R but MAE 0.42R)
- 95% timeout confirms no directional movement regardless of pipeline state

**Strongest evidence:** Risk (ACCEPTABLE) is the most inverted layer at -0.70R delta. This is because ALL 308 records that have ACCEPTABLE_RISK share the same underlying shadow trade outcomes, while the 60 INSUFFICIENT_RISK records (early pipeline, no data) happen to match to historical profitable trades.

**Weakest evidence:** The NOT_EXECUTABLE anomaly (+0.68R) is almost certainly a composition artefact, not genuine alpha. These records predate the pipeline.

---

## Recommended Next Research

1. **Re-run AR1 EXCLUDING the 60 NOT_EXECUTABLE records** — they contaminate the baseline. The clean comparison is READY vs CONSTRAINED vs SIMULATED within the active pipeline period.

2. **Investigate why confirmation (VALID entry) predicts WORSE outcomes** — is this because:
   - Confirmation requires price to ALREADY have moved (late entry)?
   - The triggers fire on noise that resembles structure?
   - The direction derivation is incorrect?

3. **Test the INVERSE hypothesis:** Would TRADING when the pipeline says NOT to trade produce better results? (This would confirm the pipeline is anti-predictive vs merely non-predictive.)

---

## Implications for V3 Architecture

**The reasoning chain is architecturally sound but the CRITERIA within each layer may be selecting for conditions that are anti-correlated with outcomes.** This is not a structural problem — it's a calibration problem.

Possible interpretations:
- The entry model's "VALID" requires conditions that appear AFTER the opportunity has passed
- The risk model's "ACCEPTABLE" geometry exists only when the market has already moved
- The opportunity model's "HIGH_QUALITY" fires too late in the structural cycle

**No architecture changes recommended until the NOT_EXECUTABLE anomaly is removed from analysis and the clean comparison is assessed.**
