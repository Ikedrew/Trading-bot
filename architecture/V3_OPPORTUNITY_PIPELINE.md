# V3 Opportunity Pipeline — Market Location & Liquidity Research

## Purpose

V3 captures WHERE price is positioned relative to market structure and liquidity — information that V2's general context features lacked the granularity to express.

The V2 Discovery Engine concluded NO_PREDICTIVE_VALUE from broad context variables (regime, bias, session, pattern). V3 tests a different hypothesis: **precise market location and liquidity context may predict outcomes when general context does not.**

This is research infrastructure only. It never modifies trading behaviour.

---

## Research Hypothesis

> "Does proximity to institutional order flow levels (order blocks, fair value gaps, liquidity pools, session extremes) predict trade outcome when general market context does not?"

---

## Architecture

```
Market Data
    │
    ▼
MarketContext
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
Existing Decision Engine                V3OpportunityBuilder
    │                                              │
    ▼                                              ▼
Execution / Shadow Trades              V3 Opportunity Observer (#9)
                                                   │
                                                   ▼
                                          logs/v3_opportunities/
                                          {SYMBOL}/{DATE}.jsonl
```

---

## Schema Domains (80+ fields)

### 1. Price Position
Where is price within H4/H1/M15 ranges?

| Field | Description |
|---|---|
| `h4_swing_high/low` | H4 structure extremes |
| `h4_range_position` | 0.0 (at low) to 1.0 (at high) |
| `h4_distance_from_high/low_pips` | Distance in pips |
| `h1_swing_high/low` | H1 structure extremes |
| `h1_range_position` | Position within H1 range |
| `h1_last_bos_price` | Where last BOS occurred |
| `h1_distance_from_bos_pips` | Distance from BOS level |
| `m15_swing_high/low` | M15 structure extremes |
| `m15_range_position` | Position within M15 range |

### 2. Support / Resistance
Nearest levels with quality metrics.

| Field | Description |
|---|---|
| `nearest_support/resistance_price` | Level price |
| `nearest_support/resistance_distance_pips` | Distance to level |
| `nearest_support/resistance_touches` | How many bounces |
| `nearest_support/resistance_age_bars` | Recency |
| `nearest_support/resistance_timeframe` | Which TF defined it |
| `support/resistance_quality_score` | Composite 0-1 |

### 3. Liquidity
Where are liquidity pools and have they been swept?

| Field | Description |
|---|---|
| `equal_highs_above` / `equal_lows_below` | Liquidity target exists |
| `equal_highs/lows_distance_pips` | Distance to pool |
| `equal_highs/lows_count` | Number of touches |
| `prev_session_high/low` | Previous session extremes |
| `prev_day_high/low` | Previous day extremes |
| `*_swept` | Whether liquidity was taken |
| `liquidity_sweep_just_occurred` | Recent sweep |
| `sweep_direction` | BULLISH / BEARISH |
| `bars_since_sweep` | Recency of sweep |

### 4. Order Blocks
Institutional supply/demand zones.

| Field | Description |
|---|---|
| `nearest_demand/supply_ob_price` | OB level |
| `nearest_demand/supply_ob_distance_pips` | Distance |
| `demand/supply_ob_timeframe` | H4 / H1 / M15 |
| `demand/supply_ob_mitigated` | Already visited |
| `demand/supply_ob_strength` | Displacement quality |
| `price_inside_ob` | Currently in OB |
| `ob_type_if_inside` | DEMAND / SUPPLY |

### 5. Fair Value Gaps
Price imbalance zones.

| Field | Description |
|---|---|
| `nearest_fvg_above/below_price` | FVG midpoint |
| `nearest_fvg_above/below_distance_pips` | Distance |
| `fvg_above/below_filled_pct` | Fill percentage |
| `price_inside_fvg` | Currently in FVG |
| `total_unfilled_fvgs_above/below` | Count |

### 6. Displacement & Momentum
How price arrived at this location.

| Field | Description |
|---|---|
| `displacement_into_level` | Aggressive move into zone |
| `displacement_magnitude_atr` | Move size in ATR |
| `rejection_candle_present` | Rejection detected |
| `rejection_body_ratio` | Body/range of rejection |
| `rejection_wick_atr_ratio` | Wick size in ATR |
| `bars_at_current_level` | Time at this zone |
| `consolidation_range_pips` | Range of consolidation |

### 7. Execution Context (minimal)
For cost adjustment only.

| Field | Description |
|---|---|
| `bid` / `ask` / `spread` | Current prices |
| `spread_risk_ratio` | Spread / nearest stop |
| `atr` | Current ATR |
| `session` | LONDON / NY / ASIA / OFF |

---

## What V3 Captures That V2 Did Not

| Information | V2 | V3 |
|---|---|---|
| H4 swing high/low prices | No | Yes |
| Range position (0-1) | No | Yes |
| Distance to structure in pips | No | Yes |
| Support/resistance touches | No | Yes |
| Level age/recency | No | Yes |
| Equal highs/lows (liquidity) | No | Yes |
| Previous session/day levels | No | Yes |
| Liquidity sweep detection | Boolean only | Distance + direction + recency |
| Order block proximity | Boolean only | Price + distance + strength + TF |
| Fair value gap detail | Boolean only | Fill %, direction, count |
| Displacement measurement | No | ATR-normalized |
| Rejection candle quality | No | Body ratio + wick/ATR |
| Consolidation at level | No | Bars + range |

---

## Persistence

**Location:** `logs/v3_opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl`

**Each line:** Single JSON object with all V3Opportunity fields.

**Schema version:** `v3_opportunity_v1`

---

## Outcome Linkage

Same mechanism as V2:
```
V3Opportunity.correlation_id
        │
        ├── shadow_trades.entity_id
        ├── decision_ledger.correlation_id
        └── V2Opportunity.correlation_id (cross-reference)
```

---

## Proof: Execution Untouched

1. Observer #9 registered LAST in ObserverRegistry
2. `observe_v3_opportunity()` returns `None`
3. Wrapped in `try/except Exception: pass` — failure never propagates
4. Lazy imports (inside function body) — import failure cannot break registry
5. No imports from execution, risk, or pipeline decision modules
6. `engine_result` is only READ (`.get()` calls) — never mutated
7. Full regression: **3369 passed, 0 new failures**

---

## Test Results

| Suite | Result |
|---|---|
| `test_v3_pipeline.py` | **26 passed** |
| Full regression | **3369 passed**, 1 pre-existing failure (unchanged) |
| New regressions | **0** |

---

## Files

| File | Purpose |
|---|---|
| `core/v3_opportunity.py` | Frozen dataclass (80+ fields) |
| `core/v3_opportunity_builder.py` | Builder + persistence + read |
| `core/observers/v3_opportunity_observer.py` | Observer #9 |
| `core/pipeline/observers.py` | +10 lines for observer dispatch |
| `tests/test_v3_pipeline.py` | 26 tests |

---

## Next Research Steps

Once sufficient V3 records accumulate (target: n >= 200 linked):

1. Run V2 outcome linker to attach results
2. Run V2 Discovery Engine against V3 fields
3. Test whether location precision predicts outcomes
4. Key questions:
   - Does range position (price near extremes) predict reversal?
   - Do liquidity sweeps followed by rejection predict direction?
   - Does order block proximity correlate with outcome?
   - Does FVG fill predict continuation?
   - Does displacement into a level differ from drift into a level?
