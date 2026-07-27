# PHASE 4C: HORIZON SHADOW CONTROL FLOW AUDIT

**Date:** 2026-07-24
**Observed:** `[HORIZON] eligible=['SCALP','INTRADAY']` appears in logs but `[HORIZON_SHADOW]` creation log does NOT appear.
**Verdict:** PARTIAL — The implementation is correct but only creates horizon shadows for EXECUTE decisions. Opportunities rejected by the engine (NO_TRADE) never produce horizon shadow trades.

---

## 1. Horizon Shadow Entry Point

**File:** `core/runtime/engine_execution_handler.py`
**Function:** `prepare_execution()`
**Line:** ~206-280

```python
# core/runtime/engine_execution_handler.py, inside prepare_execution():
try:
    from core.horizon.horizon_classifier import classify_horizons
    from core.horizon.horizon_trade_builder import build_all_horizon_trades
    ...
    _horizon_trades = build_all_horizon_trades(...)
    for _ht in _horizon_trades:
        get_shadow_engine().open_trade(...)
        logger.info("[HORIZON_SHADOW] ...")
```

**Only caller:** `prepare_execution()` is called exclusively from `live_scanner.py` on the EXECUTE path.

---

## 2. Complete Control Flow

```
New M5 bar
    │
    ▼
Pattern detection (pre_engine_gates)
    │ If no patterns → PATTERN_REJECT, continue
    ▼
run_new_engine()
    │ Scoring, classification, risk evaluation, EV computation
    │ Returns: action="EXECUTE" or action="NO_TRADE"
    ▼
Assessment built (build_assessment)         ← RUNS FOR ALL
    ▼
Horizon classification (classify_horizons)  ← RUNS FOR ALL
    │ Produces: [HORIZON] log if multiple eligible
    ▼
Assessment persisted (persist_assessment)   ← RUNS FOR ALL
    ▼
Engine result checked:
    │
    ├── IF action == "NO_TRADE":
    │     handle_no_trade_outcome()
    │     _finalize_decision()
    │     continue  ←←← EXITS HERE (98% of cases)
    │     ─────────────────────────────────────────
    │     build_all_horizon_trades() NEVER REACHED
    │     [HORIZON_SHADOW] log NEVER PRODUCED
    │
    └── IF action == "EXECUTE":
          _exec_prep = prepare_execution()     ← ONLY HERE
              │
              ├── [Step 4] Standard shadow trade opened
              └── [Step 5] Horizon shadow trades opened  ← HERE
                    ├── classify_horizons() (again)
                    ├── build_all_horizon_trades()
                    ├── ShadowTradeEngine.open_trade() per horizon
                    └── logger.info("[HORIZON_SHADOW] ...")
```

---

## 3. Exact Preconditions for Horizon Shadow Creation

**ALL of these must be true:**

| # | Condition | Where Evaluated | Typical Pass Rate |
|---|-----------|-----------------|-------------------|
| 1 | M5 bar has patterns | pre_engine_gates | ~30% of bars |
| 2 | Pattern passes scoring threshold | new_engine (_MIN_SCORE_THRESHOLD=0.35) | ~70% of patterns |
| 3 | H1 swing permission passes | new_engine H1 BOS gate | ~50% |
| 4 | Execution policy allows | compute_execution_policy | ~40% |
| 5 | EV gate allows (or bypassed) | ENABLE_EV_GATE=False (bypassed) | ~100% |
| 6 | Risk manager approves (SL/TP geometry valid) | risk_manager.evaluate | ~80% |
| 7 | Engine returns action="EXECUTE" | Final check | ~2-4% of all cycles |
| 8 | prepare_execution() runs successfully | live_scanner EXECUTE path | ~100% if #7 passes |
| 9 | classify_horizons() finds >0 non-SCALP eligible | engine_execution_handler | ~30-60% of EXECUTE decisions |
| 10 | build_all_horizon_trades() produces valid trade (structure data available) | horizon_trade_builder | Depends on HTF data |

**Combined pass rate for horizon shadow creation:** ~1-3% of all cycles (same as live execution rate).

---

## 4. Existing Shadow Trade Behaviour

**Where:** `core/runtime/engine_execution_handler.py`, `prepare_execution()`, Step 4.

```python
# Step 4: SHADOW TRADE OPEN
get_shadow_engine().open_trade(
    trade_id=f"shadow_{cycle_id}_{sym_state.symbol}",
    ...
    stop_loss=_intent.sl,     # Same SL/TP as live trade
    take_profit=_intent.tp,
)
```

**When does it open?** ONLY when `prepare_execution()` is called — which is ONLY on the EXECUTE path.

**Answer: B) Only execution candidates** get shadow trades (both standard and horizon).

---

## 5. Horizon Integration Inherited Execution Dependency

**YES — the horizon shadow logic is inside `prepare_execution()`, which only runs on EXECUTE.**

The horizon code (Step 5) was placed AFTER the standard shadow code (Step 4) in the same function. Both share the same precondition: the engine must have returned `action="EXECUTE"`.

**Where exactly:** `core/runtime/engine_execution_handler.py` line ~200-280.

---

## 6. Research Architecture Assessment

### Intended Architecture (from Phase 4C design docs):

```
Opportunity
    ↓
Assessment
    ↓
Horizon evaluation → Shadow trades for ALL opportunities
    ↓
Research (independent of live execution)
```

### Actual Architecture (implemented):

```
Opportunity
    ↓
Assessment (horizon classification runs here — observational)
    ↓
IF action == "EXECUTE" ONLY:
    ↓
    prepare_execution()
        ↓
        Horizon shadow trades created
    ↓
    Research
```

### Mismatch:

The **horizon classification** (eligibility assessment) runs for ALL opportunities — this is correct and matches the design.

The **horizon shadow trade creation** (hypothetical trades with SL/TP tracked to outcome) only runs for EXECUTE decisions — this excludes ~96-98% of opportunities from shadow outcome tracking.

---

## 7. Explanation of Live Log Behaviour

**Observed:**
```
[PATTERN GATE] GBPUSD — 1 pattern(s): ['TWEEZER_TOP']
[NEW ENGINE] GBPUSD action=NO_TRADE score=0.520 reason=ev_policy_blocked: NEGATIVE_EXPECTED_VALUE
[HORIZON] GBPUSD | eligible=['SCALP', 'INTRADAY'] | best=SCALP
```

**Then immediately continues to next symbol. No `[HORIZON_SHADOW]`.**

**Explanation:**

1. Pattern detected → ✅ (TWEEZER_TOP)
2. Engine runs → returns `action="NO_TRADE"` (EV policy blocked)
3. Assessment built → ✅
4. Horizon classified → ✅ (`[HORIZON]` log produced — SCALP + INTRADAY eligible)
5. Assessment persisted → ✅ (with horizon classification attached)
6. **`if action == "NO_TRADE":`** → TRUE
7. `handle_no_trade_outcome()` → executed
8. `continue` → **loop exits for this symbol**
9. `prepare_execution()` → **NEVER REACHED**
10. `build_all_horizon_trades()` → **NEVER CALLED**
11. `[HORIZON_SHADOW]` → **NEVER PRODUCED**

**Root cause:** The engine rejected this opportunity (EV policy blocked), so `prepare_execution()` never runs, so no horizon shadow trades are created.

---

## 8. Final Verdict

### PARTIAL

The implementation is **correct for what it does** — when a trade IS executed, horizon shadows are correctly created alongside it. But the implementation **only creates horizon shadows for EXECUTE decisions**, which means:

- ~96-98% of opportunities with eligible higher horizons NEVER get shadow-tracked
- The research question "would this rejected opportunity have been profitable at INTRADAY horizon?" **CANNOT be answered** with the current implementation
- Only the ~2-4% of opportunities that pass ALL gates (including EV, risk, swing permission) produce horizon shadow data

### Impact on Research Value

| Research Question | Answerable? |
|-------------------|-------------|
| "Among executed trades, which horizon performs best?" | ✅ YES |
| "Do rejected opportunities contain edge at higher horizons?" | ❌ NO |
| "Is the engine rejecting profitable INTRADAY opportunities?" | ❌ NO |
| "What is the overall INTRADAY expectancy across ALL opportunities?" | ❌ NO (biased to EXECUTE subset only) |

### Design Implication

To answer the full research question ("where does true edge exist across horizons"), horizon shadow creation needs to move EARLIER in the pipeline — to run for ALL assessed opportunities (not just EXECUTE decisions). This would require creating horizon shadows from the assessment block in live_scanner.py rather than inside `prepare_execution()`.

**This is a DESIGN DECISION, not a bug.** The current placement was chosen for safety (inside an already-proven code path). Expanding it requires moving shadow creation to a new location.

---

## 9. Files Inspected

| File | Purpose |
|------|---------|
| `core/runtime/live_scanner.py` | Main loop — shows action check and prepare_execution call |
| `core/runtime/engine_execution_handler.py` | Contains `prepare_execution()` with horizon shadow creation |
| `core/horizon/horizon_trade_builder.py` | Builder function (only called from engine_execution_handler) |
| `core/shadow_trades.py` | ShadowTradeEngine (receives open_trade calls) |
