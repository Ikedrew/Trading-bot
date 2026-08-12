# V10 RESEARCH ENGINE — DUAL LIVE/SHADOW ARCHITECTURE SPECIFICATION

**Date:** 2026-07-27  
**Type:** Architectural Design / Audit  
**Status:** READ-ONLY — No code modified, no runtime affected  
**Authoritative files inspected:** Listed per section  

---

## A. CURRENT LIVE RESEARCH ARCHITECTURE

### A.1 Pipeline Trace

```
LIVE DATA SOURCES
  logs/decision_trace/{SYMBOL}/{DATE}.jsonl        → Decision, Market, Strategy, Risk universes
  data/research/research_universe.jsonl            → Execution universe
  logs/execution_results/{SYMBOL}/{DATE}.jsonl     → entity_id enrichment for Execution
  logs/market_context/{SYMBOL}/{DATE}.jsonl        → Market universe (secondary)
  logs/strategy_observations/{SYMBOL}/{DATE}.jsonl → Strategy universe (secondary)
      ↓
UNIVERSE BUILDERS (research_engine/v10/universes/)
  ExecutionUniverseBuilder  → 94 records
  DecisionUniverseBuilder   → ~10,453 records
  MarketUniverseBuilder     → ~10,453 records (deduped)
  StrategyUniverseBuilder   → ~14,501 records (deduped)
  RiskUniverseBuilder       → subset that reached risk stage
  OutcomeUniverseBuilder    → 94 records (wraps Execution)
      ↓
POPULATIONS (defined in models.py, filtered by get_population())
  35+ named populations across 6 universes
      ↓
PRIMITIVES (research_engine/v10/runner/primitives/implementations.py)
  12 primitives: expectancy, distribution, comparison, conditional_expectancy,
  calibration, predictive_power, segmentation, transition,
  execution_quality, degradation, anomaly_analysis, exceptional_analysis
      ↓
QUESTION BANK (research_engine/v10/universes/question_bank.py)
  45 questions: E(10), D(7), M(6), S(4), ED(3), EM(2), ES(1), DM(3), DS(2), MS(3), EDM(1), DMS(1), EDMS(2)
      ↓
QUESTION RUNNER (research_engine/v10/runner/question_runner.py)
  QuestionRunner.run_question() → resolves primitives → executes → composes evidence
      ↓
FINDINGS (research_engine/v10/control_plane/finding_schema.py)
  ResearchFinding dataclass — standardised evidence container
  Persisted to: reports/research/questions/{question_id}/
      ↓
FEEDBACK (research_engine/v10/feedback/)
  generator.py, model.py, persistence.py
  Produces feedback records linking findings to actionable observations
      ↓
KNOWLEDGE (research_engine/v10/knowledge/)
  engine.py, model.py, store.py
  Maintains persistent knowledge state with confidence levels
      ↓
PROPOSALS (research_engine/v10/proposals/)
  generator.py, model.py, store.py, ranking.py
  Generates governed proposals from knowledge + feedback
      ↓
CANDIDATES (research_engine/v10/candidates/)
  models.py, candidate_lifecycle.py, candidate_registry.py
  Designs testable candidates with POPULATION_FILTER configurations
      ↓
EXPERIMENTS (research_engine/v10/proposals/)
  experiment.py, run_experiment.py, first_candidate.py
  Executes candidate against population, measures improvement
      ↓
VALIDATION (research_engine/v10/proposals/validator.py)
  Statistical validation of experiment results
      ↓
PROMOTION (research_engine/v10/proposals/promotion.py)
  Determines PROMOTION_ELIGIBLE status — never auto-deploys
```

### A.2 Authoritative Files

| Stage | File | Class/Function |
|-------|------|---------------|
| Universe base | `universes/base.py` | `UniverseBuilder` ABC |
| Universe models | `universes/models.py` | `Universe`, `Population`, `NewEngineQuestion` |
| Universe contracts | `universes/contracts.py` | `UniverseContract`, `PopulationContract`, `JoinContract` |
| Question bank | `universes/question_bank.py` | `QUESTION_BANK` (45 questions) |
| Runner | `runner/question_runner.py` | `QuestionRunner.run_question()` |
| Primitives | `runner/primitives/implementations.py` | 12 `AnalysisPrimitive` subclasses |
| Primitive mapping | `runner/primitive_mapping.py` | `QUESTION_PARAMETERS`, `ANALYSIS_TYPE_PRIMITIVES` |
| Finding schema | `control_plane/finding_schema.py` | `ResearchFinding` dataclass |
| Cross-universe | `cross_universe/tracer.py` | `CrossUniverseTracer` |
| Cross-universe | `cross_universe/comparison.py` | `ComparisonBuilder` |
| Feedback | `feedback/generator.py` | Feedback generation |
| Knowledge | `knowledge/engine.py` | Knowledge state engine |
| Proposals | `proposals/generator.py` | Proposal generation |
| Proposals | `proposals/ranking.py` | Evidence-quality-aware ranking |
| Candidates | `candidates/models.py` | Candidate data model |
| Experiments | `proposals/experiment.py` | Experiment execution |
| Validation | `proposals/validator.py` | Statistical validation |
| Promotion | `proposals/promotion.py` | Promotion eligibility |

---

## B. SHADOW TRACE

### B.1 End-to-End Shadow Lifecycle

```
MARKET OBSERVATION (real M5 bars from MT5 via bar_provider.py)
    ↓
DECISION / PATTERN (V10Pipeline evaluates — produces engine result)
    ↓
SHADOW CREATION (live_scanner.py ~line 730)
  Trigger: pattern detected AND horizon classifier produces eligible horizons
  Scope:   ALL decisions where pattern found (EXECUTE AND NO_TRADE)
    ↓
SHADOW ENTRY (build_all_horizon_trades())
  Entry: market price at decision time (ask if BUY, bid if SELL)
  Geometry: horizon-specific SL/TP from M5/M15/H1 structure levels
    ↓
SHADOW RISK / SL / TP
  SL: Structure-based (nearest support/resistance ± buffer per horizon)
  TP: R:R ratio applied to SL distance (horizon-specific)
  Position size: fixed 0.01 (not risk-managed)
    ↓
BAR-BY-BAR PROGRESSION (bar_provider.py → get_shadow_engine().evaluate_bar())
  Called: every closed M5 bar, every symbol
  Progression: bars_elapsed++, MFE/MAE update, state_log append
    ↓
SHADOW EXIT
  SL hit: bar_low <= SL (BUY) or bar_high >= SL (SELL)
  TP hit: bar_high >= TP (BUY) or bar_low <= TP (SELL)
  Timeout: bars_elapsed >= 60 (5 hours)
  Priority: SL checked before TP within same bar
    ↓
R-MULTIPLE / MFE / MAE
  R: compute_r_multiple(direction, entry, exit, stop_loss) from core/trade_truth.py
  MFE: compute_mfe_r() — maximum favourable excursion in R
  MAE: compute_mae_r() — maximum adverse excursion in R
    ↓
PERSISTENCE
  Local: logs/shadow_trades/{SYMBOL}/{DATE}.jsonl
  S3: s3://v10-engine/shadow_trades/
  Schema: shadow_trades_v2 (4 domains: identity, decision_snapshot, simulation_environment, simulated_outcome)
```

### B.2 Shadow Trade Types

| Type | ID Format | Creation Trigger | Scope | Purpose |
|------|-----------|-----------------|-------|---------|
| **Horizon Shadow** | `hshadow_{cycle}_{symbol}_{HORIZON}` | Pattern + eligible horizons | ALL decisions with pattern | Counterfactual per-horizon analysis |
| **Primary Shadow** | `shadow_{cycle}_{symbol}` | EXECUTE decision | Only executed | Live vs shadow comparison |
| **Research Shadow** | via `research_shadow_engine.py` | RESEARCH_WOULD_EXECUTE | Promotion monitor | Dual EV tracking |

### B.3 Key Evidence (from actual persisted data)

**Verified:** `logs/shadow_trades/EURUSD/2026-08-11.jsonl` contains:
- `shadow_32547_EURUSD` — primary shadow (EXECUTE path), R=+0.0527, exit=take_profit, bars=1
- `hshadow_32547_EURUSD_SCALP` — horizon shadow (same entity), R=-0.5238, exit=max_bars_timeout, bars=60
- Both share `entity_id: "EURUSD_1786445100"`
- Both contain full decision_snapshot with frozen entry/SL/TP/direction/score/pattern

### B.4 Critical Facts

| Fact | Evidence | Source |
|------|----------|--------|
| Horizon shadows run for NO_TRADE decisions | Code comment: "regardless of whether the engine approved execution" | `live_scanner.py` ~line 718 |
| Bar evaluation uses REAL market data | `bar_provider.py` passes `candles[closed_i].high/low/close` | VERIFIED |
| entity_id is deterministic | Format `{symbol}_{bar_time}` — same as Decision universe | VERIFIED in code + data |
| Multiple shadows per entity | Different horizons produce different trade_ids for same entity_id | VERIFIED |
| Shadow never affects trading | All wrapped in `try/except: pass` | VERIFIED throughout |

---

## C. JOIN MODEL

### C.1 Authoritative Cross-Side Join Key

**`entity_id`** — format: `{symbol}_{bar_time}`

| Property | Status |
|----------|--------|
| Deterministic | YES — same bar always produces same entity_id |
| Present in Decision Universe | YES — primary identity field |
| Present in Market Universe | YES — from decision_trace |
| Present in Strategy Universe | YES — from decision_trace |
| Present in Risk Universe | YES — from decision_trace |
| Present in Execution Universe | YES — enriched via deal/ticket |
| Present in Shadow records | YES — `identity.entity_id` |
| Unique per M5 bar per symbol | YES |
| Immutable | YES — never changes after creation |

### C.2 Join Relationships

```
DECISION (entity_id) ←──1:1──→ MARKET (entity_id)
DECISION (entity_id) ←──1:1──→ STRATEGY (entity_id)
DECISION (entity_id) ←──1:1──→ RISK (entity_id) [subset]
DECISION (entity_id) ←──1:1──→ EXECUTION (entity_id) [EXECUTE only, ~0.9%]
EXECUTION (entity_id) ←──1:1──→ OUTCOME (entity_id)
DECISION (entity_id) ←──1:N──→ SHADOW_OUTCOME (entity_id) [multiple horizons]
```

### C.3 Join Cardinality for Shadow

One `entity_id` may have:
- 0 shadow records (no pattern detected, or pattern detected but no eligible horizons)
- 1 shadow record (one eligible horizon)
- 2-3 shadow records (multiple eligible horizons: SCALP, INTRADAY, EXTENDED)
- +1 primary shadow (if EXECUTE decision also generated primary shadow)

### C.4 Join Quality Assessment

| Risk | Likelihood | Impact | Evidence |
|------|-----------|--------|----------|
| Empty entity_id in shadow | LOW | Record unjoinable | Observed occasionally when V10 pipeline errors |
| Duplicate entity_id (two cycles same bar) | NEGLIGIBLE | Bot processes each bar once per symbol | By design |
| Shadow exists but Decision missing | NEGLIGIBLE | Decision trace always written first | Sequence guaranteed |
| Decision exists but Shadow missing | COMMON | Many decisions have no pattern → no shadow | Expected (only pattern-detected decisions get shadows) |

**Estimated shadow coverage of Decision population:**
- ~10,453 total decisions
- Pattern detected in ~30-60% → ~3,000-6,000 decisions have shadows
- Each may produce 1-3 shadow records → estimated 5,000-15,000 shadow outcome records

---

## D. DUAL SIX-UNIVERSE MODEL

### D.1 Proposed Architecture

```
LIVE                                    SHADOW
────                                    ──────
EXECUTION (physical)                    SHADOW_EXECUTION (physical — from shadow records)
DECISION (physical)                     SHADOW_DECISION (derived view — Decision filtered to entities with shadow)
MARKET (physical)                       SHADOW_MARKET (derived view — Market filtered to entities with shadow)
STRATEGY (physical)                     SHADOW_STRATEGY (derived view — Strategy filtered to entities with shadow)
RISK (physical)                         SHADOW_RISK (physical — from shadow risk_config_snapshot)
OUTCOME (derived — wraps Execution)     SHADOW_OUTCOME (physical — from shadow simulated_outcome)
```

### D.2 Shadow Universe Specifications

#### SHADOW_OUTCOME (PRIMARY — the core value proposition)

| Property | Specification |
|----------|---------------|
| Source | `logs/shadow_trades/{SYMBOL}/{DATE}.jsonl` (schema: shadow_trades_v2) |
| Builder | NEW: `ShadowOutcomeUniverseBuilder` |
| Type | Physical (independent data source) |
| Grain | 1 closed shadow trade = 1 counterfactual outcome |
| Identity | `shadow_trade_id` (trade_id from shadow record) |
| Join keys | `entity_id` (to all Live universes), `shadow_trade_id`, `symbol`, `correlation_id` |
| Contract | "Counterfactual economic consequence of a shadow opportunity under the defined shadow execution/risk/exit model" |
| Provenance | schema_version, source files, content_hash, generation_timestamp |
| Key fields | `r_multiple` (counterfactual), `mfe_r`, `mae_r`, `exit_reason`, `bars_held`, `exit_price`, `direction`, `trade_horizon` |
| Populations | ALL_SHADOW_OUTCOMES, SHADOW_WINS, SHADOW_LOSSES, SHADOW_TP_HIT, SHADOW_SL_HIT, SHADOW_TIMEOUT, SHADOW_HORIZON_SCALP, SHADOW_HORIZON_INTRADAY, SHADOW_HORIZON_EXTENDED |
| Primitives | expectancy, distribution, comparison, segmentation, degradation, predictive_power, conditional_expectancy, transition |
| Cannot provide | Realised P&L, broker slippage, commission, fill quality |

#### SHADOW_EXECUTION (secondary — entry geometry analysis)

| Property | Specification |
|----------|---------------|
| Source | Same `logs/shadow_trades/` — extracts `decision_snapshot` domain |
| Builder | NEW: `ShadowExecutionUniverseBuilder` |
| Type | Physical (from same source, different projection) |
| Grain | 1 shadow trade entry intent = 1 hypothetical execution |
| Identity | `shadow_trade_id` |
| Join keys | `entity_id`, `symbol` |
| Contract | "The hypothetical execution parameters (entry/SL/TP/direction/size) that the shadow model applied" |
| Key fields | `entry_price`, `stop_loss`, `take_profit`, `direction`, `position_size`, `spread_at_entry`, `risk_distance_pips`, `reward_risk_ratio` |
| Populations | ALL_SHADOW_EXECUTIONS, SHADOW_EXEC_BUY, SHADOW_EXEC_SELL |
| Primitives | distribution, comparison, segmentation |
| Cannot provide | Actual broker fill, slippage, requotes |

#### SHADOW_RISK (secondary — risk parameter sensitivity)

| Property | Specification |
|----------|---------------|
| Source | Same `logs/shadow_trades/` — extracts `decision_snapshot.risk_config_snapshot` |
| Builder | NEW: `ShadowRiskUniverseBuilder` |
| Type | Physical |
| Grain | 1 shadow trade risk configuration |
| Identity | `shadow_trade_id` |
| Join keys | `entity_id`, `symbol` |
| Contract | "The risk parameters (SL distance, R:R ratio, position size) governing the counterfactual simulation" |
| Key fields | `risk_price_distance`, `risk_pips`, `reward_risk_ratio`, `position_size` |
| Populations | ALL_SHADOW_RISK_CONFIGS |
| Primitives | comparison, segmentation (R:R groups), predictive_power (R:R → outcome) |

#### SHADOW_DECISION (derived view)

| Property | Specification |
|----------|---------------|
| Source | Existing Decision Universe FILTERED to entities with shadow outcomes |
| Builder | NOT required — computed population via cross-join |
| Type | Derived view (not independent data) |
| Contract | "The Live decision context for entities that ALSO have counterfactual outcomes available" |
| Implementation | `Decision Universe records WHERE entity_id IN ShadowOutcome.entity_ids` |
| Key fields | All Decision fields + joined shadow r_multiple for analysis |
| Why derived | The decision is the SAME observation. Shadow doesn't change what was decided. It adds what WOULD have happened. |

#### SHADOW_MARKET (derived view)

| Property | Specification |
|----------|---------------|
| Source | Existing Market Universe FILTERED to entities with shadow outcomes |
| Builder | NOT required — computed population via cross-join |
| Type | Derived view |
| Contract | "The market state at decision time for entities that also have counterfactual outcomes" |
| Implementation | `Market Universe records WHERE entity_id IN ShadowOutcome.entity_ids` |
| Why derived | Market state is the SAME reality — it doesn't change counterfactually. |

#### SHADOW_STRATEGY (derived view)

| Property | Specification |
|----------|---------------|
| Source | Existing Strategy Universe FILTERED to entities with shadow outcomes |
| Builder | NOT required — computed population via cross-join |
| Type | Derived view |
| Contract | "The strategy evaluation for entities that also have counterfactual outcomes" |
| Implementation | `Strategy Universe records WHERE entity_id IN ShadowOutcome.entity_ids` |
| Why derived | Strategy selection is the SAME observation — shadow reveals what it WOULD have produced. |

### D.3 Why Three Physical + Three Derived

The Live side has six PHYSICAL universes because each has an independent data source.

The Shadow side only has THREE independent data sources (all from `logs/shadow_trades/`):
1. `simulated_outcome` → SHADOW_OUTCOME
2. `decision_snapshot` (entry geometry) → SHADOW_EXECUTION
3. `decision_snapshot.risk_config_snapshot` → SHADOW_RISK

The other three Shadow dimensions (Decision, Market, Strategy) are the SAME observations as Live — the shadow doesn't change what was decided, what the market was doing, or what strategy was selected. It only changes the OUTCOME. Therefore those three are derived views (filter the Live universe to entities that have shadow data).

---

## E. SIX LIVE UNIVERSE CONTRACTS (Confirmed)

No changes required to existing Live contracts. They are correct as defined in `contracts.py`.

| Universe | Grain | Identity | Contract Summary | Changes Needed |
|----------|-------|----------|-----------------|----------------|
| EXECUTION | 1 completed trade | trade_id | Realised execution outcomes with broker-confirmed P&L | NONE |
| DECISION | 1 decision event | entity_id | Pipeline decision (EXECUTE/NO_TRADE) with full reasoning | NONE |
| MARKET | 1 market snapshot | entity_id | Market state observation at decision time | NONE |
| STRATEGY | 1 strategy evaluation | entity_id | Strategy assessment for an opportunity | NONE |
| RISK | 1 risk evaluation | entity_id | Risk-control mechanism assessment | NONE |
| OUTCOME | 1 completed trade | entity_id | Realised economic result (wraps Execution) | NONE |

---

## F. SIX SHADOW UNIVERSE CONTRACTS (Proposed)

| Universe | Grain | Identity | Contract |
|----------|-------|----------|----------|
| SHADOW_OUTCOME | 1 closed shadow trade | shadow_trade_id | Counterfactual economic consequence under shadow model |
| SHADOW_EXECUTION | 1 shadow entry intent | shadow_trade_id | Hypothetical execution parameters applied by shadow |
| SHADOW_RISK | 1 shadow risk config | shadow_trade_id | Risk assumptions governing counterfactual simulation |
| SHADOW_DECISION | 1 decision (filtered) | entity_id | Live decision context for shadow-available entities |
| SHADOW_MARKET | 1 market snapshot (filtered) | entity_id | Market state for shadow-available entities |
| SHADOW_STRATEGY | 1 strategy eval (filtered) | entity_id | Strategy context for shadow-available entities |

**Semantic guarantee:** SHADOW_OUTCOME.r_multiple is ALWAYS labelled as counterfactual. It is NEVER described as realised performance. The contract explicitly states this.

---

## G. PRIMITIVE COMPATIBILITY MATRIX

| Primitive | LIVE | SHADOW | CROSS-SIDE | Notes |
|-----------|------|--------|------------|-------|
| `expectancy` | YES | YES | N/A | Same computation, different semantic label on output |
| `distribution` | YES | YES | N/A | Universal numeric analysis |
| `comparison` | YES | YES | YES (groups=side) | Can compare Live group vs Shadow group |
| `conditional_expectancy` | YES | YES | N/A | Condition fields available via join |
| `calibration` | YES | PARTIAL | N/A | Calibrating against counterfactual is weaker (validates model, not reality) |
| `predictive_power` | YES | YES | N/A | Monotonicity analysis is universal |
| `segmentation` | YES | YES | N/A | Categorical segmentation works on any population |
| `transition` | YES | YES | N/A | Temporal analysis if timestamps present |
| `execution_quality` | YES | PARTIAL | N/A | Shadow has bars_held not duration_seconds; exit_reason present |
| `degradation` | YES | YES | N/A | Time-series comparison works on both |
| `anomaly_analysis` | YES | NO | N/A | Shadow has no anomaly concept |
| `exceptional_analysis` | YES | PARTIAL | N/A | Needs shadow-specific exceptional criteria |
| **`cross_side_comparison`** | N/A | N/A | **NEW REQUIRED** | Paired entity comparison: Live R vs Shadow R |

### G.1 New Primitive: `cross_side_comparison`

```python
class CrossSideComparisonPrimitive(AnalysisPrimitive):
    """Compares paired Live and Shadow outcomes for the same entities."""
    
    # Input: population with both live_r_multiple and shadow_r_multiple per entity
    # (constructed by joining Outcome + ShadowOutcome on entity_id)
    
    # Output:
    #   matched_count: entities with both outcomes
    #   live_mean_r: mean realised R
    #   shadow_mean_r: mean counterfactual R  
    #   leakage: shadow_mean_r - live_mean_r
    #   direction_agreement: % where both positive or both negative
    #   correlation: Pearson(live_r, shadow_r)
```

This is the ONLY genuinely new primitive required. All other analyses are expressible with existing primitives operating on appropriately constructed populations.

---

## H. COMPLETE 45-QUESTION CLASSIFICATION

### Classification Legend

| Code | Meaning |
|------|---------|
| LIVE_ONLY | Requires realised broker outcomes — shadow cannot answer |
| LIVE+SHADOW | Valid on Live (keep). Meaningful Shadow counterpart exists (create pair) |
| SHADOW_ONLY | Structurally requires counterfactual data — Live cannot answer |
| CROSS | Requires comparing Live outcomes with Shadow outcomes |
| SPLIT | Current question conflates two distinct analytical intents |

---

| # | ID | Title | Classification | Reasoning |
|---|-----|-------|---------------|-----------|
| 1 | E-001 | System Expectancy | LIVE+SHADOW | Live: realised EV. Shadow: total opportunity pool EV (much larger sample) |
| 2 | E-002 | Win/Loss Distribution | LIVE+SHADOW | Live: realised shape. Shadow: opportunity shape (reveals selection bias) |
| 3 | E-003 | Exit Reason Distribution | LIVE+SHADOW | Live: broker exits. Shadow: model exits (compare SL/TP ratios) |
| 4 | E-004 | Execution Quality by Session | LIVE_ONLY | Slippage/fill quality is broker-only reality |
| 5 | E-005 | Probability of Ruin | LIVE_ONLY | Account survival requires realised variance |
| 6 | E-006 | Out-of-Sample Validation | LIVE_ONLY | Overfitting detection requires realised holdout |
| 7 | E-007 | Stop Placement | LIVE+SHADOW | Live: actual SL outcomes. Shadow: SL sensitivity across all signals |
| 8 | E-008 | Pattern Degradation | LIVE+SHADOW | Live: degradation in executed. Shadow: degradation in all signals (earlier detection) |
| 9 | E-009 | Duration vs Outcome | LIVE+SHADOW | Live: real duration. Shadow: bars_held vs R (same concept, larger sample) |
| 10 | E-010 | R:R Effectiveness | LIVE+SHADOW | Live: achieved R:R. Shadow: intended R:R → counterfactual R (natural experiment) |
| 11 | D-001 | Score Predictive Power | LIVE+SHADOW | Live: score → realised R (executed). Shadow: score → counterfactual R (all signals) |
| 12 | D-002 | EV Calibration | LIVE_ONLY | Calibration requires realised truth (not model output) |
| 13 | D-003 | Threshold Effectiveness | CROSS | "Move threshold" inherently asks: above=Live R, below=Shadow R |
| 14 | D-004 | Rejection Stage Analysis | SPLIT | Conflates "where rejected" (descriptive/Live) + "cost of rejection" (counterfactual/Shadow) + "was rejection correct" (cross-side) |
| 15 | D-005 | Opportunity Quality | LIVE+SHADOW | Quality prediction testable on both executed and full signal set |
| 16 | D-006 | Opportunity Failure | LIVE_ONLY | "Failure characterisation" = things we traded that lost (real failures only) |
| 17 | D-007 | Risk Gate Value | CROSS | "Does risk protect or destroy?" requires blocked→Shadow R vs approved→Live R |
| 18 | M-001 | Regime Predicts Outcomes | LIVE+SHADOW | Regime → R testable on both sides (shadow has regime via join) |
| 19 | M-002 | HTF Alignment Value | LIVE+SHADOW | Alignment prediction testable on full signal set via shadow |
| 20 | M-003 | Volatility State Impact | LIVE+SHADOW | Volatility → R testable both sides |
| 21 | M-004 | Market Structure Clarity | LIVE+SHADOW | Clarity → R testable both sides |
| 22 | M-005 | Location Quality Impact | LIVE+SHADOW | Location → R testable both sides |
| 23 | M-006 | Session Edge Variation | LIVE+SHADOW | Session → R testable both sides |
| 24 | S-001 | Strategy Family Expectancy | LIVE+SHADOW | Strategy → R per family on both sides |
| 25 | S-002 | Pattern Expectancy | LIVE+SHADOW | Pattern → R testable both sides |
| 26 | S-003 | Strategy Selection Accuracy | LIVE_ONLY | Selection accuracy requires realised truth for "did selection improve?" |
| 27 | S-004 | Strategy Rejection Patterns | SHADOW_ONLY | "Profitable patterns the engine misses" requires counterfactual R of rejected |
| 28 | ED-001 | Edge Leakage | CROSS | True leakage = Shadow R (intended) vs Live R (achieved) for same entity |
| 29 | ED-002 | Missed Opportunity Cost | SHADOW_ONLY | "Would have succeeded if allowed" = counterfactual of rejected decisions |
| 30 | ED-003 | Position Sizing | LIVE_ONLY | Sizing impact requires real account P&L |
| 31 | EM-001 | Regime-Conditioned Expectancy | LIVE+SHADOW | Regime × expectancy on both sides |
| 32 | EM-002 | Market Drift | LIVE_ONLY | Drift detection requires realised temporal truth |
| 33 | ES-001 | Execution by Strategy | LIVE+SHADOW | Strategy families → R on both sides |
| 34 | DM-001 | Decision Quality Under Regime | LIVE+SHADOW | Score accuracy by regime — both sides |
| 35 | DM-002 | Opportunity vs Market State | LIVE+SHADOW | Quality robustness by market — both sides |
| 36 | DM-003 | Rejection Rate by Market | SPLIT | "Rejection rate" = descriptive/Live. "Missing edge" = counterfactual/Shadow |
| 37 | DS-001 | Strategy Confidence Calibration | LIVE+SHADOW | Confidence → R testable both sides |
| 38 | DS-002 | Strategy Conditions vs Outcome | LIVE+SHADOW | Conditions → R testable both sides |
| 39 | MS-001 | Strategy x Regime | LIVE+SHADOW | Strategy×regime interaction — both sides |
| 40 | MS-002 | Pattern x Market Context | LIVE+SHADOW | Pattern×market — both sides |
| 41 | MS-003 | Strategy Availability by Market | LIVE_ONLY | Coverage gap = structural/descriptive (no outcome needed) |
| 42 | EDM-001 | Complete Lifecycle | LIVE_ONLY | Full broker-confirmed lifecycle |
| 43 | DMS-001 | Decision x Strategy x Market | LIVE+SHADOW | Multi-dimensional — both sides (shadow enables this with adequate sample) |
| 44 | EDMS-001 | Full System Attribution | LIVE_ONLY | Attribution requires realised end-to-end |
| 45 | EDMS-002 | Promotion Impact Analysis | CROSS | Promotion confidence requires both Live and Shadow convergence |

### H.1 Summary Counts

| Classification | Count | Questions |
|----------------|-------|-----------|
| LIVE_ONLY | 11 | E-004, E-005, E-006, D-002, D-006, ED-003, EM-002, MS-003, EDM-001, EDMS-001, S-003 |
| LIVE+SHADOW | 24 | E-001/2/3/7/8/9/10, D-001/5, M-001/2/3/4/5/6, S-001/2, EM-001, ES-001, DM-001/2, DS-001/2, MS-001/2, DMS-001 |
| SHADOW_ONLY | 2 | ED-002, S-004 |
| CROSS | 4 | D-003, D-007, ED-001, EDMS-002 |
| SPLIT | 2 | D-004 (→3), DM-003 (→2) |
| **TOTAL** | **45** (with 2 splits producing 5 sub-questions → net +3) |

---

## I. QUESTION TRANSFORMATION MAP

| Original | Live Form | Shadow Form | Cross-Side Form | Action |
|----------|-----------|-------------|-----------------|--------|
| E-001 | E-001 (unchanged) | SE-001 "Counterfactual system expectancy" | — | Create Shadow pair |
| E-002 | E-002 (unchanged) | SE-002 "Counterfactual distribution" | — | Create Shadow pair |
| E-003 | E-003 (unchanged) | SE-003 "Shadow exit distribution" | — | Create Shadow pair |
| E-004 | E-004 (unchanged) | — | — | Unchanged |
| E-005 | E-005 (unchanged) | — | — | Unchanged |
| E-006 | E-006 (unchanged) | — | — | Unchanged |
| E-007 | E-007 (unchanged) | SE-007 "SL sensitivity on all signals" | — | Create Shadow pair |
| E-008 | E-008 (unchanged) | SE-008 "Pattern degradation (shadow)" | — | Create Shadow pair |
| E-009 | E-009 (unchanged) | SE-009 "Duration vs shadow outcome" | — | Create Shadow pair |
| E-010 | E-010 (unchanged) | SE-010 "R:R counterfactual sensitivity" | — | Create Shadow pair |
| D-001 | D-001 (unchanged) | SD-001 "Score predicts shadow R" | — | Create Shadow pair |
| D-002 | D-002 (unchanged) | — | — | Unchanged |
| D-003 | D-003 (unchanged) | — | X-001 "Threshold optimisation (both sides)" | Create Cross-side |
| D-004 | D-004a "Rejection funnel (descriptive)" | SD-004 "Rejection stage counterfactual R" | X-002 "Correct vs incorrect rejections" | Split into 3 |
| D-005 | D-005 (unchanged) | SD-005 "Quality predicts shadow R" | — | Create Shadow pair |
| D-006 | D-006 (unchanged) | — | — | Unchanged |
| D-007 | D-007 (unchanged) | — | X-003 "Risk gate value (live vs shadow)" | Create Cross-side |
| M-001 | M-001 (unchanged) | SM-001 "Regime → shadow R" | — | Create Shadow pair |
| M-002 | M-002 (unchanged) | SM-002 "HTF alignment → shadow R" | — | Create Shadow pair |
| M-003 | M-003 (unchanged) | SM-003 "Volatility → shadow R" | — | Create Shadow pair |
| M-004 | M-004 (unchanged) | SM-004 "Clarity → shadow R" | — | Create Shadow pair |
| M-005 | M-005 (unchanged) | SM-005 "Location → shadow R" | — | Create Shadow pair |
| M-006 | M-006 (unchanged) | SM-006 "Session → shadow R" | — | Create Shadow pair |
| S-001 | S-001 (unchanged) | SS-001 "Strategy family shadow R" | — | Create Shadow pair |
| S-002 | S-002 (unchanged) | SS-002 "Pattern shadow R" | — | Create Shadow pair |
| S-003 | S-003 (unchanged) | — | — | Unchanged |
| S-004 | — | SS-004 "Strategy rejection counterfactual" | — | Move to Shadow |
| ED-001 | ED-001 (unchanged) | — | X-004 "Live vs Shadow R leakage" | Create Cross-side |
| ED-002 | — | SED-002 "Missed opportunity counterfactual" | — | Move to Shadow |
| ED-003 | ED-003 (unchanged) | — | — | Unchanged |
| EM-001 | EM-001 (unchanged) | SEM-001 "Regime × shadow R" | — | Create Shadow pair |
| EM-002 | EM-002 (unchanged) | — | — | Unchanged |
| ES-001 | ES-001 (unchanged) | SES-001 "Strategy × shadow R" | — | Create Shadow pair |
| DM-001 | DM-001 (unchanged) | SDM-001 "Score accuracy × regime (shadow)" | — | Create Shadow pair |
| DM-002 | DM-002 (unchanged) | SDM-002 "Quality × market (shadow)" | — | Create Shadow pair |
| DM-003 | DM-003a "Rejection rate (descriptive)" | SDM-003 "High-rejection market counterfactual R" | — | Split into 2 |
| DS-001 | DS-001 (unchanged) | SDS-001 "Confidence → shadow R" | — | Create Shadow pair |
| DS-002 | DS-002 (unchanged) | SDS-002 "Conditions → shadow R" | — | Create Shadow pair |
| MS-001 | MS-001 (unchanged) | SMS-001 "Strategy × regime (shadow)" | — | Create Shadow pair |
| MS-002 | MS-002 (unchanged) | SMS-002 "Pattern × market (shadow)" | — | Create Shadow pair |
| MS-003 | MS-003 (unchanged) | — | — | Unchanged |
| EDM-001 | EDM-001 (unchanged) | — | — | Unchanged |
| DMS-001 | DMS-001 (unchanged) | SDMS-001 "Decision × strategy × market (shadow)" | — | Create Shadow pair |
| EDMS-001 | EDMS-001 (unchanged) | — | — | Unchanged |
| EDMS-002 | EDMS-002 (unchanged) | — | X-005 "Promotion requires both-side convergence" | Create Cross-side |

### I.1 Net Inventory After Transformation

| Category | Count |
|----------|-------|
| Live questions (preserved) | 45 (all unchanged) |
| Shadow pairs (new) | 26 (SE-*, SD-*, SM-*, SS-*, SEM-*, SES-*, SDM-*, SDS-*, SMS-*, SDMS-*) |
| Shadow-only (moved) | 2 (SS-004, SED-002) |
| Cross-side (new) | 5 (X-001 through X-005) |
| Split products (new) | 3 (D-004a, D-004→SD-004+X-002, DM-003a, DM-003→SDM-003) |
| **Total research questions** | **~78** |

**No questions deleted. All 45 originals preserved. Shadow/Cross additions are purely additive.**

---

## J. D-004 REDESIGN

### J.1 Original Question

```python
D_004 = NewEngineQuestion(
    question_id="D-004",
    title="Rejection Stage Analysis",
    research_intent="Where in the pipeline are trades rejected? Which rejection stage
                     removes the most potential edge vs protecting from losses?",
    required_universes=(Universe.DECISION,),
    required_populations=(NO_TRADE_DECISIONS, REJECTED_AT_*),
    required_fields=("terminal_stage", "terminal_reason"),
    analysis_type=AnalysisType.SEGMENTATION,
)
```

### J.2 Why It Fails

The intent has TWO distinct analytical requirements:
1. "Where are trades rejected?" — descriptive, counts only, NO outcome needed → LIVE
2. "Which stage removes edge vs protects?" — requires knowing outcome of rejected → SHADOW/CROSS

The current single question conflates these. The metric (`r_multiple` via SEGMENTATION) only works for (2) but the population (NO_TRADE_DECISIONS) only produces meaningful R on the Shadow side.

### J.3 Correct Decomposition

**D-004a — LIVE (descriptive pipeline funnel)**
```
ID:         D-004a (or keep D-004 with narrowed scope)
Title:      "Decision Pipeline Rejection Funnel"
Intent:     "Where are opportunities rejected? What is the funnel shape?"
Side:       LIVE_ONLY
Universe:   DECISION
Population: NO_TRADE_DECISIONS
Primitive:  segmentation (dimensions=["terminal_stage"], metric=count/%)
Fields:     terminal_stage, terminal_reason
Outcome:    No r_multiple needed — purely count-based segmentation
Sample:     ~10,453
```

**SD-004 — SHADOW (counterfactual opportunity value)**
```
ID:         SD-004
Title:      "Rejection Stage Counterfactual Expectancy"
Intent:     "What counterfactual R did rejected opportunities produce, by rejection stage?"
Side:       SHADOW_ONLY
Universe:   SHADOW_OUTCOME joined to DECISION (for terminal_stage)
Population: SHADOW_FROM_NO_TRADE
Primitive:  segmentation (dimensions=["terminal_stage"], metric=r_multiple)
Fields:     shadow r_multiple, Decision.terminal_stage (via entity_id join)
Outcome:    Mean counterfactual R per rejection stage
Sample:     Estimated 3,000-8,000 (entities with both shadow + NO_TRADE)
Value:      "Opportunity stage produces +0.12R counterfactual → we're leaving money there"
            "Risk stage produces -0.08R counterfactual → correctly protecting"
```

**X-002 — CROSS (decision quality assessment)**
```
ID:         X-002
Title:      "Rejection Decision Quality"
Intent:     "Which stages correctly reject losing opportunities vs incorrectly reject profitable ones?"
Side:       CROSS_LIVE_SHADOW
Universe:   DECISION + SHADOW_OUTCOME
Population: NO_TRADE decisions with shadow R available
Primitive:  cross_side_comparison OR segmentation with classification
Fields:     Decision.terminal_stage, ShadowOutcome.r_multiple (classified as positive/negative)
Outcome:    Per stage: {correctly_rejected: count(shadow_R < 0), missed_opportunity: count(shadow_R > 0), protection_rate: %}
Sample:     Same as SD-004
Value:      "Risk stage: 78% correct protection, 22% missed opportunity"
            "Entry stage: 45% correct, 55% missed → entry criteria too tight"
```

### J.4 Lineage

```
D-004 (original)
    ├── D-004a (Live successor — descriptive funnel, no R needed)
    ├── SD-004 (Shadow successor — counterfactual R by stage)
    └── X-002 (Cross-side successor — protection vs missed classification)

relationship: SPLIT
reason: "Original conflated descriptive pipeline analysis with counterfactual outcome analysis"
```

---

## K. HUMAN QUESTION ARCHITECTURE

### K.1 Entry Points

A human-originated research question enters the system through:

```
HUMAN OBSERVATION
  "I think we're rejecting too many opportunities in the London session"
      ↓
QUESTION INTAKE (not yet implemented — future CLI/interface)
      ↓
CLASSIFICATION
  Side: {LIVE, SHADOW, CROSS_LIVE_SHADOW}
  Universe(s): {DECISION, MARKET, SHADOW_OUTCOME, ...}
  Population: {NO_TRADE_DECISIONS × SESSION_LONDON, SHADOW_FROM_NO_TRADE × session_london}
  Primitive: {segmentation, comparison, expectancy}
      ↓
GOVERNANCE CHECK
  Does this require new infrastructure? → flag
  Is the population available? → check builder status
  Minimum sample met? → verify
      ↓
QUESTION REGISTRATION
  Assigned ID (human-originated: H-nnn prefix)
  Full NewEngineQuestion contract filled
      ↓
EXECUTION via standard QuestionRunner
      ↓
FINDING (same ResearchFinding schema)
      ↓
Standard governed pipeline (Feedback → Knowledge → Proposal → etc.)
```

### K.2 Classification Logic (Conceptual)

| Human asks... | Classification |
|---------------|---------------|
| "What is our actual win rate?" | LIVE_ONLY |
| "What would happen if we allowed more trades in session X?" | SHADOW_ONLY |
| "Are we losing money by being too conservative?" | CROSS_LIVE_SHADOW |
| "Which patterns work in trending markets?" | LIVE+SHADOW (testable on both) |
| "Is our broker giving us bad fills?" | LIVE_ONLY |

### K.3 Required Question Contract (all questions, human or generated)

```python
NewEngineQuestion(
    question_id=str,          # Human: "H-001", Shadow: "SE-001", Cross: "X-001"
    title=str,
    research_intent=str,
    required_universes=tuple[Universe],    # Including SHADOW_* if applicable
    required_populations=tuple[Population],
    required_joins=tuple[JoinRequirement],
    angle_requirements=tuple[AngleRequirement],
    analysis_type=AnalysisType,
    minimum_sample_size=int,
    status=QuestionStatus,
    evidence_side=str,        # NEW: "LIVE" | "SHADOW" | "CROSS_LIVE_SHADOW"
    source=str,               # NEW: "SYSTEM" | "HUMAN" | "GENERATED"
    provenance=str,           # NEW: what originated this question
)
```

### K.4 Question Evolution Sources

| Source | Example | Process |
|--------|---------|---------|
| Findings | "E-001 finding shows negative expectancy" | System proposes diagnostic follow-ups |
| Feedback | "Feedback on D-004 identifies structural flaw" | Generates reformulation proposal |
| Knowledge | "Knowledge state contradicts prior assumption" | New investigative question |
| Proposals | "Proposal needs more evidence" | GATHER_MORE_DATA question |
| Experiment Results | "Experiment invalidated hypothesis" | New hypothesis question |
| Human | "I notice X happening" | Human intake process |
| Cross-Side Discrepancies | "Live and Shadow disagree on regime effect" | Automatic discrepancy question |

---

## L. PROPOSAL / CANDIDATE IMPLICATIONS

### L.1 Current Pipeline (unchanged)

```
FINDING → FEEDBACK → KNOWLEDGE → PROPOSAL → CANDIDATE → EXPERIMENT → VALIDATION → PROMOTION
```

### L.2 How Shadow Evidence Enters

```
SHADOW QUESTION EXECUTED
      ↓
SHADOW FINDING (labelled evidence_source=COUNTERFACTUAL)
      ↓
FEEDBACK (normal feedback loop — but records evidence source)
      ↓
KNOWLEDGE (updates knowledge state with counterfactual evidence weighting)
      ↓
PROPOSAL (proposal generated — can include "test on shadow population")
      ↓
CANDIDATE (POPULATION_FILTER applied to Shadow population)
      ↓
EXPERIMENT (executed against ShadowOutcome population)
      ↓
VALIDATION (statistical significance on counterfactual improvement)
      ↓
PROMOTION_ELIGIBLE (but labelled as COUNTERFACTUAL_EVIDENCE)
      ↓
HUMAN GOVERNANCE (decides whether counterfactual evidence warrants live change)
```

### L.3 Candidate Types

| Type | Population Target | Example |
|------|-------------------|---------|
| Live Candidate | Execution/Outcome Universe | "Exclude TRANSITIONAL from executed trades" — tests on 94 live trades |
| Shadow Candidate | ShadowOutcome Universe | "Exclude TRANSITIONAL from all shadow signals" — tests on thousands |
| Cross Candidate | Both populations | "Exclude TRANSITIONAL: measure impact on BOTH live R and shadow R" |

### L.4 What Already Works

The existing `CandidateExperiment` uses `POPULATION_FILTER` which is a declarative configuration:
```python
{"field": "regime", "operator": "!=", "value": "TRANSITIONAL"}
```

This filter is population-agnostic — it works on any list of dicts with a `regime` field. Therefore:
- It already works on Live populations (proven: prop_EM-001 experiment)
- It will work on Shadow populations without modification (shadow records have strategy_id, pattern, and can be joined to get regime)
- No candidate infrastructure changes required

### L.5 What Needs Labelling

The `CandidateExperiment` result and `ValidationResult` must record:
```
experiment_population_source: "LIVE" | "SHADOW" | "CROSS"
```

This enables the Promotion gate to weight evidence appropriately:
- Live experiment validation → stronger promotion confidence
- Shadow experiment validation → indicates potential but needs live corroboration
- Cross experiment → strongest signal (both sides agree)

### L.6 Governance Chain (Preserved)

```
SHADOW RESULT ≠ DEPLOYMENT DECISION

Shadow finding says: "Hypothesis deserves investigation"
Shadow experiment says: "Counterfactual improvement is statistically significant"
Promotion gate says: "Human must review before any live system change"
Human decides: "Is counterfactual evidence sufficient, or do we need live corroboration?"
```

---

## M. DATA INTEGRITY RISKS

### M.1 Join Risks

| Risk | Likelihood | Impact | Detection | Mitigation |
|------|-----------|--------|-----------|------------|
| Empty entity_id in shadow records | LOW | Record unjoinable | Count at build time | Exclude (existing pattern) |
| entity_id format change over time | NEGLIGIBLE | Historical data unjoinable | Schema version check | Format is stable ({symbol}_{bar_time}) |
| Many-to-many join (multiple shadow per entity) | BY DESIGN | Inflated sample if not handled | Document 1:N cardinality | Question contracts declare handling |
| Shadow exists without corresponding Decision | NEGLIGIBLE | Orphan shadow records | Cross-reference at build time | Exclude orphans |

### M.2 Shadow Data Quality Risks

| Risk | Likelihood | Impact | Detection | Mitigation |
|------|-----------|--------|-----------|------------|
| Horizon SL/TP geometry differs from V10 engine | HIGH | Shadow R doesn't match what V10 would produce | Compare Primary shadow (V10 geometry) vs Horizon shadow | Document difference; use Primary for leakage analysis |
| Spread not modelled | HIGH | Overstates R by spread amount | Known at design time | Label limitation; spread_at_entry captured for future correction |
| 60-bar timeout biases outcomes | MEDIUM | Trades that would run longer forced to close | Timeout exit distribution analysis | Document; future: per-horizon max_bars |
| Intra-bar SL/TP ordering unknown | MEDIUM | Both may trigger same bar — SL checked first | Bias toward SL hits | Document; conservative (protects against over-optimism) |
| Shadow never experiences slippage | HIGH | No fill cost modelled | Design choice | Label all shadow R as "gross counterfactual" |

### M.3 Analytical Bias Risks

| Risk | Type | Mitigation |
|------|------|-----------|
| Survivorship bias | Shadow only creates records for detected patterns | Document population scope — "all PATTERN-DETECTED opportunities, not all market bars" |
| Look-ahead bias | Shadow uses future bars for exit | NO — shadow advances bar-by-bar using closed bars only (same info as live) |
| Selection bias | Not all decisions get shadows (only pattern-detected + horizon-eligible) | Document coverage rate; never claim shadow represents "all possible trades" |
| Sample inflation | Multiple horizons per entity count as separate observations | Question contracts must declare handling; avoid claiming independence when correlated |

### M.4 Semantic Confusion Risks

| Risk | Prevention |
|------|-----------|
| Reporting shadow R as realised | Evidence labelling: `evidence_source: COUNTERFACTUAL` mandatory |
| Combining Live R and Shadow R in same metric | Never combine without explicit cross-side primitive |
| Treating shadow as prediction of live | Shadow is a MODEL, not a prediction. Document limitations. |
| Over-confidence from large shadow sample | Evidence quality system must account for counterfactual nature |

---

## N. IMPLEMENTATION ROADMAP

### Phase 1 — Contracts & Models (Foundation)

**Files:**
- `research_engine/v10/universes/models.py` — extend Universe enum with SHADOW_OUTCOME, SHADOW_EXECUTION, SHADOW_RISK, SHADOW_DECISION, SHADOW_MARKET, SHADOW_STRATEGY; add Shadow Population values
- `research_engine/v10/universes/contracts.py` — add Shadow universe contracts, population contracts, join contracts

**Dependencies:** None  
**Risk:** LOW (additive only)  
**Tests:** Enum membership, contract completeness, no regression on existing  
**Duration:** 1 session

### Phase 2 — Shadow Universe Builders

**Files (all NEW):**
- `research_engine/v10/universes/shadow_outcome_universe.py`
- `research_engine/v10/universes/shadow_execution_universe.py`
- `research_engine/v10/universes/shadow_risk_universe.py`

**Dependencies:** Phase 1  
**Risk:** LOW (new files only)  
**Tests:** Builder load/build tests, population filter tests, entity_id join validation, schema validation  
**Data:** `logs/shadow_trades/` (already exists in production)  
**Duration:** 1-2 sessions

### Phase 3 — Cross-Universe Extension

**Files:**
- `research_engine/v10/cross_universe/tracer.py` — extend to index Shadow universes
- `research_engine/v10/universes/base.py` — no changes needed (builders already conform)
- NEW: derived population helpers for SHADOW_DECISION/MARKET/STRATEGY views

**Dependencies:** Phase 2  
**Risk:** MEDIUM (extends existing infrastructure)  
**Tests:** Join rate verification against real data, 1:N cardinality handling  
**Duration:** 1 session

### Phase 4 — Evidence Labelling

**Files:**
- `research_engine/v10/control_plane/finding_schema.py` — add `evidence_source` field
- `research_engine/v10/runner/question_runner.py` — propagate evidence source from universe to finding

**Dependencies:** Phase 1 (universe knows its side)  
**Risk:** LOW (additive field)  
**Tests:** Existing findings unaffected, new findings carry label  
**Duration:** 0.5 sessions

### Phase 5 — Shadow Questions + D-004 Redesign

**Files:**
- `research_engine/v10/universes/question_bank.py` — add shadow pairs (SE-*, SD-*, etc.), add D-004a/SD-004/X-002
- `research_engine/v10/runner/primitive_mapping.py` — add parameter mappings for new questions

**Dependencies:** Phase 2 + Phase 4  
**Risk:** MEDIUM (must not break existing 45 questions)  
**Tests:** Full regression on all 45 questions, new question execution tests  
**Duration:** 2 sessions

### Phase 6 — Cross-Side Primitive

**Files (NEW):**
- `research_engine/v10/runner/primitives/cross_side.py` — CrossSideComparisonPrimitive

**Dependencies:** Phase 3 (need joined populations)  
**Risk:** LOW (new primitive, isolated)  
**Tests:** Paired comparison correctness, edge cases (unmatched entities)  
**Duration:** 0.5 sessions

### Phase 7 — Cross-Side Questions + Validation

**Files:**
- `research_engine/v10/universes/question_bank.py` — add X-001 through X-005
- Research CLI updates for shadow/cross commands

**Dependencies:** Phase 5 + Phase 6  
**Risk:** LOW  
**Tests:** End-to-end: shadow question → finding → (manually verify)  
**Duration:** 1 session

### Deliberately Postponed

- Human question intake interface (future — after dual architecture is operational)
- Question auto-generation from cross-side discrepancies (future)
- Shadow-specific confidence weighting in ranking (after initial shadow findings validate the model)
- Candidate experiments on shadow populations (after Phase 7 validated)
- Promotion gate shadow-awareness (after experiments demonstrate value)

---

## FINAL DECISION GATE

### The Question

> **Can the V10 Research Engine support Live and Shadow as two structurally equivalent but analytically different research worlds without compromising the existing universe contracts or research governance?**

### The Answer: **YES — with specific, bounded infrastructure additions.**

---

### 1. What already exists?

| Component | Status |
|-----------|--------|
| Shadow data in production (`logs/shadow_trades/`) | EXISTS — active, daily accumulation |
| Deterministic entity_id join key | EXISTS — matches all Live universes |
| Generic QuestionRunner (universe-agnostic) | EXISTS — will process Shadow populations without modification |
| 12 analysis primitives (population-agnostic) | EXISTS — 10/12 work directly on Shadow data |
| Cross-universe tracer infrastructure | EXISTS — extendable to index Shadow |
| Governed pipeline (Finding → Proposal → Candidate → Experiment → Validation → Promotion) | EXISTS — requires only evidence labelling addition |
| Universe builder pattern (ABC with load/build/get_population) | EXISTS — Shadow builders follow same contract |

### 2. What is missing?

| Component | Effort | Priority |
|-----------|--------|----------|
| Shadow Universe enum values in `models.py` | Small | P0 |
| Shadow Population enum values | Small | P0 |
| Shadow universe contracts in `contracts.py` | Small | P0 |
| `ShadowOutcomeUniverseBuilder` | Medium | P0 |
| `ShadowExecutionUniverseBuilder` | Small | P1 |
| `ShadowRiskUniverseBuilder` | Small | P1 |
| `evidence_source` field in ResearchFinding | Small | P1 |
| Cross-side comparison primitive | Medium | P2 |
| Shadow question definitions (26 pairs + 5 cross) | Medium | P2 |
| D-004 redesign (3 sub-questions) | Small | P1 |

### 3. What must change?

| What | Why | Impact |
|------|-----|--------|
| `Universe` enum | Add SHADOW_* values | Additive — no existing code breaks |
| `Population` enum | Add shadow populations | Additive |
| `ResearchFinding` | Add `evidence_source` field | Additive (optional field with default) |
| `CrossUniverseTracer` | Index shadow universes | Extension of existing logic |
| Question bank | Add new questions + reformulate D-004 | Additive (old questions preserved) |

**Nothing is replaced. Everything is extended.**

### 4. What should NOT change?

| Component | Reason |
|-----------|--------|
| Trading runtime (`core/`) | Architecture boundary — research never modifies live |
| Shadow trade engine (`core/shadow_trades.py`) | Data source — research READS, never WRITES |
| Existing 6 Live universe builders | Correct and operational |
| Existing 45 question definitions | Preserved with original IDs |
| Existing 12 primitives | Work unchanged on both sides |
| Existing findings/proposals/knowledge | Historical artifacts — immutable |
| Governance chain | Strengthened by evidence labelling, not weakened |

### 5. What should be implemented first?

1. **Phase 1: Contracts/Enums** — foundation with zero risk
2. **Phase 2: ShadowOutcomeUniverseBuilder** — the core value (unlocks shadow questions)
3. **Phase 4: Evidence labelling** — prevents semantic confusion from day one
4. **Phase 5: D-004 redesign** — demonstrates the architecture working on the canonical broken question

### 6. What should be deliberately postponed?

| Component | Reason to Postpone |
|-----------|-------------------|
| Human question intake UI | Architecture must be proven first |
| Automatic question generation from discrepancies | Requires operational cross-side analysis |
| Shadow-weighted ranking adjustments | Need shadow findings to calibrate weighting |
| Candidate experiments on shadow populations | After shadow questions produce validated findings |
| Promotion gate shadow-awareness | After experiments demonstrate trustworthiness |

### 7. What architectural risks must be resolved before implementation?

| Risk | Resolution Required |
|------|-------------------|
| 1:N cardinality (multiple horizons per entity) | Define in contracts: per-horizon populations are the primary analytical unit; questions declare which horizon(s) they consume |
| Shadow R ≠ Live R semantics | Evidence labelling system must be operational BEFORE any shadow questions run |
| Shadow coverage rate unknown precisely | Phase 2 builder must report actual match rate; if < 30% entity coverage, some questions may be BLOCKED |
| Shadow geometry differs from V10 intent | Document explicitly in Shadow contracts; use Primary shadows (not horizon) for Live-vs-Shadow comparison |

---

### Final Architectural Diagram

```
                    MARKET REALITY (real M5 bars)
                              |
              +---------------+---------------+
              |                               |
         LIVE SYSTEM                   SHADOW SYSTEM
      (broker execution)            (simulated lifecycle)
              |                               |
     +--------+--------+            +--------+--------+
     |        |        |            |        |        |
  DECISION  MARKET  STRATEGY     (same observations —
     |        |        |          filtered to shadow-
     |        |        |          available entities)
     +--------+--------+                    |
     |        |        |            +-------+-------+
   RISK   EXECUTION OUTCOME      S-RISK  S-EXEC  S-OUTCOME
   (live)   (94)     (94)       (shadow) (shadow) (thousands)
              |                               |
              +---------------+---------------+
                              |
                       entity_id JOIN
                              |
              +---------------+---------------+
              |               |               |
         LIVE_ONLY      LIVE+SHADOW      CROSS_SIDE
         questions       questions        questions
         (11)            (24+26 pairs)    (5)
              |               |               |
              +---------------+---------------+
                              |
                    RESEARCH FINDING
                    (evidence_source labelled)
                              |
                    FEEDBACK → KNOWLEDGE
                              |
                    PROPOSAL → CANDIDATE
                              |
                    EXPERIMENT → VALIDATION
                              |
                    PROMOTION GATE
                              |
                    HUMAN GOVERNANCE
```

---

*End of specification. No code modified. No runtime affected. Implementation awaits human review.*
