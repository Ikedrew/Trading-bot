# Strategy Observation Engine

## Purpose

The Strategy Observation Engine collects evidence by recording what strategy
conditions were present at each market cycle and what happened afterwards.
It answers the research question:

> "When strategy X's conditions were met, what was the outcome?"

This is the bridge between the Strategy Framework (architecture) and the
Research Engine (evidence). Without it, strategies remain untested hypotheses.

---

## Data Flow

```
Each Market Cycle
    ↓
┌─────────────────────────────────────────────────┐
│  MarketContext (regime, phase, structure, bias)  │
│  + detected pattern                             │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│  StrategyObserver.observe()                     │
│                                                 │
│  1. Build market snapshot (flat dict)           │
│  2. Evaluate ALL registered strategies          │
│  3. Create StrategyObservation per strategy     │
│  4. Store observations in memory                │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────┐
│  StrategyObservation (per strategy per cycle)   │
│                                                 │
│  - strategy_id, family                          │
│  - regime, phase, direction                     │
│  - conditions_met / failed / missing            │
│  - confidence, overall_status                   │
│  - pattern_detected                             │
│  - outcome_status: PENDING                      │
└──────────────────────┬──────────────────────────┘
                       ↓
            (Later, when trade resolves)
                       ↓
┌─────────────────────────────────────────────────┐
│  link_outcome(observation_id, "WIN", +1.5R)     │
│                                                 │
│  Creates the evidence pair:                     │
│  "Conditions were X → Outcome was Y"           │
└─────────────────────────────────────────────────┘
```

---

## What This Enables

Once observations accumulate with linked outcomes, research can answer:

1. "When range_reversal_v1 was FULLY_MET, what was the average outcome?"
2. "Does phase eligibility correlate with positive expectancy?"
3. "Which strategy has the highest win rate when conditions are met?"
4. "Is confidence score predictive of outcome?"
5. "Which conditions matter most for outcome prediction?"

These are the questions M9/M10/M11 need to answer before any strategy
can be promoted to ACTIVE.

---

## Safety Guarantees

The observer:
- NEVER influences execution decisions
- NEVER blocks trade placement
- NEVER modifies scoring or confidence
- NEVER connects to the decision engine
- NEVER activates any strategy
- Only READS market context and WRITES observation records

If the observer fails or is absent, trading continues unchanged.

---

## Observation Schema

| Field | Type | Description |
|-------|------|-------------|
| observation_id | str (UUID) | Unique identifier |
| timestamp_utc | float | Unix timestamp of observation |
| cycle_id | int | Processing cycle number |
| symbol | str | Trading pair |
| strategy_id | str | Which strategy was evaluated |
| family | str | Strategy family (REVERSAL, MOMENTUM, etc.) |
| regime | str | H4 regime at observation time |
| market_phase | str | Market phase at observation time |
| direction | str | Unified direction |
| eligible_by_phase | bool | Were environment conditions met? |
| conditions_met | int | Count of conditions that passed |
| conditions_failed | int | Count that explicitly failed |
| conditions_missing | int | Count with no data available |
| conditions_unavailable | int | Count for unimplemented features |
| confidence | float | Fraction of required conditions passed |
| overall_status | str | FULLY_MET / PARTIALLY_MET / NOT_MET / INCOMPLETE |
| pattern_detected | str | Pattern name if any |
| pattern_in_strategy_triggers | bool | Is pattern in this strategy's trigger set? |
| outcome_status | str | PENDING → WIN / LOSS / TIMEOUT / NO_TRADE |
| outcome_r_multiple | float | Result in R-multiples (after linkage) |
| outcome_linked | bool | Whether outcome has been connected |

---

## Lifecycle

```
1. COLLECTION (current)
    Observer creates observations each cycle.
    All outcomes start as PENDING.

2. LINKAGE (next phase)
    When a shadow trade or real trade resolves,
    link its outcome to matching observations.

3. ANALYSIS (research engine)
    Query observations with linked outcomes.
    Calculate: "When FULLY_MET, average R = ?"
    Statistical tests: "Is this better than random?"

4. VALIDATION (decision gates)
    If n >= 100, p < 0.05, walk-forward holds:
    Strategy can be promoted.
```

---

## Integration Point (Future)

The observer is designed to be called from the existing ObserverRegistry
pipeline without modifying the decision flow:

```python
# In future observer integration (NOT YET CONNECTED):
# ObserverRegistry → StrategyObserver.observe(market_context, pattern)
```

This integration is a separate phase requiring its own approval.
The observer currently operates standalone for testing and research.
