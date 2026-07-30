# V2 Opportunity Builder Implementation

## Data Flow

```
Market Data
    ↓
MarketContext Builder (existing, unchanged)
    ↓
V2OpportunityBuilder.build_v2_opportunity()  ← NEW (observation only)
    │
    ├── Extracts: H4 regime, H1 bias/BOS, M15 structure/levels
    ├── Extracts: pattern features, candle geometry
    ├── Extracts: bid, ask, spread, ATR, session
    ├── Computes: risk geometry (structure/candle/ATR stops)
    ├── Produces: V2Opportunity (frozen)
    └── Persists: logs/v2_opportunities/{SYMBOL}/{DATE}.jsonl
    ↓
Existing V1 Pipeline (unchanged — no dependency on V2Opportunity)
```

## Fields Populated from MarketContext

| Field | Source | Always Available? |
|-------|--------|------------------|
| h4_regime | market_context.h4.regime | When MarketContext exists |
| h4_trend_direction | market_context.h4.trend_bias | When h4 sub-object exists |
| h4_volatility_state | Derived from h4.atr_ratio | When h4 sub-object exists |
| h1_bias | market_context.h1.direction | When h1 sub-object exists |
| h1_structure_type | market_context.h1.swing_structure | When h1 sub-object exists |
| h1_bos_confirmed | market_context.h1.bos_confirmed | When h1 sub-object exists |
| h1_bos_direction | market_context.h1.bos_direction | When h1 sub-object exists |
| near_support | market_context.m15.at_key_level | When m15 sub-object exists |
| order_block_present | market_context.m15.order_block_present | When m15 sub-object exists |
| volatility | market_context.tradability_score | When MarketContext exists |

## Fields Populated from External Inputs

| Field | Source |
|-------|--------|
| pattern_detected, pattern_direction, pattern_quality | From engine_result or detected_patterns |
| candle_range, body_ratio, wick_ratio | From trigger candle analysis |
| bid, ask, spread | From live market data |
| atr | From feature engine |
| session | From time-based classification |
| proposed_direction | From pattern or context |
| candle/structure/atr_stop_distance | From risk geometry computation |
| risk_distance_pips | Derived from stop distances |
| correlation_id | From correlation engine |

## Remaining Fields (not yet populated — future work)

| Field | Why Not Yet | When Needed |
|-------|-----------|-------------|
| distance_to_support/resistance | M15 nearest_support/resistance not passed as params | When distance analysis needed |
| liquidity_sweep_detected | Not currently computed | When liquidity detection exists |
| fair_value_gap_present | Not currently computed | When FVG detection exists |
| zone_type | Not currently derived | When zone classification exists |
| m15_confirmation_type | Not currently classified | When M15 confirmation typed |
| m5_displacement | Not currently computed | When displacement measured |
| predicted_probability | Placeholder for future ML | When probability model trained |
| outcome fields | Linked after trade resolves | Via outcome linker |

## Test Results

- `test_v2_opportunity_builder.py`: **13 passed**
- Full regression: **3273 passed**, 1 pre-existing failure
- Regressions: **0**

## Confirmation

**V2 observation pipeline collects market intelligence without changing trading behaviour.**

No production code was modified. The builder is a standalone module (`core/v2_opportunity_builder.py`) that:
- Imports only from `core/v2_opportunity.py` (the schema) and standard library
- Has zero dependencies on pipeline, execution, or risk modules
- Can be called from the ObserverRegistry when ready (observer #8)
- Persists independently to `logs/v2_opportunities/`
- Never returns a value consumed by any trading component
