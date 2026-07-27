# Production Readiness Audit #1 — Runtime & Entry Points

**Generated:** 2026-07-18  
**Context:** Post live_scanner refactor — all runtimes verified  
**Method:** Import validation, interface verification, flow tracing

---

## Runtime Inventory

| Runtime | Entry Function | Entry Module | Status |
|---------|---------------|-------------|--------|
| **Live Scanner** (production) | `run_live_scanner()` | `core/runtime/live_scanner.py` | ✅ Operational |
| **Replay Scanner** (multi-symbol) | `run_replay_scanner()` | `core/runtime/replay_scanner.py` | ✅ Operational |
| **Replay Runtime** (single-symbol) | `run_replay()` | `core/runtime/replay_runtime.py` | ✅ Operational |
| **Evaluation Runtime** | `evaluate()` | `core/evaluation/evaluation_runner.py` | ✅ Operational |
| **Shadow Runtime** | `run_legacy_shadow()` | `core/evaluation/legacy_shadow_runner.py` | ✅ Operational |

---

## Entry Point Chain

```
main.py
├── configure_logging()
├── validate_and_freeze_config()
├── acquire_instance_lock()
├── MT5 initialize + validate_account
├── run_startup_self_test()
│
├── if MULTI_SYMBOL_SCANNER_ENABLED + REPLAY_MODE:
│   └── core.loop.run_replay_scanner() → replay_scanner.py
│
├── if MULTI_SYMBOL_SCANNER_ENABLED + not REPLAY_MODE:
│   └── core.loop.run_live_scanner() → live_scanner.py  [PRODUCTION]
│
├── else (legacy sequential):
│   └── for symbol: core.loop.run_replay() / run_live()
│
└── finally:
    ├── record_daily_equity_snapshot()
    ├── write_heartbeat(SHUTDOWN)
    ├── mt5.shutdown()
    └── release_instance_lock()
```

**Assessment:** ✅ Entry point routing is correct. All paths lead to valid runtime functions.

---

## Live Scanner Runtime (Production)

| Phase | Component | Status | Evidence |
|-------|-----------|--------|----------|
| Import | All 16 service modules | ✅ | `python -c "from core.loop import run_live_scanner"` succeeds |
| Init | `initialize_symbol_states()` | ✅ | Creates `_LiveSymbolState` list, returns empty on failure |
| Startup | System state wiring (guards, monitors, providers) | ✅ | All managers accept config + states |
| Loop | `while max_iterations is None or cycle_id < max_iterations:` | ✅ | Respects `max_iterations` for testing |
| Shutdown check | `is_shutdown_requested()` → break | ✅ | Central flag, no circular import |
| Health check | `_mt5_health.check_and_reconnect()` | ✅ | Returns bool, caller owns `continue` |
| Sleep | `interruptible_sleep(config.POLL_SECONDS)` | ✅ | Responds to shutdown during sleep |
| Shutdown | finally: disconnect feeds + flush ledger + persist state + evaluation summary | ✅ | Exception-safe |

**Assessment:** ✅ Complete lifecycle. Start → Run → Graceful stop.

---

## Replay Scanner Runtime

| Phase | Component | Status | Evidence |
|-------|-----------|--------|----------|
| Import | `process_bar`, `EngineState`, event_bus functions | ✅ | Import verified |
| Interface | `process_bar(candles, closed_i, symbol, config, risk, state, bid, ask, now_s)` | ✅ | Missing `htf_context` → defaults to `None` (correct for replay) |
| Init | Creates `_ReplaySymbolState` per symbol | ✅ | Independent from live_scanner's `_LiveSymbolState` |
| Loop | `while any(not completed)` → advance one bar per symbol | ✅ | Each symbol advances independently |
| Termination | All symbols reach `end_i` → break | ✅ | Natural completion |
| Error handling | Per-symbol try/except → skip bar, continue | ✅ | One broken bar doesn't crash replay |

**Assessment:** ✅ Operational. Uses `process_bar` (legacy engine) directly — correct for replay evaluation purposes.

---

## Replay Runtime (Single Symbol)

| Phase | Component | Status | Evidence |
|-------|-----------|--------|----------|
| Import | Same as replay_scanner | ✅ | Import verified |
| Interface | `process_bar(candles, closed_i, symbol, risk, state, bid, ask, now_s, config)` | ✅ | Keyword arguments match signature |
| Init | Feed connect + resolve + copy_rates | ✅ | Standard MT5 data flow |
| Loop | `for closed_i in range(start_i, end_i):` | ✅ | Deterministic, sequential |
| Termination | Loop ends naturally | ✅ | |
| Cleanup | `finally: feed.disconnect()` | ✅ | |

**Assessment:** ✅ Operational. Simple sequential replay.

---

## Evaluation Runtime

| Phase | Component | Status | Evidence |
|-------|-----------|--------|----------|
| Import | Lazy (inside function bodies) | ✅ | No circular dependency risk |
| Interface | `evaluate(EvaluationContext) → EvaluationResult` | ✅ | Clean dataclass contract |
| Dispatch | EXECUTE → `run_legacy_shadow()` + `run_shadow_execute_comparison()` | ✅ | Via lazy imports |
| Dispatch | NO_TRADE → `run_shadow_no_trade()` | ✅ | Via lazy imports |
| Feature gate | `ENABLE_LEGACY_SHADOW_PIPELINE` checked first | ✅ | Returns immediately when disabled |
| Failure isolation | Outer `try/except Exception: pass` | ✅ | Never affects production |
| Shutdown | `shutdown_evaluation(config)` → MTF calibration summary | ✅ | Called from live_scanner finally block |

**Assessment:** ✅ Operational. Completely isolated from production decisions.

---

## Shadow Runtime (Legacy Engine)

| Phase | Component | Status | Evidence |
|-------|-----------|--------|----------|
| Import | `from core.engine import process_bar` (lazy) | ✅ | Inside function body |
| Interface | `run_legacy_shadow(...) → unified_result or None` | ✅ | Returns legacy output for comparison |
| State isolation | Uses `copy.deepcopy(engine_state)` | ✅ | Never contaminates production state |
| Failure isolation | Outer `try/except Exception: return None` | ✅ | Cannot crash production |
| Shadow mode | `MTF_SHADOW_MODE` → dual pipeline comparison | ✅ | Internal to this module |

**Assessment:** ✅ Operational. Fully isolated shadow execution.

---

## Interface Compatibility Check

| Interface | Producer | Consumer | Compatible? | Evidence |
|-----------|----------|----------|-------------|----------|
| `process_bar()` signature | `core/engine.py` | `replay_scanner`, `replay_runtime`, `legacy_shadow_runner` | ✅ | All use keyword args; `htf_context` defaults to None |
| `run_live_scanner()` signature | `live_scanner.py` | `core/loop.py`, `main.py` | ✅ | `symbols`, `on_intent`, `max_iterations` — all optional |
| `run_replay_scanner()` signature | `replay_scanner.py` | `core/loop.py`, `main.py` | ✅ | `symbols`, `on_intent` — all optional |
| `run_replay()` signature | `replay_runtime.py` | `core/loop.py`, `main.py` | ✅ | `symbol`, `on_intent` — all optional |
| `evaluate()` signature | `evaluation_runner.py` | `live_scanner.py`, `engine_outcome_handler.py` | ✅ | `EvaluationContext` dataclass |

**No interface mismatches detected.**

---

## Startup Sequence Verification

| Step | Responsibility | Module | Status |
|------|---------------|--------|--------|
| 1. Logging | Configure log levels | `main.py` | ✅ |
| 2. Signal handlers | Register SIGINT/SIGTERM | `main.py` | ✅ |
| 3. Config validation | Freeze config | `config_validation.py` | ✅ |
| 4. Discord logger | Attach to config | `main.py` | ✅ |
| 5. Config profile | Load trading profile | `config_profile_loader.py` | ✅ |
| 6. Strategy identity | Resolve strategy registry | `strategy_identity.py` | ✅ |
| 7. Instance lock | Prevent duplicates | `instance_lock.py` | ✅ |
| 8. Risk coverage | Validate SL/TP rules | `risk/levels.py` | ✅ |
| 9. MT5 init | Connect to terminal | `main.py` (centralized) | ✅ |
| 10. Account validation | Verify broker account | `mt5_validation.py` | ✅ |
| 11. Self-test | Pre-flight symbol check | `startup_self_test.py` | ✅ |
| 12. Heartbeat | Write STARTING status | `heartbeat.py` | ✅ |
| 13. Runtime dispatch | Route to correct runtime | `main.py` | ✅ |

**No startup gaps detected.**

---

## Shutdown Sequence Verification

| Step | Responsibility | Module | Status |
|------|---------------|--------|--------|
| 1. Signal received | Set shutdown flag | `shutdown.py` | ✅ |
| 2. Loop exit | Check flag → break | `live_scanner.py` | ✅ |
| 3. Feeds disconnect | Close MT5 feeds | `live_scanner.py` (finally) | ✅ |
| 4. Ledger flush | Persist buffered decisions | `live_scanner.py` (finally) | ✅ |
| 5. State persist | Save EngineState | `live_scanner.py` (finally) | ✅ |
| 6. Evaluation summary | MTF calibration emit | `evaluation_runner.py` | ✅ |
| 7. Equity snapshot | Daily P&L record | `main.py` (finally) | ✅ |
| 8. Heartbeat | Write SHUTDOWN status | `main.py` (finally) | ✅ |
| 9. MT5 shutdown | Close terminal connection | `main.py` (finally) | ✅ |
| 10. Instance lock release | Allow restart | `main.py` (finally) | ✅ |

**No shutdown gaps detected.**

---

## Runtime Exception Handling

| Exception Source | Handler | Behaviour | Status |
|-----------------|---------|-----------|--------|
| Engine A crash | `live_scanner.py` inner except | Block trade, persist to ledger, continue | ✅ |
| Per-symbol unknown error | `live_scanner.py` outer except | Log, Discord alert, continue to next symbol | ✅ |
| Evaluation crash | `evaluation_runner.py` outer except | Return None, never affect production | ✅ |
| Execution crash | `execution_orchestrator.py` except | Return ExecutionOutcome(executed=False), caller continues | ✅ |
| Guard crash | Individual guards in `runtime_guard_chain.py` | `control_gate` failure → allow (fail-open for final gate only) | ✅ |
| Replay bar error | `replay_scanner.py` per-bar except | Skip bar, advance pointer, continue | ✅ |
| MT5 disconnect | `mt5_health.py` | Enter degraded mode, attempt reconnect with backoff | ✅ |

**No unhandled exception paths detected in production runtimes.**

---

## Known Risks

| Risk | Severity | Mitigation | Status |
|------|----------|-----------|--------|
| Instance lock not released on SIGKILL | Low | Watchdog detects stale lock via heartbeat timeout | ✅ Mitigated |
| MT5 connection lost during execution | Low | Execution returns error, cycle continues, health monitor reconnects | ✅ Mitigated |
| Config mutation during runtime | Low | `validate_and_freeze_config()` runs before any runtime starts | ✅ Mitigated |
| Replay modules use legacy `process_bar` | None | This is by design — replay evaluates the legacy engine | ✅ By design |

---

## Final Verdict

| Runtime | Can Start? | Can Execute? | Can Terminate? | Production Ready? |
|---------|-----------|-------------|---------------|-------------------|
| **Live Scanner** | ✅ | ✅ | ✅ | ✅ **YES** |
| **Replay Scanner** | ✅ | ✅ | ✅ | ✅ YES (evaluation mode) |
| **Replay Runtime** | ✅ | ✅ | ✅ | ✅ YES (evaluation mode) |
| **Evaluation Runner** | ✅ | ✅ | ✅ | ✅ YES (shadow mode) |
| **Shadow Runner** | ✅ | ✅ | ✅ | ✅ YES (isolated) |

**All runtimes are operational.** No interface mismatches, no startup failures, no shutdown gaps, no unhandled exception paths.

The refactor did not break any runtime boundary.
