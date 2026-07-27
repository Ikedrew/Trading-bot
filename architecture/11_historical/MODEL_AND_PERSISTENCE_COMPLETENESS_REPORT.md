# MODEL AND PERSISTENCE COMPLETENESS REPORT

> **STATUS: SUPERSEDED.** This report predates the persistence completion work. For current persistence architecture (24/24 datasets, all S3-mirrored, all versioned), see `local_+_s3_persistence/PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md`.

**Generated:** 2026-07-14
**Verified against:** Live runtime trace

---

## LIFECYCLE DIAGRAM: Candle → Outcome

```
MT5 CANDLE ARRIVES
    │
    ▼
BAR DEDUP (same bar? → skip)
    │
    ▼
EXECUTION CONTEXT #1 persisted ──────────────► logs/execution_context/ + S3
    │                                          (EVERY new bar, generic cor_id)
    ▼
DECISION STATE INITIALIZED
    │
    ├── KILL SWITCH? → LEDGER + continue
    ├── DAILY LOSS?  → LEDGER + continue
    ├── SESSION?     → LEDGER + continue
    │
    ▼
PATTERN GATE
    │
    ├── NO PATTERNS → LEDGER (PATTERN_REJECT) + continue
    │                 ⚠️ NO assessment persisted
    │                 ⚠️ NO decision audit
    │                 ⚠️ NO decision trace
    │
    ▼
run_new_engine()
    │
    ├── ASSESSMENT CONSTRUCTED ──────────────► logs/opportunity_assessment_log/
    │   (persisted BEFORE any policy gate)     (local JSONL only, NO S3)
    │
    ├── REASONING generated
    ├── UNCERTAINTY computed  
    ├── ATTRIBUTION computed
    ├── Assessment enriched (uncertainty_score, confidence_modifier, evidence_contributions)
    │
    ▼
ENGINE RETURNS
    │
    ▼
DECISION TRACE persisted ────────────────────► logs/decision_trace/
    │                                          (local JSONL only, NO S3)
    ▼
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  ┌── NO_TRADE PATH ──┐    ┌── EXECUTE PATH ──────────┐ │
│  │                    │    │                          │ │
│  │ DECISION AUDIT ────┤    │ CORRELATION_ID generated │ │
│  │  ► local + S3      │    │                          │ │
│  │                    │    │ DECISION AUDIT ──────────┤ │
│  │ LEDGER ────────────┤    │  ► local + S3            │ │
│  │  ► local + S3      │    │                          │ │
│  │                    │    │ EXEC CONTEXT #2 ─────────┤ │
│  │ ✗ No exec_ctx #2  │    │  ► local + S3 (trade     │ │
│  │ ✗ No shadow trade  │    │    correlation_id)       │ │
│  │ ✗ No execution     │    │                          │ │
│  │                    │    │ SHADOW TRADE ────────────┤ │
│  └────────────────────┘    │  ► local + S3            │ │
│                            │                          │ │
│                            │ RUNTIME GUARDS ──────────┤ │
│                            │  (10 sequential gates)   │ │
│                            │  ├── blocked → LEDGER    │ │
│                            │  └── passed ↓            │ │
│                            │                          │ │
│                            │ EXECUTION ───────────────┤ │
│                            │  ► MT5 broker            │ │
│                            │  ► execution event       │ │
│                            │     (REJECTED by         │ │
│                            │      event_stream        │ │
│                            │      allowlist!)         │ │
│                            │                          │ │
│                            │ LEDGER (EXECUTE) ────────┤ │
│                            │  ► local + S3            │ │
│                            │                          │ │
│                            │ ... LATER (trade close): │ │
│                            │ TRADE TRUTH ─────────────┤ │
│                            │  ► local + S3            │ │
│                            └──────────────────────────┘ │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## RECORDS CREATED PER CYCLE

| Record | NO_TRADE (no pattern) | NO_TRADE (engine reject) | EXECUTE | S3 Mirrored? |
|--------|----------------------|--------------------------|---------|-------------|
| execution_context (generic) | ✅ | ✅ | ✅ | ✅ |
| opportunity_assessment | ❌ | ✅ | ✅ | ❌ LOCAL ONLY |
| decision_trace | ❌ | ✅ | ✅ | ❌ LOCAL ONLY |
| decision_audit | ❌ | ✅ | ✅ | ✅ |
| decision_ledger | ✅ | ✅ | ✅ | ✅ |
| execution_context (trade) | ❌ | ❌ | ✅ | ✅ |
| shadow_trade | ❌ | ❌ | ✅ | ✅ |
| execution_event | ❌ | ❌ | ✅ (emitted) | ❌ REJECTED BY ALLOWLIST |
| trade_truth | ❌ | ❌ | ✅ (at close) | ✅ |
| trade_truth_graph | ❌ | ❌ | ❌ (offline) | ❌ OFFLINE ONLY |
| edge_attribution | ❌ | ❌ | ❌ (offline) | ❌ OFFLINE ONLY |

---

## WHY ONLY 4 S3 FOLDERS POPULATE

**Currently populated during live trading:**
1. `events/` — CANDLE, FEATURE_UPDATE, FEED_HEALTH, SYSTEM_HEALTH (observation types only)
2. `decision_ledger/` — every cycle (buffered, flushes every 50/30s)
3. `execution_context/` — every new bar
4. `decision_audit/` — every engine evaluation

**Only populated on EXECUTE (rare when no trades):**
5. `shadow_trades/` — only on EXECUTE decisions

**Only populated at trade CLOSE (requires a trade to have been opened first):**
6. `trades/` — trade_truth records

**NEVER populated during live runtime:**
7. `trade_truth_graph/` — offline only
8. `edge_attribution/` — offline only
9. `edge_optimisation/` — offline only
10. `strategy_compiler/` — offline only
11. `learning/` — offline analysis only

---

## WHICH EVENT TYPES ARE MISSING FROM S3

The `events/` folder only accepts these types (strict allowlist):
```
CANDLE, FEATURE_UPDATE, SESSION_TRANSITION, SESSION_STATE,
LATENCY_OBSERVATION, FEED_HEALTH, DATA_GAP, RECONNECT,
SYSTEM_HEALTH, PIPELINE_HEALTH, CLOCK_SYNC
```

**Rejected by allowlist (data LOST from events/ layer):**
- `DECISION` — decision audit calls `emit_decision()` which is rejected
- `STRATEGY` — strategy trace calls `emit_strategy()` which is rejected
- `ENTITY` — entity tracker calls `emit_entity()` which is rejected
- `EXECUTION` — execution event calls `emit_execution()` which is rejected
- `BIAS_CHANGE` — emitted but type not in allowlist → REJECTED
- `RISK_CHECK` — emitted from risk_event_emitter → REJECTED

**These ARE persisted via their OWN dedicated persistence layers** (decision_audit, decision_ledger, etc.) so the data is NOT lost — it's just not in the events/ folder.

---

## POINTS WHERE INFORMATION BECOMES NULL OR DISAPPEARS

### 1. Pattern Gate Rejection: Maximum Information Loss

When `_raw_patterns` is empty, the cycle terminates with:
- ❌ No OpportunityAssessment (engine never called)
- ❌ No DecisionTrace (engine never called)
- ❌ No DecisionAudit (engine never called)
- ✅ Decision Ledger records `PATTERN_REJECT` (but with zero analytical content)
- ✅ Execution Context exists (captured before pattern gate)

**Information lost:** WHY no patterns were detected. The components that would explain "what the market looked like" are never computed.

### 2. "no_viable_pattern" Exit Inside Engine

When `run_new_engine()` returns immediately because `best_pattern is None`:
- ❌ Assessment = None (returned on result dict)
- ❌ No components computed
- ❌ No scoring, no market state, no strategy classification
- ✅ entity_id IS populated
- ✅ DecisionTrace IS persisted (with minimal data)
- ✅ DecisionAudit IS persisted (with minimal data)

**Information lost:** Same as above — no analytical breakdown for pattern-free bars.

### 3. Execution Event Rejection

The execution layer calls `_emit_execution_event()` which calls `emit_execution()` which is REJECTED by the event_stream allowlist. The execution event data (fill_price, slippage, latency, retcode) is:
- ✅ Logged to console
- ✅ Stored in ExecutionResult (in-memory)
- ❌ NOT persisted to events/ or S3 via event_stream
- ✅ Partially captured in decision_ledger.execution_intent (but not fill details)

**Information lost at S3:** Actual fill price, slippage, fill latency, broker retcode. These exist only in console logs.

### 4. Reasoning/Uncertainty/Attribution on NO_TRADE

On the NO_TRADE path, reasoning/uncertainty/attribution are attached to `_cycle_decision` and flow to the ledger. But:
- They're on the decision_ledger record (✅)
- They're on the decision_audit record (partially — audit captures engine_result fields)
- They're on the DecisionTrace (✅)
- They're NOT on the opportunity_assessment_log (assessment doesn't carry reasoning post-hoc)

**Information preserved but fragmented:** The reasoning exists on 3 different records for the same cycle.

### 5. Trade Outcome Linkage

When a trade closes, `trade_truth` is written with `correlation_id`. But:
- The `entity_id` is NOT on trade_truth (only correlation_id)
- Join path: entity_id → decision_audit.correlation_id → trade_truth.correlation_id
- Direct entity_id → outcome query requires multi-hop join

---

## MODEL OWNERSHIP MAP

### OpportunityAssessment
| Property | Value |
|----------|-------|
| Created by | `core/pipeline/new_engine.py` |
| Consumed by | execution_policy, expected_value, risk_manager, reasoning, uncertainty, attribution |
| Persisted by | `core/persistence/opportunity_assessment_writer.py` |
| Serialized by | `.to_dict()` method |
| Queried from | `logs/opportunity_assessment_log/{SYMBOL}/{DATE}.jsonl` |
| Authority | PRIMARY — analytical state boundary |
| S3 Mirror | ❌ NONE |

### DecisionTrace  
| Property | Value |
|----------|-------|
| Created by | `core/decision_trace.py` `build_decision_trace()` |
| Consumed by | DecisionFunnel, console display |
| Persisted by | `persist_decision_trace()` |
| Queried from | `logs/decision_trace/{SYMBOL}/{DATE}.jsonl` |
| Authority | PRIMARY — diagnostic funnel |
| S3 Mirror | ❌ NONE |

### Decision Ledger Entry
| Property | Value |
|----------|-------|
| Created by | `core/decision_ledger.py` `build_ledger_entry()` |
| Consumed by | Offline analysis, DuckDB |
| Persisted by | `DecisionLedgerWriter` (buffered) |
| Queried from | `logs/decision_ledger/{SYMBOL}/{DATE}.jsonl` |
| Authority | PRIMARY — every-cycle canonical record |
| S3 Mirror | ✅ `decision_ledger/symbol={SYMBOL}/date={DATE}/` |

### Decision Audit Record
| Property | Value |
|----------|-------|
| Created by | `core/decision_audit.py` `persist_new_engine_decision_audit()` |
| Consumed by | Offline analysis, lifecycle linkage |
| Persisted by | Same function (JSONL + S3) |
| Queried from | `logs/decision_audit/{SYMBOL}_{DATE}.jsonl` |
| Authority | HUB — links all identity fields (entity_id + decision_id + correlation_id) |
| S3 Mirror | ✅ `decision_audit/symbol={SYMBOL}/date={DATE}/` |

### OrderIntent
| Property | Value |
|----------|-------|
| Created by | `risk/manager.py` `_execute_risk()` |
| Consumed by | `execution/mt5_execution.py` |
| Persisted by | Serialized into decision_ledger.execution_intent |
| Authority | Boundary object: risk → execution |
| S3 Mirror | ✅ (via decision_ledger) |

### ExecutionResult
| Property | Value |
|----------|-------|
| Created by | `execution/mt5_execution.py` |
| Consumed by | live_scanner (fill_price, ok/retcode) |
| Persisted by | ❌ NOT directly persisted. Partial data in ledger. |
| Authority | Execution response |
| S3 Mirror | ❌ Execution event REJECTED by allowlist |

### TradeTruth
| Property | Value |
|----------|-------|
| Created by | `core/trade_truth.py` `build_trade_truth()` |
| Consumed by | Learning engine, trade_truth_graph |
| Persisted by | Same module (JSONL + S3) |
| Queried from | `logs/trade_truth/{SYMBOL}/{DATE}.jsonl` |
| Authority | PRIMARY — trade outcome truth |
| S3 Mirror | ✅ `trades/{SYMBOL}/{DATE}.jsonl` |

### ShadowTrade
| Property | Value |
|----------|-------|
| Created by | `core/shadow_trades.py` `open_trade()` |
| Consumed by | Shadow engine lifecycle (evaluate_bar) |
| Persisted by | Same module (on close: JSONL + S3) |
| Authority | Shadow lifecycle tracking |
| S3 Mirror | ✅ `shadow_trades/` |

---

## IDENTITY FIELD LIFECYCLE

| Field | Created At | Available On | Persisted To | Can Be NULL? | NULL Reason |
|-------|-----------|-------------|-------------|-------------|-------------|
| `entity_id` | new_engine.py (top) | ALL return paths | assessment, audit, ledger, trace | Only if engine not reached (pattern gate skip) | Pattern gate blocks before engine |
| `cycle_id` | live_scanner (loop counter) | ALL records | ALL persistence | Never NULL | Always available |
| `correlation_id` | live_scanner (EXECUTE only) | EXECUTE path records | audit, ledger, exec_ctx, shadow_trade, execution_event | YES on NO_TRADE | Only generated on EXECUTE |
| `decision_id` | decision_audit.py (UUID) | audit records | audit only | YES if audit disabled | Config gate |
| `runtime_session_id` | live_scanner (init) | audit records | audit only | Empty string if not passed | Always passed since implementation |
| `trade_id` | shadow_trades / trade_truth | trade outcome | shadow_trades, trade_truth | Never NULL on EXECUTE | Only created on EXECUTE |
| `symbol` | config / MT5 | ALL | ALL | Never NULL | — |

---

## PRIORITY FIXES

### 1. CRITICAL: Execution Events Lost (data exists but rejected)

**Problem:** `_emit_execution_event()` → `emit_execution()` → REJECTED by allowlist  
**Impact:** Fill price, slippage, latency, retcode never reach S3  
**Fix:** Either add `EXECUTION` to the event_stream allowlist, OR persist execution results through a dedicated writer (similar to decision_audit)

### 2. HIGH: OpportunityAssessment has no S3 mirror

**Problem:** Assessment log is local-only. If VM dies, all assessment data is lost.  
**Impact:** Cannot reconstruct scoring components for historical decisions  
**Fix:** Add `_write_s3()` to `opportunity_assessment_writer.py` (same pattern as decision_audit)

### 3. HIGH: DecisionTrace has no S3 mirror

**Problem:** Decision trace (the diagnostic funnel source) is local-only.  
**Impact:** Best diagnostic data not durable  
**Fix:** Add `_write_s3()` to `persist_decision_trace()` in `decision_trace.py`

### 4. MEDIUM: Pattern gate produces no analytical record

**Problem:** When no patterns are detected (67% of cycles), only a minimal ledger entry is written  
**Impact:** Cannot analyze WHY patterns aren't being detected (market conditions, candle structure)  
**Fix:** Emit a lightweight market snapshot (at minimum: bar OHLC, regime, session) on pattern-free cycles

### 5. MEDIUM: entity_id not on trade_truth

**Problem:** trade_truth links via correlation_id only. Direct entity→outcome requires multi-hop.  
**Impact:** Complicates learning engine queries  
**Fix:** Add entity_id field to `build_trade_truth()` (it's available via correlation_id → decision_audit lookup, but should be direct)

### 6. LOW: Duplicate execution_context on EXECUTE path

**Problem:** Two execution_context records per EXECUTE cycle (generic + trade-specific)  
**Impact:** Storage waste, query confusion  
**Fix:** Skip the generic one on cycles that will produce a trade-specific one (or merge them)

### 7. LOW: Reasoning fragmented across 3 records

**Problem:** DecisionReasoning exists on audit, ledger, AND trace for same cycle  
**Impact:** Storage waste, potential inconsistency if one fails  
**Fix:** Acceptable trade-off (redundancy = durability). No change needed.

---

## SCHEMA COMPLETENESS STATUS

| Model | Healthy? | Issue |
|-------|----------|-------|
| OpportunityAssessment | ⚠️ | No S3 mirror. Missing on "no_viable_pattern" exits. |
| DecisionTrace | ⚠️ | No S3 mirror. Missing on pattern-gate-reject cycles. |
| Decision Ledger | ✅ | Complete — every cycle, all fields, S3 mirrored |
| Decision Audit | ✅ | Complete — both paths, S3 mirrored |
| OrderIntent | ✅ | Healthy — frozen boundary object |
| ExecutionResult | ❌ | NOT persisted. Execution event rejected by allowlist. |
| TradeTruth | ⚠️ | Missing entity_id field |
| ShadowTrade | ✅ | Healthy — S3 mirrored |
| TradeTruthGraph | ⚠️ | Offline-only, never written during live trading |
| EdgeAttribution | ⚠️ | Offline-only |
