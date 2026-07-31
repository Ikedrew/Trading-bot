# MEAN_REVERSION

## 1. Market Hypothesis

In a market without dominant directional force, price stretched to an extreme of its oscillation range will revert toward equilibrium. The edge is amplified when the extreme coincides with a level where institutional participants have previously reacted — proving the boundary is defended.

The trade: fade the extension at a meaningful level, targeting return to mid-range.

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Neutral/ranging macro environment | Reversion only works in absence of dominant trend. Trading reversion against institutional momentum = negative EV | `state.htf_alignment.macro_bias == "NEUTRAL" OR state.h4.trend == "NEUTRAL" OR state.h4.trend_strength < 0.3 OR state.regime.regime in ("RANGING","NEUTRAL","")` | HTFAlignment (V3 context), H4 regime analyzer, BehaviourContext | GREEN — multiple independent checks, at least one always evaluable. Represents genuine multi-timeframe neutrality assessment. |
| Price at range extreme (premium or discount) | Statistical edge: overextended price has higher reversion probability than price at equilibrium | `state.location.range_position >= 0.70 OR state.location.range_position <= 0.30` | M15 swing detection → MarketContextBuilder → V3MarketContext → LocationState.range_position | GREEN — populated via M15 nearest_support/resistance pivot analysis. Represents genuine position within detected range. |
| Institutional activity at this level | Distinguishes random noise-extreme from meaningful institutional boundary. Reaction at a defended level has higher hold-rate | `state.location.inside_institutional_zone` | V3 context_builders.build_location_context ← `m5.at_institutional_zone` ← OB/FVG tracker (not connected) | RED — always False. No OB tracker exists in production. Field represents the correct concept but has no data source. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| M5 rejection at extreme | Live confirmation the level IS reacting (not passive) | `state.m5.rejection_present` | GREEN — reliable candle analysis. |
| Weak momentum | No strong force pushing through the level | `state.regime.momentum_strength < 0.4` | GREEN — populated by BehaviourContext. |

## 4. Invalidations

None explicitly coded.

Conceptual invalidations:
- H4 trend strength rising above 0.5 (trend developing — reversion becoming dangerous)
- Strong displacement through the zone (level broken, not defended)
- Momentum accelerating into the zone (institutional commitment against reversion thesis)

## 5. Current Implementation Assessment

Two of three required conditions are GREEN and correctly represent the hypothesis. The third (R3: institutional zone) checks for the RIGHT concept but via a completely disconnected data source. The strategy can NEVER fire because `inside_institutional_zone` is permanently False.

The hypothesis is valid and the first two conditions are well-implemented. The failure is purely a data-source issue on R3.

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| `inside_institutional_zone` always False | CRITICAL | Strategy permanently blocked — 0% fire rate regardless of market conditions |
| No alternative zone-detection path | CRITICAL | Even if R1+R2 are perfect, strategy never reaches selection |

## 7. Recommended V10-Compatible Contract

The concept "institutional activity at this level" IS observable through available V10 signals:

| Concept | Available V10 Proxy | Reasoning |
|---|---|---|
| "Price at defended institutional level" | `h1.bos_level > 0 AND range_position at extreme` | BOS level = where institutions demonstrated structural commitment. If price is at an extreme AND that extreme has a BOS anchor, institutions were active here. |
| "Zone has reacted before" | `h1.swing_high > 0 OR h1.swing_low > 0` | Swing levels exist BECAUSE price reacted there previously. Their presence proves prior institutional activity. |
| "Level is reacting NOW" | `m5.rejection_present at extreme` | Live wick rejection at the level = current defence. |
| "Structure boundary, not noise" | `h1.structural_clarity >= 0.6` | High clarity = well-defined range with repeating structure. |

Recommended replacement for R3: `(h1.bos_level > 0 OR h1.swing_high > 0 OR h1.swing_low > 0) AND m5.rejection_present`

This preserves the concept (institutional interest + active reaction) using measurable, populated fields.
