# V10 SHADOW / HORIZON — FINAL ARCHITECTURAL CONFIRMATION

**Date:** 2026-07-27  
**Type:** Evidence-based confirmation audit  
**Status:** READ-ONLY — no implementation until approved  

---

## 2. HORIZON LOCATION CONFIRMED

### A. V10 HorizonEngine

| Property | Confirmed From Code |
|----------|-------------------|
| File | `core/v10/horizon_engine.py` |
| Function | `assess_horizon(state, opportunity, strategy)` |
| Pipeline position | Layer 4 of `V10Pipeline.process()` |
| Executes unconditionally | YES — no conditional skip |
| Selects ONE horizon | YES — returns single `HorizonDecision` with `horizon_type` |
| Entry uses selected horizon | YES — `build_entry_decision(..., horizon)` receives it |
| Risk evaluates resulting geometry | YES — `assess_risk(..., entry, ...)` uses Entry output |
| Authoritative for Live V10 | YES |

### B. Observation Horizon Classifier

| Property | Confirmed From Code |
|----------|-------------------|
| File | `core/horizon/horizon_classifier.py` |
| Function | `classify_horizons(...)` |
| Pipeline position | OUTSIDE V10Pipeline — runs separately in live_scanner |
| Input source | Raw scoring components (htf_alignment, h4_alignment, etc.) — NOT V10 objects |
| Purpose | Determines eligibility for shadow trade creation |
| Used by | `live_scanner.py` Phase 4C.3 for horizon shadow creation |
| Authoritative for V10? | **NO** — this is an independent observation system |

**CONFIRMED: These are two separate systems. The V10 HorizonEngine is authoritative. The observation classifier is NOT the V10 horizon selection mechanism.**

---

## 3. CRITICAL PIPELINE CONTROL FLOW

### CONFIRMED: V10Pipeline.process() executes ALL stages unconditionally.

Evidence: The pipeline source shows sequential calls with NO conditional returns, NO `if ... return early`, NO stage-skip logic:

```python
market_state = build_v10_market_state(...)      # Always
opportunity = assess_opportunity(market_state)   # Always
strategy = select_strategy(...)                  # Always
horizon = assess_horizon(...)                    # Always
entry = build_entry_decision(...)                # Always
risk = assess_risk(...)                          # Always
execution = build_execution_decision(...)        # Always
return PipelineResult(all 7 results)             # Always
```

### Stage Execution Table

| Stage | Executes After Earlier Rejection? | Output Available? | Output Valid? | Persisted in DecisionTrace? |
|-------|----------------------------------|-------------------|---------------|---------------------------|
| MarketState | Always first | YES | Always valid | YES (v10_market_state) |
| Opportunity | Always runs | YES | Always valid (state may be "INVALID") | YES (v10_opportunity) |
| Strategy | Always runs | YES | Always valid (family may be "NONE") | YES (v10_strategy) |
| Horizon | Always runs | YES | Always valid (selects horizon regardless) | YES (v10_horizon) |
| Entry | Always runs | YES | **INVALID** when Opportunity="INVALID" or Strategy="NONE" | YES (v10_entry) |
| Risk | Always runs | YES | **Immediately rejects** when Entry is INVALID | YES (v10_risk) |
| Execution | Always runs | YES | **Immediately rejects** when Risk rejected or Entry invalid | YES (v10_execution) |

### Conditions Where Entry Geometry is UNAVAILABLE

Entry returns INVALID (zero geometry) when:
1. `opportunity.opportunity_state == "INVALID"` — no directional bias available
2. `strategy.strategy_family == StrategyFamily.NONE.value` — no entry method selectable
3. `direction == TradeDirection.NONE` — no bias resolved
4. Risk distance = 0 — invalid geometry
5. R:R < 1.0 — geometry too poor

**Entry IS VALID (has real SL/TP/direction) when:**
- Opportunity is valid (has directional bias)
- Strategy is selected (has entry method)
- Structural levels are available (stop placement possible)

This means Entry is VALID for opportunities rejected at: **Entry (geometry sub-check), Risk, or Execution stages.**

---

## 4. PIPELINE RESULT CONTENT BY REJECTION STAGE

| Rejection Stage | Opportunity | Strategy | Horizon | Entry | Risk | Execution | **Shadow-Usable Geometry?** |
|-----------------|-------------|----------|---------|-------|------|-----------|---------------------------|
| **Opportunity** (INVALID) | state=INVALID | family=NONE | Still selects (defaults to SCALP) | **INVALID** (no direction) | Rejects | Rejects | **NO** — no direction, no geometry |
| **Strategy** (NONE) | VALID | family=NONE | Still selects | **INVALID** (no entry method) | Rejects | Rejects | **NO** — no entry method |
| **Entry** (geometry invalid) | VALID | VALID | VALID | **INVALID** (R:R<1, no structure) | Rejects | Rejects | **NO** — geometry itself is broken |
| **Risk** (rejected) | VALID | VALID | VALID | **VALID** (has SL/TP/direction) | rejected=True | Rejects | **YES** — full V10 geometry available |
| **Execution** (rejected) | VALID | VALID | VALID | **VALID** | approved=True | rejected | **YES** — full V10 geometry available |
| **Approved** (EXECUTE) | VALID | VALID | VALID | **VALID** | approved=True | approved=True | **YES** — used by Primary Shadow already |

### Summary

**V10 geometry is available for shadow creation when rejection occurs at Risk or Execution stages only.** For Opportunity/Strategy/Entry rejections, the Entry geometry is INVALID — there is no meaningful SL/TP to simulate.

---

## 5. SHADOW BRANCH LOCATION

### Current Creation Paths (confirmed from prior audit)

| Path | Location | Trigger | Geometry Source |
|------|----------|---------|-----------------|
| Primary Shadow | `engine_execution_handler.py` line ~180 | EXECUTE only | V10 Entry/Risk (OrderIntent) |
| Horizon Shadows | `live_scanner.py` Phase 4C.3 (~line 718) | ALL pattern-detected (EXECUTE + NO_TRADE) | `horizon_trade_builder.py` (INDEPENDENT of V10) |

### Where Shadow Branch SHOULD Occur

The desired branch point is: **After V10Pipeline.process() returns, for any decision where `PipelineResult.entry.entry_status != INVALID`.**

This means:
- After the V10 pipeline completes (all 7 layers run)
- The shadow uses the ACTUAL PipelineResult data
- It can access the V10 HorizonEngine's selected horizon
- It can access the V10 EntryEngine's computed geometry
- It can access the V10 RiskEngine's assessment

**The branch does NOT need to occur "at" Horizon.** It occurs AFTER the full pipeline, using the pipeline's own outputs. This is architecturally cleaner than intercepting mid-pipeline.

### Can This Be Implemented Without Changing Live V10?

**YES.** The `_new_result["v10_pipeline_result"]` already contains the complete `PipelineResult` with all 7 layer outputs. Shadow creation simply reads from it. No pipeline interception needed.

---

## 6. PRIMARY SHADOW — CONFIRMED

| Question | Answer | Evidence |
|----------|--------|----------|
| Created only for EXECUTE? | YES | `engine_execution_handler.py` only called from EXECUTE path |
| Uses V10's actual geometry? | YES | `_intent.sl`, `_intent.tp`, `_intent.volume` from OrderIntent |
| Uses V10 selected horizon? | YES (indirectly) | Entry/Risk used the selected horizon's movement expectations |
| Shares entity_id? | YES | `entity_id=new_result.get("entity_id", "")` |
| Represents "what happened to V10's exact trade plan"? | **YES** — this is precisely what it is |

---

## 7. HORIZON SHADOW — CONFIRMED

| Question | Answer | Evidence |
|----------|--------|----------|
| Uses V10 HorizonEngine? | **NO** | Uses `core/horizon/horizon_classifier.py` (SEPARATE system) |
| Uses V10 EntryEngine? | **NO** | Uses `core/horizon/horizon_trade_builder.py` (independent geometry) |
| Uses V10 RiskEngine? | **NO** | Uses fixed 0.01 lot size |
| SL/TP geometry independently constructed? | **YES** | M5 candle / M15 support-resistance / H1 swings + fixed R:R ratios |
| Represents "V10 horizon alternatives"? | **NO** — it represents "structure-based counterfactual trades" | The geometry has no relationship to what V10's Entry/Risk engines would produce |

**This is the critical architectural truth:** Current horizon shadows do NOT represent "what V10 would have done with a different horizon." They represent "what a simplified structure-based trading model would have done." These are different counterfactual contracts.

---

## 8. WHY 18 EXTENDED RECORDS — PROVEN FROM CODE

From `core/horizon/horizon_profiles.py`:

```python
EXTENDED = HorizonProfile(
    min_htf_alignment=0.7,           # Strong HTF alignment required
    requires_trend=True,             # Only in TRENDING H4 regime
    requires_bos=True,               # Must have H1 BOS confirmed
    requires_structure_quality=0.6,  # High M15 quality
)
```

From `core/horizon/horizon_classifier.py`, the `_evaluate_horizon` function checks:
- `profile.requires_trend` → H4 regime must be TRENDING
- `profile.requires_bos` → H1 BOS must be confirmed
- `profile.min_htf_alignment` → htf_alignment score must be ≥ 0.7
- `profile.requires_structure_quality` → market_quality must be ≥ 0.6

**Answer: A. Only 18 opportunities genuinely met ALL EXTENDED eligibility requirements in the observation classifier.**

This is CORRECT according to the current production eligibility rules. The classifier's requirements for EXTENDED are strict — they reflect conditions under which a multi-day hold would be appropriate for LIVE trading.

For RESEARCH purposes, the question "what would EXTENDED have produced?" is answerable whenever H1 swing structure data exists (to construct an SL). The LIVE eligibility requirements are more restrictive than what research needs.

**The 18 is NOT a bug. It IS a design constraint appropriate for Live but overly restrictive for research counterfactuals.**

---

## 9. HORIZON RESEARCH — WHAT THE DATA MODEL NEEDS

To distinguish selected vs alternative horizons, the shadow record needs:

| Field | Purpose | Values |
|-------|---------|--------|
| `v10_selected_horizon` | What V10's HorizonEngine actually chose | "SCALP" / "INTRADAY" / "EXTENDED" |
| `horizon_selection_status` | Whether this shadow represents the selected or an alternative | "SELECTED" / "ALTERNATIVE" / "UNKNOWN" (legacy) |

These two fields allow the Research Engine to construct:
- "What did V10 select?" → filter on `horizon_selection_status == "SELECTED"`
- "What would alternatives have produced?" → filter on `horizon_selection_status == "ALTERNATIVE"`
- "Compare selected vs alternatives" → group by status, compare R-multiple

**Neither field currently exists in the shadow records.** This is the minimum addition required.

---

## 10. NO_TRADE COUNTERFACTUALS — HOW FAR SHADOW CAN CONTINUE

| Live Rejection Stage | Can Shadow Continue? | Available Geometry | Confidence |
|---------------------|---------------------|-------------------|------------|
| **Opportunity** (INVALID) | **NO** | No direction, no strategy → no meaningful geometry | — |
| **Strategy** (NONE) | **NO** | No entry method → cannot construct SL/TP | — |
| **Entry** (geometry invalid) | **PARTIAL** — horizon shadow builder CAN construct independent geometry | Structure-based only (NOT V10 geometry) | LOW — simplified model |
| **Risk** (rejected) | **YES** | V10 Entry geometry is VALID (SL/TP/direction computed) | HIGH — uses actual V10 plan |
| **Execution** (rejected) | **YES** | Same as Risk — full V10 geometry | HIGH |
| **Pre-engine** (kill switch, daily loss, session, etc.) | **NO** | V10 pipeline never ran | — |

### What This Means

- **Risk-rejected + Execution-rejected** decisions have FULL V10 geometry. Shadow can use it directly. This is the highest-quality counterfactual.
- **Entry-rejected** decisions have no V10 geometry, but the horizon_trade_builder can construct structure-based alternatives. This is a lower-confidence counterfactual.
- **Opportunity/Strategy rejected** decisions lack directional information. Shadow cannot meaningfully continue these.

---

## 11. LINEAGE / IDENTITY

### Confirmed Identity Hierarchy

```
entity_id = f"{symbol}_{bar_time}"  [CANONICAL — from scanner_adapter]
    │
    ├── observation_id [from OpportunityAssessment — V10 internal]
    │
    ├── DecisionTrace (persisted to logs/decision_trace/)
    │     Contains: v10_market_state, v10_opportunity, v10_strategy,
    │               v10_horizon, v10_entry, v10_risk, v10_execution
    │
    ├── Live Execution (if EXECUTE)
    │     correlation_id = COR-{date}-{cycle}-{symbol}-{hash}
    │     trade_id = pos_{deal}
    │
    ├── Primary Shadow (if EXECUTE)
    │     trade_id = shadow_{cycle}_{symbol}
    │     correlation_id = COR-{date}-{cycle}-{symbol}-{hash}
    │
    └── Horizon Shadows (if pattern detected + eligible)
          trade_id = hshadow_{cycle}_{symbol}_{HORIZON}
          correlation_id = HORIZON-{cycle}-{symbol}
```

**All branches share the SAME entity_id.** This is the authoritative join key.

### Correct Representation

```
ONE OPPORTUNITY (entity_id = "EURUSD_1786445100")
    │
    ├── LIVE: V10 selected SCALP → Risk approved → Broker filled → R = +0.15
    │
    ├── SHADOW (V10 geometry): SCALP with V10 SL/TP → R = +0.05
    │
    ├── SHADOW (horizon): SCALP with structure SL/TP → R = -0.52
    │
    ├── SHADOW (horizon): INTRADAY with structure SL/TP → R = +0.31
    │
    └── SHADOW (horizon): EXTENDED with structure SL/TP → [not created — H1 data insufficient]
```

These are NOT 5 separate opportunities. They are 1 opportunity with multiple observation paths.

---

## 12. RESEARCH UNIVERSE IMPACT

### Current ShadowOutcomeUniverseBuilder Status

| Population | Current Count | Correctly Classified? | Notes |
|------------|--------------|----------------------|-------|
| ALL_SHADOW_OUTCOMES | 4,153 | YES | Excludes test contamination |
| PRIMARY_V10_SHADOW | 952 | **PARTIALLY** — represents V10 geometry correctly but only EXECUTE | Missing: V10 geometry for Risk/Execution-rejected |
| HORIZON_SCALP | 1,824 | **YES but mislabelled** — these are structure-based, not V10-horizon-alternatives | They do NOT represent "what V10 would have done at SCALP" |
| HORIZON_INTRADAY | 1,359 | Same as SCALP — structure-based, not V10 | Same issue |
| HORIZON_EXTENDED | 18 | Same — too restrictive eligibility | Correct under current rules, but research-insufficient |

### What Needs Changing in Research Engine

1. Add `v10_selected_horizon` field to normalised shadow records
2. Add `horizon_selection_status` field
3. Update population logic to distinguish:
   - `PRIMARY_V10_SHADOW` → V10 geometry shadows (currently EXECUTE only, target: EXECUTE + Risk-rejected + Execution-rejected)
   - `HORIZON_SCALP/INTRADAY/EXTENDED` → Structure-based alternatives (correctly labelled as simplified geometry)

### Quarantine Classification

| Data Category | Records | Classification | Research Usability |
|---------------|---------|---------------|-------------------|
| Primary shadows (V10 geometry) | 952 | **VALID** | Full research evidence |
| Horizon shadows (structure geometry) | 3,201 | **CONDITIONAL** | Usable for structure-based research with limitation: "geometry is NOT V10's actual calculation" |
| Test contamination | 1,633 | **INVALID** | Excluded (already handled) |
| Future V10-geometry shadows for NO_TRADE | 0 (not yet produced) | Will be VALID when created | Highest-quality counterfactual |

---

## 14. RESEARCH ENGINE READINESS

| # | Question | Answer | Why |
|---|----------|--------|-----|
| 1 | What happened in Live V10? | **YES** | 15,865 decision traces with full pipeline reasoning |
| 2 | What would have happened to rejected opportunities? | **PARTIAL** | Structure-based horizon shadows exist (3,201). V10-geometry shadows for NO_TRADE do NOT exist yet. |
| 3 | What did the selected V10 horizon produce? | **PARTIAL** | Primary shadow has V10 geometry for EXECUTE (952). No `v10_selected_horizon` field to distinguish. |
| 4 | What would alternative horizons have produced? | **PARTIAL** | Horizon shadows exist but use independent geometry. No `horizon_selection_status` to distinguish selected vs alternative. |
| 5 | Which rejection stages destroy profitable opportunities? | **PARTIAL** | Can segment horizon shadow R by terminal_stage (via entity_id join). BUT uses simplified geometry. |
| 6 | Which horizon has strongest evidence? | **YES** | Can compare SCALP (1,824) vs INTRADAY (1,359) expectancy |
| 7 | Which strategy/regime/session are underperforming? | **YES** | Live segmentation questions operational (45 questions) |
| 8 | Which changes should become proposals? | **YES** | Full Finding→Feedback→Knowledge→Proposal pipeline proven |
| 9 | Can proposals become candidates? | **YES** | Candidate system operational (EM-001 proven) |
| 10 | Can candidates be validated before human approval? | **YES** | Experiment/Validation framework proven |

---

## 15. CURRENT STATE VS DESIRED STATE

| Area | Current | Desired | Change Required |
|------|---------|---------|-----------------|
| V10-geometry shadow for EXECUTE | EXISTS (952) | Same | NONE |
| V10-geometry shadow for Risk-rejected | DOES NOT EXIST | V10 SL/TP/direction available → create shadow | Add creation path in live_scanner |
| V10-geometry shadow for Execution-rejected | DOES NOT EXIST | Same as Risk | Same change |
| V10-geometry shadow for Entry-rejected | CANNOT EXIST | Accept limitation — geometry is INVALID | NONE (architectural impossibility) |
| V10-geometry shadow for Opportunity/Strategy-rejected | CANNOT EXIST | Accept limitation | NONE |
| `v10_selected_horizon` field | DOES NOT EXIST | Present in all shadow records | Add to ShadowTrade dataclass + open_trade |
| `horizon_selection_status` field | DOES NOT EXIST | SELECTED / ALTERNATIVE / UNKNOWN | Same |
| EXTENDED shadow observations | 18 | Hundreds (research-eligible relaxation) | Add research_mode to classifier |
| Research Engine populations | Correct but missing fields | Updated with new classification fields | Update builder normalisation |
| Historical data | Structure-based geometry | Remains usable as CONDITIONAL evidence | Label, don't delete |
| entity_id coverage | 78% | Improved going forward (fix exception path) | 5-line fix |

---

## 17. FINAL DECISION REPORT

### A. CONFIRMED FACTS

1. V10Pipeline.process() executes ALL 7 layers unconditionally — CONFIRMED from code
2. PipelineResult contains all 7 layer outputs regardless of approval — CONFIRMED
3. V10 HorizonEngine is Layer 4 and selects ONE horizon — CONFIRMED
4. Entry geometry is VALID when rejected at Risk or Execution — CONFIRMED
5. Entry geometry is INVALID when rejected at Opportunity/Strategy — CONFIRMED
6. Primary Shadow uses V10's actual geometry — CONFIRMED
7. Horizon Shadows use INDEPENDENT simplified geometry — CONFIRMED
8. entity_id is deterministic and shared across all paths — CONFIRMED
9. V10 HorizonEngine and observation classifier are SEPARATE systems — CONFIRMED
10. 18 EXTENDED records is correct under current eligibility rules — CONFIRMED

### B. INCORRECT ASSUMPTIONS (from prior specifications)

1. ~~"V10 geometry is available for ~30-50% of decisions"~~ — **OVERSTATED.** V10 geometry is available only for decisions rejected at Risk/Execution (a subset of NO_TRADE). Opportunity/Strategy/Entry rejections produce INVALID geometry. Actual percentage needs measurement but is likely 5-15%.

2. ~~"Horizon shadows represent V10 horizon alternatives"~~ — **FALSE.** They represent structure-based counterfactuals with independent geometry. They are useful but they are NOT "what V10 would have done at INTRADAY."

### C. CURRENT ARCHITECTURAL GAPS

| Gap | Impact | Required Change |
|-----|--------|-----------------|
| No V10-geometry shadow for Risk/Execution-rejected NO_TRADE | Highest-quality counterfactuals missing | New creation path |
| No `v10_selected_horizon` field | Cannot distinguish what V10 chose | New field |
| No `horizon_selection_status` field | Cannot distinguish selected vs alternative | New field |
| EXTENDED research-eligible mode missing | Only 18 observations | Classifier parameter |
| entity_id missing on exception path | ~5% of horizon shadows unjoinable | 5-line fix |

### D. MINIMUM REQUIRED CHANGES

1. Add `v10_selected_horizon` + `horizon_selection_status` to `ShadowTrade` dataclass and `open_trade()` signature
2. In live_scanner: after V10 pipeline returns NO_TRADE where `entry.entry_status != INVALID`, open a V10-geometry shadow
3. In live_scanner: pass `v10_selected_horizon` to existing horizon shadow creation
4. In `horizon_classifier.py`: add `research_mode` parameter relaxing EXTENDED requirements
5. Fix `scanner_adapter.py` exception handler to include entity_id
6. Update `ShadowOutcomeUniverseBuilder._normalise()` to extract new fields

### E. DATA THAT CAN BE RETAINED

ALL existing shadow data remains usable:
- Primary shadows (952): VALID — V10 geometry, full lineage
- Horizon shadows (3,201): CONDITIONAL — structure-based geometry, useful for research with documented limitation
- Legacy `horizon_selection_status` = "UNKNOWN" (distinguishes pre-redesign from post-redesign)

### F. DATA THAT SHOULD BE QUARANTINED

- Test contamination (1,633 records with `sep_3a`, `ctx_test_1` etc.): Already excluded by builder prefix filter

### G. DATA THAT SHOULD BE EXCLUDED

Same as F. No additional exclusions.

### H. CORRECT SHADOW LINEAGE MODEL

```
entity_id (ONE OPPORTUNITY)
    │
    ├── DecisionTrace (Live V10 reasoning — ALL stages recorded)
    │
    ├── Live Outcome (if EXECUTE → broker fill → trade journal)
    │
    ├── V10 Shadow (V10 geometry — EXECUTE or Risk/Execution-rejected)
    │     └── v10_selected_horizon = V10's choice
    │         horizon_selection_status = "SELECTED"
    │
    └── Horizon Shadows (structure geometry — all eligible)
          ├── SCALP   → horizon_selection_status = "SELECTED" or "ALTERNATIVE"
          ├── INTRADAY → "SELECTED" or "ALTERNATIVE"
          └── EXTENDED → "ALTERNATIVE" (if research-eligible)
```

### I. CORRECT HORIZON MODEL

- V10 HorizonEngine remains at Layer 4 — UNCHANGED
- V10 selects ONE horizon → this is AUTHORITATIVE for Live
- Shadow records carry `v10_selected_horizon` showing what V10 chose
- Horizon shadows carry `horizon_selection_status` showing whether they match V10's choice
- Research can compare: "Did V10 choose optimally?" by examining selected vs alternative outcomes

### J. CORRECT RESEARCH UNIVERSE MODEL

```
LIVE EVIDENCE (realised)
├── Execution Universe (94 completed trades)
├── Decision Universe (15,865 traces)
├── Market/Strategy/Risk universes (from decision_trace)
└── Outcome Universe (wraps Execution)

SHADOW EVIDENCE (counterfactual)
├── Shadow Outcome Universe (populations):
│     ├── PRIMARY_V10_SHADOW (V10 geometry — EXECUTE + Risk/Exec-rejected)
│     ├── HORIZON_SCALP (structure geometry)
│     ├── HORIZON_INTRADAY (structure geometry)
│     └── HORIZON_EXTENDED (structure geometry)
└── Fields: r_multiple, mfe_r, mae_r, exit_reason, bars_held,
            v10_selected_horizon, horizon_selection_status, evidence_source

QUARANTINE (historical with limitations)
└── Pre-redesign horizon shadows with horizon_selection_status="UNKNOWN"
    (usable but cannot distinguish selected from alternative)
```

### K. EXACT IMPLEMENTATION SEQUENCE

1. Add fields to `ShadowTrade` dataclass: `v10_selected_horizon`, `horizon_selection_status`
2. Update `open_trade()` signature to accept new fields
3. Fix scanner_adapter exception path entity_id
4. Add V10-geometry shadow creation for NO_TRADE when entry is VALID (new section in live_scanner)
5. Pass `v10_selected_horizon` + status to existing horizon shadow creation in Phase 4C.3
6. Add `research_mode` to horizon_classifier for EXTENDED relaxation
7. Update `ShadowOutcomeUniverseBuilder._normalise()` to extract new fields
8. Run end-to-end validation with real data

---

## THE STATEMENT

> "The Shadow layer should be a counterfactual continuation of the same V10 opportunity trace, branching at the existing Horizon stage, preserving the authoritative Live V10 path, recording what V10 selected, and additionally simulating alternative horizons so the Research Engine can compare selected versus unselected possibilities."

### Verdict: **MOSTLY TRUE — with one architectural correction.**

The Shadow does NOT need to "branch at the Horizon stage." It branches AFTER the full pipeline completes, using the already-computed PipelineResult. This is functionally equivalent to branching at Horizon but architecturally simpler:

- The V10 pipeline already runs fully (all 7 stages)
- The PipelineResult already contains the Horizon decision
- Shadow creation reads from PipelineResult AFTER the pipeline returns
- No mid-pipeline interception is needed

**Corrected statement:**

> "The Shadow layer is a counterfactual continuation of the same V10 opportunity trace. It uses the V10 pipeline's own computed outputs (available in PipelineResult after the pipeline completes) to create shadow observations. For the selected horizon, it uses V10's actual Entry/Risk geometry. For alternative horizons, it uses structure-based geometry. The authoritative Live V10 path remains unchanged. Each shadow record identifies which horizon V10 selected and whether this particular shadow represents that selection or a counterfactual alternative. The Research Engine can then compare selected versus unselected possibilities through population filtering."

---

*End of audit. Implementation awaits approval.*
