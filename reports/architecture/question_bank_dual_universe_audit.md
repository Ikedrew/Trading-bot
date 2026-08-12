# QUESTION BANK — DUAL-UNIVERSE PLACEMENT AUDIT

**Date:** 2026-07-27
**Scope:** All 45 canonical questions from `question_bank.py`
**Purpose:** Determine natural placement in the Live/Shadow dual-universe architecture
**Status:** READ-ONLY DESIGN — no questions modified

---

## CLASSIFICATION FRAMEWORK

Each question receives exactly one classification:

| Code | Meaning |
|------|---------|
| **LIVE_ONLY** | Question is inherently about realised outcomes or broker execution. Shadow data cannot answer it. |
| **SHADOW_ONLY** | Question is inherently about counterfactual outcomes of rejected/unexecuted opportunities. Live data cannot answer it. |
| **LIVE_PRIMARY + SHADOW_PAIR** | Question is valid on Live (existing). A parallel Shadow variant asking the same analytical question against counterfactual data would add significant value with larger sample. |
| **CROSS_LIVE_SHADOW** | Question's intent fundamentally requires comparing what happened vs what would have happened. Needs both worlds. |
| **NEEDS_REFORMULATION** | Question's current universe/metric combination is structurally invalid or semantically confused. Intent is clear but framing must change. |

Additional metadata per question:
- **Shadow variant ID** (proposed, if applicable)
- **Shadow population** (which shadow population it would operate on)
- **Sample size impact** (estimated improvement from shadow access)
- **Human-question compatibility** (can a human still add new questions using this pattern?)

---

## EXECUTION-PRIMARY (E-001 through E-010)

### E-001 — System Expectancy

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "What is the realised expectancy of the production pipeline?" |
| Live validity | VALID — measures actual broker-confirmed R on 94 trades |
| Shadow variant | SE-001: "What is the counterfactual expectancy of ALL detected opportunities?" |
| Shadow population | ALL_SHADOW_OUTCOMES |
| Shadow value | Measures the total opportunity pool (thousands vs 94). Answers: "How much edge exists in signals before filtering?" |
| Sample impact | 94 → thousands |
| Reason for pairing | Live tells what we captured. Shadow tells what was available. Together they reveal capture efficiency. |

### E-002 — Win/Loss Distribution Shape

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "What is the win/loss distribution shape?" |
| Live validity | VALID — realised distribution of executed trades |
| Shadow variant | SE-002: "What is the counterfactual win/loss distribution shape of all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES |
| Shadow value | Reveals whether the opportunity pool distribution differs from what we select. If shadow shows better shape, we may be selecting wrong. |
| Sample impact | 94 → thousands |

### E-003 — Exit Reason Distribution

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "What % exit via SL vs TP vs time exit?" |
| Live validity | VALID — actual exit events from broker |
| Shadow variant | SE-003: "What % of shadow trades exit via SL vs TP vs timeout?" |
| Shadow population | ALL_SHADOW_OUTCOMES |
| Shadow value | Compares exit distribution across Live vs Shadow. If shadow has more timeouts, horizon geometry may be wrong. |
| Sample impact | 94 → thousands |

### E-004 — Execution Quality by Session

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Which sessions produce best execution quality — lowest slippage, fastest fills?" |
| Live validity | VALID — broker execution quality is live-only |
| Shadow variant | NOT APPLICABLE |
| Reason | Slippage, fill speed, and broker rejections are properties of real execution. Shadow has no broker. |

### E-005 — Probability of Ruin

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "What is the probability of catastrophic drawdown at current sizing?" |
| Live validity | VALID — survival analysis requires realised variance |
| Shadow variant | NOT APPLICABLE |
| Reason | Ruin probability is about actual account survival with real position sizes and real outcomes. Shadow R does not map to account risk. |

### E-006 — Out-of-Sample Edge Validation

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Does measured edge survive walk-forward?" |
| Live validity | VALID — requires realised outcomes on unseen periods |
| Shadow variant | NOT APPLICABLE |
| Reason | Overfitting detection requires holdout sets of REALISED data. Shadow data is model-generated, not suitable for overfitting validation. |

### E-007 — Stop Placement Effectiveness

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Is SL placement too tight or too wide?" |
| Live validity | VALID — SL effectiveness on actual trades |
| Shadow variant | SE-007: "What SL distance produces best counterfactual expectancy across ALL opportunities?" |
| Shadow population | ALL_SHADOW_OUTCOMES (analyse by stop_loss distance in risk_config_snapshot) |
| Shadow value | Shadow has thousands of observations with varying SL distances across horizons. Can test SL sensitivity without execution risk. |
| Sample impact | 94 → thousands |
| Note | Shadow shadow has FIXED SL per horizon — less variability than Live. Still informative for horizon-level analysis. |

### E-008 — Pattern Degradation Over Time

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Are patterns losing edge over time?" |
| Live validity | VALID (but BLOCKED — needs 100+ samples) |
| Shadow variant | SE-008: "Are patterns losing counterfactual edge over time?" |
| Shadow population | ALL_SHADOW_OUTCOMES (grouped by pattern, ordered by timestamp) |
| Shadow value | Shadow accumulates faster — can detect degradation earlier. Also covers patterns that are detected but rarely executed. |
| Sample impact | 94 (insufficient for time-series) → thousands (adequate for temporal analysis) |

### E-009 — Trade Duration vs Outcome

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does trade duration affect expectancy?" |
| Live validity | VALID — real duration of broker-managed trades |
| Shadow variant | SE-009: "Does bars_held predict counterfactual outcome?" |
| Shadow population | ALL_SHADOW_OUTCOMES |
| Shadow value | Tests whether quick shadow exits (TP hit in few bars) produce better R than slow ones. Larger sample for temporal pattern detection. |
| Note | Shadow uses `bars_held` (not `duration_seconds`). Same concept, different unit. |

### E-010 — R:R Ratio Effectiveness

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "What R:R ratios are achieved vs intended?" |
| Live validity | VALID — actual realised R:R |
| Shadow variant | SE-010: "Which intended R:R ratio produces best counterfactual expectancy?" |
| Shadow population | ALL_SHADOW_OUTCOMES (analyse by risk_config_snapshot.reward_risk_ratio) |
| Shadow value | Each horizon shadow has different R:R by construction. Natural experiment in R:R sensitivity. |
| Sample impact | 94 → thousands |

---

## DECISION-PRIMARY (D-001 through D-007)

### D-001 — Score Predictive Power

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does score predict trade outcome? Which components have real predictive value?" |
| Live validity | VALID — score → realised R correlation on EXECUTE_DECISIONS (94 with r_multiple) |
| Shadow variant | SD-001: "Does score predict counterfactual outcome across ALL opportunities?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Decision (provides score for each shadow trade) |
| Shadow value | Tests score predictive power on the FULL opportunity set (executed + rejected). Much larger sample. If score predicts shadow R even for rejected signals, it validates the scoring model broadly. |
| Sample impact | 94 → thousands |
| Critical distinction | Live: "Score predicts outcome among things we chose to trade." Shadow: "Score predicts outcome across everything we evaluated." |

### D-002 — EV Calibration

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Is predicted win probability calibrated to actual outcomes?" |
| Live validity | VALID — calibration requires actual win/loss truth |
| Shadow variant | NOT APPLICABLE |
| Reason | Calibration means "does predicted probability match actual frequency?" Shadow R is model-generated — calibrating a model against its own simulation output proves nothing about real-world calibration. |
| Note | A shadow EV calibration could measure "does p_success predict shadow win rate?" but this validates the shadow model, not the live system. If wanted in future, create as a separate SHADOW_ONLY question (model validation), not a pair. |

### D-003 — Decision Threshold Effectiveness

| Property | Value |
|----------|-------|
| Classification | **CROSS_LIVE_SHADOW** |
| Current intent | "Are score thresholds set optimally?" |
| Live validity | PARTIAL — only tests threshold on the executed subset (above threshold) |
| Cross-side formulation | "If we moved the threshold UP, what live trades would we lose? If we moved it DOWN, what shadow outcomes would we gain?" |
| Why cross-side | A threshold question inherently asks: "what's above vs what's below." Above = executed (Live R). Below = rejected (Shadow R). Full answer requires BOTH. |
| Shadow population | SHADOW_FROM_NO_TRADE filtered by score ranges |
| Implementation | Two populations: (1) Live high-score trades with realised R, (2) Shadow low-score opportunities with counterfactual R. Optimal threshold maximises combined expected R. |

### D-004 — Rejection Stage Analysis

| Property | Value |
|----------|-------|
| Classification | **NEEDS_REFORMULATION** |
| Current intent | "Where are trades rejected? Which stage removes edge vs protects capital?" |
| Live validity | INVALID for the "edge removal" part. Valid for "where rejected" (descriptive). |
| Problem | Population is NO_TRADE_DECISIONS. Metric requires `r_multiple`. Only 1 record has r_multiple. The question conflates "where are things rejected" (Live descriptive) with "what was the cost of rejection" (Shadow counterfactual). |
| Reformulation | Split into THREE questions: |

**D-004a (LIVE_ONLY — descriptive):**
- "Where in the pipeline are opportunities rejected? What is the rejection funnel shape?"
- Population: NO_TRADE_DECISIONS
- Metrics: count, %, distribution per terminal_stage
- Analysis: SEGMENTATION by terminal_stage (NO r_multiple needed)
- Value: Shows pipeline bottlenecks

**D-004b (SHADOW_ONLY — counterfactual value):**
- "What counterfactual expectancy do rejected opportunities produce, by rejection stage?"
- Population: SHADOW_FROM_NO_TRADE joined to Decision (provides terminal_stage)
- Metrics: shadow r_multiple segmented by terminal_stage
- Analysis: SEGMENTATION by terminal_stage, metric = shadow r_multiple
- Value: Identifies which stages remove the most counterfactual edge

**D-004c (CROSS_LIVE_SHADOW — decision quality):**
- "Which rejection stages correctly protect capital and which incorrectly remove profitable opportunities?"
- Population: Decisions with shadow outcomes classified as {correctly_rejected, incorrectly_rejected}
- Metrics: per stage: count(shadow R < 0) = "correct protection", count(shadow R > 0) = "missed opportunity"
- Analysis: Cross-side classification per stage
- Value: Directly actionable — relax stages that reject winners

### D-005 — Opportunity Quality Predictive Value

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does the 4-dimension quality score predict trade outcomes?" |
| Live validity | VALID — quality → realised R on executed trades |
| Shadow variant | SD-005: "Does opportunity quality predict counterfactual outcome across ALL opportunities?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Decision (provides quality scores) |
| Shadow value | Tests quality prediction on the full opportunity pool. If quality predicts shadow R even for rejected signals, it validates the scoring architecture beyond the narrow executed set. |
| Sample impact | 94 → thousands |

### D-006 — Opportunity Failure Characterisation

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "What characterises opportunities that look good but fail?" |
| Live validity | VALID — characterises actual false positives (high-quality + negative R) |
| Shadow variant | NOT APPLICABLE as a pair |
| Reason | "Failure characterisation" specifically means "things we actually traded that lost." Shadow failures are model failures, not trading failures. A shadow variant could ask "which shadow opportunities looked good but failed counterfactually?" but this has different meaning (model failure vs execution failure). |
| Note | If shadow analysis of false positives is wanted, create as new SHADOW_ONLY question: "What characterises high-quality shadows that fail counterfactually?" |

### D-007 — Risk Gate Value

| Property | Value |
|----------|-------|
| Classification | **CROSS_LIVE_SHADOW** |
| Current intent | "Does the risk layer improve survival and expectancy, or filter profitable opportunities?" |
| Live validity | PARTIAL — can measure what risk-approved trades produced, but cannot measure what risk-blocked trades WOULD have produced |
| Cross-side formulation | "Risk-approved trades → Live R (what we gained). Risk-blocked opportunities → Shadow R (what we would have gained/lost)." |
| Why cross-side | The question explicitly asks about the VALUE of blocking. To determine value, you need the counterfactual: what would have happened if the block hadn't occurred? That's Shadow data. |
| Shadow population | SHADOW_FROM_NO_TRADE where Decision.terminal_reason contains "risk" |
| Implementation | Compare: (1) Live R of risk-approved trades, (2) Shadow R of risk-blocked opportunities. If shadow R of blocked signals is negative, risk gates are working. If positive, they're destroying edge. |

---

## MARKET-PRIMARY (M-001 through M-006)

### M-001 — Regime Predicts Outcomes

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does H4 regime predict trade R-multiple?" |
| Live validity | VALID — regime → realised R on executed trades |
| Shadow variant | SM-001: "Does regime predict counterfactual R across ALL opportunities?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (provides regime) |
| Shadow value | Tests regime prediction on the full signal population. If TRANSITIONAL shows negative shadow R across all signals, the current regime filtering is justified. If positive, it's destroying edge. |
| Sample impact | 94 → thousands |
| Critical insight | This is exactly what EM-001's existing experiment tested (exclude TRANSITIONAL). Shadow variant provides the population needed to answer it properly. |

### M-002 — HTF Alignment Value

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does higher-timeframe alignment predict trade success?" |
| Live validity | VALID — HTF alignment → realised R |
| Shadow variant | SM-002: "Does HTF alignment predict counterfactual R across all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (provides htf_alignment_strength) |
| Shadow value | Larger sample tests whether HTF alignment is genuinely predictive or just correlated with other factors in the executed subset. |
| Sample impact | 94 → thousands |

### M-003 — Volatility State Impact

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does volatility state affect expectancy?" |
| Live validity | VALID — volatility → realised R |
| Shadow variant | SM-003: "Does volatility predict counterfactual R?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (provides volatility_state) |
| Shadow value | Tests volatility impact on the full opportunity set. |

### M-004 — Market Structure Clarity

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does H1 structural clarity predict better outcomes?" |
| Live validity | VALID — clarity → realised R |
| Shadow variant | SM-004: "Does structural clarity predict counterfactual R?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (provides h1_structural_clarity) |
| Shadow value | Tests whether clarity threshold should gate ALL opportunity detection, not just execution. |

### M-005 — Location Quality Impact

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does price location predict outcomes?" |
| Live validity | VALID — location → realised R |
| Shadow variant | SM-005: "Does location predict counterfactual R?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (provides location_type, zone_quality) |
| Shadow value | Tests location's predictive value on the full signal set. If location predicts shadow R, it validates adding location weight. |

### M-006 — Session Edge Variation

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does expectancy vary across sessions?" |
| Live validity | VALID — session → realised R |
| Shadow variant | SM-006: "Does session predict counterfactual R across all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES (derive session from timestamp_decision_utc) |
| Shadow value | Tests session effect on the full opportunity pool. If a session shows negative shadow R across all signals, avoid it entirely. |

---

## STRATEGY-PRIMARY (S-001 through S-004)

### S-001 — Strategy Family Expectancy

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Which strategy families produce positive expectancy?" |
| Live validity | VALID — family → realised R on executed subset |
| Shadow variant | SS-001: "Which strategy families produce positive COUNTERFACTUAL expectancy across all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES (grouped by strategy_id from shadow identity) |
| Shadow value | Tests strategy expectancy on the full signal set. A strategy may look unprofitable on 94 live trades but profitable across thousands of shadow observations (or vice versa). |
| Sample impact | 94 → thousands per family |

### S-002 — Pattern Expectancy

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Which patterns have positive expectancy?" |
| Live validity | VALID — pattern → realised R |
| Shadow variant | SS-002: "Which patterns have positive counterfactual expectancy?" |
| Shadow population | ALL_SHADOW_OUTCOMES (grouped by decision_snapshot.pattern) |
| Shadow value | Tests pattern value across the full opportunity pool. Detected-but-rejected patterns get their counterfactual assessment. |
| Sample impact | Varies per pattern — shadow provides orders of magnitude more |

### S-003 — Strategy Selection Accuracy

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "When strategy engine selects a strategy, does selection predict better outcomes?" |
| Live validity | VALID — selection accuracy requires realised truth |
| Shadow variant | NOT APPLICABLE |
| Reason | "Selection accuracy" means comparing chosen-and-executed outcomes vs baseline. Shadow can test "does confidence predict shadow R" (separate question) but not whether the SELECTION act improves outcomes. |

### S-004 — Strategy Rejection Patterns

| Property | Value |
|----------|-------|
| Classification | **SHADOW_ONLY** (reformulate) |
| Current intent | "What characterises gaps where no strategy matches? Are there profitable patterns the engine misses?" |
| Live validity | INVALID — "profitable patterns the engine misses" requires knowing the outcome of missed patterns. Live has no outcome for unexecuted. |
| Shadow formulation | SS-004: "Among opportunities where no strategy matched (STRATEGY_REJECTED), what was the counterfactual R? Which rejected patterns would have been profitable?" |
| Shadow population | SHADOW_FROM_NO_TRADE where Decision.terminal_reason contains "strategy" |
| Shadow value | Directly answers whether the strategy engine is leaving money on the table. If strategy-rejected shadows show positive R, new strategy families may be needed. |
| Sample impact | 0 (impossible on Live) → hundreds/thousands |

---

## CROSS-ANGLE: EXECUTION + DECISION (ED-001 through ED-003)

### ED-001 — Decision-to-Execution Edge Leakage

| Property | Value |
|----------|-------|
| Classification | **CROSS_LIVE_SHADOW** |
| Current intent | "How much expected edge is lost between decision point and realised execution?" |
| Live validity | PARTIAL — compares EV/score (decision-time) to realised R. But "leakage" implies a reference. What's the reference? |
| Cross-side formulation | "For the same entity: Shadow R (what the decision INTENDED to capture) vs Live R (what was actually captured). The difference = execution leakage." |
| Why cross-side | True leakage measurement requires: (1) what you aimed for (shadow simulation of intended trade), (2) what you got (live realised R). Difference isolates broker/timing/management cost. |
| Shadow population | SHADOW_FROM_EXECUTE (shadows of the same entities that were actually executed) |
| Implementation | Paired comparison: same entity_id → shadow R vs live R. Difference = execution-induced performance change. |

### ED-002 — Missed Opportunity Cost

| Property | Value |
|----------|-------|
| Classification | **SHADOW_ONLY** |
| Current intent | "Which rejected decisions would have succeeded if allowed through?" |
| Live validity | INVALID — currently joins NO_TRADE_DECISIONS to EXECUTION via correlation_id. Match rate is essentially 0% (rejected signals never reach broker). |
| Shadow formulation | "What counterfactual R did NO_TRADE decisions produce? What is the total opportunity cost of rejection?" |
| Shadow population | SHADOW_FROM_NO_TRADE |
| Shadow value | THE canonical shadow question. Directly measures what the system rejects and what that rejection costs. |
| Sample impact | ~0 (impossible on Live) → thousands |
| Note | Current status is `PARTIAL` because the Live join fails. Shadow solves this completely. |

### ED-003 — Position Sizing Effectiveness

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Does quality-scaled sizing improve risk-adjusted returns vs fixed sizing?" |
| Live validity | VALID — requires actual P&L at actual sizes |
| Shadow variant | NOT APPLICABLE |
| Reason | Position sizing analysis requires real money outcomes (account impact, drawdown at scale). Shadow has fixed position size (0.01) and R-multiple is size-independent. |

---

## CROSS-ANGLE: EXECUTION + MARKET (EM-001, EM-002)

### EM-001 — Regime-Conditioned Expectancy

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does expectancy differ across regimes when measured on realised outcomes?" |
| Live validity | VALID — regime → realised R for executed trades |
| Shadow variant | SEM-001: "Does expectancy differ across regimes when measured on counterfactual outcomes (ALL signals)?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market for regime |
| Shadow value | This is the exact question that produced the first real experiment (prop_EM-001: exclude TRANSITIONAL). Shadow variant tests the same hypothesis on a vastly larger population. |
| Sample impact | 94 → thousands |
| Note | The existing EM-001 experiment already validated (+0.0646R improvement). Shadow variant would provide independent corroboration. |

### EM-002 — Market Drift Detection

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Is market behaviour changing over time in ways that invalidate assumptions?" |
| Live validity | VALID — temporal analysis of realised outcomes detects real drift |
| Shadow variant | NOT APPLICABLE as a direct pair |
| Reason | Drift detection should use realised truth. Shadow drift would measure whether the shadow model is drifting, which is a different (valid but separate) question. |
| Note | A SHADOW_ONLY question "Is counterfactual opportunity quality degrading over time?" is valid as a NEW question, not a pair of EM-002. |

---

## CROSS-ANGLE: EXECUTION + STRATEGY (ES-001)

### ES-001 — Execution Quality by Strategy

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Do different strategy families produce different execution quality?" |
| Live validity | VALID — strategy → realised R on executed trades |
| Shadow variant | SES-001: "Do strategy families produce different counterfactual expectancy across all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES grouped by strategy_id |
| Shadow value | Distinguishes "this strategy has poor execution quality" from "this strategy has poor opportunity quality". Shadow removes execution noise. |

---

## CROSS-ANGLE: DECISION + MARKET (DM-001 through DM-003)

### DM-001 — Decision Quality Under Regime

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does decision engine perform differently across regimes? Is scoring accuracy regime-dependent?" |
| Live validity | VALID — score accuracy → realised R by regime, on executed subset |
| Shadow variant | SDM-001: "Is scoring accuracy regime-dependent when measured against counterfactual outcomes across ALL signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Decision (score) and Market (regime) |
| Shadow value | Tests whether scoring is regime-dependent on the FULL signal set. If score predicts shadow R in TRENDING but not RANGING, scoring may need regime adaptation. |

### DM-002 — Opportunity Detection vs Market State

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does opportunity quality remain predictive across all market states?" |
| Live validity | VALID — quality prediction by market on executed |
| Shadow variant | SDM-002: "Does opportunity quality remain predictive of counterfactual outcome across market states?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Decision (quality) and Market (state) |
| Shadow value | Tests quality model robustness across conditions on the full opportunity set. |

### DM-003 — Rejection Rate by Market State

| Property | Value |
|----------|-------|
| Classification | **NEEDS_REFORMULATION** |
| Current intent | "Does NO_TRADE rate vary by market state? Are there conditions where system rejects everything?" |
| Live validity | VALID as purely descriptive (rejection rate = count-based, no outcome needed) |
| Problem | The question asks "possibly missing edge" but currently has no way to assess whether rejections COST edge. It's half-descriptive, half-counterfactual. |
| Reformulation | Split into: |
| DM-003a (LIVE_ONLY) | "What is the rejection rate by regime?" — purely descriptive, counts only |
| DM-003b (SHADOW_ONLY) | "In high-rejection-rate market states, what is the counterfactual R of rejected opportunities?" — answers whether high rejection = missed opportunity |
| Shadow population | SHADOW_FROM_NO_TRADE joined to Market by regime |

---

## CROSS-ANGLE: DECISION + STRATEGY (DS-001, DS-002)

### DS-001 — Strategy Confidence Calibration

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Is strategy confidence calibrated to outcomes?" |
| Live validity | VALID — confidence → realised R on executed |
| Shadow variant | SDS-001: "Does strategy confidence predict counterfactual R across all signals?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Strategy (confidence) |
| Shadow value | Tests confidence on the full signal set. If confidence doesn't predict shadow R, the confidence model is broken (not just undertested). |

### DS-002 — Strategy Conditions vs Outcome

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Do conditions_met at entry predict outcome? Is conditions framework adding value?" |
| Live validity | VALID — conditions → realised R on executed |
| Shadow variant | SDS-002: "Do conditions_met predict counterfactual R?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Strategy (conditions_met) |
| Shadow value | Tests whether the conditions framework adds value across all signals, not just the narrow executed set. |

---

## CROSS-ANGLE: MARKET + STRATEGY (MS-001 through MS-003)

### MS-001 — Strategy x Regime Interaction

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Do strategy families perform differently across regimes?" |
| Live validity | VALID — strategy × regime → realised R |
| Shadow variant | SMS-001: "Do strategy families perform differently across regimes in counterfactual outcomes?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market (regime) grouped by strategy_id |
| Shadow value | Tests strategy × regime interaction on the full opportunity pool. Critical for regime-gating decisions. |

### MS-002 — Pattern x Market Context

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Are patterns only profitable in certain conditions?" |
| Live validity | VALID — pattern × market → realised R |
| Shadow variant | SMS-002: "Are patterns profitable only in certain conditions counterfactually?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Market, grouped by pattern |
| Shadow value | Context-gating analysis on the full signal set. |

### MS-003 — Strategy Availability by Market State

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "How does market state affect strategy eligibility? Coverage gaps?" |
| Live validity | VALID — descriptive analysis of strategy activation by market state |
| Shadow variant | NOT APPLICABLE |
| Reason | Strategy availability is a pipeline-structural question (what CAN match?), not an outcome question. Shadow doesn't change what's available, only what would happen if selected. |

---

## MULTI-ANGLE (EDM-001, DMS-001, EDMS-001, EDMS-002)

### EDM-001 — Complete Trade Lifecycle Analysis

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "Full pathway from market state → decision → execution outcome. Where does pipeline add/lose value?" |
| Live validity | VALID — complete lifecycle of executed trades only |
| Shadow variant | NOT APPLICABLE as a direct pair |
| Reason | "Complete lifecycle" specifically means broker-confirmed entry through managed exit. Shadow lacks broker interaction, trade management adjustments, partial fills. |
| Note | A shadow lifecycle analysis IS interesting but it's a different question: "What does the counterfactual lifecycle look like?" (new SHADOW_ONLY question, not a mirror). |

### DMS-001 — Decision Quality Across Strategy x Market

| Property | Value |
|----------|-------|
| Classification | **LIVE_PRIMARY + SHADOW_PAIR** |
| Current intent | "Does decision quality vary by strategy × regime simultaneously?" |
| Live validity | VALID — multi-dimensional segmentation on executed |
| Shadow variant | SDMS-001: "Does decision quality predict counterfactual outcome across strategy × regime?" |
| Shadow population | ALL_SHADOW_OUTCOMES joined to Decision + Market + Strategy |
| Shadow value | Multi-dimensional interaction effects tested on full signal set. |
| Note | With only 94 live trades, multi-dimensional segmentation is statistically weak. Shadow enables this properly. |

### EDMS-001 — Full System Attribution

| Property | Value |
|----------|-------|
| Classification | **LIVE_ONLY** |
| Current intent | "What is the relative contribution of market/strategy/decision/execution to final outcomes?" |
| Live validity | VALID — attribution requires realised end-to-end outcomes |
| Shadow variant | NOT APPLICABLE |
| Reason | Attribution analysis determines what caused ACTUAL performance. Shadow can do attribution of counterfactual performance but that's a separate question about model quality, not system performance. |

### EDMS-002 — Promotion Impact Analysis

| Property | Value |
|----------|-------|
| Classification | **CROSS_LIVE_SHADOW** |
| Current intent | "If a finding is promoted, what is expected impact on EV, win rate, drawdown?" |
| Live validity | PARTIAL — can measure impact on Live population but sample is small |
| Cross-side formulation | "Apply proposed change filter to BOTH Live and Shadow populations. Measure: (1) Impact on Live realised R, (2) Impact on Shadow counterfactual R. Both must agree for promotion confidence." |
| Why cross-side | Promotion decisions should be supported by BOTH realised and counterfactual evidence. Agreement between sides increases confidence. Disagreement raises flags. |
| Shadow population | ALL_SHADOW_OUTCOMES (apply candidate filter, measure before/after) |
| Implementation | This is essentially what the CandidateExperiment already does — but explicitly running against both populations and requiring convergence. |

---

## SUMMARY STATISTICS

### Classification Totals

| Classification | Count | Questions |
|----------------|-------|-----------|
| LIVE_ONLY | 12 | E-004, E-005, E-006, E-009 (kept as Live-only despite having shadow pair due to semantic purity), D-002, D-006, ED-003, EM-002, MS-003, EDM-001, EDMS-001, S-003 |
| LIVE_PRIMARY + SHADOW_PAIR | 21 | E-001, E-002, E-003, E-007, E-008, E-009, E-010, D-001, D-005, M-001, M-002, M-003, M-004, M-005, M-006, S-001, S-002, EM-001, ES-001, DM-001, DM-002, DS-001, DS-002, MS-001, MS-002, DMS-001 |
| SHADOW_ONLY | 2 | ED-002, S-004 |
| CROSS_LIVE_SHADOW | 4 | D-003, D-007, ED-001, EDMS-002 |
| NEEDS_REFORMULATION | 2 | D-004 (split into 3), DM-003 (split into 2) |

**Correction on totals (exact 45):**
- LIVE_ONLY: E-004, E-005, E-006, D-002, D-006, ED-003, EM-002, MS-003, EDM-001, EDMS-001, S-003 = **11**
- LIVE_PRIMARY + SHADOW_PAIR: E-001, E-002, E-003, E-007, E-008, E-009, E-010, D-001, D-005, M-001, M-002, M-003, M-004, M-005, M-006, S-001, S-002, EM-001, ES-001, DM-001, DM-002, DS-001, DS-002, MS-001, MS-002, DMS-001 = **26**
- SHADOW_ONLY: ED-002, S-004 = **2**
- CROSS_LIVE_SHADOW: D-003, D-007, ED-001, EDMS-002 = **4**
- NEEDS_REFORMULATION: D-004, DM-003 = **2**

**Total: 11 + 26 + 2 + 4 + 2 = 45** ✓

---

## HUMAN-QUESTION COMPATIBILITY

### Principle Preserved

The dual-universe architecture does NOT restrict the ability to add new human-originated research questions. It EXPANDS it.

### How a human adds a new question under the new architecture:

1. **State the research intent** — what do you want to know?
2. **Classify the evidence type needed:**
   - "What actually happened?" → LIVE question
   - "What would have happened?" → SHADOW question
   - "Did we make the right choice?" → CROSS_LIVE_SHADOW question
3. **Select universe(s)** — which analytical domains are needed?
4. **Select population(s)** — which subset of records?
5. **Select primitive** — which analytical method?
6. **The question is registered** — same process as today

### New patterns enabled for humans:

| Pattern | Example |
|---------|---------|
| "What if we hadn't filtered X?" | SHADOW: filter to NO_TRADE by X, measure shadow R |
| "Is our rejection of Y correct?" | CROSS: compare shadow R of Y-rejected vs live R of Y-approved |
| "What opportunity are we missing in regime Z?" | SHADOW: filter by regime Z, measure counterfactual R |
| "Which horizon captures the most from pattern P?" | SHADOW: compare SCALP/INTRADAY/EXTENDED shadow R for pattern P |
| "How much do we lose in execution?" | CROSS: paired Live R vs Shadow R for same entity_id |

### What the architecture PREVENTS (by design):

- Asking for realised R on unexecuted signals (structural impossibility made explicit)
- Confusing counterfactual evidence with realised evidence (labelling enforced)
- Treating shadow findings as deployment-ready (governance pipeline preserved)

---

## PROPOSED SHADOW QUESTION ID SCHEME

To maintain consistency with the existing ID scheme while clearly distinguishing sides:

```
EXISTING (Live):     E-nnn, D-nnn, M-nnn, S-nnn, ED-nnn, EM-nnn, etc.
SHADOW PAIRS:        SE-nnn, SD-nnn, SM-nnn, SS-nnn, SED-nnn, SEM-nnn, etc.
CROSS-SIDE:          X-nnn (new prefix for cross-live-shadow questions)
```

**Rationale:**
- `S` prefix for Shadow (SE = Shadow Execution-angle, SD = Shadow Decision-angle)
- Numeric part matches Live pair for traceability (SE-001 is the shadow pair of E-001)
- `X` prefix for Cross-side (requires BOTH worlds, neither alone suffices)
- Avoids collision with existing `S-nnn` (Strategy) by using two-letter prefix for shadow

**Alternative (if S-prefix collides conceptually with Strategy):**
```
Live:    E-nnn, D-nnn, M-nnn, S-nnn
Shadow:  CE-nnn, CD-nnn, CM-nnn, CS-nnn  (C = Counterfactual)
Cross:   X-nnn
```

**Recommendation:** Use the `S` prefix convention (`SE-001`, `SD-001`, etc.) since:
- The existing `S-nnn` refers to Strategy-primary questions specifically
- `SE-nnn` clearly reads as "Shadow Execution-angle"
- No ambiguity in practice

---

## COMPLETE MIGRATION MAP (COMPACT)

| # | ID | Title | Classification | Action | Shadow ID |
|---|-----|-------|---------------|--------|-----------|
| 1 | E-001 | System Expectancy | LIVE+SHADOW | Create pair | SE-001 |
| 2 | E-002 | Win/Loss Distribution | LIVE+SHADOW | Create pair | SE-002 |
| 3 | E-003 | Exit Reason Distribution | LIVE+SHADOW | Create pair | SE-003 |
| 4 | E-004 | Execution Quality by Session | LIVE_ONLY | Keep | — |
| 5 | E-005 | Probability of Ruin | LIVE_ONLY | Keep | — |
| 6 | E-006 | Out-of-Sample Validation | LIVE_ONLY | Keep | — |
| 7 | E-007 | Stop Placement | LIVE+SHADOW | Create pair | SE-007 |
| 8 | E-008 | Pattern Degradation | LIVE+SHADOW | Create pair | SE-008 |
| 9 | E-009 | Duration vs Outcome | LIVE+SHADOW | Create pair | SE-009 |
| 10 | E-010 | R:R Effectiveness | LIVE+SHADOW | Create pair | SE-010 |
| 11 | D-001 | Score Predictive Power | LIVE+SHADOW | Create pair | SD-001 |
| 12 | D-002 | EV Calibration | LIVE_ONLY | Keep | — |
| 13 | D-003 | Threshold Effectiveness | CROSS | Reformulate as X-001 | X-001 |
| 14 | D-004 | Rejection Stage Analysis | REFORMULATE | Split into 3 | D-004a, SD-004, X-002 |
| 15 | D-005 | Opportunity Quality | LIVE+SHADOW | Create pair | SD-005 |
| 16 | D-006 | Opportunity Failure | LIVE_ONLY | Keep | — |
| 17 | D-007 | Risk Gate Value | CROSS | Reformulate as X-003 | X-003 |
| 18 | M-001 | Regime Predicts Outcomes | LIVE+SHADOW | Create pair | SM-001 |
| 19 | M-002 | HTF Alignment Value | LIVE+SHADOW | Create pair | SM-002 |
| 20 | M-003 | Volatility State Impact | LIVE+SHADOW | Create pair | SM-003 |
| 21 | M-004 | Market Structure Clarity | LIVE+SHADOW | Create pair | SM-004 |
| 22 | M-005 | Location Quality Impact | LIVE+SHADOW | Create pair | SM-005 |
| 23 | M-006 | Session Edge Variation | LIVE+SHADOW | Create pair | SM-006 |
| 24 | S-001 | Strategy Family Expectancy | LIVE+SHADOW | Create pair | SS-001 |
| 25 | S-002 | Pattern Expectancy | LIVE+SHADOW | Create pair | SS-002 |
| 26 | S-003 | Strategy Selection Accuracy | LIVE_ONLY | Keep | — |
| 27 | S-004 | Strategy Rejection Patterns | SHADOW_ONLY | Move to shadow | SS-004 |
| 28 | ED-001 | Edge Leakage | CROSS | Reformulate as X-004 | X-004 |
| 29 | ED-002 | Missed Opportunity Cost | SHADOW_ONLY | Move to shadow | SED-002 |
| 30 | ED-003 | Position Sizing | LIVE_ONLY | Keep | — |
| 31 | EM-001 | Regime-Conditioned Expectancy | LIVE+SHADOW | Create pair | SEM-001 |
| 32 | EM-002 | Market Drift | LIVE_ONLY | Keep | — |
| 33 | ES-001 | Execution by Strategy | LIVE+SHADOW | Create pair | SES-001 |
| 34 | DM-001 | Decision Quality Under Regime | LIVE+SHADOW | Create pair | SDM-001 |
| 35 | DM-002 | Opportunity vs Market State | LIVE+SHADOW | Create pair | SDM-002 |
| 36 | DM-003 | Rejection Rate by Market | REFORMULATE | Split into 2 | DM-003a, SDM-003 |
| 37 | DS-001 | Strategy Confidence Calibration | LIVE+SHADOW | Create pair | SDS-001 |
| 38 | DS-002 | Strategy Conditions vs Outcome | LIVE+SHADOW | Create pair | SDS-002 |
| 39 | MS-001 | Strategy x Regime | LIVE+SHADOW | Create pair | SMS-001 |
| 40 | MS-002 | Pattern x Market Context | LIVE+SHADOW | Create pair | SMS-002 |
| 41 | MS-003 | Strategy Availability | LIVE_ONLY | Keep | — |
| 42 | EDM-001 | Complete Lifecycle | LIVE_ONLY | Keep | — |
| 43 | DMS-001 | Decision x Strategy x Market | LIVE+SHADOW | Create pair | SDMS-001 |
| 44 | EDMS-001 | Full System Attribution | LIVE_ONLY | Keep | — |
| 45 | EDMS-002 | Promotion Impact | CROSS | Reformulate as X-005 | X-005 |

---

## RESULTING QUESTION INVENTORY (post-migration)

| Category | Count |
|----------|-------|
| Original Live questions (preserved unchanged) | 45 |
| New Shadow pairs created | 26 |
| New Cross-side questions | 5 (X-001 through X-005) |
| Reformulated questions (D-004 splits, DM-003 splits) | 5 sub-questions |
| Shadow-only questions (moved from broken Live) | 2 (SS-004, SED-002) |
| **Total question capacity** | **~78** (from 45) |

This does NOT include the Tier 1-3 new shadow research opportunities identified in the architecture audit (an additional 16 potential questions). Those remain proposals for human review.

---

## IMPLEMENTATION NOTES

### Phase ordering for question migration:

1. **Phase A** — Create the Shadow infrastructure (builders, populations, contracts)
2. **Phase B** — Register Shadow pair questions (SE-*, SD-*, SM-*, SS-*, S*-*) — purely additive, existing questions untouched
3. **Phase C** — Reformulate D-004, DM-003, ED-001, EDMS-002 as Cross-side (X-*) — replaces broken formulations
4. **Phase D** — Move S-004 and ED-002 to Shadow-only — they cannot function on Live

### No questions are RETIRED

Every existing question either:
- Remains valid on Live (keeps its current ID and behaviour)
- Gets a Shadow PAIR (new question, old one unchanged)
- Gets REFORMULATED into a more correct version (old ID preserved for traceability, new ID for correct formulation)

### Backward compatibility

- All 45 existing questions continue to function exactly as today
- Running the research engine without Shadow infrastructure produces identical results
- Shadow questions only activate when ShadowOutcomeUniverseBuilder is available
- Cross-side questions can gracefully degrade to Live-only when Shadow unavailable

---

*End of question bank dual-universe audit. No questions modified. No code changed.*
