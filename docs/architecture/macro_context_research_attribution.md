# Macro Context Research Attribution Requirements

## Purpose

To determine whether macro context is earning its keep, we must be able to independently measure three distinct contributions:

1. Did macro improve which trades we took? (selection quality)
2. Did macro improve how confident we were? (confidence calibration)
3. Did macro improve how much we risked? (risk adjustment)

Each must be measurable WITHOUT the others. If all three are conflated into one number, we cannot diagnose which aspect is working vs which is noise.

---

## The Three Attribution Channels

### Channel 1: Selection Quality

**Question:** "Would the trades macro encouraged (boosted confidence) outperform the trades macro discouraged (reduced confidence)?"

**Measurement:** Compare R-multiples of trades where macro was supportive vs trades where macro was opposing — holding strategy family constant.

**What We Need to Persist:**
- Whether macro encouraged or discouraged this particular trade (the sign of the modifier)
- The trade outcome (R-multiple)
- Strategy family (control variable)

### Channel 2: Confidence Calibration

**Question:** "Is the macro-adjusted confidence a better predictor of trade success than the base confidence alone?"

**Measurement:** Plot base_confidence vs outcome, then macro_adjusted_confidence vs outcome. Compare which has tighter fit (lower Brier score / better calibration curve).

**What We Need to Persist:**
- Base confidence BEFORE macro (the strategy engine's raw output)
- Final confidence AFTER macro (what was actually used)
- The trade outcome (win/loss at minimum, R-multiple ideally)

### Channel 3: Risk Adjustment

**Question:** "Did macro-informed position sizing produce better risk-adjusted returns than uniform sizing would have?"

**Measurement:** Compare actual equity curve (with macro-influenced sizing) vs simulated curve using base confidence for sizing.

**What We Need to Persist:**
- Base confidence (what sizing WOULD have been without macro)
- Final confidence (what sizing actually WAS)
- Position size used
- Trade outcome (P&L)

---

## Required Persisted Fields

### In the Decision Record (`v10_decisions` JSONL)

```json
{
  "confidence_attribution": {
    "base_confidence": 0.70,
    "macro_modifier": -0.05,
    "final_confidence": 0.65,
    "modifier_direction": "OPPOSING",
    "would_trade_without_macro": true,
    "confidence_delta_pct": -7.1
  }
}
```

| Field | Type | Definition |
|---|---|---|
| `base_confidence` | float | Strategy engine output BEFORE macro is applied |
| `macro_modifier` | float | The modifier computed from MacroAlignment |
| `final_confidence` | float | `base_confidence + macro_modifier` (clamped [0.40, 1.00]) |
| `modifier_direction` | str | "ALIGNED" / "OPPOSING" / "NEUTRAL" — simple label |
| `would_trade_without_macro` | bool | Would the decision have been the same without macro? (Always true since macro never gates — but becomes relevant if confidence is used for go/no-go thresholds in future) |
| `confidence_delta_pct` | float | `(final - base) / base × 100` — percentage change |

### In the Outcome Record (linked by `observation_id`)

```json
{
  "observation_id": "...",
  "r_multiple": 1.5,
  "pnl_usd": 45.00,
  "exit_reason": "TARGET_HIT",
  "duration_bars": 24,
  "max_adverse_excursion": -0.5,
  "position_size_used": 0.02,
  "position_size_base_confidence": 0.025
}
```

| Field | Type | Definition |
|---|---|---|
| `position_size_used` | float | Actual position size (influenced by final_confidence) |
| `position_size_base_confidence` | float | Position size that WOULD have been used with base_confidence (counterfactual) |

---

## Research Queries — One Per Channel

### Query 1: Selection Quality Attribution

```
"Do trades where macro was ALIGNED outperform trades where macro was OPPOSING?"

SELECT
    modifier_direction,
    strategy_family,
    AVG(r_multiple) as avg_r,
    COUNT(*) as n,
    SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
FROM decisions d
JOIN outcomes o USING (observation_id)
WHERE final_action = 'EXECUTE'
  AND macro_data_quality = 'COMPLETE'
GROUP BY modifier_direction, strategy_family
ORDER BY strategy_family, avg_r DESC
```

**Interpretation:**
- If ALIGNED avg_r > OPPOSING avg_r → macro correctly identifies better conditions
- If no difference → macro adds no selection insight
- If OPPOSING avg_r > ALIGNED avg_r → macro is WRONG (invert or remove)

### Query 2: Confidence Calibration Attribution

```
"Is final_confidence a better predictor of win probability than base_confidence?"

-- Brier score comparison (lower = better calibrated)

-- Base confidence calibration:
SELECT
    ROUND(base_confidence, 1) as confidence_bin,
    AVG(CASE WHEN r_multiple > 0 THEN 1.0 ELSE 0.0 END) as actual_win_rate,
    AVG(base_confidence) as predicted_prob,
    COUNT(*) as n
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE final_action = 'EXECUTE'
GROUP BY confidence_bin

-- Final confidence calibration:
SELECT
    ROUND(final_confidence, 1) as confidence_bin,
    AVG(CASE WHEN r_multiple > 0 THEN 1.0 ELSE 0.0 END) as actual_win_rate,
    AVG(final_confidence) as predicted_prob,
    COUNT(*) as n
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE final_action = 'EXECUTE'
GROUP BY confidence_bin
```

**Interpretation:**
- Perfect calibration: confidence_bin 0.7 → actual_win_rate 70%
- If final_confidence bins are closer to actual win rates than base_confidence bins → macro IMPROVES calibration
- Brier score: `AVG((predicted_prob - actual_outcome)^2)` — lower = better

### Query 3: Risk Adjustment Attribution

```
"Did macro-influenced sizing produce better risk-adjusted returns?"

SELECT
    SUM(pnl_usd) as actual_pnl,
    SUM(pnl_usd * (position_size_base_confidence / position_size_used)) as counterfactual_pnl,
    STDDEV(pnl_usd) as actual_vol,
    STDDEV(pnl_usd * (position_size_base_confidence / position_size_used)) as counterfactual_vol,
    SUM(pnl_usd) / STDDEV(pnl_usd) as actual_sharpe_proxy,
    SUM(pnl_usd * (position_size_base_confidence / position_size_used)) / 
        STDDEV(pnl_usd * (position_size_base_confidence / position_size_used)) as counterfactual_sharpe
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE final_action = 'EXECUTE'
  AND position_size_used > 0
  AND position_size_base_confidence > 0
```

**Interpretation:**
- If actual_sharpe > counterfactual_sharpe → macro-adjusted sizing is BETTER
- If equal → macro sizing adds no value (but no harm)
- If worse → macro sizing is destructive (should be removed)

---

## Control Requirements

### Must Hold Constant

To attribute performance to macro (and not other factors), comparisons must control for:

| Variable | How to Control |
|---|---|
| Strategy family | GROUP BY strategy_family |
| Market conditions (regime) | GROUP BY regime at time of trade |
| Time period | Compare within same week/month, not across different market epochs |
| Symbol | GROUP BY symbol (different instruments have different characteristics) |

### Minimum Sample Sizes

| Attribution Channel | Minimum Trades Per Group | Reasoning |
|---|---|---|
| Selection quality | 30 per (strategy × modifier_direction) | Basic statistical reliability |
| Confidence calibration | 50 per confidence bin | Need enough for calibration curve |
| Risk adjustment | 100 total | Sharpe ratio needs sufficient samples for volatility estimation |

---

## What Must NOT Be Conflated

| Conflation Risk | Danger | Prevention |
|---|---|---|
| "Macro improved outcomes" (but was it selection, calibration, or sizing?) | Cannot diagnose WHAT to fix if it's wrong | Three separate queries, three separate metrics |
| "High-confidence trades do better" (but is that strategy quality or macro boost?) | Credits macro for strategy's work | Compare base_confidence vs final_confidence SEPARATELY against outcomes |
| "Macro aligned trades won more" (but maybe those were just trending markets) | Credits macro for market regime | Control for regime in all queries |

---

## Persistence Schema Addition

This section extends the persistence contract. Add to `build_v10_decision_record`:

```python
"confidence_attribution": {
    "base_confidence": strat.strategy_confidence,  # BEFORE macro
    "macro_modifier": alignment.confidence_modifier if alignment else 0.0,
    "final_confidence": final_confidence,  # AFTER macro (clamped)
    "modifier_direction": alignment.alignment_state if alignment else "UNAVAILABLE",
    "would_trade_without_macro": True,  # Always true (macro never gates)
    "confidence_delta_pct": round(
        ((final_confidence - base) / base * 100) if base > 0 else 0.0, 1
    ),
}
```

And in the outcome record (separate system):

```python
"sizing_attribution": {
    "position_size_used": actual_size,
    "position_size_base_confidence": compute_size_from(base_confidence),  # counterfactual
}
```

---

## Summary

| Channel | Field Needed | Comparison |
|---|---|---|
| Selection Quality | `modifier_direction` + `r_multiple` | ALIGNED vs OPPOSING outcomes |
| Confidence Calibration | `base_confidence` + `final_confidence` + `win/loss` | Which predicts win_rate better? |
| Risk Adjustment | `position_size_used` + `position_size_base_confidence` + `pnl` | Actual Sharpe vs counterfactual Sharpe |

All three are independently measurable. All three require persisting BOTH the "before macro" and "after macro" state. The counterfactual (what would have happened without macro) is reconstructible from persisted data.
