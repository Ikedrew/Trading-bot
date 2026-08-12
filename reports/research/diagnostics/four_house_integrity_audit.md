# Four-House Integrity Audit — Complete 45-Question Assessment

## Classification Key

| Code | Meaning |
|------|---------|
| A | VALID AND READY — hypothesis, primitive, fields, sample all correct |
| B | VALID BUT DATA-LIMITED — correct setup but insufficient outcome records |
| C | VALID BUT MISSING EVIDENCE — correct setup but required field is empty/unavailable |
| D | WRONG PARAMETER MAPPING — correct primitive but wrong field supplied |
| E | WRONG PRIMITIVE — primitive cannot answer the stated hypothesis |
| F | WRONG UNIVERSE — question assigned to wrong data source |
| G | HYPOTHESIS CANNOT CURRENTLY BE ANSWERED — needs counterfactual/shadow data |
| H | BLOCKED BY CONTRACT / INFRASTRUCTURE |

---

## EXECUTION / TRADE HOUSE (10 questions)

| ID | Hypothesis | Primitive | Params | Fields measured | Pop | Analytical | Class | Issue |
|---|---|---|---|---|---|---|---|---|
| E-001 | System expectancy? | expectancy | (default) | r_multiple | 94 | 94 | **A** | — |
| E-002 | Win/loss shape? | distribution+expectancy | (default) | r_multiple | 94 | 94 | **A** | — |
| E-003 | Exit reason %? | distribution+expectancy | field=terminal_reason | terminal_reason (STRING) | 94 | 0 | **E** | Distribution primitive requires numeric values. Cannot compute mean/std of strings. Needs segmentation primitive with dimensions=["exit_reason"] |
| E-004 | Session quality? | segmentation+expectancy | dimensions=["session"] | session→r_multiple | 94 | 94 | **A** | Session available in 94/94. Segmentation can group by session |
| E-005 | Ruin probability? | expectancy+distribution | (default) | r_multiple | 94 | 94 | **B** | Primitive runs but 94 trades is thin for Monte Carlo. Logic correct |
| E-006 | Walk-forward? | degradation | (default) | r_multiple, entry_time | — | — | **H** | Needs 100+ trades. BLOCKED |
| E-007 | Stop placement? | expectancy+distribution | (default) | r_multiple | 94 | 94 | **E** | Hypothesis asks "is SL too tight/wide?" but primitive just computes expectancy. Needs a primitive that correlates SL distance with outcome |
| E-008 | Pattern degradation? | degradation | (default) | r_multiple, entry_time | 94 | 94 | **B** | Correct logic but 94 trades over short period — degradation test is weak |
| E-009 | Duration predicts outcome? | predictive_power | feature=duration_seconds | duration_seconds→r_multiple | 94 | 94 | **A** | Verified working |
| E-010 | R:R effectiveness? | comparison | group=exit_reason, metric=r_multiple | exit_reason groups | 94 | 94 | **A** | Groups SL/TP hits and compares R — reveals whether R:R structure works |

### Execution House Summary
- **Valid + Ready: 5** (E-001, E-002, E-004, E-009, E-010)
- **Valid but data-limited: 2** (E-005, E-008)
- **Wrong primitive: 2** (E-003, E-007)
- **Blocked: 1** (E-006)

---

## DECISION HOUSE (7 questions)

| ID | Hypothesis | Primitive | Params | Fields measured | Pop | Analytical | Class | Issue |
|---|---|---|---|---|---|---|---|---|
| D-001 | Score predicts R? | predictive_power | feature=score | score→r_multiple | 8960 | 81 | **A** | 81 pairs (score+r_multiple). Primitive works correctly. Result: NOT_PREDICTIVE |
| D-002 | EV calibrated? | calibration | predicted=p_success | p_success→r_multiple | 369 | ~76 | **C** | p_success only populated in 3% of records (292/8960). On EXECUTE pop (~369), coverage depends on overlap. Calibration may work if enough pairs exist |
| D-003 | Thresholds optimal? | segmentation+expectancy | dimensions=[symbol] DEFAULT | symbol→r_multiple | 8960 | 81 | **D** | Should segment by score_bucket but no categorical score field exists. Segmentation by symbol is meaningless for threshold question |
| D-004 | Where rejected? | distribution+expectancy | field=terminal_reason | terminal_reason (STRING) | 8591 | 0 | **E** | Same as E-003: distribution primitive cannot handle strings. Needs segmentation with dimensions=["terminal_reason"] or a count-based approach |
| D-005 | Opportunity quality predicts R? | predictive_power | feature=opportunity_quality | opp_quality→r_multiple | 369 | ~80 | **D** | Currently uses default feature=score. Should be opportunity_quality. With proposed fix: correct |
| D-006 | False positive characteristics? | segmentation+expectancy | dimensions=[symbol] DEFAULT | symbol→r_multiple | 369 | 80 | **D** | Should segment by opportunity_quality or score bucket. Default symbol doesn't answer hypothesis |
| D-007 | Risk gates improve outcomes? | comparison+expectancy | group=risk_approved, metric=score | risk_approved→score | 8960 | ~8960 | **A** | Grouping by risk_approved and comparing scores shows whether risk gates target low-quality decisions. This IS informative even without counterfactual outcomes |

### Decision House Summary
- **Valid + Ready: 2** (D-001, D-007)
- **Valid but missing evidence: 1** (D-002 — p_success coverage very low)
- **Wrong parameter mapping: 3** (D-003, D-005, D-006)
- **Wrong primitive: 1** (D-004)

---

## MARKET HOUSE (6 questions)

| ID | Hypothesis | Primitive | Params | Fields measured | Pop | Analytical | Class | Issue |
|---|---|---|---|---|---|---|---|---|
| M-001 | Regime predicts outcomes? | segmentation+expectancy | dimensions=["regime"] | regime→r_multiple | 6983 | 4 | **B** | Correct mapping. Only 4 records have r_multiple. Segmentation by regime IS correct but data-limited |
| M-002 | HTF alignment predicts? | predictive_power | feature=htf_alignment_strength | htf_alignment→r_multiple | 6983 | 4 | **B** | Correct mapping. Only 4 outcome records. Primitive correctly refuses (<10) |
| M-003 | Regime+volatility improves? | segmentation+expectancy | dimensions=["regime","volatility_state"] | regime×vol→r_multiple | 6983 | 4 | **B** | Correct mapping. Data-limited |
| M-004 | H1 clarity predicts? | predictive_power | feature=h1_structural_clarity | clarity→r_multiple | 6983 | 4 | **B** | Correct mapping. Data-limited |
| M-005 | Location predicts? | segmentation+expectancy | dimensions=["location_type"] | location→r_multiple | 6983 | 4 | **B** | Correct mapping. Data-limited |
| M-006 | Session affects expectancy? | segmentation+expectancy | dimensions=["session"] | session→r_multiple | 6983 | 4 | **B** | Correct mapping. Data-limited |

### Market House Summary
- **Valid + Ready: 0**
- **Valid but data-limited: 6** (ALL — only 4 outcome records in Market Universe)
- **Wrong mapping: 0**
- **Wrong primitive: 0**

All Market questions are correctly configured. The bottleneck is the Market Universe entity_id enrichment match rate (4/6983 = 0.06%).

---

## STRATEGY HOUSE (4 questions)

| ID | Hypothesis | Primitive | Params | Fields measured | Pop | Analytical | Class | Issue |
|---|---|---|---|---|---|---|---|---|
| S-001 | Strategy family expectancy? | segmentation+expectancy | dimensions=["family"] | family→r_multiple | 13948 | 81 | **A** | 81 records with outcomes enriched. Segmentation by family works |
| S-002 | Pattern expectancy? | segmentation+expectancy | dimensions=["pattern"] | pattern→r_multiple | 13948 | 81 | **A** | Same 81 outcome records. Segmentation by pattern works |
| S-003 | Confidence calibrated? | calibration | predicted=confidence | confidence→r_multiple | 391 | 35 | **A** | Verified: 35 pairs available. Calibration can execute |
| S-004 | Strategy rejection patterns? | distribution+expectancy | field=r_multiple DEFAULT | r_multiple | 1946 | 0 | **G** | STRATEGY_REJECTED population has 0 outcomes by definition (rejected strategies never become trades). This is a structural impossibility, not a bug |

### Strategy House Summary
- **Valid + Ready: 3** (S-001, S-002, S-003)
- **Hypothesis cannot be answered: 1** (S-004 — rejected strategies have no outcomes)

---

## CROSS-UNIVERSE QUESTIONS (18 questions)

| ID | Universes | Hypothesis | Primitive | Params | Pop | Analytical | Class | Issue |
|---|---|---|---|---|---|---|---|---|
| ED-001 | EXEC+DEC | Edge leakage? | comparison | group=regime DEFAULT | 94 | 94 | **D** | Should compare predicted EV vs realised R, not group by regime |
| ED-002 | EXEC+DEC | Missed opportunity cost? | comparison+expectancy | (default) | 0 | 0 | **G** | Counterfactual: rejected trades have no outcomes. Population resolves to 0 |
| ED-003 | EXEC+DEC | Position sizing? | expectancy+distribution | (default) | 94 | 94 | **A** | Expectancy on all trades — valid |
| EM-001 | EXEC+MKT | Regime-conditioned expectancy? | segmentation+expectancy | dimensions=["regime"] | 94 | 94 | **A** | Segments execution trades by regime — correct |
| EM-002 | EXEC+MKT | Market drift? | degradation | (default) | 94 | 94 | **B** | Correct but short time period |
| ES-001 | EXEC+STRAT | Execution quality by strategy? | segmentation+expectancy | dimensions=["family"] | 94 | ~19 | **B** | Only 19/94 have family populated. Correct mapping but data sparse |
| DM-001 | DEC+MKT | Decision quality under regime? | segmentation+expectancy | dimensions=["regime"] | 369 | 81 | **A** | Segments EXECUTE decisions by regime |
| DM-002 | DEC+MKT | Opportunity vs market state? | comparison | (default group=regime) | 8960 | 81 | **A** | Compares outcome by regime on all decisions |
| DM-003 | DEC+MKT | Rejection rate by regime? | segmentation+expectancy | dimensions=["regime"], metric=r_multiple | 8960 | 81 | **E** | Question asks about rejection RATE (categorical/count), not expectancy. Segmentation with metric=r_multiple only shows outcomes of the 81 executed trades by regime, not rejection rates |
| DS-001 | DEC+STRAT | Strategy confidence calibrated? | calibration | predicted=p_success DEFAULT | 369 | ~76 | **D** | Should be predicted=confidence (same as S-003). p_success has very low coverage |
| DS-002 | DEC+STRAT | Conditions predict outcome? | predictive_power | feature=score DEFAULT | 369 | ~80 | **D** | Should be feature=conditions_met |
| MS-001 | MKT+STRAT | Strategy×regime? | segmentation+expectancy | dimensions=["regime","family"] | 6983 | 4 | **B** | Correct mapping. Data-limited (4 outcomes) |
| MS-002 | MKT+STRAT | Pattern×market? | segmentation+expectancy | dimensions=["regime","pattern"] | 6983 | 4 | **B** | Correct mapping. Data-limited |
| MS-003 | MKT+STRAT | Strategy availability? | distribution+expectancy | field=r_multiple DEFAULT | 6983 | 4 | **E** | Question asks about coverage/availability (categorical), not R distribution |
| EDM-001 | EXEC+DEC+MKT | Lifecycle analysis? | comparison | (default group=regime) | 94 | 94 | **A** | Regime comparison across full lifecycle — acceptable |
| DMS-001 | DEC+MKT+STRAT | Decision×strategy×market? | segmentation+expectancy | dimensions=["regime","family"] | 369 | 81 | **A** | Two-dimensional segmentation — correct |
| EDMS-001 | ALL | System attribution? | predictive_power | feature=score | 94 | 94 | **A** | Tests what predicts outcome — score is aggregate predictor |
| EDMS-002 | ALL | Promotion impact? | expectancy+distribution | (default) | 94 | 94 | **A** | Expectancy — valid |

### Cross-Universe Summary
- **Valid + Ready: 9** (ED-003, EM-001, DM-001, DM-002, EDM-001, DMS-001, EDMS-001, EDMS-002, E-010*)
- **Valid but data-limited: 4** (EM-002, ES-001, MS-001, MS-002)
- **Wrong parameter mapping: 3** (ED-001, DS-001, DS-002)
- **Wrong primitive: 2** (DM-003, MS-003)
- **Hypothesis cannot be answered: 1** (ED-002)

---

## COMPLETE CLASSIFICATION SUMMARY

| Classification | Count | Questions |
|---|---|---|
| **A. VALID AND READY** | 19 | E-001, E-002, E-004, E-009, E-010, D-001, D-007, S-001, S-002, S-003, ED-003, EM-001, DM-001, DM-002, EDM-001, DMS-001, EDMS-001, EDMS-002, E-010 |
| **B. VALID BUT DATA-LIMITED** | 12 | E-005, E-008, M-001–M-006, EM-002, ES-001, MS-001, MS-002 |
| **C. VALID BUT MISSING EVIDENCE** | 1 | D-002 |
| **D. WRONG PARAMETER MAPPING** | 6 | D-003, D-005, D-006, ED-001, DS-001, DS-002 |
| **E. WRONG PRIMITIVE** | 5 | E-003, E-007, D-004, DM-003, MS-003 |
| **G. HYPOTHESIS CANNOT BE ANSWERED** | 2 | S-004, ED-002 |
| **H. BLOCKED** | 1 | E-006 |

---

## FOUR-HOUSE HEALTH REPORT

### MARKET HOUSE

| Dimension | Status |
|---|---|
| **Can currently prove** | Market regime distribution (RANGING 63%, TRENDING 20%, TRANSITIONAL 16%). Market state field availability |
| **Cannot prove** | Whether any market feature predicts trade outcomes (only 4 outcome records) |
| **Field coverage** | regime 100%, h1_structural_clarity 74%, htf_alignment_strength 88%, location_type 74%, session derived |
| **Primitive coverage** | segmentation ✓, predictive_power ✓ |
| **Question coverage** | 6/6 correctly mapped |
| **Known defects** | None — all mappings correct |
| **Evidence gap** | Entity_id enrichment only matches 4/6983 records. Need more executed trades with market-state correlation |

### DECISION HOUSE

| Dimension | Status |
|---|---|
| **Can currently prove** | Score is NOT predictive of outcome (D-001). Decision funnel shape (3789 opportunity, 2586 risk, 991 strategy rejections). Risk gates target appropriate decisions (D-007) |
| **Cannot prove** | EV calibration (p_success coverage 3%). Threshold optimality (needs score bucketing). Where in funnel edge is lost vs protected |
| **Field coverage** | score 100%, terminal_reason 97%, opportunity_quality 60%, regime 60%, p_success 3% |
| **Primitive coverage** | predictive_power ✓, calibration ✓, comparison ✓ |
| **Question coverage** | 2/7 correct. 3 need parameter fix. 1 needs primitive change. 1 needs missing evidence |
| **Known defects** | D-003, D-005, D-006 wrong params. D-004 wrong primitive |
| **Evidence gap** | p_success extremely sparse. Rejection distribution needs segmentation not numeric distribution |

### STRATEGY HOUSE

| Dimension | Status |
|---|---|
| **Can currently prove** | Strategy family expectancy (S-001). Pattern expectancy (S-002). Strategy confidence calibration (S-003) |
| **Cannot prove** | What characterises rejected strategies (no outcomes for rejected pop) |
| **Field coverage** | family 100%, pattern 100%, confidence 54%, conditions_met 100% |
| **Primitive coverage** | segmentation ✓, calibration ✓ |
| **Question coverage** | 3/4 correct |
| **Known defects** | S-004 structurally unanswerable |
| **Evidence gap** | S-004 needs reformulation (rejected strategies cannot have outcomes) |

### TRADE / EXECUTION HOUSE

| Dimension | Status |
|---|---|
| **Can currently prove** | System expectancy (-0.18R). Win/loss shape. Duration vs outcome. R:R effectiveness. Session segmentation |
| **Cannot prove** | Exit reason distribution (wrong primitive). Stop placement effectiveness (needs specific primitive) |
| **Field coverage** | ALL core fields 100% (r_multiple, exit_reason, duration, SL, TP, session, regime, pattern, score) |
| **Primitive coverage** | expectancy ✓, distribution ✓, degradation ✓, predictive_power ✓, comparison ✓, segmentation ✓ |
| **Question coverage** | 5/10 correct. 2 wrong primitive. 2 data-limited. 1 blocked |
| **Known defects** | E-003 needs segmentation not distribution. E-007 needs SL-specific analysis |
| **Evidence gap** | Small sample (94 trades). Pattern degradation weak over short period |

---

## ORDERED REPAIR LIST

### Priority 1 — Fix parameter mappings (6 questions, straightforward)

| # | Question | Fix | File | Test |
|---|---|---|---|---|
| 1 | D-005 | Add `"D-005": {"feature_field": "opportunity_quality", "outcome_field": "r_multiple"}` | primitive_mapping.py | Run D-005, verify sample_size > 0 and monotonic/spread reported |
| 2 | DS-001 | Add `"DS-001": {"predicted_field": "confidence", "outcome_field": "r_multiple"}` | primitive_mapping.py | Run DS-001, verify calibration buckets |
| 3 | DS-002 | Add `"DS-002": {"feature_field": "conditions_met", "outcome_field": "r_multiple"}` | primitive_mapping.py | Run DS-002, verify predictive_power output |
| 4 | D-003 | Need score-bucket dimension. Workaround: create a derived `score_bucket` field in Decision Universe or accept that D-003 cannot currently segment by threshold | decision_universe.py OR question redesign | Verify segmentation produces meaningful threshold buckets |
| 5 | D-006 | Segment by `opportunity_state` (categorical, 60% coverage): `"D-006": {"dimensions": ["opportunity_state"], "metric_field": "r_multiple"}` | primitive_mapping.py | Run D-006, verify segments |
| 6 | ED-001 | Currently intractable without a comparison-of-predicted-vs-realised primitive. Accept as WRONG PRIMITIVE for now | — | — |

### Priority 2 — Fix wrong primitives (5 questions, need primitive or analysis_type change)

| # | Question | Fix | File | Test |
|---|---|---|---|---|
| 7 | E-003 | Change analysis_type from DISTRIBUTION to SEGMENTATION in question bank: `analysis_type=AnalysisType.SEGMENTATION`. Add `"E-003": {"dimensions": ["exit_reason"], "metric_field": "r_multiple"}` | question_bank.py + primitive_mapping.py | Run E-003, verify exit_reason segments with counts |
| 8 | D-004 | Same fix: change analysis_type to SEGMENTATION. Add `"D-004": {"dimensions": ["terminal_reason"], "metric_field": "r_multiple"}` | question_bank.py + primitive_mapping.py | Run D-004, verify rejection stages |
| 9 | DM-003 | Same fix: analysis_type→SEGMENTATION. Params already set to dimensions=["regime"]. But metric should measure action distribution, not r_multiple. This is fundamentally a frequency/rate question not a numeric metric question | question_bank.py | Verify regime×action counts |
| 10 | MS-003 | Same: asks about "availability" which is a count/coverage question. Needs segmentation with action-based metric or custom approach | question_bank.py | — |
| 11 | E-007 | Hypothesis asks "is SL too tight?" but expectancy+distribution doesn't test this. Needs predictive_power with feature_field correlating SL distance to outcome | question_bank.py + primitive_mapping.py | Verify SL-distance→R relationship |

### Priority 3 — Accept structural limitations

| # | Question | Status | Action |
|---|---|---|---|
| 12 | S-004 | HYPOTHESIS CANNOT BE ANSWERED | Reformulate: "What characterises rejected opportunities?" using decision behaviour fields WITHOUT r_multiple |
| 13 | ED-002 | HYPOTHESIS CANNOT BE ANSWERED | Needs shadow trade data. Park until shadow system provides counterfactual outcomes |
| 14 | E-006 | BLOCKED | Needs 100+ trades. Will resolve naturally with more data |
| 15 | All Market questions | DATA-LIMITED | Will resolve as more trades accumulate (need ~80 → Market Universe enrichment) |

---

## "FOUR HOUSES READY" CHECKLIST

Before moving to Risk, ALL of these must be true:

- [ ] Every parameter mapping in QUESTION_PARAMETERS is verified against primitive field-type requirements
- [ ] No string field is passed to a numeric primitive (E-003, D-004 fixed)
- [ ] Every segmentation question has explicit `dimensions` parameter (not defaulting to [symbol])
- [ ] Every predictive_power question has explicit `feature_field` matching its hypothesis
- [ ] Every calibration question has explicit `predicted_field` matching its hypothesis
- [ ] D-005, DS-001, DS-002 parameter fixes applied
- [ ] E-003 and D-004 analysis_type changed to SEGMENTATION
- [ ] D-007 metric_field confirmed as appropriate (score comparison IS informative)
- [ ] 19+ questions produce VALID AND READY results
- [ ] All data-limited questions (12) are correctly classified and not producing false verdicts
- [ ] CLI ANALYTICAL SAMPLE reporting shows primitive sample_size not population size (already fixed)
- [ ] No question reports HIGH confidence with 0 analytical evidence (already fixed)
- [ ] S-004 and ED-002 explicitly marked as structurally unanswerable
- [ ] Full 45-question bank run produces no ERROR status (only COMPLETE, INCONCLUSIVE, or BLOCKED)
- [ ] At least one question per house produces independently verified evidence
