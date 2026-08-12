# Parameter-to-Hypothesis Integrity Audit — All 45 Questions

## A. Full Audit Table

| ID | Universe | Hypothesis (short) | Primitive | Params supplied | Actual fields measured | Verdict | Reason |
|---|---|---|---|---|---|---|---|
| E-001 | EXEC | System has positive expectancy? | expectancy | None (defaults) | r_multiple | **CORRECT** | expectancy uses r_multiple directly — matches hypothesis |
| E-002 | EXEC | Win/loss distribution shape? | distribution+expectancy | None | field=r_multiple | **CORRECT** | distribution of R is what the question asks |
| E-003 | EXEC | Exit reason percentages? | distribution+expectancy | None | field=r_multiple | **INCOMPLETE** | Question asks about exit_reason distribution but primitive analyses r_multiple distribution. Exit counts visible in population but not the primitive's output. Should set field=exit_reason for the distribution primitive |
| E-004 | EXEC | Session execution quality? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Segments by symbol (default) instead of session. Should be dimensions=["session"] |
| E-005 | EXEC | Probability of ruin? | expectancy+distribution | None | r_multiple | **CORRECT** | Expectancy + distribution of R gives the inputs for ruin estimation |
| E-006 | EXEC | Edge survives walk-forward? | degradation | None | r_multiple, entry_time | **CORRECT** | Degradation primitive splits by time — appropriate |
| E-007 | EXEC | Stop placement reducing expectancy? | expectancy+distribution | None | r_multiple | **INCOMPLETE** | Question asks about stop_loss distance vs outcome. Simulation primitive would need stop_loss and entry_price. Current primitives just compute expectancy which doesn't specifically test SL effectiveness |
| E-008 | EXEC | Patterns degrading over time? | degradation | None | r_multiple, entry_time | **CORRECT** | Degradation by time period — matches hypothesis |
| E-009 | EXEC | Duration predicts outcome? | predictive_power | feature_field=duration_seconds | duration_seconds→r_multiple | **CORRECT** | Tests if longer/shorter trades have different outcomes |
| E-010 | EXEC | R:R ratios achieved vs intended? | comparison | group_field=exit_reason | exit_reason groups, metric=r_multiple | **INCORRECT** | Question asks about intended R:R vs achieved R:R. Grouping by exit_reason shows SL vs TP outcomes but doesn't compare INTENDED R:R ratios. Should use a computed intended_rr field or bucket by rr_ratio if available |
| D-001 | DEC | Score predicts trade outcome? | predictive_power | None (default score) | score→r_multiple | **CORRECT** | Default feature_field=score is exactly what D-001 tests |
| D-002 | DEC | EV estimate calibrated? | calibration | None (default p_success) | p_success→r_multiple | **CORRECT** | Calibration checks if p_success predicts win rate — correct |
| D-003 | DEC | Score thresholds optimal? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Should segment by score buckets to show expectancy at different thresholds. Needs dimensions=["score"] or a custom score-bucket field |
| D-004 | DEC | Where are trades rejected? | distribution+expectancy | field=terminal_reason | terminal_reason distribution | **CORRECT** | Distribution of terminal_reason directly answers "where rejected" |
| D-005 | DEC | Opportunity quality predicts outcomes? | predictive_power | None (default score) | score→r_multiple | **INCORRECT** | Question is about opportunity_quality predicting r_multiple, NOT score. Should be feature_field=opportunity_quality |
| D-006 | DEC | What characterises false positives? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Should segment by opportunity_quality or score bucket to characterise failures. Default symbol segmentation doesn't answer the hypothesis |
| D-007 | DEC | Risk gates improve survival? | comparison+expectancy | group_field=risk_approved, metric=score | risk_approved groups, score as metric | **INCOMPLETE** | Groups by risk_approved is correct direction, but metric should be r_multiple (to compare outcomes of approved vs rejected), not score. Also: risk-rejected trades don't have r_multiple, making this fundamentally a counterfactual question |
| M-001 | MKT | Regime predicts outcomes? | segmentation+expectancy | dimensions=["regime"] | regime segments, metric=r_multiple | **CORRECT** | Segments expectancy by regime — exactly what's asked |
| M-002 | MKT | HTF alignment predicts success? | predictive_power | feature_field=htf_alignment_strength | htf_alignment_strength→r_multiple | **CORRECT** | Tests if alignment strength predicts outcome |
| M-003 | MKT | Regime+volatility improves prediction? | segmentation+expectancy | dimensions=["regime","volatility_state"] | regime×volatility segments, metric=r_multiple | **CORRECT** | Tests combined segmentation — matches hypothesis |
| M-004 | MKT | H1 clarity predicts outcomes? | predictive_power | feature_field=h1_structural_clarity | h1_structural_clarity→r_multiple | **CORRECT** | Tests if clarity predicts outcome |
| M-005 | MKT | Location predicts outcomes? | segmentation+expectancy | dimensions=["location_type"] | location_type segments, metric=r_multiple | **CORRECT** | Segments by location — matches hypothesis |
| M-006 | MKT | Session affects expectancy? | segmentation+expectancy | dimensions=["session"] | session segments, metric=r_multiple | **CORRECT** | Segments by session — matches hypothesis |
| S-001 | STRAT | Strategy family expectancy? | segmentation+expectancy | dimensions=["family"] | family segments, metric=r_multiple | **CORRECT** | Segments by strategy family — matches hypothesis |
| S-002 | STRAT | Pattern expectancy? | segmentation+expectancy | dimensions=["pattern"] | pattern segments, metric=r_multiple | **CORRECT** | Segments by pattern — matches hypothesis |
| S-003 | STRAT | Strategy confidence calibrated? | calibration | predicted_field=confidence | confidence→r_multiple | **CORRECT** | Tests if confidence predicts win rate |
| S-004 | STRAT | Strategy rejection patterns? | distribution+expectancy | None | field=r_multiple | **INCORRECT** | Question asks "what characterises gaps in strategy matching" but primitive analyses r_multiple distribution. Should analyse strategy_family or evaluation_status distribution. Also: STRATEGY_REJECTED population has 0 outcomes by definition |
| ED-001 | EXEC+DEC | Edge leakage decision→execution? | comparison | None (default group=regime) | regime groups, metric=r_multiple | **INCORRECT** | Question asks about leakage between decision EV and realised outcome. Should compare predicted EV vs realised r_multiple, not segment by regime |
| ED-002 | EXEC+DEC | Missed opportunity cost? | comparison+expectancy | None (default group=regime) | regime groups | **INCORRECT** | Question asks about counterfactual outcomes of rejected trades. Population resolves to 0 records (wrong population source). Fundamentally needs shadow/counterfactual data |
| ED-003 | EXEC+DEC | Position sizing effectiveness? | expectancy+distribution | None | r_multiple | **CORRECT** | Expectancy analysis is valid for evaluating current sizing |
| EM-001 | EXEC+MKT | Regime-conditioned expectancy? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Should segment by regime. Currently defaults to symbol. Needs dimensions=["regime"] |
| EM-002 | EXEC+MKT | Market drift detection? | degradation | None | r_multiple, entry_time | **CORRECT** | Degradation over time — matches hypothesis |
| ES-001 | EXEC+STRAT | Execution quality by strategy? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Should segment by family/strategy. Needs dimensions=["family"] |
| DM-001 | DEC+MKT | Decision quality under regime? | segmentation+expectancy | dimensions=["regime"] | regime segments, metric=r_multiple | **CORRECT** | Segments by regime — matches hypothesis |
| DM-002 | DEC+MKT | Opportunity detection vs market state? | comparison | None (default group=regime) | regime groups, metric=r_multiple | **CORRECT** | Comparison by regime is appropriate |
| DM-003 | DEC+MKT | Rejection rate by market state? | segmentation+expectancy | dimensions=["regime"] | regime segments, metric=r_multiple | **INCOMPLETE** | Segments by regime but metric should be rejection_rate or action distribution, not r_multiple. Question asks about NO_TRADE rate by regime |
| DS-001 | DEC+STRAT | Strategy confidence calibrated? | calibration | None (default p_success) | p_success→r_multiple | **INCORRECT** | Same as S-003 — should use predicted_field=confidence, not p_success |
| DS-002 | DEC+STRAT | Strategy conditions vs outcome? | predictive_power | None (default score) | score→r_multiple | **INCORRECT** | Question tests if conditions_met predicts outcome. Should be feature_field=conditions_met |
| MS-001 | MKT+STRAT | Strategy×regime interaction? | segmentation+expectancy | dimensions=["regime","family"] | regime×family segments | **CORRECT** | Two-dimensional segmentation matches hypothesis |
| MS-002 | MKT+STRAT | Pattern×market context? | segmentation+expectancy | dimensions=["regime","pattern"] | regime×pattern segments | **CORRECT** | Two-dimensional segmentation matches hypothesis |
| MS-003 | MKT+STRAT | Strategy availability by market state? | distribution+expectancy | None | field=r_multiple | **INCORRECT** | Question asks about strategy coverage/availability, not r_multiple distribution. Should analyse family or evaluation_status distribution across market states |
| EDM-001 | EXEC+DEC+MKT | Complete trade lifecycle? | comparison | None (default group=regime) | regime groups | **INCOMPLETE** | Question asks "where does pipeline add/lose value" — regime comparison is partial but not wrong. Needs multi-factor analysis |
| DMS-001 | DEC+MKT+STRAT | Decision quality×strategy×market? | segmentation+expectancy | None | dimensions=[symbol] | **INCORRECT** | Should segment by regime×family or similar. Defaults to symbol |
| EDMS-001 | ALL | Full system attribution? | predictive_power | None (default score) | score→r_multiple | **CORRECT** | Testing what predicts outcome across full system — score is the aggregate predictor |
| EDMS-002 | ALL | Promotion impact? | expectancy+distribution | None | r_multiple | **CORRECT** | Expectancy analysis for promotion estimation |

## B. Questions Requiring Code/Mapping Changes

| ID | Issue | Required Change |
|---|---|---|
| **E-003** | Distribution analyses r_multiple instead of exit_reason | Add `"E-003": {"field": "exit_reason"}` |
| **E-004** | Segments by symbol instead of session | Add `"E-004": {"dimensions": ["session"], "metric_field": "r_multiple"}` |
| **E-010** | Comparison by exit_reason doesn't answer R:R hypothesis | Change to `"E-010": {"group_field": "exit_reason", "metric_field": "r_multiple"}` — actually this IS a reasonable proxy. Keep but note limitation |
| **D-003** | Segments by symbol instead of score bucket | Needs a derived field or custom bucketing. Cannot trivially fix with params alone |
| **D-005** | Tests score→r_multiple instead of opportunity_quality→r_multiple | Add `"D-005": {"feature_field": "opportunity_quality", "outcome_field": "r_multiple"}` |
| **D-006** | Segments by symbol instead of quality/outcome | Add `"D-006": {"dimensions": ["opportunity_quality"], "metric_field": "r_multiple"}` — but opportunity_quality is numeric not categorical. Needs bucketing |
| **D-007** | metric_field=score should be r_multiple | Change to `"D-007": {"group_field": "risk_approved", "metric_field": "r_multiple"}` |
| **DS-001** | Uses p_success instead of confidence | Add `"DS-001": {"predicted_field": "confidence", "outcome_field": "r_multiple"}` |
| **DS-002** | Tests score instead of conditions_met | Add `"DS-002": {"feature_field": "conditions_met", "outcome_field": "r_multiple"}` |
| **EM-001** | Segments by symbol instead of regime | Add `"EM-001": {"dimensions": ["regime"], "metric_field": "r_multiple"}` |
| **ES-001** | Segments by symbol instead of family | Add `"ES-001": {"dimensions": ["family"], "metric_field": "r_multiple"}` |
| **DM-003** | metric=r_multiple doesn't answer "rejection rate by regime" | Should use metric_field for action counts, or needs custom approach |
| **DMS-001** | Segments by symbol instead of regime×family | Add `"DMS-001": {"dimensions": ["regime", "family"], "metric_field": "r_multiple"}` |
| **MS-003** | Distribution of r_multiple instead of strategy availability | Needs different primitive or custom approach |
| **S-004** | Distribution of r_multiple on a zero-outcome population | Needs different approach — this population has no outcomes |
| **ED-001** | Comparison by regime doesn't answer "edge leakage" | Needs comparison of predicted EV vs realised R |
| **ED-002** | Empty population + counterfactual hypothesis | Needs shadow data — fundamentally cannot be answered currently |

## C. Questions Where the QUESTION DEFINITION Needs Changing

| ID | Issue |
|---|---|
| **D-003** | Hypothesis asks about score thresholds but no score-bucket field exists. Needs either a derived field or a different primitive that can bucket a continuous variable |
| **D-006** | Hypothesis asks about "false positive characteristics" but segmentation by a continuous quality score isn't meaningful without bucketing |
| **D-007** | Hypothesis is counterfactual ("would rejected trades have succeeded?") which cannot be answered without shadow outcomes for rejected trades |
| **DM-003** | Asks "rejection rate by regime" but segmentation+expectancy primitive measures r_multiple by regime, not rejection rates |
| **MS-003** | Asks "strategy availability" which is a coverage question, not an expectancy/outcome question |
| **S-004** | Asks about "strategy rejection patterns" on a population that by definition has zero outcomes |
| **ED-001** | Asks about "edge leakage" which requires comparing predicted vs realised — not a simple group comparison |
| **ED-002** | Asks about "missed opportunity cost" which is counterfactual — needs shadow trade data |

## D. Questions Already Correct — Do NOT Touch

| ID | Title | Why correct |
|---|---|---|
| E-001 | System Expectancy | expectancy on r_multiple — verified |
| E-002 | Win/Loss Distribution | distribution of r_multiple — correct |
| E-005 | Probability of Ruin | expectancy+distribution — appropriate |
| E-006 | Walk-Forward (BLOCKED) | degradation — correct when data available |
| E-008 | Pattern Degradation | degradation over time — correct |
| E-009 | Duration vs Outcome | feature=duration_seconds — verified |
| D-001 | Score Predictive Power | feature=score → r_multiple — verified |
| D-002 | EV Calibration | predicted=p_success — correct |
| D-004 | Rejection Stage | field=terminal_reason — verified |
| M-001 | Regime→Outcomes | dimensions=["regime"] — correct |
| M-002 | HTF Alignment | feature=htf_alignment_strength — verified |
| M-003 | Volatility Impact | dimensions=["regime","volatility_state"] — correct |
| M-004 | Structure Clarity | feature=h1_structural_clarity — verified |
| M-005 | Location Quality | dimensions=["location_type"] — correct |
| M-006 | Session Variation | dimensions=["session"] — correct |
| S-001 | Strategy Family | dimensions=["family"] — correct |
| S-002 | Pattern Expectancy | dimensions=["pattern"] — correct |
| S-003 | Strategy Calibration | predicted=confidence — verified |
| MS-001 | Strategy×Regime | dimensions=["regime","family"] — correct |
| MS-002 | Pattern×Market | dimensions=["regime","pattern"] — correct |
| DM-001 | Decision Under Regime | dimensions=["regime"] — correct |
| DM-002 | Opportunity vs Market | comparison by regime — appropriate |
| EM-002 | Market Drift | degradation — correct |
| ED-003 | Position Sizing | expectancy — appropriate |
| EDMS-001 | System Attribution | predictive_power(score) — appropriate |
| EDMS-002 | Promotion Impact | expectancy — appropriate |
| EDM-001 | Lifecycle Analysis | comparison — partial but acceptable |

**26 questions correct. Do NOT touch.**

## E. Justification for Each Proposed Change

| ID | Change | Evidence |
|---|---|---|
| E-003 | field=exit_reason | Hypothesis: "What % exit via SL vs TP?" — that's exit_reason distribution |
| E-004 | dimensions=["session"] | Hypothesis: "Which sessions produce best execution?" — segment by session |
| D-005 | feature_field=opportunity_quality | Hypothesis: "Does opportunity quality predict outcomes?" — test quality→R |
| D-007 | metric_field=r_multiple (not score) | Hypothesis: "Do risk gates improve expectancy?" — compare R for approved vs rejected |
| DS-001 | predicted_field=confidence | Same question as S-003 but cross-domain — same fix applies |
| DS-002 | feature_field=conditions_met | Hypothesis: "Do conditions met predict outcome?" — test conditions→R |
| EM-001 | dimensions=["regime"] | Hypothesis: "Regime-conditioned expectancy" — segment by regime |
| ES-001 | dimensions=["family"] | Hypothesis: "Execution quality by strategy" — segment by family |
| DMS-001 | dimensions=["regime","family"] | Hypothesis: "Decision×strategy×market" — multi-dimensional segment |

## F. Cases Where Current QUESTION_PARAMETERS Would Accidentally Change Meaning

| ID | Risk | Assessment |
|---|---|---|
| E-010 | group_field=exit_reason substitutes for R:R analysis | **Acceptable proxy** — SL/TP hit rate reveals R:R effectiveness indirectly. Not perfect but not wrong |
| D-007 | group_field=risk_approved with metric=score | **WRONG** — should be metric=r_multiple. Current mapping measures score distribution by approval status, not whether risk gates improve outcomes |
| DM-003 | dimensions=["regime"] with metric=r_multiple | **INCOMPLETE** — measures expectancy by regime on EXECUTE decisions, but question asks about rejection RATE which is an action-distribution question |

## Summary

- **26 questions: CORRECT** — do not touch
- **9 questions: need parameter fix** (E-003, E-004, D-005, D-007, DS-001, DS-002, EM-001, ES-001, DMS-001)
- **8 questions: need question/primitive redesign** (D-003, D-006, D-007(partial), DM-003, MS-003, S-004, ED-001, ED-002)
- **1 question: BLOCKED** (E-006)
- **1 question: acceptable proxy** (E-010)
