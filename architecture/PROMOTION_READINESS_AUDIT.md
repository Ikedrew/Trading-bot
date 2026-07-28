# Promotion Readiness Audit

## CRITICAL FINDING: All "PROMOTE" Recommendations Are Based on Obsolete Data

The research reports recommending PROMOTE (R3, R4, R5) and POSITIVE_EDGE (Q19) were computed using **ALL-epoch data** (n=901) which includes LEGACY and TRANSITIONAL records from a fundamentally different architecture version.

**CURRENT-epoch reality (n=846):**

| Metric | Q19 Report (ALL epochs) | CURRENT Epoch (Real) |
|--------|------------------------|---------------------|
| Sample size | 901 | 846 |
| EV | **+0.675R** | **-0.200R** |
| Win rate | 45.8% | **33.3%** |
| Avg win R | 2.30 | **0.33** |
| Avg loss R | 0.83 | **0.48** |
| Profit factor | 2.79 | **<1.0** |
| Edge | STRONG_EDGE | **NEGATIVE EDGE** |

**The system has NEGATIVE expected value in its current architecture.**

Additional CURRENT-epoch facts:
- 78.7% of trades exit by timeout (max_bars)
- Only 0.5% hit take profit
- 20.8% hit stop loss
- Average MFE = 0.696R (price moves favourably 0.7R on average before timing out)
- Only 18.7% of trades ever reach 1.0R

---

## SECTION 1: Immediate Promotions

### 🔴 NONE

There are **zero** research findings ready for immediate implementation.

Every PROMOTE recommendation in the existing reports is based on data that does not represent the current system:

| Report | Recommendation | Why BLOCKED |
|--------|---------------|-------------|
| R3 (Ruin) | PROMOTE | Used synthetic inputs (WR=80%, avg_win=2.0R) — not real CURRENT data (WR=33%, avg_win=0.33R) |
| R4 (Drawdown) | PROMOTE | Based on ALL-epoch R-multiples which include +0.675R EV. Current EV is -0.20R — halt threshold is meaningless when system loses money |
| R5 (Sizing) | PROMOTE | Based on ALL-epoch data. Simulating Fixed 0.5% on CURRENT data: **-57.2% return, 57.9% max drawdown** |
| Q19 (EV) | POSITIVE_EDGE | ALL-epoch contamination. CURRENT epoch shows -0.20R |
| Q1 (Weights) | WEIGHT_ADJUSTMENT | Based on n=237 with 19.9% join rate, epoch unclear. Cannot trust without CURRENT-epoch reproduction |
| D2/Q4 (Calibration) | PROMOTE_CALIBRATION | Calibrating a system with -0.20R EV improves nothing — there is no edge to preserve |

---

## SECTION 2: Conditional Promotions

### 🟡 Fix Exit Mechanism (Evidence-backed but not a "promotion")

**Research question:** Why do 78.7% of trades time out?

**Evidence:**
- CURRENT epoch: 666/846 trades exit by `max_bars_timeout`
- Average MFE = 0.696R — price DOES move favourably
- Only 4 trades hit take profit (0.5%)
- 30.3% of trades reach 0.5R MFE

**Interpretation:** The system IS detecting directional movement (MFE=0.7R proves the entries contain some signal), but the exit mechanism fails catastrophically. Take-profit targets are unreachable, and the system holds until timeout, by which time profits have reversed.

**What the evidence supports:** 
- Reducing take-profit distance (currently too far — only 0.5% hit it)
- OR implementing a trailing stop to capture the 0.7R average MFE

**Contradiction:** No experiment has directly tested alternative exit distances. The M9 trailing stop analysis from earlier research showed a realistic trailing stop converts -0.19R to +0.08R. This is the closest evidence available.

**Verdict:** 🟡 Conditional — requires A/B shadow test of modified TP/trailing before production change.

---

### 🟡 Score Recalibration (D2/Q4)

**Evidence:** Score IS monotonically related to win rate (confirmed). 15pp miscalibration exists.

**Contradiction:** With EV = -0.20R, improving calibration of a losing system does not create edge. It makes probability estimates more accurate, but accurate estimates of negative EV are still negative.

**Verdict:** 🟡 Implement ONLY if exit mechanism is fixed first. Correct order: fix exits → validate new EV → then calibrate.

---

## SECTION 3: Blocked Promotions

### 🔴 R3 — Probability of Ruin: BLOCKED

**Claimed finding:** P(ruin) = 0%, survival = 100%, n=100.
**Evidence quality:** Report used WR=80%, avg_win=2.0R, avg_loss=1.0R — these are NOT from the CURRENT epoch (real: WR=33%, avg_win=0.33R). The R3 input appears to be either LEGACY data or synthetic test values.
**Reality check:** With CURRENT EV = -0.20R, probability of ruin is effectively 100% over time.
**Blocking contradiction:** Complete disconnect between R3 inputs and CURRENT system performance.
**Verdict:** 🔴 Do NOT promote. Re-run R3 with CURRENT-epoch data. The result will show P(ruin) approaching certainty.

### 🔴 R4 — Drawdown Halt: BLOCKED

**Claimed finding:** Halt at 50% DD, resume at 25%.
**Evidence quality:** Based on ALL-epoch R-multiples (EV=+0.675R). The recommendation to halt at 50% DD assumes the system recovers — which requires positive EV.
**Reality check:** With CURRENT EV = -0.20R, the system will monotonically approach and exceed any halt threshold. The halt threshold is correct as a concept, but the implication should be "halt the system NOW" not "halt if it drops 50%."
**CURRENT-epoch simulation:** Fixed 0.5% sizing produces -57.2% return (already past the 50% halt).
**Blocking contradiction:** Halt threshold is designed for a profitable system experiencing variance. This is a losing system experiencing its expected outcome.
**Verdict:** 🔴 Do NOT promote as a "recovery" mechanism. The system should be halted due to negative EV, not because of temporary drawdown.

### 🔴 R5 — Position Sizing: BLOCKED

**Claimed finding:** Fixed 0.5% optimal (1917% return, 46% DD).
**Evidence quality:** Based on ALL-epoch data (n=901, EV=+0.675R).
**CURRENT-epoch reality:** Fixed 0.5% on CURRENT data = **-57.2% return, 57.9% max drawdown**.
**Blocking contradiction:** Position sizing optimisation on a losing system produces the "least bad" way to lose money. No sizing model turns -0.20R EV into profit.
**Verdict:** 🔴 Do NOT promote. Position sizing is irrelevant until EV is positive.

### 🔴 Q1 — Weight Adjustment: BLOCKED

**Claimed finding:** Increase confirmation_pre weight, decrease pattern_quality.
**Evidence quality:** n=237 matched outcomes. Join rate only 19.9%. Epoch composition unknown. The matched 237 may be disproportionately LEGACY/TRANSITIONAL.
**Reality check:** Even if the weight adjustment is correct, changing weights on a system with -0.20R EV and 78.7% timeout rate will not create positive EV.
**Blocking contradiction:** Weight optimisation assumes the scoring framework is fundamentally sound and only needs tuning. But 78.7% timeout rate suggests the problem is not in pattern selection (scoring) but in exit logic.
**Verdict:** 🔴 Do NOT promote. Root cause is exit mechanism, not scoring weights.

---

## SECTION 4: Research Still Collecting

| Question | Status | What's Needed |
|----------|--------|--------------|
| Strategy Intelligence value | n=159 observations | n≥500 with outcome linkage |
| M3: Phase improves prediction | n=728, thin cells | n≥100 per phase×regime cell |
| M4: Regime×phase×strategy | Many cells n<10 | Larger per-cell samples |
| E4: Strategy×pattern edge | Thin cells | More data per combination |
| X4: Shadow vs live gap | 0 matched | Fix correlation_id in trade_truth |
| S2/S6: Horizon effect | ~50% coverage | Clean horizon field in CURRENT |

---

## Contradiction Analysis Summary

| Finding | Supporting Experiment | Contradicting Evidence | Resolution |
|---------|---------------------|----------------------|-----------|
| EV = +0.675R | Q19 (ALL epochs, n=901) | M9 (CURRENT epoch, n=728): ALL phases negative EV | **Q19 is WRONG for current system.** LEGACY/TRANSITIONAL data inflates EV. Real CURRENT EV = -0.20R |
| P(ruin) = 0% | R3 (n=100, WR=80%) | CURRENT epoch WR=33%, EV=-0.20R | **R3 inputs are not real data.** Recompute with WR=33% → P(ruin) ≈ 100% |
| Fixed 0.5% optimal | R5 (ALL epochs, n=901) | CURRENT simulation: -57.2% | **R5 is misleading for current architecture.** The "optimal" sizing still loses |
| Scoring components predictive | Q1 (n=237, epoch mixed) | 78.7% timeout rate means exit, not entry, is the problem | **Q1 may be correct but irrelevant** — fixing scoring doesn't fix exits |
| TWEEZER_BOTTOM/REVERSAL has edge | M9 (n=37, +0.098R) | n=37 is below statistical significance threshold | **Possibly real but insufficient evidence.** Need n≥100 |

---

## Answers to Final Questions

### 1. "If the bot were deployed today, which research findings would improve profitability if implemented immediately?"

**None.**

The system has NEGATIVE expected value (-0.20R per trade) in its CURRENT epoch. No scoring adjustment, position sizing change, or strategy classification will convert -0.20R into positive EV. The evidence clearly shows:

- Entries have some directional signal (MFE = 0.70R)
- The exit mechanism destroys that signal (78.7% timeout, 0.5% TP hit rate)
- All experiment reports recommending PROMOTE were based on obsolete data

The single evidence-backed change that MIGHT improve profitability is:
- **Reduce take-profit distance** or **implement trailing stop** to capture the 0.7R MFE before it reverses

But this has NOT been validated by a completed experiment in the current architecture.

### 2. "If absolutely no more code were written and only more data were collected, which future promotions would likely become available over the next 3–6 months?"

**None would become "available" because the fundamental problem is not sample size — it's that the system loses money.**

More data will only confirm with higher confidence that:
- CURRENT-epoch EV is approximately -0.20R
- 78.7% of trades time out
- Take profit is unreachable
- The exit mechanism is the primary failure mode

The data that WOULD become useful:
- Strategy observation data (n=159→n=5000+) would confirm whether strategy-context matching improves directional accuracy — but this still doesn't fix exits
- Per-phase data (M9/M10) with larger cells might identify one or two pattern×phase combinations with genuine edge — these could be selectively traded

**The honest answer:** Without fixing the exit mechanism, no amount of data collection will produce a promotion-ready finding. The research architecture is working correctly — it has successfully identified that the system loses money and WHY (exits, not entries).
