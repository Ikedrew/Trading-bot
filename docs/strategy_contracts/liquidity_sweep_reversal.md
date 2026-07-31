# LIQUIDITY_SWEEP_REVERSAL

## 1. Market Hypothesis

Institutional participants engineer price runs beyond obvious stop-loss clusters (equal highs/lows, session extremes) to fill large orders against trapped retail participants, then reverse aggressively. The "liquidity sweep" is a manufactured event: price momentarily exceeds a level where stops are clustered, fills institutional orders against those stops, then reverses.

The trade: after the sweep is confirmed (rejection), enter the reversal direction. The evidence chain must prove: (a) liquidity was targeted, (b) the sweep failed (trap confirmed), (c) institutional commitment to the new direction (structure shift).

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Liquidity pool existed to target | No pool = no sweep. Equal highs/lows or session extremes represent clustered stops that institutions target | `state.location.liquidity_above OR state.location.liquidity_below` | V3 context_builders.build_location_context: checks `h1.equal_highs_level > 0 OR h1.session_high > 0` | YELLOW — conditionally populated. Depends on BiasSnapshot or liquidity_snapshot providing session/equal levels. Sometimes True when H1 pivots align. Not guaranteed every cycle. |
| Aggressive rejection after the sweep | Proves the move beyond the level was a trap, not genuine. Strong rejection wick = institutional activity opposing the sweep direction | `state.m5.rejection_present AND state.m5.rejection_strength_atr >= 0.5` | M5 candle wick analysis in build_m5_understanding() | GREEN — reliable candle-based detection, computed every M5 bar. ATR threshold ensures meaningful wicks only. |
| Structure shift to new direction (CHoCH) | Change of Character = first structural break opposing the prior trend. Confirms the reversal is institutional and structural, not just a wick that will be retested | `state.h1.choch_detected OR state.m15.internal_choch` | H1Understanding.choch_detected (no detector), M15State.internal_choch (no detector) | RED — no CHoCH detection algorithm exists in the codebase. Both fields permanently False. The concept has no implementation. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| M15 displacement in reversal direction | Institutional urgency — large candle confirming commitment to new direction | `state.m15.displacement_present` | GREEN — candle analysis (range > 1.5 ATR). |
| Inside institutional zone after reversal | Price returned to a zone = new direction has structure to trade from | `state.location.inside_institutional_zone` | RED — dead field. |

## 4. Invalidations

None explicitly coded.

Conceptual invalidations:
- Price returns above the swept level (the "reversal" failed — it was a genuine breakout)
- H4 trend is strong in the sweep direction (fighting macro = dangerous)
- Momentum continues in sweep direction (no actual reversal)
- Volume/displacement in sweep direction exceeds rejection (genuine breakout, not trap)

## 5. Current Implementation Assessment

The hypothesis is the most sophisticated and highest-conviction setup in the strategy family. The implementation correctly identifies 2 of 3 required evidence pieces. The third (CHoCH / structure shift) has ZERO implementation — no algorithm exists to detect it.

R1 (liquidity) is partially available — fires when H1 BiasSnapshot detects equal_highs/lows or session levels.
R2 (rejection) is fully functional.
R3 (CHoCH) is completely absent — the strategy is permanently blocked.

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| No CHoCH detector in entire codebase | CRITICAL | Strategy permanently blocked |
| CHoCH requires temporal state comparison (current vs prior BOS direction) | Architectural | Single-snapshot evaluation cannot easily detect "direction changed" without prior-state reference |
| `inside_institutional_zone` dead (supporting) | Low | Supporting only |

## 7. Recommended V10-Compatible Contract

CHoCH = "first BOS in the opposite direction to the prior established trend." Available proxies:

| Concept | Available V10 Proxy | Trade-off |
|---|---|---|
| "Structure reversed direction" | `h1.bos_confirmed AND h1.bos_direction OPPOSING opportunity.directional_bias` | This only works if the opportunity detected the PRIOR direction correctly. If opportunity.directional_bias was set from the sweep direction, this fires. Imperfect but functional. |
| "Structure reversed direction" (stronger) | `h1.bos_direction != h4.trend` | H1 BOS opposing H4 = intraday structure is diverging from macro. This IS a character change. Cleaner proxy. |
| "Displacement confirms reversal" | `m15.displacement_present AND m15.displacement_direction OPPOSING h4.trend` | Displacement against the macro trend = institutional force in new direction. Not BOS-grade but meaningful. |

Recommended replacement for R3: `(h1.bos_direction != h4.trend) OR (m15.displacement_present AND m15.displacement_direction != h4.trend)`

This captures "structure is moving against the prior established direction" — which IS the functional meaning of CHoCH.

Trade-off: less precise than true CHoCH detection (which would require tracking BOS direction changes over time), but functional with available single-snapshot data.
