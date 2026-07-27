# Production Readiness Audit #5 — Interface & Dependency

**Generated:** 2026-07-18  
**Context:** Post live_scanner refactor — 24 extracted interfaces verified  
**Method:** Signature inspection, caller tracing, circular dependency analysis, dead interface detection

---

## Interface Availability Check

All 24 extracted public interfaces import and resolve correctly:

| Module | Interface | Parameters | Status |
|--------|-----------|-----------|--------|
| `scanner_init` | `initialize_symbol_states()` | 2 | ✅ |
| `cycle_guards` | `CycleGuards.evaluate()` | 1 (self) | ✅ |
| `tick_monitor` | `TickMonitor.evaluate()` | 4 | ✅ |
| `bar_provider` | `BarProvider.fetch_bar()` | 2 | ✅ |
| `pre_engine_gates` | `evaluate_pre_engine_gates()` | 7 | ✅ |
| `decision_recorder` | `DecisionRecorder` (class) | — | ✅ |
| `execution_context_builder` | `build_cycle_context()` | 10 | ✅ |
| `engine_outcome_handler` | `handle_no_trade_outcome()` | 16 | ✅ |
| `engine_execution_handler` | `prepare_execution()` | 17 | ✅ |
| `health_monitor` | `HealthMonitor.tick()` | 5 | ✅ |
| `runtime_state_classifier` | `RuntimeStateClassifier.check_gap()` | 5 | ✅ |
| `filter_hit_classifier` | `classify_new_engine_reason()` | 1 | ✅ |
| `mt5_health` | `MT5HealthManager.check_and_reconnect()` | 1 (self) | ✅ |
| `risk_event_emitter` | `emit_risk_guard_result()` | 5 | ✅ |
| `evaluation_runner` | `evaluate()` | 1 (context) | ✅ |
| `legacy_shadow_runner` | `run_legacy_shadow()` | 11 | ✅ |
| `observers` | `ObserverRegistry.notify_all()` | 2 | ✅ |
| `cycle_report` | `emit_cycle_report()` | 10 | ✅ |
| `pipeline_diagnostics` | `emit_pipeline_diagnostics()` | 4 | ✅ |
| `shadow_pipeline` | `run_shadow_no_trade()` | 8 | ✅ |
| `execution_orchestrator` | `ExecutionOrchestrator.execute_trade()` | 8 | ✅ |
| `post_execution_handler` | `emit_post_trade_success()` | 11 | ✅ |
| `runtime_guard_chain` | `evaluate_runtime_guards()` | 10 | ✅ |
| `tick_driver` | `drive_tick()` | 5 | ✅ |

**All interfaces resolve. No import errors. No signature mismatches.**

---

## Circular Dependency Report

| Pair | Direction | Type | Severity |
|------|-----------|------|----------|
| `scanner_init.py` ↔ `live_scanner.py` | scanner_init imports `_LiveSymbolState` from live_scanner | Lazy import (inside function body) | ⚠️ **Monitor** — type construction only, no runtime circularity |
| `evaluation_runner.py` ↔ `live_scanner.py` | NONE — only a comment mentions "live_scanner" | No import | ✅ Clean |
| `execution_orchestrator.py` ↔ `live_scanner.py` | NONE | No import | ✅ Clean |
| `observers.py` ↔ `live_scanner.py` | NONE | No import | ✅ Clean |
| `runtime_guard_chain.py` ↔ `live_scanner.py` | NONE | No import | ✅ Clean |

**One known circular (type reference only):** `scanner_init.py` → `live_scanner._LiveSymbolState`. This is a lazy import inside the function body and does not create a runtime circular dependency. Python handles this correctly.

---

## Dead Interface Detection

| Interface | Module | Production Callers | Status |
|-----------|--------|-------------------|--------|
| `classify_old_pipeline_drop` | `filter_hit_classifier.py` | **0** | ⚠️ **Dead** — was used in removed NO_TRADE block |
| `FilterHitResult` (type export) | `filter_hit_classifier.py` | **0** (only tests) | ⚠️ **Dead export** — used internally by `classify_new_engine_reason` but not imported externally |
| `ShadowResult` | `shadow_pipeline.py` | **0** (only tests) | ⚠️ **Dead export** — functions return it but callers don't destructure |

**3 dead interfaces detected.** None affect runtime behaviour (they're unused but harmless).

---

## Stale Import Check

| File | Import | Issue | Severity |
|------|--------|-------|----------|
| None | — | — | — |

**No stale imports detected in production code.** All imports resolve to active, callable interfaces.

---

## Wrapper/Shim Audit

| Wrapper | Location | Wraps | Purpose | Remove? |
|---------|----------|-------|---------|---------|
| `_write_heartbeat()` | `live_scanner.py` L182 | `_health_monitor.write_heartbeat()` | Compatibility shim (ignores `n_symbols` param) | Low priority — 3 lines |
| `run_live` alias | `core/loop.py` L5 | `run_live_scanner` | Backward compatibility for `main.py` | ❌ Keep — used by entry point |
| `_finalize_decision()` | `live_scanner.py` (per-cycle) | `_decision_recorder.finalize()` | Closure capturing `cycle_start` | ❌ Keep — provides cycle_start context |

---

## Stale API References in Documentation

| Document | Reference | Status |
|----------|-----------|--------|
| `LIVE_SCANNER_RESPONSIBILITY_AUDIT.md` | `_new_pipeline_handled` (6×) | ⚠️ **Stale** — pre-refactor document |
| `BOT_ARCHITECTURE_OWNERSHIP_MAP.md` | `ALLOW_LEGACY_FALLBACK` (1×) | ⚠️ **Stale** — dead config |
| `MODULE_CLASSIFICATION_AUDIT.md` | `USE_NEW_PIPELINE` (1×) | ⚠️ **Stale** — dead config |

**All stale references are in documentation, not production code.**

---

## Dependency Direction Verification

```
CORRECT DIRECTION (downward only):

live_scanner.py
    ↓ (imports)
├── scanner_init.py          ✅
├── cycle_guards.py          ✅
├── tick_monitor.py          ✅
├── bar_provider.py          ✅
├── pre_engine_gates.py      ✅
├── decision_recorder.py     ✅
├── execution_context_builder.py  ✅
├── engine_outcome_handler.py     ✅
├── engine_execution_handler.py   ✅
├── health_monitor.py        ✅
├── runtime_state_classifier.py   ✅
├── evaluation_runner.py     ✅
├── observers.py             ✅
├── cycle_report.py          ✅
├── pipeline_diagnostics.py  ✅
├── runtime_guard_chain.py   ✅
├── execution_orchestrator.py ✅
└── post_execution_handler.py ✅

REVERSE (upward — only type reference):
scanner_init.py → live_scanner._LiveSymbolState  ⚠️ (lazy, acceptable)

NO OTHER REVERSE DEPENDENCIES EXIST.
```

---

## Replay Runtime Interface Check

| Runtime | Uses `process_bar`? | Correct Signature? | `htf_context` Handling |
|---------|--------------------|--------------------|----------------------|
| `replay_scanner.py` | ✅ Direct import | ✅ All kwargs match | Defaults to `None` (not passed) ✅ |
| `replay_runtime.py` | ✅ Direct import | ✅ All kwargs match | Defaults to `None` (not passed) ✅ |
| `legacy_shadow_runner.py` | ✅ Lazy import | ✅ Passes `htf_context` explicitly | ✅ |

**No interface mismatches between replay/evaluation callers and `process_bar()` signature.**

---

## Summary

| Check | Result |
|-------|--------|
| All 24 interfaces importable | ✅ |
| All signatures match callers | ✅ |
| No stale production imports | ✅ |
| No production circular dependencies | ✅ (1 lazy type reference — acceptable) |
| No duplicate wrappers (beyond 1 shim) | ✅ |
| Dead interfaces | 3 found (harmless — in test-only paths) |
| Stale documentation references | 3 found (docs only, not code) |
| Replay interface compatibility | ✅ All `process_bar` callers verified |
| Dependency direction | ✅ Strictly downward (no upward) |

---

## Priority Fix List

### High (breaks runtime)
**None.**

### Medium (cleanup)
| # | Issue | Action |
|---|-------|--------|
| 1 | `classify_old_pipeline_drop` is dead in production | Remove export or keep for future use |
| 2 | `_write_heartbeat` shim drops `n_symbols` parameter silently | Inline the call or fix parameter passing |

### Low (documentation only)
| # | Issue | Action |
|---|-------|--------|
| 3 | 3 architecture docs reference removed variables/configs | Mark as historical or regenerate |

---

## Final Verdict

**All interfaces are correct and active.** The refactor successfully updated all callers, removed all stale production imports, and maintained correct dependency direction throughout. The only issues are 3 dead exports (test-only), 1 minor shim, and 3 stale documentation references — none of which affect runtime behaviour.

No interface-level regressions exist.
