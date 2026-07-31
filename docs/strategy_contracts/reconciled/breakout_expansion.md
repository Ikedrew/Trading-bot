# BREAKOUT_EXPANSION — Reconciled Evidence Contract

## Trading Concept

After a period of volatility compression (energy accumulation), a sudden expansion with institutional displacement signals the start of a new directional move. The compression is the prerequisite; the displacement is the trigger. The trade enters with the expansion.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| `expansion_state == "COMPRESSING" OR volatility_state == "CONTRACTION" OR compression_bars > 5` | **TRUE REQUIREMENT about the concept; IMPLEMENTATION ASSUMPTION about the signals.** The concept is "market was compressed/quiet." Of three paths: `expansion_state` is dead, `compression_bars` is dead, `volatility_state == "CONTRACTION"` IS valid and available. | MUST HAVE (concept); only one signal path works |
| `m15.displacement_present OR expansion_state == "EXPANDING" OR volatility_state == "EXPANSION"` | **PARTIALLY REDUNDANT.** R3 also requires displacement. If R3 (displacement) is MUST HAVE, then R2 adds "OR expansion state" — which is either redundant (displacement already proves expansion) or a weaker substitute. | SUPPORTING (subsumes into R3) |
| `m15.displacement_present` | **TRUE REQUIREMENT.** The institutional candle IS the trigger. Without displacement, expansion is just noise or gradual drift. The displacement proves institutional participation and provides direction. | MUST HAVE |

**Reconciled requirements:**
- R1: Market was compressed/quiet (energy accumulation)
- R2: Displacement occurred (energy release with institutional force)
- R3 (original R2) becomes unnecessary as separate gate — it's satisfied automatically when R2 fires

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Prior compression / low volatility environment | MUST HAVE | `regime.volatility_state == "CONTRACTION"` | YES — the surviving valid path. Regime classifier detects low-vol environments. |
| 2 | Institutional displacement (expansion trigger) | MUST HAVE | `m15.displacement_present AND m15.displacement_magnitude_atr >= 1.5` | YES — reliable candle analysis. 1.5 ATR threshold ensures institutional scale. |
| 3 | Displacement has clear direction | MUST HAVE | `m15.displacement_direction in ("BULLISH","BEARISH")` | YES |
| 4 | Not fighting strong opposing macro trend | SUPPORTING | `h4.trend_strength < 0.6 OR h4.trend == m15.displacement_direction OR h4.trend == "NEUTRAL"` | YES |
| 5 | Ranging or neutral regime pre-displacement | SUPPORTING | `regime.regime in ("RANGING","NEUTRAL","")` | YES — compression typically occurs in ranges |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| Displacement immediately reverses | False expansion (wick / trapped move) | Requires next-bar analysis — NOT REPRESENTABLE in single snapshot | NOT REPRESENTABLE |
| Opposing displacement on same bar | Chop, not directional expansion | `m15.displacement_direction == ""` (shouldn't fire if direction unclear) | YES |
| H4 trend strong against displacement | Fighting macro | `h4.trend_strength > 0.6 AND h4.trend != displacement_direction` | YES |

---

## Key Insight: The Temporal Problem

This strategy describes a STATE TRANSITION: quiet → loud. The current implementation tries to observe BOTH states on the same snapshot. This works when:

1. Regime classifier still reports `CONTRACTION` (hasn't updated yet)
2. Current M15 bar produces displacement (> 1.5 ATR)

This IS a valid observation window — the regime classifier uses smoothing and won't flip to `EXPANSION` until the next update cycle. So on the exact bar where displacement occurs, `CONTRACTION` + `displacement_present` CAN coexist.

The trade-off: this is timing-sensitive. If the regime classifier updates BEFORE the strategy engine evaluates, R1 may already show `NEUTRAL` instead of `CONTRACTION`. This is acceptable — it means the strategy fires on the FIRST bar of expansion (when regime hasn't caught up yet) but not on subsequent bars.

This is actually CORRECT behaviour: you only want to enter at the START of the expansion, not after it's been running for multiple bars.

---

## NOT REPRESENTABLE Elements

| Concept | Why Not | Impact |
|---|---|---|
| Duration of compression (how many bars) | `compression_bars` is dead; would require counter in regime classifier | Low — `CONTRACTION` state implies sufficient duration |
| Whether expansion sustains beyond first bar | Requires temporal follow-through analysis | Medium — could be validated by next-cycle persistence check |

---

## Status: REPRESENTABLE with current V10 signals. Fires on CONTRACTION + displacement sequence. Naturally rare (correct for a breakout strategy — most observations should NOT be breakouts).
