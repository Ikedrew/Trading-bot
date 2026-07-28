# Stop Distance Confirmation Experiment — Results

**Date:** 2026-07-27
**Data:** STRICT out-of-sample (last 40% of CURRENT epoch, n=346)
**Method:** Bar-by-bar sequential simulation, no look-ahead
**Design:** Same trades across all variants, only SL changes

---

## OUT-OF-SAMPLE RESULTS (n=346 unseen trades)

| SL | EV | 95% CI | Win Rate | PF | Stop % | vs Baseline | p-value | Sig? |
|---|---|---|---|---|---|---|---|---|
| **0.25R** | +0.033R | [-0.025, +0.090] | 0.344 | 1.29 | 34.7% | +0.061R | 0.00025 | ✅ vs baseline |
| 0.35R | +0.018R | [-0.041, +0.078] | 0.353 | 1.14 | 25.7% | +0.047R | 0.0015 | ✅ vs baseline |
| 0.50R | +0.010R | [-0.054, +0.074] | 0.361 | 1.07 | 15.9% | +0.038R | <0.001 | ✅ vs baseline |
| 1.00R | -0.028R | [-0.097, +0.041] | 0.364 | 0.86 | 6.4% | — | — | — |

**EV > 0 test (one-tailed):**
- SL=0.25R: p = 0.131 — **NOT significant**
- SL=0.35R: p = 0.273 — NOT significant
- SL=0.50R: p = 0.378 — NOT significant

**The improvement vs baseline is significant, but EV > 0 is NOT confirmed OOS.**

---

## SYMBOL BREAKDOWN (OOS)

| Symbol | n | SL=0.25R | SL=0.50R | SL=1.0R |
|--------|---|----------|----------|---------|
| NZDUSD | 87 | -0.022R | -0.053R | -0.059R |
| USDCAD | 125 | +0.002R | -0.048R | -0.072R |
| USDCHF | 88 | +0.018R | -0.017R | -0.058R |
| **USDJPY** | 46 | **+0.250R** | **+0.339R** | **+0.207R** |

**USDJPY is the only symbol with positive EV across ALL SL levels.** Other symbols show tighter SL improves over wider SL, but none clearly positive except USDJPY.

---

## REGIME BREAKDOWN (OOS)

| Regime | n | SL=0.25R | SL=0.50R | SL=1.0R |
|--------|---|----------|----------|---------|
| RANGE | 346 | +0.033R | +0.010R | -0.028R |

All OOS trades are classified as RANGE regime. No multi-regime comparison possible in this sample.

---

## ROLLING WINDOWS (OOS — 4 windows of ~86 trades)

| Window | SL=0.25R | SL=0.35R | SL=0.50R | SL=1.0R |
|--------|----------|----------|----------|---------|
| W1 | -0.022R | -0.036R | -0.053R | -0.059R |
| W2 | -0.003R | -0.026R | -0.053R | -0.078R |
| W3 | +0.036R | +0.008R | -0.009R | -0.044R |
| **W4** | **+0.125R** | **+0.133R** | **+0.163R** | +0.080R |

**Only the last window (W4) is clearly positive for all SL levels.** Windows 1-2 are negative. Window 3 is marginal. This suggests a **recent improvement in signal quality** rather than a stable edge.

---

## 🔴 CRITICAL: TRANSACTION COST ANALYSIS

### Effective SL in pips (median trade)

| SL Level | Distance in Pips |
|----------|-----------------|
| 0.25R | **0.72 pips** |
| 0.35R | 1.01 pips |
| 0.50R | 1.45 pips |
| 1.00R | 2.90 pips |

### Spread as % of SL distance

| Spread | SL=0.25R | SL=0.35R | SL=0.50R | SL=1.0R |
|--------|----------|----------|----------|---------|
| 0.8 pip | **110%** | 79% | 55% | 28% |
| 1.0 pip | **138%** | 99% | 69% | 34% |
| 1.5 pip | **207%** | 148% | 103% | 52% |

**At SL=0.25R, the spread EXCEEDS the stop distance.** A trade would be stopped immediately at market open due to the bid-ask spread alone.

### EV After Spread Deduction (OOS)

| Spread | SL=0.25R | SL=0.35R | SL=0.50R | SL=1.0R |
|--------|----------|----------|----------|---------|
| 0.8 pip | **-0.394R** | -0.408R | -0.416R | -0.455R |
| 1.0 pip | -0.500R | -0.515R | -0.523R | -0.561R |
| 1.5 pip | -0.767R | -0.781R | -0.790R | -0.828R |

**After accounting for spread, ALL configurations produce deeply negative EV.** The apparent tight-SL advantage is an artefact of the bar-close-only simulation which ignores that the SL would be triggered by the spread before the first bar even closes.

---

## WHY THE TIGHT-SL FINDING IS AN ARTEFACT

The bar-by-bar simulation checks R **at bar close** only. It does not simulate the intra-bar bid-ask spread. In reality:

1. Trade opens at ask price (for BUY)
2. Immediately, the bid is spread-distance below ask
3. If SL is 0.72 pips and spread is 0.8 pips, the trade is IMMEDIATELY in SL territory
4. The first bar-close reading might show R=+0.01 (price moved up mid-bar), but the intra-bar low was -spread, which would trigger a 0.72-pip SL

**This is a form of LOOK-AHEAD BIAS at the intra-bar level.** The simulation assumes the trade survives to bar close, but in reality the spread would trigger the tight SL before the bar completes.

---

## CLASSIFICATION

### SL = 0.25R: 🔴 REJECT

**Reason:** SL distance (0.72 pips) is LESS than typical spread (0.8-1.5 pips). Physically impossible to execute. The positive in-sample EV is an artefact of bar-close-only simulation that ignores spread.

### SL = 0.35R: 🔴 REJECT

**Reason:** SL distance (1.01 pips) is approximately equal to spread. Nearly all trades would stop immediately. Same artefact.

### SL = 0.50R: 🟡 CONTINUE TESTING — with caveats

**Evidence for:**
- OOS EV is positive (+0.010R)
- SL distance (1.45 pips) marginally exceeds typical spread
- Significantly better than 1.0R baseline (p<0.001)

**Evidence against:**
- OOS EV confidence interval includes zero
- After 0.8-pip spread deduction: EV = -0.416R (catastrophically negative)
- Only 1 of 4 OOS windows is clearly positive
- The spread-to-SL ratio (55-69%) means a large portion of the risk budget is consumed by the spread

**Verdict:** Even 0.50R is likely not viable after realistic transaction costs. The 1.45-pip SL leaves only 0.65 pips of "breathing room" beyond a 0.8-pip spread.

### SL = 1.00R (current): Confirmed negative

**EV = -0.028R** in OOS. Not viable.

---

## FINAL VERDICT

### 🔴 REJECT — The tight-SL finding does not survive transaction cost reality.

The bar-by-bar simulation demonstrated a statistically significant improvement with tighter stops, but this improvement is an **execution impossibility artefact**. When the stop distance is smaller than or comparable to the bid-ask spread, the trade cannot physically exist long enough to be evaluated.

**The system does NOT have a viable edge at any tested stop distance after accounting for realistic execution costs.**

---

## WHAT THIS MEANS

1. **The entry signal does NOT contain a strong enough directional bias** to overcome transaction costs at any SL level.
2. **The apparent tight-SL advantage** was actually measuring "how much does reducing the loss-per-stop help?" — but this ignores that tight stops would be triggered by spread alone in production.
3. **The system needs entries that produce larger initial movement** (significantly beyond spread) before any SL configuration can capture positive EV.
4. **No stop distance optimisation can fix a signal that doesn't move far enough beyond transaction costs.**

---

## NEXT RESEARCH QUESTION

The correct next question is no longer about exits or stops. It is:

> "Do any specific entry conditions (pattern × regime × phase × time) produce entries that move significantly beyond spread within the first 1-3 bars?"

This is an entry quality filtering question — identifying WHICH entries have strong enough initial momentum to survive realistic execution costs.
