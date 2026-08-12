# V10 RESEARCH ENGINE — DUAL LIVE/SHADOW ARCHITECTURE SPECIFICATION v3

**Date:** 2026-07-27  
**Type:** Specification Validation + Implementation Readiness Audit  
**Predecessors:** v1, v2 specifications  
**Status:** READ-ONLY — No code modified, no runtime affected  
**Data measurement:** Verified against actual persisted shadow and decision data  

---

## 1. Executive Summary

### Critical Finding

The dual Live/Shadow architecture is **structurally sound** but the v2 specification **overestimated shadow data coverage significantly**. Real measurements reveal:

- **5,780** total shadow records (not "thousands" as previously estimated)
- **36% have EMPTY entity_id** (not "<5%" as v2 claimed)
- **Only 14% of Decision entities have shadow coverage** (not "30-60%")
- **The join itself is reliable** — 99% of shadows WITH entity_id match a decision

This means: the architecture is correct, but initial shadow research will operate on **~1,731 joinable entities** (not thousands). Still analytically viable for many questions (minimum_sample_size is typically 10-50), but some multi-dimensional analyses may be sample-limited initially.

### Verdict

**IMPLEMENTATION_READY** — with the specific data coverage constraints documented and the implementation order adjusted to prioritise the entity_id gap fix.

---

## 2. Current Live Architecture (CONFIRMED)

No changes from v2. The existing Live research pipeline is confirmed operational:

```
LIVE DATA:  decision_trace (15,865 records, 11,743 unique entities)
            execution_results (430 EXECUTE decisions)
            research_universe.jsonl (94 completed trades)
     ↓
UNIVERSES:  Execution(94) → Decision(~15,865) → Market → Strategy → Risk → Outcome(94)
     ↓
POPULATIONS: 35+ named populations
     ↓
PRIMITIVES:  12 analytical primitives
     ↓
QUESTIONS:   45 canonical questions
     ↓
RUNNER → FINDING → FEEDBACK → KNOWLEDGE → PROPOSAL → CANDIDATE → EXPERIMENT → VALIDATION → PROMOTION
```

**Key numbers from real data:**
- 15,865 decision trace records
- 11,743 unique entity_ids in decision traces
- 430 EXECUTE decisions (2.7%)
- 15,435 NO_TRADE decisions (97.3%)
- 94 completed trades in execution universe

---

## 3. Runtime Shadow Architecture (VERIFIED FROM CODE)

### 3.1 Creation

**Trigger:** `core/runtime/live_scanner.py` ~line 730  
**Condition:** Pattern detected AND horizon classifier produces eligible horizons  
**Scope:** ALL decisions where pattern found (EXECUTE AND NO_TRADE)

### 3.2 Types

| Type | ID Format | Count in Data | Description |
|------|-----------|---------------|-------------|
| Horizon Shadow | `hshadow_{cycle}_{symbol}_{HORIZON}` | 3,198 | Per-horizon counterfactual |
| Primary Shadow | `shadow_{cycle}_{symbol}` | 949 | V10 engine geometry parallel |
| Other/Legacy | Various | 1,633 | Older format or edge cases |

### 3.3 Geometry (from `core/horizon/horizon_trade_builder.py`)

| Horizon | SL Source | SL Buffer | R:R | TP |
|---------|-----------|-----------|-----|-----|
| SCALP | M5 candle high/low | 0.0002 | 2:1 | entry ± risk × 2.0 |
| INTRADAY | M15 nearest support/resistance | 0.0003 | 3:1 | entry ± risk × 3.0 |
| EXTENDED | H1 last swing high/low | 0.0005 | 4:1 | entry ± risk × 4.0 |

### 3.4 Lifecycle (from `core/shadow_trades.py`)

- **Entry:** Market price at decision time (ask if BUY, bid if SELL)
- **Bar progression:** Called every M5 bar via `bar_provider.py`
- **Exit conditions:** SL hit → TP hit → timeout (60 bars). SL checked BEFORE TP on same bar.
- **R calculation:** `core/trade_truth.py` → `compute_r_multiple(direction, entry, exit, stop_loss)`
- **MFE/MAE:** Tracked per-bar from bar high/low

### 3.5 Persistence

- **Local:** `logs/shadow_trades/{SYMBOL}/{DATE}.jsonl`
- **S3:** `s3://v10-engine/shadow_trades/`
- **Schema:** `shadow_trades_v2` (4 domains: identity, decision_snapshot, simulation_environment, simulated_outcome)

---

## 4. Research Shadow Architecture (SEPARATE SYSTEM)

**Location:** `research_engine/v10/shadow/`

| Property | Runtime Shadow | Research Shadow |
|----------|---------------|-----------------|
| Purpose | Counterfactual lifecycle simulation | Candidate parameter what-if testing |
| Input | Live market bars (future) | Completed historical trades |
| Output | Independent R from full lifecycle | Modified R from parameter changes |
| When runs | Real-time, per bar | Offline, on demand |
| Persistence | logs/shadow_trades/ | In-memory / reports |
| Data source for dual-universe | **YES** | **NO** |

**Conclusion:** The dual-universe architecture consumes ONLY runtime shadow data. The research shadow module (`ShadowRunner`, `ShadowCandidate`, `ShadowComparison`) is a separate candidate-testing mechanism and remains independent.

---

## 5. ACTUAL SHADOW DATA MEASUREMENTS

**Measurement methodology:** `scripts/measure_shadow_data.py` — streaming JSONL parser, full dataset scan.

### 5.1 Shadow Data Summary

| Metric | Value | Implication |
|--------|-------|-------------|
| Total records | 5,780 | Moderate dataset — adequate for many questions |
| Files processed | 112 | Across 10 symbols, multiple dates |
| Valid entity_id | 3,655 (63%) | **36% have NO entity_id — significant gap** |
| Valid R-multiple | 5,780 (100%) | Every record has an outcome — excellent |
| Unique entity_ids | 1,735 | Distinct opportunities with counterfactual data |
| Entities with >1 shadow | 1,449 (84% of entities) | Most have multiple horizons — 1:N is the norm |
| Max shadows per entity | 158 | Extreme outlier — likely repeated cycles |
| Avg shadows per entity | 3.33 | Typical: 1 primary + 1-2 horizon shadows |

### 5.2 Shadow Types

| Type | Count | % |
|------|-------|---|
| Horizon (hshadow_*) | 3,198 | 55% |
| Primary (shadow_*) | 949 | 16% |
| Other/legacy | 1,633 | 28% |

### 5.3 Horizon Distribution

| Horizon | Count | % of Horizon Shadows |
|---------|-------|---------------------|
| SCALP | 1,821 | 57% |
| INTRADAY | 1,359 | 42% |
| EXTENDED | 18 | <1% |

**Note:** EXTENDED is nearly absent — likely because H1 swing data is rarely available when needed.

### 5.4 Exit Distribution

| Exit Reason | Count | % |
|-------------|-------|---|
| max_bars_timeout | 3,053 | 53% |
| stop_loss | 1,998 | 35% |
| take_profit | 728 | 13% |
| tp (legacy) | 1 | <1% |

**Critical observation:** 53% of shadows timeout. This means the 60-bar cap is the dominant exit — R values are heavily influenced by this artificial constraint.

### 5.5 Join Coverage

| Metric | Value |
|--------|-------|
| Decision records | 15,865 |
| Decision unique entities | 11,743 |
| Shadow unique entities | 1,735 |
| **Joined (both)** | **1,731** |
| Shadow orphans (no decision) | 4 |
| Decisions without shadow | 10,012 |
| **Join rate (shadow→decision)** | **99%** |
| **Coverage (decision→shadow)** | **14%** |

### 5.6 Implications for Research

| Analysis | Feasible? | Reason |
|----------|-----------|--------|
| Overall shadow expectancy | YES | 5,780 records with R (all valid) |
| Per-horizon expectancy | YES for SCALP (1,821) and INTRADAY (1,359) | EXTENDED too small (18) |
| Shadow × regime segmentation | MAYBE | Requires regime from joined decision — 1,731 joinable entities |
| Shadow × terminal_stage | YES | 1,731 joined → segment by stage |
| Per-symbol shadow expectancy | YES | 167-1,307 per symbol |
| Multi-dimensional (regime × strategy × horizon) | RISKY | Sample may fragment below minimum |
| Live vs Shadow paired comparison | LIMITED | Only EXECUTE entities with shadow — needs measurement |

---

## 6. Correct Universe Model

### 6.1 Challenge to v2's "ONE physical universe" recommendation

v2 proposed: ONE physical SHADOW_OUTCOME universe.

After data measurement, this recommendation **STANDS but with caveats:**

**Why ONE is correct:**
- All shadow data comes from a single source (`logs/shadow_trades/`)
- Entry geometry, risk params, and outcome are all FIELDS within the same record
- Splitting into multiple universes would create 3 builders reading the same files
- The data is not large enough to justify splitting for performance

**The caveat:**
- 36% of records have NO entity_id — these cannot participate in cross-universe joins
- The builder MUST create TWO populations: `ALL_SHADOW_OUTCOMES` (5,780) and `JOINABLE_SHADOW_OUTCOMES` (3,655)
- Questions requiring Decision/Market/Strategy joins operate on the JOINABLE subset only

### 6.2 Final Universe Architecture

```
LIVE WORLD (existing, unchanged):
  EXECUTION (94 records)
  DECISION (15,865 records)
  MARKET (derived from decision_trace)
  STRATEGY (derived from decision_trace + strategy_observations)
  RISK (subset of decision_trace)
  OUTCOME (wraps Execution)

SHADOW WORLD (new):
  SHADOW_OUTCOME (5,780 records — one physical builder)

CROSS-SIDE:
  Governed joins via entity_id between SHADOW_OUTCOME and Live universes
```

### 6.3 Why NOT six shadow universes

| Proposed Shadow | Verdict | Reason |
|-----------------|---------|--------|
| SHADOW_OUTCOME | **BUILD** | Independent data, independent lifecycle, independent R-multiple. Genuinely new evidence. |
| SHADOW_EXECUTION | REJECT | Entry geometry is a FIELD within shadow outcome records. Not an independent observation. |
| SHADOW_DECISION | REJECT | The decision is the SAME Live decision. Shadow adds the OUTCOME, not a different decision. |
| SHADOW_MARKET | REJECT | Market state is identical (same bar, same entity). Join to Market universe provides this. |
| SHADOW_STRATEGY | REJECT | Strategy evaluation is the same Live observation. Shadow reveals what the strategy WOULD have produced, not a different strategy evaluation. |
| SHADOW_RISK | REJECT | Risk parameters are FIELDS within shadow outcome (risk_config_snapshot). Not independent. |

**Principle:** A universe exists when it represents an independent observation domain with its own authoritative data source and distinct grain. Shadow outcome meets this test. The others do not.

---

## 7. Live/Shadow Universe Contract Matrix

| Live Universe | Shadow Equivalent | Physical? | Derived? | Valid? | Reason |
|---------------|-------------------|-----------|----------|--------|--------|
| EXECUTION | — | NO | — | N/A | Execution is broker-confirmed fills. Shadow has no broker. |
| DECISION | — | NO | Join filter | PARTIAL | Decisions are shared. Shadow adds outcome data via join, not a new decision. |
| MARKET | — | NO | Join filter | PARTIAL | Market is shared reality. Access via entity_id join. |
| STRATEGY | — | NO | Join filter | PARTIAL | Strategy evaluation is shared. Shadow reveals outcome, not new strategy. |
| RISK | — | NO | Field projection | PARTIAL | Risk params are within shadow records (risk_config_snapshot field). |
| OUTCOME | SHADOW_OUTCOME | **YES** | — | **YES** | Genuinely different: counterfactual R vs realised R. Independent lifecycle. |

### Symmetry Contract

**WHAT SHOULD BE SYMMETRICAL:**
- Universe governance (contracts, populations, versioning, provenance)
- Population governance (declared filters, minimum samples, exclusion criteria)
- Question governance (standardised NewEngineQuestion contract)
- Primitive governance (same primitives operate on both)
- Finding governance (same schema, labelled evidence_source)
- Experiment governance (same validation standards)
- Lineage (both sides produce reproducible, versioned findings)

**WHAT MUST NOT BE SYMMETRICAL:**
- Evidence semantics (realised ≠ counterfactual)
- Outcome meaning (Live R = broker-confirmed; Shadow R = model-simulated)
- Confidence weighting (Live evidence > Shadow evidence for promotion)
- Coverage (Live OUTCOME = 94; Shadow OUTCOME = 5,780)
- Completeness (Live covers ALL executed; Shadow covers only 14% of decisions)
- Position lifecycle (Live = managed by TradeStateManager; Shadow = SL/TP/timeout only)
- Execution quality (Live has slippage, spread, broker rejections; Shadow has none)

---

## 8. Population Architecture

### 8.1 Shadow Outcome Populations

| Population | Filter | Expected Size | Primary Use |
|------------|--------|---------------|-------------|
| ALL_SHADOW_OUTCOMES | r_multiple IS NOT NULL | ~5,780 | Total counterfactual pool |
| JOINABLE_SHADOWS | entity_id IS NOT EMPTY | ~3,655 | Cross-universe analysis |
| SHADOW_HORIZON_SCALP | trade_id contains "_SCALP" | ~1,821 | Per-horizon analysis |
| SHADOW_HORIZON_INTRADAY | trade_id contains "_INTRADAY" | ~1,359 | Per-horizon analysis |
| SHADOW_PRIMARY | trade_id starts with "shadow_" (not "hshadow_") | ~949 | V10-geometry counterfactual |
| SHADOW_WINS | r_multiple > 0 | ~13% of total (TP hits) | Opportunity identification |
| SHADOW_LOSSES | r_multiple <= 0 | ~87% of total | Correct rejection validation |
| SHADOW_TP_HIT | exit_reason = "take_profit" | ~728 | Target quality |
| SHADOW_SL_HIT | exit_reason = "stop_loss" | ~1,998 | Stop effectiveness |
| SHADOW_TIMEOUT | exit_reason = "max_bars_timeout" | ~3,053 | Duration analysis |

### 8.2 Cross-Side Populations (Governed Joins)

| Population | Construction | Expected Size |
|------------|-------------|---------------|
| DECISIONS_WITH_SHADOW | Decision WHERE entity_id IN JOINABLE_SHADOWS | ~1,731 |
| NO_TRADE_WITH_SHADOW | Above WHERE action = "NO_TRADE" | ~1,500-1,700 (est.) |
| EXECUTE_WITH_SHADOW | Above WHERE action = "EXECUTE" | ~30-50 (est.) |

### 8.3 Critical Population Constraint

**53% of shadow outcomes are TIMEOUTS.** This means:
- The "take profit" population is only ~728 records
- Win rate is artificially depressed by the 60-bar cap
- Questions about "positive shadow outcomes" operate on a small subset
- The timeout issue is a KNOWN LIMITATION, not a data quality problem

---

## 9. Join/Identity Model

### 9.1 Canonical Join Key: `entity_id`

Format: `{symbol}_{bar_time}` (e.g., `EURUSD_1786445100`)

| Property | Measured Status |
|----------|----------------|
| Present in 63% of shadow records | **GAP — 36% unjoinable** |
| 99% join rate when present | STRONG — the key itself works |
| 14% decision coverage | LOW — most decisions have no shadow |
| 1:N cardinality (avg 3.33 shadows per entity) | BY DESIGN — multiple horizons |
| Max 158 per entity | OUTLIER — needs investigation |

### 9.2 entity_id Sufficiency

entity_id **IS sufficient** as the join key, but:
- Must always be used WITH a population filter (e.g., specific horizon) to avoid 1:N confusion
- Questions must declare their horizon handling strategy
- The 36% empty-entity-id records are EXCLUDED from cross-universe analysis

### 9.3 The Empty entity_id Problem

**Root cause (from code):** Horizon shadows inherit entity_id from `_new_result.get("entity_id", "")`. When V10 pipeline errors occur or when the bar_time is unavailable, entity_id is empty.

**Impact:** 2,125 shadow records (36%) cannot be joined to decisions. They CAN still participate in non-joined analyses (overall shadow expectancy, exit distribution, etc.) but NOT in "what did the decision pipeline reject?" analyses.

**Implication for implementation:** The ShadowOutcomeUniverseBuilder must clearly distinguish:
- `ALL_SHADOW_OUTCOMES` — includes empty entity_id
- `JOINABLE_SHADOW_OUTCOMES` — entity_id required (for cross-universe)

---

## 10. Counterfactual Contract

### 10.1 Precise Definition

> A Shadow R-multiple represents the gross price-space return (in risk units) that a hypothetical trade would have produced IF:
> - Entered at market price at decision time
> - With horizon-specific stop-loss (M5/M15/H1 structure-based)
> - With take-profit at fixed R:R ratio (2.0/3.0/4.0 per horizon)
> - Evaluated bar-by-bar against real subsequent M5 closes
> - Exiting at first of: SL hit, TP hit, or 60-bar timeout
> - With no slippage, no commission, no spread deduction
> - With SL evaluated before TP when both could trigger on same bar

### 10.2 What Shadow R Is NOT

- NOT realised broker-confirmed P&L
- NOT guaranteed achievable execution
- NOT the same across horizons (different geometry = different measurement)
- NOT directly comparable to Live R (Live includes management, slippage, commission)
- NOT a prediction of what Live would have produced

### 10.3 Multiple Counterfactual Models

One entity may have shadows under different counterfactual contracts:
- SCALP (tight SL, 2:1 R:R)
- INTRADAY (medium SL, 3:1 R:R)
- EXTENDED (wide SL, 4:1 R:R)
- PRIMARY (V10 engine geometry — only for EXECUTE decisions)

**These are DIFFERENT measurements.** Research MUST NOT pool them without declaring this explicitly.

---

## 11. Primitive Compatibility

| Primitive | Live | Shadow | Cross-Side | Changes Required |
|-----------|------|--------|------------|------------------|
| expectancy | YES | YES | N/A | NONE — operates on r_multiple field identically |
| distribution | YES | YES | N/A | NONE |
| comparison | YES | YES | YES (via group_field) | NONE — can group by "side" if population constructed with label |
| conditional_expectancy | YES | YES | N/A | NONE |
| calibration | YES | WEAK | N/A | Not semantically meaningful on shadow (calibrates model against itself) |
| predictive_power | YES | YES | N/A | NONE |
| segmentation | YES | YES | N/A | NONE |
| transition | YES | YES | N/A | NONE (if timestamp present) |
| execution_quality | YES | PARTIAL | N/A | Shadow has `bars_held` not `duration_seconds`; `exit_reason` works |
| degradation | YES | YES | N/A | NONE |
| anomaly_analysis | YES | NO | N/A | Shadow has no anomaly concept — NOT_APPLICABLE |
| exceptional_analysis | YES | PARTIAL | N/A | Needs shadow-specific exceptional criteria |
| **cross_side_comparison** | N/A | N/A | **NEW** | Paired entity Live R vs Shadow R |

### New Primitive Requirement

`cross_side_comparison` — needed ONLY for paired per-entity comparison (same entity, both outcomes). The existing `comparison` primitive compares GROUPS, not individual entity pairs.

**Estimated scope:** ~30-50 entities (EXECUTE decisions with matching shadow). Very small paired sample initially. This primitive may be deferred until more EXECUTE decisions accumulate with shadow matches.

---

## 12. Complete Question Classification (REVISED with Real Data Constraints)

Given that only 1,731 entities are joinable and only ~14% of decisions have shadows, some v2 classifications change:

| # | ID | Title | Classification | Reason | Shadow Sample Available |
|---|-----|-------|---------------|--------|------------------------|
| 1 | E-001 | System Expectancy | LIVE_ONLY | Realised system performance | N/A |
| 2 | E-002 | Win/Loss Distribution | LIVE_ONLY | Realised distribution | N/A |
| 3 | E-003 | Exit Reason Distribution | LIVE+SHADOW | Shadow exits meaningfully different (53% timeout vs Live managed exits) | ~5,780 |
| 4 | E-004 | Execution Quality by Session | LIVE_ONLY | Broker-only | N/A |
| 5 | E-005 | Probability of Ruin | LIVE_ONLY | Realised variance | N/A |
| 6 | E-006 | Out-of-Sample Validation | LIVE_ONLY | Realised holdout | N/A |
| 7 | E-007 | Stop Placement | LIVE+SHADOW | Shadow tests SL sensitivity on larger sample | ~5,780 |
| 8 | E-008 | Pattern Degradation | LIVE+SHADOW | Shadow detects degradation with larger sample | ~3,655 (needs entity_id for time ordering by decision) |
| 9 | E-009 | Duration vs Outcome | LIVE_ONLY | Duration is managed-trade concept | N/A |
| 10 | E-010 | R:R Effectiveness | LIVE+SHADOW | Horizons provide natural R:R experiment | ~5,780 |
| 11 | D-001 | Score Predictive Power | LIVE+SHADOW | Score → shadow R tests on full signal set | ~1,731 (joinable) |
| 12 | D-002 | EV Calibration | LIVE_ONLY | Requires realised truth | N/A |
| 13 | D-003 | Threshold Effectiveness | CROSS | Both sides of threshold needed | ~1,731 |
| 14 | D-004 | Rejection Stage Analysis | SPLIT | See Section 13 | ~1,500-1,700 |
| 15 | D-005 | Opportunity Quality | LIVE+SHADOW | Quality → shadow R | ~1,731 |
| 16 | D-006 | Opportunity Failure | LIVE_ONLY | Actual failures only | N/A |
| 17 | D-007 | Risk Gate Value | CROSS | Blocked→shadow vs approved→live | ~1,731 |
| 18 | M-001 | Regime Predicts Outcomes | LIVE+SHADOW | Regime → shadow R on full pool | ~1,731 |
| 19 | M-002 | HTF Alignment | LIVE+SHADOW | Alignment → shadow R | ~1,731 |
| 20 | M-003 | Volatility Impact | LIVE+SHADOW | Volatility → shadow R | ~1,731 |
| 21 | M-004 | Structure Clarity | LIVE_ONLY | Prediction requires realised truth | N/A |
| 22 | M-005 | Location Quality | LIVE+SHADOW | Location → shadow R | ~1,731 |
| 23 | M-006 | Session Edge | LIVE+SHADOW | Session → shadow R | ~5,780 (derivable from timestamp) |
| 24 | S-001 | Strategy Family Expectancy | LIVE+SHADOW | Strategy → shadow R | ~3,655 (from strategy_id in shadow) |
| 25 | S-002 | Pattern Expectancy | LIVE+SHADOW | Pattern → shadow R | ~3,655 (from pattern in shadow) |
| 26 | S-003 | Strategy Selection Accuracy | LIVE_ONLY | Requires realised truth | N/A |
| 27 | S-004 | Strategy Rejection Patterns | SHADOW_ONLY | Requires counterfactual of rejected | ~1,500 (NO_TRADE with shadow) |
| 28 | ED-001 | Edge Leakage | CROSS | Paired Live R vs Shadow R | ~30-50 (VERY small) |
| 29 | ED-002 | Missed Opportunity Cost | SHADOW_ONLY | Counterfactual of rejected | ~1,500 |
| 30 | ED-003 | Position Sizing | LIVE_ONLY | Real account P&L | N/A |
| 31 | EM-001 | Regime-Conditioned Expectancy | LIVE+SHADOW | Shadow variant on full pool | ~1,731 |
| 32 | EM-002 | Market Drift | LIVE_ONLY | Realised temporal | N/A |
| 33 | ES-001 | Execution by Strategy | LIVE+SHADOW | Strategy → shadow R | ~3,655 |
| 34 | DM-001 | Decision Quality Under Regime | LIVE+SHADOW | Score × regime on shadow | ~1,731 |
| 35 | DM-002 | Opportunity vs Market | LIVE+SHADOW | Quality × market on shadow | ~1,731 |
| 36 | DM-003 | Rejection Rate by Market | SPLIT | Descriptive + counterfactual | ~1,731 |
| 37 | DS-001 | Strategy Confidence Calibration | LIVE+SHADOW | Confidence → shadow R | ~1,731 |
| 38 | DS-002 | Strategy Conditions vs Outcome | LIVE+SHADOW | Conditions → shadow R | ~1,731 |
| 39 | MS-001 | Strategy × Regime | LIVE+SHADOW | Interaction on shadow | ~1,731 |
| 40 | MS-002 | Pattern × Market | LIVE+SHADOW | Pattern × context on shadow | ~1,731 |
| 41 | MS-003 | Strategy Availability | LIVE_ONLY | Structural/descriptive | N/A |
| 42 | EDM-001 | Complete Lifecycle | LIVE_ONLY | Full broker lifecycle | N/A |
| 43 | DMS-001 | Decision × Strategy × Market | LIVE+SHADOW | Multi-dimensional (BUT sample may be too small for 3-way segmentation on 1,731) | ~1,731 |
| 44 | EDMS-001 | Full System Attribution | LIVE_ONLY | Attribution requires realised | N/A |
| 45 | EDMS-002 | Promotion Impact | CROSS | Both-side convergence | ~1,731 |

### Summary

| Classification | Count |
|----------------|-------|
| LIVE_ONLY | 14 |
| LIVE+SHADOW | 19 |
| SHADOW_ONLY | 2 |
| CROSS | 4 |
| SPLIT | 2 |
| **TOTAL** | **45** (with 2 splits → net +3 sub-questions) |

**Change from v2:** Reduced LIVE+SHADOW from 21 to 19. DMS-001 flagged as potentially sample-limited. ED-001 (leakage) noted as having extremely small paired sample (~30-50).

---

## 13. D-004 Deep Analysis

### 13.1 Current Problem Path

```
D-004 question: "Which rejection stage removes edge vs protects?"
  ↓
Population: NO_TRADE_DECISIONS (15,435 records)
  ↓
Primitive: segmentation(dimensions=["terminal_reason"], metric="r_multiple")
  ↓
Metric requirement: r_multiple on NO_TRADE records
  ↓
Actual metric-bearing records: ~1 (from execution join artifact)
  ↓
Evidence quality: INSUFFICIENT
  ↓
Ranking: #29 (evidence quality penalty applied)
  ↓
Action: GATHER_MORE_DATA
```

### 13.2 Why It Fails

The question asks "which stage removes EDGE" — this requires knowing the OUTCOME of rejected opportunities. NO_TRADE decisions have no Live outcome by definition. The only way to answer "what would have happened?" is counterfactual (Shadow) evidence.

### 13.3 Correct Dual-World Formulation

**D-004 (narrowed to descriptive — KEEP):**
```
Intent: "Where in the pipeline are opportunities rejected?"
Side: LIVE_ONLY (descriptive)
Population: NO_TRADE_DECISIONS
Metric: COUNT per terminal_stage (NO r_multiple needed)
Analysis: segmentation by terminal_stage
Sample: 15,435
```

**SD-004 (new — counterfactual value):**
```
Intent: "What counterfactual R did rejected opportunities produce, by rejection stage?"
Side: SHADOW_ONLY
Population: JOINABLE_SHADOWS WHERE Decision.action = "NO_TRADE"
Metric: shadow r_multiple segmented by Decision.terminal_stage (via entity_id join)
Analysis: segmentation
Sample: ~1,500-1,700 (estimated NO_TRADE entities with shadow)
```

**X-002 (new — cross-side decision quality):**
```
Intent: "Which stages correctly reject losing opportunities vs incorrectly reject profitable ones?"
Side: CROSS_LIVE_SHADOW
Population: NO_TRADE_WITH_SHADOW
Metric: per stage {shadow_R<0: "correct protection", shadow_R>0: "missed opportunity"}
Sample: ~1,500-1,700
```

### 13.4 Expected Impact

SD-004 transforms D-004 from INSUFFICIENT (1 metric observation) to STRONG (~1,500+ counterfactual R observations). This is the single most impactful use of the dual-universe architecture.

---

## 14. Candidate/Experiment Integration

### 14.1 Current Pipeline (Unchanged)

```
Finding → Feedback → Knowledge → Proposal → Ranking → Candidate → Experiment → Validation → Promotion
```

### 14.2 Where Shadow Evidence Enters

Shadow findings enter at the FINDING stage with `evidence_source: COUNTERFACTUAL`. They flow through the standard pipeline with:
- Feedback records noting counterfactual evidence source
- Knowledge weighting counterfactual evidence lower than realised
- Proposals marked with evidence type
- Ranking applying counterfactual discount

### 14.3 Candidate Sources

| Source | Example | Pipeline Entry |
|--------|---------|---------------|
| Live finding | "TRANSITIONAL regime has negative realised R" | Standard (existing) |
| Shadow finding | "Strategy-rejected opportunities have positive counterfactual R" | Standard + evidence label |
| Cross-side finding | "Risk gate rejects 40% profitable opportunities" | Standard + cross-side evidence |

### 14.4 Research Shadow Optimiser Relationship

The `research_engine/v10/shadow/` module (`ShadowRunner`) is the candidate-TESTING mechanism. It applies candidate parameter changes to historical data and measures the what-if outcome. It:
- Remains separate from the dual-universe architecture
- Operates on completed trades (not the runtime shadow layer)
- Is activated AFTER a candidate is designed
- Uses its own comparison model (baseline vs modified)

**Relationship to dual-universe:**
- Dual-universe SHADOW_OUTCOME provides FINDINGS (what opportunities look like counterfactually)
- `ShadowRunner` TESTS CANDIDATES (what would happen with different parameters)
- They serve different stages of the pipeline and should remain separate

### 14.5 Governance Preserved

```
Shadow finding: "Hypothesis deserves investigation"
   ≠
"Deploy this change"

Shadow experiment: "Counterfactual improvement is significant"
   ≠
"Live performance will improve"

Promotion: ALWAYS requires human governance
```

---

## 15. Human Research Interface

### 15.1 Observation → Question Path

```
HUMAN OBSERVATION
  "I think the bot rejects too many RANGING opportunities"
      ↓
CLASSIFICATION
  Needs: counterfactual outcome of rejected signals in RANGING
  Side: SHADOW (counterfactual question) or CROSS (if comparing to approved)
      ↓
POPULATION CHECK
  JOINABLE_SHADOWS WHERE regime="RANGING" AND action="NO_TRADE"
  Available? Check size.
      ↓
PRIMITIVE SELECTION
  segmentation(dimensions=["terminal_stage"], metric="r_multiple")
  Available: YES
      ↓
QUESTION FORMALISATION
  NewEngineQuestion contract filled (ID, intent, universes, populations, primitives)
      ↓
EXECUTION
  Standard QuestionRunner
      ↓
FINDING
  evidence_source: COUNTERFACTUAL
```

### 15.2 Key Design Requirement

The human does NOT need to understand:
- Which universe contains the data
- How entity_id joins work
- The difference between horizon shadows
- Population construction details

The human only needs to express:
- What they want to know
- Whether it's about "what happened" or "what would have happened"

The system routes to the correct evidence world.

---

## 16. Governance Model

| Rule | Enforcement |
|------|------------|
| Shadow cannot modify live trading | Architecture boundary (research_engine/ reads, never writes to core/) |
| Shadow cannot activate candidates | Promotion gate requires human |
| Research cannot alter runtime behaviour | No import path from research → core execution |
| Candidate experiments remain counterfactual | POPULATION_FILTER on historical/shadow data |
| Evidence provenance preserved | `evidence_source` field in ResearchFinding |
| Cross-side conclusions retain both provenances | Finding records both universes_used |
| Promotion remains human-governed | ProposalPromotion requires governance review |

---

## 17. Risks and Failure Modes

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **36% empty entity_id** | CONFIRMED (measured) | 2,125 records unjoinable | Population contract: JOINABLE vs ALL distinction. Future: fix entity_id propagation in runtime. |
| **53% timeout exits** | CONFIRMED | Win rate appears ~13% (artificially low) | Document limitation. Consider per-horizon max_bars in future. |
| **14% decision coverage** | CONFIRMED | Most NO_TRADE decisions have no shadow | Shadow only exists for pattern-detected opportunities. Clearly scope all claims. |
| **EXTENDED horizon near-zero** | CONFIRMED (18 records) | Cannot research EXTENDED horizon | Exclude from initial implementation. May indicate H1 data availability issue. |
| **Max 158 shadows per entity** | CONFIRMED | Outlier could skew averages | Investigate cause. Apply per-entity deduplication where needed. |
| **Shadow ≠ V10 geometry** | BY DESIGN | Shadow R doesn't match what V10 intended | Separate PRIMARY vs HORIZON populations. Use PRIMARY for leakage analysis. |
| **Very small paired sample for ED-001** | ESTIMATED (~30-50) | Cross-side leakage analysis statistically weak | Defer ED-001 cross-side until more EXECUTE+shadow accumulates |

---

## 18. What Must NOT Be Built

| Item | Reason |
|------|--------|
| Six physical shadow universes | Only SHADOW_OUTCOME has independent data ownership |
| Shadow primitives (copies of existing) | Existing primitives work unchanged on shadow populations |
| Automatic shadow→live promotion | Governance must remain human |
| Shadow-live R combination without labels | Evidence types must never be silently merged |
| EXTENDED horizon research | 18 records — insufficient for any meaningful analysis |
| Cross-side leakage primitive (initially) | ~30-50 paired records — too small. Defer. |
| NLP question generation | Not needed — manual formalisation works initially |

---

## 19. Implementation Dependencies

```
Phase 1: Foundation
  Depends on: nothing
  Produces: SHADOW_OUTCOME enum, Population enum values, contract

Phase 2: ShadowOutcomeUniverseBuilder
  Depends on: Phase 1
  Produces: Built shadow population, coverage report

Phase 3: D-004 proof-of-concept
  Depends on: Phase 2
  Produces: SD-004 finding demonstrating architecture works

Phase 4: Evidence labelling
  Depends on: Phase 1
  Produces: evidence_source field in findings

Phase 5: Shadow question batch
  Depends on: Phase 2 + Phase 4
  Produces: 19 shadow pairs operational

Phase 6: Cross-side questions
  Depends on: Phase 5
  Produces: D-003, D-007, EDMS-002 cross-side operational

Phase 7 (DEFERRED): Cross-side primitive
  Depends on: More EXECUTE+shadow data accumulating
  Produces: Paired comparison for ED-001
```

---

## 20. Phased Implementation Plan

| Phase | Scope | Risk | Sessions | Milestone |
|-------|-------|------|----------|-----------|
| 1 | Enums + contract + evidence_source field | ZERO | 1 | Foundation in place |
| 2 | ShadowOutcomeUniverseBuilder | LOW | 1-2 | Builder loads 5,780 records, distinguishes joinable vs all |
| 3 | SD-004 (D-004 shadow variant) | LOW | 1 | D-004 problem solved — 1,500+ metric observations vs 1 |
| 4 | Remaining shadow pairs (19 questions) | LOW | 2 | Shadow research operational |
| 5 | Cross-side questions (4) | MEDIUM | 1 | Cross-side analysis operational |
| 6 | Integration test (finding → proposal pipeline) | LOW | 1 | Full dual-world cycle verified |

**DEFERRED:** Cross-side comparison primitive (wait for larger paired sample). EXTENDED horizon (18 records — useless). Human question intake UI (manual works).

---

## 21. Verification/Test Strategy

| Test | Purpose | Pass Criteria |
|------|---------|---------------|
| Builder loads without error | Basic functionality | All 5,780 records loaded |
| Population filters produce correct counts | Data integrity | SCALP=1,821, INTRADAY=1,359, etc. |
| entity_id join produces expected coverage | Join quality | ~1,731 matched entities |
| SD-004 produces meaningful finding | Architecture validation | R-multiple segmented by terminal_stage with >100 records per segment |
| Existing 45 questions unchanged | Regression | Identical findings before/after |
| Evidence_source field propagates | Labelling | Shadow findings say "COUNTERFACTUAL" |
| No import from research → core/ execution | Governance | Static analysis confirms |

---

## 22. Final Architecture Diagram

```
                    MARKET REALITY (real M5 bars from MT5)
                              |
              +---------------+---------------+
              |                               |
         LIVE BOT                      SHADOW ENGINE
      (broker execution)            (bar-by-bar simulation)
              |                               |
   logs/decision_trace/              logs/shadow_trades/
   logs/execution_results/                    |
   data/research/research_universe.jsonl      |
              |                               |
     +--------+--------+                      |
     |   |    |   |    |                      |
    DEC MKT STRAT RISK EXEC              SHADOW_OUTCOME
     |                 |                 (5,780 records)
     +--------+--------+                      |
              |                               |
           OUTCOME                            |
           (94 trades)                        |
              |                               |
              +------entity_id JOIN (1,731)----+
              |                               |
     LIVE QUESTIONS              SHADOW QUESTIONS
        (14 Live-only)              (19 shadow pairs + 2 shadow-only)
              |                               |
              +---------- CROSS-SIDE ---------+
              |            (4 questions)       |
              |                               |
              +-----------+-------------------+
                          |
                    RESEARCH FINDING
                   (evidence_source labelled)
                          |
                    STANDARD PIPELINE
              (feedback → knowledge → proposal →
               candidate → experiment → validation →
               promotion gate → HUMAN)
```

---

## 23. Decision Gate

### Verdict: **IMPLEMENTATION_READY**

The architecture is structurally sound and the data exists. Implementation can proceed with the following specific constraints acknowledged:

### What Already Exists
- Runtime shadow data: 5,780 records, 100% valid R-multiples
- Deterministic entity_id join: 99% success rate when entity_id present
- Generic question runner: universe-agnostic, works on any population
- 11/12 primitives: work unchanged on shadow data
- Governed pipeline: finding → proposal → candidate → experiment → validation → promotion

### What Is Missing
- SHADOW_OUTCOME enum value + contract (Phase 1)
- ShadowOutcomeUniverseBuilder (Phase 2)
- evidence_source field in ResearchFinding (Phase 1)
- Shadow question definitions (Phase 4)
- Cross-side questions (Phase 5)

### What Must Be Resolved During Implementation (Not Before)
- The 36% empty entity_id rate — document as known limitation; monitor whether runtime fix improves it over time
- The 53% timeout rate — document; future consideration for per-horizon max_bars
- The max-158-per-entity outlier — investigate during builder implementation; apply reasonable dedup if needed

### What Must NOT Change
- Live trading runtime
- Existing 6 universe builders
- Existing 45 question definitions (preserved with original IDs)
- Existing 12 primitives
- Existing research results, proposals, knowledge
- Governance chain ordering

### Implementation Order
1. Phase 1: Foundation (enums, contract, evidence_source) — ZERO RISK
2. Phase 2: ShadowOutcomeUniverseBuilder — LOW RISK
3. Phase 3: SD-004 proof-of-concept — validates entire architecture
4. Phase 4: Shadow question batch — makes shadow research operational
5. Phase 5: Cross-side questions — enables comparative analysis
6. Phase 6: Integration verification — proves full pipeline works

---

*End of v3 specification. No code modified. No runtime affected. Implementation proceeds after human review.*
