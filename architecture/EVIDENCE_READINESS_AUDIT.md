# Evidence Readiness Audit

## Current Data Inventory

| Dataset | Records | Notes |
|---------|---------|-------|
| Shadow trades | 2,250 | 846 CURRENT (38%), 992 TRANSITIONAL (44%), 412 LEGACY (18%) |
| Decision traces | 2,248 | Full pipeline diagnostics |
| Trade truth (live) | 4,931 | Real broker execution results |
| Strategy observations | 159 | Recently started collecting (Observer #7) |
| Decision ledger | 3,789 | Decision records |
| Execution context | 4,010 | Infrastructure snapshots |
| Assessments | 754 | Opportunity assessment snapshots |
| Research reports | 15 | Generated experiment outputs |

---

## Research Questions That Can Be Answered TODAY

These have sufficient evidence and completed analysis:

| ID | Question | Sample | Confidence | Finding | Recommended Action |
|---|---|---|---|---|---|
| E1/Q19 | What is the system's true EV? | n=901 | HIGH | EV = +0.675R, significant (p<0.001), profit factor 2.79 | **Verified positive edge exists in shadow trades** |
| D1/Q1 | Which scoring components predict outcomes? | n=237 | HIGH | Best: confirmation_pre, bias_stability, bias_alignment. Worst: pattern_quality, h4_alignment | Ready for weight adjustment |
| D2/Q4 | Is probability calibrated? | n=1,220 | HIGH | Miscalibrated by 15pp. Score is monotonically related to win rate but needs recalibration | Ready for calibration |
| R3 | What is probability of ruin? | n=901 | HIGH | P(ruin) ≈ 0% (both analytical and Monte Carlo). System survival acceptable | No action needed |
| R4 | At what drawdown should system halt? | n=901 | MEDIUM | Halt at 50% DD. Max observed DD: 108.3%. Avg recovery: 89.6 trades | Promote halt threshold |
| R5 | What position sizing is optimal? | n=901 | HIGH | Fixed 0.5% (return 1917%, DD 46.3%). Kelly=26.26% (too aggressive) | Promote Fixed 0.5% |
| D5/Q3 | What are missed opportunity costs? | n=4,531 | HIGH | 4,531 rejected decisions available for analysis | Review filter criteria |
| M1/Q6 | Does regime predict outcomes? | n=1,220 | HIGH | Regime distribution mapped. Analysis complete | Monitor |
| M9 | Which patterns work in which phases? | n=728 | HIGH | Best: TWEEZER_BOTTOM in REVERSAL (+0.098R, n=37). Most cells negative EV | Continue collecting per-cell |
| M10 | Do phases need different strategy families? | n=728 | HIGH | Interaction detected but no HIGH-confidence positive EV cell yet | Continue collecting |

### Evidence-Supported Decisions Available Now:

1. **Position sizing: promote Fixed 0.5%** — R5 shows clearly superior risk-adjusted returns vs Kelly and other models.
2. **Score calibration: promote empirical curve** — D2/Q4 confirms 15pp miscalibration. ScoreCalibrator infrastructure exists.
3. **Weight adjustment: feasible** — D1/Q1 identifies confirmation_pre as best predictor, pattern_quality as worst.
4. **Ruin risk: acceptable** — R3 confirms zero probability of ruin at current position sizes.
5. **Drawdown halt: 50% threshold** — R4 recommends automatic suspension at 50% DD.

---

## Research Questions That Are CLOSE

These have partial evidence and need more data:

| ID | Question | Sample | Missing | Est. Time |
|---|---|---|---|---|
| M3 | Does phase improve prediction beyond regime? | n=728 CURRENT | Need n≥100 per phase×regime cell. Current: 5 phases have data but n<50 in some combinations | 4-8 weeks |
| M4 | Regime × phase × strategy edge? | n=728 | Need clean strategy + phase + regime in same records (41% coverage currently) | 4-8 weeks |
| E3/S1 | Which strategy types have positive EV? | n=1,220 | Strategy field coverage ~75%. Need CURRENT-epoch trades with clean strategy field | 2-4 weeks |
| S4 | Strategies specialised for phases? | n=728 | Phase × strategy breakdown; most cells have n<30 | 6-12 weeks |
| E4 | Strategy × pattern combinations? | n=728 | Many cells have n<10. Need more data per combination | 8-16 weeks |
| D3/Q21 | Does EV gate improve outcomes? | n=1,220 | EV gate currently disabled. Need side-by-side comparison when enabled | Requires A/B test |
| Strategy Intelligence | Does strategy taxonomy add value? | n=159 | Strategy observations just started. Need n≥100 per strategy×phase cell | 8-16 weeks |

---

## Research Questions That Cannot Yet Be Answered

| ID | Question | Why Not | What's Missing |
|---|---|---|---|
| X4/Q16 | Shadow vs live execution gap | **BLOCKED** — 0 matched shadow↔live trades found | Need live execution with correlation_id propagating to trade_truth |
| X5 | Execution leakage quantification | Same as X4 — requires shadow↔live join | correlation_id in trade_truth records |
| M5 | Phase transitions predict drawdown | No temporal phase history linked to equity curve | MarketContext persistence + equity curve time series |
| M8 | Phase transition behaviour | Requires sequential phase observations with outcomes | Longer strategy observation collection period |
| L2/Q15 | System improvement tracking | Only 15 reports exist. Need pre/post architecture change comparison | More time + architecture version tracking |
| L4/Q17 | Market behaviour drift | Requires 6+ months of stable data to compare periods | Time |
| X1/Q11 | Slippage model | Trade truth exists (n=4,931) but slippage fields need verification | Verify slippage field population |
| X2/Q12 | Broker failure patterns | Execution context exists (n=4,010) but retcode analysis not yet run | Run experiment |
| S2/S6 | Horizon affects expectancy | Trade horizon field has ~50% coverage. Need separate horizon in CURRENT records | Verify horizon field in recent trades |
| D6 | Portfolio ranking quality | Need cycles with multiple simultaneous signals | Portfolio ranking shadow comparison data |

---

## Detailed Assessment Per Research Question

| ID | Question | Stage | n | Conf | Evidence | Missing | Action |
|---|---|---|---|---|---|---|---|
| E1 | True system EV | ✅ Enough evidence | 901 | HIGH | EV=+0.675R, p<0.001 | Walk-forward confirmation | Validate OOS |
| E2 | Pattern expectancy | ✅ Enough evidence | 1,220 | HIGH | Per-pattern breakdown available | Degradation over time | Monitor |
| E3 | Strategy expectancy | 🔄 Collecting | 1,220 | MEDIUM | Strategy field 75% populated | Need CURRENT-epoch clean strategy | Continue |
| E4 | Strategy×pattern | 🔄 Collecting | 728 | LOW | Many cells n<10 | Per-cell sample size | Continue |
| E5 | Walk-forward validation | 📋 Implemented | 901 | - | Runner exists, not yet run on latest data | Run experiment | Run E5 |
| M1 | Regime predicts outcomes | ✅ Enough evidence | 1,220 | HIGH | Regime distribution mapped | Regime×outcome correlation depth | Monitor |
| M2 | Regime edge by strategy | 🔄 Collecting | 728 | LOW | Partial coverage | Clean strategy + regime in same records | Continue |
| M3 | Phase improves prediction | 🔄 Collecting | 728 | MEDIUM | Phase exists in 41% | Need 80%+ phase coverage in CURRENT | Continue |
| M4 | Regime×phase×strategy | 🔄 Collecting | 728 | LOW | All three fields needed | n<30 per cell | Continue |
| M5 | Phase transitions | 📋 Designed | - | NONE | No temporal phase history linked | Phase change time series | Future |
| M6 | Phase expectancy | ✅ Enough evidence | 728 | HIGH | M9 computed per-phase EV | All negative (best=-0.04R) | No action |
| M7 | Regime+phase interaction | 🔄 Collecting | 728 | LOW | Some combinations thin | Need bigger cells | Continue |
| M8 | Phase transition behaviour | 📋 Designed | - | NONE | Requires sequential observations | Longer collection | Future |
| M9 | Phase×pattern | ✅ Enough evidence | 728 | HIGH | 22 cells analysed | Per-cell n varies (10-242) | Monitor |
| M10 | Phase×family | ✅ Enough evidence | 728 | HIGH | Interaction detected | No promotable cell yet | Continue |
| M11 | Context > pattern? | 📋 Designed | - | NONE | Not yet implemented | Experiment runner | Build |
| D1 | Components predict R | ✅ Enough evidence | 237 | HIGH | Best/worst predictors identified | Join rate only 20% | Continue |
| D2 | Confidence calibration | ✅ Enough evidence | 1,220 | HIGH | 15pp miscalibration confirmed | Recalibration implementation | Implement |
| D3 | EV gate value | 🔄 Collecting | 1,220 | MEDIUM | EV gate disabled; shadow comparison exists | A/B comparison | Enable A/B |
| D4 | Optimal thresholds | ✅ Enough evidence | 1,220 | HIGH | Threshold analysis done | Context segmentation | Monitor |
| D5 | Missed opportunities | ✅ Enough evidence | 4,531 | HIGH | Top rejection stages identified | Counterfactual R for rejected | Analyse |
| D6 | Portfolio ranking | 🔄 Collecting | - | LOW | Need multi-signal cycles | Shadow comparison data | Continue |
| S1 | Strategy type EV | 🔄 Collecting | 1,220 | MEDIUM | Strategy field partially populated | Clean strategy coverage | Continue |
| S2 | Horizon EV | 🔄 Collecting | - | LOW | Horizon field ~50% | Separate horizon in CURRENT | Continue |
| S4 | Strategy×phase | 🔄 Collecting | 728 | LOW | Thin cells | n≥30 per cell | Continue |
| S5 | Strategy identity EV | 🔄 Collecting | 1,220 | MEDIUM | Same as S1 | Clean separation | Continue |
| X1 | Slippage model | 📋 Implemented | 4,931 | MEDIUM | Trade truth exists | Verify slippage fields | Verify |
| X2 | Broker failures | 📋 Implemented | 4,010 | MEDIUM | Execution context exists | Run experiment | Run |
| X3 | Session quality | ✅ Enough evidence | 14,189 | HIGH | Execution context analysed | Session segmentation | Monitor |
| X4 | Shadow vs live gap | ❌ Blocked | 0 | NONE | Zero matched trades | correlation_id in trade_truth | Fix lineage |
| R1 | Risk model effectiveness | ✅ Enough evidence | 3,789 | HIGH | Guard blocks counted | Counterfactual outcomes | Analyse |
| R3 | Probability of ruin | ✅ Enough evidence | 901 | HIGH | P(ruin)=0% | Acceptable | No action |
| R4 | Drawdown threshold | ✅ Enough evidence | 901 | MEDIUM | Halt at 50% DD | Validate with longer history | Promote |
| R5 | Position sizing | ✅ Enough evidence | 901 | HIGH | Fixed 0.5% optimal | Validate walk-forward | Promote |
| L1 | Pattern degradation | ✅ Enough evidence | 1,220 | HIGH | Per-pattern tracking active | Time comparison | Monitor |
| L7 | Shadow A/B | 📋 Implemented | - | NONE | No A/B experiment running | Define control/candidate | Design |
| P1 | Promotion impact | 📋 Implemented | - | NONE | No promotion proposals yet | Validated findings | Wait |

---

## Roadmap

### Immediate (can act now)

1. **Promote Fixed 0.5% position sizing** — R5 validated. Clear winner with acceptable drawdown.
2. **Promote 50% drawdown halt** — R4 recommends. Prevents catastrophic loss.
3. **Implement score recalibration** — D2/Q4 confirms 15pp miscalibration. ScoreCalibrator ready.
4. **Run E5 walk-forward** — Runner exists. Data sufficient (n=901). Validates whether EV=+0.675R holds out-of-sample.
5. **Run X1/X2 experiments** — Trade truth (n=4,931) and execution context (n=4,010) exist but experiments not recently re-run.

### Next Milestone (after more data)

6. **Strategy intelligence validation** — Observer #7 collecting (n=159). Need n≥500 for first meaningful analysis. ~4-8 weeks.
7. **Phase×strategy interaction (M3/M4)** — CURRENT-epoch phase coverage at 41%. Increasing with MarketContext improvements. ~4-8 weeks.
8. **Fix shadow↔live lineage (X4)** — Correlation_id not propagating to trade_truth. One code fix enables execution quality research.
9. **Per-cell M9/M10 validation** — Best cell (TWEEZER_BOTTOM/REVERSAL) has n=37. Need n≥50-100 for promotion confidence. ~8-16 weeks.

### Long-term (requires future infrastructure)

10. **Market behaviour drift (L4)** — Requires 6+ months of stable collection for temporal comparison.
11. **Phase transition prediction (M5/M8)** — Requires sequential MarketContext persistence with equity curve linkage.
12. **Automated strategy promotion (P1)** — Requires validated findings passing all decision gates first.
13. **A/B strategy testing (L7)** — Requires defining control vs candidate strategies and running both in shadow.

---

## Critical Observation

The Q19/E1 report shows **EV = +0.675R** on n=901 shadow trades — this is an extraordinarily high value that warrants skepticism. Combined with the M9 finding that **every phase has negative EV** (when broken down by CURRENT-epoch trades n=728), there is a discrepancy:

- Q19 uses ALL shadow trades (901, mixed epochs)
- M9 uses only CURRENT-epoch trades (728)
- M9 shows overall negative EV per phase

This suggests the **LEGACY and TRANSITIONAL epoch trades may have inflated the Q19 result**. The true CURRENT-epoch EV is likely negative (M9 phase summaries show EV ranging from -0.20R to -0.04R).

**The most important immediate action is: re-run E1 (expected_value) on CURRENT-epoch trades only.** This will reveal the real edge (or lack thereof) in the current pipeline.
