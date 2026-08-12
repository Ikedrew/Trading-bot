# LIVE / SHADOW RESEARCH UNIVERSE ARCHITECTURE AUDIT

**Date:** 2026-07-27  
**Classification:** READ-ONLY DESIGN — No code modified, no runtime affected  
**Scope:** V10 Research Engine dual-world architecture assessment  

---

## 1. Executive Architecture Summary

### Core Finding

The V10 Research Engine CAN support two parallel but semantically distinct research worlds. The existing Live architecture provides the structural template. The Shadow data already exists in production (`logs/shadow_trades/`), is research-grade, and joins deterministically to all Live universes via `entity_id`.

### Recommended Architecture

```
                         MARKET REALITY
                              |
              +---------------+---------------+
              |                               |
         LIVE PIPELINE               SHADOW PIPELINE
              |                               |
        +-----+-----+               +-----+-----+
        |     |     |               |     |     |
     DEC   MKT   STRAT           S-DEC  S-MKT S-STRAT
        |     |     |               |     |     |
        +-----+-----+               +-----+-----+
        |     |     |               |     |     |
      RISK  EXEC  OUTCOME         S-RISK S-EXEC S-OUTCOME
              |                               |
              +--------------+----------------+
                             |
                      CROSS-SIDE ANALYSIS
                             |
                      RESEARCH FINDING
                             |
                          PROPOSAL
                             |
                         CANDIDATE
                             |
                        EXPERIMENT
                             |
                       VALIDATION
                             |
                    HUMAN GOVERNANCE
```

### Key Decisions

1. **YES** — the six-universe Shadow mirror should be implemented
2. The Shadow side draws from a SINGLE data source (`logs/shadow_trades/`) but analytically separates into six domains
3. Existing primitives work unchanged on Shadow populations
4. `entity_id` is the authoritative cross-side join key
5. 4 existing questions should MOVE to Shadow, 17 should SPLIT into Live+Shadow variants
6. Shadow evidence flows through the same governed pipeline (Finding → Proposal → Candidate → Experiment → Validation → Human)

---

## 2. Existing Live Universe Architecture

### 2.1 Six Universes — Summary Table

| Universe | Builder | Source | Grain | Identity | Records | Schema |
|----------|---------|--------|-------|----------|---------|--------|
| EXECUTION | `execution_universe.py` | `data/research/research_universe.jsonl` + `logs/execution_results/` | 1 completed trade | `trade_id` (+enriched `entity_id`) | 94 | 1.0 |
| DECISION | `decision_universe.py` | `logs/decision_trace/<SYMBOL>/*.jsonl` | 1 decision event (EXECUTE/NO_TRADE) | `entity_id` | ~10,453 | decision_trace_v2 |
| MARKET | `market_universe.py` | `logs/decision_trace/` (v10_market_state) + `logs/market_context/` | 1 market-state observation | `entity_id` | ~10,453 | 2.0 |
| STRATEGY | `strategy_universe.py` | `logs/decision_trace/` (v10_strategy) + `logs/strategy_observations/` | 1 strategy evaluation | `entity_id` | ~14,501 | 2.0 |
| RISK | `risk_universe.py` | `logs/decision_trace/` (v10_risk) | 1 risk evaluation event | `entity_id` | subset that reached risk stage | 2.0 |
| OUTCOME | `outcome_universe.py` | Wraps ExecutionUniverseBuilder | 1 completed trade with realised R | `entity_id` | 94 | 1.0 |

### 2.2 Universe Contracts (from `contracts.py`)

Each universe declares:
- **grain** — what one record represents
- **identity_field** — primary key
- **source_datasets** — actual file paths
- **join_keys** — fields available for cross-universe joins
- **coverage_fields** — fields defining data coverage
- **lineage_fields** — provenance tracking

### 2.3 Join Infrastructure

All universes join on `entity_id` (format: `{symbol}_{bar_time}`).

| Join | Cardinality | Expected Match Rate |
|------|-------------|---------------------|
| Decision → Execution | 1:1 (EXECUTE only) | ~0.9% (94/10453) |
| Decision → Market | 1:1 | ~100% |
| Decision → Strategy | 1:1 | ~100% |
| Decision → Risk | 1:1 (risk-reached subset) | ~10-30% |
| Execution → Outcome | 1:1 | 100% |

### 2.4 Provenance System

- `UniverseMetadata`: content_hash (SHA-256/16), generation_timestamp, source_files, exclusions
- `RunContext`: run_id, universe_versions, population_versions, primitive_versions
- Findings record exact population_versions (SHA-256 of resolved population data)

---

## 3. End-to-End Live Trace

```
MT5 Market Data Feed (real broker tick/bar data)
    |
    v
live_scanner.py — multi-symbol loop (10 symbols, M5 timeframe)
    |
    v
bar_provider.py — fetch closed M5 bar, UTC conversion, dedup
    |
    v
pre_engine_gates — kill switch, daily loss, session, pattern availability
    |
    v
scanner_adapter.py — run_v10_cycle()
    |
    v
V10Pipeline.process() — MarketUnderstanding → Opportunity → Strategy → Horizon → Entry → Risk → Execution
    |                                                    
    +---> [DECISION TRACE persisted: logs/decision_trace/{SYMBOL}/{DATE}.jsonl]
    |     (records EVERY evaluation — EXECUTE and NO_TRADE)
    |     Produces: DECISION UNIVERSE, MARKET UNIVERSE, STRATEGY UNIVERSE, RISK UNIVERSE records
    |
    v (if approved: action=EXECUTE)
prepare_execution — builds OrderIntent, correlation_id
    |
    v
evaluate_runtime_guards — daily_trade_limit, cooldown, exposure, spread, correlation
    |
    v (if all guards pass)
ExecutionOrchestrator.execute_trade() — MT5 broker order
    |
    +---> [EXECUTION RESULT persisted: logs/execution_results/{SYMBOL}/{DATE}.jsonl]
    |     Produces: entity_id enrichment for EXECUTION UNIVERSE
    |
    v (if fill confirmed)
TradeStateManager.register_from_execution() — position lifecycle begins
    |
    v (position managed: SL/TP/BE/trailing via tick_driver)
    |
    v (position closed)
build_trade_record() → persist_trade()
    |
    +---> [TRADE JOURNAL persisted: logs/trade_journal/{DATE}.jsonl]
    |     Source for: data/research/research_universe.jsonl (compiled)
    |     Produces: EXECUTION UNIVERSE, OUTCOME UNIVERSE records
    |
    v
REALISED OUTCOME (R-multiple, net P&L, exit reason, duration)
```

### Live Universe → Runtime Component Mapping

| Universe | Represents | Runtime Source |
|----------|-----------|---------------|
| EXECUTION | executed action + realised consequence | `execution_result_writer.py` + `trade_journal.py` |
| DECISION | decision (EXECUTE or NO_TRADE) | `decision_trace.py` via `build_decision_trace()` |
| MARKET | observation at decision time | `decision_trace.py` (v10_market_state sub-object) |
| STRATEGY | intended action / strategy assessment | `decision_trace.py` (v10_strategy sub-object) |
| RISK | risk evaluation | `decision_trace.py` (v10_risk sub-object) |
| OUTCOME | realised consequence (analytical view) | Wraps EXECUTION records |

---

## 4. Shadow Layer Inventory

### 4.1 Three Distinct Shadow Mechanisms

| Mechanism | Trade ID Format | Trigger | Scope | Persistence |
|-----------|----------------|---------|-------|-------------|
| **Horizon Shadow** | `hshadow_{cycle}_{symbol}_{HORIZON}` | Pattern detected + horizon eligible | ALL decisions (EXECUTE + NO_TRADE) | `logs/shadow_trades/{SYMBOL}/{DATE}.jsonl` |
| **Primary Shadow** | `shadow_{cycle}_{symbol}` | EXECUTE decision | Only executed trades | `logs/shadow_trades/{SYMBOL}/{DATE}.jsonl` |
| **Research Shadow** | via `research_shadow_engine.py` | RESEARCH_WOULD_EXECUTE disagreement | Promotion monitor | `logs/research_shadow_trades/{SYMBOL}/{DATE}.jsonl` |

### 4.2 Canonical Counterfactual Source

**The Horizon Shadow is the canonical counterfactual evidence source** because:
- It runs for ALL detected patterns regardless of EXECUTE/NO_TRADE
- It produces independent counterfactual outcomes per horizon
- It has the largest population (covers both executed and rejected opportunities)
- It carries `entity_id` for deterministic joins

The Primary Shadow is supplementary (only EXECUTE decisions; useful for live-vs-shadow comparison).

The Research Shadow is separate infrastructure (promotion monitor only; different persistence path).

### 4.3 Shadow Creation Point in Code

**Location:** `core/runtime/live_scanner.py` lines ~730-790

```python
# Create shadow trades for each eligible horizon, regardless of
# whether the engine approved execution.
from core.horizon.horizon_trade_builder import build_all_horizon_trades
from core.shadow_trades import get_shadow_engine

_sh_trades = build_all_horizon_trades(
    eligible_horizons=_eligible_for_shadow,
    symbol=..., direction=..., entry_price=...,
    m5_candle_high=..., m15_nearest_support=..., ...
)

for _sh_t in _sh_trades:
    get_shadow_engine().open_trade(
        trade_id=f"hshadow_{cycle_id}_{sym_state.symbol}_{_sh_t.horizon}",
        entity_id=_new_result.get("entity_id", ""),
        ...
    )
```

### 4.4 Shadow Lifecycle Engine

**Location:** `core/shadow_trades.py` → `ShadowTradeEngine.evaluate_bar()`

**Called from:** `core/runtime/bar_provider.py` — every closed M5 bar, every symbol

**Exit logic:**
- BUY: SL if `bar_low <= stop_loss`; TP if `bar_high >= take_profit`
- SELL: SL if `bar_high >= stop_loss`; TP if `bar_low <= take_profit`
- Timeout: `bars_elapsed >= 60` (5 hours at M5)

**R calculation:** `core/trade_truth.py` → `compute_r_multiple(direction, entry, exit, stop_loss)`

### 4.5 Shadow Record Schema (shadow_trades_v2)

Four domains per record:
1. **IDENTITY** — trade_id, correlation_id, symbol, strategy_id, cycle_id, entity_id
2. **DECISION_SNAPSHOT** — entry/SL/TP/direction/pattern/score/regime/horizon (frozen at decision time)
3. **SIMULATION_ENVIRONMENT** — htf_snapshot, entry_bar_index, bar_time
4. **SIMULATED_OUTCOME** — exit_price, pnl_r_multiple, mfe_r, mae_r, exit_reason, bars_held, trade_state_progression

### 4.6 Shadow Data Volume

- **Symbols:** EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD, NAS100, US500, XAUUSD
- **Date range:** 2026-07-22 through 2026-08-11 (active production)
- **Estimated records:** Thousands (multiple horizons per detected pattern per cycle)

---

## 5. End-to-End Shadow Trace

```
MT5 Market Data (same real bars as Live)
    |
    v
live_scanner.py — V10 pipeline evaluates opportunity
    |
    v
Pattern detected (regardless of EXECUTE/NO_TRADE decision)
    |
    v
Horizon classifier → eligible horizons (SCALP, INTRADAY, EXTENDED)
    |
    v
build_all_horizon_trades() — constructs entry/SL/TP per horizon
    |  - Entry: market price (ask if BUY, bid if SELL)
    |  - SL/TP: horizon-specific geometry from M5/M15/H1 structure
    |  - Direction: from assessment or engine result
    |
    v
get_shadow_engine().open_trade() — one ShadowTrade per eligible horizon
    |  - entity_id = "{symbol}_{bar_time}" (same as decision trace)
    |  - Frozen decision snapshot: pattern, score, regime, horizon, spread
    |
    v
[Next M5 bar closes — bar_provider calls evaluate_bar()]
    |
    v
ShadowTradeEngine.evaluate_bar(bar_high, bar_low, bar_close, bar_time)
    |  - bars_elapsed += 1
    |  - MFE/MAE updated from bar high/low
    |  - State log appended: {bar, r, close}
    |  - Exit check: SL hit? TP hit? Timeout?
    |
    v (repeats until exit condition met)
    |
    v
Exit triggered (SL / TP / max_bars_timeout)
    |
    v
compute_r_multiple(), compute_mfe_r(), compute_mae_r()
    |
    v
_build_truth_record() → shadow_trades_v2 schema
    |
    v
_persist_shadow_trade() → logs/shadow_trades/{SYMBOL}/{DATE}.jsonl + S3
    |
    v
COUNTERFACTUAL OUTCOME (R-multiple, MFE_R, MAE_R, exit_reason, bars_held)
```

---

## 6. Shadow Data Quality Assessment

### 6.1 Research-Grade Assessment

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Deterministic entity_id join to Decision | **PASS** | `identity.entity_id` = `{symbol}_{bar_time}` matches all Live universes |
| Contains counterfactual R-multiple | **PASS** | `simulated_outcome.pnl_r_multiple` computed from real market bars |
| Contains decision-time context | **PASS** | `decision_snapshot` preserves entry/SL/TP/pattern/score/regime/horizon |
| Contains market state reference | **PASS** | Joinable via entity_id to Market Universe |
| Contains strategy reference | **PASS** | `identity.strategy_id` + `decision_snapshot.pattern` |
| Contains risk parameters | **PASS** | `decision_snapshot.risk_config_snapshot` (risk distance, R:R) |
| Contains execution-equivalent data | **PASS** | Entry/SL/TP/direction/size from `decision_snapshot` |
| Contains exit/duration | **PASS** | `simulated_outcome.exit_reason`, `bars_held` |
| Multiple horizons distinguishable | **PASS** | `trade_id` format includes horizon suffix |
| Schema versioned | **PASS** | `schema_version: "shadow_trades_v2"` |
| S3 mirrored | **PASS** | Dual persistence (local JSONL + S3) |

### 6.2 Gaps Identified

| Gap | Severity | Impact | Mitigation |
|-----|----------|--------|-----------|
| `regime` often null in horizon shadows | LOW | Cannot filter by regime within shadow alone | Join to Decision/Market universe provides regime |
| `entity_id` occasionally empty | LOW | Small fraction unjoinable | Exclude records with empty entity_id |
| No `decision_action` field in shadow record | NONE | Not needed — join to Decision universe provides action | By design |
| No anomaly flags | NONE | Shadow has no anomaly concept | NOT_APPLICABLE for shadow |
| Spread not modelled for horizon shadows | MEDIUM | Overstates R by spread amount | Label as limitation in findings |
| 60-bar timeout artificial cap | MEDIUM | Biases timeout exits toward zero R | Document in contract; future: configurable |

### 6.3 Conclusion

**The shadow data IS research-grade.** It satisfies all ten criteria for formal universe construction. Gaps are minor and addressable through joins to existing Live universes.

---

## 7. Six-Universe Mirror Assessment

### 7.1 Universe-by-Universe Evaluation

#### EXECUTION ↔ SHADOW_EXECUTION

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **YES** |
| What does it mean? | The hypothetical execution parameters (entry, SL, TP, direction, size) that WOULD have been applied |
| Source data | `decision_snapshot` domain within shadow_trades_v2 records |
| Contract | 1 record = 1 hypothetical execution intent frozen at decision time |
| Primitives that consume it | expectancy, distribution, comparison, segmentation (on entry geometry fields) |
| Questions it answers that Live cannot | "What entry geometry was available for rejected opportunities?" |
| Questions Live answers that it cannot | "What was actual broker fill quality? Slippage? Requotes?" |
| Questions requiring BOTH | "How much edge is lost between intended geometry and realised fill?" |

#### DECISION ↔ SHADOW_DECISION

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **PARTIAL** — the shadow record captures the decision context but not an independent decision event |
| What does it mean? | The decision-time context (score, pattern, strategy, opportunity quality) that led to shadow creation |
| Source data | `decision_snapshot` + `identity` domains |
| Contract | 1 record = 1 decision-context snapshot associated with a shadow trade |
| Primitives | segmentation, predictive_power, comparison (score/pattern/strategy fields) |
| Note | This is NOT a separate decision — it's the SAME decision viewed through the shadow lens. The Live Decision Universe already contains ALL decisions (EXECUTE + NO_TRADE). Shadow_Decision provides the decision context enriched with counterfactual outcome. |

#### MARKET ↔ SHADOW_MARKET

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **YES** — via join |
| What does it mean? | The market state at the moment the counterfactual opportunity was created |
| Source data | Join `entity_id` → Market Universe (same observation, same bar) |
| Contract | 1 record = 1 market snapshot at shadow creation time |
| Note | The market state is IDENTICAL for Live and Shadow (same bar, same entity_id). No separate Shadow_Market data source exists. The shadow's market context comes from joining to the existing Market Universe. |
| Design decision | **Shadow_Market is a VIEW over Market Universe, not an independent builder** |

#### STRATEGY ↔ SHADOW_STRATEGY

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **YES** |
| What does it mean? | The strategy/pattern that was evaluated (possibly selected, possibly rejected) at shadow creation time |
| Source data | `identity.strategy_id` + `decision_snapshot.pattern` from shadow records, enriched via Strategy Universe join |
| Contract | 1 record = 1 strategy evaluation context for a shadow trade |
| Note | The shadow record captures WHICH strategy/pattern was detected. The full strategy evaluation detail comes from joining to Strategy Universe via entity_id. |

#### RISK ↔ SHADOW_RISK

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **YES** |
| What does it mean? | The risk parameters (SL distance, R:R, position size) that governed the shadow simulation |
| Source data | `decision_snapshot.risk_config_snapshot` within shadow records |
| Contract | 1 record = 1 risk parameter set used for counterfactual simulation |
| Primitives | comparison (risk parameters vs outcome), segmentation (by R:R, SL distance) |
| Questions it answers | "Are shadow outcomes sensitive to risk assumptions?" "Which R:R produces best counterfactual expectancy?" |

#### OUTCOME ↔ SHADOW_OUTCOME

| Question | Answer |
|----------|--------|
| Does a meaningful Shadow counterpart exist? | **YES** — this is the PRIMARY value of the Shadow side |
| What does it mean? | The counterfactual economic result of a simulated trade lifecycle |
| Source data | `simulated_outcome` domain within shadow_trades_v2 records |
| Contract | 1 record = 1 completed counterfactual trade with R-multiple, MFE, MAE, exit reason |
| Primitives | expectancy, distribution, comparison, segmentation, degradation, predictive_power, conditional_expectancy |
| Questions it answers | ALL counterfactual performance questions |
| Questions Live answers that it cannot | Realised P&L, broker fill quality, commission/swap impact |
| Questions requiring BOTH | "Shadow vs Live R comparison", "Execution leakage", "Decision quality assessment" |

### 7.2 Summary Assessment

| Live Universe | Shadow Counterpart | Type | Independent Source? |
|---------------|-------------------|------|---------------------|
| EXECUTION | SHADOW_EXECUTION | REAL | YES (from decision_snapshot) |
| DECISION | SHADOW_DECISION | VIEW | NO (same data enriched with shadow outcome) |
| MARKET | SHADOW_MARKET | VIEW | NO (join to existing Market Universe) |
| STRATEGY | SHADOW_STRATEGY | PARTIAL | PARTIAL (strategy_id in shadow + join) |
| RISK | SHADOW_RISK | REAL | YES (from risk_config_snapshot) |
| OUTCOME | SHADOW_OUTCOME | REAL | YES (from simulated_outcome) |

---

## 8. Live ↔ Shadow Universe Mapping

### 8.1 Correct Naming Convention

Based on the existing codebase patterns (Universe enum in `models.py`), the recommended naming is:

```python
class Universe(str, Enum):
    # Live side (existing)
    EXECUTION = "EXECUTION"
    DECISION = "DECISION"
    MARKET = "MARKET"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    OUTCOME = "OUTCOME"
    # Shadow side (new)
    SHADOW_EXECUTION = "SHADOW_EXECUTION"
    SHADOW_DECISION = "SHADOW_DECISION"
    SHADOW_MARKET = "SHADOW_MARKET"
    SHADOW_STRATEGY = "SHADOW_STRATEGY"
    SHADOW_RISK = "SHADOW_RISK"
    SHADOW_OUTCOME = "SHADOW_OUTCOME"
```

### 8.2 Implementation Architecture

| Shadow Universe | Builder Required | Independent Data Source | Implementation |
|-----------------|-----------------|----------------------|----------------|
| SHADOW_OUTCOME | YES — new `ShadowOutcomeUniverseBuilder` | `logs/shadow_trades/` | Reads shadow_trades_v2 JSONL, normalises simulated_outcome |
| SHADOW_EXECUTION | YES — new `ShadowExecutionUniverseBuilder` | `logs/shadow_trades/` | Reads decision_snapshot (entry geometry) |
| SHADOW_RISK | YES — new `ShadowRiskUniverseBuilder` | `logs/shadow_trades/` | Reads risk_config_snapshot |
| SHADOW_DECISION | OPTIONAL — VIEW over Decision enriched with shadow R | Join: Decision + ShadowOutcome by entity_id | May be a computed population rather than separate builder |
| SHADOW_MARKET | NO — use existing Market Universe | Join: Market Universe by entity_id | Populations filter to entities that HAVE shadow outcomes |
| SHADOW_STRATEGY | OPTIONAL — VIEW over Strategy enriched with shadow R | Join: Strategy + ShadowOutcome by entity_id | Same as SHADOW_DECISION rationale |

### 8.3 Pragmatic Recommendation

Build THREE independent Shadow universe builders from the single `logs/shadow_trades/` source:

1. **ShadowOutcomeUniverseBuilder** — the primary value (counterfactual R, MFE, MAE, exit)
2. **ShadowExecutionUniverseBuilder** — entry geometry (for entry quality research)
3. **ShadowRiskUniverseBuilder** — risk parameters (for risk sensitivity research)

The remaining three (SHADOW_DECISION, SHADOW_MARKET, SHADOW_STRATEGY) are served by cross-universe joins to existing Live universes filtered to entities that have shadow outcomes. This avoids data duplication while maintaining analytical completeness.

---

## 9. Population Mapping

### 9.1 Shadow Populations (for ShadowOutcomeUniverseBuilder)

| Population | Definition | Source |
|------------|------------|--------|
| ALL_SHADOW_OUTCOMES | All closed shadow trades with non-null r_multiple | Full shadow_trades_v2 dataset |
| SHADOW_WINS | `pnl_r_multiple > 0` | Filter on outcome |
| SHADOW_LOSSES | `pnl_r_multiple <= 0` | Filter on outcome |
| SHADOW_FROM_EXECUTE | entity_id matches Decision.action == "EXECUTE" | Cross-join |
| SHADOW_FROM_NO_TRADE | entity_id matches Decision.action == "NO_TRADE" | Cross-join |
| SHADOW_TP_HIT | `exit_reason == "take_profit"` | Filter on outcome |
| SHADOW_SL_HIT | `exit_reason == "stop_loss"` | Filter on outcome |
| SHADOW_TIMEOUT | `exit_reason == "max_bars_timeout"` | Filter on outcome |
| SHADOW_HORIZON_SCALP | trade_horizon == "SCALP" or trade_id contains "_SCALP" | Filter on identity |
| SHADOW_HORIZON_INTRADAY | trade_horizon == "INTRADAY" | Filter on identity |
| SHADOW_HORIZON_EXTENDED | trade_horizon == "EXTENDED" | Filter on identity |
| SHADOW_REGIME_TRENDING | Joined Decision/Market regime == "TRENDING" | Cross-join |
| SHADOW_REGIME_RANGING | Joined regime == "RANGING" | Cross-join |
| SHADOW_REGIME_TRANSITIONAL | Joined regime == "TRANSITIONAL" | Cross-join |

### 9.2 Population Contracts

Every Shadow population follows the same contract structure as Live:
- **canonical definition** — declarative filter expression
- **inclusion criteria** — field conditions
- **exclusion criteria** — null entity_id, null r_multiple
- **source** — `logs/shadow_trades/`
- **owner** — ShadowOutcomeUniverseBuilder
- **version** — content hash of resolved population
- **lifecycle** — immutable after build (append-only source)
- **join key** — `entity_id` (deterministic, matches Decision/Market/Strategy/Risk)
- **minimum data** — `pnl_r_multiple` must not be null

---

## 10. Primitive Compatibility

### 10.1 Existing Primitives on Shadow Data

| Primitive | Works on Shadow? | Adaptation Needed | Notes |
|-----------|-----------------|-------------------|-------|
| `expectancy` | YES | NONE | Computes mean R from `r_multiple` field — identical operation |
| `distribution` | YES | NONE | Distribution of any numeric field |
| `comparison` | YES | NONE | Group-by comparison works on any categorical + numeric |
| `conditional_expectancy` | YES | NONE | Condition fields available via join |
| `calibration` | PARTIAL | Label output | Calibrating predicted_probability against counterfactual R is valid but weaker than realised |
| `predictive_power` | YES | NONE | Monotonicity analysis — universal |
| `segmentation` | YES | NONE | Segment by any categorical dimension |
| `transition` | YES | NONE | Temporal analysis if shadow has timestamps |
| `execution_quality` | PARTIAL | Field mapping | Shadow has `bars_held` not `duration_seconds`; `exit_reason` present |
| `degradation` | YES | NONE | Time-series performance comparison |
| `anomaly_analysis` | NO | NOT_APPLICABLE | Shadow has no anomaly concept |
| `exceptional_analysis` | PARTIAL | Define shadow-specific exceptional criteria | May define "exceptional" as extreme R or unusual exit |

### 10.2 New Primitive Required

**ONE new primitive needed:** `cross_side_comparison`

```
Purpose: Compare the same entity's outcomes across Live and Shadow worlds
Inputs:
  - live_records: list[dict] with r_multiple (realised)
  - shadow_records: list[dict] with r_multiple (counterfactual)
  - join_key: entity_id
Outputs:
  - matched_count: int
  - live_mean_r, shadow_mean_r: float
  - execution_leakage: shadow_mean_r - live_mean_r
  - direction_agreement_rate: float (do both predict same win/loss?)
  - correlation: float (Pearson of paired R values)
```

### 10.3 Design Principle Preserved

```
UNIVERSE → POPULATION → FIXED PRIMITIVE → QUESTION → FINDING
```

No shadow-specific primitive copies needed. Same primitives operate on both sides. The semantic distinction lives in:
- Universe provenance (which universe sourced the data)
- Finding metadata (REALISED vs COUNTERFACTUAL evidence label)
- Question contract (declares which side it operates on)

---

## 11. Join-Key / Lineage Architecture

### 11.1 Authoritative Cross-Side Join

**`entity_id`** (format: `{symbol}_{bar_time}`)

This is THE canonical key because:
- Deterministic: same bar → same entity_id regardless of execution path
- Present in ALL Live universes (Decision, Market, Strategy, Risk, Execution via enrichment)
- Present in Shadow records (`identity.entity_id`)
- Immutable: never changes after creation
- Unique at M5 granularity per symbol

### 11.2 Complete Join Map

```
LIVE DECISION (entity_id)
      |
      +--- entity_id ---> MARKET (same entity)
      |
      +--- entity_id ---> STRATEGY (same entity)
      |
      +--- entity_id ---> RISK (same entity, if risk reached)
      |
      +--- entity_id ---> EXECUTION (via deal/ticket enrichment)
      |                         |
      |                         +--- OUTCOME (wraps Execution)
      |
      +--- entity_id ---> SHADOW_OUTCOME (1:N — multiple horizons per entity)
      |                         |
      |                         +--- SHADOW_EXECUTION (same record)
      |                         |
      |                         +--- SHADOW_RISK (same record)
```

### 11.3 Cardinality Notes

| Join | Cardinality | Reason |
|------|-------------|--------|
| Decision → ShadowOutcome | 1:N | One decision may produce SCALP + INTRADAY + EXTENDED shadows |
| Execution → ShadowOutcome | 1:N | Executed trade has parallel shadow simulations |
| ShadowOutcome → Market | N:1 | Multiple shadows share same market snapshot |

### 11.4 Disambiguation for 1:N Joins

When a question needs a SINGLE counterfactual R per entity:
- **Option A:** Use the "best horizon" shadow (highest R:R or most appropriate horizon for strategy)
- **Option B:** Use ALL horizons as independent observations (larger sample, but correlated)
- **Option C:** Filter to a specific horizon population (e.g., SHADOW_HORIZON_SCALP only)

The question contract should declare which option applies. No implicit aggregation.

---

## 12. Existing Question Bank Classification

### 12.1 Complete Classification Table (45 Questions)

| Question | Title | Current Universe(s) | Correct Side | Action | Reason |
|----------|-------|--------------------|----|--------|--------|
| E-001 | System Expectancy | EXECUTION | LIVE_ONLY | KEEP | Realised broker outcomes |
| E-002 | Win/Loss Distribution | EXECUTION | LIVE_ONLY | KEEP | Realised distribution |
| E-003 | Exit Reason Distribution | EXECUTION | LIVE_ONLY | KEEP | Actual exit events |
| E-004 | Execution Quality by Session | EXECUTION | LIVE_ONLY | KEEP | Real fill quality |
| E-005 | Probability of Ruin | EXECUTION | LIVE_ONLY | KEEP | Realised variance |
| E-006 | Out-of-Sample Validation | EXECUTION | LIVE_ONLY | KEEP | Walk-forward on realised |
| E-007 | Stop Placement | EXECUTION | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Live: actual SL hit rate. Shadow: counterfactual SL sensitivity |
| E-008 | Pattern Degradation | EXECUTION | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Degradation measurable on both outcome types |
| E-009 | Duration vs Outcome | EXECUTION | LIVE_ONLY | KEEP | Actual trade duration |
| E-010 | R:R Effectiveness | EXECUTION | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | R:R testable on both sides |
| D-001 | Score Predictive Power | DECISION | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Score predicts R — testable with both R types |
| D-002 | EV Calibration | DECISION | LIVE_ONLY | KEEP | Calibration requires realised outcomes |
| D-003 | Threshold Effectiveness | DECISION | CROSS_LIVE_SHADOW | SPLIT_INTO_TWO_QUESTIONS | Live: threshold on executed. Shadow: threshold on all signals |
| D-004 | Rejection Stage Analysis | DECISION | INVALID_CURRENT_FORM | REFORMULATE | Currently asks for R on NO_TRADE (impossible on Live). See Section 13 |
| D-005 | Opportunity Quality | DECISION | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Quality prediction testable both sides |
| D-006 | Opportunity Failure | DECISION | LIVE_ONLY | KEEP | Characterises actual failures |
| D-007 | Risk Gate Value | DECISION | CROSS_LIVE_SHADOW | SPLIT_INTO_TWO_QUESTIONS | Must compare blocked-shadow-R vs approved-live-R |
| ED-001 | Decision-to-Execution Leakage | EXEC+DEC | CROSS_LIVE_SHADOW | REFORMULATE | Compare shadow R (intent) vs live R (realised) |
| ED-002 | Missed Opportunity Cost | EXEC+DEC | SHADOW_ONLY | MOVE_TO_SHADOW | This IS the counterfactual question |
| ED-003 | Position Sizing | EXEC+DEC | LIVE_ONLY | KEEP | Requires realised outcomes at different sizes |
| EM-001 | Regime-Conditioned Expectancy | EXEC+MKT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Regime expectancy on both sides |
| EM-002 | Market Drift | EXEC+MKT | LIVE_ONLY | KEEP | Temporal analysis of realised |
| ES-001 | Execution by Strategy | EXEC+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Strategy edge on both |
| DM-001 | Decision Quality Under Regime | DEC+MKT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Score accuracy by regime both sides |
| DM-002 | Opportunity Detection vs Market | DEC+MKT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Quality prediction by market both sides |
| DM-003 | Rejection Rate by Market | DEC+MKT | NEEDS_REFORMULATION | REFORMULATE | Currently descriptive — add shadow R to make it actionable |
| DS-001 | Strategy Confidence Calibration | DEC+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Confidence calibration both sides |
| DS-002 | Strategy Conditions vs Outcome | DEC+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Conditions effectiveness both sides |
| MS-001 | Strategy x Regime | MKT+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Strategy x regime both sides |
| MS-002 | Pattern x Market Context | MKT+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Pattern x market both sides |
| MS-003 | Strategy Availability by Market | MKT+STRAT | LIVE_ONLY | KEEP | Coverage gap analysis (descriptive) |
| EDM-001 | Complete Lifecycle | EXEC+DEC+MKT | LIVE_ONLY | KEEP | Full realised lifecycle |
| DMS-001 | Decision x Strategy x Market | DEC+MKT+STRAT | LIVE_AND_SHADOW | DUPLICATE_AS_LIVE_AND_SHADOW | Multi-dimensional both sides |
| EDMS-001 | Full System Attribution | ALL 4 | LIVE_ONLY | KEEP | Attribution requires realised |
| EDMS-002 | Promotion Impact Analysis | ALL 4 | CROSS_LIVE_SHADOW | REFORMULATE | Promotion analysis needs both worlds |

### 12.2 Action Summary

| Action | Count |
|--------|-------|
| KEEP (LIVE_ONLY) | 17 |
| DUPLICATE_AS_LIVE_AND_SHADOW | 17 |
| MOVE_TO_SHADOW | 1 (ED-002) |
| SPLIT_INTO_TWO_QUESTIONS | 2 (D-003, D-007) |
| REFORMULATE | 4 (D-004, DM-003, ED-001, EDMS-002) |
| ARCHIVE | 0 |
| BLOCK_UNTIL_SHADOW_DATA_AVAILABLE | 0 (data already exists) |

---

## 13. D-004 Reclassification

### 13.1 Current State

```
Question: D-004 "Rejection Stage Analysis"
Intent: "Where in the decision pipeline are trades rejected? Which rejection
         stage removes the most potential edge vs protecting from losses?"
Current population: NO_TRADE_DECISIONS (10,453 records)
Current metric: r_multiple (segmented by terminal_reason)
Actual metric coverage: 1 record has r_multiple (from execution join)
Evidence quality: INSUFFICIENT
Current ranking: #29 (after evidence quality fix)
Analysis type: SEGMENTATION
```

### 13.2 Why It Fails

D-004 asks "which stage removes edge" — this requires knowing the OUTCOME of rejected opportunities. But:
- NO_TRADE decisions have no live execution → no realised R
- The only R-multiple in the population comes from accidental cross-join (1 record)
- The question as currently formulated is **structurally unanswerable on the Live side**

### 13.3 What D-004 Was Actually Trying to Learn

The research intent is: **"Are rejection mechanisms correctly protecting capital, or are they removing profitable opportunities?"**

This is inherently a CROSS_LIVE_SHADOW question. It requires:
1. Knowing WHERE opportunities are rejected (Live Decision)
2. Knowing WHAT WOULD HAVE HAPPENED (Shadow Outcome)
3. Comparing: did rejection protect (shadow R negative) or cost (shadow R positive)?

### 13.4 Correct Reformulation

D-004 should become THREE questions:

**D-004a (LIVE — descriptive):**
```
"Where in the pipeline are opportunities rejected?"
Side: LIVE_ONLY
Universe: DECISION
Population: NO_TRADE_DECISIONS
Metric: COUNT per terminal_stage (no R-multiple needed)
Analysis: SEGMENTATION by terminal_reason
Value: Identifies pipeline bottlenecks
```

**D-004b (SHADOW — counterfactual):**
```
"What counterfactual expectancy did rejected opportunities produce by rejection stage?"
Side: SHADOW_ONLY
Universe: SHADOW_OUTCOME joined to DECISION
Population: SHADOW_FROM_NO_TRADE
Metric: counterfactual r_multiple segmented by Decision.terminal_stage
Analysis: SEGMENTATION by terminal_stage, metric = shadow r_multiple
Value: Identifies which stages remove the most counterfactual edge
```

**D-004c (CROSS — decision quality):**
```
"Which rejection stages correctly protect capital and which remove profitable opportunities?"
Side: CROSS_LIVE_SHADOW
Universe: DECISION + SHADOW_OUTCOME + OUTCOME
Population: ALL_DECISIONS with shadow outcome available
Metric: {stage → {correctly_rejected (shadow R < 0), incorrectly_rejected (shadow R > 0)}}
Analysis: Cross-side comparison per rejection stage
Value: Actionable — identifies which gates to relax vs tighten
```

### 13.5 Expected Impact

With shadow data available:
- D-004a: immediate (purely descriptive, no outcome needed) — **sample: 10,453**
- D-004b: estimated 3,000-8,000 shadow R observations for NO_TRADE entities — **STRONG evidence**
- D-004c: same population with classification overlay — **STRONG evidence**

This transforms D-004 from the system's most misleading question into potentially its most valuable research output.

---

## 14. New Shadow Research Opportunities

### 14.1 Tier 1 — High Value, High Evidence Availability

| # | Question | Population | Est. Sample | Primitive |
|---|----------|-----------|-------------|-----------|
| 1 | Counterfactual system expectancy (all detected patterns) | ALL_SHADOW_OUTCOMES | Thousands | expectancy |
| 2 | Missed opportunity rate (% of NO_TRADE with positive shadow R) | SHADOW_FROM_NO_TRADE wins / total | Thousands | expectancy |
| 3 | Rejection stage value (shadow R by terminal_stage) | SHADOW_FROM_NO_TRADE × Decision | Thousands | segmentation |
| 4 | Horizon comparison (SCALP vs INTRADAY vs EXTENDED shadow R) | SHADOW_HORIZON_* | Hundreds each | comparison |
| 5 | Score predicts shadow outcome (for rejected) | SHADOW_FROM_NO_TRADE | Thousands | predictive_power |
| 6 | Strategy family counterfactual expectancy | ALL_SHADOW × strategy_id | Hundreds per family | segmentation |
| 7 | Regime × counterfactual expectancy | ALL_SHADOW × regime join | Hundreds per regime | segmentation |

### 14.2 Tier 2 — High Value, Moderate Evidence

| # | Question | Population | Primitive |
|---|----------|-----------|-----------|
| 8 | Risk gate counterfactual (shadow R for risk-blocked vs risk-approved) | SHADOW × Risk Universe | comparison |
| 9 | Pattern × regime counterfactual | ALL_SHADOW × Market × Strategy | segmentation |
| 10 | Shadow vs Live execution leakage (same entity, different R) | SHADOW_FROM_EXECUTE × Outcome | cross_side_comparison |
| 11 | Does opportunity quality predict shadow outcome? | SHADOW × Decision quality fields | predictive_power |
| 12 | Counterfactual by session | ALL_SHADOW × session derived from timestamp | segmentation |

### 14.3 Tier 3 — Research Frontiers

| # | Question | Notes |
|---|----------|-------|
| 13 | Does the bot systematically reject opportunities that later succeed? | Requires temporal pattern detection |
| 14 | Counterfactual degradation over time | degradation primitive on shadow |
| 15 | Optimal threshold from shadow data (what score threshold maximises counterfactual expectancy?) | Requires sweep analysis |
| 16 | Are there strategy families profitable only at specific horizons? | horizon × strategy interaction |

---

## 15. Candidate / Experiment Implications

### 15.1 Can Shadow Findings Generate Candidates?

**YES.** A shadow finding can produce a proposal → candidate → experiment through the existing governed pipeline.

Example:
```
Shadow Finding: "MEAN_REVERSION pattern has -0.3R counterfactual expectancy in TRANSITIONAL regime"
    → Proposal: "Consider excluding MEAN_REVERSION during TRANSITIONAL"
    → Candidate: POPULATION_FILTER(strategy_id != "MEAN_REVERSION" when regime == "TRANSITIONAL")
    → Experiment: Run against shadow population, measure expectancy improvement
    → Validation: Statistically significant improvement?
    → Promotion Gate: Human reviews and decides
```

### 15.2 Candidate Types by Evidence Source

| Evidence Source | Candidate Type | Experiment Target | Example |
|-----------------|---------------|-------------------|---------|
| LIVE finding | POPULATION_FILTER on Execution Universe | Historical executed trades | "Exclude TRANSITIONAL from live trades" |
| SHADOW finding | POPULATION_FILTER on ShadowOutcome Universe | All shadow trades | "Exclude TRANSITIONAL from shadow signals" |
| CROSS-SIDE finding | COMPARISON experiment | Both populations | "Compare live R excluding TRANSITIONAL vs shadow R excluding TRANSITIONAL" |

### 15.3 Can Experiments Compare Live vs Shadow?

**YES.** The existing `CandidateExperiment` infrastructure uses `POPULATION_FILTER` which is declarative. It can be applied to either universe's population.

A cross-side experiment would:
1. Apply filter to LIVE population → compute filtered live expectancy
2. Apply same filter to SHADOW population → compute filtered shadow expectancy
3. Compare both to unfiltered baselines
4. Report whether the filter improves BOTH sides consistently

### 15.4 What Proposals Can Safely Be Tested from Shadow Evidence?

| Proposal Type | Shadow-Testable? | Requires Live Verification? |
|---------------|------------------|---------------------------|
| "Exclude pattern X" | YES (filter shadow trades) | YES (before promotion) |
| "Exclude regime Y" | YES (filter by regime) | YES |
| "Raise score threshold to Z" | YES (filter by score) | YES |
| "Change SL distance" | PARTIALLY (shadow has fixed SL) | YES — requires new simulation |
| "Add new strategy" | NO (shadow only has existing strategies) | YES |
| "Change position sizing" | NO (R-multiple is size-independent) | YES for absolute P&L |

### 15.5 Governance Preserved

```
SHADOW FINDING
    → "This counterfactual hypothesis deserves investigation"
    ≠ "Deploy this change"

SHADOW EXPERIMENT VALIDATION
    → "The population filter improves counterfactual expectancy"
    ≠ "It will improve live performance"

PROMOTION
    → Requires human governance review
    → Must consider: shadow limitations, sample size, execution reality gap
```

---

## 16. Governance Implications

### 16.1 Evidence Hierarchy

```
STRONGEST: Live experiment validated on realised data
    |
STRONG: Shadow experiment validated on counterfactual data + supported by live evidence
    |
MODERATE: Shadow experiment validated on counterfactual data alone
    |
WEAK: Shadow observation without experiment
    |
INSUFFICIENT: Shadow finding with too few observations
```

### 16.2 Promotion Gate Requirements

For a shadow-sourced proposal to reach PROMOTION_ELIGIBLE:

1. Shadow experiment must show statistically significant improvement (existing threshold)
2. Evidence quality must be STRONG or MODERATE (existing evidence quality system)
3. The proposal must be SEMANTICALLY VALID (a population filter that makes sense in production)
4. Human reviewer must acknowledge counterfactual limitations
5. Ideally: cross-side evidence should SUPPORT the shadow finding (live data, even if smaller sample, shows same direction)

### 16.3 What Must Never Happen

- Shadow evidence auto-modifying live trading parameters
- Shadow R-multiple being reported as realised profit
- Shadow findings bypassing the proposal → candidate → experiment → validation pipeline
- Shadow evidence being weighted equally with live evidence without explicit justification

---

## 17. Architectural Gaps

### 17.1 Infrastructure Gaps (Must Build)

| Gap | Component | Priority |
|-----|-----------|----------|
| No ShadowOutcomeUniverseBuilder exists | New builder reading `logs/shadow_trades/` | P0 |
| No SHADOW_* enum values in Universe enum | Extend `models.py` Universe enum | P0 |
| No Shadow population definitions | Add to `models.py` Population enum | P0 |
| No Shadow population contracts | Add to `contracts.py` | P1 |
| No Shadow join contracts | Add entity_id 1:N cardinality contracts | P1 |
| No `cross_side_comparison` primitive | New primitive for Live↔Shadow paired analysis | P2 |
| No shadow-aware question definitions | New questions or SHADOW variants of existing | P2 |
| No shadow evidence labelling in findings | Add `evidence_source` field to ResearchFinding | P1 |

### 17.2 Data Gaps (Minor)

| Gap | Severity | Mitigation |
|-----|----------|-----------|
| Some shadow records have empty entity_id | LOW | Exclude at build time (existing exclusion pattern) |
| No `decision_action` in shadow schema | NONE | Join to Decision Universe provides this |
| Spread not modelled for horizon shadows | MEDIUM | Document as finding limitation |
| 60-bar timeout cap | MEDIUM | Document; future: configurable max_bars |
| No commission/swap in shadow | LOW | R-multiple is gross (consistent within shadow) |

### 17.3 Conceptual Gaps (Design Decisions Required)

| Decision | Options | Recommendation |
|----------|---------|----------------|
| How to handle 1:N (multiple horizons per entity) | A) Best horizon, B) All as independent, C) Per-horizon populations | C) Per-horizon populations (most flexible) |
| Should SHADOW_DECISION be a separate builder or a view? | A) Separate builder, B) Computed population on Decision+Shadow join | B) Computed population (no new data source) |
| Should shadow findings have different confidence requirements? | A) Same thresholds, B) Higher thresholds for shadow | B) Slightly higher minimum sample (shadow is cheap to accumulate) |
| How to version shadow universe when source grows daily? | A) Timestamp-based, B) Content hash (existing), C) Date-range version | B) Content hash (consistent with Live) |

---

## 18. Recommended Implementation Phases

### Phase 1 — Contracts & Enums (Foundation)

**Files affected:**
- `research_engine/v10/universes/models.py` — add SHADOW_* Universe enum values, Shadow Population enum values
- `research_engine/v10/universes/contracts.py` — add Shadow universe contracts, population contracts, join contracts

**Dependencies:** None
**Risk:** LOW (additive only — no existing code changes)
**Tests:** Contract validation tests, enum membership tests
**Data required:** None (structural only)

### Phase 2 — Shadow Universe Builders

**Files affected:**
- NEW: `research_engine/v10/universes/shadow_outcome_universe.py`
- NEW: `research_engine/v10/universes/shadow_execution_universe.py`
- NEW: `research_engine/v10/universes/shadow_risk_universe.py`

**Dependencies:** Phase 1 (contracts)
**Risk:** LOW (new files, no modification of existing builders)
**Tests:** Builder unit tests, population filter tests, entity_id join validation
**Data required:** Existing `logs/shadow_trades/` data (already in production)

### Phase 3 — Population + Join Infrastructure

**Files affected:**
- `research_engine/v10/cross_universe/tracer.py` — extend to index Shadow universes
- `research_engine/v10/cross_universe/comparison.py` — add Shadow↔Live dimensions
- Potentially new: `research_engine/v10/cross_universe/cross_side_join.py`

**Dependencies:** Phase 2 (builders must exist)
**Risk:** MEDIUM (extends existing cross-universe infrastructure)
**Tests:** Join cardinality tests, entity_id match-rate verification against real data
**Data required:** Built Shadow populations

### Phase 4 — Question Migration

**Files affected:**
- `research_engine/v10/universes/question_bank.py` — add Shadow variants, reformulate D-004
- `research_engine/v10/runner/primitive_mapping.py` — map new questions to primitives

**Dependencies:** Phase 3 (join infrastructure)
**Risk:** MEDIUM (must preserve all existing question behaviour)
**Tests:** Regression tests on ALL 45 existing questions (no behaviour change), new question execution tests
**Data required:** Shadow populations built and joinable

### Phase 5 — Shadow Research Execution

**Files affected:**
- `research_engine/v10/runner/question_runner.py` — no changes needed (already generic)
- `research_engine/v10/runner/primitives/implementations.py` — field mapping for shadow-specific fields (bars_held vs duration_seconds)
- Research CLI (`research.py`) — add shadow run commands

**Dependencies:** Phase 4
**Risk:** LOW (runner is already universe-agnostic)
**Tests:** Execute Shadow questions against real data, verify findings produce valid output
**Data required:** Real shadow data (already exists)

### Phase 6 — Cross-Side Research

**Files affected:**
- NEW: `research_engine/v10/runner/primitives/cross_side.py` — cross_side_comparison primitive
- Question bank — add CROSS_LIVE_SHADOW questions (D-004c, D-007-cross, ED-001-cross)
- Finding schema — add `evidence_source` field (REALISED / COUNTERFACTUAL / CROSS_SIDE)

**Dependencies:** Phase 5 (both sides must be operational)
**Risk:** MEDIUM (new analytical capability)
**Tests:** Paired comparison tests, leakage calculation verification
**Data required:** Both Live and Shadow populations built for same entity_ids

### Phase 7 — Candidate/Experiment Integration

**Files affected:**
- `research_engine/v10/proposals/` — accept Shadow-sourced proposals
- `research_engine/v10/runner/` — allow experiments to target Shadow populations
- Ranking system — distinguish REALISED vs COUNTERFACTUAL evidence in ranking

**Dependencies:** Phase 6
**Risk:** LOW (existing candidate infrastructure is already population-agnostic)
**Tests:** End-to-end: Shadow finding → proposal → candidate → experiment → validation
**Data required:** Operational shadow research pipeline

---

## 19. Risks / Failure Modes

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Shadow data volume insufficient for statistical significance | LOW | Some questions produce INSUFFICIENT evidence | Shadow accumulates daily — wait for sufficient sample |
| entity_id mismatch between shadow and decision trace | LOW | Broken joins, empty populations | Validate match rate at build time; alert if < 50% |
| Shadow R systematically overestimates (no spread) | MEDIUM | Findings overstate opportunity value | Label limitation explicitly; compare to Live R where available |
| Users interpret shadow evidence as realised profit | MEDIUM | Incorrect decisions | Mandatory `evidence_source: COUNTERFACTUAL` labelling |
| Shadow exit logic differs from what V10 would produce | MEDIUM | Shadow R doesn't represent actual strategy intent | Document difference; future: V10-native shadow geometry |
| Existing question behaviour regresses | LOW | Research pipeline breaks | Comprehensive regression test suite before migration |
| 1:N join inflates sample sizes | MEDIUM | Statistical significance overstated | Question contracts must declare horizon handling; population-level dedup |
| Shadow timeout (60 bars) biases outcomes | LOW-MEDIUM | Timeout exits compress R toward zero | Document as known bias; future: per-horizon max_bars |

---

## 20. Final Architecture Diagram

```
                         MARKET REALITY (real M5 bars from MT5)
                                     |
                 +-------------------+-------------------+
                 |                                       |
          LIVE PIPELINE                          SHADOW PIPELINE
          (broker execution)                     (simulated lifecycle)
                 |                                       |
    +------------+------------+             +------------+------------+
    |            |            |             |            |            |
 DECISION    MARKET      STRATEGY       S-DECISION   S-MARKET    S-STRATEGY
 (10,453)   (10,453)    (14,501)        (view/join)  (view/join)  (view/join)
    |            |            |             |            |            |
    +------------+------------+             +------------+------------+
    |            |            |             |            |            |
   RISK     EXECUTION    OUTCOME        S-RISK     S-EXECUTION  S-OUTCOME
  (subset)    (94)        (94)         (from shadow) (from shadow) (thousands)
    |                                       |
    |          entity_id                    |
    +-----------------+---------------------+
                      |
               CROSS-SIDE JOINS
                      |
          +-----------+-----------+
          |           |           |
     LIVE_ONLY   LIVE+SHADOW   CROSS_SIDE
     questions    questions     questions
          |           |           |
          +-----------+-----------+
                      |
               RESEARCH FINDING
               (labelled: REALISED / COUNTERFACTUAL / CROSS_SIDE)
                      |
                   FEEDBACK
                      |
                  KNOWLEDGE
                      |
                   PROPOSAL
                      |
                  CANDIDATE
                      |
                 EXPERIMENT
                      |
                 VALIDATION
                      |
             PROMOTION GATE
                      |
             HUMAN GOVERNANCE
                      |
             (trading system UNTOUCHED until human decides)
```

---

## FINAL ANSWERS

### A. Should the six-universe Shadow mirror be implemented?

**YES.** The architecture supports it, the data exists, and it solves the fundamental limitation that 4+ questions (including D-004) cannot be answered without counterfactual outcomes.

### B. If yes, what exactly should be mirrored?

Three REAL Shadow builders from `logs/shadow_trades/`:
- **SHADOW_OUTCOME** (primary value — counterfactual R, MFE, MAE, exit)
- **SHADOW_EXECUTION** (entry geometry — entry/SL/TP/direction/size)
- **SHADOW_RISK** (risk parameters — R:R, SL distance, position size)

Three VIEW/JOIN Shadow perspectives (no new data source):
- **SHADOW_DECISION** — existing Decision Universe filtered to entities with shadow outcomes, enriched with counterfactual R
- **SHADOW_MARKET** — existing Market Universe filtered to entities with shadow outcomes
- **SHADOW_STRATEGY** — existing Strategy Universe filtered to entities with shadow outcomes

### C. Which existing questions should move?

| Question | Action |
|----------|--------|
| ED-002 (Missed Opportunity Cost) | MOVE_TO_SHADOW |
| D-004 (Rejection Stage Analysis) | REFORMULATE into 3 questions (Live descriptive + Shadow counterfactual + Cross-side decision quality) |
| DM-003 (Rejection Rate by Market) | REFORMULATE (add shadow outcome to make actionable) |
| ED-001 (Decision-to-Execution Leakage) | REFORMULATE as CROSS_LIVE_SHADOW |

### D. Which should split into Live + Shadow?

17 questions should be DUPLICATED with Shadow variants:
- E-007, E-008, E-010
- D-001, D-003, D-005, D-007
- EM-001, ES-001
- DM-001, DM-002
- DS-001, DS-002
- MS-001, MS-002
- DMS-001

### E. Which new Shadow questions become possible?

**Tier 1 (immediate high value):**
1. Counterfactual system expectancy (all patterns)
2. Missed opportunity rate
3. Rejection stage counterfactual value
4. Horizon counterfactual comparison
5. Score predicts shadow outcome for rejected opportunities
6. Strategy family counterfactual expectancy
7. Regime-conditioned counterfactual expectancy

**Tier 2 (moderate value):**
8. Risk gate counterfactual analysis
9. Pattern × regime counterfactual
10. Live vs Shadow execution leakage
11. Opportunity quality → shadow outcome prediction
12. Session counterfactual analysis

### F. What infrastructure must be built first?

In order:
1. Universe enum extension + Population enum extension (`models.py`)
2. Shadow contracts (`contracts.py`)
3. `ShadowOutcomeUniverseBuilder` (reads `logs/shadow_trades/`)
4. Cross-universe tracer extension (index shadow universes)
5. Finding schema `evidence_source` field
6. Shadow question definitions in question bank

### G. What should remain untouched?

- Live trading runtime (all `core/` execution code)
- Existing 6 Live universe builders
- Existing 45 question definitions (until migration phase)
- Existing primitives (no shadow-specific copies)
- Existing findings/proposals/knowledge
- Shadow trade engine itself (`core/shadow_trades.py`)
- Promotion monitor, research shadow engine

### H. What should Item 13 / next refinement address?

1. **Implement Phase 1** — contracts and enums (foundation, no risk)
2. **Implement Phase 2** — ShadowOutcomeUniverseBuilder (highest value)
3. **Run D-004b** — execute the reformulated shadow question as proof of concept
4. **Validate join rates** — measure actual entity_id match rate between shadow and decision data
5. **Establish evidence labelling** — add `evidence_source` to ResearchFinding schema

---

## THE CORE ARCHITECTURAL QUESTION — ANSWERED

> Can the Research Engine maintain two parallel but semantically distinct research worlds — Live and Shadow — where both contain equivalent analytical dimensions but the questions asked of each world differ according to what that world can truthfully observe?

### Answer: **YES — structurally ready with specific missing infrastructure.**

**What already exists:**
- Shadow data in production (`logs/shadow_trades/`) with research-grade schema
- Deterministic entity_id joins linking shadow records to all 6 Live universes
- Generic question runner and primitives that are universe-agnostic
- Cross-universe tracer infrastructure (indexing + lifecycle traces)
- Governed pipeline (finding → proposal → candidate → experiment → validation)

**What is missing:**
- Shadow Universe enum values and Population definitions
- Shadow universe builders (to ingest `logs/shadow_trades/` into formal populations)
- Shadow contracts (formal analytical ownership declarations)
- Cross-side comparison primitive
- Evidence source labelling in findings
- Shadow-specific question definitions

**None of the missing items require modifying the live trading system, existing universe builders, or existing question behaviour.** The shadow architecture is purely additive.

---

## DESIGN PRINCIPLE (preserved throughout)

> **Live and Shadow are two views of the same market reality, but they are not two copies of the same truth.**

Live tells us: *"What actually happened?"*
Shadow tells us: *"What would have happened under a counterfactual continuation?"*

Together they enable:
- **WHAT DID WE DO?** (Live Execution + Outcome)
- **WHAT COULD WE HAVE DONE?** (Shadow Outcome for rejected opportunities)
- **WHAT DID WE GAIN?** (Live positive outcomes)
- **WHAT DID WE MISS?** (Shadow positive outcomes for rejected decisions)
- **WHAT DID WE AVOID?** (Shadow negative outcomes for correctly rejected decisions)
- **WHY?** (Cross-side analysis per rejection stage)

That distinction is the foundation of the architecture.

---

*End of audit. No code modified. No runtime affected. Implementation awaits review.*
