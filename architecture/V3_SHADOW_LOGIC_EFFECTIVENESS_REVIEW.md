# V3 Shadow Logic Effectiveness Review

**Date:** 2026-07-28
**Dataset:** ~400-470 records per layer, 29 linked outcomes
**Status:** Early operational — behavioural patterns emerging

---

## Layer Health Table

| Layer | Health | Evidence | Recommendation |
|---|---|---|---|
| Market Understanding | PROMISING | 438 records, producing structured observations | KEEP |
| Market Context | PROMISING | 471 records, three sub-engines producing data | KEEP |
| Opportunity Assessment | PROMISING | 57% INTERESTING, 37% LOW, 3% HIGH — sensible distribution | MONITOR |
| Horizon Assessment | UNCERTAIN | 55% SCALP, 10% INTRADAY, 35% NO_HORIZON — SCALP dominates | RESEARCH |
| Entry Behaviour | WEAK | 51% UNKNOWN behaviour, only 3% VALID confirmation | RESEARCH |
| Risk Assessment | PROMISING | 425 records, producing geometry evaluations | KEEP |
| Execution Assessment | UNCERTAIN | 35% NOT_EXECUTABLE, 33% CONSTRAINED, 3% READY — very selective | MONITOR |

---

## 1. Market Understanding

**Health: PROMISING**

438 records produced. The layer is generating observations across all timeframes. No evidence suggesting structural problems.

**Evidence:** Data flows correctly to downstream layers. Confidence scoring differentiates between data-rich and data-poor cycles.

**Recommendation: KEEP** — no changes needed. Producing useful input.

---

## 2. Market Context

**Health: PROMISING**

471 records. HTF Structure, Location, and Behaviour sub-engines all producing output.

**Evidence from V3 Discovery Pass 2:**
- Location consistently separates outcomes (inside OB +0.071R, discount WR 62.7%)
- Structure provides directional context (BOS alignment)
- Behaviour has NOT been validated as predictive (V2 proved regime non-useful)

**Recommendation: KEEP** — Location dominance confirmed by research. No changes to weighting justified yet.

---

## 3. Opportunity Assessment

**Health: PROMISING**

Distribution (sampled n=100):
- INTERESTING_CONTEXT: 57%
- LOW_QUALITY_CONTEXT: 37%
- HIGH_QUALITY_CONTEXT: 3%
- MIXED_CONTEXT: 3%

**Observation:** HIGH_QUALITY fires very rarely (3%). This may be appropriate (institutional zone + BOS + discount is a rare confluence) or the thresholds may be too strict.

**Evidence:** Cannot validate yet — need to compare HIGH vs LOW outcomes. The 37% LOW_QUALITY rate suggests the gate IS filtering (not passing everything).

**Recommendation: MONITOR** — wait for outcome comparison. If HIGH_QUALITY produces same EV as INTERESTING, the distinction may be unnecessary. If it genuinely separates, the strict threshold is correct.

---

## 4. Horizon Assessment

**Health: UNCERTAIN**

Distribution (n=100):
- SCALP: 55%
- NO_HORIZON: 35%
- INTRADAY: 10%

**Concern:** SCALP dominates because the SCALP prior (0.30) is highest and most conditions boost it slightly. INTRADAY requires inside-zone + quality > 0.5 + additional structure — which fires only 10% of the time.

**Is this a problem?** Maybe not. If the market genuinely produces more scalp-sized reactions than intraday moves, SCALP dominance is correct. But if inside-OB (+0.071R, the only positive finding) is an INTRADAY phenomenon, the system should be selecting INTRADAY more often at those contexts.

**Evidence gap:** Cannot determine which horizon ACTUALLY matches observed movement until MFE data is compared per horizon selection.

**Recommendation: RESEARCH** — After linking 50+ outcomes, compare: when SCALP was selected, what was the actual MFE? Does it match 5-20 pips? Or did the market move 30+ pips (meaning INTRADAY was the correct classification)?

---

## 5. Entry Behaviour

**Health: WEAK**

Distribution (n=100):
- UNKNOWN: 51%
- STRUCTURE_ALIGNMENT: 14%
- MOMENTUM_TRANSITION: 11%
- REJECTION_BEHAVIOUR: 6%
- RETEST_BEHAVIOUR: 3%
- (empty): 15%

Entry states:
- INSUFFICIENT_ENTRY_DATA: 36%
- WEAK_ENTRY_CONFIRMATION: 32%
- NO_ENTRY_CONFIRMATION: 29%
- VALID_ENTRY_CONFIRMATION: 3%

**Concern:** 51% of entries classified as UNKNOWN behaviour means the trigger detection isn't firing for half of observations. VALID_ENTRY_CONFIRMATION at only 3% means the quality bar is extremely high.

**Root cause analysis:**
- UNKNOWN = `primary_trigger == NONE` → no trigger detected → behaviour maps to UNKNOWN
- The triggers require specific conditions: BOS active, displacement active, at-zone + momentum, etc.
- Most cycles don't have BOS active AND aren't inside a zone AND don't have strong momentum → no trigger fires

**Is this correct?** Yes — if the market doesn't show confirmation behaviour, the system correctly reports "no confirmation." But 3% VALID is very selective. The question is whether this 3% corresponds to the positive-EV inside-OB moments.

**Recommendation: RESEARCH** — Compare VALID (n≈3%) outcomes vs WEAK (32%) vs NO_CONFIRMATION (29%). If VALID genuinely produces better outcomes, the strict threshold is correct. If WEAK produces similar outcomes to VALID, loosen the threshold.

---

## 6. Risk Assessment

**Health: PROMISING**

425 records. Producing geometry evaluations with spread/risk ratios.

**Evidence:** The risk model correctly identifies SCALP as higher spread/risk (29%) vs INTRADAY (10%). This aligns with V2/V3 research proving spread dominance at tight stops.

**Recommendation: KEEP** — thresholds are research priors from validated V2 findings (48% = failure, 30% = improvement from SV1). No evidence to change.

---

## 7. Execution Assessment

**Health: UNCERTAIN**

Distribution (n=100):
- NOT_EXECUTABLE: 35%
- EXECUTION_CONSTRAINED: 33%
- SIMULATED_ONLY: 29%
- READY_FOR_EXECUTION: 3%

**Concern:** Only 3% reaches READY_FOR_EXECUTION. This means the full V3 pipeline (quality opportunity + valid entry + acceptable risk) aligns in only ~3% of cycles.

**Is this too strict?** Depends on outcomes. If those 3% produce positive EV, the selectivity is the edge. If they produce the same EV as random, the gate adds no value.

**Evidence:** 29 linked outcomes exist with WR=37.9%, EV=+0.52R. But these include ALL execution states (not just READY). Need to separate outcomes BY execution state.

**Recommendation: MONITOR** — compare READY (n≈3%) vs CONSTRAINED (33%) vs SIMULATED (29%) outcomes once more data accumulates. If READY consistently outperforms, the pipeline works. If all states produce similar outcomes, the reasoning chain isn't creating value.

---

## Cross-Layer Relationships

| Transition | Strength | Evidence |
|---|---|---|
| Context → Opportunity | STRONG | 57% INTERESTING (context produces meaningful quality variation) |
| Opportunity → Horizon | MODERATE | LOW_QUALITY correctly blocks (35% NO_HORIZON matches 37% LOW) |
| Horizon → Entry | WEAK | 51% UNKNOWN entry behaviour suggests triggers aren't aligned with horizon expectations |
| Entry → Risk | MODERATE | Risk assessment runs regardless of entry state (correct — risk is about geometry) |
| Risk → Execution | STRONG | NOT_EXECUTABLE strongly correlates with missing upstream data |

**Weakest link: Horizon → Entry.** The horizon selects SCALP (55%) but entry triggers don't fire (51% UNKNOWN). This suggests the entry model expects confirmation that doesn't exist at SCALP-classified moments. The entry triggers (BOS, retest, displacement) may be better suited to INTRADAY contexts.

---

## Emerging Patterns

1. **The pipeline is appropriately selective.** 3% READY_FOR_EXECUTION means it would trade very rarely — which is correct if the edge only exists at institutional zones (V3 discovery found inside-OB at ~15% frequency).

2. **SCALP dominance may be incorrect.** The prior (0.30) and broad conditions mean SCALP wins most comparisons. But V3 research showed the signal (+0.071R) exists at institutional zones — which the INTRADAY horizon was designed to capture.

3. **Entry behaviour UNKNOWN rate is high.** 51% means the system observes opportunities but can't identify HOW the market confirms. This may be because: (a) the triggers are too specific, (b) confirmation hasn't happened yet at observation time, or (c) M5 data doesn't show the required patterns.

4. **Location continues to dominate.** The only positive research finding (inside-OB) is a location feature, supporting the 50% weighting.

---

## Suggested Engine Changes

| Engine | Suggested Modification | Reason | Confidence |
|---|---|---|---|
| None | — | — | — |

**No engine changes currently justified.**

Reasoning: The pipeline has only 29 linked outcomes and ~400 records per layer. The distributions (3% READY, 51% UNKNOWN entry, 55% SCALP) may be correct for a selective system in a RANGE-dominated market. Without comparing outcomes BY state, any modification is premature optimisation.

---

## Research Prior Update

| Prior | Current Value | Status | Evidence |
|---|---|---|---|
| SCALP baseline | 0.30 | UNCHANGED | No outcome data per horizon yet |
| INTRADAY baseline | 0.20 | UNCHANGED | Need MFE comparison |
| EXTENDED baseline | 0.10 | UNCHANGED | Very few events |
| Location weight 50% | 0.50 | STRENGTHENED | Inside-OB remains only positive finding |
| Structure weight 30% | 0.30 | UNCHANGED | BOS alignment directionally useful but unvalidated |
| Behaviour weight 20% | 0.20 | WEAKENED (slightly) | V2 proved regime/session non-predictive; no new evidence |
| Spread/risk threshold 20% | 0.20 | VALIDATED | SV1 proved wider stops improve by +0.47R |
| Spread/risk failure 35%+ | 0.35 | VALIDATED | V2 CE1 proved 48% is definitively non-viable |

---

## Final Assessment

> "Based on the evidence collected so far, is V3 beginning to converge toward a measurable trading model, or is the current research indicating that one or more reasoning engines need to evolve before that is likely?"

**V3 is converging correctly, but has not yet proven convergence.**

Evidence supporting convergence:
- The pipeline correctly identifies ~3% of cycles as READY (extremely selective)
- Location dominance (inside-OB +0.071R) is architecturally captured as the highest-weight factor
- Risk geometry correctly identifies INTRADAY as cost-viable (10% spread/risk)
- The system produces structured, comparable data every cycle

Evidence that convergence is unproven:
- Only 29 linked outcomes (need 50+ minimum)
- Cannot separate outcomes by execution state yet (need more READY events)
- Entry behaviour layer has 51% UNKNOWN (may need evolution or may be correct)
- Horizon selection hasn't been validated against actual movement

**The architecture does NOT need to evolve.** What's needed is TIME — more cycles with the pipeline running, more outcomes linked, and then the Minimum Validation Set (V1-V4) can determine if the reasoning produces positive EV.

**Priority:** Collect data → link outcomes → run RG1 → compare outcomes by execution state.
