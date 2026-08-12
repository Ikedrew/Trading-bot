# V10 RESEARCH ENGINE — DUAL LIVE/SHADOW ARCHITECTURE SPECIFICATION v2

**Date:** 2026-07-27  
**Type:** Specification Review & Refinement  
**Predecessor:** `v10_dual_universe_architecture_spec.md` (v1)  
**Status:** READ-ONLY — No code modified, no runtime affected  

---

## A. EXECUTIVE ARCHITECTURE

### The Simplest Explanation

The V10 Research Engine already operates in a single world: **what actually happened** (Live). The proposed extension adds a second parallel world: **what would have happened** (Shadow). A third analytical layer — **Cross-Side** — compares the two.

```
LIVE WORLD                    SHADOW WORLD
(what the bot did)            (what opportunities would have done)
      |                              |
 6 existing universes          1-3 new universes (from shadow_trades)
      |                              |
 45 existing questions          New shadow questions
      |                              |
      +---------- CROSS-SIDE --------+
                      |
            Comparative analysis
                      |
            "Was the decision right?"
```

### What Changed From v1

1. **Corrected:** Not all six Shadow universes should exist physically. Only SHADOW_OUTCOME is independently meaningful. The others are analytical projections.
2. **Added:** Recognition of the separate `research_engine/v10/shadow/` candidate-testing system (distinct from runtime shadow layer).
3. **Challenged:** Some v1 LIVE+SHADOW pairings are forced symmetry — revised classification below.
4. **Refined:** Shadow evidence semantics explicitly separated by shadow type (horizon vs primary).

---

## B. CONFIRMED CURRENT LIVE ARCHITECTURE

Verified against actual code (no changes from v1 — confirmed accurate):

| Stage | Authoritative File | Status |
|-------|-------------------|--------|
| 6 Universe Builders | `research_engine/v10/universes/*.py` | CONFIRMED |
| Universe Contracts | `research_engine/v10/universes/contracts.py` | CONFIRMED |
| 45-Question Bank | `research_engine/v10/universes/question_bank.py` | CONFIRMED |
| 12 Primitives | `research_engine/v10/runner/primitives/implementations.py` | CONFIRMED |
| Question Runner | `research_engine/v10/runner/question_runner.py` | CONFIRMED |
| Finding Schema | `research_engine/v10/control_plane/finding_schema.py` | CONFIRMED |
| Feedback | `research_engine/v10/feedback/{generator,model,persistence}.py` | CONFIRMED |
| Knowledge | `research_engine/v10/knowledge/{engine,model,store}.py` | CONFIRMED |
| Proposals | `research_engine/v10/proposals/{generator,model,store,ranking}.py` | CONFIRMED |
| Candidates | `research_engine/v10/candidates/{models,candidate_lifecycle,candidate_registry}.py` | CONFIRMED |
| Experiments | `research_engine/v10/proposals/{experiment,run_experiment}.py` | CONFIRMED |
| Validation | `research_engine/v10/proposals/validator.py` | CONFIRMED |
| Promotion | `research_engine/v10/proposals/promotion.py` | CONFIRMED |

**Additional system discovered (not in v1):**
| Component | Location | Purpose |
|-----------|----------|---------|
| Shadow Optimisation Engine | `research_engine/v10/shadow/` | Candidate-level what-if testing against completed trades. SEPARATE from runtime shadow layer. |

This shadow optimisation engine (`ShadowRunner`) applies candidate parameter changes to historical trade data and computes what the modified candidate would have produced. It operates on ALREADY-CLOSED trades, not on live bars. It is architecturally separate from `core/shadow_trades.py`.

---

## C. CONFIRMED CURRENT SHADOW ARCHITECTURE

### C.1 Three Distinct Shadow Mechanisms (CONFIRMED)

| Mechanism | Runtime Module | Research Module | Purpose |
|-----------|---------------|-----------------|---------|
| **Runtime Horizon Shadow** | `core/shadow_trades.py` + `core/horizon/horizon_trade_builder.py` | None (not yet consumed by research) | Simulates counterfactual trade lifecycle for ALL detected patterns across eligible horizons |
| **Runtime Primary Shadow** | `core/shadow_trades.py` | None (not yet consumed) | Simulates parallel lifecycle for EXECUTED trades (for live-vs-shadow comparison) |
| **Research Shadow Optimisation** | None (doesn't produce runtime data) | `research_engine/v10/shadow/` | Applies candidate parameter modifications to historical trades |

### C.2 Semantic Difference (CRITICAL — not captured in v1)

| Property | Runtime Shadow (core/) | Research Shadow (research_engine/v10/shadow/) |
|----------|----------------------|----------------------------------------------|
| When it runs | Live, per-bar, real-time | Offline, on historical completed trades |
| What it simulates | Independent lifecycle from entry to exit using future bars | What-if of modified parameters on same exit price |
| Market data | Real future bars (bar-by-bar progression) | Actual exit price of the completed trade |
| Exit logic | SL/TP/timeout (independent simulation) | Re-calculated R from modified SL/TP against actual exit |
| entity_id | Has own entity_id (from decision trace) | Uses existing trade entity_id |
| Persistence | `logs/shadow_trades/` | In-memory / report files |
| R-multiple meaning | "What this opportunity would have done from entry to simulated exit" | "What this already-closed trade would have R'd with different parameters" |

**This distinction matters enormously for the dual-universe design.**

The RUNTIME shadow is the genuine counterfactual engine — it simulates what would have happened using subsequent real market bars. This is the data source for SHADOW_OUTCOME.

The RESEARCH shadow optimisation is a candidate-testing tool — it retroactively applies parameter modifications. This is NOT the same kind of evidence and should NOT be conflated with SHADOW_OUTCOME.

---

## D. CANONICAL SHADOW SEMANTICS

### D.1 What Does One Runtime Shadow R-Multiple Mean?

**Precise definition:**

> A runtime shadow R-multiple represents the price-space return (in units of risk) that would have been realised IF:
> - The trade had been entered at market price at decision time
> - With a stop-loss derived from horizon-specific market structure (M5/M15/H1)
> - With a take-profit at a fixed R:R ratio (2.0/3.0/4.0 per horizon)
> - Evaluated bar-by-bar against subsequent real closed M5 candles
> - Exiting at SL, TP, or 60-bar timeout (whichever occurs first)
> - With no slippage, commission, or spread (gross counterfactual)
> - With SL checked before TP when both could trigger on same bar

### D.2 What It Does NOT Mean

- NOT what the V10 engine would have produced (different geometry)
- NOT what the live broker would have filled (no slippage)
- NOT the same across horizons (SCALP/INTRADAY/EXTENDED have different SL/TP)
- NOT guaranteed achievable (no liquidity/spread check)
- NOT equivalent to a Primary shadow for the same entity (different SL/TP geometry)

### D.3 Multiple Counterfactual Models

For the same `entity_id`, there may exist:

| Shadow Type | Geometry | R:R | SL Source | Timeout | Meaning |
|-------------|----------|-----|-----------|---------|---------|
| `hshadow_*_SCALP` | M5 candle ± 2 pips | 2:1 | M5 high/low | 60 bars | "Tight scalp counterfactual" |
| `hshadow_*_INTRADAY` | M15 structure ± 3 pips | 3:1 | M15 support/resistance | 60 bars | "Medium-term counterfactual" |
| `hshadow_*_EXTENDED` | H1 swings ± 5 pips | 4:1 | H1 swing high/low | 60 bars | "Wide counterfactual" |
| `shadow_*` (primary) | V10 engine geometry | V10 engine R:R | V10 risk engine | 60 bars | "What V10 intended to capture" |

**These represent DIFFERENT counterfactual contracts. They are NOT interchangeable observations of the same thing.**

### D.4 Architectural Implication

A Shadow question must declare WHICH counterfactual model it operates on:
- All horizons pooled (treats each as independent observation — but they're correlated)
- Specific horizon (SCALP only, INTRADAY only, etc.)
- Primary shadow only (V10 engine geometry — only for EXECUTE decisions)
- Best-horizon (select the horizon with highest R:R for each entity — introduces selection bias)

**OPEN DECISION:** The question contract must explicitly declare its shadow population scope. Default should be "specific horizon" rather than "all pooled" to avoid correlation inflation.

---

## E. LIVE/SHADOW UNIVERSE MODEL (REVISED)

### E.1 Challenge to the Six-Mirrored-Universes Model

The v1 spec proposed 3 physical + 3 derived shadow universes. Under critical review:

| Proposed Shadow Universe | Should It Exist? | Reasoning |
|--------------------------|-----------------|-----------|
| SHADOW_OUTCOME | **YES — physical** | Contains genuinely new data (counterfactual R, MFE, MAE, exit) not available in ANY Live universe |
| SHADOW_EXECUTION | **NO — unnecessary** | Entry geometry is already IN the SHADOW_OUTCOME record. Separate universe adds no analytical value a population filter can't provide. |
| SHADOW_RISK | **NO — unnecessary** | Risk parameters are already IN the SHADOW_OUTCOME record (risk_config_snapshot). A field projection, not a universe. |
| SHADOW_DECISION | **NO — it's a population filter on Decision** | The decision is the SAME Live decision. "Decisions that have shadow outcomes" is a population of the Decision universe, not a separate universe. |
| SHADOW_MARKET | **NO — it's a population filter on Market** | Same reasoning — market state doesn't change counterfactually |
| SHADOW_STRATEGY | **NO — it's a population filter on Strategy** | Same reasoning |

### E.2 Correct Architecture

**ONE new physical universe: SHADOW_OUTCOME**

Everything else is achieved through:
- Population filters on existing Live universes (e.g., "Decision records WHERE entity_id IN shadow_outcome_entities")
- Cross-universe joins via entity_id (existing infrastructure)
- Field projections within SHADOW_OUTCOME (entry geometry, risk params are FIELDS, not universes)

### E.3 Why This Is Simpler AND Correct

The principle is: **a universe represents an independent observation domain with its own data source and grain.**

- Decision, Market, Strategy, Risk already observe ALL decisions (EXECUTE + NO_TRADE). They don't need shadow copies.
- Entry geometry and risk parameters are PROPERTIES OF the shadow outcome, not independent observations.
- Creating six shadow universes introduces maintenance cost and conceptual confusion without analytical benefit.

### E.4 What SHADOW_OUTCOME Contains (Comprehensive)

It serves multiple analytical needs via POPULATIONS rather than sub-universes:

| Population | Analytical Purpose | Formerly Proposed As |
|------------|-------------------|---------------------|
| ALL_SHADOW_OUTCOMES | Counterfactual expectancy of detected opportunity pool | SHADOW_OUTCOME |
| SHADOW_WINS / LOSSES | Win/loss classification | SHADOW_OUTCOME populations |
| SHADOW_FROM_EXECUTE | Live vs shadow comparison | — |
| SHADOW_FROM_NO_TRADE | Missed opportunity analysis | — |
| SHADOW_HORIZON_* | Per-horizon analysis | — |
| SHADOW_EXIT_* | Exit type analysis | — |

And the shadow record ALREADY contains:
- Entry/SL/TP/direction (what v1 called "SHADOW_EXECUTION")
- Risk distance, R:R ratio (what v1 called "SHADOW_RISK")
- Strategy, pattern, score (joinable context)

**Conclusion:** The six-universe mirror is architecturally elegant but practically incorrect. ONE universe + rich populations + cross-joins is the right model.

---

## F. UNIVERSE CONTRACTS

### F.1 Live Contracts (Unchanged)

All six Live universe contracts remain as defined in `contracts.py`. No modifications needed.

### F.2 Shadow Outcome Contract (New)

```
SHADOW_OUTCOME

Universe ID:         SHADOW_OUTCOME
Name:                Shadow Outcome Universe
Description:         Counterfactual economic consequences of detected opportunities 
                     under horizon-specific shadow models, evaluated against real 
                     subsequent market bars.
Grain:               1 closed shadow trade = 1 counterfactual outcome under 1 model
Identity:            shadow_trade_id
Source:              logs/shadow_trades/{SYMBOL}/{DATE}.jsonl (schema: shadow_trades_v2)
Schema versions:     shadow_trades_v2
Join keys:           entity_id (1:N to Decision/Market/Strategy/Risk), symbol, correlation_id
Coverage fields:     timestamp_decision_utc, symbol, trade_horizon
Lineage fields:      shadow_trade_id, entity_id, correlation_id

SEMANTIC CONTRACT:
  r_multiple in this universe means COUNTERFACTUAL R.
  It is NEVER described as realised performance.
  It is ALWAYS labelled with:
    - evidence_source: "COUNTERFACTUAL"
    - shadow_model: {horizon, sl_source, rr_ratio, max_bars}
    - limitations: [no slippage, no spread, SL-first-on-same-bar, 60-bar cap]
```

---

## G. POPULATION MODEL

### G.1 Shadow Outcome Populations

| Population | Definition | Source |
|------------|-----------|--------|
| ALL_SHADOW_OUTCOMES | All closed shadow_trades_v2 records with pnl_r_multiple not null | Full dataset |
| SHADOW_WINS | pnl_r_multiple > 0 | Outcome filter |
| SHADOW_LOSSES | pnl_r_multiple <= 0 | Outcome filter |
| SHADOW_FROM_EXECUTE | entity_id IN Decision(action="EXECUTE") | Cross-join |
| SHADOW_FROM_NO_TRADE | entity_id IN Decision(action="NO_TRADE") | Cross-join |
| SHADOW_HORIZON_SCALP | trade_horizon="SCALP" OR trade_id contains "_SCALP" | Identity filter |
| SHADOW_HORIZON_INTRADAY | trade_horizon="INTRADAY" | Identity filter |
| SHADOW_HORIZON_EXTENDED | trade_horizon="EXTENDED" | Identity filter |
| SHADOW_PRIMARY | trade_id starts with "shadow_" (not "hshadow_") | Identity filter |
| SHADOW_TP_HIT | exit_reason="take_profit" | Outcome filter |
| SHADOW_SL_HIT | exit_reason="stop_loss" | Outcome filter |
| SHADOW_TIMEOUT | exit_reason="max_bars_timeout" | Outcome filter |

### G.2 Cross-Side Populations (Governed Joins)

These are NOT new universes. They are governed population constructions:

| Population | Construction | Purpose |
|------------|-------------|---------|
| DECISIONS_WITH_SHADOW | Decision WHERE entity_id IN ShadowOutcome | "All decisions that have counterfactual data" |
| NO_TRADE_WITH_SHADOW | Decision WHERE action=NO_TRADE AND entity_id IN ShadowOutcome | D-004b analysis |
| EXECUTE_WITH_BOTH | Decision WHERE action=EXECUTE AND entity_id IN BOTH Outcome AND ShadowOutcome | Leakage analysis |

### G.3 Population Contract Requirements

Every Shadow population satisfies:
- **Canonical definition:** Declarative filter
- **Inclusion criteria:** shadow_trades_v2 schema, non-null r_multiple, non-empty entity_id
- **Exclusion criteria:** Empty entity_id, null pnl_r_multiple
- **Source:** `logs/shadow_trades/`
- **Owner:** ShadowOutcomeUniverseBuilder
- **Version:** Content hash (consistent with Live pattern)
- **Join key:** entity_id (to all Live universes)
- **Minimum data:** Declared per-question (minimum_sample_size)

---

## H. PRIMITIVE COMPATIBILITY MATRIX (REVISED)

| Primitive | LIVE | SHADOW_OUTCOME | CROSS-SIDE | Notes |
|-----------|------|----------------|------------|-------|
| `expectancy` | YES | YES | N/A | Same computation; semantic difference in output label |
| `distribution` | YES | YES | N/A | Universal |
| `comparison` | YES | YES | YES (group="side") | Can group Live vs Shadow if joined population constructed |
| `conditional_expectancy` | YES | YES | N/A | Works on joined populations |
| `calibration` | YES | WEAK | N/A | Calibrating against counterfactual validates model, not reality |
| `predictive_power` | YES | YES | N/A | Universal |
| `segmentation` | YES | YES | N/A | Universal |
| `transition` | YES | YES | N/A | If temporal field present |
| `execution_quality` | YES | PARTIAL | N/A | Shadow has bars_held not duration_seconds |
| `degradation` | YES | YES | N/A | Temporal analysis |
| `anomaly_analysis` | YES | NO | N/A | Shadow has no anomaly concept |
| `exceptional_analysis` | YES | PARTIAL | N/A | Needs shadow-specific criteria |
| **cross_side_comparison** | N/A | N/A | **NEW** | Paired entity comparison |

**Revision from v1:** The `comparison` primitive can ALREADY serve many cross-side needs if the joined population is constructed correctly (one group = "LIVE", other = "SHADOW"). The new `cross_side_comparison` is only needed for PAIRED per-entity analysis (same entity, both outcomes). This is genuinely new — existing primitives compare groups, not pairs.

---

## I. QUESTION TAXONOMY

### I.1 Three Types of Research Knowledge

| Type | Question Form | Evidence Source | Example |
|------|--------------|----------------|---------|
| **DESCRIPTIVE** | "What does the bot do?" | Live pipeline observations (no outcome needed) | "Where are decisions rejected?" |
| **OUTCOME** | "What happened / would have happened?" | Realised R (Live) or Counterfactual R (Shadow) | "What is expectancy?" |
| **COMPARATIVE** | "Was the bot's choice correct?" | Both worlds compared for same entity | "Did rejection protect or cost?" |

### I.2 Evidence Quality Hierarchy

```
STRONGEST: Realised Live outcome (broker-confirmed, actual P&L)
STRONG:    Counterfactual Shadow outcome (real bars, model limitations acknowledged)
MODERATE:  Cross-side comparison (both sources, but counterfactual has limitations)
WEAK:      Descriptive without outcome (informative but not actionable alone)
```

### I.3 Question Placement Rule

A question belongs where its REQUIRED EVIDENCE naturally lives:
- If it needs realised R → LIVE
- If it needs counterfactual R of unexecuted signals → SHADOW
- If it needs BOTH to compare → CROSS-SIDE
- If it needs no outcome (counts, distributions, rates) → DESCRIPTIVE (typically LIVE)

A question does NOT get a Shadow pair merely because the primitive could operate on Shadow data. It gets a pair only when the Shadow variant asks a DISTINCT research question that the Live version cannot answer.

---

## J. FULL 45-QUESTION AUDIT (REVISED)

### J.1 Revised Classification

Key changes from v1:
- Several v1 "LIVE+SHADOW" pairs revised to "LIVE_ONLY" (Shadow pair adds no distinct insight)
- Clearer distinction between "Shadow pair adds analytical value" vs "Shadow pair is just bigger sample of same question"

| # | ID | Title | Classification | Reasoning |
|---|-----|-------|---------------|-----------|
| 1 | E-001 | System Expectancy | **LIVE_ONLY** | "Realised system expectancy" is inherently Live. Shadow has a DIFFERENT question: "What is the counterfactual opportunity pool?" — not a pair, a new question. |
| 2 | E-002 | Win/Loss Distribution | **LIVE_ONLY** | Realised distribution is about actual trading outcomes |
| 3 | E-003 | Exit Reason Distribution | **LIVE_AND_SHADOW** | Live: broker exits. Shadow: model exits. COMPARISON between them reveals management impact |
| 4 | E-004 | Execution Quality by Session | **LIVE_ONLY** | Broker-only evidence |
| 5 | E-005 | Probability of Ruin | **LIVE_ONLY** | Account survival — realised only |
| 6 | E-006 | Out-of-Sample Validation | **LIVE_ONLY** | Overfitting — realised only |
| 7 | E-007 | Stop Placement | **LIVE_AND_SHADOW** | Live: actual SL outcomes. Shadow: counterfactual SL sensitivity (genuinely different question) |
| 8 | E-008 | Pattern Degradation | **LIVE_AND_SHADOW** | Degradation detectable on both; shadow detects earlier (larger/faster accumulating sample) |
| 9 | E-009 | Duration vs Outcome | **LIVE_ONLY** | Real trade duration is broker-managed; shadow bars_held is model artifact |
| 10 | E-010 | R:R Effectiveness | **LIVE_AND_SHADOW** | Shadow provides natural experiment in R:R (horizons have different fixed R:R) |
| 11 | D-001 | Score Predictive Power | **LIVE_AND_SHADOW** | Shadow tests score prediction on FULL signal set (fundamentally different from testing on executed-only) |
| 12 | D-002 | EV Calibration | **LIVE_ONLY** | Calibration requires realised truth |
| 13 | D-003 | Threshold Effectiveness | **CROSS_LIVE_SHADOW** | "Move threshold" requires seeing both sides of the cut |
| 14 | D-004 | Rejection Stage Analysis | **SPLIT** | See Section M |
| 15 | D-005 | Opportunity Quality | **LIVE_AND_SHADOW** | Quality tested against both outcome types reveals whether quality predicts universally or only among selected |
| 16 | D-006 | Opportunity Failure | **LIVE_ONLY** | "Failure" = actual loss. Shadow failures are model failures. |
| 17 | D-007 | Risk Gate Value | **CROSS_LIVE_SHADOW** | Requires blocked→shadow R vs approved→live R |
| 18 | M-001 | Regime Predicts Outcomes | **LIVE_AND_SHADOW** | Shadow tests regime effect on full signal set (distinct from testing on executed subset) |
| 19 | M-002 | HTF Alignment Value | **LIVE_AND_SHADOW** | Alignment tested on full vs executed (different question) |
| 20 | M-003 | Volatility State Impact | **LIVE_AND_SHADOW** | Same rationale as M-001 |
| 21 | M-004 | Market Structure Clarity | **LIVE_ONLY** | Structural clarity is a market observation question, not outcome-dependent |
| 22 | M-005 | Location Quality Impact | **LIVE_AND_SHADOW** | Location prediction testable on full signal set |
| 23 | M-006 | Session Edge Variation | **LIVE_AND_SHADOW** | Session effect on full signal pool is distinct from executed pool |
| 24 | S-001 | Strategy Family Expectancy | **LIVE_AND_SHADOW** | Strategy edge on all signals vs executed only (different) |
| 25 | S-002 | Pattern Expectancy | **LIVE_AND_SHADOW** | Pattern edge on full signal set |
| 26 | S-003 | Strategy Selection Accuracy | **LIVE_ONLY** | Selection accuracy requires realised truth |
| 27 | S-004 | Strategy Rejection Patterns | **SHADOW_ONLY** | "Are there profitable patterns the engine misses?" — requires counterfactual |
| 28 | ED-001 | Edge Leakage | **CROSS_LIVE_SHADOW** | Paired comparison: shadow R vs live R for same entity |
| 29 | ED-002 | Missed Opportunity Cost | **SHADOW_ONLY** | "Would have succeeded" requires counterfactual |
| 30 | ED-003 | Position Sizing | **LIVE_ONLY** | Real account P&L required |
| 31 | EM-001 | Regime-Conditioned Expectancy | **LIVE_AND_SHADOW** | Shadow variant tests on full signal pool per regime |
| 32 | EM-002 | Market Drift | **LIVE_ONLY** | Drift requires realised temporal truth |
| 33 | ES-001 | Execution by Strategy | **LIVE_AND_SHADOW** | Strategy → outcome on both populations |
| 34 | DM-001 | Decision Quality Under Regime | **LIVE_AND_SHADOW** | Score prediction by regime on both |
| 35 | DM-002 | Opportunity vs Market State | **LIVE_AND_SHADOW** | Quality × market on full signal set |
| 36 | DM-003 | Rejection Rate by Market | **SPLIT** | Descriptive (Live) + counterfactual cost (Shadow) |
| 37 | DS-001 | Strategy Confidence Calibration | **LIVE_AND_SHADOW** | Confidence → outcome testable both |
| 38 | DS-002 | Strategy Conditions vs Outcome | **LIVE_AND_SHADOW** | Conditions → outcome testable both |
| 39 | MS-001 | Strategy x Regime | **LIVE_AND_SHADOW** | Interaction on full signal pool |
| 40 | MS-002 | Pattern x Market Context | **LIVE_AND_SHADOW** | Pattern × context on full signal pool |
| 41 | MS-003 | Strategy Availability | **LIVE_ONLY** | Descriptive/structural |
| 42 | EDM-001 | Complete Lifecycle | **LIVE_ONLY** | Full broker lifecycle |
| 43 | DMS-001 | Decision x Strategy x Market | **LIVE_AND_SHADOW** | Multi-dimensional on both |
| 44 | EDMS-001 | Full System Attribution | **LIVE_ONLY** | Attribution requires realised |
| 45 | EDMS-002 | Promotion Impact | **CROSS_LIVE_SHADOW** | Both-side convergence for promotion confidence |

### J.2 Revised Summary

| Classification | Count | Change from v1 |
|----------------|-------|----------------|
| LIVE_ONLY | **14** | +3 (E-001, E-002, E-009 moved from LIVE+SHADOW) |
| LIVE_AND_SHADOW | **21** | -3 (moved to LIVE_ONLY) |
| SHADOW_ONLY | **2** | Unchanged |
| CROSS_LIVE_SHADOW | **4** | Unchanged |
| SPLIT | **2** | Unchanged (D-004 → 3, DM-003 → 2) |
| REFORMULATE | **2** | Unchanged |
| **TOTAL** | **45** | |

### J.3 Rationale for Revisions

**E-001 moved to LIVE_ONLY:**
"System expectancy" means "what is the TRADING SYSTEM producing?" That's inherently Live. The Shadow question "what is the counterfactual opportunity pool expectancy?" is a DIFFERENT question — not a pair, but a genuinely new research question (should be created as new, not as a duplicate of E-001).

**E-002 moved to LIVE_ONLY:**
Win/loss distribution shape of actual trades is about position management quality. Shadow distribution shape is about opportunity quality. These are different analytical questions, not the same question on two populations.

**E-009 moved to LIVE_ONLY:**
Duration is a managed-trade concept (broker holds position, trade management adjusts SL, etc.). Shadow `bars_held` is a model timeout parameter, not a decision variable. The analytical questions are different.

**M-004 moved to LIVE_ONLY:**
"Does structural clarity predict outcomes?" — the prediction relationship is most meaningfully tested on realised outcomes. Shadow R is the PREDICTED variable, not the verification of prediction. Testing "does clarity predict shadow R" tests whether clarity predicts the shadow MODEL, not whether it predicts reality.

---

## K. HUMAN OBSERVATION → RESEARCH QUESTION

### K.1 Governed Interface Contract

```python
@dataclass
class ResearchRequest:
    """A human-originated research observation awaiting formalisation."""
    request_id: str                    # Auto-generated
    observation: str                   # Human's natural-language observation
    submitted_at: str                  # Timestamp
    
    # Classification (determined during formalisation)
    knowledge_type: str = ""           # DESCRIPTIVE | COUNTERFACTUAL | COMPARATIVE
    evidence_side: str = ""            # LIVE | SHADOW | CROSS_SIDE
    
    # Formalisation output
    formalised_question_id: str = ""   # Links to NewEngineQuestion if formalised
    formalisation_status: str = ""     # PENDING | FORMALISED | REJECTED | BLOCKED
    rejection_reason: str = ""         # Why it couldn't become a question
    
    # Required capabilities
    requires_new_population: bool = False
    requires_new_primitive: bool = False
    requires_new_data: bool = False
```

### K.2 Formalisation Process

```
HUMAN OBSERVATION
  "I think the bot rejects too many opportunities in RANGING"
      ↓
EVIDENCE TYPE DETERMINATION
  Needs: counterfactual outcome of rejected signals in RANGING
  Side: SHADOW (or CROSS if comparing to accepted)
      ↓
POPULATION VERIFICATION  
  Required: SHADOW_FROM_NO_TRADE where regime=RANGING
  Available: YES (if shadow data + market join available)
      ↓
PRIMITIVE SELECTION
  Analysis: segmentation by regime, metric = shadow r_multiple
  Available: YES (existing segmentation primitive)
      ↓
QUESTION FORMALISATION
  ID: H-001 (human-originated)
  Contract: standard NewEngineQuestion
  Universe: SHADOW_OUTCOME + DECISION + MARKET (join)
  Population: SHADOW_FROM_NO_TRADE, RANGING_REGIME
  Primitive: segmentation
      ↓
GOVERNANCE CHECK
  Minimum sample met? (check population size)
  Valid analytical relationship? (shadow R meaningful here)
  Not already answered by existing question? (check for overlap)
      ↓
EXECUTION via standard QuestionRunner
```

### K.3 What This Architecture Requires (Future)

- A `ResearchRequest` model (not yet implemented)
- A formalisation helper that maps observations to contracts (future — could be manual or assisted)
- A governance check that prevents invalid questions from entering the pipeline
- Lineage from request → question → finding (provenance preserved)

**NOT required:** Natural-language AI that auto-generates questions. The formalisation process can be human-assisted or fully manual initially.

---

## L. CROSS-SIDE RESEARCH (GOVERNED COMPARISON)

### L.1 Minimum Architecture

Cross-side analysis requires:
1. A **joined population** containing matched Live + Shadow observations for the same entity
2. A **primitive** capable of paired comparison (new: `cross_side_comparison`)
3. A **question contract** declaring the comparison intent
4. **Evidence labelling** distinguishing the output as CROSS_SIDE

### L.2 Join Construction

```python
def build_cross_side_population(
    live_population: list[dict],     # From Outcome Universe
    shadow_population: list[dict],   # From ShadowOutcome Universe
    join_key: str = "entity_id",     # Authoritative join
) -> list[dict]:
    """
    Constructs paired records for cross-side analysis.
    
    Each output record contains:
        entity_id, live_r_multiple, shadow_r_multiple, 
        live_exit_reason, shadow_exit_reason, ...
    
    Only entities present in BOTH populations are included.
    Multiple shadow records per entity: question contract declares handling.
    """
```

### L.3 Handling Multiple Shadow Records

**OPEN DECISION (must be settled per-question):**

| Strategy | When to Use | Trade-off |
|----------|------------|-----------|
| **Primary shadow only** | Live-vs-shadow leakage (ED-001) | Uses V10 geometry — closest to actual intent. Only available for EXECUTE decisions. |
| **Best-horizon match** | When comparing "what the bot would have captured at the best available horizon" | Selection bias (optimistic) |
| **Specific horizon** | Per-horizon research (e.g., "are SCALP rejections costly?") | Clean but narrow |
| **Average across horizons** | Aggregate opportunity quality | Smooths noise but obscures horizon effects |

**Recommendation:** Default to **specific horizon** declared per-question. Never implicitly pool horizons.

---

## M. D-004 REFERENCE DESIGN (CONFIRMED — No Change from v1)

D-004 splits into three questions:

**D-004 (preserved, narrowed to descriptive):**
- "Where in the pipeline are opportunities rejected?"
- LIVE_ONLY, DESCRIPTIVE, no outcome needed
- Analysis: count/percentage per terminal_stage

**SD-004 (new SHADOW question):**
- "What counterfactual expectancy do rejected opportunities produce by rejection stage?"
- SHADOW_ONLY, COUNTERFACTUAL
- Population: SHADOW_FROM_NO_TRADE joined to Decision for terminal_stage
- Analysis: segmentation by terminal_stage, metric = shadow r_multiple

**X-002 (new CROSS-SIDE question):**
- "Which rejection stages correctly protect capital vs incorrectly reject profitable opportunities?"
- CROSS_LIVE_SHADOW, COMPARATIVE
- Per stage: classify shadow R as correct_rejection (R<0) or missed_opportunity (R>0)

**Lineage:** D-004 → {D-004 (narrowed), SD-004, X-002}

---

## N. PROPOSAL/CANDIDATE IMPLICATIONS (CONFIRMED)

No changes from v1. The existing governed pipeline supports shadow evidence without structural modification:

1. Shadow findings enter the pipeline with `evidence_source: COUNTERFACTUAL`
2. Feedback/Knowledge process them with appropriate confidence weighting
3. Proposals can target shadow populations for candidate experiments
4. The `POPULATION_FILTER` mechanism is already population-agnostic
5. Promotion gate requires human governance (strengthened by evidence labelling)

**Critical governance rule:**
> Shadow evidence alone is NEVER sufficient for automatic promotion. A positive shadow result means "this hypothesis deserves further investigation" — not "deploy this change."

---

## O. DATA INTEGRITY / STATISTICAL RISKS

### O.1 Counterfactual Validity

| Risk | Severity | Description | Mitigation |
|------|----------|-------------|-----------|
| Shadow ≠ Reality | HIGH | Shadow R does not account for slippage, spread, market impact, partial fills | ALWAYS label as counterfactual; never report as realised |
| Horizon geometry ≠ V10 geometry | HIGH | Horizon shadows use simplified structure-based SL/TP; V10 engine uses its own Entry engine output | Separate populations: horizon shadows vs primary shadows |
| 60-bar timeout bias | MEDIUM | Forces exit at 5 hours — biases long-duration trades toward zero R | Document; consider per-horizon max_bars in future |
| SL-first-on-same-bar bias | LOW | When both SL and TP could trigger on same bar, SL is evaluated first (pessimistic) | Conservative — acceptable |
| No commission/swap | LOW | Shadow R is gross; live R may include commission/swap | Consistent within shadow; label "gross counterfactual" |

### O.2 Sample Bias

| Bias | Description | Mitigation |
|------|-------------|-----------|
| **Selection bias** | Only pattern-detected decisions get shadows (~30-60% of all decisions) | Never claim shadow covers "all possible trades"; state coverage rate |
| **Horizon availability bias** | Not all horizons eligible for all opportunities (depends on structure data availability) | Document per-horizon population sizes; exclude low-N horizons |
| **Survivorship bias** | Shadow only exists for opportunities where required structure data was available | Acknowledge: "among opportunities where horizon geometry could be constructed" |
| **Correlation inflation** | Multiple horizons per entity are NOT independent observations | Question contracts must declare handling; never treat horizon shadows as independent samples without justification |

### O.3 Join Quality

| Issue | Expected Rate | Impact | Detection |
|-------|--------------|--------|-----------|
| Empty entity_id in shadow | <5% | Minor sample loss | Count at build time |
| Shadow without Decision match | <1% | Orphan records | Cross-reference; exclude |
| Decision without Shadow | ~40-70% | Many decisions have no shadow | Expected — only pattern-detected get shadows |
| Multiple shadows per entity | By design (1-3) | Cardinality handling required | Population contracts declare strategy |

---

## P. IMPLEMENTATION BOUNDARIES

### P.1 What Can Eventually Change

| Component | Permissible Change | Governance |
|-----------|-------------------|-----------|
| Universe enum | Add SHADOW_OUTCOME | Code change — review required |
| Population enum | Add shadow populations | Code change |
| contracts.py | Add SHADOW_OUTCOME_CONTRACT | Code change |
| New builder file | ShadowOutcomeUniverseBuilder | New file |
| Finding schema | Add evidence_source field | Additive field (optional, backward-compatible) |
| Question bank | Add shadow/cross questions | Additive (new entries, old preserved) |
| Primitive registry | Register cross_side_comparison | New primitive file |

### P.2 What Must NEVER Change

| Component | Reason |
|-----------|--------|
| Live trading runtime (core/) | Architecture boundary — research never modifies live |
| Shadow trade engine (core/shadow_trades.py) | Data source — research READS only |
| Existing 6 Live universe builders | Correct and operational |
| Existing 45 question definitions (IDs, intents) | Historical research — preserved |
| Existing 12 primitives (semantics) | Infrastructure — semantically stable |
| Governance chain ordering | Research → Proposal → Candidate → Validation → Human → Change |

---

## Q. OPEN DECISIONS (Requires Human Architectural Approval)

### Q.1 Must-Decide Before Implementation

| Decision | Options | Recommendation | Impact If Wrong |
|----------|---------|----------------|-----------------|
| **How many physical Shadow universes?** | 1 (SHADOW_OUTCOME only) vs 3 (v1 proposal) vs 6 (full mirror) | **1** — others are populations/projections | Over-engineering if >1; loss of entry geometry analysis if 0 |
| **Horizon handling default** | Pool all / specific horizon / average | **Specific horizon per-question** | Correlation inflation if pooled; data scarcity if too narrow |
| **Evidence weighting in ranking** | Same as Live / higher threshold / explicit discount | **Explicit counterfactual discount** in ranking score | Over-confidence if same; under-utilisation if too discounted |
| **Shadow question ID scheme** | SE-nnn / C-nnn / SD-nnn / other | **SE-nnn** (Shadow variant of E-nnn) | Naming confusion if unclear |
| **When shadow findings can generate proposals** | Immediately / after N findings / after cross-side validation | **After ≥1 cross-side validation** supporting the shadow finding | False positives if too early; slow discovery if too late |

### Q.2 Can-Defer Decisions

| Decision | Deferral Reason |
|----------|----------------|
| Human question intake interface | Not blocking — manual question creation works |
| Auto-generation of shadow questions from findings | Optimisation — not needed initially |
| Shadow-specific confidence model | Needs shadow findings to calibrate |
| Full cross-side primitive library | One primitive sufficient initially |

---

## R. IMPLEMENTATION ROADMAP (REVISED)

### Phase 1 — Foundation (1 session, zero risk)

- Add `SHADOW_OUTCOME` to Universe enum
- Add shadow Population enum values  
- Add SHADOW_OUTCOME_CONTRACT to contracts.py
- Add `evidence_source` field to ResearchFinding (optional, defaults to "LIVE")
- Tests: enum validation, contract completeness, finding schema backward compat

### Phase 2 — ShadowOutcomeUniverseBuilder (1-2 sessions, low risk)

- New file: `research_engine/v10/universes/shadow_outcome_universe.py`
- Reads `logs/shadow_trades/`, normalises to flat records, provides populations
- Tests: load/build/filter, entity_id validation, population coverage report
- **Milestone:** Run coverage check — what % of Decision entities have shadow matches?

### Phase 3 — D-004 Proof of Concept (1 session, medium risk)

- Add D-004a (descriptive), SD-004 (shadow), X-002 (cross-side) to question bank
- Demonstrate: shadow question runs, produces finding with counterfactual evidence label
- Tests: SD-004 produces meaningful results (R-multiple segmented by terminal_stage)
- **Milestone:** D-004 problem is solved — evidence quality moves from INSUFFICIENT to STRONG

### Phase 4 — Shadow Question Batch (2 sessions, low risk)

- Add remaining 21 shadow pair questions
- Map to existing primitives via primitive_mapping.py
- Tests: all questions execute without error, findings labelled correctly
- **Milestone:** Shadow research operational

### Phase 5 — Cross-Side Primitive + Questions (1 session, medium risk)

- New file: `research_engine/v10/runner/primitives/cross_side.py`
- Add X-001 through X-005 to question bank
- Tests: paired comparison produces valid output
- **Milestone:** Cross-side analysis operational

### Phase 6 — Integration + Governance (1 session)

- Verify: shadow findings → feedback → knowledge → proposals pipeline works
- Verify: evidence_source propagates through full pipeline
- Verify: ranking system handles counterfactual evidence correctly
- **Milestone:** Full dual-world research cycle operational

---

## ASSUMPTIONS THAT MUST NOT BECOME ARCHITECTURE

1. **"Every Live question needs a Shadow twin"** — NO. Only where the Shadow variant asks a genuinely different research question that Live cannot answer.

2. **"Every Shadow record represents the same counterfactual"** — NO. Horizon shadows (SCALP/INTRADAY/EXTENDED) and primary shadows represent DIFFERENT counterfactual contracts with different geometry.

3. **"entity_id alone guarantees valid causal pairing"** — MOSTLY YES, but with caveats: one entity_id maps to MULTIPLE shadow records (horizon multiplicity). Questions must declare handling.

4. **"All Shadow R values are directly comparable"** — NO. R from SCALP (2:1 R:R, tight SL) is not the same measurement as R from EXTENDED (4:1 R:R, wide SL). They use different geometry.

5. **"Shadow is simply simulated Live"** — NO. Shadow uses different entry geometry (horizon-specific vs V10 engine), different risk parameters, and no trade management (no trailing, no BE move, no partial exits). It's a DIFFERENT MODEL.

6. **"Six mirrored universes must physically exist"** — NO. Only SHADOW_OUTCOME requires independent physical existence. Everything else is a population filter or cross-join on existing data.

7. **"The existing 45 questions are sufficient"** — NO. They are the initial vocabulary. The architecture must support future human-originated questions without requiring structural changes.

8. **"A new primitive is required for every new analytical concept"** — NO. Most shadow analysis uses existing primitives on new populations. Only PAIRED cross-side comparison genuinely requires a new primitive.

9. **"Shadow evidence should be weighted equally with Live evidence"** — NO. Shadow evidence has known limitations (no slippage, simplified geometry, model-generated exits). It should be explicitly discounted in ranking/governance.

10. **"The research_engine/v10/shadow/ module IS the dual-universe shadow system"** — NO. That module is a CANDIDATE TESTING tool (retroactive what-if on closed trades). The dual-universe shadow system consumes data from `core/shadow_trades.py` (runtime bar-by-bar simulation). They are architecturally separate.

---

## FINAL ARCHITECTURAL TEST — 10 Questions

### 1. Can Live and Shadow be structurally analogous without pretending they are the same evidence?

**YES.** Both produce populations of flat records with `r_multiple` fields. The same primitives operate on both. The semantic difference is preserved through:
- Universe provenance (which universe sourced the data)
- `evidence_source` field in findings (LIVE / COUNTERFACTUAL / CROSS_SIDE)
- Contract declarations (different contracts, different limitations)
- Governance rules (shadow evidence discounted in promotion decisions)

### 2. Can the architecture answer questions about what the bot DID?

**YES.** This is the existing Live architecture — 45 questions already operational. Unchanged by shadow additions.

### 3. Can it answer questions about what the bot COULD HAVE DONE?

**YES.** Shadow questions (SD-*, SS-*, SED-*, etc.) operate on SHADOW_OUTCOME populations containing counterfactual R-multiples for detected-but-not-necessarily-executed opportunities.

### 4. Can it answer questions about what the bot MISSED?

**YES.** Population `SHADOW_FROM_NO_TRADE` combined with shadow R > 0 directly identifies missed profitable opportunities. Cross-side question X-002 classifies these per rejection stage.

### 5. Can it answer questions about what the bot correctly REJECTED?

**YES.** Same population (`SHADOW_FROM_NO_TRADE`) with shadow R < 0 identifies correct rejections. X-002 quantifies per stage.

### 6. Can a human observation become a governed research question?

**YES** — with defined process: Observation → Classification (LIVE/SHADOW/CROSS) → Population verification → Primitive selection → Question formalisation (NewEngineQuestion contract) → Standard execution pipeline. Does not require NLP — can be manually formalised.

### 7. Can findings generate new research questions without being trapped inside the original 45?

**YES.** The `ResearchFinding.research_gaps` field already exists for this purpose. The architecture supports new question IDs (H-nnn for human, SD-nnn for shadow, X-nnn for cross-side) without modifying existing questions.

### 8. Can the resulting research eventually produce governed candidates?

**YES.** The existing POPULATION_FILTER candidate mechanism is already population-agnostic. A shadow finding → proposal → candidate → experiment against shadow population follows the exact same governed path. No trading code modification needed.

### 9. Can all of this happen without modifying the live trading system?

**YES.** Every proposed change is within `research_engine/v10/`. No file in `core/`, `execution/`, `risk/`, `strategy/`, or `data/` requires modification. The research engine READS shadow data — it never WRITES to trading infrastructure.

### 10. What is the SINGLE most important architectural decision that must be settled before implementation begins?

**The number of physical Shadow universes.**

The v1 spec proposed 3 physical + 3 derived. This v2 review concludes that **ONE physical universe (SHADOW_OUTCOME)** is correct, with everything else served by populations and cross-joins.

This decision must be explicitly approved because:
- It determines how much builder code is written
- It determines whether shadow entry geometry gets its own population model or is a field within SHADOW_OUTCOME
- It determines the complexity of the maintenance surface
- Getting it wrong means either over-engineering (too many universes) or under-powering (can't express needed analyses)

**Recommendation: Start with ONE. If analytical needs emerge that cannot be expressed as populations of that one universe, add a second then. Do not pre-build what isn't yet needed.**

---

*End of v2 specification. No code modified. No runtime affected. Implementation awaits human review and approval of open decisions.*
