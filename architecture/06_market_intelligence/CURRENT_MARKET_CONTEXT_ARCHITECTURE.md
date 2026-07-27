# Current Market Context Architecture — Ground Floor Audit

**Generated:** 2026-07-20
**Status:** Discovery only — no code modified
**Method:** Static analysis of all architecture docs, contracts, pipeline modules, and persistence layers

---

## 1. Existing Design Rules

### 1.1 Governance Contracts (architecture/contracts/)

| Contract | Rule | Source |
|----------|------|--------|
| **Runtime Isolation** | Live runtime must NEVER import offline modules. One-way flow: LIVE → Persistence → OFFLINE | `runtime_isolation.py` |
| **Structure Authority** | Structure influences decisions via ONE point only (SWM in ConfluenceEngine). All other usage is observational | `structure_authority_contract.py` |
| **Core Governance** | Only EA-1→EA-4 modules may decide execution. Voters emit scores only. No diagnostic may influence decisions | `core_governance_principles.py` |
| **Influence Registry** | All data flow paths must be explicitly declared. Hidden paths are architecture violations | `influence_registry.py` |

### 1.2 Timeframe Rules (from module docstrings)

| Rule | Location | Status |
|------|----------|--------|
| HTF snapshot is OBSERVATIONAL ONLY — does NOT trigger/block trades | `core/timeframes/htf_snapshot.py` | Active |
| HTFContext is immutable — never modified after creation per cycle | `core/timeframes/types.py` | Active |
| TimeframeCache NEVER calls MT5 during `get_htf_context()` — only reads cached state | `core/timeframes/cache.py` | Active |
| Closed-bar only enforcement — forming bar always excluded from analysis | `core/timeframes/cache.py` | Active |
| Analyzers must NOT import from cache, integration, or engine | `h4_regime.py`, `h1_bias.py`, `m15_structure.py` | Active |
| `integration.py` is Phase 1 (no-op) — returns zero influence | `core/timeframes/integration.py` | Dead code |

### 1.3 Bias FSM Rules (from bias_fsm.py docstring)

| Rule | Detail |
|------|--------|
| Single authority for writing bias state | Only `bias_fsm.py` writes EngineState bias fields |
| Phase 3.6 — runs AFTER scoring | Does NOT influence current cycle scoring/EV/ranking/execution |
| Regime classification is READ-ONLY metadata | `regime_label` field never influences scoring |
| Uses ONLY raw price + FSM state | No circular feedback from scoring back to bias |
| Deterministic, no learning, no adaptation | Pure state machine |

### 1.4 Market State Engine Rules

| Rule | Detail |
|------|--------|
| Independent of strategy, scoring, execution | Only evaluates system stability |
| Only answers "Is this a tradeable environment?" | Does not evaluate direction/quality |
| Rolling window (20 observations) | No persistent state between sessions |
| Deterministic | Same inputs → same output |

### 1.5 Execution Policy Rules

| Rule | Detail |
|------|--------|
| EV is PRIMARY discriminator | Must be positive to trade |
| Market State CHOP is NOT a hard block | Reduces probability via uncertainty dampening |
| RR is SECONDARY validation only | Structural feasibility check |
| Does NOT compute scores, classify strategies, or detect patterns | Only consumes upstream outputs |

---

## 2. Current Architecture Flow

### 2.1 Complete Pipeline Map

```
STAGE 1: MT5 Data Acquisition
    MT5DataFeed.last_tick() → (bid, ask, tick_time)
    MT5DataFeed.copy_rates_closed() → candles[]
    ├─ Receives: MT5 terminal connection
    └─ Produces: raw market data (immutable through pipeline)

STAGE 2: Tick Validation
    TickMonitor.evaluate() → TickMonitorResult
    ├─ Receives: tick_time
    └─ Produces: valid/stale classification

STAGE 3: Bar Provision
    BarProvider.fetch_bar() → BarResult {candles, closed_i, closed_time, feed_state}
    ├─ Receives: sym_state (feed, stale_monitor)
    └─ Produces: validated bar data + dedup + UTC conversion

STAGE 4: HTF Cache Update
    TimeframeCache.update_if_needed() → internal state refresh
    ├─ Receives: current_time_s, current_price
    ├─ Produces: updated H4/H1/M15 cached snapshots (internal)
    └─ Authority: TimeframeCache owns all HTF fetch scheduling

STAGE 5: Pre-Engine Gates
    evaluate_pre_engine_gates() → GateResult {allowed, raw_patterns}
    ├─ Receives: kill_active, daily_loss_blocked, candles, closed_i
    ├─ Produces: permission + detected patterns
    └─ Authority: blocks before engine evaluation (session, kill switch, pattern detection)

STAGE 6: Engine A (sole production authority)
    run_new_engine() → _new_result dict
    ├─ Receives: candles, closed_i, symbol, bid, ask, engine_state,
    │            config, detected_patterns, risk_manager, htf_context, cycle_id
    ├─ Contains (inline):
    │   ├─ Strategy Activation (regime detection + context evaluation)
    │   ├─ Swing Context computation
    │   ├─ 10-component scoring
    │   ├─ Market State Engine evaluation
    │   ├─ Opportunity Assessment construction
    │   ├─ Pre-risk Execution Policy check
    │   ├─ Risk evaluation (SL/TP/sizing)
    │   ├─ Expected Value computation
    │   └─ Final Execution Policy (EV-gate)
    └─ Produces: action (EXECUTE/NO_TRADE), score, intent, assessment, reason

STAGE 7: Post-Engine (Phase 3.6 — after scoring)
    update_bias_fsm() → EngineState mutation (metadata only)
    ├─ Receives: engine_state, candles, closed_i, pattern, time
    └─ Produces: bias phase transition, regime_label classification
    NOTE: Does NOT influence current cycle decision

STAGE 8: Observers (fire-and-forget)
    ObserverRegistry.notify_all() → void
    ├─ Receives: ObserverContext (engine_result + full context)
    └─ Produces: event_observer, forensic_logger, entity_tracker,
                 visibility_layer, shadow_rooms, decision_trace (all passive)

STAGE 9: Outcome Handling
    ├─ NO_TRADE: handle_no_trade_outcome() → decision audit + finalize
    └─ EXECUTE: prepare_execution() → ExecutionPrep
                    → evaluate_runtime_guards() → GuardChainResult
                    → ExecutionOrchestrator.execute_trade() → ExecutionOutcome
                    → post_execution_handler → state update + effects
```

### 2.2 Decision Authority Chain

```
Pattern Gate (pre_engine_gates) ─── hard block (no patterns → skip)
     │
Strategy Activation ─── advisory only (selects weights, no hard block)
     │
Score Threshold (0.35) ─── hard block (noise rejection)
     │
Pre-Risk Execution Policy ─── hard block (neutral score floor, confidence floor)
     │
Swing Context Permission ─── hard block (reversal without BOS)
     │
Risk Manager ─── hard block (no valid SL/TP geometry)
     │
Expected Value ─── PRIMARY GATE (EV must be positive)
     │
Final Execution Policy ─── hard block (negative EV, RR below threshold)
     │
Runtime Guard Chain ─── hard block (10 guards: drawdown, daily loss, exposure, etc.)
     │
Broker Execution ─── final (MT5 order placement)
```

---

## 3. Existing Timeframe Handling

### 3.1 Where Each Timeframe Enters the System

| Timeframe | Entry Point | Fetched By | Stored In |
|-----------|-------------|-----------|-----------|
| **H4** | `TimeframeCache._fetch_candles(_TF_H4, 100)` | `cache.py` on new bar detection | `_CacheEntry.snapshot` (RegimeSnapshot) |
| **H1** | `TimeframeCache._fetch_candles(_TF_H1, 200)` | `cache.py` on new bar detection | `_CacheEntry.snapshot` (BiasSnapshot) |
| **M15** | `TimeframeCache._fetch_candles(_TF_M15, 200)` | `cache.py` on new bar detection | `_CacheEntry.snapshot` (StructureSnapshot) |
| **M5** | `MT5DataFeed.copy_rates_closed(symbol, TF_M5, 300)` | `bar_provider.py` every cycle | `candles[]` passed through pipeline |

### 3.2 How Each Timeframe is Used

| Timeframe | Usage Type | Module | Component Weight | Influence |
|-----------|-----------|--------|-----------------|-----------|
| **H4** | SCORED (component) | `new_engine._score_h4()` | 0.10 (10%) | Regime alignment with trade direction |
| **H1** | SCORED (component) | `new_engine._score_htf()` | 0.14 (14%) | Bias alignment + M15 quality combined |
| **M15** | SCORED (via HTF) | `new_engine._score_htf()` | (included in 0.14) | Structure quality modifier ±0.1 |
| **M5** | MULTIPLE uses | Various | Multiple | See table below |

### 3.3 M5 Timeframe Uses (scattered across modules)

| Use | Module | Role |
|-----|--------|------|
| Pattern detection | `strategy/signal_orchestrator.py` | Gate: no patterns → no trade |
| Strategy Activation regime | `strategy_activation.py` `_detect_regime()` | Advisory: selects weight profile |
| Bias alignment scoring | `new_engine._score_bias_alignment()` | Score component (0.18 weight) |
| Trend alignment scoring | `new_engine._score_trend()` | Score component (0.10 weight) |
| Chop clarity scoring | `new_engine._score_chop()` | Score component (0.06 weight) |
| Volatility quality scoring | `new_engine._score_volatility()` | Score component (0.07 weight) |
| Bias stability scoring | `new_engine._score_bias_stability()` | Score component (0.07 weight) |
| Market filter (chop gate) | `strategy/market_filter.py` | Hard block in legacy pipeline |
| Swing Context | `swing_context.py` | Hard block for reversals without BOS |
| Market memory | `market_context.py` | Mutates EngineState.regime_state |
| Bias FSM | `bias_fsm.py` | Writes bias fields to EngineState (post-scoring) |
| Structure scoring | `structure_scoring.py` | Writes structure_score/regime to EngineState |

### 3.4 Current Behaviour Classification

| Timeframe | Independent? | Averaged? | Scored? | Used as Filter? | Observation Only? |
|-----------|-------------|-----------|---------|----------------|------------------|
| H4 | ✅ (own analyzer) | ❌ | ✅ (0.10 weight) | ❌ | Partially (trend_bias is shadow) |
| H1 | ✅ (own analyzer) | ❌ | ✅ (0.14 weight) | ❌ | ❌ |
| M15 | ✅ (own analyzer) | ❌ | ✅ (via HTF combined) | ❌ | ❌ |
| M5 | ✅ (primary TF) | ❌ | ✅ (multiple components) | ✅ (market_filter, swing) | ❌ |

**Timeframes are scored independently — they are NEVER averaged together or hierarchically resolved.**

The only cross-TF interaction is `_score_htf()` which combines H1 direction + M15 quality into a single 0.0–1.0 value. H4 is scored completely separately via `_score_h4()`.

---

## 4. Current Market Interpretation — Mapping to Proposed Concepts

### 4.1 Does the system have equivalents of H4:STRUCTURE / H1:PHASE / M15:SETUP / M5:TRIGGER?

| Proposed Concept | Equivalent Exists? | Current Name | Module | Behaviour |
|------------------|--------------------|--------------|--------|-----------|
| **H4: STRUCTURE** (macro regime) | ✅ YES | `RegimeClassification` | `h4_regime.py` | TRENDING_BULLISH / TRENDING_BEARISH / RANGING / VOLATILE / TRANSITIONAL |
| **H1: PHASE** (directional bias) | ✅ YES | `BiasDirection` + swing_structure | `h1_bias.py` | BULLISH / BEARISH / NEUTRAL + HH_HL / LH_LL / MIXED |
| **M15: SETUP** (structural quality) | ✅ YES | `StructureSnapshot.quality_score` | `m15_structure.py` | 0.0–1.0 quality + at_key_level + order_block_present |
| **M5: TRIGGER** (execution signal) | ✅ YES | `Signal` (pattern + confirmation) | `signal_orchestrator.py` | Pattern detection + confirmation grading |

### 4.2 Additional Classifications That Have No Proposed Equivalent

| Current System | Module | Classification |
|----------------|--------|----------------|
| Swing Direction | `swing_context.py` | BULLISH / BEARISH / NEUTRAL |
| Swing Phase | `swing_context.py` | EXPANSION / DISTRIBUTION / CORRECTION |
| Break of Structure (BOS) | `swing_context.py` | boolean (hard gate for reversals) |
| Market State | `market_state_engine.py` | STRUCTURED / TRANSITIONAL / CHOP |
| Bias FSM Phase | `bias_fsm.py` | EXPIRED / FORMING / CONFIRMING / CONFIRMED / WEAKENING |
| Bias FSM Regime Label | `bias_fsm.py` | TRENDING_STABLE / TRENDING_WEAKENING / CHOPPING / TRANSITIONAL / POST_FLIP_RECOVERY |
| Structure Cohesion Regime | `structure_scoring.py` | WEAK / BUILDING / CONFIRMED / INVALID |
| Strategy Activation Regime | `strategy_activation.py` | TRENDING / RANGE / TRANSITIONAL |

### 4.3 Regime Classification Overlap (Key Finding)

**Three separate systems classify "regime" independently:**

1. **H4 Regime** (`h4_regime.py`): TRENDING_BULLISH / TRENDING_BEARISH / RANGING / VOLATILE / TRANSITIONAL
   - Based on: H4 EMA slope + ATR ratio + HH/HL structure
   - Used in: `_score_h4()` scoring component

2. **Strategy Activation Regime** (`strategy_activation.py`): TRENDING / RANGE / TRANSITIONAL
   - Based on: M5 20-bar displacement + HH/HL detection
   - Used in: strategy candidate evaluation (weight modulation)

3. **Bias FSM Regime Label** (`bias_fsm.py`): TRENDING_STABLE / CHOPPING / etc.
   - Based on: FSM phase + divergence + cooldown state
   - Used in: read-only metadata only (never influences scoring)

4. **Market State Engine** (`market_state_engine.py`): STRUCTURED / TRANSITIONAL / CHOP
   - Based on: rolling 20-cycle delta stability + flip rate + score consistency
   - Used in: ExecutionPolicy gate (sizing + RR requirements)

**These four systems classify market environment independently with no cross-system arbitration.**

---

## 5. Existing Persistence — Where Market Context Data Lives

### 5.1 Current Persistence Layers

| Layer | Location (Local) | Location (S3) | Market Context Fields |
|-------|-----------------|---------------|----------------------|
| **Event Stream** | `events/{date}.jsonl` | `s3://.../events/symbol={SYM}/date={DATE}/` | CANDLE, FEATURE_UPDATE only (strict allowlist — NO decisions/bias/scores) |
| **Decision Trace** | `logs/decision_trace/{SYM}/{DATE}.jsonl` | `s3://.../decision_trace/symbol={SYM}/date={DATE}/` | regime, regime_confidence, market_state, market_state_confidence, htf_alignment, h4_alignment, all 10 components, scores |
| **Decision Audit** | `logs/decision_audit/` | `s3://.../decision_audit/symbol={SYM}/date={DATE}/` | Full engine_state snapshot, all bias/structure fields, strategy, policy_reasoning |
| **Decision Ledger** | `logs/decision_ledger/{SYM}/{DATE}.jsonl` | `s3://.../decision_ledger/symbol={SYM}/date={DATE}/` | regime, session_state, signal_score, causal_signature (per-cycle summary) |
| **Opportunity Assessment** | Embedded in engine_result → decision_audit | Same as Decision Audit | All 26+ fields (full analytical snapshot at analysis-policy boundary) |
| **Shadow Trades** | `logs/shadow_trades/{SYM}/{DATE}.jsonl` | `s3://.../shadow_trades/` | Entry/SL/TP/pattern/score at trade open time |
| **Research Shadow Trades** | `logs/research_shadow_trades/{SYM}/{DATE}.jsonl` | `s3://.../research_shadow_trades/` | Full decision_snapshot including htf_snapshot |
| **Trade Truth** | `logs/trade_truth/` | `s3://.../trade_truth/` | Execution reality (outcome, not context) |
| **Engine State Checkpoint** | `logs/state/` | N/A (local only) | Full EngineState including bias/regime fields |

### 5.2 What Market Context Is Persisted Today

| Field | Persisted In | Format |
|-------|-------------|--------|
| H4 regime classification | Decision Trace (as `regime`) | String |
| H4 confidence | Decision Trace (as `regime_confidence`) | Float |
| H1 bias direction | Not directly — only as `htf_alignment` score | Float (0.0–1.0) |
| H1 confidence | Not directly — embedded in htf_alignment calculation | N/A |
| M15 structure quality | Not directly — only as part of `htf_alignment` | N/A |
| M5 regime_state | Decision Audit (via EngineState snapshot) | String |
| Market State | Decision Trace + Decision Audit | STRUCTURED/TRANSITIONAL/CHOP |
| Bias phase | Decision Audit (via EngineState snapshot) | String |
| Bias strength | Decision Audit (via EngineState snapshot) | Float |
| Swing direction | Decision Audit (assessment.components) | Implicit in scoring |
| Strategy regime | Decision Trace, Audit, Ledger (as `regime`) | TRENDING/RANGE/TRANSITIONAL |
| All 10 component scores | Decision Trace + OpportunityAssessment | dict[str, float] |

### 5.3 Where a Market Context Layer Would Naturally Attach

```
EXISTING PERSISTENCE:
                                    ┌─────────────────┐
    Decision Trace ─────────────────│ Per-entity       │
    Decision Audit ─────────────────│ (per decision)   │
    Decision Ledger ────────────────│                  │
    OpportunityAssessment ──────────│                  │
                                    └─────────────────┘
                                            │
                                            ▼ (references)
                                    ┌─────────────────┐
    Market Context (NEW) ───────────│ Per-change       │  ← ONLY on material change
                                    │ (not per cycle)  │
                                    └─────────────────┘
                                            │
                                            ▼ (underlying data)
                                    ┌─────────────────┐
    Event Stream ───────────────────│ Per-bar          │  (raw observations only)
                                    │ (continuous)     │
                                    └─────────────────┘
```

**Natural attachment point:** Between event stream (raw) and decision trace (per-decision).
Market Context represents "the interpreted state" — derived from raw data but not tied to a specific trade decision.

---

## 6. Architecture Gaps

### 6.1 What Already Exists

| Capability | Module | Status |
|-----------|--------|--------|
| H4 regime analysis | `core/timeframes/h4_regime.py` | ✅ Production |
| H1 bias analysis | `core/timeframes/h1_bias.py` | ✅ Production |
| M15 structure analysis | `core/timeframes/m15_structure.py` | ✅ Production |
| Per-symbol timeframe cache | `core/timeframes/cache.py` | ✅ Production |
| HTF immutable snapshot | `core/timeframes/htf_snapshot.py` | ✅ Production (observational) |
| Swing context computation | `core/pipeline/swing_context.py` | ✅ Production |
| Market stability classification | `core/pipeline/market_state_engine.py` | ✅ Production |
| Strategy-level regime detection | `core/pipeline/strategy_activation.py` | ✅ Production |
| M5 bias FSM with regime labels | `core/pipeline/bias_fsm.py` | ✅ Production (metadata) |
| M5 structure cohesion scoring | `core/pipeline/structure_scoring.py` | ✅ Production (parallel, non-authoritative) |
| Decision trace with context fields | `core/decision_trace.py` | ✅ Production |
| Cross-TF agreement scoring | `core/timeframes/htf_snapshot.py` `_compute_agreement()` | ✅ Production (observational) |
| S3 mirrored persistence patterns | Multiple (ledger, audit, shadow trades) | ✅ Production |

### 6.2 What Is Missing

| Missing Capability | Why It's Missing | Impact |
|--------------------|-----------------|--------|
| **Single unified market interpretation** | Each module interprets independently | No authoritative "what is the market doing?" answer |
| **Cross-TF conflict resolution** | H4 and H1 can contradict with no arbiter | Engine scores both independently — conflict reduces score but isn't explicitly handled |
| **Market state lifecycle (IMPULSE/PULLBACK/etc.)** | Swing Phase (EXPANSION/DISTRIBUTION/CORRECTION) exists but isn't propagated to scoring | No "phase of the move" in decision data |
| **Material change detection** | Context is rebuilt every cycle regardless of whether anything changed | No efficient delta tracking |
| **Dedicated market context persistence** | Context fields are scattered across decision_trace and decision_audit | Cannot query "what was the market doing at time T?" without parsing decision records |
| **Unified direction conclusion** | H4 trend_bias, H1 direction, M5 bias_fsm all produce direction independently | Three separate "direction" answers with no resolution |
| **Context-to-decision linkage** | Decision records carry context fields but no immutable context_id | Cannot compare two decisions' market contexts without field-by-field comparison |

### 6.3 What Would Need a New Component

| New Component | Reason |
|---------------|--------|
| `core/market_context/builder.py` | Combines existing analyzer outputs into one interpretation |
| `core/market_context/models.py` | Frozen MarketContext dataclass (unified output) |
| `core/market_context/conflict_resolver.py` | Explicit cross-TF disagreement handling |
| `core/market_context/change_detector.py` | Material change detection (direction/regime/state) |
| `core/market_context/persistence.py` | Dedicated JSONL + S3 writer for context records |
| `core/market_context/state_machine.py` | Market state transitions (IMPULSE→PULLBACK→etc.) |

### 6.4 What Should NOT Be Changed

| Component | Reason |
|-----------|--------|
| `core/timeframes/h4_regime.py` | Working analyzer — produces clean RegimeSnapshot |
| `core/timeframes/h1_bias.py` | Working analyzer — produces clean BiasSnapshot |
| `core/timeframes/m15_structure.py` | Working analyzer — produces clean StructureSnapshot |
| `core/timeframes/cache.py` | Working cache — manages fetch scheduling correctly |
| `core/timeframes/types.py` | Clean type definitions — HTFContext, RegimeSnapshot, BiasSnapshot, StructureSnapshot |
| `core/pipeline/new_engine.py` (Phase 0-1) | Sole execution authority — must not change behaviour during shadow phase |
| `core/pipeline/execution_policy.py` | EV-first gate logic is correct and isolated |
| `core/pipeline/expected_value.py` | EV computation is correct |
| `risk/` (entire package) | Risk management is independent of market context |
| `strategy/signal_orchestrator.py` | Pattern detection is independent of context layer |
| Event stream allowlist | Strict observation-only policy is correct |
| All persistence paths (ledger, audit, trace) | Working, S3-mirrored, Athena-queryable |
| Architecture contracts | Governance rules are sound — new layer must comply |

---

## 7. Current Timeframe Responsibilities

### 7.1 Responsibility Matrix

| Timeframe | Analyzer | Produces | Consumed By | Scoring Weight | Hard Gate? |
|-----------|----------|----------|-------------|---------------|------------|
| H4 | `h4_regime.py` → `RegimeSnapshot` | classification, confidence, atr_ratio, ema_slope, trend_bias, trend_strength | `_score_h4()` in new_engine | 0.10 (10%) | No |
| H1 | `h1_bias.py` → `BiasSnapshot` | direction, confidence, ema_position, swing_structure | `_score_htf()` in new_engine | 0.14 (14%) shared with M15 | No |
| M15 | `m15_structure.py` → `StructureSnapshot` | quality_score, nearest_support/resistance, at_key_level, order_block_present | `_score_htf()` in new_engine (modifier ±0.1) | (within 0.14) | No |
| M5 | Multiple sources | Patterns, bias, regime_state, structure, swing | 8 scoring components + swing gate + market filter | 0.76 (76%) combined | Yes (swing, market_filter) |

### 7.2 Timeframe Update Frequency

| Timeframe | Update Trigger | Typical Frequency |
|-----------|---------------|-------------------|
| H4 | New H4 bar closed (checked per M5 cycle) | Every 48 M5 bars (~4 hours) |
| H1 | New H1 bar closed (checked per M5 cycle) | Every 12 M5 bars (~1 hour) |
| M15 | New M15 bar closed (checked per M5 cycle) | Every 3 M5 bars (~15 minutes) |
| M5 | Every cycle | Every cycle (~5 minutes or faster in replay) |

### 7.3 Current Cross-Timeframe Interaction

The ONLY cross-TF interactions in the current system are:

1. **`_score_htf()` combines H1 + M15** into one 0.0–1.0 value
   - H1 direction alignment → primary contribution (0.5 base ± confidence)
   - M15 quality → modifier (±0.1)

2. **`htf_snapshot.py` computes alignment scores** (observational only)
   - `h4_h1_agreement` — whether H4 trend_bias matches H1 direction
   - `h1_m15_agreement` — whether H1 direction matches M15 bias
   - `overall_alignment_score` — weighted average (H4↔H1: 60%, H1↔M15: 40%)

3. **No cross-TF conflict resolution exists** — disagreements simply result in lower individual scores

---

## 8. Recommended Integration Point

### Where the Market Context Layer Fits

```
TimeframeCache.update_if_needed()     ← EXISTING (unchanged)
         │
         ▼
TimeframeCache.get_htf_context()      ← EXISTING (unchanged)
         │
         ▼
┌──────────────────────────────────────────────────┐
│  MarketContextBuilder.build()  ← NEW             │
│                                                  │
│  Inputs:                                         │
│    - HTFContext (from cache)                      │
│    - candles + closed_i (M5)                     │
│    - EngineState (bias/regime fields, read-only) │
│                                                  │
│  Outputs:                                        │
│    - MarketContext (frozen, immutable)            │
│                                                  │
│  Side effects:                                   │
│    - Persist on material change (JSONL + S3)     │
│    - Emit MARKET_CONTEXT_CHANGE event (optional) │
└──────────────────────────────────────────────────┘
         │
         ▼
run_new_engine(market_context=ctx)    ← MODIFIED (new param, future phase)
```

### Integration Rules

1. Builder MUST NOT call MT5 — only reads cached state
2. Builder MUST NOT import from `core/pipeline/` — avoids circular dependencies
3. Builder MUST NOT influence current behaviour in Phase 0 (shadow mode)
4. Builder MUST be wrapped in try/except — never crash production
5. Builder MUST comply with Runtime Isolation contract (no offline imports)
6. Persistence MUST follow existing patterns (JSONL + S3, gated by `EVENT_STREAM_S3_MIRROR`)
7. MarketContext MUST be frozen (immutable after construction)
8. Builder MUST be per-symbol (one per symbol state)
9. Builder SHOULD reuse existing analyzers (h4_regime, h1_bias, m15_structure) — not duplicate them

---

## 9. Summary

### Current State

The system has a **complete but fragmented** market interpretation capability:
- All timeframe analyzers exist and work correctly
- The cache layer manages fetch scheduling properly
- Scoring consumes HTF context via two weighted components (24% total)
- Four independent regime classifications coexist without arbitration
- Market context fields are persisted but scattered across decision records
- No dedicated market context persistence layer exists
- No cross-TF conflict resolution exists
- No material change detection exists

### Key Finding

The system already possesses all the raw interpretation capability needed.
What's missing is **unification** — a single component that reads all existing
analyzer outputs and produces one authoritative answer about market state.

The existing analyzers (h4_regime, h1_bias, m15_structure) + cache infrastructure
are sound and should NOT be replaced. The Market Context Layer sits ON TOP of them,
consuming their outputs and producing a unified interpretation.

---

*Document produced: 2026-07-20*
*Status: Architecture Discovery — Read-Only*
*Files modified: None*
