# FALSE_BREAK

## 1. Market Hypothesis

Price breaks a key level, drawing in breakout participants who place stops on the other side. The breakout then fails and price reclaims the level — trapping the new entrants. The trapped participants' stops become fuel for the counter-move.

The trade: after the false breakout is confirmed (price back inside the range), trade against the breakout direction. Targets: trapped participants' stops + original range boundary.

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Breakout was attempted (key level tested/broken) | Must have a break first to have a FALSE break. Liquidity flags indicate levels with clustered interest that could trigger breakout participation | `state.location.liquidity_above OR state.location.liquidity_below` | V3 context_builders: `h1.equal_highs_level > 0 OR h1.session_high > 0` (above), same for below | YELLOW — conditionally available. Fires when H1 BiasSnapshot or liquidity_snapshot provides session/equal levels. The proxy is imperfect: "liquidity present" proves a TARGET level exists, not that price actually REACHED it. |
| Breakout failed (rejection) | The move beyond the level reversed. Without failure, it's a genuine breakout. Rejection wick = participants trapped on the wrong side | `state.m5.rejection_present` | M5 candle wick analysis | GREEN — reliable, computed every M5 bar. |
| Level reclaimed (price back inside range) | Confirms the false break is COMPLETE. If price is still at the extreme, the breakout might still succeed. Reclaim = definitively failed | `0.2 < state.location.range_position < 0.8` | M15 swing → LocationState.range_position | GREEN — populated after context-ordering fix. Represents genuine position within M15 range. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| Strong rejection wick (>= 0.7 ATR) | High conviction that the breakout failed. Bigger wick = more trapped participants = more fuel | `state.m5.rejection_strength_atr >= 0.7` | GREEN — reliable candle measurement. |

## 4. Invalidations

None explicitly coded.

Conceptual invalidations:
- Price re-breaks the level (the "false break" was actually a pullback before continuation)
- H4 trend is strong in the breakout direction (the break might be a trend continuation, not false)
- Displacement continues in breakout direction (genuine institutional commitment to the break)
- Range_position returns to extreme (price going back to the broken level = re-test, not reclaim)

## 5. Current Implementation Assessment

This is the most achievable strategy after TREND_CONTINUATION. All required conditions use available data:
- R1: YELLOW (conditionally available, imperfect proxy)
- R2: GREEN
- R3: GREEN

The strategy CAN fire when liquidity flags populate (session_high/equal_highs detected from H1 candles). The main limitation is R1's proxy quality — "liquidity present" is not the same as "breakout occurred." The condition is necessary (need a level to break) but not sufficient (doesn't prove the break happened).

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| R1 proxy is imperfect ("level exists" ≠ "level was broken") | Medium | May fire when level wasn't actually reached, or miss breaks at non-liquidity levels |
| No temporal check ("was at extreme, now inside") | Medium | Single snapshot can't prove the SEQUENCE: was extreme → now mid. It only sees current state (mid-range + rejection + liquidity nearby) |
| `liquidity_above/below` depends on H1 session/equal levels | Medium | These are only populated when BiasSnapshot detects them — not every H1 analysis produces equal_highs |

## 7. Recommended V10-Compatible Contract

Better evidence for "breakout was attempted AND failed":

| Concept | Available V10 Proxy | Trade-off |
|---|---|---|
| "Price was at extreme recently" | `range_position currently 0.2-0.8 BUT m15.pullback_active` | Pullback from extreme = was at extreme, came back. Imperfect but captures the sequence. |
| "A level exists that could be broken" | `h1.swing_high > 0 OR h1.swing_low > 0` | Swing levels ARE the boundaries breakout traders target. More reliably populated than liquidity flags. |
| "Rejection occurred at/near the level" | `m5.rejection_present AND m5.rejection_strength_atr >= 0.7` (already checked) | Strong rejection already implies the level was tested and failed. |

Recommended improvement for R1: `(liquidity_above OR liquidity_below OR h1.swing_high > 0 OR h1.swing_low > 0)` — accepting swing levels as valid breakout targets alongside liquidity pools. This increases availability without weakening the concept.

The temporal limitation ("was extreme, now inside") is architecturally hard to solve without a state buffer. The current proxy (range_pos 0.2-0.8 + rejection + liquidity present) is the best available single-snapshot approximation.
