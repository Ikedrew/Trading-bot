# V2 Discovery Execution Results

**Date:** 2026-07-28
**Engine:** V2 Discovery Engine v1.0
**Epoch:** CURRENT
**Verdict:** NO_PREDICTIVE_VALUE (HIGH CONFIDENCE)

---

## 1. Dataset Summary

| Metric | Value |
|---|---|
| Total V2-equivalent records | 437 |
| Unique entities | 437 (deduplicated by entity_id) |
| Symbols | EURUSD(65), GBPUSD(64), NZDUSD(81), USDCAD(75), USDCHF(62), AUDUSD(49), USDJPY(41) |
| Win rate | 37.3% |
| Mean R | -0.1224 |
| Median R | -0.1154 |
| Date range | CURRENT epoch |
| Source | Retrospective reconstruction from shadow_trades_v2 records |
| Linkage method | Direct (outcome embedded from same shadow trade) |
| Linkage rate | 100% |

### Feature Distribution

| Dimension | Distribution |
|---|---|
| H4 regime | RANGE 92%, TRANSITIONAL 7%, TRENDING <1% |
| H1 bias | BULLISH 42%, BEARISH 31%, NEUTRAL 26% |
| Session | NY 41%, OFF 37%, LONDON 17%, ASIA 4% |
| Top patterns | TWEEZER_BOTTOM(108), TWEEZER_TOP(104), THREE_BLACK_CROWS(71), EVENING_STAR(36), THREE_WHITE_SOLDIERS(33) |

---

## 2. CQ1 — Individual Feature Predictive Value

**Question:** "Which individual features contain predictive information?"

**Result: NONE.**

| Rank | Feature | Best Category | Cost-Adj EV | Predictive |
|---|---|---|---|---|
| 1 | risk_distance_pips | Q2 | -0.4923R | No |
| 2 | session | LONDON | -0.5246R | No |
| 3 | m15_structure_state | CONSOLIDATION | -0.5260R | No |
| 4 | spread_atr_ratio | Q1 | -0.5443R | No |
| 5 | atr | Q1 | -0.5443R | No |
| 6 | volatility | Q1 | -0.5443R | No |
| 7 | candle_range | Q1 | -0.5443R | No |
| 8 | body_ratio | Q1 | -0.5443R | No |
| 9 | wick_ratio | Q1 | -0.5443R | No |
| 10 | m15_displacement | Q1 | -0.5443R | No |

**Key findings:**
- 21 features analysed across all timeframes
- Zero features achieve statistical significance
- ALL cost-adjusted EVs are deeply negative (-0.49R to -0.54R)
- Even the "best" feature (risk_distance_pips Q2) is -0.49R after costs
- No individual piece of market information predicts positive outcomes

**Conclusion:** No individual feature in the V2Opportunity schema shows statistically significant positive cost-adjusted EV.

---

## 3. CQ2 — Context Combinations

**Question:** "Do combinations of features create an edge when individual features do not?"

**Result: NONE.**

| Hypothesis | Description | Validated OOS |
|---|---|---|
| COMBO_1 | H4 trending + H1 BOS + M15 support + M5 pattern | Insufficient data (H4 TRENDING <1%) |
| COMBO_2 | H4 trending + H1 bias + London + low spread | Insufficient data |
| COMBO_3 | H1 BOS + order block + pattern | No validation |
| COMBO_4 | H4 ranging + support + BUY | No positive OOS EV |
| COMBO_5 | H4 ranging + resistance + SELL | No positive OOS EV |
| COMBO_6 | High quality + support + London | No positive OOS EV |
| COMBO_7 | H1 CHOCH + H4 direction + pattern | Insufficient data |
| COMBO_8 | Low vol + tight spread + structure | No positive OOS EV |

**Key findings:**
- 8 pre-registered hypotheses tested (theory-driven, not brute-force)
- Zero combinations validated out-of-sample
- Many combinations lack sufficient data (H4 TRENDING occurs in <1% of records — the market is predominantly ranging)
- Even combinations that have sufficient data produce negative cost-adjusted EV in both in-sample and out-of-sample splits

**Conclusion:** No combination of available features creates predictive value. Multi-timeframe alignment does not rescue the signal.

---

## 4. CQ3 — Environment Classification

**Question:** "When does the market environment allow a signal to work?"

**Result: NEVER (within available data).**

| Environments analysed | 8 dimensions |
|---|---|
| Favourable (positive EV + significant) | **0** |
| Unfavourable (negative EV + significant) | **0** |
| Statistically significant effects | **0** |

**Key findings:**
- 8 environment dimensions tested: volatility, spread, session, H4 regime, structure proximity, risk geometry, H1 alignment, tradability score
- No environment state produces statistically significant deviation from baseline
- LONDON session shows slightly less negative EV (-0.52R vs -0.60R baseline) but is NOT significant
- The market environment does not create permission-to-trade conditions for this signal set

**Conclusion:** No identifiable market environment makes the current signal architecture profitable.

---

## 5. CQ4 — Probability Model

**Question:** "Can available information estimate probability of success?"

**Result: NO.**

| Metric | Value |
|---|---|
| Model accuracy | 63.6% |
| Baseline accuracy (always predict majority) | 62.3% |
| Improvement over baseline | +1.3% (below 2% threshold) |
| Brier score | 0.2537 |
| Mean calibration error | 0.1745 |
| Model useful | **No** |

### Feature Importance (Leave-One-Out)

| Feature | Importance |
|---|---|
| h4_regime | 0.0000 |
| h1_bias | 0.0000 |
| h1_bos_confirmed | 0.0000 |
| session | 0.0000 |
| near_support | 0.0000 |

**Key findings:**
- Model barely exceeds majority-class baseline (63.6% vs 62.3%)
- Does NOT meet the 2% improvement threshold required to be considered useful
- ALL features have zero importance — removing any single feature does not degrade predictions
- The model is effectively random — it always predicts the majority class (loss)
- Calibration error is high (0.17) — predicted probabilities do not match actuals

**Conclusion:** Available features cannot estimate outcome probability. The information set is not predictive.

---

## 6. Final Architectural Conclusion

### Verdict

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   NO_PREDICTIVE_VALUE                                           ║
║                                                                  ║
║   Confidence: HIGH                                              ║
║   Sample: n=437 (sufficient for detection of meaningful edge)   ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

### Evidence Summary

| Question | Result | Evidence |
|---|---|---|
| CQ1: Individual features | FAIL | 0/21 features significant, all EVs < -0.49R |
| CQ2: Combinations | FAIL | 0/8 hypotheses validate OOS |
| CQ3: Environments | FAIL | 0 favourable environments detected |
| CQ4: Probability | FAIL | Model accuracy 63.6% vs 62.3% baseline (not useful) |

### What This Means

The V2 Discovery Engine has scientifically established that:

1. **No individual market context variable** (H4 regime, H1 bias, session, volatility, spread, pattern type, etc.) contains predictive information about whether the next trade will be profitable.

2. **No combination of context variables** creates an edge that survives out-of-sample validation and transaction costs.

3. **No market environment** (time of day, volatility state, spread conditions, structure proximity) creates conditions where the signal becomes profitable.

4. **The available feature set cannot estimate probability** of success better than always guessing the majority outcome.

### Root Cause

The primary driver is the **negative baseline EV (-0.12R raw, -0.60R after costs)**. The spread cost of 0.48R dominates the system economics. Even if a feature could identify slightly better subsets, the transaction cost floor ensures negative expected value.

The H4 regime distribution (92% RANGE) means the system has virtually no data on trending conditions — a fundamental limitation of the observation period.

---

## 7. Separation: Discovery vs Validation

| Category | Findings |
|---|---|
| **Discovery** (what looks promising) | LONDON session least negative (-0.52R); risk_distance Q2 least negative (-0.49R) |
| **Validation** (what survives testing) | NOTHING. Zero findings survive significance testing, OOS validation, or cost adjustment |

No finding can be considered an edge because:
- No finding is statistically significant (p < 0.05)
- No finding survives transaction costs (+0.48R burden)
- No finding validates out-of-sample
- The sample (n=437) is sufficient to detect a meaningful effect size

---

## 8. Next Steps

Based on the HIGH confidence null result, the recommended paths are:

| Option | Description | Rationale |
|---|---|---|
| A | Alternative information sources | Order flow, institutional positioning, macro data, news sentiment |
| B | Different timeframe entry | H1 or H4 entries (larger moves, lower spread/risk ratio) |
| C | Different market | Indices, crypto, commodities where spread burden is smaller |
| D | Accept null result | The current information set does not contain an edge; halt research |

**What should NOT be done:**
- Do not continue optimising M5 patterns
- Do not add more M5 pattern variants
- Do not adjust scoring thresholds
- Do not search for more feature combinations in this dataset
- Do not reduce sample size requirements to find "significance"

---

## 9. Test Results

| Suite | Result |
|---|---|
| V2 discovery tests | 40 passed |
| Full regression | 3343 passed, 1 pre-existing failure |
| Production behaviour changes | 0 |

---

## 10. Artefacts

| File | Purpose |
|---|---|
| `analysis/artifacts/v2_discovery_dataset.json` | 437-record research dataset |
| `analysis/reports/v2_discovery_DISCOVERY_20260728_130808.json` | Machine-readable full report |
| `analysis/run_v2_discovery.py` | Reproducible execution script |
| This document | Human-readable findings |
