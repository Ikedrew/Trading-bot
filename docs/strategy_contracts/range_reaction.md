# RANGE_REACTION

## 1. Market Hypothesis

In an established oscillating range, price at a boundary will react toward the opposing boundary — specifically when the boundary shows evidence of institutional defence (repeated reactions, order flow concentration). The range persists because large participants absorb directional pressure at its edges.

The trade: enter at the defended boundary, target the opposite boundary, stop beyond the zone.

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Ranging regime confirmed | Range reactions fail in trends. Need established oscillation, not one-directional flow | `state.regime.regime in ("RANGING","NEUTRAL","")` | BehaviourContext.regime via V3 MarketContext | GREEN — populated by regime classifier from H4 regime + volatility analysis. Reliable indicator of non-trending market. |
| Price at range boundary | Must be at the edge to trade the reaction. Mid-range entries have no edge | `state.location.range_position >= 0.70 OR state.location.range_position <= 0.30` | M15 swing detection → LocationState.range_position | GREEN — populated, represents genuine position within M15 pivot range. |
| Boundary is institutionally defended | Not all extremes are boundaries. Need evidence the level has held before or has institutional activity | `state.location.inside_institutional_zone OR state.location.zone_quality >= 0.5` | LocationState.inside_institutional_zone (← M5 OB tracker, dead), zone_quality (← computed from inside_zone, dead) | RED — both fields are dead. `inside_institutional_zone` always False, `zone_quality` always 0.0. No path to True exists in production. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| High zone quality (>= 0.7) | Repeated reactions = high-confidence boundary (stable range) | `state.location.zone_quality >= 0.7` | RED — always 0.0. |
| Mean-reverting momentum (<0.3) | Confirms oscillating behaviour, not building for breakout | `state.regime.momentum_strength < 0.3` | GREEN — populated. |

## 4. Invalidations

None explicitly coded.

Conceptual invalidations:
- Regime transitions to TRENDING (range is breaking)
- Strong displacement through the boundary (range is ending)
- Momentum accelerating through the level (breakout beginning)
- H1 BOS in direction of the boundary test (structure shifting)

## 5. Current Implementation Assessment

Structurally identical failure to MEAN_REVERSION: R1 and R2 are GREEN, R3 is RED (dead field). Strategy can NEVER fire.

Design overlap with MEAN_REVERSION: The only distinction is R1 (RANGE_REACTION requires `regime == RANGING`, MEAN_REVERSION allows any neutral/weak-trend state). In practice, a RANGING market also satisfies MEAN_REVERSION's R1. These two strategies would select from nearly identical market conditions if both were functional.

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| `inside_institutional_zone` always False | CRITICAL | Strategy permanently blocked |
| `zone_quality` always 0.0 | CRITICAL | Alternative path also blocked |
| Overlap with MEAN_REVERSION not resolved | Design | Both strategies fire (or fail) on same conditions |

## 7. Recommended V10-Compatible Contract

The concept "boundary is institutionally defended" can be represented by:

| Concept | Available V10 Proxy | Reasoning |
|---|---|---|
| "Boundary is real structure, not noise" | `h1.structural_clarity >= 0.7` | High clarity in a ranging market = well-defined repeated boundaries. The range wouldn't have high clarity without defended edges. |
| "Boundary has prior reactions" | `h1.swing_high > 0 AND h1.swing_low > 0` | Swing pivots ARE reaction points. Their existence proves price has turned here before. |
| "Boundary is defending NOW" | `m5.rejection_present AND range_position at extreme` | Live rejection at boundary = active institutional defence. |
| "Range is established (not forming)" | `regime == RANGING AND h1.structural_clarity >= 0.6` | Combining regime + clarity proves the range has persisted long enough to be tradeable. |

Recommended replacement for R3: `h1.structural_clarity >= 0.6 AND (m5.rejection_present OR h1.swing_high > 0)`

Distinction from MEAN_REVERSION: RANGE_REACTION should additionally require evidence the range is ESTABLISHED (structural clarity high, multiple swing levels exist), while MEAN_REVERSION is more opportunistic (just neutral + extreme + zone reaction).
