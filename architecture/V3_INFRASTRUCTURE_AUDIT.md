# V3 Infrastructure Audit & Implementation Plan

## Executive Summary

**60 of 85 V3 fields are always empty.** The root causes are:

1. **Model mismatch** — V3 builder reads `market_context.h4.swing_high` but `H4Summary` has no `swing_high` field
2. **ATR not propagated** — V3 builder receives ATR=0 in 84% of records (source: `engine_state.volatility_filter` is often None)
3. **No liquidity/OB/FVG detectors exist** — Schema defined but no computation engine
4. **Config flags may disable HTF** — `MTF_ENABLED=False` or `MARKET_CONTEXT_ENABLED=False` stops all upstream data

---

## Component 1: Market Structure / Swing Level Detection

### Current Implementation Status

**Swing detection EXISTS in three places:**

| Location | Timeframe | What it produces | Fields |
|---|---|---|---|
| `core/timeframes/h1_bias.py` | H1 | `BiasSnapshot.last_swing_high`, `last_swing_low` | Available since Phase 4C.1 |
| `core/timeframes/m15_structure.py` | M15 | `StructureSnapshot.nearest_support`, `nearest_resistance` | Swing highs/lows computed internally but only support/resistance exposed |
| `core/pipeline/swing_context.py` | M5 | `SwingContext.last_swing_high`, `last_swing_low` | Used for pipeline gating |

**H4 has NO swing level detection.** The `RegimeSnapshot` contains only `classification`, `confidence`, `trend_bias`, `trend_strength`, `atr_ratio`.

### Why V3 Fields Are Empty

The V3 builder does:
```python
h4 = getattr(market_context, "h4", None)
if h4:
    h4_swing_high = float(getattr(h4, "swing_high", 0) or 0)
```

But `H4Summary` has these fields:
```python
class H4Summary:
    regime: str
    confidence: float
    trend_bias: str
    trend_strength: float
    atr_ratio: float
```

**There is no `swing_high` or `swing_low` on `H4Summary`.** The `getattr` returns the default `0`. Same problem for H1 — `H1Summary` has no `swing_high`/`swing_low` either, even though `BiasSnapshot` (the upstream type) DOES have `last_swing_high`/`last_swing_low`.

### Root Cause Chain

```
BiasSnapshot.last_swing_high = 1.088  ← COMPUTED by h1_bias.py
        │
        ▼
MarketContextBuilder._extract_h1()  ← DOES NOT COPY swing levels
        │
        ▼
H1Summary (no swing_high field)  ← FIELD MISSING in model
        │
        ▼
V3 builder reads h1.swing_high → gets 0  ← ALWAYS ZERO
```

### Required Changes

1. **Add swing level fields to H4Summary and H1Summary:**
```python
class H4Summary:
    ...existing fields...
    swing_high: float = 0.0
    swing_low: float = 0.0

class H1Summary:
    ...existing fields...
    swing_high: float = 0.0
    swing_low: float = 0.0
```

2. **Add H4 swing detection** to `core/timeframes/h4_regime.py` (or create a dedicated analyzer)

3. **Update `MarketContextBuilder._extract_h1()`** to copy `last_swing_high`/`last_swing_low` from BiasSnapshot

4. **Update `MarketContextBuilder._extract_m15()`** to expose M15 swing_high/swing_low from StructureSnapshot internals

5. **Add `swing_high`/`swing_low` to M15Summary** model

### Range Position Fix

Once swing levels flow through, range_position computation will work automatically (the V3 builder already has `_range_position(price, low, high)` — it just receives 0,0 currently).

### Tests Required

- H4Summary/H1Summary/M15Summary model tests (new fields present)
- MarketContextBuilder copies swing levels from snapshots
- V3 builder computes non-zero range_position when swings available
- Existing tests still pass (no regression)

---

## Component 2: ATR / Volatility Data Propagation

### Where ATR Is Currently Calculated

| Location | How | Stored As |
|---|---|---|
| `scoring_engine.volatility_penalty()` | `sum(c.high - c.low) / 14` from M5 candles | `layer_score.volatility_penalty` |
| `h1_bias.py` | `sum(c.high - c.low) / 14` from H1 candles (local only) | Not persisted |
| `m15_structure.py` | `sum(c.high - c.low) / len(recent)` from M15 candles | Used internally for `at_key_level` |

### Why ATR Is Missing in V3

The V3 observer reads ATR from:
```python
atr_val = float(getattr(ctx.engine_state, "volatility_filter", 0.0) or 0.0)
```

`engine_state.volatility_filter` is populated by `StateDelta` from the scoring engine — but it's a **penalty value** (0-1 range), not actual ATR in price units. The V3 builder uses it as if it's ATR (comparing candle ranges against it), which explains the nonsensical `ATR mean=1.0` in the early analysis.

### Root Cause

There is **no canonical ATR-in-price-units** stored in `engine_state` or `MarketContext`. ATR is computed locally in several places and discarded.

### Required Changes

1. **Add `atr_m5: float` field to MarketContext** (or a dedicated execution context):
```python
class MarketContext:
    ...
    atr_m5: float = 0.0  # M5 ATR in price units (14-period)
```

2. **Compute and store ATR in MarketContextBuilder.build():**
```python
def build(self, *, candles=None, ...):
    atr = self._compute_atr(candles) if candles else 0.0
```

3. **V3 builder reads from `market_context.atr_m5`** instead of `engine_state.volatility_filter`

4. **Alternative (simpler):** V3 observer computes ATR directly from `ctx.candles` (available on ObserverContext):
```python
if ctx.candles and len(ctx.candles) > 14:
    recent = ctx.candles[-14:]
    atr_val = sum(c.high - c.low for c in recent) / 14
```

### Impact After Fix

With real ATR available:
- `displacement_into_level` will fire when candle range > 1.5 ATR
- `rejection_candle_present` will fire when wick > body * 1.5
- `displacement_magnitude_atr` will be normalized correctly
- `rejection_wick_atr_ratio` will give meaningful values

### Tests Required

- ATR computation produces correct values from known candle data
- V3 builder receives non-zero ATR
- Displacement fires for large candles
- Rejection fires for wick-dominant candles

---

## Component 3: Liquidity Detection Engine

### Algorithm — Equal Highs/Lows

**Objective definition (no subjectivity):**

```
Equal Highs:
    Given candles C[i] and C[j] where j > i + min_separation:
    IF |C[i].high - C[j].high| < tolerance
    AND tolerance = pip_size * tolerance_pips (default: 3 pips)
    AND min_separation >= 5 bars
    THEN equal_highs detected at level = mean(C[i].high, C[j].high)

Equal Lows:
    Same logic with .low
```

**Liquidity Pool:**
- A cluster of equal highs/lows forms a pool
- Pool level = mean of all contributing touches
- Valid for `max_age_bars` (default: 200 M5 bars = ~16 hours)
- Invalidated when price closes beyond pool by > 2 * tolerance

**Liquidity Sweep:**
```
Sweep detected when:
    1. Price exceeds pool level (high > pool_high for above, low < pool_low for below)
    2. Price then CLOSES back inside the pool range within `sweep_window` bars (default: 3)
    3. Sweep distance = max excursion beyond pool level
```

### Data Requirements

- M5 candle history (minimum 200 bars)
- Current price (bid/ask)
- Pip size (symbol-dependent)

### Implementation Location

`core/market_intelligence/liquidity_detector.py` (new module)

### Fields Populated

| Field | Source |
|---|---|
| `equal_highs_above` | bool — pool exists above current price |
| `equal_highs_distance_pips` | distance to nearest pool above |
| `equal_highs_count` | number of touches forming the pool |
| `equal_lows_below` | bool — pool exists below |
| `equal_lows_distance_pips` | distance to nearest pool below |
| `equal_lows_count` | touches |
| `liquidity_sweep_just_occurred` | sweep in last N bars |
| `sweep_direction` | BULLISH (swept lows) / BEARISH (swept highs) |
| `sweep_distance_pips` | how far past the level |
| `bars_since_sweep` | recency |

### Session Extremes (Previous Day/Session)

```
prev_session_high = max(candle.high) for candles in previous session
prev_session_low = min(candle.low) for candles in previous session

Session boundaries (UTC):
    ASIA:   00:00 - 07:00
    LONDON: 07:00 - 12:00
    NY:     12:00 - 17:00
    OFF:    17:00 - 00:00

prev_day_high = max(candle.high) for previous calendar day
prev_day_low = min(candle.low) for previous calendar day

Swept: current bar high > prev_session_high (or low < prev_session_low)
```

### Edge Cases

- Weekend gaps: invalidate session data from Friday
- Low-liquidity periods (ASIA on minors): wider tolerance
- Multiple pools at similar levels: merge into single pool
- Sweep followed by continuation (not reversal): still record as sweep

### Tests Required

- Equal highs detected from known candle patterns
- Equal lows detected
- Pool invalidation when price breaks through
- Sweep detection with close-back requirement
- Session boundaries computed correctly
- Previous day extremes correct
- Sweep distance computed in pips

---

## Component 4: Fair Value Gap Detection

### Algorithm

**Bullish FVG (imbalance favoring buyers):**
```
Given three consecutive candles C[0], C[1], C[2]:
    IF C[2].low > C[0].high
    THEN Bullish FVG exists between C[0].high and C[2].low
    FVG size = C[2].low - C[0].high
    FVG midpoint = (C[0].high + C[2].low) / 2
```

**Bearish FVG (imbalance favoring sellers):**
```
Given three consecutive candles C[0], C[1], C[2]:
    IF C[2].high < C[0].low
    THEN Bearish FVG exists between C[2].high and C[0].low
    FVG size = C[0].low - C[2].high
    FVG midpoint = (C[2].high + C[0].low) / 2
```

**Minimum size filter:**
```
FVG valid only if: fvg_size >= atr * min_atr_ratio (default: 0.3)
```

**Fill tracking:**
```
filled_pct = (penetration into FVG) / fvg_size
    - 0% = untouched
    - 50% = price reached midpoint
    - 100% = fully filled (price closed through entire gap)

FVG invalidated when: filled_pct >= 100% (fully closed)
```

**Validity duration:** Maximum 100 M5 bars (~8 hours). Older FVGs expire.

### Implementation Location

`core/market_intelligence/fvg_detector.py` (new module)

### Fields Populated

| Field | Source |
|---|---|
| `nearest_fvg_above_price` | midpoint of nearest unfilled bullish FVG above |
| `nearest_fvg_above_distance_pips` | distance to it |
| `fvg_above_filled_pct` | how filled |
| `nearest_fvg_below_price` | nearest bearish FVG below |
| `nearest_fvg_below_distance_pips` | distance |
| `fvg_below_filled_pct` | fill level |
| `price_inside_fvg` | currently within an FVG |
| `fvg_direction_if_inside` | BULLISH / BEARISH |
| `total_unfilled_fvgs_above` | count above price |
| `total_unfilled_fvgs_below` | count below price |

### Tests Required

- Bullish FVG detected from 3-candle pattern
- Bearish FVG detected
- Minimum size filter applied
- Fill tracking updates correctly
- Expired FVGs removed
- Multiple FVGs handled
- Nearest FVG computation correct

---

## Component 5: Order Block Detection

### Algorithm

**Bullish Order Block:**
```
Given candles C[0..n]:
    1. Find displacement: 3+ consecutive bullish candles with expanding bodies
       AND total move > 2.0 * ATR
    2. The LAST BEARISH candle BEFORE the displacement = Order Block
    3. OB range = [candle.low, candle.high]
    4. OB is valid until mitigated (price returns to the zone)
```

**Bearish Order Block:**
```
    1. Find displacement: 3+ consecutive bearish candles with expanding bodies
       AND total move > 2.0 * ATR
    2. The LAST BULLISH candle BEFORE the displacement = Order Block
    3. OB range = [candle.low, candle.high]
```

**Strength score:**
```
strength = min(1.0, displacement_size / (ATR * 4))
    - 0.5 = displacement of 2 ATR (minimum)
    - 1.0 = displacement of 4+ ATR (maximum)
```

**Mitigation:**
```
OB is mitigated when: price closes within [ob.low, ob.high]
After mitigation: OB is marked as used (can still be observed but flagged)
```

**Invalidation:**
```
OB invalidated when:
    - Price closes THROUGH the OB entirely (break of the zone)
    - OR age > 500 bars (~40 hours)
```

**Multiple OBs:**
- Maximum 3 active demand OBs + 3 active supply OBs per symbol
- Newer OBs take priority

### Implementation Location

`core/market_intelligence/order_block_detector.py` (new module)

### Fields Populated

| Field | Source |
|---|---|
| `nearest_demand_ob_price` | midpoint of nearest unmitigated demand OB |
| `nearest_demand_ob_distance_pips` | distance |
| `demand_ob_timeframe` | "M15" (all detected from M15/M5 data) |
| `demand_ob_mitigated` | whether price has already visited |
| `demand_ob_strength` | 0-1 displacement quality |
| `nearest_supply_ob_price` | nearest supply OB |
| `nearest_supply_ob_distance_pips` | distance |
| `supply_ob_timeframe` | detection TF |
| `supply_ob_mitigated` | visited flag |
| `supply_ob_strength` | quality |
| `price_inside_ob` | currently in an OB zone |
| `ob_type_if_inside` | DEMAND / SUPPLY |

### Known Limitations

- 1-bar confirmation for swing levels may miss some institutional moves
- M5 detection only (H4/H1 OBs would need separate higher-TF implementation)
- Consecutive expanding bodies is a proxy for institutional displacement — not guaranteed

### Tests Required

- Bullish OB detected after bullish displacement
- Bearish OB detected after bearish displacement
- Minimum displacement filter (2 ATR) enforced
- Mitigation flagged correctly
- Invalidation on break-through
- Strength score computed
- Age-based expiry works
- Max 3 active per direction

---

## Dependency Order

```
Phase 1 (must be first — enables all other components):
    ├── 1. ATR propagation fix (5 lines of code)
    └── 2. Swing level model fix (add fields to summaries + extraction)

Phase 2 (independent of each other — can be parallel):
    ├── 3. Liquidity detector
    ├── 4. FVG detector
    └── 5. Order Block detector

Phase 3 (requires Phase 2 complete):
    └── 6. V3 builder integration (connect all detectors to V3 fields)
```

**Rationale:** ATR is required by FVG (min size), OB (displacement threshold), and displacement/rejection detection. Swing levels are required for range_position. Without Phase 1, nothing else works.

---

## Architecture Location

| Component | Module | Reason |
|---|---|---|
| ATR propagation | `core/observers/v3_opportunity_observer.py` (compute from candles) | Simplest fix, no model changes needed |
| Swing level fields | `core/market_context/models.py` + `builder.py` | Structural fix — makes data available to all consumers |
| Liquidity detector | `core/market_intelligence/liquidity_detector.py` (NEW) | New domain — clean separation |
| FVG detector | `core/market_intelligence/fvg_detector.py` (NEW) | New domain |
| OB detector | `core/market_intelligence/order_block_detector.py` (NEW) | New domain |
| V3 builder integration | `core/v3_opportunity_builder.py` | Consumes detector outputs |

---

## Implementation Roadmap

### Phase 1: Minimum Viable V3 (makes observations meaningful)

**Scope:** Fix ATR + fix swing levels → enables displacement/rejection + range_position

| Task | Files Changed | LOC | Impact |
|---|---|---|---|
| Compute ATR from candles in V3 observer | `core/observers/v3_opportunity_observer.py` | ~5 | Enables 7 displacement/rejection fields |
| Add `swing_high`/`swing_low` to H1Summary | `core/market_context/models.py` | ~4 | Model fix |
| Add `swing_high`/`swing_low` to M15Summary | `core/market_context/models.py` | ~4 | Model fix |
| Copy swing levels in `_extract_h1()` | `core/market_context/builder.py` | ~4 | Data flows |
| Copy swing levels in `_extract_m15()` | `core/market_context/builder.py` | ~6 | Data flows |
| Update V3 builder to read correct field names | `core/v3_opportunity_builder.py` | ~10 | Correct mapping |

**Result:** 25+ previously-empty fields become populated (range_position, distances, displacement, rejection).

### Phase 2: Location Intelligence Detectors

| Task | New Files | LOC | Impact |
|---|---|---|---|
| Create `core/market_intelligence/__init__.py` | 1 | 5 | Package |
| Liquidity detector | 1 | ~200 | 12 liquidity fields |
| FVG detector | 1 | ~150 | 10 FVG fields |
| Order Block detector | 1 | ~200 | 12 OB fields |
| Session extremes (prev day/session) | In liquidity detector | ~50 | 8 session fields |
| V3 builder integration | 1 modified | ~40 | Connect all to V3 |

**Result:** All 85 V3 fields potentially populated.

### Phase 3: Research Validation

| Task | Impact |
|---|---|
| Collect 200+ fully-populated V3 records | Statistical significance possible |
| Run V2 Discovery Engine on V3 fields | Test location hypothesis |
| Compare V3 vs V2 predictive power | Determine if location adds information |

---

## V3 Readiness Criteria

A valid V3 record requires ALL of:

- [ ] `price_at_observation` > 0
- [ ] `h4_range_position` in (0, 1) — not exactly 0 (indicates computed)
- [ ] `h1_range_position` in (0, 1)
- [ ] `atr` > 0 (real ATR in price units)
- [ ] `spread` > 0
- [ ] At least ONE of: `liquidity_sweep_just_occurred`, `price_inside_ob`, `price_inside_fvg`, `rejection_candle_present`, or `displacement_into_level` OR reasonable distances to levels
- [ ] `nearest_support_distance_pips` > 0 OR `nearest_resistance_distance_pips` > 0

**Collection target after Phase 1:** 200 records meeting first 5 criteria
**Collection target after Phase 2:** 200 records meeting all criteria

---

## Testing Strategy

### Per-Component Unit Tests

| Component | Tests |
|---|---|
| ATR propagation | ATR computed correctly from 14 candles; zero on insufficient data; V3 record has non-zero ATR |
| Swing level fix | H1Summary.swing_high populated; MarketContextBuilder copies from BiasSnapshot; V3 range_position non-zero |
| Liquidity detector | Equal highs found; tolerance works; pools expire; sweeps detected; session extremes correct |
| FVG detector | Bullish/bearish FVG found; size filter; fill tracking; expiry; nearest computation |
| OB detector | Demand/supply OB found; displacement threshold; mitigation; invalidation; strength score |

### Integration Tests

- Full pipeline: candles → TimeframeCache → HTFContext → MarketContext → V3 builder → JSONL
- V3 record quality check: percentage of non-zero fields must exceed threshold
- Observer #9 still fire-and-forget (failure doesn't break pipeline)

### Historical Replay Tests

- Run detectors on known historical candle data with expected outcomes
- Verify FVG count matches manual count on sample data
- Verify liquidity sweep detection on known sweep events

### Data Quality Checks

- After Phase 1: `>80%` of V3 records should have non-zero range_position
- After Phase 2: `>50%` of V3 records should have at least one liquidity/OB/FVG event
- Field variance check: each populated field should have `>2` distinct values across dataset

---

## Final Goal

> "Turn the current V3 schema from mostly empty observations into a reliable market intelligence dataset that can answer whether location, liquidity, and structure contain predictive information."

**Phase 1 (2-3 hours work):** Makes 25+ fields usable. Enables displacement/rejection/range research immediately.

**Phase 2 (1-2 days work):** Makes all 85 fields potentially populated. Enables full location/liquidity research.

**Phase 3 (2-4 weeks elapsed):** Collects sufficient data for statistical analysis and runs discovery.
