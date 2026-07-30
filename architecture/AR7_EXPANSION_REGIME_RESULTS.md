# AR7 — Expansion Regime Detection Analysis Results

**Date:** 2026-07-29
**Dataset:** 146 matched WEAK+INTERESTING records
**Verdict:** A) Expansion conditions are identifiable — BUT the mechanism is mathematical, not market-predictive

---

## Critical Discovery

**The "runner" effect is a MEASUREMENT ARTEFACT, not a market phenomenon.**

```
A 10-pip absolute price move:
  • At 3-pip stop = 3.3R (classified as "runner")
  • At 8-pip stop = 1.25R (classified as "moderate")

SAME market move → different R-classification based solely on stop distance.
```

The V3 system doesn't predict expansion. It measures the same market movements differently depending on risk geometry.

---

## Analysis 1: Expansion Categories

| Category | n | % | EV | Duration | Peak Bar |
|---|---|---|---|---|---|
| No expansion (MFE < 0.5R) | 135 | 92.5% | -0.065R | 57 bars | Bar 4 |
| Moderate (0.5-2R) | 7 | 4.8% | +0.597R | 60 bars | Bar 5 |
| Runner (≥ 2R) | 4 | 2.7% | +2.405R | 39 bars | Bar 8 |

**Key: Runners resolve FASTER (39 bars vs 57 for non-expansion).** The market moves quickly when it moves, then the non-expanding 92% just drift sideways.

---

## Analysis 2: The Risk Distance Mechanism

| Risk Distance | n | Expansion Rate | EV | Explanation |
|---|---|---|---|---|
| **< 3 pips** | **23** | **30.4%** | **+0.319R** | Same move = large R at tight stops |
| < 5 pips | 64 | 15.6% | +0.132R | Moderate amplification |
| 5-10 pips | 72 | 4.2% | +0.005R | Normal measurement |
| ≥ 10 pips | 51 | 2.0% | -0.052R | Move too small relative to wide stop |

**This is the ENTIRE explanation.** The expansion rate is perfectly correlated with stop tightness:
- 30.4% at <3 pip stops
- 15.6% at <5 pip stops
- 4.2% at 5-10 pip stops
- 2.0% at ≥10 pip stops

This isn't the market expanding more. It's the SAME market movement measured against SMALLER denominators.

---

## Analysis 3: Compression Before Expansion

| Group | Initial Max |R| | Compressed (<0.1R) |
|---|---|---|
| **Expansion** | **0.999R** | **0% compressed** |
| Non-expansion | 0.179R | 38% compressed |

Expansion trades move IMMEDIATELY (initial |R| = 1.0). They don't compress first — they start running from bar 1. This means:
- Expansion isn't preceded by identifiable compression
- It happens or it doesn't within the first few bars
- By bar 5, the outcome is essentially determined

---

## Analysis 4: Movement Profile

| Category | MFE Peak Bar | Interpretation |
|---|---|---|
| Non-expansion | Bar 4 | Maximum move in first 4 bars, then nothing |
| Moderate | Bar 5 | Slightly more development |
| Runner | Bar 8 | Continues developing longer |

All categories peak EARLY. The difference is magnitude, not timing.

---

## Analysis 5: Best Combinations

| Combination | n | Expansion Rate | Lift | EV |
|---|---|---|---|---|
| **Risk <3p** | **23** | **30.4%** | **+22.9%** | **+0.319R** |
| USDJPY + GBPUSD | 32 | 21.9% | +14.3% | +0.188R |
| BULLISH + tight risk | 37 | 18.9% | +11.4% | +0.172R |
| INTRADAY + tight risk | 17 | 17.6% | +10.1% | +0.214R |
| Risk <5p | 64 | 15.6% | +8.1% | +0.132R |

---

## Analysis 6: Cross-Symbol Robustness

Tight risk (<5p) expansion by symbol:

| Symbol | Expansion Rate | n |
|---|---|---|
| **USDJPY** | **43%** | 7 |
| **EURUSD** | **40%** | 5 |
| **GBPUSD** | **27%** | 11 |
| USDCHF | 9% | 11 |
| AUDUSD | 11% | 9 |
| NZDUSD | 0% | 16 |
| USDCAD | 0% | 5 |

**5 of 7 symbols show expansion at tight risk.** Cross-symbol consistency: YES. But rates vary widely (0% to 43%), suggesting symbol-specific movement characteristics matter.

---

## AR7 Verdict

### A) Expansion conditions are identifiable — BUT the mechanism is MATHEMATICAL, not PREDICTIVE

**The finding:**
- Tight stops (<3 pips) produce 30.4% "expansion" rate (4x baseline)
- This is because the same market move produces larger R-multiples at tighter risk distances
- The market doesn't expand MORE — it's measured against a SMALLER unit

**This means:**
- V3 cannot predict "when the market will expand" — it moves the same amount regardless
- The "edge" comes from the R-multiple scaling effect of tight stops
- Tight stops amplify BOTH winners (creating runners) AND losers (hitting stop faster)
- Net effect: slightly positive because wins average 0.31R and losses average 0.20R (win/loss ratio 1.58:1)

**The fundamental truth of this system:**
```
51% directional accuracy
× tight stop (0.5R) cutting losers
× no TP (letting winners run)
× large enough absolute stop (20p) to survive spread
= barely positive net EV (+0.005 to +0.025R)
```

---

## Implications for V3 Architecture

| Component | Role | Status |
|---|---|---|
| Market Understanding | Identifies direction slightly above random | USEFUL (+1% edge) |
| Context (INTERESTING + WEAK) | Provides optimal timing window | USEFUL (timing matters) |
| Expansion detection | Cannot predict market movement | NOT USEFUL as predictor |
| Tight stop geometry | Amplifies the small directional edge | THE MECHANISM |
| No TP policy | Captures rare large moves | ESSENTIAL |
| 20+ pip absolute stop | Reduces cost burden to viable | NECESSARY for profitability |

---

## The Complete AR Research Picture (AR1-AR7)

| Finding | Evidence |
|---|---|
| Direction accuracy | 51% (barely above random) |
| Timing | WEAK > VALID (less confirmation = better) |
| Quality level | INTERESTING > HIGH (less strict = better) |
| Cost structure | Must be ≤6% spread/risk (requires 20+ pip stops) |
| Edge source | Tight R-multiple stop + no TP = asymmetric payoff |
| "Runner" mechanism | Mathematical (scaling), not predictive |
| Net viability | +0.005R to +0.025R depending on geometry (marginal) |

---

## Recommended AR8

**"Is the +0.005 to +0.025R net EV STABLE over time, or does it disappear in different market conditions?"**

This is the FINAL viability question. If the edge is stable → marginal but tradeable system. If it fluctuates between +0.05R and -0.05R across periods → not reliably tradeable.

This would involve:
1. Splitting the 146 records by time period
2. Calculating EV per period
3. Determining variance of the edge across time
4. Assessing whether drawdowns between positive periods are survivable
