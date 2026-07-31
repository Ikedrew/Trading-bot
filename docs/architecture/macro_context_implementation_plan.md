# Macro Context Implementation Plan

---

## 1. Exact Files That Need Modification

### Modified Files (8)

| File | Change Type | Scope |
|---|---|---|
| `core/timeframes/types.py` | ADD dataclass | New `MacroSnapshot` type + updated `HTFContext` |
| `core/timeframes/cache.py` | MODIFY | Add TF constants, _CacheEntry slots, _TimeframeConfig entries, analyzer dispatch, `get_htf_context` builder |
| `core/v10/market_state.py` | MODIFY | Add `macro` field to `V10MarketState` (or pass-through) |
| `core/v3_shadow/builders.py` | MODIFY | Pass `MacroSnapshot` through to MarketContext if needed |
| `core/v10/strategy_engine.py` | MODIFY | Add post-selection confidence modifier (~30 lines) |
| `core/v10/persistence_adapter.py` | MODIFY | Add `macro_context` and `macro_alignment` sections to decision record |
| `core/runtime/live_scanner.py` | MODIFY | Add macro fields to startup version log (optional) |
| `core/config.py` | MODIFY | Add `MTF_D1_ENABLED`, `MTF_W1_ENABLED`, `MTF_MN_ENABLED`, candle count configs |

### New Files (2)

| File | Purpose |
|---|---|
| `core/timeframes/macro_alignment.py` | Pure function: `compute_macro_alignment(MacroSnapshot, trade_direction) → MacroAlignment` |
| `tests/test_macro_context.py` | Unit tests for MacroSnapshot, alignment computation, confidence modifiers |

---

## 2. New Dataclasses Required

### `MacroSnapshot` (in `core/timeframes/types.py`)

```python
@dataclass(frozen=True)
class MacroSnapshot:
    """MN/W1/D1 macro context — the market story before H4."""

    # Monthly (from RegimeSnapshot on MN1 candles)
    monthly_trend: str = ""
    monthly_trend_strength: float = 0.0
    monthly_phase: str = ""

    # Weekly (from BiasSnapshot on W1 candles)
    weekly_trend: str = ""
    weekly_trend_strength: float = 0.0
    weekly_swing_high: float = 0.0
    weekly_swing_low: float = 0.0
    weekly_bos_level: float = 0.0
    weekly_range_position: float = 0.0

    # Daily (from RegimeSnapshot + BiasSnapshot on D1 candles)
    daily_bias: str = ""
    daily_bias_strength: float = 0.0
    daily_swing_high: float = 0.0
    daily_swing_low: float = 0.0
    daily_range_position: float = 0.0
    daily_atr_ratio: float = 1.0

    # Meta
    bar_time: int = 0
```

### `MacroAlignment` (in `core/timeframes/macro_alignment.py`)

```python
@dataclass(frozen=True)
class MacroAlignment:
    """Interpretation of macro context relative to trade direction."""

    monthly_alignment: str = "NEUTRAL"
    weekly_alignment: str = "NEUTRAL"
    daily_alignment: str = "NEUTRAL"
    alignment_state: str = "NEUTRAL"
    confidence_modifier: float = 0.0
    primary_influence: str = "NONE"
    is_conflicted: bool = False
    data_quality: str = "UNAVAILABLE"
    raw_score: float = 0.0
    narrative: str = ""
```

---

## 3. Data Flow Changes

### Current Flow

```
M5 bar closes
    → TimeframeCache.update_if_needed()
        → checks H4/H1/M15 for new bars
        → runs analyzers if needed
    → TimeframeCache.get_htf_context()
        → returns HTFContext(regime, bias, structure)
    → V10 pipeline runs
        → opportunity → strategy → entry → risk → execution
    → persistence writes record
```

### Proposed Flow (additions in bold)

```
M5 bar closes
    → TimeframeCache.update_if_needed()
        → checks H4/H1/M15 for new bars
        → **checks D1/W1/MN for new bars** (rarely triggers)
        → runs analyzers if needed
    → TimeframeCache.get_htf_context()
        → **builds MacroSnapshot from D1/W1/MN entries**
        → returns HTFContext(**macro**, regime, bias, structure)
    → V10 pipeline runs
        → opportunity → strategy → entry → risk → execution
        → **after strategy selection: compute_macro_alignment(macro, direction)**
        → **apply confidence_modifier to strategy_confidence**
    → persistence writes record
        → **adds macro_context section**
        → **adds macro_alignment section**
```

### Fetch Frequency

| Timeframe | Bar Duration | Fetch Triggered | MT5 Calls Per Day |
|---|---|---|---|
| MN1 | 30 days | Once per day (staleness check) | 1 |
| W1 | 7 days | Once per H4 bar (staleness check) | ~6 |
| D1 | 24 hours | Once per H1 bar (new-bar check) | ~24 |
| **Total additional MT5 calls** | | | **~31/day** (negligible) |

### TimeframeCache Internal Changes

```python
# New constants
_TF_D1 = 16408
_TF_W1 = 32769
_TF_MN = 49153

# New duration mappings
_TF_SECONDS[_TF_D1] = 86400
_TF_SECONDS[_TF_W1] = 604800
_TF_SECONDS[_TF_MN] = 2592000

# New _CacheEntry slots in __init__
self._entries[_TF_D1] = _CacheEntry()
self._entries[_TF_W1] = _CacheEntry()
self._entries[_TF_MN] = _CacheEntry()

# New _TimeframeConfig entries appended to self._tf_configs
_TimeframeConfig(tf_constant=_TF_D1, candle_count=100, enabled=True, name="D1")
_TimeframeConfig(tf_constant=_TF_W1, candle_count=52, enabled=True, name="W1")
_TimeframeConfig(tf_constant=_TF_MN, candle_count=24, enabled=True, name="MN")

# New _run_analyzer branches
elif tf == _TF_D1:
    return analyze_regime(candles)   # Reuse H4 regime analyzer
elif tf == _TF_W1:
    return analyze_bias(candles)     # Reuse H1 bias analyzer
elif tf == _TF_MN:
    return analyze_regime(candles)   # Reuse H4 regime analyzer
```

### `get_htf_context` Change

```python
def get_htf_context(self, current_price: float = 0.0) -> HTFContext:
    # ... existing code for regime/bias/structure ...

    # Build MacroSnapshot from D1/W1/MN entries
    macro = self._build_macro_snapshot(current_price)

    return HTFContext(
        macro=macro,       # NEW
        regime=regime_snap,
        bias=bias_snap,
        structure=struct_snap,
    )
```

### Strategy Engine Change (post-selection only)

```python
# In select_strategy(), AFTER winner is determined:
if winner != StrategyFamily.NONE and state.macro is not None:
    from core.timeframes.macro_alignment import compute_macro_alignment
    alignment = compute_macro_alignment(state.macro, winner_direction)
    confidence = min(1.0, max(0.40, confidence + alignment.confidence_modifier))
```

---

## 4. Testing Requirements

### Unit Tests (`tests/test_macro_context.py`)

| Test | Verifies |
|---|---|
| `test_macro_snapshot_creation` | MacroSnapshot can be created with defaults |
| `test_macro_snapshot_from_regime_snapshot` | RegimeSnapshot fields map correctly to monthly/daily fields |
| `test_macro_snapshot_from_bias_snapshot` | BiasSnapshot fields map correctly to weekly fields |
| `test_alignment_full_aligned` | All layers ALIGNED → +0.15 modifier, state=FA |
| `test_alignment_full_opposition` | All layers OPPOSING → -0.15, state=FO |
| `test_alignment_conflicted` | Mixed aligned+opposing → CONFLICTED state, penalty applied |
| `test_alignment_all_neutral` | All NEUTRAL → 0.00, state=NEUTRAL |
| `test_alignment_missing_data` | None macro → modifier=0.00, quality=UNAVAILABLE |
| `test_alignment_weak_strength_treated_neutral` | Strength < 0.3 → NEUTRAL regardless of direction |
| `test_weighting_d1_highest` | D1 ALIGNED alone gives more modifier than MN alone |
| `test_confidence_floor_040` | Modifier cannot push confidence below 0.40 |
| `test_confidence_cap_100` | Modifier cannot push confidence above 1.00 |
| `test_modifier_cap_020` | Even extreme inputs produce max ±0.20 |
| `test_primary_influence_correct` | Highest absolute contributor identified |
| `test_strategy_selection_unchanged_by_macro` | Same state → same strategy family with/without macro (only confidence changes) |

### Integration Tests

| Test | Verifies |
|---|---|
| `test_htf_context_includes_macro` | `get_htf_context()` returns HTFContext with `macro` field |
| `test_macro_not_fetched_every_m5` | D1/W1/MN _check_new_bar returns False on most M5 cycles |
| `test_persistence_includes_macro_context` | Decision record contains `macro_context` section |
| `test_persistence_includes_macro_alignment` | Decision record contains `macro_alignment` section |
| `test_null_macro_persistence` | If macro is None, sections written as null (not omitted) |

### Non-Regression Tests

| Test | Verifies |
|---|---|
| `test_existing_strategy_tests_still_pass` | All 15 existing strategy engine tests unchanged |
| `test_existing_pipeline_tests_pass` | Full V10 pipeline suite green |
| `test_macro_never_blocks_strategy` | With FO macro, strategy still selects (only confidence modified) |

---

## 5. Rollout Order

### Phase 1: Foundation (no pipeline impact)

| Step | File | Change | Risk |
|---|---|---|---|
| 1.1 | `core/timeframes/types.py` | Add `MacroSnapshot` dataclass | NONE — new type, nothing uses it yet |
| 1.2 | `core/timeframes/types.py` | Add `macro: MacroSnapshot | None = None` to `HTFContext` | NONE — optional field with default None |
| 1.3 | `core/timeframes/macro_alignment.py` | Create new file with `compute_macro_alignment()` | NONE — new file, nothing imports it |
| 1.4 | `tests/test_macro_context.py` | Write all unit tests for MacroSnapshot + alignment | NONE — tests only |

**Checkpoint: All tests pass. No behaviour change.**

### Phase 2: Cache Integration (data flows but isn't consumed)

| Step | File | Change | Risk |
|---|---|---|---|
| 2.1 | `core/config.py` | Add `MTF_D1_ENABLED=True`, etc. config vars | NONE — config only |
| 2.2 | `core/timeframes/cache.py` | Add `_TF_D1/W1/MN` constants + `_TF_SECONDS` entries | NONE — constants only |
| 2.3 | `core/timeframes/cache.py` | Add `_CacheEntry` slots + `_TimeframeConfig` entries | LOW — registers TFs but `_check_new_bar` + `_fetch_candles` already handle any TF |
| 2.4 | `core/timeframes/cache.py` | Add analyzer dispatch branches in `_run_analyzer` | LOW — new `elif` branches for new TF constants |
| 2.5 | `core/timeframes/cache.py` | Add `_build_macro_snapshot()` method + update `get_htf_context()` | LOW — builds MacroSnapshot and adds to HTFContext. Downstream ignores it (field is new). |

**Checkpoint: TimeframeCache fetches D1/W1/MN data. HTFContext.macro is populated. Pipeline doesn't read it yet. All tests pass.**

### Phase 3: Pipeline Consumption (macro influences confidence)

| Step | File | Change | Risk |
|---|---|---|---|
| 3.1 | `core/v10/strategy_engine.py` | Add post-selection macro confidence modifier (~15 lines at end of `select_strategy`) | MEDIUM — modifies strategy confidence. Cannot change selection (applied after). Confidence bounded [0.40, 1.00]. |
| 3.2 | Run full strategy test suite | Verify all pass (macro=None in tests → modifier=0.00) | Validation |

**Checkpoint: Strategy confidence is now macro-adjusted. Selection unchanged. All tests pass.**

### Phase 4: Persistence (data visible in records)

| Step | File | Change | Risk |
|---|---|---|---|
| 4.1 | `core/v10/persistence_adapter.py` | Add `macro_context` + `macro_alignment` sections to `build_v10_decision_record()` | LOW — additive fields. Null if macro unavailable. |
| 4.2 | `core/v10/persistence_adapter.py` | Add `macro_alignment_state` + `macro_confidence_modifier` + `macro_data_quality` to ledger entry | LOW — additive. |
| 4.3 | Run persistence tests | Verify schema validation passes with new fields | Validation |

**Checkpoint: Decision records include macro data. Research queries enabled. All tests pass.**

### Phase 5: Observability

| Step | File | Change | Risk |
|---|---|---|---|
| 5.1 | `core/runtime/live_scanner.py` | Add macro context to startup version log | NONE |
| 5.2 | Terminal report | Add `[V10 MACRO CONTEXT]` section to decision trace output | NONE — display only |

**Checkpoint: Full implementation live. All features operational.**

---

## 6. Rollback Plan

### Per-Phase Rollback

| Phase | Rollback Method | Impact |
|---|---|---|
| Phase 5 (observability) | Revert display code | Zero pipeline impact |
| Phase 4 (persistence) | Remove `macro_context`/`macro_alignment` from `build_v10_decision_record` | Records shrink back to pre-macro size. No data loss for existing fields. |
| Phase 3 (confidence modifier) | Remove the ~15 lines in `select_strategy` that apply macro modifier | Strategy confidence returns to base. Zero selection change. |
| Phase 2 (cache integration) | Set `MTF_D1_ENABLED=False`, `MTF_W1_ENABLED=False`, `MTF_MN_ENABLED=False` | Cache stops fetching D1/W1/MN. `get_htf_context` returns `macro=None`. Zero impact downstream. |
| Phase 1 (foundation) | Delete `macro_alignment.py`, remove MacroSnapshot from types.py, remove `macro` from HTFContext | Full removal (only if never deployed) |

### Emergency Killswitch (no code change needed)

Add to `core/config.py`:

```python
MACRO_CONTEXT_ENABLED = True  # Set False to disable all macro influence
```

In `select_strategy`:
```python
if config.MACRO_CONTEXT_ENABLED and state.macro is not None:
    # apply modifier
```

**Killswitch disables macro influence with a single config change. No deployment needed.**

### Data Safety

- Old decision records remain valid (no field removed or renamed)
- New records with macro_context are backwards-readable (extra fields are ignored by old parsers)
- If rolled back, new records simply won't have macro_context (null section)
- No database migration required (JSONL files)

---

## Summary

| Metric | Value |
|---|---|
| Files modified | 8 |
| Files created | 2 |
| New lines of code | ~200 |
| New algorithms | 0 (all reuse existing analyzers) |
| Phases | 5 |
| Highest-risk phase | Phase 3 (confidence modifier — bounded, tested, killswitch available) |
| Total estimated effort | ~3 hours |
| Rollback time | <5 minutes (config flag) |
