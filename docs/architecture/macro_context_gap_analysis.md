# Macro Context Layer — Design Gap Analysis

## 1. Existing Analyzers That Can Operate on MN1/W1/D1

| Analyzer | File | Input | Output | Timeframe-Agnostic? | Can Process D1/W1/MN? |
|---|---|---|---|---|---|
| `analyze_regime()` | `core/timeframes/h4_regime.py` | `candles: list[Candle]` | `RegimeSnapshot` (classification, confidence, trend_bias, trend_strength, atr_ratio, ema_slope) | **YES** — no H4-specific constants. Uses EMA-20, ATR-14, structure detection. Works on any candle list with 20+ bars. | **YES** |
| `analyze_bias()` | `core/timeframes/h1_bias.py` | `candles: list[Candle]` | `BiasSnapshot` (direction, confidence, BOS, bos_level, swing_high, swing_low, swing_structure) | **YES** — uses EMA-20, swing pivot detection, BOS detection. No H1-specific logic. | **YES** |
| `analyze_structure()` | `core/timeframes/m15_structure.py` | `candles: list[Candle], current_price: float` | `StructureSnapshot` (quality_score, nearest_support, nearest_resistance, at_key_level, order_block_present) | **YES** — S/R detection from candle pivots + ATR proximity. | **YES** |

**Verdict: All three existing analyzers are fully reusable. Zero new algorithms needed.**

---

## 2. Existing Fields That Can Be Reused

### From `RegimeSnapshot` (produced by `analyze_regime`)

| Field | Useful For | Reuse As |
|---|---|---|
| `classification` | Monthly/Weekly/Daily regime | `monthly_phase`, `weekly_phase`, `daily_phase` (map TRENDING_BULLISH → IMPULSE, RANGING → CONSOLIDATION, etc.) |
| `trend_bias` | Trend direction | `monthly_trend`, `weekly_trend`, `daily_bias` |
| `trend_strength` | Trend conviction | `monthly_trend_strength`, `weekly_trend_strength`, `daily_bias_strength` |
| `atr_ratio` | Volatility context | `daily_atr_ratio` (is today normal or expanded?) |
| `confidence` | Classification certainty | Can feed into macro confidence |

### From `BiasSnapshot` (produced by `analyze_bias`)

| Field | Useful For | Reuse As |
|---|---|---|
| `direction` | Weekly/Daily directional bias | Already provided by RegimeSnapshot.trend_bias (redundant if both run) |
| `last_swing_high` | Weekly/Daily structural boundaries | `weekly_swing_high`, `daily_high_ref` |
| `last_swing_low` | Same | `weekly_swing_low`, `daily_low_ref` |
| `bos_confirmed` + `bos_direction` | Weekly/Daily structural breaks | Context for macro trend changes |
| `bos_level` | Key institutional price level | Weekly bos_level = major structural reference |
| `swing_structure` | HH_HL / LH_LL | Structural character at macro scale |

### From `StructureSnapshot` (produced by `analyze_structure`)

| Field | Useful For | Reuse As |
|---|---|---|
| `nearest_support` | Daily support level | `daily_support` |
| `nearest_resistance` | Daily resistance level | `daily_resistance` |
| `quality_score` | Structure quality | Macro structure quality |

---

## 3. New Fields Required for MacroSnapshot

Only the **composite dataclass** is new. All individual data points come from existing analyzer outputs.

```python
@dataclass(frozen=True)
class MacroSnapshot:
    """MN/W1/D1 context — the market story before H4."""

    # Monthly (from RegimeSnapshot on MN1 candles)
    monthly_trend: str = ""                    # BULLISH / BEARISH / NEUTRAL
    monthly_trend_strength: float = 0.0        # 0.0–1.0
    monthly_phase: str = ""                    # IMPULSE / PULLBACK / CONSOLIDATION / VOLATILE

    # Weekly (from BiasSnapshot on W1 candles)
    weekly_trend: str = ""                     # BULLISH / BEARISH / NEUTRAL
    weekly_trend_strength: float = 0.0
    weekly_swing_high: float = 0.0             # Price level
    weekly_swing_low: float = 0.0              # Price level
    weekly_bos_level: float = 0.0              # Institutional reference
    weekly_range_position: float = 0.0         # 0.0–1.0

    # Daily (from RegimeSnapshot + BiasSnapshot on D1 candles)
    daily_bias: str = ""                       # BULLISH / BEARISH / NEUTRAL
    daily_bias_strength: float = 0.0
    daily_swing_high: float = 0.0              # Today's structural high
    daily_swing_low: float = 0.0               # Today's structural low
    daily_range_position: float = 0.0          # 0.0–1.0
    daily_atr_ratio: float = 1.0              # Today's ATR vs average (volatility context)

    # Meta
    bar_time: int = 0                          # Latest daily bar timestamp
```

**New fields: 16** (all derived from existing analyzer outputs — no new calculations)

---

## 4. Data Flow Location Inside HTFContext

### Current `HTFContext`

```python
@dataclass(frozen=True)
class HTFContext:
    regime: RegimeSnapshot | None = None    # H4
    bias: BiasSnapshot | None = None        # H1
    structure: StructureSnapshot | None = None  # M15
```

### Proposed `HTFContext`

```python
@dataclass(frozen=True)
class HTFContext:
    macro: MacroSnapshot | None = None       # MN/W1/D1  ← NEW
    regime: RegimeSnapshot | None = None     # H4
    bias: BiasSnapshot | None = None         # H1
    structure: StructureSnapshot | None = None  # M15
```

### Flow

```
TimeframeCache._entries:
    _TF_MN → _CacheEntry(snapshot=RegimeSnapshot)     ← analyzer: analyze_regime(mn_candles)
    _TF_W1 → _CacheEntry(snapshot=BiasSnapshot)       ← analyzer: analyze_bias(w1_candles)
    _TF_D1 → _CacheEntry(snapshot=RegimeSnapshot)     ← analyzer: analyze_regime(d1_candles)
    _TF_H4 → _CacheEntry(snapshot=RegimeSnapshot)     (existing)
    _TF_H1 → _CacheEntry(snapshot=BiasSnapshot)       (existing)
    _TF_M15 → _CacheEntry(snapshot=StructureSnapshot)  (existing)

TimeframeCache.get_htf_context():
    # Build MacroSnapshot from MN/W1/D1 entries
    macro = _build_macro_snapshot(mn_entry, w1_entry, d1_entry)
    return HTFContext(macro=macro, regime=h4_entry, bias=h1_entry, structure=m15_entry)
```

---

## 5. Persistence Requirements

### Decision Record Enrichment

Add to `build_v10_decision_record()` in `persistence_adapter.py`:

```python
"macro_context": {
    "monthly_trend": macro.monthly_trend,
    "monthly_strength": macro.monthly_trend_strength,
    "weekly_trend": macro.weekly_trend,
    "weekly_swing_high": macro.weekly_swing_high,
    "weekly_swing_low": macro.weekly_swing_low,
    "weekly_range_position": macro.weekly_range_position,
    "daily_bias": macro.daily_bias,
    "daily_range_position": macro.daily_range_position,
    "daily_atr_ratio": macro.daily_atr_ratio,
} if macro else None
```

### Schema Evolution

This is an ADDITIVE change (new field added, no existing fields removed or renamed). Compatible with `v10_decision_v1` schema evolution rules.

### S3 Impact

No new S3 datasets — macro context is embedded in existing decision records. No schema break.

---

## 6. Architectural Conflicts

| Potential Conflict | Assessment | Mitigation |
|---|---|---|
| **H4 trend_bias vs weekly_trend** | Could disagree (H4 ranging, weekly trending). NOT a conflict — different timeframes measuring different things. | Clear documentation: H4 = session regime, Weekly = multi-day narrative. Both can coexist. |
| **Daily bias vs H1 BOS direction** | Could disagree (daily bearish, H1 BOS bullish = intraday counter-move). | Expected: daily provides context, H1 has authority for trade direction. No override. |
| **Monthly opposing strategy** | Monthly bearish but TREND_CONTINUATION buying. | By design: macro REDUCES confidence (max -0.10), never blocks. |
| **TimeframeCache performance** | Adding 3 new timeframes = 3 more MT5 calls. | Negligible: D1/W1/MN bars close rarely (once per day/week/month). After initial fetch, staleness check will pass most cycles without a refetch. |
| **`_run_analyzer` dispatch** | Currently returns None for unknown TFs. Needs new branches. | Simple addition: `if tf == _TF_D1: return analyze_regime(candles)` etc. |
| **HTFContext is frozen dataclass** | Adding `macro` field requires changing the class definition. | Additive: new optional field `macro: MacroSnapshot | None = None`. No breaking change for existing consumers (they don't read `macro` yet). |
| **Stale multiplier for MN/W1** | `_STALE_MULTIPLIER = 3` works for H4 (12h stale). For D1 that's 3 days, W1 = 3 weeks, MN = 3 months. | Acceptable: macro data IS valid for extended periods. A monthly snapshot from last month is still the correct monthly context until a new month closes. |

---

## Summary: What Exists vs What's New

| Component | Exists? | Status |
|---|---|---|
| MT5 D1/W1/MN candle API | YES | Same `copy_rates_from_pos` API |
| Candle data model | YES | Same `Candle` dataclass |
| Regime analyzer (for MN/D1) | YES | `analyze_regime()` — timeframe-agnostic |
| Bias analyzer (for W1/D1) | YES | `analyze_bias()` — timeframe-agnostic |
| TimeframeCache infrastructure | YES | Fetch, cache, staleness, new-bar detection |
| `_run_analyzer` dispatch | YES (needs 3 new branches) | ~3 lines of code |
| `HTFContext` interface | YES (needs 1 new field) | Additive change |
| `MacroSnapshot` dataclass | **NO — new** | ~16 fields, built from existing snapshots |
| `_build_macro_snapshot()` helper | **NO — new** | ~30 lines mapping analyzer outputs to MacroSnapshot fields |
| Confidence modifiers in strategy engine | **NO — new** | Post-selection adjustment logic (~50 lines) |
| Persistence enrichment | **NO — new** | ~10 lines in persistence_adapter |
| TF constants + config | **NO — new** | `_TF_D1 = 16408`, `_TF_W1 = 32769`, `_TF_MN = 49153` + _TimeframeConfig entries |

### New Code Required

| Category | Estimated Lines |
|---|---|
| `MacroSnapshot` dataclass | 20 |
| `_build_macro_snapshot()` | 30 |
| TF constants + config entries | 15 |
| `_run_analyzer` new branches | 6 |
| `HTFContext` new field | 1 |
| Confidence modifiers (strategy_engine) | 50 |
| Persistence addition | 15 |
| Tests | 60 |
| **Total new code** | **~200 lines** |
