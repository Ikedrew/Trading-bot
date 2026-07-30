# V2Opportunity Schema

## Why This Schema Exists

The research engine has proven that the current entry model (pattern → direction) contains no directional predictive value after costs. The V2Opportunity schema captures the COMPLETE market state at every opportunity so future research can determine:

> "Which combination of market information, if any, actually predicts future price movement?"

It is a research data structure, NOT a trading decision object.

---

## What Research Questions It Enables

| Question | Required Fields |
|----------|----------------|
| Does H4 regime predict direction? | h4_regime, outcome_raw_r |
| Does H1 structure predict continuation? | h1_bos_confirmed, h1_bos_direction, outcome |
| Does market location matter? | near_support, order_block_present, zone_type, outcome |
| Does wider risk geometry reduce cost impact? | risk_distance_pips, spread, outcome |
| Does volatility predict movement scale? | atr, volatility, mfe |
| Does session affect accuracy? | session, outcome |
| Which patterns have genuine value? | pattern_detected, pattern_quality, outcome |
| Does multi-timeframe alignment help? | h4 + h1 + m15 + m5 fields, outcome |
| Can a probability model be trained? | All context fields → predicted_probability |

---

## How It Differs from the Existing V1 Objects

| V1 Object | Purpose | Limitation |
|-----------|---------|-----------|
| OpportunityAssessment | Records engine scoring result | Only captures what V1 considers relevant |
| ShadowTrade | Simulates trade lifecycle | Missing pre-trade context (M15, location, volatility) |
| DecisionTrace | Diagnostic of V1 decision | Tied to V1 pipeline stages |
| **V2Opportunity** | **Complete state for open research** | **Captures everything — lets research determine what matters** |

---

## Schema Structure (47 fields)

```
V2Opportunity (frozen dataclass)
├── Identity (6 fields)
│   opportunity_id, correlation_id, timestamp, symbol, timeframe, version
├── H4 Context (5 fields)
│   regime, structure_state, trend_direction, volatility_state, distance_from_level
├── H1 Context (5 fields)
│   bias, structure_type, bos_confirmed, bos_direction, choch_detected
├── Market Location (9 fields)
│   near_support/resistance, distances, liquidity_sweep, order_block, FVG, zone
├── M15 Confirmation (4 fields)
│   structure_state, confirmation_type, displacement, rejection_strength
├── M5 Entry Features (7 fields)
│   pattern, direction, quality, candle_range, body_ratio, wick_ratio, displacement
├── Execution Conditions (8 fields)
│   bid, ask, spread, spread_atr, atr, volatility, session, market_state
├── Risk Geometry (6 fields)
│   direction, entry, structure/candle/atr stop distances, risk_pips
├── Probability (3 fields — placeholder)
│   predicted_probability, model_version, confidence_score
└── Outcome (7 fields — placeholder)
    outcome_recorded, raw_r, mfe, mae, positive/negative target, bars
```

---

## Integration Point

```
Market Data
    ↓
MarketContext Builder (existing)
    ↓
V2Opportunity Builder  ← NEW (observation only)
    ↓
Existing V1 Pipeline (unchanged)
```

The builder will be connected via the ObserverRegistry (observer #8) when ready. Currently the schema is defined and tested but not yet wired to live data.

---

## Safety Guarantees

- `frozen=True` — cannot be modified after creation
- No imports from `core/pipeline/`, `execution/`, `risk/`
- No return value consumed by any production component
- Zero lines of existing code modified
- All 3260 tests pass with zero regressions
