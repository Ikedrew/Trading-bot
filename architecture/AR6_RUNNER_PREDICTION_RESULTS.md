# AR6 — Runner Prediction and Expansion Condition Analysis Results

**Date:** 2026-07-29
**Dataset:** 146 matched WEAK+INTERESTING records
**Verdict:** B) Runner conditions exist but require more data

---

## Executive Summary

Runner prediction shows THREE promising signals, all with small samples:

| Predictor | n | Runner Rate | Baseline | Lift | Robust? |
|---|---|---|---|---|---|
| **USDJPY** | 13 | **30.8%** | 7.5% | +23.2% | n too small |
| **GBPUSD** | 19 | **15.8%** | 7.5% | +8.3% | n too small |
| **Risk <5 pips** | 64 | **15.6%** | 7.5% | +8.1% | Moderate n |

**Without runners, the system is NOT VIABLE (EV=-0.065R).** The entire edge depends on the 7.5% of trades that expand beyond 0.5R.

---

## Analysis 1: Runner Categories

| Category | n | % | EV | Contribution |
|---|---|---|---|---|
| A: No expansion (MFE < 0.5R) | 135 | 92.5% | **-0.065R** | -173% |
| B: Moderate (MFE 0.5-2.0R) | 7 | 4.8% | +0.597R | +83% |
| C: True runner (MFE ≥ 2.0R) | 4 | 2.7% | **+2.405R** | **+190%** |

**The 4 true runners (2.7%) contribute +190% of total EV.** The 135 non-expanding trades contribute -173%. Net: +0.035R only because the 11 total expansion trades outweigh 135 losing/flat trades.

---

## Analysis 2: Pre-Entry Runner Characteristics

### Direction (strongest differentiator)

| Direction | Runner Rate | Non-Runner Rate | Difference |
|---|---|---|---|
| **BULLISH** | **63.6%** of runners | 51.1% of non-runners | **+12.5%** overrepresented |
| BEARISH | 36.4% | 47.4% | -11.0% underrepresented |

Runners are disproportionately BULLISH. This may reflect the current market epoch (trending instruments moving up) rather than a systematic edge.

### Risk Distance (counterintuitive)

| Metric | Runners | Non-Runners |
|---|---|---|
| Avg risk distance | **3.9 pips** | 7.4 pips |

Runners come from TIGHTER stops (3.9p vs 7.4p). This is because tighter stops = more R captured from the same absolute move. A 15-pip move with a 3.9-pip stop = 3.8R (runner). The same 15-pip move with 7.4-pip stop = 2.0R (moderate, barely runner).

### Symbol (most discriminating)

| Symbol | Runner Rate | n |
|---|---|---|
| **USDJPY** | **30.8%** | 13 |
| **GBPUSD** | **15.8%** | 19 |
| EURUSD | 8.3% | 24 |
| USDCHF | 4.2% | 24 |
| AUDUSD | 3.6% | 28 |
| NZDUSD | 0.0% | 26 |
| USDCAD | 0.0% | 12 |

**USDJPY produces runners at 4x the baseline rate.** But n=13 is too small for confidence. GBPUSD at 15.8% (2x baseline) with n=19 is also small.

---

## Analysis 3: Runner Rate by Condition

| Condition | n | Runners | Rate | vs Baseline (+7.5%) |
|---|---|---|---|---|
| **USDJPY** | 13 | 4 | **30.8%** | **+23.2%** |
| **GBPUSD** | 19 | 3 | **15.8%** | **+8.3%** |
| **Risk <5 pips** | 64 | 10 | **15.6%** | **+8.1%** |
| BULLISH | 76 | 7 | 9.2% | +1.7% |
| INTRADAY | 36 | 3 | 8.3% | +0.8% |
| SCALP | 110 | 8 | 7.3% | -0.3% |
| BEARISH | 68 | 4 | 5.9% | -1.7% |
| NZDUSD | 26 | 0 | 0.0% | -7.5% |
| Risk 5-10 pips | 47 | 0 | 0.0% | -7.5% |

---

## Analysis 4: Predictive Ranking

The three strongest runner predictors:

1. **USDJPY (+23.2% lift)** — but n=13, may be single-epoch effect
2. **GBPUSD (+8.3% lift)** — but n=19, needs validation
3. **Risk <5 pips (+8.1% lift)** — n=64, most robust signal

The "Risk <5 pips" finding is the most reliable because:
- Larger sample (n=64)
- Mathematical explanation: tight stops convert moderate absolute moves into large R-multiples
- Consistent with the V3 finding that SCALP geometry captures first reaction

---

## Analysis 5: False Runners

Only 1 "false runner" found (MFE≥0.25 but R<0). The system has very low false-positive rate for expansion detection — when MFE exceeds 0.5R, the final result is almost always positive (11/11 = 100% in this sample).

---

## Analysis 6: Robustness

| Subset | n | EV |
|---|---|---|
| All trades | 146 | +0.035R |
| **Without runners (MFE<0.5R)** | **135** | **-0.065R** |
| Runners only (MFE≥0.5R) | 11 | +1.255R |
| Best condition (USDJPY) | 13 | +0.494R |

**System is NOT VIABLE without runners.** EV=-0.065R for the 92.5% of trades that don't expand.

---

## AR6 Verdict

### B) Runner conditions exist but require more data

**Evidence:**
- USDJPY shows 30.8% runner rate (4x baseline) — but n=13
- GBPUSD shows 15.8% (2x baseline) — but n=19
- Tight risk (<5 pips) shows 15.6% (2x baseline) — n=64, most reliable
- BULLISH direction overrepresented in runners (+12.5%)

**The predictors are suggestive but NOT statistically conclusive:**
- All have small absolute runner counts (3-10 events)
- Symbol-based filtering may reflect epoch-specific market conditions
- "Risk <5 pips" is the only mechanically explainable finding (same absolute move = more R at tight stops)

**Critical reality:** The edge cannot function without runners. Without them, EV=-0.065R. No amount of context filtering fixes this. The system REQUIRES that 7.5% of trades produce >0.5R expansion, and this rate is currently unpredictable from available V3 features.

---

## Implications

### What V3 Can Do
- Identify direction correctly ~51% of the time
- Identify good timing (WEAK > VALID)
- Cut losers early (tight SL improves EV)

### What V3 Cannot Do (yet)
- Predict which trades will become runners
- Guarantee minimum runner rate
- Function profitably without runner dependency

### The Fundamental Trade-Off

The system works as a high-variance asymmetric bet:
- Trade frequently at small risk
- Accept that 92.5% produce <0.5R outcomes
- Depend on 7.5% producing large moves
- Net result: barely positive after costs at 20+ pip stops

---

## Recommended AR7

**"Is the runner rate stable over time, or is it driven by specific market epochs?"**

If the 7.5% runner rate is stable across different periods → the system has a fragile but real edge
If the runner rate varies dramatically (0% in some periods, 15% in others) → the edge is epoch-dependent and not tradeable consistently

This determines whether V3 is a viable long-term system or a period-specific anomaly.
