# FALSE_BREAK — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `state.location.liquidity_above OR state.location.liquidity_below` | Key structural level existed | **YES — EXPAND** |
| R2 | `state.m5.rejection_present` | Breakout failed (rejection) | NO — identical |
| R3 | `0.2 < state.location.range_position < 0.8` | Level reclaimed (back inside) | NO — identical |
| S1 | `state.m5.rejection_strength_atr >= 0.7` | Strong rejection | NO — identical |

## Conditions That Must Change

### Change 1: R1 — expand level detection to include swing boundaries (MODERATE)

| Dimension | Detail |
|---|---|
| Current condition | `state.location.liquidity_above OR state.location.liquidity_below` |
| Problem | Liquidity flags are only conditionally populated (require session_high/equal_highs from BiasSnapshot). Strategy fires rarely because the trigger depends on an unreliable input. |
| Reconciled requirement | "A key structural level existed that breakout traders would target" |
| Required V10 field(s) | `(state.location.liquidity_above OR state.location.liquidity_below OR state.h1.swing_high > 0 OR state.h1.swing_low > 0)` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_false_break`, line ~192 (first required condition) |
| Risk of change | **LOW-MEDIUM.** Adding `h1.swing_high/low > 0` as additional paths increases availability significantly (swing levels are reliably populated from BiasSnapshot). The concept is preserved: swing boundaries ARE levels that breakout traders target. Risk: may fire more often — but R2 (rejection) + R3 (reclaimed) still provide strong filtering. Without an actual break + failure + reclaim, the strategy won't select. |

## No Other Changes Required

R2 and R3 are both GREEN and match the reconciled contract exactly. S1 is GREEN.

## Summary

| Change | Severity | Effect |
|---|---|---|
| Expand R1 to include `h1.swing_high > 0 OR h1.swing_low > 0` | MODERATE | Increases trigger availability from rare → frequent. Still requires rejection + reclaim for selection. |

## Post-Change Expected Behaviour

With expanded R1, the strategy fires when:
1. H1 swing levels are defined (almost always true when BiasSnapshot has pivots) — **OR** liquidity flags are present
2. M5 rejection occurred (common at reversals)
3. Range position is 0.2–0.8 (price is back inside range, not at extreme)

The triple conjunction remains selective — but achievable. This should be the SECOND most achievable strategy after TREND_CONTINUATION.
