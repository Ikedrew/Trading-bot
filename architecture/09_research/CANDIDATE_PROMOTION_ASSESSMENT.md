# Candidate Promotion — Architectural Assessment

**Date:** 2026-07-19  
**Scope:** Can the existing Strategy Compiler consume validated research candidates?

---

## Executive Summary

**The Strategy Compiler CANNOT directly consume validated candidates from `research_engine/edge_candidates/validation.py` in their current form.** The two systems speak different languages — the compiler expects `edge_optimisation_v2` reports (feature attribution statistics), while the research engine produces `EdgeCandidate` objects (condition-based rules). A promotion interface is required to bridge the gap.

---

## Current Architecture

### Research Engine Output (validated candidates)

```python
EdgeCandidate:
    candidate_id: "EC-HIGH_TWEEZER_TOP-574D6A"
    conditions: {"pattern": "TWEEZER_TOP", "bias_alignment_bin": "HIGH"}
    sample_size: 216
    win_rate: 0.42
    expectancy: +0.246R
    validation_status: "VALIDATED"
    validation_results: {splits_positive: 4/5, total_r: +53.9}
```

**Format:** Conditional boolean rules (pattern=X AND condition=Y).

### Strategy Compiler Input (edge_report)

```python
compile_strategy(edge_report={
    "feature_edges": {
        "bias_alignment": {"stability_score": 0.6, "mean_attribution_score": 0.15, "sample_count": 200},
        "htf_alignment": {"stability_score": 0.3, "mean_attribution_score": 0.08, "sample_count": 200},
    },
    "causal_weights": {
        "bias_alignment": {"outcome_strength": 0.22, "stability_weight": 0.8},
    },
    "regime_breakdowns": [...],
    "statistics": {"rolling_window_id": "..."},
    "portfolio_metrics": {"overall_expectancy": 0.15},
})
```

**Format:** Continuous feature attributions with stability scores.

### Production Strategy System

```
StrategyType (enum):  CONTINUATION | REVERSAL | FALSE_BREAK
StrategyActivation:   classify_strategy() → StrategyType + confidence
strategy_weights.py:  per-type weight profiles (10 factors)
new_engine.py:        applies weights, computes score, evaluates EV
```

**Format:** Predefined archetypes with static weight profiles.

---

## Gap Analysis

### Missing Interface: Candidate → Edge Report Adapter

| Research Output | Strategy Compiler Input | Gap |
|-----------------|------------------------|-----|
| Boolean conditions (`pattern=X`) | Continuous attribution scores | Incompatible types |
| Walk-forward EV | `stability_score` per feature | Needs translation |
| `win_rate`, `expectancy` | `mean_attribution_score` | Different semantics |
| Condition combinations | Individual feature weights | 1:N decomposition needed |

### Missing Interface: Compiled Strategy → Production Weight Profile

| Compiled Strategy | Production System | Gap |
|-------------------|-------------------|-----|
| `entry_conditions` (boolean rules) | 10-factor weighted score | Different execution model |
| `feature_weights` (arbitrary features) | Fixed 10 components | Schema mismatch |
| `regime_filters` | StrategyType enum | No dynamic registration |

### Missing Workflow: Shadow Strategy Status

Currently there is no concept of a "shadow strategy" in the production system. Strategies are:
1. **Hardcoded** (CONTINUATION/REVERSAL/FALSE_BREAK weight profiles)
2. **Statically classified** (pattern → primary strategy mapping)
3. **Never changed at runtime**

There is no mechanism to:
- Register a new strategy type dynamically
- Run a strategy in shadow mode alongside production
- Compare shadow vs production outcomes
- Promote a shadow strategy to production

---

## Data Contract Gaps

### 1. Candidate → edge_report Translation

The compiler expects `feature_edges` with per-feature statistics. A validated candidate like `{"pattern": "TWEEZER_TOP", "bias_alignment_bin": "HIGH"}` needs to be translated into:

```python
{
    "feature_edges": {
        "bias_alignment": {"stability_score": 0.80, "mean_attribution_score": 0.246, "sample_count": 216},
        "pattern_quality": {"stability_score": 0.6, "mean_attribution_score": 0.1, "sample_count": 216},
    },
    "causal_weights": {"bias_alignment": {"outcome_strength": 0.246}},
    "portfolio_metrics": {"overall_expectancy": 0.246},
    "regime_breakdowns": [{"regime_type": "TRANSITIONAL", "regime_stability": 0.8, "regime_edge_strength": 0.246}],
}
```

This translation is **lossy** — a boolean condition (`bias_alignment_bin=HIGH`) doesn't naturally map to continuous feature weights.

### 2. Strategy Registration

The production system has exactly 3 strategy types. A new candidate like "TWEEZER_TOP + HIGH_BIAS" doesn't fit any existing archetype. Options:
- A. Map to existing type (e.g., classify as REVERSAL variant) — loses specificity
- B. Create new StrategyType — requires enum extension + weight profile + classifier changes
- C. Bypass strategy system entirely — use EV formula modification instead

### 3. Shadow Execution Contract

No interface exists for:
- Registering a shadow strategy
- Routing decisions through shadow vs production
- Persisting shadow results separately
- Comparing shadow vs production P&L

---

## Implementation Plan

### Phase 1: Promotion Adapter (research only)

Create `research_engine/promotion/candidate_to_edge_report.py`:
- Translates `EdgeCandidate` + validation results into `edge_optimisation_v2` format
- Does NOT modify production — only produces compatible reports
- Allows testing the compiler pipeline with research output

```python
def promote_candidate_to_edge_report(
    candidate: EdgeCandidate,
    validation: CandidateValidationResult,
    attribution_records: list[EdgeAttributionRecord],
) -> dict[str, Any]:
    """Convert validated candidate into Strategy Compiler input format."""
```

### Phase 2: Shadow Strategy Registry (infrastructure)

Create `core/shadow_strategies/registry.py`:
- Stores compiled shadow strategies (from promoted candidates)
- Provides a lookup interface for the decision engine
- Does NOT affect execution — only records what shadow strategies WOULD do
- Persists to `logs/shadow_strategies/`

### Phase 3: Shadow Strategy Evaluator (production instrumentation)

Modify `core/runtime/live_scanner.py` (minimal change):
- After Engine A produces a decision, check shadow strategies
- Record shadow strategy decision + outcome
- Never affects execution
- Gated behind a config flag: `SHADOW_STRATEGY_ENABLED=False`

### Phase 4: Promotion Workflow

Create `research_engine/promotion/workflow.py`:
- Input: validated candidate + walk-forward results
- Step 1: Convert to edge_report format
- Step 2: Run through `compile_strategy()`
- Step 3: Validate compiled strategy
- Step 4: Register as shadow strategy
- Step 5: Monitor shadow performance for N cycles
- Step 6: Human review gate before production promotion

### Phase 5: Production Deployment (requires human approval)

Only after shadow strategy demonstrates positive EV in live shadow:
- Create new weight profile entry in `strategy_weights.py`
- Register pattern mapping in `mapping_activation.py`
- Enable via config flag

---

## Recommended Approach: Bypass Strategy Compiler

Given the findings (EV formula is the bottleneck, not strategy classification), the **simplest path to production** does not require the Strategy Compiler at all:

**Option A (Recommended):** Modify the EV formula's P_success computation to use pattern-specific empirical win rates from validated candidates, gated by minimum walk-forward evidence. This is a **single-point change** in `core/pipeline/expected_value.py`:

```python
# Instead of synthetic probability:
p_base = (score_neutral * 0.6) + (strategy_confidence * 0.4)

# Use validated empirical rate (with Bayesian shrinkage):
p_base = get_validated_pattern_probability(pattern_name, regime)
```

This bypasses the compiler entirely because:
1. No new strategy type is needed
2. No weight profile changes are needed
3. The execution path remains identical
4. Only the probability input to the EV gate changes

**Option B:** Full compiler pipeline (Phases 1-5 above). More architecturally pure but requires 5x more implementation for the same outcome.

---

## Missing Pieces Summary

| Component | Status | Required For |
|-----------|--------|-------------|
| Candidate → edge_report adapter | NOT BUILT | Compiler consumption |
| Shadow strategy registry | NOT BUILT | Live shadow testing |
| Shadow strategy evaluator | NOT BUILT | Live comparison |
| Promotion workflow | NOT BUILT | End-to-end pipeline |
| Config-gated EV probability lookup | NOT BUILT | Simplest production path |
| Walk-forward validation data store | EXISTS | Already in research_reports/ |
| Empirical pattern win rates | EXISTS | Available from research experiments |

---

## Recommendation

**Start with Option A** (single EV probability fix) because:
1. Research has definitively identified the problem (EV formula suppresses P_success)
2. The solution is a single function change
3. Walk-forward validation showed 18 candidates survive
4. The top candidate (TWEEZER_TOP + HIGH_BIAS) has HIGH confidence, 216 trades, 4/5 splits positive
5. No architectural overhead required

Only build the full compiler pipeline (Option B) if:
- The system needs to manage >5 validated strategies simultaneously
- Strategy evolution/mutation tracking becomes important
- Multiple strategy archetypes need different weight profiles

Currently, the system has identified ONE viable probability model (empirical pattern win rates). One change unlocks it.
