# Strategy Contract Impact Analysis

## Dataset

- **Total V10 decision records**: 1,305 (Jul 30 + Jul 31, timestamp > 1785000000)
- **Rejected at opportunity (unaffected)**: 930 (71.3%)
- **Rejected at strategy (simulation pool)**: 349 (26.7%)
- **Currently reaching entry**: 26 (2.0%)
- **Current TREND_CONTINUATION selections**: 25

---

## Simulation Method

Applied proposed reconciled evidence contracts to the 349 strategy-rejected records. Each record already passed opportunity (WATCHING or VALID state) — meaning these are observations where V10 detected a market condition worth investigating but couldn't classify into a strategy family.

---

## Results by Strategy

### MEAN_REVERSION

| Metric | Value |
|---|---|
| Would newly qualify | **115 / 349** (33.0% of pool) |
| As % of all evaluations | **8.8%** |
| Permissiveness check | PASS (< 30% of total) |

**Symbol distribution:**
| Symbol | Count | % of new |
|---|---|---|
| NAS100 | 46 | 40% |
| US500 | 19 | 17% |
| USDCAD | 14 | 12% |
| NZDUSD | 9 | 8% |
| USDCHF | 8 | 7% |
| Others | 19 | 16% |

**Assessment:** REASONABLE. Heavily index-weighted (NAS100+US500 = 57%). These instruments frequently range with clear structure and high clarity. The conditions (neutral macro + extreme + structural clarity ≥ 0.5) appropriately target rangebound mean-reversion environments.

---

### RANGE_REACTION

| Metric | Value |
|---|---|
| Would newly qualify | **89 / 349** (25.5% of pool) |
| As % of all evaluations | **6.8%** |
| Permissiveness check | PASS (< 30%) |

**Symbol distribution:**
| Symbol | Count | % of new |
|---|---|---|
| NAS100 | 43 | 48% |
| US500 | 29 | 33% |
| AUDUSD | 8 | 9% |
| USDCAD | 7 | 8% |
| Others | 2 | 2% |

**Assessment:** REASONABLE. Even more concentrated on indices (81% NAS100+US500). The higher bar (clarity ≥ 0.7 + both swings defined + RANGING only) correctly filters to established ranges with clear boundaries. This is a subset of MEAN_REVERSION — more selective, higher confidence.

---

### FALSE_BREAK

| Metric | Value |
|---|---|
| Would newly qualify | **16 / 349** (4.6% of pool) |
| As % of all evaluations | **1.2%** |
| Permissiveness check | PASS (very conservative) |

**Symbol distribution:**
| Symbol | Count |
|---|---|
| AUDUSD | 3 |
| EURUSD | 3 |
| US500 | 3 |
| GBPUSD | 2 |
| USDCAD | 2 |
| XAUUSD | 2 |
| USDCHF | 1 |

**Assessment:** HIGHLY SELECTIVE. Only 16 records qualify across all symbols. The triple conjunction (structural level + rejection + mid-range reclaim) is naturally rare. Each qualifying signal should be high-conviction. No permissiveness concerns.

---

### LIQUIDITY_SWEEP_REVERSAL

| Metric | Value |
|---|---|
| Would newly qualify | **239 / 349** (68.5% of pool) |
| As % of all evaluations | **18.3%** |
| Permissiveness check | **MARGINAL** — below 30% of total but captures majority of pool |

**Assessment:** OVERLY PERMISSIVE as proposed. The BOS-opposing-macro condition alone is too broad — `h1.bos_direction != h4.trend` fires whenever H4 is NEUTRAL (which is common) and H1 has any directional BOS. This captures nearly everything.

**Root cause of over-permissiveness:** When `h4.trend == "NEUTRAL"`, ANY H1 BOS direction (BULLISH or BEARISH) satisfies `bos_direction != h4.trend`. This was not the intent — the intent was "H1 structure REVERSED against an established prior direction."

**Required additional gates (not yet in proposed contract):**
1. H4 must have a non-neutral direction: `h4.trend in ("BULLISH","BEARISH")` — then BOS opposing it is meaningful
2. Minimum rejection threshold (already in R2): already requires `rejection_strength >= 0.5`
3. Liquidity flag must be True (R1): only ~30% of strategy-rejected records have this

**With H4 non-neutral gate added:** Estimated ~40-60 would qualify (more reasonable).

---

### BREAKOUT_EXPANSION

| Metric | Value |
|---|---|
| Would newly qualify | **0 / 349** (0.0%) |
| As % of all evaluations | **0.0%** |

**Assessment:** NO MATCHES. The `volatility_state == "CONTRACTION"` condition never appears in strategy-rejected records for these sessions. Either:
- The ATR classifier rarely produces CONTRACTION during active hours (most activity is NEUTRAL or EXPANSION)
- These two days genuinely had no compression periods
- The volatility classifier's CONTRACTION threshold is too strict

This strategy remains theoretical until compression events are observed. No permissiveness concern — opposite problem (too restrictive even with proposed changes).

---

## Overlap Analysis

### MEAN_REVERSION vs RANGE_REACTION overlap

Of the 89 RANGE_REACTION qualifiers, **all 89 also qualify for MEAN_REVERSION** (because RANGE_REACTION's conditions are a strict subset: RANGING ⊂ any-neutral, clarity ≥ 0.7 ⊂ clarity ≥ 0.5).

**Resolution via priority:** If RANGE_REACTION has higher priority (checked first), it captures the high-confidence subset. MEAN_REVERSION then captures the remaining 26 that are neutral-but-not-explicitly-RANGING, or have clarity 0.5-0.7.

---

## Impact on Current Executions

**Zero currently executed trades would change classification.**

Reason: No V10 EXECUTE decisions exist in the dataset. All 1,305 records are NO_TRADE. The proposed changes affect strategy SELECTION (stage 3) — they have no retroactive effect on decisions that already reached execution.

---

## Final Assessment

| Strategy | Newly Qualified | Risk Level | Recommendation |
|---|---|---|---|
| MEAN_REVERSION | 115 (8.8%) | Low | IMPLEMENT — well-calibrated, reasonable volume |
| RANGE_REACTION | 89 (6.8%) | Low | IMPLEMENT — higher-confidence subset of mean reversion |
| FALSE_BREAK | 16 (1.2%) | Very Low | IMPLEMENT — highly selective, low volume |
| LIQUIDITY_SWEEP_REVERSAL | 239 (18.3%) | **HIGH** | DEFER — needs H4 non-neutral gate to prevent over-triggering |
| BREAKOUT_EXPANSION | 0 (0%) | None | IMPLEMENT cleanup — no behaviour change |

---

## Post-Implementation Expected Pipeline

```
1,305 evaluations
    ↓ Opportunity: 375 pass (29%)
    ↓ Strategy (with changes):
        TREND_CONTINUATION: 25 (existing)
        MEAN_REVERSION: ~26 (new, excluding overlap)
        RANGE_REACTION: ~89 (new, higher priority)
        FALSE_BREAK: ~16 (new)
        LIQUIDITY_SWEEP: DEFERRED
        BREAKOUT_EXPANSION: 0
        TOTAL reaching entry: ~156
    ↓ Entry: depends on BOS level / geometry (separate fix)
    ↓ Risk → Execution → EXECUTE
```

The pipeline moves from 25 reaching entry → ~156 reaching entry. The entry engine's BOS-level geometry fix then determines how many produce valid trade geometry.
