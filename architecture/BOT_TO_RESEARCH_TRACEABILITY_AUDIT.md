# Bot-to-Research Traceability Audit

---

## SECTION 1: Complete Bot → Research Mapping

| # | Bot Component | Decision Made | Data Produced | Research Question | Experiment | Status |
|---|---|---|---|---|---|---|
| 1 | **MarketContext** | What environment exists? | regime, phase, direction, H4/H1/M15/M5 summaries, tradability_score | M3: Phase improves prediction | M9, M10, strategy_observations | 🟡 Partially mapped — M9/M10 run on shadow data, not directly on MarketContext persistence |
| 2 | **Regime Classification** | TRENDING / RANGING / TRANSITIONAL | h4_regime in decision_trace + shadow_trades | M1: Regime predicts outcomes | Q6 (run_q06) | ✅ Correctly mapped |
| 3 | **Phase Detection** | IMPULSE / PULLBACK / CONSOLIDATION / EXHAUSTION / REVERSAL | market_phase in shadow_trades + decision_trace | M6: Phase expectancy; M9: Phase×pattern | M9 (run_m9_phase_pattern) | ✅ Correctly mapped |
| 4 | **Pattern Detection** | Which candlestick pattern? | pattern name in decision_trace + shadow_trades | E2: Pattern expectancy; L1: Pattern degradation | Q5 (run_q05) | ✅ Correctly mapped |
| 5 | **Strategy Selection** | REVERSAL / CONTINUATION / FALSE_BREAK | selected_strategy in decision_trace + shadow_trades | E3: Strategy expectancy; S1: Strategy type EV | Q24 (run_q24) | 🟡 Partially — Q24 uses ALL epochs (contamination risk) |
| 6 | **Scoring (10-factor)** | Weighted composite score (0-1) | components dict + score_neutral + score_strategy in decision_trace | D1: Components predict R; D4: Optimal thresholds | Q1 (component_reward), Q2 (run_q02) | 🟡 Partially — Q1 join rate only 20%, epoch mix unclear |
| 7 | **Probability Estimation** | p_success (0-1) | p_success in decision_trace | D2: Confidence calibration | Q4 (run_q04) | ✅ Correctly mapped (finding: 15pp miscalibration) |
| 8 | **EV Gate** | Block negative-EV trades? | policy_trade_allowed, ev in engine_result | D3: EV filtering value | Q21 (run_q21) | 🟡 Partially — EV gate currently disabled (ENABLE_EV_GATE=False), cannot A/B test |
| 9 | **Risk Management** | SL/TP geometry + position size + guard chain | risk_decision, guards in decision_ledger | R1: Risk model effectiveness; R2: Guard value | Q10 (run_q10) | 🟡 Partially — counts guard blocks but no counterfactual outcome comparison |
| 10 | **Horizon Selection** | SCALP / INTRADAY / EXTENDED | trade_horizon in shadow_trades, horizon shadow trade_ids | S2: Horizon EV; S6: Horizon expectancy | None (no dedicated runner) | 🔴 Missing experiment — data exists but no formal experiment evaluates it |
| 11 | **Exit Management** | max_bars_timeout / stop_loss / take_profit | exit_reason, bars_held, mfe_r, mae_r in shadow_trades | None registered | trade_management_simulator (tool, not experiment) | 🔴 Missing — most critical gap. No registered research question for exit policy |
| 12 | **Execution** | Place broker order | execution_context, trade_truth | X1: Slippage; X4: Shadow vs live gap | Q16 (shadow_validation), Q11, Q12 | 🔴 Q16 BLOCKED (0 matched trades), Q11/Q12 incomplete |
| 13 | **Trade Lifecycle** | Open → manage → close | trade_truth, trade_truth_graph | X5: Execution leakage | None running | 🔴 Missing — no experiment tracks lifecycle efficiency |
| 14 | **Learning/Promotion** | Promote/demote strategies | decision_gates, promotion_readiness | P1: Promotion impact; L7: A/B validation | promotion_impact.py, shadow_ab_validation.py | 🟡 Partially — runners exist but no active promotion has been tested |
| 15 | **Strategy Intelligence** | Which strategies match context? | strategy_observations | New (no registered ID) | Observer #7 + evidence_store | 🟡 Partially — infrastructure complete, data accumulating (n=159) |

---

## SECTION 2: Research Questions With Valid Experiments

These have correctly designed experiments that measure what they claim:

| ID | Question | Component | Experiment | Data | Validity |
|---|---|---|---|---|---|
| M1/Q6 | Regime predicts outcomes | Regime | run_q06 | decision_trace + shadow | ✅ Valid — single variable (regime), groups outcomes by regime, uses shadow R |
| E2/Q5 | Pattern expectancy | Patterns | run_q05 | shadow_trades | ✅ Valid — groups by pattern, computes per-pattern EV, no multi-variable confusion |
| M9 | Phase×pattern performance | Phase + Pattern | run_m9_phase_pattern | shadow_trades CURRENT | ✅ Valid — two-way breakdown, CURRENT epoch only, confidence per cell |
| M10 | Phase×family interaction | Phase + Family | run_m10_strategy_family_per_phase | shadow_trades CURRENT | ✅ Valid — family classification + phase breakdown, epoch-filtered |
| D2/Q4 | Confidence calibration | Probability | run_q04 | decision_trace + shadow | ✅ Valid — compares predicted p_success to actual win rate, single metric |
| E5 | Walk-forward validation | System EV | out_of_sample_validation | shadow_trades | ✅ Valid — train/test split, rolling windows, drift detection |
| R3 | Probability of ruin | Risk | probability_of_ruin | shadow_trades | ⚠️ Design valid but INPUT DATA WRONG (used WR=80% instead of real 33%) |
| R4 | Drawdown threshold | Risk | drawdown_threshold | shadow_trades | ⚠️ Design valid but used ALL-epoch data (inflated EV) |
| R5 | Position sizing | Risk | position_sizing | shadow_trades | ⚠️ Design valid but used ALL-epoch data (inflated EV) |

---

## SECTION 3: Research Questions With Invalid Experiment Design

| ID | Question | What's Wrong | Severity |
|---|---|---|---|
| **E1/Q19** | True system EV | Uses ALL epochs (n=901) mixing LEGACY (+0.37R) with CURRENT (-0.20R). Reports "STRONG_EDGE" when current system loses. | 🔴 CRITICAL — conclusion is wrong for current architecture |
| **R3** | Probability of ruin | Input data shows WR=80%, avg_win=2.0R — these are NOT real current system stats (real: WR=33%, avg_win=0.33R). Either synthetic data or extreme LEGACY contamination. | 🔴 CRITICAL — conclusion (P(ruin)=0%) is dangerously wrong |
| **R4** | Drawdown threshold | Based on ALL-epoch R-multiples with EV=+0.675R. Recommends "halt at 50% DD" but current system is already past this. | 🔴 INVALID for current system |
| **R5** | Position sizing | Optimises sizing for EV=+0.675R system. "Fixed 0.5%" produces -57% return on CURRENT data. | 🔴 INVALID for current system |
| **D1/Q1** | Components predict R | Join rate only 19.9% (237/1189). Epoch composition of the 237 matched records is unknown. May be disproportionately LEGACY. | 🟡 SUSPECT — needs CURRENT-epoch reproduction |
| **Q3/D5** | Missed opportunities | Counts rejection stages but doesn't compute counterfactual R for rejected trades. Cannot answer "would they have been profitable?" | 🟡 INCOMPLETE — descriptive only, not causal |
| **Horizon** | Duration comparison | SCALP and INTRADAY use same max_bars=60. Two variables change simultaneously (SL + RR). No statistical test. | 🔴 NOT A VALID EXPERIMENT — multiple variables, no isolation |

---

## SECTION 4: Research Questions Missing Experiments

| Bot Component | Missing Research Question | Why It Matters | Priority |
|---|---|---|---|
| **Exit Management** | "What exit policy maximises capture of available MFE?" | Exit is the PRIMARY source of negative EV (78.7% timeout, -1.51 capture ratio). No registered question. | 🔴 P0 — HIGHEST PRIORITY |
| **Horizon Duration** | "Does holding longer improve outcomes for wider-SL trades?" | Horizon shadows use same max_bars, cannot distinguish duration effect | P1 |
| **Entry Quality** | "Which entries actually move in the predicted direction?" | 85% of entries never reach +0.5R. No experiment identifies which entries have signal | P1 |
| **Strategy Intelligence** | "Does strategy-context matching improve outcomes vs unfiltered?" | Observer running (n=159) but no formal experiment compares FULLY_MET vs NOT_MET outcomes | P2 |
| **Trade Lifecycle** | "How much edge is lost between shadow and live execution?" | Q16 is BLOCKED (0 matches). No alternative approach exists | P2 |
| **Trailing Stop** | "Does trailing stop improve EV per horizon per strategy family?" | Simulator exists but not registered as formal experiment | P1 |

---

## SECTION 5: Priority Order for Fixing Research Validity

### Priority 1: Fix critically wrong conclusions

| # | Action | Why | Impact |
|---|---|---|---|
| 1 | **Re-run E1/Q19 on CURRENT epoch only** | Current report claims +0.675R STRONG_EDGE. Real CURRENT EV = -0.20R. This is the most dangerous active misinformation in the system. | Prevents false confidence in system profitability |
| 2 | **Re-run R3 with real CURRENT stats** (WR=33%, avg_win=0.33R) | Current report claims P(ruin)=0%. With real numbers, P(ruin)≈100%. | Prevents catastrophic risk misunderstanding |
| 3 | **Re-run R4/R5 on CURRENT epoch** | Position sizing and drawdown recommendations are optimised for a profitable system that doesn't exist | Prevents deployment of harmful configurations |

### Priority 2: Fill critical research gaps

| # | Action | Why |
|---|---|---|
| 4 | **Register Exit Management research questions (EX1-EX10)** | Exit is the #1 driver of negative EV. No formal research question exists. The knowledge IS in the system (MFE/MAE data, simulator) but no experiment runner produces a report. |
| 5 | **Create trailing stop experiment as formal registered experiment** | Bar-by-bar validation was done ad-hoc. Needs to be a repeatable registered experiment with CURRENT-epoch gate. |
| 6 | **Fix Horizon variable isolation** | Set INTRADAY max_bars to 180, create single-variable variants |

### Priority 3: Improve experiment quality

| # | Action | Why |
|---|---|---|
| 7 | **Add epoch filter to ALL experiment runners** | Prevent future ALL-epoch contamination. Every runner should default to CURRENT epoch. |
| 8 | **Reproduce Q1 (component_reward) on CURRENT epoch** | Current finding (confirmation_pre best predictor) may be LEGACY-era artefact |
| 9 | **Add paired significance tests to comparison experiments** | Horizon, trailing, strategy comparisons produce means but not p-values |
| 10 | **Add walk-forward to trailing stop finding** | Split 846 trades temporally to validate trailing improvement holds |

---

## Final Question: "Can the current research engine be trusted to make implementation decisions?"

### NO

**Evidence:**

1. **The highest-confidence report (Q19: EV=+0.675R, STRONG_EDGE) is demonstrably wrong** for the current system. CURRENT-epoch EV = -0.20R. The research engine currently tells you the system is highly profitable when it is actually losing money.

2. **Three PROMOTE recommendations (R3, R4, R5) are based on invalid inputs.** R3 uses synthetic/LEGACY statistics (WR=80%). R5 sizing optimisation produces -57% return on real CURRENT data.

3. **The most critical system failure (exit management) has no registered research question.** The research engine cannot be trusted to identify problems it doesn't ask about.

4. **No experiment enforces CURRENT-epoch filtering by default.** Any runner can silently include LEGACY data that inflates results.

5. **The Decision Gate framework checks coverage but not epoch validity.** A question can reach "PROMOTE" status while being evaluated on obsolete data.

### What WOULD make it trustworthy:

1. All experiment runners default to CURRENT epoch with explicit opt-in for historical comparison.
2. Every PROMOTE recommendation includes the epoch breakdown and CURRENT-only metric.
3. Exit management becomes a first-class research domain.
4. The Command Centre report includes a "CURRENT-epoch EV" as its headline metric (currently shows ALL-epoch).

### What the research engine DOES do correctly:

- Data collection infrastructure is comprehensive and working
- Observation pipeline (shadow trades, decision traces, strategy observations) is sound
- The experiment framework (base, runners, registry, reports) is well-designed
- Individual experiment LOGIC is mostly correct (the issue is input data, not algorithms)
- Decision Gates correctly define what coverage is needed
- The architecture can discover truth — it just needs epoch hygiene

**The engine is architecturally capable but operationally unreliable due to epoch contamination in existing reports.**
