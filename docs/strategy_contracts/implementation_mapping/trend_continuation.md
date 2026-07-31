# TREND_CONTINUATION — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `state.h4.trend in ("BULLISH","BEARISH") and state.h4.trend_strength >= 0.5` | Strong H4 directional trend (strength >= 0.5) | NO — identical |
| R2 | `state.h1.dominant_trend == state.h4.trend and state.h1.bos_confirmed and state.h1.bos_direction == state.h4.trend` | H1 BOS aligned with H4 | NO — identical |
| R3 | `state.m15.pullback_active` | M15 pullback active | NO — identical |
| S1 | `state.m15.internal_bos and state.m15.internal_bos_direction == state.h4.trend` | Pullback exhaustion signal | YES — swap to available signal |
| S2 | `state.regime.regime == "TRENDING"` | TRENDING regime | NO — identical |

## Conditions That Must Change

### Change 1: Supporting condition S1 (dead field → available proxy)

| Dimension | Detail |
|---|---|
| Current condition | `state.m15.internal_bos and state.m15.internal_bos_direction == state.h4.trend` |
| Problem | `m15.internal_bos` is never populated (always False) |
| Reconciled requirement | "Pullback exhaustion signal" — evidence pullback is ending |
| Required V10 field(s) | `state.m5.rejection_present AND state.m5.rejection_direction == state.h4.trend` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_trend_continuation`, line ~215 (supporting section) |
| Risk of change | **LOW.** This is a supporting condition (adds confidence score, does not gate selection). Replacing a dead field with an available equivalent cannot reduce strategy fire rate. May slightly increase confidence when M5 rejection aligns with trend. |

## No Other Changes Required

All required conditions (R1-R3) are GREEN and match the reconciled contract exactly.
