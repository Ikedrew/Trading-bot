# Research Engine — Question Bank

**Generated:** 2026-07-19  
**Updated:** 2026-07-19 (Priority Classification + Question Registry)  
**Basis:** 19 discovered data assets from Phase 0 Repository Audit  
**Purpose:** Define the highest-value questions the Research Engine should continuously investigate  
**Registry:** `research_engine/question_registry.py`

---

## Priority Classification System

| Priority | Criteria | Action Threshold |
|----------|----------|-----------------|
| **P0** | Directly improves next-trade probability or sizing | Implement immediately; run daily |
| **P1** | Identifies systematic edge decay or regime misclassification | Implement next; run weekly |
| **P2** | Improves execution quality or reduces friction | Implement when P0/P1 are stable |
| **P3** | Deepens understanding without immediate tactical value | Implement as capacity allows |

### Status Definitions

| Status | Meaning |
|--------|---------|
| `ready` | Experiment implemented and runnable with available data |
| `blocked` | Requires data that doesn't exist yet (e.g., live trades with identity) |
| `not_implemented` | Question defined but code not written |

### Current Registry State

| Status | Count | Next Action |
|--------|-------|-------------|
| Ready | 3 (Q1, Q19, Q20) | Run experiments |
| Blocked | 1 (Q16) | Awaiting first live trade with correlation_id |
| Not Implemented | 16 | Implement in priority order |

### Execution Order

| Phase | Questions | Prerequisite |
|-------|-----------|--------------|
| **Now** | Q19 (Expected Value) | Shadow trades available (469+ records) |
| **After first live trade** | Q16 (Shadow Validation) | Live Trade Truth with correlation_id |
| **After Q16 validated** | Q4, Q1, Q5, Q2, Q3 | Q16 confirms shadow-based research is trustworthy |
| **Optimisation** | Q7, Q9, Q13 | Stable P0 metrics |
| **Deep research** | Q6, Q8, Q10-Q18 | As capacity allows |

---

## P0 — Decision Quality & Edge

### Q1: Which scoring components predict actual R-multiples?

**Question:** For trades that reach execution (shadow or live), which individual scoring components (from `decision_trace.components`) correlate most strongly with realised R-multiple outcomes?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (components, weights_used, score_strategy) + `shadow_trades` (pnl_r_multiple) |
| Join key | `correlation_id` or `entity_id` + `cycle_id` |
| Output | Ranked component importance with correlation coefficients |
| Frequency | Daily recalculation over rolling 30-day window |
| Research Value | **5/5** — Directly informs weight adjustment |
| Actionable? | Yes — component weights in Engine A are configurable |

---

### Q2: What is the optimal score threshold by regime?

**Question:** Does the minimum score threshold (currently static at 0.35) perform differently across market regimes? Should it be regime-adaptive?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (score_strategy, threshold_gap) + `shadow_trades` (pnl_r_multiple) + `decision_ledger` (regime) |
| Analysis | Group shadow trade R-multiples by regime × score bucket; compute optimal threshold per regime |
| Output | Regime-specific threshold recommendations with expected improvement |
| Frequency | Weekly recalculation |
| Research Value | **5/5** — Single-parameter change with measurable impact |
| Actionable? | Yes — threshold is a config value |

---

### Q3: Which terminal stages have the highest missed-opportunity cost?

**Question:** When the pipeline terminates at a specific stage (e.g., `scoring_engine`, `confirmations`), how often would the trade have been profitable?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (terminal_stage, terminal_reason, closest_flip_component, flip_feasible) + hypothetical shadow forward-projection |
| Analysis | For NO_TRADE decisions where `flip_feasible=True`, estimate what R-multiple the trade would have achieved |
| Output | Stage × reason × "regret cost" table |
| Frequency | Weekly |
| Research Value | **5/5** — Identifies over-filtering |
| Actionable? | Yes — identifies which gates to relax |

---

### Q4: Is the engine's confidence calibrated?

**Question:** When the engine produces high-score signals, do they actually win more often? When uncertainty is high, do outcomes spread wider?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_ledger` (signal_score, uncertainty, reasoning) + `shadow_trades` (pnl_r_multiple) or `trade_truth` (r_multiple_realised) |
| Analysis | Calibration curve: predicted confidence vs actual win rate in decile buckets |
| Output | Calibration plot + Brier score + ECE (Expected Calibration Error) |
| Frequency | Daily |
| Research Value | **5/5** — Measures engine reliability |
| Actionable? | Yes — identifies overconfidence or underconfidence regimes |

---

### Q5: What patterns degrade over time?

**Question:** Which detected patterns (`decision_trace.pattern_name`) show declining R-multiples or declining win rates over rolling windows?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (pattern_name, pattern_quality) + `shadow_trades` (pnl_r_multiple, exit_reason) |
| Analysis | Per-pattern rolling 20-trade win rate + average R; detect monotonic decline |
| Output | Pattern health scorecard with degradation alerts |
| Frequency | After every 10 shadow trade completions per pattern |
| Research Value | **5/5** — Identifies strategy decay before capital loss |
| Actionable? | Yes — can disable or downweight degraded patterns |

---

## P1 — Regime & Market Structure

### Q6: Does the regime classifier agree with realised outcomes?

**Question:** When the system classifies a regime as "TRENDING" vs "RANGING", do subsequent shadow trades confirm that classification?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (regime, regime_confidence) + `shadow_trades` (pnl_r_multiple, bars_held, exit_reason) |
| Analysis | Regime classification accuracy: did "TRENDING" regimes produce trending-strategy R-multiples? |
| Output | Regime confusion matrix + accuracy per classification |
| Frequency | Weekly |
| Research Value | **4/5** — Validates market model |
| Actionable? | Yes — can adjust regime confidence thresholds |

---

### Q7: Which sessions produce the best edge?

**Question:** Does the system's edge vary significantly by trading session (LONDON/NY/ASIA/OFF_SESSION)?

| Aspect | Detail |
|--------|--------|
| Data sources | `execution_context` (session_state) + `shadow_trades` (pnl_r_multiple) or `trade_truth` |
| Analysis | Per-session: win rate, avg R, Sharpe-equivalent, trade count |
| Output | Session performance matrix with statistical significance |
| Frequency | Weekly |
| Research Value | **4/5** — Identifies time-of-day edge |
| Actionable? | Yes — session guard can restrict/expand hours |

---

### Q8: How does HTF alignment affect outcomes?

**Question:** Do trades with strong H4/H1/M15 alignment produce better R-multiples than those without?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (htf_alignment, h4_alignment) + `shadow_trades` (pnl_r_multiple) |
| Analysis | Correlation between HTF alignment score and realised R; conditional win rate |
| Output | Alignment threshold recommendations |
| Frequency | Weekly |
| Research Value | **4/5** — Validates multi-timeframe hypothesis |
| Actionable? | Yes — HTF constraints are configurable |

---

### Q9: What spread/volatility conditions produce the best fills?

**Question:** How does spread_atr_ratio at decision time correlate with execution slippage and trade outcome?

| Aspect | Detail |
|--------|--------|
| Data sources | `execution_context` (spread_atr_ratio, spread) + `execution_results` (slippage, fill_price) + `trade_truth` (slippage_entry) |
| Analysis | Slippage distribution by spread_atr_ratio bucket; optimal entry conditions |
| Output | Execution quality model |
| Frequency | Daily |
| Research Value | **4/5** — Directly improves net P&L |
| Actionable? | Yes — spread guard thresholds are configurable |

---

### Q10: Are guard rejections improving or degrading system performance?

**Question:** When runtime guards block a trade, what would the R-multiple have been (via shadow projection)?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_ledger` (decision=RISK_BLOCK, risk_flag, signal_score) + forward shadow projection (requires extension) |
| Analysis | For each guard type: "saved loss" vs "missed gain" ratio |
| Output | Guard efficacy scorecard |
| Frequency | Monthly |
| Research Value | **4/5** — Validates risk architecture |
| Actionable? | Yes — guard thresholds are configurable |

---

## P2 — Execution Quality

### Q11: What is the true slippage model per symbol per session?

**Question:** Build an empirical slippage model: expected slippage = f(symbol, session, volume, spread)?

| Aspect | Detail |
|--------|--------|
| Data sources | `execution_results` (fill_price, entry_reference, slippage, symbol) + `execution_context` (session_state, spread) |
| Analysis | Regression: slippage ~ symbol + session + volume + spread_atr_ratio |
| Output | Per-symbol slippage model with confidence intervals |
| Frequency | Weekly recalibration |
| Research Value | **4/5** — Improves position sizing and expected value |
| Actionable? | Yes — can adjust RR targets by expected slippage |

---

### Q12: Is the broker rejecting orders in predictable patterns?

**Question:** Do retcode failures cluster by time, symbol, volume, or market conditions?

| Aspect | Detail |
|--------|--------|
| Data sources | `execution_results` (result_ok=False, retcode, comment, timestamp) |
| Analysis | Rejection rate by hour, symbol, volume bucket; pattern detection |
| Output | Broker reliability heatmap |
| Frequency | Daily |
| Research Value | **3/5** — Operational improvement |
| Actionable? | Yes — can avoid execution during unreliable periods |

---

### Q13: What is the optimal trade duration?

**Question:** What is the relationship between bars_held and final R-multiple? Is there an optimal exit timing?

| Aspect | Detail |
|--------|--------|
| Data sources | `shadow_trades` (bars_held, pnl_r_multiple, mfe_r, mae_r, trade_state_progression[]) |
| Analysis | R-multiple evolution over time; MFE timing distribution; optimal exit bar |
| Output | Time-based exit recommendations |
| Frequency | After every 50 shadow trade completions |
| Research Value | **4/5** — Directly improves trade management |
| Actionable? | Yes — max_bars_timeout and trailing parameters configurable |

---

## P3 — Deep Understanding

### Q14: What causal chains produce the best trades?

**Question:** Using the trade truth graph, which sequences of events (regime transition → pattern detection → session alignment) precede profitable trades?

| Aspect | Detail |
|--------|--------|
| Data sources | `trade_truth_graph` (edges, temporal relationships) + `trade_truth` (r_multiple) |
| Analysis | Frequent subgraph mining on profitable vs unprofitable trade graphs |
| Output | Causal pattern templates |
| Frequency | Monthly |
| Research Value | **3/5** — Deepens market model |
| Actionable? | Long-term — could inform strategy design |

---

### Q15: How does the engine learn over time?

**Question:** Are learning records showing improving or degrading calibration over weeks/months?

| Aspect | Detail |
|--------|--------|
| Data sources | `learning` records (calibration_result, evidence_quality, uncertainty_score) over time |
| Analysis | Rolling calibration quality trend; regime-conditional improvement rate |
| Output | Learning velocity report |
| Frequency | Weekly |
| Research Value | **3/5** — Meta-learning signal |
| Actionable? | Indirect — validates or challenges the learning approach |

---

### Q16: What is the correlation between shadow and live outcomes?

**Question:** How well do shadow trade R-multiples predict actual trade_truth R-multiples for the same signal?

| Aspect | Detail |
|--------|--------|
| Data sources | `shadow_trades` (pnl_r_multiple) + `trade_truth` (r_multiple_realised), joined on correlation_id |
| Analysis | Correlation, bias, systematic over/under-estimation |
| Output | Shadow accuracy model (needed to trust Q1-Q5 shadow-based analysis) |
| Frequency | After every 20 live trade completions |
| Research Value | **5/5** — Validates ALL shadow-based research |
| Actionable? | Yes — calibrates shadow-to-live translation |

---

### Q17: What market conditions precede system drawdowns?

**Question:** Before periods of consecutive losses, what environmental signals were visible in execution_context?

| Aspect | Detail |
|--------|--------|
| Data sources | `execution_context` (all fields) + `trade_truth` (sequential losses) |
| Analysis | Feature importance for predicting loss clusters; early warning model |
| Output | Drawdown risk signal |
| Frequency | Daily |
| Research Value | **4/5** — Protective intelligence |
| Actionable? | Yes — could dynamically tighten guards during risk periods |

---

### Q18: Are there symbols that should be removed or added?

**Question:** Which symbols in the universe consistently underperform or have degraded data quality?

| Aspect | Detail |
|--------|--------|
| Data sources | `shadow_trades` (per symbol R-multiples), `event_stream` (FEED_HEALTH per symbol), `decision_ledger` (per symbol patterns) |
| Analysis | Per-symbol Sharpe, win rate, feed reliability, pattern frequency |
| Output | Symbol universe recommendations |
| Frequency | Monthly |
| Research Value | **3/5** — Portfolio composition |
| Actionable? | Yes — symbol list is config |

---

### Q19: What is the system's true edge expressed as expected value?

**Question:** Across all signals, what is E[R] = (win_rate × avg_win_R) - (loss_rate × avg_loss_R), and how is it trending?

| Aspect | Detail |
|--------|--------|
| Data sources | `shadow_trades` (full history) or `trade_truth` (full history) |
| Analysis | Rolling expected value with confidence intervals |
| Output | Edge magnitude + trend + statistical significance |
| Frequency | Daily |
| Research Value | **5/5** — The fundamental question |
| Actionable? | Yes — if edge ≤ 0, system should reduce exposure |

---

### Q20: Is score calibrated to observed outcomes?

**Question:** Does the model's confidence score (raw_score → calibrated_probability → p_success) accurately predict the observed frequency of trade success?

| Aspect | Detail |
|--------|--------|
| Data sources | `decision_trace` (raw_score, p_success, probability_raw_score) + `shadow_trades` (pnl_r_multiple) + `research_shadow_trades` (pnl_r_multiple) |
| Analysis | Calibration curve: predicted probability by score bucket vs actual win rate; Expected Calibration Error (ECE); reliability diagram |
| Output | Calibration report with per-bucket error, overall ECE, recommendation |
| Frequency | Weekly (after 50+ new shadow outcomes) |
| Research Value | **5/5** — Validates the probability layer used by EV |
| Actionable? | Yes — if miscalibrated, ScoreCalibrator should apply empirical mapping |
| Promotion Rule | Result recommends PROMOTE_CALIBRATION / KEEP_CURRENT_MODEL / INSUFFICIENT_DATA. A separate promotion process applies changes. |

---

## Question Prioritisation Matrix

| Question | Priority | Data Ready? | Requires New Code? | Expected Impact |
|----------|----------|-------------|-------------------|----------------|
| Q1: Component → R correlation | P0 | ✅ | Minimal (read JSONL + join) | High |
| Q2: Regime-adaptive threshold | P0 | ✅ | Minimal | High |
| Q3: Missed opportunity cost | P0 | ✅ | Moderate (forward projection) | High |
| Q4: Confidence calibration | P0 | ✅ | Minimal | High |
| Q5: Pattern degradation | P0 | ✅ | Minimal (rolling window) | High |
| Q16: Shadow→Live accuracy | P0* | ✅ | Minimal | Critical (validates all shadow research) |
| Q19: True edge / EV | P0* | ✅ | Minimal | Critical (go/no-go signal) |
| Q20: Score calibration | P0 | ✅ | Minimal | Critical (validates probability layer) |
| Q6: Regime accuracy | P1 | ✅ | Minimal | Medium |
| Q7: Session edge | P1 | ✅ | Minimal | Medium |
| Q8: HTF alignment value | P1 | ✅ | Minimal | Medium |
| Q9: Spread/fill quality | P1 | ✅ | Minimal | Medium |
| Q10: Guard efficacy | P1 | Partial | Moderate (shadow projection for blocked) | Medium |
| Q13: Optimal duration | P2 | ✅ | Minimal | Medium |
| Q11: Slippage model | P2 | ✅ | Minimal | Low-Medium |
| Q12: Broker reliability | P2 | ✅ | Minimal | Low |
| Q17: Drawdown precursors | P3 | ✅ | Moderate (clustering) | Medium (protective) |
| Q14: Causal chains | P3 | ✅ | Complex (graph mining) | Low-Medium |
| Q15: Learning velocity | P3 | ✅ | Minimal | Low |
| Q18: Symbol universe | P3 | ✅ | Minimal | Low |

---

## Recommended Investigation Order

### Phase 1 (Immediate — validates foundation)
1. **Q16** — Shadow→Live accuracy (must be validated before trusting shadow-based research)
2. **Q19** — True edge / Expected Value (the fundamental "is this working?" metric)
3. **Q4** — Confidence calibration (is the engine reliable?)

### Phase 2 (Optimisation — improves performance)
4. **Q1** — Component→R correlation (which weights matter?)
5. **Q5** — Pattern degradation (what's dying?)
6. **Q2** — Regime-adaptive threshold (biggest single-parameter improvement?)
7. **Q3** — Missed opportunity cost (are we over-filtering?)

### Phase 3 (Execution quality)
8. **Q7** — Session edge
9. **Q9** — Spread/volatility conditions
10. **Q13** — Optimal trade duration

### Phase 4 (Deep understanding)
11. **Q6, Q8, Q10, Q11, Q14, Q15, Q17, Q18** — as capacity allows

---

## Implementation Requirements

A Research Engine answering these questions needs:

1. **JSONL reader** — parse `logs/` directories (primary data source)
2. **Correlation joiner** — link records across layers via `correlation_id`
3. **Rolling window calculator** — maintain sliding statistics
4. **Statistical framework** — correlation, significance testing, confidence intervals
5. **Scheduler** — daily/weekly recalculation triggers
6. **Output format** — research findings as structured reports (JSONL or dashboard)

**No new runtime instrumentation required.** All 19 questions are answerable from existing data assets.
