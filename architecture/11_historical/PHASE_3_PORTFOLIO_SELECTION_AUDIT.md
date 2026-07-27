# PHASE 2: PORTFOLIO SELECTION AUDIT

**Date:** 2026-07-23
**Objective:** Determine what happens when multiple symbols qualify for trading simultaneously. Does the bot have a true portfolio selection layer?
**Scope:** Architecture audit only. No code changes.

---

## 1. Current Behaviour

### Summary

**There is no portfolio selection layer.** Each symbol is evaluated independently in a fixed sequential loop. The first symbol to pass all guards and reach execution wins. Subsequent symbols may also execute in the same cycle because `MAX_OPEN_POSITIONS` is enforced reactively (check current count at guard time) rather than proactively (reserve a slot before execution).

### Key Finding

**MAX_OPEN_POSITIONS = 1 does NOT prevent multiple entries.** Evidence shows 5 trades filled in a single cycle (cycle 1, July 22). The position limit check reads broker state at guard evaluation time — if multiple symbols pass guards before any fill is registered, all of them can execute.

### Architectural Classification

| Property | Current State |
|----------|--------------|
| Selection model | First-come-first-served (loop order) |
| Ranking active? | Exists (passive logging only) |
| Position limit enforcement | Reactive (check count → execute if < limit) |
| Symbol priority | Fixed array order (EURUSD always first) |
| Best-opportunity selection | NO |
| Competing opportunity rejection with reason | NO |

---

## 2. Multi-Symbol Decision Flow

### Current Execution Path

```
MAIN LOOP (every POLL_SECONDS = 1.0s):
  │
  ├── cycle_guards.evaluate() → check drawdown, daily loss, kill switch
  │
  └── FOR EACH sym_state in states (FIXED ORDER):
        │
        ├── tick_monitor → validate tick freshness
        ├── drive_tick() → update trade management (existing positions)
        ├── bar_provider.fetch_bar() → check for new M5 bar
        │
        ├── pre_engine_gates → session guard, pattern detection
        │
        ├── run_new_engine() → scoring, strategy, risk → produces EXECUTE or NO_TRADE
        │     └── Appends result to _cycle_candidates[] (passive collection)
        │
        ├── IF EXECUTE:
        │     ├── rebuild _all_open_positions (from ALL trade managers)
        │     ├── evaluate_runtime_guards() → daily limit, cooldown, correlation, exposure
        │     │     └── MAX_OPEN_POSITIONS checked HERE via count_bot_positions()
        │     │
        │     ├── IF guards pass:
        │     │     └── execution_orchestrator.execute_trade() → broker order_send
        │     │         └── register_from_execution() → position now tracked
        │     │
        │     └── IF guards fail:
        │           └── Record RISK_BLOCK, continue to next symbol
        │
        └── NEXT SYMBOL (same cycle)

  POST-LOOP (after all symbols processed):
    └── rank_candidates(_cycle_candidates) → passive ranking + print narrative
```

### Symbol Processing Order

```python
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
```

This order is **fixed** and determines priority. EURUSD is always evaluated and potentially executed first. NZDUSD is always last.

### Consequence

If EURUSD has a mediocre opportunity (score 4.6) and NZDUSD has an excellent opportunity (score 7.0), EURUSD will execute first. NZDUSD may then be blocked by MAX_OPEN_POSITIONS — **or it may also execute** because the position count hasn't updated yet.

---

## 3. Existing Ranking Logic

### The Opportunity Ranker (passive, post-execution)

**File:** `core/pipeline/opportunity_ranker.py`

The module exists and is fully implemented:

```python
def rank_candidates(candidates: list[dict]) -> OpportunityPool:
    # Ranks by: EV × market_state_multiplier
    # MarketState multipliers: STRUCTURED=1.0, TRANSITIONAL=0.65, CHOP=0.15
    # Returns: OpportunityPool with SELECTED, OUTRANKED, BLOCKED candidates
```

**Integration point (live_scanner.py line ~933):**

```python
# ─── OPPORTUNITY RANKING (passive, post-cycle) ────────────
if _cycle_candidates:
    _opp_pool = rank_candidates(_cycle_candidates)
    if _opp_pool.total_candidates > 1 or _opp_pool.eligible_count > 0:
        print(format_ranking_narrative(_opp_pool))
```

### Why It Doesn't Control Execution

1. It runs **AFTER** the per-symbol loop completes
2. By then, trades have already been executed or blocked
3. It only prints the ranking narrative (observational)
4. The `selected` field is computed but never read by any execution code
5. `OUTRANKED` status is assigned retroactively — the outranked symbol may have already executed

### What The Bot Currently Knows (per opportunity)

| Data Point | Available? | Used For Selection? |
|-----------|-----------|-------------------|
| Symbol | Yes | No (loop order only) |
| Score (strategy-weighted) | Yes | No |
| EV (expected value) | Yes | No |
| Market state | Yes | No |
| Strategy confidence | Yes | No |
| Regime quality | Yes | No |
| HTF alignment | Yes | No |
| Rank score (EV × multiplier) | Yes (post-cycle) | No |

**All data needed for intelligent selection exists. It is simply not used before execution.**

---

## 4. Position Limit Behaviour

### How MAX_OPEN_POSITIONS Is Enforced

```python
# risk/guards.py
def count_bot_positions(symbol: str, magic: int) -> int:
    rows = mt5_call(mt5.positions_get, symbol=symbol)  # Per-symbol only!
    return sum(1 for p in rows if int(p.magic) == magic)
```

**Critical observation:** `count_bot_positions` queries MT5 for **the specific symbol only**. It does NOT count total positions across all symbols. This means MAX_OPEN_POSITIONS applies **per symbol**, not globally.

Wait — let me verify. Looking at `trade_quality.py`:
```python
if count_bot_positions(symbol, config.BOT_MAGIC) >= config.MAX_OPEN_POSITIONS:
    layer_quality.max_positions_blocked = True
```

This is called with the specific symbol. With `MAX_OPEN_POSITIONS = 1`, it blocks a second trade on the **same symbol** only. It does NOT prevent opening positions on different symbols simultaneously.

### The Real Position Limit: Correlation Guard

The actual multi-symbol limit comes from `risk/runtime_guard_chain.py`:

```
Guard 3: check_correlation(symbol, direction, volume, open_positions)
Guard 4: check_portfolio_exposure(proposed_risk_pct, open_positions)
```

These DO check all open positions across symbols. But:
- `MAX_CORRELATION_GROUP_POSITIONS = 2` (per correlation group, not global)
- Correlation groups: `[EURUSD, GBPUSD, AUDUSD, NZDUSD]` and `[USDJPY, USDCHF, USDCAD]`

So the system can have up to **4 positions open** (2 per group) before being blocked.

### Position Limit Timeline Within One Cycle

```
t=0ms:  _all_open_positions rebuilt = [pos_EURUSD]  (1 position open)
t=5ms:  GBPUSD guard check → 1 position (EURUSD) → correlation group check:
         Same group [EURUSD, GBPUSD, AUDUSD, NZDUSD] → 1 < MAX(2) → PASS
t=10ms: GBPUSD executes → broker fill → register_from_execution()
         Now 2 positions exist (EURUSD + GBPUSD)
t=15ms: USDJPY reaches guard → _all_open_positions NOT rebuilt (still old state!)
         ⚠️ Actually: _all_open_positions IS rebuilt per-symbol (line 700-702)
         New check: 2 positions exist. Different correlation group → PASS
t=20ms: USDJPY executes → 3 positions

Result: 3 positions opened in one cycle despite intent to limit exposure.
```

**Correction:** `_all_open_positions` IS rebuilt before each symbol's guard chain. But `register_from_execution()` is called by the TradeStateManager which adds to `_by_id`. The next symbol's `positions_open()` call WILL see it. So the sequence DOES provide some protection — but only if the execution + registration completes before the next symbol reaches guard evaluation.

### Race Condition

The practical protection depends on timing:
- If order_send + register happens in <1ms (which it does)
- AND the next symbol reaches guard_chain after that
- THEN the limit works sequentially

**But:** All symbols in the same cycle may have had their new bar at the same time. The loop processes them sequentially, and execution + registration is synchronous. So in practice, the second symbol DOES see the first's position. The 5-fill case from cycle 1 happened because **the system was starting up** and processed a backlog of pending signals simultaneously.

---

## 5. Evidence From Logs

### Case 1: Cycle 1, July 22 — 5 Simultaneous Executions

| Timestamp | Symbol | Pattern | Result |
|-----------|--------|---------|--------|
| 17:08:03 | USDCAD | THREE_BLACK_CROWS | FILLED |
| 17:39:35 | NZDUSD | THREE_BLACK_CROWS | FILLED |
| 17:40:28 | GBPUSD | THREE_BLACK_CROWS | REJECTED |
| 17:40:38 | USDJPY | THREE_WHITE_SOLDIERS | FILLED |
| 17:40:50 | USDCHF | THREE_WHITE_SOLDIERS | REJECTED |
| 17:41:07 | AUDUSD | THREE_BLACK_CROWS | FILLED |
| 18:53:42 | GBPUSD | TWEEZER_TOP | FILLED |
| 18:53:56 | USDCAD | THREE_BLACK_CROWS | FILLED |

**5 fills in one "cycle"** (cycle_id=1 covers a wide time range because the system likely restarted and processed initial bars for all symbols). GBPUSD was rejected at 17:40 but later filled at 18:53.

### Case 2: Cycle 449, July 22 — 2 Competing High-Quality Signals

| Symbol | Score | Pattern |
|--------|-------|---------|
| GBPUSD | 7.0 | (unknown) |
| NZDUSD | 7.0 | (unknown) |

Both scored 7.0 (highest possible). No selection between them — both reached EXECUTE.

### Case 3: Cycle 4578, July 23 — Simultaneous Execution

| Symbol | Score | Timestamp |
|--------|-------|-----------|
| GBPUSD | 6.0 | 12:30:20 |
| NZDUSD | 6.0 | 12:30:50 |

30 seconds apart in the same cycle. Both executed.

### Implications

- The "portfolio" has no selection pressure
- Multiple positions open simultaneously without comparative evaluation
- The GBPUSD -4.5R loss (Trade #12) may have been partly caused by multiple simultaneous positions consuming margin

---

## 6. Problems Found

### P0: No Selection Between Competing Opportunities

When 3 symbols produce EXECUTE simultaneously, the bot opens all 3. There is no "pick the best one" logic. This leads to:
- Capital dilution across mediocre + excellent opportunities
- Increased margin usage (multiple open positions)
- Correlation risk (multiple USD-correlated positions)

### P1: MAX_OPEN_POSITIONS Does Not Work As Intended

`MAX_OPEN_POSITIONS = 1` is meant to limit to 1 position. In practice:
- `count_bot_positions(symbol)` only counts positions on THAT symbol
- Correlation guard allows up to 2 per group
- Portfolio exposure guard has its own threshold
- Net result: multiple positions can and do coexist

### P2: Symbol Array Order Determines Priority (Not Quality)

EURUSD always evaluates first. If EURUSD has a weak opportunity and NZDUSD has a strong one, EURUSD executes because it's first in the loop. The ranking module correctly identifies the best opportunity — but only AFTER execution has already happened.

### P3: No Rejection Logging For "Outranked" Opportunities

When the ranker identifies a candidate as OUTRANKED, this information is printed but:
- Not persisted to decision_ledger
- Not available for forensic analysis
- Not used to improve future decisions

### P4: Startup Batch Execution

On cold start, the system processes initial bars for all symbols simultaneously. This can produce a burst of 5+ trades before any position limit takes effect.

---

## 7. Recommended Minimal Solution

### Design: Evaluate-Then-Execute (two-pass per cycle)

Replace the current single-pass (evaluate + execute per symbol) with a two-pass model:

```
PASS 1: EVALUATE ALL SYMBOLS (collect candidates)
  for sym_state in states:
    result = run_new_engine(...)
    if result.action == "EXECUTE":
      candidates.append(result)

PASS 2: SELECT AND EXECUTE BEST (rank, then execute top-K)
  pool = rank_candidates(candidates)
  for candidate in pool.candidates:
    if candidate.selection_status == "SELECTED":
      execute(candidate)
    else:
      log_rejection(candidate, reason="outranked")
```

### Minimal Implementation

1. **Move execution OUT of the per-symbol loop** — collect all EXECUTE intents first
2. **Activate the existing ranker** — `rank_candidates()` already works correctly
3. **Execute only `pool.selected`** — the highest EV × market_state candidate
4. **Log OUTRANKED rejections** — persist to decision_ledger with reason "outranked_by:{winner_symbol}"
5. **Honour MAX_OPEN_POSITIONS as a global cap** — execute top-K where K = MAX_OPEN_POSITIONS - current_open_count

### What This Requires

| Change | Complexity | Risk |
|--------|-----------|------|
| Restructure per-symbol loop into evaluate/execute phases | MEDIUM | Low (logic separation, no new algorithms) |
| Connect existing ranker to execution gate | LOW | Zero (ranker is already correct) |
| Add "outranked" rejection to decision_ledger | LOW | Zero (new log field only) |
| Fix MAX_OPEN_POSITIONS to be truly global | LOW | Low (change `count_bot_positions` to count all symbols) |
| Add startup burst protection | LOW | Low (skip execution on cycle_id=1 or add warmup period) |

### What This Does NOT Require

- New scoring models
- EV changes
- Strategy modifications
- Risk parameter changes
- Trade management changes

### Expected Impact

- Only the highest-quality opportunity executes per cycle
- Capital concentrated on best opportunity instead of diluted
- Reduced simultaneous position count
- Reduced margin consumption
- Better forensic analysis (can compare what was selected vs what was rejected)
- The ranker goes from passive observation to active authority

### Key Files For Implementation

| File | Change |
|------|--------|
| `core/runtime/live_scanner.py` | Restructure loop: evaluate-all-then-execute-best |
| `core/pipeline/opportunity_ranker.py` | Already complete — just connect to execution |
| `risk/guards.py` | Make `count_bot_positions` check all symbols (global count) |
| `core/decision_ledger.py` | Add OUTRANKED outcome type |

---

## 8. Answers To Audit Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | What happens when multiple symbols generate EXECUTE? | All of them attempt execution independently. Multiple can fill. |
| 2 | Is there a ranking system? | Yes (`opportunity_ranker.py`) but it is passive/post-execution only. |
| 3 | Is the highest quality opportunity selected? | No. The first symbol in array order executes first. |
| 4 | Are lower-ranked opportunities rejected with a reason? | No. They execute too (or are blocked by unrelated guards). |
| 5 | Does MAX_OPEN_POSITIONS prevent or select? | It checks per-symbol count only. Does not prevent multi-symbol simultaneous entries. |
| 6 | Are correlation/exposure applied before or after selection? | After EXECUTE decision, before broker send. They gate but do not select. |
| 7 | Is there a portfolio manager component? | The ranker exists but has no authority. No true portfolio manager. |
| 8 | Does the bot know priority/ranking/confidence/EV/regime? | Yes — all data exists on each OpportunityAssessment. It is simply not used for selection. |
| 9 | Evidence of simultaneous opportunities? | Yes: Cycle 1 (6 EXECUTE decisions, 5 fills), Cycle 449 (2 at score 7), Cycle 4578 (2 at score 6). |
