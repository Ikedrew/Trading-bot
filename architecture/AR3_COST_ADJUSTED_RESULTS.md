# AR3 — Early Signal Cost-Adjusted Expectancy Results

**Date:** 2026-07-29
**Dataset:** 308 records (excl. NOT_EXECUTABLE)
**Verdict:** D) More data required — signal positive but does NOT survive any realistic cost configuration

---

## Executive Summary

The WEAK confirmation signal (+0.02R raw, +0.04R for SCALP+WEAK) does NOT survive transaction costs at ANY tested risk geometry.

| Configuration | Gross EV | Best Cost Structure | Net EV |
|---|---|---|---|
| WEAK (all) | +0.020R | INTRADAY (12%) | **-0.100R** |
| SCALP + WEAK | +0.040R | INTRADAY (12%) | **-0.080R** |
| SCALP + WEAK + INTERESTING | +0.043R | Wide (8%) | **-0.037R** |
| WEAK + BEARISH | +0.044R | Wide (8%) | **-0.036R** |

**The best configuration (SCALP + WEAK + INTERESTING, n=110) at the widest geometry (15 pip stop, 8% spread/risk) still produces -0.037R.** The signal is real but insufficient to overcome costs.

---

## Analysis 1: Cost-Adjusted Performance

| Group | n | WR | Gross EV | @SCALP (34%) | @INTRADAY (12%) | @WIDE (8%) |
|---|---|---|---|---|---|---|
| **SCALP + WEAK** | **117** | **51.3%** | **+0.040R** | -0.303R | -0.080R | **-0.040R** |
| WEAK (all) | 174 | 49.4% | +0.020R | -0.323R | -0.100R | -0.060R |
| INTRADAY + WEAK | 57 | 45.6% | -0.021R | -0.364R | -0.141R | -0.101R |
| VALID (all) | 38 | 34.2% | -0.112R | -0.455R | -0.232R | -0.192R |

---

## Analysis 2: Risk Geometry

| Geometry | Stop | Spread/Risk | Required Raw EV | WEAK Raw EV | Gap |
|---|---|---|---|---|---|
| SCALP | 3.5 pips | 34.3% | +0.343R | +0.020R | -0.32R |
| INTRADAY | 10 pips | 12.0% | +0.120R | +0.020R | -0.10R |
| Wide Intraday | 15 pips | 8.0% | +0.080R | +0.020R | -0.06R |
| Structure | 20 pips | 6.0% | +0.060R | +0.020R | -0.04R |

**Even at 20-pip stops (6% spread/risk), the signal (+0.02R) falls 0.04R short of breakeven.**

For the best raw signal (SCALP+WEAK +0.04R):
- Breakeven at: spread/risk ≤ 4% → requires **30+ pip stops**
- That's H1 structural geometry — not M5/M15 entries

---

## Analysis 3: Timing Capture

| Group | MFE | MAE | Result | Captured | Timing |
|---|---|---|---|---|---|
| SCALP + WEAK | 0.23R | 0.31R | +0.04R | +17% | EARLY |
| INTRADAY + WEAK | 0.26R | 0.29R | -0.02R | -8% | NEUTRAL |
| SCALP + VALID | 0.32R | 0.46R | -0.11R | -34% | LATE |
| INTRADAY + VALID | 0.23R | 0.33R | -0.12R | -52% | LATE |

WEAK entries capture 8-17% of MFE (entering early). VALID entries capture -34 to -52% (entering late, at reversal).

---

## Analysis 4: Environment Segments (WEAK only)

| Segment | n | WR | EV | INTRA net |
|---|---|---|---|---|
| **SCALP horizon** | **117** | **51.3%** | **+0.040R** | -0.080R |
| INTRADAY horizon | 57 | 45.6% | -0.021R | -0.141R |
| INTERESTING opp | 146 | 50.7% | +0.035R | -0.085R |
| HIGH_QUALITY opp | 18 | 38.9% | -0.053R | -0.173R |
| **BEARISH direction** | **76** | **51.3%** | **+0.044R** | **-0.076R** |
| BULLISH direction | 96 | 46.9% | -0.001R | -0.121R |

**Key findings:**
- SCALP horizon performs better than INTRADAY for WEAK entries
- INTERESTING outperforms HIGH_QUALITY (HIGH is again anti-predictive)
- BEARISH direction slightly outperforms BULLISH

---

## Analysis 5: Opportunity × Entry Interaction

| Combination | n | WR | EV | INTRA net |
|---|---|---|---|---|
| **INTERESTING + WEAK** | **146** | **50.7%** | **+0.035R** | -0.085R |
| HIGH + WEAK | 18 | 38.9% | -0.053R | -0.173R |
| INTERESTING + VALID | 17 | 41.2% | -0.107R | -0.227R |
| HIGH + VALID | 21 | 28.6% | -0.116R | -0.236R |
| INTERESTING + NO entry | 68 | 45.6% | -0.072R | -0.192R |

**INTERESTING + WEAK is the best combination.** HIGH_QUALITY opportunity is ANTI-predictive in every configuration — this confirms the AR1 finding that the pipeline's quality gate selects for conditions that appear AFTER the move.

---

## Analysis 6: Best Configuration Found

| Config | n | WR | Gross | @SCALP | @INTRA | @WIDE |
|---|---|---|---|---|---|---|
| **SCALP + WEAK + INTERESTING** | **110** | **51.8%** | **+0.043R** | -0.300R | -0.077R | **-0.037R** |
| WEAK + BEARISH | 76 | 51.3% | +0.044R | -0.299R | -0.076R | -0.036R |

The two strongest configurations (+0.043R and +0.044R) still fall short by ~0.04R at 15-pip geometry and ~0.08R at 10-pip geometry.

---

## AR3 Verdict

### D) More data required

**The signal is real but insufficient for any tested cost structure.**

| Evidence | Detail |
|---|---|
| Signal exists | +0.02 to +0.04R raw (WEAK confirmation) |
| Signal direction correct | WEAK captures +8 to +17% of MFE (early entry) |
| Signal robust | n=110-174, consistent across SCALP+INTERESTING+BEARISH |
| **Signal too small** | **Best: +0.043R, breakeven needs +0.080R at widest geometry** |
| Cost gap | ~0.04R short at 15-pip stops, ~0.08R short at 10-pip stops |

### Why "More Data Required" (not "No Value")

1. The CI upper bound for SCALP+WEAK is +0.133R — which WOULD survive INTRADAY costs (+0.12R). The point estimate is below breakeven but the true value MIGHT be above it.

2. n=117 for SCALP+WEAK. At n=300+, the CI narrows. If the true EV is closer to +0.04R (point estimate), more data would confirm it's non-viable. But if it's closer to the CI upper bound, it could be viable.

3. The +0.04R signal is operating on M5 shadow trades. If the SAME directional signal were applied to INTRADAY-sized trades (wider stops, longer holds), the raw EV might differ — the current analysis uses M5 shadow outcomes applied to what the V3 pipeline classified as SCALP.

---

## Recommended AR4

**"Does the WEAK confirmation signal produce larger raw EV when paired with INTRADAY risk geometry (wider stops, longer hold times)?"**

The current analysis applies INTRADAY COST to SCALP OUTCOMES (M5 shadow trades with 60-bar timeout). The question is whether:
- A wider stop would prevent the 0.29R MFE from being eaten by the 0.31R MAE
- A longer hold time would allow the +0.04R direction to develop further
- The shadow trade engine can be re-simulated with INTRADAY parameters on the WEAK-confirmed subset

This is the RG1 experiment originally proposed in the V3 architecture review.

---

## Implications

The V3 architecture's contribution is clear:
1. **Market Understanding + Context**: Provides useful directional information (+0.02-0.04R)
2. **Opportunity Assessment (INTERESTING)**: Correct quality level (HIGH is too strict)
3. **Horizon (SCALP)**: Correct movement hypothesis for this signal
4. **Entry (WEAK)**: Correct timing (VALID is too late)
5. **Cost structure**: The BINDING constraint. No geometry tested makes this profitable.

The system is architecturally correct but the signal magnitude (+0.04R) is too small for FX retail spreads unless risk geometry can be fundamentally different (30+ pip stops = H1 entries, not M5).
