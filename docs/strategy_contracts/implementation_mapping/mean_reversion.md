# MEAN_REVERSION — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `htf_alignment.macro_bias == "NEUTRAL" OR h4.trend == "NEUTRAL" OR h4.trend_strength < 0.3 OR regime.regime in ("RANGING","NEUTRAL","")` | Neutral/ranging macro environment | NO — identical |
| R2 | `location.range_position >= 0.70 OR location.range_position <= 0.30` | Price at range extreme | MINOR — reconciled suggests 0.75/0.25 threshold |
| R3 | `state.location.inside_institutional_zone` | Level is structurally meaningful | **YES — MUST CHANGE** |

## Conditions That Must Change

### Change 1: R3 — replace dead field with V10-compatible evidence (CRITICAL)

| Dimension | Detail |
|---|---|
| Current condition | `state.location.inside_institutional_zone` |
| Problem | Always False. No OB tracker exists. Strategy can NEVER fire. |
| Reconciled requirement | "Level is structurally meaningful" — evidence the boundary has institutional significance |
| Required V10 field(s) | `(state.h1.swing_high > 0 OR state.h1.swing_low > 0 OR state.h1.bos_level > 0) AND state.h1.structural_clarity >= 0.5` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_mean_reversion`, line ~283 (third required condition check) |
| Risk of change | **MEDIUM.** This unblocks the strategy — it will begin firing where it currently never fires. The new evidence is conceptually valid (swing levels + structural clarity prove institutional interest). Risk: may allow selections in conditions where the original OB-based check would not have fired. Mitigation: the supporting conditions (rejection + weak momentum) provide additional filtering, and downstream entry/risk gates provide further protection. |

### Change 2: R2 threshold tightening (OPTIONAL)

| Dimension | Detail |
|---|---|
| Current condition | `range_position >= 0.70 OR range_position <= 0.30` |
| Reconciled suggestion | `range_position >= 0.75 OR range_position <= 0.25` |
| Code location | Same function, line ~278 |
| Risk of change | **LOW.** Slightly stricter threshold reduces false positives at near-equilibrium levels. Optional improvement. |

## Summary

| Change | Severity | Effect |
|---|---|---|
| Replace `inside_institutional_zone` with structural evidence | CRITICAL | Unblocks strategy from 0% → functional |
| Tighten range_position threshold | Optional | Marginal quality improvement |
