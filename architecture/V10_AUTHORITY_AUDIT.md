# V10 End-to-End Authority Audit

---

## Overall Verdict

**V10 is the sole DECISION authority — YES**

**V10 controls EXECUTION end-to-end — NO (gap found)**

---

## 1. Runtime Decision Path — PASS (with gap)

| Step | Status | Evidence |
|---|---|---|
| MT5 data → live_scanner | ✓ | Bar provision, tick fetch |
| live_scanner → scanner_adapter | ✓ | `run_v10_cycle()` called when `ENGINE_MODE == "V10"` |
| scanner_adapter → V10Pipeline | ✓ | `pipeline.process(understanding, context, account, broker)` |
| V10Pipeline → DecisionContext | ✓ | All 7 stages, context built progressively |
| DecisionContext → Persistence | ✓ | `persist_v10_full()` called |
| **V10 result → Execution** | **⚠️ GAP** | See below |

### THE GAP: `prepare_execution` expects `OrderIntent` object

When V10 returns `action=EXECUTE`, the downstream path calls `prepare_execution(new_result=_new_result, ...)` which does:
```python
_intent = new_result["intent"]  # Expects OrderIntent object
```

But V10's scanner adapter returns flat dict fields:
```python
{"action": "EXECUTE", "side": "BUY", "entry_price": 1.09, "stop_loss": 1.088, ...}
```

**There is no `"intent"` key.** This means V10 EXECUTE decisions will crash at line 765 with `KeyError: 'intent'`.

**Impact:** V10 can DECIDE to trade but cannot currently EXECUTE. The execution infrastructure expects the legacy `OrderIntent` object format.

**Fix required:** Either:
- A) Scanner adapter creates an `OrderIntent` from V10's flat fields
- B) Separate V10 execution path bypasses `prepare_execution`

---

## 2. Decision Authority — PASS

| Component | Authority | Can anything else change it? |
|---|---|---|
| Direction (BUY/SELL) | OpportunityAssessment (H1 BOS) | NO — preserved through all layers |
| Strategy family | StrategyEngine | NO — frozen in StrategyDecision |
| Horizon | HorizonEngine | NO — frozen in HorizonDecision |
| Entry method | EntryEngine | NO — frozen in EntryDecision |
| Stop placement | EntryEngine (structural) | NO — frozen in StopReference |
| Target placement | EntryEngine (structural) | NO — frozen in TargetReference |
| Risk approval | RiskEngine | NO — risk can only APPROVE/REJECT |
| Execution | ExecutionEngine | NO — execution can only ALLOW/BLOCK |

**No downstream layer modifies V10's trade plan. Execution gates (spread, margin, volume) can BLOCK but never ALTER.**

---

## 3. Legacy Override Search

| Term | Found in V10 active path? | Classification |
|---|---|---|
| `run_new_engine` | Line 462 — guarded by `if _engine_mode == "V10": pass` | **Legacy only** (does not execute) |
| `composite_score` | NOT FOUND in core/ | Dead concept |
| `strategy_score` | NOT FOUND in core/ | Dead concept |
| `confidence_threshold` | NOT FOUND in core/ | Dead concept |
| `grade` | NOT FOUND in core/ | Dead concept |
| `pattern_gate` | NOT FOUND in core/ | Dead concept |
| `PERMITTED_HORIZONS` | config.py line 308 — used by `core/horizon/execution_authority.py` | **Legacy only** (not in V10 path) |
| `MIN_SCORE_TO_TRADE` | config.py — used by legacy `run_new_engine` | **Legacy only** |
| `candidate_score` | NOT FOUND | Dead concept |
| `opportunity scoring` | `core/opportunity/factory.py` — shadow layer only | **Observational** (cannot affect V10) |

**No legacy concept can influence V10 decisions.**

---

## 4. Horizon Authority — PASS

| Check | Status |
|---|---|
| V10 HorizonEngine produces SCALP/INTRADAY/EXTENDED freely | ✓ |
| `PERMITTED_HORIZONS` NOT read by V10 pipeline | ✓ |
| No execution layer limits horizon | ✓ |
| No risk model forces horizon | ✓ (risk uses horizon for SIZE adjustment, not restriction) |
| Modifiers can upgrade/downgrade based on HTF/volatility/space | ✓ |
| Nothing downstream overrides horizon after `assess_horizon()` | ✓ |

**HorizonEngine is sole horizon authority.** TREND_CONTINUATION + strong H4 + large space = EXTENDED works without restriction.

---

## 5. ID Lineage — PASS

```
observation_id (SHA-256 of symbol + timestamp, 16 chars)
        │
        ├── OpportunityAssessment.observation_id
        ├── StrategyDecision.opportunity_id
        ├── HorizonDecision.opportunity_id
        ├── EntryDecision.opportunity_id
        ├── RiskDecision.opportunity_id
        ├── ExecutionDecision.opportunity_id
        └── Decision Record.decision_id
```

**Single root identity (`observation_id`) flows through all layers.** Other IDs exist in the legacy path (`correlation_id`, `entity_id`, `decision_id` from uuid) but these are generated in `prepare_execution` which is the legacy execution path — not the V10 decision path.

---

## 6. Persistence Completeness — PASS (with gap)

### Currently persisted:

| Layer | Persisted? | Location |
|---|---|---|
| Market state (H4/H1/M15/M5) | ✓ | v10_decisions/*.jsonl |
| Opportunity assessment | ✓ | v10_decisions/*.jsonl |
| Strategy family | ✓ | v10_decisions/*.jsonl |
| Horizon decision | ✓ | v10_decisions/*.jsonl |
| Entry decision | ✓ | v10_decisions/*.jsonl |
| Risk decision | ✓ | v10_decisions/*.jsonl |
| Execution decision | ✓ | v10_decisions/*.jsonl |
| Account snapshot | ✓ | v10_decisions/*.jsonl |
| Broker snapshot | ✓ | v10_decisions/*.jsonl |
| **Trade outcome** | **✗** | Not yet linked |
| **Exit reason** | **✗** | Not yet linked |
| **P&L** | **✗** | Not yet linked |

**Gap:** V10 decision records do not yet capture post-execution outcomes (win/loss/P&L/exit reason). This requires the trade outcome linker to connect execution fills back to decision_id.

---

## 7. Hidden Blockers — PASS (safety gates correctly positioned)

### Places where EXECUTE → NO_TRADE:

| Location | Gate | Classification |
|---|---|---|
| V10 RiskEngine | Balance=0 | ✓ Safety (data unavailable) |
| V10 RiskEngine | R:R below minimum | ✓ Safety (geometry) |
| V10 RiskEngine | Daily loss exceeded | ✓ Safety (capital protection) |
| V10 RiskEngine | Max positions reached | ✓ Safety (exposure) |
| V10 ExecutionEngine | Broker disconnected | ✓ Safety (infrastructure) |
| V10 ExecutionEngine | Market closed | ✓ Safety (session) |
| V10 ExecutionEngine | Spread too high | ✓ Safety (cost) |
| V10 ExecutionEngine | Margin insufficient | ✓ Safety (capital) |
| V10 ExecutionEngine | Volume below min | ✓ Safety (broker rules) |
| V10 ExecutionEngine | Stops level violated | ✓ Safety (broker rules) |
| live_scanner guard chain (line 1028) | Spread/cooldown/correlation | ✓ Safety (redundant with V10's own checks) |

**No blocker examines strategy quality, confidence, or legacy scores.** All are infrastructure/capital safety gates.

---

## 8. Configuration Audit

### Hardcoded policy values in V10 engines:

| Value | Location | Should be config? |
|---|---|---|
| `DEFAULT_RISK_PCT = 0.0025` | risk_engine.py | Yes |
| `MIN_RR = 1.5` | risk_engine.py | Yes |
| `MAX_DAILY_LOSS_PCT = 0.04` | risk_engine.py | Yes (exists: `config.DAILY_LOSS_LIMIT_PERCENT`) |
| `MAX_OPEN_POSITIONS = 3` | risk_engine.py | Yes (exists: `config.MAX_TOTAL_OPEN_POSITIONS`) |
| `MAX_TOTAL_RISK_PCT = 0.03` | risk_engine.py | Yes |
| `MAX_SYMBOL_EXPOSURE = 2` | risk_engine.py | Yes |
| `MAX_SPREAD_ATR_RATIO = 0.30` | execution_engine.py | Yes (exists: `config.MAX_SPREAD_ATR_RATIO`) |
| `DEFAULT_SLIPPAGE_PIPS = 2.0` | execution_engine.py | Yes |
| `DEFAULT_TIMEOUT = 5.0` | execution_engine.py | Yes |
| Horizon pip ranges (5-20, 20-50, 50-150) | horizon_engine.py | Instrument policy — acceptable in engine |
| Quality weights (0.35, 0.30, 0.15, 0.20) | opportunity_engine.py | Business rule — acceptable in engine |

---

## Summary Table

| Area | Status | Issue |
|---|---|---|
| Decision path | ✓ PASS | V10 is sole decision maker |
| Legacy override | ✓ PASS | No legacy concept in V10 active path |
| Horizon control | ✓ PASS | HorizonEngine unrestricted |
| Risk authority | ✓ PASS | Safety only, never modifies trade |
| Execution authority | ✓ PASS | Blocks only, never modifies |
| Persistence | ⚠️ PARTIAL | Decisions persisted; outcomes NOT YET linked |
| ID lineage | ✓ PASS | Single root `observation_id` |
| Hidden blockers | ✓ PASS | All are safety gates |
| **Execution bridge** | **⚠️ BROKEN** | `prepare_execution` expects `OrderIntent` — V10 provides flat dict |
| Configuration | ⚠️ MINOR | 9 values should move to config.py |

---

## Critical Finding: Execution Bridge Gap

**V10 cannot currently send orders to MT5.**

The path is:
```
V10 result: {"action": "EXECUTE", "side": "BUY", "entry_price": ...}
        ↓
prepare_execution(new_result=...)
        ↓
_intent = new_result["intent"]  ← KeyError! V10 has no "intent" key
        ↓
CRASH
```

**This means V10 is the sole decision authority but execution is not yet wired.** The fix is to either:
1. Have scanner_adapter build an `OrderIntent` from V10's ExecutionDecision
2. Create a V10-native execution path that bypasses `prepare_execution`

Until this is fixed, V10 decisions will always crash on EXECUTE and be caught by the exception handler (which logs "engine_exception" and continues).

---

## Final Verdict

| Question | Answer |
|---|---|
| Is V10 the sole decision authority? | **YES** |
| Can anything override V10's decisions? | **NO** |
| Can V10 actually execute trades? | **NO** (execution bridge broken) |
| Is persistence complete? | Decisions: YES. Outcomes: NO |
| Are legacy concepts removed? | **YES** (from V10 active path) |
| Is the system production-ready? | **NO** (execution bridge must be fixed first) |
