# LIQUIDITY_SWEEP_REVERSAL — Reconciled Evidence Contract

## Trading Concept

Smart money engineers a move beyond an obvious level (where retail stops cluster), fills orders against those stops, then reverses. The trade enters the reversal after the trap is confirmed. Highest-conviction reversal setup in ICT methodology.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| Liquidity pool present (above/below) | **TRUE REQUIREMENT.** Without a cluster of stops to sweep, there is no institutional incentive for the engineered move. Equal highs/lows or session extremes represent these pools. | MUST HAVE |
| Aggressive rejection (>= 0.5 ATR) | **TRUE REQUIREMENT.** The sweep must FAIL visibly. A wick beyond the level that closes back inside = the sweep was a trap. Strength threshold ensures it's meaningful, not noise. | MUST HAVE |
| CHoCH (h1.choch_detected OR m15.internal_choch) | **IMPLEMENTATION ASSUMPTION about the signal; TRUE REQUIREMENT about the concept.** The concept is "structural commitment to the new direction." CHoCH is one way to detect this, but it's not the ONLY valid evidence of structural reversal. BOS opposing the prior trend is equally valid. | See below |

**The true third requirement is:** "Structural evidence that the reversal is institutional — not just a wick that will be retested."

This can be validated by:
- H1 BOS direction opposing the sweep direction (institutional structure reversing)
- M15 displacement in reversal direction (large candle = institutional commitment)
- H1 BOS direction opposing H4 trend (intraday reversing against macro)

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Liquidity pool targeted | MUST HAVE | `location.liquidity_above OR location.liquidity_below` | CONDITIONAL — requires h1.session_high/equal_highs > 0. Available when H1 BiasSnapshot detects session levels or equal highs/lows. |
| 2 | Aggressive rejection after sweep | MUST HAVE | `m5.rejection_present AND m5.rejection_strength_atr >= 0.5` | YES |
| 3 | Structural reversal evidence | MUST HAVE | `h1.bos_confirmed AND h1.bos_direction != h4.trend` | YES — H1 BOS opposing H4 = intraday structure reversing against macro. This IS the functional meaning of CHoCH (first structural break in the new direction). |
| 4 | M15 displacement in reversal direction | SUPPORTING | `m15.displacement_present AND m15.displacement_direction != h4.trend` | YES |
| 5 | Rejection direction opposes sweep | SUPPORTING | `m5.rejection_direction OPPOSING sweep direction` | YES |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| Price re-breaks the swept level | "Reversal" failed — genuine breakout after all | Requires temporal tracking (not available in snapshot) | NOT REPRESENTABLE |
| H4 trend strong in sweep direction (>0.7) | Fighting macro = dangerous even with CHoCH | `h4.trend_strength > 0.7` | YES |
| Displacement continues in sweep direction | Institutional commitment to breakout, not reversal | `m15.displacement_direction == sweep direction` | YES |

---

## Key Insight

The original R3 demanded "CHoCH" — a concept from ICT methodology meaning "first structural break in the opposing direction." No CHoCH-specific algorithm exists, but the CONCEPT is fully representable:

**CHoCH = H1 BOS in the direction opposing the prior established trend.**

`h1.bos_confirmed AND h1.bos_direction != h4.trend` captures exactly this: H1 structure has shifted against the macro. This is more conservative than true CHoCH detection (which would fire on the very first bar of reversal) but is AVAILABLE and MEANINGFUL.

The trade-off: may fire slightly later than a dedicated CHoCH detector (requires a full BOS, not just the first lower-low after higher-highs). But it's structurally sound and doesn't depend on unbuilt components.

---

## Status: REPRESENTABLE with available proxy. R1 is conditional (needs liquidity flags), R3 uses BOS-opposition as CHoCH proxy.
