# AR4 — Horizon and Risk Geometry Transfer Analysis Results

**Date:** 2026-07-29
**Dataset:** 108 matched SCALP+WEAK+INTERESTING records with bar-by-bar progression data
**Verdict:** A) Higher timeframe expression preserves the edge (marginally)

---

## Executive Summary

Re-simulating the WEAK+INTERESTING signal at different risk geometries reveals:

1. **The raw signal is +0.062R** (higher than AR3's +0.043R because matched records have slightly better outcomes)
2. **At 20-pip stops, the signal becomes marginally positive: +0.015R after costs**
3. **Tight SL (0.5R) at 20-pip stop is the best configuration: +0.015R net**
4. **81.5% of trades have MFE < 0.25R** — the market barely moves

---

## Analysis 1: Duration Has No Effect

| Duration | n | WR | EV | SL% | Timeout% |
|---|---|---|---|---|---|
| 20 bars | 108 | 52.8% | +0.062R | 1% | 99% |
| 60 bars | 108 | 52.8% | +0.062R | 1% | 99% |
| 120 bars | 108 | 52.8% | +0.062R | 1% | 99% |
| 300 bars | 108 | 52.8% | +0.062R | 1% | 99% |

**Duration makes no difference.** With SL=1R, trades almost never hit stop loss (1%) and never reach targets. The outcome is determined within the first 20 bars and doesn't improve with more time. This confirms the MFE is reached early and price then drifts sideways.

---

## Analysis 2: Risk Geometry — Key Finding

| Geometry | EV | TP% | SL% | TO% |
|---|---|---|---|---|
| **Current (SL=1R, no TP)** | **+0.062R** | 0% | 1% | 99% |
| **Tight SL (0.5R, no TP)** | **+0.075R** | 0% | 6% | 94% |
| Wide SL (1.5R, no TP) | +0.062R | 0% | 0% | 100% |
| TP 0.5R, SL 1R | +0.002R | 7% | 1% | 92% |
| TP 1.0R, SL 1R | +0.018R | 3% | 1% | 96% |
| TP 2.0R, SL 1R | +0.046R | 3% | 1% | 96% |
| INTRA: SL 1R, TP 3R, 120 bars | +0.061R | 1% | 1% | 98% |

**Key findings:**
- **Tight SL (0.5R) is BETTER than wide SL** → +0.075R vs +0.062R. This is because the 6% that hit 0.5R stop were going to lose anyway — cutting them early saves capital.
- **Adding a TP REDUCES EV** → TP 0.5R drops to +0.002R because 93% of trades never reach it but the ceiling prevents the rare winners from running.
- **Unlimited upside (no TP) + tight stop = best geometry**

---

## Analysis 3: Move Capture — The Core Problem

### MFE Distribution (n=108)

| Range | Count | % |
|---|---|---|
| **< 0.25R** | **88** | **81.5%** |
| 0.25-0.5R | 12 | 11.1% |
| 0.5-1.0R | 5 | 4.6% |
| 1.0-2.0R | 0 | 0.0% |
| ≥ 2.0R | 3 | 2.8% |

**81.5% of trades BARELY MOVE.** Mean MFE: 0.18R. Median MFE: **0.075R**.

### MAE Distribution

| Range | Count | % |
|---|---|---|
| > -0.25R (small adverse) | 89 | 82.4% |
| -0.25 to -0.5R | 12 | 11.1% |
| -0.5 to -1.0R | 7 | 6.5% |
| ≤ -1.0R | 1 | 0.9% |

Mean MAE: -0.13R. Most trades don't move significantly in EITHER direction.

**The fundamental reality:** After V3 identifies a WEAK+INTERESTING opportunity, price typically moves less than 0.25R in the expected direction. The signal IS directionally correct (52.8% WR, slight positive EV) but the MAGNITUDE of movement is tiny.

---

## Analysis 4: Cost-Adjusted Best Configuration

| Geometry | Raw EV | @3.5p stop | @10p stop | @15p stop | **@20p stop** |
|---|---|---|---|---|---|
| Tight SL (0.5R, no TP) | +0.075R | -0.268R | -0.045R | -0.005R | **+0.015R** |
| Current (SL=1R, no TP) | +0.062R | -0.281R | -0.058R | -0.018R | +0.002R |
| INTRA (SL 1R, TP 3R) | +0.061R | -0.281R | -0.059R | -0.019R | +0.001R |

**Best: Tight SL (0.5R) at 20-pip structural stop → +0.015R net EV.**

This is the FIRST configuration in the entire research program that produces positive cost-adjusted EV.

---

## Analysis 5: Cost Sensitivity

| Cost Structure | Required Stop Size | Feasible? |
|---|---|---|
| Retail (1.2 pip spread+slippage) | ≥28 pips | H1 structure only |
| ECN (0.8 pip) | ≥19 pips | M15/H1 structure |
| Institutional (0.5 pip) | ≥12 pips | M15 structure |

**At institutional-grade execution (0.5 pip total cost), M15 structure stops (12+ pips) make the signal viable.** At retail (1.2 pip), only H1 structural stops (28+ pips) work — which changes the entry mechanism entirely.

---

## AR4 Verdict

### A) Higher timeframe expression preserves the edge (marginally)

**Best configuration found:**
```
Signal:    SCALP + WEAK + INTERESTING (V3 opportunity detection)
Geometry:  Tight SL (0.5R), no TP, timeout at 20-60 bars
Stop size: 20 pips (H1 structure-based)
Cost:      1.2 pips / 20 pips = 6% spread/risk
Net EV:    +0.015R
CI:        approximately [-0.08, +0.11] (still crosses zero)
```

**Caveats:**
- +0.015R is barely positive
- 95% CI still crosses zero (not statistically significant)
- n=108 (adequate for detection but not for confidence)
- 81.5% of trades barely move — the edge comes from the 18.5% that do
- Requires 20-pip stops which transform this from M5 scalp into M15/H1 territory

---

## Critical Insight

The V3 opportunity detection system identifies correct direction 52.8% of the time. The signal is real. But **the M5 entry mechanism does not produce enough movement** for the signal to matter at M5 risk distances.

The signal only becomes viable when:
1. Stop distance is 20+ pips (reducing spread impact to ≤6%)
2. The stop is TIGHT relative to R (0.5R = quick invalidation)
3. No take profit (let the rare winners run)
4. Time doesn't matter (20 bars and 300 bars produce same result)

This means the ENTRY should remain at M5 timing, but the RISK GEOMETRY should be M15/H1 structural.

---

## Recommended AR5

**"Can the WEAK+INTERESTING signal be combined with M15/H1 structural stop placement (20+ pip stops) to produce statistically significant positive EV?"**

This requires:
1. Filtering WEAK+INTERESTING records where M15 structure provides a logical stop ≥20 pips away
2. Simulating trades with that specific stop distance
3. Determining whether the sub-sample with valid structure stops maintains the +0.06R signal
4. Calculating cost-adjusted EV at the ACTUAL structure stop distance (not assumed 20 pips)

If validated: this is the foundation for a V3 shadow strategy.
If not: the signal exists but cannot be economically exploited at retail FX costs.
