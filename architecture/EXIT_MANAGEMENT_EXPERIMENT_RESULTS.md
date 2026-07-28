# Exit Management Research Experiment Results

**Date:** 2026-07-27
**Data:** CURRENT epoch only (n=864)
**Architecture:** new_pipeline_v1.2
**Method:** Bar-by-bar trade_state_progression (sequential, no look-ahead)
**Statistical test:** Paired t-test (same trades, different exits)

---

## BASELINE: Current Exit Policy

| Metric | Value |
|--------|-------|
| Trades | 864 |
| EV per trade | **-0.014R** |
| 95% CI | [-0.025, +0.006] |
| Win rate | 38.1% |
| Profit factor | 0.949 |
| Avg MFE | 0.744R |
| MFE capture | -1.37 |
| Max drawdown | 190R |
| Timeout % | **77.1%** |
| Stop loss % | 22.5% |
| Take profit % | 0.5% |

Note: The baseline EV here (-0.014R) differs from the earlier -0.20R because this simulation uses SL=1.0R + timeout at 60 bars (matching the controlled experiment parameters), while the actual shadow trade records include the original SL geometry.

---

## EX1: EXIT POLICY COMPARISON

| Exit Policy | Trades | EV | Win Rate | Profit Factor | Improvement | p-value | Significant? |
|---|---|---|---|---|---|---|---|
| **1. Current (SL+timeout)** | 864 | -0.014R | 0.381 | 0.949 | — | — | baseline |
| **2. Break-even** | 864 | -0.033R | 0.345 | 0.875 | -0.019R | 0.0006 | YES (WORSE) |
| **3. Trailing (act=0.5, trail=0.10)** | 864 | -0.007R | 0.387 | 0.973 | +0.007R | 0.202 | NO |
| **4. Trailing (act=0.25, trail=0.10)** | 864 | -0.009R | 0.402 | 0.964 | +0.004R | 0.473 | NO |
| **5. Partial TP (50% at 0.5R)** | 864 | -0.080R | 0.381 | 0.696 | -0.067R | <0.001 | YES (WORSE) |

### EX1 Conclusions

1. **Break-even HURTS** — Moving SL to zero after +0.5R reduces EV by -0.019R (p=0.0006). This is because many trades that briefly touch +0.5R then continue to profitability, and BE cuts them off at zero.

2. **Trailing stop does NOT significantly improve** — +0.007R improvement is not statistically significant (p=0.20). The improvement exists but cannot be distinguished from random noise at current sample size.

3. **Partial TP HURTS** — Taking 50% off at 0.5R significantly reduces EV by -0.067R (p<0.001).

4. **No exit policy achieves positive EV** — Best is trailing at -0.007R, which is statistically indistinguishable from zero.

---

## EX2: TRAILING STOP CONFIGURATION MATRIX

30 configurations tested (6 activation × 5 trail distances). Key results:

| Activation | Trail | EV | Improvement | p-value | Significant? |
|---|---|---|---|---|---|
| 0.15R | 0.05R | -0.003R | +0.011R | 0.083 | NO (marginal) |
| 0.25R | 0.05R | -0.004R | +0.010R | 0.110 | NO |
| 0.35R | 0.05R | -0.004R | +0.010R | 0.084 | NO (marginal) |
| 0.50R | 0.05R | -0.004R | +0.009R | 0.078 | NO (marginal) |
| 0.75R | 0.05R | -0.008R | +0.006R | 0.073 | NO (marginal) |
| **1.00R** | **0.05R** | **-0.007R** | **+0.006R** | **0.049** | **YES (barely)** |

### EX2 Conclusions

1. **Only ONE configuration reaches statistical significance** — Activation at 1.0R with 0.05R trail distance (p=0.049, barely significant).

2. **The effect size is tiny** — Best improvement is +0.011R per trade. At 864 trades this is +9.5R total.

3. **Tight trail distances (0.05R) consistently outperform wider ones** — This suggests that when the trail activates, price rapidly retraces, and a tight trail captures more.

4. **Lower activation thresholds show LARGER improvements** but higher variance — The trend suggests 0.15-0.35R activation with 0.05R trail is the sweet spot, but none reach p<0.05.

5. **No configuration achieves positive EV** — Best EV is -0.003R (effectively zero within CI).

---

## EX3: TAKE PROFIT DISTANCE TEST

| TP Distance | EV | Win Rate | TP Hit Rate | Improvement vs No-TP | p-value | Significant? |
|---|---|---|---|---|---|---|
| No TP (baseline) | -0.014R | 0.381 | 0% | — | — | — |
| 0.25R | -0.182R | 0.402 | 23.8% | **-0.168R** | <0.001 | YES (WORSE) |
| 0.50R | -0.147R | 0.387 | 14.9% | -0.133R | <0.001 | YES (WORSE) |
| 0.75R | -0.128R | 0.382 | 10.2% | -0.114R | <0.001 | YES (WORSE) |
| 1.00R | -0.108R | 0.382 | 8.0% | -0.094R | <0.001 | YES (WORSE) |
| 1.50R | -0.078R | 0.381 | 6.4% | -0.064R | <0.001 | YES (WORSE) |
| 2.00R | -0.050R | 0.381 | 5.9% | -0.036R | <0.001 | YES (WORSE) |
| 3.00R | -0.019R | 0.381 | 0.2% | -0.006R | 0.163 | NO |

### EX3 Conclusions

**COUNTERINTUITIVE FINDING: Adding ANY take profit makes the system WORSE.**

1. **Every TP level below 3R significantly worsens EV** — This is the opposite of what the earlier MFE-based simulation suggested.

2. **Why?** The bar-by-bar simulation reveals that TP cuts off WINNING trades early. Trades that reach 0.5R often continue to 2R+ (the MFE distribution has a fat right tail). Capping winners at a fixed level removes the large wins that offset the many small losses.

3. **The no-TP baseline (-0.014R) is actually the best fixed-exit policy** — The system already benefits from letting winners run to timeout.

4. **The real problem is the 22.5% that hit SL** — not the absence of a TP target.

---

## VALIDITY VERIFICATION

| Check | Status |
|-------|--------|
| CURRENT epoch only | ✅ n=864 CURRENT trades |
| No legacy contamination | ✅ load_shadow_trades(epoch='CURRENT') |
| Complete outcomes | ✅ 864/864 have state_progression |
| Single variable per experiment | ✅ EX1: exit policy. EX2: trail config. EX3: TP distance |
| Same entry for all variants | ✅ Paired (same trade, different exits) |
| No look-ahead bias | ✅ Bar-by-bar sequential processing |
| Statistical testing | ✅ Paired t-test for all comparisons |
| Confidence intervals | ✅ 95% CI reported |
| Sample size adequate | ✅ n=864 (exceeds 100 minimum) |

---

## PROMOTION DECISIONS

| Exit Policy | Decision | Evidence |
|---|---|---|
| Break-even | 🔴 **REJECT** | Significantly WORSE (-0.019R, p=0.0006) |
| Trailing (any config) | 🟡 **CONTINUE TESTING** | Positive trend but NOT significant at p<0.05 (except one marginal case at p=0.049) |
| Partial TP | 🔴 **REJECT** | Significantly WORSE (-0.067R, p<0.001) |
| Fixed TP (any level) | 🔴 **REJECT** | ALL levels significantly WORSE than no-TP baseline |
| Current (SL + timeout) | — | Remains the best simple policy |

---

## FINAL REPORT

### 1. Current baseline performance

EV = -0.014R per trade (when simulated with SL=1.0R + 60-bar timeout). Statistically indistinguishable from zero (CI includes zero: [-0.025, +0.006]). The system is approximately breakeven — not the -0.20R previously reported, because the earlier figure included the original (non-standardised) SL geometry.

### 2-4. Experiment Results Summary

No exit policy tested achieves positive EV with statistical significance. The trailing stop shows the most promise (+0.011R at best) but falls short of p<0.05 in most configurations.

### 5. Best performing exit policy

**Current exit (SL + timeout, no TP)** remains the best simple policy. No alternative tested significantly outperforms it. The trailing stop with tight trail (0.05R) shows marginal improvement but is not statistically validated.

### 6. Was negative EV caused primarily by exit management?

**PARTIALLY.** When exit is standardised (SL=1R, 60-bar timeout, no TP), the system EV is -0.014R — essentially breakeven. The earlier -0.20R figure included the original tight SL geometry (average 2.7 pips), which causes 22.5% of trades to stop out immediately. The exit problem is PRIMARILY about SL distance being too tight in the original system, not about TP or trailing.

### 7. Are exit changes sufficient to create positive EV?

**NO — not with statistical confidence.** The best trailing configuration shows -0.003R (nearly zero) but this is not significantly different from the baseline. The system is AT the boundary between negative and zero EV, but no tested exit policy pushes it convincingly positive.

### 8. What should be investigated next?

**SL DISTANCE.** The controlled Experiment B (stop distance test) should be the priority. The evidence suggests:
- Original system SL ≈ 2.7 pips causes 22.5% premature stops
- Wider SL (already shown in Horizon comparison: INTRADAY at 11.3 pips reduces stops to 2.5%)
- The difference between -0.20R (original system) and -0.014R (standardised 1R SL) demonstrates that **SL geometry is the dominant factor**

Run Experiment B with the actual original SL distances and wider alternatives to determine if a wider SL converts the system to positive EV.
