# V2.1 Confirm-or-Cut Validation Results

**Date:** 2026-07-28
**Data:** CURRENT epoch, INTRADAY (M15 SL) trades, n=328
**Method:** Exit at bar-1 close if bar1 <= threshold; hold otherwise
**Cost model:** Symbol-specific spread / risk_distance
**Validation:** Walk-forward 60/40 split

---

## DECISION: ❌ FAIL

**The confirm-or-cut mechanism does NOT produce positive EV.** No threshold tested improves adjusted EV. Walk-forward validation shows zero improvement and possible degradation.

---

## Baseline

| Metric | Value |
|--------|-------|
| Trades | 328 |
| Raw EV | -0.038R |
| **Adj EV** | **-0.343R** [-0.411, -0.275] |
| Win rate | 12.2% |
| Profit factor | 0.055 |

---

## Variant Results

| Cut Threshold | Trades Cut | Trades Held | Raw EV | Adj EV | 95% CI | WR | PF |
|---|---|---|---|---|---|---|---|
| bar1 ≤ -0.20 | 27 (8%) | 301 | -0.068 | -0.372 | [-0.470, -0.275] | 11.6% | 0.049 |
| bar1 ≤ -0.10 | 67 (20%) | 261 | -0.063 | -0.368 | [-0.465, -0.271] | 11.3% | 0.048 |
| bar1 ≤ -0.05 | 104 (32%) | 224 | -0.061 | -0.366 | [-0.462, -0.269] | 10.4% | 0.046 |
| **bar1 ≤ 0.00** | **210 (64%)** | **118** | **-0.057** | **-0.362** | **[-0.458, -0.266]** | 8.5% | 0.043 |
| bar1 ≤ +0.05 | 260 (79%) | 68 | -0.057 | -0.362 | [-0.457, -0.266] | 6.4% | 0.033 |
| bar1 ≤ +0.10 | 288 (88%) | 40 | -0.055 | -0.360 | [-0.455, -0.264] | 5.2% | 0.031 |
| **Baseline** | **0** | **328** | **-0.038** | **-0.343** | **[-0.411, -0.275]** | **12.2%** | **0.055** |

### Critical Observation

**Every confirm-or-cut variant produces WORSE adjusted EV than the baseline** (-0.36 to -0.37R vs baseline -0.343R). The model makes things worse, not better.

---

## Why Confirm-or-Cut FAILS

### The mechanism explained:

1. When bar1 ≤ 0: exit at bar-1 close price (typically a small negative R)
2. This IMMEDIATELY realizes the spread cost (cost_r ≈ 0.30R) plus the bar-1 loss
3. The trade pays the full spread cost for a 5-minute exposure
4. The "held" trades also pay the spread cost but have more time to recover

### The math:

- Cutting a trade at bar1 = -0.05R produces adj result = -0.05 - 0.30 = **-0.35R**
- Holding the same trade: it might recover to 0.0R at timeout → adj = 0.0 - 0.30 = **-0.30R**

**Cutting EARLY forces realisation of the spread loss immediately.** Holding gives the trade a chance (however small) to recover enough to offset the spread. The "confirm-or-cut" mechanism essentially locks in the spread loss faster.

### Why the V2 CQ5 finding was misleading:

The bar-1 correlation (r=0.42) between bar-1 and final-R was genuine — but it measured correlation with the FULL outcome (which includes timeout at 60 bars). It does NOT mean that cutting at bar-1 improves total EV, because:

1. Trades that start negative sometimes recover (cutting prevents this)
2. The cost of cutting (spread) is paid regardless
3. The correlation exists because BAD trades (that eventually hit SL) start negative AND end negative — not because cutting them early saves money

---

## Walk-Forward Validation

| Period | Baseline Adj EV | Variant Adj EV | Improvement | Significant? |
|--------|----------------|---------------|-------------|-------------|
| Train (60%, n=196) | -0.328R | -0.324R | +0.004R | NO (p=0.80) |
| Test (40%, n=132) | -0.364R | -0.417R | **-0.053R (WORSE)** | NO (p=0.24) |

**The model degrades out-of-sample.** Train shows negligible improvement; test shows degradation.

---

## Subgroup Analysis

### By Symbol (threshold=0.0)

| Symbol | n | Baseline | CutModel | Improvement |
|--------|---|----------|----------|-------------|
| NZDUSD | 75 | -0.403R | -0.397R | +0.005R |
| EURUSD | 54 | -0.385R | -0.365R | +0.021R |
| USDCAD | 50 | -0.230R | -0.211R | +0.020R |
| AUDUSD | 45 | -0.239R | -0.236R | +0.003R |
| GBPUSD | 44 | -0.248R | -0.263R | -0.016R |
| USDCHF | 38 | -0.395R | **-0.542R** | -0.147R |
| USDJPY | 22 | -0.603R | -0.720R | -0.117R |

**No symbol shows meaningful improvement.** USDCHF and USDJPY degrade significantly.

### By Phase

| Phase | n | Baseline | CutModel | Diff |
|-------|---|----------|----------|------|
| IMPULSE | 86 | -0.342R | -0.372R | -0.030R (worse) |
| CONSOLIDATION | 81 | -0.400R | -0.419R | -0.019R (worse) |
| PULLBACK | 75 | -0.212R | -0.243R | -0.032R (worse) |
| REVERSAL | 70 | -0.423R | -0.416R | +0.006R (negligible) |

**No phase benefits from confirm-or-cut.**

---

## Validity Assessment

| Check | Status |
|-------|--------|
| CURRENT epoch only | ✅ |
| Same entry (only exit changes) | ✅ |
| No look-ahead | ✅ (bar-1 known at bar-1 close) |
| Transaction costs included | ✅ |
| Walk-forward validation | ✅ (FAILS — degrades OOS) |
| Statistical testing | ✅ (no significance found) |
| Minimum sample | ✅ n=328 |

---

## Success Criteria Evaluation

| Criterion | Required | Actual | Status |
|-----------|----------|--------|--------|
| Cost-adjusted EV > 0 | > 0 | -0.362R (best variant) | ❌ FAIL |
| n ≥ 200 | ≥ 200 | 328 | ✅ PASS |
| Significant improvement over baseline | p < 0.05 | p = 0.80 | ❌ FAIL |
| Survives walk-forward | Train + test positive | Test WORSE | ❌ FAIL |

---

## CONCLUSION

### ❌ FAIL — Bar-1 behaviour does NOT create a viable execution layer.

The first-bar correlation (r=0.42) with final outcome is statistically real but not actionable:

1. **Cutting early locks in spread loss** (you pay 0.30R in costs for 5 minutes of exposure)
2. **Holding bad starters sometimes allows recovery** (timeout at 0R > cutting at -0.05R)
3. **The bar-1 signal is DESCRIPTIVE, not PRESCRIPTIVE** — it describes which trades will eventually fail, but cutting them early doesn't save money because the spread is already paid

### What this means for V2:

The last remaining "predictive signal" (bar-1 velocity) does not translate into positive EV when used as an exit mechanism. Combined with the V2 context results (H1/H4/Phase all fail):

> **No information available to this system — either before or immediately after entry — can be used to create positive expected value after transaction costs.**

---

## Final Research Position

| Hypothesis | Status |
|-----------|--------|
| V1: Pattern predicts direction | ❌ DISPROVEN |
| V2: Pre-entry context predicts direction | ❌ DISPROVEN |
| V2.1: Post-entry bar-1 enables confirm-or-cut | ❌ DISPROVEN |

All three architectural hypotheses have been empirically tested and rejected. The system's information set (candlestick patterns + H1/H4 context + M15 structure) does not contain exploitable predictive value on M5 FX at current spread levels.
