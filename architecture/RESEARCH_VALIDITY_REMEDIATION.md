# Research Validity Remediation Audit

---

## Step 1: Inventory of All Research Questions

### Category E — System Edge (5 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| E1 | True system EV | Edge | expected_value.py | ALL/MIXED | 🔴 Invalid — epoch contamination gives +0.675R vs real -0.20R |
| E2 | Pattern expectancy | Edge | run_q05 | ALL/MIXED | 🟡 Needs rerun — CURRENT only |
| E3 | Strategy expectancy | Edge | run_q24 | ALL/MIXED | 🟡 Needs rerun — CURRENT only |
| E4 | Strategy×pattern combos | Edge | None | N/A | 🟡 No runner, partially answerable from M9 data |
| E5 | Walk-forward validation | Edge | out_of_sample_validation.py | ALL/MIXED | 🟡 Runner exists, needs CURRENT-epoch run |

### Category M — Market Context (11 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| M1 | Regime predicts outcomes | Context | run_q06 | ALL/MIXED | 🟡 Needs CURRENT rerun |
| M2 | Regime edge by strategy | Context | None | N/A | 🟡 Derivable from M9/M10 data |
| M3 | Phase improves prediction | Context | None | N/A | 🟡 Answerable from M9 data (CURRENT) |
| M4 | Regime×phase×strategy | Context | None | N/A | 🟡 Thin cells, needs more data |
| M5 | Phase transitions → drawdown | Context | None | N/A | 🔴 No data, no runner |
| M6 | Phase expectancy | Context | Via M9 | CURRENT | ✅ Valid — all phases negative (M9 report) |
| M7 | Regime+phase interaction | Context | Via M10 | CURRENT | ✅ Valid — interaction detected (M10 report) |
| M8 | Phase transition behaviour | Context | None | N/A | 🔴 No data, no runner |
| M9 | Phase×pattern classification | Context | m9_phase_pattern.py | CURRENT | ✅ Valid |
| M10 | Phase×family interaction | Context | m10_strategy_family_per_phase.py | CURRENT | ✅ Valid |
| M11 | Context > pattern? | Context | None | N/A | 🔴 Not implemented |

### Category D — Decision Quality (6 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| D1 | Scoring components predict R | Decision | component_reward.py | ALL/MIXED | 🟡 Join rate 20%, epoch unclear |
| D2 | Confidence calibration | Decision | run_q04 | ALL/MIXED | 🟡 Finding valid but needs CURRENT verification |
| D3 | EV gate value | Decision | run_q21 | ALL/MIXED | 🔴 EV gate disabled, cannot A/B test |
| D4 | Optimal thresholds | Decision | run_q02 | ALL/MIXED | 🟡 Needs CURRENT rerun |
| D5 | Missed opportunity cost | Decision | run_q03 | ALL/MIXED | 🟡 Descriptive only, no counterfactual |
| D6 | Portfolio ranking quality | Decision | portfolio_ranking.py | ALL/MIXED | 🟡 Insufficient concurrent cycles |

### Category S — Strategy & Horizon (7 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| S1 | Strategy type EV | Strategy | run_q24 | ALL/MIXED | 🟡 Same as E3, needs CURRENT |
| S2 | Horizon EV | Horizon | None | N/A | 🟡 Data exists (horizon shadows), no formal experiment |
| S3 | Strategy×horizon combos | Horizon | None | N/A | 🔴 No runner |
| S4 | Strategies per phase | Strategy | Via M9/M10 | CURRENT | ✅ Valid (derived from M9/M10) |
| S5 | Strategy identity EV | Strategy | run_q24 | ALL/MIXED | 🟡 Duplicate of E3 |
| S6 | Horizon expectancy | Horizon | None | N/A | 🟡 Data exists, no runner |
| S7 | Strategy×horizon interaction | Horizon | None | N/A | 🔴 No runner |

### Category X — Execution (6 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| X1 | Slippage model | Execution | run_q11 | ALL | 🟡 Data exists (trade_truth), not epoch-filtered |
| X2 | Broker failures | Execution | run_q12 | ALL | 🟡 Data exists, not analysed by epoch |
| X3 | Session quality | Execution | run_q09 | ALL | 🟡 Descriptive, no statistical test |
| X4 | Shadow vs live gap | Execution | shadow_validation.py | N/A | 🔴 BLOCKED — 0 matched trades |
| X5 | Execution leakage | Execution | None | N/A | 🔴 Same blocker as X4 |
| X6 | Execution stability | Execution | None | N/A | 🔴 No runner |

### Category R — Risk Management (5 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| R1 | Risk model effectiveness | Risk | run_q10 | ALL/MIXED | 🟡 Counts blocks, no counterfactual |
| R2 | Guard value analysis | Risk | run_q10 | ALL/MIXED | 🟡 Same as R1 |
| R3 | Probability of ruin | Risk | probability_of_ruin.py | INVALID INPUTS | 🔴 Used WR=80%, real=33% |
| R4 | Drawdown threshold | Risk | drawdown_threshold.py | ALL/MIXED | 🔴 Based on +0.675R EV system |
| R5 | Position sizing | Risk | position_sizing.py | ALL/MIXED | 🔴 Based on +0.675R EV system |

### Category L — Learning (7 questions)

| ID | Question | Domain | Experiment | Epoch Used | Status |
|---|---|---|---|---|---|
| L1 | Pattern degradation | Learning | run_q05 | ALL/MIXED | 🟡 Same as E2 |
| L2 | System improvement | Learning | run_q15 | N/A | ✅ Valid (counts reports, no epoch issue) |
| L3 | Architecture assumptions | Learning | component_reward.py | ALL/MIXED | 🟡 Same as D1 |
| L4 | Market drift | Learning | run_q17 | ALL | 🟡 Needs time series, CURRENT only |
| L7 | Shadow A/B validation | Learning | shadow_ab_validation.py | N/A | 🟡 Runner exists, no A/B test active |

### Category G — Governance (3 questions) + P — Promotion (1 question)

| ID | Question | Domain | Experiment | Status |
|---|---|---|---|---|
| G1-G3 | Governance | Governance | None | 🟡 Framework questions, not experiments |
| P1 | Promotion impact | Promotion | promotion_impact.py | 🟡 Runner exists, no promotion to test |

---

## Step 2: Experimental Design Validation

### Experiments with Valid Design (4)

| Experiment | Hypothesis Clear? | Single Variable? | Control? | Correct Population? |
|---|---|---|---|---|
| **M9** (phase×pattern) | ✅ "Which patterns work in which phases?" | ✅ Grouped by two factors | ✅ All trades, grouped | ✅ CURRENT epoch |
| **M10** (phase×family) | ✅ "Does phase need different families?" | ✅ Grouped by two factors | ✅ All trades, grouped | ✅ CURRENT epoch |
| **E5** (walk-forward) | ✅ "Does edge survive OOS?" | ✅ Train/test split | ✅ Time-ordered split | ⚠️ Needs CURRENT rerun |
| **L7** (A/B validation) | ✅ "Does candidate beat control?" | ✅ Two-arm comparison | ✅ Same period, different policy | ⚠️ No active test |

### Experiments with Design Flaws (7)

| Experiment | Flaw | Severity |
|---|---|---|
| **E1/Q19** (expected_value) | No epoch filter. Mixes architectures. | 🔴 Critical — wrong conclusion |
| **R3** (ruin) | Inputs don't match real data | 🔴 Critical — false safety |
| **R4** (drawdown) | Based on wrong EV (+0.675 vs -0.20) | 🔴 Critical — wrong threshold |
| **R5** (sizing) | Optimises for non-existent positive EV | 🔴 Critical — harmful recommendation |
| **D1/Q1** (components) | 20% join rate, epoch unclear | 🟡 Moderate — may be valid but unverifiable |
| **Horizon comparison** | Multiple variables change (SL+RR+source), same max_bars | 🟡 Moderate — not a controlled experiment |
| **D3/Q21** (EV gate) | Gate disabled, no comparison possible | 🟡 Blocked by config |

---

## Step 3: Statistical Validity

| Experiment | Sample Size | CI? | Significance? | Effect Size? | Correction? | Strength |
|---|---|---|---|---|---|---|
| M9 | n=728, cells 10-242 | ❌ | ❌ | ❌ | ❌ | Moderate (descriptive) |
| M10 | n=728, cells 10-486 | ❌ | ❌ | ❌ | ❌ | Moderate (descriptive) |
| E1 (CURRENT recompute) | n=846 | ✅ [-0.236, -0.164] | ✅ p<0.000001 | ✅ EV=-0.20R | N/A | **Strong** |
| Trailing stop validation | n=846 | ❌ | ✅ t=6.66, p<0.001 | ✅ +0.185R | N/A | **Strong** |
| D1/Q1 | n=237 | ❌ | ❌ | Partial (predictive_value) | ❌ | Weak |
| R3 | n=100 (wrong inputs) | ❌ | N/A | N/A | N/A | **Invalid** |
| R4/R5 | n=901 (wrong epoch) | ❌ | N/A | N/A | N/A | **Invalid** |

---

## Step 4: Promotion Safety Audit

| Recommendation | Original Evidence | Validity | Action |
|---|---|---|---|
| **R3: PROMOTE** | P(ruin)=0%, WR=80% | 🔴 Inputs are wrong | **INVALIDATE** |
| **R4: PROMOTE** | Halt at 50% DD, EV=+0.675R | 🔴 Wrong EV, wrong context | **INVALIDATE** |
| **R5: PROMOTE** | Fixed 0.5%, +1917% | 🔴 Produces -57% on CURRENT | **INVALIDATE** |
| **Q4: PROMOTE_CALIBRATION** | 15pp miscalibration | 🟡 Finding may be valid | **RERUN** on CURRENT |
| **Q19: POSITIVE_EDGE** | EV=+0.675R | 🔴 CURRENT EV=-0.20R | **INVALIDATE** |
| **M9: MONITOR** | TWEEZER_BOTTOM/REVERSAL +0.098R | ✅ CURRENT epoch, correct design | **KEEP** |
| **M10: WAIT** | Interaction exists, no promotable cell | ✅ CURRENT epoch, correct | **KEEP** |

---

## Step 5: Research Contracts (for invalid/incomplete experiments)

### Contract 1: True Current-Epoch EV

```
Research Question: What is the system's true expected value?
Hypothesis: The current pipeline has EV < 0 due to exit failures.
Control: All CURRENT-epoch shadow trades (no filtering).
Variable: None (measurement only).
Data Required: shadow_trades where classify_record == CURRENT.
Success Metric: EV, CI, p-value, profit factor, exit distribution.
Minimum Sample: n ≥ 200 (have 846).
Validation: Bootstrap CI, significance test vs EV=0.
Promotion Criteria: N/A (measurement, not promotion).
```

### Contract 2: Trailing Stop Improvement (Walk-Forward)

```
Research Question: Does trailing stop improve EV under realistic conditions?
Hypothesis: Trailing (activate 0.5R, trail 0.10R) improves EV by ≥ 0.10R.
Control: Current exit (max_bars=60, no trailing).
Variable: One — trailing stop addition (all else constant).
Data Required: CURRENT-epoch trades with trade_state_progression.
Success Metric: Paired t-test (same trades, different exits).
Minimum Sample: n ≥ 200 (have 846).
Validation: Walk-forward (first 60% train, last 40% test).
Promotion Criteria: Test period EV improvement > 0 with p < 0.05.
```

### Contract 3: Exit Distance Optimisation

```
Research Question: What TP distance maximises CURRENT-epoch EV?
Hypothesis: Any TP ≤ 2R outperforms current unreachable TP.
Control: Current TP hit rate (0.5%).
Variable: TP distance (0.25R, 0.5R, 0.75R, 1.0R, 1.5R, 2.0R).
Data Required: MFE data from CURRENT-epoch trades.
Success Metric: Simulated EV per TP level, comparison vs baseline.
Minimum Sample: n ≥ 200 per variant (have 846 for all).
Validation: Walk-forward split.
Promotion Criteria: Best TP achieves EV > 0 with p < 0.05 in test period.
```

### Contract 4: Risk Model (CURRENT Data)

```
Research Question: What risk level avoids catastrophic drawdown?
Hypothesis: Risk ≤ 0.2% per trade keeps P(ruin) < 5% over 1000 trades.
Control: Monte Carlo with measured CURRENT R-distribution.
Variable: Risk fraction (0.1%, 0.2%, 0.5%, 1.0%).
Data Required: CURRENT-epoch R-multiple distribution (n=846).
Success Metric: P(50% DD) per risk level over 1000-trade horizon.
Minimum Sample: 5000 Monte Carlo paths per risk level.
Validation: Bootstrap confidence interval on ruin probability.
Promotion Criteria: Lowest risk achieving < 5% P(ruin) with 95% confidence.
```

### Contract 5: Component Reward (CURRENT Epoch)

```
Research Question: Which scoring components predict outcomes in current architecture?
Hypothesis: Some components correlate with R positively, others negatively.
Control: All CURRENT-epoch trades with matched decision_trace.
Variable: Component value (high vs low split at median).
Data Required: decision_trace + shadow_trades joined by entity_id, CURRENT only.
Success Metric: Predictive value = avg_R_when_high - avg_R_when_low per component.
Minimum Sample: n ≥ 100 matched pairs.
Validation: Bootstrap CI on predictive value.
Promotion Criteria: Component with predictive_value > 0.10R and p < 0.05.
```

---

## Step 6: Research Validity Scorecard

| Domain | Valid Experiments | Issues | Fixes Required | Confidence |
|---|---|---|---|---|
| **Market Behaviour** | M9 ✅, M10 ✅ | M5/M8 unimplemented | Minor — existing experiments cover core questions | HIGH |
| **Pattern** | E2 🟡 | Needs CURRENT rerun | Low effort — add epoch filter to run_q05 | MEDIUM |
| **Strategy** | S4 ✅ (via M9/M10) | E3/S1 need CURRENT rerun | Low effort — epoch filter | MEDIUM |
| **Decision** | D2 🟡 | Q1 join rate low, epoch unclear | Medium — needs entity_id join improvement | LOW |
| **Probability** | D2 🟡 | Calibration valid but context changed | Re-verify in negative EV context | LOW |
| **Risk** | NONE ✅ | R3/R4/R5 all invalid | **Critical** — complete rerun needed | 🔴 ZERO |
| **Execution** | NONE ✅ | X4 blocked, X1-X3 descriptive only | Medium — fix correlation_id lineage | LOW |
| **Horizon** | NONE ✅ | Same max_bars, multiple variables | Medium — redesign experiment | LOW |
| **Exit** | Ad-hoc only | No registered question or runner | **Critical** — needs new research domain | 🔴 ZERO |
| **Learning** | L2 ✅ | L7 not active | Low — activate when A/B defined | MEDIUM |

---

## Step 7: Final Research Engine Verdict

### 1. Which conclusions can currently be trusted?

| Conclusion | Source | Why Trusted |
|---|---|---|
| System EV = -0.20R | CURRENT recompute (this audit) | n=846, p<0.001, CURRENT only, CI provided |
| 78.7% timeout, 0.5% TP | Direct count, CURRENT | Objective measurement |
| Phase×pattern interaction exists | M9, CURRENT epoch | Correct design, epoch-filtered |
| Phase×family interaction exists | M10, CURRENT epoch | Correct design, epoch-filtered |
| REVERSAL family outperforms MOMENTUM | M10, CURRENT | Grouped comparison, same population |
| Trailing improves by +0.185R | Bar-by-bar simulation | Sequential, no look-ahead, paired t-test p<0.001 |
| Entries have directional signal (MFE=0.70R) | Direct measurement | Objective, 100% coverage |
| Score is monotonically related to WR | Q4/Q20 | Valid design (may need CURRENT recheck) |
| Architecture authority separation correct | Design audit | Structural fact, not empirical |

### 2. Which conclusions must be ignored?

| Conclusion | Why Ignored |
|---|---|
| "EV = +0.675R" (Q19) | ALL-epoch contamination |
| "P(ruin) = 0%" (R3) | Wrong inputs (WR=80% vs real 33%) |
| "Fixed 0.5% = +1917%" (R5) | Based on non-existent positive EV |
| "Halt at 50% DD" (R4) | Assumes recovery from positive EV |
| "STRONG_EDGE" classification | Based on contaminated EV |
| Any PROMOTE from R3/R4/R5 | All three invalidated |

### 3. Which experiments need redesign?

| Experiment | Redesign Required |
|---|---|
| **E1** (expected_value) | Add CURRENT-epoch gate as DEFAULT. Include epoch breakdown in output. |
| **R3** (ruin) | Use MEASURED current stats as inputs (not synthetic). Add Monte Carlo with real R-distribution. |
| **R4** (drawdown) | Re-simulate with CURRENT R-values. Report "halt now" if EV < 0. |
| **R5** (sizing) | Simulate all risk levels on CURRENT data. Acknowledge no sizing fixes negative EV. |
| **D1** (components) | Filter to CURRENT-epoch joins only. Report join rate and epoch of matched records. |
| **Horizon** | Isolate ONE variable per comparison. Set different max_bars per horizon. |

### 4. Minimum work required before safe implementation recommendations?

| # | Task | Effort | Unlocks |
|---|---|---|---|
| 1 | **Add `epoch=CURRENT` default to `load_shadow_trades()` or every runner** | 2-3 lines per runner | Prevents ALL future epoch contamination |
| 2 | **Re-run R3/R4/R5 on CURRENT data** | Run existing code with correct inputs | Restores risk model trust |
| 3 | **Register Exit Management as research domain (EX1-EX10)** | Registry entries + simple runners | Addresses #1 research gap |
| 4 | **Run E5 walk-forward on trailing stop result** | Call existing E5 runner with trailing-simulated R-values | Validates the one promising improvement |
| 5 | **Update research_knowledge.json** to mark R3/R4/R5/Q19 as INVALIDATED | Documentation only | Prevents humans trusting old reports |

**Total: ~4-6 hours of engineering work.** Not architectural. Not complex. Just epoch hygiene + formal registration.

### 5. Is the research engine operating as an evidence system?

**PARTIALLY.**

The engine's **architecture** is an evidence system:
- Registry with formal questions
- Runners that produce structured reports
- Decision gates with promotion criteria
- Command centre that aggregates findings
- Data quality classifier that KNOWS about epoch issues

The engine's **operation** has been a reporting system:
- Ran experiments without epoch filtering
- Produced "PROMOTE" recommendations on contaminated data
- Did not validate inputs against current reality
- Did not automatically reject conclusions when data changed

**The gap is operational, not architectural.** The epoch classifier EXISTS but experiments don't USE it by default. The fix is mechanical: make CURRENT-epoch the default for all experimental data loading. Once done, the engine becomes a genuine evidence system that cannot produce contaminated conclusions.

**After the 5 tasks above are complete: YES, the engine can be trusted for implementation decisions.**
