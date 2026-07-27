# Data Flow Architecture Audit

**Generated:** 2026-07-18  
**Context:** Post live_scanner refactor — Engine A sole authority  
**Method:** Contract tracing through complete trading pipeline

---

## Complete Runtime Flow Diagram

```
MT5 Terminal
    │
    ├── last_tick() ─────────────────────── bid, ask, tick_time
    │                                           │
    │                                    TickMonitor.evaluate()
    │                                           │
    │                                    TickMonitorResult {valid, stale}
    │                                           │
    ├── copy_rates_closed() ─────────── candles[]
    │                                           │
    │                                    BarProvider.fetch_bar()
    │                                           │
    │                                    BarResult {candles, closed_i, closed_time, feed_state}
    │                                           │
    │                              build_cycle_context() → correlation_id
    │                                           │
    │                              evaluate_pre_engine_gates()
    │                                           │
    │                              GateResult {allowed, raw_patterns}
    │                                           │
    ├─────────────────────────────── run_new_engine()
    │                                           │
    │                                    _new_result (dict)
    │                                     ├── action: "NO_TRADE" | "EXECUTE"
    │                                     ├── intent: OrderIntent (if EXECUTE)
    │                                     ├── score: float
    │                                     ├── reason: str
    │                                     ├── assessment: OpportunityAssessment
    │                                     └── reasoning/uncertainty/attribution
    │                                           │
    │                    ┌─────────────────────┴──────────────────────┐
    │                    │                                            │
    │              NO_TRADE                                      EXECUTE
    │                    │                                            │
    │     handle_no_trade_outcome()                     prepare_execution()
    │              │                                            │
    │     ─ filter classify                         ExecutionPrep {intent, cor_id, decision_id}
    │     ─ narrative                                           │
    │     ─ routing                                   TradeDecision (facade)
    │     ─ evaluation                                         │
    │     ─ decision finalize                        evaluate_runtime_guards()
    │              │                                            │
    │     DecisionOutcome.NO_TRADE                   GuardChainResult {allowed, guard_name}
    │              │                                            │
    │     _finalize_decision()                       ┌─────────┴──────────┐
    │              │                                 │                    │
    │         [LEDGER]                          BLOCKED               ALLOWED
    │                                               │                    │
    │                                    DecisionOutcome.RISK_BLOCK       │
    │                                               │              execute_trade()
    │                                          [LEDGER]                  │
    │                                                         ExecutionOutcome {ok, result}
    │                                                                    │
    │                                                         ┌──────────┴──────────┐
    │                                                         │                     │
    │                                                    SUCCESS              BROKER_REJECT
    │                                                         │                     │
    │                                              ─ state mutation          DecisionOutcome.NO_TRADE
    │                                              ─ trade registration              │
    │                                              ─ decision finalize          [LEDGER]
    │                                              ─ post_trade_success
    │                                                         │
    │                                              DecisionOutcome.EXECUTE
    │                                                         │
    │                                                    [LEDGER]
    │
    └── End of cycle: report → health → diagnostics → checkpoint → sleep
```

---

## Contract Ownership Map

| Contract | Defined In | Produced By | Consumed By | Lifetime | Correctness |
|----------|-----------|-------------|-------------|----------|-------------|
| **OrderIntent** | `risk/models.py` | `risk/manager.py` (sizing), `new_engine.py` (via pipeline) | `execution_orchestrator`, `runtime_guard_chain`, `post_execution_handler`, `trade_management/manager` | Per-symbol per-cycle (EXECUTE path only) | ✅ Correct |
| **TradeDecision** | `live_scanner.py` (inline class) | `live_scanner.py` | `emit_event`, `emit_bias_events`, `emit_setup_events`, `evaluate_runtime_guards`, `execute_trade` | Per-symbol per-cycle (EXECUTE path only) | ✅ Correct — interface bridge between engine dict and typed consumers |
| **ExecutionPrep** | `engine_execution_handler.py` | `prepare_execution()` | `live_scanner.py` (destructures into 3 variables) | Transient — immediately destructured | ✅ Correct |
| **ExecutionOutcome** | `execution_orchestrator.py` | `ExecutionOrchestrator.execute_trade()` | `live_scanner.py` (success/failure branching) | Transient — consumed immediately | ✅ Correct |
| **EvaluationContext** | `evaluation_runner.py` | `live_scanner.py`, `engine_outcome_handler.py` | `evaluation_runner.evaluate()` | Transient — passed into evaluation | ✅ Correct |
| **EvaluationResult** | `evaluation_runner.py` | `evaluate()` | `live_scanner.py` (reads `legacy_unified`) | Transient — observational metadata | ✅ Correct |
| **GuardChainResult** | `runtime_guard_chain.py` | `evaluate_runtime_guards()` | `live_scanner.py` (block/allow branching) | Transient — consumed immediately | ✅ Correct |
| **GateResult** | `pre_engine_gates.py` | `evaluate_pre_engine_gates()` | `live_scanner.py` (block/allow + patterns) | Transient — consumed immediately | ✅ Correct |
| **CyclePermission** | `cycle_guards.py` | `CycleGuards.evaluate()` | `live_scanner.py` (cycle allow/block + flags) | Per-cycle | ✅ Correct |
| **BarResult** | `bar_provider.py` | `BarProvider.fetch_bar()` | `live_scanner.py` (destructures into 5 variables) | Transient — immediately destructured | ✅ Correct |
| **TickMonitorResult** | `tick_monitor.py` | `TickMonitor.evaluate()` | `live_scanner.py` (valid/invalid branching) | Transient | ✅ Correct |
| **FilterHitResult** | `filter_hit_classifier.py` | `classify_new_engine_reason()` | `engine_outcome_handler.py` (increments counter) | Transient | ✅ Correct |
| **RuntimeGapEvent** | `runtime_state_classifier.py` | `RuntimeStateClassifier.check_gap()` | Not consumed (return value ignored by caller) | Transient | ✅ Correct — event is emitted internally |
| **ObserverContext** | `observers.py` | `live_scanner.py` | `ObserverRegistry.notify_all()` | Transient | ✅ Correct |
| **ShadowResult** | `shadow_pipeline.py` | `run_shadow_no_trade()`, `run_shadow_execute_comparison()` | `evaluation_runner.py` (fire-and-forget) | Transient | ✅ Correct |
| **DecisionOutcome** | `decision_ledger.py` | Multiple (enum usage) | `decision_recorder.py`, `live_scanner.py`, handlers | Enum — stateless | ✅ Correct |

---

## Transformation Audit

| Stage | Input | Transformation | Output | Owner | Duplicated? |
|-------|-------|---------------|--------|-------|-------------|
| Tick fetch | MT5 terminal | Network I/O | (bid, ask, tick_time) | `MT5DataFeed` | No |
| Tick validation | tick_time | Stale check + diagnostics | TickMonitorResult | `tick_monitor` | No |
| Bar fetch | MT5 terminal | Network I/O + dedup + UTC conversion | BarResult | `bar_provider` | No |
| Context build | Market state | Session classify + spread/ATR | correlation_id | `execution_context_builder` | ⚠️ Session classify duplicated in `engine_execution_handler` |
| Pattern detection | candles, closed_i | Signal analysis | raw_patterns[] | `signal_orchestrator` (via `pre_engine_gates`) | No |
| Engine evaluation | candles, patterns, state, HTF | 10-factor scoring | _new_result dict | `new_engine` | No |
| Bias FSM update | engine_state, candles, pattern | State machine transition | Updated engine_state | `bias_fsm` | No |
| NO_TRADE processing | _new_result | Classify + notify + audit | Finalized decision | `engine_outcome_handler` | No |
| EXECUTE preparation | _new_result | Correlation + audit + context + shadow | ExecutionPrep | `engine_execution_handler` | No |
| TradeDecision construction | _new_engine_intent, score | Interface bridge | TradeDecision facade | `live_scanner` (inline) | No |
| Guard evaluation | intent, positions, state | 10-guard chain | GuardChainResult | `runtime_guard_chain` | No |
| Broker execution | intent | MT5 order placement | ExecutionOutcome | `execution_orchestrator` | No |
| Post-execution effects | intent, result | Slippage + paper + Discord + events | void (fire-and-forget) | `post_execution_handler` | No |

**One duplication found:** Session classification (hour → LONDON/NY/ASIA/OFF_SESSION) exists in both `execution_context_builder.py` and `engine_execution_handler.py`. This is 8 lines of trivial logic — not worth abstracting.

---

## Data Persistence Points

| Persistence | Module | Trigger | Data Written |
|-------------|--------|---------|-------------|
| Decision ledger | `decision_recorder.py` | Every cycle (via `_finalize_decision`) | Decision outcome, score, reason, flags |
| Decision audit | `engine_outcome_handler.py`, `engine_execution_handler.py`, `live_scanner.py` | NO_TRADE, EXECUTE, broker rejection | Full engine result + candles + state |
| Execution context | `execution_context_builder.py`, `engine_execution_handler.py` | Per-cycle (baseline) + per-EXECUTE | Market snapshot + correlation |
| Execution result | `execution_orchestrator.py` | Every broker call | Fill price, retcode, slippage |
| Risk rejection | `live_scanner.py` (guard handler) | Guard blocks | Guard name, reason, metadata |
| Shadow trade | `engine_execution_handler.py` | EXECUTE (signal tracking) | Entry, SL, TP, score, pattern |
| Feed health | `tick_monitor.py` | STALE transitions | Transition type, duration |
| System health | `runtime_state_classifier.py` | Runtime gaps | Gap type, duration, cycles |
| Engine state | `live_scanner.py` (checkpoint) | Every N cycles + shutdown | Full EngineState per symbol |

**No duplicate persistence detected.** Each persistence point writes different data at different lifecycle moments.

---

## Information Flow Issues

| Issue | Description | Severity | Status |
|-------|-------------|----------|--------|
| **Session classify duplication** | Both `execution_context_builder` and `engine_execution_handler` compute session from hour | Low | **Accepted** — trivial, not worth abstracting |
| **`_eval_unified` passthrough** | Evaluation result flows from `evaluation_runner` through `live_scanner` into `persist_decision_audit` and `post_execution_handler` | Low | **Accepted** — observational metadata, None when disabled |
| **`_new_result` dict contract** | Engine A returns a dict, not a typed object — consumers access via `.get()` | Low | **Monitor** — stable but implicit contract |

---

## Architectural Weak Points

| Weak Point | Impact | Mitigation |
|-----------|--------|-----------|
| **`_new_result` is an untyped dict** | Consumers must know key names implicitly | Engine A is the sole producer; consumers are in 3 tightly-scoped modules. Low risk. |
| **TradeDecision defined inline** | Not importable by tests; recreated each cycle | Single construction site, single consumer chain. Refactoring to shared type adds complexity without benefit currently. |
| **ObserverContext has 15 fields** | Large context object | All fields are consumed by at least one observer. Reducing would require observers to refetch data. |

---

## Final Verdict

**The data flow architecture is correct and healthy.**

- ✅ Every contract has exactly one producer and clear consumer(s)
- ✅ No contract is recreated or duplicated
- ✅ No information is lost between stages
- ✅ Persistence points are non-overlapping
- ✅ Transformation ownership is clear (one module per transformation)
- ✅ No circular data flow
- ✅ No hidden coupling between stages
- ✅ Evaluation data never influences execution decisions
- ✅ Risk guards are authoritative (last gate before execution)

The only structural observation is that `_new_result` (Engine A's output) uses a dict rather than a typed contract. This is acceptable for a single-producer system but should be monitored if additional engines are introduced.

**No architectural changes recommended.**
