# Strategy Family Layer

## Why This Layer Exists

The trading system detects candlestick patterns and uses them to make entry decisions. However, patterns are not all trying to exploit the same market behaviour. A HAMMER is looking for exhaustion and reversal. THREE_WHITE_SOLDIERS is looking for directional conviction and momentum.

Research (M9, M10) has shown that pattern performance varies significantly depending on market phase. The same pattern can be profitable in one phase and unprofitable in another. This suggests the system needs an abstraction layer between "what environment are we in?" and "what specific setup do we trade?"

The Strategy Family Layer provides that abstraction.

---

## Conceptual Hierarchy

```
Market Context (regime + phase)
    "What environment are we in?"
        ↓
Strategy Family (reversal, momentum, etc.)
    "What type of behaviour should we look for?"
        ↓
Pattern Detection (hammer, engulfing, etc.)
    "What specific setup was detected?"
        ↓
Scoring + Decision
    "Is this setup strong enough to trade?"
```

Without this layer, the system treats all patterns as equivalent regardless of context. With this layer, the system can (once research validates) scope pattern detection to families that are statistically profitable in the current conditions.

---

## Pattern Detection vs Strategy Selection

These are fundamentally different concerns:

| Concern | Question | Example |
|---------|----------|---------|
| Pattern Detection | "Did a candlestick formation appear?" | HAMMER detected |
| Strategy Family | "What market behaviour does this exploit?" | REVERSAL (exhaustion → direction change) |
| Strategy Selection | "Should we trade this in the current context?" | Research says REVERSAL works in REVERSAL phase |

Pattern detection is mechanical. Strategy family is classification. Strategy selection is the evidence-driven decision that this layer enables.

---

## Current Families

| Family | Patterns | Behaviour Exploited |
|--------|----------|---------------------|
| REVERSAL | 12 (86%) | Exhaustion → direction change |
| MOMENTUM | 2 (14%) | Strong directional conviction |
| CONTINUATION | 0 (future) | Trend-following after pullback |
| BREAKOUT | 0 (future) | Range escape |
| MEAN_REVERSION | 0 (future) | Bounce from statistical extremes |

The library is currently 86% reversal patterns. This is a known limitation, not a design flaw. The system cannot test "does this phase need continuation?" until continuation pattern detectors are built.

---

## Relationship: Market Phase → Strategy Family

Research hypothesis (M10):

| Market Phase | Expected Favoured Family | Status |
|-------------|-------------------------|--------|
| REVERSAL | REVERSAL | Awaiting validation |
| PULLBACK | CONTINUATION | No patterns available |
| IMPULSE | MOMENTUM | Partial evidence |
| CONSOLIDATION | BREAKOUT / MEAN_REVERSION | No patterns available |
| EXHAUSTION | REVERSAL | Awaiting validation |

These mappings are HYPOTHESES, not validated rules. They cannot be activated until:
- n >= 100 in the specific phase x family combination
- EV significantly > 0 (p < 0.05)
- Walk-forward validated
- Promoted through research decision gates

---

## Architecture Components

```
core/strategy_family/
    __init__.py         — Public API, exports
    models.py           — StrategyFamily enum, data models
    registry.py         — Pattern → family mapping (source of truth)
    authority.py        — StrategyFamilyAuthority (eligibility engine)
    diagnostics.py      — Formatted reporting
```

### StrategyFamilyAuthority

The central component. Operates in two modes:

**PASSTHROUGH (current)**
- All families always eligible
- No filtering occurs
- Trading engine behaves exactly as before
- Zero runtime impact

**RESEARCH_GATED (future)**
- Filters families based on validated research rules
- Only activatable when ResearchValidation passes ALL checks
- Requires: sufficient sample, statistical significance, walk-forward validation

---

## Future Activation Path

```
1. Collect data (CURRENT state)
    M9/M10 experiments gather phase x family performance data
        ↓
2. Reach statistical power
    n >= 100 per phase x family combination
        ↓
3. Validate findings
    p < 0.05, walk-forward holds
        ↓
4. Promote through decision gates
    Research Command Centre approves
        ↓
5. Load rules into authority
    authority.load_research_rules(rules, validation)
        ↓
6. Authority switches to RESEARCH_GATED
    Only families validated for current phase are eligible
        ↓
7. Downstream adjusts
    Pattern detection scoped to eligible family patterns
```

Steps 1-3 are ongoing. Steps 4-7 require architecture changes that are NOT implemented and will NOT be implemented until evidence justifies them.

---

## Design Constraints

1. **No runtime impact in PASSTHROUGH mode.** The layer exists but does nothing to trading.
2. **Research proves rules before activation.** No manual overrides without evidence.
3. **Immutable data models.** All results are frozen dataclasses.
4. **Single source of truth.** `FAMILY_REGISTRY` is the canonical mapping.
5. **Safe unknown handling.** Unknown patterns return None, never raise.
6. **Observable.** Diagnostics expose all internal state for debugging.

---

## What This Layer Does NOT Do

- Does not change trade selection
- Does not modify scoring
- Does not block execution
- Does not add runtime gates
- Does not connect to the live decision pipeline
- Does not assume which families should be active

It only provides the classification infrastructure that future research can activate.
