# DECISION OBJECT OWNERSHIP AUDIT

**Generated:** 2026-07-16  
**Scope:** Decision-making objects from candle to order intent  
**Question:** Can every trading decision be reconstructed from objects created before persistence?

---

## 1. DECISION OBJECT LIFECYCLE MAP

```
Candle (OHLCV + time)
  │
  ▼
Signal (pattern, side, bar_index, bar_time, confidence)
  │
  ▼
ActivationResult (regime, eligible/gated/selected strategies, weights, pressure)
  │
  ▼
MarketStateResult (state, confidence, delta_stability, flip_rate, reasoning)
  │
  ▼
OpportunityAssessment ← FROZEN analytical snapshot (26+ fields)
  │
  ├─── ExecutionPolicy (trade_allowed, block_reason, required_rr, sizing)
  │       │
  │       ▼ [if blocked → NO_TRADE return]
  │
  ├─── RiskDecision = RiskAccepted(OrderIntent) | RiskRejected(reason)
  │       │
  │       ▼ [if rejected → NO_TRADE return]
  │
  ├─── ExpectedValueResult (ev, p_success, rr_effective, reasoning)
  │       │
  │       ▼ ExecutionPolicy(final) with EV
  │           │
  │           ▼ [if blocked → NO_TRADE return]
  │
  ▼
engine_result dict (carries ALL above as fields)
  │
  ├─── DecisionTrace (diagnostic snapshot of the journey)
  ├─── Decision Audit (full record of decision + context)
  ├─── Decision Ledger (every-cycle structured log)
  └─── OrderIntent → MT5Execution → ExecutionResult
```

---

## 2. OBJECT OWNERSHIP TABLE

| Object | Created Where | Owner | Inputs | Outputs (consumed by) | Persisted Fields | Lost Fields |
|--------|---------------|-------|--------|----------------------|-----------------|-------------|
| **Signal** | `strategy/signal_orchestrator.py` | signal_orchestrator | Candles, closed_i | new_engine pattern gate | pattern, side, bar_index, bar_time, confidence | — |
| **ActivationResult** | `strategy/selection_activation.py` | run_strategy_activation() | Candles, closed_i, Signal, swing context | new_engine scoring/strategy | regime, regime_confidence, eligible_strategies, selected_strategy, selected_weight, rejected_strategies, context_state, raw_pressure, final_pressure | context_state (not serialized) |
| **MarketStateResult** | `core/pipeline/market_state_engine.py` | MarketStateEngine.evaluate() | score_neutral, score_strategy, strategy_type | ExecutionPolicy, Assessment | state, confidence, delta_stability, reasoning | flip_rate, score_consistency (not propagated to assessment) |
| **OpportunityAssessment** | `core/pipeline/new_engine.py` ~line 208 | new_engine | Signal + ActivationResult + components + MarketStateResult | Policy, Risk, Trace, Audit, Persistence | ALL 26+ fields via to_dict() | flip_rate, score_consistency, raw_pressure, context_state |
| **ExecutionPolicy** | `core/pipeline/execution_policy.py` | compute_execution_policy() | MarketStateResult, Assessment, EV (optional) | engine_result dict | trade_allowed, block_reason, required_rr, max_position_fraction, policy_reasoning | ev details (only present on second call) |
| **RiskDecision** | `risk/manager.py` | RiskManager.evaluate() | Assessment, candles, bid, ask | engine (accept → intent / reject → reason) | On accept: all OrderIntent fields. On reject: reason + metadata | Full geometry reasoning (only reason string persisted) |
| **ExpectedValueResult** | `core/pipeline/expected_value.py` | compute_expected_value() | Assessment, market_state_result, entry/SL/TP, confirmation_score | engine_result → final policy | ev, p_success, p_failure, reward, risk, rr_effective, uncertainty_dampening, reasoning | — (all persisted via engine_result) |
| **OrderIntent** | `risk/manager.py` → `_execute_risk()` | RiskManager | SL/TP geometry, volume calc | MT5Execution.execute() | symbol, side, volume, entry_reference, sl, tp, pattern | sizing_mode reasoning, risk_pct input |
| **DecisionTrace** | `core/decision_trace.py` | build_decision_trace() | engine_result dict | DecisionFunnel, S3 | 40 fields (all scored/diagnostic) | correlation_id, decision_id |
| **DecisionAudit** | `core/decision_audit.py` | persist_new_engine_decision_audit() | engine_result, engine_state, candles | S3 | ~45 fields (most complete record) | component-level drag analysis |

---

## 3. FIELD CREATION LOCATIONS

### Strategy Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `selected_strategy` | run_strategy_activation() | `strategy/selection_activation.py` | `"REVERSAL"` / `"CONTINUATION"` / `"FALSE_BREAK"` / `None` | Assessment, Trace, Audit, Ledger |
| `strategy_confidence` | Same (as `selected_weight`) | Same | float 0.0–1.0 | Assessment, Trace, Audit, Ledger |
| `eligible_strategies` | Same | Same | tuple of strings | Assessment (as list), engine_result |
| `rejected_strategies` | Same | Same | tuple[RejectedStrategy] | engine_result (serialized to list of dicts) |
| `weights_used` | new_engine.py ~line 148 | `core/pipeline/new_engine.py` | `"strategy_specific"` / `"global_fallback"` | Assessment, Trace |

### Scoring Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `components` | `_compute_all_scores()` | `core/pipeline/new_engine.py` ~line 170 | dict[str, float] (10 keys, each 0.0–1.0) | Assessment, Trace, engine_result |
| `score_neutral` | Dot product: GLOBAL_WEIGHTS × components | `new_engine.py` ~line 178 | float 0.0–1.0 (rounded 4dp) | Assessment, Trace, Audit, Ledger |
| `score_strategy` | Dot product: active_weights × components | `new_engine.py` ~line 182 | float 0.0–1.0 (rounded 4dp) | Assessment, Trace, Audit, Ledger |
| `score_delta` | `score_strategy - score_neutral` | `new_engine.py` ~line 184 | float (rounded 4dp) | Assessment, Trace |

### Market Context Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `regime` | `classify_regime()` → ActivationResult | `strategy/regime_activation.py` | `"TRENDING"` / `"RANGE"` / `"TRANSITIONAL"` | Assessment, Trace, Audit, Ledger |
| `regime_confidence` | Same | Same | float 0.0–1.0 | Assessment, Trace, Audit |
| `market_state` | MarketStateEngine.evaluate() | `core/pipeline/market_state_engine.py` | `"STRUCTURED"` / `"TRANSITIONAL"` / `"CHOP"` | Assessment, Trace, Audit |
| `market_state_confidence` | Same | Same | float 0.0–1.0 | Assessment, Trace, Audit |
| `delta_stability` | Same | Same | float 0.0–1.0 | Assessment |
| `volatility_quality` | `_score_volatility_quality()` | `new_engine.py` | float 0.0–1.0 (component) | Assessment (in components dict) |
| `bias_alignment` | `_score_bias_alignment()` | `new_engine.py` | float 0.0–1.0 (component) | Assessment |

### Decision Explanation Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `action` | engine return value | `new_engine.py` (8 return paths) | `"EXECUTE"` / `"NO_TRADE"` | Trace, Audit |
| `terminal_stage` | `_classify_terminal_stage()` | `core/decision_trace.py` | one of 9 stage names | Trace |
| `terminal_reason` | engine `"reason"` field | `new_engine.py` return dicts | free-form string | Trace |
| `policy_reasoning` | ExecutionPolicy.policy_reasoning | `execution_policy.py` | descriptive string | Trace, Audit, engine_result |

### Diagnostic Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `threshold_gap` | `_compute_component_diagnostics()` | `core/decision_trace.py` | float (score - 0.35) | Trace ONLY |
| `closest_flip_component` | Same | Same | component name or None | Trace ONLY |
| `closest_flip_delta` | Same | Same | float or None | Trace ONLY |
| `flip_feasible` | Same | Same | bool | Trace ONLY |
| `weakest_component` | Same | Same | component name | Trace ONLY |
| `largest_drag_component` | Same | Same | component name | Trace ONLY |
| `largest_drag_value` | Same | Same | float (weight × gap) | Trace ONLY |

### Learning Fields

| Field | Created | File:Line | Format | Persisted In |
|-------|---------|-----------|--------|--------------|
| `uncertainty` | `compute_uncertainty()` | `core/uncertainty/` | UncertaintyAssessment object | engine_result, Ledger (as dict) |
| `attribution` | `compute_attribution()` | `core/attribution/` | ScoreAttribution object | engine_result, Ledger (as dict) |
| `evidence_contributions` | Same → dataclasses.replace() | `new_engine.py` ~line 285 | tuple of dicts | Assessment (⚠️ always [] in persisted copy) |

---

## 4. FIELD LOSS LOCATIONS

| Field | Present In | Lost At | Cause | Recoverable? |
|-------|-----------|---------|-------|--------------|
| `flip_rate` | MarketStateResult | OpportunityAssessment | Not copied to Assessment schema | Yes (from MSE window in memory, not persisted) |
| `score_consistency` | MarketStateResult | OpportunityAssessment | Not copied to Assessment schema | Same as above |
| `raw_pressure` | ActivationResult | Assessment | Not a schema field | Only in strategy_trace.jsonl (local) |
| `final_pressure` | ActivationResult | Assessment | Not a schema field | Same |
| `context_state` | ActivationResult | Assessment | Not a schema field (internal) | Lost (in-memory only) |
| `rejected_strategies` details | ActivationResult | Assessment | Not a schema field; only in engine_result dict | Via decision_audit (serialized) |
| `risk_pct` used | RiskManager scope | OrderIntent | Not an OrderIntent field | Lost — no record of which % was used |
| `sizing_mode` | RiskManager scope | OrderIntent | Not an OrderIntent field | Lost at intent level; present in event_stream RISK_CHECK event |
| `evidence_contributions` | Assessment (in-memory, enriched) | Persisted Assessment | Persisted BEFORE enrichment (timing bug) | Lost in persisted copy; only lives in engine_result dict |
| `uncertainty_score` | Assessment (in-memory, enriched) | Persisted Assessment | Same timing bug | Same |
| `confidence_modifier` | Assessment (in-memory, enriched) | Persisted Assessment | Same timing bug | Same |
| `threshold_gap` | DecisionTrace | Decision Audit | Audit doesn't extract diagnostics | Present in Trace S3 only |
| `closest_flip_*` | DecisionTrace | Decision Audit | Audit doesn't extract diagnostics | Present in Trace S3 only |
| `drag analysis` | DecisionTrace | Decision Audit | Same | Same |
| `correlation_id` | live_scanner (EXECUTE) | DecisionTrace | Trace built before EXECUTE decision | Join via entity_id → audit |
| `swing_context` details | SwingContext object | engine_result (partial) | Only direction/phase/strength copied | Full swing data lost |

---

## 5. NULL ANALYSIS

### `components = {}`

| Cause | When | Expected? | Owner |
|-------|------|-----------|-------|
| A: Engine exited at `no_viable_pattern` (line 95) | No pattern detected | ✅ Yes | new_engine.py |
| B: Assessment construction failed (try/except) | Exception in OpportunityAssessment() | ✅ Yes (fallback) | new_engine.py |
| C: Serializer removed data | Never — `to_dict()` always includes components | — | — |
| D: Intentional empty value | Only on "no_viable_pattern" exit | ✅ Yes | — |

**Real owner:** new_engine.py — `components` is only empty when the engine exits before scoring. This is correct.

### `selected_strategy = None`

| Cause | When | Expected? | Owner |
|-------|------|-----------|-------|
| A: No strategy passed eligibility | Regime = TRANSITIONAL (all strategies fail eligibility) | ✅ Yes | selection_activation.py |
| B: All strategies gated | Gate conditions not met (e.g., no liquidity sweep for REVERSAL) | ✅ Yes | gating_activation.py |
| C: Scoring produced weight < threshold | Activation weight below 0.5 → global fallback | ✅ Yes (strategy=None, weights_used="global_fallback") | new_engine.py |

**Real owner:** selection_activation.py decides. new_engine.py accepts None as valid (uses global weights).

### `ev = None`

| Cause | When | Expected? | Owner |
|-------|------|-----------|-------|
| A: Pipeline exited before risk evaluation | Policy blocked, score below threshold, swing blocked | ✅ Yes | new_engine.py |
| B: Risk rejected (no SL/TP → no entry/TP prices for EV) | SLTP_CALCULATION_FAILED | ✅ Yes | new_engine.py |

**Real owner:** new_engine.py — EV requires OrderIntent prices. If risk rejects, EV is never computed. This is correct.

### `reasoning = None` (on Decision Ledger)

| Cause | When | Expected? | Owner |
|-------|------|-----------|-------|
| A: Pre-engine exit (kill switch, session, daily loss) | Engine never called | ✅ Yes | live_scanner.py |
| B: Engine called but reasoning subsystem failed | `generate_reasoning()` threw | ✅ Yes (try/except: pass) | new_engine.py |
| C: Assessment was None (no_viable_pattern) | Assessment required for reasoning | ✅ Yes | new_engine.py |

**Real owner:** Reasoning subsystem. NULL is acceptable on all early-exit paths.

### `confirmation_score = None`

| Cause | When | Expected? | Owner |
|-------|------|-----------|-------|
| A: Pipeline exited before confirmation stage | Score below threshold, policy blocked, swing blocked | ✅ Yes | new_engine.py |
| B: Assessment was None | Rare (assessment construction failed) | ✅ Yes | new_engine.py |

**Real owner:** new_engine.py — confirmation is late-stage (after swing filter). NULL before that stage is correct.

---

## 6. FIELDS REQUIRED FOR PROFITABILITY OPTIMISATION

These fields are needed to answer: "Which conditions produce profitable trades?"

| Field | Available in S3? | Source | Join Required? |
|-------|-----------------|--------|----------------|
| `score_neutral` | ✅ | decision_audit, decision_trace | — |
| `score_strategy` | ✅ | decision_audit, decision_trace | — |
| `components` (all 10) | ✅ | decision_trace (each rounded 4dp) | — |
| `selected_strategy` | ✅ | decision_audit, decision_trace | — |
| `strategy_confidence` | ✅ | decision_audit, decision_trace | — |
| `regime` | ✅ | decision_audit, decision_trace | — |
| `market_state` | ✅ | decision_audit, decision_trace | — |
| `ev` | ✅ | decision_audit, decision_trace | — |
| `p_success` | ✅ | decision_audit, decision_trace | — |
| `rr_effective` | ✅ | decision_audit, decision_trace | — |
| `confirmation_score` | ✅ | decision_audit, decision_trace | — |
| `pnl_realised` | ✅ | trade_truth | JOIN via correlation_id |
| `r_multiple_realised` | ✅ | trade_truth | JOIN via correlation_id |
| `entry_fill_price` | ✅ | trade_truth | JOIN via correlation_id |
| `exit_fill_price` | ✅ | trade_truth | JOIN via correlation_id |
| `slippage_entry` | ✅ | trade_truth | JOIN via correlation_id |
| `exit_reason` | ✅ | trade_truth | JOIN via correlation_id |
| `uncertainty_score` | ⚠️ | decision_ledger (if uncertainty populated) | Via engine_result dict; NOT in assessment S3 |
| `evidence_contributions` | ❌ | Only in engine_result (in-memory) | NOT persisted anywhere (timing bug) |
| `sizing_mode` | ⚠️ | event_stream RISK_CHECK event | Indirect |
| `risk_pct` | ❌ | In-memory only (RiskManager scope) | NOT persisted |

**Verdict:** 15/17 critical fields available via S3. Two gaps:
1. `evidence_contributions` — lost due to assessment persistence timing
2. `risk_pct` — never persisted anywhere

---

## 7. FIELDS REQUIRED ONLY FOR DEBUGGING

| Field | Source | Purpose |
|-------|--------|---------|
| `threshold_gap` | DecisionTrace | "How far from trading?" |
| `closest_flip_component` | DecisionTrace | "Which single factor would flip the decision?" |
| `closest_flip_delta` | DecisionTrace | "By how much?" |
| `flip_feasible` | DecisionTrace | "Is a single-factor flip even possible?" |
| `weakest_component` | DecisionTrace | "What's dragging score down most?" |
| `largest_drag_component` | DecisionTrace | "Weighted drag leader?" |
| `largest_drag_value` | DecisionTrace | "How much score is lost?" |
| `stages_reached` | DecisionTrace | "How deep did the pipeline go?" |
| `stages_passed` | DecisionTrace | "Which gates passed?" |
| `terminal_stage` | DecisionTrace | "Which gate killed it?" |
| `swing_direction` | engine_result | "Macro context at decision time" |
| `swing_phase` | engine_result | "Structural phase" |
| `policy_reasoning` | ExecutionPolicy | "Human-readable policy explanation" |
| `ev_reasoning` | ExpectedValueResult | "Full EV computation breakdown" |
| `causal_signature` | Decision Ledger | "Compressed evaluation path" |
| `runtime_session_id` | DecisionTrace, Audit | "Which process produced this?" |

All debugging fields ARE persisted via DecisionTrace (S3). No gaps.

---

## 8. RECONSTRUCTION TEST

**Question: Can a NO_TRADE decision be fully reconstructed?**

| What | Source | Available? |
|------|--------|-----------|
| Which bar triggered it? | assessment.bar_time OR decision_audit.trigger_candle | ✅ |
| Which pattern was detected? | assessment.pattern OR decision_audit.pattern | ✅ |
| Which strategy was selected? | assessment.selected_strategy OR audit.strategy | ✅ |
| What were all 10 scores? | assessment.components OR trace.components | ✅ |
| What was the neutral/strategy score? | audit.score_neutral, audit.score_strategy | ✅ |
| Why was it blocked? | trace.terminal_reason OR audit.reason | ✅ |
| Which stage terminated? | trace.terminal_stage | ✅ |
| What single change would flip it? | trace.closest_flip_component + delta | ✅ |
| What was the market state? | trace.market_state, trace.regime | ✅ |
| Was EV computed? | trace.ev (None if not reached) | ✅ |

**Answer: YES — every NO_TRADE decision is fully reconstructable from S3.**

---

**Question: Can an EXECUTE decision be fully reconstructed?**

| What | Source | Available? |
|------|--------|-----------|
| Everything above (assessment through EV) | ✅ | ✅ |
| What SL/TP was calculated? | audit.intent.sl, audit.intent.tp | ✅ |
| What volume was sized? | audit.intent.volume | ✅ |
| What sizing mode was used? | event_stream RISK_CHECK event | ⚠️ indirect |
| What broker fill occurred? | execution_result.fill_price | ✅ |
| What was the final PnL? | trade_truth.pnl_realised | ✅ (at close) |
| Which process executed it? | audit.runtime_session_id | ✅ |
| What was the correlation chain? | audit.correlation_id → all layers | ✅ |

**Answer: YES — every EXECUTE decision is fully reconstructable. Minor gap: sizing reasoning not explicit.**

---

## 9. RECOMMENDED FIXES (priority order)

| # | Fix | Impact | Owner |
|---|-----|--------|-------|
| 1 | Pass `cycle_id=cycle_id` to `run_new_engine()` | Assessment S3 records get correct cycle_id (currently always 0) | live_scanner.py |
| 2 | Move `persist_opportunity_assessment()` AFTER uncertainty/attribution enrichment | Assessment S3 records include evidence_contributions, uncertainty_score | new_engine.py |
| 3 | Add `flip_rate` and `score_consistency` from MarketStateResult to Assessment schema | Full market stability data preserved with assessment | opportunity_assessment.py |
| 4 | Add `risk_pct` to OrderIntent or ExecutionResult | Sizing reasoning becomes queryable | risk/models.py or execution_result_writer.py |
| 5 | Include `threshold_gap` and `closest_flip_*` in Decision Audit | Debugging fields available without joining to trace | decision_audit.py |

---

## 10. ANSWER TO CORE QUESTION

> "Can every trading decision be reconstructed from the objects created before persistence?"

**YES — with two caveats:**

1. **Assessment.cycle_id = 0** (bug): The persisted assessment always has cycle_id=0 because live_scanner doesn't pass cycle_id to the engine. Reconstruction requires joining on entity_id instead.

2. **Evidence contributions lost at persistence**: The enriched assessment (with uncertainty + attribution) is only available in the engine_result dict (in-memory). The persisted assessment was written before enrichment. The data exists in the decision_ledger (as embedded uncertainty/attribution dicts) but not on the assessment record itself.

Everything else is fully available in S3 across the decision_audit + decision_trace + assessment_log + execution_result + trade_truth layers.

---

*End of audit. No code was modified.*
