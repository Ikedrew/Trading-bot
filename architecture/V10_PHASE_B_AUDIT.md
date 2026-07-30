# Phase B — Full V10 Authority Audit

---

## Final Verdict

| Question | Answer |
|---|---|
| 1. Is V10 the only decision maker? | **YES** |
| 2. Can anything override V10? | **NO** |
| 3. Can anything block valid V10 trades? | **YES** — two issues found |
| 4. Which blockers are safety only? | Spread, margin, volume, broker, stops_level |
| 5. Which blockers must be removed/adapted? | See below |
| 6. What remains before live deployment? | Execution bridge + horizon authority adaptation |

---

## 1. Horizon Lock — POTENTIAL BLOCKER FOUND

### V10 HorizonEngine: UNRESTRICTED ✓

V10 `assess_horizon()` freely produces SCALP, INTRADAY, and EXTENDED. No code within the V10 pipeline restricts this.

### Legacy Horizon Authority: WILL BLOCK (once bridge is fixed)

**File:** `core/runtime/live_scanner.py` line 1010-1016
```python
_horizon_perm = _horizon_authority.can_open(
    symbol=sym_state.symbol,
    horizon=decision.intent.metadata.get("horizon", "SCALP"),
    current_positions=_all_open_positions,
)
if not _horizon_perm.allowed:
    # BLOCKS EXECUTION
```

**File:** `core/horizon/execution_authority.py` line 116
```python
self._permitted = list(getattr(config, "PERMITTED_HORIZONS", ["SCALP"]))
```

**File:** `core/config.py` line 308
```python
PERMITTED_HORIZONS = ["SCALP"]
```

**Impact:** Once the execution bridge is fixed, any V10 trade with horizon=INTRADAY or EXTENDED will be BLOCKED by this legacy authority — even though V10 approved it.

**Classification:** DECISION BLOCKER (not safety) — must be adapted.

**Fix options:**
- A) Add INTRADAY/EXTENDED to `PERMITTED_HORIZONS` config
- B) Bypass `_horizon_authority` when `ENGINE_MODE == "V10"` (V10 has its own risk checks)
- C) Remove legacy horizon authority entirely

---

## 2. Old Score Gate — NO ACTIVE IMPACT ✓

| Term | In V10 active path? | Effect |
|---|---|---|
| `composite_score` | NOT FOUND | None |
| `strategy_score` | NOT FOUND in live_scanner | None |
| `neutral_score` | NOT FOUND in V10 path | None |
| `MIN_SCORE_TO_TRADE` | config.py only — used by `run_new_engine` (guarded) | None |
| `score_threshold` | line 996 — **BUT** only logs/tracks, does NOT block | None |
| `confidence_threshold` | `new_engine.py` only — never executes under V10 | None |
| `pattern_gate` | `decision_ledger.py` mapping only | None |
| `grade` | V10 report text + output_router (legacy) | None |

**The score tracker at line 996 is OBSERVATIONAL ONLY** — it appends to `_score_tracker["passed_scores"]` for reporting but never blocks execution.

**No score gate can prevent V10 EXECUTE decisions.**

---

## 3. Old Strategy Threshold — NO ACTIVE IMPACT ✓

All strategy scoring (`_CLASSIFIER_CONFIDENCE_THRESHOLD`, `_MIN_NEUTRAL_SCORE`, `scoring_engine.py`) lives inside `run_new_engine()` which does NOT execute when `ENGINE_MODE == "V10"`.

V10's `StrategyDecision` is produced by `select_strategy()` and nothing downstream modifies it.

---

## 4. Legacy Override — GUARDED CORRECTLY ✓

| Item | Status |
|---|---|
| `run_new_engine` import (line 465) | Guarded: `if _engine_mode == "V10": pass` |
| `run_new_engine` call (line 470) | Inside `else:` block — does NOT execute |
| Fallback to legacy (line 402) | Only on V10 EXCEPTION — then runs legacy |
| Legacy output overwriting V10 | NOT POSSIBLE (V10 sets `_new_result` first) |

---

## 5. Hidden Execution Blockers

### Category A — VALID SAFETY BLOCKERS (should remain):

| Blocker | File:Line | Effect |
|---|---|---|
| Spread guard | risk/runtime_guard_chain.py | Blocks if spread too wide |
| Cooldown guard | risk/runtime_guard_chain.py | Blocks if too soon after last trade |
| Correlation guard | risk/runtime_guard_chain.py | Blocks if correlated exposure too high |
| Position limit | risk/runtime_guard_chain.py | Blocks if max positions reached |
| Drawdown guard | core/runtime/cycle_guards.py | Blocks entire cycle if DD exceeded |
| Daily loss guard | core/runtime/cycle_guards.py | Blocks new entries if daily loss exceeded |

These are CAPITAL PROTECTION — they cannot modify trade direction/strategy/horizon. They can only BLOCK.

### Category B — DECISION BLOCKERS (should NOT exist after V10):

| Blocker | File:Line | Effect |
|---|---|---|
| **Horizon authority** | live_scanner.py:1010-1016 | Blocks INTRADAY/EXTENDED trades |

This is the ONLY decision blocker found that could prevent a valid V10 trade.

### Category C — BROKEN PATH (prevents all EXECUTE):

| Issue | File:Line | Effect |
|---|---|---|
| Missing `intent` key | live_scanner.py:765 via engine_execution_handler.py | ALL V10 EXECUTE decisions crash |

---

## 6. Authority Map

### CURRENT (broken):

```
Market Data
     ↓
V10 Pipeline (full 7 stages)
     ↓
scanner_adapter returns {"action": "EXECUTE", ...}
     ↓
live_scanner: _new_result["intent"]  ← KeyError CRASH
     ↓
Exception handler → logs "engine_exception" → NO_TRADE
```

### TARGET (after fixes):

```
Market Data
     ↓
V10 Pipeline (full 7 stages)
     ↓
scanner_adapter builds OrderIntent from V10 ExecutionDecision
     ↓
live_scanner: prepare_execution → OrderIntent
     ↓
Runtime guard chain (safety only)
     ↓
MT5 Execution
     ↓
Outcome → Decision record linkage
```

---

## Complete Blocker Inventory

### Must fix before V10 can execute:

| # | Issue | Impact | Fix |
|---|---|---|---|
| 1 | **No `intent` key in V10 result** | ALL EXECUTE decisions crash | Scanner adapter must build OrderIntent |
| 2 | **`PERMITTED_HORIZONS=["SCALP"]` blocks INTRADAY/EXTENDED** | V10 horizon decisions overridden | Update config or bypass under V10 |

### Safety blockers (KEEP — correct architecture):

| # | Guard | Purpose |
|---|---|---|
| 3 | Spread guard | Prevents execution when spread is abnormal |
| 4 | Cooldown guard | Prevents rapid re-entry |
| 5 | Correlation guard | Prevents correlated exposure |
| 6 | Position limit | Prevents over-trading |
| 7 | Drawdown guard | Prevents trading during DD |
| 8 | Daily loss guard | Prevents trading after daily loss limit |

### Observational code (NO IMPACT — runs but doesn't block):

| Code | Purpose | Blocks V10? |
|---|---|---|
| Score tracker (line 996) | Research logging | NO |
| Shadow opportunity layer | Observation persistence | NO |
| Assessment builder | Research record | NO |
| Bias FSM | Legacy state tracking | NO |
| Horizon classifier (legacy) | Shadow observation | NO |

---

## Conclusion

**V10 is the undisputed decision authority.** No scoring, strategy threshold, pattern gate, or legacy engine can influence V10 decisions.

**Two specific fixes are required before V10 can trade live:**

1. **Execution bridge:** scanner_adapter must produce an `OrderIntent` object from V10's `ExecutionDecision`
2. **Horizon permission:** either expand `PERMITTED_HORIZONS` to include INTRADAY/EXTENDED, or bypass the legacy authority check under V10 mode

After these two fixes, V10 will have a clear, unobstructed path from decision to execution.
