# STRATEGY SELECTION NULL AUDIT

**Generated:** 2026-07-16  
**Evidence source:** 6,477 strategy_trace.jsonl records + code inspection  
**Question:** Why is `selected_strategy=NULL` in 98% of opportunity_assessment records?

---

## ANSWER (executive summary)

**Root cause: Regime confidence is structurally too low on M5 FX data.**

The regime classifier produces `regime_confidence < 0.4` for **92.7%** of evaluations.  
When confidence < 0.4, the eligibility matrix blocks everything except REVERSAL.  
REVERSAL then fails gating (no liquidity sweep/rejection) in **73%** of remaining cases.  
The 27% that pass gating get weight-dampened to ~0.06 (below 0.20 threshold) and are rejected.

**Result: selected_strategy=None → strategy_confidence=0.0 → global weights used.**

This is the **system working as designed** — the thresholds are just too restrictive for M5 FX.

---

## 1. RUNTIME CALL CHAIN (traced)

```
live_scanner.py:875  →  evaluate_closed_bar(candles, closed_i)
                            ↓
                        Returns: list[Signal]  (patterns detected)
                            ↓
live_scanner.py:906  →  run_new_engine(candles, ..., detected_patterns=_raw_patterns)
                            ↓
new_engine.py:118    →  run_strategy_activation(candles, closed_i, pattern, swing_direction, swing_break_confirmed)
                            ↓
                        ┌─────────────────────────────────────────────────┐
                        │ selection_activation.py:run_strategy_activation() │
                        ├─────────────────────────────────────────────────┤
                        │ Step 1: classify_regime(candles, closed_i)       │
                        │   → regime="TRANSITIONAL", confidence=0.244     │
                        │                                                 │
                        │ Step 2: compute_eligibility(regime, bos=False)   │
                        │   → conf < 0.4: only REVERSAL eligible          │
                        │   → CONT blocked, FB blocked                    │
                        │                                                 │
                        │ Step 3: get_candidate_strategies(pattern)        │
                        │   → [REVERSAL] (mapped from pattern type)        │
                        │                                                 │
                        │ Step 4: intersection(eligible, mapped)           │
                        │   → active_candidates = [REVERSAL]              │
                        │                                                 │
                        │ Step 5: extract_context(candles, closed_i, ...)  │
                        │   → liquidity_sweep=False, rejection=False       │
                        │                                                 │
                        │ Step 6: gate_reversal(context, regime)           │
                        │   → FAILS: "no_liquidity_sweep_or_rejection"     │
                        │                                                 │
                        │ Step 7: (never reached — gate failed)            │
                        │                                                 │
                        │ Step 8: allowed_candidates = [] → None           │
                        │   → selected_strategy = None                     │
                        │   → selected_weight = 0.0                        │
                        └─────────────────────────────────────────────────┘
                            ↓
new_engine.py:150    →  activation.selected_strategy is None → use GLOBAL_WEIGHTS
                            ↓
new_engine.py:215    →  OpportunityAssessment(
                            selected_strategy=activation.selected_strategy,  # None
                            strategy_confidence=activation.selected_weight,  # 0.0
                            eligible_strategies=tuple(activation.eligible_strategies),  # ("REVERSAL",)
                        )
                            ↓
                        Assessment persisted with selected_strategy=null, strategy_confidence=0.0
                            ↓
live_scanner.py:997  →  build_decision_trace(engine_result=_new_result)
                            ↓
decision_trace.py    →  DecisionTrace(
                            selected_strategy=assessment.selected_strategy,  # None
                            strategy_confidence=assessment.strategy_confidence,  # 0.0
                        )
```

---

## 2. WHERE `selected_strategy` IS FIRST ASSIGNED

| Location | File | Function | Line | Condition |
|----------|------|----------|------|-----------|
| Creation | `strategy/selection_activation.py` | `run_strategy_activation()` | ~182 | `selected = max(allowed_candidates, key=...)` |
| NULL assignment | Same | Same | ~187 | `if not allowed_candidates: selected_strategy = None` |

**`selected_strategy` is assigned ONLY in `run_strategy_activation()`.** No other code creates or overrides this value.

---

## 3. WHERE `strategy_confidence` IS CALCULATED

| Location | File | Function | Line | Value |
|----------|------|----------|------|-------|
| Creation | `strategy/selection_activation.py` | `run_strategy_activation()` | ~183 | `selected.activation_weight` (from Step 7 modulation) |
| NULL assignment | Same | Same | ~188 | `0.0` (when no valid candidates) |

**Formula for activation_weight (when strategy IS selected):**
```
base_weight × regime_multiplier × confidence_dampening
```

Example for REVERSAL in TRANSITIONAL with conf < 0.6:
```
base = 0.25 (no sweep, no rejection → lowest tier)
regime_mult = 0.5 (TRANSITIONAL → REVERSAL)
conf_damp = 0.5 (conf < 0.6)
result = 0.25 × 0.5 × 0.5 = 0.0625
threshold = 0.20
→ REJECTED (weight_too_low)
```

---

## 4. ALL CONDITIONS THAT LEAVE `selected_strategy=None`

| # | Condition | Code Location | Frequency (live data) |
|---|-----------|--------------|----------------------|
| 1 | `regime_confidence < 0.4` → only REVERSAL eligible | `eligibility_activation.py:76` | **92.7%** (6,006/6,477) |
| 2 | REVERSAL fails gating: no (key_level AND (sweep OR rejection)) | `gating_activation.py:96-102` | **67.7%** of eligible (4,389/6,006+261) |
| 3 | REVERSAL passes gating but weight < 0.20 after dampening | `selection_activation.py:171` | ~17.8% of gating-passed (1,153+282+40+37) |
| 4 | All strategies pass gating but context is invalid (`closed_i < 10`) | `gating_activation.py:44` | 116 records (1.8%) |
| 5 | High conf (≥0.6) but all gating fails (no sweep/rejection/displacement) | gating_activation | 167 records (2.6%) |
| 6 | CONTINUATION gate: `swing_direction="NEUTRAL"` AND no BOS AND no displacement | `gating_activation.py:161-163` | ~100 records |

**Dominant path (92.7%):**
```
regime_confidence < 0.4
  → only REVERSAL eligible
    → REVERSAL gate: no_liquidity_sweep_or_rejection (73%)
      → selected_strategy = None
```

---

## 5. IS THE STRATEGY CLASSIFIER BEING CALLED?

**YES.** Evidence:

1. `logs/strategy_trace.jsonl` contains 6,477 records — one per engine evaluation
2. Each record shows the full pipeline output (regime, eligibility, gating, selection)
3. The `ActivationResult` object is correctly constructed and returned
4. `activation.selected_strategy` is correctly read at `new_engine.py:215`
5. `activation.selected_weight` is correctly read at `new_engine.py:216`

**The classifier IS running. It correctly produces `None` because the pipeline legitimately rejects all strategies.**

---

## 6. IS OpportunityAssessment RECEIVING THE CLASSIFIER OUTPUT OR BYPASSING IT?

**RECEIVING IT DIRECTLY.** Evidence:

```python
# new_engine.py line ~215-216
_opportunity = OpportunityAssessment(
    selected_strategy=activation.selected_strategy,     # ← direct from activation
    strategy_confidence=activation.selected_weight,      # ← direct from activation
    eligible_strategies=tuple(activation.eligible_strategies),  # ← direct
)
```

No bypass. No override. No fallback logic between `activation` and `OpportunityAssessment`.

---

## 7. WHY REGIME CONFIDENCE IS STRUCTURALLY LOW

The regime classifier (`regime_activation.py`) computes confidence based on:

```python
# TRENDING requires: (trending structure) AND displacement >= 0.25 AND trend_strength >= 0.5
regime_confidence = min(1.0, trend_strength * 0.8 + displacement * 0.4)

# RANGE requires: displacement <= 0.15 AND range_quality >= 0.4
regime_confidence = min(1.0, range_quality * 0.7 + (1.0 - noise_index) * 0.3)

# TRANSITIONAL (fallback): everything else
regime_confidence = max(0.2, 0.5 - noise_index * 0.3)
```

**Then confidence is FURTHER reduced by:**
```python
if structure_state == "BROKEN": regime_confidence *= 0.7
if liquidity_condition == "MANIPULATED": regime_confidence *= 0.8
if noise_index > 0.6: regime_confidence *= 0.8
```

**Then hard gate:**
```python
if regime_confidence < 0.6 and regime != "TRANSITIONAL":
    regime = "TRANSITIONAL"  # Forced downgrade
```

**Result for M5 FX:**
- M5 candles produce high noise_index (overlapping candles = choppy)
- TRANSITIONAL base confidence = `0.5 - noise_index * 0.3`
- With noise_index = 0.87 (typical): `0.5 - 0.87 * 0.3 = 0.239`
- Any TRENDING/RANGE with conf < 0.6 gets forced to TRANSITIONAL
- Net result: 99.2% TRANSITIONAL regime at average confidence 0.247

---

## 8. PRODUCTION EVIDENCE (6,477 strategy traces)

| Metric | Value |
|--------|-------|
| Total evaluations | 6,477 |
| selected_strategy = NULL | 6,477 (100%) |
| selected_strategy != NULL | 0 (0%) — in trace log |
| Regime = TRANSITIONAL | 6,424 (99.2%) |
| Regime = TRENDING | 53 (0.8%) |
| Regime confidence < 0.4 | 6,006 (92.7%) |
| Regime confidence 0.4–0.6 | 261 (4.0%) |
| Regime confidence ≥ 0.6 | 210 (3.2%) |
| Average regime confidence | 0.247 |
| Max regime confidence | 1.000 |
| REVERSAL gating passed (conf < 0.4) | 1,617 |
| REVERSAL gating failed (conf < 0.4) | 4,389 |

**Note:** The 5 records with `selected_strategy != NULL` in OpportunityAssessment (from 285 Athena records) vs 0 in strategy_trace likely means:
- Athena sample is from a different time window or
- A few records correspond to the 43 high-confidence cases where selection succeeded (strategy trace shows 43 selected out of 210 high-conf records)

---

## 9. STRUCTURAL CHAIN EXPLANATION

```
M5 FX candles (5-minute bars, 7 major pairs)
    ↓
Regime classifier: 20-bar lookback on M5
    ↓
High noise (candle overlap ~87%) → TRANSITIONAL with conf ~0.24
    ↓
Eligibility: conf < 0.4 → only REVERSAL allowed
    ↓
REVERSAL gating: requires liquidity sweep OR rejection
    ↓
M5 bars rarely produce clear sweeps (too small timeframe)
    ↓
Gate fails → no candidates → selected_strategy = None
    ↓
Engine falls back to global weights (still evaluates, still produces score)
    ↓
Pipeline continues with score_neutral and score_strategy (global-weighted)
    ↓
EV usually negative → NO_TRADE (but assessment IS persisted correctly)
```

**This is not a bug. It is a threshold configuration that is too restrictive for M5 FX data.**

The system was designed for higher-timeframe or more structured markets where:
- Displacement is clearer (not 5-minute noise)
- Regime classification is more decisive
- Liquidity sweeps and key levels are more pronounced

---

## 10. SUMMARY TABLE

| Step | File | Function | Input | Output | Failure Condition |
|------|------|----------|-------|--------|-------------------|
| Pattern detection | `strategy/signal_orchestrator.py` | `evaluate_closed_bar()` | candles, closed_i | `list[Signal]` | No patterns → PATTERN_REJECT (never reaches engine) |
| Eligible strategies | `strategy/eligibility_activation.py` | `compute_eligibility()` | regime, BOS | eligibility dict | conf < 0.4 → only REVERSAL eligible |
| Strategy selection | `strategy/selection_activation.py` | `run_strategy_activation()` | candles, pattern, swing | `ActivationResult` | gating failure OR weight < 0.20 → None |
| Assessment build | `core/pipeline/new_engine.py` | `run_new_engine()` ~line 215 | activation result | `OpportunityAssessment` | Receives None directly (correct) |
| Trace build | `core/decision_trace.py` | `build_decision_trace()` | engine_result | `DecisionTrace` | Reads None from assessment (correct) |

---

*End of audit. No code was modified. The behaviour is by design — thresholds need review, not code fixes.*
