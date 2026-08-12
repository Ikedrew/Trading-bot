# V10 SHADOW — FINAL DESIGN SPECIFICATION

**Date:** 2026-07-27  
**Status:** DESIGN ONLY — awaiting approval before implementation  
**Based on:** Confirmed audit findings (shadow_horizon_final_confirmation.md)  

---

## 1. FINAL SHADOW ARCHITECTURE

```
                    ONE OPPORTUNITY
                          │
                   V10Pipeline.process()
                          │
                   PipelineResult
                   (all 7 layers complete)
                          │
         ┌────────────────┴────────────────┐
         │                                 │
      LIVE V10                         SHADOW V10
         │                                 │
  If EXECUTE:                      For all decisions where
  Broker → Position →              V10 geometry exists:
  Trade Management →               
  Realised Outcome                 ┌─────────────────────────┐
         │                         │  V10 SHADOW             │
         │                         │  (selected horizon,     │
         │                         │   V10 SL/TP/direction)  │
         │                         └─────────────────────────┘
         │                                 +
         │                         ┌─────────────────────────┐
         │                         │  HORIZON EVALUATIONS    │
         │                         │  (structure geometry     │
         │                         │   per eligible horizon)  │
         │                         └─────────────────────────┘
         │                                 │
         │                          Shadow Outcomes
         │                          (R, MFE, MAE, exit)
         │                                 │
         └──────── SAME entity_id ─────────┘
```

---

## 2. FINAL LINEAGE / ID DIAGRAM

```
entity_id = f"{symbol}_{bar_time}"    [CANONICAL — one per opportunity]
    │
    ├── DecisionTrace
    │     observation_id (from OpportunityAssessment)
    │     v10_market_state, v10_opportunity, v10_strategy,
    │     v10_horizon, v10_entry, v10_risk, v10_execution
    │     action = EXECUTE | NO_TRADE
    │     terminal_stage, terminal_reason
    │
    ├── Live Execution (only if EXECUTE + guards pass + broker fills)
    │     correlation_id = COR-{date}-{cycle}-{symbol}-{hash}
    │     trade_id = pos_{deal}
    │     → TradeRecord → Outcome Universe
    │
    ├── V10 Shadow Observation (when Entry geometry is VALID)
    │     shadow_trade_id = f"shadow_{cycle_id}_{symbol}"
    │     shadow_type = "V10_PRIMARY"
    │     v10_selected_horizon = PipelineResult.horizon.horizon_type
    │     horizon_selection_status = "SELECTED"
    │     geometry: V10 Entry SL/TP/direction
    │     → Shadow Outcome (R, MFE, MAE)
    │
    └── Horizon Evaluations (per eligible alternative horizon)
          shadow_trade_id = f"hshadow_{cycle_id}_{symbol}_{HORIZON}"
          shadow_type = "HORIZON_ALTERNATIVE"
          v10_selected_horizon = (what V10 chose — same for all)
          horizon_selection_status = "ALTERNATIVE"
          evaluated_horizon = this specific horizon
          geometry: structure-based (horizon_trade_builder)
          → Shadow Outcome (R, MFE, MAE)
```

### Key Rules

- **ONE entity_id** per opportunity. Never duplicated.
- **ONE V10 Shadow** per opportunity (when geometry available). Uses V10's own SL/TP.
- **N Horizon Evaluations** per opportunity (one per eligible alternative). Use structure geometry.
- All observations carry `v10_selected_horizon` showing what V10 chose.
- All observations carry `horizon_selection_status` showing their role.
- Horizon evaluations are NOT separate opportunities. They are evaluation branches of the SAME opportunity.

---

## 3. SHADOW OBSERVATION CONTRACT

### Required Fields (every shadow record)

```
# ─── IDENTITY (links to canonical opportunity) ─────────────
entity_id: str                      # Canonical opportunity identity
shadow_trade_id: str                # Unique per shadow observation
symbol: str
cycle_id: int
correlation_id: str

# ─── CLASSIFICATION ─────────────────────────────────────────
shadow_type: str                    # "V10_PRIMARY" | "HORIZON_ALTERNATIVE"
evidence_source: str                # Always "COUNTERFACTUAL"

# ─── V10 CONTEXT (what V10 decided for THIS opportunity) ───
v10_selected_horizon: str           # What V10 HorizonEngine chose
v10_rejection_stage: str            # Where Live V10 stopped ("" if EXECUTE)
v10_action: str                     # "EXECUTE" | "NO_TRADE"

# ─── THIS OBSERVATION'S HORIZON ────────────────────────────
evaluated_horizon: str              # Which horizon THIS shadow evaluates
horizon_selection_status: str       # "SELECTED" | "ALTERNATIVE"
horizon_geometry_source: str        # "V10_ENTRY_ENGINE" | "STRUCTURE_BASED"

# ─── ENTRY GEOMETRY (frozen at creation) ────────────────────
direction: str
entry_price: float
stop_loss: float
take_profit: float
position_size: float
risk_distance: float
reward_risk_ratio: float

# ─── DECISION CONTEXT (from V10 pipeline) ──────────────────
strategy_id: str
pattern: str
score: float
regime: str
market_phase: str

# ─── COUNTERFACTUAL OUTCOME (from shadow lifecycle) ─────────
r_multiple: float
mfe_r: float
mae_r: float
exit_reason: str                    # "stop_loss" | "take_profit" | "max_bars_timeout"
bars_held: int
exit_price: float

# ─── LINEAGE QUALITY ───────────────────────────────────────
has_entity_id: bool
has_v10_geometry: bool              # True for V10_PRIMARY, False for HORIZON_ALTERNATIVE
```

---

## 4. REJECTION-STAGE MATRIX

| Live V10 Stage | V10 Geometry Available? | V10 Shadow Created? | Horizon Alternatives Created? | Evidence Classification |
|----------------|------------------------|--------------------|-----------------------------|----------------------|
| **EXECUTE** (approved) | YES (OrderIntent has SL/TP/vol) | YES — existing Primary Shadow | YES — per eligible horizon | VALID |
| **Risk** rejected | YES (Entry computed valid SL/TP) | **YES — NEW** | YES — per eligible horizon | VALID |
| **Execution** rejected | YES (Risk approved, Entry valid) | **YES — NEW** | YES — per eligible horizon | VALID |
| **Entry** rejected (geometry invalid) | **NO** (SL/TP = 0, direction unclear) | NO | YES — structure geometry only | CONDITIONAL |
| **Strategy** rejected (NONE) | **NO** (no entry method available) | NO | PARTIAL — if direction available from Opportunity | CONDITIONAL |
| **Opportunity** rejected (INVALID) | **NO** (no directional bias) | NO | NO — no direction means no meaningful trade | NON_REPLAYABLE |

### Evidence Labels

| Classification | Meaning | Research Usability |
|----------------|---------|-------------------|
| **VALID** | V10 geometry available. Counterfactual represents what V10's own trade plan would have done. | Full research evidence |
| **CONDITIONAL** | Structure-based geometry only. Counterfactual represents what a simplified model would have done. Limitation must travel with evidence. | Usable with documented limitation |
| **NON_REPLAYABLE** | Insufficient information to construct a meaningful counterfactual. | Excluded from shadow research populations |

---

## 5. HORIZON EVALUATION CONTRACT

### Semantic Distinctions (Must Never Be Conflated)

| Concept | Definition | Source | Example |
|---------|-----------|--------|---------|
| **A. V10 Selected** | The horizon V10's HorizonEngine actually chose | `PipelineResult.horizon.horizon_type` | "SCALP" |
| **B. Structurally Eligible** | Horizons that pass the observation classifier's eligibility rules | `horizon_classifier.classify_horizons()` | ["SCALP", "INTRADAY"] |
| **C. Research Eligible** | Horizons where sufficient structural data exists to construct SL/TP | Structure data availability check | ["SCALP", "INTRADAY", "EXTENDED"] |
| **D. Actually Simulated** | Horizons for which a shadow trade was opened and ran to completion | Persisted shadow records | ["SCALP", "INTRADAY"] |
| **E. Valid Outcome** | Simulated horizons that produced a non-null R-multiple | Completed shadow trades | ["SCALP": -0.52, "INTRADAY": +0.31] |

### How These Relate

```
Research Eligible ⊇ Structurally Eligible ⊇ V10 Selected (always 1)
Actually Simulated ⊆ Research Eligible
Valid Outcome ⊆ Actually Simulated
```

### Eligibility Logic (Three Tiers)

**Production Eligibility** (used by observation classifier for shadow creation today):
- SCALP: always eligible
- INTRADAY: htf_alignment ≥ 0.5, structure_quality ≥ 0.5
- EXTENDED: htf_alignment ≥ 0.7, TRENDING, BOS, quality ≥ 0.6

**Research Eligibility** (proposed — used for alternative horizon simulation):
- SCALP: always eligible (M5 candle data always available)
- INTRADAY: M15 support/resistance data available (can construct SL)
- EXTENDED: H1 swing high/low data available (can construct SL)

**Research eligibility is legitimate because:** The counterfactual question "what would EXTENDED have produced?" requires only that we can construct a meaningful entry geometry (direction + SL + TP). The PRODUCTION eligibility asks "should we TRUST this horizon enough to risk real capital?" — a much higher bar that's irrelevant for research observation.

---

## 6. POPULATION MODEL

### Shadow Outcome Universe Populations

| Population | Filter | Purpose |
|------------|--------|---------|
| ALL_SHADOW_OUTCOMES | All records with valid R | Total counterfactual pool |
| V10_PRIMARY | shadow_type = "V10_PRIMARY" | What V10's own geometry produces |
| HORIZON_SCALP | evaluated_horizon = "SCALP" AND shadow_type = "HORIZON_ALTERNATIVE" | Structure-based SCALP counterfactual |
| HORIZON_INTRADAY | evaluated_horizon = "INTRADAY" AND shadow_type = "HORIZON_ALTERNATIVE" | Structure-based INTRADAY counterfactual |
| HORIZON_EXTENDED | evaluated_horizon = "EXTENDED" AND shadow_type = "HORIZON_ALTERNATIVE" | Structure-based EXTENDED counterfactual |
| SHADOW_SELECTED | horizon_selection_status = "SELECTED" | All shadows representing V10's chosen path |
| SHADOW_ALTERNATIVE | horizon_selection_status = "ALTERNATIVE" | All shadows representing unchosen alternatives |
| SHADOW_WINS | r_multiple > 0 | Profitable counterfactuals |
| SHADOW_LOSSES | r_multiple <= 0 | Unprofitable counterfactuals |
| SHADOW_VALID_GEOMETRY | has_v10_geometry = True | Highest-confidence subset |
| SHADOW_CONDITIONAL | has_v10_geometry = False | Structure-based (labelled limitation) |

### How Populations Map to Research Contracts

| Shadow Research Contract | Primary Population | Evidence Type |
|--------------------------|-------------------|---------------|
| Shadow-Outcome | ALL_SHADOW_OUTCOMES | R-multiple, MFE, MAE, exit |
| Shadow-Execution | V10_PRIMARY (has V10 entry geometry) | Entry price, SL, TP, direction |
| Shadow-Risk | V10_PRIMARY (has V10 risk params) | Risk distance, R:R, position size |
| Shadow-Decision | Join entity_id → Decision Universe | V10 action, terminal_stage, strategy |
| Shadow-Market | Join entity_id → Market Universe | Regime, volatility, HTF alignment |
| Shadow-Strategy | Join entity_id → Strategy Universe | Family, confidence, conditions |

No new top-level universes needed. Shadow-Decision/Market/Strategy are governed joins. Shadow-Execution and Shadow-Risk are field projections within V10_PRIMARY records.

---

## 7. QUARANTINE CLASSIFICATION

| Data Category | Records | Classification | Live Universe? | Shadow Universe? | Research Questions? | Proposals? |
|---------------|---------|---------------|---------------|-----------------|-------------------|-----------|
| Primary shadows (V10 geometry, current) | 952 | **VALID** | No | Yes (V10_PRIMARY) | Full evidence | Yes |
| Horizon shadows with new fields (future) | 0 (not yet produced) | **VALID** | No | Yes (HORIZON_*) | Full evidence | Yes |
| Horizon shadows WITHOUT new fields (historical) | 3,201 | **CONDITIONAL** | No | Yes (labelled `horizon_selection_status=UNKNOWN`) | Yes — with limitation: "cannot distinguish selected from alternative" | Yes (lower evidence weight) |
| Test contamination | 1,633 | **INVALID** | No | No (excluded by builder) | No | No |
| Live decision traces | 15,865 | **VALID** | Yes (Decision Universe) | Via join only | Yes | Yes |
| Live execution outcomes | 94 | **VALID** | Yes (Execution/Outcome) | Via join for comparison | Yes | Yes |

### Quarantine Rules

- **VALID** data: enters research normally, full evidence weight
- **CONDITIONAL** data: enters research with explicit limitation field in finding; lower confidence weight in ranking; must not be mixed with VALID without labelling
- **INVALID** data: permanently excluded from all research populations

---

## 8. RESEARCH ENGINE INTEGRATION

### Finding Schema Addition

```python
# Already implemented:
evidence_source: str = "REALISED"  # "REALISED" | "COUNTERFACTUAL" | "CROSS_SIDE"

# Needed for Shadow findings:
shadow_geometry_type: str = ""     # "V10_GEOMETRY" | "STRUCTURE_BASED" | "" (for Live)
data_quality_classification: str = ""  # "VALID" | "CONDITIONAL" | ""
```

### How Shadow Questions Consume Populations

```
SD-001 (Shadow Expectancy):
    Population: ALL_SHADOW_OUTCOMES
    Primitive: expectancy
    evidence_source: COUNTERFACTUAL

SD-004 (Rejection Stage Cost):
    Population: ALL_SHADOW_OUTCOMES joined to Decision (terminal_stage)
    Primitive: segmentation by terminal_stage
    evidence_source: COUNTERFACTUAL

SD-005 (Horizon Comparison):
    Population: SHADOW_ALTERNATIVE (grouped by evaluated_horizon)
    Primitive: comparison by evaluated_horizon
    evidence_source: COUNTERFACTUAL

NEW: "Is V10's horizon selection optimal?"
    Population: SHADOW_SELECTED vs SHADOW_ALTERNATIVE (same entity_ids)
    Primitive: comparison (selected R vs alternative R)
    evidence_source: COUNTERFACTUAL
```

---

## 9. MINIMUM CODE CHANGES

| # | Change | File | Lines | Risk |
|---|--------|------|-------|------|
| 1 | Add `v10_selected_horizon`, `horizon_selection_status`, `v10_rejection_stage`, `v10_action`, `horizon_geometry_source`, `evaluated_horizon` to ShadowTrade | `core/shadow_trades.py` | ~10 | LOW |
| 2 | Update `open_trade()` to accept new kwargs | `core/shadow_trades.py` | ~6 | LOW |
| 3 | Include new fields in `_build_truth_record()` (decision_snapshot domain) | `core/shadow_trades.py` | ~8 | LOW |
| 4 | Fix scanner_adapter exception path entity_id | `core/v10/scanner_adapter.py` | ~5 | ZERO |
| 5 | Create V10-geometry shadow for NO_TRADE when entry valid | `core/runtime/live_scanner.py` (new section) | ~30 | LOW |
| 6 | Pass `v10_selected_horizon` + `horizon_selection_status` to existing horizon shadow creation | `core/runtime/live_scanner.py` Phase 4C.3 | ~5 | LOW |
| 7 | Add `research_mode` parameter to horizon_classifier | `core/horizon/horizon_classifier.py` | ~15 | LOW |
| 8 | Update `ShadowOutcomeUniverseBuilder._normalise()` for new fields | `research_engine/v10/universes/shadow_outcome_universe.py` | ~12 | LOW |

**Total: ~91 lines across 5 files. No logic changes to V10 pipeline.**

---

## 10. DATA MIGRATION REQUIREMENTS

**None.** All changes are additive:
- New fields have empty/default values for historical records
- Builder classifies records WITH new fields as current; WITHOUT as `horizon_selection_status = "UNKNOWN"` (CONDITIONAL)
- No existing records are modified or deleted
- No schema migration needed — JSONL is schema-flexible

---

## 11. WHAT MUST NOT CHANGE IN LIVE V10

| Component | Why Untouchable |
|-----------|----------------|
| `V10Pipeline.process()` | The pipeline logic is correct. Shadow reads its output, never modifies its flow. |
| `V10 HorizonEngine` | Authoritative Live selection. Shadow observes it, never replaces it. |
| `V10 EntryEngine` | Produces geometry that Shadow uses. Must remain stable. |
| `V10 RiskEngine` | Determines approval/rejection. Shadow observes the result. |
| `V10 ExecutionEngine` | Final gate. Shadow observes. |
| `engine_execution_handler.py` | Existing Primary Shadow path for EXECUTE. Preserved. |
| `bar_provider.py` shadow evaluation | Existing bar-by-bar lifecycle engine. Preserved. |
| `ShadowTradeEngine.evaluate_bar()` | Lifecycle mechanics. Unchanged. |

---

## 12. WHAT CAN SAFELY BE IMPLEMENTED NOW

| Change | Why Safe |
|--------|---------|
| All 8 items in Section 9 | All observational. All wrapped in try/except. Cannot affect trading. New fields are optional kwargs with defaults. |

---

## 13. WHAT SHOULD REMAIN FUTURE RESEARCH INFRASTRUCTURE

| Item | Why Defer |
|------|-----------|
| Cross-side comparison primitive (paired Live R vs Shadow R) | Need more EXECUTE + V10 Shadow pairs to accumulate first |
| "Force horizon" V10 pipeline mode (re-run pipeline with different horizon) | Would produce TRUE V10 alternatives but requires pipeline modification |
| Shadow-specific confidence model in ranking | Need shadow findings to calibrate appropriate weighting |
| Human question intake interface | Manual formalisation works initially |
| Automatic question generation from shadow findings | Optimisation — not blocking |

---

## FINAL ANSWER

> **Does this architecture give the Research Engine enough information to determine both what V10 actually did and what V10 could plausibly have done differently, with enough lineage and evidence classification to generate evidence-backed findings, proposals and candidates?**

**YES.**

Specifically:

| Question | Answerable? | How |
|----------|-------------|-----|
| What did V10 do? | YES | DecisionTrace with full 7-layer V10 reasoning (15,865 records) |
| What did V10's selected horizon produce? | YES | V10_PRIMARY shadow with V10 geometry (952 existing + new Risk/Execution-rejected) |
| What would alternative horizons have produced? | YES | HORIZON_ALTERNATIVE shadows with structure geometry |
| Which horizon does V10 select? | YES | `v10_selected_horizon` field in all shadow records |
| Is V10's selection optimal? | YES | Compare SHADOW_SELECTED vs SHADOW_ALTERNATIVE R-multiples for same entities |
| Which rejection stages destroy value? | YES | Shadow R segmented by `v10_rejection_stage` |
| What is the opportunity cost of Risk gates? | YES | V10_PRIMARY shadow for Risk-rejected decisions |
| Can findings become proposals? | YES | Existing Finding→Feedback→Knowledge→Proposal pipeline (proven) |
| Can proposals become candidates? | YES | Existing POPULATION_FILTER candidate system (proven: EM-001) |
| Is evidence correctly labelled? | YES | `evidence_source=COUNTERFACTUAL`, `shadow_geometry_type`, `data_quality_classification` |

The lineage is unambiguous (entity_id joins Live ↔ Shadow), the evidence is classified (REALISED vs COUNTERFACTUAL vs CONDITIONAL), and the governance is preserved (human approval required for any production change).

---

*Specification complete. Awaiting approval before implementation.*
