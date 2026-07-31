# TREND_CONTINUATION — Reconciled Evidence Contract

## Trading Concept

Enter with a strong established trend during a pullback. The trend is the edge; the pullback provides the entry.

---

## Evidence Reconciliation

| Current Requirement | True Market Requirement or Implementation Assumption? | Classification |
|---|---|---|
| H4 trend BULLISH/BEARISH, strength >= 0.5 | **TRUE REQUIREMENT.** A strong macro trend is the foundational hypothesis. Without it, there is no trend to continue. | MUST HAVE |
| H1 BOS confirmed + aligned with H4 | **TRUE REQUIREMENT.** Multi-timeframe alignment proves the trend is active at execution scale, not just on higher timeframes. | MUST HAVE |
| M15 pullback active | **TRUE REQUIREMENT.** The pullback IS the setup. Without retracement, entry is at extension — no value, no structural stop. | MUST HAVE |
| M15 internal BOS in trend direction (supporting) | **IMPLEMENTATION ASSUMPTION.** The concept is "pullback is ending." M15 BOS is one way to detect this — but not the only way and not the best available. | SUPPORTING |
| TRENDING regime (supporting) | **TRUE REQUIREMENT (supporting).** Independent confirmation of directional environment. Adds confidence but not strictly necessary if H4+H1 already confirm. | SUPPORTING |

---

## V10-Compatible Evidence Contract

| # | Evidence | Classification | V10 Field | Available? |
|---|---|---|---|---|
| 1 | Strong macro directional trend | MUST HAVE | `h4.trend in ("BULLISH","BEARISH") AND h4.trend_strength >= 0.5` | YES |
| 2 | Execution-timeframe structure confirms macro direction | MUST HAVE | `h1.bos_confirmed AND h1.bos_direction == h4.trend` | YES |
| 3 | Price has pulled back (not at extension) | MUST HAVE | `m15.pullback_active AND m15.pullback_depth_atr >= 0.5` | YES |
| 4 | Pullback exhaustion signal | SUPPORTING | `m5.rejection_present AND m5.rejection_direction == h4.trend` | YES |
| 5 | Trending regime classification | SUPPORTING | `regime.regime == "TRENDING"` | YES |
| 6 | Pullback not too deep (not a breakdown) | SUPPORTING | `m15.pullback_depth_atr < 3.0` | YES |

---

## Invalidations

| Condition | Meaning | V10 Field | Available? |
|---|---|---|---|
| H4 trend_strength drops below 0.3 | Trend dying | `h4.trend_strength < 0.3` | YES |
| H1 BOS direction opposes H4 | Structure reversing | `h1.bos_direction != h4.trend` | YES |
| Pullback exceeds 100% retracement | No longer pullback — trend broken | `m15.retracement_pct > 1.0` | YES |

---

## Status: FULLY REPRESENTABLE — no changes required to contract, only minor supporting signal improvement.
