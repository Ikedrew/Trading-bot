# OBSERVER BLUEPRINT

**Status:** Governing design contract. No implementation yet.
**Purpose:** Define the complete Observer vision before code is written.
**Principle:** "I understand the whole machine. Ask me anything and I know where the answer lives."

---

## 1. Observer Mission

### Why It Exists

The trading system has grown beyond what a single person can hold in memory. It contains:
- 25+ core subpackages
- 9 top-level source packages
- 24 persistence datasets with 600+ fields
- 84 architecture documents
- 7 authority layers
- 10 runtime guards
- Multiple decision paths with different evidence chains

No human can reliably answer "why did the system do X?" without spending 20+ minutes searching through logs, traces, and code.

### What Problem It Solves

The Observer removes the burden of manual system archaeology. Instead of a developer searching through `decision_trace`, `decision_ledger`, `execution_results`, and `trade_truth` to reconstruct an explanation — the Observer knows where each piece of evidence lives and can assemble the answer.

### What Human Burden It Removes

| Without Observer | With Observer |
|:---------------:|:------------:|
| "Let me grep through 5 JSONL files to figure out why EURUSD didn't trade" | "Observer, why didn't EURUSD trade?" → immediate evidence chain |
| "Which of these 84 documents explains the guard chain?" | "Observer, where is the risk authority documented?" → exact file + section |
| "Is the persistence layer healthy? Let me check each dataset manually" | "Observer, system health?" → all 24 datasets checked in seconds |
| "What changed since last week?" | "Observer, what's different?" → config diff + new datasets + architecture changes |

---

## 2. Position in System Hierarchy

```
┌──────────────────────────────────────┐
│          HUMAN OWNER                  │
│  Makes strategic decisions.           │
│  Approves changes. Sets direction.    │
└──────────────────┬───────────────────┘
                   │ asks questions, receives explanations
                   ▼
┌──────────────────────────────────────┐
│     SYSTEM INTELLIGENCE LAYER         │
│                                      │
│  ┌────────────────────────────────┐  │
│  │         OBSERVER               │  │
│  │  Reads. Queries. Explains.     │  │
│  │  Never modifies. Never acts.   │  │
│  └────────────────────────────────┘  │
│                                      │
│  Reads ALL. Writes NOTHING.          │
└──────────────────┬───────────────────┘
                   │ observes (read-only)
                   ▼
┌──────────────────────────────────────┐
│         TRADING SYSTEM                │
│                                      │
│  config → runtime → decision →       │
│  risk → execution → management →     │
│  persistence → research              │
└──────────────────────────────────────┘
```

### Authority Rules

- The **Owner** makes decisions (enable horizons, change thresholds, deploy)
- The **Observer** advises and explains (reports state, traces causality, identifies gaps)
- The **Trading System** executes (trades, persists, manages positions)
- The Observer **CANNOT** override trading authorities, modify config, or place orders

---

## 3. Observer Responsibilities

### 3.1 System Understanding

The Observer knows the machine's architecture:

| Knowledge | Source |
|-----------|--------|
| What modules exist | Source code packages (`core/`, `risk/`, `execution/`, etc.) |
| What each module owns | CAN/CANNOT docstrings + authority audits |
| How modules connect | Import graph + data flow |
| What authorities exist | `TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` |
| What data flows where | `PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md` + `DATA_FLOW_ARCHITECTURE_AUDIT.md` |

### 3.2 Current State Understanding

The Observer knows what the system is doing right now:

| Question | Source |
|----------|--------|
| Is the bot running? | `runtime/heartbeat.json` → status field |
| What config is active? | `core/config.py` (importable, all flags readable) |
| Which symbols enabled? | `config.SYMBOLS` |
| What positions exist? | `logs/trade_journal/` (latest) + heartbeat |
| Are datasets healthy? | File timestamps + record counts in `logs/` |

### 3.3 Causal Understanding

The Observer knows WHY the system behaved as it did:

| Question | Evidence Chain |
|----------|---------------|
| Why pattern detected? | `decision_trace.pattern_name`, `.pattern_quality` |
| Why opportunity scored X? | `decision_trace.components{}` (10 factors) |
| Why strategy selected/rejected? | `decision_trace.selected_strategy`, `.strategy_confidence` |
| Why trade blocked? | `decision_ledger.risk_flag` + `.reason` |
| Why execution failed? | `execution_results.retcode`, `.comment` |
| Why trade lost? | `trade_truth.outcome.r_multiple_realised`, `.exit.exit_reason` |
| What would have happened? | `shadow_trades.simulated_outcome.pnl_r_multiple` |

### 3.4 Change Understanding

The Observer knows what changed:

| Question | Method |
|----------|--------|
| Config changed? | Compare config snapshot to previous |
| New datasets? | Check `logs/` directory for new folders |
| Architecture changed? | Compare document modification dates |
| Behaviour changed? | Decision distribution shift in decision_ledger |

---

## 4. Observer Knowledge Model

```
┌─────────────────────────────────────────────────────────┐
│               OBSERVER KNOWLEDGE SOURCES                  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ARCHITECTURE KNOWLEDGE (static, from docs + code)      │
│  ├── 01_foundation/ (principles, authority)             │
│  ├── Component map (packages, modules, classes)         │
│  ├── Authority boundaries (CAN/CANNOT per module)       │
│  └── Data flow graph (inputs → outputs per component)   │
│                                                         │
│  RUNTIME TRUTH (dynamic, from live state)               │
│  ├── runtime/heartbeat.json (liveness, cycle, MT5)      │
│  ├── core/config.py (all feature flags, limits)         │
│  └── logs/ file timestamps (data freshness)             │
│                                                         │
│  DECISION TRUTH (per-cycle, from persistence)           │
│  ├── decision_ledger (outcome per cycle per symbol)     │
│  ├── decision_trace (component scores, terminal stage)  │
│  └── decision_audit (full snapshot + intent)            │
│                                                         │
│  EXECUTION TRUTH (per-trade, from persistence)          │
│  ├── execution_context (environment at decision time)   │
│  ├── execution_results (broker response)                │
│  └── trade_truth (realised outcome)                     │
│                                                         │
│  TRADE TRUTH (lifecycle, from persistence)              │
│  ├── trade_journal (complete closed trade record)       │
│  └── opportunities (all detected setups + states)       │
│                                                         │
│  LEARNING TRUTH (research, from persistence)            │
│  ├── shadow_trades (counterfactual outcomes)            │
│  ├── edge_attribution (causal decomposition)            │
│  ├── research_reports/ (experiment outputs)             │
│  └── horizon research (contracts, observations)         │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### How Sources Connect

```
Architecture Knowledge → defines WHAT exists
Runtime Truth          → defines WHAT is active
Decision Truth         → defines WHAT was decided
Execution Truth        → defines WHAT happened at broker
Trade Truth            → defines WHAT the outcome was
Learning Truth         → defines WHAT should change
```

Each layer answers a different temporal question:
- Architecture: "What CAN happen?" (design-time)
- Runtime: "What IS happening?" (now)
- Decision: "What WAS decided?" (per-cycle)
- Execution: "What DID the broker do?" (per-trade)
- Trade: "What WAS the result?" (per-position)
- Learning: "What SHOULD change?" (aggregate)

---

## 5. Observer Domain Map (Validated From Repository)

### Discovery Method

Domains were identified by inspecting the actual source tree — not documentation alone:
- `core/` contains 25 subpackages (runtime, pipeline, horizon, trade_management, assessment, opportunity, market_context, learning, persistence, storage, timeframes, etc.)
- `risk/` contains 19 modules (guards, manager, sizing, exposure, correlation, regime, spread, cooldown, etc.)
- `execution/` contains 3 modules (orchestrator, mt5_execution, post_execution_handler)
- `research_engine/` contains multiple subpackages (data_access, experiments, correlation, edge_candidates, etc.)
- `strategy/`, `patterns/`, `data/` are top-level source packages
- `core/config.py` contains ~500 lines of configuration (every runtime flag, limit, and toggle)
- 24 persistence datasets verified via `test_s3_architecture_guard.py` allowlist (23 registered writers)

Each domain below represents a verified functional boundary in the running system.

Derived from actual source code inspection (not documentation alone):

| Domain | Packages | Responsibility | Key Authority |
|--------|----------|----------------|:-------------:|
| **Configuration** | `core/config.py` | All runtime flags, limits, feature toggles | SUPREME |
| **Runtime** | `core/runtime/` (12 modules) | Cycle orchestration, health, recovery | ORCHESTRATION |
| **Decision** | `core/pipeline/` (47 modules) | Pattern → Strategy → Score → Policy → Intent | DECISION |
| **Market Intelligence** | `core/market_context/`, `core/timeframes/`, `core/horizon/` | Regime, structure, multi-TF, horizon classification | CONTEXT |
| **Risk** | `risk/` (19 modules) | Guards, sizing, exposure, correlation | VETO |
| **Execution** | `execution/` (3 modules) | Broker order submission | EXECUTION |
| **Trade Management** | `core/trade_management/` (6 modules) | Position lifecycle: BE, trailing, exit | MANAGEMENT |
| **Persistence** | `core/persistence/`, `core/storage/`, 23 writer modules | 24 datasets, local + S3 | DATA OWNERSHIP |
| **Research** | `research_engine/` (multiple subpackages) | Read-only analysis, experiments | RESEARCH |
| **Learning** | `core/learning/`, `core/edge_attribution.py`, `core/edge_optimisation.py` | Attribution, edge discovery, strategy compilation | LEARNING |
| **Assessment** | `core/assessment/`, `core/opportunity/` | Opportunity scoring + lifecycle | ASSESSMENT |
| **Observability** | `core/event_stream.py`, `core/event_bus.py` | Event persistence, metrics | OBSERVATION |
| **External** | `data/mt5_data.py`, MT5 API, Discord, S3/AWS | Broker, alerting, cloud | EXTERNAL |
| **Patterns** | `patterns/`, `strategy/` | Signal detection, pattern recognition | DETECTION |
| **Infrastructure** | `main.py`, `core/mt5_connection.py`, `core/runtime/shutdown.py` | Process lifecycle, MT5 connection | INFRASTRUCTURE |

**15 domains identified.** The previous 13-section architecture grouping missed: Patterns/Detection (separate from Decision) and Infrastructure (separate from Runtime).

---

## 6. Observer Question Routing Model

The Observer does not search blindly. It has a routing model:

### Route: "Why didn't EURUSD trade today?"

```
1. Check runtime state (heartbeat) → Is bot running?
   └── If SHUTDOWN → answer: "Bot not running since {timestamp}"
   
2. Check decision_ledger for EURUSD today → What decisions were made?
   └── If PATTERN_REJECT → answer: "No patterns detected. Regime was {regime}."
   └── If NO_TRADE → check decision_trace for terminal_stage
       └── "Scored {score} but failed at {stage}: {reason}"
   └── If RISK_BLOCK → answer: "Guard {risk_flag} blocked: {reason}"
   └── If EXECUTE → check execution_results
       └── "Executed but broker rejected: retcode={retcode}"
```

### Route: "Why did this trade lose?"

```
1. Find trade in trade_journal by trade_id or symbol+date
2. Read trade_truth via correlation_id → r_multiple, exit_reason
3. Answer: "Exit reason: {exit_reason}. R = {r_multiple}."
4. If exit_reason = stop_loss → "Stop loss was hit. No execution error. Market-driven outcome."
5. If exit_reason = margin_call → "Broker force-closed. Investigate risk_deviation dataset."
```

### Route: "Is the system healthy?"

```
1. Read heartbeat → status, timestamp, mt5_state
2. Check file freshness → latest record per dataset vs current time
3. Check record counts → today's records vs expected (>0 during market hours)
4. Answer: "Bot {status}. Last active {timestamp}. {N} datasets fresh. {M} stale."
```

### Route: "Where is X documented?"

```
1. Search ARCHITECTURE_KNOWLEDGE_MAP.md for topic keywords
2. Return: file path + section + authority level + confidence
```

### Route: "What changed?"

```
1. Compare current config snapshot to last saved snapshot
2. Check file modification dates in architecture/
3. Check new datasets in logs/ (folders that didn't exist before)
4. Answer: structured diff
```

### Route: "What is the current configuration?"

```
1. Import core.config
2. Read all feature flags: EXECUTION_ENABLED, DRY_RUN, USE_NEW_PIPELINE, PERMITTED_HORIZONS, etc.
3. Read all limits: MAX_OPEN_POSITIONS, MAX_TOTAL_POSITIONS, HORIZON_MAX_POSITIONS_PER_SYMBOL
4. Read all guards: CORRELATION_GUARD_ENABLED, REGIME_GUARD_ENABLED, PORTFOLIO_EXPOSURE_GUARD_ENABLED
5. Answer: structured dict of all active config with categories
```

### Route: "How did this trade perform?"

```
1. Find trade in trade_journal by trade_id or symbol+date
2. Read trade_truth via correlation_id → execution details
3. Read shadow_trades for same cycle → counterfactual
4. Compute: actual vs shadow. Did the right thing happen?
5. Answer: "Trade {id}: entered at {price}, exited at {price}, R={r}. Shadow would have been {shadow_r}."
```

### Route: "What is the research engine showing?"

```
1. Read latest research_reports/ JSON files
2. Read horizon research contracts vs observations
3. Read shadow evaluation activation readiness
4. Answer: "SCALP: validated. INTRADAY: 180 shadow samples, expectancy positive, READY_FOR_REVIEW."
```

---

## 7. Observer Boundaries

### The Observer MUST NOT:

| Forbidden Action | Reason |
|:---------------:|:------:|
| Modify `core/config.py` | Config authority belongs to human owner |
| Place broker orders | Execution authority belongs to `execution_orchestrator.py` |
| Override risk guards | Veto authority belongs to `runtime_guard_chain.py` |
| Create new persistence datasets | Dataset ownership is one-writer-per-dataset (enforced by test) |
| Delete or modify existing records | All persistence is append-only, immutable |
| Make trading decisions | Decision authority belongs to `new_engine.py` |
| Generate strategy recommendations without evidence | Must be evidence-backed or silent |
| Replace existing architecture documents | May annotate, never overwrite |

### The Observer MUST:

| Required Behaviour | Reason |
|:-----------------:|:------:|
| Be read-only | Cannot affect trading behaviour |
| Cite evidence sources | Every answer traceable to a dataset or document |
| Acknowledge uncertainty | If it cannot determine an answer, say so |
| Degrade gracefully | Missing data = "unknown", not crash |
| Never block execution | Fire-and-forget pattern if queried during runtime |

---

## 8. Implementation Phases

### Phase 1: Observer v1 (~15 hours)

**Capabilities:**
- `state()` — Is the bot running? What config is active? Dataset health?
- `explain(symbol)` — Why was the last decision X? What evidence supports it?
- `health()` — Are all 24 datasets receiving records? Freshness check.
- `config_snapshot()` — All feature flags and limits as structured dict.

**Constraints:**
- Read-only. No new persistence.
- Uses existing `research_engine/data_access/loaders.py` patterns.
- ~400 lines of code + ~150 lines tests.

### Phase 2: Intelligence Expansion (~23 hours)

**Capabilities:**
- `architecture()` — Structured model of system components + authorities.
- `route(question)` — Map a question to the data source that answers it.
- `changes()` — Detect config drift, new files, modified architecture.
- `recommend()` — Synthesise health + explain → prioritised suggestions.

**Constraints:**
- Still read-only. Still no new persistence.
- May read architecture markdown files.
- ~500 lines additional code + tests.

### Phase 3: Mature Intelligence (~8 hours)

**Capabilities:**
- Continuous awareness (can detect when documents are stale vs implementation).
- Architecture drift detection (referenced files no longer exist, etc.).
- Guided improvement suggestions (evidence-backed only).

**Constraints:**
- Never auto-implements. Produces recommendations for human review.

---

## 9. Success Criteria

| Criterion | Test |
|-----------|------|
| New engineer can ask "What is this system?" | Observer produces complete system explanation from `SYSTEM_STATE_REPORT.md` + live state |
| Developer can ask "Why did X happen?" | Observer returns evidence chain (decision_trace → specific fields → conclusion) |
| Owner can ask "What should I investigate?" | Observer returns prioritised list with evidence (e.g., "decision_trace shows 80% rejection at risk stage — investigate MIN_SL_DISTANCE threshold") |
| Observer never crashes on missing data | Returns "unknown" or "insufficient data" — never raises |
| Observer answers are verifiable | Every claim references a specific dataset, record, or file that a human can check |

---

## 10. Relationship With Existing Documentation

### Classification (finalised)

| Category | Documents | Observer's Role |
|:--------:|:---------:|:---------------:|
| **Active source of truth** | 01_foundation/, 02_authority/, 07_persistence/ primary docs | Reads and references directly |
| **Specialist reference** | 03_decision/, 04_execution/, 06_market_intelligence/, 09_research/ | Reads when domain-specific question asked |
| **Validation evidence** | 10_validation/ | References for production readiness queries |
| **Historical evidence** | 11_historical/ (31 files) | Does NOT actively read. Only if explicitly asked about history. |
| **Incident records** | 12_incidents/ | References for "has this happened before?" queries |

### The Observer is the Navigation Layer

It does not replace documents. It knows WHICH document answers WHICH question and routes the human there — or extracts the answer directly from persisted data when the question is about runtime behaviour rather than design.

---

*End of Observer Blueprint.*
