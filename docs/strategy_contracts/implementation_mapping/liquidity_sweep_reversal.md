# LIQUIDITY_SWEEP_REVERSAL — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `state.location.liquidity_above OR state.location.liquidity_below` | Liquidity pool targeted | NO — same field, conditionally available |
| R2 | `state.m5.rejection_present AND state.m5.rejection_strength_atr >= 0.5` | Aggressive rejection after sweep | NO — identical |
| R3 | `state.h1.choch_detected OR state.m15.internal_choch` | Structural reversal evidence | **YES — MUST CHANGE** |
| S1 | `state.m15.displacement_present` | M15 displacement in reversal direction | MINOR — reconciled adds direction check |
| S2 | `state.location.inside_institutional_zone` | (supporting) Zone reaction | REMOVE — dead field, low value |

## Conditions That Must Change

### Change 1: R3 — replace unimplemented CHoCH with BOS-opposition proxy (CRITICAL)

| Dimension | Detail |
|---|---|
| Current condition | `state.h1.choch_detected OR state.m15.internal_choch` |
| Problem | No CHoCH detection algorithm exists. Both fields are always False. Strategy can NEVER fire. |
| Reconciled requirement | "Structural reversal evidence" — proof that intraday structure is opposing the prior macro trend |
| Required V10 field(s) | `state.h1.bos_confirmed AND state.h1.bos_direction != state.h4.trend` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_liquidity_sweep`, line ~155 (third required condition) |
| Risk of change | **MEDIUM-HIGH.** This is the most conceptually significant change. CHoCH is precisely "first BOS in opposing direction" — using `bos_direction != h4.trend` is conceptually correct but LESS precise: it doesn't verify this is the FIRST such BOS (vs an ongoing counter-trend). However: (a) the strategy requires R1 (liquidity present) + R2 (rejection), which already narrow context significantly; (b) a BOS opposing macro in the presence of a liquidity sweep + rejection IS structurally a reversal event. The risk is acceptable given the multi-condition filtering. |

### Change 2: S1 — add direction check to displacement (MINOR)

| Dimension | Detail |
|---|---|
| Current condition | `state.m15.displacement_present` (any direction) |
| Reconciled suggestion | `state.m15.displacement_present AND state.m15.displacement_direction != state.h4.trend` (opposing macro) |
| Code location | Same function, line ~160 (supporting section) |
| Risk of change | **LOW.** More precise — only counts displacement that confirms the reversal direction. |

### Change 3: S2 — remove dead supporting condition

| Dimension | Detail |
|---|---|
| Current condition | `state.location.inside_institutional_zone` (supporting zone check) |
| Action | Remove or replace with `state.h1.bos_level > 0` (structural reference exists) |
| Code location | Same function, line ~165 |
| Risk of change | **NONE.** Removing a dead field that contributes 0 to confidence. |

## Summary

| Change | Severity | Effect |
|---|---|---|
| Replace `choch_detected` with BOS opposing H4 trend | CRITICAL | Unblocks strategy from 0% → functional |
| Add direction filter to displacement supporting check | Low | Quality improvement |
| Remove dead `inside_institutional_zone` supporting check | None | Cleanup |

## Important Note on R1

`liquidity_above/below` is conditionally available (requires h1.session_high or equal_highs > 0 from BiasSnapshot). This strategy will only fire when BOTH R1 (liquidity exists) AND the new R3 (BOS opposing trend) are satisfied simultaneously. This is appropriately selective for a high-conviction reversal strategy.
