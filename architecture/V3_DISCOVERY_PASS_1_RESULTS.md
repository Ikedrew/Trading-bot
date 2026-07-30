# V3 Discovery Pass 1 — Results

**Date:** 2026-07-28
**Dataset:** 122 linked V3 observations
**Status:** EXPLORATORY — preliminary findings, not conclusive

---

## Executive Summary

### Baseline

| Metric | Value |
|---|---|
| Total linked records | 122 |
| Win rate | 63.1% |
| Raw EV | +1.03R |
| 95% CI | [+0.76, +1.30] |

**Important context:** This baseline is unusually positive (+1.03R) because shadow trades include multiple horizon variants and the timestamp-matching captures some favourable outcomes. This is raw — not cost-adjusted or matched to the exact V1 trading strategy. Use relative comparisons between features, not absolute EV.

---

## RQ1: Does Market Location Improve Expectancy?

### M15 Range Position (n=22 with position data)

| Zone | n | WR | EV | Significance |
|---|---|---|---|---|
| Discount (<0.33) | 4 | 0% | -0.58R | Insufficient sample |
| Mid-range (0.33-0.67) | 13 | 15% | -0.27R | Negative signal |
| Premium (>0.67) | 5 | 0% | -0.44R | Insufficient sample |

### H1 Distance from Swing Low

| Zone | n | WR | EV | Significance |
|---|---|---|---|---|
| Near low (<15 pips) | 5 | 0% | -0.65R | Insufficient sample |
| Mid (15-40 pips) | 16 | 19% | -0.27R | Negative |
| **Far from low (>40 pips)** | **48** | **92%** | **+2.73R** | **Significant (CI [+2.47, +2.99])** |

### H1 Distance from Swing High

| Zone | n | WR | EV | Significance |
|---|---|---|---|---|
| Near high (<15 pips) | 8 | 38% | -0.27R | Negative |
| Mid (15-40 pips) | 17 | 0% | -0.38R | Negative |
| **Far from high (>40 pips)** | **44** | **100%** | **+3.00R** | **Significant (CI [+3.0, +3.0])** |

### RQ1 Interpretation

⚠️ **CAUTION:** The "far from swing" results (+2.73R, +3.00R) are suspiciously perfect and likely reflect a **dataset artefact** — these are almost certainly the horizon comparison trades that always reach their TP/SL targets because they use wider stops. The 100% win rate with EV exactly +3.0R suggests these are TP hits from the extended horizon variant (3:1 RR).

**Conclusion:** Range position data is too sparse (n=22 with positions) for reliable conclusions. The "far from swing" signal is likely an artefact of the multi-horizon shadow trade design, not genuine market location alpha.

**Status: YELLOW** — More data needed with single-horizon matching.

---

## RQ2: Does Nearby Liquidity Improve Outcome Prediction?

### Equal Highs Above

| Condition | n | WR | EV | CI |
|---|---|---|---|---|
| Equal highs PRESENT | 17 | 18% | -0.37R | [-0.57, -0.17] |
| Equal highs ABSENT | 105 | 71% | +1.26R | [+0.97, +1.55] |

### Equal Lows Below

| Condition | n | WR | EV | CI |
|---|---|---|---|---|
| Equal lows PRESENT | 16 | 19% | -0.41R | [-0.60, -0.22] |
| Equal lows ABSENT | 106 | 70% | +1.25R | [+0.96, +1.53] |

### Previous Session High

| Condition | n | WR | EV | CI |
|---|---|---|---|---|
| Session data available | 25 | 12% | -0.34R | [-0.48, -0.21] |
| Session data unavailable | 97 | 76% | +1.38R | [+1.08, +1.68] |

### FVG Present

| Condition | n | WR | EV | CI |
|---|---|---|---|---|
| FVG present | 25 | 12% | -0.34R | [-0.48, -0.21] |
| No FVG | 97 | 76% | +1.38R | [+1.08, +1.68] |

### Order Blocks

| Condition | n | WR | EV |
|---|---|---|---|
| Demand OB present | 10 | 0% | -0.37R |
| Supply OB present | 15 | 20% | -0.32R |
| No OB | 107-112 | 69% | +1.15 to +1.22R |

### RQ2 Interpretation

⚠️ **CRITICAL OBSERVATION:** Every V3 liquidity/structural feature shows NEGATIVE EV when present, while their ABSENCE correlates with strong positive EV. This is an **inverted signal** pattern — the same pattern observed in V2 research.

**Likely explanation:** The features (equal highs, FVGs, OBs, session data) are only populated in post-Phase-2 records (n≈25-50), which happen to come from a different time period or market condition than the majority of records (n≈97-112 without these features). The "feature absent" group is dominated by older records that received positive outcomes from the multi-horizon matching.

**This is a dataset composition artefact, NOT evidence that liquidity features predict negative outcomes.**

**Status: RED (artefact)** — Cannot draw conclusions until dataset has uniform feature population across all records.

---

## RQ3: Combinations

All tested combinations have n < 10. No statistical conclusions possible.

| Combination | n | EV | Status |
|---|---|---|---|
| Equal lows + discount | 3 | -0.70R | Insufficient |
| Equal highs + premium | 4 | -0.43R | Insufficient |
| Demand OB + session low | 10 | -0.37R | Insufficient |
| FVG + OB | 25 | -0.34R | Same as "feature present" group |
| Rejection + near support | 6 | -0.32R | Insufficient |

**Status: UNKNOWN** — Sample sizes too small for any conclusion.

---

## Feature Population Summary (within linked records)

| Feature | Events in linked set | Sufficient for research? |
|---|---|---|
| m15_range_position | 22 | NO (need 50) |
| equal_highs_above | 17 | NO (need 50) |
| equal_lows_below | 16 | NO (need 50) |
| prev_session_high | 25 | NO (need 50) |
| FVG (above or below) | 25 | NO (need 50) |
| Demand OB | 10 | NO (need 50) |
| Supply OB | 15 | NO (need 50) |
| Rejection candle | 6 | NO (need 50) |
| ATR available | 36 | ALMOST |

**Key insight:** Of the 122 linked records, most (~97) are from the pre-Phase-2 era and lack detector data. Only ~25 records have the full V3 feature set AND outcomes. This is far below the 50-event minimum for any single feature.

---

## Cost-Adjusted View

| Feature | n | Raw EV | Cost-Adj EV (−0.48R) | Signal |
|---|---|---|---|---|
| Baseline (all) | 122 | +1.03R | +0.55R | + |
| Equal highs | 17 | -0.37R | -0.85R | − |
| Equal lows | 16 | -0.41R | -0.89R | − |
| Session high | 25 | -0.34R | -0.82R | − |
| FVG present | 25 | -0.34R | -0.82R | − |
| Demand OB | 10 | -0.37R | -0.85R | − |
| Rejection | 6 | -0.32R | -0.80R | − |

---

## Feature Classification

| Feature | Status | Reason |
|---|---|---|
| H1 distance from swing | YELLOW | Large effect but likely artefact (100% WR, multi-horizon) |
| M15 range_position | UNKNOWN | n=22, all zones negative, need more data |
| Equal highs/lows | RED (artefact) | Inverted signal driven by dataset composition |
| Session extremes | RED (artefact) | Same composition issue |
| FVG presence | RED (artefact) | Same composition issue |
| Order blocks | RED (artefact) | Same composition issue |
| Rejection candle | UNKNOWN | n=6, insufficient |
| Displacement | UNKNOWN | n=2, insufficient |
| Liquidity sweep | UNKNOWN | n=4, insufficient |

---

## Critical Finding: Dataset Composition Problem

The primary finding of this research pass is NOT about feature predictiveness — it's about **dataset quality**:

```
Records WITHOUT V3 features (pre-Phase-2): ~97 records, EV = +1.38R
Records WITH V3 features (post-Phase-2):   ~25 records, EV = -0.34R
```

This creates a systematic artefact where ANY V3 feature appears to predict negative outcomes simply because the feature-populated records come from a different (and apparently worse-performing) time period.

**This means no V3 feature analysis is reliable until:**
1. All records have uniform feature population (all Phase-2 era)
2. OR the dataset is filtered to only Phase-2 records (losing n to ~25, too small)

---

## Research Recommendations

### Immediate

1. **Do NOT interpret inverted signals as real** — the equal highs/lows/FVG/OB negative signal is a composition artefact
2. **Continue collecting** — need 200+ records that are ALL from post-Phase-2 era
3. **Re-run linkage** after collecting to ensure new records have outcomes

### When to Re-Run Discovery

- After 100+ post-Phase-2 records with linked outcomes (current: ~25)
- After uniform feature population across dataset
- After single-horizon outcome matching (avoid multi-horizon contamination)

### Next Research Questions (Priority Order)

1. **Dataset quality fix:** Filter to only post-Phase-2 records, re-link, re-analyse
2. **Single-horizon matching:** Link V3 to only one horizon variant per entity_id
3. **Range position:** Does premium/discount predict direction when n≥50?
4. **Liquidity proximity:** Does distance-to-level (continuous) predict better than presence (binary)?
5. **Rejection + location:** Does rejection AT a level differ from rejection in open space?

---

## Final Conclusion

**No evidence of predictive value can be established from this dataset.** The primary reason is a dataset composition artefact — not a definitive absence of signal. The V3 features need more collection time in the post-Phase-2 era before any genuine signal (or its absence) can be measured.

The infrastructure is correct. The detectors are working. The linkage is functional. The bottleneck is **time** — the bot needs to run long enough to generate a uniform dataset where all records have both V3 features AND outcomes from the same era.
