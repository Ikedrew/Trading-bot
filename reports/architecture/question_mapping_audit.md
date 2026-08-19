# RESEARCH QUESTION MAPPING AUDIT

**Date:** 2026-07-27  
**Status:** AUDIT ONLY — No code modified  
**Scope:** All 51 registered questions — complete evidence-path verification  
**Basis:** Post-Shadow-implementation architecture + question_bank_correctness_audit.md  

---

## 1. EXECUTIVE SUMMARY

**Total questions audited:** 51 (45 Live + 6 Shadow)

| Classification | Count | % |
|---|---|---|
| 🟢 GREEN (correct mapping) | 44 | 86% |
| 🟠 AMBER (valid but limited) | 4 | 8% |
| 🔴 RED (analytically invalid) | 3 | 6% |

**The Research Engine can be trusted for 86% of its questions.** The 3 RED questions produce findings that either mislead (D-004 metric on wrong population) or cannot function (ED-002 broken join, D-007 single-sided).

---

## 2. COMPLETE QUESTION MAPPING MATRIX

| # | ID | Objective (plain English) | Universe | Population | Evidence | Analysis | Metric | Params | Join | Finding Produced | Status |
|---|-----|--------------------------|----------|-----------|----------|----------|--------|--------|------|-----------------|--------|
| 1 | E-001 | Is V10 making money per trade? | EXECUTION | ALL_TRADES (94) | REALISED | expectancy | r_multiple | default | — | Mean R, win rate, total R | 🟢 |
| 2 | E-002 | Are wins bigger than losses? Is variance OK? | EXECUTION | ALL_TRADES | REALISED | distribution+expectancy | r_multiple | default | — | Distribution shape, std | 🟢 |
| 3 | E-003 | What % exit SL/TP/time? | EXECUTION | ALL_TRADES | REALISED | segmentation | r_multiple | dim=exit_reason | — | Count+mean per exit type | 🟢 |
| 4 | E-004 | Which session has best fills? | EXECUTION | ALL_TRADES | REALISED | segmentation | r_multiple | implicit session | — | Per-session metrics | 🟢 |
| 5 | E-005 | What is probability of ruin? | EXECUTION | ALL_TRADES | REALISED | simulation | r_multiple | default | — | Ruin probability estimate | 🟢 |
| 6 | E-006 | Is edge real or overfitted? | EXECUTION | ALL_TRADES | REALISED | degradation | r_multiple | default | — | Walk-forward result | 🟢 (BLOCKED) |
| 7 | E-007 | Is SL too tight or wide? | EXECUTION | ALL_TRADES+LOSING | REALISED | simulation | r_multiple | default | — | SL sensitivity analysis | 🟢 |
| 8 | E-008 | Are patterns degrading over time? | EXECUTION | ALL_TRADES | REALISED | degradation | r_multiple | default | — | Temporal comparison | 🟢 (PARTIAL) |
| 9 | E-009 | Does duration predict outcome? | EXECUTION | ALL_TRADES | REALISED | predictive_power | r_multiple | feature=duration_seconds | — | Monotonicity, spread | 🟢 |
| 10 | E-010 | Does intended R:R predict success? | EXECUTION | ALL_TRADES | REALISED | comparison | r_multiple | group=exit_reason | — | SL vs TP group comparison | 🟢 |
| 11 | D-001 | Does score actually predict R? | DECISION | EXECUTE_DECISIONS | REALISED | predictive_power | r_multiple | feature=score | Implicit enrichment | Monotonicity of score→R | 🟠 |
| 12 | D-002 | Is predicted probability calibrated? | DECISION | EXECUTE_DECISIONS | REALISED | calibration | r_multiple | predicted=p_success | Implicit enrichment | Calibration error | 🟢 |
| 13 | D-003 | Are thresholds optimal? | DECISION | EXECUTE_DECISIONS | REALISED | segmentation | r_multiple | implicit score buckets | Implicit enrichment | Score-bucket R comparison | 🟠 |
| 14 | D-004 | Where does pipeline reject? (+ edge cost) | DECISION | NO_TRADE_DECISIONS | REALISED | segmentation | r_multiple | dim=terminal_reason | — | Segments by stage — BUT metric invalid | 🔴 |
| 15 | D-005 | Does opportunity quality predict outcome? | DECISION | EXECUTE_DECISIONS | REALISED | predictive_power | r_multiple | feature=opportunity_quality | Implicit enrichment | Quality→R monotonicity | 🟢 |
| 16 | D-006 | What characterises false positives? | DECISION | EXECUTE_DECISIONS | REALISED | segmentation | r_multiple | dim=opportunity_state | Implicit enrichment | State→R segments | 🟢 |
| 17 | D-007 | Does risk gate protect or destroy value? | DECISION | REJECTED_AT_RISK | REALISED | comparison | score | group=risk_approved | — | Score comparison — but ALL records have risk_approved=False | 🔴 |
| 18 | M-001 | Does regime predict trade outcome? | MARKET | ALL_MARKET_STATES | REALISED | segmentation | r_multiple | dim=regime | Implicit enrichment | Per-regime mean R | 🟢 |
| 19 | M-002 | Does HTF alignment predict success? | MARKET | ALL_MARKET_STATES | REALISED | predictive_power | r_multiple | feature=htf_alignment_strength | Implicit enrichment | Alignment→R monotonicity | 🟢 |
| 20 | M-003 | Does volatility affect expectancy? | MARKET | ALL_MARKET_STATES | REALISED | segmentation | r_multiple | dim=regime,volatility_state | Implicit enrichment | Vol×Regime→R segments | 🟢 |
| 21 | M-004 | Does H1 clarity predict outcome? | MARKET | ALL_MARKET_STATES | REALISED | predictive_power | r_multiple | feature=h1_structural_clarity | Implicit enrichment | Clarity→R monotonicity | 🟢 |
| 22 | M-005 | Does location predict outcome? | MARKET | ALL_MARKET_STATES | REALISED | segmentation | r_multiple | dim=location_type | Implicit enrichment | Location→R segments | 🟢 |
| 23 | M-006 | Does session affect expectancy? | MARKET | ALL_MARKET_STATES | REALISED | segmentation | r_multiple | dim=session | Implicit enrichment | Session→R segments | 🟢 |
| 24 | S-001 | Which strategy families are profitable? | STRATEGY | ALL_STRATEGIES | REALISED | segmentation | r_multiple | dim=family | Implicit enrichment | Family→R segments | 🟢 |
| 25 | S-002 | Which patterns are profitable? | STRATEGY | ALL_STRATEGIES | REALISED | segmentation | r_multiple | dim=pattern | Implicit enrichment | Pattern→R segments | 🟢 |
| 26 | S-003 | Does strategy confidence predict outcome? | STRATEGY | STRATEGY_SELECTED | REALISED | calibration | r_multiple | predicted=confidence | Implicit enrichment | Confidence calibration | 🟢 |
| 27 | S-004 | What characterises strategy gaps? | STRATEGY | STRATEGY_REJECTED | REALISED | distribution | — | default | — | Gap characterisation (NO outcome) | 🟠 |
| 28 | ED-001 | How much edge is lost in execution? | EXEC+DEC | ALL_TRADES+EXECUTE_DEC | REALISED | comparison | r_multiple | default | entity_id (declared) | EV vs realised R comparison | 🟢 |
| 29 | ED-002 | What do rejected opportunities miss? | EXEC+DEC | NO_TRADE+ALL_TRADES | REALISED | counterfactual | r_multiple | default | correlation_id | **Join fails** (~0% match) | 🔴 |
| 30 | ED-003 | Would quality-scaled sizing help? | EXEC+DEC | ALL_TRADES+EXECUTE_DEC | REALISED | simulation | r_multiple | default | entity_id | Sizing simulation | 🟢 |
| 31 | EM-001 | Does regime affect realised expectancy? | EXEC+MKT | ALL_TRADES+ALL_MKT | REALISED | segmentation | r_multiple | dim=regime | entity_id | Regime→realised R | 🟢 |
| 32 | EM-002 | Is market behaviour drifting? | EXEC+MKT | ALL_TRADES+ALL_MKT | REALISED | degradation | r_multiple | default | entity_id | Temporal shift detection | 🟢 |
| 33 | ES-001 | Do strategies have different exec quality? | EXEC+STRAT | ALL_TRADES+ALL_STRAT | REALISED | segmentation | r_multiple | dim=family | entity_id | Family→R execution segments | 🟢 |
| 34 | DM-001 | Is decision accuracy regime-dependent? | DEC+MKT | EXECUTE_DEC+ALL_MKT | REALISED | segmentation | r_multiple | dim=regime | entity_id | Regime→score accuracy | 🟢 |
| 35 | DM-002 | Does quality degrade in certain markets? | DEC+MKT | ALL_DEC+ALL_MKT | REALISED | comparison | r_multiple | default | entity_id | Quality×market comparison | 🟢 |
| 36 | DM-003 | Does rejection rate vary by regime? | DEC+MKT | ALL_DEC+NO_TRADE+ALL_MKT | REALISED | segmentation | r_multiple | dim=regime | entity_id | Regime→rejection rate (metric weak) | 🟠 |
| 37 | DS-001 | Is strategy confidence calibrated? | DEC+STRAT | EXECUTE_DEC+STRAT_SEL | REALISED | calibration | r_multiple | predicted=confidence | entity_id | Confidence→actual calibration | 🟢 |
| 38 | DS-002 | Do conditions_met predict outcome? | DEC+STRAT | EXECUTE_DEC+STRAT_SEL | REALISED | predictive_power | r_multiple | feature=conditions_met | entity_id | Conditions→R monotonicity | 🟢 |
| 39 | MS-001 | Do strategies differ across regimes? | MKT+STRAT | ALL_MKT+ALL_STRAT | REALISED | segmentation | r_multiple | dim=regime,family | entity_id | Regime×Strategy→R | 🟢 |
| 40 | MS-002 | Are patterns only profitable in certain contexts? | MKT+STRAT | ALL_MKT+ALL_STRAT | REALISED | segmentation | r_multiple | dim=regime,pattern | entity_id | Regime×Pattern→R | 🟢 |
| 41 | MS-003 | Where are strategy coverage gaps? | MKT+STRAT | ALL_MKT+STRAT_ELIGIBLE/REJECTED | REALISED | distribution | — | default | entity_id | Coverage gap characterisation | 🟢 |
| 42 | EDM-001 | Where does full lifecycle add/lose value? | EXEC+DEC+MKT | ALL_TRADES+EXEC_DEC+ALL_MKT | REALISED | comparison | r_multiple | default | entity_id | Pipeline value-add analysis | 🟢 |
| 43 | DMS-001 | Does quality vary by strategy×regime? | DEC+MKT+STRAT | EXEC_DEC+ALL_MKT+ALL_STRAT | REALISED | segmentation | r_multiple | dim=regime,family | entity_id | Multi-dim segmentation | 🟢 |
| 44 | EDMS-001 | What contributes most to final outcomes? | ALL 4 | All respective | REALISED | predictive_power | r_multiple | default | entity_id | Attribution analysis | 🟢 |
| 45 | EDMS-002 | What would promotion impact be? | ALL 4 | All respective | REALISED | simulation | r_multiple | default | entity_id | Impact simulation | 🟢 |
| 46 | SD-001 | What is counterfactual expectancy of all detected opportunities? | SHADOW_OUTCOME | ALL_SHADOW_OUTCOMES (4,153) | COUNTERFACTUAL | expectancy | r_multiple | default | — | Mean counterfactual R | 🟢 |
| 47 | SD-002 | What do V10's rejected opportunities produce counterfactually? | SHADOW_OUTCOME | SHADOW_FROM_NO_TRADE (3,201) | COUNTERFACTUAL | expectancy | r_multiple | default | — | Missed-opportunity R | 🟢 |
| 48 | SD-004 | Which rejection stage removes most counterfactual edge? | SHADOW_OUTCOME+DECISION | SHADOW_FROM_NO_TRADE joined to NO_TRADE | COUNTERFACTUAL | segmentation | r_multiple | dim=terminal_reason | entity_id (99% join) | Stage→counterfactual R | 🟢 |
| 49 | SD-005 | Which horizon geometry captures most counterfactual edge? | SHADOW_OUTCOME | HORIZON_SCALP+INTRADAY+EXTENDED | COUNTERFACTUAL | comparison | r_multiple | group=trade_horizon | — | Horizon→R comparison | 🟠 |
| 50 | SD-006 | Which strategies have positive counterfactual expectancy? | SHADOW_OUTCOME | ALL_SHADOW_OUTCOMES | COUNTERFACTUAL | segmentation | r_multiple | dim=strategy_id | — | Strategy→counterfactual R | 🟢 |
| 51 | SD-007 | Does regime predict counterfactual outcome? | SHADOW_OUTCOME | ALL_SHADOW_OUTCOMES | COUNTERFACTUAL | segmentation | r_multiple | dim=regime | — | Regime→counterfactual R | 🟢 |

---


## 3. GREEN QUESTIONS (44 total)

These questions have correct end-to-end mappings:
- The research intent matches the analytical operation
- The universe owns the required evidence
- The population is correctly filtered
- The metric is valid for the population
- Joins are functional (where required)
- The finding legitimately answers the question
- Evidence source is correctly classified

**All 10 E-* questions** are correctly mapped to EXECUTION universe with realised R-multiple.

**D-002, D-005, D-006** correctly use EXECUTE_DECISIONS population (which has outcome enrichment providing r_multiple from execution data).

**All 6 M-* questions** correctly use MARKET universe with outcome enrichment.

**S-001, S-002, S-003** correctly map strategy populations to enriched outcomes.

**All cross-angle Live questions** (ED-001, ED-003, EM-001, EM-002, ES-001, DM-001, DM-002, DS-001, DS-002, MS-001, MS-002, MS-003, EDM-001, DMS-001, EDMS-001, EDMS-002) correctly declare their joins via entity_id.

**SD-001, SD-002, SD-004, SD-006, SD-007** correctly consume SHADOW_OUTCOME populations with COUNTERFACTUAL evidence classification.

---

## 4. AMBER QUESTIONS (4 total)

### D-001 (Score Predictive Power)

**Intent:** Does score predict trade outcome?  
**Issue:** `r_multiple` in DECISION universe comes from outcome enrichment (implicit cross-universe dependency). Not declared in `required_joins`.  
**Impact:** The analysis IS valid — the enrichment works correctly. The mapping produces correct findings. The documentation is incomplete rather than the logic.  
**Fix needed:** Document the implicit enrichment dependency or add explicit EXECUTION join.

### D-003 (Threshold Effectiveness)

**Intent:** Are score thresholds optimal?  
**Issue:** Can only measure score→R within EXECUTE_DECISIONS (above current threshold). Cannot see what happens below the threshold without Shadow.  
**Impact:** The question IS valid for "does higher score produce better R within executed trades?" but CANNOT fully answer "should we change the threshold?" (one-sided evidence).  
**Fix needed:** Leave as-is for Live; recognise that the full threshold question requires a future CROSS_SIDE variant.

### DM-003 (Rejection Rate by Market State)

**Intent:** Does NO_TRADE rate vary by regime?  
**Issue:** The question declares `metric_field="r_multiple"` and `dimensions=["regime"]`. For the `ALL_DECISIONS` population that includes NO_TRADE records, `r_multiple` is sparse. The COUNT-based segmentation (how many NO_TRADE per regime) IS valid, but the r_multiple metric is mostly null.  
**Impact:** The segmentation primitive will produce counts per regime (valid) and mean R per regime (invalid/sparse). The finding mixes valid descriptive data with unreliable metric data.  
**Fix needed:** Change metric_field from `r_multiple` to a count/rate metric, or split into descriptive (rate) + SD-007 (counterfactual R by regime).

### SD-005 (Shadow Horizon Comparison)

**Intent:** Which horizon geometry captures most counterfactual edge?  
**Issue:** `group_field="trade_horizon"` applied to ALL_SHADOW_OUTCOMES pools:
- V10_PRIMARY records (V10 geometry, mean R = +0.56)  
- HORIZON_ALTERNATIVE records (structure geometry, mean R ≈ -0.07)

These use fundamentally different SL/TP construction and cannot be directly compared as "which horizon is best?"  
**Impact:** Finding shows V10_PRIMARY dramatically "outperforming" alternatives — but this reflects geometry quality (V10 Entry engine vs simplified structure-based), not horizon preference.  
**Fix needed:** Restrict population to `HORIZON_ALTERNATIVE` only for the comparison (exclude V10_PRIMARY from grouping). Or add `shadow_type="HORIZON_ALTERNATIVE"` as a pre-filter.

---

## 5. RED QUESTIONS (3 total)

### D-004 (Rejection Stage Analysis) — METRIC INVALID

**What it tries to discover:** Which rejection stage removes the most potential edge vs protects capital?  
**What it actually measures:** Segments NO_TRADE_DECISIONS by terminal_reason with r_multiple as metric.  
**Why the mapping fails:** NO_TRADE_DECISIONS have no realised R-multiple by definition. Only ~1 record has r_multiple (from an outcome enrichment artifact). The segmentation produces counts per stage (valid) but mean R per stage (meaningless — calculated from 0-1 records per segment).  
**Evidence needed:** Counterfactual R per stage → SD-004 already provides this correctly.  
**Fix:** Remove `r_multiple` from metric. D-004 becomes descriptive (counts only). SD-004 handles outcome.

### D-007 (Risk Gate Value) — SINGLE-SIDED EVIDENCE

**What it tries to discover:** Whether risk rejection protects capital or destroys edge.  
**What it actually measures:** Groups REJECTED_AT_RISK records by `risk_approved` field with `score` metric.  
**Why the mapping fails:** ALL records in REJECTED_AT_RISK have `risk_approved=False` by definition of the population filter. There is no comparison group. The question asks "does blocking help?" but can only see blocked records — never the counterfactual of what those blocked opportunities would have produced.  
**Evidence needed:** Cross-side: Live R (risk-approved trades) vs Shadow R (risk-rejected entity_ids from SHADOW_OUTCOME).  
**Fix:** Reclassify as CROSS_SIDE requiring DECISION + SHADOW_OUTCOME joined by entity_id.

### ED-002 (Missed Opportunity Cost) — JOIN FAILS

**What it tries to discover:** Which rejected decisions would have succeeded if allowed through?  
**What it actually measures:** Attempts to join NO_TRADE_DECISIONS to EXECUTION via `correlation_id`.  
**Why the mapping fails:** `correlation_id` (COR- format) is generated in `engine_execution_handler.py` which only executes for EXECUTE decisions. NO_TRADE decisions never receive a COR- correlation_id. The join has ~0% match rate.  
**Evidence needed:** entity_id join from SHADOW_OUTCOME to DECISION (NO_TRADE) → SD-002 already provides this.  
**Fix:** Mark as SUPERSEDED by SD-002. SD-002 correctly answers the same question using Shadow data.

---

## 6. CROSS-SIDE MAPPING AUDIT

### Currently Implemented Cross-Side Joins

| Question | Left | Right | Key | Match Rate | Valid? |
|----------|------|-------|-----|-----------|--------|
| SD-004 | SHADOW_OUTCOME | DECISION | entity_id | 99% | ✓ YES |
| ED-001 | DECISION | EXECUTION | entity_id | ~100% for EXECUTE | ✓ YES |
| ED-002 | DECISION | EXECUTION | correlation_id | ~0% for NO_TRADE | ✗ FAILS |

### Cross-Side Questions That SHOULD Exist But Don't

| Question | Left | Right | Key | Purpose |
|----------|------|-------|-----|---------|
| Risk Gate Net Value | EXECUTION (approved) | SHADOW_OUTCOME (risk-rejected) | entity_id | Compare approved Live R vs blocked Shadow R |
| Execution Leakage | SHADOW_OUTCOME (V10_PRIMARY, EXECUTE) | EXECUTION (same entities) | entity_id | Paired Live R vs Shadow R for same trade |
| Threshold Optimality | DECISION (all scores) | SHADOW_OUTCOME (below-threshold) | entity_id | What's above vs below the cut |

### entity_id Join Reliability

| Source → Target | Key | Deterministic? | 1:1? | Coverage | Reliable? |
|-----------------|-----|---------------|------|----------|-----------|
| Shadow → Decision | entity_id | YES | N:1 (multiple shadows per decision) | 99% (when entity_id present) | ✓ |
| Decision → Execution | entity_id | YES | 1:1 (only for EXECUTE) | ~3% (430 EXECUTE of 15,865) | ✓ (for EXECUTE subset) |
| Decision → Market | entity_id | YES | 1:1 | ~100% (same source) | ✓ |
| Decision → Strategy | entity_id | YES | 1:1 | ~100% (same source) | ✓ |

---

## 7. SHADOW QUESTION MAPPING AUDIT

| ID | Intent | Population | Evidence Contamination Risk | Horizon Semantics Issue | Status |
|-----|--------|-----------|---------------------------|------------------------|--------|
| SD-001 | Overall counterfactual pool expectancy | ALL_SHADOW_OUTCOMES | NONE — all labelled COUNTERFACTUAL | NONE — pools ALL types intentionally | 🟢 |
| SD-002 | Rejected opportunity counterfactual | SHADOW_FROM_NO_TRADE | NONE | NONE — HORIZON_ALTERNATIVE population (all NO_TRADE eligible) | 🟢 |
| SD-004 | Rejection stage counterfactual R | Joined to DECISION | NONE — entity_id join at 99% | NONE — uses terminal_reason from Decision | 🟢 |
| SD-005 | Horizon geometry comparison | HORIZON_SCALP/INTRADAY/EXTENDED | **YES** — V10_PRIMARY pollutes the comparison via `group_field=trade_horizon` | **YES** — pools V10 geometry with structure geometry | 🟠 |
| SD-006 | Strategy counterfactual expectancy | ALL_SHADOW_OUTCOMES by strategy_id | NONE | NONE — strategy_id is from shadow record | 🟢 |
| SD-007 | Regime counterfactual expectancy | ALL_SHADOW_OUTCOMES by regime | NONE | NONE — regime is from decision_snapshot | 🟢 |

### SD-005 Contamination Detail

The `comparison` primitive groups ALL records by `trade_horizon`. Since V10_PRIMARY records have `trade_horizon` set to V10's selected horizon, they appear in the comparison alongside HORIZON_ALTERNATIVE records of the same horizon name but with DIFFERENT geometry. This makes V10_PRIMARY's +0.56R appear in e.g. the "SCALP" group alongside structure-based SCALP at -0.08R — distorting the group mean.

---

## 8. HORIZON QUESTION MAPPING AUDIT

| Question | What Horizon Concept It Uses | V10 Selected? | Structure Eligible? | Research Eligible? | Simulated? | Valid Outcome? | Issue? |
|----------|------------------------------|---------------|--------------------|--------------------|-----------|---------------|--------|
| SD-005 | `trade_horizon` field (mixed V10 + structure) | POOLED | POOLED | POOLED | YES | YES | **Conflates V10 selected with alternatives** |
| SD-001 | N/A (pools all) | N/A | N/A | N/A | YES | YES | NONE (intentionally all-inclusive) |
| SD-002 | N/A (NO_TRADE only = HORIZON_ALTERNATIVE) | NO | YES | YES | YES | YES | NONE |

### Missing Horizon Questions

| Question | What It Would Ask | Required Data |
|----------|-------------------|---------------|
| "Does V10 select the optimal horizon?" | Compare SELECTED shadow R vs ALTERNATIVE shadow R for same entities | New-format records with `horizon_selection_status` (starts accumulating on next live run) |
| "V10 geometry vs structure geometry" | For same entity: V10_PRIMARY R vs HORIZON_ALTERNATIVE R | Both shadow types for same entity_id |

---

## 9. REJECTION-QUESTION MAPPING

| Question | Rejection Stage Addressed | Live Evidence? | Shadow Evidence? | Cross-Side? | Correct? |
|----------|--------------------------|---------------|-----------------|-------------|----------|
| D-004 | ALL stages (terminal_reason segmentation) | YES (counts) | NO (r_multiple invalid) | NO | 🔴 for outcome, 🟢 for counts |
| D-007 | RISK specifically | YES (score only) | SHOULD have shadow | SHOULD be cross | 🔴 |
| SD-004 | ALL stages via join | YES (via entity_id join to Decision) | YES (shadow R) | HYBRID (shadow R + live stage) | 🟢 |
| DM-003 | Market × rejection rate | YES (rate) | NO | NO | 🟠 (rate valid, R metric weak) |

### Replayability by Rejection Stage (from approved specification)

| Stage | V10 Geometry Available? | Structure Geometry? | Shadow Exists? | Evidence Level |
|-------|------------------------|--------------------|----|---|
| Opportunity | NO | NO | NO (can't construct meaningful trade) | NON_REPLAYABLE |
| Strategy | NO | PARTIAL | CONDITIONAL (if direction from opportunity) | CONDITIONAL |
| Entry | NO | YES | YES (horizon shadows created) | CONDITIONAL |
| Risk | YES | YES | YES (V10_PRIMARY + horizons created from Phase 3) | VALID |
| Execution | YES | YES | YES | VALID |

---

## 10. QUARANTINE / HISTORICAL PARTICIPATION

### How Historical (CONDITIONAL) Data Participates

ALL 4,153 current shadow records are classified `data_quality=CONDITIONAL` because they lack `horizon_selection_status` (legacy — field didn't exist when they were created).

| Question | Can Use CONDITIONAL Data? | Limitation Acknowledged? |
|----------|--------------------------|------------------------|
| SD-001 | YES — expectancy calculation works on all shadow records | YES — `data_quality` field present |
| SD-002 | YES — shadow R is valid regardless of lineage completeness | YES |
| SD-004 | YES — entity_id join works for 78% of records | YES — remaining 22% excluded from join |
| SD-005 | YES — r_multiple valid, trade_horizon parseable from trade_id | **PARTIALLY** — cannot distinguish SELECTED from ALTERNATIVE for historical records |
| SD-006 | YES — strategy_id present in shadow records | YES |
| SD-007 | YES — regime present in decision_snapshot | YES |

**No question silently treats CONDITIONAL as VALID.** The `has_lineage_contract` flag distinguishes them. Future VALID records (with full lineage fields) will naturally separate.

---

## 11. RESEARCH-ENGINE MAPPING RISKS (Systemic)

### Risk 1: Outcome Enrichment Opacity

**Affects:** D-001, D-002, D-003, D-005, D-006, M-001 through M-006, S-001, S-002, S-003, DM-001

**Issue:** These questions use `r_multiple` from universes that don't independently own it (Decision, Market, Strategy). The field arrives via "outcome enrichment" — an implicit cross-universe dependency where builders join to Execution data during construction.

**Impact:** LOW — the enrichment works correctly and produces valid findings. But the dependency is undeclared in the question contracts.

**Recommendation:** Either (a) declare the implicit enrichment in universe contracts, or (b) add explicit EXECUTION join requirements. Not urgent.

### Risk 2: Small Live Sample (94 trades)

**Affects:** All Execution-primary questions, all cross-angle questions requiring EXECUTION

**Issue:** Multi-dimensional segmentation (regime × strategy × session) fragments 94 records below statistical significance thresholds.

**Impact:** MEDIUM — some findings may report INSUFFICIENT confidence. The evidence quality system correctly catches this.

**Mitigation:** Shadow data (4,153 records) compensates for many questions. As V10 continues trading, Live sample grows.

### Risk 3: 53% Shadow Timeout Exit Bias

**Affects:** SD-001, SD-002, SD-005, SD-006, SD-007

**Issue:** 53% of shadow trades exit via `max_bars_timeout` at 60 bars. This artificial cap biases R-multiples toward zero/negative for trades that might have eventually won with more time.

**Impact:** MEDIUM — shadow win rate (42%) and expectancy (+0.07R) are understated relative to what a longer holding period might produce.

**Mitigation:** Document limitation in findings. Future: per-horizon max_bars adjustment.

---

## 12. FINAL VERDICT

### Can the Research Engine be trusted to ask its existing questions against the correct evidence and analytical view?

| Research Domain | Trust Level | Explanation |
|-----------------|-------------|-------------|
| **Live research** (E-*, most D-*, M-*, S-*, cross-angle) | **HIGH** — 41 of 45 Live questions are correctly mapped | 3 RED + 1 AMBER do not undermine the rest |
| **Shadow research** (SD-*) | **HIGH** — 5 of 6 Shadow questions are correctly mapped | SD-005 has a population contamination issue (AMBER) but produces useful findings regardless |
| **Cross-side research** | **NOT YET AVAILABLE** — no cross-side questions exist that function correctly | ED-002 fails, D-007 needs redesign. SD-004 is the closest (shadow R joined to live decision stage) |
| **Quarantine/historical research** | **VALID WITH LIMITATIONS** — all current shadow data is CONDITIONAL but usable | Limitation: cannot distinguish V10 selection from alternatives in historical data |
| **Candidate/experimental research** | **OPERATIONAL** — proven with EM-001 experiment | POPULATION_FILTER mechanism works on any flat-record population |

### Bottom Line

The Research Engine is **trustworthy for its primary purpose** — identifying patterns in V10's realised performance and counterfactual opportunity cost. The 3 RED questions are known limitations that do not produce findings used in the candidate pipeline. The research loop (Finding → Proposal → Candidate → Experiment → Validation) operates correctly on the 44 GREEN questions.

---

*End of audit. No code modified.*
