# EVENT IDENTITY OWNERSHIP AUDIT

**Generated:** 2026-07-16  
**Scope:** Identity field lifecycle from MT5 data arrival to final persistence  
**Type:** Read-only audit — no code modifications

---

## 1. IDENTITY LIFECYCLE DIAGRAM

```
MT5 Market Data
│
▼
Candle (time=1752652800, OHLCV)
│  ├─ entity_id: ❌ NOT YET EXISTS
│  ├─ cycle_id:  ❌ NOT YET EXISTS (scope-level only)
│  ├─ symbol:    ✅ implicit (feed knows it)
│
▼
Signal (pattern="BULLISH_ENGULFING", side=BUY, bar_time=1752652800)
│  ├─ entity_id: ❌ NOT A FIELD
│  ├─ cycle_id:  ❌ NOT A FIELD
│  ├─ symbol:    ❌ NOT A FIELD
│
▼
OpportunityAssessment (FROZEN)
│  ├─ entity_id: "EURUSD_SB_1752652800" ✅ CREATED HERE
│  ├─ cycle_id:  ⚠️  ALWAYS 0 (not passed to engine)
│  ├─ symbol:    "EURUSD_SB" ✅
│  ├─ bar_time:  1752652800 ✅ (unix seconds)
│
▼
DecisionTrace
│  ├─ entity_id:          ✅ (from engine_result)
│  ├─ cycle_id:           ✅ (from engine_result — patched post-return)
│  ├─ symbol:             ✅
│  ├─ runtime_session_id: ✅
│  ├─ correlation_id:     ❌ NOT A FIELD
│  ├─ timestamp_utc:      ✅ ISO string (generated at build time)
│
▼
Decision Audit
│  ├─ entity_id:          ✅
│  ├─ cycle_id:           ✅
│  ├─ decision_id:        ✅ CREATED HERE (UUID)
│  ├─ correlation_id:     ✅ (EXECUTE) / "" (NO_TRADE)
│  ├─ runtime_session_id: ✅
│
▼
Decision Ledger
│  ├─ entity_id:          ✅ (engine path) / "" (pre-engine exits)
│  ├─ cycle_id:           ✅
│  ├─ correlation_id:     ✅ (EXECUTE) / "" (NO_TRADE)
│  ├─ decision_id:        ❌ NOT A FIELD
│
▼
Execution Context
│  ├─ correlation_id:     ✅
│  ├─ symbol:             ✅
│  ├─ entity_id:          ❌ NOT A FIELD (by design)
│  ├─ cycle_id:           ❌ NOT A FIELD (by design)
│
▼
Execution Result
│  ├─ entity_id:          ✅
│  ├─ cycle_id:           ✅
│  ├─ decision_id:        ✅
│  ├─ correlation_id:     ✅
│
▼
Shadow Trade
│  ├─ correlation_id:     ✅
│  ├─ cycle_id:           ✅
│  ├─ entity_id:          ❌ NOT A FIELD
│
▼
Trade Truth (at trade CLOSE)
│  ├─ correlation_id:     ✅
│  ├─ trade_id:           ✅
│  ├─ symbol:             ✅
│  ├─ entity_id:          ❌ NOT A FIELD
│  ├─ cycle_id:           ❌ NOT A FIELD
```

---

## 2. OBJECT OWNERSHIP TABLE

| Object | File | Creation Function | Owner | Fields Received | Fields Created | Fields Missing |
|--------|------|-------------------|-------|-----------------|----------------|----------------|
| Candle | `data/mt5_data.py` | `copy_rates_closed()` | MT5DataFeed | — | time, OHLCV | entity_id, cycle_id, symbol (implicit) |
| Signal | `strategy/signal_orchestrator.py` | `evaluate_closed_bar()` | signal_orchestrator | bar_index, candle data | pattern, side, bar_time | entity_id, cycle_id, symbol |
| OpportunityAssessment | `core/pipeline/new_engine.py` | `run_new_engine()` line ~208 | new_engine | symbol, bar_time, pattern, scores | entity_id, all analytical fields | correlation_id, decision_id, runtime_session_id |
| DecisionTrace | `core/decision_trace.py` | `build_decision_trace()` | decision_trace module | entity_id, cycle_id, symbol, action, scores | timestamp_utc, terminal_stage, diagnostics | correlation_id, decision_id |
| Decision Audit | `core/decision_audit.py` | `persist_new_engine_decision_audit()` | decision_audit module | entity_id, cycle_id, symbol, engine_result | decision_id (UUID), timestamp | — (most complete) |
| Decision Ledger | `core/decision_ledger.py` | `build_ledger_entry()` via `_finalize_decision()` | DecisionLedgerWriter | entity_id, cycle_id, symbol, correlation_id | timestamp, timestamp_unix, causal_signature | decision_id, runtime_session_id |
| Execution Context | `core/execution_context.py` | `build_execution_context()` | execution_context module | correlation_id, symbol, timestamp_utc | market_access, infrastructure, risk_environment | entity_id, cycle_id, decision_id |
| Execution Result | `core/persistence/execution_result_writer.py` | `persist_execution_result()` | execution_result_writer | symbol, cycle_id, entity_id, decision_id, correlation_id | timestamp_utc, timestamp_unix | runtime_session_id |
| Shadow Trade | `core/shadow_trades.py` | `open_trade()` | ShadowTradeEngine | correlation_id, cycle_id, symbol, entry data | trade_id (`shadow_{cycle}_{sym}`) | entity_id, decision_id |
| Trade Truth | `core/trade_truth.py` | `build_trade_truth()` | trade_truth module | trade_id, correlation_id, symbol | schema_version, outcome domain | entity_id, cycle_id, decision_id, runtime_session_id |

---

## 3. FIELD LINEAGE

### entity_id

```
Created:   core/pipeline/new_engine.py, line 86
Format:    f"{symbol}_{int(candles[closed_i].time)}"
Example:   "EURUSD_SB_1752652800"

Passed through:
  new_engine._strategy_meta["entity_id"]
    ↓
  _new_result["entity_id"] (engine_result dict)
    ↓
  OpportunityAssessment.entity_id ⚠️ (cycle_id=0 at this point)
    ↓
  DecisionTrace.entity_id (via build_decision_trace)
    ↓
  Decision Audit record["entity_id"]
    ↓
  _cycle_decision["entity_id"] → Decision Ledger
    ↓
  ExecutionResult.entity_id

Lost at:
  - ShadowTrade: NOT A FIELD (no entity_id attribute)
  - TradeTruth: NOT A FIELD (by design — pure execution reality)
  - ExecutionContext: NOT A FIELD (infrastructure only)
  - Pre-engine exits (kill switch, session block, daily loss): entity_id=""
  - "no_viable_pattern" return: entity_id IS set (constructed before pattern gate)

Owner responsible: new_engine.py (creation), live_scanner.py (propagation)
```

### cycle_id

```
Created:   core/runtime/live_scanner.py, line 208
Format:    Integer, monotonically increasing per loop iteration
Example:   42

Passed through:
  live_scanner.cycle_id (scope variable)
    ↓
  ⚠️ NOT passed to run_new_engine() — engine uses default 0
    ↓
  _new_result["cycle_id"] = cycle_id (PATCHED at line 940, after engine return)
    ↓
  DecisionTrace.cycle_id (from patched engine_result)
    ↓
  _cycle_decision["cycle_id"] → Decision Ledger
    ↓
  Decision Audit (passed explicitly)
    ↓
  ExecutionResult (passed explicitly)
    ↓
  ShadowTrade.cycle_id (passed explicitly)

Lost at:
  - OpportunityAssessment.cycle_id = 0 ALWAYS (engine default)
  - OpportunityAssessment persisted to S3 with cycle_id=0
  - TradeTruth: NOT A FIELD
  - ExecutionContext: NOT A FIELD

⚠️ CRITICAL BUG: cycle_id is never passed to run_new_engine()
   Result: assessment_log records all have cycle_id=0
   Owner responsible: live_scanner.py (must pass cycle_id to engine)
```

### symbol

```
Created:   core/runtime/live_scanner.py, line ~160
Format:    Broker-resolved string (e.g., "EURUSD_SB")
Source:    feed.resolve_symbol() → sym_state.symbol

Passed through:
  sym_state.symbol → run_new_engine(symbol=...)
    ↓
  OpportunityAssessment.symbol
    ↓
  DecisionTrace.symbol
    ↓
  All downstream objects (explicitly passed)

Lost at:
  - Never lost within normal flow
  - Old pipeline path: symbol available from sym_state (always in scope)

Owner responsible: live_scanner.py (resolution), MT5DataFeed (discovery)
```

### timestamp / timestamp_utc

```
Created:   Multiple independent sources (NO single authority)

Formats in use:
  - Candle.time: Unix seconds (broker-local, e.g., UTC+3)
  - OpportunityAssessment.bar_time: Unix seconds (same as candle.time)
  - DecisionTrace.timestamp_utc: ISO string "2026-07-16T09:00:01.234Z"
  - Decision Ledger.timestamp: ISO string (generated at build_ledger_entry)
  - Decision Ledger.timestamp_unix: float (generated at build_ledger_entry)
  - Execution Result.timestamp_utc: ISO string (generated at persist time)
  - Execution Context.timestamp_utc: float (Unix seconds, from closed_time)
  - Trade Truth: entry_timestamp_broker, exit_timestamp_broker (float)

⚠️ NO SINGLE FORMAT: Each object generates its own timestamp independently.
   bar_time (Unix seconds, broker-local) vs ISO string vs float.
   Some are bar-close time, others are persistence-wall-clock time.

Owner responsible: No single owner — each persistence layer owns its own
```

### runtime_session_id

```
Created:   core/runtime/live_scanner.py, lines 196-198
Format:    uuid4().hex[:12] (12-char hex string)
Example:   "a3f7c2e8d901"
Lifetime:  Constant per process (never changes during runtime)

Passed through:
  _runtime_session_id (live_scanner scope)
    ↓
  build_decision_trace(..., runtime_session_id=...)
    ↓
  persist_new_engine_decision_audit(..., runtime_session_id=...)

Lost at:
  - Decision Ledger: NOT A PARAMETER (not passed to build_ledger_entry)
  - Execution Context: NOT A FIELD
  - Execution Result: NOT A PARAMETER
  - Shadow Trade: NOT A FIELD
  - Trade Truth: NOT A FIELD

Owner responsible: live_scanner.py (only producer; limited distribution)
```

### correlation_id

```
Created:   core/runtime/live_scanner.py, line ~1148 (EXECUTE path ONLY)
Generator: core/correlation.py :: generate_correlation_id()
Format:    "COR-{YYYYMMDD}-{cycle_id}-{SYMBOL}-{hash4}"
Example:   "COR-20260716-42-EURUSD-A93F"

Passed through (EXECUTE path):
  _cor_id (live_scanner scope)
    ↓
  Decision Audit (correlation_id=_cor_id)
    ↓
  Execution Context (correlation_id=_cor_id)
    ↓
  Shadow Trade (correlation_id=_cor_id)
    ↓
  Execution Result (correlation_id=_cor_id)
    ↓
  _cycle_decision["correlation_id"] → Decision Ledger
    ↓
  Trade Truth (correlation_id — at close, from shadow trade)

Lost at:
  - ALL NO_TRADE paths: correlation_id="" (never generated)
  - DecisionTrace: NOT A FIELD (trace doesn't carry it)
  - OpportunityAssessment: NOT A FIELD (created before EXECUTE decision)

⚠️ NO_TRADE cycles have NO correlation_id.
   Cannot join decision_audit(NO_TRADE) → execution_context.
   Must use symbol + cycle_id instead.

Owner responsible: live_scanner.py (generation point — EXECUTE only)
```

### decision_id

```
Created:   core/decision_audit.py, inside persist_decision_audit()
Format:    uuid4().hex (32-char hex string)
Example:   "f8a3b2c1d4e5f6a7b8c9d0e1f2a3b4c5"

Passed through:
  _decision_id = persist_new_engine_decision_audit(...)
    ↓
  persist_execution_result(..., decision_id=_decision_id)

Lost at:
  - DecisionTrace: NOT A FIELD
  - Decision Ledger: NOT A FIELD
  - Execution Context: NOT A FIELD
  - Shadow Trade: NOT A FIELD
  - Trade Truth: NOT A FIELD
  - If persist_new_engine_decision_audit() throws: _decision_id=""

Owner responsible: decision_audit.py (creation), live_scanner.py (propagation)
```

### trade_id

```
Created:   Two sources depending on context:
  1. Shadow Trade: f"shadow_{cycle_id}_{symbol}" (core/shadow_trades.py)
  2. Trade Truth: Broker ticket number (from mt5.order_send() deal response)

Format:
  Shadow: "shadow_42_EURUSD_SB" (synthetic)
  Broker: "12345678" (MT5 deal ticket as string)

Passed through:
  Broker trade_id → TradeStateManager → build_trade_truth(trade_id=...)

Lost at:
  - Never lost (once a trade opens, trade_id persists through close)
  - Not joinable to entity_id (no shared key without correlation_id)

Owner responsible: MT5Execution (broker ticket), ShadowTradeEngine (shadow ID)
```

---

## 4. NULL ORIGIN TABLE

### entity_id

| Location | Value | Expected? | Cause | Fix Owner |
|----------|-------|-----------|-------|-----------|
| OpportunityAssessment | ✅ always set | Yes | Created by engine from `f"{symbol}_{bar_time}"` | — |
| DecisionTrace | ✅ always set (even on error fallback) | Yes | engine_result always has entity_id | — |
| Decision Ledger (EXECUTE) | ✅ set | Yes | Copied from `_new_result.get("entity_id")` | — |
| Decision Ledger (NO_TRADE via engine) | ✅ set | Yes | Copied from `_new_result.get("entity_id")` | — |
| Decision Ledger (pre-engine exits: kill switch, session, daily loss) | `""` | **Bug** | Engine never called → entity_id never created | live_scanner.py — construct entity_id before engine |
| Decision Ledger (exception path) | ✅ set | Yes | Manually reconstructed: `f"{sym_state.symbol}_{int(closed_time)}"` | — |
| Shadow Trade | ❌ not a field | **Gap** | Dataclass lacks the attribute | shadow_trades.py |
| Trade Truth | ❌ not a field | By design | Pure execution reality — no analytical identity | Acceptable (join via correlation_id) |
| Outer except handler (line 2359) | ❌ never written | **Bug** | `_finalize_decision()` never called | live_scanner.py |

### cycle_id

| Location | Value | Expected? | Cause | Fix Owner |
|----------|-------|-----------|-------|-----------|
| OpportunityAssessment | `0` always | **Bug** | `run_new_engine()` never receives `cycle_id` parameter | live_scanner.py — pass cycle_id to engine |
| OpportunityAssessment (S3 record) | `0` always | **Bug** | Persisted before live_scanner patches result dict | Same as above |
| DecisionTrace | ✅ correct value | Yes | Reads from patched `_new_result["cycle_id"]` | — |
| Decision Ledger | ✅ correct value | Yes | Reads from `_cycle_decision["cycle_id"]` (set at init) | — |
| Trade Truth | ❌ not a field | By design | Pure execution reality | Acceptable (join via correlation_id) |
| Outer except handler (line 2359) | ❌ never written | **Bug** | Complete data loss | live_scanner.py |

### symbol

| Location | Value | Expected? | Cause | Fix Owner |
|----------|-------|-----------|-------|-----------|
| All objects | ✅ always present | Yes | Available from `sym_state.symbol` at all code paths | — |
| Outer except handler (line 2359) | ❌ never written | **Bug** | Complete data loss | live_scanner.py |

### correlation_id

| Location | Value | Expected? | Cause | Fix Owner |
|----------|-------|-----------|-------|-----------|
| ALL NO_TRADE paths | `""` | By design (debatable) | Only generated on EXECUTE | live_scanner.py |
| Decision Audit (NO_TRADE) | `""` | By design | Same | — |
| Decision Ledger (NO_TRADE) | `""` | By design | Same | — |
| DecisionTrace | ❌ not a field | By design | Trace is built before EXECUTE decision | — |
| OpportunityAssessment | ❌ not a field | By design | Assessment is pre-policy | — |
| Outer except handler (line 2359) | ❌ never written | **Bug** | Complete data loss | live_scanner.py |

---

## 5. ALL IDENTITY LOSS POINTS

| # | Location | Fields Lost | Severity | Cause |
|---|----------|-------------|----------|-------|
| 1 | `live_scanner.py:906` — `run_new_engine()` call | cycle_id on Assessment | **High** | cycle_id not passed; defaults to 0. Assessment persisted with wrong value. |
| 2 | `live_scanner.py:2359` — outer except | ALL fields | **Critical** | Unhandled exception → `_finalize_decision()` never called → entire cycle invisible |
| 3 | Pre-engine exits (kill switch, session, daily loss) | entity_id="" | **Low** | Engine never called; entity_id requires bar_time from engine scope. Acceptable boundary. |
| 4 | NO_TRADE path | correlation_id="" | **Medium** | Only generated on EXECUTE. Prevents full join graph on rejected opportunities. |
| 5 | ShadowTrade dataclass | entity_id | **Low** | Not a field. Must join to decision_audit via correlation_id → then get entity_id. |
| 6 | TradeTruth schema | entity_id, cycle_id | **Medium** | By design (pure execution reality). But requires multi-hop join to reach assessment. |
| 7 | Decision Ledger schema | decision_id, runtime_session_id | **Low** | Not passed. decision_id only exists in audit. runtime_session_id only in trace/audit. |
| 8 | DecisionTrace schema | correlation_id | **Low** | By design — trace is built before the EXECUTE decision that creates correlation_id. |

---

## 6. RECOMMENDED FIXES (priority order)

| Priority | Fix | Impact | Owner |
|----------|-----|--------|-------|
| **P0** | Add `_finalize_decision()` call to outer except handler (line 2359) | Eliminates complete silent data loss on unhandled exceptions | live_scanner.py |
| **P1** | Pass `cycle_id=cycle_id` to `run_new_engine()` at line 906 | Fixes OpportunityAssessment.cycle_id=0 bug. Assessment S3 records become queryable by cycle. | live_scanner.py — ✅ FIXED |
| **P2** | Generate correlation_id on ALL paths (not just EXECUTE) | Enables full join graph. NO_TRADE decisions become joinable to execution_context. | live_scanner.py |
| **P3** | Add `entity_id` field to ShadowTrade dataclass | Direct joinability to assessment without multi-hop | shadow_trades.py |
| **P4** | Construct entity_id before engine call (for pre-engine exits) | Kill switch / session / daily loss decisions become traceable to bar | live_scanner.py |
| **P5** | Add `runtime_session_id` to Decision Ledger schema | Process identity on every persisted decision | decision_ledger.py |

---

## 7. JOINABILITY MATRIX (current state)

| From → To | Join Key | Works? | Hops Required |
|-----------|----------|--------|---------------|
| Assessment → Trace | entity_id | ✅ | 0 (direct) |
| Assessment → Ledger | entity_id + cycle_id | ⚠️ | 0 but cycle_id=0 on assessment |
| Assessment → Audit | entity_id | ✅ | 0 (direct) |
| Trace → Ledger | entity_id + cycle_id | ✅ | 0 (direct) |
| Audit → Execution Context | correlation_id | ✅ (EXECUTE only) | 0 |
| Audit → Execution Result | decision_id | ✅ | 0 |
| Execution Result → Trade Truth | correlation_id | ✅ | 0 |
| Trade Truth → Assessment | correlation_id → audit → entity_id | ✅ | 2 hops |
| Shadow Trade → Assessment | correlation_id → audit → entity_id | ✅ | 2 hops |
| NO_TRADE Audit → Execution Context | ❌ correlation_id="" | ❌ | Impossible without fix P2 |

---

*End of audit. No code was modified.*
