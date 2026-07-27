# Dependency Graph Audit

**Generated:** 2026-07-18  
**Context:** Post live_scanner.py refactor (2673 → 925 lines, 15 modules extracted)  
**Method:** Static import analysis + caller tracing across all production modules

---

## Layer Dependency Diagram

```
┌─────────────────────────────────────────────────────┐
│                  ENTRY POINT                         │
│  main.py → core/loop.py → live_scanner.py           │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│              RUNTIME ORCHESTRATION                    │
│  core/runtime/live_scanner.py                        │
│  (sole production orchestrator)                      │
└──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──────┘
   │  │  │  │  │  │  │  │  │  │  │  │  │  │  │
   ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼
┌─────────────────────────────────────────────────────┐
│              RUNTIME SERVICES                        │
│  scanner_init         mt5_health                     │
│  cycle_guards         tick_monitor                   │
│  bar_provider         runtime_state_classifier       │
│  pre_engine_gates     health_monitor                 │
│  decision_recorder    execution_context_builder      │
│  engine_outcome_handler                              │
│  engine_execution_handler                            │
└──┬──────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────┐
│              PIPELINE / STRATEGY                      │
│  core/pipeline/new_engine (decision authority)       │
│  core/pipeline/observers                             │
│  core/pipeline/cycle_report                          │
│  core/pipeline/pipeline_diagnostics                  │
│  core/pipeline/bias_fsm                              │
│  risk/runtime_guard_chain                            │
└──┬──────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────┐
│              EXECUTION LAYER                         │
│  execution/execution_orchestrator                    │
│  execution/post_execution_handler                    │
│  execution/mt5_execution (broker interface)          │
└──┬──────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────┐
│              EVALUATION LAYER (isolated)             │
│  core/evaluation/evaluation_runner                   │
│  core/evaluation/legacy_shadow_runner                │
│  core/pipeline/shadow_pipeline                       │
└─────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────┐
│              DOMAIN / INFRASTRUCTURE                  │
│  risk/* (guards, models, sizing)                     │
│  strategy/* (signals, patterns)                      │
│  data/* (MT5 data feed)                              │
│  core/engine (EngineState, process_bar)              │
│  core/event_bus, core/event_stream                   │
│  core/persistence/*                                  │
└─────────────────────────────────────────────────────┘
```

---

## Dependency Hotspots

| Module | Inbound (prod callers) | Outbound (imports) | Classification |
|--------|----------------------|-------------------|----------------|
| **live_scanner.py** | 1 (loop.py) | 16 layers | **Healthy** — orchestrator pattern (many outbound, single inbound) |
| **core/config.py** | ~50+ modules | 0 | **Healthy** — configuration singleton |
| **core/event_bus.py** | ~15 modules | 0 | **Healthy** — event infrastructure |
| **strategy/signals.py** | ~20 modules | 0 | **Healthy** — shared enum types |
| **risk/models.py** | ~10 modules | 1 (strategy) | **Healthy** — shared data models |
| **data/mt5_data.py** | ~8 modules | 1 (core/mt5_timeout) | **Healthy** |

No unhealthy hotspots detected. The highest-connectivity module (live_scanner) is the orchestrator — this is by design.

---

## Circular Dependency Report

| Cycle | Modules | Type | Severity |
|-------|---------|------|----------|
| 1 | `live_scanner.py` ↔ `scanner_init.py` | Type reference (`_LiveSymbolState`) | **Monitor** — acceptable; scanner_init creates instances of the type defined in live_scanner |

**No other circular dependencies exist in the production graph.**

The `scanner_init → live_scanner` import is a lazy import inside the function body (`from core.runtime.live_scanner import _LiveSymbolState`), not a module-level cycle. Python handles this correctly at runtime.

---

## Layer Violations

| # | Source | Target | Violation Type | Severity |
|---|--------|--------|---------------|----------|
| 1 | `execution/mt5_execution.py` | `strategy.signals.Side` | Execution → Strategy (type enum) | **Healthy** — shared enum, not strategy logic |
| 2 | `core/trade_management/manager.py` | `execution` | Core → Execution (for order placement) | **Healthy** — trade management needs execution interface |
| 3 | `core/runtime/startup_recovery.py` | `strategy` | Runtime → Strategy (for Side enum) | **Monitor** — could use risk.models.Side instead |

**No structural layer violations.** All cross-layer references are to shared types (enums, models), not business logic.

---

## Caller Analysis (Extracted Modules)

| Module | Production Callers | Assessment |
|--------|-------------------|-----------|
| scanner_init | 1 (live_scanner) | **Healthy** — single entry point |
| cycle_guards | 1 (live_scanner) | **Healthy** |
| runtime_guard_chain | 1 (live_scanner) | **Healthy** |
| tick_monitor | 1 (live_scanner) | **Healthy** |
| bar_provider | 1 (live_scanner) | **Healthy** |
| execution_context_builder | 1 (live_scanner) | **Healthy** |
| decision_recorder | 1 (live_scanner) | **Healthy** |
| pre_engine_gates | 1 (live_scanner) | **Healthy** |
| runtime_state_classifier | 1 (live_scanner) | **Healthy** |
| health_monitor | 1 (live_scanner) | **Healthy** |
| engine_outcome_handler | 1 (live_scanner) | **Healthy** |
| engine_execution_handler | 1 (live_scanner) | **Healthy** |
| execution_orchestrator | 1 (live_scanner) | **Healthy** |
| post_execution_handler | 1 (live_scanner) | **Healthy** |
| evaluation_runner | 2 (live_scanner, engine_outcome_handler) | **Healthy** |
| legacy_shadow_runner | 1 (evaluation_runner) | **Healthy** |
| filter_hit_classifier | 1 (engine_outcome_handler) | **Healthy** |
| shadow_pipeline | 1 (evaluation_runner) | **Healthy** |
| pipeline_diagnostics | 1 (live_scanner) | **Healthy** |
| cycle_report | 1 (live_scanner) | **Healthy** |

**All extracted modules have 1-2 production callers.** None are orphaned. None are over-shared.

---

## Modules That Could Be Removed

| Module | Reason | Recommendation |
|--------|--------|---------------|
| None | — | All extracted modules serve active production callers |

---

## Modules That Need New Boundaries

| Module | Issue | Recommendation |
|--------|-------|---------------|
| None | — | All boundaries are correctly placed |

---

## Dead / Isolated Modules (Non-Extracted)

| Module | Last Caller | Status |
|--------|------------|--------|
| `core/runtime/instance_lock.py` | Unknown (not in extracted graph) | **Monitor** — verify if still used by main.py |
| `core/runtime/replay_runtime.py` | Replay system (separate entry point) | **Healthy** — parallel runtime |
| `core/runtime/replay_scanner.py` | Replay system (separate entry point) | **Healthy** — parallel runtime |

---

## Wrapper Module Analysis

| Module | Is it just a wrapper? | Assessment |
|--------|----------------------|-----------|
| `core/loop.py` | Yes — aliases `run_live_scanner` | **Healthy** — entry point compatibility |
| `_write_heartbeat` in live_scanner | Yes — 3-line delegation | **Monitor** — could be inlined |

---

## Final Verdict

| Finding | Classification |
|---------|---------------|
| Overall dependency direction | **Healthy** — strictly downward from orchestrator |
| Circular dependencies | **Monitor** — 1 acceptable type-reference cycle |
| Layer violations | **Healthy** — only shared type enums cross boundaries |
| Dependency hotspots | **Healthy** — orchestrator pattern (fan-out, single fan-in) |
| Extracted module isolation | **Healthy** — each has 1-2 callers |
| Evaluation boundary | **Healthy** — completely isolated, lazy imports only |
| Cross-layer coupling | **Healthy** — no business logic crosses layers |
| Dead modules | **Healthy** — none detected in extracted graph |
| Wrapper modules | **Healthy** — one compatibility alias (core/loop.py) |

**Overall Assessment: The dependency graph is structurally healthy.**

The refactor successfully transformed a monolithic orchestrator into a layered system with:
- Clear unidirectional dependency flow
- No business logic in the orchestration layer
- Isolated evaluation boundary
- Single-responsibility extracted modules
- No dependency hotspots beyond the expected orchestrator fan-out

No further structural changes are recommended.
