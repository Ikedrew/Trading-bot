# Research Tracking: Strategy-Context Alignment

**Status:** Active Investigation  
**Started:** 2026-07-27  
**Last Updated:** 2026-07-27  
**Category:** Strategy Architecture Research  
**Priority:** P0 — Directly affects whether the system can achieve positive expectancy  

---

## Current Hypothesis

> Market behaviour + context + risk model + execution policy may be a better basis for positive expected value than isolated pattern signals.

Specifically: the system's edge (if it exists) is NOT in individual candlestick pattern recognition alone, but in the COMBINATION of:
1. Correct market regime identification
2. Correct market phase identification
3. Appropriate strategy family selection for those conditions
4. Pattern selection within the appropriate strategy family
5. Exit management matched to the expected price behaviour

---

## 1. Research Questions

### Answered

| # | Question | Answer | Confidence | Evidence |
|---|----------|--------|-----------|----------|
| RQ1 | Do isolated candlestick patterns predict direction? | NO (aggregate 41% directional success vs 50% random) | HIGH (n=436) | Entry Predictive Power Audit, CURRENT-epoch data |
| RQ2 | Does any single pattern show positive EV under current management? | NO — 0/10 patterns positive with RR=2.0 and no trailing | HIGH (n=449) | Pattern Selection Value Audit |
| RQ3 | Is the negative EV caused by entries or exits? | EXITS — trailing stop converts system from -0.19R to +0.08R (realistic) | HIGH (n=92 bar-by-bar, n=594 MFE-based) | Exit Management Simulation + Validation |
| RQ4 | Does EVENING_STAR predict direction above random? | YES — 60% directional success, 48% reach 1R | MEDIUM (n=58) | Pattern Selection Audit |

### Open

| # | Question | Status | Blocking? |
|---|----------|--------|-----------|
| RQ5 | Does TWEEZER_BOTTOM have positive EV specifically in RANGE + REVERSAL phase? | Athena evidence suggests YES — needs validation | YES |
| RQ6 | Are different strategy families (reversal vs continuation vs breakout) required for different phases? | Hypothesised — insufficient data | YES |
| RQ7 | Does combining regime + phase + strategy family produce reliable conditional EV? | Unknown — requires multi-factor data | YES |
| RQ8 | Would a trailing stop on regime-filtered, phase-matched trades produce significant positive EV? | Unknown — requires combined simulation | NO |
| RQ9 | Are the current patterns appropriate for the conditions they trade in? | Partially answered — most patterns are reversal-type but the system trades in all phases | NO |

---

## 2. Evidence Collected

### Source: CURRENT-epoch Shadow Trades (n=594)

- **Overall EV:** -0.190R per trade
- **Win rate:** 33.8%
- **Take-profit rate:** 0.5% (3/594)
- **Timeout rate:** 80.1%
- **Stop-loss rate:** 19.4%

### Source: MFE/MAE Analysis (n=436 RR2 trades)

- **92% of entries produce SOME favourable movement** (MFE > 0)
- **53% produce minimal movement** (MFE < 0.25R)
- **16% reach 1R favourably**
- **11% reach 2R (current TP target)**
- **Directional success overall:** 41% (below random)
- **EVENING_STAR directional success:** 60% (above random, n=58)
- **HAMMER directional success:** 88% (n=25, but appears inverted)

### Source: Exit Management Simulation

- **Trailing stop (optimistic):** +0.329R per trade
- **Trailing stop (realistic bar-by-bar):** +0.082R per trade
- **Look-ahead bias factor:** 4x (optimistic overstates by 4x)
- **Even at 50% discount:** EV remains positive and statistically significant

### Source: Athena Context Investigation (2026-07-27)

- TWEEZER_BOTTOM shows conditional positive expectancy in RANGE regime + REVERSAL market phase
- Same pattern shows negative expectancy in other phase combinations
- The system's pattern library is predominantly reversal patterns
- IMPULSE, CONTINUATION, and BREAKOUT market phases have no matching pattern strategy families

### Source: Regime Distribution

- **RANGE:** 92% of CURRENT-epoch data
- **TRENDING:** 8% (all stop-loss, EV=-1.0R)
- **TRANSITIONAL:** <1%

---

## 3. Confirmed Findings

| # | Finding | Confidence | Implication |
|---|---------|-----------|-------------|
| CF1 | Isolated candlestick patterns do not reliably predict direction at M5 FX | HIGH | Pattern-only strategy is insufficient |
| CF2 | The current exit management (RR=2.0 fixed, 60-bar timeout) destroys potential edge | HIGH | Exit reform is necessary regardless of entry changes |
| CF3 | EVENING_STAR contains above-random directional prediction | MEDIUM | At least one pattern has signal value in the right conditions |
| CF4 | Trailing stop captures edge that fixed TP does not | HIGH | Exit architecture must change |
| CF5 | The scoring model is inversely correlated with outcome | HIGH | Score CANNOT be used as-is for trade selection |
| CF6 | Market phase matters — same pattern performs differently by phase | MEDIUM (Athena evidence) | Context-conditional strategy required |
| CF7 | The pattern library is reversal-biased | CONFIRMED | System cannot trade continuation or breakout phases |

---

## 4. Unproven Hypotheses

| # | Hypothesis | Current Evidence | What Would Prove/Disprove |
|---|-----------|-----------------|--------------------------|
| H1 | Strategy families (REVERSAL/CONTINUATION/BREAKOUT) should gate pattern selection | Logical argument + phase mismatch observation | Need outcome data partitioned by strategy_family × phase |
| H2 | TWEEZER_BOTTOM is profitable specifically in RANGE + REVERSAL conditions | Athena query showed conditional positive EV | Need 100+ samples in that specific condition |
| H3 | A system trading ONLY reversal patterns in REVERSAL phase would be profitable | Indirect evidence from phase analysis | Need sufficient data with correct phase labels |
| H4 | Continuation patterns would be profitable in IMPULSE phase | No evidence — no continuation patterns exist | Requires new pattern implementation (future) |
| H5 | The optimal system is regime→phase→strategy_family→pattern→exit | Logical inference from accumulated evidence | Full pipeline simulation with conditional logic |
| H6 | The scoring model should be replaced by phase-conditional probability | Score is inversely predictive (confirmed) | Need empirical calibration by context |

---

## 5. Future Experiments Required

### Next Priority (can be done with current data)

| # | Experiment | Data Required | Available? | Expected Insight |
|---|-----------|---------------|-----------|-----------------|
| FE1 | TWEEZER_BOTTOM EV in RANGE + REVERSAL phase (with trailing) | Shadow trades with phase labels | PARTIAL — phase coverage 0% in most shadow data | Conditional pattern viability |
| FE2 | EVENING_STAR with trailing stop shadow A/B | 100+ EVENING_STAR trades | YES (n=60, accumulating) | Validates exit improvement |
| FE3 | Score component removal experiment | Current data + component scores | YES (via decision_trace lineage) | Identifies harmful score components |
| FE4 | Phase-stratified pattern performance | Shadow trades with market_phase populated | WAITING — need more phase-labelled data | Context-conditional EV |

### Requires New Data Collection

| # | Experiment | What's Needed | Timeline |
|---|-----------|---------------|----------|
| FE5 | Strategy family × phase × pattern matrix | market_phase populated consistently in shadow trades | 2-4 weeks of clean collection |
| FE6 | Continuation pattern shadow testing | New pattern detectors (not yet built) | Requires implementation |
| FE7 | Breakout pattern shadow testing | New pattern detectors (not yet built) | Requires implementation |
| FE8 | Full conditional EV simulation (regime + phase + family + trail) | All above combined | After FE1-FE5 complete |

---

## 6. Conditions Required Before Implementing Architecture Changes

### Do NOT implement strategy family architecture until:

1. **Market phase is reliably populated in shadow trades** (currently 0% meaningful coverage)
   - Required: ≥80% market_phase coverage in CURRENT-epoch data
   - Current: ~3% (based on data audit)

2. **At least ONE context-conditional positive EV finding is validated out-of-sample**
   - Candidate: TWEEZER_BOTTOM in RANGE + REVERSAL + trailing
   - Required: n≥100 in the specific condition, walk-forward validated

3. **Trailing stop improvement is confirmed in shadow A/B testing**
   - Required: L7 experiment with p < 0.05 on EVENING_STAR or system-wide
   - Current: Optimistic simulation suggests positive, realistic validation shows +0.08R

4. **Score model replacement is designed and validated**
   - Required: New probability model that is NOT inversely predictive
   - Options: phase-conditional empirical calibration, remove scoring entirely, or use MFE-based selection

5. **The current research engine can measure the improvement**
   - Required: Full lineage (entity_id ≥ 80%) + clean strategy field + phase labels
   - Current: entity_id = 96% (good), strategy = 7% (poor), phase = ~3% (poor)

### Summary Gate:

> **No architecture changes to strategy selection until research proves conditional positive EV with n≥100 and p<0.05 in at least one well-defined market context.**

---

## Change Log

| Date | Update |
|------|--------|
| 2026-07-27 | Document created from accumulated research findings |
| 2026-07-27 | Athena investigation confirmed context-dependent pattern behaviour |
| 2026-07-27 | Strategy-context alignment hypothesis formalised |

---

## References

- Entry Predictive Power Audit (2026-07-27)
- Pattern Selection Value Audit (2026-07-27)
- Exit Management Simulation (2026-07-27)
- Exit Simulation Validation (2026-07-27)
- Research Progress Report (2026-07-27)
- Profitability Readiness Audit (2026-07-27)
- Data Lineage Report (2026-07-27)
- Athena Strategy-Context Query Results (2026-07-27)
