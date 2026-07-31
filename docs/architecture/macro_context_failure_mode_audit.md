# Macro Context Failure Mode Audit

---

## 1. What Happens If D1/W1/MN Data Is Missing?

### Scenarios

| Scenario | Cause | Frequency |
|---|---|---|
| Cold start — no snapshots cached yet | Bot just launched, first cycle | Every restart |
| MT5 not connected at startup | Network/auth failure | Occasional |
| MT5 returns empty candle array | Symbol not available on broker, market closed, data gap | Rare |
| Analyzer returns None | Insufficient bars (< 20 needed for EMA), malformed data | Rare |
| Single timeframe missing (e.g. MN None, D1/W1 available) | Monthly hasn't been fetched yet, or < 24 bars available | First few hours after cold start |

### Impact

| Component | Effect If Macro Is None |
|---|---|
| Strategy selection | **ZERO impact** — selection uses H4/H1/M15/M5 only. Macro is post-selection. |
| Confidence modifier | **ZERO** — if `macro is None`, modifier = 0.00 |
| Persistence | `macro_context: null`, `macro_alignment: {"alignment_state": "UNAVAILABLE", ...}` |
| Research | Record excluded from macro analysis (filtered by `data_quality = 'COMPLETE'`) |

### Mitigation

```python
# In _build_macro_snapshot():
if all entries are None:
    return None  # MacroSnapshot not built — HTFContext.macro = None

# In select_strategy():
if state.macro is None:
    # Skip macro modifier entirely — base confidence unchanged
    pass
```

**Mitigation is inherent:** the design treats None as "no opinion" at every layer. No defensive code needed beyond the null checks already planned.

### Risk Level: NONE

Missing data = no influence = system operates identically to pre-macro behaviour.

---

## 2. What Happens If Timeframe Data Is Stale?

### Scenarios

| Scenario | Staleness Duration | Cause |
|---|---|---|
| Weekend gap (D1 stale) | 2–3 days | Forex market closed Sat/Sun |
| Holiday gap (D1/W1 stale) | 1–5 days | Christmas, New Year, national holidays |
| MN stale after month-end | Up to 30 days old until new monthly bar closes | Normal — monthly only updates once |
| W1 stale mid-week | Up to 7 days | Normal — weekly updates once per week |
| Persistent MT5 disconnect | Hours to days | Network/broker issues |

### Impact

| Staleness Type | Is the Data Still Valid? | Should It Influence? |
|---|---|---|
| W1 snapshot from 3 days ago | **YES** — weekly structure doesn't change intraday | YES (full weight) |
| MN snapshot from 2 weeks ago | **YES** — monthly trend persists | YES (full weight) |
| D1 snapshot from Friday (now Monday) | **PARTIALLY** — Friday's daily context is outdated, Monday is a new day | REDUCE weight |
| D1 snapshot from 3+ days ago | **NO** — too stale to represent "today" | Treat as NEUTRAL |

### Current System Handling

The existing `_is_stale()` check uses `3x timeframe duration`:
- D1 stale after: 3 × 86400 = 3 days ← correct (weekend = stale)
- W1 stale after: 3 × 604800 = 21 days ← appropriate (3 weeks without update = something wrong)
- MN stale after: 3 × 2592000 = 90 days ← appropriate (3 months without update = broken)

When a staleness check triggers, the cache attempts a refetch. If refetch fails, the OLD snapshot persists.

### Mitigation: Add Staleness Awareness to MacroAlignment

```python
def _assess_quality(macro: MacroSnapshot, current_time: float) -> str:
    d1_age = current_time - macro.bar_time
    if d1_age > 86400 * 2:  # D1 older than 2 days
        return "STALE"
    if macro.monthly_trend == "" and macro.weekly_trend == "":
        return "UNAVAILABLE"
    if any field is default/empty:
        return "PARTIAL"
    return "COMPLETE"
```

When `data_quality == "STALE"`:
- Cap confidence modifier at ±0.05 (reduced influence)
- Flag in persistence for research filtering

### Risk Level: LOW

Stale macro data is a CONSERVATIVE error — it either gives slightly wrong context (which is bounded to ±0.05 when stale) or degrades to no influence. Cannot cause bad trades.

---

## 3. What Happens If MT5 Returns Incomplete Candles?

### Scenarios

| Scenario | MT5 Returns | Effect on Analyzer |
|---|---|---|
| Fewer bars than requested | 15 bars instead of 100 for D1 | Analyzer may produce low-confidence result or fail |
| Bars with zero OHLC | Candle(open=0, high=0, low=0, close=0) | ATR/EMA calculations produce NaN or 0 |
| Duplicate timestamps | Same bar_time repeated | No structural pivots detected |
| Gap in timestamps | Missing bars (e.g., 3 consecutive bars absent) | EMA/ATR computed incorrectly |
| Forming bar included | Last element is incomplete current bar | Regime/bias analysis on unfinished data |

### Current System Protection

The TimeframeCache already has:

1. **Closed-bar enforcement:** `candles = candles[:-1]` — always removes the forming bar
2. **Minimum bar check:** If `len(candles) <= 1` after trim → abort, increment failure counter
3. **Analyzer exception catch:** `_run_analyzer` wraps in try/except → returns None on failure
4. **Failure counter:** Consecutive failures tracked per TF/symbol → logged as warnings

### Additional Protection Needed for Macro

| Threat | Mitigation |
|---|---|
| D1 returns 5 bars (< 20 needed for EMA) | `analyze_regime` should handle gracefully — if it doesn't, the existing `try/except` in `_run_analyzer` catches it. Snapshot stays at previous value. |
| W1 returns bars with zero prices | `analyze_bias` swing detection ignores zero-value pivots. BOS detection skips. Result: low-confidence BiasSnapshot (direction=NEUTRAL). |
| MN returns 1 bar only | After forming-bar trim: 0 bars remaining → `len(candles) <= 1` check catches → abort. Entry retains previous snapshot or stays None. |

### Recommended Guard (in `_build_macro_snapshot`)

```python
def _build_macro_snapshot(self, current_price: float) -> MacroSnapshot | None:
    mn_entry = self._entries.get(_TF_MN)
    w1_entry = self._entries.get(_TF_W1)
    d1_entry = self._entries.get(_TF_D1)

    # At minimum need daily context — if ALL are empty, return None
    if all(e.snapshot is None for e in [mn_entry, w1_entry, d1_entry] if e):
        return None

    # Build from whatever is available (None fields → defaults)
    ...
```

### Risk Level: LOW

Existing infrastructure handles incomplete data. Macro layer inherits these protections. The worst case (all analyzers fail) = macro is None = no influence.

---

## 4. What Happens If Macro Confidence Conflicts with Strategy Confidence?

### Scenarios

| Scenario | Strategy Confidence | Macro Modifier | Result | Problem? |
|---|---|---|---|---|
| High-confidence trade, macro aligned | 0.80 | +0.15 | 0.95 | NO — correct boost |
| High-confidence trade, macro full opposition | 0.80 | -0.15 | 0.65 | NO — still tradeable, reduced conviction |
| Low-confidence trade, macro full opposition | 0.50 | -0.15 | 0.40 (floor) | **EDGE CASE** — trade barely passes confidence floor |
| Low-confidence trade, macro full alignment | 0.50 | +0.15 | 0.65 | **POSSIBLE CONCERN** — weak strategy evidence boosted by macro |
| Borderline trade just above threshold, macro pushes below | 0.45 | -0.10 | 0.40 (floor) | **EDGE CASE** — floor prevents macro from killing the trade |

### The Critical Question

**Can macro alignment SAVE a bad trade?**

If strategy confidence is 0.40 (borderline) and macro gives +0.15 → 0.55. Is this dangerous?

**Analysis:** No. Strategy confidence of 0.40 means ALL required evidence was met (R1+R2+R3 all True), just with minimal supporting evidence. Macro alignment (+0.15) says "the larger environment also supports this direction." Boosting from 0.40 to 0.55 means: weak intraday setup, strong macro tailwind. This is a legitimate signal combination — not a false positive.

**Can macro opposition KILL a good trade?**

Strategy confidence 0.90 with macro -0.15 → 0.75. The trade still executes. The lower confidence may result in smaller position size (if risk engine uses confidence for sizing). This is the INTENDED behaviour — a macro headwind should reduce exposure.

### Mitigations (already designed)

| Protection | Effect |
|---|---|
| Floor at 0.40 | Macro cannot push confidence below tradeable threshold |
| Cap at ±0.20 | Macro cannot dominate — maximum 20% of a 1.0 confidence scale |
| Applied post-selection | Macro cannot prevent a strategy from being CHOSEN |
| Only modifies confidence, not selection | A TREND_CONTINUATION with macro opposition is still TREND_CONTINUATION |

### Risk Level: LOW

The bounded modifier (±0.20, floor 0.40) prevents macro from creating or destroying trades. It only adjusts conviction within safe bounds.

---

## 5. What Happens If Macro Fields Fail Persistence?

### Scenarios

| Scenario | Cause | Effect |
|---|---|---|
| `macro_context` section raises during serialization | Field is non-serializable type, NaN float, or circular reference | Decision record write fails entirely |
| Macro fields are None but persistence expects values | `MacroSnapshot` is None, code tries `.monthly_trend` | AttributeError → persistence crash |
| JSONL write succeeds but macro section is malformed | Float precision issues, invalid UTF-8 in narrative string | Record written but unparseable on read |
| Disk full during write | OS-level failure | Record lost (same as any other write failure) |

### Impact of Persistence Failure

| If macro persistence fails... | Does it affect trading? |
|---|---|
| Yes — if it crashes `build_v10_decision_record` | **YES — entire decision record is lost for this cycle. Trade still executes (persistence is post-execution) but no audit trail.** |
| No — if only macro section fails | Depends on implementation. If macro is a separate `try/except` block within the record builder, other fields survive. |

### Mitigation: Isolate Macro Persistence

```python
def build_v10_decision_record(result, cycle_id=0):
    record = { ... existing fields ... }

    # Macro section — isolated, cannot crash the record
    try:
        if result.macro_alignment is not None:
            record["macro_context"] = { ... }
            record["macro_alignment"] = { ... }
        else:
            record["macro_context"] = None
            record["macro_alignment"] = None
    except Exception as exc:
        logger.warning("[PERSISTENCE] macro serialization failed: %s", exc)
        record["macro_context"] = None
        record["macro_alignment"] = {"error": str(exc)[:100]}

    return record
```

**Key principle:** Macro persistence failure must NEVER crash the decision record writer. Wrap in try/except, default to null, log the error.

### Risk Level: MEDIUM (if not isolated), NONE (if isolated)

The mitigation is straightforward: wrap macro serialization in try/except. Without isolation, a macro serialization bug could lose all decision records. With isolation, macro failure only loses macro data for that record.

---

## 6. Observability Required to Prove Correct Version Is Running

### Problem Statement

From the previous audit: the bot was running old code after fixes were deployed. We need to prove:
1. Macro code IS loaded
2. Macro data IS being fetched
3. Macro IS influencing confidence
4. Macro IS being persisted

### Required Observability

#### A. Startup Verification (Phase 1)

```
==================================================
V10 CODE VERSION
==================================================
  strategy_engine: 2026-08-01 09:15:42 UTC
    path: C:\...\core\v10\strategy_engine.py
  macro_alignment: 2026-08-01 09:15:42 UTC
    path: C:\...\core\timeframes\macro_alignment.py
  MACRO_CONTEXT_ENABLED: True
==================================================
```

**Proves:** The macro module was imported from the correct path with the expected modification time.

#### B. First-Fetch Confirmation (Phase 2)

```
[MTF_CACHE_UPDATE] symbol=EURUSD tf=D1 bar_time=1785456000 snapshot_type=RegimeSnapshot
[MTF_CACHE_UPDATE] symbol=EURUSD tf=W1 bar_time=1785283200 snapshot_type=BiasSnapshot
[MTF_CACHE_UPDATE] symbol=EURUSD tf=MN bar_time=1783238400 snapshot_type=RegimeSnapshot
[MACRO_CONTEXT] symbol=EURUSD macro_built=True monthly=BULLISH weekly=BEARISH daily=NEUTRAL quality=COMPLETE
```

**Proves:** D1/W1/MN candles were fetched, analyzers ran, MacroSnapshot was built.

#### C. Per-Decision Confirmation (Phase 3)

```
[V10_STRATEGY] symbol=EURUSD family=MEAN_REVERSION base_confidence=0.70 macro_modifier=-0.05 final_confidence=0.65 alignment=PARTIAL_OPPOSITION
```

**Proves:** Macro modifier was computed and applied to this specific decision.

#### D. Persistence Verification (Phase 4)

Check presence of `macro_context` in the latest decision record:

```python
# Startup check or periodic health check:
last_record = read_last_decision_record(symbol)
assert "macro_context" in last_record
assert last_record["macro_alignment"]["alignment_state"] != "UNAVAILABLE"
```

Or via a simple grep:
```bash
tail -1 logs/v10_decisions/EURUSD/2026-08-01.jsonl | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('macro_alignment',{}).get('alignment_state','MISSING'))"
```

**Proves:** Macro data IS being written to records.

#### E. Absence Detection (the most important)

Log a WARNING if macro was expected but not produced:

```python
# In strategy engine, after selecting strategy:
if config.MACRO_CONTEXT_ENABLED and state.macro is None:
    logger.warning(
        "[MACRO_MISSING] symbol=%s cycle=%d — macro enabled but snapshot unavailable",
        state.symbol, cycle_id,
    )
```

**Proves:** If macro is SUPPOSED to be running but isn't producing data, the absence is visible.

#### F. Health Metric (continuous)

Track and log every 100 cycles:

```
[MACRO_HEALTH] last_100_cycles: macro_available=97 macro_stale=2 macro_missing=1 avg_modifier=+0.03
```

**Proves:** Macro is consistently available and producing reasonable modifiers (not stuck at 0.00 or ±0.15).

---

## Summary: Risk Matrix

| Failure Mode | Probability | Impact on Trading | Impact on Research | Mitigation Difficulty | Risk After Mitigation |
|---|---|---|---|---|---|
| D1/W1/MN data missing | HIGH (every cold start) | NONE (design inherent) | Record excluded | Already designed | **NONE** |
| Timeframe data stale | MEDIUM (weekends) | NONE (modifier capped) | Reduced accuracy | Cap modifier when stale | **NONE** |
| MT5 returns incomplete candles | LOW | NONE (existing protections) | Record excluded | Already built | **NONE** |
| Macro conflicts with strategy | CERTAIN (by design) | LOW (bounded ±0.20) | None | Floor 0.40, cap ±0.20 | **NONE** |
| Macro fails persistence | LOW | **HIGH if not isolated** | Lost macro data | try/except isolation | **NONE** (if isolated) |
| Wrong version running | MEDIUM (proven history) | Unknown | Unknown | Startup log + per-decision log + absence warnings | **NONE** (if observed) |

### Critical Implementation Rule

The single non-negotiable rule from this audit:

> **Macro persistence MUST be wrapped in try/except, isolated from the rest of `build_v10_decision_record`.** A macro serialization bug must never crash the decision record writer.

Everything else is handled by the existing design (None = no influence, bounded modifiers, floor/cap constraints).
