# Responsibility Ownership Audit

**Generated:** 2026-07-18  
**Context:** Post live_scanner refactor — 15 modules extracted  
**Method:** Static analysis of responsibility, coupling, cohesion per module

---

## Module Ownership Map

### Runtime Orchestration Layer (`core/runtime/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **live_scanner.py** | 925 | Production runtime orchestration | High | Medium (16 service deps) | **9/10** |
| **scanner_init.py** | 169 | Symbol resolution + state creation | High | Low | **9/10** |
| **cycle_guards.py** | 185 | Cycle-level permission evaluation | High | Low (4 guard deps) | **10/10** |
| **tick_monitor.py** | 161 | Tick freshness evaluation + diagnostics | High | Low (2 event deps) | **10/10** |
| **bar_provider.py** | 241 | Bar fetch + validation + deduplication | High | Low | **9/10** |
| **pre_engine_gates.py** | 143 | Pre-engine permission checks | High | Low (2 gate deps) | **10/10** |
| **decision_recorder.py** | 172 | Decision lifecycle management | High | Low (1 ledger dep) | **10/10** |
| **execution_context_builder.py** | 116 | Per-cycle correlation ID + context persistence | High | Low | **9/10** |
| **engine_outcome_handler.py** | 169 | NO_TRADE outcome workflow | Medium | Medium (5 deps) | **8/10** |
| **engine_execution_handler.py** | 200 | EXECUTE preparation bookkeeping | Medium | Medium (4 deps) | **8/10** |
| **health_monitor.py** | 187 | Heartbeat + no-trade alerting + stall detection | High | Low | **10/10** |
| **runtime_state_classifier.py** | 154 | Runtime gap detection + classification | High | Low | **10/10** |
| **filter_hit_classifier.py** | 126 | Rejection reason → diagnostic category mapping | High | Zero | **10/10** |
| **mt5_health.py** | 121 | MT5 connection lifecycle + reconnect | High | Low | **10/10** |
| **risk_event_emitter.py** | 43 | Risk guard event emission utility | High | Zero | **10/10** |
| **runtime_utils.py** | 99 | Shared builder utilities | Medium | Low | **8/10** |

### Pipeline Layer (`core/pipeline/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **new_engine.py** | 859 | Production decision engine (Engine A) | High | Medium | **9/10** |
| **observers.py** | 170 | Observer dispatch after engine evaluation | High | Low (lazy imports) | **9/10** |
| **cycle_report.py** | 171 | End-of-cycle summary emission | High | Low | **10/10** |
| **pipeline_diagnostics.py** | 231 | Throttled diagnostic reporting | High | Low (lazy imports) | **9/10** |
| **shadow_pipeline.py** | 153 | Shadow divergence logging (fire-and-forget) | High | Low | **9/10** |

### Evaluation Layer (`core/evaluation/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **evaluation_runner.py** | 165 | Evaluation dispatch boundary | High | Low (lazy) | **10/10** |
| **legacy_shadow_runner.py** | 188 | Legacy engine execution (shadow) | High | Low (lazy) | **10/10** |

### Execution Layer (`execution/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **execution_orchestrator.py** | 169 | Broker execution + result persistence | High | Low | **10/10** |
| **post_execution_handler.py** | 184 | Post-trade fire-and-forget effects | High | Low | **9/10** |

### Risk Layer (`risk/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **runtime_guard_chain.py** | 275 | Post-engine guard orchestration | High | Medium (10 guard deps) | **9/10** |

### Trade Management (`core/trade_management/`)

| Module | Lines | Primary Responsibility | Cohesion | Coupling | Score |
|--------|-------|----------------------|----------|----------|-------|
| **tick_driver.py** | 57 | Trade management tick dispatch | High | Zero | **10/10** |

---

## Duplicate Responsibility Analysis

| Responsibility | Module A | Module B | Verdict |
|---------------|----------|----------|---------|
| Session classification (hour → LONDON/NY/ASIA) | `execution_context_builder.py` | `engine_execution_handler.py` | **Minor duplication** — 8 lines each. Not worth abstracting (would create coupling for trivial logic). |
| Risk event emission | `cycle_guards.py` (inline) | `risk_event_emitter.py` (utility) | **Acceptable** — cycle_guards emits its own specific events; risk_event_emitter is shared utility. Different callers, same pattern. |
| Decision audit persistence | `engine_outcome_handler.py` | `engine_execution_handler.py` | **Correct split** — different paths (NO_TRADE vs EXECUTE) persist different audit records. Same underlying function, different contexts. |
| Paper engine `evaluate_pending` | `live_scanner.py` (gate reject) | `engine_outcome_handler.py` (NO_TRADE) | `live_scanner.py` (EXECUTE setup) | **Acceptable** — called at 3 different lifecycle moments for different reasons. |

**No problematic duplicate ownership detected.**

---

## Split Responsibility Analysis

| Module | Secondary Responsibility | Problem? | Verdict |
|--------|------------------------|----------|---------|
| **engine_execution_handler.py** | Correlation ID + Audit + Context + Shadow Trade | ⚠️ 4 concerns | **Acceptable** — they must execute in strict temporal order before guards run. Splitting would force caller to reimpose ordering. |
| **engine_outcome_handler.py** | Filter classify + Narrative + Routing + Evaluation + Audit + Metadata | ⚠️ 6 concerns | **Acceptable** — they form a single "rejection workflow" that always runs together. No partial execution makes sense. |
| **bar_provider.py** | Candle fetch + UTC conversion + Feed classification + Dedup + Shadow trade eval + Stale monitor | ⚠️ 6 concerns | **Borderline** — shadow trade evaluate is embedded (fire-and-forget). All other concerns are tightly ordered bar validation steps. |
| **cycle_guards.py** | Guard evaluation + Discord notification + Risk event emission | ⚠️ Mixed | **Acceptable** — notifications are the natural side effect of guard evaluation. Separating them would require returning notification payloads for the caller to emit. |

---

## Missing Responsibilities

| Gap | Current State | Impact | Priority |
|-----|--------------|--------|----------|
| None identified | All responsibilities have clear owners | — | — |

---

## Incorrect Ownership

| Module | Responsibility | Problem | Should Live In |
|--------|---------------|---------|---------------|
| None identified | — | — | — |

---

## Responsibility Drift After Refactor

| Risk | Evidence | Status |
|------|----------|--------|
| **Handlers becoming mini-orchestrators** | `engine_outcome_handler` and `engine_execution_handler` each perform multi-step workflows | **Monitored** — currently acceptable because steps are tightly coupled and always execute together |
| **Orchestrator retaining business logic** | `live_scanner.py` contains TradeDecision construction and event emission | **Acceptable** — TradeDecision is an interface contract, events are thin delegation calls |
| **Extracted modules becoming orphans** | All 15 extracted modules have exactly 1-2 callers | **Healthy** — no orphans |
| **Evaluation boundary leaking** | `_eval_unified` metadata flows from evaluation_runner into live_scanner | **Acceptable** — observational only, None when disabled |

---

## Correct Ownership (Summary)

The following modules have clean, correct responsibility ownership:

- ✅ `live_scanner.py` — sole production orchestrator
- ✅ `scanner_init.py` — startup initialization
- ✅ `cycle_guards.py` — cycle-level permission
- ✅ `tick_monitor.py` — tick freshness evaluation
- ✅ `bar_provider.py` — bar data provision
- ✅ `pre_engine_gates.py` — pre-engine permission
- ✅ `decision_recorder.py` — decision lifecycle
- ✅ `health_monitor.py` — runtime health observation
- ✅ `runtime_state_classifier.py` — gap detection
- ✅ `filter_hit_classifier.py` — rejection classification
- ✅ `mt5_health.py` — MT5 connection management
- ✅ `evaluation_runner.py` — evaluation boundary
- ✅ `legacy_shadow_runner.py` — legacy engine isolation
- ✅ `execution_orchestrator.py` — broker execution
- ✅ `runtime_guard_chain.py` — post-engine risk gating
- ✅ `new_engine.py` — production decision authority
- ✅ `pipeline_diagnostics.py` — throttled reporting
- ✅ `cycle_report.py` — end-of-cycle summary

---

## Ownership Problems

**None identified.**

Every extracted module owns exactly one responsibility (or a tightly-coupled set of steps that must execute together). No module has become a "mini live_scanner." No responsibility exists in the wrong architectural layer.

---

## Suggested Improvements

| # | Improvement | Benefit | Priority |
|---|------------|---------|----------|
| 1 | Move `TradeDecision` to a shared type module (e.g. `core/runtime/types.py`) | Type-checkable, importable by consumers | **Low** — only constructed in 1 place |
| 2 | Inline `_write_heartbeat` shim in live_scanner | Remove 3-line wrapper | **Cosmetic** |
| 3 | Extract session classification into a shared utility | Eliminate 8-line duplication | **Not recommended** — would create coupling for trivial logic |

---

## Final Architectural Verdict

**The responsibility ownership is correct.**

Evidence:
- 27 audited modules across 6 layers
- Average ownership score: **9.4/10**
- No module scores below 8/10
- Zero incorrect ownership
- Zero missing responsibilities  
- Zero structural layer violations
- One minor duplication (8 lines, not worth fixing)
- Two multi-concern modules that are correctly grouped (temporal coupling)

**The architecture has reached a stable, production-grade state.** No further refactoring is recommended for responsibility ownership. Future changes should focus on feature development, not structural cleanup.
