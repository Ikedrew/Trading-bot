# SV1 — Structure + Geometry Viability Results

**Date:** 2026-07-27
**Data:** CURRENT epoch, n=323 paired trades
**Variable:** SL distance only (M5 candle 2.56 pips vs M15 structure 5.64 pips)
**Exit logic:** Identical (SL at -1R + timeout at 60 bars, NO TP)
**Validity:** All pre-experiment checks passed (100% pairing confirmed)

---

## 1. Research Question

> Does H1 directional structure + M15 structure-based stop placement + minimum risk distance produce higher cost-adjusted EV than the current M5 candle-risk architecture?

---

## 2. Experimental Design

| Element | Control (M5) | Variant (M15) |
|---------|-------------|--------------|
| Entry signal | Same pattern | Same pattern |
| Direction | Same | Same |
| Score | Same | Same |
| H1 bias | Same | Same |
| Phase | Same | Same |
| **SL source** | **M5 candle (2.56 pips)** | **M15 structure (5.64 pips)** |
| Exit logic | SL + timeout | SL + timeout |
| Timeout | 60 bars | 60 bars |
| TP | None (unreachable) | None (unreachable) |

---

## 3. Variable Isolation Verification

| Check | Result |
|-------|--------|
| Direction match | 100% (300/300 sample checks) |
| Pattern match | 100% |
| Score match | 100% (within 0.01) |
| H1 bias match | 100% |
| Phase match | 100% |
| Timestamp match | 100% |
| Only SL differs | ✅ Confirmed |

---

## 4. Data Quality

| Metric | Value |
|--------|-------|
| Total CURRENT trades | 867 |
| Paired trades (same cycle+symbol) | **323** |
| H1 bias coverage | 100% on paired set |
| Phase coverage | 100% on paired set |
| trade_state_progression | 100% |
| Epoch | CURRENT only |

---

## 5. Results

| Metric | Control (M5 SL) | Variant (M15 SL) |
|--------|-----------------|------------------|
| **Cost-adjusted EV** | **-0.810R** | **-0.342R** |
| 95% CI | [-0.955, -0.665] | [-0.411, -0.273] |
| Raw EV (before costs) | -0.022R | -0.037R |
| Win rate | 11.8% | 12.4% |
| Profit factor | 0.046 | 0.056 |
| Median adjusted R | -0.636R | -0.218R |
| Avg MFE (R) | 0.275 | 0.108 |
| Avg MAE (R) | 0.379 | 0.173 |
| Spread/Risk ratio | **0.788** | **0.305** |
| Timeout % | 91.3% | 98.8% |
| Stop loss % | 8.7% | 1.2% |
| Avg hold (bars) | 7.8 | 7.0 |
| Max drawdown (R) | 261.6 | 110.4 |

---

## 6. Statistical Analysis

### Paired Significance Test

| Statistic | Value |
|-----------|-------|
| Mean improvement (Variant - Control) | **+0.468R per trade** |
| 95% CI of improvement | **[+0.320, +0.617]** |
| t-statistic | 6.177 |
| p-value | **< 0.00000001** |
| Effect size (Cohen's d) | 0.344 (small-medium) |
| Significant at 5% | ✅ YES |
| Significant at 1% | ✅ YES |

### Walk-Forward Validation

| Period | n | Improvement | 95% CI | CI above zero? |
|--------|---|-------------|--------|---------------|
| Train (first 60%) | 193 | +0.605R | [+0.369, +0.841] | ✅ YES |
| Test (last 40%) | 130 | +0.265R | [+0.155, +0.376] | ✅ YES |

**The improvement survives out-of-sample with CI entirely above zero.**

### Variant EV > 0 Test

| Metric | Value |
|--------|-------|
| Variant adj EV | -0.342R |
| t-test (EV > 0) | t = -9.71, p = 1.0 |
| **Significantly positive?** | **❌ NO** |

---

## 7. Cost-Adjusted Analysis

| Component | Control | Variant |
|-----------|---------|---------|
| Raw signal EV | -0.022R | -0.037R |
| Spread cost per trade | 0.788R | 0.305R |
| **Net (cost-adjusted) EV** | **-0.810R** | **-0.342R** |

The variant reduces cost from 0.79R to 0.31R per trade (2.6× reduction). This is the PRIMARY source of the 0.47R improvement.

---

## 8. Sub-Group Analysis

### H1 Aligned (direction matches H1 bias)

| Metric | Variant Adj EV | n |
|--------|---------------|---|
| H1 aligned | -0.302R | 182 |
| Improvement vs control | +0.315R [+0.202, +0.428] | |

### PULLBACK Phase + H1 Aligned

| Metric | Variant Adj EV | n |
|--------|---------------|---|
| Pullback + aligned | -0.209R | 74 |

Still negative. Best sub-group is -0.21R (not viable).

---

## 9. Threats to Validity

| Threat | Assessment | Mitigation |
|--------|-----------|-----------|
| RR target differs (2:1 vs 3:1) | Mitigated | Simulated identical exit (no TP) |
| Same max_bars for both | Real limitation | True structure entry would hold longer. This experiment is conservative. |
| Spread estimated (not recorded) | Real limitation | Used conservative typical spreads per symbol. Future trades will have spread_at_entry. |
| INTRADAY progression normalised to its own risk | Correct — each R-unit represents that variant's risk distance | No bias introduced |
| Sample size | n=323 adequate for paired test | CI reported |
| Walk-forward | ✅ Passes | Test period confirms with CI above zero |

---

## 10. Decision

### 🟡 CONTINUE TESTING

**The improvement is REAL, VALIDATED, and SIGNIFICANT:**
- +0.47R per trade improvement (p < 0.001)
- Walk-forward confirms (test CI entirely positive)
- Effect is mechanically explained (cost reduction from wider SL)

**But the system still does NOT achieve positive EV:**
- Variant EV = -0.342R (still negative)
- 95% CI entirely below zero
- No sub-group achieves positive EV

**This means:** Structure geometry REDUCES the cost problem (from 0.79R to 0.31R per trade) but the underlying signal ALSO needs to be neutral-to-positive for the combined system to work. Current signal is -0.037R raw (slightly negative).

---

## 11. Recommendation

### What this experiment PROVES:

1. ✅ M15 structure SL reduces cost-to-risk ratio by 2.6× (validated, walk-forward confirmed)
2. ✅ The improvement is not a fluke (p < 0.001, Cohen's d = 0.34, OOS confirms)
3. ❌ The improvement alone is NOT sufficient for profitability

### What STILL needs to be true for the system to work:

For the M15-structure variant to reach positive EV, it needs:
- Raw signal EV to be approximately 0 or positive (currently -0.037R)
- OR additional filtering to remove the worst entries

**The gap: -0.342R.** The signal needs to improve by +0.35R to break even.

### Next experiment to run:

**EI10 on the VARIANT (M15 structure) subset specifically:**

> "Within the M15-structure-SL trades (n=323), does the combination of H1 aligned + PULLBACK phase + score ≥ 0.60 produce positive cost-adjusted EV?"

The best sub-group found (PULLBACK + H1 aligned) produces -0.209R with n=74. If score filtering can push this another 0.21R positive, the system becomes viable.

This is the LAST remaining test before the foundation decision.

---

## 12. Knowledge Update

```
SV1: M15 structure geometry improves cost-adjusted EV by +0.47R vs M5 candle geometry.
  Status: VALIDATED (p<0.001, walk-forward confirmed, n=323)
  But: Variant EV still -0.34R (not viable alone)
  Unlocks: EI10 on structure-geometry subset
  Does NOT unlock: promotion, deployment, or strategy changes
```
