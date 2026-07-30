# Phase D — V10 Full Runtime Authority Audit (Final)

**Post-Phase C: Horizon authority removed. All V10 layers operational.**

---

## 1. Complete V10 Runtime Path (EXECUTE trace)

```
MT5 Data Feed
    │ last_tick() + fetch_bar()
    ▼
live_scanner.py: per-symbol loop
    │ bar provision, tick freshness
    ▼
Pre-engine gates (kill switch, daily loss, patterns)
    │ If blocked → NO_TRADE (safety — valid)
    ▼
ENGINE_MODE check → "V10"
    │
    ▼
run_v10_cycle() [core/v10/scanner_adapter.py]
    │
    ├── get_account_context() → AccountContext (from MT5)
    ├── get_broker_context() → BrokerContext (from MT5)
    │
    ├── build_market_understanding() → MarketUnderstanding
    ├── build_v3_market_context() → V3MarketContext
    │
    └── V10Pipeline.process() [core/v10/pipeline.py]
            │
            ├── build_v10_market_state() → V10MarketState
            ├── assess_opportunity() → OpportunityAssessment
            ├── select_strategy() → StrategyDecision
            ├── assess_horizon() → HorizonDecision
            ├── build_entry_decision() → EntryDecision
            ├── assess_risk() → RiskDecision (with BrokerContext for exact sizing)
            └── build_execution_decision() → ExecutionDecision
                    │
                    ▼
            PipelineResult (approved=True)
                    │
                    ▼
    _build_order_intent() → OrderIntent [core/v10/scanner_adapter.py]
                    │
                    ▼
    persist_v10_full() + format_v10_decision() [persistence + report]
                    │
                    ▼
    Return to live_scanner: {"action": "EXECUTE", "intent": OrderIntent, ...}
                    │
                    ▼
live_scanner.py: prepare_execution()
    │ Generates correlation_id, persists decision audit, opens shadow trade
    ▼
TradeDecision object constructed (from OrderIntent)
    │
    ▼
HORIZON AUTHORITY CHECK → SKIPPED (ENGINE_MODE == "V10")
    │
    ▼
Runtime Guard Chain [risk/runtime_guard_chain.py]
    │ evaluate_runtime_guards(intent, ...)
    │ Checks: spread, cooldown, correlation, position limit
    │ Can BLOCK (safety) — cannot MODIFY
    ▼
ExecutionOrchestrator → MT5Execution.execute()
    │ Builds MT5 request, submits order
    ▼
Broker fill/reject
    │
    ▼
Post-execution handling (events, ledger, shadow trade close)
```

**Every function in this path is identified. No hidden decision points.**

---

## 2. Legacy Decision Authority Search

| Search Term | Found? | In V10 Active Path? | Classification |
|---|---|---|---|
| `run_new_engine` | line 465, 470 | NO (guarded: `if _engine_mode == "V10": pass`) | **C) Legacy but inactive** |
| `composite_score` | NOT FOUND in core/ | — | Dead concept |
| `strategy_score` | `core/pipeline/market_state_engine.py` | NO (legacy engine only) | **C) Legacy but inactive** |
| `neutral_score` | `core/pipeline/execution_policy.py` | NO (legacy engine only) | **C) Legacy but inactive** |
| `MIN_SCORE_TO_TRADE` | `config.py`, `scoring_engine.py` | NO (only read by `run_new_engine`) | **C) Legacy but inactive** |
| `confidence_threshold` | `new_engine.py` | NO (guarded) | **C) Legacy but inactive** |
| `pattern_gate` | `decision_ledger.py` mapping only | NO (label, not logic) | **C) Legacy but inactive** |
| `grade` | `audit_persistence.py`, `output_router.py` | NO (reporting only) | **D) Observational** |
| `HorizonExecutionAuthority` | `live_scanner.py` line 1007 | NO (wrapped: `if _engine_mode != "V10":`) | **C) Legacy but inactive** |
| `PERMITTED_HORIZONS` | `config.py`, `execution_authority.py` | NO (authority bypassed under V10) | **C) Legacy but inactive** |
| `candidate_score` | NOT FOUND | — | Dead concept |
| `signal_validator` | NOT FOUND | — | Dead concept |

**ZERO legacy decision authority found in V10 active path.**

---

## 3. Scoring Influence

| Question | Answer |
|---|---|
| Does any score influence EXECUTE/NO_TRADE? | **NO** — V10 uses opportunity quality + strategy confidence + risk approval |
| Does any score influence position size? | **NO** — V10 uses `calculate_position_size_exact(tick_value, ...)` |
| Does any score influence strategy selection? | **NO** — V10 `select_strategy()` evaluates conditions, not scores |
| Are legacy scores computed under V10? | NO — `run_new_engine` doesn't execute |
| Is `MIN_SCORE_TO_TRADE` checked? | NO — only inside `run_new_engine` |

---

## 4. Strategy Authority

| Check | Status |
|---|---|
| StrategyEngine decides family | ✓ `select_strategy()` in `strategy_engine.py` |
| Nothing replaces strategy downstream | ✓ Frozen in `StrategyDecision` dataclass |
| No "unknown strategy" rejection exists | ✓ NONE returns cleanly, entry becomes INVALID |
| No legacy strategy filter applies | ✓ All strategy filtering is inside V10 |
| No whitelist/blacklist constrains strategies | ✓ All 6 families available |

---

## 5. Entry Authority

| Check | Status |
|---|---|
| EntryDecision controls entry_price | ✓ |
| EntryDecision controls stop | ✓ (StopReference.price) |
| EntryDecision controls target | ✓ (TargetReference.price) |
| EntryDecision controls method | ✓ (CONFIRMATION/LIMIT/BREAK) |
| OrderIntent preserves SL from EntryDecision | ✓ `intent.sl = entry.stop_reference.price` |
| OrderIntent preserves TP from EntryDecision | ✓ `intent.tp = entry.target_reference.price` |
| Nothing modifies SL/TP after V10 | ✓ OrderIntent is frozen |
| prepare_execution modifies values? | NO — it only generates IDs and persists |

---

## 6. Risk Authority

| Check | Status |
|---|---|
| Position sizing from RiskEngine | ✓ `calculate_position_size_exact()` |
| Uses broker tick_value | ✓ When available |
| Uses volume_min/max/step | ✓ Clamps and rounds |
| Execution validates volume limits | ✓ Additional broker check |
| Nothing changes direction after risk | ✓ |
| Nothing changes strategy after risk | ✓ |
| Nothing changes horizon after risk | ✓ |

---

## 7. Hidden Blocker Inventory (Complete)

### VALID SAFETY (keep):

| # | Blocker | Location | Can Modify V10? |
|---|---|---|---|
| 1 | Pre-engine kill switch | `live_scanner.py` pre_engine_gates | NO — blocks entire cycle |
| 2 | Daily loss cycle block | `cycle_guards.py` | NO — blocks entire cycle |
| 3 | Drawdown cycle block | `cycle_guards.py` | NO — blocks entire cycle |
| 4 | V10 Risk: balance=0 | `risk_engine.py` | NO — rejects cleanly |
| 5 | V10 Risk: R:R too low | `risk_engine.py` | NO — rejects cleanly |
| 6 | V10 Risk: max positions | `risk_engine.py` | NO — rejects cleanly |
| 7 | V10 Risk: daily loss limit | `risk_engine.py` | NO — rejects cleanly |
| 8 | V10 Execution: disconnected | `execution_engine.py` | NO — rejects cleanly |
| 9 | V10 Execution: market closed | `execution_engine.py` | NO — rejects cleanly |
| 10 | V10 Execution: spread too high | `execution_engine.py` | NO — rejects cleanly |
| 11 | V10 Execution: volume invalid | `execution_engine.py` | NO — rejects cleanly |
| 12 | V10 Execution: stops_level violated | `execution_engine.py` | NO — rejects cleanly |
| 13 | Guard chain: spread guard | `runtime_guard_chain.py` | NO — blocks only |
| 14 | Guard chain: cooldown | `runtime_guard_chain.py` | NO — blocks only |
| 15 | Guard chain: correlation | `runtime_guard_chain.py` | NO — blocks only |
| 16 | Guard chain: position limit | `runtime_guard_chain.py` | NO — blocks only |

### LEGACY DECISION (disabled):

| # | Blocker | Location | Active Under V10? |
|---|---|---|---|
| 17 | Horizon authority | `live_scanner.py` line 1004 | **NO** — wrapped in `if _engine_mode != "V10":` |
| 18 | `run_new_engine` scoring | `live_scanner.py` line 470 | **NO** — guarded by `if _engine_mode == "V10": pass` |
| 19 | `MIN_SCORE_TO_TRADE` | `scoring_engine.py` | **NO** — never called |
| 20 | `_MIN_NEUTRAL_SCORE` | `execution_policy.py` | **NO** — never called |

**None of blockers #17-20 can execute under ENGINE_MODE="V10".**

---

## 8. Persistence Lineage

### Root identity: `observation_id`

Generated in `opportunity_engine.py`:
```python
observation_id = sha256(f"{symbol}_{timestamp}")[:16]
```

### Propagation:

```
observation_id
    ├── OpportunityAssessment.observation_id
    ├── StrategyDecision.opportunity_id
    ├── HorizonDecision.opportunity_id
    ├── EntryDecision.opportunity_id
    ├── RiskDecision.opportunity_id
    ├── ExecutionDecision.opportunity_id
    ├── OrderIntent.risk_id
    ├── OrderIntent.metadata["decision_id"]
    ├── V10DecisionRecord.decision_id
    └── V10LedgerEntry.correlation_id / entity_id
```

### Competing IDs (exist but are CHILDREN):

| ID | Source | Relationship |
|---|---|---|
| `correlation_id` | `prepare_execution()` | Generated for execution audit — CHILD of decision |
| `decision_id` (uuid) | `prepare_execution()` | Audit trail ID — CHILD |
| `shadow_trade_id` | Shadow engine | `"shadow_{cycle}_{symbol}"` — CHILD |

**One root identity (`observation_id`) traces through all layers.**

---

## 9. Final Authority Map

### GREEN (Decision Authority — V10 only):

| Component | Authority |
|---|---|
| `V10MarketState` | What the market IS |
| `OpportunityAssessment` | Whether opportunity exists |
| `StrategyDecision` | What type of trade |
| `HorizonDecision` | Expected movement magnitude |
| `EntryDecision` | How to enter (method, price, stop, target) |
| `RiskDecision` | Whether risk is acceptable |
| `ExecutionDecision` | Whether broker conditions permit |
| `OrderIntent` | Final immutable order (translated from V10) |

### YELLOW (Observational — logs/persists, no authority):

| Component | Purpose |
|---|---|
| Shadow opportunity layer | Research persistence |
| Assessment builder | Research record |
| Horizon classifier (legacy) | Shadow observation |
| Bias FSM | Legacy state tracking |
| Score tracker | Research logging |
| Decision funnel | Diagnostics |
| Market context builder | V3 shadow observation |

### RED (Can override V10 — NONE FOUND):

**No component in the active V10 runtime path can override direction, strategy, horizon, entry, stop, target, or position size.**

---

## Final Verdict

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   V10 IS THE SINGLE DECISION AUTHORITY                           ║
║                                                                  ║
║   • 7 pipeline stages execute in sequence                        ║
║   • No legacy scoring in active path                             ║
║   • No strategy/horizon/entry overrides after V10                ║
║   • OrderIntent preserves all V10 values (frozen)                ║
║   • Execution bridge translates without modification             ║
║   • Horizon authority bypassed under V10                         ║
║   • Only safety guards remain (spread/margin/cooldown)           ║
║   • One root identity (observation_id) traces all layers         ║
║   • Both EXECUTE and NO_TRADE decisions fully persisted          ║
║                                                                  ║
║   STATUS: GREEN — V10 owns the complete decision path            ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

### Remaining work for production deployment:

| Item | Type | Priority |
|---|---|---|
| Outcome linker (trade result → decision_id) | Feature | HIGH |
| S3 mirror for V10 decisions | Infrastructure | MEDIUM |
| Config migration (hardcoded values → config.py) | Maintenance | LOW |
| Legacy code cleanup (remove dead imports) | Housekeeping | LOW |

**The architecture is complete. V10 makes all decisions. Safety protects capital. MT5 executes orders.**
