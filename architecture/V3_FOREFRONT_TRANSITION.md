# V3 Forefront Transition — Architecture & Development Recommendation

**Date:** 2026-07-28
**Decision:** V3 becomes the primary development direction

---

## 1. Architecture Decision

### Is moving V3 to the forefront correct?

**YES.** But with a critical caveat: V3 has found a signal, not an edge.

### Why

1. **V2 is conclusively dead.** CE1/EQ1/MS1 proved M5 patterns contain zero directional value after costs (n=867, -0.70R). Continuing V2 optimisation is mathematically guaranteed to fail.

2. **V3 found the only positive signal in the entire project's history.** Inside Order Block = +0.071R (n=23, CI > 0). This is small and needs confirmation, but it EXISTS. Every other research direction produced zero.

3. **The location hypothesis is theoretically sound.** Institutional zones represent points where large orders were previously placed. Price returning to these zones has a logical mechanism (unfilled orders). This isn't curve-fitting — it's measuring something real about market microstructure.

4. **The infrastructure supports the transition.** 9 observers, 3 detectors, outcome linkage, discovery engine — all operational.

### What risks exist

| Risk | Severity | Mitigation |
|---|---|---|
| n=23 is too small — OB finding may be noise | HIGH | Do NOT build strategy yet. Validate at n=50 first. |
| +0.071R does not survive spread cost (0.48R) | HIGH | Risk geometry research BEFORE strategy development |
| Confirmation bias — seeing what we want | MEDIUM | Maintain research engine discipline: n≥50, CI, OOS |
| Over-engineering before proving edge | MEDIUM | Minimal architecture changes until cost-adjusted EV > 0 |
| 92% RANGE epoch — findings may not generalise | MEDIUM | Mark all findings as "RANGE-EPOCH ONLY" until trending data exists |

### What should remain from V2

| Component | Status | Reason |
|---|---|---|
| Pattern detection | **KEEP** (as confirmation layer) | Patterns fire at zones — timing role |
| Shadow trade engine | **KEEP** (unchanged) | Outcome measurement is essential |
| Observer system | **KEEP** (expanded) | V3 detectors already integrated |
| Risk manager (structural) | **KEEP** (position limits, exposure) | Safety layer still needed |
| Research engine | **KEEP** (primary value creator) | Prevents false discoveries |
| Decision engine logic | **DEPRECATE as authority** | No longer drives trades |
| Scoring weights | **DEPRECATE** | V2 scoring has zero predictive value |
| Strategy family selection | **DEPRECATE** | All strategies are negative-EV |

### What should be retired

- Pattern score as a trading decision
- Confidence thresholds based on V1 scoring
- Regime-based strategy selection (regime proven non-predictive)
- Fixed 2R TP model (0% hit rate)
- The assumption that more patterns = better system

---

## 2. What Should Be Done Next

### Recommended: RISK GEOMETRY RESEARCH

**Not coding. Not architecture refactor. Research.**

Here's why:

The V3 location signal exists (+0.071R). But it cannot be traded because:
- Spread cost = 0.48R
- Net EV = +0.071 - 0.48 = **-0.41R** (still losing)

No amount of context improvement, feature engineering, or architectural excellence changes this arithmetic. The ONLY path to positive EV is:

**Reduce spread/risk ratio OR increase raw signal magnitude.**

### Specific Research Experiment

**RG1: Risk Geometry Viability Experiment**

Question: "At what stop distance does the inside-OB signal become cost-viable?"

| Stop distance | Spread/risk | Required raw EV | Current raw EV | Viable? |
|---|---|---|---|---|
| 2.2 pips (current) | 48% | +0.48R | +0.071R | NO |
| 5 pips | 20% | +0.20R | ? | UNKNOWN |
| 10 pips | 10% | +0.10R | ? | POSSIBLE |
| 15 pips | 7% | +0.07R | +0.071R | BREAKEVEN |

**At 15-pip stop distance, the current OB signal (+0.071R) approximately covers spread cost.** But: does the OB signal persist at wider stops? Do wider stops change the timeout/SL/TP ratio? This is the experiment.

### Why NOT other options

| Alternative | Reason to defer |
|---|---|
| Architecture refactor | Building architecture for a signal that doesn't survive costs is waste |
| More feature development | More features won't fix the 0.48R spread burden |
| Shadow testing of V3 strategy | Can't shadow-test what loses money after costs |
| Data collection only | Already have n=158, enough for initial risk research |

---

## 3. First V3 Milestone

### Milestone: "Cost-Adjusted Positive EV at One Location Configuration"

**Success criteria:**

A specific configuration where:
```
Context (inside OB or equivalent) + Risk geometry (wider stop)
= Raw EV > spread/risk ratio
= Cost-adjusted EV > 0
```

**Measurable target:**
- At least ONE location+risk combination with cost-adjusted EV > 0
- Sample size n ≥ 30 for that configuration
- 95% CI lower bound > -0.10R (approaching zero if not clearly positive)

**Why this milestone:**

If this cannot be achieved, V3 cannot trade profitably regardless of architecture quality. If it CAN be achieved, it becomes the foundation for everything that follows.

**NOT the milestone:**
- "Beautiful architecture" — useless without edge
- "More features" — lateral movement without solving the cost problem
- "Full V3 system" — premature optimisation

---

## 4. Existing Component Classification

| Component | Classification | Reasoning |
|---|---|---|
| **Pattern detection** | KEEP | Becomes timing/confirmation. Detectors still needed for pattern-at-zone. |
| **Strategy scoring** | DEPRECATE | V2 scoring has zero predictive value. Replace with location scoring when ready. |
| **Decision engine** | MODIFY | Keep structure, replace authority source from score→location context. |
| **Risk manager** | MODIFY | Keep safety (position limits, max loss), research new risk geometry. |
| **Execution layer** | KEEP | Mechanically correct. Will be used when edge exists. |
| **Observability** (9 observers) | KEEP | Primary data source for all research. Do not touch. |
| **Research engine** | KEEP | Most valuable component. Expand with risk geometry experiments. |
| **Shadow trade engine** | MODIFY | Need configurable stop/target distances for risk geometry research. |
| **Market intelligence detectors** | KEEP | Liquidity/FVG/OB working. Core V3 value. |
| **MarketContext** | KEEP | Data conduit. Add new fields as needed. |
| **V3 schema + builder** | KEEP | Observation layer. Working correctly. |

---

## 5. Suggested Build Order

### Phase 1: Risk Geometry Research (IMMEDIATE — weeks)

**Goal:** Determine if wider risk distances preserve the OB signal while reducing spread burden.

Tasks:
1. Design RG1 experiment (stop distances: 5, 10, 15 pips)
2. Run on existing 158 linked V3 records
3. Determine: does inside-OB signal persist at wider stops?
4. Calculate: at what spread/risk ratio is the signal viable?
5. Result: either a viable configuration exists or it doesn't

**No code changes to production.** Research only using existing data.

### Phase 2: Location Signal Validation (weeks → months)

**Goal:** Confirm OB finding at n=50+ and test combinations.

Tasks:
1. Continue collecting V3 observations (need ~180 more records for 50 inside-OB events)
2. Test OB + FVG + discount stacking
3. Validate in LONDON session (currently only OFF session data)
4. Determine: does the combined signal exceed breakeven?

**Minimal code changes.** Maybe add configurable risk parameters to shadow trades for controlled experiments.

### Phase 3: Context Scoring System (only after Phase 1+2 succeed)

**Goal:** Build a location-based opportunity scoring replacement.

Tasks:
1. Score = f(inside_OB, discount_zone, FVG_proximity, OB_strength)
2. Threshold = minimum score for shadow-trade consideration
3. Validate: does scored subset have positive cost-adjusted EV?

**Code changes:** New scoring function, likely in a new V3 strategy module. Does NOT replace V2 — runs in parallel via observer.

### Phase 4: Risk Model Replacement (only after Phase 3)

**Goal:** Replace fixed 2R model with location-aware risk geometry.

Tasks:
1. Stop placement: structure-based (beyond OB zone boundary)
2. Target: based on opposing liquidity zone
3. RR: variable, calculated per opportunity
4. Position sizing: based on location confidence

### Phase 5: V3 Shadow Strategy (only after Phase 4)

**Goal:** First complete V3 decision pipeline running as shadow.

```
V3 Location Score > threshold
→ Pattern confirmation at zone
→ Structure-based stop
→ Liquidity-based target
→ Shadow trade
→ Outcome measurement
→ Research feedback
```

### Phase 6: Execution Validation (only after Phase 5 proves profitable)

**Goal:** Paper trading, then live.

---

## 6. Architecture Risks

| Risk | Description | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **Building before proving** | Constructing full V3 architecture before confirming cost-adjusted edge | HIGH (tempting) | HIGH (wasted effort) | Phase gate: no code without research evidence |
| **Overfitting to n=23** | Designing system around a finding that may be noise | MEDIUM | HIGH | Require n=50 confirmation before any production change |
| **Feature accumulation** | Adding more features without solving the cost problem | MEDIUM | MEDIUM | Every new feature must demonstrate it REDUCES the cost gap, not just "looks interesting" |
| **Confirmation bias** | Interpreting ambiguous results as supporting V3 | MEDIUM | HIGH | Maintain CI requirements, OOS validation, pre-registration |
| **Execution before signal** | Optimising execution/management before proving entry | LOW (aware) | HIGH | Phase gates enforce order |
| **Single-epoch risk** | All findings are from RANGE regime only | HIGH | MEDIUM | Label all findings with epoch constraint; validate in next regime |

---

## Final Output

### 1. Recommended Next Action

**Run Risk Geometry Research Experiment (RG1)** on existing 158 V3 records. Test whether the inside-OB signal survives at 5/10/15-pip stop distances. This is a pure data analysis task — no code changes to production.

### 2. Recommended Development Priority

```
1. Risk geometry research ← YOU ARE HERE
2. OB signal confirmation (n=50)
3. Location combination testing
4. Context scoring system
5. Risk model replacement
6. V3 shadow strategy
```

### 3. Components to Modify First

1. **Shadow trade engine** — add configurable stop/target for risk experiments (small change)
2. **Research engine** — add RG1 experiment runner
3. **Nothing else** — until research proves viability

### 4. Components to Leave Untouched

- All 9 observers
- All 3 market intelligence detectors
- V3 schema and builder
- Pattern detection
- Execution layer
- MarketContext models
- Outcome linkage

### 5. V3 Roadmap

| Phase | Milestone | Gate |
|---|---|---|
| 1 | Risk geometry viable at one configuration | Cost-adj EV > 0 at n≥30 |
| 2 | OB signal confirmed at n=50 | CI lower > 0 |
| 3 | Location combination > single feature | Stacked EV > individual |
| 4 | Context scoring replaces pattern scoring | Scored trades > unscored |
| 5 | V3 shadow strategy profitable over 100 trades | Cost-adj EV > 0 |
| 6 | Live validation | Same performance in live |

Each phase gates the next. No skipping.

### 6. Confidence Level

**MEDIUM confidence** in the V3 direction.

Reasoning:
- The location signal (+0.071R) is the strongest finding in the project's history
- The theoretical basis (institutional order flow) is sound
- But n=23 is small, single-epoch, single-session, and doesn't survive current costs
- The risk geometry research will determine whether this is a viable path or a dead end

**If RG1 shows no configuration achieves breakeven:** The project has exhausted its current information set and must either find external data or accept the null result.

**If RG1 shows a viable configuration exists:** V3 becomes a real trading system development project with a defined path to profitability.

The next experiment answers the question.
