# V10 SHADOW ARCHITECTURE — HORIZON BRANCHING AUDIT

**Date:** 2026-07-27  
**Type:** Audit + Redesign Specification  
**Status:** READ-ONLY until implementation approved  

---

## 1. CURRENT LIVE V10 PIPELINE TRACE

```
V10Pipeline.process() [core/v10/pipeline.py]
    |
    v
LAYER 1: MarketState (build_v10_market_state)
    |    Always runs. Produces V10MarketState.
    v
LAYER 2: Opportunity (assess_opportunity)
    |    Always runs. May produce opportunity_state = "INVALID".
    v
LAYER 3: Strategy (select_strategy)
    |    Always runs. May produce strategy_family = "NONE".
    v
LAYER 4: Horizon (assess_horizon) ← V10 HorizonEngine
    |    Always runs. Selects ONE horizon type.
    |    Uses: strategy family → base horizon, then modifiers (HTF, volatility, space).
    v
LAYER 5: Entry (build_entry_decision) ← V10 EntryEngine
    |    Always runs. Uses Horizon output to set target distances.
    |    May produce entry_status = "INVALID" if no valid geometry.
    v
LAYER 6: Risk (assess_risk) ← V10 RiskEngine
    |    Always runs. Computes position sizing, risk percentage.
    |    May produce approved = False.
    v
LAYER 7: Execution (build_execution_decision)
    |    Always runs. Final gate.
    v
PipelineResult (ALL 7 layer results stored regardless of approval)
```

**CRITICAL FACT:** The V10 pipeline runs ALL layers unconditionally. Even when Layer 2 rejects (INVALID opportunity), Layers 3-7 still execute. The `rejection_stage` is determined AFTER the fact by checking which layer first failed. All intermediate results are available in `PipelineResult`.

---

## 2. CURRENT SHADOW PIPELINE TRACE

### Path A: Primary Shadow (EXECUTE only)

```
V10 Pipeline approves → result.approved = True
    |
    v
engine_execution_handler.py:prepare_execution()
    |
    v
get_shadow_engine().open_trade(
    trade_id = f"shadow_{cycle_id}_{symbol}"
    entry_price = (bid + ask) / 2         ← MARKET MIDPOINT
    stop_loss = _intent.sl               ← FROM V10 RISK/ENTRY ENGINE
    take_profit = _intent.tp             ← FROM V10 ENTRY ENGINE (horizon-specific)
    lot_size = _intent.volume            ← FROM V10 RISK ENGINE
    entity_id = from V10 result
    correlation_id = COR- format
)
    |
    v
ShadowTradeEngine evaluates bar-by-bar
    |
    v
Persists to logs/shadow_trades/ as shadow_trades_v2
```

**This path uses V10's actual geometry. But only for EXECUTE decisions (~430 of 15,865).**

### Path B: Horizon Shadows (ALL pattern-detected)

```
V10 Pipeline completes (EXECUTE or NO_TRADE)
    |
    v
live_scanner.py Phase 4C.3:
    Horizon classifier (SEPARATE from V10 HorizonEngine) determines eligible horizons
    |
    v
build_all_horizon_trades() [horizon_trade_builder.py]
    |    Constructs SIMPLIFIED geometry:
    |    - SCALP: SL from M5 candle ± 2 pips, TP at 2:1 R:R
    |    - INTRADAY: SL from M15 support/resistance ± 3 pips, TP at 3:1 R:R
    |    - EXTENDED: SL from H1 swings ± 5 pips, TP at 4:1 R:R
    v
get_shadow_engine().open_trade(
    trade_id = f"hshadow_{cycle_id}_{symbol}_{horizon}"
    entry_price = ask (BUY) or bid (SELL)    ← MARKET PRICE
    stop_loss = structure-based               ← NOT from V10
    take_profit = fixed R:R                   ← NOT from V10
    lot_size = 0.01                           ← FIXED (not V10 risk sizing)
    entity_id = from V10 result
    correlation_id = HORIZON-{cycle}-{symbol}
)
```

**This path does NOT use V10 geometry. It uses an independent simplified model.**

---

## 3. EXACT LOCATION OF HORIZON IN BOTH PATHS

| System | Where Horizon Lives | What It Does |
|--------|-------------------|--------------|
| **V10 Pipeline (Live)** | `core/v10/horizon_engine.py` → Layer 4 of `V10Pipeline.process()` | Selects ONE horizon (SCALP/INTRADAY/EXTENDED) based on strategy + market modifiers |
| **Horizon Shadow (Observation)** | `core/horizon/horizon_classifier.py` → called in live_scanner after V10 pipeline | Determines which horizons are ELIGIBLE for shadow creation. DIFFERENT logic from V10 HorizonEngine. |

**These are TWO SEPARATE systems with different eligibility logic:**

| Property | V10 HorizonEngine | Observation Horizon Classifier |
|----------|-------------------|-------------------------------|
| Determines | Which ONE horizon V10 selects | Which horizons are ELIGIBLE for shadow |
| Logic | Strategy → base horizon ± modifiers (HTF, volatility, space) | Profile requirements (min_htf_alignment, requires_trend, requires_bos) |
| Input | V10MarketState, Opportunity, Strategy | Raw scoring components (htf_alignment, h4_alignment, etc.) |
| Output | Single HorizonDecision | List of eligible horizons |
| Used by | V10 EntryEngine (sets target distances) | Horizon shadow trade builder |

---

## 4. WHY THE CURRENT COUNTS ARE SCALP=1824, INTRADAY=1359, EXTENDED=18

**Root cause: The observation horizon_classifier has strict eligibility requirements for EXTENDED.**

From `horizon_profiles.py`:
```python
EXTENDED = HorizonProfile(
    min_htf_alignment=0.7,    # Strong HTF alignment required
    requires_trend=True,       # Only viable in TRENDING regime
    requires_bos=True,         # Must have H1 BOS confirmed
    requires_structure_quality=0.6,
)
```

For EXTENDED to be eligible, ALL of:
- HTF alignment score ≥ 0.7
- H4 regime must be TRENDING
- H1 BOS must be confirmed
- M15 structure quality ≥ 0.6

For SCALP:
```python
SCALP = HorizonProfile(
    min_htf_alignment=0.0,    # No requirement
    requires_trend=False,
    requires_bos=False,
    requires_structure_quality=0.0,
)
```

SCALP is ALWAYS eligible. INTRADAY requires moderate alignment (≥0.5). EXTENDED requires everything.

**This means:** Only 18 opportunities met ALL EXTENDED requirements out of ~3,200+ shadow-eligible opportunities. This is the classifier's own eligibility filter — not a reflection of whether EXTENDED evaluation would be INFORMATIVE for research.

### Answer to the Audit Question

**B. The current Shadow Horizon implementation only creates EXTENDED observations when its eligibility classifier permits them.**

For RESEARCH purposes, the counterfactual question "what would EXTENDED have produced?" is valid for any opportunity where entry geometry can be constructed (i.e., where H1 swing structure data exists to place an SL). The research intent is different from the production eligibility intent.

---

## 5. ALL SHADOW CREATION PATHS

| # | Path | File | Trigger | Trade ID | Geometry Source | Population |
|---|------|------|---------|----------|-----------------|-----------|
| 1 | Primary Shadow | `engine_execution_handler.py` | EXECUTE decision | `shadow_{cycle}_{symbol}` | V10 Entry/Risk engines | ~952 |
| 2 | Horizon SCALP | `live_scanner.py` Phase 4C.3 | Pattern + SCALP eligible | `hshadow_{cycle}_{symbol}_SCALP` | `horizon_trade_builder.py` M5 geometry | ~1,824 |
| 3 | Horizon INTRADAY | Same | Pattern + INTRADAY eligible | `hshadow_{cycle}_{symbol}_INTRADAY` | M15 geometry | ~1,359 |
| 4 | Horizon EXTENDED | Same | Pattern + EXTENDED eligible | `hshadow_{cycle}_{symbol}_EXTENDED` | H1 geometry | ~18 |

---

## 6. ALL RELEVANT IDs AND LINEAGE FIELDS

| Field | Source | Present In | Purpose |
|-------|--------|-----------|---------|
| `entity_id` | `f"{symbol}_{bar_time}"` in scanner_adapter | DecisionTrace, Shadow records | **CANONICAL CROSS-WORLD JOIN KEY** |
| `observation_id` | Generated by OpportunityAssessment | PipelineResult.opportunity | V10 internal opportunity ID |
| `correlation_id` | `generate_correlation_id()` for EXECUTE; `HORIZON-{cycle}-{symbol}` for shadows | Shadow records, execution context | Trace ID (format differs by path) |
| `cycle_id` | Scanner loop counter | All records | Temporal ordering |
| `shadow_trade_id` (trade_id) | Generated at shadow open_trade call | Shadow records | Unique shadow observation ID |

---

## 7. WHICH DOWNSTREAM V10 VALUES ARE AVAILABLE AT HORIZON

When the V10 pipeline reaches Layer 4 (Horizon), the following are ALREADY computed:

| Available at Horizon | From Layer | Content |
|---------------------|-----------|---------|
| `V10MarketState` | Layer 1 | Full multi-timeframe state (H4/H1/M15/M5/regime/location) |
| `OpportunityAssessment` | Layer 2 | Quality scores, directional bias, opportunity state |
| `StrategyDecision` | Layer 3 | Strategy family, confidence, direction |
| `HorizonDecision` | Layer 4 | V10's SELECTED horizon type + movement expectation |

After Horizon, Layers 5-7 compute:
| Computed AFTER Horizon | Layer | Content |
|----------------------|-------|---------|
| `EntryDecision` | Layer 5 | Entry price, SL, TP, direction, expected R:R |
| `RiskDecision` | Layer 6 | Position size, risk percentage, approval |
| `ExecutionDecision` | Layer 7 | Final approval, order details |

**Key: The pipeline runs ALL layers regardless of failure.** So even for NO_TRADE decisions, Layers 5-7 produce their results (though Entry may be INVALID if Opportunity/Strategy failed).

---

## 8. WHAT CAN SAFELY BE SIMULATED AFTER LIVE REJECTION

| Live Terminal Stage | What's Available | Can Shadow Continue? | What's Missing? |
|--------------------|-----------------|--------------------|-----------------|
| **Opportunity** (INVALID) | MarketState only | **PARTIALLY** — Horizon/Entry need strategy + opportunity | No directional bias, no strategy family |
| **Strategy** (NONE) | MarketState + Opportunity | **PARTIALLY** — Entry needs strategy family to determine method | No strategy → no entry method → simplified geometry only |
| **Entry** (INVALID) | MarketState + Opportunity + Strategy + Horizon | **YES** — V10 tried but geometry invalid. Shadow can attempt with horizon-builder geometry | V10 geometry unavailable (it was INVALID) |
| **Risk** (rejected) | ALL layers complete with valid geometry | **YES FULLY** — V10 computed valid entry/SL/TP, risk just said "too much exposure" | Nothing missing. Full V10 geometry available. |
| **Execution** (rejected) | ALL layers complete | **YES FULLY** — same as Risk | Nothing missing |

---

## 9. GAP ANALYSIS: CURRENT → TARGET

### Current System

```
OPPORTUNITY → V10 Pipeline (full)
                    |
          ┌────────┴────────┐
          |                  |
     If EXECUTE          If NO_TRADE
          |                  |
   Primary Shadow      Horizon Shadows (simplified geometry)
   (V10 geometry)      (INDEPENDENT of V10 Horizon/Entry/Risk)
```

### Target System

```
OPPORTUNITY → V10 Pipeline (full)
                    |
          ┌────────┴────────┐
          |                  |
     If EXECUTE          If NO_TRADE
          |                  |
   Primary Shadow      V10 SHADOW (V10's own geometry)
   (V10 geometry)           +
          +             HORIZON ALTERNATIVES
   Horizon Alternatives    (for ALL eligible horizons)
```

### Required Changes

| Change | Nature | Risk |
|--------|--------|------|
| 1. On NO_TRADE, open a "V10 shadow" using PipelineResult's entry geometry (when Entry is VALID) | New shadow creation path in live_scanner | LOW — observational only |
| 2. Create horizon alternative shadows for horizons V10 did NOT select (regardless of EXECUTE/NO_TRADE) | Extension of existing Phase 4C.3 | LOW — existing mechanism extended |
| 3. Mark which shadow is "selected" vs "alternative" in the shadow record | Add `horizon_selection_status` field to open_trade | LOW — new field |
| 4. Relax EXTENDED eligibility for RESEARCH shadow creation (separate from Live eligibility) | Allow shadow even when classifier says ineligible, if H1 data exists | MEDIUM — needs careful separation |
| 5. Preserve V10 HorizonEngine's selection as authoritative | Label in shadow record: `v10_selected_horizon` field | LOW — metadata only |

---

## 10. PROPOSED SHADOW CREATION (TARGET)

### After V10 Pipeline Completes (for EVERY pattern-detected decision):

```python
# In live_scanner, AFTER V10 pipeline runs (Phase 4C.3 replacement):

_pipeline_result = _new_result.get("v10_pipeline_result")
_v10_horizon = _pipeline_result.horizon.horizon_type if _pipeline_result else ""
_v10_entry = _pipeline_result.entry if _pipeline_result else None
_v10_risk = _pipeline_result.risk if _pipeline_result else None

# 1. V10 GEOMETRY SHADOW (if entry is valid)
#    Uses V10's actual computed SL/TP/direction
#    This is what V10 WOULD have done mechanically
if _v10_entry and _v10_entry.entry_status != "INVALID":
    get_shadow_engine().open_trade(
        trade_id=f"shadow_{cycle_id}_{symbol}",
        entry_price=_v10_entry.entry_price,
        stop_loss=_v10_entry.stop_reference.price,
        take_profit=_v10_entry.target_reference.price,
        lot_size=_v10_risk.position_size if _v10_risk and _v10_risk.approved else 0.01,
        trade_horizon=_v10_horizon,
        # LINEAGE
        entity_id=_new_result.get("entity_id", ""),
        correlation_id=_cor_id,
        # CLASSIFICATION
        shadow_type="V10_PRIMARY",
        v10_selected_horizon=_v10_horizon,
        horizon_selection_status="SELECTED",
    )

# 2. HORIZON ALTERNATIVE SHADOWS (all eligible horizons EXCEPT the selected one)
for _horizon in _eligible_horizons:
    if _horizon == _v10_horizon:
        # Already covered by V10 geometry shadow above
        # Create horizon-geometry variant for comparison
        _status = "SELECTED_ALTERNATIVE_GEOMETRY"
    else:
        _status = "COUNTERFACTUAL_ALTERNATIVE"
    
    # Use existing horizon_trade_builder for alternative geometry
    _trade = build_horizon_trade(horizon=_horizon, ...)
    if _trade:
        get_shadow_engine().open_trade(
            trade_id=f"hshadow_{cycle_id}_{symbol}_{_horizon}",
            ...
            v10_selected_horizon=_v10_horizon,
            horizon_selection_status=_status,
        )
```

### Key Distinctions

| Shadow Type | Meaning | Geometry | When Created |
|-------------|---------|----------|--------------|
| V10_PRIMARY | "What V10's exact trade plan would have produced" | V10 Entry/Risk engines | When V10 produces valid entry geometry |
| SELECTED_ALTERNATIVE_GEOMETRY | "What the SELECTED horizon would have produced under structure-based geometry" | horizon_trade_builder | When selected horizon is eligible in classifier |
| COUNTERFACTUAL_ALTERNATIVE | "What an UNSELECTED horizon would have produced" | horizon_trade_builder | For each non-selected eligible horizon |

---

## 11. EXTENDED ELIGIBILITY FOR RESEARCH

### Current Constraint (Too Restrictive for Research)

The observation classifier requires ALL of:
- HTF alignment ≥ 0.7
- H4 TRENDING
- H1 BOS confirmed
- Structure quality ≥ 0.6

This is appropriate for LIVE eligibility (don't open real trades without these conditions). But for RESEARCH counterfactuals, the question is:

> "What WOULD EXTENDED have produced?" — regardless of whether we'd actually trade it.

### Proposed Research-Eligible Relaxation

For shadow/counterfactual creation ONLY (never affects Live):
- EXTENDED shadow can be created whenever H1 swing structure data EXISTS (to place SL)
- The strict eligibility check remains for LIVE selection
- Research shadows are labelled `research_eligible=True` vs `live_eligible=True/False`

This would significantly increase EXTENDED observations from 18 to likely hundreds/thousands.

**Implementation:** Add a `research_mode=True` parameter to horizon_classifier that relaxes requirements to minimum structural data availability rather than full production confidence thresholds.

---

## 12. QUANTITATIVE COMPARISON

### CURRENT SYSTEM

| Metric | Value |
|--------|-------|
| Total shadow records (production) | 4,153 |
| V10-geometry shadows (EXECUTE only) | 952 |
| Horizon shadows (simplified geometry) | 3,201 |
| SCALP observations | 1,824 |
| INTRADAY observations | 1,359 |
| EXTENDED observations | 18 |
| Opportunities with V10 geometry shadow | 952 (EXECUTE only = 2.7% of decisions) |
| Opportunities with ANY counterfactual | ~1,735 unique entities |
| Entity_id coverage | 78% overall |
| Earlier-rejection counterfactual (NO_TRADE with V10 geometry) | 0 |

### TARGET SYSTEM (estimated after redesign)

| Metric | Estimated Value | Improvement |
|--------|----------------|-------------|
| V10-geometry shadows | ~3,000-5,000 (all decisions with valid Entry) | **+3,000-4,000** |
| Horizon SCALP | ~3,000+ (all pattern-detected) | +1,200 |
| Horizon INTRADAY | ~2,500+ (relaxed eligibility) | +1,100 |
| Horizon EXTENDED | ~500-1,500 (research-eligible relaxation) | **+500-1,500** (from 18!) |
| Opportunities with V10 geometry shadow | ~3,000-5,000 | **From 2.7% to ~30-50% of decisions** |
| NO_TRADE with V10 counterfactual | ~2,500-4,500 | **From 0 to thousands** |
| Total shadow observations per entity | 3-4 (selected + alternatives) | Same structure, richer content |

### NEW QUESTIONS THAT BECOME ANSWERABLE

| # | Question | Current | After Redesign |
|---|----------|---------|----------------|
| 1 | "What would V10's actual intended trade have produced for rejected opportunities?" | IMPOSSIBLE (no V10 geometry for NO_TRADE) | **ANSWERABLE** with ~3,000+ records |
| 2 | "Is V10's selected horizon the best one for this opportunity?" | IMPOSSIBLE (no V10 context in horizon shadows) | **ANSWERABLE** — compare selected vs alternatives |
| 3 | "What is the counterfactual expectancy of EXTENDED horizon?" | IMPOSSIBLE (18 records) | **ANSWERABLE** with 500-1,500 records |
| 4 | "When V10 rejects at Risk/Execution, does the geometry still work?" | IMPOSSIBLE (no shadow for risk-rejected) | **ANSWERABLE** — V10 geometry available |
| 5 | "What is the opportunity cost of each V10 rejection stage, using V10's own geometry?" | Only approximate (simplified geometry) | **PRECISE** — uses V10's actual SL/TP/direction |
| 6 | "Does the V10 HorizonEngine systematically choose sub-optimally?" | IMPOSSIBLE | **ANSWERABLE** — compare V10 selected vs alternatives |

---

## 13. IMPLEMENTATION PLAN

| Phase | Change | Files Affected | Risk |
|-------|--------|---------------|------|
| 1 | Add `v10_selected_horizon` and `horizon_selection_status` fields to ShadowTrade dataclass | `core/shadow_trades.py` | LOW |
| 2 | Create V10-geometry shadow on NO_TRADE path when Entry is VALID | `core/runtime/live_scanner.py` (new section after Phase 4C.3) | LOW |
| 3 | Add `horizon_selection_status` to existing horizon shadow creation | `core/runtime/live_scanner.py` Phase 4C.3 | LOW |
| 4 | Add research-eligible mode to horizon_classifier | `core/horizon/horizon_classifier.py` | MEDIUM |
| 5 | Update ShadowOutcomeUniverseBuilder to parse new fields | `research_engine/v10/universes/shadow_outcome_universe.py` | LOW |
| 6 | Fix scanner_adapter exception path entity_id | `core/v10/scanner_adapter.py` | LOW |

**Total estimated effort: 2-3 sessions**

---

## 14. HISTORICAL DATA CLASSIFICATION

| Category | Records | Classification | Usable? |
|----------|---------|---------------|---------|
| Primary shadows (V10 geometry, EXECUTE) | 952 | **VALID** — correct V10 geometry, usable immediately | YES |
| Horizon SCALP (simplified geometry) | 1,824 | **CONDITIONAL** — valid counterfactual under simplified model. Clearly labelled as non-V10-geometry. | YES (with limitation noted) |
| Horizon INTRADAY (simplified geometry) | 1,359 | **CONDITIONAL** — same as SCALP | YES (with limitation noted) |
| Horizon EXTENDED (simplified geometry) | 18 | **CONDITIONAL** — too few for statistical research but not invalid | YES (sample too small) |
| Test contamination | 1,633 | **INVALID** — test artifacts | NO (excluded by builder) |

**No data needs to be deleted.** The ShadowOutcomeUniverseBuilder already correctly classifies and excludes test data. Historical horizon shadows remain usable as "simplified geometry counterfactuals" — they answer a different (still useful) question from V10-geometry shadows.

After the redesign produces new data, the research engine can distinguish:
- `shadow_type=V10_PRIMARY` + `horizon_selection_status=SELECTED` → V10's exact geometry
- `shadow_type=HORIZON` + `horizon_selection_status=COUNTERFACTUAL_ALTERNATIVE` → alternative geometry
- Historical `shadow_type=HORIZON` without `horizon_selection_status` → legacy simplified (still usable as evidence of structure-based opportunity)

---

## SUCCESS CRITERIA VERIFICATION

| # | Criterion | Met By |
|---|-----------|--------|
| 1 | Live V10 unchanged | No modification to V10Pipeline, HorizonEngine, or execution logic |
| 2 | Horizon stays at existing pipeline position | V10 HorizonEngine remains Layer 4. No relocation. |
| 3 | Shadow branches at Horizon | New shadow creation uses PipelineResult AFTER horizon selected |
| 4 | Selected + alternative horizons observable | V10_PRIMARY shadow + horizon alternative shadows with `horizon_selection_status` |
| 5 | Earlier rejection doesn't destroy counterfactual | V10 shadow created whenever Entry is VALID (regardless of Risk/Execution rejection) |
| 6 | Lineage preserved | All observations share entity_id from same V10 evaluation |
| 7 | Live and Shadow distinguishable | `shadow_type`, `horizon_selection_status`, `evidence_source` fields |
| 8 | Research Engine can consume | ShadowOutcomeUniverseBuilder updated to parse new fields → populations |
| 9 | Historical data classified | VALID / CONDITIONAL / INVALID already implemented |
| 10 | No premature Research Engine redesign | Only builder update and new population definitions needed |

---

*End of audit. Implementation awaits approval.*
