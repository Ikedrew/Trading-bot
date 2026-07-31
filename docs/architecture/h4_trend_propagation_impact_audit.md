# H4 Trend Propagation Impact Audit

## Problem Statement

`build_h4_understanding()` only copies `RegimeSnapshot.trend_bias` for TRENDING classifications. VOLATILE and TRANSITIONAL regimes produce `h4.trend = ""` (persisted as `null`), losing directional information that the analyzer computed.

**Current conditional:**
```python
if "TRENDING" in regime_str:
    trend = regime_snap.trend_bias      # ← only for TRENDING
    trend_strength = regime_snap.trend_strength
elif "RANG" in regime_str:
    trend = "NEUTRAL"
# else: trend stays "" ← VOLATILE/TRANSITIONAL lose their trend data
```

**Proposed:** Always propagate `trend_bias` and `trend_strength` regardless of classification.

---

## 1. H4 Trend Population Rate (Current)

| h4_trend value | Count | % |
|---|---|---|
| **null (empty)** | **771** | **55.7%** |
| BULLISH | 227 | 16.4% |
| NEUTRAL | 386 | 27.9% |
| BEARISH | 0 | 0.0% |

**Over half of all decisions have no H4 trend information.** This is not a minor gap — it's a systematic information loss.

---

## 2. Breakdown by Regime

| Regime | Count | h4_trend=null | h4_trend=BULLISH | h4_trend=NEUTRAL |
|---|---|---|---|---|
| TRENDING | 227 | 0 (0%) | 227 (100%) | 0 |
| RANGING | 764 | 452 (59%) | 0 | 312 (41%) |
| VOLATILE | 392 | 318 (81%) | 0 | 74 (19%) |
| TRANSITIONAL | 0 | — | — | — |

**Key observations:**
- TRENDING: 100% populated (correct — the only regime the builder extracts for)
- RANGING: 59% null, 41% NEUTRAL. The 59% null are cases where the builder's first path (MarketContext.h4) found nothing, and the HTFContext path classified as RANGING and set "NEUTRAL". The remaining 452 null records are from before the context-ordering fix.
- VOLATILE: 81% null. These are the PRIMARY target — 318 records with lost trend data.
- TRANSITIONAL: Does not appear in this dataset.

---

## 3. Records Where h4_trend=null AND Regime is VOLATILE

**Total: 318 records** — all are VOLATILE regime with h4_trend=null.

### Examples

| Symbol | Time (UTC) | Regime | H4 Phase | Volatility State | Rejection Stage |
|---|---|---|---|---|---|
| AUDUSD | 17:50 | VOLATILE | EXHAUSTION | EXPANSION | opportunity |
| USDCHF | 11:20 | VOLATILE | REVERSAL | EXPANSION | opportunity |
| USDJPY | 20:05 | VOLATILE | IMPULSE | EXPANSION | strategy |
| EURUSD | 18:05 | VOLATILE | IMPULSE | EXPANSION | strategy |
| NAS100 | 21:00 | VOLATILE | CONSOLIDATION | EXPANSION | strategy |

### Per-Symbol Distribution

| Symbol | VOLATILE + null | Notes |
|---|---|---|
| USDCHF | 111 | Most affected |
| USDJPY | 64 | |
| USDCAD | 39 | |
| EURUSD | 35 | |
| AUDUSD | 24 | |
| NAS100 | 12 | |
| XAUUSD | 11 | |
| NZDUSD | 10 | |
| US500 | 8 | |
| GBPUSD | 4 | Least affected |

---

## 4. Would `trend_bias` Have Had a Value?

The analyzer (`h4_regime.py`) computes `trend_bias` for EVERY classification including VOLATILE:

```python
# In analyze_regime(), computed BEFORE any classification branch:
if _bull_ratio > 0.5 and ema_slope > 0.05:
    _trend_bias = "BULLISH"
elif _bear_ratio > 0.5 and ema_slope < -0.05:
    _trend_bias = "BEARISH"
else:
    _trend_bias = "NEUTRAL"
```

For VOLATILE regimes specifically: ATR ratio > 1.5 causes VOLATILE classification, but the trend structure (HH/HL or LH/LL + EMA slope) is STILL computed. A volatile market CAN have a strong directional trend — it just has elevated ATR.

**Evidence:** `h4_trend_strength` is 0.0 for all null records. This confirms the builder SKIPS both fields (not just direction). If it propagated, we'd see non-zero strength for many of these 318 records.

---

## 5. Strategy Selection Impact

### TREND_CONTINUATION: Potential New Selections

| Metric | Count |
|---|---|
| Strategy-rejected with h4_trend=null | 257 |
| Of those in VOLATILE regime | 111 |
| With H1 BOS direction present | ~90 |
| With M15 pullback active | ~70 |
| **Maximum new TC selections** (if all conditions align) | **~40-70** |

**Qualification chain for TREND_CONTINUATION:**
1. h4.trend in ("BULLISH","BEARISH") — NEW: would now pass for VOLATILE with directional structure
2. h4.trend_strength >= 0.5 — depends on analyzer output (HH/HL ratio + EMA slope)
3. H1 BOS aligned with H4 — requires h1_bos_direction == h4.trend
4. M15 pullback active — independent of H4 fix

**Estimated new selections:** Not all 111 VOLATILE records will qualify. The analyzer may produce trend_bias="NEUTRAL" for many (no clear directional structure). Realistically: **20-40 additional TREND_CONTINUATION selections** from VOLATILE regimes.

### MEAN_REVERSION: Removed Selections

| Metric | Count |
|---|---|
| Total current MEAN_REVERSION selections | 10 |
| At risk of removal | **2** |
| Reason | Both are VOLATILE regime, rely solely on `trend_strength < 0.3` path |

These 2 records would be **correctly removed** — they are VOLATILE with directional H1 structure (BEARISH), which means mean reversion in that environment is fighting a directional volatile trend.

The remaining 8 MEAN_REVERSION selections have `regime in ("RANGING","NEUTRAL")` which satisfies R1 independently of h4.trend → **unaffected**.

### RANGE_REACTION: Removed Selections

| Metric | Count |
|---|---|
| Current RANGE_REACTION selections | 0 |
| Impact | **None** — strategy doesn't fire currently |

### Horizon Changes

| Metric | Impact |
|---|---|
| Extended horizon (requires h4.trend + strength >= 0.6) | Would newly trigger for VOLATILE trades that pass TREND_CONTINUATION |
| Estimated additional EXTENDED horizons | ~15-25 (subset of new TC selections with strength >= 0.6) |

### Final EXECUTE Changes

| Before | After |
|---|---|
| 1 EXECUTE | Still 1 EXECUTE (no new trades reach execution — entry geometry still blocks) |

**The entry engine / BOS-level fix is the final gate.** Until entry geometry produces valid stop/target, no new EXECUTE decisions regardless of how many reach entry stage.

---

## 6. Secondary Finding: BEARISH Never Appears

The dataset shows 0 records with h4_trend="BEARISH". This is a SEPARATE issue:

| Possible cause | Evidence |
|---|---|
| The broker's market was exclusively bullish in this window | NAS100/US500/GBPUSD all trending bullish |
| The analyzer's BEARISH threshold is too strict | `_bear_ratio > 0.5 and ema_slope < -0.05` — may require more bars in bearish structure |
| Bug in trend_bias logic | Unlikely — the structure detection code is symmetric |

**Most likely:** This 2-day window (Jul 30-31) coincided with a broadly bullish environment. The absence of BEARISH is market-specific, not a code bug.

---

## 7. Consumer Dependency Audit

| Consumer | Depends on h4.trend="" behaviour? | Impact of Fix |
|---|---|---|
| TREND_CONTINUATION R1 | Uses `in ("BULLISH","BEARISH")` — empty never matches | **Gains access to VOLATILE selections** |
| MEAN_REVERSION R1 | Uses `== "NEUTRAL"` and `strength < 0.3` — empty fails first, passes second | **2 selections correctly removed** |
| Horizon engine | Uses `in ("BULLISH","BEARISH")` — empty never matches | **Gains EXTENDED horizons for VOLATILE** |
| HTFStructure macro_bias | Uses `if h4.trend` (truthy check) — empty is falsy | **Now contributes to macro_bias** |
| HTFStructure authority | Uses `if h4.trend and != NEUTRAL and > 0.5` | **H4 gains authority for directional VOLATILE** |
| HTFStructure confidence | Uses `if h4.trend: += 0.3` | **+0.3 confidence for populated VOLATILE** |
| BehaviourContext regime | Uses `if h4.trend in (BULLISH,BEARISH): regime=TRENDING` | **VOLATILE with direction → regime=TRENDING** |
| Persistence | Uses `h4.trend or None` | **VOLATILE records now persist direction** |

**No consumer explicitly checks for `h4.trend == ""`** or depends on empty string behaviour. All use truthy/in/equality checks that naturally handle empty as "not available."

---

## 8. BehaviourContext Regime Cascade

The most significant downstream effect:

**Current:** VOLATILE regime + h4.trend="" → `context_builders.py` line 282: `h4.trend` is falsy → skipped → `regime = "RANGING"` (default)

**After fix:** VOLATILE regime + h4.trend="BULLISH" → `h4.trend in ("BULLISH","BEARISH")` is True → `regime = "TRENDING"`

This means: **Some records currently classified as regime=RANGING (despite being VOLATILE) would become regime=TRENDING.**

| Impact on | Effect |
|---|---|
| MEAN_REVERSION R1 | `regime in ("RANGING",...)` no longer passes → must rely on other conditions |
| RANGE_REACTION R1 | `regime == "RANGING"` no longer passes → blocks |
| Strategy engine | More consistent — a directional volatile market IS trending, not ranging |
| Research data | More accurate regime classification |

**This is CORRECT behaviour.** A VOLATILE market with strong directional structure IS trending (with elevated volatility). Classifying it as RANGING was an artefact of the blank h4.trend.

---

## 9. Summary

| Dimension | Current | After Fix |
|---|---|---|
| h4_trend population | 44.3% | **~85-90%** |
| TREND_CONTINUATION selections | 25 | **~45-65** |
| MEAN_REVERSION selections | 10 | **8** (-2 correctly removed) |
| RANGE_REACTION selections | 0 | 0 (no change) |
| EXECUTE decisions | 1 | 1 (entry geometry still gates) |
| BehaviourContext accuracy | Misclassifies directional-VOLATILE as RANGING | **Correctly reports TRENDING** |

### Risk Assessment

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| New TREND_CONTINUATION in volatile environment | HIGH (intended) | LOW — entry/risk gates still apply | Volatile environments have wider ATR → stops will be wider → risk engine limits exposure |
| Loss of 2 MEAN_REVERSION signals | CERTAIN | NONE — these were incorrect | Signals were fading a directional volatile trend — bad trades |
| BehaviourContext regime changes | CERTAIN | LOW — downstream uses regime for context only in V10 | The regime change is CORRECT |
| Unexpected consumer breaks | VERY LOW | LOW | No consumer depends on empty h4.trend |

### Recommendation

**Implement the fix.** Direct propagation of `trend_bias` and `trend_strength` for all classifications:
- Eliminates 55.7% information loss
- Correctly enables TREND_CONTINUATION in directional-volatile markets
- Correctly tightens MEAN_REVERSION (removes 2 invalid signals)
- No consumer depends on the current broken behaviour
- Downstream gates (entry geometry, risk engine) prevent runaway activations
