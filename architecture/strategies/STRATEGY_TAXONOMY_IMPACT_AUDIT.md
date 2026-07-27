# Strategy Taxonomy Completion — Structural Impact Audit

## Executive Summary

The Strategy Taxonomy Library is a **contained intelligence-layer expansion**. It can remain entirely additive and observation-only. No existing interfaces require modification. No execution behaviour changes. The taxonomy sits behind existing authorities and communicates through the ObserverRegistry pattern already established.

---

## 1. Current Strategy Ownership

| Location | Current Responsibility | Should Taxonomy Modify This? |
|----------|----------------------|------------------------------|
| `core/pipeline/strategy_activation.py` | Classifies detected pattern into CONTINUATION/REVERSAL/FALSE_BREAK based on M5 regime and swing context. Outputs `StrategyActivationResult`. | **NO.** This is the live strategy selection logic. Taxonomy is a parallel observation layer, not a replacement. |
| `core/pipeline/new_engine.py` | Uses `activation.selected_strategy` to choose weight profiles for 10-factor scoring. Strategy is ADVISORY — if none selected, falls back to global weights. | **NO.** The engine's strategy concept (weight profile selection) is independent of the taxonomy's strategy concept (research hypothesis). |
| `core/pipeline/strategy_weights.py` | Defines per-strategy weight profiles for CONTINUATION/REVERSAL/FALSE_BREAK scoring. | **NO.** These are the production scoring weights. Taxonomy never touches scoring. |
| `core/decision_trace.py` | Records `selected_strategy`, `strategy_confidence`, `regime` passively after the decision. | **NO.** DecisionTrace is observation-only already. Taxonomy adds its own observations separately. |
| `core/shadow_trades.py` | Stores `strategy`, `pattern`, `regime`, `market_phase` on ShadowTrade at creation time. | **NO.** ShadowTrade receives these from the engine result. Taxonomy observes independently. |
| `core/pipeline/observers.py` | ObserverRegistry dispatches 6 observers after engine evaluation. | **Could ADD observer #7** (StrategyObserver), but existing observers are unchanged. |
| `strategy/signals.py` | Pattern detection Signal objects (pattern name, side, bar_index). | **NO.** Taxonomy reads pattern names but never modifies signal objects. |
| `risk/manager.py` | SL/TP geometry and position sizing from assessment. | **NO.** Risk has zero strategy awareness. Only reads pattern/side/bar_time. |
| `execution/execution_orchestrator.py` | Broker order submission. | **NO.** Execution has zero strategy awareness. Only receives OrderIntent. |
| `core/market_context/` | MarketContext production (regime, phase, direction, timeframe summaries). | **NO.** Taxonomy consumes MarketContext as read-only input. |

---

## 2. API Boundary Audit

### MarketContext
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. Taxonomy reads MarketContext.regime/phase/direction/etc. through `snapshot_from_market_context()`. No fields need to be added to MarketContext.
- **Compatibility risk:** None.

### DecisionEngine (run_new_engine)
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. The engine returns a dict. Taxonomy observes this dict through the ObserverRegistry — it never inserts itself into the engine's decision flow.
- **Compatibility risk:** None. Engine result dict is extensible (new keys are ignored by existing consumers).

### PatternDetection (Signal objects)
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. Taxonomy reads `pattern.pattern` (string name) for classification. It never modifies Signal objects or the detection pipeline.
- **Compatibility risk:** None.

### RiskManager
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. RiskManager reads assessment.pattern/side/symbol/bar_time only. It has zero awareness of strategy families, conditions, or eligibility. The two systems have no interface.
- **Compatibility risk:** None.

### ExecutionOrchestrator
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. Execution receives OrderIntent (numeric: side, volume, SL, TP, entry_reference). It never reads strategy/family/condition data.
- **Compatibility risk:** None.

### ShadowTrade
- **Contract change required?** No (for observation-only mode).
- **Can taxonomy remain additive?** Yes. The taxonomy's StrategyObserver creates its own StrategyObservation records — it does not need to write into ShadowTrade fields.
- **Future note:** If outcome linkage is implemented, a join by entity_id could connect observations to shadow trade outcomes without modifying the ShadowTrade model.
- **Compatibility risk:** None currently. Future outcome linking would be a read-only join, not a schema change.

### DecisionTrace
- **Contract change required?** No.
- **Can taxonomy remain additive?** Yes. DecisionTrace already records `selected_strategy` passively. Taxonomy observations are separate records (StrategyObservation). The two can be joined by entity_id for research without modifying DecisionTrace.
- **Compatibility risk:** None.

### ObserverRegistry
- **Contract change required?** No structural change. One new observer could be added.
- **Can taxonomy remain additive?** Yes. Adding observer #7 follows the exact same pattern as observers 1-6: try/except isolated, failure never blocks, no return value consumed.
- **Compatibility risk:** Minimal. ObserverRegistry is designed for extension.

---

## 3. Leakage Risk Assessment

### Can taxonomy remain OBSERVATION ONLY?

**YES.** Here is why:

The live decision pipeline flows:
```
Pattern Detection → Strategy Activation → Scoring → Policy → Risk → Execute
```

The taxonomy flows:
```
MarketContext → StrategyConditionEvaluator → StrategyObserver → Observation Records
```

These are **parallel paths with no intersection**. The taxonomy:
- Reads MarketContext (produced upstream, read-only)
- Reads detected pattern name (string, read-only)
- Writes StrategyObservation records (its own model, not consumed by decision pipeline)

### Paths where strategy information currently flows into decisions:

| Path | What Flows | Taxonomy Impact |
|------|-----------|-----------------|
| `strategy_activation.py` → `new_engine.py` | `selected_strategy` → weight profile selection | **Zero.** Taxonomy does not feed into this path. |
| `new_engine.py` → `engine_result["strategy"]` | Strategy name propagates to trace/shadow | **Zero.** Taxonomy observes this output, never produces it. |
| `engine_result` → `RiskManager.evaluate()` | Only pattern/side/symbol/bar_time pass to risk | **Zero.** Strategy name doesn't reach risk evaluation. |
| `engine_result["intent"]` → `ExecutionOrchestrator` | Only OrderIntent (numeric params) reach broker | **Zero.** Strategy concept is absent at execution. |

### Conclusion: There is NO path through which taxonomy data can influence execution.

The taxonomy's output (`StrategyObservation`) is consumed only by:
1. In-memory storage (research access)
2. Future: JSONL persistence (research queries)
3. Future: Research engine experiments (M9/M10/M11)

None of these feed back into the decision pipeline.

---

## 4. Required Data Model Changes

### Existing models that CAN support the taxonomy (no changes needed):

| Model | Already Supports |
|-------|-----------------|
| `StrategyFamily` (enum) | REVERSAL, MOMENTUM, CONTINUATION, BREAKOUT, MEAN_REVERSION |
| `StrategyDefinition` (dataclass) | Full strategy definition with conditions, risk/exit models, evidence |
| `StrategyConditionSet` | Structured conditions per strategy |
| `ConditionEvaluationResult` | Per-strategy evaluation outcome |
| `StrategyObservation` | Observation record with outcome linkage fields |

### New schemas NOT required:

The full taxonomy already exists:
- `core/strategy_family/` — Family classification layer
- `core/strategies/` — Strategy definitions, conditions, evaluator, observer

No new schemas are needed. The existing implementation is complete for observation-only mode.

### Future schema additions (NOT required now):

| If/When | Schema Needed |
|---------|--------------|
| Outcome linkage active | Join table or JSONL correlating observation_id → trade result |
| Persistence to S3 | JSONL writer for StrategyObservation records |
| Research experiment integration | Query functions over observation corpus |

---

## 5. Minimum Safe Implementation

### The system can already answer:

"The bot can describe which strategies match the current market."

This capability **already exists** in the current codebase:

```python
from core.strategies.condition_evaluator import StrategyConditionEvaluator, build_market_snapshot
evaluator = StrategyConditionEvaluator()
snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL", ...)
results = evaluator.evaluate_all(snapshot)
# → [ConditionEvaluationResult per strategy with FULLY_MET/NOT_MET/etc.]
```

### What it cannot do:

"Change what trades happen."

This is **impossible** because:
- No production file imports from `core/strategies/`
- No decision pipeline function calls any taxonomy component
- The taxonomy has no write access to any production data structure
- All taxonomy models are in separate modules with no reverse dependencies

### Files that should NOT change (execution boundary):

| File | Reason |
|------|--------|
| `core/pipeline/new_engine.py` | THE decision authority |
| `core/pipeline/strategy_activation.py` | Live strategy selection |
| `core/pipeline/strategy_weights.py` | Scoring weights |
| `core/pipeline/execution_policy.py` | Policy gates |
| `risk/manager.py` | Risk evaluation |
| `risk/levels.py` | SL/TP geometry |
| `execution/execution_orchestrator.py` | Broker interaction |
| `core/runtime/live_scanner.py` | Main loop (until observer integration approved) |
| `core/shadow_trades.py` | Shadow trade model |
| `core/decision_trace.py` | Trace model |

### Modules that exist and are complete:

| Module | Status |
|--------|--------|
| `core/strategy_family/` | Complete (5 families, 14 patterns, authority, diagnostics) |
| `core/strategies/models.py` | Complete (StrategyDefinition, Status, Evidence, Risk/Exit models) |
| `core/strategies/registry.py` | Complete (5 hypothesis strategies) |
| `core/strategies/authority.py` | Complete (OBSERVATION mode, promotion gate) |
| `core/strategies/conditions.py` | Complete (structured conditions for all 5 strategies) |
| `core/strategies/condition_evaluator.py` | Complete (evaluates conditions against MarketContext) |
| `core/strategies/strategy_observer.py` | Complete (creates observation records each cycle) |
| `core/strategies/evaluation_diagnostics.py` | Complete (formatted reporting) |
| `core/strategies/diagnostics.py` | Complete (framework-level reporting) |

---

## 6. Stalemate Test

### After Strategy Taxonomy Completion is implemented — what capabilities exist?

| Capability | Status | Notes |
|-----------|--------|-------|
| Identify market environment? | **YES** | MarketContext provides regime, phase, direction, timeframe summaries every cycle |
| Identify suitable strategy family? | **YES** | StrategyFamilyAuthority classifies patterns into families; StrategyConditionEvaluator checks phase eligibility |
| Identify candidate strategy? | **YES** | StrategyAuthority.evaluate_context() returns eligible strategies for current phase |
| Evaluate conditions? | **YES** | StrategyConditionEvaluator.evaluate() checks all structured conditions against live data |
| Prove profitability? | **NO** | Requires: (1) observation pipeline connected to live cycle, (2) outcome linker connecting observations to trade results, (3) sufficient sample accumulation (n≥100 per strategy×phase), (4) statistical analysis |

### What still cannot exist until evidence is collected?

1. **Strategy activation** — Cannot promote any strategy to ACTIVE without n≥100, p<0.05, walk-forward validation
2. **Phase-based filtering** — Cannot restrict patterns by phase without validated evidence
3. **Strategy-informed scoring** — Cannot adjust confidence based on strategy match without proof it improves outcomes
4. **Execution influence** — Cannot allow taxonomy to affect trade selection without a complete evidence chain

### The honest answer:

The architecture is complete. The taxonomy can classify, evaluate, and observe. But it cannot PROVE anything until it starts collecting evidence by running alongside live trading. The bottleneck is now **data accumulation**, not **architecture**.

---

## 7. Architecture Impact Summary

| Dimension | Impact | Risk Level |
|-----------|--------|-----------|
| Code architecture | Additive only — new modules, no modifications | LOW |
| API contracts | No contract changes to any existing interface | NONE |
| Execution behaviour | Zero impact — parallel observation path | NONE |
| Data models | All exist, no schema changes required | NONE |
| Test surface | +196 tests added, 0 existing tests affected | NONE |
| Runtime performance | One additional observer dispatch per cycle (if integrated) | NEGLIGIBLE |

---

## 8. Migration Plan

### Phase A: Current (COMPLETE)
- Strategy Family Layer ✓
- Strategy Framework ✓
- Condition Evaluator ✓
- Strategy Observer ✓
- All observation-only, all tested

### Phase B: Pipeline Integration (NEXT — requires approval)
- Add StrategyObserver as observer #7 in ObserverRegistry
- Single file change: `core/pipeline/observers.py` (add try/except block)
- Passes MarketContext + detected pattern to observer
- Observer creates StrategyObservation records

### Phase C: Persistence
- Add JSONL writer for StrategyObservation records
- Mirror to S3 (follows existing pattern from decision_trace.py)
- Enables offline research queries

### Phase D: Outcome Linkage
- When shadow trades close, match observations by entity_id/timestamp
- Update StrategyObservation.outcome_status and outcome_r_multiple
- Creates evidence pairs: "conditions X → outcome Y"

### Phase E: Research Validation
- Query accumulated observations with linked outcomes
- Run statistical tests per strategy × phase × condition set
- If n≥100, p<0.05, walk-forward passes → strategy can be promoted

---

## 9. Recommended Next Step

**Implement Phase B: Add StrategyObserver to ObserverRegistry.**

This is a single, isolated change to `core/pipeline/observers.py` — adding observer #7 following the exact same try/except pattern as observers 1-6. It connects the taxonomy to live data flow, starting evidence accumulation while maintaining zero execution impact.

The change is:
- One file modified (`core/pipeline/observers.py`)
- ~15 lines added (try/except isolated observer dispatch)
- Failure in the observer never affects trading (same guarantee as all other observers)
- Reverts cleanly by removing the block

This unblocks data collection, which is the only remaining bottleneck between "architecture complete" and "evidence available for validation."
