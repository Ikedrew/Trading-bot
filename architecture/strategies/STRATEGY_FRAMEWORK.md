# Strategy Framework

## Why This Layer Exists

The system currently jumps from Market Context directly to Pattern Detection. This means every detected pattern is treated the same way regardless of what market behaviour it's trying to exploit.

Research (M9, M10) has shown that the same pattern performs differently depending on market phase. The system needs an intermediate layer that answers: "Given this environment, what TYPE of behaviour should we be looking for, and HOW should we exploit it?"

The Strategy Framework provides the "HOW" — a container where future trading strategies can be defined, researched, validated, and eventually promoted into production.

---

## The Three-Level Abstraction

```
Strategy Family
    "What behaviour are we exploiting?"
    Example: REVERSAL — exhaustion leading to direction change
        ↓
Strategy
    "How do we exploit that behaviour?"
    Example: range_reversal_v1 — exploit failed continuation at range extremes
        ↓
Pattern
    "What trigger confirms the opportunity?"
    Example: HAMMER — single-candle reversal signal at support
```

### Differences

| Layer | Question | Changes When | Example |
|-------|----------|-------------|---------|
| Strategy Family | What behaviour? | New family of market behaviour identified | Adding MEAN_REVERSION family |
| Strategy | How to exploit? | New hypothesis about exploiting a behaviour | Creating momentum_expansion_v2 |
| Pattern | What trigger? | New candlestick pattern detector built | Adding RISING_THREE_METHODS |

A Strategy Family can have multiple strategies. A strategy can use multiple patterns as triggers. This is a one-to-many relationship at each level.

---

## Strategy Lifecycle

```
HYPOTHESIS
    Strategy defined. No research started.
    Requirements: description, family, conditions
        ↓
RESEARCHING
    Active data collection and analysis.
    Requirements: experiment registered, data accumulating
        ↓
SHADOW_TESTING
    Generating signals without executing trades.
    Requirements: shadow trade pipeline connected
        ↓
VALIDATED
    Research complete. Evidence supports positive expectancy.
    Requirements: n>=100, EV>0, p<0.05, walk-forward, OOS
        ↓
ACTIVE
    Live. Influencing trade decisions.
    Requirements: manual promotion through decision gates
        ↓
DISABLED
    Deactivated. Evidence degraded or strategy superseded.
    Can be reactivated if new evidence emerges.
```

Each transition requires evidence. No strategy can skip stages.

---

## Activation Requirements

A strategy CANNOT become ACTIVE unless ALL of the following are satisfied:

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| Sample size | >= 100 trades | Statistical power requirement |
| Expectancy | > 0 R-multiple | Must be profitable |
| Statistical significance | p < 0.05 | Not due to chance |
| Walk-forward validation | Pass | Not overfit to training period |
| Out-of-sample validation | Pass | Generalises to unseen data |
| Manual promotion | Approved | Human decision gate |

---

## Current State

| Strategy | Family | Status | Trigger Patterns | Notes |
|----------|--------|--------|-----------------|-------|
| range_reversal_v1 | REVERSAL | HYPOTHESIS | 10 patterns | Core hypothesis |
| liquidity_sweep_reversal_v1 | REVERSAL | HYPOTHESIS | 6 patterns | Needs liquidity detection |
| momentum_expansion_v1 | MOMENTUM | HYPOTHESIS | 2 patterns | Limited impulse data |
| trend_pullback_continuation_v1 | CONTINUATION | HYPOTHESIS | 0 (none exist) | Blocked: no continuation patterns |
| range_breakout_v1 | BREAKOUT | HYPOTHESIS | 0 (none exist) | Blocked: no breakout patterns |

**Active strategies: 0**
**Strategies influencing execution: 0**

---

## Relationship to Other Components

```
┌─────────────────────────────────────────────────────────┐
│                    MARKET CONTEXT                         │
│  (H4 Regime, Market Phase, H1 Bias, Structure)          │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│               STRATEGY FAMILY AUTHORITY                   │
│  "What type of behaviour is relevant here?"              │
│  Mode: PASSTHROUGH (all families eligible)               │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│               STRATEGY AUTHORITY                          │
│  "What hypothesis attempts to exploit that behaviour?"   │
│  Mode: OBSERVATION (no execution influence)              │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│               PATTERN DETECTION                           │
│  "What specific candlestick trigger was detected?"       │
│  (Currently: 14 patterns across 2 families)              │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│               DECISION ENGINE                            │
│  (Scoring, confidence, execution decision)               │
│  Unchanged by this architecture phase                    │
└─────────────────────────────────────────────────────────┘
```

---

## What This Framework Does NOT Do

- Does NOT modify live trading behaviour
- Does NOT connect strategies to the decision engine
- Does NOT activate any strategies
- Does NOT assume any strategy is profitable
- Does NOT replace the current pattern-based decision flow

It provides the STRUCTURE where future evidence-driven strategies can exist once research validates them.

---

## Future Integration Path

1. **Phase 2: Shadow Integration** — Connect validated strategies to shadow trade pipeline for signal generation without execution.
2. **Phase 3: Research Validation** — Run M9/M10 experiments against strategy-aligned signals.
3. **Phase 4: Promotion** — If evidence passes all gates, promote strategy to ACTIVE.
4. **Phase 5: Execution** — Strategy influences pattern filtering and decision confidence.

Each phase requires its own design document and approval before implementation.
