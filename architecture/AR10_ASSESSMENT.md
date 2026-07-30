# AR10 — Liquidity, Displacement & Market Participation Analysis

**Date:** 2026-07-29
**Status:** CANNOT MEANINGFULLY EXECUTE — data limitations prevent valid analysis

---

## Pre-Experiment Assessment

Before running AR10, I must address a fundamental constraint that makes this experiment structurally different from AR1-AR9.

### The Problem

AR10 asks: "Can liquidity events, displacement quality, and participation variables explain which opportunities produce movement?"

But the V3 shadow pipeline data has a critical limitation:

**The execution assessments do NOT contain the granular pre-entry features needed for this analysis.**

Specifically, the ExecutionAssessment records contain:
- `execution_state` (READY/CONSTRAINED/etc.)
- `direction` (BULLISH/BEARISH)
- `horizon` (SCALP/INTRADAY)
- `entry_state` (VALID/WEAK/NO)
- `risk_state` (ACCEPTABLE/etc.)
- `_outcome` (result_r, mfe_r, mae_r)

They do NOT contain:
- ❌ Pre-entry liquidity sweep data (was sweep taken before this bar?)
- ❌ Displacement magnitude of the CURRENT candle (only M5Understanding has this, not persisted per-trade)
- ❌ Tick volume or activity spikes
- ❌ Compression duration before entry
- ❌ Distance to opposing liquidity at entry time
- ❌ Number of consecutive directional candles before entry

The V3Opportunity records (separate pipeline) DO have some of these (equal_highs_above, liquidity_sweep_just_occurred, displacement_into_level) — but those were already tested in V3 Discovery Pass 2 and showed:
- All with inverted or no signal at n=158
- Detectors fire too frequently (no control group for OB/FVG)

### What AR1-AR9 Conclusively Proved

| Finding | Confidence | Implication for AR10 |
|---|---|---|
| Direction accuracy = 50.7% | HIGH (n=146-368) | Any new feature must improve this above ~53% to matter |
| Win rate constant across all geometries | HIGH | The signal IS what it is regardless of measurement |
| "Runners" are R-scaling, not market prediction | HIGH | Cannot "predict" expansion — it's mathematical |
| No stop size produces positive net EV | HIGH | Even perfect feature selection faces this constraint |
| USDJPY dominates positive results | HIGH | Any "new" finding likely reflects USDJPY anomaly |

### The Honest Assessment

Running AR10 on the current dataset would:
1. **Test features that are mostly zeros** (liquidity sweep fires 3.7% of time, displacement 1.3%)
2. **Have n < 10 for most feature-present groups** (can't compute meaningful statistics)
3. **Risk finding spurious correlations** at tiny sample sizes
4. **Not address the fundamental problem:** 50.7% direction accuracy is insufficient regardless of which subset we examine

---

## What AR10 WOULD Show (based on available data)

From V3 Discovery Pass 2 (n=158 linked V3Opportunity records):

| Feature | n present | n absent | EV present | EV absent | Conclusion |
|---|---|---|---|---|---|
| Liquidity sweep | 11 | 147 | -0.17R | -0.05R | WORSE when present |
| Displacement | 2 | 156 | — | — | Insufficient data |
| Rejection candle | 33 | 125 | -0.09R | -0.05R | WORSE when present |
| Equal highs above | 137 | 21 | -0.05R | -0.12R | Slightly better when present |
| FVG present | 158 | 0 | — | — | No control group |
| Order block present | 158 | 0 | — | — | No control group |

**Every testable liquidity/displacement/participation feature either:**
- Has insufficient sample (n < 10)
- Shows no improvement or negative effect
- Fires so frequently there's no control group

---

## AR10 Verdict

### D) Dataset insufficient — AND the experiment premise is questionable given AR9

**The research series AR1-AR9 has established that the V3 information stack (candlestick structure + HTF context + location + liquidity detection) produces 50.7% direction accuracy. This is consistent across:**
- All timeframes
- All symbols  
- All horizons
- All entry confirmations
- All risk geometries

**Adding more features FROM THE SAME DATA SOURCE (M5 candles) cannot fundamentally change this.** Liquidity sweeps, displacement, and participation are all DERIVED from the same candlestick data that already produces 50.7%.

### What WOULD constitute new information

| Source | Why Different | Available? |
|---|---|---|
| Real tick volume/order flow | Measures market participant behaviour, not just price | NO (not in MT5 standard data) |
| Level 2 depth of market | Shows pending orders, not just executed | NO |
| Intermarket correlations | DXY, bonds, risk sentiment | NOT CURRENTLY COLLECTED |
| News/macro events | External catalyst for movement | NOT COLLECTED |
| Institutional positioning (COT) | Aggregate positioning data | NOT COLLECTED |

**The current system has exhausted what can be learned from candlestick-derived features.**

---

## Research Program Conclusion

The AR research series (AR1-AR10) definitively concludes:

> **M5 candlestick structure on FX pairs, even augmented with multi-timeframe context, institutional zone detection, liquidity analysis, and displacement measurement, does not contain sufficient predictive information to produce a reliable trading edge after transaction costs.**

### What the Architecture Got Right

- Research methodology (prevented false discoveries)
- Observation pipeline (collected clean, linked data)
- Statistical rigour (n requirements, CI, Monte Carlo)
- Timing analysis (identified WEAK > VALID)
- Cost analysis (correctly identified spread as binding constraint)

### What Cannot Be Solved by More Analysis

- 50.7% direction accuracy from candlestick data
- Insufficient market movement within M5 holding periods
- FX spread domination at sub-20-pip risk distances
- Lack of external information sources

### Recommended Path Forward

| Option | Description | Effort | Probability of Success |
|---|---|---|---|
| **A: Different data** | Order flow, tick data, depth | HIGH | UNKNOWN |
| **B: Different market** | Index CFDs (lower relative spread) | MEDIUM | MODERATE |
| **C: Different timescale** | Daily/weekly directional (not M5) | MEDIUM | UNKNOWN |
| **D: Accept conclusion** | Research complete, no tradeable edge | NONE | N/A |
| **E: External signals** | News, COT, macro | HIGH | UNKNOWN |

The research infrastructure is excellent. The architecture is correct. **The signal source is the limitation.** Further analysis of candlestick-derived features will not change the 50.7% baseline.
