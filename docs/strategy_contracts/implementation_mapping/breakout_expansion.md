# BREAKOUT_EXPANSION — Implementation Mapping

## Current vs Reconciled

| # | Current Condition | Reconciled Requirement | Change Needed? |
|---|---|---|---|
| R1 | `state.regime.expansion_state == "COMPRESSING" OR state.regime.volatility_state == "CONTRACTION" OR state.regime.compression_bars > 5` | Prior compression / low volatility | **YES — SIMPLIFY** |
| R2 | `state.m15.displacement_present OR state.regime.expansion_state == "EXPANDING" OR state.regime.volatility_state == "EXPANSION"` | Volatility expanding NOW | **YES — SIMPLIFY** |
| R3 | `state.m15.displacement_present` | Institutional displacement | NO — identical |
| S1 | `state.m15.displacement_magnitude_atr >= 1.5` | Strong displacement magnitude | NO — identical |

## Conditions That Must Change

### Change 1: R1 — remove dead paths, keep functional one (CLEANUP)

| Dimension | Detail |
|---|---|
| Current condition | `expansion_state == "COMPRESSING" OR volatility_state == "CONTRACTION" OR compression_bars > 5` |
| Problem | `expansion_state` is never populated (always ""). `compression_bars` is never computed (always 0). Only `volatility_state == "CONTRACTION"` works. The dead paths add complexity without value. |
| Reconciled requirement | "Prior compression / low volatility environment" |
| Required V10 field(s) | `state.regime.volatility_state == "CONTRACTION"` |
| Code location | `core/v10/strategy_engine.py`, function `_evaluate_breakout_expansion`, line ~247 (first required condition) |
| Risk of change | **NONE.** Removing dead code paths that can never evaluate to True. The surviving path is unchanged. Behaviour is identical to current production (only CONTRACTION path could ever fire). |

### Change 2: R2 — simplify to displacement (subsumes into R3)

| Dimension | Detail |
|---|---|
| Current condition | `m15.displacement_present OR expansion_state == "EXPANDING" OR volatility_state == "EXPANSION"` |
| Problem | This condition is REDUNDANT with R3 (which also requires `displacement_present`). The additional paths (`expansion_state == "EXPANDING"`, `volatility_state == "EXPANSION"`) add alternatives that either don't fire (dead) or weaken the requirement (allowing expansion without displacement). |
| Reconciled requirement | R2 as a separate gate is unnecessary — displacement (R3) already proves expansion. |
| Required action | MERGE R2 into R3 — single condition: `m15.displacement_present AND m15.displacement_magnitude_atr >= 1.5` |
| Code location | Same function, line ~255 |
| Risk of change | **LOW.** The merged condition is STRICTER than current R2 (removes the `volatility_state == "EXPANSION"` fallback that allows non-displacement expansions). Since displacement is the defining trigger for this strategy, this is conceptually correct. However: removing `volatility_state == "EXPANSION"` as a standalone qualifying path means the strategy ONLY fires on displacement — never on a gradual volatility increase. This matches the hypothesis (breakout = sudden, not gradual). |

### Alternative: Keep R2 as `volatility_state == "EXPANSION"` fallback

If gradual expansion without displacement should qualify, keep:
```
R2: state.regime.volatility_state == "EXPANSION" OR state.m15.displacement_present
```
This allows the strategy to fire on EITHER: sudden displacement OR regime-detected expansion. Decision depends on whether the hypothesis includes gradual expansions. Reconciled contract says NO (displacement is the defining feature).

## Summary

| Change | Severity | Effect |
|---|---|---|
| Remove dead `expansion_state` and `compression_bars` from R1 | None (cleanup) | No behaviour change — dead paths removed |
| Simplify R2 to merge with R3 (displacement-only) | Low | Slightly stricter — removes non-displacement expansion path |

## Post-Change Expected Behaviour

The strategy fires when:
1. `volatility_state == "CONTRACTION"` (market was quiet)
2. `m15.displacement_present AND magnitude >= 1.5` (sudden institutional candle)

This is a naturally rare conjunction — appropriate for a breakout strategy. Most market observations are NOT breakout moments. The strategy should fire ~1-3 times per day across all symbols during active sessions with genuine compression→expansion transitions.
