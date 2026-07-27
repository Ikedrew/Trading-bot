# PHASE 4C: HORIZON SHADOW ARCHITECTURE AUDIT

**Date:** 2026-07-24
**Finding:** Duplication exists. EXECUTE opportunities create horizon shadows TWICE (two different code paths).

---

## Audit 1: All Horizon Shadow Creation Points

### Location 1: `core/runtime/live_scanner.py` (line ~520-590)

| Property | Value |
|----------|-------|
| File | `core/runtime/live_scanner.py` |
| Context | Inside per-symbol loop, after assessment persistence |
| Trigger condition | `_eligible_for_shadow` is non-empty AND `_new_result.get("pattern")` exists |
| Runs for | **ALL opportunities** (EXECUTE and NO_TRADE) |
| Horizons created | ALL eligible (SCALP + INTRADAY + EXTENDED) |
| trade_id prefix | `hshadow_{cycle}_{SYMBOL}_{HORIZON}` |
| Phase added | Phase 4C.3 (research decoupling) |

### Location 2: `core/runtime/engine_execution_handler.py` (line ~206-290)

| Property | Value |
|----------|-------|
| File | `core/runtime/engine_execution_handler.py` |
| Function | `prepare_execution()` |
| Trigger condition | Engine returned `action="EXECUTE"`, inside prepare_execution() |
| Runs for | **EXECUTE only** |
| Horizons created | Non-SCALP only (`_higher_horizons = [h for h in ... if h != "SCALP"]`) |
| trade_id prefix | `shadow_{cycle}_{SYMBOL}_{HORIZON}` |
| Phase added | Phase 4C.2 (original integration) |

---

## Audit 2: Duplication Analysis

### YES — Duplication Exists For EXECUTE Decisions

When `action == "EXECUTE"`:

```
Step 1: live_scanner (line ~520)
  → classify_horizons() → eligible = [SCALP, INTRADAY, EXTENDED]
  → build_all_horizon_trades(eligible_horizons=[SCALP, INTRADAY, EXTENDED])
  → open_trade(trade_id="hshadow_100_GBPUSD_SCALP")
  → open_trade(trade_id="hshadow_100_GBPUSD_INTRADAY")
  → open_trade(trade_id="hshadow_100_GBPUSD_EXTENDED")

Step 2: (later in same cycle) prepare_execution() runs:
  → classify_horizons() (AGAIN — second classification)
  → _higher_horizons = [INTRADAY, EXTENDED] (excludes SCALP)
  → build_all_horizon_trades(eligible_horizons=[INTRADAY, EXTENDED])
  → open_trade(trade_id="shadow_100_GBPUSD_INTRADAY")    ← DUPLICATE
  → open_trade(trade_id="shadow_100_GBPUSD_EXTENDED")    ← DUPLICATE
```

**Result for one EXECUTE opportunity:**
- `hshadow_100_GBPUSD_SCALP` — from live_scanner (research path)
- `hshadow_100_GBPUSD_INTRADAY` — from live_scanner (research path)
- `hshadow_100_GBPUSD_EXTENDED` — from live_scanner (research path)
- `shadow_100_GBPUSD_INTRADAY` — from engine_execution_handler (DUPLICATE)
- `shadow_100_GBPUSD_EXTENDED` — from engine_execution_handler (DUPLICATE)

**5 shadow trades for one opportunity.** INTRADAY and EXTENDED are tracked twice (with different trade_ids but same SL/TP).

### NO_TRADE Path: No Duplication

For `action == "NO_TRADE"`:
- Only live_scanner creates shadows (Step 1 above)
- `prepare_execution()` never runs
- No duplication

---

## Audit 3: Intended Architecture

**Correct answer: B) All assessed opportunities including NO_TRADE**

**Reasoning:**
- The purpose of horizon shadows is to answer: "What would this opportunity have done under different horizons?"
- This question is equally valid for rejected opportunities (NO_TRADE) as for executed ones
- If only EXECUTE decisions get shadows, we cannot answer: "Are we rejecting profitable INTRADAY opportunities?"
- Survivorship bias makes research conclusions unreliable

**Therefore:** The live_scanner path (Phase 4C.3) is the CORRECT single source of truth.
The engine_execution_handler path (Phase 4C.2) is now REDUNDANT.

---

## Audit 4: Survivorship Bias Check

| Path | SCALP | INTRADAY | EXTENDED | Bias? |
|------|-------|----------|----------|-------|
| live_scanner (all opps) | ✅ | ✅ | ✅ | ❌ No bias |
| engine_execution_handler (EXECUTE only) | ❌ (excluded) | ✅ | ✅ | ✅ BIASED (only winners) |

The engine_execution_handler path creates shadows ONLY for trades that passed all gates — introducing survivorship bias if used for research.

---

## Audit 5: Persistence Duplication

### Duplicate Records WILL Be Written

For EXECUTE opportunities, the ShadowTradeEngine will track:
- `hshadow_100_GBPUSD_INTRADAY` (from live_scanner)
- `shadow_100_GBPUSD_INTRADAY` (from engine_execution_handler)

Both will evaluate_bar() independently and both will persist to `logs/shadow_trades/GBPUSD/{date}.jsonl` when closed.

**Evidence:** These have different `trade_id` values so they won't overwrite each other. But they track the SAME opportunity with the SAME SL/TP — producing duplicate outcome records.

---

## Audit 6: Final Recommendation

| # | Question | Answer | Justification |
|---|----------|--------|---------------|
| 1 | Current architecture | **FAIL (duplication)** | EXECUTE opportunities create INTRADAY/EXTENDED shadows twice |
| 2 | Is horizon shadow creation duplicated? | **YES** | Two code paths produce overlapping shadows for EXECUTE decisions |
| 3 | Should engine_execution_handler contain horizon shadow creation? | **NO** | The live_scanner path (Phase 4C.3) already covers ALL opportunities including EXECUTE |
| 4 | Should live_scanner be the single source of truth? | **YES** | It runs for ALL opportunities (no bias), creates ALL horizons, and uses the `hshadow_` prefix |
| 5 | Minimal fix required | **Remove Phase 4C.2 horizon code from engine_execution_handler.py** | The Phase 4C.3 decoupled path supersedes it |

### Specific Fix (for future implementation — NOT done in this audit):

Remove lines ~200-290 in `core/runtime/engine_execution_handler.py` (the "Step 5: HORIZON SHADOW TRADES" block). This was the Phase 4C.2 integration that is now superseded by the Phase 4C.3 research-decoupled path in live_scanner.py.

**Impact of removal:**
- Zero execution behaviour change (horizon shadows are research-only)
- Eliminates duplicate shadow records for EXECUTE decisions
- Single source of truth: live_scanner creates ALL horizon shadows
- Simpler architecture: one creation point, one logging pattern
