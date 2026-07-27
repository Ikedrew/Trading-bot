# ARCHITECTURAL OWNERSHIP & ROUTING ANALYSIS

**Generated:** 2026-07-17  
**Baseline:** `LIVE_SCANNER_RESPONSIBILITY_AUDIT.md` (established conclusions)  
**Purpose:** Determine the exact destination for every extracted responsibility

---

## EXISTING PACKAGE STRUCTURE (current state)

```
Trading bot build/
├── core/                          # Core domain logic + infrastructure
│   ├── runtime/                   # Runtime loop + lifecycle
│   ├── pipeline/                  # Decision pipeline (engine, policy, EV, scoring)
│   ├── persistence/               # Persistence writers
│   ├── models/                    # Domain models (OpportunityAssessment)
│   ├── storage/                   # S3 batch writer
│   ├── timeframes/                # MTF system
│   ├── [various .py modules]      # Domain services (correlation, audit, ledger, etc.)
│   └── ...
├── data/                          # Market data feed (MT5DataFeed)
├── execution/                     # Broker execution (MT5Execution)
├── risk/                          # Risk management (guards, sizing, SL/TP)
├── strategy/                      # Strategy classification + pattern detection
├── patterns/                      # Pattern recognition implementations
├── data_pipeline/                 # Offline analytics (query layer, Glue)
├── analysis/                      # Offline analysis scripts
├── tests/                         # Test suite
└── tools/                         # Development utilities
```

---

## ARCHITECTURAL PRINCIPLES APPLIED

1. **Dependency Direction:** Runtime → Domain → Infrastructure (never reverse)
2. **Domain Ownership:** Each package owns one domain concept entirely
3. **Lifecycle Boundary:** Objects with different lifecycles belong in different modules
4. **Runtime vs Domain:** Orchestration logic (when/how to call) is separate from domain logic (what to compute)
5. **Existing Structure Respect:** Use existing packages where they already own the domain
6. **Minimal New Packages:** Only create new modules within existing packages — no new top-level packages

---

## ROUTING TABLE

| # | Responsibility | Current Location | Dept | Module | New? | Justification | Incoming | Outgoing | Confidence |
|---|---------------|-----------------|------|--------|------|--------------|----------|----------|-----------|
| R3 | MT5 Connection Health | `live_scanner.py:381-420` | Runtime | `core/runtime/mt5_health.py` | **New** | Reconnect lifecycle with backoff is self-contained state machine. No coupling to strategy/data. Belongs in runtime because it governs the connection that all data flows through. | live_scanner (orchestrator only) | `core/mt5_connection`, `MetaTrader5`, `core/runtime/startup_recovery` | High |
| R4 | System-Level Guards | `live_scanner.py:422-474` | Runtime | `core/runtime/cycle_guards.py` | **New** | Cycle-level permission checks (drawdown, daily loss, daily reset, kill switch) form a pre-evaluation gate. Lifecycle = once per cycle, before any symbol processing. Distinct from per-trade guards (R13). | live_scanner (orchestrator only) | `risk/drawdown_guard`, `risk/daily_loss_guard`, `core/kill_switch`, `core/daily_reset` | High |
| R5 | Data Acquisition | `live_scanner.py:484-680` | Runtime | `core/runtime/bar_provider.py` | **New** | Tick fetch + candle fetch + stale detection + bar dedup + feed health classification are all "get data ready for evaluation." Mechanical data preparation — no analytical logic. Named `bar_provider` because it answers: "Is there a new bar to evaluate?" | live_scanner per-symbol loop | `data/mt5_data`, `core/stale_monitor`, `core/runtime/runtime_utils` | High |
| R6 | Decision Recording | `live_scanner.py:757-845` | Persistence | `core/runtime/decision_recorder.py` | **New** | Owns the `_cycle_decision` lifecycle: init → mutate → finalize → persist. Separation from orchestration because the recording concern (what fields, invariants, idempotency) is independent of when decisions happen. | decision_handler, execution_handler, pre_engine_gates | `core/decision_ledger` | Medium |
| R7 | Pre-Engine Gates | `live_scanner.py:847-905` | Runtime | `core/runtime/pre_engine_gates.py` | **New** | Sequential gate checks before engine evaluation (kill switch, daily loss, session, pattern detection). Each produces an early-exit decision. Lifecycle = per-symbol, before engine call. | live_scanner per-symbol loop | `risk/session_guard`, `strategy/signal_orchestrator`, `core/pipeline/paper_outcome_engine` | High |
| R9 | Post-Engine Observers | `live_scanner.py:962-1033` | Pipeline | `core/pipeline/observers.py` | **New** | 8 independent fire-and-forget observers (bias FSM, event observer, forensic logger, entity tracker, visibility layer, shadow rooms, decision trace, score tracker). All follow identical pattern. Belongs in `pipeline/` because they observe pipeline output. | live_scanner (called after engine) | `core/pipeline/bias_fsm`, `core/pipeline/event_observer`, `core/pipeline/forensic_logger`, `core/pipeline/entity_tracker`, `core/pipeline/visibility_layer`, `core/pipeline/shadow_rooms`, `core/decision_trace` | High |
| R10 | Decision Trace | `live_scanner.py:1035-1047` | Pipeline | `core/pipeline/observers.py` | **Existing** (merged into R9) | Decision trace is architecturally identical to other observers — fire-and-forget, reads engine_result, produces persisted record. Should be registered as one observer among many. | observer registry | `core/decision_trace` | High |
| R11 | NO_TRADE Handling | `live_scanner.py:1049-1143` | Runtime | `core/runtime/decision_handler.py` | **New** | Handles the NO_TRADE outcome: audit recording, narrative generation, output routing, filter hit updates, shadow pipeline comparison. Outcome handling is a distinct domain from orchestration. | live_scanner (when action=NO_TRADE) | `core/decision_audit`, `core/pipeline/trade_narrative`, `core/pipeline/output_router`, decision_recorder | High |
| R12 | EXECUTE Pre-Setup | `live_scanner.py:1145-1270` | Runtime | `core/runtime/decision_handler.py` | **Same as R11** | The EXECUTE branch (correlation_id generation, decision audit, execution context, shadow trade open) is the other outcome of the same decision fork. Same module, different method. | live_scanner (when action=EXECUTE) | `core/correlation`, `core/decision_audit`, `core/execution_context`, `core/shadow_trades` | High |
| R13 | Runtime Guard Chain | `live_scanner.py:1870-2190` | Risk | `risk/runtime_guard_chain.py` | **New** | 10 sequential per-trade risk checks. Belongs in `risk/` because it IS risk management — evaluating whether a specific trade meets risk constraints. The individual guards already live in `risk/`. The chain that sequences them should too. | decision_handler (EXECUTE path) | All `risk/*_guard.py` modules, `core/challenge_progress_tracker`, `core/consistency_rules`, `core/prop_firm_rules`, `core/weekend_protection`, `core/pipeline/control_layer` | High |
| R14 | Broker Execution | `live_scanner.py:2192-2250` | Execution | `execution/execution_orchestrator.py` | **New** | Order send + result persistence. Belongs in `execution/` because it IS the execution concern — sending orders and recording results. The existing `execution/mt5_execution.py` handles the MT5 API call; this module handles the higher-level orchestration (timing, metadata injection, result recording). | decision_handler (after guards pass) | `execution/mt5_execution`, `core/persistence/execution_result_writer` | High |
| R15 | Trade Lifecycle Registration | `live_scanner.py:2252-2345` | Execution | `execution/execution_orchestrator.py` | **Same as R14** | Post-fill registration (TradeStateManager, slippage, daily counter, cohort assignment) is the completion phase of execution orchestration. Same lifecycle as R14 — they form a single unit: attempt → record → register. | Called after successful R14 | `core/trade_management/TradeStateManager`, `core/slippage_monitor`, `risk/daily_trade_limit`, `core/pipeline/paper_outcome_engine` | High |
| R16 | Post-Cycle Diagnostics | `live_scanner.py:2394-2570` | Pipeline | `core/pipeline/cycle_report.py` | **New** | End-of-cycle reporting (ranking, summary, funnel display, market snapshot, no-trade alerts). Belongs in `pipeline/` because it reports on pipeline outcomes. Not in `runtime/` because it has no runtime control logic. | live_scanner (end of cycle) | `core/pipeline/opportunity_ranker`, `core/pipeline/dashboard`, `core/decision_trace.DecisionFunnel`, `core/quiet_period_diagnostics` | High |
| R17 | Health Monitoring | `live_scanner.py:2514-2600` | Runtime | `core/runtime/health_monitor.py` | **New** | Heartbeat, liveness classification, periodic reconciliation, risk timeline snapshots. Belongs in `runtime/` because it monitors runtime health — not pipeline outcomes. | live_scanner (end of cycle) | `core/heartbeat`, `core/event_bus`, `core/mt5_connection.reconcile_state_sanity`, `risk/risk_timeline` | High |
| R20 | Shadow Pipeline | `live_scanner.py:1317-1570` | Pipeline | `core/pipeline/shadow_pipeline.py` | **New** | Runs legacy engine on state copies for comparison. Belongs in `pipeline/` because it IS a pipeline — just running in parallel/shadow. Not in `runtime/` because it computes analytical decisions. | live_scanner (after engine, when enabled) | `core/engine.process_bar()`, `core/engine_state.validate_engine_state`, `core/timeframes/` | Medium |
| R21 | Per-Cycle Execution Context | `live_scanner.py:700-755` | Persistence | `core/runtime/bar_provider.py` | **Merged into R5** | Environment snapshot capture belongs with data acquisition — it captures the market environment state at bar-open time. Same lifecycle moment (new bar detected → capture context). | bar_provider output | `core/correlation`, `core/execution_context` | Medium |
| R22 | Error Handling | `live_scanner.py:1272-1315, 2380-2392` | Runtime | Split: inner → `core/runtime/decision_handler.py`, outer → stays in live_scanner | **Split** | Inner exception (engine crash) is outcome handling. Outer exception (unhandled per-symbol failure) is orchestrator's last-resort boundary. | — | `core/event_bus.log_runtime_exception`, Discord | Medium |
| R23 | Trade Management | `live_scanner.py:562-569` | Trade Mgmt | `core/trade_management/tick_driver.py` | **New** | Tick-level position management (SL/TP updates, retry queues). Belongs in existing `core/trade_management/` package because that package already owns trade state. | live_scanner per-tick (or bar_provider) | `core/trade_management/TradeStateManager` | High |

---

## DEPARTMENT STRUCTURE (proposed)

```
Trading bot build/
│
├── core/
│   ├── runtime/                         ← RUNTIME COORDINATION DEPT
│   │   ├── live_scanner.py              (orchestrator — 300 lines target)
│   │   ├── mt5_health.py               ← R3: Connection lifecycle
│   │   ├── cycle_guards.py             ← R4: System-level pre-cycle gates
│   │   ├── bar_provider.py             ← R5+R21: Data fetch + environment capture
│   │   ├── pre_engine_gates.py         ← R7: Pre-engine per-symbol gates
│   │   ├── decision_handler.py         ← R11+R12+R22(inner): Outcome handling
│   │   ├── decision_recorder.py        ← R6: Decision lifecycle (init→mutate→finalize)
│   │   ├── health_monitor.py           ← R17: Heartbeat + liveness + reconciliation
│   │   ├── runtime_utils.py            (existing — utility functions)
│   │   ├── risk_event_emitter.py       (existing — risk event emission)
│   │   ├── shutdown.py                 (existing — graceful shutdown)
│   │   ├── startup_recovery.py         (existing — position recovery)
│   │   ├── instance_lock.py            (existing — single instance)
│   │   └── replay_scanner.py           (existing — replay mode)
│   │
│   ├── pipeline/                        ← DECISION PIPELINE DEPT
│   │   ├── new_engine.py               (existing — strategy engine, untouched)
│   │   ├── observers.py                ← R9+R10: Post-engine observer registry
│   │   ├── shadow_pipeline.py          ← R20: Legacy comparison pipeline
│   │   ├── cycle_report.py             ← R16: Post-cycle diagnostics
│   │   ├── [existing modules...]       (execution_policy, expected_value, etc.)
│   │   └── ...
│   │
│   ├── persistence/                     ← PERSISTENCE DEPT (existing)
│   │   ├── opportunity_assessment_writer.py  (existing)
│   │   ├── execution_result_writer.py        (existing)
│   │   └── decision_trace_writer.py          (existing — legacy, unused)
│   │
│   ├── trade_management/                ← TRADE MANAGEMENT DEPT (existing)
│   │   ├── tick_driver.py              ← R23: Tick-level position updates
│   │   └── [existing modules...]
│   │
│   └── [other existing packages unchanged]
│
├── risk/                                ← RISK MANAGEMENT DEPT (existing)
│   ├── runtime_guard_chain.py          ← R13: 10-guard sequential chain
│   ├── [existing guard modules...]      (daily_trade_limit, cooldown, correlation, etc.)
│   └── ...
│
├── execution/                           ← EXECUTION DEPT (existing)
│   ├── mt5_execution.py                (existing — low-level MT5 API)
│   ├── execution_orchestrator.py       ← R14+R15: High-level execute + register
│   └── ...
│
└── [other packages unchanged]
```

---

## OWNERSHIP JUSTIFICATION (per module)

### `core/runtime/mt5_health.py` (R3)

**Why here:** Connection health is a runtime infrastructure concern. It governs whether the system CAN operate, not what it should do. It has no coupling to strategy, data, or decisions.

**Why not `core/mt5_connection.py`:** That module provides stateless utility functions (`attempt_reconnect`, `is_mt5_healthy`). The health manager adds stateful lifecycle management (backoff state, fail counter) which is runtime-specific.

**Interface:**
```
class MT5HealthManager:
    check_and_reconnect(states) → ConnectionStatus
    on_disconnect() → None
    on_reconnect_success(states) → None
```

**Allowed callers:** live_scanner ONLY  
**Dependencies:** `core/mt5_connection`, `core/runtime/startup_recovery`  
**Depends on it:** Nothing (leaf node)

---

### `core/runtime/cycle_guards.py` (R4)

**Why here:** Cycle-level guards determine whether the ENTIRE cycle should proceed. They operate at the orchestration boundary — above per-symbol logic. They are runtime permission checks, not risk calculations.

**Why not `risk/`:** These are not per-trade risk decisions. They block entire evaluation cycles based on account-level state. The individual guard implementations (`DrawdownGuard`, `DailyLossGuard`) correctly live in `risk/`. This module composes them into a cycle-level permission check.

**Interface:**
```
class CycleGuards:
    evaluate() → CyclePermission(allowed, daily_loss_blocked, kill_active)
    reset_daily() → None
```

**Allowed callers:** live_scanner ONLY  
**Dependencies:** `risk/drawdown_guard`, `risk/daily_loss_guard`, `core/kill_switch`, `core/daily_reset`  
**Depends on it:** Nothing

---

### `core/runtime/bar_provider.py` (R5 + R21)

**Why here:** Data acquisition is a runtime concern — it bridges the broker data source into the evaluation pipeline. It answers: "Is there a new bar to evaluate, and what does the environment look like?"

**Why not `data/`:** The `data/` package owns the MT5 API abstraction. `bar_provider` owns the runtime logic around it: dedup, stale detection, feed health classification, environment context capture. These are runtime decisions about data, not data access itself.

**Interface:**
```
class BarProvider:
    fetch_bar(sym_state, config) → BarResult | None
    # BarResult = (bid, ask, candles, closed_i, closed_time, feed_state, is_new_bar)
    capture_environment(sym_state, bid, ask, closed_time, guards) → None
```

**Allowed callers:** live_scanner per-symbol loop  
**Dependencies:** `data/mt5_data`, `core/stale_monitor`, `core/runtime/runtime_utils`, `core/execution_context`  
**Depends on it:** Nothing

---

### `core/runtime/pre_engine_gates.py` (R7)

**Why here:** Pre-engine gates are runtime permission checks applied per-symbol before the expensive engine call. They prevent wasted computation.

**Why not `risk/`:** Pattern detection is not a risk check. Session guard is borderline but the orchestration of "check session → check pattern → decide whether to call engine" is runtime coordination.

**Interface:**
```
def evaluate_pre_engine_gates(
    kill_active, daily_loss_blocked, candles, closed_i, config
) → PreEngineResult(proceed, patterns, decision_outcome, reason)
```

**Allowed callers:** live_scanner per-symbol loop  
**Dependencies:** `risk/session_guard`, `strategy/signal_orchestrator`, `core/pipeline/paper_outcome_engine`  
**Depends on it:** Nothing

---

### `core/pipeline/observers.py` (R9 + R10)

**Why here:** Post-engine observers react to pipeline output. They ARE pipeline components — they observe and record pipeline decisions. They belong with other pipeline modules.

**Why not `core/runtime/`:** Observers don't make runtime decisions. They process analytical output. Their dependencies are all pipeline modules (`bias_fsm`, `event_observer`, `forensic_logger`, etc.).

**Interface:**
```
class ObserverRegistry:
    register(observer: PipelineObserver) → None
    notify_all(engine_result, context) → None
    # Each observer: try/except: pass (fire-and-forget)
```

**Allowed callers:** live_scanner (via `notify_all`), configuration (via `register`)  
**Dependencies:** `core/pipeline/bias_fsm`, `core/pipeline/event_observer`, `core/pipeline/forensic_logger`, `core/pipeline/entity_tracker`, `core/pipeline/visibility_layer`, `core/pipeline/shadow_rooms`, `core/decision_trace`  
**Depends on it:** Nothing

---

### `core/runtime/decision_handler.py` (R11 + R12 + R22-inner)

**Why here:** Handles the outcome of the engine evaluation. Regardless of whether the result is NO_TRADE or EXECUTE, there is decision recording, audit, narrative, and routing work to do. This is runtime outcome handling — not orchestration and not pipeline computation.

**Why not `core/pipeline/`:** The pipeline computes the decision. This module handles what happens after the decision is made — recording, routing, correlation_id generation. Different lifecycle phase.

**Interface:**
```
class DecisionHandler:
    handle_no_trade(engine_result, context) → None
    handle_execute(engine_result, context) → ExecuteSetup(correlation_id, decision_id, intent)
    handle_engine_exception(exc, context) → None
```

**Allowed callers:** live_scanner ONLY  
**Dependencies:** `core/correlation`, `core/decision_audit`, `core/execution_context`, `core/shadow_trades`, `core/pipeline/trade_narrative`, `core/pipeline/output_router`, decision_recorder  
**Depends on it:** Nothing

---

### `core/runtime/decision_recorder.py` (R6)

**Why here:** Owns the decision lifecycle — initialization, mutation, finalization, persistence. This is a distinct concern from what decision was made or how it was made.

**Why not `core/decision_ledger.py`:** The ledger is the persistence mechanism. The recorder is the lifecycle manager that ensures every cycle produces exactly one record with correct invariants.

**Interface:**
```
class DecisionRecorder:
    init_cycle(symbol, cycle_id, regime, guards) → None
    mutate(**fields) → None
    finalize() → None  # idempotent, writes ledger entry
```

**Allowed callers:** decision_handler, pre_engine_gates, execution_orchestrator, live_scanner (fallback)  
**Dependencies:** `core/decision_ledger`  
**Depends on it:** Nothing

---

### `risk/runtime_guard_chain.py` (R13)

**Why here:** This IS risk management. It evaluates whether a specific trade meets 10 independent risk constraints. The individual guards already live in `risk/`. The chain that composes them into a sequential evaluation naturally belongs here too.

**Why not `core/runtime/`:** The chain doesn't make runtime decisions. It makes risk decisions. Its inputs are risk-domain (intent, exposure, positions) and its outputs are risk-domain (allow/block + reason).

**Interface:**
```
def evaluate_runtime_guards(
    intent: OrderIntent,
    symbol: str,
    context: GuardContext,
) → GuardChainResult(allowed, blocking_guard, reason)
```

**Allowed callers:** decision_handler (EXECUTE path), live_scanner  
**Dependencies:** `risk/daily_trade_limit`, `risk/trade_cooldown`, `risk/correlation_guard`, `risk/portfolio_exposure_guard`, `risk/regime_guard`, `risk/spread_guard`, `core/challenge_progress_tracker`, `core/consistency_rules`, `core/prop_firm_rules`, `core/weekend_protection`, `core/pipeline/control_layer`  
**Depends on it:** Nothing

---

### `execution/execution_orchestrator.py` (R14 + R15)

**Why here:** Execution orchestration is the execution package's responsibility. The existing `mt5_execution.py` is the low-level MT5 API wrapper. This module is the high-level "execute a trade decision" — including metadata injection, result persistence, and trade registration.

**Why not `core/runtime/`:** Execution is a domain concern, not a runtime concern. It answers "how to execute and register a trade" — regardless of which runtime (live, replay, test) is calling it.

**Interface:**
```
class ExecutionOrchestrator:
    execute_trade(
        intent, decision_metadata, trade_manager, config
    ) → ExecutionOutcome(success, fill_price, registered, slippage)
```

**Allowed callers:** decision_handler (via live_scanner), replay_scanner  
**Dependencies:** `execution/mt5_execution`, `core/persistence/execution_result_writer`, `core/trade_management/TradeStateManager`, `core/slippage_monitor`, `risk/daily_trade_limit`  
**Depends on it:** Nothing

---

### `core/pipeline/shadow_pipeline.py` (R20)

**Why here:** A shadow pipeline IS a pipeline — it runs the old engine on state copies for comparison. It produces analytical output. It belongs with other pipeline modules.

**Why not `core/runtime/`:** Shadow mode computes decisions. It doesn't orchestrate runtime. Its coupling is to `core/engine.process_bar()` and `core/timeframes/` — pipeline dependencies.

**Interface:**
```
def run_shadow_comparison(
    candles, closed_i, sym_state, config, htf_context, new_pipeline_handled
) → ShadowResult | None
```

**Allowed callers:** live_scanner (when `ENABLE_LEGACY_SHADOW_PIPELINE=True`)  
**Dependencies:** `core/engine`, `core/engine_state`, `core/timeframes/`  
**Depends on it:** Nothing

---

### `core/pipeline/cycle_report.py` (R16)

**Why here:** Reports on pipeline outcomes. Every input is a pipeline product (candidates, traces, funnel data). It belongs with other pipeline output modules.

**Interface:**
```
def emit_cycle_report(
    candidates, drops, funnel, filter_hits, score_tracker, cycle_id, had_trade
) → None
```

**Allowed callers:** live_scanner (end of cycle)  
**Dependencies:** `core/pipeline/opportunity_ranker`, `core/pipeline/dashboard`, `core/decision_trace.DecisionFunnel`, `core/quiet_period_diagnostics`  
**Depends on it:** Nothing

---

### `core/runtime/health_monitor.py` (R17)

**Why here:** Health monitoring is runtime infrastructure. It doesn't evaluate markets or decisions — it evaluates whether the system itself is operating correctly.

**Interface:**
```
class HealthMonitor:
    tick(cycle_id, latency_ms, states, mt5_state) → None
    reconcile_if_needed(states, trade_managers) → None
```

**Allowed callers:** live_scanner (end of cycle)  
**Dependencies:** `core/heartbeat`, `core/event_bus`, `core/mt5_connection`, `risk/risk_timeline`  
**Depends on it:** Nothing

---

### `core/trade_management/tick_driver.py` (R23)

**Why here:** `core/trade_management/` already owns TradeStateManager. The tick-level driver that calls `on_price_update()` and drains retry queues naturally belongs in the same package.

**Interface:**
```
def drive_tick_update(trade_manager, symbol, bid, ask, kill_active) → None
```

**Allowed callers:** bar_provider or live_scanner per-tick  
**Dependencies:** `core/trade_management/TradeStateManager`  
**Depends on it:** Nothing

---

## DEPENDENCY DIRECTION MAP

```
┌──────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR LAYER                          │
│                                                                    │
│   core/runtime/live_scanner.py (thin coordinator)                 │
│     ↓ calls into all subsystems, never called by them             │
└──────────────────────────┬───────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────────────┐
         │                 │                         │
         ▼                 ▼                         ▼
┌─────────────────┐ ┌─────────────────┐ ┌──────────────────────┐
│ RUNTIME INFRA   │ │ PIPELINE LAYER  │ │ RISK/EXECUTION LAYER │
│                 │ │                 │ │                      │
│ mt5_health      │ │ new_engine      │ │ risk/runtime_guard_  │
│ cycle_guards    │ │ observers       │ │   chain              │
│ bar_provider    │ │ shadow_pipeline │ │ execution/execution_ │
│ pre_engine_gates│ │ cycle_report    │ │   orchestrator       │
│ decision_handler│ │                 │ │                      │
│ decision_recorder│ │                │ │                      │
│ health_monitor  │ │                 │ │                      │
└────────┬────────┘ └────────┬────────┘ └──────────┬───────────┘
         │                   │                      │
         ▼                   ▼                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                        DOMAIN SERVICES                             │
│                                                                    │
│  core/decision_audit    core/decision_ledger    core/correlation  │
│  core/execution_context core/shadow_trades      core/event_stream │
│  core/stale_monitor     data/mt5_data           risk/*_guard      │
│  strategy/*             core/trade_management   core/persistence  │
└──────────────────────────────────────────────────────────────────┘
```

### Dependency Rules

1. **Orchestrator → Runtime Infra → Domain Services** (downward only)
2. **Orchestrator → Pipeline → Domain Services** (downward only)
3. **Orchestrator → Risk/Execution → Domain Services** (downward only)
4. **Runtime Infra modules do NOT call each other** (except decision_handler → decision_recorder)
5. **Pipeline modules do NOT call runtime modules** (never upward)
6. **Risk/Execution modules do NOT call runtime modules** (never upward)
7. **Domain services are leaves** — they never call upward

---

## LIVE_SCANNER.PY TARGET STRUCTURE

After all extractions, the orchestrator contains:

```python
def run_live_scanner(*, symbols, on_intent, max_iterations):
    # ═══ STARTUP ═══════════════════════════════════════════
    states = initialize_symbols(symbols, config)
    mt5_health = MT5HealthManager(states)
    cycle_guards = CycleGuards(config)
    bar_providers = {s.symbol: BarProvider(s) for s in states}
    decision_handler = DecisionHandler(config)
    guard_chain = build_runtime_guard_chain(config)
    executor = ExecutionOrchestrator(config)
    observers = build_observer_registry(config)
    health = HealthMonitor(states)
    recorder = DecisionRecorder(get_ledger())

    # ═══ MAIN LOOP ═════════════════════════════════════════
    cycle_id = 0
    while not is_shutdown_requested():
        cycle_id += 1

        if not mt5_health.check_and_reconnect():
            sleep(); continue

        permission = cycle_guards.evaluate()
        if not permission.allowed:
            sleep(); continue

        for sym_state in states:
            bar = bar_providers[sym_state.symbol].fetch_bar()
            if bar is None:
                continue

            recorder.init_cycle(sym_state.symbol, cycle_id, ...)

            gate_result = evaluate_pre_engine_gates(permission, bar)
            if not gate_result.proceed:
                recorder.mutate(decision=gate_result.outcome, ...)
                recorder.finalize()
                continue

            engine_result = run_new_engine(bar, gate_result.patterns, ...)
            observers.notify_all(engine_result, ...)

            if engine_result["action"] == "NO_TRADE":
                decision_handler.handle_no_trade(engine_result, ...)
                recorder.finalize()
                continue

            setup = decision_handler.handle_execute(engine_result, ...)
            guard_result = guard_chain.evaluate(setup.intent, ...)
            if not guard_result.allowed:
                recorder.mutate(decision=RISK_BLOCK, ...)
                recorder.finalize()
                continue

            outcome = executor.execute_trade(setup, ...)
            recorder.mutate(decision=EXECUTE if outcome.success else NO_TRADE, ...)
            recorder.finalize()

        cycle_report(candidates, drops, ...)
        health.tick(cycle_id, ...)
        ledger.tick()
        sleep()

    # ═══ SHUTDOWN ══════════════════════════════════════════
    shutdown(states, ledger)
```

**Target: ~200-300 lines of pure orchestration.**

---

## SUMMARY

| Metric | Current | Target | Change |
|--------|---------|--------|--------|
| Lines in live_scanner.py | 2,721 | ~300 | -89% |
| Responsibilities in live_scanner.py | 23 | 5 | -78% |
| External imports in live_scanner.py | ~60 | ~15 | -75% |
| New modules created | 0 | 12 | +12 |
| Packages modified | 1 | 4 | +3 |
| Behaviour change | — | None | Zero |

---

*End of analysis. No code was moved. This document defines the target ownership model.*
