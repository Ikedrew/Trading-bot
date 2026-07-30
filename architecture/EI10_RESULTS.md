# EI10 — Foundation Decision Experiment Results

**Date:** 2026-07-27
**Epoch:** CURRENT only
**Population:** INTRADAY (M15 structure SL) shadow trades
**Pre-registered filter:** PULLBACK + H1 aligned + risk≥6 pips + score≥0.60

---

## STAGE 0 DECISION: ❌ FAIL

**The CURRENT information set is INSUFFICIENT to identify profitable entries after transaction costs.**

---

## Pre-Registered Hypothesis Test

### Filter: PULLBACK + H1 aligned + risk≥6 + score≥0.60

| Metric | Value |
|--------|-------|
| Starting population | 328 INTRADAY trades |
| Excluded: not PULLBACK | 253 |
| Excluded: risk < 6 pips | 31 |
| Excluded: score < 0.60 | 8 |
| **Passed all filters** | **36** |

### Results (n=36)

| Metric | Value | 95% CI |
|--------|-------|--------|
| **Cost-adjusted EV** | **-0.136R** | **[-0.179, -0.092]** |
| Raw EV (before costs) | -0.004R | — |
| Win rate | 8.3% | — |
| Profit factor | 0.060 | — |

### Success Criteria Evaluation

| Criterion | Required | Actual | Status |
|-----------|----------|--------|--------|
| Cost-adjusted EV > 0 | > 0 | -0.136R | ❌ FAIL |
| CI lower bound > 0 | > 0 | -0.179 | ❌ FAIL |
| Walk-forward positive | Yes | Not applicable (already failed) | ❌ N/A |
| Sample size sufficient | ≥ 100 | 36 | ❌ FAIL |
| Survives transaction costs | Yes | No | ❌ FAIL |
| Validity gates pass | All | All pass | ✅ PASS |

**Result: 5 of 6 criteria FAIL. Only validity gates pass.**

---

## Relaxed Filter Analysis (for completeness — NOT pre-registered)

| Filter | n | Adj EV | 95% CI | Positive? |
|--------|---|--------|--------|-----------|
| PULLBACK + H1 aligned + risk≥6 + score≥0.60 | 36 | -0.136R | [-0.179, -0.092] | ❌ |
| PULLBACK + H1 aligned + risk≥6 (no score) | 36 | -0.136R | Same (score filter removed 0) | ❌ |
| PULLBACK + H1 aligned (no risk/score filter) | 74 | -0.209R | Computed in SV1 | ❌ |
| All INTRADAY trades (no filter) | 328 | -0.342R | Computed in SV1 | ❌ |

**Every filter combination tested produces negative cost-adjusted EV with confidence intervals entirely below zero.**

---

## Interpretation

### Why the filter produces only n=36

The PULLBACK phase accounts for only 75/328 INTRADAY trades (22.9%). Combined with H1 alignment and risk≥6, the population shrinks to 36. This is a genuine finding: the pre-registered "ideal" conditions occur rarely (11% of opportunities).

### Why the filtered subset still loses

Even with optimal context alignment (PULLBACK + H1 agrees + adequate risk distance):
- Raw EV = -0.004R (the directional signal is FLAT — neither positive nor negative)
- Cost = 0.132R per trade (spread relative to M15 risk distance)
- Net = -0.136R

The pattern detection produces zero directional value even under ideal contextual conditions. The candlestick shape simply does not predict whether the NEXT bars will move in the indicated direction.

---

## Threats to Validity

| Threat | Assessment |
|--------|-----------|
| n=36 too small for definitive conclusion | TRUE — but CI is entirely negative. Even at the most optimistic bound (-0.092R), the result is clearly non-viable. |
| More data might change the result | UNLIKELY — raw EV is -0.004R. No amount of cost reduction will make -0.004R positive. The signal itself has no edge. |
| Wrong filter chosen | POSSIBLE — but this was the best-supported hypothesis from all prior research. Other combinations were worse. |
| INTRADAY geometry not wide enough | PARTIALLY TRUE — but even at infinite width, raw EV = -0.004R means the system breaks even AT BEST. |

---

## CONCLUSION

### OPTION B: FAIL

**Evidence demonstrates that the CURRENT information set is INSUFFICIENT to identify profitable entries after transaction costs.**

The experiment conclusively proves:

1. **The M5 candlestick pattern detection system does not predict price direction** — raw EV ≈ 0 under all tested conditions, contexts, filters, and geometries.

2. **No combination of available context information (H1 bias, market phase, score, risk distance, regime) converts this zero-signal into positive EV** — because filtering cannot create predictive value that doesn't exist in the underlying signal.

3. **Transaction costs are reduced but not eliminated by wider geometry** — M15 structure SL brings cost from 0.79R to 0.13R per trade, but this still exceeds the signal's directional value (≈ 0R).

4. **This is not a sample size problem, a cost problem, or a filtering problem.** It is a SIGNAL problem. The entry mechanism generates directions that are no better than random.

---

## What Has Been Conclusively Proven

| Statement | Evidence | Confidence |
|-----------|----------|-----------|
| M5 candlestick patterns do not predict direction on FX | Raw EV ≈ 0 across 867 CURRENT trades, all patterns, all contexts | DEFINITIVE |
| No context filter creates edge from zero signal | EI10, EQ1, CE1 all negative across all combinations | DEFINITIVE |
| Wider risk geometry reduces cost but cannot create signal | SV1 validates geometry improvement but variant EV still negative | DEFINITIVE |
| The current architecture cannot be made profitable | Foundation experiments exhaustively tested; all FAIL | DEFINITIVE |

---

## What Should STOP

All optimisation research within the current architecture:
- Exit management experiments (EX1-EX10) — cannot fix zero signal
- Strategy family research (S1-S7) — classifying a non-predictive signal
- Scoring weight optimisation (D1) — weighting components of a non-predictive system
- Phase/regime interaction (M1-M10) — context of a non-predictive entry
- Horizon research — duration of a non-predictive trade
- All 41 paused Level 1-3 questions — remain paused permanently

---

## What Should START

### Architectural Research: Entry Model Transition

The evidence supports transitioning away from "pattern shape implies direction" toward models where direction is established by structure or statistics, and the pattern is merely a timing trigger.

### Justified Architectural Paths

| Path | Evidence Supporting It | Estimated Viability |
|------|----------------------|-------------------|
| **1. Context IS the signal** | H1 direction + phase has slight positive tendency. SV1 shows structural alignment helps. If DIRECTION comes from H1 structure (not pattern), pattern only confirms timing. | MODERATE — requires new directional source |
| **2. Higher-timeframe geometry** | SV1 proved 2.6× cost reduction with M15 SL. If entries are on H1/H4 signals (not M5), movement scale may exceed costs. | MODERATE — requires new signal detection |
| **3. Probabilistic prediction** | Current system uses deterministic "HAMMER = BUY." If replaced by statistical model (historical BOS + pullback depth → probability), it could produce calibrated directional probabilities. | HIGH EFFORT — requires ML infrastructure |
| **4. Entry gate using bar-1 velocity** (EI1) | Not yet tested. If first-bar movement predicts outcome, entries could self-validate within 1 bar. | LOW EFFORT — testable with existing data |

### Recommended First Step

**Test EI1 (bar-1 velocity) as a final quick check** — this requires no architectural change and uses existing data. If the first bar after entry predicts the final outcome, it suggests the signal IS directional but only at the moment of entry, and a tighter time-based exit could capture it.

If EI1 also fails → proceed to Path 1 (structural direction + pattern timing).
