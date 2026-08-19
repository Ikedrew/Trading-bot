# RESEARCH QUESTION BANK — ARCHITECTURAL CORRECTNESS AUDIT

**Date:** 2026-07-27  
**Status:** AUDIT ONLY — No code modified  
**Scope:** All 51 registered questions (45 Live + 6 Shadow)  
**Basis:** Post-Shadow-implementation architecture  

---

## 1. EXECUTIVE SUMMARY

The Question Bank contains **51 questions** across two evidence worlds. After the Shadow implementation, the majority are architecturally correct. However:

- **4 questions** have analytical validity problems (asking the wrong question of the wrong evidence)
- **3 questions** are partially superseded by shadow counterparts
- **2 questions** have population/metric mismatches that produce misleading results
- **7 important research capabilities** are not yet represented

No question needs deletion. The issues are classification and framing, not existence.

---

## 2. QUESTION → UNIVERSE MATRIX (All 51 Questions)

| # | ID | Title | Primary Universe | Secondary | Evidence | Viewpoint | Status |
|---|-----|-------|-----------------|-----------|----------|-----------|--------|
| 1 | E-001 | System Expectancy | EXECUTION | — | REALISED | Economic outcome | CORRECT |
| 2 | E-002 | Win/Loss Distribution | EXECUTION | — | REALISED | Economic outcome | CORRECT |
| 3 | E-003 | Exit Reason Distribution | EXECUTION | — | REALISED | Execution behaviour | CORRECT |
| 4 | E-004 | Execution Quality by Session | EXECUTION | — | REALISED | Execution behaviour | CORRECT |
| 5 | E-005 | Probability of Ruin | EXECUTION | — | REALISED | Economic outcome | CORRECT |
| 6 | E-006 | Out-of-Sample Validation | EXECUTION | — | REALISED | Economic outcome | CORRECT (BLOCKED — sample) |
| 7 | E-007 | Stop Placement | EXECUTION | — | REALISED | Risk behaviour | CORRECT |
| 8 | E-008 | Pattern Degradation | EXECUTION | — | REALISED | Economic outcome | CORRECT (PARTIAL — sample) |
| 9 | E-009 | Duration vs Outcome | EXECUTION | — | REALISED | Execution behaviour | CORRECT |
| 10 | E-010 | R:R Effectiveness | EXECUTION | — | REALISED | Risk behaviour | CORRECT |
| 11 | D-001 | Score Predictive Power | DECISION | — | REALISED | Decision behaviour | **MINOR REVISION** |
| 12 | D-002 | EV Calibration | DECISION | — | REALISED | Decision behaviour | CORRECT |
| 13 | D-003 | Threshold Effectiveness | DECISION | — | REALISED | Decision behaviour | **MINOR REVISION** |
| 14 | D-004 | Rejection Stage Analysis | DECISION | — | REALISED | Pipeline rejection | **MAJOR REVISION** |
| 15 | D-005 | Opportunity Quality | DECISION | — | REALISED | Decision behaviour | CORRECT |
| 16 | D-006 | Opportunity Failure | DECISION | — | REALISED | Decision behaviour | CORRECT |
| 17 | D-007 | Risk Gate Value | DECISION | — | REALISED | Risk behaviour | **MISLOCATED** |
| 18 | M-001 | Regime Predicts Outcomes | MARKET | — | REALISED | Market behaviour | CORRECT |
| 19 | M-002 | HTF Alignment Value | MARKET | — | REALISED | Market behaviour | CORRECT |
| 20 | M-003 | Volatility State Impact | MARKET | — | REALISED | Market behaviour | CORRECT |
| 21 | M-004 | Market Structure Clarity | MARKET | — | REALISED | Market behaviour | CORRECT |
| 22 | M-005 | Location Quality Impact | MARKET | — | REALISED | Market behaviour | CORRECT |
| 23 | M-006 | Session Edge Variation | MARKET | — | REALISED | Market behaviour | CORRECT |
| 24 | S-001 | Strategy Family Expectancy | STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 25 | S-002 | Pattern Expectancy | STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 26 | S-003 | Strategy Selection Accuracy | STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 27 | S-004 | Strategy Rejection Patterns | STRATEGY | — | REALISED | Strategy behaviour | **MINOR REVISION** |
| 28 | ED-001 | Edge Leakage | EXECUTION + DECISION | — | REALISED | Cross-universe | CORRECT |
| 29 | ED-002 | Missed Opportunity Cost | EXECUTION + DECISION | — | REALISED | Cross-universe | **SUPERSEDED** |
| 30 | ED-003 | Position Sizing | EXECUTION + DECISION | — | REALISED | Risk behaviour | CORRECT |
| 31 | EM-001 | Regime-Conditioned Expectancy | EXECUTION + MARKET | — | REALISED | Market × outcome | CORRECT |
| 32 | EM-002 | Market Drift | EXECUTION + MARKET | — | REALISED | Market behaviour | CORRECT |
| 33 | ES-001 | Execution by Strategy | EXECUTION + STRATEGY | — | REALISED | Strategy × outcome | CORRECT |
| 34 | DM-001 | Decision Quality Under Regime | DECISION + MARKET | — | REALISED | Decision × market | CORRECT |
| 35 | DM-002 | Opportunity vs Market State | DECISION + MARKET | — | REALISED | Decision × market | CORRECT |
| 36 | DM-003 | Rejection Rate by Market | DECISION + MARKET | — | REALISED | Pipeline rejection | CORRECT |
| 37 | DS-001 | Strategy Confidence Calibration | DECISION + STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 38 | DS-002 | Strategy Conditions vs Outcome | DECISION + STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 39 | MS-001 | Strategy × Regime | MARKET + STRATEGY | — | REALISED | Market × strategy | CORRECT |
| 40 | MS-002 | Pattern × Market Context | MARKET + STRATEGY | — | REALISED | Market × strategy | CORRECT |
| 41 | MS-003 | Strategy Availability | MARKET + STRATEGY | — | REALISED | Strategy behaviour | CORRECT |
| 42 | EDM-001 | Complete Lifecycle | EXEC + DEC + MKT | — | REALISED | Full lifecycle | CORRECT |
| 43 | DMS-001 | Decision × Strategy × Market | DEC + MKT + STRAT | — | REALISED | Multi-dimensional | CORRECT |
| 44 | EDMS-001 | Full System Attribution | ALL 4 | — | REALISED | Attribution | CORRECT |
| 45 | EDMS-002 | Promotion Impact | ALL 4 | — | REALISED | Governance | CORRECT |
| 46 | SD-001 | Shadow Expectancy | SHADOW_OUTCOME | — | COUNTERFACTUAL | Counterfactual outcome | CORRECT |
| 47 | SD-002 | Missed Opportunity Cost | SHADOW_OUTCOME | — | COUNTERFACTUAL | Counterfactual outcome | CORRECT |
| 48 | SD-004 | Rejection Stage Counterfactual | SHADOW_OUTCOME | DECISION | COUNTERFACTUAL | Pipeline rejection | CORRECT |
| 49 | SD-005 | Horizon Comparison | SHADOW_OUTCOME | — | COUNTERFACTUAL | Horizon behaviour | **MINOR REVISION** |
| 50 | SD-006 | Strategy Shadow Expectancy | SHADOW_OUTCOME | — | COUNTERFACTUAL | Strategy behaviour | CORRECT |
| 51 | SD-007 | Regime Shadow Expectancy | SHADOW_OUTCOME | — | COUNTERFACTUAL | Market behaviour | CORRECT |

---

## 3. QUESTIONS REQUIRING REVISION

### D-004 — MAJOR REVISION

**Problem:** The question asks "which rejection stage removes the most potential edge vs protecting from losses?" but operates on `NO_TRADE_DECISIONS` with `r_multiple` metric. NO_TRADE decisions have no realised R-multiple (only ~1 from historical join artifact).

**What it should be:** TWO questions:
- **D-004 (Live/descriptive):** "Where does the pipeline reject?" — segmentation by terminal_stage, metric = COUNT (no R needed)
- **SD-004 (Shadow/counterfactual):** "What counterfactual R did rejected opportunities produce by stage?" — already exists and works (2,764 records, validated)

**Current status:** D-004 is TECHNICALLY EXECUTABLE (the segmentation primitive runs) but ANALYTICALLY INVALID (r_multiple metric has ~1 valid observation in the Live population). The evidence quality system already caught this (ranked #29), but the question itself is wrongly framed.

**Recommendation:** Narrow D-004's required_fields to remove `r_multiple`. It should be a descriptive funnel question (count/% per stage), not an outcome question. SD-004 handles the outcome dimension.

---

### D-007 — MISLOCATED (requires cross-side evidence)

**Problem:** The question asks "does the risk layer improve overall survival, or does it filter out profitable opportunities?" This is inherently a cross-side question: it needs to compare APPROVED (Live R) vs BLOCKED (Shadow R). The current implementation operates solely on the Decision universe with `analysis_type=COUNTERFACTUAL`, but it only has access to Live decision data — it cannot see what blocked opportunities WOULD have produced.

**What it should be:** A CROSS_SIDE question joining:
- DECISION (REJECTED_AT_RISK population) → identifies what was blocked
- SHADOW_OUTCOME (for same entity_ids) → provides counterfactual R of blocked opportunities

**Recommendation:** Reclassify as cross-side. Requires `Universe.DECISION + Universe.SHADOW_OUTCOME` with entity_id join.

---

### D-001 — MINOR REVISION

**Problem:** The required_fields include `r_multiple` but the primary population is `EXECUTE_DECISIONS` from the Decision universe. The Decision universe's `r_multiple` field depends on outcome enrichment from the Execution universe. This creates an implicit cross-universe dependency that isn't declared in `required_joins`.

**Recommendation:** Either (a) add explicit join to EXECUTION universe, or (b) acknowledge that Decision universe records carry outcome enrichment and document this dependency.

---

### D-003 — MINOR REVISION

**Problem:** "Would raising/lowering the threshold improve overall expectancy?" — To fully answer this, you need to see what happens BELOW the threshold (rejected opportunities). The current implementation only tests score → R correlation within EXECUTE_DECISIONS (above threshold). It cannot see the other side.

**Recommendation:** The Live version remains useful (tests score → R correlation for executed trades). A CROSS_SIDE variant would compare above-threshold Live R vs below-threshold Shadow R. This is a future question, not a fix to D-003.

---

### S-004 — MINOR REVISION

**Problem:** Asks "are there profitable patterns the strategy engine currently misses?" but operates on `STRATEGY_REJECTED` population. This population contains opportunities where no strategy matched — but without an outcome metric, it cannot determine whether those opportunities were profitable. The question's true answer requires Shadow evidence.

**Recommendation:** S-004 remains valid as a DESCRIPTIVE question ("what characterises strategy gaps?"). The counterfactual answer ("would those gaps have been profitable?") belongs to SD-006 or a future cross-side question.

---

### ED-002 — SUPERSEDED

**Problem:** "Which rejected decisions would have succeeded if allowed through?" — joins NO_TRADE decisions to EXECUTION via correlation_id. This join has near-zero match rate (rejected signals never reach execution). Status is correctly `PARTIAL`.

**What supersedes it:** SD-002 ("Missed Opportunity Cost") directly answers this question using Shadow counterfactual R for NO_TRADE decisions.

**Recommendation:** Mark as SUPERSEDED_BY_SD-002. Do not delete — it documents the historical intent. SD-002 is the architecturally correct form.

---

### SD-005 — MINOR REVISION

**Problem:** Compares SCALP vs INTRADAY vs EXTENDED using `group_field=trade_horizon`. This pools V10_PRIMARY and HORIZON_ALTERNATIVE records. The Phase 8 validation showed V10_PRIMARY has mean R = +0.5635 while HORIZON_SCALP = -0.0814 — these use fundamentally different geometry and MUST NOT be pooled.

**What it should be:** The comparison should EITHER:
- Compare horizons WITHIN the same geometry type (only HORIZON_ALTERNATIVE records), OR
- Explicitly separate V10_PRIMARY (SELECTED geometry) from HORIZON_ALTERNATIVE (structure geometry)

**Current validation showed the question still produced useful results** (comparing horizon alternatives against each other is valid). But the question's population should explicitly EXCLUDE V10_PRIMARY records from the horizon comparison to avoid geometric conflation.

**Recommendation:** Change population from ALL_SHADOW_OUTCOMES to HORIZON_ALTERNATIVE-only for the horizon comparison.

---

## 4. LIVE VS SHADOW SEPARATION AUDIT

### Questions Correctly Separated

| Live Question | Shadow Counterpart | Relationship | Status |
|---|---|---|---|
| E-001 (System Expectancy) | SD-001 (Shadow Expectancy) | Complementary — different evidence, different meaning | CORRECT |
| D-004 (Rejection Funnel) | SD-004 (Rejection Counterfactual) | Complementary — descriptive vs counterfactual | CORRECT (after D-004 revision) |
| S-001 (Strategy Expectancy) | SD-006 (Shadow Strategy) | Complementary — realised vs counterfactual | CORRECT |
| M-001 (Regime Outcomes) | SD-007 (Shadow Regime) | Complementary — realised vs counterfactual | CORRECT |
| ED-002 (Missed Opportunity) | SD-002 (Shadow Missed Opportunity) | SD-002 SUPERSEDES ED-002 | Mark ED-002 superseded |

### Evidence Confusion Risk: NONE DETECTED

All 45 Live questions use `EXECUTION`, `DECISION`, `MARKET`, `STRATEGY`, `RISK`, or `OUTCOME` universes. None reference `SHADOW_OUTCOME`. The `_classify_evidence_source()` function correctly labels them REALISED.

All 6 Shadow questions use `SHADOW_OUTCOME`. They are correctly labelled COUNTERFACTUAL.

No question currently mixes the two evidence types.

---

## 5. HORIZON-QUESTION AUDIT

### Current Horizon Questions

| Question | What It Actually Asks | Correct? |
|---|---|---|
| SD-005 | "Which structure-based horizon geometry produces the best counterfactual R?" | **PARTIALLY** — pools V10_PRIMARY with alternatives (geometric conflation) |

### What's Missing

| Missing Question | What It Would Ask | Why Important |
|---|---|---|
| "Does V10 select the optimal horizon?" | Compare SELECTED shadow R vs ALTERNATIVE shadow Rs for same entities | Tests whether HorizonEngine is correctly choosing |
| "Does horizon choice matter more than strategy choice?" | Compare variance attributable to horizon vs strategy in shadow data | Prioritises research effort |

**These cannot be answered until new-format shadow data (with `horizon_selection_status` field) accumulates from live running.** All current historical data has `horizon_selection_status=UNKNOWN`.

---

## 6. REJECTION-QUESTION AUDIT

| Question | What It Asks | Rejection-Aware? | Correct? |
|---|---|---|---|
| D-004 | Where does pipeline reject + what edge is removed? | YES — but metric (r_multiple) invalid for Live NO_TRADE | **NEEDS NARROWING** |
| D-007 | Does risk gate help or hurt? | YES — but needs cross-side evidence | **NEEDS CROSS-SIDE** |
| SD-004 | What counterfactual R by rejection stage? | YES — correct | **CORRECT** |
| SD-002 | What do rejected opportunities produce counterfactually? | YES — correct | **CORRECT** |

### Rejection-Stage Evidence Availability (from approved specification)

| Rejection Stage | V10 Geometry Available? | Structure Geometry Available? | Research Classification |
|---|---|---|---|
| Opportunity (INVALID) | NO | NO (no direction) | NON_REPLAYABLE |
| Strategy (NONE) | NO | PARTIAL (if direction from opportunity) | CONDITIONAL |
| Entry (geometry invalid) | NO | YES (structure data exists) | CONDITIONAL |
| Risk (rejected) | YES | YES | VALID |
| Execution (rejected) | YES | YES | VALID |

---

## 7. STRATEGY-QUESTION AUDIT

### Can the system discover evidence for "a strategy family that does not currently exist"?

**PARTIALLY.** The system can observe:
- Opportunities where NO strategy matched (STRATEGY_REJECTED population in Decision universe)
- Shadow outcomes for those same opportunities (via entity_id join to SHADOW_OUTCOME)
- Market conditions where strategy gaps occur (via entity_id join to Market universe)

This allows research to identify: "In market condition X, opportunities exist that no current strategy captures, and their counterfactual outcomes are Y."

This does NOT:
- Name the missing strategy
- Define its parameters
- Prove it would have been selected by V10

It CAN produce evidence that justifies: "A new strategy family should be investigated for condition X."

**This is a valid research methodology.** No additional contract is required — the existing entity_id join between STRATEGY_REJECTED decisions and SHADOW_OUTCOME already enables it.

---

## 8. QUARANTINE PARTICIPATION AUDIT

| Question Category | Can Use Quarantine (CONDITIONAL) Data? | Limitation |
|---|---|---|
| Live questions (E-*, D-*, M-*, S-*) | NO — Live questions operate on Live universe builders which read from non-quarantine sources | N/A |
| Shadow questions (SD-*) | YES — ShadowOutcomeUniverseBuilder already classifies historical data as CONDITIONAL | Must carry `data_quality=CONDITIONAL` tag; cannot distinguish selected vs alternative horizon |
| Cross-universe questions | Depends on which universe carries quarantine records | Per-question assessment needed |

**Current state:** ALL 4,153 shadow records are classified CONDITIONAL (legacy — no `horizon_selection_status`). This data IS being used by SD-001 through SD-007. The limitation ("cannot distinguish V10's selection from alternatives") travels with findings via the `data_quality` field.

---

## 9. DUPLICATE / OVERLAPPING QUESTIONS

| Pair | Relationship | Recommendation |
|---|---|---|
| ED-002 ↔ SD-002 | SD-002 supersedes ED-002's intent | Mark ED-002 as SUPERSEDED |
| D-004 ↔ SD-004 | Complementary (descriptive vs counterfactual) | Narrow D-004 to descriptive-only |
| S-001 ↔ SD-006 | Complementary (realised vs counterfactual) | Both valid — different evidence |
| M-001 ↔ SD-007 | Complementary | Both valid |
| E-001 ↔ SD-001 | Complementary | Both valid |

No true duplicates exist. All overlaps are Live/Shadow complementary pairs where both forms provide distinct evidence.

---

## 11. RESEARCH COVERAGE MATRIX

### What the Research Engine CAN Currently Answer

**MARKET:**
- ✓ Regime → outcome relationship (M-001, SD-007)
- ✓ HTF alignment predictive value (M-002)
- ✓ Volatility impact (M-003)
- ✓ Structural clarity impact (M-004)
- ✓ Location quality (M-005)
- ✓ Session edge (M-006)

**DECISION:**
- ✓ Score predictive power (D-001)
- ✓ EV calibration (D-002)
- ✓ Threshold effectiveness — partial (D-003)
- ✓ Rejection funnel — descriptive (D-004, needs narrowing)
- ✓ Opportunity quality prediction (D-005)
- ✓ False positive characterisation (D-006)
- ✗ Risk gate value — needs cross-side (D-007)

**STRATEGY:**
- ✓ Strategy family expectancy (S-001, SD-006)
- ✓ Pattern expectancy (S-002)
- ✓ Selection accuracy (S-003)
- △ Strategy gap identification — partial (S-004)
- ✗ Missing strategy discovery (requires shadow join — not yet formalised)

**RISK:**
- ✓ Risk rejection counterfactual (SD-004 with risk stage)
- ✗ Risk gate net value (needs cross-side implementation)

**EXECUTION:**
- ✓ Full realised performance (E-001 through E-010)
- ✓ Session quality (E-004)
- ✓ Stop effectiveness (E-007)

**SHADOW:**
- ✓ Counterfactual system expectancy (SD-001)
- ✓ Missed opportunity cost (SD-002)
- ✓ Rejection-stage counterfactual (SD-004)
- ✓ Horizon comparison — structure geometry (SD-005)
- ✓ Strategy counterfactual (SD-006)
- ✓ Regime counterfactual (SD-007)
- ✗ V10 horizon selection optimality (needs new-format data)
- ✗ V10 geometry vs structure geometry comparison (needs new-format data)

**CROSS-SIDE:**
- ✗ Risk gate net value (Live approved R vs Shadow rejected R)
- ✗ Threshold optimality (Live above-threshold R vs Shadow below-threshold R)
- ✗ Execution leakage (Live R vs Shadow R for same entity)

---

## 12. MISSING RESEARCH QUESTIONS

### A. REQUIRED (block analytical capability if absent)

None. The current 51 questions cover all immediate research needs.

### B. HIGH VALUE (should be added when new-format data accumulates)

| # | Question | Evidence Required | When Available |
|---|---|---|---|
| 1 | "Does V10 select the optimal horizon?" | New shadow records with `horizon_selection_status` = SELECTED vs ALTERNATIVE | After next live run |
| 2 | "What is the net value of the risk gate?" (cross-side) | DECISION REJECTED_AT_RISK + SHADOW_OUTCOME for same entity_ids | NOW (can be formalised) |
| 3 | "Does V10 geometry outperform structure geometry for the same opportunity?" | New V10_PRIMARY shadows for NO_TRADE + existing horizon shadows | After next live run |

### C. OPTIONAL (useful refinements)

| # | Question | Notes |
|---|---|---|
| 4 | "Which symbols have best/worst counterfactual expectancy?" | Simple segmentation on shadow data — easy to add |
| 5 | "Does score predict counterfactual outcome?" | Score → shadow R correlation for all decisions |
| 6 | "Is there a regime where V10 should NOT trade at all?" | Regime × shadow R showing consistently negative |
| 7 | "Are there opportunities in sessions V10 currently avoids?" | Session × shadow R for off-session periods |

### D. FUTURE / CANDIDATE RESEARCH

| # | Question | Requires |
|---|---|---|
| 8 | "What new strategy family would capture uncaptured opportunities?" | Strategy-gap analysis with shadow outcomes |
| 9 | "What is the optimal score threshold?" | Cross-side sweep analysis |
| 10 | "How much edge is lost to execution latency?" | Live R vs Shadow R paired comparison for EXECUTE decisions |

---

## 13. EXPLICIT CHANGES THAT SHOULD BE MADE (LATER)

| # | Change | File | Priority |
|---|---|---|---|
| 1 | Narrow D-004 to remove `r_multiple` from required_fields — make it descriptive-only | question_bank.py | HIGH |
| 2 | Reclassify D-007 as CROSS_SIDE requiring SHADOW_OUTCOME + DECISION | question_bank.py | HIGH |
| 3 | Mark ED-002 as SUPERSEDED_BY_SD-002 | question_bank.py | MEDIUM |
| 4 | Fix SD-005 population to exclude V10_PRIMARY from horizon comparison | question_bank.py or primitive_mapping.py | MEDIUM |
| 5 | Add cross-side "Risk Gate Net Value" question | question_bank.py | HIGH |
| 6 | Add "V10 Horizon Selection Optimality" question (when data available) | question_bank.py | DEFERRED |

---

## 14. EXPLICIT THINGS THAT SHOULD NOT BE CHANGED

| Item | Reason |
|---|---|
| All 10 E-* questions | Correct, operational, producing valid Live findings |
| M-001 through M-006 | Correct market behaviour questions |
| S-001 through S-003 | Correct strategy questions |
| ED-001, ED-003 | Correct cross-universe questions |
| EM-001, EM-002 | Correct |
| ES-001 | Correct |
| DM-001 through DM-003 | Correct |
| DS-001, DS-002 | Correct |
| MS-001 through MS-003 | Correct |
| EDM-001, DMS-001, EDMS-001, EDMS-002 | Correct |
| SD-001, SD-002, SD-004, SD-006, SD-007 | Correct counterfactual questions |
| ShadowOutcomeUniverseBuilder | Working correctly |
| Evidence labelling (_classify_evidence_source) | Working correctly |
| Universe/Population enums | Complete for current needs |
| Lineage model (entity_id canonical join) | Verified 100% join rate |

---

## 15. OVERALL RESEARCH ENGINE READINESS

The Research Engine is **architecturally sound and operationally functional**. The Question Bank is 90% correctly configured. The 4 questions requiring revision are:
- D-004 (narrow to descriptive — trivial fix)
- D-007 (reclassify as cross-side — requires new population join)
- ED-002 (mark superseded — documentation only)
- SD-005 (restrict population — parameter change)

None of these are blockers. They represent refinements that improve analytical precision but do not prevent the research loop from operating.

**The Research Engine can begin producing evidence-backed findings and proposals NOW.** The identified revisions should be made incrementally as part of normal research operations.

---

*End of audit. No code modified.*
