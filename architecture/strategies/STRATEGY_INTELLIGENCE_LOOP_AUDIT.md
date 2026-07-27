# Strategy Intelligence Loop Audit — Hypothesis Validation vs Confirmation Bias

---

## 1. Is This a Valid Research Framework?

### Assessment: YES — with one critical gap.

The architecture can genuinely answer the stated question:

> "The market was in environment X. Strategy Y was designed for this environment. Did its conditions occur? What happened afterwards?"

The data flow is sound:

```
MarketContext (objective measurement)
    → StrategyConditionEvaluator (deterministic check)
        → StrategyObservation (recorded fact)
            → Outcome Linkage (measured result)
                → Statistical Query (quantified answer)
```

Each step is:
- Deterministic (same input → same output)
- Observable (recorded, not inferred)
- Separable (observation independent of outcome)

### The Critical Gap: Temporal Separation

The system does NOT yet enforce temporal separation between observation and outcome. For the research to be valid, the following must be TRUE:

1. **Observation must be recorded BEFORE the outcome is known.** This is structurally guaranteed — observations are created at condition-check time, outcomes are linked later when trades close. This is correct.

2. **The conditions being evaluated must not be derived FROM the outcome.** This is also correct — conditions reference MarketContext (regime, phase, levels) which exist independently of whether a trade wins or loses.

3. **The strategy definitions must not be retroactively modified based on results.** This is NOT yet enforced. Nothing prevents someone from seeing "range_reversal_v1 loses in IMPULSE" and then removing IMPULSE from `valid_market_phases`. This would be data snooping disguised as "correcting a definition."

### Missing Layer: Definition Versioning

The system needs a mechanism to freeze strategy definitions at observation time. If a strategy's conditions change after observations are collected, those observations are invalidated. Currently `StrategyDefinition` is frozen (immutable dataclass), but the registry itself can be replaced between sessions.

**Recommendation:** Log the strategy definition version alongside each observation. If the definition changes, prior observations under the old version are analysed separately.

---

## 2. Am I Accidentally Creating Confirmation Bias?

### A) Legitimate Hypothesis Creation

These are scientifically valid:

| Activity | Why It's Valid |
|----------|---------------|
| Defining strategy families | This is taxonomy — naming categories of market behaviour |
| Writing market hypotheses | Every research programme starts with hypotheses |
| Specifying conditions | These are testable predictions ("if X then Y should follow") |
| Creating the test framework | Building measurement instruments is not bias |

Creating hypotheses is not bias. **Testing hypotheses with the intent to confirm them is bias.**

### B) Requires Independent Validation

These are bias risks:

| Risk | Why It's a Risk | Mitigation |
|------|----------------|------------|
| You defined conditions AND will judge pass/fail | You could unconsciously calibrate thresholds to match desired results | Pre-register conditions. Once defined, DO NOT tune them to improve pass rate |
| You chose which phases are "valid" for each strategy | If wrong, you'll never observe the strategy in the phases where it might actually work | Include an "ALL PHASES" baseline comparison for every strategy |
| You query your own evidence | You might only query the winning subset | Require ALL strategies to be evaluated, not just the ones you think will work |
| Exit criteria are subjective | "WIN" vs "LOSS" depends on SL/TP which you designed | Use raw R-multiple distribution, not binary win/loss |

### C) Controls Against Proving Your Own Assumptions

Currently implemented controls:
- ✅ Activation requires n≥100, p<0.05, walk-forward
- ✅ Frozen dataclasses prevent mutation after creation
- ✅ Observer evaluates ALL strategies (not just "promising" ones)
- ✅ Statistics computed over ALL records (no cherry-picking)

Controls that are MISSING:
- ❌ **Null hypothesis comparison.** No query asks "does this strategy beat random entry?"
- ❌ **Multiple comparison correction.** Testing 17 strategies × 5 phases = 85 comparisons. At p<0.05, you expect ~4 false positives by chance alone. Need Bonferroni or FDR correction.
- ❌ **Out-of-sample holdout.** No mechanism to split data into training/test periods.
- ❌ **Strategy definition freeze.** Nothing prevents post-hoc modification of conditions.
- ❌ **Adversarial query.** No query specifically designed to FALSIFY the hypothesis.

### Verdict: The framework is structurally sound but lacks falsification safeguards.

The biggest risk is not that the code is wrong — it's that the researcher (you) will subconsciously focus on confirming results and ignore disconfirming evidence. The fix is mechanical: add mandatory falsification queries.

---

## 3. Evidence Lifecycle Audit

### Required stages:

```
HYPOTHESIS          → Define what you expect to happen
    ↓
OBSERVATION         → Record what conditions existed
    ↓
MEASUREMENT         → Record what outcome occurred
    ↓
STATISTICAL TEST    → Determine if result differs from chance
    ↓
VALIDATION          → Confirm on unseen data (walk-forward / OOS)
    ↓
PROMOTION           → Activate if all gates pass
```

### Current status per stage:

| Stage | Implemented? | Quality |
|-------|-------------|---------|
| HYPOTHESIS | ✅ Yes | Strategy definitions with explicit hypotheses |
| OBSERVATION | ✅ Yes | StrategyObserver creates records each cycle |
| MEASUREMENT | ✅ Yes | OutcomeLinker connects observations to results |
| STATISTICAL TEST | ⚠️ Partial | Win rate and average R computed, but no significance test (p-value, confidence interval) |
| VALIDATION | ❌ Missing | No walk-forward split. No out-of-sample holdout |
| PROMOTION | ✅ Yes (gate exists) | ResearchValidation requires n≥100, p<0.05, walk-forward — but p-value is not computed by the evidence store |

### Missing Components:

1. **Statistical significance testing.** The evidence store computes `win_rate` and `average_r` but does not compute p-values, confidence intervals, or test against a null hypothesis (random entry). Without this, "57% win rate on 100 trades" cannot be distinguished from noise.

2. **Walk-forward validation infrastructure.** No mechanism to split observations into training period (discover pattern) and validation period (confirm it holds). This is the most important overfitting protection.

3. **Baseline comparison.** No "control group." What would random entry during the same phase produce? Without this, a strategy showing +0.2R might actually be underperforming the baseline.

---

## 4. Minimum Research Query Catalogue

### Before ANY strategy activation, ALL of the following must be answerable:

#### Strategy Existence Queries

| Query | Purpose |
|-------|---------|
| "How often do this strategy's conditions occur?" | If conditions occur 2x per year, the strategy is untestable |
| "What fraction of observations are FULLY_MET vs PARTIALLY_MET?" | If always partial, conditions may be too strict |
| "In which phases/regimes do conditions occur most?" | Validates the hypothesised valid_market_phases |

#### Performance Queries

| Query | Purpose |
|-------|---------|
| "When FULLY_MET, what is the R-multiple distribution?" | Core performance metric |
| "What is the mean R? Median R? Standard deviation?" | Distribution shape matters more than mean |
| "Is the win rate significantly different from 50%?" | Statistical significance test |
| "Is the mean R significantly different from 0?" | One-sample t-test against null |

#### Context Interaction Queries

| Query | Purpose |
|-------|---------|
| "Does performance differ by regime?" | Strategy may only work in one regime |
| "Does performance differ by phase?" | Strategy may work in phases not originally hypothesised |
| "Does performance differ by time of day/session?" | Time-dependent edge detection |
| "Does confidence score predict outcome?" | Validates the condition evaluation system |

#### Failure Analysis Queries

| Query | Purpose |
|-------|---------|
| "What market conditions precede strategy LOSSES?" | Find invalidation patterns |
| "Is there a condition count threshold below which it always loses?" | Identify minimum requirements |
| "What is the maximum drawdown sequence?" | Consecutive loss risk |
| "Does the strategy degrade over time?" | Detect regime shift vulnerability |

#### Comparison Queries

| Query | Purpose |
|-------|---------|
| "Does this strategy beat random entry in the same phase?" | The fundamental question |
| "Does this strategy beat 'always take the trade' in the same conditions?" | Tests whether the conditions ADD value |
| "Does strategy A outperform strategy B in the same environment?" | Comparative selection |
| "Does condition-filtered entry beat unfiltered entry?" | Tests the taxonomy's value proposition |

#### Overfitting Protection Queries

| Query | Purpose |
|-------|---------|
| "Does performance hold in the second half of the sample?" | Time-split validation |
| "Does performance hold on different symbols?" | Cross-instrument validation |
| "Does removing the best 5% of trades change the conclusion?" | Outlier sensitivity |
| "Is the edge concentrated in a small number of trades?" | Fragility test |

---

## 5. Phase Progression Audit

### Proposed:

- Phase 1: Build observation capability
- Phase 2: Collect strategy evidence
- Phase 3: Research queries and promotion decisions

### Assessment: Ordering is CORRECT but Phase 3 has two sub-phases that must not be conflated.

**Corrected progression:**

```
Phase 1: OBSERVATION ARCHITECTURE (COMPLETE)
    Build the ability to record strategy conditions and outcomes.
    Status: Done.

Phase 2: DATA COLLECTION (NEXT)
    Connect observer to live pipeline.
    Accumulate observations with outcomes.
    Target: n≥100 per strategy in primary phase.
    Status: Blocked on pipeline integration.

Phase 3A: DISCOVERY (after data exists)
    Run research queries to discover which strategies show signal.
    Identify candidates for validation.
    DO NOT activate anything yet.
    Status: Infrastructure exists, awaiting data.

Phase 3B: VALIDATION (after discovery)
    Walk-forward test on holdout period.
    Statistical significance with multiple-comparison correction.
    Out-of-sample confirmation.
    Status: Infrastructure NOT YET BUILT.

Phase 4: PROMOTION (after validation)
    Activate validated strategies through decision gates.
    Status: Gate mechanism exists, awaiting validated evidence.
```

### What should NOT happen before evidence exists:

- DO NOT tune strategy conditions based on early results
- DO NOT add new strategies inspired by looking at outcomes
- DO NOT change valid_market_phases based on observation frequency
- DO NOT modify the observer to only record "interesting" observations

### What should happen automatically:

- ALL strategies evaluated every cycle (already designed)
- ALL observations persisted (needs pipeline integration)
- ALL outcomes linked when trades close (needs automation)
- Statistics recomputed on demand (exists)

---

## 6. Is the Current Hypothesis Proven?

### The hypothesis:

> "If the system knows what environment exists, what strategies fit that environment, whether conditions occurred, and historical outcomes, then it can improve decision quality."

### Assessment:

| Classification | Status |
|---------------|--------|
| A) Reasonable architecture hypothesis? | **YES.** This is a standard adaptive systems design. Context-aware strategy selection is well-established in systematic trading literature. |
| B) Proven trading edge? | **NO.** Zero evidence exists that YOUR specific strategy definitions, in YOUR specific market, produce positive expectancy. |
| C) Not yet proven? | **CORRECT.** The architecture is sound but the content (specific strategies, conditions, thresholds) is unvalidated. |

### The critical distinction:

**Architecture ≠ Edge.**

The architecture can discover whether an edge exists. It cannot create one. If the underlying pattern library has no predictive power (which current research shows: system EV = -0.19R), then perfectly classifying "which strategy was relevant" will still produce negative EV — just with better explanations for WHY it lost.

The architecture's value proposition is:
1. If an edge EXISTS but is being diluted by inappropriate application, the taxonomy will isolate it.
2. If NO edge exists in the current pattern set, the taxonomy will prove that conclusively.

Both outcomes are valuable. But "the system will be profitable" is not the same as "the system will know whether it's profitable."

---

## 7. The Actual Bottleneck

### Ranked limiting factors:

| Rank | Factor | Why |
|------|--------|-----|
| 1 | **Statistical evidence (sample size)** | Cannot validate anything without n≥100 per strategy×phase. Current rate ~770 trades over months. 17 strategies × 5 phases = 85 cells. Most will have n<10 for a long time. |
| 2 | **Pattern library predictive power** | Research shows system EV = -0.19R. If patterns contain no signal, no taxonomy will extract one. The taxonomy can only ISOLATE signal, not CREATE it. |
| 3 | **Pipeline integration** | Observer not connected to live data. No observations accumulating. This is a one-time fix (~15 lines) but blocks everything downstream. |
| 4 | **Outcome linkage automation** | Manual linkage won't scale. Need automatic matching of observations to shadow trade outcomes. |
| 5 | **Walk-forward infrastructure** | Doesn't exist yet. Cannot validate without it. |

### The honest bottleneck:

**The bottleneck is NOT architecture. It's signal.**

If the current 14 patterns (86% reversal) contain genuine predictive information about future price movement, the taxonomy will find it. If they don't, no amount of classification will help.

Research finding E1 showed: "Score is INVERSELY correlated with outcome." This suggests the current signal generation pipeline may have fundamental issues that taxonomy cannot fix.

---

## 8. Stalemate Test

> "If I complete pipeline integration, persistence, and evidence collection, what prevents me from discovering whether this idea works?"

### Answer: Nothing prevents discovery. But discovery might reveal the answer is "no."

Remaining unknowns after infrastructure completion:

| Unknown | Can Be Resolved By | Risk |
|---------|-------------------|------|
| Do strategy conditions occur frequently enough? | 2-4 weeks of observation | Low risk — phases repeat |
| Does FULLY_MET correlate with better outcomes than PARTIALLY_MET? | n≥50 per bucket | Medium — might find no difference |
| Does any single strategy show positive EV? | n≥100 per strategy | High — current EV is -0.19R overall |
| Is the taxonomy adding value over "trade everything"? | A/B comparison | High — might find taxonomy adds nothing |
| Does walk-forward hold? | Temporal split after enough data | High — overfitting risk |

### The uncomfortable truth:

The architecture is designed to discover truth. The truth might be:
- "These patterns don't contain edge regardless of context"
- "The taxonomy correctly identifies environments but the entries still lose"
- "Strategy conditions are too rare to generate statistical power"

All of these are valid, falsifying outcomes. The system is designed to detect them.

---

## 9. Final Assessment

### What has genuinely been achieved:

1. **A complete observation-to-evidence pipeline** that can measure strategy performance without influencing execution.
2. **Temporal separation** between observation and outcome measurement (structural guarantee against look-ahead bias).
3. **A falsifiable research framework** — strategies can be rejected as well as promoted.
4. **284+ tests** proving the architecture works correctly and has zero execution impact.
5. **A taxonomy** that organises existing trading logic into testable units.

### What is still only a hypothesis:

1. That strategy conditions correlate with outcomes.
2. That context-aware strategy selection improves EV.
3. That the current pattern library contains exploitable signal.
4. That the taxonomy's classification of "valid phases" is correct.
5. That conditions defined in prose actually capture the relevant market features.

### What would count as proof:

- A specific strategy shows R > 0 with p < 0.05 (Bonferroni-corrected for 17 comparisons → p < 0.003) on n≥100 trades.
- The result holds on a walk-forward holdout period not used during discovery.
- The strategy outperforms unconditional entry in the same phase.
- The effect is not explained by a single outlier trade.

### What would falsify the idea:

- After 500+ observations per strategy, no strategy achieves p < 0.05.
- FULLY_MET observations perform no better than PARTIALLY_MET or NOT_MET.
- The taxonomy adds zero predictive value over random phase assignment.
- Walk-forward consistently degrades in-sample findings.

Any of these would prove the taxonomy is architecturally correct but the underlying signal doesn't exist.

### The exact next engineering step:

**Add StrategyObserver as observer #7 in `core/pipeline/observers.py`.**

This is a single try/except block (~15 lines) following the exact pattern of observers 1-6. It connects the taxonomy to live market data and begins evidence accumulation. Without it, the entire architecture remains untested against reality.

Everything else — statistical tests, walk-forward, multiple comparison correction — can be built AFTER data starts flowing. But nothing can be built or validated without data, and data requires pipeline integration.
