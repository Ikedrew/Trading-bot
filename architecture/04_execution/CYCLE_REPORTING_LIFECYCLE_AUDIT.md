# CYCLE REPORTING LIFECYCLE AUDIT

**Date:** 2026-07-24
**Status:** Forensic audit complete. Implementation plan ready.

---

## Phase 1: Current Flag Audit

### `_cycle_had_trade` — Complete Reference Map

| Location | File | Line | Usage | Actual Lifecycle Stage |
|----------|------|------|-------|----------------------|
| Initialization | `live_scanner.py` | ~199 | `_cycle_had_trade = False` | Cycle start (reset) |
| Set to True | `live_scanner.py` | ~921 | `_cycle_had_trade = True` | **EXECUTE decision generated** (before guards) |
| Read: cycle report | `live_scanner.py` | ~1288 | `cycle_had_trade=_cycle_had_trade` | Passed to emit_cycle_report |
| Read: health monitor | `live_scanner.py` | (post-report) | `cycle_had_trade=_cycle_had_trade` | Passed to health_monitor.tick() |
| Consumed: report | `cycle_report.py` | ~114-126 | Controls "✅ TRADE PASSED" vs "❌ NO TRADE" | Display logic |
| Consumed: health | `health_monitor.py` | ~128 | Resets `consecutive_no_trade_cycles` | Alert suppression |

### Semantic Problem

| What the flag claims | What actually happened |
|---------------------|----------------------|
| "TRADE PASSED FULL PIPELINE" | An EXECUTE decision was generated |
| "At least one trade executed" | Only means scoring + policy gates passed |
| Implies broker fill | Does NOT guarantee execution attempt, guard pass, or fill |

### Risk Assessment

The flag is set at **line 921** — BEFORE:
- Guard chain evaluation (line ~930) — may BLOCK the trade
- Execution orchestrator (line ~1000) — may fail or be rejected
- Broker response (line ~1010) — may reject the order
- Fill confirmation (line ~1015) — `result.ok` may be False

**`_cycle_had_trade = True` means only: "the engine said EXECUTE"** — NOT "a trade was filled."

---

## Phase 2: Execution Lifecycle Map

```
Pattern detected (pre_engine_gates)
    │
    ▼
Decision Engine (run_new_engine)
    │
    ├── NO_TRADE → _cycle_drops.append() → continue
    │
    └── EXECUTE → prepare_execution()
                    │
                    ▼
              ┌─────────────────────────────────────────┐
              │ _cycle_had_trade = True   ← SET HERE    │ ← DECISION STAGE
              │ (line 921)                              │
              └─────────────────────────────────────────┘
                    │
                    ▼
              Guard Chain (evaluate_runtime_guards)
                    │
                    ├── BLOCKED → RISK_BLOCK decision → continue  ← EXECUTION DROP
                    │
                    └── PASSED
                          │
                          ▼
                    Execution Orchestrator
                    (_exec_orchestrator.execute_trade)
                          │
                          ├── executed=False → continue           ← EXECUTION FAILURE
                          │
                          └── executed=True
                                │
                                ├── result.ok=False                ← BROKER REJECTION
                                │     (decision = NO_TRADE:broker_rejected)
                                │
                                └── result.ok=True                 ← CONFIRMED FILL
                                      │
                                      ▼
                                Position registered
                                Protection verified
                                Trade management active
```

### Existing Variables/Events Per Stage

| Stage | Variable/Event | Exists? |
|-------|---------------|---------|
| EXECUTE decision generated | `_cycle_had_trade = True` | ✅ |
| Guard chain blocked | `DecisionOutcome.RISK_BLOCK` logged | ✅ (in decision_ledger) |
| Execution attempted | `_exec_outcome.executed` | ✅ (local variable, not tracked at cycle level) |
| Broker rejected | `result.ok = False` | ✅ (local variable, not tracked at cycle level) |
| Confirmed fill | `result.ok = True` | ✅ (local variable, not tracked at cycle level) |
| **Cycle-level execution attempt flag** | DOES NOT EXIST | ❌ |
| **Cycle-level fill flag** | DOES NOT EXIST | ❌ |

---

## Phase 3: Proposed Refactor

### Replace Single Boolean With Three-Stage Tracking

```python
# At cycle start (line ~199):
_cycle_had_execute_decision = False   # Engine said EXECUTE
_cycle_had_execution_attempt = False  # Passed guards, entered broker layer
_cycle_had_fill = False               # Broker confirmed position opened
_cycle_execute_symbols = []           # Symbols with EXECUTE decisions
_cycle_blocked_symbols = []           # Symbols blocked by guard chain
_cycle_filled_symbols = []            # Symbols with confirmed fills
_cycle_rejected_symbols = []          # Symbols with broker rejection
```

### Where Each Is Set

| Flag | Set At | Condition |
|------|--------|-----------|
| `_cycle_had_execute_decision = True` | Line ~921 (current `_cycle_had_trade`) | Engine returns `action="EXECUTE"` |
| `_cycle_execute_symbols.append(sym)` | Same location | Same condition |
| `_cycle_had_execution_attempt = True` | After guard chain passes, before execute_trade | Guard chain `allowed=True` |
| `_cycle_had_fill = True` | After `result.ok = True` (line ~1015) | Broker confirmed fill |
| `_cycle_filled_symbols.append(sym)` | Same | Same |
| `_cycle_blocked_symbols.append(sym)` | Inside guard chain BLOCKED path | Guard blocks trade |
| `_cycle_rejected_symbols.append(sym)` | Inside `result.ok = False` path | Broker rejects |

---

## Phase 4: New Cycle Report Format

### Current (Misleading)

```
==================================================
CYCLE 1 PIPELINE TRACE
==================================================
  ✅ TRADE PASSED FULL PIPELINE
  → EURUSD   | DROP at new_engine          | ev_policy_blocked
  → GBPUSD   | DROP at new_engine          | score_below_threshold
==================================================
```

### Proposed (Accurate)

```
==================================================
CYCLE 1 PIPELINE TRACE
==================================================
Decision Layer:
  ✅ EXECUTE decisions: 1 (USDCHF)
  → EURUSD   | DROP at new_engine    | ev_policy_blocked
  → GBPUSD   | DROP at new_engine    | score_below_threshold

Execution Layer:
  ❌ Guard blocked: 0
  ✅ Execution attempts: 1 (USDCHF)

Broker Layer:
  ✅ Confirmed fills: 1 (USDCHF)
  ❌ Rejected: 0
==================================================
```

Or for the failing case:

```
==================================================
CYCLE 5 PIPELINE TRACE
==================================================
Decision Layer:
  ✅ EXECUTE decisions: 2 (USDCHF, USDCAD)

Execution Layer:
  ❌ Guard blocked: 1 (USDCAD — correlation_guard:GROUP_LIMIT)
  ✅ Execution attempts: 1 (USDCHF)

Broker Layer:
  ❌ Confirmed fills: 0
  USDCHF — broker rejected (retcode=10016)
==================================================
```

---

## Phase 5: Drop Separation

### Current `_cycle_drops`

Only records NO_TRADE decisions from the engine — misses guard blocks and broker rejections.

### Proposed Three-Level Drops

```python
_cycle_decision_drops = []      # (symbol, stage, reason) — engine NO_TRADE
_cycle_execution_drops = []     # (symbol, guard, reason) — guard chain blocks
_cycle_broker_drops = []        # (symbol, retcode, reason) — broker rejections
```

---

## Phase 6: Validation Against Cycle 1 Evidence

### User's Observed Logs

```
[NEW ENGINE] symbol=USDCHF action=EXECUTE
[STATE] symbol=USDCHF | ENTRY
[EXECUTION_DEBUG] symbol=USDCHF side=BUY
[PAPER] Recorded executed_trade signal
...
❌ NO TRADE | dominant drop: new_engine (2/2 symbols)
```

### Correct Interpretation

| Stage | EURUSD | GBPUSD | USDCHF |
|-------|--------|--------|--------|
| Engine decision | NO_TRADE | NO_TRADE | EXECUTE |
| Guard chain | N/A | N/A | PASSED |
| Execution attempt | N/A | N/A | ✅ |
| Broker fill | N/A | N/A | ✅ (logs show ENTRY + EXECUTION_DEBUG) |

### Why Report Says "NO TRADE"

The most likely explanation (to be confirmed by diagnostic logs):

**Hypothesis A:** Only 2 symbols had new bars in cycle 1 (EURUSD + GBPUSD). USDCHF executed in a different cycle. The "2/2" refers to the 2 symbols evaluated, not all 7.

**Hypothesis B:** `_cycle_had_trade` was not set to True because an exception occurred in the ~300 lines between `prepare_execution()` and line 921 where the flag is set.

**Hypothesis C:** The flag IS set (True) but the "2/2 symbols" count from `_cycle_drops` misleads — the report shows drops for 2 symbols (correct) but the ❌ should not appear because `cycle_had_trade=True` prevents it.

The diagnostic logging (already deployed) will resolve which hypothesis is correct.

---

## Phase 7: Files Requiring Modification

| File | Change |
|------|--------|
| `core/runtime/live_scanner.py` | Replace `_cycle_had_trade` with 3-stage tracking. Set flags at correct lifecycle points. |
| `core/pipeline/cycle_report.py` | Update `emit_cycle_report` to accept 3 flags + symbol lists. Update report format. |
| `core/runtime/health_monitor.py` | Update `tick()` signature to accept `cycle_had_fill` instead of `cycle_had_trade` |

### Migration Plan

1. Add new variables alongside `_cycle_had_trade` (don't remove yet)
2. Set new flags at correct lifecycle points
3. Update `emit_cycle_report` to accept new parameters (keep old parameter for compatibility)
4. Update report format
5. Verify with tests
6. Remove `_cycle_had_trade` once replaced

---

## Phase 8: Test Plan

| Case | Decision | Guards | Broker | Expected Flags |
|------|----------|--------|--------|---------------|
| 1 | EXECUTE generated, guard blocks | EXECUTE | BLOCKED | decision=T, attempt=F, fill=F |
| 2 | EXECUTE generated, broker rejects | EXECUTE | PASSED | decision=T, attempt=T, fill=F |
| 3 | Successful fill | EXECUTE | PASSED | decision=T, attempt=T, fill=T |
| 4 | All NO_TRADE | NO_TRADE | N/A | decision=F, attempt=F, fill=F |
| 5 | Multiple EXECUTE, one blocked one fills | 2×EXECUTE | 1 BLOCK, 1 FILL | decision=T, attempt=T, fill=T, blocked=[sym] |

---

## Summary

| Finding | Status |
|---------|--------|
| `_cycle_had_trade` represents EXECUTE decision only | ✅ Confirmed |
| Execution attempt not tracked at cycle level | ❌ MISSING |
| Broker fill not tracked at cycle level | ❌ MISSING |
| Report conflates decision with outcome | ✅ Confirmed |
| Three-stage separation needed | ✅ Designed |
| Migration is low-risk (additive) | ✅ |
