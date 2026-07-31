# RANGE_REACTION — Reconciled Evidence Contract

## Trading Concept

In an established range with clear boundaries, enter at the boundary expecting a reaction toward the opposite side. The edge: institutional participants defend range edges, absorbing directional pressure.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| Ranging regime | **TRUE REQUIREMENT.** Range-boundary trades only work in oscillating markets. In a trend, "boundaries" get broken. | MUST HAVE |
| Price at range extreme | **TRUE REQUIREMENT.** Must be AT the boundary to trade the reaction. | MUST HAVE |
| `inside_institutional_zone OR zone_quality >= 0.5` | **IMPLEMENTATION ASSUMPTION.** The true requirement is "boundary is established and defended" — not specifically "inside an OB." The original intent (institutional defence) is valid; the signal choice is broken. | See below |

**The true third requirement is:** "The range is established (not forming) and the boundary has been defended before."

**How RANGE_REACTION differs from MEAN_REVERSION:**
- MEAN_REVERSION: any neutral environment + extreme + meaningful level
- RANGE_REACTION: ESTABLISHED range + high-clarity boundaries + evidence the range has PERSISTED

The distinction: RANGE_REACTION should require STRONGER evidence that the range is real and repeating.

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Ranging regime confirmed | MUST HAVE | `regime.regime in ("RANGING")` | YES |
| 2 | Price at range extreme | MUST HAVE | `location.range_position >= 0.75 OR location.range_position <= 0.25` | YES |
| 3 | Range is established (clear repeating structure) | MUST HAVE | `h1.structural_clarity >= 0.7 AND (h1.swing_high > 0 AND h1.swing_low > 0)` | YES — clarity proves repeated structure; both swing levels prove defined range edges |
| 4 | Boundary is defending NOW | SUPPORTING | `m5.rejection_present AND (range_position >= 0.75 OR range_position <= 0.25)` | YES |
| 5 | Momentum is mean-reverting | SUPPORTING | `regime.momentum_strength < 0.3` | YES |
| 6 | BOS level nearby (institutional footprint) | OPTIONAL | `h1.bos_level > 0` | YES (when BOS detected) |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| Regime changes to TRENDING | Range is breaking | `regime.regime == "TRENDING"` | YES |
| H1 BOS through the boundary | Structure broken — no longer ranging | `h1.bos_direction aligned with pressure through boundary` | YES |
| Displacement through boundary | Institutional breakout | `m15.displacement_present toward opposing boundary` | YES |
| Structural clarity drops below 0.4 | Range losing definition (becoming choppy) | `h1.structural_clarity < 0.4` | YES |

---

## Distinction from MEAN_REVERSION

| Dimension | MEAN_REVERSION | RANGE_REACTION |
|---|---|---|
| Macro requirement | Any neutral/weak environment | Specifically RANGING regime |
| Structure requirement | Structural level exists | **High clarity (>= 0.7)** + both swing boundaries defined |
| Conviction level | Opportunistic | Higher confidence (established range) |
| Suitable when | One-off reversion opportunities | Repeated boundary reactions in known range |

This distinction ensures both strategies CAN co-exist without being redundant — RANGE_REACTION is the higher-bar, higher-confidence version.

---

## Status: REPRESENTABLE with V10-compatible evidence. Requires higher structural bar than MEAN_REVERSION to maintain distinction.
