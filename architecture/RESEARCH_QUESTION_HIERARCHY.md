# Research Question Hierarchy Audit

---

## The Ultimate Question

> "Can this trading system produce positive expected value after transaction costs and survive out-of-sample validation?"

**Current answer: NO.** Cost-adjusted EV = -0.70R. No pattern, context, or filter achieves positive EV after costs. The entry signal has no demonstrated directional predictive value at M5 timeframe.

---

## Dependency Graph

```
LEVEL 0: DOES THE ENTRY PREDICT DIRECTION AFTER COSTS?
    Current answer: NO (proven by CE1, EQ1, MS1-MS3)
    ↓ BLOCKED — nothing below this matters until Level 0 is YES
    
LEVEL 1: WHERE DOES EDGE EXIST? (which contexts improve?)
    Current answer: NOWHERE (proven by EQ1 — all cells negative)
    ↓ BLOCKED
    
LEVEL 2: HOW TO OPTIMISE THE EDGE? (exits, sizing, stops)
    Current answer: PREMATURE (no edge to optimise)
    ↓ BLOCKED
    
LEVEL 3: HOW TO DEPLOY? (execution, monitoring, scaling)
    Current answer: PREMATURE (nothing to deploy)
```

**The system is stuck at Level 0.** All 61 registered research questions except 5 assume Level 0 is passed.

---

## Complete Question Classification

### A) FOUNDATION QUESTIONS (Level 0) — Required NOW

These determine whether the project CAN succeed:

| ID | Question | Status | Answer | Still Needed? |
|---|---|---|---|---|
| **E1** | True system EV | ✅ ANSWERED | -0.22R raw, -0.70R after costs | Done |
| **CE1** (new) | Cost-adjusted EV | ✅ ANSWERED | -0.70R, no subset positive | Done |
| **EQ1** (new) | Any entry subset survive costs? | ✅ ANSWERED | NO — zero positive cells | Done |
| **MS1-3** (new) | Market scale viable? | ✅ ANSWERED | Signal has no direction at any scale | Done |
| **EI1-10** (designed) | Can ANY available info predict direction after costs? | ⚠️ NOT YET RUN | 3 immediately testable | **THIS IS THE BOTTLENECK** |

### B) EDGE DISCOVERY QUESTIONS (Level 1) — Premature until Level 0 passes

| ID | Question | Dependency | Current Status | Should Run? |
|---|---|---|---|---|
| E2 | Pattern expectancy | Requires E1 > 0 | ANSWERED (all patterns negative) | ❌ Pause |
| E3 | Strategy expectancy | Requires E1 > 0 | Needs CURRENT rerun | ❌ Pause |
| E4 | Strategy × pattern | Requires E2+E3 | Not run | ❌ Pause |
| M1 | Regime predicts outcomes | Requires edge to exist | ANSWERED (all regimes negative) | ❌ Pause |
| M2 | Regime edge by strategy | Requires M1 | Not run | ❌ Pause |
| M3 | Phase improves prediction | Requires E1 > 0 | Via M9 (all negative) | ❌ Pause |
| M4 | Regime×phase×strategy | Requires M1+M3 | Not run | ❌ Pause |
| M6 | Phase expectancy | Requires edge | Via M9 (all negative) | ❌ Pause |
| M7 | Regime+phase interaction | Requires edge | Via M10 | ❌ Pause |
| M9 | Phase×pattern | Standalone (CURRENT) | ✅ Done | Keep for reference |
| M10 | Phase×family | Standalone (CURRENT) | ✅ Done | Keep for reference |
| S1/S5 | Strategy type EV | Requires edge | Needs rerun | ❌ Pause |
| S4 | Strategies per phase | Requires S1 | Not run | ❌ Pause |

### C) OPTIMISATION QUESTIONS (Level 2) — Premature until edge exists

| ID | Question | Why Premature |
|---|---|---|
| D1 | Scoring components predict R | Optimising weights for a signal with no edge |
| D2 | Confidence calibration | Calibrating a system that loses money |
| D3 | EV gate value | Gating negative-EV trades doesn't create positive EV |
| D4 | Optimal thresholds | Threshold for a losing system |
| D5 | Missed opportunities | "Missed" losses are not opportunities |
| D6 | Portfolio ranking | Ranking losers by quality |
| EX1-EX10 | Exit management | Proven: no exit fixes -0.70R |
| R1 | Risk model effectiveness | Risk management of a losing system |
| R2 | Guard value | Guards blocking losing trades = helpful (keep losing!) |
| R3 | Probability of ruin | INVALIDATED |
| R4 | Drawdown threshold | INVALIDATED |
| R5 | Position sizing | INVALIDATED |
| S2/S6/S7 | Horizon EV | Different horizon of same losing signal |
| L1 | Pattern degradation | Patterns already have no edge |
| L3 | Architecture assumptions | Assumptions about a non-working system |
| L7 | A/B validation | Nothing to A/B test |
| P1 | Promotion impact | Nothing to promote |

### D) PRODUCTION QUESTIONS (Level 3) — Premature until system works

| ID | Question | Why Premature |
|---|---|---|
| X1 | Slippage model | Execution quality of a system with no edge |
| X2 | Broker failures | Broker reliability for no-trade scenario |
| X3 | Session quality | Session timing of losing trades |
| X4 | Shadow vs live gap | No shadow edge to lose in execution |
| X5 | Execution leakage | Nothing to leak |
| X6 | Execution stability | No execution occurring |

### E) GOVERNANCE/LEARNING (Keep active regardless)

| ID | Question | Why Keep |
|---|---|---|
| M5 | Phase transitions | Future — useful if system changes architecture |
| M8 | Phase transition behaviour | Future |
| M11 | Context > pattern? | Partially answered (yes, but neither works after costs) |
| L2 | System improvement tracking | Tracks whether any change helps |
| L4 | Market drift | Monitors environment changes |
| G1-G3 | Data governance | Infrastructure health |
| E5 | Walk-forward | Validation method — needed whenever edge is claimed |

---

## Orphan Questions (optimise without foundation)

**41 of 61 questions** are currently orphans — they optimise or investigate aspects of a system that has no demonstrated edge. Running them produces valid statistics about a losing system but cannot improve profitability.

| Category | Count | Status |
|----------|-------|--------|
| Orphaned (optimise nothing) | 41 | PAUSE |
| Foundation (determine viability) | 5 | 4 DONE, 1 IN PROGRESS |
| Active governance/monitoring | 7 | KEEP |
| Discovery completed | 8 | REFERENCE ONLY |
| **Total** | **61** | |

---

## Missing Foundation Questions

### Already covered by recent experiments:

| Foundation Need | Covered By | Result |
|---|---|---|
| "Does entry predict direction?" | CE1 + EQ1 | NO |
| "Does signal survive costs?" | CE1 | NO — costs = 0.48R |
| "Does system have positive EV?" | E1 (CURRENT recompute) | NO — EV = -0.22R raw |
| "Is failure signal or geometry?" | MS1-MS3 | BOTH (signal ≈ 0, geometry amplifies costs) |

### Not yet answered (the remaining critical question):

| Foundation Need | Designed As | Status |
|---|---|---|
| **"Can ANY available information predict positive cost-adjusted EV?"** | EI1, EI6, EI10 | ⚠️ DESIGNED but NOT YET RUN |

This IS the bottleneck. If EI experiments show NO:
- Project cannot succeed with current architecture
- Decision: redesign or halt

If EI experiments show YES (some combination filters to positive adjusted EV):
- Validate via walk-forward (E5)
- Then all Level 1-3 questions become relevant

---

## Final Output

### A) Minimum research set to decide project viability

| # | Experiment | Purpose | Can Run Now? |
|---|---|---|---|
| 1 | **EI10** (combined filter) | Does risk≥6 + H1 aligned + score≥0.60 + PULLBACK/REVERSAL = positive adjusted EV? | ✅ YES |
| 2 | **EI6** (risk filter) | Does risk≥6 alone approach zero EV? | ✅ YES |
| 3 | **EI1** (bar-1 velocity) | Does first-bar direction predict final outcome? | ✅ YES |
| 4 | **E5** (walk-forward) | If any positive subset found, does it hold OOS? | ✅ YES (runner exists) |
| 5 | **Decision gate** | After EI results: continue or halt | Manual |

**Total: 4 experiments + 1 decision. ~2 hours of computation.**

### B) Questions to PAUSE until edge exists (41 questions)

All Level 1, 2, and 3 questions should be paused:
- E2-E4, M1-M4, M6-M7, D1-D6, EX1-EX10, R1-R5, S1-S7, X1-X6, L1, L3, L7, P1

These are not wrong — they're premature. Resume when Level 0 is YES.

### C) Questions ready for implementation

**NONE.** No research finding currently supports an implementation change because no positive cost-adjusted EV has been demonstrated.

The only actionable findings are:
- **Reduce risk to ≤0.2% per trade** (prevents catastrophic loss while research continues)
- **Do not execute USDJPY** (cost ratio makes it mathematically impossible)
- **Do not promote any previous recommendation** (all INVALIDATED)

### D) The current bottleneck preventing profitability

```
The M5 candlestick pattern detection system does not predict price direction.
Raw EV ≈ -0.01R (effectively random after SL normalisation).
Transaction costs at 3.5-pip median SL consume 48% of risk budget.
Combined: -0.70R per trade after costs.

No exit, stop, strategy, phase, regime, or scoring optimisation
can convert zero directional signal into positive EV.

The bottleneck IS the signal itself.
```

**The project's viability depends on one question:**

> "Is there ANY combination of available entry-time information that produces cost-adjusted EV > 0 with n≥100 and CI above zero?"

If EI experiments answer NO → **project architecture is non-viable.**
If EI experiments answer YES → proceed to walk-forward → resume Level 1-3 research.

This is the only research that matters right now.
