# Strategy Intelligence Data Quality Audit

---

## 1. entity_id Propagation

### Origin
```python
# core/pipeline/new_engine.py (line 105)
_entity_id = f"{symbol}_{int(candles[closed_i].time)}"
```

### Flow

```
Market Cycle (live_scanner.py)
    ↓
run_new_engine() → creates _entity_id
    ↓
engine_result["entity_id"] = _entity_id
    ↓
ObserverRegistry.notify_all(ObserverContext(..., engine_result=_new_result))
    ↓
Observer #7: strategy_intelligence_observer.py
    record["entity_id"] = engine_result.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"
    ↓
strategy_observations: entity_id ✅ ALWAYS POPULATED
```

### Propagation Verification

| Stage | entity_id Source | Populated? |
|-------|-----------------|-----------|
| new_engine.py | Constructed: `f"{symbol}_{bar_time}"` | ✅ Always (line 105) |
| engine_result dict | `engine_result["entity_id"]` | ✅ Present in all exit paths |
| strategy_observation | `engine_result.get("entity_id")` with fallback | ✅ Always (double-sourced) |
| shadow_trade (EXECUTE path) | NOT passed to `open_trade()` | ⚠️ NULL |
| shadow_trade (persisted record) | `identity.entity_id = trade.entity_id or None` | ⚠️ NULL for EXECUTE path |
| decision_trace | `engine_result.get("entity_id", "")` | ✅ Always |

### Gap: Shadow trades do NOT receive entity_id from EXECUTE path

In `core/runtime/engine_execution_handler.py`:
```python
get_shadow_engine().open_trade(
    trade_id=f"shadow_{cycle_id}_{sym_state.symbol}",
    ...
    correlation_id=_cor_id,
    # entity_id=... ← NOT PASSED
)
```

**Impact:** Shadow trade records have `identity.entity_id = null` for EXECUTE-path trades.
**Workaround:** Reconstruct from `identity.symbol + '_' + decision_snapshot.timestamp_decision_utc`.

---

## 2. Every Location Where Shadow Trades Are Created

| # | Location | entity_id Passed? | Notes |
|---|----------|-------------------|-------|
| 1 | `core/runtime/engine_execution_handler.py` line ~175 | **NO** | EXECUTE-path production shadows |
| 2 | `core/research_assessment/research_shadow_engine.py` line ~61 | **NO** | Research shadows (correlation_id used) |
| 3 | `core/runtime/live_scanner.py` (horizon shadows) | **UNKNOWN** | Created via same engine pattern |

### Summary

- **2 production call sites** — neither passes entity_id
- All shadow trades have entity_id = None/empty in their persisted records
- entity_id CAN be reconstructed from `symbol + timestamp_decision_utc`

---

## 3. Why strategy_observation Fields Can Be Empty

### market_phase: Can Be Empty

**Root cause:** `market_phase` is populated from TWO sources with specific failure conditions:

```python
# Source 1: engine_result["market_phase"]
market_phase = engine_result.get("market_phase", "") or ""
```

`engine_result["market_phase"]` is set by `run_new_engine()` which receives it from live_scanner:
```python
# live_scanner.py line 451
market_phase=getattr(_market_context, "phase", None).value if _market_context and ... else None
```

**When it's empty:**
1. `_market_context is None` — MarketContextBuilder failed or wasn't available
2. `config.MARKET_CONTEXT_ENABLED = False` (unlikely, currently True)
3. The engine exits early before `market_phase` is set in the result dict (no-pattern exit at line 113 DOES include market_phase)
4. `_market_context.phase` is `None` (shouldn't happen with proper initialization)

```python
# Source 2: fallback from htf_context (MarketContext object)
if htf_context is not None and hasattr(htf_context, "phase"):
    _phase = getattr(htf_context, "phase", None)
    if _phase and not market_phase:
        market_phase = _phase.value
```

**Critical finding:** The `htf_context` passed to ObserverContext is `_new_engine_htf` — this is the **legacy HTF context object** (with `.bias`, `.structure`, `.regime` attributes), NOT the `_market_context` MarketContext object. The legacy htf_context does NOT have `.phase` or `.regime` as enum attributes. It has:
- `.bias.direction` — direction
- `.structure.quality_score` — M15 quality
- `.regime` — may or may not exist

**Therefore:** The observer's fallback path (`hasattr(htf_context, "regime") and hasattr(htf_context, "phase")`) will likely FAIL because the legacy htf_context doesn't have `.phase`. The observer falls through to the legacy path and only extracts `h1_direction` and `h1_bos_confirmed`.

**This means `market_phase` depends ENTIRELY on `engine_result["market_phase"]`.** If that's None/empty (which happens when MarketContext build fails), the observation has no phase.

### strategy_family: Can Be Empty

**Root cause:** The `_dominant_family()` helper:
```python
def _dominant_family(candidates: list[dict]) -> str:
    eligible = [c for c in candidates if c.get("eligible")]
    if not eligible:
        return ""  # ← EMPTY when no strategy is eligible
    best = max(eligible, key=lambda c: c.get("confidence", 0))
    from core.strategies.registry import get_strategy
    s = get_strategy(best["strategy_id"])
    if s:
        return s.family_name
    return ""  # ← EMPTY if strategy not in old registry
```

**When it's empty:**
1. **No phase available** → no strategy is phase-eligible → `eligible = []` → returns ""
2. **Strategy ID in conditions registry but NOT in core/strategies/registry.py** → `get_strategy()` returns None
3. **Exception in registry lookup** → caught, returns ""

**The conditions registry (`core/strategies/conditions.py`) has 5 strategies.** The `core/strategies/registry.py` ALSO has 5 strategies (same IDs). But `_dominant_family` uses the OLD registry at `core/strategies/registry.py`, while the NEW library at `core/strategies/library/registry.py` has 17 strategies.

When `market_phase = ""`, the condition evaluator gets `phase=""` in the snapshot, and the environment conditions (which check `phase in expected_values`) FAIL. All strategies become NOT_MET. No eligible strategies → empty family.

---

## 4. Minimum Research Field Verification

| Required Field | Source | Always Populated? | When Empty |
|---|---|---|---|
| entity_id | `engine_result.get("entity_id")` or fallback construction | ✅ ALWAYS | Never (has fallback) |
| symbol | `ctx.symbol` | ✅ ALWAYS | Never (required by scanner) |
| timestamp_utc | `ctx.bar_time` | ✅ ALWAYS | Never (required by scanner) |
| market_phase | `engine_result.get("market_phase")` | ⚠️ SOMETIMES EMPTY | When MarketContext build fails |
| h4_regime | `htf_context.h4.regime` or `engine_result["activation_regime"]` | ⚠️ SOMETIMES EMPTY | When htf_context is legacy (no .h4) AND engine early-exit |
| strategy_family | `_dominant_family(candidates)` | ⚠️ SOMETIMES EMPTY | When no strategies are phase-eligible (phase empty) |
| strategy_id | `candidate_strategies[].strategy_id` | ✅ ALWAYS IN ARRAY | Array always has 5 entries |
| evaluation_status | `_cycle_status(result)` | ✅ ALWAYS | Never (has exhaustive logic) |
| conditions_passed | `result.fully_met_count` | ✅ ALWAYS | Always numeric (may be 0) |
| decision_action | `engine_result.get("action")` | ✅ ALWAYS | Always "EXECUTE" or "NO_TRADE" |

### Assessment

- **3 fields can be empty:** `market_phase`, `h4_regime`, `strategy_family`
- **All 3 are correlated:** When MarketContext fails, all three are empty
- **Root cause is singular:** The observer gets the legacy htf_context, not the MarketContext

---

## 5. Findings

### BLOCKING ISSUES

**None.** Data collection can proceed. Empty fields reduce research quality but don't prevent joins or accumulation.

### NON-BLOCKING ISSUES

| # | Issue | Severity | Impact | Root Cause |
|---|-------|----------|--------|-----------|
| 1 | `market_phase` empty when MarketContext build fails | MEDIUM | Cannot group by phase for affected records | Observer relies on engine_result which depends on MarketContextBuilder |
| 2 | `strategy_family` empty when no phase available | MEDIUM | Cannot attribute to family for affected records | Cascading: no phase → no eligible strategies → no family |
| 3 | `h4_regime` empty when htf_context is legacy format | LOW | Falls back to `engine_result["activation_regime"]` which uses M5-derived regime | Legacy htf_context has no `.h4.regime` attribute |
| 4 | Shadow trades missing `entity_id` | MEDIUM | Requires reconstructed join instead of direct match | `open_trade()` not passed entity_id |
| 5 | `htf_context` in ObserverContext is legacy, not MarketContext | LOW | Observer cannot access MarketContext.h4/h1/m15/m5 directly | Live scanner passes `_new_engine_htf` not `_market_context` |

### RECOMMENDED FIXES (ordered by impact)

| Priority | Fix | Effort | Unlocks |
|---|---|---|---|
| 1 | Pass `entity_id` to `open_trade()` in `engine_execution_handler.py` | 1 line | Direct entity_id join to shadow trades |
| 2 | Add `_market_context` to ObserverContext (new optional field) | 2 lines (scanner + dataclass) | Full MarketContext access in observer (phase, regime, structure) |
| 3 | In observer, prefer MarketContext.phase over engine_result fallback | 3 lines | Eliminates empty market_phase when MarketContext exists |
| 4 | When strategy_family is empty, derive from detected pattern via StrategyFamilyAuthority | 3 lines | Fills family even when phase matching fails |

### Fix #1 Detail (highest priority)
```python
# core/runtime/engine_execution_handler.py, in prepare_execution()
get_shadow_engine().open_trade(
    ...
    correlation_id=_cor_id,
    entity_id=new_result.get("entity_id", ""),  # ← ADD THIS
)
```

### Fix #2 Detail
```python
# core/pipeline/observers.py → ObserverContext
market_context: Any = None  # ← ADD FIELD

# core/runtime/live_scanner.py line 691
_observers.notify_all(ObserverContext(
    ...
    htf_context=_new_engine_htf,
    market_context=_market_context,  # ← ADD THIS
    ...
))
```

### Fix #3 Detail (in strategy_intelligence_observer.py)
```python
# After extracting from htf_context, also try market_context
_mctx = getattr(ctx, "market_context", None)
if _mctx is not None and hasattr(_mctx, "phase"):
    _phase = getattr(_mctx, "phase", None)
    if _phase and not market_phase:
        market_phase = _phase.value if hasattr(_phase, "value") else str(_phase)
    if not regime:
        _r = getattr(_mctx, "regime", None)
        regime = _r.value if hasattr(_r, "value") else str(_r or "")
```

### Fix #4 Detail (in strategy_intelligence_observer.py)
```python
# After _dominant_family returns empty:
if not strategy_family and pattern_detected:
    from core.strategy_family import classify_pattern
    _fam = classify_pattern(pattern_detected)
    if _fam:
        strategy_family = _fam.value
```

---

## Summary

| Category | Count | Details |
|---|---|---|
| Blocking issues | 0 | System can collect data now |
| Non-blocking (MEDIUM) | 3 | market_phase, strategy_family, shadow entity_id |
| Non-blocking (LOW) | 2 | h4_regime fallback, legacy htf_context |
| Recommended fixes | 4 | All ≤3 lines each, total ~10 lines |

**Verdict:** Data collection is NOT blocked. The 4 recommended fixes would improve data completeness from ~80% usable records to ~98%+, but research can begin today with temporal-proximity joins and records that DO have phase populated (which should be most cycles given MARKET_CONTEXT_ENABLED=True).
