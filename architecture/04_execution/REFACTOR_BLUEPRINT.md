# ARCHITECTURE VERIFICATION & REFACTOR BLUEPRINT

**Generated:** 2026-07-17  
**Baseline documents:**  
- `LIVE_SCANNER_RESPONSIBILITY_AUDIT.md`  
- `ARCHITECTURAL_OWNERSHIP_AND_ROUTING.md`  
**Purpose:** Verify internal consistency, then produce step-by-step extraction roadmap

---

## PHASE 1 — MODULE VERIFICATION

### 1.1 `core/runtime/mt5_health.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Manage MT5 connection lifecycle (detect/reconnect/resync) |
| Owns | Reconnect state machine, backoff timing, fail counter, resync coordination |
| Does NOT own | MT5 API calls (delegated to `core/mt5_connection`), position recovery logic |
| Incoming deps | `live_scanner.py` only |
| Outgoing deps | `core/mt5_connection` (attempt_reconnect, is_mt5_healthy, resync_positions), `MetaTrader5` (symbol_select) |
| Dependency direction | ✅ Downward only (runtime → domain service) |
| Circular risk | None — leaf consumer of mt5_connection |
| Interface sufficient | ✅ `check_and_reconnect(states) → bool` covers the entire responsibility |

### 1.2 `core/runtime/cycle_guards.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Evaluate whether the current cycle is permitted to proceed |
| Owns | Composition of drawdown + daily loss + kill switch + daily reset into single permission |
| Does NOT own | Individual guard implementations, risk calculations, account queries |
| Incoming deps | `live_scanner.py` only |
| Outgoing deps | `risk/drawdown_guard`, `risk/daily_loss_guard`, `core/kill_switch`, `core/daily_reset` |
| Dependency direction | ✅ Downward only (runtime → risk domain) |
| Circular risk | None — reads guard state, never called by guards |
| Interface sufficient | ✅ `evaluate() → CyclePermission(allowed, daily_loss_blocked, kill_active)` |

### 1.3 `core/runtime/bar_provider.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Acquire and validate market data for one bar evaluation cycle |
| Owns | Tick fetch, candle fetch, stale detection, bar dedup, feed health, UTC conversion, per-cycle execution context capture |
| Does NOT own | MT5 API internals (in `data/mt5_data`), stale monitor logic (in `core/stale_monitor`) |
| Incoming deps | `live_scanner.py` per-symbol loop |
| Outgoing deps | `data/mt5_data`, `core/stale_monitor`, `core/runtime/runtime_utils`, `core/execution_context`, `core/correlation` |
| Dependency direction | ✅ Downward only |
| Circular risk | None |
| Interface sufficient | ✅ `fetch_bar(sym_state) → BarResult\|None` plus `tick_update(sym_state, kill_active)` |

**Note:** Execution context capture (R21) merged here because it shares the same lifecycle moment (new bar detected). Verified: no coupling to decision logic — it captures environment only.

### 1.4 `core/runtime/pre_engine_gates.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Evaluate per-symbol early-exit conditions before engine call |
| Owns | Sequencing of kill-switch check, daily-loss check, session check, pattern detection |
| Does NOT own | Session guard logic, pattern detection algorithms, paper outcome engine |
| Incoming deps | `live_scanner.py` per-symbol loop |
| Outgoing deps | `risk/session_guard`, `strategy/signal_orchestrator`, `core/pipeline/paper_outcome_engine` |
| Dependency direction | ✅ Downward (runtime → risk + strategy) |
| Circular risk | None |
| Interface sufficient | ✅ `evaluate(kill_active, daily_loss_blocked, candles, closed_i) → GateResult(proceed, patterns, outcome, reason)` |

### 1.5 `core/pipeline/observers.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Dispatch engine results to registered fire-and-forget observers |
| Owns | Observer registry, sequential dispatch, exception isolation per observer |
| Does NOT own | Individual observer logic (each lives in its own module) |
| Incoming deps | `live_scanner.py` (calls `notify_all`) |
| Outgoing deps | `core/pipeline/bias_fsm`, `core/pipeline/event_observer`, `core/pipeline/forensic_logger`, `core/pipeline/entity_tracker`, `core/pipeline/visibility_layer`, `core/pipeline/shadow_rooms`, `core/decision_trace` |
| Dependency direction | ✅ Pipeline → pipeline services (lateral, acceptable) |
| Circular risk | None — observers don't call back to registry |
| Interface sufficient | ✅ `notify_all(engine_result, context) → None` |

### 1.6 `core/runtime/decision_handler.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Handle engine evaluation outcomes (NO_TRADE or EXECUTE pre-setup) |
| Owns | Correlation ID generation, decision audit recording, execution context capture, shadow trade opening, narrative generation, output routing |
| Does NOT own | Engine computation, risk guard evaluation, broker execution, ledger finalization |
| Incoming deps | `live_scanner.py` |
| Outgoing deps | `core/correlation`, `core/decision_audit`, `core/execution_context`, `core/shadow_trades`, `core/pipeline/trade_narrative`, `core/pipeline/output_router`, decision_recorder |
| Dependency direction | ✅ Runtime → domain services |
| Circular risk | ⚠️ Calls `decision_recorder` — verified one-way (handler → recorder, never reverse) |
| Interface sufficient | ✅ `handle_no_trade(...)`, `handle_execute(...)→ExecuteSetup`, `handle_engine_exception(...)` |

### 1.7 `core/runtime/decision_recorder.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Manage the decision record lifecycle (init → mutate → finalize → persist) |
| Owns | Decision dict template, invariant enforcement, idempotent finalization, ledger write |
| Does NOT own | Decision content (set by callers), ledger buffering/flush (owned by DecisionLedgerWriter) |
| Incoming deps | `decision_handler`, `pre_engine_gates`, `execution_orchestrator`, `live_scanner` (fallback) |
| Outgoing deps | `core/decision_ledger` |
| Dependency direction | ✅ Downward only |
| Circular risk | None |
| Interface sufficient | ✅ `init_cycle(...)`, `mutate(**fields)`, `finalize()` |

### 1.8 `risk/runtime_guard_chain.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Evaluate 10 sequential per-trade risk guards and produce allow/block result |
| Owns | Guard sequencing, early-exit logic, guard result aggregation, risk event emission |
| Does NOT own | Individual guard implementations (each in its own module) |
| Incoming deps | `decision_handler` (EXECUTE path via live_scanner) |
| Outgoing deps | `risk/daily_trade_limit`, `risk/trade_cooldown`, `risk/correlation_guard`, `risk/portfolio_exposure_guard`, `risk/regime_guard`, `risk/spread_guard`, `core/challenge_progress_tracker`, `core/consistency_rules`, `core/prop_firm_rules`, `core/weekend_protection`, `core/pipeline/control_layer` |
| Dependency direction | ✅ Risk domain → risk domain + core domain services |
| Circular risk | ⚠️ Depends on `core/pipeline/control_layer` — verified one-way. Control layer is a final authority gate, not a pipeline computation. Acceptable cross-package dependency. |
| Interface sufficient | ✅ `evaluate(intent, symbol, context) → GuardChainResult(allowed, guard, reason)` |

### 1.9 `execution/execution_orchestrator.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Execute a trade decision and register the result |
| Owns | MT5 order send orchestration, result persistence, trade registration, slippage recording, daily counter update |
| Does NOT own | MT5 API details (in `mt5_execution`), risk guard evaluation, decision-making |
| Incoming deps | `live_scanner.py` (via decision_handler result) |
| Outgoing deps | `execution/mt5_execution`, `core/persistence/execution_result_writer`, `core/trade_management/TradeStateManager`, `core/slippage_monitor`, `risk/daily_trade_limit`, `core/pipeline/paper_outcome_engine` |
| Dependency direction | ✅ Execution → domain services (downward) |
| Circular risk | None |
| Interface sufficient | ✅ `execute_trade(intent, metadata, trade_manager) → ExecutionOutcome` |

### 1.10 `core/pipeline/shadow_pipeline.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Run legacy engine on state copies for comparison/validation |
| Owns | State copy management, baseline vs MTF comparison, divergence logging, calibration |
| Does NOT own | Old engine logic (in `core/engine`), MTF cache (in `core/timeframes/`) |
| Incoming deps | `live_scanner.py` (when shadow mode enabled) |
| Outgoing deps | `core/engine`, `core/engine_state`, `core/timeframes/` |
| Dependency direction | ✅ Pipeline → domain |
| Circular risk | None |
| Interface sufficient | ✅ `run_shadow(candles, sym_state, config, htf_context) → ShadowResult\|None` |

### 1.11 `core/pipeline/cycle_report.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Produce end-of-cycle diagnostic output |
| Owns | Opportunity ranking display, pipeline trace summary, funnel display, market snapshot, no-trade alerts |
| Does NOT own | Ranking algorithm (in `opportunity_ranker`), funnel accumulation (in `DecisionFunnel`) |
| Incoming deps | `live_scanner.py` (end of cycle) |
| Outgoing deps | `core/pipeline/opportunity_ranker`, `core/pipeline/dashboard`, `core/decision_trace.DecisionFunnel`, `core/quiet_period_diagnostics`, Discord |
| Dependency direction | ✅ Pipeline → pipeline services |
| Circular risk | None |
| Interface sufficient | ✅ `emit_cycle_report(candidates, drops, funnel, cycle_id, had_trade)` |

### 1.12 `core/runtime/health_monitor.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Monitor runtime health (heartbeat, liveness, reconciliation, risk timeline) |
| Owns | Heartbeat file write, liveness classification, periodic reconciliation trigger, risk timeline snapshot |
| Does NOT own | Reconciliation logic (in `core/mt5_connection`), risk calculations |
| Incoming deps | `live_scanner.py` (end of cycle) |
| Outgoing deps | `core/heartbeat`, `core/event_bus`, `core/mt5_connection`, `risk/risk_timeline` |
| Dependency direction | ✅ Runtime → domain services |
| Circular risk | None |
| Interface sufficient | ✅ `tick(cycle_id, latency, states, mt5_state)` |

### 1.13 `core/trade_management/tick_driver.py`

| Aspect | Verification |
|--------|-------------|
| Single responsibility | Drive tick-level position management updates |
| Owns | Price update dispatch, retry queue draining |
| Does NOT own | Position state (in TradeStateManager), SL/TP logic |
| Incoming deps | `live_scanner.py` (or `bar_provider`) |
| Outgoing deps | `core/trade_management/TradeStateManager` |
| Dependency direction | ✅ Caller → domain |
| Circular risk | None |
| Interface sufficient | ✅ `drive_tick(trade_manager, symbol, bid, ask, kill_active)` |

---

## PHASE 2 — DEPARTMENT BOUNDARY VERIFICATION

### `core/runtime/` — Runtime Coordination Department

| Module | Belongs? | Justification |
|--------|----------|---------------|
| `mt5_health.py` | ✅ | Governs connection lifecycle — runtime infrastructure |
| `cycle_guards.py` | ✅ | Cycle-level permission — runtime boundary control |
| `bar_provider.py` | ✅ | Data acquisition orchestration for runtime loop |
| `pre_engine_gates.py` | ✅ | Per-symbol early-exit — runtime coordination |
| `decision_handler.py` | ✅ | Outcome routing — runtime coordination |
| `decision_recorder.py` | ✅ | Decision lifecycle management — runtime concern |
| `health_monitor.py` | ✅ | System health — runtime infrastructure |

**Verdict:** All modules correctly assigned. The department owns "when to do things and in what order" — never "what to compute."

### `core/pipeline/` — Decision Pipeline Department

| Module | Belongs? | Justification |
|--------|----------|---------------|
| `observers.py` | ✅ | Observes pipeline output — pipeline concern |
| `shadow_pipeline.py` | ✅ | Runs analytical pipeline (shadow) — pipeline concern |
| `cycle_report.py` | ✅ | Reports on pipeline outcomes — pipeline output |

**Verdict:** All modules correctly assigned. They process or report on analytical pipeline output.

### `risk/` — Risk Management Department

| Module | Belongs? | Justification |
|--------|----------|---------------|
| `runtime_guard_chain.py` | ✅ | Composes risk guards into per-trade evaluation — risk domain |

**Potential concern:** This module depends on `core/challenge_progress_tracker`, `core/consistency_rules`, `core/prop_firm_rules`, `core/weekend_protection` — modules in `core/` not `risk/`.

**Resolution:** These are compliance/business-rule modules that function as risk gates. They COULD live in `risk/` but their current location in `core/` is acceptable — they serve broader compliance concerns beyond pure risk. The guard chain calling them from `risk/` follows dependency direction (risk → core domain services). **No change needed.**

### `execution/` — Execution Department

| Module | Belongs? | Justification |
|--------|----------|---------------|
| `execution_orchestrator.py` | ✅ | Trade execution lifecycle — execution domain |

**Potential concern:** Depends on `core/trade_management/TradeStateManager` for trade registration.

**Resolution:** Trade registration is the completion phase of execution. The orchestrator delegates to TradeStateManager (downward dependency). Alternative: move registration into `core/trade_management/`. However, keeping it in the orchestrator maintains the atomic "execute → register" unit as one operation. **Keep as proposed.**

### `core/trade_management/` — Trade Management Department

| Module | Belongs? | Justification |
|--------|----------|---------------|
| `tick_driver.py` | ✅ | Drives position updates — trade management concern |

**Verdict:** Correct. Smallest possible module (single function).

---

## PHASE 3 — RUNTIME CALL GRAPH

```
main.py
  │
  ▼
run_live_scanner()                    [live_scanner.py — ORCHESTRATOR]
  │
  ├─── STARTUP ──────────────────────────────────────────────────────
  │     Construct: states, mt5_health, cycle_guards, bar_providers,
  │                decision_handler, guard_chain, executor,
  │                observers, health_monitor, recorder, ledger
  │
  ├─── MAIN LOOP ─────────────────────────────────────────────────────
  │     │
  │     ├── mt5_health.check_and_reconnect(states)
  │     │     Data: states list (read), mt5_state (mutated)
  │     │     Owner: mt5_health owns reconnect lifecycle
  │     │     Direction: one-way (orchestrator → mt5_health)
  │     │     Lifecycle: orchestrator owns mt5_health instance
  │     │
  │     ├── cycle_guards.evaluate()
  │     │     Data: None in (reads from guard instances internally)
  │     │     Returns: CyclePermission(allowed, daily_loss_blocked, kill_active)
  │     │     Owner: cycle_guards owns permission evaluation
  │     │     Direction: one-way
  │     │     Lifecycle: orchestrator owns cycle_guards instance
  │     │
  │     ├── FOR EACH symbol_state:
  │     │     │
  │     │     ├── bar_provider.tick_update(sym_state, kill_active)
  │     │     │     Data: sym_state (trade_manager ref), bid/ask, kill_active
  │     │     │     Owner: bar_provider delegates to trade_management
  │     │     │     Direction: one-way
  │     │     │
  │     │     ├── bar_provider.fetch_bar(sym_state)
  │     │     │     Data: sym_state (feed ref, stale_monitor)
  │     │     │     Returns: BarResult(bid, ask, candles, closed_i, closed_time, feed_state) or None
  │     │     │     Owner: bar_provider owns data acquisition
  │     │     │     Direction: one-way
  │     │     │     Lifecycle: orchestrator owns bar_provider instances (one per symbol)
  │     │     │
  │     │     ├── recorder.init_cycle(symbol, cycle_id, regime, guards)
  │     │     │     Data: identity fields for decision record
  │     │     │     Owner: recorder owns decision lifecycle
  │     │     │     Direction: one-way
  │     │     │
  │     │     ├── pre_engine_gates.evaluate(permission, bar)
  │     │     │     Data: CyclePermission + BarResult
  │     │     │     Returns: GateResult(proceed, patterns, outcome, reason)
  │     │     │     Owner: pre_engine_gates owns gate evaluation
  │     │     │     Direction: one-way
  │     │     │     On early-exit: recorder.mutate() → recorder.finalize()
  │     │     │
  │     │     ├── run_new_engine(candles, patterns, ...)  [STAYS in orchestrator]
  │     │     │     Data: BarResult fields + patterns + config + risk_manager
  │     │     │     Returns: engine_result dict
  │     │     │     Owner: new_engine owns computation
  │     │     │     Direction: one-way (orchestrator → engine)
  │     │     │
  │     │     ├── observers.notify_all(engine_result, context)
  │     │     │     Data: engine_result (read-only)
  │     │     │     Owner: observer registry owns dispatch
  │     │     │     Direction: one-way (fire-and-forget)
  │     │     │
  │     │     ├── [IF NO_TRADE]:
  │     │     │     decision_handler.handle_no_trade(engine_result, context)
  │     │     │     recorder.finalize()
  │     │     │
  │     │     ├── [IF EXECUTE]:
  │     │     │     │
  │     │     │     ├── decision_handler.handle_execute(engine_result, context)
  │     │     │     │     Returns: ExecuteSetup(correlation_id, decision_id, intent)
  │     │     │     │
  │     │     │     ├── guard_chain.evaluate(intent, symbol, context)
  │     │     │     │     Returns: GuardChainResult(allowed, guard, reason)
  │     │     │     │     On block: recorder.mutate(RISK_BLOCK) → recorder.finalize()
  │     │     │     │
  │     │     │     ├── executor.execute_trade(setup, trade_manager)
  │     │     │     │     Returns: ExecutionOutcome(success, fill, registered)
  │     │     │     │
  │     │     │     └── recorder.mutate(EXECUTE/FAIL) → recorder.finalize()
  │     │     │
  │     │     └── [IF EXCEPTION]:
  │     │           decision_handler.handle_engine_exception(exc, context)
  │     │           recorder.finalize()
  │     │
  │     ├── shadow_pipeline.run_shadow(...)  [if enabled]
  │     │
  │     ├── cycle_report.emit(candidates, drops, funnel, cycle_id)
  │     │
  │     ├── health_monitor.tick(cycle_id, latency, states, mt5_state)
  │     │
  │     ├── ledger.tick()
  │     │
  │     └── sleep(POLL_SECONDS)
  │
  └─── SHUTDOWN ──────────────────────────────────────────────────────
        ledger.flush()
        save_engine_states(states)
        disconnect_feeds(states)
```

---

## PHASE 4 — RESPONSIBILITY CLASSIFICATION

| # | Responsibility | Verdict | Destination | Reason |
|---|---------------|---------|-------------|--------|
| R1 | Startup & Construction | **STAY** | `live_scanner.py` | Orchestrator owns dependency construction |
| R2 | Main Event Loop | **STAY** | `live_scanner.py` | Core loop IS the orchestrator |
| R3 | MT5 Health | **MOVE** | `core/runtime/mt5_health.py` | Self-contained state machine, no strategy coupling |
| R4 | System-Level Guards | **MOVE** | `core/runtime/cycle_guards.py` | Pure guard composition, no data coupling |
| R5 | Data Acquisition | **MOVE** | `core/runtime/bar_provider.py` | Mechanical data preparation, no analytical logic |
| R6 | Decision Recording | **MOVE** | `core/runtime/decision_recorder.py` | Decision lifecycle is distinct from orchestration |
| R7 | Pre-Engine Gates | **MOVE** | `core/runtime/pre_engine_gates.py` | Gate evaluation is rule checking, not coordination |
| R8 | Engine Invocation | **STAY** | `live_scanner.py` | High-level pipeline coordination (1 call) |
| R9 | Post-Engine Observers | **MOVE** | `core/pipeline/observers.py` | 8 independent observers, identical pattern |
| R10 | Decision Trace | **MOVE** | `core/pipeline/observers.py` (merged with R9) | Same pattern as other observers |
| R11 | NO_TRADE Handling | **MOVE** | `core/runtime/decision_handler.py` | Outcome handling ≠ orchestration |
| R12 | EXECUTE Pre-Setup | **MOVE** | `core/runtime/decision_handler.py` | Same as R11 (other branch) |
| R13 | Runtime Guard Chain | **MOVE** | `risk/runtime_guard_chain.py` | Risk composition belongs in risk package |
| R14 | Broker Execution | **MOVE** | `execution/execution_orchestrator.py` | Execution domain |
| R15 | Trade Registration | **MOVE** | `execution/execution_orchestrator.py` (with R14) | Atomic with R14 |
| R16 | Post-Cycle Diagnostics | **MOVE** | `core/pipeline/cycle_report.py` | Reports on pipeline outcomes |
| R17 | Health Monitoring | **MOVE** | `core/runtime/health_monitor.py` | Runtime infrastructure monitoring |
| R18 | State Checkpoint + Ledger Flush | **STAY** | `live_scanner.py` | Simple 2-line housekeeping, not worth a module |
| R19 | Shutdown / Cleanup | **STAY** | `live_scanner.py` | Orchestrator owns lifecycle |
| R20 | Shadow Pipeline | **MOVE** | `core/pipeline/shadow_pipeline.py` | Self-contained analytical comparison |
| R21 | Per-Cycle Execution Context | **MOVE** | `core/runtime/bar_provider.py` (merged with R5) | Same lifecycle moment as data acquisition |
| R22 | Error Handling (inner) | **MOVE** | `core/runtime/decision_handler.py` | Engine crash is an outcome |
| R22 | Error Handling (outer) | **STAY** | `live_scanner.py` | Last-resort boundary is orchestrator's responsibility |
| R23 | Trade Management Tick | **MOVE** | `core/trade_management/tick_driver.py` | Belongs with trade management package |

**Summary:** 5 STAY, 18 MOVE, 0 DELETE.

---

## PHASE 5 — EXTRACTION ROADMAP

Ordered by: least coupling first → most coupling last. Each step must pass all tests before proceeding.

---

### Step 1: `core/trade_management/tick_driver.py` (R23)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None — zero coupling to other extracted modules |
| Files affected | `core/runtime/live_scanner.py`, new `core/trade_management/tick_driver.py` |
| Interface | `drive_tick(trade_manager, symbol, bid, ask, kill_active) → None` |
| Lines removed from live_scanner | ~10 |
| Regression risk | **Minimal** — single try/except:pass block moved |
| Tests before | `pytest tests/ -q` (full suite baseline) |
| Tests after | Same — verify 0 new failures |
| Rollback | Delete new file, revert live_scanner change (one hunk) |

---

### Step 2: `core/runtime/mt5_health.py` (R3)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None — no dependency on other extracted modules |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/mt5_health.py` |
| Interface | `MT5HealthManager(states)` with `check_and_reconnect() → bool` |
| Lines removed from live_scanner | ~40 |
| Regression risk | **Low** — self-contained state machine, clear input/output |
| Tests before | Full suite + manual MT5 disconnect/reconnect scenario |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner (one contiguous block) |

---

### Step 3: `core/runtime/cycle_guards.py` (R4)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/cycle_guards.py` |
| Interface | `CycleGuards(config)` with `evaluate() → CyclePermission` |
| Lines removed from live_scanner | ~50 |
| Regression risk | **Low** — pure function composition |
| Tests before | Full suite |
| Tests after | Same + new unit test for CycleGuards.evaluate() |
| Rollback | Delete new file, revert live_scanner |

---

### Step 4: `core/pipeline/observers.py` (R9 + R10)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None |
| Files affected | `core/runtime/live_scanner.py`, new `core/pipeline/observers.py` |
| Interface | `ObserverRegistry()` with `notify_all(engine_result, context) → None` |
| Lines removed from live_scanner | ~100 |
| Regression risk | **Low** — all observers already have try/except:pass. Behaviour identical. |
| Tests before | Full suite |
| Tests after | Same + new test verifying observer dispatch |
| Rollback | Delete new file, revert live_scanner |

---

### Step 5: `core/runtime/health_monitor.py` (R17)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/health_monitor.py` |
| Interface | `HealthMonitor(states)` with `tick(cycle_id, latency, mt5_state) → None` |
| Lines removed from live_scanner | ~90 |
| Regression risk | **Low** — observational only, no trading impact |
| Tests before | Full suite |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner |

---

### Step 6: `core/pipeline/cycle_report.py` (R16)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | Step 4 (observers provides DecisionFunnel access pattern) |
| Files affected | `core/runtime/live_scanner.py`, new `core/pipeline/cycle_report.py` |
| Interface | `emit_cycle_report(candidates, drops, funnel, filter_hits, cycle_id, had_trade) → None` |
| Lines removed from live_scanner | ~180 |
| Regression risk | **Low** — pure reporting, no trading impact |
| Tests before | Full suite |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner |

---

### Step 7: `core/runtime/bar_provider.py` (R5 + R21)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | Step 1 (tick_driver already extracted — bar_provider delegates to it or it runs independently) |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/bar_provider.py` |
| Interface | `BarProvider(sym_state, config)` with `fetch_bar() → BarResult\|None` |
| Lines removed from live_scanner | ~250 |
| Regression risk | **Medium** — data flow changes require careful testing. Feed state classification, UTC conversion, dedup logic must produce identical results. |
| Tests before | Full suite + strategy_trace output comparison (before/after sample) |
| Tests after | Same + new unit tests for BarProvider.fetch_bar() |
| Rollback | Delete new file, revert live_scanner |

---

### Step 8: `core/pipeline/shadow_pipeline.py` (R20)
Status: ⚠ PARTIALLY COMPLETE

Completed:
- Shadow-only comparison paths extracted
- Shadow divergence logging extracted
- Shadow result handling extracted
- No live execution impact

Remaining:
- Legacy pipeline execution authority remains in live_scanner.py

| Aspect | Detail |
|--------|--------|
| Preconditions | None (independent, feature-flagged) |
| Files affected | `core/runtime/live_scanner.py`, new `core/pipeline/shadow_pipeline.py` |
| Interface | `run_shadow(candles, sym_state, config, htf_context, new_pipeline_handled) → ShadowResult\|None` |
| Lines removed from live_scanner | ~250 |
| Regression risk | **Low** — entire block is gated by `ENABLE_LEGACY_SHADOW_PIPELINE` flag |
| Tests before | Full suite |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner |

---

### Step 9: `core/runtime/pre_engine_gates.py` (R7)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | Step 7 (bar_provider provides BarResult that gates consume) |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/pre_engine_gates.py` |
| Interface | `evaluate_pre_engine_gates(permission, candles, closed_i) → GateResult` |
| Lines removed from live_scanner | ~60 |
| Regression risk | **Medium** — gate ordering must be preserved exactly. Pattern detection is critical path. |
| Tests before | Full suite + verify decision_ledger output for PATTERN_REJECT/SESSION_BLOCK |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner |

---

### Step 10: `risk/runtime_guard_chain.py` (R13)
STATUS: COMPLETE ✅

| Aspect | Detail |
|--------|--------|
| Preconditions | None (all guards already modular) |
| Files affected | `core/runtime/live_scanner.py`, new `risk/runtime_guard_chain.py` |
| Interface | `evaluate_runtime_guards(intent, symbol, context) → GuardChainResult` |
| Lines removed from live_scanner | ~320 |
| Regression risk | **Low** — guards are independent, pattern is repetitive. Must preserve exact guard ORDER. |
| Tests before | Full suite + decision_ledger RISK_BLOCK count comparison |
| Tests after | Same + new test verifying guard chain order + early exit |
| Rollback | Delete new file, revert live_scanner |

---

### Step 11: `core/runtime/decision_recorder.py` (R6)

| Aspect | Detail |
|--------|--------|
| Preconditions | Steps 9, 10 (callers must exist first to define the interface they need) |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/decision_recorder.py` |
| Interface | `DecisionRecorder(ledger)` with `init_cycle()`, `mutate()`, `finalize()` |
| Lines removed from live_scanner | ~90 (including `_finalize_decision` function) |
| Regression risk | **Medium** — shared mutable state becomes typed API. Must verify every caller. |
| Tests before | Full suite + `test_decision_ledger_invariant.py` specifically |
| Tests after | Same |
| Rollback | Delete new file, revert live_scanner |

---

### Step 12: `core/runtime/decision_handler.py` (R11 + R12 + R22-inner)

| Aspect | Detail |
|--------|--------|
| Preconditions | Step 11 (decision_recorder must exist — handler calls it) |
| Files affected | `core/runtime/live_scanner.py`, new `core/runtime/decision_handler.py` |
| Interface | `DecisionHandler(config)` with `handle_no_trade()`, `handle_execute()`, `handle_engine_exception()` |
| Lines removed from live_scanner | ~350 |
| Regression risk | **Medium-High** — most complex extraction. Multiple paths, correlation_id generation, 4+ persistence calls. Must preserve exact outcome ordering. |
| Tests before | Full suite + decision_audit output comparison + correlation_id generation verification |
| Tests after | Same + new integration test for handle_execute flow |
| Rollback | Delete new file, revert live_scanner |

---

### Step 13: `execution/execution_orchestrator.py` (R14 + R15)

| Aspect | Detail |
|--------|--------|
| Preconditions | Step 12 (decision_handler produces ExecuteSetup that executor consumes) |
| Files affected | `core/runtime/live_scanner.py`, new `execution/execution_orchestrator.py` |
| Interface | `ExecutionOrchestrator(execution, config)` with `execute_trade(setup, trade_manager) → ExecutionOutcome` |
| Lines removed from live_scanner | ~200 |
| Regression risk | **Medium** — broker execution is critical path. Must preserve exact order: execute → persist → register → slippage → events. |
| Tests before | Full suite + execution_result log comparison |
| Tests after | Same + new test for ExecutionOrchestrator with mock MT5 |
| Rollback | Delete new file, revert live_scanner |

---

## CUMULATIVE LINE REDUCTION

| After Step | Module | Lines Removed | Remaining |
|------------|--------|---------------|-----------|
| 0 | (current) | 0 | ~2,721 |
| 1 | tick_driver | 10 | ~2,711 |
| 2 | mt5_health | 40 | ~2,671 |
| 3 | cycle_guards | 50 | ~2,621 |
| 4 | observers | 100 | ~2,521 |
| 5 | health_monitor | 90 | ~2,431 |
| 6 | cycle_report | 180 | ~2,251 |
| 7 | bar_provider | 250 | ~2,001 |
| 8 | shadow_pipeline | 250 | ~1,751 |
| 9 | pre_engine_gates | 60 | ~1,691 |
| 10 | runtime_guard_chain | 320 | ~1,371 |
| 11 | decision_recorder | 90 | ~1,281 |
| 12 | decision_handler | 350 | ~931 |
| 13 | execution_orchestrator | 200 | ~731 |

**Note:** Target of ~300 lines requires additional cleanup of inline code (diagnostic prints, Discord calls, filter_hit updates) that migrates with their parent responsibilities. The ~731 estimate is conservative — actual reduction will be closer to target with incidental code that moves with its responsibility.

---

## VALIDATION CRITERIA

After ALL extractions are complete, verify:

| Criterion | Verification Method |
|-----------|-------------------|
| Zero behaviour change | Full test suite passes (94+ tests) |
| Identical decision_ledger output | Compare JSONL output before/after for 100-cycle run |
| Identical decision_trace output | Compare JSONL output before/after for 100-cycle run |
| Identical opportunity_assessment output | Compare JSONL output before/after for 100-cycle run |
| Same trade decisions | Shadow comparison (before/after should produce same EXECUTE/NO_TRADE for same input) |
| No new imports in live_scanner | live_scanner imports only extracted modules, not their dependencies |
| Dependency direction holds | No extracted module imports live_scanner or other runtime modules (except recorder) |
| Each module < 200 lines | Verify file lengths |
| live_scanner < 400 lines | Verify file length |

---

*End of blueprint. Ready for implementation when authorised.*
