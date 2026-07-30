# V2 Observer Integration

## Summary

Observer #8 (`v2_opportunity_observer`) captures the complete market state every evaluation cycle and persists it for research analysis. It runs in parallel with the existing decision pipeline and never influences trading behaviour.

---

## Architecture: Before

```
Market Data
    │
    ▼
MarketContext
    │
    ▼
Decision Engine
    │
    ▼
Execution / Shadow Trades
```

## Architecture: After

```
Market Data
    │
    ▼
MarketContext
    │
    ├──────────────────────────────┐
    │                              │
    ▼                              ▼
Decision Engine              V2OpportunityBuilder
    │                              │
    ▼                              ▼
Execution / Shadow Trades    V2 Opportunity Observer
                                   │
                                   ▼
                           logs/v2_opportunities/
                           {SYMBOL}/{DATE}.jsonl
```

---

## Data Flow

1. `ObserverRegistry.notify_all(ctx)` is called after every engine evaluation
2. Observer #8 receives `ObserverContext` (read-only)
3. Extracts market state from `ctx.market_context`, `ctx.engine_result`, `ctx.candles`
4. Calls `build_v2_opportunity()` to produce a frozen `V2Opportunity` dataclass
5. Calls `persist_v2_opportunity()` to write JSONL (fire-and-forget)
6. Returns `None` — no value is consumed by any downstream component

---

## Observer Responsibilities

| Responsibility | Implementation |
|---|---|
| Capture H4/H1/M15 context | Reads from `market_context` sub-objects |
| Capture pattern features | Reads from `engine_result["pattern"]`, `_best_pattern` |
| Capture execution environment | Reads `ctx.bid`, `ctx.ask`, derives spread/session |
| Capture risk geometry | Reads intent SL, M15 structure levels |
| Persist observation | JSONL to `logs/v2_opportunities/{SYMBOL}/{DATE}.jsonl` |
| Never influence decisions | Returns None, never mutates ctx or engine_result |
| Never block on failure | Wrapped in try/except, logs debug on error |

---

## Fields Captured

### Higher Timeframe (H4)
- `h4_regime` — TRENDING / RANGING / TRANSITIONAL
- `h4_structure_state` — structural classification
- `h4_trend_direction` — BULLISH / BEARISH / NEUTRAL
- `h4_volatility_state` — EXPANSION / CONTRACTION / NEUTRAL

### Middle Timeframe (H1)
- `h1_bias` — directional bias
- `h1_structure_type` — HH_HL / LL_LH / etc.
- `h1_bos_confirmed` — break of structure confirmed (bool)
- `h1_bos_direction` — BOS direction
- `h1_choch_detected` — change of character (bool)

### M15 Structure
- `m15_structure_state` — structural classification
- `m15_rejection_strength` — quality score
- `m15_displacement` — displacement measurement
- `near_support` / `near_resistance` — proximity flags
- `order_block_present` — order block detected

### M5 Pattern (as feature, not signal)
- `pattern_detected` — pattern name
- `pattern_direction` — BUY / SELL
- `pattern_quality` — quality score [0, 1]
- `candle_range` — high-low range
- `body_ratio` — body / range
- `wick_ratio` — max wick / range

### Execution Context
- `bid` / `ask` / `spread`
- `spread_atr_ratio` — spread as fraction of ATR
- `atr` — current ATR value
- `volatility` — tradability score
- `session` — LONDON / NY / ASIA / OFF

### Risk Geometry
- `proposed_direction` — BUY / SELL
- `proposed_entry` — mid-price
- `candle_stop_distance` — SL from candle geometry
- `structure_stop_distance` — SL from M15 structure
- `atr_stop_distance` — SL from ATR multiplier
- `risk_distance_pips` — primary stop in pips

### Identity
- `opportunity_id` — unique ID (`v2_{SYMBOL}_{TS}_{UUID8}`)
- `correlation_id` — links to shadow_trade / decision ledger
- `timestamp_utc` — bar timestamp
- `symbol` — instrument

---

## Outcome Linkage

Each V2Opportunity can be joined to execution results via:

```
V2Opportunity.correlation_id
        │
        ├── shadow_trades.entity_id
        ├── decision_ledger.correlation_id
        └── trade_truth.entity_id
```

Outcome fields are initially empty:
```json
{
    "linked": false,
    "result_r": null,
    "outcome_category": ""
}
```

These are populated later by the research engine's outcome linker.

---

## Persistence Format

**Location:** `logs/v2_opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl`

**Each line:** Single JSON object containing all V2Opportunity fields.

**Schema version:** `v2_opportunity_1.0`

---

## Proof: Execution Path Untouched

1. Observer is registered LAST (position #8) in ObserverRegistry
2. `observe_v2_opportunity()` returns `None` — no return value consumed
3. Entire observer wrapped in `try/except Exception: pass` — failure never propagates
4. Observer imports are lazy (inside function body) — import failure cannot break registry
5. No imports from `execution/`, `risk/`, `core/pipeline/` decision modules
6. `engine_result` dict is only READ (`.get()` calls) — never mutated
7. Full regression confirms zero behaviour change: **3288 passed, 0 new failures**

---

## Files Changed

| File | Change |
|---|---|
| `core/observers/__init__.py` | Created (empty) |
| `core/observers/v2_opportunity_observer.py` | Created — observer #8 implementation |
| `core/pipeline/observers.py` | Added observer #8 dispatch (10 lines) |
| `tests/test_v2_observer.py` | Created — 15 safety/integration tests |

---

## Test Results

| Suite | Result |
|---|---|
| `test_v2_observer.py` | **15 passed** |
| `test_v2_opportunity_builder.py` | **13 passed** |
| Full regression | **3288 passed**, 1 pre-existing failure (unchanged) |
| New regressions | **0** |

---

## Confirmation

**V2 observation pipeline is integrated as a research-only observer. It captures complete market state every evaluation cycle without influencing any trading decision, risk calculation, or execution behaviour.**
