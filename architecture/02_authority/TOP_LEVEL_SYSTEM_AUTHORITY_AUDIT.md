# TOP-LEVEL SYSTEM AUTHORITY AUDIT

**Date:** 2026-07-23
**Status:** Discovery audit. No code changes.
**Purpose:** Identify the existing hierarchy of governance — who controls what.

---

## 1. System Hierarchy (Discovered)

```
SYSTEM_INTELLIGENCE_PRINCIPLES.md          ← Architecture constitution (NEW, just created)
    │
    ▼
core/config.py                             ← Configuration authority (all feature flags, limits)
    │
    ▼
main.py                                    ← Startup/shutdown authority (mode dispatch, lifecycle)
    │
    ▼
core/runtime/live_scanner.py               ← Runtime orchestration authority (the main loop)
    │
    ├──▶ core/runtime/cycle_guards.py      ← Cycle-level veto (drawdown, daily loss, kill switch)
    │
    ├──▶ core/pipeline/ (new_engine)       ← Decision authority (scoring, strategy, pattern)
    │
    ├──▶ core/horizon/execution_authority.py  ← Horizon allocation authority (slot/symbol/portfolio)
    │
    ├──▶ risk/runtime_guard_chain.py       ← Risk veto authority (10 sequential guards)
    │
    ├──▶ execution/execution_orchestrator.py  ← Execution authority (broker submission)
    │
    ├──▶ core/trade_management/manager.py  ← Position lifecycle authority (BE/trailing/exit)
    │
    ▼
24 persistence writers                     ← Data ownership (append-only, immutable)
    │
    ▼
research_engine/                           ← Research/learning authority (read-only analysis)
```

---

## 2. Existing Authorities Found

### 2.1 Mission / Purpose Layer

| Document | Location | Responsibility | Authoritative? |
|----------|----------|----------------|:-:|
| SYSTEM_INTELLIGENCE_PRINCIPLES.md | `architecture/` | Governing principle: observable, attributable, explainable, learnable | ✅ YES — created as the constitutional standard |
| (No explicit MISSION.md) | — | Project mission statement | ❌ MISSING — no single "why does this project exist" document |

**Finding:** The project has an architecture constitution (just created) but no explicit mission document. The mission is implicit: "FX trading bot on MT5 (Pepperstone) with continuous self-improvement." This exists only in conversation context, not in a persistent document.

---

### 2.2 Configuration Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `core/config.py` | Root config module | ALL runtime behaviour flags, limits, feature toggles | `EXECUTION_ENABLED`, `USE_NEW_PIPELINE`, `PERMITTED_HORIZONS`, `MAX_OPEN_POSITIONS`, `PORTFOLIO_RANKING_AUTHORITY`, etc. | ✅ YES — single source for all configuration |
| `core/config_validation.py` | Validation | Validates config before runtime starts | Called from main.py at boot | ✅ YES |
| `core/config_profile_loader.py` | Profile override | Loads environment-specific overrides | Optional — profiles can override config values | ✅ YES (when profiles exist) |

**Finding:** `core/config.py` is the unambiguous configuration authority. Every runtime decision reads from it. No other module defines runtime parameters.

---

### 2.3 Startup / Shutdown Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `main.py` | Project root | Process lifecycle: init → validate → dispatch → shutdown | Lines 1-232: logging, MT5 init, config validate, mode dispatch, signal handlers | ✅ YES — single entry point |
| `core/runtime/shutdown.py` | Shutdown signals | Graceful shutdown coordination | `request_shutdown()`, `is_shutdown_requested()` — checked by live_scanner loop | ✅ YES |
| `core/mt5_validation.py` | Broker validation | Validates MT5 account before trading | Called from main.py before dispatch | ✅ YES |

**Finding:** `main.py` owns the process lifecycle. It validates config, initialises MT5, dispatches to the correct mode (`run_live_scanner` or `run_replay_scanner`), and handles termination signals.

---

### 2.4 Runtime Orchestration Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `core/runtime/live_scanner.py` | Runtime loop | The production main loop: tick → bar → evaluate → decide → execute → persist | `run_live_scanner()` at line 95, ~1000 lines orchestrating the complete cycle | ✅ YES — sole production runtime orchestrator |
| `core/runtime/cycle_guards.py` | Pre-cycle veto | Drawdown check, daily loss check, kill switch, daily reset | Called at cycle start — can abort entire cycle | ✅ YES — cycle-level veto |
| `core/runtime/pre_engine_gates.py` | Pre-engine veto | Session guard, pattern detection gate | Called before engine evaluation | ✅ YES — evaluation-level veto |

**Finding:** `live_scanner.py` is the single runtime authority. Everything happens inside its loop. It delegates to specialised modules but retains flow control.

---

### 2.5 Decision Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `core/pipeline/new_engine.py` | Decision engine | Produce EXECUTE or NO_TRADE decision | `run_new_engine()` — evaluates patterns, scores, strategies, produces action | ✅ YES — sole decision producer |
| `core/pipeline/scoring_engine.py` | Scoring | 10-component weighted scoring | Produces score_neutral and score_strategy | ✅ YES — scoring authority |
| `core/pipeline/execution_policy.py` | Policy gate | EV check, swing block, risk reject | Can convert EXECUTE → NO_TRADE | ✅ YES — policy veto |
| `config.USE_NEW_PIPELINE = True` | Config flag | Which engine has decision authority | "New engine is now the SOLE execution authority" | ✅ YES — governance flag |

**Finding:** The new engine pipeline is the sole decision authority (legacy pipeline disabled via `USE_NEW_PIPELINE=True`, `ALLOW_LEGACY_FALLBACK=False`).

---

### 2.6 Risk Veto Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `risk/runtime_guard_chain.py` | Guard chain | 10 sequential per-trade risk checks | `evaluate_runtime_guards()` — daily limit, cooldown, correlation, exposure, regime, spread, consistency, weekend, prop firm, control layer | ✅ YES — can block any EXECUTE |
| `core/horizon/execution_authority.py` | Horizon guard | Portfolio allocation limits | `HorizonExecutionAuthority.can_open()` — slot uniqueness, symbol cap, portfolio cap | ✅ YES — runs BEFORE guard chain |
| `risk/manager.py` | Position sizing + SL/TP | Risk calculation | `RiskManager._execute_risk()` — computes volume, SL, TP | ✅ YES — sizing authority |

**Finding:** Risk veto has three layers: (1) HorizonExecutionAuthority (portfolio slots), (2) runtime_guard_chain (10 guards), (3) RiskManager (sizing/rejection). Any layer can prevent execution.

---

### 2.7 Execution Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `execution/execution_orchestrator.py` | Execution | Submit orders to MT5 broker | `ExecutionOrchestrator.execute_trade()` — single path to broker | ✅ YES — sole broker interface for orders |
| `execution/mt5_execution.py` | Broker I/O | Raw MT5 API calls | `MT5Execution.execute()` — calls `mt5.order_send()` | ✅ YES — lowest level |
| `config.EXECUTION_ENABLED` | Kill switch | Master gate for all broker I/O | If False, no orders are sent regardless of decisions | ✅ YES — absolute veto |

**Finding:** `execution_orchestrator.py` is the single execution authority. No other code path can submit orders. `EXECUTION_ENABLED=False` is the absolute system kill switch.

---

### 2.8 Data Ownership Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| 23 registered S3 writers | Various `core/` modules | Each dataset has exactly ONE writer | Enforced by `test_s3_architecture_guard.py` allowlist | ✅ YES — tested ownership |
| `core/trade_truth.py` | Trade truth | Defines what constitutes execution reality | `_FORBIDDEN_FIELDS` enforcement rejects cross-layer contamination | ✅ YES — truth authority |
| `core/trade_truth_graph.py` | Relationship graph | Defines entity relationships | `_FORBIDDEN_FIELDS` + domain separation | ✅ YES |
| `config.EVENT_STREAM_S3_MIRROR` | S3 gate | Controls whether any S3 writes occur | Single boolean gates all 23 writers | ✅ YES |

**Finding:** Data ownership is well-governed. Each dataset has one writer, enforced by automated test. `trade_truth.py` is the authoritative definition of "what actually happened" — it rejects intent/strategy data via forbidden fields.

---

### 2.9 Research / Learning Authority

| Component | Location | Responsibility | Evidence | Authoritative? |
|-----------|----------|----------------|----------|:-:|
| `research_engine/` | Research package | Read-only analysis of persisted data | `research_engine/main.py`, `horizon_research.py`, `data_access/loaders.py` | ✅ YES — isolated research layer |
| `core/horizon/research_contract.py` | Research contracts | Define expected horizon behaviour | V1 contracts for SCALP/INTRADAY/EXTENDED | ✅ YES — hypothesis authority |
| `core/horizon/shadow_evaluation.py` | Shadow evaluation | Assess activation readiness | Reads shadow results, produces readiness reports | ✅ YES |
| `core/learning/store.py` | Learning persistence | Persist learning insights | Records learning observations | ✅ YES |
| `PORTFOLIO_RANKING_AUTHORITY=False` | Config | Controls whether research findings affect execution | Currently passive (research observes but does not control) | ✅ YES — governance flag |

**Finding:** Research is strictly read-only and isolated from execution. It cannot modify config, place orders, or change decisions. Learning outputs exist but are not yet connected to automatic parameter modification (Level 4 → Level 5 gap).

---

## 3. Control Flow Summary (Who Can Veto What)

```
main.py
    │ Can: refuse to start (config validation fails)
    ▼
live_scanner (cycle)
    │ Can: skip cycle (cycle_guards block)
    ▼
pre_engine_gates
    │ Can: skip engine evaluation (session/pattern gate)
    ▼
new_engine (decision)
    │ Produces: EXECUTE or NO_TRADE
    │ Can: reject (score below threshold, policy block)
    ▼
HorizonExecutionAuthority
    │ Can: block (slot occupied, symbol full, portfolio full)
    ▼
runtime_guard_chain (10 guards)
    │ Can: block (daily limit, cooldown, correlation, exposure, regime, spread...)
    ▼
ExecutionOrchestrator
    │ Can: fail (broker rejects, timeout, connection lost)
    ▼
MT5 Broker
    │ Can: reject (invalid price, margin, symbol disabled)
    ▼
Position registered → Trade management begins
```

**7 layers of veto exist between opportunity detection and a live trade.**

---

## 4. Missing Governance (Gaps Identified)

| # | Gap | What's Missing | Impact | Severity |
|---|-----|---------------|--------|:--------:|
| 1 | Mission document | No `MISSION.md` or `PURPOSE.md` stating why the project exists | Team alignment. Future contributors. | LOW |
| 2 | Change control | No formal process for modifying `config.py` in production | Config changes have immediate live effect | MEDIUM |
| 3 | Deployment authority | No document defining who/what can deploy to the production VM | Deployment is ad-hoc | MEDIUM |
| 4 | Research → Execution bridge | No formal process for when research findings should change live params | Learning outputs exist but activation is manual | LOW (by design currently) |
| 5 | Version authority | No `VERSION` file or release tagging | Cannot determine what code version produced a given trade | MEDIUM |
| 6 | Incident authority | No runbook for "what to do when things go wrong" | Debugging relies on knowledge in developer's head | LOW |

### What IS Well-Governed

| Governance Area | Status |
|----------------|:------:|
| Configuration single source | ✅ `core/config.py` |
| Execution kill switch | ✅ `EXECUTION_ENABLED` |
| Data ownership (one writer per dataset) | ✅ Enforced by test |
| Forbidden field contamination | ✅ Enforced at write time |
| S3 write permission | ✅ Allowlist enforced by test |
| Decision authority (single engine) | ✅ `USE_NEW_PIPELINE=True` |
| Risk veto chain | ✅ 7 layers documented |
| Research isolation (cannot affect execution) | ✅ By architecture |
| Architecture principles | ✅ `SYSTEM_INTELLIGENCE_PRINCIPLES.md` |

---

## 5. True "Top of the Pyramid"

The actual governance hierarchy, as implemented:

| Layer | Authority | Document/Component |
|:-----:|:---------:|:------------------:|
| **1** | Architecture Principle | `architecture/SYSTEM_INTELLIGENCE_PRINCIPLES.md` |
| **2** | Configuration | `core/config.py` (all runtime behaviour defined here) |
| **3** | Process Lifecycle | `main.py` (boot/shutdown/mode) |
| **4** | Runtime Loop | `core/runtime/live_scanner.py` (cycle orchestration) |
| **5** | Decision | `core/pipeline/new_engine.py` (EXECUTE / NO_TRADE) |
| **6** | Risk Veto | `risk/runtime_guard_chain.py` + `HorizonExecutionAuthority` |
| **7** | Execution | `execution/execution_orchestrator.py` (broker orders) |
| **8** | Post-Trade | `core/trade_management/manager.py` (position lifecycle) |
| **9** | Persistence | 23 registered writers (immutable append-only) |
| **10** | Research | `research_engine/` + `core/horizon/` research modules |

**`core/config.py` is the single most powerful component in the system.** It can:
- Disable all execution (`EXECUTION_ENABLED=False`)
- Change what horizons trade (`PERMITTED_HORIZONS`)
- Enable/disable every feature flag
- Change all risk limits
- Switch between live and replay mode

No other file has this breadth of control.

---

*End of Top-Level System Authority Audit.*
