# FALSE_BREAK — Reconciled Evidence Contract

## Trading Concept

Price breaks a key level (drawing in breakout participants), fails to continue, and reclaims the level. Trapped breakout traders' stops become fuel for the counter-move. The trade fades the failed breakout.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| `liquidity_above OR liquidity_below` | **IMPLEMENTATION ASSUMPTION about the signal.** The true requirement is "a key level existed that attracted breakout interest." Liquidity flags are ONE proxy for this — but swing boundaries and session levels serve the same purpose. | See below |
| M5 rejection present | **TRUE REQUIREMENT.** The breakout MUST fail. Rejection is the clearest candle-level evidence of failure. Without it, the break may still be genuine. | MUST HAVE |
| Range position 0.2–0.8 (reclaimed) | **TRUE REQUIREMENT.** Price must be BACK INSIDE the range. If still at the extreme, the breakout hasn't failed yet. This confirms completeness of the false break. | MUST HAVE |

**The true first requirement is:** "A structural level existed that breakout traders would target."

This is validated by:
- Liquidity flags (session extremes, equal highs/lows)
- H1 swing boundaries (swing_high/swing_low = obvious levels)
- Any clearly defined structural edge visible to participants

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Key structural level existed | MUST HAVE | `(location.liquidity_above OR location.liquidity_below OR h1.swing_high > 0 OR h1.swing_low > 0)` | YES — swing levels are reliably populated from BiasSnapshot. Liquidity flags add confirmation when available. |
| 2 | Breakout failed (rejection) | MUST HAVE | `m5.rejection_present` | YES |
| 3 | Level reclaimed (back inside range) | MUST HAVE | `0.2 < location.range_position < 0.8` | YES |
| 4 | Strong rejection (high conviction failure) | SUPPORTING | `m5.rejection_strength_atr >= 0.7` | YES |
| 5 | Not fighting strong macro trend | SUPPORTING | `h4.trend_strength < 0.6 OR h4.trend == "NEUTRAL"` | YES |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| Price returns to extreme after reclaim | False break was actually just a pullback before genuine break | Requires temporal state (range_pos was 0.2-0.8, now back at extreme) | NOT REPRESENTABLE (single snapshot) |
| Strong displacement in breakout direction | Genuine institutional breakout, not false | `m15.displacement_present AND direction == breakout direction` | YES |
| H4 strong trend in breakout direction | Fighting macro | `h4.trend_strength > 0.6 AND h4.trend == breakout_direction` | YES |

---

## Key Insight

The original R1 used `liquidity_above/below` as the sole proxy for "breakable level existed." This is:
- Conceptually correct (liquidity pools ARE breakout targets)
- Practically unreliable (only available when session_high/equal_highs detected)

But `h1.swing_high/low > 0` serves the SAME purpose — swing boundaries are the levels breakout traders target. These are MORE reliably populated (from H1 BiasSnapshot pivot detection). By combining both, R1 becomes almost always available:

`h1.swing_high > 0` = "there IS a level above that could be broken"
`h1.swing_low > 0` = "there IS a level below that could be broken"

This doesn't prove the level WAS broken (temporal limitation), but combined with R2 (rejection) + R3 (reclaimed), the three together imply: level existed + tested + failed + reclaimed. The sequence is complete even without explicit breakout detection.

---

## Status: FULLY REPRESENTABLE with expanded R1. Currently the most achievable non-TREND_CONTINUATION strategy.
