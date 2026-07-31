# RANGE_REACTION — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `state.regime.regime in ("RANGING","NEUTRAL","")` | Ranging regime confirmed | MINOR — reconciled requires ONLY "RANGING" (not neutral/"") for higher bar |
| R2 | `state.location.range_position >= 0.70 OR state.location.range_position <= 0.30` | Price at range extreme | MINOR — reconciled suggests 0.75/0.25 |
| R3 | `state.location.inside_institutional_zone OR state.location.zone_quality >= 0.5` | Range is established with clear defended boundaries | **YES — MUST CHANGE** |
| S1 | `state.location.zone_quality >= 0.7` | (supporting) Stable boundaries | **YES — dead field** |

## Conditions That Must Change

### Change 1: R3 — replace dead fields with structural clarity evidence (CRITICAL)

| Dimension | Detail |
|---|---|
| Current condition | `state.location.inside_institutional_zone OR state.location.zone_quality >= 0.5` |
| Problem | Both fields are always False/0.0. Strategy can NEVER fire. |
| Reconciled requirement | "Range is established (clear repeating structure) and boundaries are defined" |
| Required V10 field(s) | `state.h1.structural_clarity >= 0.7 AND state.h1.swing_high > 0 AND state.h1.swing_low > 0` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_range_reaction`, line ~327 (third required condition) |
| Risk of change | **MEDIUM.** Unblocks the strategy. The new evidence is STRICTER in concept than MEAN_REVERSION (requires clarity >= 0.7 vs 0.5, AND both swings defined). This preserves the distinction: RANGE_REACTION is the higher-confidence version requiring an established range. Risk: may fire in early-stage ranges before they're truly established. Mitigation: 0.7 clarity threshold is conservative. |

### Change 2: R1 — tighten to RANGING only (OPTIONAL)

| Dimension | Detail |
|---|---|
| Current condition | `regime in ("RANGING","NEUTRAL","")` |
| Reconciled suggestion | `regime == "RANGING"` only |
| Code location | Same function, line ~320 |
| Risk of change | **LOW.** Removes "NEUTRAL" and "" from qualifying. Ensures only explicitly detected RANGING regime qualifies. Prevents firing in undefined market states. |

### Change 3: S1 — replace dead supporting condition

| Dimension | Detail |
|---|---|
| Current condition | `state.location.zone_quality >= 0.7` (supporting) |
| Problem | `zone_quality` always 0.0 |
| Reconciled replacement | `state.m5.rejection_present AND (range_position >= 0.75 OR range_position <= 0.25)` — live boundary defence |
| Code location | Same function, line ~335 (supporting section) |
| Risk of change | **LOW.** Supporting only — adds confidence, doesn't gate. |

## Summary

| Change | Severity | Effect |
|---|---|---|
| Replace `inside_institutional_zone OR zone_quality` with structural clarity + swing evidence | CRITICAL | Unblocks strategy from 0% → functional |
| Tighten regime to RANGING only | Optional | Increases distinction from MEAN_REVERSION |
| Replace dead `zone_quality` supporting check | Low | Supporting signal improvement |
