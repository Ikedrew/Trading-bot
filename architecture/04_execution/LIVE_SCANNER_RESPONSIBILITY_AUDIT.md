# LIVE SCANNER ARCHITECTURAL RESPONSIBILITY AUDIT

**Generated:** 2026-07-17  
**File:** `core/runtime/live_scanner.py`  
**Lines:** ~2,721  
**Functions:** 1 public (`run_live_scanner`), 1 nested (`_finalize_decision`), 1 helper (`_write_heartbeat`)  
**Type:** Monolithic runtime orchestrator

---

## EXECUTIVE SUMMARY

`live_scanner.py` currently owns **23 distinct responsibilities** spanning 7 macro-domains.
It should own **5** (startup, dependency construction, main loop, coordination, shutdown).
The remaining **18** should be extracted into dedicated modules.

Current state: **2,721 lines**, ~60 external imports, ~15 inline lazy imports.  
Target state: **~300 lines** — a thin orchestrator delegating to subsystems.

---

## 1. RESPONSIBILITY INVENTORY

### R1: Runtime Startup & Dependency Construction

| Aspect | Detail |
|--------|--------|
| Purpose | Build all runtime objects, resolve symbols, initialize guards |
| Functions | `run_live_scanner()` lines 93–310 |
| Internal deps | `_LiveSymbolState`, `_build_risk_manager`, `_build_trade_management_config` |
| External deps | `config`, `MT5Execution`, `MT5DataFeed`, `symbol_resolver`, `load_engine_state`, `TimeframeCache`, `StaleDataMonitor`, guards |
| Inputs | `symbols` param, config module |
| Outputs | `states` list, `execution` instance, all guards |
| State mutated | All system-level variables |
| Side effects | MT5 symbol_select, feed connect, position recovery |
| Belongs in live_scanner? | ✅ YES (orchestrator owns construction) |
| Coupling | High |
| Cohesion | Medium |

---

### R2: Main Event Loop

| Aspect | Detail |
|--------|--------|
| Purpose | Drive per-cycle iteration, shutdown detection, gap classification |
| Functions | While loop (lines 312–380) |
| Internal deps | `cycle_id`, `_last_cycle_wall` |
| External deps | `is_shutdown_requested()`, `emit_system_health`, Discord |
| Inputs | Time, shutdown flag |
| Outputs | Cycle ID, gap alerts |
| State mutated | `cycle_id`, `_last_cycle_wall`, `_cycle_had_trade` |
| Side effects | Runtime incident alerts |
| Belongs in live_scanner? | ✅ YES (core loop is orchestrator responsibility) |
| Coupling | Low |
| Cohesion | High |

---

### R3: MT5 Connection Health Management

| Aspect | Detail |
|--------|--------|
| Purpose | Detect disconnect, reconnect with backoff, resync positions |
| Functions | Lines 381–420 |
| Internal deps | `mt5_state`, `reconnect_fail_count`, `last_reconnect_attempt` |
| External deps | `attempt_reconnect`, `is_mt5_healthy`, `resync_positions`, `MetaTrader5` |
| Inputs | MT5 state, time |
| Outputs | Reconnected or degraded-mode skip |
| State mutated | `mt5_state`, `reconnect_fail_count` |
| Side effects | MT5 reconnection, position resync |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/mt5_health_manager.py` |
| Reason | Self-contained lifecycle with backoff logic, no coupling to strategy |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | Medium |

---

### R4: System-Level Guards (Drawdown, Daily Loss, Kill Switch, Daily Reset)

| Aspect | Detail |
|--------|--------|
| Purpose | Pre-symbol gate checks that block entire cycles |
| Functions | Lines 422–474 |
| Internal deps | `_drawdown_guard`, `_daily_loss_guard`, `_daily_trade_limit`, `_kill_active` |
| External deps | `DrawdownGuard`, `DailyLossGuard`, `is_kill_switch_active`, `DailyResetCoordinator` |
| Inputs | Account state, time |
| Outputs | Boolean flags (`_daily_loss_blocked`, `_kill_active`) |
| State mutated | Guard internal state, `_daily_loss_blocked` |
| Side effects | Risk guard events, Discord alerts |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/system_guards.py` |
| Reason | Pure guard evaluation with no data/strategy coupling |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | Medium |

---

### R5: Market Data Acquisition (Tick + Candle + Stale Monitoring)

| Aspect | Detail |
|--------|--------|
| Purpose | Fetch tick/candles, detect staleness, classify feed health |
| Functions | Lines 484–680 |
| Internal deps | `sym_state.feed`, `sym_state.stale_monitor` |
| External deps | `MT5DataFeed`, `StaleDataMonitor`, `emit_feed_health`, `_closed_bar_index` |
| Inputs | Symbol, timeframe, config |
| Outputs | `bid`, `ask`, `candles`, `closed_time`, `_is_new_bar`, `_feed_state` |
| State mutated | `sym_state.last_closed_time`, `sym_state.iterations`, stale monitor |
| Side effects | Feed health events, diagnostic prints |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/data_acquisition.py` |
| Reason | Mechanical data fetch/validation — no strategy or decision logic |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | High |

---

### R6: Per-Cycle Decision Context Setup

| Aspect | Detail |
|--------|--------|
| Purpose | Initialize `_cycle_decision` dict, define `_finalize_decision()` |
| Functions | Lines 757–845 |
| Internal deps | `_cycle_decision`, `_cycle_decision_written`, `_ledger` |
| External deps | `DecisionLedgerWriter.record()` |
| Inputs | Symbol, cycle_id, regime, guard state |
| Outputs | Decision dict template, finalization closure |
| State mutated | `_cycle_decision`, `_cycle_decision_written` |
| Side effects | Ledger write on finalize |
| Belongs in live_scanner? | ❌ NO (partially) |
| Destination | `core/runtime/decision_recorder.py` |
| Reason | Decision recording is a distinct concern from orchestration |
| Coupling | Medium |
| Cohesion | High |
| Risk | Medium |
| Priority | Medium |

---

### R7: Pre-Engine Gates (Kill Switch, Daily Loss, Session, Pattern)

| Aspect | Detail |
|--------|--------|
| Purpose | Early-exit checks before engine evaluation |
| Functions | Lines 847–905 |
| Internal deps | `_kill_active`, `_daily_loss_blocked`, `_cycle_decision` |
| External deps | `risk.session_guard`, `signal_orchestrator.evaluate_closed_bar` |
| Inputs | Flags, candles, closed_i |
| Outputs | Early-exit decisions or pattern list |
| State mutated | `_cycle_decision` |
| Side effects | Decision finalization on early exits |
| Belongs in live_scanner? | Partially (pattern detection should be separate) |
| Destination | `core/runtime/pre_engine_gates.py` |
| Reason | Gate logic is rule evaluation, not orchestration |
| Coupling | Medium |
| Cohesion | Medium |
| Risk | Medium |
| Priority | Medium |

---

### R8: Strategy Engine Invocation

| Aspect | Detail |
|--------|--------|
| Purpose | Call `run_new_engine()` with all inputs, capture result |
| Functions | Lines 907–960 |
| Internal deps | `_new_result`, `_new_engine_score`, `_new_pipeline_handled` |
| External deps | `core.pipeline.new_engine.run_new_engine()`, `TimeframeCache` |
| Inputs | Candles, patterns, bid/ask, engine_state, risk_manager, HTF context, cycle_id |
| Outputs | Engine result dict |
| State mutated | `_new_result`, `_new_engine_score`, `_new_pipeline_handled` |
| Side effects | None (pure function call) |
| Belongs in live_scanner? | ✅ YES (this is pipeline coordination) |
| Coupling | Medium |
| Cohesion | High |

---

### R9: Post-Engine Observability (Bias FSM, Event Observer, Forensic, Entity, Visibility, Shadow Rooms)

| Aspect | Detail |
|--------|--------|
| Purpose | 6 observational subsystems triggered after engine evaluation |
| Functions | Lines 962–1033 |
| Internal deps | `_new_result`, `sym_state.engine_state` |
| External deps | `bias_fsm`, `event_observer`, `forensic_logger`, `entity_tracker`, `visibility_layer`, `shadow_rooms` |
| Inputs | Engine result, candles, engine state |
| Outputs | State transitions, log entries, shadow comparisons |
| State mutated | `engine_state.bias_*` (FSM only) |
| Side effects | File writes, Discord messages |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/post_engine_observers.py` |
| Reason | 6 independent subsystems all following same pattern (try/except: pass). Can be a loop over registered observers. |
| Coupling | Low |
| Cohesion | Low (each is independent) |
| Risk | Low |
| Priority | High |

---

### R10: Decision Trace + Funnel

| Aspect | Detail |
|--------|--------|
| Purpose | Build diagnostic trace, persist, update funnel |
| Functions | Lines 1035–1047 |
| Internal deps | `_new_result`, `_runtime_session_id`, `_decision_funnel` |
| External deps | `build_decision_trace`, `persist_decision_trace`, `DecisionFunnel` |
| Inputs | Engine result, runtime_session_id, pattern count |
| Outputs | Persisted trace, funnel update |
| State mutated | `_decision_funnel` |
| Side effects | Trace persistence (local + S3) |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/post_engine_observers.py` (as one of the observers) |
| Reason | Self-contained observation with no orchestration coupling |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | High |

---

### R11: NO_TRADE Path Handling

| Aspect | Detail |
|--------|--------|
| Purpose | Record rejection, audit, narrative, filter-hit update, shadow pipeline |
| Functions | Lines 1049–1143 |
| Internal deps | `_filter_hits`, `_cycle_decision`, `_new_result` |
| External deps | `trade_narrative`, `output_router`, `decision_audit`, `paper_outcome_engine`, old `process_bar` |
| Inputs | Engine result, candles, engine_state |
| Outputs | Finalized decision, audit record, narratives |
| State mutated | `_filter_hits`, `_cycle_decision` |
| Side effects | File writes, Discord, legacy pipeline execution |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/decision_handler.py` |
| Reason | Decision recording + narrative + routing is outcome handling, not orchestration |
| Coupling | Medium |
| Cohesion | Medium |
| Risk | Medium |
| Priority | High |

---

### R12: EXECUTE Path Pre-Execution Setup

| Aspect | Detail |
|--------|--------|
| Purpose | Generate correlation_id, audit, execution context, shadow trade open |
| Functions | Lines 1145–1270 |
| Internal deps | `_cor_id`, `_decision_id`, `_new_engine_intent` |
| External deps | `generate_correlation_id`, `persist_new_engine_decision_audit`, `build_execution_context`, `shadow_trades` |
| Inputs | Engine result, bid/ask, candles, closed_time |
| Outputs | Correlation ID, decision audit record, execution context, shadow trade |
| State mutated | `_new_pipeline_handled`, `_cor_id`, `_decision_id` |
| Side effects | 4 persistence writes |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/decision_handler.py` (EXECUTE branch) |
| Reason | Pre-execution setup is distinct from orchestration |
| Coupling | Medium |
| Cohesion | High |
| Risk | Medium |
| Priority | High |

---

### R13: Runtime Risk Guards (10 sequential checks)

| Aspect | Detail |
|--------|--------|
| Purpose | Post-engine risk validation before broker execution |
| Functions | Lines 1870–2190 (~320 lines) |
| Internal deps | `_filter_hits`, `_decision_funnel`, `_cycle_decision` |
| External deps | 10 guard modules (daily_trade_limit, cooldown, correlation, exposure, regime, challenge, consistency, prop_firm, weekend, control_layer) |
| Inputs | Intent, symbol, bid/ask, account state |
| Outputs | RISK_BLOCK decision or pass-through |
| State mutated | `_filter_hits`, `_decision_funnel`, `_cycle_decision` |
| Side effects | Risk guard events, Discord alerts, decision finalization |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/runtime_guard_chain.py` |
| Reason | 10 independent guard checks following identical pattern. Can be a guard chain with early-exit. |
| Coupling | Low (each guard is independent) |
| Cohesion | High (all serve same purpose) |
| Risk | Low |
| Priority | Critical |

---

### R14: Broker Execution

| Aspect | Detail |
|--------|--------|
| Purpose | Send order to MT5, persist result |
| Functions | Lines 2192–2250 |
| Internal deps | `execution`, `_decision_id`, `_cor_id` |
| External deps | `MT5Execution.execute()`, `persist_execution_result` |
| Inputs | OrderIntent, decision metadata |
| Outputs | Execution result (ok, retcode, deal, fill_price) |
| State mutated | Broker state (order placed) |
| Side effects | Real money at risk, execution result persistence |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/execution_handler.py` |
| Reason | Execution is a critical boundary requiring isolation and clear interface |
| Coupling | Low |
| Cohesion | High |
| Risk | Medium |
| Priority | High |

---

### R15: Trade Lifecycle Registration

| Aspect | Detail |
|--------|--------|
| Purpose | Register fill in TradeStateManager, record slippage, assign cohort, emit events |
| Functions | Lines 2252–2345 |
| Internal deps | `sym_state.trade_manager`, `_daily_trade_limit`, `_cycle_decision` |
| External deps | `TradeStateManager`, `record_slippage`, `paper_outcome_engine`, `cohort_analysis`, `emit_trade_events` |
| Inputs | Execution result, intent, engine result |
| Outputs | Registered trade, slippage record, cohort assignment |
| State mutated | Trade manager, daily trade limit, engine state |
| Side effects | Trade registration, slippage monitoring, Discord |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/execution_handler.py` (post-fill section) |
| Reason | Trade registration is post-execution lifecycle, not orchestration |
| Coupling | Medium |
| Cohesion | High |
| Risk | Medium |
| Priority | High |

---

### R16: Post-Cycle Diagnostics (Ranking, Summary, Funnel, Snapshot)

| Aspect | Detail |
|--------|--------|
| Purpose | End-of-cycle reporting: opportunity ranking, pipeline trace, decision funnel display, market snapshot |
| Functions | Lines 2394–2570 |
| Internal deps | `_cycle_candidates`, `_cycle_drops`, `_decision_funnel`, `_filter_hits`, `_score_tracker` |
| External deps | `opportunity_ranker`, `log_cycle_summary_simple`, Discord, `get_dashboard_metrics` |
| Inputs | Cycle results, diagnostic accumulators |
| Outputs | Console output, Discord messages |
| State mutated | `consecutive_no_trade_cycles` |
| Side effects | Console prints, Discord messages |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/cycle_diagnostics.py` |
| Reason | Reporting is pure observation — no coupling to pipeline |
| Coupling | Low |
| Cohesion | Medium |
| Risk | Low |
| Priority | Low |

---

### R17: Heartbeat + Liveness + Health Monitoring

| Aspect | Detail |
|--------|--------|
| Purpose | Emit heartbeat, detect stalls, periodic reconciliation, risk timeline |
| Functions | Lines 2514–2600 |
| Internal deps | `cycle_start`, `states`, `mt5_state` |
| External deps | `log_heartbeat`, `_write_heartbeat`, `log_liveness_status`, `reconcile_state_sanity`, `risk_timeline` |
| Inputs | Cycle timing, states |
| Outputs | Heartbeat file, liveness classification |
| State mutated | `last_reconcile_time`, filesystem |
| Side effects | Heartbeat file write, reconciliation |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/health_monitor.py` |
| Reason | Health monitoring is independent of strategy/execution |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | Low |

---

### R18: State Checkpoint + Ledger Flush

| Aspect | Detail |
|--------|--------|
| Purpose | Periodic persistence of engine state and ledger buffer |
| Functions | Lines 2602–2617 |
| Internal deps | `states`, `_ledger` |
| External deps | `save_engine_states`, `_ledger.tick()` |
| Inputs | Cycle count, states |
| Outputs | Persisted state files |
| State mutated | Filesystem, ledger buffer |
| Side effects | File writes |
| Belongs in live_scanner? | ✅ YES (part of cycle housekeeping) |
| Coupling | Low |
| Cohesion | Medium |

---

### R19: Shutdown / Cleanup

| Aspect | Detail |
|--------|--------|
| Purpose | Disconnect feeds, flush ledger, save states, emit calibration summary |
| Functions | Lines 2621–2721 |
| Internal deps | `states`, `_ledger` |
| External deps | `MT5DataFeed.disconnect()`, `save_engine_states`, `_ledger.flush()`, `mtf_calibration` |
| Inputs | All states |
| Outputs | Clean shutdown |
| State mutated | Feed connections (closed) |
| Side effects | Final file writes |
| Belongs in live_scanner? | ✅ YES (orchestrator owns lifecycle) |
| Coupling | Low |
| Cohesion | High |

---

### R20: Old Pipeline / Shadow Mode Execution

| Aspect | Detail |
|--------|--------|
| Purpose | Run legacy pipeline for comparison or fallback |
| Functions | Lines 1317–1570 (~250 lines) |
| Internal deps | `sym_state.engine_state`, `_new_pipeline_handled` |
| External deps | `core.engine.process_bar()`, `validate_engine_state`, `TimeframeCache`, `core.timeframes.calibration` |
| Inputs | Candles, engine state (copy), HTF context |
| Outputs | `unified` decision, divergence logs |
| State mutated | `_old_pipeline_state` copy (when shadow), real state (when authority) |
| Side effects | Calibration records, divergence logs |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/shadow_pipeline.py` |
| Reason | Shadow mode is an independent subsystem for comparison/validation |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | Low |

---

### R21: Execution Context Persistence (Per-Cycle)

| Aspect | Detail |
|--------|--------|
| Purpose | Capture environment snapshot (spread, session, latency) every new bar |
| Functions | Lines 700–755 |
| Internal deps | `_cor_id_cycle` |
| External deps | `generate_correlation_id`, `build_execution_context`, `persist_execution_context` |
| Inputs | Bid, ask, closed_time, feed state, guard states |
| Outputs | Execution context record |
| State mutated | Filesystem |
| Side effects | S3 persistence |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/post_engine_observers.py` or `core/runtime/data_acquisition.py` |
| Reason | Environment capture is observational — no coupling to decision logic |
| Coupling | Low |
| Cohesion | Medium |
| Risk | Low |
| Priority | Medium |

---

### R22: Error Handling (New Engine Exception + Outer Exception)

| Aspect | Detail |
|--------|--------|
| Purpose | Catch engine crashes, log, persist evidence, decide fallback |
| Functions | Lines 1272–1315, 2380–2392 |
| Internal deps | `_cycle_decision`, `_new_pipeline_handled` |
| External deps | `log_runtime_exception`, Discord |
| Inputs | Exception object |
| Outputs | Fallback decision or blocked cycle |
| State mutated | `_cycle_decision`, `_new_pipeline_handled` |
| Side effects | Error logs, Discord alerts |
| Belongs in live_scanner? | Partially (outer handler YES, inner handler could be in decision_handler) |
| Coupling | Medium |
| Cohesion | Medium |
| Risk | Medium |
| Priority | Medium |

---

### R23: Trade Management (Tick-Level Updates)

| Aspect | Detail |
|--------|--------|
| Purpose | Update open positions on price ticks (SL/TP management) |
| Functions | Lines 562–569 |
| Internal deps | `sym_state.trade_manager` |
| External deps | `TradeStateManager.on_price_update()`, retry queues |
| Inputs | Bid, ask, time |
| Outputs | Updated stop/target levels |
| State mutated | Trade manager internal state |
| Side effects | Broker order modifications (if TRADE_MANAGEMENT_ENABLED) |
| Belongs in live_scanner? | ❌ NO |
| Destination | `core/runtime/trade_management_driver.py` |
| Reason | Position management is independent of signal generation |
| Coupling | Low |
| Cohesion | High |
| Risk | Low |
| Priority | Low |

---

## 2. DEPENDENCY MAP

```
                    ┌─────────────────────────────┐
                    │     LIVE_SCANNER (orchestrator)     │
                    └─────────────────┬───────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
    ┌─────▼─────┐             ┌──────▼──────┐             ┌─────▼─────┐
    │ STARTUP   │             │ MAIN LOOP   │             │ SHUTDOWN  │
    │           │             │             │             │           │
    │ R1: init  │             │ R2: cycle   │             │ R19: clean│
    └─────┬─────┘             └──────┬──────┘             └───────────┘
          │                          │
          │         ┌────────────────┼────────────────────────────┐
          │         │                │                            │
    ┌─────▼─────┐  ┌▼───────────┐  ┌▼───────────────┐   ┌──────▼──────┐
    │ PER-CYCLE │  │ SYSTEM     │  │ PER-SYMBOL     │   │ POST-CYCLE  │
    │ GUARDS    │  │ HEALTH     │  │ PIPELINE       │   │ DIAGNOSTICS │
    │           │  │            │  │                │   │             │
    │ R4: guard │  │ R3: mt5    │  │ R5: data       │   │ R16: report │
    │           │  │ R17: heart │  │ R7: gates      │   │ R18: flush  │
    └───────────┘  └────────────┘  │ R8: engine     │   └─────────────┘
                                   │ R9: observers  │
                                   │ R10: trace     │
                                   │ R11: NO_TRADE  │
                                   │ R12: EXECUTE   │
                                   │ R13: guards    │
                                   │ R14: execute   │
                                   │ R15: lifecycle │
                                   │ R20: shadow    │
                                   │ R21: context   │
                                   │ R23: trade_mgmt│
                                   └────────────────┘
```

### Data Flow (Per-Symbol, Normal Path)

```
tick → stale check → candles → bar dedup → shadow eval → context persist
  ↓
pattern gate → engine call → bias FSM → observers → trace
  ↓
[NO_TRADE] → audit → ledger → finalize
  ↓
[EXECUTE] → correlation_id → audit → context → shadow trade open
  ↓
runtime guards (10 checks) → [BLOCK] → finalize
  ↓
[PASS] → MT5 execute → result persist → trade register → finalize
```

---

## 3. LOGICAL SUBSYSTEM GROUPING

| Subsystem | Responsibilities | Proposed Module | Lines Recovered |
|-----------|-----------------|-----------------|-----------------|
| **Runtime Orchestration** | R1, R2, R8, R18, R19 | `live_scanner.py` (remains) | ~300 |
| **MT5 Health** | R3 | `core/runtime/mt5_health_manager.py` | ~40 |
| **System Guards** | R4 | `core/runtime/system_guards.py` | ~50 |
| **Data Acquisition** | R5, R21 | `core/runtime/data_acquisition.py` | ~250 |
| **Pre-Engine Gates** | R7 | `core/runtime/pre_engine_gates.py` | ~60 |
| **Post-Engine Observers** | R9, R10 | `core/runtime/post_engine_observers.py` | ~100 |
| **Decision Handling** | R6, R11, R12, R22 | `core/runtime/decision_handler.py` | ~350 |
| **Runtime Guard Chain** | R13 | `core/runtime/runtime_guard_chain.py` | ~320 |
| **Execution** | R14, R15 | `core/runtime/execution_handler.py` | ~200 |
| **Diagnostics** | R16 | `core/runtime/cycle_diagnostics.py` | ~180 |
| **Health Monitoring** | R17 | `core/runtime/health_monitor.py` | ~90 |
| **Shadow Pipeline** | R20 | `core/runtime/shadow_pipeline.py` | ~250 |
| **Trade Management** | R23 | `core/runtime/trade_management_driver.py` | ~10 (call site) |

---

## 4. TARGET ARCHITECTURE

### `live_scanner.py` (target: ~300 lines)

The orchestrator should contain ONLY:

```
1. Startup
   - Parse config, resolve symbols
   - Construct all subsystem instances
   - Wire dependencies

2. Main Loop
   - Increment cycle_id
   - Check shutdown
   - Classify runtime gaps
   - Call system_guards.evaluate_cycle()
   - For each symbol:
     - Call data_acquisition.fetch_bar()
     - If no new bar: continue
     - Call trade_management.tick_update()
     - Call pre_engine_gates.evaluate()
     - Call engine (run_new_engine)
     - Call post_engine_observers.notify_all()
     - Call decision_handler.handle_result()
     - If EXECUTE:
       - Call runtime_guard_chain.evaluate()
       - Call execution_handler.execute()
   - Call cycle_diagnostics.report()
   - Call health_monitor.tick()
   - Call ledger.tick()
   - Sleep

3. Shutdown
   - Flush ledger
   - Save states
   - Disconnect feeds
```

### Interface Contracts (proposed)

| Subsystem | Input | Output |
|-----------|-------|--------|
| `system_guards` | account state, time | `CyclePermission(allowed, blocked_reason)` |
| `data_acquisition` | symbol_state, config | `BarResult(bid, ask, candles, closed_i, is_new, feed_state)` |
| `pre_engine_gates` | kill_switch, daily_loss, session, patterns | `GateResult(passed, decision_outcome, patterns)` |
| `post_engine_observers` | engine_result, context | None (fire-and-forget) |
| `decision_handler` | engine_result, context | `DecisionOutcome(action, correlation_id, decision_id)` |
| `runtime_guard_chain` | intent, symbol, context | `GuardResult(allowed, block_reason, guard_name)` |
| `execution_handler` | intent, correlation_id, decision_id | `ExecutionOutcome(success, fill_price, trade_registered)` |
| `cycle_diagnostics` | candidates, drops, funnel | None (prints/emits) |
| `health_monitor` | cycle_id, latency, states | None (heartbeat/liveness) |

---

## 5. EXTRACTION PRIORITY

| Priority | Module | Lines Recovered | Risk | Rationale |
|----------|--------|-----------------|------|-----------|
| **Critical** | `runtime_guard_chain.py` | ~320 | Low | 10 independent guards, identical pattern, zero coupling to strategy |
| **High** | `decision_handler.py` | ~350 | Medium | NO_TRADE + EXECUTE paths are outcome handling, not orchestration |
| **High** | `data_acquisition.py` | ~250 | Low | Mechanical fetch/validate with no strategy coupling |
| **High** | `execution_handler.py` | ~200 | Medium | Execution is a critical boundary needing isolation |
| **High** | `post_engine_observers.py` | ~100 | Low | 8 independent try/except:pass observers |
| **Medium** | `shadow_pipeline.py` | ~250 | Low | Entire shadow mode is self-contained |
| **Medium** | `cycle_diagnostics.py` | ~180 | Low | Pure reporting |
| **Medium** | `pre_engine_gates.py` | ~60 | Medium | Gate checks are rule evaluation |
| **Medium** | `system_guards.py` | ~50 | Low | Simple guard checks |
| **Medium** | `mt5_health_manager.py` | ~40 | Low | Self-contained reconnect logic |
| **Low** | `health_monitor.py` | ~90 | Low | Independent monitoring |
| **Low** | `trade_management_driver.py` | ~10 | Low | Single call delegation |

**Total recoverable:** ~1,900 lines (70% of file)  
**Remaining in live_scanner.py:** ~300 lines (orchestration core)

---

## 6. COUPLING ANALYSIS

### High Coupling (extraction requires careful interface design)

| From → To | Shared State | Interface Needed |
|-----------|-------------|-----------------|
| decision_handler → _cycle_decision | Mutable dict | DecisionRecorder with typed methods |
| execution_handler → _cycle_decision | Mutable dict | Same |
| runtime_guard_chain → _filter_hits, _decision_funnel | Mutable counters | Guard result callback |
| decision_handler → _new_result dict | Untyped dict | Engine result as typed dataclass |

### Low Coupling (clean extraction boundaries)

- data_acquisition → only needs symbol_state + config
- post_engine_observers → only needs engine_result (read-only)
- cycle_diagnostics → only needs accumulators (read-only)
- shadow_pipeline → only needs candles + config
- health_monitor → only needs timing + states
- system_guards → only needs guard instances

---

## 7. RISK ASSESSMENT

| Risk | Description | Mitigation |
|------|-------------|-----------|
| Behaviour regression | Extraction could alter execution order or exception handling | Extract one module at a time, run full test suite after each |
| State sharing | `_cycle_decision` dict is mutated by 5+ responsibilities | Replace with typed DecisionRecorder class with explicit API |
| Exception semantics | Nested try/except:pass blocks define fallback behaviour | Preserve exact same exception boundaries in extracted modules |
| Import cycle risk | Some responsibilities use lazy imports (inline `from ... import`) | Extracted modules must handle their own lazy imports |
| Test coverage | No unit tests for most individual responsibilities | Extract with integration tests first, add unit tests to new modules |

---

*End of audit. No code was modified. This document describes the target architecture only.*
