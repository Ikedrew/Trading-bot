# PHASE 4C: SHADOW HORIZON ARCHITECTURE AUDIT

**Date:** 2026-07-24
**Objective:** Determine if the system can support shadow evaluation of hypothetical trades per horizon.
**Verdict:** READY WITH GAPS — Core infrastructure exists (shadow trade engine, ATR computation, structure levels). Two additions needed: horizon-aware SL/TP builder and per-horizon shadow trade creation.

---

## 1. Executive Summary

The system already has a `ShadowTradeEngine` that tracks hypothetical trades from entry through bar-by-bar evaluation to outcome. It supports entry_price, stop_loss, take_profit, MFE/MAE tracking, R-multiple computation, and persists results to S3. This is 80% of what Phase 4C needs.

What's missing: a function that computes horizon-specific SL/TP values (using M15/H1 structure instead of M5 candle geometry) and logic to open one shadow trade PER eligible horizon.

---

## 2. Current Architecture Diagram

```
run_new_engine() → OrderIntent (M5 SL/TP)
    │
    ├── EXECUTE → Broker fill (live trade)
    │
    └── Shadow trade opened (same M5 SL/TP)
            │
            ├── evaluate_bar() per cycle (bar_high, bar_low, bar_close)
            ├── MFE/MAE tracked
            ├── Exit: SL hit | TP hit | max_bars timeout
            └── persist_trade_truth() → S3
```

Phase 4C target:

```
run_new_engine() → Horizon classification
    │
    ├── SCALP horizon → shadow trade (M5 SL/TP) ← existing behaviour
    ├── INTRADAY horizon → shadow trade (M15/H1 SL/TP) ← NEW
    └── EXTENDED horizon → shadow trade (H1/H4 SL/TP) ← NEW
            │
            └── Each evaluates independently per bar
```

---

## 3. SL/TP Generation Findings

### Current (M5 SCALP only)

| Component | Location | Behaviour |
|-----------|----------|-----------|
| Entry price | `risk/manager.py` | `ask` (BUY) or `bid` (SELL) |
| Stop loss | `risk/levels.py` | `candle.low - SL_BUFFER` (BUY) or `candle.high + SL_BUFFER` (SELL) |
| Take profit | `risk/levels.py` | `entry ± risk_distance × RR` |
| RR multiplier | `risk/levels.py` | 2.0 (base) or 3.0 (special patterns) |

### Can Different Horizons Produce Different SL/TP?

**Not with current `build_sl_tp()`** — it is hardcoded to M5 candle geometry. BUT:

**Reusable components:**
- Entry price determination (bid/ask) — universal
- RR calculation logic — parameterizable
- MIN_SL guard (ATR-based) — already adaptive

**Components coupled to M5:**
- `candle.high/candle.low` used for SL anchor — must be replaced per horizon
- `SL_BUFFER = 0.0002` — must scale per horizon

### Required New Function

```python
def compute_horizon_sl_tp(
    *,
    horizon: str,           # "SCALP" | "INTRADAY" | "EXTENDED"
    direction: str,         # "BUY" | "SELL"
    entry_price: float,
    m5_candle: Candle,      # For SCALP
    m15_structure: StructureSnapshot | None,  # For INTRADAY
    h1_swing_high: float | None,  # For EXTENDED
    h1_swing_low: float | None,   # For EXTENDED
    atr_m5: float,
) -> tuple[float, float] | None:
    """Returns (sl, tp) for the given horizon."""
```

**Effort:** ~1-2 hours. Medium difficulty.

---

## 4. Market Structure Data Findings

### H1 Structure

| Field | Available? | Source | Contains Price Level? |
|-------|-----------|--------|----------------------|
| H1 direction | ✅ | `BiasSnapshot.direction` | No — only BULLISH/BEARISH/NEUTRAL |
| H1 BOS confirmed | ✅ | `BiasSnapshot.bos_confirmed` | No — only boolean |
| H1 BOS direction | ✅ | `BiasSnapshot.bos_direction` | No — only "BULLISH"/"BEARISH" |
| H1 swing structure | ✅ | `BiasSnapshot.swing_structure` | No — only "HH_HL"/"LH_LL"/"MIXED" |
| **H1 swing high price** | ❌ | NOT in BiasSnapshot | **MISSING** |
| **H1 swing low price** | ❌ | NOT in BiasSnapshot | **MISSING** |
| **H1 BOS break price** | ❌ | NOT in BiasSnapshot | **MISSING** |

**Verdict: PARTIAL.** Direction and structure type exist, but numeric price levels (swing high/low) are NOT exposed in the snapshot. The H1 analyzer (`h1_bias.py`) computes `swing_highs` and `swing_lows` internally but does NOT include them in the `BiasSnapshot` output.

### M15 Structure

| Field | Available? | Source | Contains Price Level? |
|-------|-----------|--------|----------------------|
| Quality score | ✅ | `StructureSnapshot.quality_score` | N/A |
| **Nearest support** | ✅ | `StructureSnapshot.nearest_support` | **YES — price level** |
| **Nearest resistance** | ✅ | `StructureSnapshot.nearest_resistance` | **YES — price level** |
| At key level | ✅ | `StructureSnapshot.at_key_level` | Boolean |

**Verdict: PASS for INTRADAY.** M15 `nearest_support` and `nearest_resistance` provide numeric price levels usable for INTRADAY SL/TP calculation.

### Summary

| Horizon | Structure Source | Price Levels Available? |
|---------|-----------------|----------------------|
| SCALP | M5 candle high/low | ✅ YES |
| INTRADAY | M15 nearest_support/resistance | ✅ YES |
| EXTENDED | H1 swing high/low | ❌ **MISSING** (computed internally, not exposed) |

---

## 5. ATR / Volatility Findings

| ATR Type | Available? | Source | Access Method |
|----------|-----------|--------|---------------|
| M5 ATR(14) | ✅ | `core/features/engine.py` `_compute_atr()` | Called on M5 candles |
| M5 ATR ratio | ✅ | `RegimeSnapshot.atr_ratio` (H4 level) | Available in HTF context |
| M15 ATR | ⚠️ Not pre-computed | Derivable from M15 candles (available in tf_cache) | Would need explicit computation |
| H1 ATR | ⚠️ Not pre-computed | Derivable from H1 candles (available in tf_cache) | Would need explicit computation |

**Can ATR support horizon-specific stop sizing?**

YES — `_compute_atr()` is a pure function that accepts any candle list + period. If M15/H1 candles are available from `tf_cache`, ATR can be computed on them. The `tf_cache` stores H1 and M15 candles already.

**Effort:** Trivial — call `_compute_atr(h1_candles, 14)` on cached candles.

---

## 6. Risk Manager Findings

### Can risk remain constant while stop distance changes?

**YES — infrastructure exists.** `volume_for_risk()` in `risk/position_sizing.py` computes:

```
volume = (equity × risk_percent) / (risk_distance_in_account_currency)
```

This naturally produces smaller lots for wider stops:
- SCALP: 5 pip stop → 0.20 lot
- INTRADAY: 20 pip stop → 0.05 lot
- EXTENDED: 50 pip stop → 0.02 lot

All at the same account risk.

**Currently disabled:** `POSITION_SIZING_MODE = "FIXED"`. But for SHADOW trades, lot size is informational only (not executed). Shadow trades can use any sizing model.

---

## 7. Trade Management Findings

### Which behaviours assume one universal trade?

| Behaviour | Current Config | Assumes Single Horizon? |
|-----------|---------------|------------------------|
| Break-even trigger | `TM_BREAK_EVEN_TRIGGER_RR = 1.0` | YES — same for all |
| Trailing step | `TM_TRAILING_STEP = 0.0` (disabled) | N/A |
| Max time | `TM_MAX_TIME_IN_TRADE_SECONDS = 0.0` (disabled) | N/A |

**For shadow evaluation (Phase 4C):** Trade management is NOT needed. Shadow trades only track bar-by-bar against fixed SL/TP. The `ShadowTradeEngine.evaluate_bar()` already handles this:
- If bar_high >= SL (for SELL) → closed at SL
- If bar_low <= TP (for SELL) → closed at TP
- If bars_elapsed >= max_bars → timeout exit

No break-even or trailing required for shadow evaluation. Just SL/TP/timeout.

---

## 8. Position / Execution Schema Findings

### Can current objects store horizon context?

| Object | Has Horizon Field? | Can Add? |
|--------|-------------------|----------|
| `ShadowTrade` | ❌ No `trade_horizon` field | ✅ Easy — add field to dataclass |
| `OrderIntent` | ❌ No horizon field | ✅ Has `metadata: dict` — use `metadata["horizon"]` |
| `Position` | ❌ No horizon field | ✅ Easy — add field |
| `TradeRecord` | ❌ No horizon field | Not needed for shadow evaluation |
| `Assessment.evidence_contributions` | ✅ Contains `_horizon_classification` | Already stores horizon data |

### Existing Shadow Trade Structure (fully suitable)

```python
ShadowTrade(
    trade_id: str,          # Can encode horizon: f"{symbol}_{bar_time}_{horizon}"
    symbol: str,
    direction: str,
    entry_price: float,     # Same for all horizons (market price)
    stop_loss: float,       # DIFFERENT per horizon
    take_profit: float,     # DIFFERENT per horizon
    entry_time: float,
    strategy: str,          # Can include horizon info
    pattern: str,
    score: float,
    correlation_id: str,
)
```

**The `ShadowTradeEngine` can open multiple shadow trades per opportunity** (one per eligible horizon) with different SL/TP values. No structural changes needed to the engine itself.

---

## 9. Outcome Tracking Findings

### Current Shadow Trade Outcome Tracking

| Capability | Status | Method |
|-----------|--------|--------|
| Bar-by-bar evaluation | ✅ | `evaluate_bar(bar_high, bar_low, bar_close)` |
| SL hit detection | ✅ | Direction-aware comparison |
| TP hit detection | ✅ | Direction-aware comparison |
| Max bars timeout | ✅ | Configurable per engine instance |
| MFE/MAE tracking | ✅ | `max_favourable_price`, `max_adverse_price` |
| R-multiple computation | ✅ | `compute_r_multiple()` at each bar |
| Persistence on close | ✅ | Trade truth v2 written to S3 |

### Can the system calculate hypothetical outcomes per horizon?

**YES — directly.** Example:

```
At detection: GBPUSD SELL

SCALP shadow:   entry=1.2900, SL=1.2910, TP=1.2880 → evaluate_bar() → Result: +2R (TP hit after 5 bars)
INTRADAY shadow: entry=1.2900, SL=1.2940, TP=1.2780 → evaluate_bar() → Result: +3R (TP hit after 40 bars)
EXTENDED shadow: entry=1.2900, SL=1.2980, TP=1.2620 → evaluate_bar() → Result: OPEN (still tracking)
```

Each shadow trade runs independently through `evaluate_bar()` with its own SL/TP.

---

## 10. Required New Components

| Component | Purpose | Difficulty | Depends On |
|-----------|---------|-----------|-----------|
| `core/horizon/horizon_trade_builder.py` | Compute SL/TP per horizon from structure data | MEDIUM | M15 `nearest_support/resistance`, H1 swing levels |
| Expose H1 swing prices in `BiasSnapshot` | Provide numeric price levels for EXTENDED SL | MEDIUM | Modify `h1_bias.py` + `BiasSnapshot` |
| Open multiple shadow trades per opportunity | One per eligible horizon | LOW | Modify shadow trade opening logic in live_scanner |
| Tag shadow trades with `horizon` field | Distinguish SCALP/INTRADAY/EXTENDED outcomes | LOW | Add field to `ShadowTrade` |
| Horizon-specific max_bars | SCALP=12, INTRADAY=96, EXTENDED=576 | LOW | Pass to ShadowTradeEngine |

---

## 11. Phase 4C Readiness Verdict

### READY WITH GAPS

| Requirement | Status | Gap Size |
|-------------|--------|----------|
| Shadow trade engine | ✅ EXISTS | None |
| Bar-by-bar outcome evaluation | ✅ EXISTS | None |
| MFE/MAE tracking | ✅ EXISTS | None |
| R-multiple computation | ✅ EXISTS | None |
| SCALP SL/TP (M5 geometry) | ✅ EXISTS | None |
| INTRADAY SL/TP (M15 structure) | ✅ `nearest_support/resistance` available | Need builder function |
| EXTENDED SL/TP (H1 swing levels) | ⚠️ PARTIAL | H1 swing prices computed internally but NOT exposed in BiasSnapshot |
| Per-horizon shadow trade creation | ❌ NOT IMPLEMENTED | Need loop over eligible horizons |
| Horizon tag on shadow trades | ❌ NOT IMPLEMENTED | Add `trade_horizon` field |
| Horizon-specific timeout (max_bars) | ❌ NOT IMPLEMENTED | Trivial config |

### Blocking Gaps (must fix before Phase 4C)

1. **H1 swing price levels** — Modify `h1_bias.py` to include `last_swing_high` and `last_swing_low` in `BiasSnapshot`. Currently computed but discarded.
2. **Horizon trade builder** — New function: given a horizon + structure data, produce (SL, TP).

### Non-Blocking (can implement within Phase 4C)

- Per-horizon shadow trade creation
- Horizon tag on ShadowTrade
- Horizon-specific max_bars

---

## 12. Implementation Plan (for Phase 4C when approved)

```
Step 1: Expose H1 swing prices (modify BiasSnapshot + h1_bias.py)
Step 2: Create horizon_trade_builder.py (compute SL/TP per horizon)
Step 3: Add trade_horizon field to ShadowTrade
Step 4: Open one shadow trade per eligible horizon (in live_scanner)
Step 5: Configure per-horizon max_bars
Step 6: Persist with horizon tag for research differentiation
```

**Estimated effort:** 4-6 hours total.
