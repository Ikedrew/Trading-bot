# V3 Research Question Validation Review

**Date:** 2026-07-28
**Status:** Architecture complete — research capability assessment

---

## 1. Research Question Priority Table

| Priority | Question | Layer | Status | Reason |
|---|---|---|---|---|
| **1** | Does INTRADAY horizon produce positive cost-adjusted EV? | Risk + Horizon | **READY NOW** | RG1 experiment uses existing shadow trade progression data. Core viability question. |
| **2** | Does inside-OB confirm at n=50 with INTRADAY geometry? | Location + Risk | **NEEDS MORE DATA** | Currently n=23. Need ~27 more events (~180 records). |
| **3** | Does opportunity quality (HIGH vs LOW) separate outcomes? | Opportunity | **READY NOW** | 158 linked records exist. Can compare quality states vs shadow outcomes. |
| **4** | Does discount outperform premium after cost adjustment? | Location | **READY NOW** | n=59 discount, n=36 premium in linked set. |
| **5** | Which entry behaviour produces the best outcomes? | Entry | **NEEDS MORE DATA** | Entry assessment just deployed. No linked outcomes for V3 entry data yet. |
| **6** | Does wider stop (M15 structure) survive where M5 fails? | Risk | **READY NOW** | SV1 proved +0.47R improvement. Can re-simulate with location filter. |
| **7** | Does liquidity target outperform fixed RR? | Risk + Execution | **NEEDS MORE DATA** | Need shadow trades with progression data + V3 liquidity positions. |
| **8** | Which horizon actually matches observed movement size? | Horizon | **NEEDS MORE DATA** | Horizon assessment just deployed. Need outcomes per predicted horizon. |
| **9** | Does RETEST_ENTRY outperform other triggers? | Entry | **PREMATURE** | Entry triggers just deployed. Zero outcome data. Need 200+ cycles. |
| **10** | Does execution cost degrade expectancy vs theoretical? | Execution | **PREMATURE** | Execution layer just deployed. No live comparison possible yet. |
| **11** | Does regime (TRENDING vs RANGING) matter for V3? | Behaviour | **NOT REQUIRED NOW** | V2 proved regime non-predictive. 92% RANGE means no variation to test. |
| **12** | Does session timing matter for V3? | Behaviour | **NOT REQUIRED NOW** | Only OFF session data exists. Cannot test until LONDON/NY collected. |
| **13** | Does M1 micro-structure improve entry timing? | Entry (M1) | **PREMATURE** | M1 layer is experimental. No M1 candle data flowing. |
| **14** | Can the probability model be calibrated from V3? | CQ4 | **NEEDS MORE DATA** | Need 200+ uniformly-populated linked records. |

---

## 2. Missing Research Questions

### MQ1: Does the V3 shadow pipeline produce DIFFERENT decisions than V1 production?

**Why it matters:** If V3 shadow always agrees with V1 (same entry timing, same direction), then V3 adds no information. The value of V3 is in DIVERGENCE — trading when V1 doesn't, or NOT trading when V1 does.

**Missing data:** Need to compare V3 `execution_state=READY_FOR_EXECUTION` events against V1 `engine_result.action`. Are they the same opportunities?

**Required:** Cross-reference V3 ExecutionAssessment timestamps with V1 shadow trades.

---

### MQ2: What is the FALSE POSITIVE rate of the opportunity engine?

**Why it matters:** If HIGH_QUALITY_CONTEXT fires frequently but only n=23 inside-OB events produce positive EV, the engine may be too lenient.

**Missing data:** Need: count of HIGH_QUALITY_CONTEXT assessments vs count that actually matched a positive outcome.

**Required:** Link V3 opportunity assessments to V3 outcomes (shadow trade results).

---

### MQ3: What is the actual movement distribution AFTER inside-OB entry?

**Why it matters:** The horizon engine classifies expected movement (5-20, 20-50, 50+). But we don't know what ACTUALLY happens. If inside-OB produces 8-pip reactions 80% of the time, SCALP is correct and INTRADAY is wrong.

**Missing data:** MFE (maximum favourable excursion) per V3 observation linked to outcome.

**Required:** Link V3 HorizonAssessment predictions to actual shadow trade MFE.

---

### MQ4: At what point does the V3 pipeline STOP being profitable?

**Why it matters:** Even if combined context + risk geometry produces positive EV, there must be a minimum threshold. Below it, the system should NOT trade.

**Missing data:** A threshold study on context_quality score vs outcome.

**Required:** Enough linked outcomes (200+) to bin by quality and measure EV per bin.

---

## 3. Minimum Validation Set

The SMALLEST set of questions that must be answered before considering live:

| # | Question | Why Critical | Minimum Sample |
|---|---|---|---|
| **V1** | Does INTRADAY horizon at inside-OB produce cost-adjusted EV > 0? | If no → V3 cannot trade | n=50 inside-OB events with M15 stop simulation |
| **V2** | Does the opportunity quality gate reduce losing trades? | If no → the gate is useless | n=200 linked outcomes across all quality states |
| **V3** | Does the selected horizon match actual movement size? | If no → targets/stops are wrong | n=100 horizon predictions vs actual MFE |
| **V4** | Does the complete V3 pipeline (when READY_FOR_EXECUTION) outperform random? | Final integration test | n=50 READY executions with outcomes |

**If V1 fails:** Stop. No further development justified.
**If V1 passes but V2-V4 fail:** Architecture correct but thresholds need calibration.
**If all pass:** Ready for paper trading validation.

---

## 4. Architecture Gaps

### Gap 1: V3 Outcome Linkage for Shadow Pipeline Outputs

**Current state:** V3Opportunity (Phase 1-2) has outcome linkage via `v3_outcome_linker.py`. But the NEW shadow pipeline layers (Understanding → Context → Opportunity → Horizon → Entry → Risk → Execution) have NO outcome linkage yet.

**Impact:** Cannot answer any research question about whether shadow pipeline decisions are correct.

**Fix required:** A V3 Shadow Outcome Linker that links `ExecutionAssessment` records to shadow trade results via correlation_id/timestamp.

**Severity:** HIGH — this is the primary blocker for research.

---

### Gap 2: No V3-to-V1 Decision Comparison

**Current state:** V3 shadow decisions and V1 production decisions are persisted in different formats with no cross-reference.

**Impact:** Cannot answer MQ1 (does V3 produce different decisions?).

**Fix required:** Align `ExecutionAssessment.timestamp_utc` with `shadow_trade.identity.entity_id` — same join key as V3Opportunity. May already work.

**Severity:** MEDIUM — needed for divergence analysis but not for basic EV validation.

---

### Gap 3: No MFE/MAE Per Horizon Prediction

**Current state:** Shadow trades have `trade_state_progression` (bar-by-bar R). V3 HorizonAssessment predicts expected_move_min/max_pips. But there's no automated comparison.

**Impact:** Cannot validate whether horizon predictions match reality.

**Fix required:** Research script that: loads HorizonAssessments → links to shadow trade MFE → compares predicted vs actual movement size.

**Severity:** MEDIUM — needed for V3 validation question.

---

### Gap 4: No Execution Layer Real-World Validation

**Current state:** ExecutionAssessment is purely theoretical. No real spread timing, no real slippage, no real fills.

**Impact:** Cannot validate execution quality assumptions (0.2 pip slippage estimate).

**Fix required:** None NOW. Only needed when approaching live validation. The 0.2 pip estimate is conservative enough for research.

**Severity:** LOW — premature concern.

---

### Gap 5: Session Coverage Bias

**Current state:** 100% of post-Phase-2 linked data is from OFF session. Zero LONDON/NY.

**Impact:** All findings are epoch-specific and session-biased.

**Fix required:** Run bot during LONDON and NY sessions.

**Severity:** MEDIUM — operational, not architectural.

---

## 5. Research Readiness by Layer

| Layer | Data Available | Linked to Outcomes | Research Ready |
|---|---|---|---|
| Market Understanding | Yes (315+ records) | No (new format) | Partially — via V3Opportunity linkage |
| V3 Market Context | Yes (collecting) | No direct linkage | Needs shadow outcome linker |
| Opportunity Assessment | Yes (collecting) | No direct linkage | Needs shadow outcome linker |
| Horizon Assessment | Yes (collecting) | No direct linkage | Needs shadow outcome linker |
| Entry Assessment | Yes (just deployed) | No | Needs time + linkage |
| Risk Assessment | Yes (collecting) | No direct linkage | Needs shadow outcome linker |
| Execution Assessment | Yes (just deployed) | No | Needs time + linkage |

**Critical finding:** The shadow pipeline PRODUCES data but CANNOT VALIDATE it against outcomes without a Shadow Outcome Linker.

---

## 6. Final Answer

> "Is the research system now capable of discovering whether a positive expectancy trading model exists, or are there still fundamental measurement gaps?"

### Answer: ONE fundamental gap remains.

**The architecture is complete.** All 7 layers produce structured observations. The models are correct. The observer persists everything. The production system is untouched.

**The gap:** There is no automated linkage between V3 shadow pipeline outputs (particularly ExecutionAssessment) and actual trade outcomes (shadow trade results). Without this linkage, the research engine cannot compute EV for V3 decisions.

**The fix is small:** A V3 Shadow Outcome Linker (similar to existing `v3_outcome_linker.py`) that joins ExecutionAssessment records to shadow trade results via `correlation_id` / `timestamp_utc`. The join key already exists in both formats.

**Once that linker exists:**
- RG1 can run (INTRADAY geometry at inside-OB)
- Opportunity quality can be validated
- Horizon predictions can be compared to actual movement
- The complete V3 pipeline can be assessed for EV

**Priority action:** Build V3 Shadow Outcome Linker → then run Minimum Validation Set (V1-V4).
