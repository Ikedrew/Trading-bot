# AR1 — Incremental Predictive Value Analysis Results

**Date:** 2026-07-28
**Status:** CANNOT EVALUATE — timing mismatch between linked outcomes and V3 shadow pipeline deployment

---

## Critical Finding

The 29 linked execution assessments ALL have:
- `opportunity_state = LOW_QUALITY_CONTEXT` (all 29)
- `horizon = NO_HORIZON` (all 29)
- `entry_state = INSUFFICIENT_ENTRY_DATA` (all 29)
- `risk_state = INSUFFICIENT_RISK_DATA` (all 29)
- `execution_state = NOT_EXECUTABLE` (27) or `EXECUTION_CONSTRAINED` (2)

**Zero records have READY_FOR_EXECUTION with linked outcomes.**

---

## Why This Happened

The V3 shadow pipeline (Phases 1-7) was deployed RECENTLY. The 29 linked outcomes come from the EARLIEST execution assessments — before enough market data accumulated for the pipeline to produce quality assessments.

The pipeline needs:
1. **MarketContext with HTF data** → requires `MTF_ENABLED=True` and active TimeframeCache
2. **Liquidity/FVG/OB detectors** to fire → requires 200+ candle history
3. **H1 BOS confirmation** → requires BiasSnapshot with `last_swing_high/low`

Without these upstream inputs, the pipeline correctly classifies everything as LOW_QUALITY → NO_HORIZON → INSUFFICIENT → NOT_EXECUTABLE.

---

## Baseline Statistics

| Metric | Value |
|---|---|
| Total execution assessments | 394 |
| With linked outcomes | 29 |
| WR | 37.9% |
| Mean R | +0.52R |
| 95% CI | [-0.20, +1.24] |
| MFE | 2.14R |
| MAE | 1.12R |

The +0.52R baseline comes from timestamp-matched shadow trades that happen to have positive outcomes in this period. It is NOT the V3 pipeline EV — it's the EV of whatever the shadow trade engine produced at those timestamps.

---

## Incremental Value Table

| Stage | n | WR | EV | Δ EV | Status |
|---|---|---|---|---|---|
| Baseline (all linked) | 29 | 37.9% | +0.52R | — | Only data available |
| + Opportunity (HIGH/INTERESTING) | 0 | — | — | — | **NO DATA** |
| + Horizon (any) | 0 | — | — | — | **NO DATA** |
| + Entry (VALID/WEAK) | 0 | — | — | — | **NO DATA** |
| + Risk (ACCEPTABLE/MARGINAL) | 0 | — | — | — | **NO DATA** |
| + Execution (READY) | 0 | — | — | — | **NO DATA** |

---

## Why Zero Positive Pipeline Events

The pipeline has 394 execution assessments but only 29 linked outcomes. Of those 29:
- ALL were classified as NOT_EXECUTABLE or CONSTRAINED
- ZERO had full positive pipeline flow (opportunity + horizon + entry + risk → READY)

This means the V3 pipeline has NEVER yet produced a READY_FOR_EXECUTION event that also had a linked outcome. The 3% READY rate (from earlier audit) has produced assessment records, but those records either:
1. Don't have linked outcomes yet (shadow trades not closed)
2. Were generated at timestamps that didn't match any shadow trade

---

## Research Verdict

**AR1 CANNOT BE ANSWERED WITH CURRENT DATA.**

The incremental value analysis requires records where each pipeline stage PASSES (opportunity = HIGH/INTERESTING, horizon selected, entry confirmed, risk acceptable) AND has a linked outcome.

**Current state:** 0 records meet this criterion.

---

## Required for AR1 to Succeed

1. **Run bot continuously** during active market sessions (LONDON/NY) with MTF_ENABLED=True
2. **Collect 50+ execution assessments** that are NOT all NOT_EXECUTABLE
3. **Re-run outcome linker** after shadow trades close
4. **Wait for READY events** to accumulate with outcomes (estimated: 3% of cycles × enough cycles = 50+ READY needs ~1,700 total cycles)

---

## Alternative: Use V3Opportunity Data Instead

The V3Opportunity pipeline (separate from shadow) has 158 linked post-Phase-2 records with actual V3 feature variation. The V3 Discovery Pass 2 already showed:
- Inside OB: +0.071R (n=23)
- Location gradient: discount > premium
- OB/FVG presence correlates with better outcomes

**Recommendation:** AR1 should be deferred until the shadow pipeline has sufficient READY events with outcomes. Meanwhile, continue using V3Opportunity linkage for feature-level research (which has more data).

---

## Conclusion

The V3 shadow pipeline architecture is correct but has not yet operated long enough (with full upstream inputs) to produce enough READY_FOR_EXECUTION events with linked outcomes for incremental value analysis.

**No engine changes justified.** The pipeline needs TIME and continuous operation, not redesign.
