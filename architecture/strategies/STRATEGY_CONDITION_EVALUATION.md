# Strategy Condition Evaluation Layer

## Why This Layer Exists

The Strategy Framework defines strategies as hypotheses — "range_reversal_v1 attempts to exploit failed continuation at range extremes." But until now, the system could not answer: "Did the conditions for that strategy actually occur right now?"

The taxonomy was complete (families, strategies, patterns) but the decision logic was absent. This layer bridges that gap by converting prose conditions into structured, evaluable objects that can be checked against live market data.

---

## What Question This Layer Answers

```
"The market is in this environment.
 Which strategy is designed for this environment?
 Did its conditions occur?"
```

Specifically:
- Given a MarketContext snapshot, which strategies are phase-eligible?
- For each eligible strategy, which specific conditions are satisfied?
- What data is missing that prevents a complete evaluation?
- What is the overall confidence that conditions were met?

---

## How It Connects: MarketContext → Strategy → Evidence

```
MarketContext (produced each cycle)
    regime, phase, direction, H4/H1/M15/M5 summaries
        ↓
build_market_snapshot() — flattens to evaluable dict
        ↓
StrategyConditionEvaluator.evaluate(strategy_id, snapshot)
        ↓
    For each Condition in StrategyConditionSet:
        - Check data_field exists in snapshot
        - Dispatch to appropriate evaluator (enum_match, bool_check, etc.)
        - Record PASSED / FAILED / MISSING_DATA / NOT_APPLICABLE
        ↓
ConditionEvaluationResult
    - eligible_by_phase: did environment conditions pass?
    - conditions_passed / failed / missing
    - overall_status: FULLY_MET | PARTIALLY_MET | NOT_MET | INCOMPLETE
    - confidence: fraction of required conditions with data that passed
        ↓
Research Pipeline (observation and logging)
    - Record which strategies would have been eligible
    - Track condition pass rates over time
    - Generate evidence for activation decisions
```

---

## Why Observation-Only Initially

1. No strategy has validated evidence yet. Activation requires n>=100, p<0.05, walk-forward.
2. The evaluator must prove its classifications are correct before influencing decisions.
3. By observing first, we accumulate data showing: "strategy X's conditions occurred N times, and the outcome was Y." This IS the research evidence.
4. Connecting to execution before validation would violate the core principle: no trading logic changes without proven research.

---

## Architecture Components

```
core/strategies/
    conditions.py               — Condition models + per-strategy definitions
    condition_evaluator.py      — StrategyConditionEvaluator + snapshot builders
    evaluation_diagnostics.py   — Formatted reporting
```

### Condition Model

Each strategy's prose conditions are converted to structured `Condition` objects:

| Field | Purpose |
|-------|---------|
| name | Machine identifier (e.g. "regime_is_ranging") |
| description | Human explanation |
| category | ENVIRONMENT / LOCATION / STRUCTURE / MOMENTUM / TIMING / PATTERN |
| required | Must this pass for eligibility? |
| data_field | MarketContext path (e.g. "m15.at_key_level") |
| evaluator_key | Which evaluation function handles this |
| expected_values | Acceptable values for enum checks |
| threshold | Numeric threshold for comparisons |
| comparison | How to compare (eq, in, gte, lte, bool_true, not_neutral) |

### Evaluator Dispatch

| evaluator_key | Logic | Example |
|---------------|-------|---------|
| enum_match | value in expected_values | regime in ("RANGING",) |
| bool_check | value is truthy | m15.at_key_level == True |
| numeric_threshold | value >= / <= threshold | quality_score >= 0.3 |
| pattern_family_check | detected pattern in trigger set | "HAMMER" in reversal patterns |
| bias_alignment_check | direction != NEUTRAL | h1.direction != "NEUTRAL" |
| unavailable | data not yet in system | liquidity_levels (future) |

### Overall Status Determination

| Status | Meaning |
|--------|---------|
| FULLY_MET | All required conditions passed |
| PARTIALLY_MET | Some required passed, some missing data |
| NOT_MET | At least one required condition failed |
| INCOMPLETE | Required conditions exist but no data available |
| NO_CONDITIONS_DEFINED | Strategy has no structured conditions |

---

## Current Strategy Condition Coverage

| Strategy | Environment | Entry | Unavailable | Can Fully Evaluate? |
|----------|-------------|-------|-------------|---------------------|
| range_reversal_v1 | 2 (regime + phase) | 4 (level, momentum, pattern, structure) | 0 | Yes |
| liquidity_sweep_reversal_v1 | 1 (phase) | 4 (level, OB, pattern, liquidity) | 1 (liquidity) | Partially |
| momentum_expansion_v1 | 2 (regime + phase) | 4 (bias, pattern, strength, BOS) | 0 | Yes |
| trend_pullback_continuation_v1 | 2 (regime + phase) | 3 (bias, pattern, structure) | 1 (pattern) | Partially |
| range_breakout_v1 | 1 (phase) | 3 (trigger, pattern, range) | 2 (pattern, range) | No |

---

## Data Field Mapping

All conditions reference fields that exist on the MarketContext object:

| Condition data_field | MarketContext source |
|---------------------|---------------------|
| regime | MarketContext.regime (Regime enum) |
| phase | MarketContext.phase (Phase enum) |
| h4.trend_strength | MarketContext.h4.trend_strength |
| h1.direction | MarketContext.h1.direction |
| h1.bos_confirmed | MarketContext.h1.bos_confirmed |
| m15.at_key_level | MarketContext.m15.at_key_level |
| m15.order_block_present | MarketContext.m15.order_block_present |
| m15.quality_score | MarketContext.m15.quality_score |
| m5.bias_strength | MarketContext.m5.bias_strength |
| m5.trigger_ready | MarketContext.m5.trigger_ready |
| pattern_detected | From OpportunityAssessment (external input) |

---

## What This Layer Does NOT Do

- Does not place trades
- Does not calculate entries or stops
- Does not modify confidence scores
- Does not approve execution
- Does not connect to the decision engine
- Does not activate any strategy
- Does not assume any strategy is profitable

It only answers: "Did this strategy's requirements occur?"
