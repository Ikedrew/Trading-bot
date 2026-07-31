# MEAN_REVERSION — Reconciled Evidence Contract

## Trading Concept

In a directionless market, fade the extension at a structural boundary. The edge: price overextended in a range has higher reversion probability, especially when the boundary shows evidence of institutional defence.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| HTF neutral / ranging / weak trend | **TRUE REQUIREMENT.** Reversion against a strong trend is negative EV. Neutrality is the prerequisite. | MUST HAVE |
| Price at range extreme (range_pos >= 0.70 or <= 0.30) | **TRUE REQUIREMENT.** The extension IS the setup. Mid-range has no reversion edge. | MUST HAVE |
| `inside_institutional_zone` | **IMPLEMENTATION ASSUMPTION masquerading as requirement.** The true requirement is "evidence the level is meaningful" — not specifically "inside an OB/FVG detected by a tracker." The concept is valid; the chosen signal is not the only (or best) way to validate it. | See below |

**The true third requirement is:** "Evidence that this extreme is a defended structural level, not random noise."

This can be validated by:
- Prior reactions at this level (swing levels exist = price turned here before)
- Live reaction happening now (M5 rejection at extreme)
- Structural clarity (well-defined range = stable boundaries)
- BOS level present (institutional footprint at or near this extreme)

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Neutral/ranging macro environment | MUST HAVE | `htf_alignment.macro_bias == "NEUTRAL" OR h4.trend == "NEUTRAL" OR h4.trend_strength < 0.3 OR regime.regime in ("RANGING","NEUTRAL","")` | YES |
| 2 | Price at range extreme | MUST HAVE | `location.range_position >= 0.75 OR location.range_position <= 0.25` | YES |
| 3 | Level is structurally meaningful | MUST HAVE | `(h1.swing_high > 0 OR h1.swing_low > 0 OR h1.bos_level > 0) AND h1.structural_clarity >= 0.5` | YES — swing levels from BiasSnapshot, bos_level from BOS detection, clarity from H1 analysis |
| 4 | Live reaction at level | SUPPORTING | `m5.rejection_present` | YES |
| 5 | Weak momentum (supports reversion thesis) | SUPPORTING | `regime.momentum_strength < 0.4` | YES |
| 6 | No strong displacement through level | SUPPORTING | `NOT m15.displacement_present` | YES |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| H4 trend develops (strength > 0.5) | Environment becoming directional — reversion dangerous | `h4.trend_strength > 0.5` | YES |
| Displacement through the zone | Level broken, not defended | `m15.displacement_present AND direction opposing reversion` | YES |
| Momentum accelerating into zone | Institutional force pushing through | `regime.momentum_strength > 0.7` | YES |

---

## Key Insight

The original R3 (`inside_institutional_zone`) was an **implementation assumption** — it assumed the ONLY way to validate "meaningful level" was via an OB/FVG tracker. The TRUE requirement is "evidence the boundary is real and defended." Multiple available V10 signals jointly prove this:

- `h1.swing_high/low > 0` → boundary exists (proved by prior reaction)
- `h1.structural_clarity >= 0.5` → structure is clear (not noise)
- `m5.rejection_present` → level is defending NOW
- `h1.bos_level > 0` → institutional commitment at nearby price

Together these are STRONGER evidence than a single OB tracker flag, because they validate from multiple independent angles.

---

## Status: REPRESENTABLE with V10-compatible proxy. No new data sources required.
